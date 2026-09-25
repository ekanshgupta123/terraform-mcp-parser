"""Vector store: ChromaDB persistent collection for module summaries.

Each module is one record:
  - id:        module name
  - document:  the composed summary text (what similarity search matches on)
  - embedding: vector of the summary text
  - metadata:  module_name, source_path, commit_sha, and the FULL parsed
               module JSON (untrimmed) so get_module_details can return it

ChromaDB runs embedded with zero infrastructure -- data lives in
CHROMA_PATH (default ./chroma_db). If you outgrow it, this is the layer
you'd swap for pgvector; nothing above it changes.
"""

import json
import os

import chromadb

COLLECTION_NAME = "terraform_modules"
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./chroma_db")


def get_collection(path: str | None = None):
    client = chromadb.PersistentClient(path=path or CHROMA_PATH)
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_module(collection,
                  module_name: str,
                  summary_text: str,
                  embedding: list[float],
                  full_parsed: dict,
                  source_path: str,
                  commit_sha: str | None = None) -> None:
    collection.upsert(
        ids=[module_name],
        documents=[summary_text],
        embeddings=[embedding],
        metadatas=[{
            "module_name": module_name,
            "source_path": source_path,
            "commit_sha": commit_sha or "",
            # Full parsed JSON stored as a string (Chroma metadata must be
            # scalar) so get_module_details can return every variable/output.
            "full_parsed": json.dumps(full_parsed),
        }],
    )


def search_modules_store(collection, query_embedding: list[float],
                         n_results: int = 5) -> list[dict]:
    """Semantic search. Returns module_name, summary, source_path, distance."""
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results,
        include=["documents", "metadatas", "distances"],
    )
    hits = []
    for i in range(len(results["ids"][0])):
        hits.append({
            "module_name": results["metadatas"][0][i]["module_name"],
            "summary": results["documents"][0][i],
            "source_path": results["metadatas"][0][i]["source_path"],
            "distance": results["distances"][0][i],
        })
    return hits


def get_module_details_store(collection, module_name: str) -> dict | None:
    """Return the full parsed JSON for one module, or None if unknown."""
    results = collection.get(ids=[module_name], include=["metadatas"])
    if not results["ids"]:
        return None
    return json.loads(results["metadatas"][0]["full_parsed"])
