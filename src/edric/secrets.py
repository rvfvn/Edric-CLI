"""Keep configured provider credentials out of tool observations and child envs."""

import os

SECRET_ENV_NAMES = (
    "GROQ_API_KEY", "CONTEXT7_API_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
)


def secret_values() -> list[str]:
    return [os.environ[name] for name in SECRET_ENV_NAMES if os.environ.get(name)]


def redact(text: str) -> str:
    for secret in sorted(secret_values(), key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text


def command_environment() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name not in SECRET_ENV_NAMES}
