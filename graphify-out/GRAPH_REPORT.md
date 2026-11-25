# Graph Report - src  (2026-11-13)

## Corpus Check
- Corpus is ~4,613 words - fits in a single context window. You may not need a graph.

## Summary
- 61 nodes · 95 edges · 8 communities
- Extraction: 87% EXTRACTED · 12% INFERRED · 1% AMBIGUOUS · INFERRED: 11 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Quality Gates & Evaluation
- Runtime, Config & Reliability Rules
- Auth & Pre-Execution Guards
- Agents & Observability
- Plugin Lifecycle Stages
- Router, HITL & Circuit Breaker
- Retrieval, Rerank & LLM
- Hybrid RAG Pipeline Core

## God Nodes (most connected - your core abstractions)
1. `Agent Runtime (FastAPI + MCP host)` - 6 edges
2. `Deterministic Router` - 6 edges
3. `TOOL_DISPATCH plugins` - 6 edges
4. `mcp-state-store server` - 6 edges
5. `mcp_config.json` - 6 edges
6. `Golden dataset (500-1000 cases)` - 6 edges
7. `OWASP Top 10 for LLM Apps (2025) mapping` - 6 edges
8. `Red teaming and fix loop` - 6 edges
9. `PRE_EXECUTION plugins` - 5 edges
10. `authn_gate plugin (priority 5)` - 5 edges

## Surprising Connections (you probably didn't know these)
- `Query diversity distribution` --conceptually_related_to--> `Ambiguity rule (clarify, never guess)`  [AMBIGUOUS]
  docs/golden-dataset.md → design.md
- `Golden batch validator` --calls--> `triage-agent`  [EXTRACTED]
  docs/golden-dataset.md → design.md
- `Quality gates and release rule (faithfulness >= 0.92)` --references--> `Golden dataset (500-1000 cases)`  [EXTRACTED]
  design.md → docs/golden-dataset.md
- `Dashboard (3 tabs)` --references--> `Golden dataset (500-1000 cases)`  [EXTRACTED]
  design.md → docs/golden-dataset.md
- `backend-agent` --calls--> `Red teaming and fix loop`  [INFERRED]
  design.md → docs/loadtest-and-security.md

## Hyperedges (group relationships)
- **End-to-end request flow** — design_client_ui, design_agent_runtime, design_pre_execution_plugins, design_router, design_mcp_hybrid_rag, design_onnx_reranker, design_llm, design_post_execution_plugins [EXTRACTED 1.00]
- **Golden dataset / KPI / evaluation loop** — docs_golden_dataset_golden_dataset, design_kpi_profile, docs_rag_metrics_catalog_operational_kpis, design_dashboard, design_quality_gates [INFERRED 0.85]
- **Red team and fix loop** — docs_loadtest_and_security_owasp_llm_top10, docs_loadtest_and_security_red_team_loop, design_triage_agent, design_backend_agent, docs_loadtest_and_security_release_gate [INFERRED 0.85]

## Communities (8 total, 0 thin omitted)

### Community 0 - "Quality Gates & Evaluation"
Cohesion: 0.14
Nodes (15): kpi_profile config, model_fallback plugin, Model fallback rule (escalate once), Quality gates and release rule (faithfulness >= 0.92), ragas_auditor plugin (min_faithfulness 0.92), Golden dataset (500-1000 cases), Golden case output schema, Golden dataset quality controls (+7 more)

### Community 1 - "Runtime, Config & Reliability Rules"
Cohesion: 0.24
Nodes (11): Agent Runtime (FastAPI + MCP host), Bootstrap checklist, Claude hooks (settings.json), idempotency_guard plugin, Idempotency rule (state record check), mcp_config.json, mcp-graphify-vault server, mcp-state-store server (+3 more)

### Community 2 - "Auth & Pre-Execution Guards"
Cohesion: 0.22
Nodes (11): ambiguity_gate plugin (priority 30), Ambiguity rule (clarify, never guess), AUTH_ENABLED toggle, authn_gate plugin (priority 5), Azure Entra ID auth, injection_shield plugin (priority 20), On-Behalf-Of (OBO) flow, Permission tiers (1 read, 2 reversible, 3 critical) (+3 more)

### Community 3 - "Agents & Observability"
Cohesion: 0.36
Nodes (8): Agent isolation and handoff files, backend-agent, Dashboard (3 tabs), frontend-agent, otel_exporter plugin (ALL_SPANS), triage-agent, Red teaming and fix loop, Release gate (no open High/Critical)

### Community 4 - "Plugin Lifecycle Stages"
Cohesion: 0.40
Nodes (4): Client / UI, Plugin lifecycle (stages and priorities), POST_EXECUTION plugins, PRE_EXECUTION plugins

### Community 5 - "Router, HITL & Circuit Breaker"
Cohesion: 0.83
Nodes (4): circuit_breaker plugin (loop/stall), HITL approval gate, Deterministic Router, TOOL_DISPATCH plugins

### Community 6 - "Retrieval, Rerank & LLM"
Cohesion: 0.50
Nodes (4): LLM (small to large fallback), mcp-hybrid-rag server, ONNX Reranker (bge-reranker-v2-m3), Retrieval hyperparameters (top_k, fusion, RRF, BM25)

### Community 7 - "Hybrid RAG Pipeline Core"
Cohesion: 0.67
Nodes (3): Dual memory (RAM + durable store), Hybrid RAG pipeline (5 steps), Typed contracts (Pydantic)

## Ambiguous Edges - Review These
- `Query diversity distribution` → `Ambiguity rule (clarify, never guess)`  [AMBIGUOUS]
  docs/golden-dataset.md · relation: conceptually_related_to

## Knowledge Gaps
- **7 isolated node(s):** `Hybrid RAG pipeline (5 steps)`, `Idempotency rule (state record check)`, `Model fallback rule (escalate once)`, `AUTH_ENABLED toggle`, `kpi_profile config` (+2 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 8 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Agent Runtime (FastAPI + MCP host)` connect `Runtime, Config & Reliability Rules` to `Agents & Observability`, `Plugin Lifecycle Stages`?**
  _High betweenness centrality (0.137) - this node is a cross-community bridge._
- **What connects `Hybrid RAG pipeline (5 steps)`, `Idempotency rule (state record check)`, `Model fallback rule (escalate once)` to the rest of the system?**
  _7 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Quality Gates & Evaluation` be split into smaller, more focused modules?**
  _Cohesion score 0.14285714285714285 - nodes in this community are weakly interconnected._
- **Why does `Dashboard (3 tabs)` connect `Agents & Observability` to `Quality Gates & Evaluation`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._

### Low-confidence Hints
_AMBIGUOUS edges — the extractor was unsure. Verify before acting on these._

- **What is the exact relationship between `Query diversity distribution` and `Ambiguity rule (clarify, never guess)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._