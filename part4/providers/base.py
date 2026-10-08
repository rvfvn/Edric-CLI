from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """Common interface for all LLM providers."""

    @abstractmethod
    def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Generate a response from the LLM.

        Args:
            messages: Conversation messages.
            tools: Optional tools available to the model.

        Returns:
            Standardized provider response.
        """
        pass