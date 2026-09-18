"""Guardrail result domain models."""
from typing import Optional
from pydantic import BaseModel


class GuardrailResult(BaseModel):
    validator: str
    passed: bool
    details: Optional[str] = None
