"""Bulkheads: bounded pools per dependency so a slow reranker or LLM cannot starve the rest.
Overflow waits briefly, then sheds (the API maps Overloaded to HTTP 429)."""
from __future__ import annotations
import contextlib, threading
from app import telemetry


class Overloaded(RuntimeError):
    pass


class Bulkhead:
    def __init__(self, name: str, size: int, wait_s: float = 0.05):
        self.name, self.size, self.wait_s = name, size, wait_s
        self._sem = threading.BoundedSemaphore(size)

    @contextlib.contextmanager
    def __call__(self):
        if not self._sem.acquire(timeout=self.wait_s):
            telemetry.incr(f"shed.{self.name}")
            raise Overloaded(self.name)
        try:
            yield
        finally:
            self._sem.release()
