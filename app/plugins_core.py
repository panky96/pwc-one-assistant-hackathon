"""Plugin lifecycle: stages, priority ordering, fail-closed (design.md: Plugin lifecycle)."""
from __future__ import annotations
import enum
from dataclasses import dataclass, field
from typing import Any, Callable


class Stage(str, enum.Enum):
    PRE = "PRE_EXECUTION"
    DISPATCH = "TOOL_DISPATCH"
    POST = "POST_EXECUTION"


class Blocked(Exception):
    def __init__(self, plugin: str, reason: str):
        super().__init__(f"{plugin}: {reason}")
        self.plugin, self.reason = plugin, reason


@dataclass
class Plugin:
    name: str
    stage: Stage
    priority: int
    fn: Callable[[dict[str, Any]], dict[str, Any]]
    fail_closed: bool = True
    enabled: bool = True


@dataclass
class PluginRegistry:
    plugins: list[Plugin] = field(default_factory=list)

    def register(self, p: Plugin) -> None:
        self.plugins.append(p)

    def run(self, stage: Stage, ctx: dict[str, Any]) -> dict[str, Any]:
        for p in sorted((x for x in self.plugins if x.stage == stage and x.enabled), key=lambda x: x.priority):
            try:
                ctx = p.fn(ctx)
            except Blocked:
                raise
            except Exception as e:  # fail closed: plugin error blocks the request
                if p.fail_closed:
                    raise Blocked(p.name, f"plugin error: {e}") from e
        return ctx
