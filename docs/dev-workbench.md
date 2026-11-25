# Multi-Tab Streamlit Developer Testing Workbench (`app.py`)

Reference for `design.md`. Build an interactive developer control plane in Streamlit with six dedicated engineering tabs for live mid-check verification. Run: `uv run streamlit run app.py`. Dev/staging only (`APP_ENV != production`); a stub/mock-data mode lets it run before the backend exists. It is a developer tool, not the production UI.

| Streamlit Developer Tab | Interactive Controls, Live Inspectors & Verification Widgets |
|---|---|
| Tab 1: Architecture & Workflow | Renders `design.md` contract checklist, displays the workflow diagram (`docs/workflow-diagram.png`, rendered from the Mermaid source in `docs/workflow-diagram.md`; currently stale/regeneration pending), live Triage routing console, ReAct step-by-step trace accordion, and Tier 3 HITL Approve/Reject queue. |
| Tab 2: 3-Stage Hybrid RAG Lab | Side-by-side table comparing Stage 1 BM25 scores, Stage 2 Bi-Encoder cosine scores, RRF fused ranks, and Stage 3 Cross-Encoder scores with interactive `k1`, `b`, `RRF k` and `min_score` (reranker sigmoid) sliders; each stage's scores are shown in their own score space. |
| Tab 3: MCP & Plugin Inspector | Live status of all connected MCP servers (`hybrid-rag`, `state-store`, `graphify-vault`), JSON-RPC payload inspector, and hot-toggle switches for Pre/Post execution plugins. |
| Tab 4: Memory & Graphify Tokens | Interactive Graphify subgraph visualizer, Obsidian atomic note viewer, live SQLite/Redis session state browser, and real-time Token Savings & Cost Reduction gauge. |
| Tab 5: Red Team & Circuit Breaker | One-click adversarial test runner (direct/indirect injections, obfuscated PII, infinite SKU retry loop, cross-tenant cache probe) with live Circuit Breaker trip/reset controls. |
| Tab 6: 36-KPI Scorecard & Load | Live OpenTelemetry span waterfall, RAGAS Triad gauges, k6/JMeter latency histograms per route (p50/p95/p99, read-only) from the 8,500 RPS arrival-rate test, and automated 36-KPI Pass/Fail release scorecard. |

## Data sources per tab

| Tab | Source |
|---|---|
| 1 | OTel spans, loop state machine, triage output (`TriageOut`), approval records (Postgres) |
| 2 | `mcp-hybrid-rag` per-stage outputs; slider defaults `k1=1.25`, `b=0.75`, `RRF k=60`, `min_score=0.42` |
| 3 | MCP `initialize`/ping, `mcp_config.json` plugin entries |
| 4 | `mcp-graphify-vault`, `knowledge_vault/`, Redis/Postgres session state (`session_facts`), token counter hook |
| 5 | `tests/redteam/` suite, circuit breaker state |
| 6 | OTel/RAGAS results, k6/JMeter result files or Influx/Prometheus, the `kpis` config |

The per-query KPI stream ("live feeding") updates Tabs 2, 4 and 6 as each query completes.

## Guardrails

1. **Image**: the workbench displays `docs/workflow-diagram.png`; it is stale until regenerated from the Mermaid source (regeneration pending).
2. **Plugin toggles**: toggling `pii_redactor`, `injection_shield` or `authn_gate` off defeats `fail_closed`. Safety plugins are non-toggleable, or a toggle needs confirmation and is written to the hash-chained audit log. Requires auth and a developer role when `AUTH_ENABLED=true`.
3. **Approval queue**: applies the production rules (approver != requester, reviewer role, tenant scope, `(tenant_id, operation_id)` uniqueness and atomic state transitions); no bypass.
4. **Data exposure**: the session browser shows redacted, tenant-scoped fields only; never raw tokens or secrets.
5. **Red-team runner**: only against your own dev/staging endpoint, synthetic tenants for the cross-tenant probe; "infinite SKU retry loop" is example wording for the loop-escape test (KPI-16). Results go to the `triage-agent (dev)` report.
6. **Load tab is read-only**: it displays k6/JMeter results per route and overlays the KPI-31..33 thresholds (p50 <= 240, p95 <= 450, p99 <= 590 for fast routes; `HYBRID_RAG` proposed targets shown separately); it never launches a load test. KPI-25 and KPI-31..33 are shown per route/model tier.
7. **Scorecard honesty**: KPIs without a recent measurement show "not measured" (not green). KPI-31..36 update only from load-test runs.
