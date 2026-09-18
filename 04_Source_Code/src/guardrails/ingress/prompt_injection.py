"""Prompt injection detection guardrail validator."""
import re
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


class PromptInjectionValidator(BaseGuardrailValidator):
    """Validator for detecting prompt injection attacks in incoming tickets."""

    def __init__(self, registry: GuardrailValidatorRegistry = _REGISTRY):
        super().__init__("detect_prompt_injection")
        self.registry = registry

    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        def _run():
            try:
                from guardrails_ai.detect_prompt_injection import DetectPromptInjection
                val = self.registry.get_validator("detect_prompt_injection", lambda: DetectPromptInjection(on_fail="noop"))
                res = val.validate(text)
                passed = _is_passed(res)
            except ImportError:
                injection_patterns = [
                    r"(?i)ignore\s+(all\s+)?(previous|prior)\s+instructions",
                    r"(?i)disregard\s+(all\s+)?(previous|prior)\s+instructions",
                    r"(?i)forget\s+(all\s+)?(previous|prior)\s+rules",
                    r"(?i)system\s+prompt\s+leak",
                    r"(?i)you\s+are\s+now\s+in\s+developer\s+mode",
                    r"(?i)override\s+system\s+(prompt|rules)",
                    r"(?i)act\s+as\s+dan",
                ]
                passed = not any(re.search(pat, text) for pat in injection_patterns)

            return GuardrailResult(
                validator=self.name,
                passed=passed,
                details=None if passed else "prompt_injection_detected",
            )
        return self.registry.safe_run(self.name, _run)
