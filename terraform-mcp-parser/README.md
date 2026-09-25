# terraform-mcp-parser

An MCP server that makes your **private, custom Terraform modules** searchable
by AI agents using plain language. Ask *"is there a module for a private
encrypted database?"* and get back the right module -- even if no file is
named anything like "database".

Public registry modules are already covered by HashiCorp's official
Terraform MCP server. This project exists for the modules only *you* have:
your team's internal library that no public tool knows about.

## How it works

Two separate systems that meet at a vector database:

```
INGESTION (run when modules change)
  parse  ->  summarize (LLM)  ->  embed  ->  store
  .tf files    English text      vector     ChromaDB

SERVING (always on, what agents call)
  search_modules("private encrypted database")
      -> embed query -> similarity search -> ranked modules
  get_module_details("my-module")
      -> full variables/outputs/resources for the chosen module
```

## Setup

```bash
cd terraform-mcp-parser
uv sync
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

## Index your modules

```bash
# 1. Put modules somewhere, e.g. ../sample-modules/<module-name>/
# 2. Run the pipeline (parse -> summarize -> embed -> store):
uv run pipeline.py ../sample-modules
```

`sample-summaries.json` holds hand-written summaries for the two sample
modules so you can test the pipeline end to end without spending API
calls: `uv run pipeline.py --summaries-json sample-summaries.json`

## Run the MCP server

```bash
uv run terraform-parser.py
```

Point your MCP client at it (Claude Code / Claude Desktop config):

```json
{
  "mcpServers": {
    "terraform-modules": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/terraform-mcp-parser", "terraform-parser.py"]
    }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `ping` | Health check, returns `"pong"` |
| `parse_module(module_path)` | Parse a module dir into structured JSON (dev/testing helper) |
| `search_modules(query, n_results=5)` | Semantic search over indexed modules -- plain language in, ranked modules out |
| `get_module_details(module_name)` | Full variables/outputs/resource types/README for one module |

## Project layout

```
terraform-parser.py    MCP server + HCL parser (variables, outputs, resource types, README)
summarizer.py          Stage 2: parsed JSON -> English summary (Anthropic API)
embedder.py            Stage 3: text -> vector (local sentence-transformers, no API key)
store.py               Stage 4: ChromaDB vector store (local ./chroma_db, zero infra)
pipeline.py            Batch ingestion: parse -> summarize -> embed -> store
sample-summaries.json  Hand-written summaries for local testing (no API spend)
```

## Costs

Summarization is the only paid step and it runs **once per module at index
time**, never per query: roughly **$0.01/module** with Sonnet, ~$0.003 with
Haiku. Embeddings and the vector DB are local and free.

## Future upgrade paths

- **pgvector on RDS** instead of ChromaDB when you want shared/production
  storage -- only `store.py` changes.
- **Two embeddings per module** (summary vs. key inputs/outputs) for finer
  retrieval, as sketched in the design notes.
- **Change detection** via the stored `commit_sha`: skip re-summarizing
  modules whose git SHA hasn't moved.
