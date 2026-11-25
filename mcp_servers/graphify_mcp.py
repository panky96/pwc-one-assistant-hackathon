"""MCP server `graphify-vault` (stdio): optional wikilink-graph subgraph pruning (depth<=2)."""
from mcp.server.fastmcp import FastMCP
from app.vault import Graph, load_vault, pruned_context

mcp = FastMCP("graphify-vault")
_notes = load_vault()
_g = Graph(_notes)


@mcp.tool()
def subgraph(query: str, entity: str | None = None, max_depth: int = 1) -> dict:
    """Resolve anchor (entity id -> title match); returns pruned context or anchor=None so the host falls back to Hybrid RAG."""
    if max_depth > 2 or max_depth < 0:
        raise ValueError("max_depth must be 0..2")
    a = _g.resolve_anchor(query, entity)
    if a is None:
        return {"anchor": None, "context": []}
    return {"anchor": a, "context": [{"id": i, "text": t} for i, t in pruned_context(_g, query, a, max_depth)]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
