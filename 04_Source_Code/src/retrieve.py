import json
import os
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

_DATA_PATH = Path(__file__).parent.parent / "data" / "documentation.json"


class DocumentRetriever:
    """Vector search retriever with graph expansion (SOLID - Single Responsibility)."""

    def __init__(self, data_path: Path = _DATA_PATH):
        self.data_path = data_path
        self._docs: list[dict[str, Any]] | None = None
        self._collection: Any = None

    def _load_docs(self) -> list[dict[str, Any]]:
        if self._docs is None:
            with open(self.data_path) as f:
                self._docs = json.load(f)
        return self._docs

    def _get_collection(self) -> Any:
        if self._collection is None:
            docs = self._load_docs()
            client = chromadb.EphemeralClient()
            ef = embedding_functions.DefaultEmbeddingFunction()
            col = client.create_collection(
                name="cloudserve_docs",
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            col.add(
                ids=[d["doc_id"] for d in docs],
                documents=[d["title"] + "\n\n" + d["content"] for d in docs],
                metadatas=[
                    {
                        "doc_id": d["doc_id"],
                        "category": d.get("category", ""),
                        "related_docs": ",".join(d.get("related_docs", [])),
                    }
                    for d in docs
                ],
            )
            self._collection = col
        return self._collection

    def get_doc_map(self) -> dict[str, dict[str, Any]]:
        return {d["doc_id"]: d for d in self._load_docs()}

    def retrieve(self, query: str, *, top_k: int | None = None, expand_hops: int = 1) -> list[dict[str, Any]]:
        """Semantic search + one-hop graph expansion. Returns list of doc dicts."""
        if top_k is None:
            top_k = int(os.getenv("RETRIEVAL_TOP_K", "3"))

        docs = self._load_docs()
        col = self._get_collection()
        doc_map = self.get_doc_map()

        results = col.query(query_texts=[query], n_results=min(top_k, len(docs)))
        seed_ids: list[str] = results["ids"][0]

        result_ids = list(seed_ids)
        seen = set(seed_ids)

        if expand_hops > 0:
            # Forward: seed → docs listed in their related_docs
            for doc_id in seed_ids:
                for nbr in doc_map.get(doc_id, {}).get("related_docs", []):
                    if nbr not in seen and nbr in doc_map:
                        result_ids.append(nbr)
                        seen.add(nbr)
            # Reverse: docs that list any seed in their own related_docs
            for d in docs:
                if d["doc_id"] not in seen:
                    if any(seed in d.get("related_docs", []) for seed in seed_ids):
                        result_ids.append(d["doc_id"])
                        seen.add(d["doc_id"])

        return [doc_map[did] for did in result_ids]

    @staticmethod
    def format_documents_block(docs: list[dict[str, Any]]) -> str:
        """Format retrieved docs into the standard [DOC-ID] ... [END DOC] block."""
        parts = []
        for d in docs:
            related = ", ".join(d.get("related_docs", [])) or "none"
            parts.append(
                f"[DOC-ID] {d['doc_id']}\n"
                f"[TITLE] {d['title']}\n"
                f"[RELATED DOCS] {related}\n"
                f"[CONTENT]\n{d['content']}\n"
                f"[END DOC]"
            )
        return "\n\n".join(parts)


_RETRIEVER = DocumentRetriever()


def get_doc_map() -> dict[str, dict]:
    return _RETRIEVER.get_doc_map()


def retrieve(query: str, *, top_k: int | None = None, expand_hops: int = 1) -> list[dict]:
    return _RETRIEVER.retrieve(query, top_k=top_k, expand_hops=expand_hops)


def format_documents_block(docs: list[dict]) -> str:
    return DocumentRetriever.format_documents_block(docs)


def get_collection() -> Any:
    return _RETRIEVER._get_collection()


def _load_docs() -> list[dict]:
    return _RETRIEVER._load_docs()

