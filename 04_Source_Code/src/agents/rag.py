"""PR-04 — RAG Agent. Retrieves documents in Python, LLM assesses relevance only."""
import os
import sys
from typing import Any

from src.agents._llm import get_client, parse_json_output
from src.agents.base import BaseAgent, call_llm
from src.prompts.rag_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.retrieve import format_documents_block, retrieve

_FALLBACK = {
    "relevant_doc_ids": [],
    "answerable": False,
    "confidence": 0,
    "reasoning_summary": "Retrieval returned no documents.",
}


class RagAgent(BaseAgent):
    """PR-04 Agent — Evaluates document relevance and ticket answerability."""

    def __init__(self):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            user_prompt_template=USER_PROMPT_TEMPLATE,
            model_name=os.getenv("MODEL_NAME", "gpt-4.1-mini"),
        )

    def _build_user_prompt(self, inputs: dict[str, Any]) -> str:
        return ""

    def run(self, inputs: dict[str, Any], client: Any = None) -> dict[str, Any]:
        intent = inputs.get("intent") or ""
        subject = inputs.get("subject") or ""
        body = inputs.get("body") or ""
        query = f"{intent.replace('_', ' ')} {subject} {body[:400]}".strip()

        retrieved_docs = retrieve(query)
        if not retrieved_docs:
            return _FALLBACK

        documents_block = format_documents_block(retrieved_docs)
        user_prompt = self.user_prompt_template.format(
            ticket_id=inputs["ticket_id"],
            intent=intent or "unknown",
            urgency=inputs.get("urgency") or "unknown",
            subject=subject or "unknown",
            body=body,
            documents=documents_block,
        )

        if client is None:
            client = get_client()

        try:
            fn_call_llm = getattr(sys.modules.get(self.__module__), "call_llm", call_llm)
            raw_text = fn_call_llm(client, self.system_prompt, user_prompt, model=self.model_name)
            return parse_json_output(raw_text)
        except (ValueError, Exception):
            return _FALLBACK


def run_rag(inputs: dict, client=None) -> dict:
    """Functional facade for RagAgent."""
    return RagAgent().run(inputs, client=client)
