"""Build the plugin registry from mcp_config.json: enabled, stage, priority and fail_closed come from config."""
from __future__ import annotations
from app.plugins_core import Plugin, PluginRegistry, Stage
from plugins import gates, lifecycle, safety

FACTORIES = {
    "authn_gate": lambda c, d: gates.authn_gate,
    "pii_redactor": lambda c, d: safety.pii_redactor,
    "injection_shield": lambda c, d: safety.injection_shield,
    "ambiguity_gate": lambda c, d: gates.make_ambiguity_gate(d["harness"], d["min_confidence"]),
    "circuit_breaker": lambda c, d: gates.circuit_breaker,
    "idempotency_guard": lambda c, d: lifecycle.idempotency_guard,
    "ragas_auditor": lambda c, d: lifecycle.make_ragas_auditor(c.get("sample_rate", 0.1), c.get("min_faithfulness", 0.92)),
    "model_fallback": lambda c, d: lifecycle.make_model_fallback(c.get("max_escalations", 1)),
}


def build_registry(entries: list[dict], deps: dict) -> PluginRegistry:
    reg = PluginRegistry()
    for e in entries:
        if not e.get("enabled", False):
            continue
        name = e["name"]
        if name not in FACTORIES:
            raise ValueError(f"no implementation for configured plugin {name!r}")
        reg.register(Plugin(name=name, stage=Stage(e["stage"]), priority=int(e["priority"]),
                            fn=FACTORIES[name](e.get("config", {}), deps),
                            fail_closed=bool(e.get("fail_closed", False))))
    return reg
