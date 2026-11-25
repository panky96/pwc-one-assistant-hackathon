"""Environment settings with strict validation (design.md: Identity & tenancy)."""
from __future__ import annotations
import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def parse_strict_bool(name: str, value: str | None) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigError(f"{name} must be exactly 'true' or 'false', got {value!r}")


@dataclass(frozen=True)
class Settings:
    app_env: str
    auth_enabled: bool
    service_name: str
    azure_tenant_id: str
    azure_audience: str
    azure_required_role: str
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_small: str = "anthropic/claude-haiku-4.5"
    model_large: str = "anthropic/claude-sonnet-4.5"
    redis_url: str = ""
    database_url: str = ""
    otlp_endpoint: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        app_env = e.get("APP_ENV", "development")
        if app_env not in ("development", "production"):
            raise ConfigError(f"APP_ENV invalid: {app_env!r}")
        auth = parse_strict_bool("AUTH_ENABLED", e.get("AUTH_ENABLED", "false"))
        if app_env == "production" and not auth:
            raise ConfigError("production requires AUTH_ENABLED=true; refusing to start")
        return cls(
            app_env=app_env,
            auth_enabled=auth,
            service_name=e.get("SERVICE_NAME", "pwc-one-assistant"),
            azure_tenant_id=e.get("AZURE_TENANT_ID", ""),
            azure_audience=e.get("AZURE_AUDIENCE", ""),
            azure_required_role=e.get("AZURE_REQUIRED_ROLE", ""),
            openrouter_api_key=e.get("OPENROUTER_API_KEY", ""),
            openrouter_base_url=e.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            model_small=e.get("LLM_MODEL_SMALL", "anthropic/claude-haiku-4.5"),
            model_large=e.get("LLM_MODEL_LARGE", "anthropic/claude-sonnet-4.5"),
            redis_url=e.get("REDIS_URL", ""), database_url=e.get("DATABASE_URL", ""),
            otlp_endpoint=e.get("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
        )
