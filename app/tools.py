"""Demo domain tools (TIER_1_READ / TIER_2_COMPUTE / TIER_3_MUTATE), extractors, and ambiguity detection."""
from __future__ import annotations
import re
from pydantic import Field
from app.contracts import Strict, Tier
from app.harness import Tool, ToolExecutionHarness


class LookupPolicyIn(Strict):
    note_id: str = Field(pattern=r"^[a-z0-9-]{3,60}$")


class PerDiemIn(Strict):
    days: int = Field(ge=1, le=60)
    tier1_country: bool
    meals_provided: bool = False


class SubmitExpenseIn(Strict):
    report_id: str = Field(pattern=r"^EXP-\d{4}$")


class CancelOrderIn(Strict):
    order_id: str = Field(pattern=r"^ORD-\d{5}$")


class TransferIn(Strict):
    from_account: str = Field(pattern=r"^ACC-\d{6}$")
    to_account: str = Field(pattern=r"^ACC-\d{6}$")
    amount: float = Field(gt=0, le=1_000_000)


def build_harness(notes: dict | None = None) -> ToolExecutionHarness:
    h = ToolExecutionHarness()
    notes = notes or {}
    h.register(Tool("lookup_policy", Tier.READ, LookupPolicyIn,
                    lambda note_id: notes[note_id].facts if note_id in notes else []))
    h.register(Tool("calc_per_diem", Tier.COMPUTE, PerDiemIn,
                    lambda days, tier1_country, meals_provided=False: round(days * (90 if tier1_country else 60) * (0.75 if meals_provided else 1), 2)))
    h.register(Tool("submit_expense_report", Tier.MUTATE, SubmitExpenseIn, lambda report_id: {"submitted": report_id}, required_role="user"))
    h.register(Tool("cancel_order", Tier.MUTATE, CancelOrderIn, lambda order_id: {"cancelled": order_id}, required_role="user"))
    h.register(Tool("transfer_funds", Tier.MUTATE, TransferIn,
                    lambda from_account, to_account, amount: {"moved": amount, "from": from_account, "to": to_account}, required_role="user"))
    return h


_SELECT = [(re.compile(r"expense report", re.I), "submit_expense_report"),
           (re.compile(r"cancel.*order|order.*cancel", re.I), "cancel_order"),
           (re.compile(r"transfer", re.I), "transfer_funds")]
_EXTRACT = {
    "report_id": re.compile(r"\bEXP[-\s]?(\d{1,4})\b", re.I),
    "order_id": re.compile(r"\bORD[-\s]?(\d{1,5})\b", re.I),
    "from_account": re.compile(r"from\s+(?:account\s+)?ACC[-\s]?(\d{1,6})", re.I),
    "to_account": re.compile(r"to\s+(?:account\s+)?ACC[-\s]?(\d{1,6})", re.I),
    "amount": re.compile(r"\$?\b(\d+(?:\.\d+)?)\s*(?:dollars|usd)?\b(?!\s*-)", re.I),
}
_PREFIX = {"report_id": ("EXP-", 4), "order_id": ("ORD-", 5), "from_account": ("ACC-", 6), "to_account": ("ACC-", 6)}


def select_tool(msg: str) -> str | None:
    return next((t for rx, t in _SELECT if rx.search(msg)), None)


def extract_args(tool: str, msg: str, h: ToolExecutionHarness) -> tuple[dict, list[str]]:
    """Returns (args, missing_required_fields). Never guesses values."""
    fields = h.tools[tool].schema.model_fields
    args, missing = {}, []
    for f, info in fields.items():
        m = _EXTRACT[f].search(msg) if f in _EXTRACT else None
        if f == "amount" and m:
            cleaned = re.sub(r"ACC[-\s]?\d+|EXP[-\s]?\d+|ORD[-\s]?\d+", "", msg, flags=re.I)
            m = _EXTRACT["amount"].search(cleaned)
        if not m:
            if info.is_required(): missing.append(f)
            continue
        if f in _PREFIX:
            pre, w = _PREFIX[f]; args[f] = f"{pre}{int(m[1]):0{w}d}"
        else:
            args[f] = float(m[1])
    return args, missing
