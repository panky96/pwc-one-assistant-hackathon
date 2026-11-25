"""Golden-set evaluation. Retrieval metrics are exact; generation metrics are deterministic proxies of RAGAS
(faithfulness = claim-to-chunk support, answer relevancy = expected-answer content coverage, entity recall = entities of the
expected answer present in retrieved context). Use `--llm` to evaluate real model answers via OpenRouter."""
from __future__ import annotations
import json, math, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from fastapi.testclient import TestClient
from app import telemetry
from app.contracts import Route
from app.main import create_app
from app.settings import Settings
from app.verification import content_tokens, verify
from evals.golden import load_golden

RESULTS = Path(__file__).resolve().parent / "results"
GATES = {"faithfulness": 0.92, "answer_relevancy": 0.90, "context_precision": 0.88, "context_recall": 0.85, "context_entity_recall": 0.85}
_ENT = re.compile(r"\d+(?:\.\d+)?|\b[A-Z][a-z]{3,}\b")
GT = dict


def ap(ranked: list[str], gt: set[str]) -> float:
    hits, s = 0, 0.0
    for k, cid in enumerate(ranked, 1):
        if cid in gt:
            hits += 1; s += hits / k
    return s / hits if hits else 0.0


def ndcg(ranked, gt):
    dcg = sum(1 / math.log2(i + 2) for i, c in enumerate(ranked) if c in gt)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(gt), len(ranked) or 1)))
    return dcg / ideal if ideal else 0.0


def evaluate(use_llm: bool = False, limit: int | None = None, workers: int = 6) -> dict:
    cases = load_golden()[:limit]
    s = Settings.from_env(dict(os.environ) if use_llm else {})
    app = create_app(s, llm="auto" if use_llm else None, use_vault=True, graphify=False)
    client = TestClient(app); agent = app.state.agent
    telemetry.reset()
    from app.contracts import TenantContext
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
    ans = [x for x in rows if x[0].question_type != "Negative/Unanswerable"]
    neg = [x for x in rows if x[0].question_type == "Negative/Unanswerable"]
    notes = {c.id: c.text for c in agent.retriever.chunks}
    m = {k: [] for k in ("precision", "recall", "recall50", "mrr", "ndcg", "faith", "relev", "entity", "answered")}
    for c, r, rr, ranked, lat in ans:
        gt = set(c.ground_truth_context_ids)
        m["precision"].append(ap(ranked, gt)); m["recall"].append(len(gt & set(ranked)) / len(gt))
        m["recall50"].append(len(gt & set(rr.candidates)) / len(gt))
        m["mrr"].append(next((1 / k for k, x in enumerate(ranked, 1) if x in gt), 0.0)); m["ndcg"].append(ndcg(ranked, gt))
        ctx = " ".join(notes[i] for i in ranked)
        ents = set(_ENT.findall(c.expected_answer))
        m["entity"].append(len(ents & set(_ENT.findall(ctx))) / len(ents) if ents else 1.0)
        m["answered"].append(0.0 if r.abstained else 1.0)
        if not r.abstained:
            m["faith"].append(verify(r.answer, [notes[x.chunk_id] for x in r.citations]).support if r.citations else 1.0)
            exp = content_tokens(c.expected_answer)
            m["relev"].append(len(exp & content_tokens(r.answer)) / len(exp) if exp else 1.0)
        else:
            m["relev"].append(0.0)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    out = {
        "n": len(rows), "mode": "llm" if use_llm else "extractive",
        "faithfulness": mean(m["faith"]), "answer_relevancy": mean(m["relev"]), "context_precision": mean(m["precision"]),
        "context_recall": mean(m["recall"]), "context_entity_recall": mean(m["entity"]),
        "recall_at_50": mean(m["recall50"]), "mrr_at_4": mean(m["mrr"]), "ndcg_at_4": mean(m["ndcg"]),
        "answerable_answered_rate": mean(m["answered"]),
        "abstention_accuracy": mean([1.0 if r.abstained else 0.0 for _, r, *_ in neg]),
        "latency_ms_p50": sorted(x[4] for x in rows)[len(rows) // 2], "latency_ms_p95": sorted(x[4] for x in rows)[int(.95 * len(rows))],
        "cost_usd_per_1000": telemetry.counter("llm.cost_usd") / max(1, len(rows)) * 1000 if use_llm else None,
        "escalations": telemetry.counter("model.escalations"), "vetoed": telemetry.counter("verification.vetoed"),
    }
    out["gates"] = {k: {"value": out[k], "gate": g, "pass": out[k] >= g} for k, g in GATES.items()}
    out["spans"] = telemetry.snapshot()["spans"]
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / ("golden_llm.json" if use_llm else "golden_extractive.json")).write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    from dotenv import load_dotenv; load_dotenv(".env")
    o = evaluate(use_llm="--llm" in sys.argv)
    print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in o.items() if k not in ("spans", "gates")}, indent=1))
    for k, g in o["gates"].items(): print(f"{k:24s} {g['value']:.3f} (gate {g['gate']}) {'PASS' if g['pass'] else 'FAIL'}")
