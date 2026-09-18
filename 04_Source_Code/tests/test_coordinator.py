"""Tests for coordinator logic — sub-agents and LLM are fully mocked."""
import json
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from src.models import (
    CompletedInvocation,
    FinalDecisionSpec,
    TicketInput,
)


_TICKET = TicketInput(
    ticket_id="T-100",
    channel="email",
    subject="API key broken",
    body="My API key stopped working yesterday.",
    received_at="2026-01-01T00:00:00Z",
    customer_id="C-100",
    customer_name="Bob",
    customer_tier="business",
    customer_region="us-west-2",
)


def _coordinator_response(next_action: str, agent_id: str = None, route: str = None, draft: str = None) -> str:
    if next_action == "invoke_agent":
        return json.dumps({
            "next_action": "invoke_agent",
            "agent_call": {
                "agent_id": agent_id,
                "agent_name": "test_agent",
                "inputs": {"ticket_id": "T-100"},
            },
            "final_decision": None,
            "reasoning": "next step",
        })
    return json.dumps({
        "next_action": "final_decision",
        "agent_call": None,
        "final_decision": {
            "route": route or "escalate",
            "escalation_reason": "billing_query" if route == "escalate" else None,
            "draft": draft,
            "confidence": 0.9,
        },
        "reasoning": "done",
    })


def test_run_pipeline_ingress_blocked():
    """Pipeline stops immediately when ingress guardrails block the ticket."""
    from src.coordinator import run_pipeline

    with patch("src.coordinator.run_ingress_guardrails") as mock_ingress, \
         patch("src.coordinator.write_decision", return_value="dec-1"):

        from src.models import GuardrailResult
        mock_ingress.return_value = (
            False,
            [GuardrailResult(validator="detect_jailbreak", passed=False, details="blocked")],
        )

        response = run_pipeline(_TICKET)

    assert response.route == "blocked"
    assert response.ticket_id == "T-100"


def test_run_pipeline_escalate_path():
    """Coordinator decides to escalate — pipeline writes decision and returns escalate."""
    from src.coordinator import run_pipeline

    coordinator_calls = [_coordinator_response("final_decision", route="escalate")]
    call_iter = iter(coordinator_calls)

    def fake_call_llm(*args, **kwargs):
        return next(call_iter)

    dummy_subagent_res = json.dumps({"fluency": "fluent", "intent": "billing_query", "must_not_auto_respond": True, "urgency": "medium", "answerable": False, "confidence": 0.9, "reasoning_summary": "ok"})

    with patch("src.coordinator.run_ingress_guardrails", return_value=(True, [])), \
         patch("src.coordinator.call_llm", side_effect=fake_call_llm), \
         patch("src.agents.fluency.call_llm", return_value=dummy_subagent_res), \
         patch("src.agents.intent.call_llm", return_value=dummy_subagent_res), \
         patch("src.agents.urgency.call_llm", return_value=dummy_subagent_res), \
         patch("src.agents.rag.call_llm", return_value=dummy_subagent_res), \
         patch("src.agents.draft.call_llm", return_value=dummy_subagent_res), \
         patch("src.coordinator.write_decision", return_value="dec-2"):

        response = run_pipeline(_TICKET)

    assert response.route == "escalate"
    assert response.decision_id == "dec-2"


def test_run_pipeline_max_turns_exceeded():
    """Pipeline escalates when coordinator loop exceeds MAX_COORDINATOR_TURNS."""
    from src.coordinator import run_pipeline
    import src.coordinator as coord_mod

    # Every coordinator call requests another agent invocation — will exhaust turns
    always_invoke = _coordinator_response("invoke_agent", agent_id="PR-02")

    with patch("src.coordinator.run_ingress_guardrails", return_value=(True, [])), \
         patch("src.coordinator.call_llm", return_value=always_invoke), \
         patch("src.coordinator.run_fluency", return_value={"fluency": "fluent", "confidence": 0.9, "reasoning_summary": "ok"}), \
         patch("src.coordinator.write_decision", return_value="dec-3"), \
         patch.object(coord_mod, "_MAX_TURNS", 3):

        response = run_pipeline(_TICKET)

    assert response.route == "escalate"


def test_format_invocations_block_empty():
    from src.coordinator import _format_invocations_block

    assert _format_invocations_block([]) == "(none)"


def test_format_invocations_block_with_entry():
    from src.coordinator import _format_invocations_block

    inv = CompletedInvocation(
        agent_id="PR-02",
        agent_name="fluency_classifier",
        inputs={"body": "hello"},
        output={"fluency": "fluent", "confidence": 0.9, "reasoning_summary": "ok"},
    )
    block = _format_invocations_block([inv])
    assert "PR-02" in block
    assert "fluency_classifier" in block
    assert "INVOCATION 1" in block
