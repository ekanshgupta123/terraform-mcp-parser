"""Embedder: text -> vector using a local sentence-transformers model.

Runs entirely on your machine: no API key, no per-call cost. The default
model (all-MiniLM-L6-v2) produces 384-dimensional vectors and downloads
automatically on first use (~90MB, cached in ~/.cache afterwards).

The SAME model must be used for indexing and for query time -- the
embedder is shared by pipeline.py (indexing) and terraform-parser.py
(the search_modules MCP tool).
"""

import os

from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(DEFAULT_MODEL)
    return _model


def embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one vector per input text."""
    return get_model().encode(texts, normalize_embeddings=True).tolist()


def embed_one(text: str) -> list[float]:
    return embed([text])[0]


def summary_to_text(summary: dict) -> str:
    """Compose the single text that gets embedded for a module: the
    capability summary plus its key inputs/outputs and use case."""
    parts = [summary.get("summary", "")]
    if summary.get("key_inputs"):
        parts.append("Key inputs: " + "; ".join(summary["key_inputs"]))
    if summary.get("key_outputs"):
        parts.append("Key outputs: " + "; ".join(summary["key_outputs"]))
    if summary.get("typical_use_case"):
        parts.append("Typical use case: " + summary["typical_use_case"])
    return "\n".join(p for p in parts if p)
