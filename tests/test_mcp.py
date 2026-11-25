import asyncio, os, socket, subprocess, sys, time
import pytest
from app.mcp_config import load_config
from app.mcp_host import McpHost
from app.settings import ConfigError

ROOT = os.path.dirname(os.path.dirname(__file__))


def test_config_valid_and_strict():
    cfg = load_config(os.path.join(ROOT, "mcp_config.json"), env={"APP_ENV": "development", "AUTH_ENABLED": "false", "SERVICE_NAME": "x"})
    assert cfg["retrieval"]["timeout_ms"] == 150 and cfg["observability"]["otel"]["service_name"] == "x"
    with pytest.raises(ConfigError):
        load_config(os.path.join(ROOT, "mcp_config.json"), env={"APP_ENV": "development", "AUTH_ENABLED": "TRUEE"})


def test_config_rejects_unknown_server(tmp_path):
    import json
    cfg = json.load(open(os.path.join(ROOT, "mcp_config.json")))
    cfg["mcpServers"]["evil"] = {"enabled": True, "command": "x", "args": [], "transport": "stdio"}
    p = tmp_path / "c.json"; p.write_text(json.dumps(cfg))
    with pytest.raises(ConfigError):
        load_config(p, env={"APP_ENV": "development", "AUTH_ENABLED": "false"})


def _cfg():
    cfg = load_config(os.path.join(ROOT, "mcp_config.json"), env={"APP_ENV": "development", "AUTH_ENABLED": "false"})
    for s in cfg["mcpServers"].values():
        if s["transport"] == "stdio": s["command"], s["args"] = sys.executable, s["args"][2:]  # python -m module
    return cfg


def test_stdio_rag_and_graphify_servers():
    async def go():
        cfg = _cfg()
        async with McpHost(cfg, only={"hybrid-rag", "graphify-vault"}, cwd=ROOT) as h:
            r = await h.call("hybrid-rag", "hybrid_retrieve", {"query": "meal expense per day", "tenant_id": "T"})
            assert not r["abstain"] and r["chunks"][0]["id"] == "expense-meals"
            r = await h.call("hybrid-rag", "hybrid_retrieve", {"query": "quantum chromodynamics lattice", "tenant_id": "T"})
            assert r["abstain"]
            g = await h.call("graphify-vault", "subgraph", {"query": "meal expense policy"})
            assert g["anchor"] == "expense-meals"
            assert (await h.call("graphify-vault", "subgraph", {"query": "zzz"}))["anchor"] is None
    asyncio.run(asyncio.wait_for(go(), 60))


def test_streamable_http_state_server():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    p = subprocess.Popen([sys.executable, "-m", "mcp_servers.state_mcp"], cwd=ROOT, env={**os.environ, "STATE_PORT": str(port)},
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: socket.create_connection(("127.0.0.1", port), 0.2).close(); break
            except OSError: time.sleep(0.2)
        cfg = _cfg(); cfg["mcpServers"]["state-store"]["url"] = f"http://127.0.0.1:{port}/mcp"
        async def go():
            async with McpHost(cfg, only={"state-store"}) as h:
                await h.call("state-store", "audit_append", {"tenant_id": "A", "event": "x"})
                assert await h.call("state-store", "audit_verify", {"tenant_id": "A"}) in (True, {"result": True})
                await h.call("state-store", "put_fact", {"tenant_id": "A", "session_id": "s", "key": "k", "value": "v"})
                assert await h.call("state-store", "get_facts", {"tenant_id": "B", "session_id": "s"}) in ({}, {"result": {}})
        asyncio.run(asyncio.wait_for(go(), 60))
    finally:
        p.terminate()


def test_production_config_requires_auth():
    with pytest.raises(ConfigError):
        load_config(os.path.join(ROOT, "mcp_config.json"), env={"AUTH_ENABLED": "false"})
