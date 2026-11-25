# RAG Hyperparameter & Metric Catalog

Reference for `design.md`. The platform knows all of these; each product enables only the subset its use case needs (`kpi_profile` + `hyperparameters` in config). Config keys are suggestions.

**Retrieval key mapping (authoritative pipeline)**: BM25 `lexical.candidate_k` = 50 and Dense `dense.candidate_k` = 50 (`dense.ef_search` = 64) -> RRF (`fusion.rrf_k` = 60) -> `fusion.fused_k` = 40 -> reranker `reranker.output_k` = 4 with `reranker.min_score` = 0.42 -> `generation.max_context_chunks` = 4. Catalog #7/#26 map to the candidate/fused/output sizes, #12 to `rrf_k`, #19 to `fused_k` (candidates passed to the reranker), #8/#38 to `reranker.min_score` (reranker sigmoid space). **BM25 score, cosine similarity, RRF score and reranker sigmoid score are different score spaces and are never interchangeable.** `max_context_tokens` (#21) is model/profile dependent and separate from the 180-token observation cap used inside the reasoning loop.

## Catalog (50)

### Ingestion & indexing

| No. | Hyperparameter | Meaning | Config key |
|---|---|---|---|
| 1 | Chunk Size | Defines the amount of text stored in each document chunk. | `chunk_size` |
| 2 | Chunk Overlap | Specifies how much text neighboring chunks share to preserve context. | `chunk_overlap` |
| 3 | Chunking Strategy | Determines how documents are split into retrievable units. | `chunking_strategy` |
| 4 | Embedding Model | Converts text into numerical vectors for semantic search. | `embedding_model` |
| 5 | Embedding Dimensions | Sets the number of values representing each text vector. | `embedding_dims` |
| 6 | Vector Similarity Metric | Measures how similar two embedding vectors are. | `similarity_metric` |
| 25 | Semantic Chunking Threshold | Sets the semantic-change criterion used to decide where text should be split. | `semantic_chunk_threshold` |

### Retrieval

| No. | Hyperparameter | Meaning | Config key |
|---|---|---|---|
| 7 | Top-K Retrieval | Sets the number of top-ranked results retrieved for a query. | `lexical.candidate_k`, `dense.candidate_k` |
| 8 | Similarity Score Threshold | Defines the minimum similarity score for accepting a result. | `reranker.min_score` (sigmoid space) |
| 9 | Dense Retrieval Weight | Controls the contribution of semantic vector search to the combined ranking. | `dense_weight` |
| 10 | Sparse Retrieval Weight | Controls the contribution of keyword-based search to the combined ranking. | `sparse_weight` |
| 11 | Hybrid Search Fusion Method | Specifies how dense and sparse search results are combined. | `fusion_method` |
| 12 | Reciprocal Rank Fusion (RRF) Constant | Controls how strongly ranked positions influence RRF score calculations. | `fusion.rrf_k` |
| 13 | BM25 Parameters (k1, b) | Controls keyword-frequency saturation and document-length normalization in BM25 search. | `bm25_k1`, `bm25_b` |
| 14 | Query Expansion | Adds related terms to improve retrieval coverage. | `query_expansion` |
| 15 | Query Rewriting | Reformulates the original query to improve search effectiveness. | `query_rewriting` |
| 16 | Multi-Query Retrieval Count | Sets the number of alternative queries generated for searching. | `multi_query_count` |
| 17 | Metadata Filtering | Restricts retrieval using attributes such as date, category, or source. | `metadata_filters` |
| 23 | Diversity / MMR Lambda | Balances relevance against diversity when selecting retrieved results. | `mmr_lambda` |
| 24 | Parent-Child Retrieval | Retrieves a smaller matching passage while optionally returning its larger parent context. | `parent_child` |
| 26 | Maximum Retrieved Documents | Caps the number of documents retained for downstream processing. | `fusion.fused_k`, `reranker.output_k`, `generation.max_context_chunks` |

### Reranking & context assembly

| No. | Hyperparameter | Meaning | Config key |
|---|---|---|---|
| 18 | Reranking Model | Re-scores retrieved passages according to their relevance to the query. | `reranker_model` |
| 19 | Reranking Top-N | Sets how many retrieved candidates are passed to the reranker. | `fusion.fused_k` (output: `reranker.output_k`) |
| 20 | Context Window Size | Defines the maximum input context the language model can process. | `context_window` |
| 21 | Maximum Context Tokens | Limits the number of retrieved-context tokens supplied to the model. | `max_context_tokens` (model-profile dependent) |
| 22 | Context Compression | Removes redundant or irrelevant information from retrieved passages. | `context_compression` |

### Generation

| No. | Hyperparameter | Meaning | Config key |
|---|---|---|---|
| 27 | Prompt Template | Defines how the question, retrieved context, and instructions are structured. | `prompt_template` |
| 28 | System Instruction | Specifies the model's role, rules, and response constraints. | `system_instruction` |
| 29 | Generation Model | Selects the language model used to generate the final answer. | `generation_model` |
| 30 | Temperature | Controls randomness in generated responses. | `temperature` |
| 31 | Top-P | Limits token selection to a probability-based nucleus of likely candidates. | `top_p` |
| 32 | Maximum Output Tokens | Sets the upper limit on the length of the generated answer. | `max_output_tokens` |
| 33 | Frequency Penalty | Discourages repeated tokens based on how often they have appeared. | `frequency_penalty` |
| 34 | Presence Penalty | Discourages reuse of tokens that have already appeared in the response. | `presence_penalty` |

### Quality gates & operations

| No. | Hyperparameter | Meaning | Config key |
|---|---|---|---|
| 35 | Groundedness Threshold | Defines the minimum acceptable degree of support from the retrieved evidence. | `groundedness_threshold` |
| 36 | Answer Relevance Threshold | Sets the minimum acceptable relevance of the generated answer to the query. | `answer_relevance_threshold` |
| 37 | Citation Enforcement | Determines whether claims must include references to supporting sources. | `require_citations` |
| 38 | Abstention Threshold | Defines when the system should decline to answer because evidence is insufficient. | `reranker.min_score` (single parameter; reranker top score < 0.42 => deterministic abstention) |
| 39 | Confidence Threshold | Sets the minimum confidence required to accept or present a response. | `confidence_threshold` |
| 40 | Retrieval Latency Timeout | Limits how long the retrieval process may run before timing out. | `retrieval.timeout_ms` (150 ms proposed) and `retrieval.stage_timeouts`; the 30 s MCP timeout is only a catastrophic ceiling |
| 41 | Cache TTL | Specifies how long cached queries or retrieval results remain valid. | `cache_ttl_s` |

### Evaluation

| No. | Hyperparameter / Metric | Meaning | Config key |
|---|---|---|---|
| 42 | Evaluation Dataset Size | Defines the number of test examples used to evaluate RAG performance. | `eval_dataset_size` |
| 43 | Retrieval Precision@K | Measures the proportion of the top-K retrieved results that are relevant. | metric |
| 44 | Retrieval Recall@K | Measures the proportion of all known relevant results found within the top K. | metric |
| 45 | Mean Reciprocal Rank (MRR) | Measures how highly the first relevant result is ranked across queries. | metric |
| 46 | Normalized Discounted Cumulative Gain (nDCG) | Evaluates ranking quality while accounting for relevance and result position. | metric |
| 47 | Faithfulness Score | Measures whether the generated answer is supported by the provided context. | metric |
| 48 | Answer Relevance Score | Measures how directly the answer addresses the user's question. | metric |
| 49 | Context Precision | Measures how much of the retrieved context is relevant to the query. | metric |
| 50 | Context Recall | Measures how much of the required supporting information is captured by the retrieved context. | metric |

## Operational KPIs (beyond the 50)

| KPI | Definition |
|---|---|
| Groundedness score | Faithfulness of the answer to retrieved context (#47, gated by #35) |
| Throughput | Completed queries per second |
| First-token latency | Split into **request TTFT** (request accepted -> first streamed token) and **model TTFT** (LLM dispatch -> first model token); reported per route/provider/model tier (KPI-25) |
| Overall latency | Request start to final response, **per route**; approval wait excluded (KPI-31..33) |
| Word Error Rate (WER) | Conditional: only if voice/ASR input exists (open); not part of the 36 KPIs |
| Recall / Precision | Retrieval recall/precision vs `ground_truth_context_ids` (#44, #43, #49, #50) |
| Answer Relevancy | Same as #48 (RAGAS name); release gate >= 0.90 |
| Context Entity Recall | **Not in the 50-item catalog.** RAGAS metric: share of entities in the ground truth that appear in the retrieved context; release gate >= 0.85 |

## Choosing KPIs by use case

Set `kpi_profile` to one profile. Track the KPIs; tune the hyperparameters.

| Use case | KPIs to track | Hyperparameters to tune first |
|---|---|---|
| Factual QA / policy lookup | Faithfulness, context precision/recall, Recall@K, abstention rate | `lexical/dense.candidate_k`, `fusion.fused_k`, `reranker.min_score`, `require_citations` |
| Regulated / high-risk | Faithfulness (>= 0.92), citation coverage, abstention correctness | `groundedness_threshold`, `require_citations`, `confidence_threshold`, HITL tiers |
| Search-heavy / large corpus | MRR, nDCG, Precision/Recall@K | `dense_weight`, `sparse_weight`, `fusion.rrf_k`, `fusion.fused_k`, `metadata_filters` |
| Conversational / multi-turn | Answer relevance, faithfulness | `query_rewriting`, `cache_ttl_s`, memory settings |
| Low-latency / high-scale | First-token and overall latency, throughput, error rate | `retrieval.timeout_ms`, `cache_ttl_s`, `lexical/dense.candidate_k`, `context_compression` |
| Summarization / multi-hop | Context recall, answer relevance | `multi_query_count`, `parent_child`, `mmr_lambda`, `max_context_tokens` |

## Dashboard

The six-tab developer workbench (`app.py`) is specified in `docs/dev-workbench.md`. Tab 2 hyperparameter controls and Tab 6 KPIs are generated from this catalog and the active `kpi_profile`; every live query feeds its KPIs/telemetry to those tabs. The 36-KPI certification matrix is in `docs/system-spec.md` section 1.5.
