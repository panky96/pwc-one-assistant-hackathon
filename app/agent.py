"""Agent turn orchestration: PRE plugins -> triage -> route -> (cache, retrieval loop, generation, verification, fallback)."""
from __future__ import annotations
import contextvars, re, uuid
from app import telemetry
from app.contracts import ChatResponse, Citation, HybridRetrieveInput, Route, TenantContext, Tier
from app.llm import LLMError, build_messages, goal_anchor
from app.loop import BoundedLoop, Final, ToolCall, TurnState
from app.plugins_core import Blocked, PluginRegistry, Stage
from app.tools import extract_args, select_tool
from app.transactions import OperationStore
from app.triage import apply_confidence_policy, triage
from app.verification import verify
from app.harness import Tool
from app.vault import Graph, ntokens, pruned_context
from mcp_servers.rag_server import HybridRetriever, tokenize
from plugins.safety import scan_injection

_tenant_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant")
_ID = re.compile(r"\b[A-Z]{3}-\d{3,6}\b|\b\d+\b")
ABSTAIN = "I don't have sufficient evidence in the knowledge base to answer that."


class Agent:
    def __init__(self, registry: PluginRegistry, retriever: HybridRetriever, ops: OperationStore, harness, llm=None,
                 store=None, cache=None, graph: Graph | None = None, models=("small", "large")):
        self.registry, self.retriever, self.ops, self.h = registry, retriever, ops, harness
        self.llm, self.store, self.cache, self.graph, self.models = llm, store, cache, graph, models
        self.h.register(Tool("hybrid_retrieve", Tier.READ, HybridRetrieveInput,
                             lambda **kw: self.retriever.retrieve(kw["query"], _tenant_var.get(), kw["candidate_k"], kw["output_k"])))
        self.last_token_stats: tuple[int, int] | None = None

    def _audit(self, t, event, payload=""):
        if self.store: self.store.audit(t.tenant_id, event, payload)

    def handle(self, message: str, tenant: TenantContext, session_id: str | None = None, operation_id: str | None = None) -> ChatResponse:
        telemetry.incr("requests")
        try:
            with telemetry.span("hook.pre"):
                ctx = self.registry.run(Stage.PRE, {"message": message, "tenant": tenant})
        except Blocked as b:
            telemetry.incr("blocked"); self._audit(tenant, f"blocked:{b.plugin}", message)
            return ChatResponse(route=Route.REJECT_OOS, answer=f"Request blocked ({b.plugin}).", abstained=True)
        msg = ctx["message"]
        with telemetry.span("triage"):
            t = apply_confidence_policy(triage(msg))
        self._audit(tenant, f"route:{t.route.value}", msg)
        if t.clarify:
            return ChatResponse(route=t.route, answer="Could you clarify what you want to do?", clarification=["intent", "target entity"])
        if t.route == Route.REJECT_OOS:
            return ChatResponse(route=t.route, answer="That is outside what I can help with.", abstained=True)
        if t.route == Route.DIRECT_ANSWER:
            return ChatResponse(route=t.route, answer="Hello! How can I help?")
        if t.route == Route.TRANSACTION_EXEC:
            return self._transaction(msg, tenant, operation_id, session_id)
        return self._rag(msg, tenant, session_id)

    # ---- TRANSACTION_EXEC ----
    def _transaction(self, msg, tenant, operation_id, session_id) -> ChatResponse:
        try:
            self.registry.run(Stage.DISPATCH, {"operation_id": operation_id})
        except Blocked as b:
            return ChatResponse(route=Route.TRANSACTION_EXEC, answer=f"Rejected ({b.plugin}).", abstained=True)
        tool = select_tool(msg)
        if not tool:
            return ChatResponse(route=Route.TRANSACTION_EXEC, answer="Which action do you want to perform?", clarification=["action"])
        args, missing = extract_args(tool, msg, self.h)
        if missing:  # ambiguity: never guess, never call downstream
            return ChatResponse(route=Route.TRANSACTION_EXEC, answer="I need more details before I can prepare this action.", clarification=missing)
        loop = BoundedLoop(self.h, tenant)
        res = loop.run(lambda obs: ToolCall(tool, args))
        if res.state != TurnState.AWAITING_APPROVAL:
            return ChatResponse(route=Route.TRANSACTION_EXEC, answer=f"Could not prepare the action ({res.state.value}).", abstained=True)
        op = self.ops.propose(tenant.tenant_id, operation_id or str(uuid.uuid4()), tenant.user_id, res.pending.name, res.pending.args, res.repairs)
        self._audit(tenant, "operation:proposed", {"op": op.operation_id, "tool": op.tool})
        if session_id and self.store:
            for k, v in res.pending.args.items():
                self.store.put_fact(tenant.tenant_id, session_id, k, str(v), verified=True)  # regex-validated user input
        note = f" (auto-repaired: {res.repairs})" if res.repairs else ""
        return ChatResponse(route=Route.TRANSACTION_EXEC, answer=f"Pending human approval; nothing has been executed{note}.", operation_id=op.operation_id)

    # ---- HYBRID_RAG ----
    def _rag(self, msg, tenant, session_id) -> ChatResponse:
        entities = sorted(set(_ID.findall(msg)))
        ident = None
        if self.cache:
            from app.cache import cache_identity
            ident = cache_identity(tenant.tenant_id, "v1", "policy_qa", entities, [], ",".join(sorted(tenant.roles)))
            hit = self.cache.get(tenant.tenant_id, ident, msg)
            if hit: telemetry.incr("cache.hit"); return ChatResponse(**hit)
        _tenant_var.set(tenant.tenant_id)
        loop = BoundedLoop(self.h, tenant)
        with telemetry.span("retrieval"):
            res = loop.run(lambda obs: ToolCall("hybrid_retrieve", {"query": msg}) if not obs else Final("ok"))
        telemetry.incr("loop.steps", res.steps); telemetry.incr("loop.tasks")
        if res.state != TurnState.DONE or not res.raw:
            return ChatResponse(route=Route.HYBRID_RAG, answer=ABSTAIN, abstained=True)
        r = res.raw[0]
        if r.abstain:
            telemetry.incr("abstain.retrieval")
            return ChatResponse(route=Route.HYBRID_RAG, answer=ABSTAIN, abstained=True)
        safe = [(c, s) for c, s in r.chunks if not scan_injection(c.text)]  # indirect-injection defense on retrieved data
        if not safe:
            return ChatResponse(route=Route.HYBRID_RAG, answer=ABSTAIN, abstained=True)
        context = [(c.id, c.text) for c, _ in safe]
        if self.graph and safe[0][0].id in self.graph.notes:  # optional Graphify pruning; falls back to RAG context
            pc = pruned_context(self.graph, msg, safe[0][0].id)
            self.last_token_stats = (sum(ntokens(t) for _, t in context), sum(ntokens(t) for _, t in pc))
            context = pc
        answer = self._generate(msg, tenant, context, entities)
        if answer is None:
            return ChatResponse(route=Route.HYBRID_RAG, answer=ABSTAIN, abstained=True)
        out = ChatResponse(route=Route.HYBRID_RAG, answer=answer, citations=[Citation(chunk_id=c.id, score=s) for c, s in safe])
        if self.cache and ident: self.cache.put(tenant.tenant_id, ident, msg, out.model_dump(mode="json"))
        return out

    def _generate(self, msg, tenant, context, entities) -> str | None:
        texts = [t for _, t in context]
        if self.llm is None:  # extractive path (no model configured)
            q = set(tokenize(msg))
            sents = [s for t in texts[:1] for s in re.split(r"(?<=\.)\s+", t)]
            best = sorted(sents, key=lambda s: -len(q & set(tokenize(s))))[:2]
            return " ".join(best) if best else None
        anchor = goal_anchor(msg, entities)
        pctx: dict = {"escalations": 0}
        for model in (self.models[0], self.models[1]):
            try:
                with telemetry.span("llm.generate", model=model):
                    gen = self.llm.complete(build_messages(anchor, context, msg), model).text
            except LLMError:
                telemetry.incr("llm.errors"); return None
            if gen.strip().upper().startswith("INSUFFICIENT"):
                return None
            with telemetry.span("verify"):
                v = verify(gen, texts)
            pctx.update(answer=gen, chunks=texts, verdict=v)
            pctx = self.registry.run(Stage.POST, pctx)
            if v.ok:
                return gen
            if not pctx.get("escalate"):
                break
            pctx["escalations"] += 1; telemetry.incr("model.escalations")
        telemetry.incr("verification.vetoed")
        return None
