"""Config-driven gates: authn_gate (PRE 5), ambiguity_gate (PRE 30), circuit_breaker (TOOL_DISPATCH 10)."""
from __future__ import annotations
from app.contracts import Route
from app.plugins_core import Blocked
from app.tools import extract_args, select_tool
from app.triage import apply_confidence_policy, triage


def authn_gate(ctx):
    t = ctx.get("tenant")
    if t is None or not t.tenant_id or not t.user_id or t.tenant_id == "*":
        raise Blocked("authn_gate", "no validated tenant context")
    return ctx


def make_ambiguity_gate(harness, min_confidence: float):
    def ambiguity_gate(ctx):
        msg = ctx["message"]
        tr = apply_confidence_policy(triage(msg), min_confidence)
        ctx["triage"] = tr  # reused by the agent so routing is decided once
        if tr.route == Route.TRANSACTION_EXEC and not tr.clarify:
            tool = select_tool(msg)
            if tool is None:
                ctx["clarification"] = ["action"]
            else:
                _, missing = extract_args(tool, msg, harness)  # never guess values
                if missing:
                    ctx["clarification"] = missing
        return ctx
    return ambiguity_gate


def circuit_breaker(ctx):
    if "tool" not in ctx:  # dispatch checks for non-tool stages (e.g. operation_id) pass through
        return ctx
    if ctx["breaker"].tripped(ctx["tool"], ctx["args"]):
        raise Blocked("circuit_breaker", "HALTED_BREAKER: repeated failures")
    if not ctx["guard"].check(ctx["tool"], ctx["args"]):
        raise Blocked("circuit_breaker", "HALTED: identical-call loop")
    return ctx
