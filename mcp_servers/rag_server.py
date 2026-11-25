"""Hybrid RAG: BM25(50) + dense(50) -> RRF(k=60, fused 40) -> rerank(4, min_score 0.42).
Dense encoder and reranker are pluggable; defaults are dependency-free stand-ins
(hashing embedding, token-overlap reranker) to be replaced by BGE ONNX models."""
from __future__ import annotations
import hashlib, math, re
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Protocol
from app import telemetry

_TOK = re.compile(r"[a-z0-9]+")


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokenize(t: str) -> list[str]:
    return [_stem(w) for w in _TOK.findall(t.lower())]


@dataclass(frozen=True)
class Chunk:
    id: str
    tenant_id: str
    text: str
    dataset_version: str = "v1"


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.25, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.avg = (sum(map(len, docs)) / len(docs)) if docs else 0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.tf = [Counter(d) for d in docs]

    def scores(self, q: list[str]) -> list[float]:
        out = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = self.tf[i].get(t, 0)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * len(d) / (self.avg or 1)))
            out.append(s)
        return out


def hash_embed(text: str, dim: int = 256) -> list[float]:
    v = [0.0] * dim
    for t in tokenize(text):
        h = int(hashlib.md5(t.encode()).hexdigest(), 16)
        v[h % dim] += 1 if (h >> 8) & 1 else -1
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def overlap_rerank(query: str, text: str) -> float:
    """Stand-in cross-encoder returning a sigmoid-space score in (0,1)."""
    q, d = set(tokenize(query)), set(tokenize(text))
    if not q:
        return 0.0
    cov = len(q & d) / len(q)
    return 1 / (1 + math.exp(-8 * (cov - 0.5)))


_STOP = set("a an the of to in on for and or is are be by with at as it its that this from per must may can not no any all each what how much many do does i we you my our me your there their which who when where why about tell please give get".split())


def make_reranker(corpus_texts: list[str]) -> Callable[[str, str], float]:
    """IDF-weighted query-term coverage mapped through a sigmoid; terms unseen in the corpus carry no weight.
    Stand-in for the bge-reranker-v2-m3 cross-encoder (same score space: sigmoid in (0,1))."""
    toks = [set(tokenize(t)) for t in corpus_texts]
    n = len(toks)
    df = Counter(w for t in toks for w in t)
    idf = {w: math.log(1 + n / f) for w, f in df.items()}

    def score(query: str, text: str) -> float:
        q = [w for w in set(tokenize(query)) if w not in _STOP and w in idf]
        if not q:
            return 0.0
        d = set(tokenize(text))
        cov = sum(idf[w] for w in q if w in d) / sum(idf[w] for w in q)
        return 1 / (1 + math.exp(-8 * (cov - 0.5)))
    return score


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    s: Counter = Counter()
    for r in rankings:
        for rank, cid in enumerate(r, 1):
            s[cid] += 1 / (k + rank)
    return sorted(s.items(), key=lambda x: (-x[1], x[0]))


class Encoder(Protocol):
    def __call__(self, text: str) -> list[float]: ...


@dataclass
class RetrievalResult:
    chunks: list[tuple[Chunk, float]]
    abstain: bool
    top_score: float
    candidates: tuple = ()  # fused candidate ids (for Recall@50)


class HybridRetriever:
    def __init__(self, chunks: list[Chunk], encoder: Encoder = hash_embed,
                 reranker: Callable[[str, str], float] | None = None,
                 rrf_k: int = 60, fused_k: int = 40, min_score: float = 0.42):
        self.chunks, self.encoder = chunks, encoder
        self.reranker = reranker or make_reranker([c.text for c in chunks])
        self.backend = "custom" if (reranker or encoder is not hash_embed) else "standin"
        self.rrf_k, self.fused_k, self.min_score = rrf_k, fused_k, min_score
        self.emb = encoder.embed_docs([c.text for c in chunks]) if hasattr(encoder, "embed_docs") else [encoder(c.text) for c in chunks]
        self.bm25 = BM25([tokenize(c.text) for c in chunks])

    def retrieve(self, query: str, tenant_id: str, candidate_k: int = 50, output_k: int = 4) -> RetrievalResult:
        idx = [i for i, c in enumerate(self.chunks) if c.tenant_id in (tenant_id, "*")]  # tenant isolation; "*" = shared enterprise knowledge
        if not idx:
            return RetrievalResult([], True, 0.0)
        with telemetry.span("rag.bm25"):
            lex = self.bm25.scores(tokenize(query))
            lex_rank = [self.chunks[i].id for i in sorted(idx, key=lambda i: -lex[i]) if lex[i] > 0][:candidate_k]
        with telemetry.span("rag.dense"):
            qe = self.encoder(query)
            den_rank = [self.chunks[i].id for i in sorted(idx, key=lambda i: -cosine(qe, self.emb[i]))][:candidate_k]
        fused = rrf([lex_rank, den_rank], self.rrf_k)[: self.fused_k]
        byid = {c.id: c for c in self.chunks}
        with telemetry.span("rag.rerank"):
            fc = [byid[cid] for cid, _ in fused]
            sc = self.reranker.batch(query, [c.text for c in fc]) if hasattr(self.reranker, "batch") else [self.reranker(query, c.text) for c in fc]
            scored = sorted(zip(fc, sc), key=lambda x: -x[1])[:output_k]
        cands = tuple(cid for cid, _ in rrf([lex_rank, den_rank], self.rrf_k)[:candidate_k])
        top = scored[0][1] if scored else 0.0
        if top < self.min_score:  # only reranker score space is compared to min_score
            return RetrievalResult([], True, top, cands)
        return RetrievalResult(scored, False, top, cands)
