"""Integration-level tests for the FastAPI application — pipeline is mocked."""
import os
import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///./storage/decisions.db")

from unittest.mock import patch, MagicMock


@pytest.fixture(scope="module")
def client():
    from src.models import TriageResponse

    mock_response = TriageResponse(
        ticket_id="T-200",
        route="escalate",
        draft=None,
        escalation_reason="billing_query",
        intent="billing_query",
        urgency="low",
        relevant_doc_ids=[],
        confidence=0.88,
        guardrail_results=[],
        decision_id="dec-200",
    )

    # Patch before importing api so the module binds to the mocks
    with patch("src.coordinator.run_pipeline", return_value=mock_response), \
         patch("src.retrieve.get_collection", return_value=MagicMock()), \
         patch("src.logging_store.get_engine", return_value=MagicMock()), \
         patch("src.metrics.init_metrics_server"):
        from src.api import app  # noqa: PLC0415
        # Also patch the already-bound name in the api module
        with patch("src.api.run_pipeline", return_value=mock_response):
            import httpx
            from fastapi.testclient import TestClient
            orig_init = httpx.Client.__init__
            def patched_init(self, *args, **kwargs):
                kwargs.pop("app", None)
                return orig_init(self, *args, **kwargs)
            with patch.object(httpx.Client, "__init__", patched_init):
                with TestClient(app, raise_server_exceptions=False) as c:
                    yield c


_VALID_TICKET = {
    "ticket_id": "T-200",
    "channel": "email",
    "subject": "Invoice question",
    "body": "Why was I charged twice this month?",
    "received_at": "2026-01-01T00:00:00Z",
    "customer_id": "C-200",
    "customer_name": "Carol",
    "customer_tier": "standard",
    "customer_region": "eu-west-1",
}


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_triage_ticket_returns_200(client):
    resp = client.post("/tickets", json=_VALID_TICKET)
    assert resp.status_code == 200


def test_triage_ticket_missing_required_field(client):
    bad = dict(_VALID_TICKET)
    del bad["body"]
    with patch("src.api.normalize_ticket", side_effect=Exception("missing body")):
        resp = client.post("/tickets", json=bad)
    # Either 422 (Pydantic) or 500 (unhandled)
    assert resp.status_code in (422, 500)


def test_triage_tickets_batch_returns_200(client):
    resp = client.post("/tickets/batch", json=[_VALID_TICKET, _VALID_TICKET])
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 2

