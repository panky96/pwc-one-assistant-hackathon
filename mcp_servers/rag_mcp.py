"""MCP server `hybrid-rag` (stdio). Resource rag://chunks/{id}; tool hybrid_retrieve.
`tenant_id` is injected by the trusted host from the validated TenantContext, never taken from end-user text."""
from mcp.server.fastmcp import FastMCP
from app.vault import load_vault, vault_chunks
from mcp_servers.rag_server import HybridRetriever

mcp = FastMCP("hybrid-rag")
_chunks = vault_chunks(load_vault(), "*")
_ret = HybridRetriever(_chunks)


@mcp.resource("rag://chunks/{chunk_id}")
def chunk(chunk_id: str) -> str:
    c = next((c for c in _chunks if c.id == chunk_id), None)
    if c is None:
        raise ValueError("unknown chunk")
    return c.text


@mcp.tool()
def hybrid_retrieve(query: str, tenant_id: str, candidate_k: int = 50, output_k: int = 4) -> dict:
    """BM25 + dense -> RRF -> rerank. Abstains when the reranker top score < min_score."""
    r = _ret.retrieve(query, tenant_id, candidate_k, output_k)
    return {"abstain": r.abstain, "top_score": r.top_score,
            "chunks": [{"id": c.id, "text": c.text, "score": s} for c, s in r.chunks]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
