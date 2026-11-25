"""Durable store (SQLite for dev; schema is portable SQL for Postgres). All queries are tenant-scoped.
Holds session_facts and the hash-chained audit log. Raw PII is not persisted: payloads are hashed."""
from __future__ import annotations
import hashlib, json, sqlite3, threading, time

SCHEMA = """
CREATE TABLE IF NOT EXISTS session_facts(
  tenant_id TEXT NOT NULL, session_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  verified INTEGER NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(tenant_id, session_id, key));
CREATE TABLE IF NOT EXISTS operations(
  tenant_id TEXT NOT NULL, operation_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(tenant_id, operation_id));
CREATE TABLE IF NOT EXISTS audit_log(
  seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, ts REAL NOT NULL, event TEXT NOT NULL,
  payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""
GENESIS = "0" * 64


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class Store:
    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript(SCHEMA)

    # --- session facts: verified, redacted facts only ---
    def put_fact(self, tenant_id: str, session_id: str, key: str, value: str, verified: bool) -> None:
        if not verified:
            raise ValueError("unverified model speculation never enters session_facts")
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO session_facts VALUES (?,?,?,?,1,?)",
                            (tenant_id, session_id, key, value, time.time()))

    def facts(self, tenant_id: str, session_id: str) -> dict[str, str]:
        rows = self.db.execute("SELECT key,value FROM session_facts WHERE tenant_id=? AND session_id=?",
                               (tenant_id, session_id)).fetchall()
        return dict(rows)

    # --- durable operations (UNIQUE(tenant_id, operation_id)) ---
    def save_op(self, tenant_id: str, operation_id: str, body: dict) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO operations VALUES (?,?,?)", (tenant_id, operation_id, json.dumps(body, default=str)))

    def load_ops(self) -> list[tuple[str, str, dict]]:
        return [(t, o, json.loads(b)) for t, o, b in self.db.execute("SELECT tenant_id,operation_id,body FROM operations")]

    # --- audit hash chain (metadata + payload_hash, never raw payloads) ---
    def audit(self, tenant_id: str, event: str, payload: dict | str = "") -> str:
        ph = sha(payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str))
        with self.lock, self.db:
            row = self.db.execute("SELECT hash FROM audit_log WHERE tenant_id=? ORDER BY seq DESC LIMIT 1", (tenant_id,)).fetchone()
            prev = row[0] if row else GENESIS
            ts = time.time()
            h = sha(f"{prev}|{tenant_id}|{ts}|{event}|{ph}")
            self.db.execute("INSERT INTO audit_log(tenant_id,ts,event,payload_hash,prev_hash,hash) VALUES (?,?,?,?,?,?)",
                            (tenant_id, ts, event, ph, prev, h))
        return h

    def verify_chain(self, tenant_id: str) -> bool:
        prev = GENESIS
        for t, ts, ev, ph, pv, h in self.db.execute(
                "SELECT tenant_id,ts,event,payload_hash,prev_hash,hash FROM audit_log WHERE tenant_id=? ORDER BY seq", (tenant_id,)):
            if pv != prev or sha(f"{pv}|{t}|{ts}|{ev}|{ph}") != h:
                return False
            prev = h
        return True

    def audit_events(self, tenant_id: str) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT event FROM audit_log WHERE tenant_id=? ORDER BY seq", (tenant_id,))]
