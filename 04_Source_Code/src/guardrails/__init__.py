"""Guardrail package entrypoint and facades (SOLID - Interface Segregation & Facade Pattern)."""
from src.guardrails.base import BaseGuardrailValidator
from src.guardrails.pipeline import GuardrailPipeline
from src.guardrails.registry import GuardrailValidatorRegistry
from src.models import GuardrailResult

_GUARDRAIL_PIPELINE = GuardrailPipeline()


def _run_validator(validator_name: str, text: str, cited_content: str = "") -> GuardrailResult:
    return _GUARDRAIL_PIPELINE.run_validator(validator_name, text, cited_content)


def run_ingress_guardrails(body: str) -> tuple[bool, list[GuardrailResult]]:
    """Functional facade for ingress guardrails."""
    return _GUARDRAIL_PIPELINE.run_ingress(body)


def run_egress_guardrails(draft: str, cited_content: str = "") -> tuple[bool, list[GuardrailResult]]:
    """Functional facade for egress guardrails."""
    return _GUARDRAIL_PIPELINE.run_egress(draft, cited_content)


__all__ = [
    "BaseGuardrailValidator",
    "GuardrailValidatorRegistry",
    "GuardrailPipeline",
    "run_ingress_guardrails",
    "run_egress_guardrails",
    "_run_validator",
]
