"""Durable TIER_3 operation state machine (in-memory store; Postgres in production).
UNIQUE(tenant_id, operation_id); only an atomic APPROVED->EXECUTING transition wins."""
from __future__ import annotations
import enum, threading, time
from dataclasses import dataclass, field


class OpState(str, enum.Enum):
    PENDING = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


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


class OperationStore:
    def __init__(self, approval_ttl_s: float = 900, lease_s: float = 30, clock=time.monotonic, store=None):
        self._ops: dict[tuple[str, str], Operation] = {}
        self._lock = threading.Lock()
        self.ttl, self.lease, self.clock, self.store = approval_ttl_s, lease_s, clock, store
        if store:  # recover durable state; stale EXECUTING ops surface via stale_executing() for reconciliation
            for t, o, b in store.load_ops():
                b["state"] = OpState(b["state"]); self._ops[(t, o)] = Operation(**b)

    def _save(self, op):
        if self.store:
            from dataclasses import asdict
            self.store.save_op(op.tenant_id, op.operation_id, asdict(op))

    def propose(self, tenant_id, operation_id, requester, tool, args, repairs=()) -> Operation:
        with self._lock:
            key = (tenant_id, operation_id)
            if key in self._ops:  # idempotent: duplicate returns stored operation
                return self._ops[key]
            op = Operation(tenant_id, operation_id, requester, tool, args,
                           expires_at=self.clock() + self.ttl, repairs=list(repairs))
            self._ops[key] = op
            self._save(op)
            return op

    def get(self, tenant_id, operation_id) -> Operation:
        op = self._ops.get((tenant_id, operation_id))
        if op is None:
            raise TxnError("not found")  # also covers cross-tenant probes
        return op

    def decide(self, tenant_id, operation_id, approver, approve: bool) -> Operation:
        with self._lock:
            op = self.get(tenant_id, operation_id)
            if op.state != OpState.PENDING:
                raise TxnError(f"cannot decide in state {op.state}")
            if self.clock() > op.expires_at:
                op.state = OpState.EXPIRED; self._save(op)
                raise TxnError("approval expired")
            if approver == op.requester:
                raise TxnError("requester cannot approve own operation")
            op.state = OpState.APPROVED if approve else OpState.DENIED
            self._save(op)
            return op

    def execute(self, tenant_id, operation_id, runner) -> Operation:
        with self._lock:  # atomic APPROVED -> EXECUTING
            op = self.get(tenant_id, operation_id)
            if op.state in (OpState.EXECUTED, OpState.FAILED):
                return op
            if op.state != OpState.APPROVED:
                raise TxnError(f"not executable in state {op.state}")
            op.state = OpState.EXECUTING
            op.lease_expires_at = self.clock() + self.lease
            self._save(op)
        try:
            res = runner(**op.args)
        except Exception as e:
            with self._lock:
                op.state, op.result = OpState.FAILED, str(e)  # never auto-retry
                self._save(op)
            return op
        with self._lock:
            op.state, op.result = OpState.EXECUTED, res
            self._save(op)
        return op

    def stale_executing(self) -> list[Operation]:
        """Reconciliation worker input: leases expired -> manual review."""
        now = self.clock()
        return [o for o in self._ops.values() if o.state == OpState.EXECUTING and o.lease_expires_at < now]
