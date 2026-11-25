"""MCP host client: connects configured servers (stdio / streamable-http), retries, per-call timeout, JSON-RPC overhead timer."""
from __future__ import annotations
import asyncio, json
from contextlib import AsyncExitStack
from app import telemetry
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


class McpHost:
    def __init__(self, cfg: dict, only: set[str] | None = None, cwd: str | None = None):
        self.cfg, self.only, self.cwd = cfg, only, cwd
        self.stack, self.sessions = AsyncExitStack(), {}

    async def __aenter__(self):
        for name, s in self.cfg["mcpServers"].items():
            if not s.get("enabled", True) or (self.only and name not in self.only):
                continue
            if s["transport"] == "stdio":
                params = StdioServerParameters(command=s["command"], args=s["args"], cwd=self.cwd)
                r, w = await self.stack.enter_async_context(stdio_client(params))
            else:
                if not s.get("url"):
                    raise RuntimeError(f"{name}: url not configured (STATE_STORE_URL)")
                r, w, _ = await self.stack.enter_async_context(streamablehttp_client(s["url"]))
            sess = await self.stack.enter_async_context(ClientSession(r, w))
            await asyncio.wait_for(sess.initialize(), timeout=self.cfg["defaults"]["startup_timeout_ms"] / 1000)
            self.sessions[name] = sess
        return self

    async def __aexit__(self, *a):
        await self.stack.aclose()

    async def call(self, server: str, tool: str, args: dict):
        s = self.cfg["mcpServers"][server]
        attempts = s.get("retry", {}).get("max_attempts", 1) if s.get("retry", {}).get("enabled") else 1
        last = None
        for i in range(max(1, attempts)):
            try:
                with telemetry.span("mcp.call", server=server, tool=tool):
                    res = await asyncio.wait_for(self.sessions[server].call_tool(tool, args), timeout=s.get("timeout_ms", 30000) / 1000)
                if res.isError:
                    raise RuntimeError(res.content[0].text if res.content else "tool error")
                return res.structuredContent if getattr(res, "structuredContent", None) else json.loads(res.content[0].text)
            except Exception as e:
                last = e
                await asyncio.sleep(s.get("retry", {}).get("backoff_ms", 0) / 1000 * (i + 1))
        raise last
