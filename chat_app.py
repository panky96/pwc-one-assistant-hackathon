"""PwC One Assistant - chat console (Streamlit, dev/staging).
Ask questions, see routing and grounded answers, approve or deny tier-3 actions, and watch the live metrics you select.
Run: uv run streamlit run chat_app.py   (AUTH_ENABLED must be false here; production needs bearer tokens)."""
import time
import uuid

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from app import telemetry
from app.contracts import TenantContext
from app.main import create_app
from app.settings import Settings
from app.transactions import TxnError

load_dotenv()
st.set_page_config(page_title="PwC One Assistant", layout="wide")

TENANT = TenantContext(tenant_id="dev-tenant", user_id="dev-user", roles=("user",))
APPROVER = TenantContext(tenant_id="dev-tenant", user_id="dev-approver", roles=("user", "approver"))
KNOWN_COUNTERS = ["requests", "blocked", "cache.hit", "abstain.retrieval", "loop.tasks", "loop.steps", "tool.calls",
                  "tool.invalid", "subagent.denied", "llm.errors", "llm.cost_usd", "model.escalations",
                  "verification.vetoed", "ragas.sampled", "ragas.flagged", "shed.rag_pool", "shed.llm_pool"]
KNOWN_SPANS = ["hook.pre", "triage", "retrieval", "rag.bm25", "rag.dense", "rag.rerank", "verify", "llm.generate",
               "llm.call", "llm.ttft", "mcp.call"]
DEFAULT_COUNTERS = ["requests", "abstain.retrieval", "llm.cost_usd", "verification.vetoed"]
DEFAULT_SPANS = ["retrieval", "rag.bm25", "rag.dense", "rag.rerank"]


@st.cache_resource
def backend():
    settings = Settings.from_env()
    api = create_app(settings, use_vault=True)  # loads the knowledge vault so grounded answers work
    return settings, api.state.agent


settings, agent = backend()
if settings.app_env == "production" or settings.auth_enabled:
    st.error("This console is for development only. Use an authenticated client in production.")
    st.stop()

st.session_state.setdefault("history", [])
st.session_state.setdefault("session_id", uuid.uuid4().hex)
st.title("PwC One Assistant")
st.caption("Dev console: grounded answers from the knowledge vault, with live metrics.")

left, right = st.columns([3, 2])

with left:
    st.subheader("Ask")
    for item in st.session_state.history:
        with st.chat_message("user"):
            st.write(item["q"])
        with st.chat_message("assistant"):
            r = item["resp"]
            st.markdown(f"**Route:** `{r['route']}` · **Latency:** {item['ms']:.0f} ms"
                        + (" · **abstained**" if r["abstained"] else ""))
            st.write(r["answer"])
            if r["citations"]:
                st.caption("Sources: " + ", ".join(f"{c['chunk_id']} ({c['score']:.2f})" for c in r["citations"]))
            if r["clarification"]:
                st.info("Needed to proceed: " + ", ".join(r["clarification"]))
            if r["operation_id"]:
                st.warning(f"Awaiting human approval: `{r['operation_id']}`")

    query = st.chat_input("Ask a policy question, or request an action (e.g. cancel order ORD-00012)")
    if query:
        t0 = time.perf_counter()
        resp = agent.handle(query, TENANT, session_id=st.session_state.session_id)
        ms = (time.perf_counter() - t0) * 1000
        st.session_state.history.append({"q": query, "resp": resp.model_dump(mode="json"), "ms": ms})
        st.rerun()

    st.subheader("Pending approvals")
    pending = agent.ops.pending(TENANT.tenant_id)
    if not pending:
        st.caption("None.")
    for op in pending:
        c1, c2, c3 = st.columns([5, 1, 1])
        c1.write(f"`{op.operation_id[:8]}` **{op.tool}** {op.args} (requested by {op.requester})")
        for col, label, ok in ((c2, "Approve", True), (c3, "Deny", False)):
            if col.button(label, key=f"{label}-{op.operation_id}"):
                try:
                    agent.ops.decide(TENANT.tenant_id, op.operation_id, APPROVER.user_id, ok)
                    if ok:
                        agent.ops.execute(TENANT.tenant_id, op.operation_id, agent.h.tools[op.tool].fn)
                    st.rerun()
                except TxnError as e:
                    st.error(str(e))

with right:
    st.subheader("Live metrics")

    @st.fragment(run_every=2)
    def live_metrics():
        snap = telemetry.snapshot()
        counter_opts = sorted(set(KNOWN_COUNTERS) | set(snap["counters"]))
        span_opts = sorted(set(KNOWN_SPANS) | set(snap["spans"]))
        sel_c = st.multiselect("Counters", counter_opts, default=DEFAULT_COUNTERS, key="sel_counters")
        sel_s = st.multiselect("Latency spans (ms)", span_opts, default=DEFAULT_SPANS, key="sel_spans")
        if sel_c:
            cols = st.columns(min(4, len(sel_c)))
            for i, name in enumerate(sel_c):
                cols[i % len(cols)].metric(name, f"{snap['counters'].get(name, 0):,.4g}")
        if sel_s:
            rows = [{"span": s, "count": snap["spans"][s]["n"], "p50 ms": snap["spans"][s]["p50"],
                     "p95 ms": snap["spans"][s]["p95"]} for s in sel_s if s in snap["spans"]]
            if rows:
                df = pd.DataFrame(rows)
                st.dataframe(df, hide_index=True)
                st.bar_chart(df.set_index("span")[["p50 ms", "p95 ms"]])
            else:
                st.caption("No timings yet for the selected spans. Ask a question first.")
        if st.session_state.history:
            st.caption("Per-query latency this session")
            st.line_chart(pd.Series([h["ms"] for h in st.session_state.history], name="ms"))

    live_metrics()
