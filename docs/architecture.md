# Edric architecture — October 8 implementation

The original October 1 component diagram remains in README and planning Git
history. This version instantiates Python 3.12, Rich, the official MCP SDK 1.30,
the official filesystem server, Context7, and Groq. Bedrock/Ollama remain skeletons;
custom RAG has not been implemented. The direct demo and autonomous agent share
the MCP registry. The direct demo is a predetermined sequence, not model autonomy.

```mermaid
flowchart TB
    U[User] <--> CLI[CLI: tasks, streaming, approvals]
    CLI <--> A[Bounded agent loop]
    CLI --> DEMO[Predetermined check-in demo]
    A <--> P[Provider interface]
    P <--> G[Groq adapter]
    P -. future .-> AWS[Bedrock skeleton]
    P -. future .-> O[Ollama skeleton]
    A <--> D[Dispatcher: schemas and policy]
    D <--> CMD[Command runner: timeout and output]
    D <--> M[MCP client: discovery and routing]
    DEMO <--> M
    M <--> FS[Official filesystem MCP: local stdio]
    M <--> C[Context7 MCP: remote HTTP]
    M -. future .-> R[Custom advanced RAG MCP]
    FS <--> W[Selected workspace]
    CMD <--> W
```

## Task state and information flow

```mermaid
stateDiagram-v2
    [*] --> Connecting
    Connecting --> Ready: tools discovered, connection status displayed
    Connecting --> Unavailable: required demo server missing
    Ready --> Model: user submits task + available schemas
    Model --> Validating: structured tool calls
    Model --> FinalResponse: final text
    Model --> Failed: provider failure / incomplete call
    Validating --> Approval: write, command, or unknown tool in confirm mode
    Validating --> Executing: permitted read / auto mode
    Validating --> Observation: invalid arguments or tool
    Approval --> Executing: accepted
    Approval --> Observation: denied
    Executing --> Observation: content, error, or timeout
    Observation --> Model: result linked to original call ID
    Observation --> Limit: maximum model turns reached
    FinalResponse --> Ready
    Failed --> Ready
    Limit --> Ready
    Model --> Cancelled: Ctrl+C
    Executing --> Cancelled: Ctrl+C and process cleanup
    Cancelled --> [*]
    Unavailable --> [*]
```

`FinalResponse` describes model termination, not independently verified task
success. Failures and denials remain visible in the observations and final report.

## Scenario 1: inspect, consult docs, and edit

This scenario covers two operations: inspecting/updating local code and retrieving
external documentation. MCP schemas are loaded before the model task begins.

```mermaid
sequenceDiagram
    actor U as User
    participant CLI
    participant A as Agent loop
    participant L as Groq LLM
    participant D as Dispatcher/MCP client
    participant F as Filesystem MCP
    participant C as Context7 MCP
    CLI->>D: Connect and discover tools
    D->>F: initialize + tools/list
    F-->>D: filesystem schemas
    D->>C: initialize + tools/list
    C-->>D: documentation schemas
    U->>CLI: Improve HTTP client error handling
    CLI->>A: task and workspace
    A->>L: conversation + tool schemas
    L-->>A: read_text_file(path)
    A->>D: validate and execute read
    D->>F: tools/call
    F-->>D: file contents
    D-->>A: result linked to call ID
    A->>L: file observation
    L-->>A: resolve-library-id then query-docs
    A->>D: documentation calls, with observations between turns
    D->>C: tools/call
    C-->>D: library ID / documentation
    D-->>A: documentation observations
    A->>L: accumulated conversation
    L-->>A: edit_file(path, edits)
    A->>D: validate requested edit
    D->>CLI: request confirmation
    CLI->>U: show tool and arguments
    U-->>CLI: approve
    CLI-->>D: accepted
    D->>F: tools/call edit_file
    F-->>D: applied edit result
    D-->>A: edit observation
    A->>L: continue with edit result
    L-->>A: final response or further checks
    A-->>CLI: outcome
    CLI-->>U: streamed text, actions, outcome
```

## Scenario 2: execute tests and observe a failure

This scenario adds command/test execution as the third distinct operation.

```mermaid
sequenceDiagram
    actor U as User
    participant CLI
    participant A as Agent loop
    participant L as Groq LLM
    participant D as Dispatcher
    participant R as Command runner
    U->>CLI: Run tests and explain the result
    CLI->>A: task
    A->>L: task + available tools
    L-->>A: local__run_command(command)
    A->>D: validate and apply policy
    D->>CLI: confirmation request
    CLI->>U: show command
    alt user approves
        U-->>CLI: yes
        CLI-->>D: accepted
        D->>R: run in selected cwd, with deadline
        R-->>D: stdout, stderr, exit code, timeout status
        D-->>A: success/error observation
    else user denies
        U-->>CLI: no
        CLI-->>D: denied
        D-->>A: action not executed
    end
    A->>L: observation tied to original call ID
    L-->>A: next permitted action or final explanation
    A-->>CLI: actual run outcome
    CLI-->>U: checks, errors, remaining work
```

## Changes from planning

- Concrete stack choices replace the planning document's open candidates.
- Groq supplies the first cloud adapter; AWS is prepared for later implementation.
- SDK 1.x was chosen deliberately for compatibility with the Fetch fallback.
- Each MCP connection owns its async SDK contexts in a dedicated task, isolating
  failed servers and ensuring context managers close in the correct task.
- The direct demo provides independent MCP integration evidence while provider
  credentials or model behavior are being validated.
- RAG, Ollama, evaluation, and submission artifacts remain final-project work.
