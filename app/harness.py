"""ToolExecutionHarness: validate -> auto-repair -> RBAC tier -> idempotency."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, Callable
from pydantic import BaseModel, ValidationError
from app import telemetry
from app.contracts import Tier, TenantContext


class ToolError(Exception):
    pass


class PermissionDenied(ToolError):
    pass


@dataclass
class Tool:
    name: str
    tier: Tier
    schema: type[BaseModel]
    fn: Callable[..., Any]
    required_role: str | None = None


_SKU = re.compile(r"^sku\s*-?\s*(\d{1,3})$", re.I)
_LOT = re.compile(r"^lot\s*-?\s*(\d{1,5})$", re.I)
_PAD = [(re.compile(rf"^{p}\s*-?\s*(\d{{1,{w}}})$", re.I), p, w) for p, w in (("EXP", 4), ("ORD", 5), ("ACC", 6))]


def repair_value(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        if (m := _SKU.match(s)):
            return f"SKU-{int(m[1]):03d}"
        if (m := _LOT.match(s)):
            return f"LOT-{int(m[1]):05d}"
        for rx, pre, w in _PAD:
            if (m := rx.match(s)):
                return f"{pre}-{int(m[1]):0{w}d}"
    return v


@dataclass
class ToolExecutionHarness:
    tools: dict[str, Tool] = field(default_factory=dict)
    repairs: list[dict] = field(default_factory=list)  # logged; shown to approvers

    def register(self, t: Tool) -> None:
        self.tools[t.name] = t

    def prepare(self, name: str, raw: dict, tenant: TenantContext) -> tuple[Tool, BaseModel, list[dict]]:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"unknown tool {name}")
        if tool.required_role and tool.required_role not in tenant.roles:
            raise PermissionDenied(f"{name} requires role {tool.required_role}")
        repaired, log = {}, []
        for k, v in raw.items():
            nv = repair_value(v)
            if nv != v:
                log.append({"field": k, "from": v, "to": nv})
            repaired[k] = nv
        self.repairs.extend(log)
        telemetry.incr("tool.calls")
        if not log: telemetry.incr("tool.first_pass_valid_unrepaired")
        try:
            args = tool.schema.model_validate(repaired)
        except ValidationError as e:
            telemetry.incr("tool.invalid")
            raise ToolError(f"invalid arguments: {e.errors(include_url=False)}") from e
        return tool, args, log
