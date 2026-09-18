"""Coordinator decision and execution state domain models."""
from typing import Optional
from pydantic import BaseModel


class AgentCallSpec(BaseModel):
    agent_id: str
    agent_name: str
    inputs: dict


class FinalDecisionSpec(BaseModel):
    route: str                          # auto_respond | escalate | blocked
    escalation_reason: Optional[str] = None
    draft: Optional[str] = None
    confidence: float


class CoordinatorOutput(BaseModel):
    next_action: str                    # invoke_agent | final_decision
    agent_call: Optional[AgentCallSpec] = None
    final_decision: Optional[FinalDecisionSpec] = None
    reasoning: str


class CompletedInvocation(BaseModel):
    agent_id: str
    agent_name: str
    inputs: dict
    output: dict
