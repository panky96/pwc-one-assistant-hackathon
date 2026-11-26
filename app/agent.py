"""Agent turn orchestration: PRE plugins -> risk flag -> triage -> route -> (retrieval loop, generation, verification).
Every answer carries request_id, response_mode and a degraded flag. Verification fails closed."""
from __future__ import annotations
import contextlib, contextvars, json, logging, re, uuid
from app import telemetry
from app.cache import cache_identity
from app.context import ReactContext
from app.contracts import ChatResponse, Citation, HybridRetrieveInput, Route, TenantContext, Tier
from app.harness import Tool
from app.llm import LLMError, build_messages, goal_anchor
from app.loop import BoundedLoop, Final, ToolCall, TurnState
from app.plugins_core import Blocked, PluginRegistry, Stage
from app.subagents import EXECUTION, RETRIEVAL
from app.tools import extract_args, select_tool
from app.transactions import OperationStore
from app.triage import MIN_CONFIDENCE, apply_confidence_policy, triage
from app.verification import Verdict, verify
from app.vault import Graph, ntokens, pruned_context
from mcp_servers.rag_server import HybridRetriever, tokenize
from plugins.safety import scan_injection

log = logging.getLogger("pwc.decision")
_tenant_var: contextvars.ContextVar[str] = contextvars.ContextVar("tenant")
_ID = re.compile(r"\b[A-Z]{3}-\d{3,6}\b|\b\d+\b")
ABSTAIN = "I don't have sufficient evidence in the knowledge base to answer that."
ESCALATED = "This request is outside what this assistant can answer safely. It has been escalated for human review."


class Agent:
    def __init__(self, registry: PluginRegistry, retriever: HybridRetriever, ops: OperationStore, harness, llm=None,
                 store=None, cache=None, graph: Graph | None = None, models=("small", "large"), pools=None, rc=None,
                 versions=None):
        self.registry, self.retriever, self.ops, self.h = registry, retriever, ops, harness
        self.llm, self.store, self.cache, self.graph, self.models = llm, store, cache, graph, models
        self.pools = pools or {}
        self.min_conf = rc.min_confidence if rc else MIN_CONFIDENCE
        self.loop_kw = rc.loop_kwargs() if rc else {}
        self.versions = dict(versions or {})
        self.h.register(Tool("hybrid_retrieve", Tier.READ, HybridRetrieveInput,
                             lambda **kw: self.retriever.retrieve(kw["query"], _tenant_var.get(), kw["candidate_k"], kw["output_k"])))
        self.last_token_stats: tuple[int, int] | None = None
        self.last_trace: list = []
        self.last_obs: list = []

    def _pool(self, name: str):
        b = self.pools.get(name)
        return b() if b else contextlib.nullcontext()

    def _audit(self, t, event, payload=""):
        if self.store:
            self.store.audit(t.tenant_id, event, payload)

    @staticmethod
    def _resp(route, risk, answer, mode, **kw) -> ChatResponse:
        return ChatResponse(route=route, answer=answer, risk=risk, response_mode=mode, **kw)

    def handle(self, message: str, tenant: TenantContext, session_id: str | None = None, operation_id: str | None = None) -> ChatResponse:
        request_id = uuid.uuid4().hex
        resp = self._handle(message, tenant, session_id, operation_id, request_id)
        resp.request_id = request_id
        return resp

    def _handle(self, message, tenant, session_id, operation_id, request_id) -> ChatResponse:
        telemetry.incr("requests")
        try:
            with telemetry.span("hook.pre"):
                ctx = self.registry.run(Stage.PRE, {"message": message, "tenant": tenant})
        except Blocked as b:
            telemetry.incr("blocked")
            self._audit(tenant, f"blocked:{b.plugin}", message)
            return ChatResponse(route=Route.REJECT_OOS, answer=f"Request blocked ({b.plugin}).", abstained=True, response_mode="blocked")
        msg = ctx["message"]
        with telemetry.span("triage"):
            t = ctx.get("triage") or apply_confidence_policy(triage(msg), self.min_conf)
        self._audit(tenant, f"route:{t.route.value}", msg)
        if t.risk != "NORMAL":  # healthcare safety boundary: escalate, never answer
            telemetry.incr("risk.escalations")
            self._audit(tenant, f"escalated:{t.risk}", {"request_id": request_id})
            log.info("escalated risk=%s request_id=%s", t.risk, request_id)
            return self._resp(t.route, t.risk, ESCALATED, "escalated", abstained=True)
        if t.clarify:
            return self._resp(t.route, t.risk, "Could you clarify what you want to do?", "clarification",
                              clarification=["intent", "target entity"])
        if t.route == Route.TRANSACTION_EXEC and ctx.get("clarification"):
            return self._resp(t.route, t.risk, "I need more details before I can prepare this action.", "clarification",
                              clarification=ctx["clarification"])
        if t.route == Route.REJECT_OOS:
            return self._resp(t.route, t.risk, "That is outside what I can help with.", "out_of_scope", abstained=True)
        if t.route == Route.DIRECT_ANSWER:
            return self._resp(t.route, t.risk, "Hello! How can I help?", "direct")
        if t.route == Route.TRANSACTION_EXEC:
            return self._transaction(msg, tenant, operation_id, session_id, t.risk)
        return self._rag(msg, tenant, session_id, t.risk, request_id)

    # ---- TRANSACTION_EXEC (execution subagent) ----
    def _transaction(self, msg, tenant, operation_id, session_id, risk) -> ChatResponse:
        try:
            self.registry.run(Stage.DISPATCH, {"operation_id": operation_id})
        except Blocked as b:
            return self._resp(Route.TRANSACTION_EXEC, risk, f"Rejected ({b.plugin}).", "rejected", abstained=True)
        tool = select_tool(msg)
        if not tool:
            return self._resp(Route.TRANSACTION_EXEC, risk, "Which action do you want to perform?", "clarification", clarification=["action"])
        args, missing = extract_args(tool, msg, self.h)
        if missing:  # ambiguity: never guess, never call downstream
            return self._resp(Route.TRANSACTION_EXEC, risk, "I need more details before I can prepare this action.", "clarification", clarification=missing)
        loop = BoundedLoop(self.h, tenant, subagent=EXECUTION, **self.loop_kw)
        res = loop.run(lambda obs: ToolCall(tool, args))
        if res.state != TurnState.AWAITING_APPROVAL:
            return self._resp(Route.TRANSACTION_EXEC, risk, f"Could not prepare the action ({res.state.value}).", "rejected", abstained=True)
        op = self.ops.propose(tenant.tenant_id, operation_id or str(uuid.uuid4()), tenant.user_id, res.pending.name, res.pending.args, res.repairs)
        self._audit(tenant, "operation:proposed", {"op": op.operation_id, "tool": op.tool})
        if session_id and self.store:
            for k, v in res.pending.args.items():
                self.store.put_fact(tenant.tenant_id, session_id, k, str(v), verified=True)  # regex-validated user input
        note = f" (auto-repaired: {res.repairs})" if res.repairs else ""
        return self._resp(Route.TRANSACTION_EXEC, risk, f"Pending human approval; nothing has been executed{note}.",
                          "pending_approval", operation_id=op.operation_id)

    # ---- HYBRID_RAG (retrieval subagent) ----
    def _rag(self, msg, tenant, session_id, risk, request_id) -> ChatResponse:
        entities = sorted(set(_ID.findall(msg)))
        ident = None
        if self.cache:
            ident = cache_identity(tenant.tenant_id, "v1", "policy_qa", entities, [], ",".join(sorted(tenant.roles)))
            hit = self.cache.get(tenant.tenant_id, ident, msg)
            if hit:
                telemetry.incr("cache.hit")
                return ChatResponse(**hit)
        _tenant_var.set(tenant.tenant_id)
        rctx = ReactContext(goal_anchor(msg, entities))
        loop = BoundedLoop(self.h, tenant, subagent=RETRIEVAL, context=rctx, **self.loop_kw)
        with telemetry.span("retrieval"), self._pool("rag_pool"):
            res = loop.run(lambda obs: ToolCall("hybrid_retrieve", {"query": msg}) if not obs else Final("ok"))
        self.last_trace, self.last_obs = res.trace, res.observations
        telemetry.incr("loop.steps", res.steps)
        telemetry.incr("loop.tasks")
        d = rctx.drift(msg)
        if d is not None:
            telemetry.incr("drift.sum", d)
            telemetry.incr("drift.n")
        if res.state != TurnState.DONE or not res.raw:
            return self._resp(Route.HYBRID_RAG, risk, ABSTAIN, "abstained_retrieval", abstained=True)
        r = res.raw[0]
        if r.abstain:
            telemetry.incr("abstain.retrieval")
            return self._resp(Route.HYBRID_RAG, risk, ABSTAIN, "abstained_retrieval", abstained=True)
        safe = [(c, s) for c, s in r.chunks if not scan_injection(c.text)]  # indirect-injection defense on retrieved data
        if not safe:
            return self._resp(Route.HYBRID_RAG, risk, ABSTAIN, "abstained_retrieval", abstained=True)
        context = [(c.id, c.text) for c, _ in safe]
        if self.graph and safe[0][0].id in self.graph.notes:  # optional Graphify pruning
            pc = pruned_context(self.graph, msg, safe[0][0].id)
            self.last_token_stats = (sum(ntokens(t) for _, t in context), sum(ntokens(t) for _, t in pc))
            context = pc
        answer, mode, model = self._generate(msg, tenant, context, entities)
        if answer is None:
            return self._resp(Route.HYBRID_RAG, risk, ABSTAIN, mode, abstained=True)
        degraded = mode == "extractive"
        out = self._resp(Route.HYBRID_RAG, risk, answer, mode,
                         citations=[Citation(chunk_id=c.id, score=s) for c, s in safe],
                         degraded=degraded, degraded_reason="llm_unavailable" if degraded else None)
        if self.cache and ident:
            self.cache.put(tenant.tenant_id, ident, msg, out.model_dump(mode="json"))
        self._decision(tenant, request_id, out, [cid for cid, _ in context], model)
        return out

    def _decision(self, tenant, request_id, out: ChatResponse, chunk_ids, model_served):
        """Traceability record: hashed into the audit chain; the full record is written to the decision log."""
        rec = {"request_id": request_id, "tenant_id": tenant.tenant_id, "route": out.route.value,
               "response_mode": out.response_mode, "risk": out.risk, "degraded": out.degraded,
               "chunk_ids": chunk_ids, "citations": [c.chunk_id for c in out.citations],
               "model_served": model_served, **self.versions}
        self._audit(tenant, "decision", rec)
        log.info("decision %s", json.dumps(rec, default=str))

    def _verify(self, answer, texts, ids) -> Verdict | None:
        try:
            with telemetry.span("verify"):
                return verify(answer, texts, chunk_ids=ids)
        except Exception:
            telemetry.incr("verifier.unavailable")  # fail closed: caller abstains
            log.exception("verifier failed; abstaining")
            return None

    def _generate(self, msg, tenant, context, entities):
        """Returns (answer | None, response_mode, model_served | None)."""
        texts = [t for _, t in context]
        ids = [cid for cid, _ in context]
        if self.llm is None:  # extractive path: verbatim evidence sentences, still verified
            q = set(tokenize(msg))
            sents = re.split(r"(?<=\.)\s+", texts[0]) if texts else []
            best = sorted(sents, key=lambda s: -len(q & set(tokenize(s))))[:2]
            text = " ".join(best).strip()
            if not text:
                return None, "abstained_no_evidence", None
            v = self._verify(text, texts, ids)
            if v is None:
                return None, "verifier_unavailable", None
            if v.ok:
                return text, "extractive", None
            telemetry.incr("verification.vetoed")
            return None, "extractive_rejected", None
        anchor = goal_anchor(msg, entities)
        pctx: dict = {"escalations": 0}
        res = None
        for model in (self.models[0], self.models[1]):
            try:
                with telemetry.span("llm.generate", model=model), self._pool("llm_pool"):
                    res = self.llm.complete(build_messages(anchor, context, msg), model)
            except LLMError:
                telemetry.incr("llm.errors")
                return None, "llm_error", None
            gen = res.text
            if gen.strip().upper().startswith("INSUFFICIENT"):
                return None, "insufficient", res.model
            v = self._verify(gen, texts, ids)
            if v is None:
                return None, "verifier_unavailable", res.model
            pctx.update(answer=gen, chunks=texts, chunk_ids=ids, verdict=v)
            pctx = self.registry.run(Stage.POST, pctx)
            if v.ok:
                return gen, "llm", res.model
            if not pctx.get("escalate"):
                break
            pctx["escalations"] += 1
            telemetry.incr("model.escalations")
        telemetry.incr("verification.vetoed")
        return None, "vetoed", res.model if res else None
