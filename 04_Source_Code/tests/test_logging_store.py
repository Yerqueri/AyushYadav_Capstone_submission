"""Tests for src/logging_store.py."""
import pytest
from sqlalchemy.orm import Session
from src.logging_store import DecisionRepository, Decision, get_engine, write_decision


def test_decision_repository_write_and_read(tmp_path):
    db_file = tmp_path / "test_decisions.db"
    repo = DecisionRepository(db_url=f"sqlite:///{db_file}")

    completed_invocations = [
        {"agent_id": "PR-01", "output": {"intent": "billing_query"}},
        {"agent_id": "PR-03", "output": {"urgency": "medium"}},
    ]
    final_decision = {
        "route": "escalate",
        "escalation_reason": "billing_query requires human review",
        "confidence": 85.0,
    }
    guardrail_results = [{"validator": "detect_jailbreak", "passed": True}]
    response_relevant_doc_ids = ["DOC-BILLING-001"]

    decision_id = repo.write_decision(
        ticket_id="TEST-TICK-001",
        completed_invocations=completed_invocations,
        final_decision=final_decision,
        guardrail_results=guardrail_results,
        response_relevant_doc_ids=response_relevant_doc_ids,
    )

    assert isinstance(decision_id, str)
    assert len(decision_id) > 0

    # Query database to verify record contents
    engine = repo.get_engine()
    with Session(engine) as session:
        rec = session.query(Decision).filter_by(decision_id=decision_id).first()
        assert rec is not None
        assert rec.ticket_id == "TEST-TICK-001"
        assert rec.prediction == "escalate"
        assert rec.action_taken == "escalate"
        assert rec.confidence == 85.0


def test_global_write_decision_facade():
    repo = DecisionRepository(db_url="sqlite:///:memory:")
    # Facade test with None final_decision
    decision_id = repo.write_decision(
        ticket_id="TEST-TICK-002",
        completed_invocations=[],
        final_decision=None,
        guardrail_results=[],
        response_relevant_doc_ids=[],
    )

    engine = repo.get_engine()
    with Session(engine) as session:
        rec = session.query(Decision).filter_by(decision_id=decision_id).first()
        assert rec is not None
        assert rec.prediction == "error"
        assert rec.reason == "error"


def test_global_get_engine_and_write_decision():
    eng = get_engine()
    assert eng is not None

    dec_id = write_decision(
        ticket_id="FACADE-001",
        completed_invocations=[],
        final_decision={"route": "auto_respond", "confidence": 90.0},
        guardrail_results=[],
        response_relevant_doc_ids=[],
    )
    assert dec_id is not None
