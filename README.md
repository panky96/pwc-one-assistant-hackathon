# PwC One Assistant

Enterprise MCP-based assistant implementing [design.md](design.md). Quick start:

```bash
uv sync
cp .env.example .env            # add OPENROUTER_API_KEY to enable real LLM answers (otherwise extractive answers)
uv run pytest
uv run uvicorn app.main:create_app --factory --env-file .env --reload
uv run streamlit run ui/workbench.py     # developer workbench
```

## Layout
| Path | Purpose |
|---|---|
| `app/` | settings, contracts, auth (Entra JWT + OBO), triage, harness, bounded loop, agent, LLM client (OpenRouter), verification, cache, storage (SQLite, hash-chained audit), telemetry, FastAPI |
| `plugins/` | PII redactor, injection shield, loop/failure breakers, idempotency, RAGAS auditor, model fallback |
| `mcp_servers/` | `rag_server.py` (hybrid retrieval library), `rag_mcp.py` / `graphify_mcp.py` (stdio), `state_mcp.py` (Streamable HTTP) |
| `mcp_config.json` | server/plugin/retrieval/resilience/observability config (validated by `app/mcp_config.py`) |
| `knowledge/` | atomic Obsidian notes with frontmatter + wikilinks (demo, PII-free) |
| `evals/` | red team (100), golden set (250), golden runner, 36-KPI scorecard; results in `evals/results/` |
| `load_tests/`, `scripts/run_load.py` | k6 constant-arrival-rate scenarios (smoke/load/soak/isolation) |
| `app.py` | six-tab Streamlit developer workbench (dev/staging only) |

## Known stand-ins (see design.md open items)
Dense encoder and reranker are lightweight stand-ins for BGE ONNX models; SQLite stands in for Postgres; the in-memory cache stands in for Redis
(a Redis backend exists, enable with `USE_REDIS=1` and `REDIS_URL`); Graphify is a native wikilink-graph implementation; RAGAS metrics are deterministic proxies.
