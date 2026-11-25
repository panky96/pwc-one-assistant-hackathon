import json
from collections import Counter
from pathlib import Path
import pytest
from evals import redteam
from evals.golden import OUT, TYPES, load_golden, validate
from evals.run_golden import ap, ndcg
from app.vault import load_vault


def test_redteam_suite_has_100_prompts_and_zero_misses():
    assert len(redteam.build_cases()) == 100
    s = redteam.run_suite()
    assert s["misses"] == 0, s["failures"]


def test_golden_validator_rejects_bad_rows():
    notes = load_vault()
    base = dict(test_case_id="a", question="What is the policy?", question_type="Factoid", difficulty="Easy", expected_answer="x",
                ground_truth_context_ids=["expense-meals"], retrieval_keywords=["k"], intent_category="i", reasoning_notes="r")
    validate([base], notes)
    for bad in ({"ground_truth_context_ids": ["nope"]}, {"question_type": "Multi-Hop"}, {"question_type": "Negative/Unanswerable"}):
        with pytest.raises(Exception):
            validate([{**base, **bad}], notes)
    with pytest.raises(ValueError):
        validate([base, base], notes)  # duplicate id


@pytest.mark.skipif(not Path(OUT).exists(), reason="golden set not generated")
def test_golden_set_distribution():
    cases = load_golden()
    assert len(cases) == 250
    assert Counter(c.question_type for c in cases) == Counter({k: v[1] for k, v in TYPES.items()})


def test_ir_metrics():
    assert ap(["a", "b"], {"a"}) == 1.0 and ap(["b", "a"], {"a"}) == 0.5 and ap(["x"], {"a"}) == 0.0
    assert ndcg(["a"], {"a"}) == 1.0
