"""Runtime configuration from mcp_config.json: single source for thresholds, pools, plugin wiring and governance."""
from __future__ import annotations
from app.mcp_config import load_config
from app.settings import Settings


class RuntimeConfig:
    def __init__(self, cfg: dict):
        self.raw = cfg
        r, h, t = cfg["retrieval"], cfg["harness"], cfg["triage"]
        self.min_confidence = float(t["min_confidence"])
        self.max_iterations = int(h["max_iterations"])
        self.turn_budget_ms = float(h["total_turn_budget_ms"])
        cb = next(p for p in cfg["plugins"] if p["name"] == "circuit_breaker")
        self.max_identical_calls = int(cb["config"]["max_identical_calls"])
        self.retrieval_backend = str(r.get("backend", "standin"))  # env RETRIEVAL_BACKEND overrides
        self.min_score = float(r["reranker"]["min_score"])
        self.output_k = int(r["reranker"]["output_k"])
        self.fused_k = int(r["fusion"]["fused_k"])
        self.rrf_k = int(r["fusion"]["rrf_k"])
        self.cache_threshold = float(r["cache"]["similarity_threshold"])
        self.cache_ttl = int(r["cache"]["ttl_seconds"])
        b = dict(cfg["bulkheads"])
        self.bulkhead_wait_s = b.pop("acquire_timeout_ms") / 1000
        self.bulkheads = {k: int(v) for k, v in b.items()}
        rec = cfg.get("reconciliation", {})
        self.reconcile_interval_s = float(rec.get("interval_s", 60)) if rec.get("enabled", True) else 0.0
        self.governance = dict(cfg.get("governance", {}))
        self.plugins = cfg["plugins"]

    def loop_kwargs(self) -> dict:
        return {"max_iterations": self.max_iterations, "total_turn_budget_ms": self.turn_budget_ms,
                "max_identical_calls": self.max_identical_calls}

    @classmethod
    def load(cls, path, settings: Settings) -> "RuntimeConfig":
        # APP_ENV / AUTH_ENABLED come from the validated Settings so both checks agree.
        env = {"APP_ENV": settings.app_env, "AUTH_ENABLED": "true" if settings.auth_enabled else "false",
               "SERVICE_NAME": settings.service_name, "STATE_STORE_URL": "",
               "OTEL_EXPORTER_OTLP_ENDPOINT": settings.otlp_endpoint}
        return cls(load_config(path, env=env))
