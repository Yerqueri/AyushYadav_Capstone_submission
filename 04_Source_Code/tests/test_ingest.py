"""Tests for src/ingest.py — ticket normalization."""
import pytest
from src.ingest import normalize_ticket
from src.models import TicketInput

_BASE = {
    "ticket_id": "T-001",
    "channel": "email",
    "subject": "Login broken",
    "body": "I cannot log in.",
    "received_at": "2026-01-01T00:00:00Z",
    "customer_id": "C-1",
    "customer_name": "Alice",
    "customer_tier": "standard",
    "customer_region": "us-east-1",
}


def test_normalize_strips_history():
    raw = {**_BASE, "history": [{"role": "user", "content": "prior"}]}
    ticket = normalize_ticket(raw)
    assert not hasattr(ticket, "history")


def test_normalize_strips_labels():
    raw = {**_BASE, "labels": ["urgent", "billing"]}
    ticket = normalize_ticket(raw)
    assert isinstance(ticket, TicketInput)


def test_normalize_valid_fluency_kept():
    raw = {**_BASE, "language_fluency": "non_fluent"}
    ticket = normalize_ticket(raw)
    assert ticket.language_fluency == "non_fluent"


def test_normalize_invalid_fluency_cleared():
    raw = {**_BASE, "language_fluency": "unknown_value"}
    ticket = normalize_ticket(raw)
    assert ticket.language_fluency is None


def test_normalize_missing_fluency_is_none():
    ticket = normalize_ticket(dict(_BASE))
    assert ticket.language_fluency is None


def test_normalize_returns_ticket_input():
    ticket = normalize_ticket(dict(_BASE))
    assert isinstance(ticket, TicketInput)
    assert ticket.ticket_id == "T-001"
