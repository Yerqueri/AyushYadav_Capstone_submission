"""Jailbreak detection guardrail validator."""
from typing import Any
from src.guardrails.base import BaseGuardrailValidator
from src.guardrails.registry import GuardrailValidatorRegistry, _REGISTRY
from src.models import GuardrailResult


def _is_passed(res: Any) -> bool:
    outcome = str(getattr(res, "outcome", "")).lower()
    if outcome in ("pass", "outcome.pass"):
        return True
    if outcome in ("fail", "outcome.fail"):
        return False
    return bool(getattr(res, "validation_passed", True))


class JailbreakValidator(BaseGuardrailValidator):
    """Validator for detecting prompt jailbreaks in incoming ticket bodies."""

    def __init__(self, registry: GuardrailValidatorRegistry = _REGISTRY):
        super().__init__("detect_jailbreak")
        self.registry = registry

    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        def _run():
            from guardrails_ai.detect_jailbreak import DetectJailbreak
            val = self.registry.get_validator("detect_jailbreak", lambda: DetectJailbreak(on_fail="noop"))
            res = val.validate(text)
            passed = _is_passed(res)
            return GuardrailResult(
                validator=self.name,
                passed=passed,
                details=None if passed else "jailbreak_detected",
            )
        return self.registry.safe_run(self.name, _run)
