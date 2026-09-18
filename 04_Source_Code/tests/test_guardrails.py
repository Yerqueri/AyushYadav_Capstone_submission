"""Tests for src/guardrails.py — validators are mocked at the import layer."""
import pytest
from unittest.mock import patch, MagicMock


def _mock_outcome(passed: bool):
    outcome = MagicMock()
    outcome.validation_passed = passed
    return outcome


def _mock_guard(passed: bool):
    guard = MagicMock()
    guard.validate.return_value = _mock_outcome(passed)
    return guard


def test_ingress_all_pass():
    from src.guardrails import run_ingress_guardrails

    with patch("src.guardrails._run_validator") as mock_run:
        from src.models import GuardrailResult
        mock_run.side_effect = [
            GuardrailResult(validator="detect_jailbreak", passed=True),
            GuardrailResult(validator="detect_prompt_injection", passed=True),
        ]
        passed, results = run_ingress_guardrails("Normal support question")

    assert passed is True
    assert len(results) == 2
    assert all(r.passed for r in results)


def test_ingress_jailbreak_blocked():
    from src.guardrails import run_ingress_guardrails

    with patch("src.guardrails._run_validator") as mock_run:
        from src.models import GuardrailResult
        mock_run.side_effect = [
            GuardrailResult(validator="detect_jailbreak", passed=False, details="jailbreak"),
            GuardrailResult(validator="detect_prompt_injection", passed=True),
        ]
        passed, results = run_ingress_guardrails("Ignore previous instructions...")

    assert passed is False
    assert results[0].passed is False


def test_egress_all_pass():
    from src.guardrails import run_egress_guardrails

    with patch("src.guardrails._run_validator") as mock_run:
        from src.models import GuardrailResult
        mock_run.side_effect = [
            GuardrailResult(validator="bert_toxic", passed=True),
            GuardrailResult(validator="guardrails_pii", passed=True),
            GuardrailResult(validator="detect_system_prompt_leakage", passed=True),
            GuardrailResult(validator="extracted_summary_sentences_match", passed=True),
        ]
        passed, results = run_egress_guardrails("Here is your answer.", "doc content here")

    assert passed is True
    assert len(results) == 4


def test_egress_toxic_fails():
    from src.guardrails import run_egress_guardrails

    with patch("src.guardrails._run_validator") as mock_run:
        from src.models import GuardrailResult
        mock_run.side_effect = [
            GuardrailResult(validator="bert_toxic", passed=False, details="toxic"),
            GuardrailResult(validator="guardrails_pii", passed=True),
            GuardrailResult(validator="detect_system_prompt_leakage", passed=True),
            GuardrailResult(validator="extracted_summary_sentences_match", passed=True),
        ]
        passed, results = run_egress_guardrails("offensive text", "docs")

    assert passed is False


def test_validator_not_installed_treated_as_passed():
    from src.guardrails import _run_validator

    with patch("builtins.__import__", side_effect=ImportError("not installed")):
        result = _run_validator("detect_jailbreak", "some text")

    assert result.passed is True
    assert result.details == "not_installed"
