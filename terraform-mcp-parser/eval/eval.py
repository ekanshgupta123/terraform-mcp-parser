"""Retrieval eval: does search quality survive messy real-world modules?

Indexing: parses every module in eval/corpus/, summarizes each with the
real Anthropic summarizer, embeds locally, and upserts into a SEPARATE
Chroma collection (terraform_modules_eval) under eval/.chroma_eval --
the production `terraform_modules` collection and your real chroma_db are
never touched.

Eval: embeds each query in eval/queries.json, runs the same search path
the MCP server uses, and reports Recall@1, Recall@3, MRR, plus a
per-query pass/fail table. Adversarial queries also check that the known
"trap" module (misleading README/name) does not win.

Usage (from terraform-mcp-parser/):
    python3 ../terraform-mcp-parser/eval/generate_corpus.py   # or: uv run eval/generate_corpus.py
    uv run eval/eval.py            # needs ANTHROPIC_API_KEY in env or .env

~15 modules x ~$0.007 each ~= $0.10 per full run.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
    sys.exit(
        "ERROR: ANTHROPIC_API_KEY is not set.\n"
        "The eval summarizes every corpus module with the real Anthropic API,\n"
        "so it needs a key. Put it in your .env (see .env.example) or export it:\n"
        "    export ANTHROPIC_API_KEY='sk-ant-...'\n"
        "Then re-run:  uv run eval/eval.py"
    )

PKG_DIR = Path(__file__).resolve().parent
# EVAL_DIR is the data dir (corpus/, chroma db, results.json). It coincides
# with PKG_DIR for the synthetic eval, but eval/real_world/eval_real.py
# overrides it via env so one codebase serves both evals.
EVAL_DIR = Path(os.environ.get("EVAL_DIR", str(PKG_DIR)))
CORPUS_DIR = Path(os.environ.get("EVAL_CORPUS_DIR", str(EVAL_DIR / "corpus")))
EVAL_CHROMA_PATH = Path(os.environ.get("EVAL_CHROMA_PATH",
                                       str(EVAL_DIR / ".chroma_eval")))
EVAL_COLLECTION = os.environ.get("EVAL_COLLECTION", "terraform_modules_eval")
EVAL_TITLE = os.environ.get("EVAL_TITLE", "messy-module retrieval")
N_RESULTS = 5

# Safety: even though we build our own collection below, pin CHROMA_PATH
# before importing store so nothing can accidentally resolve to ./chroma_db.
os.environ["CHROMA_PATH"] = str(EVAL_CHROMA_PATH)
assert Path(os.environ["CHROMA_PATH"]).name != "chroma_db", \
    "eval chroma path guard: must not point at the production chroma_db"

# terraform-parser.py has a hyphen; the sibling modules live one dir up.
sys.path.insert(0, str(PKG_DIR.parent))
_spec = importlib.util.spec_from_file_location(
    "terraform_parser", PKG_DIR.parent / "terraform-parser.py")
tp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tp)

from summarizer import summarize_module  # noqa: E402
from embedder import embed_one, summary_to_text  # noqa: E402
from store import upsert_module, search_modules_store  # noqa: E402

import chromadb  # noqa: E402


def build_eval_index() -> None:
    """Parse -> summarize -> embed -> upsert every corpus module into a
    fresh eval collection. Idempotent: the old eval collection is dropped."""
    module_dirs = sorted(p for p in CORPUS_DIR.iterdir() if p.is_dir())
    if not module_dirs:
        sys.exit(
            f"ERROR: no modules found in {CORPUS_DIR}.\n"
            "Populate it first: synthetic eval -> uv run eval/generate_corpus.py; "
            "real-world eval -> uv run eval/real_world/fetch_corpus.py"
        )

    client = chromadb.PersistentClient(path=str(EVAL_CHROMA_PATH))
    try:
        client.delete_collection(EVAL_COLLECTION)
    except Exception:
        pass
    collection = client.get_or_create_collection(
        EVAL_COLLECTION, metadata={"hnsw:space": "cosine"})

    print(f"Indexing {len(module_dirs)} modules into '{EVAL_COLLECTION}' ...")
    for module_path in module_dirs:
        print(f"  {module_path.name} ...")
        parsed = tp.parse_module(str(module_path))
        summary = summarize_module(parsed)
        text = summary_to_text(summary)
        upsert_module(
            collection,
            module_name=parsed["module_name"],
            summary_text=text,
            embedding=embed_one(text),
            full_parsed=parsed,
            source_path=str(module_path),
        )
    print(f"  done. {collection.count()} modules indexed.\n")
    return collection


def evaluate(collection) -> dict:
    queries = json.loads((EVAL_DIR / "queries.json").read_text())
    rows = []
    for q in queries:
        expected = set(q["expected"])
        hits = search_modules_store(
            collection, embed_one(q["query"]), n_results=N_RESULTS)
        got_names = [h["module_name"] for h in hits]
        rank = next((i + 1 for i, name in enumerate(got_names)
                     if name in expected), None)
        trap = q.get("trap")
        trap_rank = (next((i + 1 for i, name in enumerate(got_names)
                           if name == trap), None) if trap else None)
        rows.append({
            "query": q["query"],
            "difficulty": q.get("difficulty", "?"),
            "expected": sorted(expected),
            "got_at_1": got_names[0] if got_names else None,
            "got_at_1_distance": round(hits[0]["distance"], 3) if hits else None,
            "top_3": got_names[:3],
            "rank": rank,
            "recall_at_1": rank == 1,
            "recall_at_3": rank is not None and rank <= 3,
            "reciprocal_rank": (1.0 / rank) if rank else 0.0,
            "trap": trap,
            "trap_rank": trap_rank,
            "note": q.get("note", ""),
        })
    return {"rows": rows}


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    out = {
        "n": n,
        "recall_at_1": sum(r["recall_at_1"] for r in rows) / n,
        "recall_at_3": sum(r["recall_at_3"] for r in rows) / n,
        "mrr": sum(r["reciprocal_rank"] for r in rows) / n,
    }
    by_diff: dict = {}
    for r in rows:
        by_diff.setdefault(r["difficulty"], []).append(r)
    out["by_difficulty"] = {
        d: {"n": len(rs),
            "recall_at_1": sum(r["recall_at_1"] for r in rs) / len(rs),
            "recall_at_3": sum(r["recall_at_3"] for r in rs) / len(rs),
            "mrr": sum(r["reciprocal_rank"] for r in rs) / len(rs)}
        for d, rs in sorted(by_diff.items())
    }
    traps = [r for r in rows if r["trap"]]
    out["traps"] = {
        "n": len(traps),
        "kept_out_of_1": sum(1 for r in traps if r["trap_rank"] != 1),
        "kept_out_of_top3": sum(1 for r in traps
                                if r["trap_rank"] is None or r["trap_rank"] > 3),
    }
    return out


def print_report(results: dict) -> None:
    s = summarize(results["rows"])
    bar = "=" * 64
    print(bar)
    print(f"EVAL REPORT -- {EVAL_TITLE}")
    print(f"Modules indexed: {results.get('module_count', '?')} "
          f"| Queries: {s['n']}")
    print("-" * 64)
    print(f"Recall@1: {s['recall_at_1']:.3f} "
          f"({sum(r['recall_at_1'] for r in results['rows'])}/{s['n']})")
    print(f"Recall@3: {s['recall_at_3']:.3f} "
          f"({sum(r['recall_at_3'] for r in results['rows'])}/{s['n']})")
    print(f"MRR:      {s['mrr']:.3f}")
    t = s["traps"]
    print(f"Traps:    {t['kept_out_of_1']}/{t['n']} kept out of #1, "
          f"{t['kept_out_of_top3']}/{t['n']} kept out of top-3")
    print("-" * 64)
    print("By difficulty:")
    for d, ds in s["by_difficulty"].items():
        print(f"  {d:12s} R@1 {ds['recall_at_1']:.3f}  "
              f"R@3 {ds['recall_at_3']:.3f}  MRR {ds['mrr']:.3f}  (n={ds['n']})")
    print("-" * 64)
    print("Per query:")
    for r in results["rows"]:
        mark = "PASS" if r["recall_at_1"] else "FAIL"
        print(f"  [{mark}] ({r['difficulty']}) \"{r['query']}\"")
        print(f"         expected: {', '.join(r['expected'])}")
        print(f"         got@1: {r['got_at_1']} (dist {r['got_at_1_distance']})"
              + ("" if r["recall_at_1"] else "  <-- MISS"))
        if r["trap"]:
            tr = r["trap_rank"]
            status = (f"rank #{tr} -- TRAP IN TOP 3" if tr and tr <= 3
                      else (f"rank #{tr} (outside top-3)" if tr else "not in top-5"))
            print(f"         trap '{r['trap']}': {status}")
        if not r["recall_at_3"]:
            print(f"         top3: {', '.join(r['top_3'])}")
    print(bar)


def main() -> None:
    collection = build_eval_index()
    results = evaluate(collection)
    results["module_count"] = collection.count()
    results["summary"] = summarize(results["rows"])
    out_path = EVAL_DIR / "results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nFull results written to {out_path}\n")
    print_report(results)


if __name__ == "__main__":
    main()
