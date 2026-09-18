"""PII detection guardrail validator."""
from typing import Any

from src.guardrails.base import BaseGuardrailValidator
from src.guardrails.registry import GuardrailValidatorRegistry, _REGISTRY
from src.models import GuardrailResult

_PII_ENTITIES = ["PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION", "CREDIT_CARD"]


def _is_passed(res: Any) -> bool:
    outcome = str(getattr(res, "outcome", "")).lower()
    if outcome in ("pass", "outcome.pass"):
        return True
    if outcome in ("fail", "outcome.fail"):
        return False
    return bool(getattr(res, "validation_passed", True))


class PiiValidator(BaseGuardrailValidator):
    """Validator for detecting PII entities in response drafts."""

    def __init__(self, registry: GuardrailValidatorRegistry = _REGISTRY):
        super().__init__("guardrails_pii")
        self.registry = registry

    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        def _run():
            from guardrails_ai.guardrails_pii import GuardrailsPII
            val = self.registry.get_validator("guardrails_pii", lambda: GuardrailsPII(entities=_PII_ENTITIES, on_fail="noop"))
            res = val.validate(text, metadata={})
            passed = _is_passed(res)
            return GuardrailResult(
                validator=self.name,
                passed=passed,
                details=None if passed else "pii_detected",
            )
        return self.registry.safe_run(self.name, _run)
