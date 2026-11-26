"""Offline abstention-threshold sweep on the golden set. Retrieval gate only (no LLM calls).
Shows the trade-off per candidate threshold. It does not choose one: reviewers set
retrieval.reranker.min_score in mcp_config.json after reading the report.
Writes evals/results/threshold_sweep.json."""
from __future__ import annotations
import json, os
from pathlib import Path
from app.main import create_app
from app.settings import Settings
from evals.golden import load_golden

RESULTS = Path(__file__).resolve().parent / "results"
THRESHOLDS = (0.30, 0.35, 0.40, 0.42, 0.45, 0.50, 0.55, 0.60)
UNANS = "Negative/Unanswerable"


def sweep_metrics(records: list[dict], thresholds=THRESHOLDS) -> dict:
    """records: {answerable: bool, top: float, gold_in_top: bool}"""
    ans = [r for r in records if r["answerable"]]
    neg = [r for r in records if not r["answerable"]]
    total = len(records) or 1
    out = {}
    for t in thresholds:
        ans_pass = [r for r in ans if r["top"] >= t]
        neg_pass = [r for r in neg if r["top"] >= t]
        correct = [r for r in ans_pass if r["gold_in_top"]]
        out[f"{t:.2f}"] = {
            "answer_rate": (len(ans_pass) + len(neg_pass)) / total,
            "false_abstention_rate": (len(ans) - len(ans_pass)) / len(ans) if ans else 0.0,
            "unanswerable_passed_gate_rate": len(neg_pass) / len(neg) if neg else 0.0,
            "correct_answer_rate": len(correct) / len(ans) if ans else 0.0,
            "precision_of_passed_answerable": len(correct) / len(ans_pass) if ans_pass else None,
        }
    return out


def collect(agent, cases) -> list[dict]:
    recs = []
    for c in cases:
        rr = agent.retriever.retrieve(c.question, "eval")
        gt = set(c.ground_truth_context_ids)
        recs.append({"id": c.test_case_id, "answerable": c.question_type != UNANS, "top": rr.top_score,
                     "gold_in_top": bool(gt & {x.id for x, _ in rr.chunks})})
    return recs


def main():
    from dotenv import load_dotenv
    load_dotenv(".env")
    app = create_app(Settings.from_env(), llm=None, use_vault=True)
    recs = collect(app.state.agent, load_golden())
    table = sweep_metrics(recs)
    RESULTS.mkdir(exist_ok=True)
    backend = os.getenv("RETRIEVAL_BACKEND", "standin")
    (RESULTS / "threshold_sweep.json").write_text(json.dumps({"n": len(recs), "backend": backend,
                                                             "current_min_score": app.state.agent.retriever.min_score,
                                                             "thresholds": table}, indent=2))
    print(f"backend={backend} n={len(recs)}")
    for t, m in table.items():
        print(f"{t}  answer={m['answer_rate']:.2f}  false_abstain={m['false_abstention_rate']:.2f}  "
              f"unanswerable_passed={m['unanswerable_passed_gate_rate']:.2f}  correct={m['correct_answer_rate']:.2f}")
    print("No threshold was selected. Review the report, then set retrieval.reranker.min_score in mcp_config.json.")


if __name__ == "__main__":
    main()
