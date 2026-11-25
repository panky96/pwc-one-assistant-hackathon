# Token Optimization using Obsidian & Graphify

Reference for `design.md`. Eliminate prompt bloat and reduce LLM input token volume by 45-65% while lowering TTFT.

**Graphify is optional.** Hybrid RAG is the correctness path; Graphify is a token/context optimization. If Graphify is disabled, unavailable, or cannot resolve an anchor, the platform must still return grounded Hybrid RAG results (never an empty context while valid retrieval evidence exists). KPI-26 applies only when Graphify is enabled.

## 1. Atomic Obsidian knowledge vault

Convert all enterprise policies, SOPs, and product catalogs into atomic Markdown notes stored in `knowledge_vault/`. Each note contains strict YAML frontmatter (`id`, `domain`, `risk_tier`, `version`) and explicit `[[wikilinks]]` to related rules.

```markdown
---
id: POLICY-REFUND-007
domain: returns
risk_tier: medium        # policy classification; separate from runtime TIER_* permission tiers
version: 3
---
One rule per note. State the rule, its conditions and exceptions in a few sentences.
Related: [[POLICY-REFUND-006]], [[POLICY-WARRANTY-002]]
```

Validator (CI): fails on missing/invalid frontmatter, duplicate `id`, or dangling `[[wikilinks]]`.

## 2. Graphify topology subgraph pruning

Execute `graphify build ./src ./knowledge_vault --output graph.json` to generate a unified structural dependency graph of Python modules, Pydantic schemas, and Obsidian policy nodes. At runtime, `mcp-graphify-vault` resolves the exact query anchor node and traverses at most `depth=2` edges, injecting only the minimal connected subgraph (~320 tokens vs. ~1,400 tokens raw chunk dump).

Notes:
- **Command check (implementation-time verification)**: the Graphify CLI used in this project exposes `/graphify <path>` (output in `graphify-out/graph.json`) plus `graphify query|path|explain|export`. Do not claim `graphify build ./src ./knowledge_vault --output graph.json` works until it is verified against the installed version (`graphify --help`); the equivalent is running Graphify over `./src` and `./knowledge_vault`.
- **Numbers**: 320 vs 1,400 tokens is a 77% reduction for that block (illustrative, not measured). Design target for whole prompts: 45-65%; pass gate KPI-26: >= 45%; stack-table goal: 55%. Real values come from the token counter hook.

## 3. MCP contract (`mcp-graphify-vault`)

| Tool | Input | Output |
|---|---|---|
| `resolve_anchor` | triage `domain`, normalized entity IDs, query | anchor note `id` (or miss) |
| `get_subgraph` | `anchor`, `depth` (<= 2), `max_tokens` | note ids + versions + compressed text + provenance |

Anchor resolution strategy: normalized entity/note ID -> BM25 over note titles -> Hybrid RAG fallback. **Miss/failure behavior**: fall back to Hybrid RAG chunks; never an empty context.

## 4. Config (proposed)

```json
"graphify": {
  "vault_path": "knowledge_vault/",
  "source_paths": ["src/"],
  "graph_file": "graphify-out/graph.json",
  "max_depth": 2,
  "max_subgraph_tokens": 400,
  "anchor_resolution": ["note_id", "bm25_title"]
}
```

## 5. Build and refresh

Offline in CI (Graphify semantic extraction costs model tokens and time): rebuild when the vault or source changes; cache by note `version`; stamp the graph artifact with the note versions; sign/pin the artifact (governance rules in `docs/loadtest-and-security.md`).

## 6. Quality and security guardrails

- A pruned subgraph is compressed context. RAGAS faithfulness/recall (KPI-10/11/20/22) are scored against the text actually given to the model; every injected claim keeps `note id + version` so citations and the Verification Subagent's claim-to-chunk NLI check work.
- Run the golden set twice (raw chunks vs subgraph); no release if gates regress.
- Vault notes are an indirect-injection surface (red-team category 2): tenant scoping, PII-free content, `injection_shield` over retrieved text.
- Two vaults stay semantically separate: `knowledge_vault/` = enterprise/product knowledge used for grounded retrieval (read-only at runtime); `second-brain/` = project/development/session knowledge. If one MCP server exposes both, tenant/ACL/source boundaries must remain explicit and second-brain content must never contaminate production enterprise retrieval. The final tenant/ACL policy is open.

## 7. Measurement

KPI-26 baseline = raw chunk dump for the same query; the token counter hook reports before/after per request. Effects on TTFT feed KPI-25 and cost per 1,000 queries feeds KPI-29.
