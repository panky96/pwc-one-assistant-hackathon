from __future__ import annotations
import asyncio, contextlib, logging, os
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from app import telemetry
from app.agent import Agent
from app.auth import build_tenant_context
from app.bulkhead import Bulkhead, Overloaded
from app.cache import MemoryBackend, RedisBackend, SemanticCache
from app.contracts import ChatRequest, ChatResponse, TenantContext
from app.llm import FallbackLLM, OpenRouterClient
from app.runtime_config import RuntimeConfig
from app.settings import Settings
from app.storage import Store
from app.tools import build_harness
from app.transactions import OperationStore, OpState, TxnError
from app.vault import Graph, load_vault, vault_chunks
from mcp_servers.rag_server import HybridRetriever
from plugins.registry import build_registry

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("pwc.app")


def _retriever(chunks, rc):
    kw = dict(rrf_k=rc.rrf_k, fused_k=rc.fused_k, min_score=rc.min_score)
    backend = os.getenv("RETRIEVAL_BACKEND", rc.retrieval_backend)
    if backend == "onnx":  # pinned design models (BGE encoder + bge-reranker-v2-m3) via fastembed/ONNX
        from mcp_servers.onnx_models import OnnxEncoder, OnnxReranker
        return HybridRetriever(chunks, encoder=OnnxEncoder(), reranker=OnnxReranker(), **kw)
    return HybridRetriever(chunks, **kw)


async def _reconcile_forever(ops: OperationStore, interval_s: float):
    """Scheduled reconciliation: recovers stale EXECUTING operations to NEEDS_REVIEW. Safe to run in every worker
    because each transition is a conditional update."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            flagged = await asyncio.to_thread(ops.reconcile)
            if flagged:
                telemetry.incr("ops.needs_review", len(flagged))
                log.warning("operations need manual review: %s", flagged)
        except Exception:
            telemetry.incr("reconcile.errors")
            log.exception("reconciliation failed")


def create_app(settings: Settings | None = None, verifier=None, chunks=None, llm="auto", store: Store | None = None,
               use_vault: bool = False, graphify: bool = False, cache: bool = False, mcp_config=None) -> FastAPI:
    if settings is None:
        load_dotenv()
        settings = Settings.from_env()  # raises -> startup failure (incl. production DB guard)
    rc = RuntimeConfig.load(mcp_config or os.getenv("MCP_CONFIG") or ROOT / "mcp_config.json", settings)
    telemetry.init_otel(settings.otlp_endpoint, settings.service_name)
    notes = load_vault() if (use_vault or graphify) else {}  # approved knowledge only
    allc = list(chunks or []) + (vault_chunks(notes, "*") if use_vault else [])
    if llm == "auto":
        primary = OpenRouterClient(settings.openrouter_api_key, settings.openrouter_base_url) if settings.openrouter_api_key else None
        secondary = (OpenRouterClient(settings.groq_api_key, settings.groq_base_url)
                     if settings.groq_api_key and settings.groq_model else None)
        llm = FallbackLLM(primary, secondary, settings.groq_model) if secondary is not None else primary
    store = store or Store(settings.database_url or ":memory:")
    ops = OperationStore(store=store)
    harness = build_harness(notes)
    registry = build_registry(rc.plugins, {"harness": harness, "min_confidence": rc.min_confidence})
    sc = None
    if cache:
        backend = RedisBackend(settings.redis_url) if (os.getenv("CACHE_BACKEND") == "redis" and settings.redis_url) else MemoryBackend()
        sc = SemanticCache(backend, threshold=rc.cache_threshold, ttl=rc.cache_ttl)
    pools = {name: Bulkhead(name, size, rc.bulkhead_wait_s) for name, size in rc.bulkheads.items()}
    agent = Agent(registry, _retriever(allc, rc), ops, harness, llm=llm, store=store, cache=sc,
                  graph=Graph(notes) if graphify else None, models=(settings.model_small, settings.model_large),
                  pools=pools, rc=rc, versions=rc.governance)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        task = (asyncio.create_task(_reconcile_forever(ops, rc.reconcile_interval_s))
                if rc.reconcile_interval_s > 0 else None)
        try:
            yield
        finally:
            if task:
                task.cancel()

    app = FastAPI(title=settings.service_name, lifespan=lifespan)
    app.state.agent, app.state.ops, app.state.store = agent, ops, store

    @app.exception_handler(Overloaded)
    async def _shed(request: Request, exc: Overloaded):
        return JSONResponse({"detail": "overloaded, retry later"}, status_code=429, headers={"Retry-After": "1"})

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

    @app.get("/v1/operations")
    def list_pending(t: TenantContext = Depends(tenant)):
        if "approver" not in t.roles:
            raise HTTPException(403, "approver role required")
        return [{"operation_id": o.operation_id, "tool": o.tool, "args": o.args, "repairs": o.repairs,
                 "requester": o.requester, "state": o.state} for o in ops.pending(t.tenant_id)]

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
            op = ops.execute(t.tenant_id, op_id, harness.tools[op.tool].fn)
            store.audit(t.tenant_id, f"operation:{op.state.value.lower()}", op_id)
        return {"state": op.state, "result": op.result}

    @app.post("/v1/admin/reconcile")
    def reconcile(t: TenantContext = Depends(tenant)):
        if "approver" not in t.roles:
            raise HTTPException(403, "approver role required")
        flagged = ops.reconcile()
        store.audit(t.tenant_id, "operation:reconcile", {"flagged": flagged})
        return {"needs_review": flagged}

    return app
