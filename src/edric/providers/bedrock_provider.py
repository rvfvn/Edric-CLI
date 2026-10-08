"""AWS Bedrock adapter scaffold. No client or billable request is created."""

import os

from edric.contracts import JSON, ModelTurn, TextCallback, ToolDefinition

from .base import ProviderUnavailableError


class BedrockProvider:
    def __init__(self, model: str | None = None, stream: bool = True):
        self.model = model or os.environ.get("BEDROCK_MODEL_ID")
        self.region = os.environ.get("AWS_REGION", "us-east-1")
        self.stream = stream

    async def complete(
        self,
        messages: list[JSON],
        tools: list[ToolDefinition],
        on_text: TextCallback | None = None,
    ) -> ModelTurn:
        raise ProviderUnavailableError(
            "AWS Bedrock is scaffolded but not implemented. Follow docs/provider-setup.md "
            "after approving AWS usage costs. No AWS request was made."
        )

    async def close(self) -> None:
        pass
