"""terraform-mcp-parser: RAG over your private Terraform module library.

Index your modules once, then ask about them in plain language through
any MCP client: semantic search, full module details, and deterministic
usage-example generation.
"""

__version__ = "0.1.0"


def main() -> None:
    """Entry point for the `terraform-mcp-parser` console script."""
    from .server import main as server_main
    server_main()
