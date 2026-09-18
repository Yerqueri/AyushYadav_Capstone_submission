from typing import Any
from src.models import TicketInput

VALID_FLUENCY = {"fluent", "non_fluent"}
VALID_URGENCY = {"high", "medium", "low"}


class TicketNormalizer:
    """Normalizer strategy for incoming raw ticket payloads (SOLID - Single Responsibility)."""

    def __init__(self, valid_fluency: set[str] = VALID_FLUENCY, valid_urgency: set[str] = VALID_URGENCY):
        self.valid_fluency = valid_fluency
        self.valid_urgency = valid_urgency

    def normalize(self, raw: dict[str, Any]) -> TicketInput:
        """Normalize raw request body to TicketInput.

        - Strips 'history' and 'labels' fields (internal labels, not user input).
        - Retains 'language_fluency' only if valid; otherwise sets to None.
        - Retains 'urgency' only if valid; otherwise sets to None.
        """
        payload = dict(raw)
        payload.pop("history", None)
        payload.pop("labels", None)

        fluency = payload.get("language_fluency")
        if fluency not in self.valid_fluency:
            payload["language_fluency"] = None

        urgency = payload.get("urgency")
        if urgency not in self.valid_urgency:
            payload["urgency"] = None

        return TicketInput(**payload)


_NORMALIZER = TicketNormalizer()


def normalize_ticket(raw: dict) -> TicketInput:
    """Functional facade for TicketNormalizer."""
    return _NORMALIZER.normalize(raw)
