"""Vector store: ChromaDB persistent collection for module summaries.

Chunked indexing: each module is stored as several records, one per
summary chunk (capability summary, key inputs, key outputs, each
typical use case):
  - id:        f"{module_name}#chunk-{i}"
  - document:  the chunk text (what similarity search matches on)
  - embedding: vector of the chunk text
  - metadata:  module_name, chunk_index, chunk_kind, source_path,
               commit_sha, and the FULL parsed module JSON (untrimmed)
               so get_module_details can return it

Search scores each module by the average of its SCORE_TOP_K closest
chunks, which keeps specific signals -- e.g. a single use-case
sentence -- from drowning in the whole-document average, while
requiring a module to match on more than one aspect before a single
generic sentence can carry it.

ChromaDB runs embedded with zero infrastructure -- data lives in
CHROMA_PATH (default ./chroma_db). If you outgrow it, this is the layer
you'd swap for pgvector; nothing above it changes.
"""

import json
import os

import chromadb

COLLECTION_NAME = "terraform_modules"
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./chroma_db")

# Search over-fetches this many chunk hits per requested module, so that
# top-K chunk scoring still sees every module's closest chunks.
_CHUNK_OVERFETCH = 10

# How many of a module's closest chunks are averaged into its score.
# 1 = pure best-chunk-wins. 2 means a module has to match on two
# aspects, so one generic use-case sentence ("firewall rules for
# EC2 instances") can't carry a module on its own.
# Default is 1: the setting the real-world eval verified at 18/18.
SCORE_TOP_K = int(os.environ.get("SCORE_TOP_K", "1"))


def get_collection(path: str | None = None):
    client = chromadb.PersistentClient(path=path or CHROMA_PATH)
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_module(collection,
                  module_name: str,
                  chunks: list[tuple[str, str]],
                  embeddings: list[list[float]],
                  full_parsed: dict,
                  source_path: str,
                  commit_sha: str | None = None) -> None:
    """Index one module as one record per (kind, text) chunk.

    Delete-then-add (not upsert): chunk indexes shift when a summary
    gains or loses use cases, so stale chunk-N records must go rather
    than linger under old ids.
    """
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must align")
    collection.delete(where={"module_name": module_name})
    if not chunks:
        return
    metadatas = [{
        "module_name": module_name,
        "chunk_index": i,
        "chunk_kind": kind,
        "source_path": source_path,
        "commit_sha": commit_sha or "",
        # Full parsed JSON stored as a string (Chroma metadata must be
        # scalar) so get_module_details can return every variable/output.
        "full_parsed": json.dumps(full_parsed),
    } for i, (kind, _) in enumerate(chunks)]
    collection.add(
        ids=[f"{module_name}#chunk-{i}" for i in range(len(chunks))],
        documents=[text for _, text in chunks],
        embeddings=embeddings,
        metadatas=metadatas,
    )


def search_modules_store(collection, query_embedding: list[float],
                         n_results: int = 5) -> list[dict]:
    """Top-K chunk-average semantic search.

    Over-fetches chunk hits, keeps each module's SCORE_TOP_K closest
    chunks, and returns the top modules by that averaged distance. Each
    hit carries the matched chunk's text and kind so callers can see
    *why* it matched. Also reads old one-record-per-module indexes
    (their records simply behave as a single "summary" chunk).
    """
    total = collection.count()
    if total == 0:
        return []
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(total, n_results * _CHUNK_OVERFETCH),
        include=["documents", "metadatas", "distances"],
    )
    best: dict[str, dict] = {}
    dists: dict[str, list[float]] = {}
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        name = meta["module_name"]
        dist = results["distances"][0][i]
        dists.setdefault(name, []).append(dist)
        if name not in best or dist < best[name]["best_chunk_distance"]:
            best[name] = {
                "module_name": name,
                "summary": results["documents"][0][i],
                "matched_chunk": meta.get("chunk_kind", "summary"),
                "source_path": meta["source_path"],
                "best_chunk_distance": dist,
            }
    for name, hit in best.items():
        top = sorted(dists[name])[:SCORE_TOP_K]
        # A module with fewer fetched chunks than K pads with its worst
        # fetched one, so sparse matches aren't rewarded for being sparse.
        top += [top[-1]] * (SCORE_TOP_K - len(top))
        hit["distance"] = sum(top) / len(top)
    return sorted(best.values(), key=lambda h: h["distance"])[:n_results]


def get_module_details_store(collection, module_name: str) -> dict | None:
    """Return the full parsed JSON for one module, or None if unknown.

    Looks up by metadata rather than record id, so it works for both
    the old one-record-per-module index and the new chunked index.
    """
    results = collection.get(where={"module_name": module_name},
                             include=["metadatas"], limit=1)
    if not results["ids"]:
        return None
    return json.loads(results["metadatas"][0]["full_parsed"])
