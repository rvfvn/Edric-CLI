# October 8 check-in guide

The check-in asks the group to screen-share two of the three required MCP server
categories with working tools, then discuss challenges and next steps. The target
here is the official filesystem server plus Context7 for external documentation.
Fetch is a configured external-server fallback. This guide does not establish a
grade or imply that all final-project requirements are complete.

## Before the meeting

Follow the repository setup instructions, activate the Python 3.12 environment,
and use the repository root as your terminal working directory. Account setup
is in `docs/provider-setup.md`. Groq is the default for the rehearsed autonomous assistant,
but the predetermined MCP demonstration uses no model API.

```console
edric doctor
edric tools
edric demo prepare
edric demo checkin --reset
```

The default generated workspace is `.edric/checkin-workspace`. It is distinct
from the application's own source code. The filesystem server must permit that
workspace. `--reset` requires an Edric marker and resets only known generated
fixture files; arbitrary directories are rejected and additional files preserved.

Check the actual output before presenting. Successful discovery alone does not
prove successful tool invocation. A completed demo reports two different servers,
successful external retrieval, and exact readback of the edited file. Inspect
the generated `documentation.md`, `client.py`, and `demo-evidence.json`.

Run the generated fixture's four offline behavior checks separately:

```console
cd .edric/checkin-workspace
../../.venv/bin/python -m unittest discover -v
```

If the relative interpreter path differs on your machine, use the full path to
the project's `.venv/bin/python`. Tests mock HTTPX responses and do not contact
external websites. Tests deliberately fail on the starter before the edit.

## Three-to-five-minute presentation

1. **Scope, about 20 seconds:** "We are building a Python coding assistant. Today
   we will demonstrate the official filesystem MCP server and an external
   documentation MCP server. This first command uses predetermined tool calls."
2. **Discovery, about 30 seconds:** Run `edric tools`. Identify each connected
   server and show discovered tool definitions. Explain that the client gets
   definitions from the servers rather than maintaining a hard-coded tool list.
3. **Real tools, about 90 seconds:** Run `edric demo checkin --reset`. Show the
   filesystem read, Context7 library resolution and documentation retrieval,
   filesystem write/edit, and verification read. If Fetch is configured, name
   Fetch as the demonstrated external server.
4. **Verification, about 30 seconds:** Show the changed `client.py` and external
   documentation artifact. Run the four offline tests. Say exactly whether they
   passed; the demo does not itself run those tests.
5. **Challenges and next steps, about 60 seconds:** Describe only issues actually
   observed. Possible topics to verify beforehand are connecting two transports,
   evolving tool schemas, credentials/rate limits, and returning tool errors to
   the model. State the remaining work and team owners below.

## Optional autonomous demonstration

Only present this if a real Groq task has been rehearsed successfully. First
restore the starter with `edric demo prepare --reset`. Then run the assistant
against the generated workspace with this task:

> Read client.py and test_client.py. Retrieve HTTPX documentation through the
> external MCP server on timeouts and raise_for_status. Update fetch_json to
> accept a keyword-only timeout defaulting to 5.0 seconds, pass it to httpx.get,
> call raise_for_status before decoding JSON, and preserve HTTPX exceptions. Run
> the offline unittest suite using the project's Python interpreter. Do not change
> the tests. Summarize the documentation consulted, files changed, and test result.

Use `edric chat --workspace .edric/checkin-workspace --mode confirm` or the
equivalent one-task `edric run` command. Approve changes and commands at the
displayed prompts. The model chooses the calls in this mode. A final response
alone does not prove the task passed; inspect the actual test output.

The shortcut `edric demo agent --reset --mode confirm` prepares the same starter
and submits the task automatically. It requires a configured Groq key and working
external MCP access. Use the actual project's Python interpreter when running
the tests so HTTPX is available.

## Failure handling

- **Missing model key:** The direct MCP demonstration can still run. Do not claim
  that autonomous behavior was verified.
- **Context7 unavailable:** Fix credentials/connection, or explicitly configure
  the Fetch fallback and demonstrate it successfully before presenting.
- **Changed starter:** Repeat `edric demo checkin --reset` in the generated
  workspace. Do not reset your application source or another person's files.
- **Readback mismatch/tool error:** The demo stops with a failure. Resolve it and
  rerun; do not present partial output as successful completion.
- **Rate limit:** Keep successful artifacts as evidence and explain the live
  limitation. Retry only after the indicated interval; paid fallback is disabled.

## Final-project work still outstanding

Person 1 owns the CLI, agent loop, tool dispatch, command runner, confirmation and
auto modes, and integration. Person 2 owns MCP connection robustness and the
filesystem integration. Person 3 owns the persistent custom RAG MCP server,
corpus/indexing, and advanced RAG technique. Person 4 owns external MCP,
cloud/local providers and evaluation. AWS Bedrock remains a skeleton. The optional
Ollama adapter has mocked integration tests but no live model rehearsal yet; it
does not count as verified local inference. Groq remains today's default. The final project
also requires evaluation, report, diagrams, and video.

## Evidence record

Record the actual rehearsal result after running it:

| Check | Result to record |
|---|---|
| Filesystem MCP discovery and read/write/edit | Actual command output |
| External MCP live retrieval | Actual server and source |
| Exact changed-file readback | Demo result |
| Four offline fixture tests | Actual test result |
| Autonomous Groq tool cycle | Verified result, or "not yet verified" |

The October 8 rehearsal succeeded with filesystem and Context7, exact readback,
and four separately executed offline fixture tests. A live autonomous Groq run
also completed in 8 model turns and 7 tool calls, with all four tests passing.
See `docs/verification.md`. Free Plan waits may make the autonomous demo take
several minutes, so rehearse its timing and keep the direct demonstration ready.
The generated evidence file is created only after successful tools and matching readback.
