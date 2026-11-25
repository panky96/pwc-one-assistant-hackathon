"""idempotency_guard (TOOL_DISPATCH), ragas_auditor + model_fallback (POST_EXECUTION)."""
from __future__ import annotations
import random, uuid
from app import telemetry
from app.plugins_core import Blocked, Plugin, Stage
from app.verification import verify


def valid_uuid4(s: str) -> bool:
    try:
        return uuid.UUID(s).version == 4
    except (ValueError, AttributeError, TypeError):
        return False


def idempotency_guard(ctx):
    op = ctx.get("operation_id")
    if op is not None and not valid_uuid4(op):
        raise Blocked("idempotency_guard", "operation_id must be a UUIDv4")
    return ctx


def make_ragas_auditor(sample_rate=0.1, min_faithfulness=0.92, rng=random.random):
    def ragas_auditor(ctx):  # async/sampled monitor: flags, never blocks
        if ctx.get("chunks") and rng() < sample_rate:
            v = verify(ctx["answer"], ctx["chunks"])
            telemetry.incr("ragas.sampled"); telemetry.incr("ragas.faithfulness_sum", v.support)
            if v.support < min_faithfulness:
                telemetry.incr("ragas.flagged"); ctx["flagged"] = True
        return ctx
    return ragas_auditor


def make_model_fallback(max_escalations=1):
    def model_fallback(ctx):  # escalate small -> large once when the synchronous gate fails
        v = ctx.get("verdict")
        ctx["escalate"] = bool(v is not None and not v.ok and ctx.get("escalations", 0) < max_escalations)
        return ctx
    return model_fallback


def post_plugins(sample_rate=0.1):
    return [Plugin("ragas_auditor", Stage.POST, 10, make_ragas_auditor(sample_rate), fail_closed=False),
            Plugin("model_fallback", Stage.POST, 20, make_model_fallback())]


def dispatch_plugins():
    return [Plugin("idempotency_guard", Stage.DISPATCH, 20, idempotency_guard)]
