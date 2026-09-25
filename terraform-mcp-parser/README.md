# terraform-mcp-parser

The full documentation for this project lives in the **root
[README.md](../README.md)** (that's what GitHub renders): setup, pipeline
usage, MCP tools, client configs, costs, and the retrieval eval harness.

Quick links:

- Setup: `uv sync`, then `cp .env.example .env` and add your
  `ANTHROPIC_API_KEY`
- Index modules: `uv run pipeline.py ../sample-modules`
- Run the server: `uv run terraform-parser.py`
- Retrieval eval: `uv run eval/generate_corpus.py && uv run eval/eval.py`
  (see [eval/README.md](eval/README.md))
