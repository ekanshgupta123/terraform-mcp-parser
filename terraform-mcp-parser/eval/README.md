# Retrieval eval: messy modules

This harness answers the project's core strategic question: **does
`search_modules` still return the right module when the library looks like
a real team's — inconsistent names, missing descriptions, copy-paste
leftovers, misleading READMEs, overlapping capabilities?**

## How to run

From `terraform-mcp-parser/`:

```bash
uv run eval/generate_corpus.py   # build the 15-module corpus (free, instant)
uv run eval/eval.py              # index + evaluate (~$0.10 in Anthropic calls)
```

`eval.py` needs `ANTHROPIC_API_KEY` in the environment (or `.env`) because
it summarizes every corpus module with the real LLM — the same path
`pipeline.py` uses. It exits with a clear error if the key is missing.

The eval uses its **own** Chroma collection (`terraform_modules_eval`) in
`eval/.chroma_eval/`. Your real `terraform_modules` collection and
`chroma_db/` are never touched.

## What's in the corpus

`generate_corpus.py` (stdlib only, fully deterministic) writes 15 small
modules to `eval/corpus/`:

- **3 clean reference modules**: `sg-web-firewall`, `s3-private-bucket`,
  `rds-postgres` — good names, descriptions, READMEs.
- **12 messy modules**, each with a documented pathology in its
  `EVAL_NOTES.md`:
  - `vpc-networking` — misleading name; actually provisions security groups
  - `data-store` — misleading name; actually an RDS database
  - `team-storage` — README copy-pasted from an RDS module (mentions
    PostgreSQL) but provisions S3 buckets
  - `old-logging` — README copy-pasted from a firewall module but provisions
    an S3 log bucket
  - `s3-stuff` — unhelpful variable names (`x`, `thing`), no descriptions,
    no README at all
  - `fw-rules-legacy`, `mysql-thing`, `dns-zones`, `ec2-webserver` —
    missing descriptions, terse READMEs
  - `logs-bucket`, `cache-redis`, `notify-slack` — overlapping/sparse
    capabilities

## Queries

`queries.json` holds 18 natural-language queries, each with the expected
winning module(s):

- **easy** — direct capability match, clean module should win
- **medium** — sparse modules, overlapping capabilities, generalization
  (e.g. "host a static website" when no README mentions it)
- **adversarial** — a trap module's misleading README/name *must not* win
  (e.g. "PostgreSQL database" must not return `team-storage`, the S3 bucket
  whose README talks about PostgreSQL)

## Metrics

- **Recall@1** — fraction of queries where an expected module ranks first.
  The headline number: this is what the user experiences.
- **Recall@3** — fraction where an expected module is in the top 3.
- **MRR** (mean reciprocal rank) — average of `1/rank` of the first
  expected hit; rewards ranking the right module 2nd over 5th.
- **Trap avoidance** — for adversarial queries, how often the misleading
  module stayed out of #1 / out of the top 3.

## Interpreting failures

A single FAIL is more useful than the aggregate score. For each failure,
look at:

1. **The summary** — was the LLM misled by the README/name, or did it see
   through to the resources? (Check `eval/results.json`, or re-run the
   summarizer on that module.)
2. **The distance gap** — a miss by 0.01 is noise; a miss by 0.3 means the
   summary genuinely points the wrong way.
3. **The trap** — if a copy-paste README won, the summarizer is overweighting
   README prose vs. resource types. That's a concrete, fixable finding
   (e.g. down-weight README, or add a "README contradicts resources" check).

If Recall@1 on adversarial queries is high, the pipeline is robust to the
messiest realistic inputs. If clean queries pass but messy ones fail, the
fix belongs in the summarizer prompt or the key-variable selection — not in
bigger infrastructure.
