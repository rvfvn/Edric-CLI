"""Terminal entry point; presentation and approvals remain outside the agent."""

import argparse
import asyncio
import getpass
import json
import os
import shlex
import shutil
import sys
from pathlib import Path

from dotenv import set_key

from . import __version__
from .agent import Agent
from .config import DEFAULT_DEMO_WORKSPACE, PROJECT_ROOT, load_environment, load_servers
from .dispatcher import ToolDispatcher
from .display import Display
from .policy import ExecutionPolicy
from .terminal_input import read_line


def _parser():
    parser = argparse.ArgumentParser(prog="edric", description="A CLI coding assistant with visible MCP tools.")
    parser.add_argument("--version", action="version", version=f"Edric {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    def common(sub, demo=False):
        sub.add_argument("--workspace", type=Path, default=DEFAULT_DEMO_WORKSPACE if demo else Path.cwd())
        sub.add_argument("--config", type=Path, help="MCP server configuration JSON")
        sub.add_argument("--verbose", action="store_true", help="Display full tool output")
        sub.add_argument("--server", action="append", help="Connect only this server; repeat to select multiple")

    def agent_options(sub):
        sub.add_argument("--provider", choices=["groq", "bedrock", "ollama"], default="groq")
        sub.add_argument("--model", help="Provider model identifier")
        sub.add_argument("--mode", choices=["confirm", "auto"], default="confirm")
        sub.add_argument("--max-turns", type=int, default=12)
        sub.add_argument("--no-stream", action="store_true")

    setup = commands.add_parser("setup", help="Enter API keys locally using hidden input")
    setup.add_argument("--groq-only", action="store_true")
    setup.add_argument("--context7-only", action="store_true")
    doctor = commands.add_parser("doctor", help="Check prerequisites and MCP connections; no LLM inference")
    common(doctor)
    doctor.add_argument("--offline", action="store_true", help="Check local prerequisites only")
    tools = commands.add_parser("tools", help="Discover and display actual MCP tools")
    common(tools)
    run = commands.add_parser("run", help="Run a natural-language task")
    run.add_argument("task")
    common(run)
    agent_options(run)
    chat = commands.add_parser("chat", help="Start an interactive task prompt")
    common(chat)
    agent_options(chat)
    demo = commands.add_parser("demo", help="Prepare or run the disposable check-in demonstration")
    demos = demo.add_subparsers(dest="demo_command", required=True)
    for name, help_text in [("prepare", "Prepare starter files without service calls"),
                            ("checkin", "Demonstrate two actual MCP servers"),
                            ("agent", "Ask Groq to complete the demo task autonomously")]:
        sub = demos.add_parser(name, help=help_text)
        common(sub, demo=True)
        sub.add_argument("--reset", action="store_true", help="Reset only a marked Edric demo workspace")
        if name == "agent":
            agent_options(sub)
    return parser


def _setup(args, display):
    if args.groq_only and args.context7_only:
        raise ValueError("Choose at most one of --groq-only or --context7-only.")
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_symlink():
        raise ValueError("Refusing to write keys through a .env symlink.")
    display.text("Keys stay in the ignored local .env file. Enter a blank value to keep the current setting.")
    if not args.context7_only:
        display.text("Groq: https://console.groq.com/keys — verify that your account is on the Free Plan.")
        groq_key = getpass.getpass("Groq API key (hidden): ").strip()
        if groq_key:
            set_key(str(env_path), "GROQ_API_KEY", groq_key, quote_mode="always")
    if not args.groq_only:
        display.text("Context7: https://context7.com/dashboard — create a project API key.")
        context_key = getpass.getpass("Context7 API key (hidden): ").strip()
        if context_key:
            set_key(str(env_path), "CONTEXT7_API_KEY", context_key, quote_mode="always")
    if env_path.exists():
        env_path.chmod(0o600)
    display.text("Local configuration saved. Run 'edric doctor' to check connections.")
    return 0


async def _approval(tool, call):
    # Argument preview has already been displayed; secret values are redacted there.
    try:
        answer = await read_line(f"Allow {tool.name}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes"}


def _enabled_servers(args, workspace):
    servers = load_servers(workspace, args.config)
    if args.server:
        known = {server.name for server in servers}
        unknown = set(args.server) - known
        if unknown:
            raise ValueError("Unknown configured server: " + ", ".join(sorted(unknown)))
        servers = [server for server in servers if server.name in args.server]
    return servers


async def _run_task(args, workspace, hub, display, task):
    from .providers import create_provider
    provider = create_provider(args.provider, model=args.model, stream=not args.no_stream)
    provider.on_event = display.event
    dispatcher = ToolDispatcher(hub, workspace, ExecutionPolicy(args.mode), _approval, display.event)
    # Keep the first model context compact while discovery still lists the full inventory.
    relevant = {
        "read_text_file", "read_file", "read_multiple_files", "list_directory", "search_files",
        "write_file", "edit_file", "create_directory", "list_allowed_directories",
        "resolve-library-id", "query-docs", "get-library-docs", "fetch", "run_command",
    }
    dispatcher.tools = {name: tool for name, tool in dispatcher.tools.items()
                        if tool.original_name in relevant or tool.server not in {"filesystem", "context7", "fetch"}}
    display.text(f"Provider: {args.provider}; mode: {args.mode}; workspace: {workspace}")
    display.text("Starting an autonomous task. A final model response does not itself verify success.")
    try:
        outcome = await Agent(provider, dispatcher, display.event, args.max_turns, result_limit=8000).run(
            task, workspace, None if args.no_stream else display.stream)
        display.end_stream()
        if args.no_stream or outcome.status != "completed":
            display.text(outcome.message)
        display.text(f"Run status: {outcome.status}; model turns: {outcome.turns}; tool calls: {len(outcome.tool_results)}")
        errors = sum(result.status == "error" for result in outcome.tool_results)
        denials = sum(result.status == "denied" for result in outcome.tool_results)
        if errors or denials:
            display.text(f"Tool observations included {errors} errors and {denials} denials. Review the final response and checks.")
        # This exit status describes loop termination, not independently verified task completion.
        return 0 if outcome.status == "completed" else 1
    finally:
        await provider.close()


async def _async_main(args, display):
    from .mcp_client import MCPHub
    workspace = args.workspace.expanduser().resolve()
    if args.command == "demo":
        from .demo import prepare_demo_workspace
        prepare_demo_workspace(workspace, reset=args.reset)
        display.text(f"Disposable demonstration workspace: {workspace}")
        if args.demo_command == "prepare":
            display.text("Starter files prepared. No services were called.")
            return 0
    elif not workspace.is_dir():
        raise ValueError(f"Workspace is not a directory: {workspace}")
    servers = _enabled_servers(args, workspace)
    if args.command == "doctor":
        display.text(f"Python: {sys.version.split()[0]}")
        display.text(f"Node: {'available' if shutil.which('node') else 'missing'}")
        for name in ("GROQ_API_KEY", "CONTEXT7_API_KEY"):
            display.text(f"{name}: {'configured' if os.environ.get(name) else 'not configured'}")
        ready = True
        for server in servers:
            if server.transport == "stdio":
                exists = bool(server.command and shutil.which(server.command))
                display.text(f"{server.name} executable: {'available' if exists else 'missing; run npm ci or install the configured server'}")
                ready &= exists
        if args.offline:
            return 0 if ready else 1
        display.text("Checking MCP connections. This does not request LLM inference.")
    async with MCPHub(servers, on_event=display.event, timeout=30) as hub:
        if args.command in {"doctor", "tools"}:
            display.tools(hub.tools.values())
            required = [server.name for server in servers if server.required]
            healthy = {tool.server for tool in hub.tools.values()}
            return 0 if all(name in healthy for name in required) else 1
        if args.command == "demo" and args.demo_command == "checkin":
            from .demo import run_checkin_demo
            display.text("MCP integration demonstration: predetermined calls to real servers.")
            summary = await run_checkin_demo(hub, workspace, on_event=display.event)
            display.text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
            display.text("Two-server demonstration verified. This run did not use autonomous model selection.")
            return 0
        if args.command == "demo" and args.demo_command == "agent":
            from .demo import AUTONOMOUS_TASK
            task = AUTONOMOUS_TASK + f" Use this exact test command: {shlex.quote(sys.executable)} -m unittest discover -v."
            return await _run_task(args, workspace, hub, display, task)
        if args.command == "run":
            return await _run_task(args, workspace, hub, display, args.task)
        if args.command == "chat":
            display.text("Edric task prompt. Type /tools, /help, or /exit. Each task starts a fresh conversation.")
            while True:
                try:
                    task = await read_line("edric> ")
                except EOFError:
                    return 0
                task = task.strip()
                if task in {"/exit", "/quit"}:
                    return 0
                if task == "/tools":
                    display.tools(hub.tools.values())
                elif task == "/help":
                    display.text("Enter a coding task. /tools shows MCP tools. /exit closes the application.")
                elif task:
                    await _run_task(args, workspace, hub, display, task)
    return 0


def main():
    args = _parser().parse_args()
    load_environment()
    display = Display(verbose=getattr(args, "verbose", False))
    try:
        if args.command == "setup":
            code = _setup(args, display)
        else:
            code = asyncio.run(_async_main(args, display))
    except KeyboardInterrupt:
        display.end_stream()
        display.text("Cancelled. The current task may be incomplete; completed tool actions remain applied.")
        code = 130
    except Exception as exc:
        from .providers.base import ProviderError
        display.end_stream()
        if isinstance(exc, (ValueError, ProviderError, RuntimeError)):
            display.text(str(exc))
        else:
            display.text(f"Operation failed ({type(exc).__name__}). Check configuration and server status.")
        code = 1
    raise SystemExit(code)
