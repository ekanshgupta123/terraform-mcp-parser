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


def summary_to_chunks(summary: dict) -> list[tuple[str, str]]:
    """Split a module summary into independently-embedded chunks.

    Returns (kind, text) pairs: the capability summary, key inputs,
    key outputs, and one chunk per typical use case.

    Why chunks: a single whole-document embedding dilutes specific
    signals. "host a static website" matches the website use-case
    sentence at distance ~0.41, but the full ~300-word S3 document
    (logging, encryption, versioning, replication, ...) averages out
    to ~0.84 -- the website sentence drowns. Chunking keeps each
    aspect's signal intact; retrieval scores a module by its best
    chunk (see store.search_modules_store).
    """
    chunks: list[tuple[str, str]] = []
    if summary.get("summary"):
        chunks.append(("summary", summary["summary"]))
    if summary.get("key_inputs"):
        chunks.append(("key_inputs",
                       "Key inputs: " + "; ".join(summary["key_inputs"])))
    if summary.get("key_outputs"):
        chunks.append(("key_outputs",
                       "Key outputs: " + "; ".join(summary["key_outputs"])))
    use_cases = list(summary.get("typical_use_cases") or [])
    if summary.get("typical_use_case"):
        # Backward compat with summaries from the older single-use-case prompt.
        use_cases.append(summary["typical_use_case"])
    for uc in use_cases:
        if uc:
            chunks.append(("use_case", "Typical use case: " + uc))
    if not chunks:
        raise ValueError("summary produced no embeddable chunks")
    return chunks
