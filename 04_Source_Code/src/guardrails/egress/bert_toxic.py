"""BERT toxic content detection guardrail validator."""
import logging
import re

from src.guardrails.base import BaseGuardrailValidator
from src.guardrails.registry import GuardrailValidatorRegistry, _REGISTRY
from src.models import GuardrailResult

logger = logging.getLogger(__name__)


class BertToxicValidator(BaseGuardrailValidator):
    """Validator for detecting toxic language in response drafts."""

    def __init__(self, registry: GuardrailValidatorRegistry = _REGISTRY):
        super().__init__("bert_toxic")
        self.registry = registry

    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        def _run():
            is_toxic = False
            try:
                from transformers import pipeline
                classifier = self.registry.get_validator(
                    "bert_toxic_classifier",
                    lambda: pipeline("text-classification", model="martin-ha/toxic-comment-model", device="cpu"),
                )
                res = classifier(text[:512])
                if res and isinstance(res, list):
                    label = str(res[0].get("label", "")).lower()
                    score = float(res[0].get("score", 0.0))
                    if "toxic" in label and "non" not in label and score > 0.5:
                        is_toxic = True
            except Exception as exc:
                logger.debug("BERT toxic classifier fallback: %s", exc)

            if not is_toxic:
                toxic_patterns = [r"(?i)\b(fuck|shit|bitch|bastard|crap|asshole|idiot|stupid)\b"]
                if any(re.search(pat, text) for pat in toxic_patterns):
                    is_toxic = True

            passed = not is_toxic
            return GuardrailResult(
                validator=self.name,
                passed=passed,
                details=None if passed else "toxic_content_detected",
            )
        return self.registry.safe_run(self.name, _run)
