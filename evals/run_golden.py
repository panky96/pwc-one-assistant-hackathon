"""Golden-set evaluation with per-failure diagnostics.
Retrieval metrics are exact. Generation metrics are deterministic proxies of RAGAS (faithfulness = claim-level support
against the cited chunks; relevancy = expected-answer content coverage; entity recall = expected entities found in the
retrieved context). Use --llm to score real model answers via OpenRouter.
Each failed case is labelled with the stage that caused it: TRIAGE, RETRIEVAL, RERANKING, CONTEXT_BUILDING,
GENERATION, VERIFICATION or EVALUATION_DATA. Writes evals/results/diagnostics.jsonl."""
from __future__ import annotations
import json, math, os, re, sys, time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from app import telemetry
from app.contracts import TenantContext
from app.main import create_app
from app.settings import Settings
from app.verification import content_tokens, verify
from app.vault import load_vault
from evals.golden import load_golden

RESULTS = Path(__file__).resolve().parent / "results"
GATES = {"faithfulness": 0.92, "answer_relevancy": 0.90, "context_precision": 0.88, "context_recall": 0.85, "context_entity_recall": 0.85}
_ENT = re.compile(r"\d+(?:\.\d+)?|\b[A-Z][a-z]{3,}\b")
UNANS = "Negative/Unanswerable"


def ap(ranked, gt):
    hits, s = 0, 0.0
    for k, cid in enumerate(ranked, 1):
        if cid in gt:
            hits += 1
            s += hits / k
    return s / hits if hits else 0.0


def ndcg(ranked, gt):
    dcg = sum(1 / math.log2(i + 2) for i, c in enumerate(ranked) if c in gt)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gt), len(ranked) or 1)))
    return dcg / ideal if ideal else 0.0


def classify(case, resp, rr, ranked, notes) -> str | None:
    """Return the failing stage, or None when the case behaved as expected."""
    gt = set(case.ground_truth_context_ids)
    if gt and not gt <= set(notes):
        return "EVALUATION_DATA"
    if case.question_type == UNANS:
        return None if resp.abstained else "GENERATION"
    if resp.route.value != "HYBRID_RAG":
        return "TRIAGE"
    if not gt & (set(rr.lexical) | set(rr.dense)):
        return "RETRIEVAL"
    if not gt & set(ranked):
        return "RERANKING"
    if not resp.abstained:
        return None
    mode = resp.response_mode or ""
    if mode == "abstained_retrieval":
        return "RERANKING"
    if mode in ("vetoed", "extractive_rejected", "verifier_unavailable"):
        return "VERIFICATION"
    if mode in ("insufficient", "llm_error"):
        return "GENERATION"
    return "CONTEXT_BUILDING"


def evaluate(use_llm: bool = False, limit: int | None = None, workers: int = 6) -> dict:
    cases = load_golden()[:limit]
    notes = load_vault()
    texts = {n.id: n.title + ". " + " ".join(n.facts) for n in notes.values()}
    s = Settings.from_env(dict(os.environ) if use_llm else {})
    app = create_app(s, llm="auto" if use_llm else None, use_vault=True, graphify=False)
    agent = app.state.agent
    telemetry.reset()
    ten = TenantContext(tenant_id="eval", user_id="eval", roles=("user",))

    def one(c):
        t0 = time.perf_counter()
        r = agent.handle(c.question, ten)
        lat = (time.perf_counter() - t0) * 1000
        rr = agent.retriever.retrieve(c.question, "eval")
        ranked = [x.id for x, _ in rr.chunks]
        return c, r, rr, ranked, lat

    with ThreadPoolExecutor(workers if use_llm else 1) as ex:
        rows = list(ex.map(one, cases))
    ans = [x for x in rows if x[0].question_type != UNANS]
    neg = [x for x in rows if x[0].question_type == UNANS]
    m = {k: [] for k in ("precision", "recall", "recall50", "mrr", "ndcg", "faith", "relev", "entity", "answered")}
    for c, r, rr, ranked, lat in ans:
        gt = set(c.ground_truth_context_ids)
        m["precision"].append(ap(ranked, gt))
        m["recall"].append(len(gt & set(ranked)) / len(gt))
        m["recall50"].append(len(gt & set(rr.candidates)) / len(gt))
        m["mrr"].append(next((1 / k for k, x in enumerate(ranked, 1) if x in gt), 0.0))
        m["ndcg"].append(ndcg(ranked, gt))
        ctx = " ".join(texts[i] for i in ranked if i in texts)
        ents = set(_ENT.findall(c.expected_answer))
        m["entity"].append(len(ents & set(_ENT.findall(ctx))) / len(ents) if ents else 1.0)
        m["answered"].append(0.0 if r.abstained else 1.0)
        if not r.abstained:
            if r.citations:
                ids = [x.chunk_id for x in r.citations]
                m["faith"].append(verify(r.answer, [texts[i] for i in ids], chunk_ids=ids).support)
            else:
                m["faith"].append(1.0)
            exp = content_tokens(c.expected_answer)
            m["relev"].append(len(set(exp) & set(content_tokens(r.answer))) / len(exp) if exp else 1.0)
        else:
            m["relev"].append(0.0)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    lats = sorted(x[4] for x in rows)
    out = {
        "n": len(rows), "mode": "llm" if use_llm else "extractive", "backend": os.getenv("RETRIEVAL_BACKEND", "standin"),
        "faithfulness": mean(m["faith"]), "answer_relevancy": mean(m["relev"]), "context_precision": mean(m["precision"]),
        "context_recall": mean(m["recall"]), "context_entity_recall": mean(m["entity"]),
        "recall_at_50": mean(m["recall50"]), "mrr_at_4": mean(m["mrr"]), "ndcg_at_4": mean(m["ndcg"]),
        "answerable_answered_rate": mean(m["answered"]),
        "abstention_accuracy": mean([1.0 if r.abstained else 0.0 for _, r, *_ in neg]),
        "latency_ms_p50": lats[len(lats) // 2], "latency_ms_p95": lats[int(.95 * len(lats))],
        "cost_usd_per_1000": telemetry.counter("llm.cost_usd") / max(1, len(rows)) * 1000 if use_llm else None,
        "escalations": telemetry.counter("model.escalations"), "vetoed": telemetry.counter("verification.vetoed"),
    }
    out["gates"] = {k: {"value": out[k], "gate": g, "pass": out[k] >= g} for k, g in GATES.items()}
    # per-failure diagnostics
    stages, diag = Counter(), []
    for c, r, rr, ranked, lat in rows:
        stage = classify(c, r, rr, ranked, notes)
        if stage is None:
            continue
        stages[stage] += 1
        diag.append({"id": c.test_case_id, "type": c.question_type, "failure_stage": stage, "route": r.route.value,
                     "response_mode": r.response_mode, "top_score": round(rr.top_score, 4),
                     "gold_ids": sorted(c.ground_truth_context_ids), "ranked_ids": ranked,
                     "bm25_top5": list(rr.lexical[:5]), "dense_top5": list(rr.dense[:5]),
                     "answer_preview": r.answer[:160], "question": c.question})
    out["failure_stages"] = dict(stages)
    out["spans"] = telemetry.snapshot()["spans"]
    RESULTS.mkdir(exist_ok=True)
    tag = "llm" if use_llm else "extractive"
    (RESULTS / f"golden_{tag}.json").write_text(json.dumps(out, indent=2))
    (RESULTS / "diagnostics.jsonl").write_text("\n".join(json.dumps(d) for d in diag) + ("\n" if diag else ""))
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(".env")
    o = evaluate(use_llm="--llm" in sys.argv)
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in o.items() if k not in ("spans", "gates")}, indent=1))
    for k, g in o["gates"].items():
        print(f"{k:24s} {g['value']:.3f} (gate {g['gate']}) {'PASS' if g['pass'] else 'FAIL'}")
