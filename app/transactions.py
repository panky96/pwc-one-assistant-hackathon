"""Durable TIER_3 operation state machine. State lives in the shared Store (all workers see it).
UNIQUE(tenant_id, operation_id); only an atomic APPROVED->EXECUTING compare-and-set may execute.
Expired leases during EXECUTING go to NEEDS_REVIEW: completion is unknown, so we never auto-retry."""
from __future__ import annotations
import enum, time
from dataclasses import dataclass, field
from app.storage import Store


class OpState(str, enum.Enum):
    PENDING = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class TxnError(Exception):
    pass


@dataclass
class Operation:
    tenant_id: str
    operation_id: str
    requester: str
    tool: str
    args: dict
    state: OpState = OpState.PENDING
    expires_at: float = 0.0
    result: object = None
    repairs: list = field(default_factory=list)
    lease_expires_at: float = 0.0

    def body(self) -> dict:
        return {"requester": self.requester, "tool": self.tool, "args": self.args, "expires_at": self.expires_at,
                "result": self.result, "repairs": self.repairs}

    @classmethod
    def from_row(cls, row: dict) -> "Operation":
        return cls(tenant_id=row["tenant_id"], operation_id=row["operation_id"], requester=row["requester"], tool=row["tool"],
                   args=row["args"], state=OpState(row["state"]), expires_at=row["expires_at"], result=row.get("result"),
                   repairs=row.get("repairs", []), lease_expires_at=row.get("lease_expires_at", 0.0))


class OperationStore:
    def __init__(self, approval_ttl_s: float = 900, lease_s: float = 30, clock=time.time, store: Store | None = None):
        self.store = store or Store(":memory:")
        self.ttl, self.lease, self.clock = approval_ttl_s, lease_s, clock

    def get(self, tenant_id, operation_id) -> Operation:
        row = self.store.op_get(tenant_id, operation_id)
        if row is None:  # also the answer to cross-tenant probes
            raise TxnError("not found")
        return Operation.from_row(row)

    def propose(self, tenant_id, operation_id, requester, tool, args, repairs=()) -> Operation:
        op = Operation(tenant_id, operation_id, requester, tool, dict(args), expires_at=self.clock() + self.ttl,
                       repairs=list(repairs))
        self.store.op_insert(tenant_id, operation_id, op.state.value, op.body())  # duplicate ids keep the stored row
        return self.get(tenant_id, operation_id)

    def pending(self, tenant_id) -> list[Operation]:
        return [Operation.from_row(r) for r in self.store.op_list(tenant_id, OpState.PENDING.value)]

    def decide(self, tenant_id, operation_id, approver, approve: bool) -> Operation:
        op = self.get(tenant_id, operation_id)
        if op.state != OpState.PENDING:
            raise TxnError(f"cannot decide in state {op.state.value}")
        if self.clock() > op.expires_at:
            self.store.op_transition(tenant_id, operation_id, (OpState.PENDING.value,), OpState.EXPIRED.value, op.body())
            raise TxnError("approval expired")
        if approver == op.requester:
            raise TxnError("requester cannot approve own operation")
        new = OpState.APPROVED if approve else OpState.DENIED
        if not self.store.op_transition(tenant_id, operation_id, (OpState.PENDING.value,), new.value, op.body()):
            raise TxnError("concurrent decision: state already changed")
        return self.get(tenant_id, operation_id)

    def execute(self, tenant_id, operation_id, runner) -> Operation:
        op = self.get(tenant_id, operation_id)
        if op.state in (OpState.EXECUTED, OpState.FAILED, OpState.NEEDS_REVIEW):
            return op  # terminal: duplicate execute returns stored outcome
        if op.state != OpState.APPROVED:
            raise TxnError(f"not executable in state {op.state.value}")
        lease = self.clock() + self.lease
        if not self.store.op_transition(tenant_id, operation_id, (OpState.APPROVED.value,), OpState.EXECUTING.value,
                                        op.body(), lease_expires_at=lease):
            return self.get(tenant_id, operation_id)  # another worker won the claim
        try:
            res = runner(**op.args)
        except Exception as e:
            op.result = str(e)
            self.store.op_transition(tenant_id, operation_id, (OpState.EXECUTING.value,), OpState.FAILED.value, op.body())
            return self.get(tenant_id, operation_id)
        op.result = res
        self.store.op_transition(tenant_id, operation_id, (OpState.EXECUTING.value,), OpState.EXECUTED.value, op.body())
        return self.get(tenant_id, operation_id)

    def reconcile(self) -> list[str]:
        """Expired EXECUTING leases -> NEEDS_REVIEW (manual review path)."""
        flagged = []
        for row in self.store.op_stale_executing(self.clock()):
            op = Operation.from_row(row)
            if self.store.op_transition(op.tenant_id, op.operation_id, (OpState.EXECUTING.value,),
                                        OpState.NEEDS_REVIEW.value, op.body()):
                flagged.append(op.operation_id)
        return flagged
