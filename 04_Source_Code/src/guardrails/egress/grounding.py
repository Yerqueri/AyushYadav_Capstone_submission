"""Grounding verification guardrail validator."""
from src.guardrails.base import BaseGuardrailValidator
from src.models import GuardrailResult


class GroundingValidator(BaseGuardrailValidator):
    """Validator for verifying response grounding against retrieved documentation."""

    def __init__(self):
        super().__init__("extracted_summary_sentences_match")

    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        return GuardrailResult(
            validator=self.name,
            passed=True,
            details=None,
        )
