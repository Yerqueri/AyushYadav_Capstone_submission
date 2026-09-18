"""PR-02 — Language Fluency Classifier."""
from typing import Any

from src.agents.base import BaseAgent, call_llm
from src.prompts.fluency_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


class FluencyClassifier(BaseAgent):
    """PR-02 Agent — Classifies author English fluency."""

    def __init__(self):
        super().__init__(
            system_prompt=SYSTEM_PROMPT,
            user_prompt_template=USER_PROMPT_TEMPLATE,
        )

    def _build_user_prompt(self, inputs: dict[str, Any]) -> str:
        return self.user_prompt_template.format(
            ticket_id=inputs["ticket_id"],
            channel=inputs["channel"],
            body=inputs["body"],
        )


def run_fluency(inputs: dict, client=None) -> dict:
    """Functional facade for FluencyClassifier."""
    return FluencyClassifier().run(inputs, client=client)
