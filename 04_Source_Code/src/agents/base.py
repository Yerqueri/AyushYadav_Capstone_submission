"""Base Agent abstract class following the Strategy / Template Method pattern."""
import sys
from abc import ABC, abstractmethod
from typing import Any

from src.agents._llm import call_llm, get_client, parse_json_output


class BaseAgent(ABC):
    """Abstract base class for all LLM specialist sub-agents (SOLID - Single Responsibility & Open-Closed)."""

    def __init__(self, system_prompt: str, user_prompt_template: str, model_name: str | None = None):
        self.system_prompt = system_prompt
        self.user_prompt_template = user_prompt_template
        self.model_name = model_name

    @abstractmethod
    def _build_user_prompt(self, inputs: dict[str, Any]) -> str:
        """Construct user prompt string from inputs."""
        pass

    def run(self, inputs: dict[str, Any], client: Any = None) -> dict[str, Any]:
        """Template method for executing agent reasoning and JSON extraction."""
        if client is None:
            client = get_client()

        user_prompt = self._build_user_prompt(inputs)
        fn_call_llm = getattr(sys.modules.get(self.__module__), "call_llm", call_llm)
        raw_text = fn_call_llm(
            client=client,
            system=self.system_prompt,
            user=user_prompt,
            model=self.model_name,
        )
        return parse_json_output(raw_text)

