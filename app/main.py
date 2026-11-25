from __future__ import annotations
import os
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from app import telemetry
from app.agent import Agent
from app.auth import build_tenant_context
from app.cache import MemoryBackend, RedisBackend, SemanticCache
from app.contracts import ChatRequest, ChatResponse, TenantContext
from app.llm import OpenRouterClient
from app.plugins_core import PluginRegistry
from app.settings import Settings
from app.storage import Store
from app.tools import build_harness
from app.transactions import OperationStore, OpState, TxnError
from app.vault import Graph, load_vault, vault_chunks
from mcp_servers.rag_server import HybridRetriever, hash_embed
from plugins.lifecycle import dispatch_plugins, post_plugins
from plugins.safety import default_plugins


def create_app(settings: Settings | None = None, verifier=None, chunks=None, llm="auto", store: Store | None = None,
               use_vault: bool = False, graphify: bool = False, cache: bool = False) -> FastAPI:
    if settings is None:
        load_dotenv(); settings = Settings.from_env()  # raises -> startup failure
    telemetry.init_otel(settings.otlp_endpoint, settings.service_name)
    notes = load_vault() if (use_vault or graphify) else {}
    allc = list(chunks or []) + (vault_chunks(notes, "*") if use_vault else [])
    reg = PluginRegistry()
    for p in default_plugins() + dispatch_plugins() + post_plugins():
        reg.register(p)
    if llm == "auto":
        llm = OpenRouterClient(settings.openrouter_api_key, settings.openrouter_base_url) if settings.openrouter_api_key else None
    if store is None:
        path = settings.database_url.removeprefix("sqlite:///") if settings.database_url.startswith("sqlite:///") else ":memory:"
        store = Store(path)
    ops = OperationStore(store=store)
    harness = build_harness(notes)
    sc = None
    if cache:
        sc = SemanticCache(RedisBackend(settings.redis_url) if settings.redis_url and os.getenv("USE_REDIS") == "1" else MemoryBackend())
    if os.getenv("RETRIEVAL_BACKEND") == "onnx":  # design models: bge encoder + bge-reranker-v2-m3 (ONNX)
        from mcp_servers.onnx_models import OnnxEncoder, OnnxReranker
        retriever = HybridRetriever(allc, encoder=OnnxEncoder(), reranker=OnnxReranker())
    else:
        retriever = HybridRetriever(allc)
    agent = Agent(reg, retriever, ops, harness, llm=llm, store=store, cache=sc,
                  graph=Graph(notes) if graphify else None, models=(settings.model_small, settings.model_large))
    app = FastAPI(title=settings.service_name)
    app.state.agent, app.state.ops, app.state.store = agent, ops, store

    def tenant(request: Request) -> TenantContext:
        return build_tenant_context(request, settings, verifier)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/metrics/spans")
    def spans(t: TenantContext = Depends(tenant)):
        return telemetry.snapshot()

    @app.post("/v1/chat", response_model=ChatResponse)
    def chat(req: ChatRequest, t: TenantContext = Depends(tenant)):
        return agent.handle(req.message, t, req.session_id, req.operation_id)

    @app.get("/v1/operations/{op_id}")
    def get_op(op_id: str, t: TenantContext = Depends(tenant)):
        try:
            op = ops.get(t.tenant_id, op_id)  # tenant-scoped: other tenants get 404
        except TxnError:
            raise HTTPException(404, "not found")
        return {"state": op.state, "tool": op.tool, "args": op.args, "repairs": op.repairs}

    @app.post("/v1/operations/{op_id}/decision")
    def decide(op_id: str, approve: bool, t: TenantContext = Depends(tenant)):
        if "approver" not in t.roles:
            raise HTTPException(403, "approver role required")
        try:
            op = ops.decide(t.tenant_id, op_id, t.user_id, approve)
        except TxnError as e:
            raise HTTPException(404 if "not found" in str(e) else 409, str(e))
        store.audit(t.tenant_id, f"operation:{'approved' if approve else 'denied'}", op_id)
        if op.state == OpState.APPROVED:
            tool = harness.tools[op.tool]
            op = ops.execute(t.tenant_id, op_id, tool.fn)
            store.audit(t.tenant_id, f"operation:{op.state.value.lower()}", op_id)
        return {"state": op.state, "result": op.result}

    return app
