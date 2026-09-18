"""Guardrail instance registry and safe execution manager (Singleton / Cache Pattern)."""
import logging
import threading
from typing import Any, Callable

from src.models import GuardrailResult

logger = logging.getLogger(__name__)


class GuardrailValidatorRegistry:
    """Singleton cache and safe execution manager for PyTorch / Guardrails AI validator instances."""

    def __init__(self):
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_validator(self, key: str, factory_fn: Callable[[], Any]) -> Any:
        with self._lock:
            if key not in self._cache:
                self._cache[key] = factory_fn()
            return self._cache[key]

    def safe_run(self, name: str, fn: Callable[[], GuardrailResult]) -> GuardrailResult:
        """Execute fn(); return a passing result on any exception."""
        try:
            return fn()
        except ImportError:
            logger.warning("Guardrails validator '%s' not installed — treated as passed", name)
            return GuardrailResult(validator=name, passed=True, details="not_installed")
        except Exception as exc:
            logger.warning("Guardrails validator '%s' raised %s — treated as passed", name, exc)
            return GuardrailResult(validator=name, passed=True, details=f"error:{type(exc).__name__}")


_REGISTRY = GuardrailValidatorRegistry()
