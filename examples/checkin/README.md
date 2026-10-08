# October 8 demonstration fixture

Use `edric demo prepare` to generate a dedicated workspace. The sample deliberately
starts with a missing request timeout and missing HTTP error check.

The behavior to implement is a keyword-only `timeout` defaulting to `5.0`, passed
to `httpx.get`, followed by `response.raise_for_status()` before JSON decoding.
HTTPX exceptions should propagate. The generated `test_client.py` uses mocked
responses; it does not make network requests.

`edric demo checkin` runs a **predetermined live MCP integration demonstration**:
filesystem tools read/write/edit, and Context7 retrieves actual documentation.
Its code change is predetermined. It does not demonstrate autonomous planning.

An autonomous run uses the same tools through the model and agent loop; the task
is documented in `docs/checkin.md`. Run `edric demo prepare --reset` before that
run to restore the starter. Reset requires the Edric marker and only resets known
fixture files; additional files are preserved.
