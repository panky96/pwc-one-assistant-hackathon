"""PwC One Assistant - chat console (Streamlit, dev/staging).
Ask questions, see routing and grounded answers, approve or deny tier-3 actions, and watch selected live metrics.
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
from ui_style import MODE_KIND, ROUTE_KIND, apply_theme, badge, card, esc, hero, kpi

load_dotenv()
st.set_page_config(page_title="PwC One Assistant", page_icon="🧭", layout="wide")
apply_theme()

TENANT = TenantContext(tenant_id="dev-tenant", user_id="dev-user", roles=("user",))
APPROVER = TenantContext(tenant_id="dev-tenant", user_id="dev-approver", roles=("user", "approver"))
KNOWN_COUNTERS = ["requests", "blocked", "cache.hit", "abstain.retrieval", "loop.tasks", "loop.steps", "tool.calls",
                  "tool.invalid", "subagent.denied", "llm.errors", "llm.cost_usd", "model.escalations",
                  "verification.vetoed", "verifier.unavailable", "risk.escalations", "ragas.sampled", "ragas.flagged",
                  "shed.rag_pool", "shed.llm_pool", "ops.needs_review"]
KNOWN_SPANS = ["hook.pre", "triage", "retrieval", "rag.bm25", "rag.dense", "rag.rerank", "verify", "llm.generate",
               "llm.call", "llm.ttft", "mcp.call"]
DEFAULT_COUNTERS = ["requests", "abstain.retrieval", "risk.escalations", "verification.vetoed"]
DEFAULT_SPANS = ["retrieval", "rag.bm25", "rag.dense", "rag.rerank"]


@st.cache_resource
def backend():
    settings = Settings.from_env()
    api = create_app(settings, use_vault=True)  # loads the approved knowledge vault
    return settings, api.state.agent


settings, agent = backend()
if settings.app_env == "production" or settings.auth_enabled:
    st.error("This console is for development only. Use an authenticated client in production.")
    st.stop()

st.session_state.setdefault("history", [])
st.session_state.setdefault("session_id", uuid.uuid4().hex)
hero("PwC One Assistant", "Grounded answers from the approved knowledge base, with approvals and live metrics.")

left, right = st.columns([3, 2], gap="large")


def render_answer(item):
    r = item["resp"]
    with st.chat_message("assistant", avatar="🧭"):
        tags = badge(r["route"].replace("_", " ").title(), ROUTE_KIND.get(r["route"], "neutral"))
        if r.get("response_mode"):
            tags += badge(r["response_mode"].replace("_", " "), MODE_KIND.get(r["response_mode"], "neutral"))
        if r.get("degraded"):
            tags += badge("degraded: " + (r.get("degraded_reason") or ""), "warn")
        if r.get("risk") and r["risk"] != "NORMAL":
            tags += badge("risk: " + r["risk"].replace("_", " ").lower(), "bad")
        if r["abstained"]:
            tags += badge("abstained", "neutral")
        st.markdown(tags + f'<span class="pwc-muted">&nbsp; {item["ms"]:.0f} ms</span>', unsafe_allow_html=True)
        st.markdown(f'<div class="pwc-answer">{esc(r["answer"])}</div>', unsafe_allow_html=True)
        if r["citations"]:
            src = " ".join(badge(f"{c['chunk_id']} · {c['score']:.2f}", "info") for c in r["citations"])
            st.markdown(f'<div class="pwc-muted">Sources</div>{src}', unsafe_allow_html=True)
        if r["clarification"]:
            st.info("Needed to proceed: " + ", ".join(r["clarification"]))
        if r["operation_id"]:
            st.warning(f"Awaiting human approval · `{r['operation_id']}`")
        if r.get("request_id"):
            st.markdown(f'<div class="pwc-muted">request {esc(r["request_id"][:12])}</div>', unsafe_allow_html=True)


with left:
    st.subheader("Conversation")
    if not st.session_state.history:
        card('<span class="pwc-muted">Try: "What is the meal expense policy?" or '
             '"Cancel order ORD-00012" to see the approval flow.</span>', title="Get started")
    for item in st.session_state.history:
        with st.chat_message("user", avatar="🙂"):
            st.write(item["q"])
        render_answer(item)

    query = st.chat_input("Ask a policy question, or request an action")
    if query:
        t0 = time.perf_counter()
        resp = agent.handle(query, TENANT, session_id=st.session_state.session_id)
        ms = (time.perf_counter() - t0) * 1000
        st.session_state.history.append({"q": query, "resp": resp.model_dump(mode="json"), "ms": ms})
        st.rerun()

    st.subheader("Pending approvals")
    pending = agent.ops.pending(TENANT.tenant_id)
    if not pending:
        card('<span class="pwc-muted">Nothing waiting for approval.</span>')
    for op in pending:
        body = (f'<div><b>{esc(op.tool)}</b> {esc(op.args)}</div>'
                f'<div class="pwc-muted">requested by {esc(op.requester)} · {esc(op.operation_id[:8])}</div>')
        card(body)
        c1, c2 = st.columns(2)
        for col, label, ok in ((c1, "Approve", True), (c2, "Deny", False)):
            if col.button(label, key=f"{label}-{op.operation_id}", use_container_width=True,
                          type="primary" if ok else "secondary"):
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
            cols = st.columns(min(3, len(sel_c)))
            for i, name in enumerate(sel_c):
                cols[i % len(cols)].markdown(card_kpi(name, snap["counters"].get(name, 0)), unsafe_allow_html=True)
        if sel_s:
            rows = [{"span": s, "count": snap["spans"][s]["n"], "p50 ms": round(snap["spans"][s]["p50"] or 0, 2),
                     "p95 ms": round(snap["spans"][s]["p95"] or 0, 2)} for s in sel_s if s in snap["spans"]]
            if rows:
                df = pd.DataFrame(rows)
                st.dataframe(df, hide_index=True)
                st.bar_chart(df.set_index("span")[["p50 ms", "p95 ms"]], color=["#6366f1", "#db2777"])
            else:
                st.caption("No timings yet for the selected spans. Ask a question first.")
        if st.session_state.history:
            st.caption("Latency per query this session (ms)")
            st.line_chart(pd.Series([h["ms"] for h in st.session_state.history], name="ms"), color="#6366f1")

    def card_kpi(name, value):
        return f'<div class="pwc-card">{kpi(name, f"{value:,.4g}")}</div>'

    live_metrics()
