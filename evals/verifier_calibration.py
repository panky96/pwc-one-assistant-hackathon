"""Measure the claim verifier against hand-labelled answers (data/labels/verifier_labels.json).
No model calls. Reports false rejects (correct answers blocked) and false accepts (unsupported answers let through)."""
from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path
from app.verification import verify

LABELS = Path(__file__).resolve().parent.parent / "data" / "labels" / "verifier_labels.json"
RESULTS = Path(__file__).resolve().parent / "results"


def run(items: list[dict]) -> dict:
    per = defaultdict(lambda: {"n": 0, "false_reject": 0, "false_accept": 0})
    rows = []
    for it in items:
        ids = [cid for cid, _ in it["ev"]]
        texts = [t for _, t in it["ev"]]
        v = verify(it["ans"], texts, chunk_ids=ids)
        predicted = "supported" if v.ok else "unsupported"
        c = per[it["cat"]]
        c["n"] += 1
        if it["label"] == "supported" and predicted == "unsupported":
            c["false_reject"] += 1
        if it["label"] == "unsupported" and predicted == "supported":
            c["false_accept"] += 1
        rows.append({"id": it["id"], "cat": it["cat"], "label": it["label"], "predicted": predicted,
                     "unsupported_claims": v.unsupported})
    sup = [r for r in rows if r["label"] == "supported"]
    uns = [r for r in rows if r["label"] == "unsupported"]
    summary = {
        "n": len(rows),
        "false_reject_rate": sum(r["predicted"] == "unsupported" for r in sup) / len(sup),
        "false_accept_rate": sum(r["predicted"] == "supported" for r in uns) / len(uns),
        "by_category": dict(per),
    }
    summary["rows"] = rows
    return summary


if __name__ == "__main__":
    items = json.loads(LABELS.read_text())
    out = run(items)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "verifier_calibration.json").write_text(json.dumps(out, indent=2))
    print(f"n={out['n']}  false_reject_rate={out['false_reject_rate']:.1%}  false_accept_rate={out['false_accept_rate']:.1%}")
    for cat, c in out["by_category"].items():
        print(f"  {cat:18s} n={c['n']:3d}  false_reject={c['false_reject']:3d}  false_accept={c['false_accept']:3d}")
