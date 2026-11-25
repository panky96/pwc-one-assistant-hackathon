"""ONNX-runtime encoder + cross-encoder reranker (via fastembed). Defaults are the design models;
override with EMBED_MODEL / RERANK_MODEL (e.g. bge-small-en-v1.5 and bge-reranker-v2-m3-int8 for faster/lighter dev use)."""
from __future__ import annotations
import math, os

DEFAULT_EMBED = "BAAI/bge-large-en-v1.5"
DEFAULT_RERANK = "BAAI/bge-reranker-v2-m3"


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class OnnxEncoder:
    def __init__(self, name: str | None = None):
        from fastembed import TextEmbedding
        self.name = name or os.getenv("EMBED_MODEL", DEFAULT_EMBED)
        self.m = TextEmbedding(self.name)

    def __call__(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self.m.query_embed([text])))]

    def embed_docs(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self.m.embed(texts)]


class OnnxReranker:
    """Cross-encoder; returns sigmoid scores in (0,1) - the only space compared with min_score."""
    def __init__(self, name: str | None = None):
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        self.name = name or os.getenv("RERANK_MODEL", DEFAULT_RERANK)
        self.m = TextCrossEncoder(self.name)

    def batch(self, query: str, texts: list[str]) -> list[float]:
        return [_sigmoid(float(s)) for s in self.m.rerank(query, texts)]

    def __call__(self, query: str, text: str) -> float:
        return self.batch(query, [text])[0]
