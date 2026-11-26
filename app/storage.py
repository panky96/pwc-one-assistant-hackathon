"""Durable store: SQLite (dev/tests) or PostgreSQL (DATABASE_URL=postgresql://...), one schema.
Operations live here (not in process memory) so every uvicorn worker sees the same state.
All queries are tenant-scoped. Audit keeps payload hashes only, never raw payloads."""
from __future__ import annotations
import hashlib, json, sqlite3, threading, time

GENESIS = "0" * 64

SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS session_facts(tenant_id TEXT NOT NULL, session_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, verified INTEGER NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(tenant_id, session_id, key));
CREATE TABLE IF NOT EXISTS operations(tenant_id TEXT NOT NULL, operation_id TEXT NOT NULL, state TEXT NOT NULL, lease_expires_at REAL NOT NULL DEFAULT 0, body TEXT NOT NULL, PRIMARY KEY(tenant_id, operation_id));
CREATE TABLE IF NOT EXISTS audit_log(seq INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, ts REAL NOT NULL, event TEXT NOT NULL, payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""
SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS session_facts(tenant_id TEXT NOT NULL, session_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, verified INTEGER NOT NULL, created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(tenant_id, session_id, key));
CREATE TABLE IF NOT EXISTS operations(tenant_id TEXT NOT NULL, operation_id TEXT NOT NULL, state TEXT NOT NULL, lease_expires_at DOUBLE PRECISION NOT NULL DEFAULT 0, body TEXT NOT NULL, PRIMARY KEY(tenant_id, operation_id));
CREATE TABLE IF NOT EXISTS audit_log(seq BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL, event TEXT NOT NULL, payload_hash TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
"""


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _op_row(tenant_id, operation_id, state, lease, body_json) -> dict:
    d = json.loads(body_json)
    d.update({"tenant_id": tenant_id, "operation_id": operation_id, "state": state, "lease_expires_at": lease})
    return d


class Store:
    def __init__(self, url: str = ":memory:"):
        self.lock = threading.Lock()
        if url.startswith(("postgresql://", "postgres://")):
            import psycopg  # production path; untested without a live server
            self.pg = True
            self.db = psycopg.connect(url, autocommit=False)
            schema = SCHEMA_PG
        else:
            self.pg = False
            self.db = sqlite3.connect(url.removeprefix("sqlite:///") or ":memory:", check_same_thread=False)
            schema = SCHEMA_SQLITE
        with self.lock, self.db:
            for stmt in schema.split(";"):
                if stmt.strip():
                    self.db.execute(stmt)

    def _x(self, sql: str, args=()):
        if self.pg:
            sql = sql.replace("?", "%s")
        return self.db.execute(sql, args)

    # ---- session facts: verified, redacted facts only ----
    def put_fact(self, tenant_id, session_id, key, value, verified: bool) -> None:
        if not verified:
            raise ValueError("unverified model speculation never enters session_facts")
        with self.lock, self.db:
            self._x("INSERT INTO session_facts VALUES (?,?,?,?,1,?) ON CONFLICT(tenant_id, session_id, key) "
                    "DO UPDATE SET value=excluded.value, verified=1, created_at=excluded.created_at",
                    (tenant_id, session_id, key, value, time.time()))

    def facts(self, tenant_id, session_id) -> dict[str, str]:
        rows = self._x("SELECT key,value FROM session_facts WHERE tenant_id=? AND session_id=?", (tenant_id, session_id)).fetchall()
        return dict(rows)

    def sessions(self, tenant_id) -> list[str]:
        return [r[0] for r in self._x("SELECT DISTINCT session_id FROM session_facts WHERE tenant_id=? ORDER BY session_id",
                                      (tenant_id,)).fetchall()]

    # ---- durable operations (UNIQUE(tenant_id, operation_id)) ----
    def op_insert(self, tenant_id, operation_id, state, body: dict) -> bool:
        with self.lock, self.db:
            cur = self._x("INSERT INTO operations(tenant_id,operation_id,state,lease_expires_at,body) VALUES (?,?,?,0,?) "
                          "ON CONFLICT DO NOTHING", (tenant_id, operation_id, state, json.dumps(body, default=str)))
            return cur.rowcount == 1

    def op_get(self, tenant_id, operation_id) -> dict | None:
        rows = self._x("SELECT state, lease_expires_at, body FROM operations WHERE tenant_id=? AND operation_id=?",
                       (tenant_id, operation_id)).fetchall()
        return _op_row(tenant_id, operation_id, *rows[0]) if rows else None

    def op_transition(self, tenant_id, operation_id, from_states: tuple, to_state, body: dict, lease_expires_at=0.0) -> bool:
        """Atomic compare-and-set: succeeds only if the row is still in one of from_states."""
        ph = ",".join("?" * len(from_states))
        with self.lock, self.db:
            cur = self._x(f"UPDATE operations SET state=?, lease_expires_at=?, body=? WHERE tenant_id=? AND operation_id=? "
                          f"AND state IN ({ph})",
                          (to_state, lease_expires_at, json.dumps(body, default=str), tenant_id, operation_id, *from_states))
            return cur.rowcount == 1

    def op_list(self, tenant_id, state) -> list[dict]:
        rows = self._x("SELECT operation_id, state, lease_expires_at, body FROM operations WHERE tenant_id=? AND state=? "
                       "ORDER BY operation_id", (tenant_id, state)).fetchall()
        return [_op_row(tenant_id, o, s, l, b) for o, s, l, b in rows]

    def op_stale_executing(self, now: float) -> list[dict]:
        rows = self._x("SELECT tenant_id, operation_id, state, lease_expires_at, body FROM operations "
                       "WHERE state='EXECUTING' AND lease_expires_at < ?", (now,)).fetchall()
        return [_op_row(t, o, s, l, b) for t, o, s, l, b in rows]

    # ---- audit hash chain (metadata + payload_hash) ----
    def audit(self, tenant_id: str, event: str, payload: dict | str = "") -> str:
        ph = sha(payload if isinstance(payload, str) else json.dumps(payload, sort_keys=True, default=str))
        with self.lock, self.db:
            if self.pg:  # serialize chain appends across workers
                self._x("SELECT pg_advisory_xact_lock(hashtext(?))", (tenant_id,))
            row = self._x("SELECT hash FROM audit_log WHERE tenant_id=? ORDER BY seq DESC LIMIT 1", (tenant_id,)).fetchone()
            prev = row[0] if row else GENESIS
            ts = time.time()
            h = sha(f"{prev}|{tenant_id}|{ts}|{event}|{ph}")
            self._x("INSERT INTO audit_log(tenant_id,ts,event,payload_hash,prev_hash,hash) VALUES (?,?,?,?,?,?)",
                    (tenant_id, ts, event, ph, prev, h))
        return h

    def verify_chain(self, tenant_id: str) -> bool:
        prev = GENESIS
        rows = self._x("SELECT tenant_id,ts,event,payload_hash,prev_hash,hash FROM audit_log WHERE tenant_id=? ORDER BY seq",
                       (tenant_id,)).fetchall()
        for t, ts, ev, ph, pv, h in rows:
            if pv != prev or sha(f"{pv}|{t}|{ts}|{ev}|{ph}") != h:
                return False
            prev = h
        return True

    def audit_events(self, tenant_id: str) -> list[str]:
        return [r[0] for r in self._x("SELECT event FROM audit_log WHERE tenant_id=? ORDER BY seq", (tenant_id,)).fetchall()]
