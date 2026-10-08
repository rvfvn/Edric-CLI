import requests

from .base import LLMProvider


class OllamaProvider(LLMProvider):
    """LLM provider for locally running Ollama models."""

    def __init__(
        self,
        model: str = "qwen2.5-coder:7b",
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def generate(self, messages, tools=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        if tools:
            payload["tools"] = tools

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=120,
        )

        response.raise_for_status()

        data = response.json()
        message = data.get("message", {})

        return {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
            "raw": data,
        }