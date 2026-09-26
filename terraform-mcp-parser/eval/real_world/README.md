# Real-world retrieval eval

The synthetic eval (`eval/`) answered "does retrieval survive *deliberately*
messy modules?" This one asks the harder question: **does it survive 20
actual community Terraform modules, with their natural messiness?**

## How to run

From `terraform-mcp-parser/`:

```bash
uv run eval/real_world/fetch_corpus.py   # git clone --depth 1 the 20 modules (free)
uv run eval/real_world/eval_real.py      # index + evaluate (~$0.15 in Anthropic calls)
```

`eval_real.py` needs `ANTHROPIC_API_KEY` (env or `.env`) — it summarizes
every module with the real LLM, same as `pipeline.py`. It reuses
`eval/eval.py`'s scoring code unchanged, but points it at:

- corpus: `eval/real_world/corpus/` (never committed; recreate via fetch)
- Chroma collection: `terraform_modules_real`
- Chroma path: `eval/real_world/.chroma_real/`

Your production `chroma_db` and the synthetic eval's collection are untouched.

## The corpus

`modules.txt` pins the 20 repos (pinned 2026-09-25; HEAD SHAs recorded in
`corpus/<name>/.eval-sha` at fetch time):

- **terraform-aws-modules/*** (9): s3-bucket, security-group, rds, vpc,
  ec2-instance, route53, alb, sns, elasticache — clean, canonical, huge
  variable counts (tam-vpc: 236 vars, tam-rds: 111).
- **Community** (11): cloudposse s3-bucket/security-group/rds/vpc/ec2-instance/
  sns-topic, clouddrove s3/security-group, mineiros-io s3-bucket,
  jameswoolfenden rds, trussworks logs.

Deliberate overlap: 4x object storage, 3x security groups, 3x databases,
2x VPC, 2x EC2, 2x SNS — retrieval has to earn its ranking.

## Queries

`queries.json` holds 18 queries (5 easy / 9 medium / 4 adversarial), written
after reading the actual modules:

- **easy** — direct capability match (any overlapping module may win).
- **medium** — specificity tests: the one module with a distinguishing
  feature must win (mineiros's CloudFront origin access identity,
  cp-sns-topic's SQS DLQ, trussworks-logs' ALB log prefixes).
- **adversarial** — real quirks, not synthetic traps:
  - `trussworks-logs` is an S3 module but log-specific; it must *not* win
    "general purpose S3 bucket", but *must* win "centralized logging
    bucket with 90-day retention".
  - `jw-rds` ships a security group literally named `*-allow-db-access`;
    "restrict database access to specific IP ranges" must prefer the
    general-purpose SG modules.
  - `tam-rds` is a **composition root**: zero `resource` blocks at root
    (everything lives in `./modules/*/`). Its summary rides on README +
    variables alone — a stress test of the non-resource evidence path.

## Metrics

Same as the synthetic eval: Recall@1, Recall@3, MRR, per-difficulty
breakdown, trap avoidance. See `eval/README.md` for interpretation.

## Findings that already came out of building this

1. **Parser bug fixed**: `parse_resource_types` only read `main.tf`.
   Real modules spread resources across files (`jw-rds` uses
   `aws_db_instance.instance.tf`, etc.) — it parsed to zero resource
   types. The parser now scans every `*.tf` in the module root, which is
   correct per Terraform semantics (all files in a dir = one config).
   `jw-rds` went from 0 to 7 resource types; `tam-vpc` 27 to 32.
2. **Composition roots are real**: `tam-rds` (one of the most-used RDS
   modules on GitHub) has no direct resources at root. The pipeline
   handles it via README + variables, but it's the thinnest evidence
   path in the corpus — watch how it scores.
3. Real READMEs are noisy in the opposite direction from the synthetic
   traps: badge walls, branding headers (mineiros), auto-generated
   template text (clouddrove). The summarizer has to see past marketing
   to the capability underneath.
