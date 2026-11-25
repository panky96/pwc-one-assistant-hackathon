# Request Workflow

Mermaid below is the **source of truth** for the workflow. `docs/workflow-diagram.png` was drawn by hand before the architecture reconciliation (runtime triage, async approval, verification gate, optional Graphify) and is **stale: regeneration pending**. No Mermaid renderer is installed in this environment, so the PNG was not regenerated; do not treat it as current. Interactive knowledge graph of the docs: `graphify-out/graph.html`.

## Request flow

```mermaid
flowchart TD
    C[Client / UI] --> GW[NGINX / Envoy gateway]
    GW --> API[Agent Runtime<br/>FastAPI + MCP host]

    subgraph PRE[PRE_EXECUTION - fail closed]
        A5[authn_gate p5<br/>TenantContext from validated Entra claims] --> A10[pii_redactor p10] --> A20[injection_shield p20] --> A30[ambiguity_gate p30]
    end
    API --> A5
    A30 -->|missing params| CL[Structured clarification<br/>no tool call]
    CL --> C
    A30 -->|clear| CACHE{Semantic cache<br/>identity match then similarity}
    CACHE -->|hit| OUT
    CACHE -->|miss| TR{Triage Agent runtime<br/>4 routes, min_confidence}

    TR -->|low confidence + possibly transactional| CL
    TR -->|low confidence + knowledge request| RAG
    TR -->|REJECT_OOS| REJ[Deterministic refusal] --> C
    TR -->|DIRECT_ANSWER| LLM
    TR -->|HYBRID_RAG| RS[Retrieval Subagent]
    TR -->|TRANSACTION_EXEC| ES[Execution Subagent]

    subgraph RET[Hybrid RAG - correctness path]
        RS --> RAG[Lexical candidate_k 50<br/>Dense candidate_k 50 ef_search 64]
        RAG --> RRF[RRF k=60 -> fused_k 40]
        RRF --> RR[Reranker output_k 4]
    end
    RS -. optional .-> GR[Graphify subgraph depth 2<br/>fallback: Hybrid RAG chunks]
    GR -.-> RR
    RR -->|top score < min_score 0.42| ABS[Deterministic abstention] --> OUT
    RR -->|context chunks| LLM[LLM tier<br/>hosted Anthropic default]

    subgraph TXN[TOOL_DISPATCH - TIER_2 / TIER_3]
        ES --> CB[loop guard + failure/backend breakers] --> IG[idempotency_guard<br/>UNIQUE tenant_id, operation_id]
        IG -->|duplicate| HIST[Return stored result] --> LLM
        IG -->|TIER_3_MUTATE| PA[(PENDING_APPROVAL)]
        PA --> AP{Reviewer<br/>approver != requester}
        AP -->|APPROVED| EX[EXECUTING<br/>lease + heartbeat] --> DONEOP[EXECUTED / FAILED / manual review]
        AP -->|DENIED / EXPIRED| LLM
        DONEOP --> LLM
    end

    LLM --> VS[Verification Subagent<br/>synchronous veto]
    VS -->|ungrounded and quality gate fails| MF[model_fallback<br/>small -> large once]
    MF --> VS
    VS -->|grounded| OUT[Grounded, cited answer]
    OUT --> C

    API -. spans .-> OT[OTel exporter]
    OUT -. async, sampled .-> RA[ragas_auditor<br/>monitoring only]
    ST[(mcp-state-store<br/>Postgres + Redis)] <--> API
```

Notes: the agent turn pauses in `AWAITING_APPROVAL` while the durable operation waits in `PENDING_APPROVAL`; that wait is excluded from turn latency. `HALTED` (loop guard) and `HALTED_BREAKER` (failure/backend breaker) end the turn with a structured remediation prompt. Hybrid RAG still returns grounded results when Graphify is disabled or fails.

## Quality and security loop

```mermaid
flowchart LR
    GD[Golden dataset: 250 gate set + extended set] --> EV[RAGAS release gates + active KPIs]
    EV --> GATE{Regression?}
    GATE -->|no| REL[Release]
    GATE -->|yes| FIX[Fix plugin / config / schema]
    RT[Red team: 100-prompt suite, 0 misses] --> TR[triage-agent dev report]
    TR --> FIX
    FIX --> REG[Add regression test] --> EV
    REL --> LT[Load tests A-D: smoke, 8,500 RPS capacity, tenant isolation, 1-hour soak]
```
