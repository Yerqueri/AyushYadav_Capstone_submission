"""API triage response domain models."""
from typing import Optional
from pydantic import BaseModel, Field
from src.models.guardrails import GuardrailResult


class TriageResponse(BaseModel):
    ticket_id: str
    route: str                          # auto_respond | escalate | blocked
    draft: Optional[str] = None
    escalation_reason: Optional[str] = None
    intent: Optional[str] = None
    urgency: Optional[str] = None
    relevant_doc_ids: list[str] = Field(default_factory=list)
    rag_output: Optional[dict] = None
    confidence: float
    guardrail_results: list[GuardrailResult] = Field(default_factory=list)
    decision_id: str
