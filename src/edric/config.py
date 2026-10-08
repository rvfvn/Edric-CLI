"""Configuration reads environment values; keys are never printed or committed."""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from .contracts import ServerConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DEMO_WORKSPACE = PROJECT_ROOT / ".edric" / "checkin-workspace"


def load_environment() -> None:
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def load_servers(workspace: Path, config_path: Path | None = None) -> list[ServerConfig]:
    """Use repository defaults or a local JSON config; expand named placeholders."""
    workspace = workspace.expanduser().resolve()
    selected = config_path or PROJECT_ROOT / "mcp_servers.json"
    if selected.exists():
        try:
            raw = json.loads(selected.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read MCP configuration: {selected.name}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("servers"), list):
            raise ValueError("MCP configuration must contain a 'servers' list.")
        entries = raw["servers"]
    elif config_path:
        raise ValueError(f"MCP configuration does not exist: {selected}")
    else:
        entries = [
            {
                "name": "filesystem", "transport": "stdio",
                "command": "${PROJECT_ROOT}/node_modules/.bin/mcp-server-filesystem",
                "args": ["${WORKSPACE}"],
            },
            {
                "name": "context7", "transport": "http",
                "url": "https://mcp.context7.com/mcp",
                "headers": {"Authorization": "Bearer ${CONTEXT7_API_KEY}"},
            },
        ]

    values = dict(os.environ)
    values.update(PROJECT_ROOT=str(PROJECT_ROOT), WORKSPACE=str(workspace), PYTHON=sys.executable)
    pattern = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")

    def expand(value: str, optional: bool = False) -> str | None:
        if not isinstance(value, str):
            raise ValueError("MCP configuration strings must be strings.")
        missing = [name for name in pattern.findall(value) if not values.get(name)]
        if missing:
            if optional:
                return None
            raise ValueError("Missing configuration variable: " + ", ".join(sorted(set(missing))))
        return pattern.sub(lambda match: values[match.group(1)], value)

    servers = []
    names = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each MCP server configuration must be an object.")
        if not entry.get("enabled", True):
            continue
        name = entry.get("name")
        transport = entry.get("transport")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", name):
            raise ValueError("MCP servers need a name containing letters, digits, underscores, or hyphens.")
        if name in names:
            raise ValueError(f"Duplicate server name: {name}")
        names.add(name)
        if transport not in {"stdio", "http"}:
            raise ValueError(f"Unsupported transport for {name}: expected stdio or http.")
        args = entry.get("args", [])
        headers = entry.get("headers", {})
        env = entry.get("env", {})
        if not isinstance(args, list) or not isinstance(headers, dict) or not isinstance(env, dict):
            raise ValueError(f"Invalid args, headers, or environment for {name}.")
        command = expand(entry["command"]) if entry.get("command") else None
        url = expand(entry["url"]) if entry.get("url") else None
        if transport == "stdio" and not command:
            raise ValueError(f"Missing command for {name}.")
        if transport == "http" and not url:
            raise ValueError(f"Missing URL for {name}.")
        expanded_headers = {}
        for key, value in headers.items():
            expanded = expand(value, optional=True)
            if expanded:
                expanded_headers[key] = expanded
        expanded_env = {}
        for key, value in env.items():
            expanded = expand(value, optional=True)
            if expanded is not None:
                expanded_env[key] = expanded
        servers.append(ServerConfig(
            name=name, transport=transport, command=command,
            args=[expand(arg) for arg in args], env=expanded_env,
            url=url, headers=expanded_headers,
            required=bool(entry.get("required", True)),
        ))
    return servers
