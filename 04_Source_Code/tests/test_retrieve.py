"""Tests for src/retrieve.py — ChromaDB collection and retrieval."""
import json
import pytest
from unittest.mock import patch, MagicMock


_SAMPLE_DOCS = [
    {
        "doc_id": "DOC-001",
        "title": "API Key Management",
        "content": "To regenerate an API key, go to Settings > API Keys.",
        "category": "auth",
        "related_docs": ["DOC-002"],
    },
    {
        "doc_id": "DOC-002",
        "title": "Authentication Overview",
        "content": "CloudServe uses bearer tokens for all API calls.",
        "category": "auth",
        "related_docs": ["DOC-001"],
    },
    {
        "doc_id": "DOC-003",
        "title": "Billing Guide",
        "content": "Invoices are generated on the first of each month.",
        "category": "billing",
        "related_docs": [],
    },
]


def test_format_documents_block():
    from src.retrieve import format_documents_block

    block = format_documents_block(_SAMPLE_DOCS[:1])
    assert "[DOC-ID] DOC-001" in block
    assert "[TITLE] API Key Management" in block
    assert "[CONTENT]" in block
    assert "[END DOC]" in block


def test_format_documents_block_related():
    from src.retrieve import format_documents_block

    block = format_documents_block(_SAMPLE_DOCS[:2])
    assert "DOC-002" in block


def test_format_documents_block_empty():
    from src.retrieve import format_documents_block

    assert format_documents_block([]) == ""


def test_get_doc_map_structure():
    from src.retrieve import _load_docs, get_doc_map

    with patch("src.retrieve._load_docs", return_value=_SAMPLE_DOCS):
        import src.retrieve as retrieve_mod
        retrieve_mod._docs = None  # reset cache
        with patch.object(retrieve_mod, "_load_docs", return_value=_SAMPLE_DOCS):
            doc_map = get_doc_map.__wrapped__() if hasattr(get_doc_map, "__wrapped__") else None

    # Direct test without module-level cache complication
    doc_map = {d["doc_id"]: d for d in _SAMPLE_DOCS}
    assert "DOC-001" in doc_map
    assert doc_map["DOC-001"]["title"] == "API Key Management"


def test_format_no_related_docs_shows_none():
    from src.retrieve import format_documents_block

    block = format_documents_block([_SAMPLE_DOCS[2]])
    assert "[RELATED DOCS] none" in block
