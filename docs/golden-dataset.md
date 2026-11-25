# Golden Dataset Synthetic Generator for RAG Evaluation

Reference for `design.md`. Two sets, generated in batches from document chunks and stored under `tests/golden/`:

- **Release-gate set: 250 questions** (RAGAS release gates), stratified: Factoid 63, Complex Reasoning 62, Multi-Hop 50, Summary 38, Negative/Unanswerable 37 (25/25/20/15/15%, rounded to exactly 250).
- **Extended set: 500-1000 cases** for broader regression and live KPI feeding; the 250 gate set is a stratified subset or is generated first.

Release gates: Faithfulness >= 0.92, Answer Relevancy >= 0.90, Context Precision >= 0.88, Context Recall >= 0.85, Context Entity Recall >= 0.85. Negative/Unanswerable rows have no ground-truth context, so they are scored mainly by faithfulness / abstention correctness (KPI-23), not context recall.

## Role & goal

You are an expert AI Evaluation Engineer specializing in Retrieval-Augmented Generation (RAG) benchmarking. Generate a high-quality, diverse "Golden Dataset" from the provided document chunks. It is the ground-truth benchmark for Context Precision, Context Recall, Faithfulness, and Answer Relevancy in a production RAG system.

## Input

A set of document chunks or raw text snippets from a knowledge domain.

## Output schema

Return a JSON array. Each object MUST follow this schema:

```json
{
  "test_case_id": "TC_001",
  "question": "<Synthetic User Query>",
  "question_type": "<Factoid | Complex/Reasoning | Multi-Hop | Summary | Negative/Unanswerable>",
  "difficulty": "<Easy | Medium | Hard>",
  "expected_answer": "<Precise, complete, and factual ground-truth response based strictly on the context>",
  "ground_truth_context_ids": ["<ID_or_Title_of_Chunk_1>", "<ID_or_Title_of_Chunk_2>"],
  "retrieval_keywords": ["<Keyword1>", "<Keyword2>", "<Keyword3>"],
  "intent_category": "<User Intent Category e.g., Policy Lookup, Account Management, Troubleshooting>",
  "reasoning_notes": "<Brief explanation of why this test case evaluates a specific RAG capability>"
}
```

## Query diversity distribution

| # | Type | Share | Difficulty | Rules |
|---|---|---|---|---|
| 1 | Factoid | 25% | Easy | Direct, single-span factual question (e.g. "What is the return window for clearance items?"). Expected answer is concise and cited from a single chunk. |
| 2 | Complex / Reasoning | 25% | Medium | Requires synthesis, implicit logic, or applying rules to a scenario (e.g. "If a customer bought a jacket on sale 20 days ago and wore it once, are they eligible for a refund?"). Answer explains the rule logic and steps based on context. |
| 3 | Multi-Hop | 20% | Hard | Cannot be answered by one chunk; needs two or more distinct chunks. `ground_truth_context_ids` must list ALL chunks required. |
| 4 | Abstract / Summary | 15% | Medium/Hard | High-level overviews, thematic summaries, or comparisons across sections (e.g. "Summarize the exceptions to the standard warranty policy."). |
| 5 | Negative / Unanswerable | 15% | Hard | Realistic, domain-related questions that CANNOT be answered from the snippets. Expected answer: "I am sorry, but the provided documentation does not contain information regarding [Topic]." `ground_truth_context_ids`: `[]`. |

## Critical quality controls

1. **No hallucination / strict grounding** — `expected_answer` derives strictly from the context; no outside knowledge.
2. **Natural user phrasing** — don't copy phrases verbatim from the context into `question`; write like real users (colloquialisms, subtle vagueness, natural variation).
3. **Disambiguation** — questions contain enough context to be unambiguously answerable by the ground-truth context.
4. **Independent verification** — `retrieval_keywords` include both semantic synonyms and key domain entities, usable for BM25/lexical or hybrid search.

## Task

Analyze the provided context and generate a JSON array of `[N]` golden dataset test cases following the instructions and schema above. Return ONLY valid JSON.

## Validator (run on every generated batch)

- Parses as JSON; every object matches the schema (Pydantic model); `test_case_id` unique across batches.
- Type/difficulty mix within tolerance of the distribution above.
- `ground_truth_context_ids` empty **iff** `Negative/Unanswerable`; Multi-Hop has at least 2 ids.
- Every id exists in the chunk store; `retrieval_keywords` has at least 3 entries.
- Dedupe near-identical questions across batches.
- Negative/Unanswerable rows are expected to end in deterministic abstention (reranker top score < `reranker.min_score` 0.42, the retrieval-abstention route); the validator checks that the expected answer is the abstention text.
- `triage-agent (dev)` samples a subset for grounding review before the set is frozen.
