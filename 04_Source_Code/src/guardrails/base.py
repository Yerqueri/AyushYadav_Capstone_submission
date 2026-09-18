"""Base guardrail validator abstract class (SOLID - Strategy Pattern & Single Responsibility)."""
from abc import ABC, abstractmethod
from src.models import GuardrailResult


class BaseGuardrailValidator(ABC):
    """Abstract base class for individual guardrail validator implementations."""

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def validate(self, text: str, cited_content: str = "") -> GuardrailResult:
        """Validate input/output text and return a GuardrailResult."""
        pass
