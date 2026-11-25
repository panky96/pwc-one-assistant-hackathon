"""Obsidian vault loader + Graphify-style subgraph pruning (wikilink graph, depth<=2)."""
from __future__ import annotations
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from mcp_servers.rag_server import Chunk, tokenize

_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
_LINK = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class Note:
    id: str; domain: str; risk_tier: str; version: str; title: str; body: str; links: list[str]; facts: list[str]


def parse_note(path: Path) -> Note:
    m = _FM.match(path.read_text())
    if not m:
        raise ValueError(f"{path}: missing frontmatter")
    meta = dict(l.split(": ", 1) for l in m[1].splitlines() if ": " in l)
    for req in ("id", "domain", "risk_tier", "version"):
        if req not in meta:
            raise ValueError(f"{path}: frontmatter missing {req}")
    body = m[2]
    return Note(meta["id"], meta["domain"], meta["risk_tier"], meta["version"], meta.get("title", meta["id"]), body,
                _LINK.findall(body), [l[2:].strip() for l in body.splitlines() if l.startswith("- ")])


def load_vault(path: str | Path = "knowledge_vault") -> dict[str, Note]:
    return {n.id: n for n in (parse_note(p) for p in sorted(Path(path).glob("*.md")))}


def vault_chunks(notes: dict[str, Note], tenant_id: str) -> list[Chunk]:
    return [Chunk(n.id, tenant_id, f"{n.title}. " + " ".join(n.facts), dataset_version=n.version) for n in notes.values()]


def ntokens(text: str) -> int:
    return len(tokenize(text))


class Graph:
    def __init__(self, notes: dict[str, Note]):
        self.notes = notes
        self.adj = {i: set(n.links) & set(notes) for i, n in notes.items()}
        for i, ns in list(self.adj.items()):
            for j in ns: self.adj[j].add(i)

    def resolve_anchor(self, query: str, entity: str | None = None) -> str | None:
        if entity and entity in self.notes:
            return entity
        q = set(tokenize(query)); best, score = None, 0.0
        for n in self.notes.values():  # BM25-ish overlap over titles
            t = set(tokenize(n.title))
            s = len(q & t) / (len(t) or 1)
            if s > score: best, score = n.id, s
        return best if score >= 0.5 else None

    def subgraph(self, anchor: str, max_depth: int = 2) -> list[str]:
        seen, dq = {anchor: 0}, deque([anchor])
        while dq:
            cur = dq.popleft()
            if seen[cur] >= max_depth: continue
            for nb in sorted(self.adj[cur]):
                if nb not in seen: seen[nb] = seen[cur] + 1; dq.append(nb)
        return sorted(seen, key=lambda i: (seen[i], i))

    def context(self, query: str, max_depth: int = 1, max_notes: int = 3, entity: str | None = None) -> tuple[list[str], int] | None:
        """Pruned context notes + token count; None when the anchor cannot be resolved (fall back to Hybrid RAG)."""
        a = self.resolve_anchor(query, entity)
        if not a: return None
        ids = self.subgraph(a, max_depth)[:max_notes]
        return ids, sum(ntokens(self.notes[i].title + " ".join(self.notes[i].facts)) for i in ids)


def pruned_context(graph: "Graph", query: str, anchor: str, max_depth: int = 1) -> list[tuple[str, str]]:
    """Minimal subgraph: anchor note facts + the single best-matching fact of each neighbor (depth<=max_depth)."""
    q = set(tokenize(query))
    out = [(anchor, graph.notes[anchor].title + ". " + " ".join(graph.notes[anchor].facts))]
    for nid in graph.subgraph(anchor, max_depth)[1:]:
        best = max(graph.notes[nid].facts, key=lambda f: len(q & set(tokenize(f))), default="")
        if best and q & set(tokenize(best)):
            out.append((nid, best))
    return out
