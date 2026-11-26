"""Claim-level verifier (deterministic, fail-closed).
A claim is supported only when every content word and every number in it appears in the single evidence sentence it
is aligned to. If the claim cites a chunk, alignment is restricted to that chunk. Word overlap and question wording
do not count as support. Known limits: paraphrases that add or drop content words, and claims that combine two
evidence sentences, are rejected; that produces abstention, never an unsupported answer."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from mcp_servers.rag_server import tokenize

# Negation, quantity and scope words are deliberately NOT stop words.
STOP = set("a an the of to in on for and or is are be by with at as it its that this from per".split())
_CITE = re.compile(r"\[([^\]\s]+)\]")
_COMMA = re.compile(r"(?<=\d),(?=\d{3}\b)")
_META = re.compile(r"^(answer|based on[^,:]*|according to[^,:]*|sources?)\s*[:,]\s*", re.I)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def normalize(text: str) -> str:
    return _COMMA.sub("", text)  # 8,000 -> 8000 so numbers compare as one token


# Reviewed equivalences: wording changes that keep the meaning. Direction words (under, below, at least, up to)
# and modal words (must, should) are deliberately absent, so flips in them still fail.
EQUIV = {"reimbursement": "reimburse", "reimbursable": "reimburse", "reimbursed": "reimburse",
         "above": "over", "staff": "employee", "employees": "employee", "earn": "accrue", "receive": "accrue",
         "entitled": "accrue", "compulsory": "mandatory", "authorized": "approve", "need": "require",
         "kept": "retain", "retained": "retain", "reserved": "book", "booked": "book", "ahead": "advance",
         "paid": "pay", "payment": "pay", "changed": "rotate", "rotated": "rotate", "submitted": "submit",
         "every": "all", "can": "may", "allowed": "may", "covered": "reimburse", "alcoholic": "alcohol",
         "annually": "year", "remotely": "remote"}
NUMBER_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
                "seven": "7", "eight": "8", "nine": "9", "ten": "10", "twelve": "12"}
# Filler that carries no factual content on its own.
FILLER = {"according", "policy", "states", "stated", "limit", "amount", "have", "you", "your", "they", "them",
          "it", "this", "those", "each", "has", "using", "drinks", "drink", "state"}
NEGATIONS = {"not", "never", "no", "none", "cannot", "without", "nor", "neither"}


def _norm(w: str) -> str:
    return EQUIV.get(NUMBER_WORDS.get(w, w), NUMBER_WORDS.get(w, w))


def content_tokens(text: str) -> list[str]:
    return [_norm(w) for w in tokenize(normalize(text)) if w not in STOP and w not in FILLER]


@dataclass
class Verdict:
    ok: bool
    support: float
    unsupported: list[str]
    details: list[dict] = field(default_factory=list)


def _sentences(text: str) -> list[str]:
    out = []
    for line in normalize(text).splitlines():
        for s in re.split(r"(?<=[.!?])\s+", line.strip()):
            if s.strip():
                out.append(s.strip())
    return out


def split_claims(answer: str) -> list[str]:
    claims = []
    for s in _sentences(answer):
        s = _META.sub("", _BULLET.sub("", s)).strip()
        if s:
            claims.append(s)
    return claims


def _check(claim_text: str, pool: list[tuple[str, str]]) -> tuple[str, list[str]]:
    ct = set(content_tokens(claim_text))
    if not ct:
        return "empty", []
    best_set, best_sent, best_overlap = None, "", 0
    for _, sent in pool:
        st = set(content_tokens(sent))
        overlap = len(ct & st)
        if overlap > best_overlap:
            best_set, best_sent, best_overlap = st, sent, overlap
    if best_set is None:
        return "no_evidence", sorted(ct)
    # Asymmetric negation check: a dropped or added negation changes the meaning even if every word is present.
    claim_neg = set(tokenize(normalize(claim_text))) & NEGATIONS
    evidence_neg = set(tokenize(normalize(best_sent))) & NEGATIONS
    if claim_neg != evidence_neg:
        return "negation_mismatch", sorted(claim_neg ^ evidence_neg)
    missing = sorted(ct - best_set)
    return ("supported" if not missing else "missing_terms"), missing


def verify(answer: str, chunks: list[str], min_support: float = 1.0, chunk_ids: list[str] | None = None) -> Verdict:
    ids = list(chunk_ids) if chunk_ids is not None else [f"chunk-{i}" for i in range(len(chunks))]
    sents = [(cid, s) for cid, text in zip(ids, chunks) for s in _sentences(text)]
    claims = split_claims(answer)
    if not claims:
        return Verdict(False, 0.0, [], [])
    unsupported, details = [], []
    for claim in claims:
        cited = _CITE.findall(claim)
        pool = [(cid, s) for cid, s in sents if cid in cited] if cited else sents
        status, missing = _check(_CITE.sub("", claim), pool) if pool else ("citation_mismatch" if cited else "no_evidence", [])
        details.append({"claim": claim, "status": status, "missing_terms": missing})
        if status not in ("supported", "empty"):
            unsupported.append(claim)
    support = 1 - len(unsupported) / len(claims)
    return Verdict(support >= min_support, support, unsupported, details)
