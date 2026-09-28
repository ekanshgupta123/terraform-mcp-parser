"""MCP server exposing the module tools over stdio."""

from mcp.server.mcpserver import MCPServer

from . import parser

mcp = MCPServer("terraform-mcp-parser", "0.1.0")

_collection = None  # lazily loaded on first search


def _get_search_collection():
    """Lazily loaded on first search so importing this module stays cheap."""
    global _collection
    if _collection is None:
        from .store import get_collection
        _collection = get_collection()
    return _collection


@mcp.tool()
def ping() -> str:
    """Simple health-check tool to confirm the server is running."""
    return "pong"


@mcp.tool()
def parse_module(module_path: str) -> dict:
    """Parse a Terraform module's variables.tf and outputs.tf into
    structured JSON (name, type, default, description for each)."""
    return parser.parse_module(module_path)


@mcp.tool()
def search_modules(query: str, n_results: int = 5) -> list[dict]:
    """Semantic search over indexed Terraform modules. Describe what you
    need in plain language (e.g. "private encrypted database") -- no
    module names or paths required. Returns the best-matching modules
    with their summaries. Run terraform-mcp-index first to build the index."""
    from .embedder import embed_one
    from .store import search_modules_store

    query_vector = embed_one(query)
    return search_modules_store(_get_search_collection(), query_vector,
                               n_results=n_results)


@mcp.tool()
def get_module_details(module_name: str) -> dict:
    """Full parsed details for one module: every variable (name, type,
    default, description), outputs, resource types, and README. Use after
    search_modules to answer specific questions like "does it support X?"."""
    from .store import get_module_details_store

    details = get_module_details_store(_get_search_collection(), module_name)
    if details is None:
        return {"error": f"Module '{module_name}' is not in the index. "
                         "Run terraform-mcp-index to index it, or check the name "
                         "with search_modules first."}
    return details


@mcp.tool()
def get_usage_example(module_name: str, use_case: str = "") -> dict:
    """Generate a paste-ready Terraform `module` block for an indexed module.

    Deterministic: variable names, types, and defaults come from the
    parsed variables.tf, so the snippet can't invent or truncate names.
    Required variables (no default) are always included; pass use_case
    (e.g. "host a static website") to also pull in the optional variables
    relevant to that goal. Complex types (e.g. list(object({...}))) are
    rendered as example objects built from the type constraint, and
    variables the use case depends on (README co-mentions, summary
    callouts, use-case intent such as the policy input a website needs)
    are flagged alongside. Mutually exclusive website modes (hosting vs
    redirect-only) are resolved from the use case, and defaults that
    would silently break it (e.g. S3 public-access blocks under a
    website) are flagged. The registry version is resolved live when the
    source is a registry address (x.y.z placeholder if unreachable).

    The snippet surfaces candidate variables; it does not resolve every
    conflict between them. Deprecated patterns, README-prose noise, and
    which remaining bools to flip are the caller's judgment call --
    check the flagged variables against the module docs before applying.
    """
    from .store import get_module_details_store
    from .usage import build_usage_example

    collection = _get_search_collection()
    details = get_module_details_store(collection, module_name)
    if details is None:
        return {"error": f"Module '{module_name}' is not in the index. "
                         "Run terraform-mcp-index to index it, or check the name "
                         "with search_modules first."}
    return {
        "module_name": module_name,
        "hcl": build_usage_example(details, use_case=use_case,
                                   summary=_get_summary_text(collection,
                                                             module_name)),
    }


def _get_summary_text(collection, module_name: str) -> str:
    """Best-effort: the module's capability-summary chunk for the snippet
    header comment. Falls back to the first chunk, then to empty."""
    try:
        res = collection.get(where={"module_name": module_name},
                             include=["documents", "metadatas"])
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        for doc, meta in zip(docs, metas):
            if (meta or {}).get("chunk_kind", "summary") == "summary":
                return doc
        return docs[0] if docs else ""
    except Exception:
        return ""


def main() -> None:
    """Entry point: run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
