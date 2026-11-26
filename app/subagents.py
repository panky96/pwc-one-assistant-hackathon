"""Subagent isolation (design.md table): each stage may only call the tool tiers it is allowed."""
from __future__ import annotations
from dataclasses import dataclass
from app.contracts import Tier


@dataclass(frozen=True)
class Subagent:
    name: str
    allowed_tiers: frozenset
    servers: frozenset


RETRIEVAL = Subagent("retrieval", frozenset({Tier.READ, Tier.COMPUTE}), frozenset({"hybrid-rag", "graphify-vault"}))
EXECUTION = Subagent("execution", frozenset({Tier.READ, Tier.COMPUTE, Tier.MUTATE}), frozenset({"state-store"}))
VERIFICATION = Subagent("verification", frozenset({Tier.READ}), frozenset({"state-store"}))
