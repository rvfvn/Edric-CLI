"""Ollama adapter scaffold. It does not contact or download a local model."""

import os

from edric.contracts import JSON, ModelTurn, TextCallback, ToolDefinition

from .base import ProviderUnavailableError


class OllamaProvider:
    def __init__(self, model: str | None = None, stream: bool = True):
        self.model = model or os.environ.get("OLLAMA_MODEL")
        self.base_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        self.stream = stream

    async def complete(
        self,
        messages: list[JSON],
        tools: list[ToolDefinition],
        on_text: TextCallback | None = None,
    ) -> ModelTurn:
        raise ProviderUnavailableError(
            "Ollama is scaffolded but not implemented. A local tool-capable model and "
            "adapter implementation are still required. No model was downloaded or contacted."
        )

    async def close(self) -> None:
        pass
