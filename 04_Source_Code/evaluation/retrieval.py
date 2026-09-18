"""
Retrieval helpers for the RAG evaluation harness.

Provides build_collection() and retrieve() with the signatures expected by
run_rag_eval.py. Logic mirrors src/retrieve.py but accepts an explicit doc_list
and collection so the harness controls which corpus is loaded.
"""

import os

import chromadb
from chromadb.utils import embedding_functions


def build_collection(doc_list: list[dict]) -> "chromadb.Collection":
    """Embed doc_list into an ephemeral ChromaDB collection and return it."""
    client = chromadb.EphemeralClient()
    ef = embedding_functions.DefaultEmbeddingFunction()  # all-MiniLM-L6-v2
    col = client.create_collection(
        name="cloudserve_docs",
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    col.add(
        ids=[d["doc_id"] for d in doc_list],
        documents=[d["title"] + "\n\n" + d["content"] for d in doc_list],
        metadatas=[
            {
                "doc_id": d["doc_id"],
                "category": d.get("category", ""),
                "related_docs": ",".join(d.get("related_docs", [])),
            }
            for d in doc_list
        ],
    )
    return col


def retrieve(
    query: str,
    doc_list: list[dict],
    collection: "chromadb.Collection",
    *,
    top_k: int | None = None,
    expand_hops: int = 1,
) -> list[dict]:
    """Semantic search + one-hop graph expansion. Returns list of doc dicts."""
    if top_k is None:
        top_k = int(os.getenv("RETRIEVAL_TOP_K", "8"))

    doc_map = {d["doc_id"]: d for d in doc_list}

    results = collection.query(query_texts=[query], n_results=min(top_k, len(doc_list)))
    seed_ids: list[str] = results["ids"][0]

    result_ids = list(seed_ids)
    seen = set(seed_ids)

    if expand_hops > 0:
        for doc_id in seed_ids:
            for nbr in doc_map.get(doc_id, {}).get("related_docs", []):
                if nbr not in seen and nbr in doc_map:
                    result_ids.append(nbr)
                    seen.add(nbr)
        for d in doc_list:
            if d["doc_id"] not in seen:
                if any(seed in d.get("related_docs", []) for seed in seed_ids):
                    result_ids.append(d["doc_id"])
                    seen.add(d["doc_id"])

    return [doc_map[did] for did in result_ids if did in doc_map]
