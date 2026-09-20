"""System prompt leakage detection guardrail validator."""
from typing import Any

from src.guardrails.base import BaseGuardrailValidator
from src.guardrails.registry import GuardrailValidatorRegistry, _REGISTRY
from src.models import GuardrailResult

_PIPELINE_SYSTEM_PROMPT = (
    "You are a support ticket classifier for CloudServe Solutions. "
    "You are an urgency classifier for CloudServe Solutions. "
    "You are a language fluency detector for CloudServe Solutions. "
    "You are a document relevance assessor for CloudServe Solutions. "
    "You are a support response drafter for CloudServe Solutions."
)


def _is_passed(res: Any) -> bool:
    outcome = str(getattr(res, "outcome", "")).lower()
    if outcome in ("pass", "outcome.pass"):
        return True
    if outcome in ("fail", "outcome.fail"):
        return False
    return bool(getattr(res, "validation_passed", True))


class SystemPromptLeakageValidator(BaseGuardrailValidator):
    """Validator for preventing system prompt leakage in response drafts."""

    def __init__(self, registry: GuardrailValidatorRegistry = _REGISTRY):
        super().__init__("detect_system_prompt_leakage")
        self.registry = registry

    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        def _run():
            from guardrails_ai.detect_system_prompt_leakage import DetectSystemPromptLeakage
            val = self.registry.get_validator(
                "detect_system_prompt_leakage",
                lambda: DetectSystemPromptLeakage(system_prompt=_PIPELINE_SYSTEM_PROMPT, on_fail="noop"),
            )
            try:
                res = val.validate(text)
                passed = _is_passed(res)
            except TypeError as exc:
                if "error_message" in str(exc) or "errorMessage" in str(exc):
                    # FailResult in guardrails-ai passed errorMessage=... triggering TypeError on leakage detection
                    passed = False
                else:
                    raise

            return GuardrailResult(
                validator=self.name,
                passed=passed,
                details=None if passed else "system_prompt_leaked",
            )
        return self.registry.safe_run(self.name, _run)
