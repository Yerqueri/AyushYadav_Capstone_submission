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


# ── Additional Coordinator Coverage Tests ─────────────────────────────────────

def test_deterministic_coordinator_paths():
    from src.coordinator import _deterministic_coordinator
    from src.models import CompletedInvocation

    ticket = _TICKET

    # 1. No invocations -> invoke PR-02
    state1 = {"ticket": ticket, "completed_invocations": []}
    co1 = _deterministic_coordinator(state1)
    assert co1.next_action == "invoke_agent"
    assert co1.agent_call.agent_id == "PR-02"

    # 2. PR-02 complete -> invoke PR-01
    inv_pr02 = CompletedInvocation(agent_id="PR-02", agent_name="fluency_classifier", inputs={}, output={"fluency": "fluent"})
    state2 = {"ticket": ticket, "completed_invocations": [inv_pr02]}
    co2 = _deterministic_coordinator(state2)
    assert co2.agent_call.agent_id == "PR-01"

    # 3. PR-04 complete with answerable=False -> EXIT B escalation
    inv_pr01 = CompletedInvocation(agent_id="PR-01", agent_name="intent_classifier", inputs={}, output={"intent": "deployment_failure", "must_not_auto_respond": False})
    inv_pr03 = CompletedInvocation(agent_id="PR-03", agent_name="urgency_classifier", inputs={}, output={"urgency": "high"})
    inv_pr04_false = CompletedInvocation(agent_id="PR-04", agent_name="rag_agent", inputs={}, output={"relevant_doc_ids": [], "answerable": False})

    state3 = {"ticket": ticket, "completed_invocations": [inv_pr02, inv_pr01, inv_pr03, inv_pr04_false]}
    co3 = _deterministic_coordinator(state3)
    assert co3.next_action == "final_decision"
    assert co3.final_decision.route == "escalate"
    assert "EXIT B" in co3.reasoning or "answerable=false" in co3.reasoning

    # 4. PR-01 must_not_auto_respond=True, all agents done -> FLAG A escalation with draft
    inv_pr01_mnr = CompletedInvocation(agent_id="PR-01", agent_name="intent_classifier", inputs={}, output={"intent": "billing_query", "must_not_auto_respond": True, "confidence": 90.0})
    inv_pr04_true = CompletedInvocation(agent_id="PR-04", agent_name="rag_agent", inputs={}, output={"relevant_doc_ids": ["DOC-1"], "answerable": True})
    inv_pr05 = CompletedInvocation(agent_id="PR-05", agent_name="response_drafter", inputs={}, output={"draft": "Draft response", "citations": ["DOC-1"], "confidence": 90.0})

    state4 = {"ticket": ticket, "completed_invocations": [inv_pr02, inv_pr01_mnr, inv_pr03, inv_pr04_true, inv_pr05]}
    co4 = _deterministic_coordinator(state4)
    assert co4.next_action == "final_decision"
    assert co4.final_decision.route == "escalate"
    assert co4.final_decision.draft == "Draft response"


def test_guardrails_egress_node_blocking():
    from src.coordinator import guardrails_egress_node
    from src.models import FinalDecisionSpec, GuardrailResult

    state = {
        "final_decision": FinalDecisionSpec(route="auto_respond", escalation_reason=None, draft="some toxic draft", confidence=90.0),
        "completed_invocations": [
            CompletedInvocation(agent_id="PR-05", agent_name="response_drafter", inputs={}, output={"citations": ["DOC-1"]})
        ],
    }

    with patch("src.coordinator.run_egress_guardrails") as mock_egress, \
         patch("src.coordinator.get_doc_map", return_value={"DOC-1": {"content": "doc text"}}):
        mock_egress.return_value = (False, [GuardrailResult(validator="bert_toxic", passed=False, details="toxic")])
        updates = guardrails_egress_node(state)

    assert updates["final_decision"].route == "escalate"
    assert "guardrail_block:bert_toxic" in updates["final_decision"].escalation_reason


def test_triage_pipeline_seed_metadata():
    from src.coordinator import TriagePipelineCoordinator
    from src.models import TicketInput

    ticket_seeded = TicketInput(
        ticket_id="T-SEED",
        channel="chat",
        subject="Seeded metadata test",
        body="Body text",
        received_at="2026-01-01T00:00:00Z",
        customer_id="C-SEED",
        customer_name="Alice",
        customer_tier="standard",
        customer_region="us-east-1",
        language_fluency="fluent",
        urgency="low",
    )

    coord = TriagePipelineCoordinator(max_turns=1)
    with patch("src.coordinator.run_ingress_guardrails", return_value=(True, [])), \
         patch("src.coordinator._deterministic_coordinator") as mock_det, \
         patch("src.coordinator.write_decision", return_value="dec-seed"):

        mock_det.return_value = MagicMock(next_action="final_decision", final_decision=FinalDecisionSpec(route="escalate", escalation_reason="test", draft=None, confidence=80))
        res = coord.run(ticket_seeded)

    assert res.ticket_id == "T-SEED"


