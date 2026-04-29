# Testing

Tests spin up the proxy via Docker Compose and run against two local mock servers — one for F5 Guardrails and one for the LLM backend. No real credentials or external services are needed.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker Compose

## Setup

```bash
cd tests
uv sync
```

## Running tests

```bash
uv run pytest
```

Proxy container logs stream to the terminal in real time alongside test output. The container is torn down automatically when the session ends.

To run a specific file:

```bash
uv run pytest tests/test_scan_blocked.py -v
```

## How it works

```
pytest
  ├── Flask mock /scans server  (port 9999)   ← F5 Guardrails mock
  ├── Flask mock LLM backend    (port 11435)  ← OpenAI-compatible LLM mock
  └── Docker proxy              (port 11434)  ← system under test
```

Both mock servers start automatically before the container comes up. The proxy is pointed at them via environment variables injected at startup — no changes to `docker-compose.yml` or `.env` are needed.

## Controlling mock behaviour

Each test declares which scenario it needs via fixtures:

| Fixture | Values |
|---|---|
| `guardrails_scenario` | `cleared` (default), `blocked`, `redacted`, `422`, `guardrails_error` |
| `llm_scenario` | `normal` (default), `refusal`, `error`, `unavailable` |

```python
def test_something(guardrails_scenario, llm_scenario):
    guardrails_scenario.set("blocked")
    llm_scenario.set("normal")
    resp = chat_request("anything")
    assert resp.status_code == 400
```

Scenarios reset to their defaults after each test automatically.

## Test files

| File | What it covers |
|---|---|
| `test_scan_cleared.py` | Prompt and response pass scanning, LLM response returned intact |
| `test_scan_blocked.py` | Prompt or response flagged — proxy returns 400 |
| `test_scan_redacted.py` | Prompt or response redacted — asterisks in content |
| `test_llm_errors.py` | LLM backend returns 500 / 503 / `content_filter` |
| `test_guardrails_errors.py` | Guardrails returns 422 or 500; fail-open passthrough behaviour |
| `test_combined.py` | Cross-cutting scenarios (e.g. blocked overrides LLM error) |