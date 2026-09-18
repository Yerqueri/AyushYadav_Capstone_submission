"""Guardrail pipeline manager (Orchestrator Pattern)."""
import sys

from src.guardrails.egress import (
    BertToxicValidator,
    GroundingValidator,
    PiiValidator,
    SystemPromptLeakageValidator,
)
from src.guardrails.ingress import JailbreakValidator, PromptInjectionValidator
from src.guardrails.registry import GuardrailValidatorRegistry, _REGISTRY
from src.models import GuardrailResult


class GuardrailPipeline:
    """Object-Oriented Manager for Ingress and Egress Guardrail Validations (SOLID - Open-Closed & Dependency Inversion)."""

    def __init__(self, registry: GuardrailValidatorRegistry = _REGISTRY):
        self.registry = registry
        self.ingress_validators = [
            JailbreakValidator(registry),
            PromptInjectionValidator(registry),
        ]
        self.egress_validators = [
            BertToxicValidator(registry),
            PiiValidator(registry),
            SystemPromptLeakageValidator(registry),
            GroundingValidator(),
        ]
        self._validator_map = {v.name: v for v in self.ingress_validators + self.egress_validators}

    def run_validator(self, validator_name: str, text: str, cited_content: str = "") -> GuardrailResult:
        validator = self._validator_map.get(validator_name)
        if validator:
            return validator.validate(text, cited_content)
        return GuardrailResult(validator=validator_name, passed=True, details="unknown_validator")

    def run_ingress(self, body: str) -> tuple[bool, list[GuardrailResult]]:
        mod_run_validator = getattr(sys.modules.get("src.guardrails"), "_run_validator", self.run_validator)
        results = [
            mod_run_validator("detect_jailbreak", body),
            mod_run_validator("detect_prompt_injection", body),
        ]
        return all(r.passed for r in results), results

    def run_egress(self, draft: str, cited_content: str = "") -> tuple[bool, list[GuardrailResult]]:
        mod_run_validator = getattr(sys.modules.get("src.guardrails"), "_run_validator", self.run_validator)
        results = [
            mod_run_validator("bert_toxic", draft),
            mod_run_validator("guardrails_pii", draft),
            mod_run_validator("detect_system_prompt_leakage", draft),
            mod_run_validator("extracted_summary_sentences_match", draft, cited_content),
        ]
        return all(r.passed for r in results), results
