"""Bounded agent-turn state machine: INIT->TRIAGE->REASONING->TOOL_CALL->OBSERVATION->VERIFY->DONE
plus AWAITING_APPROVAL, HALTED (loop/stall/budget) and HALTED_BREAKER (repeated failure)."""
from __future__ import annotations
import enum, time
from dataclasses import dataclass, field
from typing import Callable
from app import telemetry
from app.contracts import TenantContext, Tier
from app.harness import ToolExecutionHarness, ToolError
from plugins.dispatch import FailureBreaker, LoopGuard


class TurnState(str, enum.Enum):
    INIT = "INIT"; TRIAGE = "TRIAGE"; REASONING = "REASONING"; TOOL_CALL = "TOOL_CALL"
    OBSERVATION = "OBSERVATION"; VERIFY = "VERIFY"; DONE = "DONE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"; RESUMED = "RESUMED"; HALTED = "HALTED"; HALTED_BREAKER = "HALTED_BREAKER"


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class Final:
    text: str


@dataclass
class LoopResult:
    state: TurnState
    final: str | None = None
    steps: int = 0
    pending: ToolCall | None = None
    repairs: list = field(default_factory=list)
    observations: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    raw: list = field(default_factory=list)


def compress_observation(obs, max_tokens: int = 180) -> str:
    """Extractive compression; applies only to observations re-entering the reasoning loop."""
    words = str(obs).split()
    return " ".join(words[:max_tokens])


class BoundedLoop:
    def __init__(self, harness: ToolExecutionHarness, tenant: TenantContext, max_iterations: int = 5,
                 total_turn_budget_ms: float = 2500, max_identical_calls: int = 2, clock: Callable[[], float] = time.monotonic):
        self.h, self.tenant, self.max_it, self.budget = harness, tenant, max_iterations, total_turn_budget_ms / 1000
        self.guard, self.breaker, self.clock = LoopGuard(max_identical_calls), FailureBreaker(2), clock

    def run(self, decide: Callable[[list], ToolCall | Final]) -> LoopResult:
        res = LoopResult(TurnState.INIT)
        start = self.clock()
        res.trace += [TurnState.INIT, TurnState.TRIAGE]
        for step in range(1, self.max_it + 1):
            if self.clock() - start > self.budget:  # overall turn deadline always binds
                res.state = TurnState.HALTED; return res
            res.trace.append(TurnState.REASONING)
            action = decide(res.observations)
            if isinstance(action, Final):
                res.trace += [TurnState.VERIFY, TurnState.DONE]
                res.state, res.final, res.steps = TurnState.DONE, action.text, step - 1
                return res
            res.trace.append(TurnState.TOOL_CALL); res.steps = step
            if self.breaker.tripped(action.name, action.args):
                res.state = TurnState.HALTED_BREAKER; return res
            if not self.guard.check(action.name, action.args):
                res.state = TurnState.HALTED; return res
            try:
                tool, args, repairs = self.h.prepare(action.name, action.args, self.tenant)
            except ToolError as e:
                self.breaker.record_failure(action.name, action.args)
                res.observations.append(f"error: {e}"); continue
            res.repairs += repairs
            if tool.tier == Tier.MUTATE:  # state freeze; async HITL
                res.state, res.pending = TurnState.AWAITING_APPROVAL, ToolCall(action.name, args.model_dump())
                res.trace.append(TurnState.AWAITING_APPROVAL); return res
            try:
                with telemetry.span(f"tool.{action.name}"):
                    out = tool.fn(**args.model_dump())
            except Exception as e:
                self.breaker.record_failure(action.name, action.args)
                res.observations.append(f"error: {e}"); continue
            res.trace.append(TurnState.OBSERVATION); res.raw.append(out)
            res.observations.append(compress_observation(out))
        res.state = TurnState.HALTED
        return res
