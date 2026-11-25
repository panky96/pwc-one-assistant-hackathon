"""Load & validate mcp_config.json (env interpolation, strict booleans, no unknown servers/plugins)."""
from __future__ import annotations
import json, os, re
from pathlib import Path
from app.settings import ConfigError, parse_strict_bool

_VAR = re.compile(r"\$\{([A-Z0-9_]+)\}")
KNOWN_PLUGINS = {"pii_redactor", "injection_shield", "circuit_breaker", "ragas_auditor", "authn_gate", "ambiguity_gate",
                 "idempotency_guard", "model_fallback"}
KNOWN_SERVERS = {"hybrid-rag", "state-store", "graphify-vault", "k6"}


def _interp(v, env):
    if isinstance(v, str):
        m = _VAR.fullmatch(v)
        if m:  # whole-value placeholder -> raw value (may be empty)
            return env.get(m[1], "")
        return _VAR.sub(lambda mm: env.get(mm[1], ""), v)
    if isinstance(v, dict): return {k: _interp(x, env) for k, x in v.items()}
    if isinstance(v, list): return [_interp(x, env) for x in v]
    return v


def load_config(path: str | Path = "mcp_config.json", env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    cfg = _interp(json.loads(Path(path).read_text()), env)
    cfg["environment"] = env.get("APP_ENV") or cfg["environment"]  # APP_ENV overrides the file
    auth = cfg["security"]["authentication"]["enabled"]
    cfg["security"]["authentication"]["enabled"] = parse_strict_bool("AUTH_ENABLED", auth if auth != "" else "false")
    if cfg["environment"] == "production" and not cfg["security"]["authentication"]["enabled"]:
        raise ConfigError("production config requires authentication.enabled=true")
    if not cfg["security"]["allow_unknown_servers"]:
        bad = set(cfg["mcpServers"]) - KNOWN_SERVERS
        if bad: raise ConfigError(f"unknown MCP servers: {bad}")
    if not cfg["security"]["allow_unknown_plugins"]:
        bad = {p["name"] for p in cfg["plugins"]} - KNOWN_PLUGINS
        if bad: raise ConfigError(f"unknown plugins: {bad}")
    for name, s in cfg["mcpServers"].items():
        if s["transport"] not in ("stdio", "streamable-http"):
            raise ConfigError(f"{name}: bad transport")
        if s["transport"] == "stdio" and "command" not in s:
            raise ConfigError(f"{name}: missing command")
    r = cfg["retrieval"]
    if r["reranker"]["output_k"] > r["generation"]["max_context_chunks"]:
        raise ConfigError("reranker.output_k exceeds generation.max_context_chunks")
    return cfg
