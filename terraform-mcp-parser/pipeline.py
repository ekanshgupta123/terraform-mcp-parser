"""Batch ingestion pipeline: parse -> summarize -> embed -> store.

Run this whenever your modules change. It walks every module directory,
parses it, summarizes it (LLM), embeds the summary, and upserts it into
the vector store. Re-running is safe: records are upserted by module
name, so only changed modules need re-summarizing (though this script
re-summarizes everything it walks -- point it at changed dirs, or extend
it with commit-SHA change detection later).

Usage:
    uv run pipeline.py [modules_dir]
    uv run pipeline.py --summaries-json sample-summaries.json [modules_dir]

Without --summaries-json, each module is summarized via the Anthropic API
(requires ANTHROPIC_API_KEY in .env). With it, summaries load from a JSON
file keyed by module name -- handy for testing the pipeline end to end
without spending API calls.
"""

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# terraform-parser.py has a hyphen, so it can't be imported normally.
_spec = importlib.util.spec_from_file_location(
    "terraform_parser", Path(__file__).parent / "terraform-parser.py")
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)

from summarizer import summarize_module  # noqa: E402
from embedder import embed, summary_to_text  # noqa: E402
from store import get_collection, upsert_module  # noqa: E402


def git_commit_sha(module_path: Path) -> str | None:
    """Last-indexed commit marker for change detection later."""
    try:
        out = subprocess.run(
            ["git", "-C", str(module_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=10,
        )
        return out.stdout.strip()
    except Exception:
        return None


def index_module(module_path: Path, collection, summary: dict | None = None) -> None:
    print(f"Parsing {module_path.name} ...")
    parsed = tp.parse_module(str(module_path))

    if summary is None:
        print(f"  summarizing ({len(parsed['variables'])} vars) ...")
        summary = summarize_module(parsed)
    else:
        print("  using pre-computed summary")

    summary_text = summary_to_text(summary)
    print("  embedding ...")
    vector = embed([summary_text])[0]

    upsert_module(
        collection,
        module_name=parsed["module_name"],
        summary_text=summary_text,
        embedding=vector,
        full_parsed=parsed,
        source_path=str(module_path),
        commit_sha=git_commit_sha(module_path),
    )
    print(f"  indexed {parsed['module_name']}: {summary['summary'][:80]}...")


def main() -> None:
    argp = argparse.ArgumentParser()
    argp.add_argument("modules_dir", nargs="?",
                      default="../sample-modules",
                      help="Directory containing one subdir per module")
    argp.add_argument("--summaries-json", default=None,
                      help="JSON file of pre-computed summaries keyed by module name")
    args = argp.parse_args()

    modules_dir = Path(args.modules_dir)
    module_dirs = sorted(p for p in modules_dir.iterdir() if p.is_dir())
    if not module_dirs:
        sys.exit(f"No module directories found in {modules_dir}")

    fixtures: dict = {}
    if args.summaries_json:
        fixtures = json.loads(Path(args.summaries_json).read_text())
        print(f"Loaded pre-computed summaries for: {sorted(fixtures)}")

    collection = get_collection()
    for module_path in module_dirs:
        index_module(module_path, collection,
                     summary=fixtures.get(module_path.name))
    print(f"\nDone. {collection.count()} module(s) in the index.")


if __name__ == "__main__":
    main()
