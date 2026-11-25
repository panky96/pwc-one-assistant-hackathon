"""Triage (runtime): deterministic rules stand-in for the distilled classifier.
Low confidence is handled deterministically and never routes to TRANSACTION_EXEC."""
from __future__ import annotations
import re
from dataclasses import dataclass
from app.contracts import Route

MIN_CONFIDENCE = 0.90  # proposed until calibrated on the golden set
_GREET = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|good (morning|afternoon))\b", re.I)
_TXN = re.compile(r"\b(submit|approve|transfer|delete|update|create|cancel|place order|file)\b", re.I)
_OOS = re.compile(r"\b(weather|lottery|horoscope|recipe|sports score)\b", re.I)
_QUESTION = re.compile(r"\b(what|how|why|when|where|which|who|explain|policy|summar)", re.I)


@dataclass
class TriageResult:
    route: Route
    confidence: float
    clarify: bool = False
    risk_tier: str = "TIER_1_READ"  # advisory only; tool schema/RBAC are authoritative


def triage(msg: str) -> TriageResult:
    if _GREET.match(msg) and len(msg.split()) <= 4:
        return TriageResult(Route.DIRECT_ANSWER, 0.99)
    if _OOS.search(msg):
        return TriageResult(Route.REJECT_OOS, 0.95)
    if _TXN.search(msg):
        if _QUESTION.search(msg) and not re.match(r"\s*(please\s+)?(submit|approve|transfer|delete|update|create|cancel)", msg, re.I):
            return TriageResult(Route.HYBRID_RAG, 0.80)  # read-only fallback
        return TriageResult(Route.TRANSACTION_EXEC, 0.93, risk_tier="TIER_3_MUTATE")
    if _QUESTION.search(msg):
        return TriageResult(Route.HYBRID_RAG, 0.92)
    return TriageResult(Route.HYBRID_RAG, 0.5)


def apply_confidence_policy(r: TriageResult) -> TriageResult:
    if r.confidence >= MIN_CONFIDENCE:
        return r
    if r.route == Route.TRANSACTION_EXEC:
        return TriageResult(Route.DIRECT_ANSWER, r.confidence, clarify=True, risk_tier=r.risk_tier)
    if r.route == Route.HYBRID_RAG:
        return r  # clearly non-mutating -> read-only RAG
    return TriageResult(Route.REJECT_OOS, r.confidence, clarify=True)
