"""Explicit provider selection; never silently falls back to a paid service."""

import os

from .base import ProviderError, ProviderUnavailableError
from .groq_provider import DEFAULT_MODEL, GroqProvider


def create_provider(name: str = "groq", model: str | None = None, stream: bool = True):
    normalized = name.lower().strip()
    if normalized == "groq":
        return GroqProvider(model=model or os.environ.get("GROQ_MODEL", DEFAULT_MODEL), stream=stream)
    if normalized in {"bedrock", "aws"}:
        raise ProviderUnavailableError(
            "AWS Bedrock is scaffolded but not implemented. See docs/provider-setup.md; "
            "AWS usage costs require approval before invocation. No AWS request was made."
        )
    if normalized == "ollama":
        from .ollama_provider import OllamaProvider
        return OllamaProvider(model=model, stream=stream)
    raise ProviderUnavailableError("Unknown provider. Choose groq or ollama; bedrock is a future adapter.")


__all__ = ["GroqProvider", "ProviderError", "ProviderUnavailableError", "create_provider"]
