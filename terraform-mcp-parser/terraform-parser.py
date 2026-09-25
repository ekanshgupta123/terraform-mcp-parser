from mcp.server.mcpserver import MCPServer
import hcl2
import json
from pathlib import Path

# Initialize MCP server
mcp = MCPServer("terraform-mcp-parser", "0.1.0")

# Lazily loaded on first search so importing this module stays cheap.
_collection = None


def _get_search_collection():
    global _collection
    if _collection is None:
        from store import get_collection
        _collection = get_collection()
    return _collection


def _clean(value):
    """python-hcl2 8.x leaves literal quote characters on string
    values and block names. Strip them so we get plain Python strings."""
    if isinstance(value, str) and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return value


def _clean_type(value):
    """Clean a variable type string. Besides _clean()'s quote stripping,
    legacy `${...}` interpolation syntax (e.g. "${map(string)}") is
    unwrapped to the inner type name."""
    value = _clean(value)
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        return value[2:-1]
    return value


def parse_variables(module_path: str) -> list[dict]:
    var_file = Path(module_path) / "variables.tf"
    if not var_file.exists():
        return []

    with open(var_file, "r") as f:
        parsed = hcl2.load(f)

    variables = []
    for block in parsed.get("variable", []):
        for raw_name, attrs in block.items():
            variables.append({
                "name": _clean(raw_name),
                "type": _clean_type(attrs.get("type")),
                "default": _clean(attrs.get("default")),
                "description": _clean(attrs.get("description")),
            })
    return variables


def parse_outputs(module_path: str) -> list[dict]:
    out_file = Path(module_path) / "outputs.tf"
    if not out_file.exists():
        return []

    with open(out_file, "r") as f:
        parsed = hcl2.load(f)

    outputs = []
    for block in parsed.get("output", []):
        for raw_name, attrs in block.items():
            outputs.append({
                "name": _clean(raw_name),
                "description": _clean(attrs.get("description")),
            })
    return outputs


def parse_resource_types(module_path: str) -> list[str]:
    """Extract just the resource type names (e.g. 'aws_s3_bucket') from
    main.tf -- not the full resource bodies, which are noise for search."""
    main_file = Path(module_path) / "main.tf"
    if not main_file.exists():
        return []

    with open(main_file, "r") as f:
        parsed = hcl2.load(f)

    resource_types: list[str] = []
    for block in parsed.get("resource", []):
        for raw_type in block:
            cleaned = _clean(raw_type)
            if cleaned not in resource_types:
                resource_types.append(cleaned)
    return resource_types


def parse_readme(module_path: str, max_chars: int = 4000) -> str:
    """Return the module's README text as-is (human-written prose about
    intent -- no parsing needed). Empty string if there isn't one."""
    for name in ("README.md", "readme.md"):
        readme_file = Path(module_path) / name
        if readme_file.exists():
            return readme_file.read_text(encoding="utf-8")[:max_chars]
    return ""


@mcp.tool()
def ping() -> str:
    """Simple health-check tool to confirm the server is running."""
    return "pong"


@mcp.tool()
def parse_module(module_path: str) -> dict:
    """Parse a Terraform module's variables.tf and outputs.tf into
    structured JSON (name, type, default, description for each)."""
    return {
        "module_name": Path(module_path).name,
        "variables": parse_variables(module_path),
        "outputs": parse_outputs(module_path),
        "resource_types": parse_resource_types(module_path),
        "readme": parse_readme(module_path),
    }


@mcp.tool()
def search_modules(query: str, n_results: int = 5) -> list[dict]:
    """Semantic search over indexed Terraform modules. Describe what you
    need in plain language (e.g. "private encrypted database") -- no
    module names or paths required. Returns the best-matching modules
    with their summaries. Run pipeline.py first to build the index."""
    from embedder import embed_one
    from store import search_modules_store

    query_vector = embed_one(query)
    return search_modules_store(_get_search_collection(), query_vector,
                               n_results=n_results)


@mcp.tool()
def get_module_details(module_name: str) -> dict:
    """Full parsed details for one module: every variable (name, type,
    default, description), outputs, resource types, and README. Use after
    search_modules to answer specific questions like "does it support X?"."""
    from store import get_module_details_store

    details = get_module_details_store(_get_search_collection(), module_name)
    if details is None:
        return {"error": f"Module '{module_name}' is not in the index. "
                         "Run pipeline.py to index it, or check the name "
                         "with search_modules first."}
    return details


if __name__ == "__main__":
    mcp.run()