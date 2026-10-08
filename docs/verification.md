# October 8 verified rehearsal

Environment: Python 3.12.14, Node 22.14.0, MCP SDK 1.30.0, Groq SDK 0.37.1,
official filesystem server 2026.8.31. Tests and tools were run from this repository.

## Live integration

`edric demo checkin --reset` completed successfully against actual servers:

- Filesystem: 14 tools dynamically discovered; list, read, write, edit, and readback.
- Context7: 2 tools discovered over HTTP; HTTPX library resolution and documentation
  query succeeded, retrieving 2,193 characters from `/encode/httpx`.
- The external result was written through filesystem MCP.
- The Python code edit was applied through filesystem MCP and verified by exact readback.
- A separate out-of-workspace read was correctly denied by the filesystem server.
- Sessions and subprocesses closed without SDK context errors.

No Context7 key was needed in this rehearsal. Authentication and service limits
can change; verify current behavior before presenting.

Generated evidence is local at `.edric/checkin-workspace/demo-evidence.json`.
That file records the demo calls and states `fixture_tests_run: false`; the demo
does not itself execute the behavioral suite.

## Behavioral verification

`python -m unittest discover -s .edric/checkin-workspace -p test_client.py -v`
passed **4 tests** on the actual code edited through MCP: JSON success/default
timeout, custom timeout, HTTP status exceptions, and network timeout propagation.
These tests mock HTTP responses and make no network requests.

`python -m pytest -q` passed **61 application tests**, covering continuation IDs,
success/error/denial observations, invalid calls, iteration limits, command output
and timeout handling, background-child cleanup, approvals, MCP discovery/lifetime
and errors, safe fixture reset/readback, provider stream assembly, bounded retry,
and credential redaction/cancellable terminal input.

## Live autonomous verification

`edric demo agent --reset --mode auto` completed a real model-selected task with
Groq's `openai/gpt-oss-120b`, in **8 model turns and 7 tool calls**:

1. Read `client.py` and `test_client.py` through filesystem MCP.
2. Resolve HTTPX through Context7 MCP.
3. Retrieve timeout and status-error documentation through Context7 MCP.
4. Edit `client.py` through filesystem MCP.
5. Execute the requested offline unittest suite with the local command tool.
6. Observe exit code 0 and four passing tests, then give a final summary.

The default changed from `openai/gpt-oss-20b` after the smaller model emitted
unparseable tool arguments. That rejected turn executed no tools. The first
larger-model attempt reached a Free Plan rate limit before editing; bounded,
visible retry intervals allowed the subsequent complete run to finish. These are
debugging observations, not a controlled final-project model comparison.

The completed live run used auto mode in the disposable fixture. Approval and
denial behavior is verified in application tests. A manual CLI Ctrl+C test also
closed the task prompt and MCP connection cleanly with exit status 130.

## Unverified or unfinished

After integrating Person 4's optional Ollama request flow, the application suite
passed **85 tests**, including mocked Ollama agent/tool/result continuation,
streaming, incomplete and invalid responses, failures, and cancellation. The
existing `edric demo checkin --reset` passed again with 14 filesystem tools and
2 Context7 tools, 2,193 documentation characters, and matching edited-file
readback. The four generated fixture tests also passed again. The agent loop,
Groq adapter/default, MCP configuration, demo fixture, and dependencies were
unchanged. No Ollama installation, model download, AWS call, or live model
inference was performed during this integration verification.

- Bedrock remains a skeleton. Ollama's optional adapter has mocked integration
  coverage but has not been verified against an actual local model.
- Custom advanced RAG is not implemented.
- LLM/RAG comparisons, final report, and video are outstanding.

The predetermined live MCP demonstration verifies the check-in integrations; the
separate Groq run verifies autonomous model selection. Final-project requirements
remain as listed above.
