# terraform-mcp-parser

An MCP server that makes your **private, custom Terraform modules** searchable
by AI agents in plain language. Ask *"is there a module for a private
encrypted database?"* and get back the right module — even when no file is
named anything like "database".

HashiCorp's official Terraform MCP server covers the **public registry**.
This covers the modules only *you* have: your team's internal library that no
public tool knows about.

## Install

```bash
pip install terraform-mcp-parser
# or: uvx terraform-mcp-parser
```

Requires Python 3.11+.

## Setup (recommended)

```bash
terraform-mcp-setup
```

An interactive wizard that walks you through the one-time setup: point it
at the folder holding your Terraform modules, give it an Anthropic API key
(only needed once, to summarize each module — a few dollars covers hundreds
of modules), and it indexes everything and prints the exact config to paste
into Claude Code or VS Code.

## Manual setup (advanced)

If you'd rather run each step yourself:

```bash
export ANTHROPIC_API_KEY=...   # only needed at index time
terraform-mcp-index /path/to/your/modules
```

Each subdirectory of `/path/to/your/modules` should be one module
(`variables.tf`, `outputs.tf`, `main.tf`; `README.md` optional). The pipeline
parses each module, summarizes it once via the Anthropic API, embeds the
summary locally, and stores everything in a local ChromaDB (`./chroma_db`,
zero infrastructure). Re-running is safe — records are replaced per module.

## Connect a client

```json
{
  "mcpServers": {
    "terraform-modules": {
      "command": "terraform-mcp-parser",
      "env": { "CHROMA_PATH": "/absolute/path/to/chroma_db" }
    }
  }
}
```

Then just ask questions in plain language.

## Tools

- `search_modules(query)` — semantic search over your library; returns the
  best-matching modules with summaries.
- `get_module_details(module_name)` — every variable (name, type, default,
  description), outputs, resource types, README.
- `get_usage_example(module_name, use_case)` — a paste-ready `module` block,
  generated deterministically from the parsed schema (no invented variables).
  Registry versions are resolved live.
- `parse_module(module_path)` — parse a module directory into structured JSON.
- `ping` — health check.

Query time never touches your `.tf` files and costs nothing: the LLM runs
once per module at index time; search is pure vector similarity.

## Costs

Indexing one module costs roughly a cent in Anthropic API calls (one
summarization). A few hundred modules is a few dollars, one time.

## Development

Full docs (setup from source, eval harness, architecture, roadmap) live in the
[root README](https://github.com/ekanshgupta123/terraform-mcp-parser).
