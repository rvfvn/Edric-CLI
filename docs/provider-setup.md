# Provider access and remaining implementation

Groq is the implemented cloud adapter. Bedrock and Ollama are explicit skeletons:
selecting either reports that it is unavailable and makes no model request.
All keys stay in your local ignored `.env` file. Do not put keys in a task,
terminal screenshot, Git commit, or chat message.

## Groq: today's cloud path

1. Sign in at [Groq Console](https://console.groq.com).
2. Check your organization's billing plan. Stay on the **Free Plan** for this
   project; do not add a payment method or upgrade for today's check-in.
   Groq describes free/developer access in its
   [billing documentation](https://console.groq.com/docs/billing-faqs).
3. Open [API Keys](https://console.groq.com/keys), create a key named
   `edric-checkin`, and copy it when shown.
4. Run `edric setup` from this repository. Its hidden prompt stores the key as
   `GROQ_API_KEY` locally. You can also edit an ignored `.env` file copied from
   `.env.example`; avoid typing a key directly into a shell command or Git.
5. Keep `GROQ_MODEL=openai/gpt-oss-120b`, or supply the model option documented by
   `edric run --help`. This is a Groq-hosted open-weight model; it does not need
   an OpenAI API account. Groq lists it as supporting
   [local tool calling](https://console.groq.com/docs/tool-use/overview).
6. Complete the MCP demonstration first. Then run one small agent task with
   confirmation mode, using the task in `docs/checkin.md`.

This application uses the [official asynchronous Groq SDK](https://github.com/groq/groq-python).
It sends discovered tool schemas, receives requested calls, and executes tools
locally through Edric. It does not use Groq's built-in tools or server-managed
remote MCP orchestration. Call IDs and complete JSON arguments are retained in
conversation continuation. Malformed or truncated calls stop the turn before
any of its tools execute. See [Groq's orchestration guide](https://console.groq.com/docs/tool-use/local-tool-calling).

Free Plan limits are organization-wide. The documented default for this model
is currently 30 requests/minute, 1,000 requests/day, 8,000 tokens/minute, and
200,000 tokens/day; your console's actual limits take precedence. Edric disables
SDK retries and retries a rate limit at most twice when Groq supplies a retry
interval of at most 60 seconds, in a header or its error message. Each wait is
visible in the terminal. Other failures are shown without raw response bodies. There is no
automatic paid-provider fallback. [Groq rate limits](https://console.groq.com/docs/rate-limits)

The API adapter cannot determine your organization's billing tier. Confirm the
Free Plan in your own console before using live requests.

## Context7: external documentation MCP access

Context7 is an MCP server, not the model provider. Its key is separate from Groq.
Create an account at the [Context7 dashboard](https://context7.com/dashboard),
create a key named `edric-checkin`, then run `edric setup` to enter it privately
as `CONTEXT7_API_KEY`. The MCP configuration sends it to Context7 only.
[Context7 API key instructions](https://context7.com/docs/howto/api-keys)

The October 8 live rehearsal successfully used Context7 without a key. You can
run the check-in demonstration immediately and add a key later for access or limits.
The application omits its authorization header when no local key is configured.

## AWS Bedrock: future cloud adapter

No AWS account, subscription, credential, SDK client, or model invocation is
created by this version. `BEDROCK_MODEL_ID` and `AWS_REGION` are placeholders.
The following is a future setup sequence; stop before inference until the group
explicitly approves the selected model's usage costs.

1. Create or use your AWS account and sign in to the
   [Bedrock console](https://console.aws.amazon.com/bedrock). Choose one Region
   (the skeleton defaults to `us-east-1`) and a model that supports Converse and
   tool use in that Region. Record its exact model or inference-profile ID.
2. Review [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) for that
   model and Region. Estimate a small test's input/output tokens and agree on a
   spending limit before invoking the model. A console playground request is
   also inference; do not use it before that approval.
3. Use an IAM identity for development. Grant the selected model's required
   invocation permissions: `bedrock:InvokeModel` for Converse and
   `bedrock:InvokeModelWithResponseStream` for ConverseStream. Scope access to
   the chosen model and any required inference-profile resources.
   [Converse permissions and request format](https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html)
4. Check access requirements for the selected model. Many models have default
   access; some require account-level approval. Third-party Marketplace models
   may need a subscription and a valid payment method, and Anthropic may require
   a first-time-use form. Do not activate a subscription until approved.
   [Current model-access requirements](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
5. For a short development trial, follow AWS's console procedure to generate a
   short-term Bedrock API key and put it locally in
   `AWS_BEARER_TOKEN_BEDROCK`. Alternatively, use an IAM role or temporary
   credentials through the standard SDK credential chain. Do not use root
   access keys. Install `boto3` when implementing the adapter.
   [AWS Bedrock quickstart](https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started.html)
6. Fill `AWS_REGION` and `BEDROCK_MODEL_ID` locally. Implement conversion from
   Edric messages/tools into Converse `messages`, `system`, and `toolConfig`.
   Preserve `toolUseId` when returning `toolResult`, and implement streaming
   block assembly for ConverseStream. Keep Groq's implementation separate.
7. After cost approval and adapter tests, issue one small non-tool request with
   a low output-token cap, then one read-only tool cycle. Confirm the actual
   Region, selected model, and billing usage before a longer agent run.

Do not count the Bedrock skeleton as a working provider in the presentation.

## Ollama: future local adapter

The skeleton reads `OLLAMA_HOST` (default `http://localhost:11434`) and
`OLLAMA_MODEL`, but performs no connectivity check, model download, or inference.
Person 4 can later install Ollama, select a model that fits the machine and
supports tools, and implement `/api/chat` streaming and continuation behind the
same provider interface. Ollama's tool-call argument representation differs
from Groq's JSON string representation; the adapter must translate it.
[Ollama chat API](https://docs.ollama.com/api/chat),
[tool-calling examples](https://docs.ollama.com/capabilities/tool-calling)

Do not count this skeleton as tested Ollama support. The final assignment still
needs an actual local model and evaluation.
