"""MCP server `state-store` (Streamable HTTP): tenant-scoped audit/facts/operations over the durable Store.
Run: STATE_PORT=8765 python -m mcp_servers.state_mcp"""
import os
from mcp.server.fastmcp import FastMCP
from app.storage import Store

mcp = FastMCP("state-store", host="127.0.0.1", port=int(os.getenv("STATE_PORT", "8765")))
_store = Store(os.getenv("STATE_DB", ":memory:"))


@mcp.tool()
def audit_append(tenant_id: str, event: str, payload: str = "") -> str:
    return _store.audit(tenant_id, event, payload)


@mcp.tool()
def audit_verify(tenant_id: str) -> bool:
    return _store.verify_chain(tenant_id)


@mcp.tool()
def put_fact(tenant_id: str, session_id: str, key: str, value: str) -> bool:
    _store.put_fact(tenant_id, session_id, key, value, verified=True)
    return True


@mcp.tool()
def get_facts(tenant_id: str, session_id: str) -> dict:
    return _store.facts(tenant_id, session_id)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
