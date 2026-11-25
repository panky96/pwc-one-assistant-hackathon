# PwC One Assistant
Design: @design.md (deep dives in docs/). Safety rules live in plugins (fail closed), never only in prompts.

## Commands
- `uv run pytest` - unit/integration tests (MCP stdio + HTTP servers, Streamlit AppTest)
- `uv run uvicorn app.main:create_app --factory --env-file .env --reload` - API
- `uv run streamlit run app.py` - developer workbench (6 tabs)
- `uv run python -m evals.redteam` - 100-prompt red-team suite (CI rule: 0 misses)
- `uv run python -m evals.golden` - (re)generate the 250-case golden set via OpenRouter (costs tokens)
- `uv run python -m evals.run_golden [--llm]` - golden evaluation against RAGAS-style gates
- `uv run python -m evals.kpis` - 36-KPI scorecard (unmeasured KPIs fail closed)
- `uv run python scripts/run_load.py load 300 20s` - local k6 capacity run (not production certification)
- `uv run python scripts/build_vault.py` - rebuild knowledge_vault/ demo notes

## Gotchas
- `app.py` (workbench) and `app/` (package) share a name by design; the package wins on import.
- Tenant identity comes only from validated token claims. With AUTH_ENABLED=false a fixed dev tenant is used.
- `.env` holds secrets (gitignored); never commit it.
