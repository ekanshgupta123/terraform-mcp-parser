"""Real-world retrieval eval: 20 actual community Terraform modules from GitHub.

Reuses eval/eval.py's full scoring machinery (parse -> Anthropic summarize
-> embed -> Chroma search, Recall@1/@3, MRR, trap stats) against a separate
corpus, collection, and Chroma path, so the synthetic eval is untouched.

Usage (from terraform-mcp-parser/):
    uv run eval/real_world/fetch_corpus.py   # clone the 20 modules (free)
    uv run eval/real_world/eval_real.py      # index + evaluate (~$0.15 in Anthropic calls)

Needs ANTHROPIC_API_KEY in the environment (or .env), like eval.py.
"""

import os
import sys
from pathlib import Path

REAL_DIR = Path(__file__).resolve().parent

os.environ["EVAL_DIR"] = str(REAL_DIR)
os.environ["EVAL_CORPUS_DIR"] = str(REAL_DIR / "corpus")
os.environ["EVAL_CHROMA_PATH"] = str(REAL_DIR / ".chroma_real")
os.environ["EVAL_COLLECTION"] = "terraform_modules_real"
os.environ["EVAL_TITLE"] = "real-world module retrieval (20 community modules)"

sys.path.insert(0, str(REAL_DIR.parent))
import eval as eval_lib  # noqa: E402  (module-level env above configures it)

if __name__ == "__main__":
    eval_lib.main()
