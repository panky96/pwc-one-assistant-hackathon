# Load Testing, Security & Auth

Reference for `design.md`. Load tests run only against systems you own (dev/staging), never shared or third-party hosts. All numbers are proposed targets or illustrations, not measured results.

## 1. Load testing model

**Certification is defined by offered arrival rate, not by VU count.** The throughput target is **8,500 requests/second** (KPI-35), generated with a k6 `constant-arrival-rate` scenario. `maxVUs` may be set up to 50,000 as a *capacity ceiling*; 50,000 VUs is not 50,000 simultaneous requests and not 50,000 RPS.

Little's Law, `L = lambda x W`: at lambda = 8,500 RPS and W = 0.240 s, about **2,040 requests are in flight** (illustrative capacity arithmetic, not a guaranteed production measurement). If latency rises, more VUs/in-flight capacity are needed to keep offering the same rate, which is why `maxVUs` is a ceiling.

### Auth modes (name the mode in every report)

| Mode | Settings | Valid for |
|---|---|---|
| Infrastructure capacity mode | `AUTH_ENABLED=false`, isolated staging only, no production data, no external mutation tools | capacity/bottleneck discovery; **not** valid for production end-to-end certification (omits token validation/authorization overhead) |
| Production-representative certification mode | `AUTH_ENABLED=true`, pre-minted per-tenant test tokens | certifying KPI-31..36 including auth and tenant-isolation overhead |

### The four test types

| Test | Purpose | Shape | Pass criteria |
|---|---|---|---|
| A. Smoke | scripts/environment sanity | 10-50 VUs, 1-5 min (k6 MCP server + InfluxDB/Grafana stack) | no errors; scripts valid |
| B. Load / capacity | certify throughput and latency | 8,500 RPS constant-arrival-rate, 10-15 min, `maxVUs` up to 50,000 | KPI-31..35 (table below) |
| C. Tenant isolation / security | prove isolation under load | explicit cross-tenant probes, e.g. token for tenant A -> resource of tenant B -> expected 403; cache probes with synthetic tenants | KPI-28 = 0 leaks; 100% 403 on cross-tenant access. Does **not** rely on the API echoing `tenant_id` |
| D. Soak | stability | 1 hour at the target rate | KPI-36 (RSS growth <= 3.0%); also memory growth, connection/resource leakage, Redis stability, Postgres pool exhaustion, latency degradation, error rate |

### Thresholds mapped to KPIs (single source: the KPI matrix)

| Threshold | KPI | Value | Scope |
|---|---|---|---|
| p50 latency | KPI-31 | <= 240 ms | fast routes (`DIRECT_ANSWER`, retrieval abstention, semantic-cache hit) |
| p95 latency | KPI-32 | <= 450 ms | fast routes; `HYBRID_RAG` completion p95 <= 2500 ms (proposed) |
| p99 latency | KPI-33 | <= 590 ms (< 600 ms) | fast routes; `HYBRID_RAG` p99 from pilot |
| 5xx / error rate | KPI-34 | <= 0.05% | all routes at 8,500 RPS |
| Throughput | KPI-35 | >= 8,500 RPS sustained | offered via arrival rate |
| 1-hour soak RSS growth | KPI-36 | <= 3.0% | test D |

The load scripts use exactly these values; there is no second threshold set. Latency KPIs certify only in production-representative mode.

### k6 notes

- Scenario: `constant-arrival-rate` (rate 8,500 per second, `preAllocatedVUs`/`maxVUs` sized from the pilot, `maxVUs` up to 50,000).
- `BASE_URL` from an environment variable; never hard-code a host.
- Representative **varied queries** with a realistic cache hit/miss mix (a single fixed query would inflate cache hits and distort KPI-27).
- `Authorization`: pre-minted per-tenant test tokens (certification mode) or the auth-disabled isolated staging profile (capacity mode).
- Safe handling: guard JSON parsing on non-JSON error bodies; count 5xx separately; no reliance on the response echoing `tenant_id`.
- Distributed execution (k6 operator or several generators); one machine cannot generate this load.
- **`load_tests/k6_50k_load.js` as committed is a closed-loop script** (ramping VUs with `sleep(1)`, single fixed query, no `Authorization` header, thresholds p50 < 250 / p99 < 600). It does not match this model and is **superseded**; it is to be rewritten at implementation time (no code is changed in this documentation pass). Its closed-loop shape would offer roughly 40,000 RPS at 50K VUs, far above the 8,500 RPS target.

### JMeter notes (independent cross-check)

- Controller + workers in non-GUI mode with a comma-separated worker list: `jmeter -n -t <plan>.jmx -R host1,host2,... -l results.jtl -e -o <empty-dir>/`.
- Thread group scheduler enabled with duration configured; a "loop forever" group without a scheduler never ends.
- **Throughput semantics are per JVM/worker**: the Constant Throughput Timer value applies to each worker, so the global 8,500 RPS (510,000/min) is split across workers (e.g. N workers -> 510,000/N per minute each; worker count comes from the pilot).
- Production certification uses **Postgres**, not SQLite (SQLite is local dev only), with tenant-scoped queries.
- Do not use an unsupported JSONPath comparison such as `$.faithfulness >= 0.92` as if JMeter evaluates it. A valid approach (to be chosen at implementation time) is a JSON filter-expression match or a JSR223 assertion; the response must define the field it checks.
- Heap, OS limits (file descriptors, ephemeral ports) and injector counts are **derived from pilot testing**; 5,000 threads per JVM is not assumed feasible.
- Restrict saved `.jtl` fields; the dashboard output folder must be empty or non-existent.
- The Duration Assertion (e.g. 600 ms) is a per-sample cap, not the p99 KPI.

### Capacity sizing (pilot-derived)

baseline deployment -> controlled pilot -> measure sustainable RPS per replica -> calculate required replicas -> add 30-50% operational headroom -> certification run -> tune/autoscale -> repeat. No replica, GPU, Redis shard, autoscaling or injector numbers are asserted. Bulkhead pools (`rag_pool`, `db_pool`, `llm_pool`, proposed `control_pool`) are separate protections and their totals do not prove 50K capacity; excess load queues or is shed with 429.

### Optional self-hosted profile

When `model_serving.mode: self_hosted` is chosen, the serving tier (vLLM/TensorRT-LLM with continuous batching and prefix caching) needs its own GPU sizing from pilots. The default hosted Anthropic path has no such component; provider throughput and rate limits are managed through platform rate limiting, retries and fallback.

### Observability of load tests

Metrics export to InfluxDB/Prometheus + Grafana (`load_tests/docker-compose.yaml`, provisioned datasource, dashboard with VUs, request rate, error rate, HTTP duration); workbench Tab 6 reads the results.

## 2. OWASP Top 10 for LLM Applications (2025) mapping

| Risk | Mitigation in this design |
|---|---|
| LLM01 Prompt Injection | `injection_shield`; sanitize MCP tool results and retrieved text before they reach the model |
| LLM02 Sensitive Information Disclosure | `pii_redactor`; tenant-isolated cache and state; zero raw PII persistence; no raw tokens/PII in logs |
| LLM03 Supply Chain | Locked dependencies; verified model artifacts; allow-listed and signed MCP server manifests |
| LLM04 Data & Model Poisoning | Ingestion validation; provenance (note id + version) on every chunk |
| LLM05 Improper Output Handling | Schema-validated tool calls; Verification Subagent (synchronous); `ragas_auditor` monitoring |
| LLM06 Excessive Agency | Permission tiers; async HITL for `TIER_3_MUTATE`; idempotency; `ambiguity_gate` |
| LLM07 System Prompt Leakage | No secrets in prompts; output filtering |
| LLM08 Vector & Embedding Weaknesses | Per-tenant namespaces/filters; access control on `rag://` resources; cache identity contract |
| LLM09 Misinformation | Grounding, citations, faithfulness gate, deterministic abstention |
| LLM10 Unbounded Consumption | Loop guard + breakers; bounded loop; timeouts; per-tenant rate limits; bulkhead pools |

MCP-specific: guard against tool poisoning and confused-deputy; require auth and TLS on any Streamable HTTP endpoint.

## 3. Governance, red teaming & fix loop

### Governance & security-in-build

- **Hash-chained audit log** (`prev_hash + event_payload`, SHA-256). Hash chaining alone does **not** create immutability. Immutability requires append-only/restricted writer permissions, ordering semantics, verification, and external/trusted anchoring (or an equivalent protection against privileged rewriting). Chain scope (per tenant vs global) and the anchoring technology are open.
- **Tenant isolation**: every SQLite/Postgres query takes `tenant_id`; Redis keys use `t:{tenant_id}:`; all values come from the trusted `TenantContext`.
- **Zero raw PII persistence**: redact or hash before storing `session_facts`, approval `args`, audit payloads and cache entries; a test scans stores for PII patterns. See the persistence/PII table in `docs/system-spec.md` (ADR-007).
- **Signed MCP manifests**: verify signature and pinned digest before enabling a server (`security.allow_unknown_servers: false`). The signature algorithm and key management are open.

### Red teaming suite

An automated 100-prompt adversarial suite covering: (1) direct jailbreaks & system prompt extraction, (2) indirect prompt injection hidden inside retrieved Obsidian/PDF chunks, (3) multi-turn crescendo social engineering, (4) Unicode homoglyph & `name[at]domain` PII smuggling, and (5) tool argument overflow / SQLi fuzzing.

- Layout: `tests/redteam/`, one file per category (20 prompts each, proposed). Mapping: 1 -> LLM01/LLM07, 2 -> LLM01 indirect/LLM04, 3 -> LLM01 multi-turn, 4 -> LLM02, 5 -> LLM05/LLM06; KPI-02/03/06.
- Harness: garak, promptfoo or PyRIT (choose per project), against a stub and the live model.
- **CI rule: 0 misses in the 100-prompt suite.** 100 prompts only resolve 1% steps, so KPI-03 (>= 99.5%) is a larger-sample target.
- Also cover duplicate/replayed writes, ambiguity-forced guessing, resource exhaustion, cross-tenant retrieval, auth bypass and token replay.
- Fix loop: score attack success per category -> triage by `triage-agent (dev)` -> fix a plugin/config/schema -> add a regression test -> re-run. Release gate: no open High/Critical findings. Report: finding, OWASP ID, reproduction, fix, retest status.

## 4. Authentication & authorization (Azure Entra ID, formerly Azure AD)

Single source of truth: `security.authentication.enabled` in `mcp_config.json`, resolved from the `AUTH_ENABLED` environment variable and parsed as a **strict boolean** (`true` | `false`; anything else, e.g. `yes`, `1`, `TRUEE`, is a startup error).

- **Production rule**: `APP_ENV=production` and `AUTH_ENABLED != true` => **application startup failure** (not merely request failure).
- **`AUTH_ENABLED=false`**: local development / isolated staging only; print a startup warning.
- **`AUTH_ENABLED=true`**: implemented by the `authn_gate` plugin (PRE_EXECUTION, priority 5, fail_closed) and a FastAPI dependency:
  1. Require `Authorization: Bearer <token>`.
  2. Validate the JWT: signature against the tenant JWKS (cache with key rotation), `iss`, `aud`, `exp`/`nbf`, `tid`.
  3. Authorize: the user must hold the required role or group; handle group-overage with a Microsoft Graph lookup.
  4. Build `TenantContext` **from validated claims only** (never from request fields such as `X-Tenant-ID` or a body `tenant_id`).
  5. **On-Behalf-Of (OBO)** for downstream Azure-protected APIs (MSAL `acquire_token_on_behalf_of`), cached per user, scopes per MCP server. Never pass the raw inbound token to the model or tools.
  6. Propagate `TenantContext` to DB queries, Redis keys, semantic cache, MCP calls, audit, `session_facts` and approval records; never log tokens.
  7. Failure: 401 (unauthenticated) or 403 (not authorized).
- **UI**: the workbench uses MSAL auth-code flow when enabled.
- **Secrets**: prefer managed identity or certificates over `AZURE_CLIENT_SECRET`; never commit secrets.
- **Tests**: boolean parsing; production startup refusal; missing/expired/wrong-audience/wrong-tenant token -> 401; valid token without role -> 403; request-field tenant spoofing ignored; OBO exchange mocked; token replay in red team.
- **Open**: role vs group authorization model; single- vs multi-tenant app registration.
