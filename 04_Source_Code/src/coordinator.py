"""PR-00 — Coordinator pipeline (plain Python loop, no LangGraph dependency)."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional, TypedDict

from src.agents._llm import SECURITY_CONSTRAINTS, call_llm, get_client, parse_json_output
from src.agents.draft import run_draft
from src.agents.fluency import run_fluency
from src.agents.intent import run_intent
from src.agents.rag import run_rag
from src.agents.urgency import run_urgency
from src.guardrails import run_egress_guardrails, run_ingress_guardrails
from src.logging_store import write_decision
from src.metrics import AGENT_CONFIDENCE, COORDINATOR_TURNS, GUARDRAIL_BLOCKS
from src.models import (
    AgentCallSpec,
    CompletedInvocation,
    CoordinatorOutput,
    FinalDecisionSpec,
    GuardrailResult,
    TicketInput,
    TriageResponse,
)
from src.prompts.coordinator_prompts import (
    COORDINATOR_SYSTEM_PROMPT,
    COORDINATOR_USER_PROMPT_TEMPLATE,
)
from src.retrieve import get_doc_map

logger = logging.getLogger(__name__)

_MAX_TURNS = int(os.getenv("MAX_COORDINATOR_TURNS", "10"))


# ── LangGraph State ───────────────────────────────────────────────────────────

class PipelineState(TypedDict):
    ticket: TicketInput
    seed_invocations: list[CompletedInvocation]      # pre-populated from ticket metadata
    completed_invocations: list[CompletedInvocation] # accumulated outputs from pipeline nodes
    coordinator_output: Optional[CoordinatorOutput]
    final_decision: Optional[FinalDecisionSpec]
    guardrail_results: list[GuardrailResult]           # extended (not replaced) on each update
    turn_count: int
    pipeline_error: Optional[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_invocations_block(invocations: list[CompletedInvocation]) -> str:
    if not invocations:
        return "(none)"
    parts = []
    for i, inv in enumerate(invocations, 1):
        parts.append(
            f"[INVOCATION {i}]\n"
            f"Agent:   {inv.agent_id} ({inv.agent_name})\n"
            f"Inputs:  {json.dumps(inv.inputs, indent=2)}\n"
            f"Output:  {json.dumps(inv.output, indent=2)}"
        )
    return "\n\n".join(parts)


def _deterministic_coordinator(state: PipelineState) -> CoordinatorOutput:
    """Route to the next pipeline agent without an LLM call."""
    all_invocations = list(state.get("seed_invocations") or []) + list(state["completed_invocations"])
    completed = {inv.agent_id: inv for inv in all_invocations}

    # EXIT B — no documentation covers this ticket
    if "PR-04" in completed:
        rag_out = completed["PR-04"].output
        if not rag_out.get("answerable", True):
            return CoordinatorOutput(
                next_action="final_decision",
                agent_call=None,
                final_decision=FinalDecisionSpec(
                    route="escalate",
                    escalation_reason="not_answerable_from_docs",
                    draft=None,
                    confidence=float(rag_out.get("confidence", 70)),
                ),
                reasoning="EXIT B: RAG agent found ticket unanswerable from documentation.",
            )

    # Advance to the first agent not yet completed
    for agent_id, agent_name in [
        ("PR-02", "fluency_classifier"),
        ("PR-01", "intent_classifier"),
        ("PR-03", "urgency_classifier"),
        ("PR-04", "rag_agent"),
        ("PR-05", "response_drafter"),
    ]:
        if agent_id not in completed:
            return CoordinatorOutput(
                next_action="invoke_agent",
                agent_call=AgentCallSpec(
                    agent_id=agent_id,
                    agent_name=agent_name,
                    inputs={},
                ),
                final_decision=None,
                reasoning=f"Next in pipeline: {agent_name}.",
            )

    # All agents done — determine final route
    draft_inv = completed.get("PR-05")
    draft = draft_inv.output.get("draft") if draft_inv else None
    confidence = float(draft_inv.output.get("confidence", 80)) if draft_inv else 80

    intent_inv = completed.get("PR-01")
    mnr = intent_inv.output.get("must_not_auto_respond", False) if intent_inv else False

    if mnr:
        return CoordinatorOutput(
            next_action="final_decision",
            agent_call=None,
            final_decision=FinalDecisionSpec(
                route="escalate",
                escalation_reason="must_not_auto_respond",
                draft=draft,
                confidence=float(intent_inv.output.get("confidence", 80)),
            ),
            reasoning="All pipeline stages complete; must_not_auto_respond=true — escalating with draft for human review.",
        )

    return CoordinatorOutput(
        next_action="final_decision",
        agent_call=None,
        final_decision=FinalDecisionSpec(
            route="auto_respond",
            escalation_reason=None,
            draft=draft,
            confidence=confidence,
        ),
        reasoning="All pipeline agents completed — auto_respond with draft.",
    )


# ── Node Functions ────────────────────────────────────────────────────────────

def guardrails_ingress_node(state: PipelineState) -> dict:
    passed, results = run_ingress_guardrails(state["ticket"].body)
    updates: dict = {"guardrail_results": results}
    if not passed:
        for r in results:
            if not r.passed:
                GUARDRAIL_BLOCKS.labels(validator=r.validator).inc()
        first_fail = next(r for r in results if not r.passed)
        updates["final_decision"] = FinalDecisionSpec(
            route="blocked",
            escalation_reason=f"{first_fail.validator}_detected",
            draft=None,
            confidence=100,
        )
    return updates


def coordinator_node(state: PipelineState) -> dict:
    new_turn = state["turn_count"] + 1
    if new_turn > _MAX_TURNS:
        logger.error("Coordinator loop reached max turns (%d)", _MAX_TURNS)
        return {
            "turn_count": new_turn,
            "final_decision": FinalDecisionSpec(
                route="escalate",
                escalation_reason="max_turns_exceeded",
                draft=None,
                confidence=0.0,
            ),
            "pipeline_error": "max_turns_exceeded",
        }

    try:
        co = _deterministic_coordinator(state)
    except Exception as exc:
        logger.error("Coordinator routing failed: %s", exc)
        return {
            "turn_count": new_turn,
            "final_decision": FinalDecisionSpec(
                route="escalate",
                escalation_reason="coordinator_error",
                draft=None,
                confidence=0.0,
            ),
            "pipeline_error": str(exc),
        }

    updates: dict = {"turn_count": new_turn, "coordinator_output": co}

    if co.next_action == "final_decision" and co.final_decision is not None:
        fd = co.final_decision
        if fd.route == "auto_respond" and not fd.draft:
            draft_inv = next(
                (i for i in state["completed_invocations"] if i.agent_id == "PR-05"), None
            )
            if draft_inv:
                fd = FinalDecisionSpec(
                    route="auto_respond",
                    escalation_reason=None,
                    draft=draft_inv.output.get("draft"),
                    confidence=fd.confidence,
                )
        updates["final_decision"] = fd

    return updates


def _make_agent_node(agent_id: str, agent_name: str, get_run_fn):
    """Factory for the five sub-agent node functions (dynamically invokes current module function)."""
    def node(state: PipelineState) -> dict:
        co = state["coordinator_output"]
        inputs = dict(co.agent_call.inputs if co and co.agent_call else {})

        ticket = state["ticket"]
        for k, v in {
            "ticket_id": ticket.ticket_id,
            "channel": ticket.channel,
            "subject": ticket.subject,
            "body": ticket.body,
            "customer_tier": ticket.customer_tier,
            "language_fluency": ticket.language_fluency,
        }.items():
            if k not in inputs:
                inputs[k] = v

        all_invocations = list(state.get("seed_invocations") or []) + list(state["completed_invocations"])
        for inv in all_invocations:
            if inv.agent_id == "PR-02":
                inputs.setdefault("language_fluency", inv.output.get("fluency"))
            elif inv.agent_id == "PR-01":
                inputs.setdefault("intent", inv.output.get("intent"))
                inputs.setdefault("must_not_auto_respond", inv.output.get("must_not_auto_respond", False))
            elif inv.agent_id == "PR-03":
                inputs.setdefault("urgency", inv.output.get("urgency"))
            elif inv.agent_id == "PR-04":
                inputs.setdefault("relevant_doc_ids", inv.output.get("relevant_doc_ids", []))

        client = get_client()
        run_fn = get_run_fn()
        try:
            output = run_fn(inputs, client)
        except Exception as exc:
            logger.error("%s failed: %s", agent_name, exc)
            output = {"error": str(exc), "confidence": 0.0}

        confidence = float(output.get("confidence", 0.0))
        AGENT_CONFIDENCE.labels(agent=agent_id).observe(confidence)

        new_inv = CompletedInvocation(
            agent_id=agent_id,
            agent_name=agent_name,
            inputs=inputs,
            output=output,
        )
        return {"completed_invocations": state["completed_invocations"] + [new_inv]}

    node.__name__ = f"{agent_id.lower().replace('-', '_')}_agent_node"
    return node


fluency_agent_node = _make_agent_node("PR-02", "fluency_classifier", lambda: run_fluency)
intent_agent_node = _make_agent_node("PR-01", "intent_classifier", lambda: run_intent)
urgency_agent_node = _make_agent_node("PR-03", "urgency_classifier", lambda: run_urgency)
rag_agent_node = _make_agent_node("PR-04", "rag_agent", lambda: run_rag)
draft_agent_node = _make_agent_node("PR-05", "response_drafter", lambda: run_draft)


def guardrails_egress_node(state: PipelineState) -> dict:
    fd = state["final_decision"]
    draft = (fd.draft or "") if fd else ""

    citations: list[str] = []
    draft_inv = next(
        (i for i in state["completed_invocations"] if i.agent_id == "PR-05"), None
    )
    if draft_inv:
        citations = draft_inv.output.get("citations", [])
    doc_map = get_doc_map()
    cited_content = "\n\n".join(
        doc_map[did]["content"] for did in citations if did in doc_map
    )

    passed, results = run_egress_guardrails(draft, cited_content)
    updates: dict = {"guardrail_results": results}

    if not passed:
        for r in results:
            if not r.passed:
                GUARDRAIL_BLOCKS.labels(validator=r.validator).inc()
        first_fail = next(r for r in results if not r.passed)
        updates["final_decision"] = FinalDecisionSpec(
            route="escalate",
            escalation_reason=f"guardrail_block:{first_fail.validator}",
            draft=None,
            confidence=fd.confidence if fd else 0.0,
        )

    return updates


# ── Routing ───────────────────────────────────────────────────────────────────

def route_after_ingress(state: PipelineState) -> str:
    if state.get("final_decision") is not None:
        return "end"
    return "coordinator"


_AGENT_ROUTE = {
    "PR-02": "fluency_agent",
    "PR-01": "intent_agent",
    "PR-03": "urgency_agent",
    "PR-04": "rag_agent",
    "PR-05": "draft_agent",
}


def route_coordinator_output(state: PipelineState) -> str:
    fd = state.get("final_decision")
    if fd is not None:
        return "guardrails_egress" if fd.route == "auto_respond" else "end"

    co = state.get("coordinator_output")
    if co is None or co.next_action != "invoke_agent" or co.agent_call is None:
        return "end"

    return _AGENT_ROUTE.get(co.agent_call.agent_id, "end")


_AGENT_NODES = {
    "fluency_agent": fluency_agent_node,
    "intent_agent": intent_agent_node,
    "urgency_agent": urgency_agent_node,
    "rag_agent": rag_agent_node,
    "draft_agent": draft_agent_node,
}


def _apply_update(state: PipelineState, update: dict) -> PipelineState:
    new_state = dict(state)
    for k, v in update.items():
        if k == "guardrail_results":
            new_state["guardrail_results"] = list(state.get("guardrail_results") or []) + list(v)
        else:
            new_state[k] = v
    return new_state  # type: ignore[return-value]


class TriagePipelineCoordinator:
    """Facade & Orchestrator class for managing ticket triage pipeline execution (SOLID)."""

    def __init__(self, max_turns: int = _MAX_TURNS):
        self.max_turns = max_turns

    def run_pipeline_loop(self, initial: PipelineState) -> PipelineState:
        state = initial

        # 1. Ingress guardrails
        state = _apply_update(state, guardrails_ingress_node(state))
        if route_after_ingress(state) == "end":
            return state

        # 2. Coordinator–agent loop
        for _ in range(self.max_turns + 2):
            state = _apply_update(state, coordinator_node(state))
            route = route_coordinator_output(state)

            if route == "end":
                break
            if route == "guardrails_egress":
                state = _apply_update(state, guardrails_egress_node(state))
                break
            agent_node = _AGENT_NODES.get(route)
            if agent_node is None:
                logger.error("Unknown route '%s' — ending pipeline", route)
                break
            state = _apply_update(state, agent_node(state))

        return state

    def run(self, ticket: TicketInput) -> TriageResponse:
        seed_invocations: list[CompletedInvocation] = []
        if ticket.language_fluency is not None:
            seed_invocations.append(CompletedInvocation(
                agent_id="PR-02",
                agent_name="fluency_classifier",
                inputs={"ticket_id": ticket.ticket_id},
                output={
                    "fluency": ticket.language_fluency,
                    "confidence": 100,
                    "reasoning_summary": f"Language fluency sourced from ticket metadata: {ticket.language_fluency}.",
                },
            ))
        if ticket.urgency is not None:
            seed_invocations.append(CompletedInvocation(
                agent_id="PR-03",
                agent_name="urgency_classifier",
                inputs={"ticket_id": ticket.ticket_id},
                output={
                    "urgency": ticket.urgency,
                    "confidence": 100,
                    "reasoning_summary": f"Urgency sourced from ticket metadata: {ticket.urgency}.",
                },
            ))

        initial: PipelineState = {
            "ticket": ticket,
            "seed_invocations": seed_invocations,
            "completed_invocations": [],
            "coordinator_output": None,
            "final_decision": None,
            "guardrail_results": [],
            "turn_count": 0,
            "pipeline_error": None,
        }

        final_state: PipelineState = self.run_pipeline_loop(initial)

        fd = final_state.get("final_decision") or FinalDecisionSpec(
            route="escalate", escalation_reason="pipeline_error", draft=None, confidence=0.0
        )

        invocations = final_state.get("completed_invocations", [])
        all_invocations = list(final_state.get("seed_invocations") or []) + invocations
        COORDINATOR_TURNS.observe(final_state.get("turn_count", 0))

        intent_inv = next((i for i in all_invocations if i.agent_id == "PR-01"), None)
        urgency_inv = next((i for i in all_invocations if i.agent_id == "PR-03"), None)
        rag_inv = next((i for i in all_invocations if i.agent_id == "PR-04"), None)

        intent = intent_inv.output.get("intent") if intent_inv else None
        urgency = urgency_inv.output.get("urgency") if urgency_inv else None
        relevant_doc_ids = rag_inv.output.get("relevant_doc_ids", []) if rag_inv else []
        rag_output = rag_inv.output if rag_inv else None

        guardrail_results = final_state.get("guardrail_results", [])

        decision_id = write_decision(
            ticket_id=ticket.ticket_id,
            completed_invocations=[i.model_dump() for i in all_invocations],
            final_decision=fd.model_dump(),
            guardrail_results=[r.model_dump() for r in guardrail_results],
            response_relevant_doc_ids=relevant_doc_ids,
        )

        return TriageResponse(
            ticket_id=ticket.ticket_id,
            route=fd.route,
            draft=fd.draft,
            escalation_reason=fd.escalation_reason,
            intent=intent,
            urgency=urgency,
            relevant_doc_ids=relevant_doc_ids,
            rag_output=rag_output,
            confidence=fd.confidence,
            guardrail_results=guardrail_results,
            decision_id=decision_id,
        )


_COORDINATOR = TriagePipelineCoordinator()


def run_pipeline(ticket: TicketInput) -> TriageResponse:
    """Functional facade for TriagePipelineCoordinator."""
    return _COORDINATOR.run(ticket)
