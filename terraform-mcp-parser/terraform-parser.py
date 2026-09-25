from mcp.server.mcpserver import MCPServer
import hcl2
import json
from pathlib import Path

# Initialize MCP server
mcp = MCPServer("terraform-mcp-parser", "0.1.0")


def _clean(value):
    """python-hcl2 8.x leaves literal quote characters on string
    values and block names. Strip them so we get plain Python strings."""
    if isinstance(value, str) and value.startswith('"') and value.endswith('"'):
        return value[1:-1]
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
                "type": _clean(attrs.get("type")),
                "default": attrs.get("default"),
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
    }


if __name__ == "__main__":
    mcp.run()