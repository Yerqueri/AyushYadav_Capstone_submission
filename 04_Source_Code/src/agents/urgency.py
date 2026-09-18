"""PR-03 — Urgency Classifier."""
from typing import Any

from src.agents.base import BaseAgent, call_llm
from src.prompts.urgency_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


class UrgencyClassifier(BaseAgent):
    """PR-03 Agent — Classifies ticket urgency (high, medium, low)."""

    def __init__(self):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            user_prompt_template=USER_PROMPT_TEMPLATE,
        )

    def _build_user_prompt(self, inputs: dict[str, Any]) -> str:
        return self.user_prompt_template.format(
            ticket_id=inputs["ticket_id"],
            channel=inputs["channel"],
            subject=inputs.get("subject", "(none)"),
            body=inputs["body"],
            customer_tier=inputs["customer_tier"],
            intent=inputs["intent"],
        )


def run_urgency(inputs: dict, client=None) -> dict:
    """Functional facade for UrgencyClassifier."""
    return UrgencyClassifier().run(inputs, client=client)
