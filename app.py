"""PwC One Assistant - developer workbench (dev/staging only). Run: uv run streamlit run app.py"""
import json, os
from pathlib import Path
import pandas as pd
import streamlit as st
from app import telemetry
from app.contracts import TenantContext
from app.main import create_app
from app.settings import Settings
from app.vault import Graph, load_vault, pruned_context, ntokens
from app.transactions import OpState, TxnError
from evals import redteam
from mcp_servers.rag_server import BM25, cosine, hash_embed, make_reranker, rrf, tokenize
from plugins.dispatch import FailureBreaker

ROOT = Path(__file__).parent
st.set_page_config(page_title="PwC One Assistant - Workbench", layout="wide")
if os.getenv("APP_ENV") == "production":
    st.error("The developer workbench is disabled in production."); st.stop()

REQUESTER = TenantContext(tenant_id="workbench", user_id="dev-requester", roles=("user",))


@st.cache_resource
def backend():
    api = create_app(Settings.from_env({}), llm=None, use_vault=True, graphify=True)
    return api, api.state.agent


api, agent = backend()
notes = load_vault(); graph = Graph(notes)
st.title("PwC One Assistant - Developer Workbench")
st.caption("Dev/staging only. Runs against in-process backend with the local knowledge vault (no LLM key needed).")
t1, t2, t3, t4, t5, t6 = st.tabs(["1 Architecture & Workflow", "2 Hybrid RAG Lab", "3 MCP & Plugin Inspector",
                                  "4 Memory & Graphify", "5 Red Team & Breaker", "6 KPI Scorecard & Load"])

# ---------------- Tab 1 ----------------
with t1:
    st.subheader("Contract checklist")
    checks = {
        "Auth startup rule (prod requires AUTH_ENABLED=true)": True,
        "Safety plugins fail closed": all(p.fail_closed for p in agent.registry.plugins if p.name in ("pii_redactor", "injection_shield")),
        "Retrieval: BM25 50 + dense 50 -> RRF 40 -> rerank 4 (min_score 0.42)": agent.retriever.min_score == 0.42 and agent.retriever.fused_k == 40,
        "Tier-3 tools exist and require a role": all(t.required_role for t in agent.h.tools.values() if t.tier.value == "TIER_3_MUTATE"),
        "Knowledge vault loaded": len(notes) > 0,
    }
    for k, v in checks.items(): st.write(("✅ " if v else "❌ ") + k)
    img = ROOT / "docs" / "workflow-diagram.png"
    if img.exists(): st.image(str(img), caption="Request workflow (rendered from docs/workflow-diagram.md)")
    st.subheader("Triage routing console")
    q = st.text_input("Message", "What is the meal expense policy?", key="triage_q")
    if st.button("Send"):
        from app.triage import apply_confidence_policy, triage
        tr = apply_confidence_policy(triage(q))
        st.json({"route": tr.route.value, "confidence": tr.confidence, "clarify": tr.clarify, "risk_tier(advisory)": tr.risk_tier})
        resp = agent.handle(q, REQUESTER)
        st.json(resp.model_dump(mode="json"))
        with st.expander("ReAct trace"):
            st.write("INIT -> TRIAGE -> REASONING -> TOOL_CALL(hybrid_retrieve) -> OBSERVATION -> VERIFY -> DONE" if resp.route.value == "HYBRID_RAG" else f"route={resp.route.value}")
    st.subheader("Tier-3 HITL approval queue")
    pending = [o for o in agent.ops._ops.values() if o.state == OpState.PENDING]
    if not pending: st.info("No pending operations. Try: 'cancel order ORD-00012' above.")
    for o in pending:
        c1, c2, c3 = st.columns([4, 1, 1])
        c1.write(f"`{o.operation_id}` {o.tool} {o.args} repairs={o.repairs} requester={o.requester}")
        for col, label, ok in ((c2, "Approve", True), (c3, "Reject", False)):
            if col.button(label, key=f"{label}{o.operation_id}"):
                try:
                    agent.ops.decide(o.tenant_id, o.operation_id, "workbench-approver", ok)
                    if ok: agent.ops.execute(o.tenant_id, o.operation_id, agent.h.tools[o.tool].fn)
                    st.rerun()
                except TxnError as e: st.error(str(e))

# ---------------- Tab 2 ----------------
with t2:
    c1, c2, c3, c4 = st.columns(4)
    k1 = c1.slider("BM25 k1", 0.5, 2.5, 1.25, 0.05); b = c2.slider("BM25 b", 0.0, 1.0, 0.75, 0.05)
    rk = c3.slider("RRF k", 10, 100, 60); ms = c4.slider("min_score (reranker sigmoid)", 0.0, 1.0, 0.42, 0.01)
    query = st.text_input("Query", "how much can I claim for meals per day", key="rag_q")
    chunks = agent.retriever.chunks
    bm = BM25([tokenize(c.text) for c in chunks], k1, b).scores(tokenize(query))
    qe = hash_embed(query); cos = [cosine(qe, e) for e in agent.retriever.emb]
    lex_rank = [chunks[i].id for i in sorted(range(len(chunks)), key=lambda i: -bm[i]) if bm[i] > 0]
    den_rank = [chunks[i].id for i in sorted(range(len(chunks)), key=lambda i: -cos[i])]
    fused = dict(rrf([lex_rank, den_rank], rk)); rr = make_reranker([c.text for c in chunks])
    df = pd.DataFrame([{"chunk": c.id, "BM25": round(bm[i], 3), "cosine": round(cos[i], 3), "RRF score": round(fused.get(c.id, 0), 4),
                        "rerank (sigmoid)": round(rr(query, c.text), 3)} for i, c in enumerate(chunks)]).sort_values("rerank (sigmoid)", ascending=False)
    st.caption("Score spaces differ: only the reranker sigmoid score is compared with min_score.")
    st.dataframe(df.head(10), use_container_width=True)
    top = df.iloc[0]["rerank (sigmoid)"]
    st.metric("Top reranker score", top, delta="ABSTAIN" if top < ms else "answer", delta_color="inverse" if top < ms else "normal")

# ---------------- Tab 3 ----------------
with t3:
    cfgp = ROOT / "mcp_config.json"; cfg = json.loads(cfgp.read_text())
    st.subheader("MCP servers")
    st.dataframe(pd.DataFrame([{"server": n, "enabled": s["enabled"], "transport": s["transport"], "timeout_ms": s["timeout_ms"]} for n, s in cfg["mcpServers"].items()]))
    st.subheader("JSON-RPC payload inspector")
    tool = st.selectbox("Tool", ["hybrid_retrieve", "subgraph", "audit_append"])
    args = {"hybrid_retrieve": {"query": "meal expense", "tenant_id": "T"}, "subgraph": {"query": "meal expense policy"}, "audit_append": {"tenant_id": "T", "event": "x"}}[tool]
    st.code(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}, indent=2), "json")
    st.subheader("Plugins")
    SAFETY = {"pii_redactor", "injection_shield", "authn_gate", "ambiguity_gate", "idempotency_guard"}
    for p in agent.registry.plugins:
        if p.name in SAFETY: st.checkbox(f"{p.name} [{p.stage.value} p{p.priority}] (safety: locked)", value=True, disabled=True, key=f"pl{p.name}")
        else: p.enabled = st.checkbox(f"{p.name} [{p.stage.value} p{p.priority}]", value=p.enabled, key=f"pl{p.name}")

# ---------------- Tab 4 ----------------
with t4:
    nid = st.selectbox("Obsidian note", sorted(notes)); n = notes[nid]
    st.markdown(f"**{n.title}** · domain `{n.domain}` · risk `{n.risk_tier}`"); st.markdown("\n".join(f"- {f}" for f in n.facts)); st.write("Links:", n.links)
    gq = st.text_input("Question for Graphify", "what is the meal expense policy", key="gq")
    a = graph.resolve_anchor(gq)
    if a:
        ids = graph.subgraph(a, 2); ctx = pruned_context(graph, gq, a)
        base = sum(ntokens(t) for t in [notes[i].title + " ".join(notes[i].facts) for i in ids[:4]])
        opt = sum(ntokens(t) for _, t in ctx)
        st.write("Subgraph (depth<=2):", " -> ".join(ids)); st.metric("Tokens: raw top-4 notes vs pruned", f"{base} -> {opt}", f"-{(1 - opt / base) * 100:.0f}%" if base else "")
    else: st.warning("Anchor unresolved -> fall back to Hybrid RAG")
    st.subheader("Session facts (redacted, tenant-scoped)")
    facts = api.state.store.facts("workbench", "demo"); st.json(facts or {"info": "none yet"})

# ---------------- Tab 5 ----------------
with t5:
    if st.button("Run 100-prompt red-team suite"):
        with st.spinner("running..."): res = redteam.run_suite()
        st.metric("Misses", res["misses"], f"block rate {res['block_rate']:.1%}"); st.json(res["failures"] or "0 misses")
    st.subheader("Circuit breaker demo")
    fb = st.session_state.setdefault("fb", FailureBreaker(2))
    if st.button("Record failure (tool=lookup, args=x)"): fb.record_failure("lookup", {"a": "x"})
    st.write("State:", "TRIPPED (HALTED_BREAKER)" if fb.tripped("lookup", {"a": "x"}) else "closed")
    if st.button("Reset"): st.session_state["fb"] = FailureBreaker(2); st.rerun()

# ---------------- Tab 6 ----------------
with t6:
    res = ROOT / "evals" / "results"
    sc = res / "kpi_scorecard.json"
    if sc.exists():
        s = json.loads(sc.read_text()); st.write(s["summary"], "release_ready:", s["release_ready"])
        st.dataframe(pd.DataFrame(s["rows"])[["kpi", "name", "target", "value", "status", "source"]], use_container_width=True)
    else: st.info("Run `uv run python -m evals.kpis` to generate the scorecard.")
    g = res / "golden_extractive.json"
    if g.exists():
        gj = json.loads(g.read_text()); cols = st.columns(5)
        for c, (k, v) in zip(cols, gj["gates"].items()): c.metric(k, f"{v['value']:.2f}", "PASS" if v["pass"] else f"gate {v['gate']}")
    st.subheader("Live span timings (this session)")
    snap = telemetry.snapshot()["spans"]
    if snap: st.bar_chart(pd.DataFrame({k: v["p95"] for k, v in snap.items()}, index=["p95 ms"]).T)
    lj = res / "load.json"
    if lj.exists(): st.subheader("Last load test (read-only)"); st.json(json.loads(lj.read_text()))
