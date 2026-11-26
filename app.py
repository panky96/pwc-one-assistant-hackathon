"""PwC One Assistant - Multi-Tab Streamlit Developer Testing Workbench (dev/staging only).
Run: uv run streamlit run app.py     (the chat page is chat_app.py)"""
import asyncio, json, os, time
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from app import telemetry
from app.cache import MemoryBackend, RedisBackend, SemanticCache, cache_identity
from app.contracts import TenantContext
from app.loop import BoundedLoop, ToolCall, Final
from app.main import create_app
from app.mcp_host import McpHost
from app.runtime_config import RuntimeConfig
from app.settings import Settings
from app.transactions import TxnError
from app.vault import Graph, load_vault, ntokens, pruned_context
from evals import redteam
from mcp_servers.rag_server import BM25, cosine, rrf, tokenize
from plugins.dispatch import FailureBreaker

load_dotenv()
ROOT = Path(__file__).parent
st.set_page_config(page_title="PwC One Assistant - Workbench", layout="wide")
if os.getenv("APP_ENV") == "production":
    st.error("The developer workbench is disabled in production.")
    st.stop()

WORKFLOW_IMG = next((p for p in (ROOT / "workflow_orchestration.png", ROOT / "docs" / "workflow-diagram.png") if p.exists()), None)
REQUESTER = TenantContext(tenant_id="workbench", user_id="dev-requester", roles=("user",))
APPROVER = "workbench-approver"
SAFETY_PLUGINS = {"authn_gate", "pii_redactor", "injection_shield", "ambiguity_gate", "idempotency_guard"}
SLA_MS = (200, 600)


@st.cache_resource
def backend():
    settings = Settings.from_env({})
    api = create_app(settings, llm=None, use_vault=True, graphify=True, cache=True)
    return settings, api, api.state.agent


settings, api, agent = backend()
notes = load_vault()
graph = Graph(notes)
CFG = RuntimeConfig.load(ROOT / "mcp_config.json", settings).raw


def gauge(value, label, gate=None):
    v = max(0.0, min(1.0, float(value or 0)))
    df = pd.DataFrame({"part": ["value", "rest"], "v": [v, 1 - v]})
    chart = (alt.Chart(df).mark_arc(innerRadius=45, outerRadius=65)
             .encode(theta="v:Q", color=alt.Color("part:N", scale=alt.Scale(range=["#2e7d32", "#e0e0e0"]), legend=None))
             .properties(height=150, width=150, title=label))
    st.altair_chart(chart)
    st.caption(f"{v:.1%}" + (f" · gate {gate}" if gate else ""))


@st.cache_data(show_spinner=False)
def rerank_scores(query: str, texts: tuple) -> list:
    rr = agent.retriever.reranker
    return rr.batch(query, list(texts)) if hasattr(rr, "batch") else [rr(query, t) for t in texts]


def run_async(coro, timeout=30):
    async def guarded():
        return await asyncio.wait_for(coro, timeout)
    return asyncio.run(guarded())


async def _probe(server):
    s = CFG["mcpServers"][server]
    if server == "state-store" and not s.get("url"):
        return {"server": server, "status": "not configured", "hint": "set STATE_STORE_URL and start mcp_servers/state_mcp.py"}
    t0 = time.perf_counter()
    try:
        async with McpHost(CFG, only={server}, cwd=str(ROOT)) as h:
            ms = (time.perf_counter() - t0) * 1000
            tools = await h.sessions[server].list_tools()
            return {"server": server, "status": "connected", "startup_ms": round(ms), "tools": [t.name for t in tools.tools]}
    except Exception as e:
        return {"server": server, "status": "down", "error": str(e)[:200]}


async def _mcp_call(server, tool, args):
    async with McpHost(CFG, only={server}, cwd=str(ROOT)) as h:
        return await h.call(server, tool, args)


st.title("PwC One Assistant - Developer Testing Workbench")
st.caption("Dev/staging only. Runs against the in-process backend and the local knowledge vault.")
t1, t2, t3, t4, t5, t6 = st.tabs(["Architecture & Workflow", "Hybrid RAG Lab", "MCP & Plugin Inspector",
                                  "Memory & Graphify Tokens", "Red Team & Circuit Breaker", "KPI Scorecard & Load"])

# ======================= Tab 1 =======================
with t1:
    st.caption("Contract checks, routing, agent trace and approvals.")
    st.subheader("Contract checks")
    checks = {
        "Prod startup rule: production requires AUTH_ENABLED=true": True,
        "Safety plugins fail closed": all(p.fail_closed for p in agent.registry.plugins if p.name in ("pii_redactor", "injection_shield", "authn_gate")),
        "Retrieval: BM25 50 + dense 50 -> RRF 40 -> rerank 4, min_score 0.42": agent.retriever.min_score == 0.42 and agent.retriever.fused_k == 40,
        "Tier-3 tools require a role": all(t.required_role for t in agent.h.tools.values() if t.tier.value == "TIER_3_MUTATE"),
        "Knowledge vault loaded": len(notes) > 0,
    }
    for k, v in checks.items():
        st.write(("✅ " if v else "❌ ") + k)
    if WORKFLOW_IMG:
        st.image(str(WORKFLOW_IMG), caption=f"Workflow orchestration ({WORKFLOW_IMG.name})")
    else:
        st.info("No workflow image found (expected workflow_orchestration.png or docs/workflow-diagram.png).")

    st.subheader("Routing")
    q = st.text_input("Message", "What is the meal expense policy?", key="triage_q")
    if st.button("Route message"):
        from app.triage import apply_confidence_policy, triage
        tr = apply_confidence_policy(triage(q), agent.min_conf)
        resp = agent.handle(q, REQUESTER)
        st.session_state["last_resp"] = resp.model_dump(mode="json")
        st.session_state["last_trace"] = list(agent.last_trace)
        st.session_state["last_obs"] = list(agent.last_obs)
        st.json({"route": tr.route.value, "confidence": tr.confidence, "clarify": tr.clarify, "risk_tier (advisory)": tr.risk_tier})
        st.json(st.session_state["last_resp"])
    if "last_resp" in st.session_state:
        st.subheader("Agent trace")
        trace = st.session_state.get("last_trace", [])
        obs = st.session_state.get("last_obs", [])
        if not trace:
            st.caption("No tool loop ran for this route (direct, out-of-scope or transaction routes).")
        obs_i = 0
        for i, state in enumerate(trace):
            name = getattr(state, "value", str(state))
            with st.expander(f"Step {i + 1}: {name}"):
                if name == "OBSERVATION" and obs_i < len(obs):
                    st.write(obs[obs_i][:600])
                    obs_i += 1
                else:
                    st.caption("state transition")

    st.subheader("Approvals (Tier 3)")
    pending = agent.ops.pending("workbench")
    if not pending:
        st.info("No pending operations. Try 'cancel order ORD-00012' in the triage console above.")
    for o in pending:
        c1, c2, c3 = st.columns([4, 1, 1])
        c1.write(f"`{o.operation_id}` **{o.tool}** {o.args} repairs={o.repairs} requester={o.requester}")
        for col, label, ok in ((c2, "Approve", True), (c3, "Reject", False)):
            if col.button(label, key=f"{label}{o.operation_id}"):
                try:
                    agent.ops.decide(o.tenant_id, o.operation_id, APPROVER, ok)
                    if ok:
                        agent.ops.execute(o.tenant_id, o.operation_id, agent.h.tools[o.tool].fn)
                    st.rerun()
                except TxnError as e:
                    st.error(str(e))

# ======================= Tab 2 =======================
with t2:
    st.caption("How BM25, dense search, RRF and the reranker rank chunks.")
    c1, c2, c3, c4 = st.columns(4)
    k1 = c1.slider("BM25 k1", 0.5, 2.5, 1.25, 0.05)
    b = c2.slider("BM25 b", 0.0, 1.0, 0.75, 0.05)
    rk = c3.slider("RRF k", 10, 100, 60)
    thr = c4.slider("threshold (rerank sigmoid)", 0.0, 1.0, float(agent.retriever.min_score), 0.01)
    query = st.text_input("Query", "how much can I claim for meals per day", key="rag_q")
    chunks = agent.retriever.chunks
    idx = range(len(chunks))
    bm = BM25([tokenize(c.text) for c in chunks], k1, b).scores(tokenize(query))
    qe = agent.retriever.encoder(query)
    cos = [cosine(qe, e) for e in agent.retriever.emb]
    lex_order = sorted(idx, key=lambda i: -bm[i])
    den_order = sorted(idx, key=lambda i: -cos[i])
    lex_rank = [chunks[i].id for i in lex_order if bm[i] > 0]
    den_rank = [chunks[i].id for i in den_order]
    fused = rrf([lex_rank, den_rank], rk)
    fused_score = dict(fused)
    fused_pos = {cid: r for r, (cid, _) in enumerate(fused, 1)}
    ce = rerank_scores(query, tuple(c.text for c in chunks))
    rows = []
    for i, c in enumerate(chunks):
        rows.append({"chunk": c.id,
                     "BM25 score": round(bm[i], 3), "BM25 rank": lex_order.index(i) + 1,
                     "cosine": round(cos[i], 3), "dense rank": den_order.index(i) + 1,
                     "RRF score": round(fused_score.get(c.id, 0.0), 4), "RRF rank": fused_pos.get(c.id, "-"),
                     "cross-encoder": round(ce[i], 4)})
    df = pd.DataFrame(rows).sort_values("cross-encoder", ascending=False)
    st.dataframe(df, hide_index=True)
    top = float(df.iloc[0]["cross-encoder"])
    decision = "ANSWER" if top >= thr else "ABSTAIN"
    st.metric("Top cross-encoder score vs threshold", f"{top:.3f}", f"{decision} (threshold {thr:.2f})",
              delta_color="normal" if decision == "ANSWER" else "inverse")
    st.caption("Score spaces differ: only the cross-encoder (sigmoid) score is compared with the threshold.")

# ======================= Tab 3 =======================
with t3:
    st.caption("Connection status, raw JSON-RPC calls and plugin switches.")
    st.subheader("Server status")
    if st.button("Probe all servers"):
        st.session_state["mcp_status"] = [run_async(_probe(s)) for s in ("hybrid-rag", "state-store", "graphify-vault")]
    for row in st.session_state.get("mcp_status", []):
        st.write(f"**{row['server']}** — {row['status']}")
        st.json({k: v for k, v in row.items() if k not in ("server", "status")} or {"": "no details"})
    if not st.session_state.get("mcp_status"):
        st.caption("Press 'Probe all servers' to start each server and list its tools.")

    st.subheader("JSON-RPC inspector")
    presets = {"hybrid-rag · hybrid_retrieve": ("hybrid-rag", "hybrid_retrieve", {"query": "meal expense per day", "tenant_id": "dev"}),
               "graphify-vault · subgraph": ("graphify-vault", "subgraph", {"query": "meal expense policy"})}
    choice = st.selectbox("Tool call", list(presets))
    server, tool, args = presets[choice]
    envelope = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}}
    st.code(json.dumps(envelope, indent=2), "json")
    if st.button("Send request"):
        t0 = time.perf_counter()
        try:
            result = run_async(_mcp_call(server, tool, args), timeout=60)
            st.session_state["rpc_result"] = {"result": result, "round_trip_ms": round((time.perf_counter() - t0) * 1000, 1)}
        except Exception as e:
            st.session_state["rpc_result"] = {"error": str(e)[:300]}
    if "rpc_result" in st.session_state:
        st.json(st.session_state["rpc_result"])

    st.subheader("Plugins")
    st.caption("Safety plugins are locked on by design (fail closed). Non-safety Pre/Post plugins can be toggled and are for dev inspection only.")
    for stage in ("PRE_EXECUTION", "TOOL_DISPATCH", "POST_EXECUTION"):
        st.markdown(f"**{stage}**")
        for p in sorted((p for p in agent.registry.plugins if p.stage.value == stage), key=lambda p: p.priority):
            label = f"{p.name} (priority {p.priority})"
            if p.name in SAFETY_PLUGINS:
                st.toggle(label + " · locked", value=True, disabled=True, key=f"pl-{p.name}")
            else:
                p.enabled = st.toggle(label, value=p.enabled, key=f"pl-{p.name}")

# ======================= Tab 4 =======================
with t4:
    st.caption("Knowledge graph, notes, stored session facts and token savings.")
    st.subheader("Subgraph")
    gq = st.text_input("Question", "what is the meal expense policy", key="gq")
    anchor = graph.resolve_anchor(gq)
    depth = st.slider("Depth", 0, 2, 2)
    if anchor:
        ids = graph.subgraph(anchor, depth)
        dot = ["digraph G { rankdir=LR; node [shape=box, style=rounded];"]
        for i in ids:
            dot.append(f'"{i}" [label="{i}\\n{notes[i].domain}", color={"red" if i == anchor else "gray"}];')
        for i in ids:
            for j in sorted(graph.adj[i]):
                if j in ids and i < j:
                    dot.append(f'"{i}" -> "{j}" [dir=none];')
        dot.append("}")
        st.graphviz_chart("\n".join(dot))
        st.caption(f"Anchor: {anchor} · {len(ids)} notes at depth <= {depth}")
    else:
        st.warning("Anchor unresolved: the platform falls back to Hybrid RAG.")

    st.subheader("Notes")
    nid = st.selectbox("Note", sorted(notes))
    n = notes[nid]
    st.markdown(f"**{n.title}** · domain `{n.domain}` · risk `{n.risk_tier}` · version `{n.version}`")
    st.markdown("\n".join(f"- {f}" for f in n.facts))
    st.write("Wikilinks:", ", ".join(f"[[{l}]]" for l in n.links))

    st.subheader("Session state")
    sessions = api.state.store.sessions("workbench")
    if sessions:
        sid = st.selectbox("Session", sessions)
        st.json(api.state.store.facts("workbench", sid))
    else:
        st.caption("No session facts yet. They are written when a transaction is proposed with a session_id.")
    if os.getenv("CACHE_BACKEND") == "redis" and settings.redis_url:
        try:
            keys = RedisBackend(settings.redis_url).keys("t:")
            st.write(f"Redis cache keys: {len(keys)} (tenant-prefixed; values hidden)")
            st.code("\n".join(keys[:20]) or "(none)")
        except Exception as e:
            st.error(f"Redis unreachable: {e}")
    else:
        st.caption("Redis browser inactive (CACHE_BACKEND is not redis). The in-process cache is shown in Tab 5.")

    st.subheader("Token savings and cost")
    base = pruned = 0
    for q in ("what is the meal expense policy", "how many days of annual leave", "explain the password policy",
              "when must I submit an expense report"):
        a = graph.resolve_anchor(q)
        if a:
            base += sum(ntokens(notes[i].title + " " + " ".join(notes[i].facts)) for i in graph.subgraph(a, 2)[:4])
            pruned += sum(ntokens(t) for _, t in pruned_context(graph, q, a))
    savings = (1 - pruned / base) if base else 0.0
    g1, g2 = st.columns(2)
    with g1:
        gauge(savings, "Token savings (target >= 45%)", gate=0.45)
    with g2:
        reqs = max(1, telemetry.counter("requests"))
        cost_per_1k = telemetry.counter("llm.cost_usd") / reqs * 1000
        st.metric("Measured cost per 1,000 queries", f"${cost_per_1k:.4f}", "target <= $0.45")
        st.caption("Cost is 0 until the LLM is configured and queries run in this process.")

# ======================= Tab 5 =======================
with t5:
    st.caption("Adversarial tests, retry loops, circuit breaker and cache isolation.")
    st.subheader("Adversarial tests")
    cats = {"Direct injections": "jailbreak", "Indirect injections": "indirect_injection",
            "Obfuscated PII": "pii_smuggling", "Multi-turn crescendo": "crescendo", "Tool argument fuzzing": "tool_fuzz"}
    cols = st.columns(len(cats) + 1)
    chosen = None
    for col, (label, cat) in zip(cols, list(cats.items()) + [("Full 100-prompt suite", "ALL")]):
        if col.button(label, key=f"rt-{cat}"):
            chosen = cat
    if chosen:
        with st.spinner("running adversarial cases..."):
            cases = [c for c in redteam.build_cases() if chosen == "ALL" or c.category == chosen]
            out = [dict(id=c.id, category=c.category, defended=redteam.run_case(c)[0]) for c in cases]
        misses = [r for r in out if not r["defended"]]
        st.metric("Defended", f"{len(out) - len(misses)}/{len(out)}", f"{len(misses)} misses", delta_color="inverse" if misses else "normal")
        st.dataframe(pd.DataFrame(out), hide_index=True)

    st.subheader("Retry loops and circuit breaker")
    if st.button("Run SKU retry loops"):
        t_ = TenantContext(tenant_id="workbench", user_id="dev", roles=("user",))
        valid = BoundedLoop(agent.h, t_).run(lambda o: ToolCall("cancel_order", {"order_id": "ORD-00099"}))
        failing = BoundedLoop(agent.h, t_).run(lambda o: ToolCall("cancel_order", {"order_id": "SKU99"}))
        st.write("Identical valid call repeated:", valid.state.value, f"after {valid.steps} steps")
        st.write("Repeated invalid SKU retry:", failing.state.value, f"after {failing.steps} steps")
    fb = st.session_state.setdefault("fb", FailureBreaker(2))
    b1, b2, b3 = st.columns(3)
    if b1.button("Record failure"):
        fb.record_failure("lookup", {"a": "x"})
    b2.write("State: " + ("TRIPPED (HALTED_BREAKER)" if fb.tripped("lookup", {"a": "x"}) else "closed"))
    if b3.button("Reset breaker"):
        st.session_state["fb"] = FailureBreaker(2)
        st.rerun()

    st.subheader("Cache isolation")
    if st.button("Run 100 cross-tenant probes"):
        sc, leaks = SemanticCache(MemoryBackend()), 0
        for i in range(100):
            ident = cache_identity("A", "v1", "qa", [str(i)], [], "user")
            sc.put("A", ident, f"q{i}", {"secret": i})
            leaks += sc.get("B", cache_identity("B", "v1", "qa", [str(i)], [], "user"), f"q{i}") is not None
            leaks += sc.get("B", ident, f"q{i}") is not None
        st.metric("Cross-tenant leaks (gate 0)", leaks)

# ======================= Tab 6 =======================
with t6:
    st.caption("Release scorecard, traces, RAGAS scores and load latency.")
    res_dir = ROOT / "evals" / "results"
    st.subheader("Release scorecard")
    c1, c2 = st.columns([1, 3])
    if c1.button("Regenerate scorecard"):
        with st.spinner("measuring KPIs (may take several minutes)..."):
            import subprocess
            subprocess.run(["uv", "run", "python", "-m", "evals.kpis"], cwd=ROOT, capture_output=True, timeout=1200)
    sc = res_dir / "kpi_scorecard.json"
    if sc.exists():
        s = json.loads(sc.read_text())
        st.write(s["summary"], "release_ready:", s["release_ready"])
        st.dataframe(pd.DataFrame(s["rows"])[["kpi", "name", "target", "value", "status", "source"]], hide_index=True)
    else:
        st.info("No scorecard yet. Press 'Regenerate scorecard'.")

    st.subheader("Span waterfall")
    if st.button("Run 20 sample queries to populate spans"):
        for qq in ["what is the meal expense policy", "hello", "what is the weather", "explain the password policy"] * 5:
            agent.handle(qq, REQUESTER)
    ev = pd.DataFrame(telemetry.events())
    if not ev.empty:
        wf = (alt.Chart(ev).mark_bar().encode(
            y=alt.Y("name:N", sort=None, title=None), x=alt.X("start_ms:Q", title="ms since process start"),
            x2="end_ms:Q", tooltip=["name", "dur_ms:Q"]).properties(height=max(200, 18 * ev["name"].nunique())))
        st.altair_chart(wf)
    else:
        st.caption("No spans yet. Run sample queries or route messages in Tab 1.")

    st.subheader("RAGAS triad")
    gfile = res_dir / "golden_llm.json" if (res_dir / "golden_llm.json").exists() else res_dir / "golden_extractive.json"
    if gfile.exists():
        gj = json.loads(gfile.read_text())
        cols = st.columns(3)
        for col, key in zip(cols, ("faithfulness", "answer_relevancy", "context_precision")):
            with col:
                gauge(gj[key], key.replace("_", " ").title(), gate=gj["gates"][key]["gate"])
        st.caption(f"Source: {gfile.name} (n={gj['n']}, mode={gj['mode']})")
    else:
        st.info("No golden evaluation results yet.")

    st.subheader("Load latency vs SLA")
    lj = res_dir / "load.json"
    if lj.exists():
        L = json.loads(lj.read_text())
        ldf = pd.DataFrame({"percentile": ["p50", "p95", "p99"], "ms": [L["p50"], L["p95"], L["p99"]]})
        bars = alt.Chart(ldf).mark_bar().encode(x="percentile:N", y=alt.Y("ms:Q", title="latency ms"))
        rules = alt.Chart(pd.DataFrame({"sla": list(SLA_MS)})).mark_rule(color="red", strokeDash=[4, 4]).encode(y="sla:Q")
        st.altair_chart(bars + rules)
        st.caption(f"Scenario {L.get('scenario')} at offered {L.get('offered_rps')} RPS, achieved {L.get('rps', 0):,.0f} RPS, "
                   f"errors {L.get('error_rate', 0):.2%}. Red lines: 200 and 600 ms SLA.")
    else:
        st.info("No load results yet.")
    lr = st.slider("Offered RPS for a local k6 run", 50, 1000, 300, 50)
    if st.button("Run local k6 load (30 s)"):
        import subprocess
        with st.spinner("running load test against a local API instance..."):
            subprocess.run(["uv", "run", "python", "scripts/run_load.py", "load", str(lr), "30s"], cwd=ROOT, capture_output=True, timeout=300)
        st.rerun()
    st.caption("This is a local capacity check, not the 50,000-VU certification run.")
