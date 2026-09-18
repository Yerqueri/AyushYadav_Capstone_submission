"""PR-05 — Response Drafter."""
from typing import Any

from src.agents.base import BaseAgent, call_llm
from src.prompts.draft_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from src.retrieve import format_documents_block, get_doc_map


class ResponseDrafter(BaseAgent):
    """PR-05 Agent — Drafts customer support responses grounded in documentation."""

    def __init__(self):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            user_prompt_template=USER_PROMPT_TEMPLATE,
        )

    def _build_documents_block(self, relevant_doc_ids: list[str]) -> str:
        doc_map = get_doc_map()
        docs = [doc_map[did] for did in relevant_doc_ids if did in doc_map]
        if not docs:
            return "(No relevant documents were identified.)"
        return format_documents_block(docs)

    def _build_user_prompt(self, inputs: dict[str, Any]) -> str:
        documents_block = self._build_documents_block(inputs.get("relevant_doc_ids", []))
        return self.user_prompt_template.format(
            ticket_id=inputs["ticket_id"],
            channel=inputs["channel"],
            body=inputs["body"],
            customer_tier=inputs["customer_tier"],
            language_fluency=inputs.get("language_fluency", "fluent"),
            intent=inputs["intent"],
            urgency=inputs["urgency"],
            must_not_auto_respond=inputs.get("must_not_auto_respond", False),
            documents_block=documents_block,
        )


def run_draft(inputs: dict, client=None) -> dict:
    """Functional facade for ResponseDrafter."""
    return ResponseDrafter().run(inputs, client=client)
