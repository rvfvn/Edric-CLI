import os

from groq import Groq

from .base import LLMProvider


class GroqProvider(LLMProvider):
    """LLM provider for Groq cloud models."""

    def __init__(self, model: str = "openai/gpt-oss-20b"):
        self.model = model
        self.client = Groq(api_key=os.environ["GROQ_API_KEY"])

    def generate(self, messages, tools=None):
        kwargs = {
            "model": self.model,
            "messages": messages,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message

        return {
            "content": message.content or "",
            "tool_calls": [
                tool_call.model_dump()
                for tool_call in (message.tool_calls or [])
            ],
            "raw": response.model_dump(),
        }
