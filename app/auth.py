"""TenantContext from validated token claims only.
Signature/issuer/audience validation (Entra JWKS) is a pluggable verifier; the default
dev verifier is only usable when AUTH_ENABLED=false."""
from __future__ import annotations
from typing import Callable
from fastapi import HTTPException, Request
from app.contracts import TenantContext
from app.settings import Settings

Verifier = Callable[[str], dict]  # token -> validated claims, raises on failure


def build_tenant_context(request: Request, settings: Settings, verifier: Verifier | None) -> TenantContext:
    if not settings.auth_enabled:
        # local dev only: fixed identity; client-supplied tenant fields are ignored.
        return TenantContext(tenant_id="dev-tenant", user_id="dev-user", roles=("user", "approver"))
    hdr = request.headers.get("authorization", "")
    if not hdr.lower().startswith("bearer ") or verifier is None:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = verifier(hdr[7:])
    except Exception:
        raise HTTPException(401, "invalid token")
    roles = tuple(claims.get("roles", ()))
    if settings.azure_required_role and settings.azure_required_role not in roles:
        raise HTTPException(403, "missing required role")
    if not claims.get("tid") or not claims.get("oid"):
        raise HTTPException(401, "token lacks tid/oid")
    return TenantContext(tenant_id=claims["tid"], user_id=claims["oid"], roles=roles)
