import datetime
import json
import os
import uuid
from pathlib import Path

from sqlalchemy import Column, Float, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Session


class Base(DeclarativeBase):
    pass


class Decision(Base):
    __tablename__ = "decisions"

    decision_id = Column(String, primary_key=True)
    timestamp = Column(String, nullable=False)
    ticket_id = Column(String, nullable=False)
    stage = Column(String, nullable=False)
    input_summary = Column(Text)
    model = Column(String)
    prediction = Column(String)
    alternatives = Column(Text)          # JSON: {agent_id: output} for all invocations
    sources_used = Column(Text)          # JSON: [doc_id, ...]
    threshold_applied = Column(Float)
    action_taken = Column(String)
    reason = Column(Text)
    guardrail_results = Column(Text)     # JSON: [GuardrailResult, ...]
    prompt_version = Column(String)
    requirement_ids = Column(Text)       # JSON: [FR-01, ...]
    confidence = Column(Float)


class DecisionRepository:
    """Repository Pattern for SQLite database logging of triage pipeline decisions."""

    def __init__(self, db_url: str | None = None):
        self.db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///./storage/decisions.db")
        self._engine = None

    def get_engine(self):
        if self._engine is None:
            Path("storage").mkdir(exist_ok=True)
            self._engine = create_engine(self.db_url)
            Base.metadata.create_all(self._engine)
        return self._engine

    def write_decision(
        self,
        ticket_id: str,
        completed_invocations: list[dict],
        final_decision: dict | None,
        guardrail_results: list[dict],
        response_relevant_doc_ids: list[str],
    ) -> str:
        decision_id = str(uuid.uuid4())
        engine = self.get_engine()

        intermediates = {inv["agent_id"]: inv["output"] for inv in completed_invocations}

        fd = final_decision or {}
        route = fd.get("route", "error")
        reason = fd.get("escalation_reason") or route

        with Session(engine) as session:
            session.add(
                Decision(
                    decision_id=decision_id,
                    timestamp=datetime.datetime.utcnow().isoformat(),
                    ticket_id=ticket_id,
                    stage="pipeline",
                    input_summary=json.dumps({"invocations": list(intermediates.keys())}),
                    model=os.getenv("MODEL_NAME", "gpt-4.1-mini"),
                    prediction=route,
                    alternatives=json.dumps(intermediates),
                    sources_used=json.dumps(response_relevant_doc_ids),
                    threshold_applied=float(os.getenv("CONFIDENCE_THRESHOLD", "80")),
                    action_taken=route,
                    reason=reason,
                    guardrail_results=json.dumps(guardrail_results),
                    prompt_version="PR-00@1.0,PR-01@1.0,PR-02@1.0,PR-03@3.0,PR-04@2.0,PR-05@1.0",
                    requirement_ids=json.dumps(["FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-06"]),
                    confidence=float(fd.get("confidence", 0.0)),
                )
            )
            session.commit()

        return decision_id


_REPOSITORY = DecisionRepository()


def get_engine():
    return _REPOSITORY.get_engine()


def write_decision(
    ticket_id: str,
    completed_invocations: list[dict],
    final_decision: dict | None,
    guardrail_results: list[dict],
    response_relevant_doc_ids: list[str],
) -> str:
    return _REPOSITORY.write_decision(
        ticket_id=ticket_id,
        completed_invocations=completed_invocations,
        final_decision=final_decision,
        guardrail_results=guardrail_results,
        response_relevant_doc_ids=response_relevant_doc_ids,
    )
