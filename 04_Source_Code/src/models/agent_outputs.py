"""Sub-agent output domain models."""
from pydantic import BaseModel


class FluencyOutput(BaseModel):
    fluency: str                        # fluent | non_fluent
    confidence: float
    reasoning_summary: str


class IntentOutput(BaseModel):
    intent: str
    confidence: float
    must_not_auto_respond: bool
    reasoning_summary: str


class UrgencyOutput(BaseModel):
    urgency: str                        # high | medium | low
    confidence: float
    reasoning_summary: str


class RAGOutput(BaseModel):
    relevant_doc_ids: list[str]
    answerable: bool
    confidence: float
    reasoning_summary: str


class DraftOutput(BaseModel):
    draft: str
    citations: list[str]
    answered_fully: bool
    confidence: float
    reasoning_summary: str
