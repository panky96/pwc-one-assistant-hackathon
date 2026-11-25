"""Entra ID token validation (signature via JWKS, issuer, audience, expiry, tenant) + On-Behalf-Of exchange."""
from __future__ import annotations
import httpx, jwt
from jwt import PyJWKClient


class EntraVerifier:
    def __init__(self, tenant_id: str, audience: str, key_resolver=None, allowed_tenants: set[str] | None = None):
        self.audience, self.allowed = audience, allowed_tenants or {tenant_id}
        self.issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        self.jwks = PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys") if key_resolver is None else None
        self.key_resolver = key_resolver  # callable(token)->key, injectable for tests

    def __call__(self, token: str) -> dict:
        key = self.key_resolver(token) if self.key_resolver else self.jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                            options={"require": ["exp", "iss", "aud", "tid", "oid"]})
        if claims["tid"] not in self.allowed:
            raise jwt.InvalidTokenError("tenant not allowed")
        return claims


def obo_exchange(tenant_id: str, client_id: str, client_secret: str, user_token: str, scope: str,
                 transport: httpx.BaseTransport | None = None) -> str:
    """Azure On-Behalf-Of flow: swap the user's token for a downstream-scoped token."""
    with httpx.Client(transport=transport, timeout=10) as c:
        r = c.post(f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token", data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "client_id": client_id,
            "client_secret": client_secret, "assertion": user_token, "scope": scope, "requested_token_use": "on_behalf_of"})
        r.raise_for_status()
        return r.json()["access_token"]
