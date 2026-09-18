"""PR-01 — Intent Classifier."""
from typing import Any

from src.agents.base import BaseAgent, call_llm
from src.prompts.intent_prompts import INTENT_CLASSES, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


class IntentClassifier(BaseAgent):
    """PR-01 Agent — Classifies ticket intent and sets must_not_auto_respond."""

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
            language_fluency=inputs.get("language_fluency", "unknown"),
            intent_classes=INTENT_CLASSES,
        )


def run_intent(inputs: dict, client=None) -> dict:
    """Functional facade for IntentClassifier."""
    return IntentClassifier().run(inputs, client=client)
