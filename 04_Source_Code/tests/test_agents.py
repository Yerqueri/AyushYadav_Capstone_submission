"""Tests for sub-agent functions — all LLM calls are mocked."""
import json
import pytest
from unittest.mock import MagicMock, patch

import os
os.environ.setdefault("OPENAI_API_KEY", "test-key")


def _mock_client(response_text: str):
    """Build a minimal mock OpenAI client that returns response_text."""
    msg = MagicMock()
    msg.content = response_text
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client = MagicMock()
    client.chat.completions.create.return_value = resp
    return client


# ── parse_json_output ─────────────────────────────────────────────────────────

def test_parse_json_output_clean():
    from src.agents._llm import parse_json_output

    text = 'Some reasoning.\n{"key": "value", "n": 1}'
    result = parse_json_output(text)
    assert result == {"key": "value", "n": 1}


def test_parse_json_output_chain_of_thought():
    from src.agents._llm import parse_json_output

    text = "STEP 1 done.\nSTEP 2 done.\n\n{\"intent\": \"billing_query\", \"confidence\": 0.9}"
    result = parse_json_output(text)
    assert result["intent"] == "billing_query"


# ── run_fluency ───────────────────────────────────────────────────────────────

def test_run_fluency_returns_expected_keys():
    from src.agents.fluency import run_fluency

    payload = json.dumps({"fluency": "fluent", "confidence": 0.9, "reasoning_summary": "ok"})
    client = _mock_client(f"Step 1 done.\n{payload}")

    result = run_fluency(
        {"ticket_id": "T-1", "channel": "email", "body": "Hello"}, client
    )
    assert result["fluency"] == "fluent"
    assert 0.0 <= result["confidence"] <= 1.0


def test_run_fluency_non_fluent():
    from src.agents.fluency import run_fluency

    payload = json.dumps({"fluency": "non_fluent", "confidence": 0.85, "reasoning_summary": "errors found"})
    client = _mock_client(payload)

    result = run_fluency(
        {"ticket_id": "T-2", "channel": "chat", "body": "builds that work last week are now fail"}, client
    )
    assert result["fluency"] == "non_fluent"


# ── run_intent ────────────────────────────────────────────────────────────────

def test_run_intent_returns_expected_keys():
    from src.agents.intent import run_intent

    payload = json.dumps({
        "intent": "api_key_issue",
        "confidence": 0.92,
        "must_not_auto_respond": False,
        "reasoning_summary": "API key rotation request",
    })
    client = _mock_client(f"Reasoning here.\n{payload}")

    result = run_intent(
        {
            "ticket_id": "T-3",
            "channel": "email",
            "subject": "Key broken",
            "body": "My API key stopped working",
            "customer_tier": "business",
            "language_fluency": "fluent",
        },
        client,
    )
    assert result["intent"] == "api_key_issue"
    assert result["must_not_auto_respond"] is False


def test_run_intent_billing_must_escalate():
    from src.agents.intent import run_intent

    payload = json.dumps({
        "intent": "billing_query",
        "confidence": 0.88,
        "must_not_auto_respond": True,
        "reasoning_summary": "Invoice dispute",
    })
    client = _mock_client(payload)

    result = run_intent(
        {
            "ticket_id": "T-4",
            "channel": "email",
            "subject": "Wrong charge",
            "body": "I was charged twice",
            "customer_tier": "standard",
            "language_fluency": "fluent",
        },
        client,
    )
    assert result["must_not_auto_respond"] is True


# ── run_urgency ───────────────────────────────────────────────────────────────

def test_run_urgency_returns_expected_keys():
    from src.agents.urgency import run_urgency

    payload = json.dumps({"urgency": "high", "confidence": 0.95, "reasoning_summary": "prod down"})
    client = _mock_client(f"Step 1.\n{payload}")

    result = run_urgency(
        {
            "ticket_id": "T-5",
            "channel": "email",
            "subject": "Down",
            "body": "Everything is down, urgent!",
            "customer_tier": "enterprise",
            "intent": "deployment_failure",
        },
        client,
    )
    assert result["urgency"] in ("high", "medium", "low")


# ── run_draft ─────────────────────────────────────────────────────────────────

def test_run_draft_for_human_review():
    from src.agents.draft import run_draft

    payload = json.dumps({
        "draft": "Thank you for reaching out regarding your billing query. Here are the steps...",
        "citations": ["DOC-BILL-001"],
        "answered_fully": True,
        "confidence": 0.9,
        "reasoning_summary": "Generated suggested response draft for human review.",
    })
    client = _mock_client(payload)

    with patch("src.agents.draft.get_doc_map", return_value={}):
        result = run_draft(
            {
                "ticket_id": "T-6",
                "channel": "email",
                "body": "billing question",
                "customer_tier": "standard",
                "language_fluency": "fluent",
                "intent": "billing_query",
                "urgency": "low",
                "must_not_auto_respond": True,
                "relevant_doc_ids": ["DOC-BILL-001"],
            },
            client,
        )
    assert result["draft"] is not None
    assert "billing query" in result["draft"]


# ── _llm.py & run_rag ─────────────────────────────────────────────────────────

def test_get_client_missing_key(monkeypatch):
    from src.agents import _llm
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(_llm, "_client", None)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        _llm.get_client()


def test_call_llm_retry_and_success():
    from src.agents._llm import call_llm

    msg = MagicMock()
    msg.content = "success text"
    resp = MagicMock()
    resp.choices = [MagicMock(message=msg)]

    client = MagicMock()
    # First call fails, second succeeds
    client.chat.completions.create.side_effect = [Exception("rate limit"), resp]

    with patch("time.sleep"):
        res = call_llm(client, "system prompt", "user prompt", max_retries=2)

    assert res == "success text"
    assert client.chat.completions.create.call_count == 2


def test_call_llm_all_retries_fail():
    from src.agents._llm import call_llm

    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("network error")

    with patch("time.sleep"):
        with pytest.raises(Exception, match="network error"):
            call_llm(client, "sys", "user", max_retries=2)


def test_parse_json_output_errors():
    from src.agents._llm import parse_json_output

    with pytest.raises(ValueError, match="No JSON object found"):
        parse_json_output("no json here")

    with pytest.raises(ValueError, match="Incomplete JSON object"):
        parse_json_output('{"unclosed": "object"')


def test_run_rag_agent():
    from src.agents.rag import run_rag

    payload = json.dumps({
        "relevant_doc_ids": ["DOC-1"],
        "answerable": True,
        "confidence": 85,
        "reasoning_summary": "Doc matches",
    })
    client = _mock_client(payload)

    mock_doc = {
        "doc_id": "DOC-1",
        "title": "Deployment Guide",
        "content": "Steps to fix build dependency issues...",
        "related_docs": [],
    }

    with patch("src.agents.rag.retrieve", return_value=[mock_doc]):
        res = run_rag({
            "ticket_id": "T-10",
            "intent": "deployment_failure",
            "urgency": "high",
            "subject": "Build broken",
            "body": "dependency issue",
        }, client)

    assert res["relevant_doc_ids"] == ["DOC-1"]
    assert res["answerable"] is True



