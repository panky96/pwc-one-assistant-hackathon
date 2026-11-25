"""TOOL_DISPATCH: loop/stall guard (scope A) and request failure breaker (scope B)."""
from __future__ import annotations
import hashlib, json
from collections import Counter


def args_hash(tool: str, args: dict) -> str:
    return hashlib.sha256((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()


class LoopGuard:
    """HALTED after > max_identical_calls identical calls in one turn."""
    def __init__(self, max_identical_calls: int = 2):
        self.max, self.calls = max_identical_calls, Counter()

    def check(self, tool: str, args: dict) -> bool:
        h = args_hash(tool, args)
        self.calls[h] += 1
        return self.calls[h] <= self.max


class FailureBreaker:
    """HALTED_BREAKER after >= threshold failures of the same tool+args."""
    def __init__(self, threshold: int = 2):
        self.threshold, self.fails = threshold, Counter()

    def record_failure(self, tool: str, args: dict) -> None:
        self.fails[args_hash(tool, args)] += 1

    def tripped(self, tool: str, args: dict) -> bool:
        return self.fails[args_hash(tool, args)] >= self.threshold
