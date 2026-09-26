"""Fetch the real-world eval corpus: 20 community Terraform modules from GitHub.

Clones each URL in modules.txt with `git clone --depth 1` into
eval/real_world/corpus/<short-name>/. Idempotent: skips repos that are
already cloned. Records each clone's HEAD SHA in <name>/.eval-sha so runs
are reproducible even though upstream keeps moving.

Usage (from terraform-mcp-parser/):
    uv run eval/real_world/fetch_corpus.py
"""

import subprocess
import sys
from pathlib import Path

REAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = REAL_DIR / "corpus"

ORG_SHORT = {
    "terraform-aws-modules": "tam",
    "cloudposse": "cp",
    "clouddrove": "clouddrove",
    "mineiros-io": "mineiros",
    "jameswoolfenden": "jw",
    "trussworks": "trussworks",
}


def short_name(url: str) -> str:
    # https://github.com/<org>/<repo>.git -> <orgshort>-<repo minus terraform-aws- prefix>
    parts = url.rstrip("/").removesuffix(".git").split("/")
    org, repo = parts[-2], parts[-1]
    repo = repo.removeprefix("terraform-aws-")
    return f"{ORG_SHORT.get(org, org)}-{repo}"


def main() -> int:
    urls = [
        line.strip()
        for line in (REAL_DIR / "modules.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    for url in urls:
        name = short_name(url)
        dest = CORPUS_DIR / name
        if dest.exists():
            print(f"  skip (exists): {name}")
            continue
        print(f"  cloning: {name} ...")
        r = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(f"  FAILED: {name}\n{r.stderr[-500:]}", file=sys.stderr)
            return 1
        sha = subprocess.run(
            ["git", "-C", str(dest), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip()
        (dest / ".eval-sha").write_text(sha + "\n")
        print(f"  ok: {name} @ {sha[:12]}")
    print(f"\nDone. {len(urls)} module(s) in {CORPUS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
