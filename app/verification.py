"""Verification subagent (synchronous veto): claim-to-chunk support check (deterministic NLI stand-in)."""
from __future__ import annotations
import re
from dataclasses import dataclass
from mcp_servers.rag_server import tokenize

STOP = set("a an the of to in on for and or is are be by with at as it its that this from per must may can not no any all each".split())
_CITE = re.compile(r"\[[^\]]*\]")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def content_tokens(t: str) -> set[str]:
    return {w for w in tokenize(t) if w not in STOP}


@dataclass
class Verdict:
    ok: bool
    support: float
    unsupported: list[str]


def claim_supported(claim: str, ctx_tokens: set[str], ctx_numbers: set[str], min_cov: float = 0.6) -> bool:
    ct = content_tokens(claim)
    if not ct:
        return True
    nums = set(_NUM.findall(claim))
    if nums and not nums <= ctx_numbers:  # any number not in evidence => unsupported
        return False
    return len(ct & ctx_tokens) / len(ct) >= min_cov


def verify(answer: str, chunks: list[str], min_support: float = 1.0) -> Verdict:
    ctx = " ".join(chunks)
    ct, cn = content_tokens(ctx), set(_NUM.findall(ctx))
    claims = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", _CITE.sub("", answer)) if s.strip()]
    if not claims:
        return Verdict(False, 0.0, [])
    bad = [c for c in claims if not claim_supported(c, ct, cn)]
    sup = 1 - len(bad) / len(claims)
    return Verdict(sup >= min_support, sup, bad)
