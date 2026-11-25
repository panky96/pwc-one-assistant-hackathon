"""Semantic cache. Structural identity must match first; similarity (>=0.98) only afterwards.
Backends: in-memory (default/tests) or Redis (REDIS_URL). Keys are tenant-prefixed `t:{tenant}:`."""
from __future__ import annotations
import hashlib, json, time
from typing import Callable
from mcp_servers.rag_server import cosine, hash_embed


def cache_identity(tenant_id, dataset_version, intent, entities, filters, permission_scope, schema_version=1) -> str:
    blob = json.dumps([tenant_id, dataset_version, intent, sorted(entities), sorted(filters), permission_scope, schema_version])
    return hashlib.sha256(blob.encode()).hexdigest()


class MemoryBackend:
    def __init__(self):
        self.d: dict[str, tuple[float, str]] = {}

    def set(self, k, v, ttl): self.d[k] = (time.time() + ttl, v)

    def get(self, k):
        e = self.d.get(k)
        if not e or e[0] < time.time():
            self.d.pop(k, None); return None
        return e[1]

    def keys(self, prefix): return [k for k in self.d if k.startswith(prefix)]
    def delete(self, k): self.d.pop(k, None)


class RedisBackend:
    def __init__(self, url: str):
        import redis
        self.r = redis.Redis.from_url(url, decode_responses=True)

    def set(self, k, v, ttl): self.r.set(k, v, ex=ttl)
    def get(self, k): return self.r.get(k)
    def keys(self, prefix): return list(self.r.scan_iter(match=prefix + "*"))
    def delete(self, k): self.r.delete(k)


class SemanticCache:
    def __init__(self, backend=None, threshold=0.98, ttl=3600, embed: Callable = hash_embed, schema_version=1):
        self.b, self.threshold, self.ttl, self.embed, self.schema_version = backend or MemoryBackend(), threshold, ttl, embed, schema_version
        self.hits = self.misses = 0

    def _prefix(self, tenant_id, ident): return f"t:{{{tenant_id}}}:{ident}:"

    def put(self, tenant_id, ident, query, response: dict) -> None:
        k = self._prefix(tenant_id, ident) + hashlib.sha256(query.encode()).hexdigest()[:16]
        self.b.set(k, json.dumps({"q": query, "emb": self.embed(query), "resp": response}), self.ttl)

    def get(self, tenant_id, ident, query) -> dict | None:
        qe = self.embed(query)
        for k in self.b.keys(self._prefix(tenant_id, ident)):  # identity prefix => structural match first
            raw = self.b.get(k)
            if raw:
                e = json.loads(raw)
                if cosine(qe, e["emb"]) >= self.threshold:
                    self.hits += 1
                    return e["resp"]
        self.misses += 1
        return None

    def purge_tenant(self, tenant_id) -> int:
        ks = self.b.keys(f"t:{{{tenant_id}}}:")
        for k in ks: self.b.delete(k)
        return len(ks)

    @property
    def hit_ratio(self) -> float:
        n = self.hits + self.misses
        return self.hits / n if n else 0.0
