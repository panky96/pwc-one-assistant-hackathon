"""Tests for the healthcare-safety hardening PRs. Synthetic placeholders only; no clinical content."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from app import telemetry
from app.contracts import TenantContext
from app.main import create_app
from app.settings import ConfigError, Settings
from app.storage import Store
from app.transactions import OperationStore, OpState
from app.triage import risk_category
from app.vault import load_vault
from app.verification import verify
from evals.golden import validate
from evals.run_golden import classify
from evals.threshold_sweep import sweep_metrics
from mcp_servers.rag_server import Chunk

T = TenantContext(tenant_id="dev-tenant", user_id="dev-user", roles=("user",))
POLICY = "Maximum reimbursement is INR 8000 per night."


# ---- PR1: verifier acceptance cases (synthetic placeholders) ----
@pytest.mark.parametrize("claim, evidence, ok", [
    ("Test Drug A: 5 mg once daily.", "Test Drug A: 5 mg once daily.", True),            # exact
    ("Test Drug A: 5 mg twice daily.", "Test Drug A: 5 mg once daily.", False),          # frequency mismatch
    ("Test Drug A: 10 mg once daily.", "Test Drug A: 5 mg once daily.", False),          # dose mismatch
    ("Maximum reimbursement is INR 8000 per trip.", POLICY, False),                       # scope mismatch
    ("The maximum reimbursement is INR 8,000 per night.", POLICY, True),                 # supported paraphrase
    ("Entity B approves expenses.", "Entity A approves expenses.", False),               # entity mismatch
    ("Entity A approves expenses.", "Entity A approves expenses.", True),
    ("Reimbursement is 9000 per night.", POLICY, False),                                 # unsupported number
])
def test_claim_level_verification(claim, evidence, ok):
    assert verify(claim, [evidence]).ok is ok


def test_citation_must_point_at_the_supporting_chunk():
    chunks = ["Meals are reimbursable up to 50 dollars per day.", "Flights must be booked 14 days in advance."]
    assert verify("Meals are reimbursable up to 50 dollars per day [a].", chunks, chunk_ids=["a", "b"]).ok
    assert not verify("Meals are reimbursable up to 50 dollars per day [b].", chunks, chunk_ids=["a", "b"]).ok
    assert not verify("Meals are reimbursable up to 50 dollars per day [zz].", chunks, chunk_ids=["a", "b"]).ok


def test_extractive_answer_is_verified_and_labelled():
    app = create_app(Settings.from_env({}), llm=None, use_vault=True)
    r = TestClient(app).post("/v1/chat", json={"message": "What is the meal expense policy?"}).json()
    assert r["response_mode"] in ("extractive", "abstained_retrieval") and r["request_id"]
    if r["response_mode"] == "extractive":
        assert r["degraded"] is True and r["degraded_reason"] == "llm_unavailable" and r["citations"]


# ---- PR3: risk flag and response metadata ----
@pytest.mark.parametrize("msg, cat", [
    ("What is the meal expense policy?", "NORMAL"),
    ("what dosage should my patient take", "CLINICAL_DECISION_INFLUENCING"),
    ("show my patient's record", "PATIENT_SPECIFIC"),
    ("is this an overdose risk", "SAFETY_CRITICAL"),
])
def test_risk_category(msg, cat):
    assert risk_category(msg) == cat


def test_high_risk_request_is_escalated_not_answered():
    app = create_app(Settings.from_env({}), llm=None, use_vault=True)
    r = TestClient(app).post("/v1/chat", json={"message": "what dosage should my patient take"}).json()
    assert r["response_mode"] == "escalated" and r["abstained"] and r["risk"] == "CLINICAL_DECISION_INFLUENCING"


# ---- PR4: production guards and reconciliation ----
def test_production_requires_postgres():
    base = {"APP_ENV": "production", "AUTH_ENABLED": "true"}
    with pytest.raises(ConfigError):
        Settings.from_env({**base, "DATABASE_URL": "sqlite:///./dev.db"})
    assert Settings.from_env({**base, "DATABASE_URL": "postgresql://u:p@h/db"}).app_env == "production"


def test_stale_execution_moves_to_needs_review():
    t = [0.0]
    ops = OperationStore(lease_s=5, clock=lambda: t[0], store=Store(":memory:"))
    ops.propose("T", "op1", "alice", "cancel_order", {"order_id": "ORD-00001"})
    ops.decide("T", "op1", "bob", True)
    ops.store.op_transition("T", "op1", (OpState.APPROVED.value,), OpState.EXECUTING.value, ops.get("T", "op1").body(), lease_expires_at=5)
    t[0] = 10
    assert ops.reconcile() == ["op1"]
    assert ops.get("T", "op1").state == OpState.NEEDS_REVIEW


# ---- PR5: telemetry works without an exporter ----
def test_telemetry_is_noop_without_exporter():
    assert telemetry.init_otel(None, "pwc-test") is False
    telemetry.incr("unit.test", 2)
    assert telemetry.counter("unit.test") == 2


# ---- PR2: calibration and diagnostics ----
def test_threshold_sweep_metrics_on_synthetic_records():
    recs = [{"answerable": True, "top": 0.9, "gold_in_top": True},
            {"answerable": True, "top": 0.3, "gold_in_top": False},
            {"answerable": False, "top": 0.5, "gold_in_top": False}]
    m = sweep_metrics(recs, thresholds=(0.42,))["0.42"]
    assert m["false_abstention_rate"] == 0.5 and m["unanswerable_passed_gate_rate"] == 1.0
    assert m["correct_answer_rate"] == 0.5 and m["precision_of_passed_answerable"] == 1.0


def test_failure_stage_classification():
    from types import SimpleNamespace as NS
    case = NS(ground_truth_context_ids=["a"], question_type="Factoid")
    rr = NS(lexical=("b",), dense=("b",))
    resp = NS(route=NS(value="HYBRID_RAG"), abstained=True, response_mode="vetoed")
    assert classify(case, resp, rr, ["b"], {"a": 1}) == "RETRIEVAL"
    rr2 = NS(lexical=("a",), dense=("a",))
    assert classify(case, resp, rr2, ["b"], {"a": 1}) == "RERANKING"
    assert classify(case, resp, rr2, ["a"], {"a": 1}) == "VERIFICATION"


# ---- PR6: governance metadata ----
def test_only_approved_knowledge_is_loaded():
    assert all(n.status == "APPROVED" for n in load_vault().values())
    assert load_vault(approved_only=False)  # drafts can be inspected explicitly


def test_golden_review_metadata_is_validated():
    base = dict(test_case_id="a", question="What is the policy?", question_type="Factoid", difficulty="Easy",
                expected_answer="x", ground_truth_context_ids=["expense-meals"], retrieval_keywords=["k"],
                intent_category="i", reasoning_notes="r")
    assert validate([base], load_vault())[0].review_status == "GENERATED"
    with pytest.raises(ValueError):
        validate([{**base, "review_status": "HUMAN_REVIEWED"}], load_vault())  # reviewer required
    assert validate([{**base, "review_status": "HUMAN_REVIEWED", "reviewed_by": "reviewer-1"}], load_vault())
