# terraform-mcp-parser

An MCP server that makes your **private, custom Terraform modules** searchable
by AI agents in plain language. Ask *"is there a module for a private
encrypted database?"* and get back the right module — even when no file is
named anything like "database".

Public registry modules are already covered by HashiCorp's official Terraform
MCP server. This project exists for the modules only *you* have: your team's
internal library that no public tool knows about. Instead of digging through
repos or pinging the platform team, a developer describes what they need and
the agent finds the module and shows how to use it.

## How it works

Two systems that meet at a vector database:

```
INGESTION — run when modules change (pipeline.py)
  parse            summarize (LLM)        embed              store
  .tf files  -->  English summary   -->  vector        -->  ChromaDB
  variables,       what the module        local              local,
  outputs,         DOES, in plain         sentence-          zero infra
  resource types,  language               transformers

SERVING — the MCP server agents call (terraform-parser.py)
  search_modules("private encrypted database")
      --> embed query --> similarity search --> ranked modules
  get_module_details("my-module")
      --> full variables / outputs / resource types / README
```

The LLM never sees your `.tf` files at query time. Summarization runs **once
per module at index time**; search is pure vector similarity over the stored
summaries, so queries are fast and free.

## Setup

Prerequisites: Python 3.11+, [`uv`](https://docs.astral.sh/uv/).

```bash
cd terraform-mcp-parser
uv sync
cp .env.example .env   # then add your ANTHROPIC_API_KEY
```

Get an API key at [platform.claude.com](https://platform.claude.com) (API keys
page). The key must be scoped to a workspace — when creating it, make sure a
workspace is selected, not "no workspace". API billing is separate from any
Claude Pro/Max subscription: add a small credit top-up (minimum $5) under
Billing; that covers hundreds of modules (see [Costs](#costs)).

## Index your modules

```bash
# 1. Put modules somewhere, e.g. ../sample-modules/<module-name>/
#    (each module dir needs variables.tf / outputs.tf / main.tf, README.md optional)
# 2. Run the pipeline:
uv run pipeline.py ../sample-modules
```

The pipeline parses each module, summarizes it via the Anthropic API,
splits the summary into chunks (capability summary, key inputs/outputs,
one chunk per typical use case), embeds each chunk locally, and upserts
them into ChromaDB (`./chroma_db`). Retrieval scores a module by its
best-matching chunk, so a specific use case isn't drowned out by the
rest of a long summary. Re-running is safe — records are replaced per
module name.

`sample-summaries.json` holds hand-written summaries for the sample modules
so you can exercise the pipeline end to end without spending API calls:

```bash
uv run pipeline.py --summaries-json sample-summaries.json ../sample-modules
```

## Run the MCP server

```bash
uv run terraform-parser.py   # stdio transport; keep running
```

## Connect a client

The server speaks MCP over stdio. Register it once per client; every config
below points at the **same script and the same `chroma_db`**.

**Claude Code** (terminal):

```bash
claude mcp add terraform-parser \
  -e CHROMA_PATH=/absolute/path/to/terraform-mcp-parser/chroma_db \
  -- /absolute/path/to/terraform-mcp-parser/.venv/bin/python \
     /absolute/path/to/terraform-mcp-parser/terraform-parser.py
```

**VS Code** (`.vscode/mcp.json` in your workspace):

```json
{
  "servers": {
    "terraform-parser": {
      "type": "stdio",
      "command": "/absolute/path/to/terraform-mcp-parser/.venv/bin/python",
      "args": ["/absolute/path/to/terraform-mcp-parser/terraform-parser.py"],
      "env": {
        "CHROMA_PATH": "/absolute/path/to/terraform-mcp-parser/chroma_db"
      }
    }
  }
}
```

> Use the venv's Python directly (not `uv run`) and make `CHROMA_PATH`
> absolute: editors may start the server with a different working directory,
> and a relative path would open an empty database instead of your index.
> Reload the editor after adding the config.

Then just ask in plain language — e.g. *"I need firewall rules for my EC2
instances, is there a module for that?"* — and watch the agent call
`search_modules` and answer from your library.

## Tools

| Tool | What it does |
|---|---|
| `ping` | Health check, returns `"pong"` |
| `parse_module(module_path)` | Parse a module dir into structured JSON (dev/testing helper) |
| `search_modules(query, n_results=5)` | Semantic search over indexed modules — plain language in, ranked modules out |
| `get_module_details(module_name)` | Full variables/outputs/resource types/README for one module |

## Retrieval eval (messy modules)

The bet behind this project: retrieval quality on **genuinely messy,
inconsistent real-world module libraries** — misleading names, missing
descriptions, copy-paste leftover READMEs, overlapping capabilities. The
harness in `terraform-mcp-parser/eval/` measures exactly that:

```bash
cd terraform-mcp-parser
uv run eval/generate_corpus.py   # build 15-module corpus (3 clean + 12 messy); free
uv run eval/eval.py              # index + score 18 queries (~$0.10 in API calls)
```

It reports **Recall@1 / Recall@3 / MRR** plus a per-query pass/fail table and
a trap-avoidance check (did the misleading README win?). Uses its own Chroma
collection — your real index is never touched. See `eval/README.md` for how
to interpret failures.

## Costs

Summarization is the only paid step, and it runs once per module at index
time — never per query. Roughly **$0.007/module** with Claude Sonnet 5
(~2k input + ~0.35k output tokens). Embeddings (sentence-transformers) and
ChromaDB run locally and are free. A 500-module library costs a few dollars
to index, one time.

## Repo layout

```
terraform-mcp-parser/
  terraform-parser.py    MCP server + HCL parser (variables, outputs, resource types, README)
  pipeline.py            Batch ingestion: parse -> summarize -> embed -> store
  summarizer.py          Parsed JSON -> English summary (Anthropic API, index time only)
  embedder.py            Text -> vector (local sentence-transformers, no API key)
  store.py               ChromaDB vector store (local ./chroma_db, zero infra)
  eval/                  Messy-module retrieval eval harness (see eval/README.md)
  sample-summaries.json  Hand-written summaries for testing without API spend
  .env.example           Template: ANTHROPIC_API_KEY + optional overrides
sample-modules/          Sample Terraform modules (submodules / fixtures)
```

## Status & roadmap

Validated end to end: real Anthropic summaries indexed, semantic search
ranking verified, and a plain-English query through Claude Code returning the
correct module with accurate inputs/outputs.

Sensible next steps:

- **`get_usage_example` tool** — return a ready-to-paste Terraform snippet
  for a module (the third tool in the original design).
- **Auto re-indexing** — run the pipeline on git changes (CI hook) so the
  index never goes stale; the stored `commit_sha` is there for change
  detection.
- **pgvector** instead of ChromaDB when you want shared/production storage —
  only `store.py` changes.
- **Remote transport** — serve over streamable HTTP so a whole team shares
  one index instead of indexing per machine.
