# f5-ai-guardrails-nginx-gateway

An nginx + [NJS](https://nginx.org/en/docs/njs/) reverse proxy that adds synchronous [F5 AI Guardrails](https://www.f5.com/products/ai-guardrails) (CalypsoAI) sideband scanning to any OpenAI-compatible LLM API. Both the prompt and the LLM response are scanned before being passed on. Flagged content is blocked; everything else passes through.

## Architecture

```
Client
  │
  │  POST /chat/completions  (stream: true or false)
  ▼
nginx (port 11434)
  │
  ├─ [if SCAN_PROMPT=true]
  │    POST https://<F5_AI_GUARDRAILS_API_URL>/scans
  │    outcome=flagged → 400 prompt_blocked
  │
  ├─ Subrequest → upstream LLM (OPENAI_API_URL)
  │    POST /chat/completions  (stream preserved as-is)
  │    ← full response buffered by NJS
  │
  ├─ [if SCAN_RESPONSE=true]
  │    POST https://<F5_AI_GUARDRAILS_API_URL>/scans
  │    Non-streaming: scans choices[].message.content
  │    Streaming:     assembles delta.content from SSE, scans assembled text
  │    outcome=flagged →
  │      Non-streaming: 400 response_blocked
  │      Streaming:     SSE chunk with finish_reason=content_filter + [DONE]
  │    outcome=redacted (redact enabled) →
  │      Non-streaming: JSON body with redacted content
  │      Streaming:     rebuilt SSE stream with redacted content
  │
  └─ Response → Client
       Non-streaming: JSON body
       Streaming:     SSE replayed from upstream buffer (or rebuilt if redacted)
```

All other paths (e.g. `GET /models`) are proxied straight through to the upstream LLM without scanning.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with the Compose plugin (`docker compose`)
- An OpenAI-compatible LLM endpoint (e.g. Ollama, vLLM, Azure OpenAI)
- An F5 AI Guardrails (CalypsoAI) account with an API token and project ID

## Quick start

```bash
# 1. Copy and fill in the environment file
cp .env.example .env
$EDITOR .env

# 2. Build and start the proxy
docker compose up --build -d

# 3. Send a request through the proxy (same interface as OpenAI)
curl http://localhost:11434/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

## Rebuilding after code changes

`docker compose build` only rebuilds the image — it does **not** restart the running container. After building you must recreate the container to pick up the changes:

```bash
# Recommended: build and recreate in one step
docker compose up --build -d

# Or separately:
docker compose build
docker compose up -d      # detects the new image and recreates the container

# To force a clean rebuild with no layer cache:
docker compose build --no-cache
docker compose up -d
```

View logs at any time with:
```bash
docker compose logs -f
```

### Sample log output

**Cleared requests** — prompt and response both pass scanning, upstream returns 200:

```
2026/04/27 02:50:29 [warn] 30#30: *2 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 32 chars)
2026/04/27 02:50:30 [warn] 30#30: *2 js: [guardrails] scan ← HTTP 200 (968ms)
2026/04/27 02:50:30 [warn] 30#30: *2 js: [guardrails] outcome=cleared  policy=  reason=
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 150 chars)
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] headers → Authorization: Bearer ***, Content-Type: application/json
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] scan ← HTTP 200 (383ms)
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] outcome=cleared  policy=  reason=
192.168.65.1 - - [27/Apr/2026:02:50:33 +0000] "POST /chat/completions HTTP/1.1" 200 1376 "-" "PostmanRuntime/7.53.0"
```

**Flagged request** — prompt scan returns `flagged`, request is blocked with HTTP 400:

```
2026/04/27 02:51:04 [warn] 30#30: *2 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 61 chars)
2026/04/27 02:51:05 [warn] 30#30: *2 js: [guardrails] scan ← HTTP 200 (902ms)
2026/04/27 02:51:05 [warn] 30#30: *2 js: [guardrails] outcome=flagged  policy=  reason=
192.168.65.1 - - [27/Apr/2026:02:51:05 +0000] "POST /chat/completions HTTP/1.1" 400 114 "-" "PostmanRuntime/7.53.0"
```

**Redacted prompt** — prompt scan returns `redacted` with `REDACT_PROMPT=true`, modified body forwarded to upstream:

```
2026/04/27 02:52:11 [warn] 30#30: *3 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 54 chars)
2026/04/27 02:52:12 [warn] 30#30: *3 js: [guardrails] scan ← HTTP 200 (874ms)
2026/04/27 02:52:12 [warn] 30#30: *3 js: [guardrails] outcome=redacted  policy=  reason=
2026/04/27 02:52:12 [warn] 30#30: *3 js: [guardrails] prompt redacted — forwarding modified body to upstream
2026/04/27 02:52:14 [warn] 30#30: *3 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 143 chars)
2026/04/27 02:52:15 [warn] 30#30: *3 js: [guardrails] scan ← HTTP 200 (391ms)
2026/04/27 02:52:15 [warn] 30#30: *3 js: [guardrails] outcome=cleared  policy=  reason=
192.168.65.1 - - [27/Apr/2026:02:52:15 +0000] "POST /chat/completions HTTP/1.1" 200 1341 "-" "PostmanRuntime/7.53.0"
```

**Redacted response (non-streaming)** — response scan returns `redacted` with `REDACT_RESPONSE=true`, modified response returned to client:

```
2026/04/27 02:53:07 [warn] 30#30: *4 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 38 chars)
2026/04/27 02:53:08 [warn] 30#30: *4 js: [guardrails] scan ← HTTP 200 (921ms)
2026/04/27 02:53:08 [warn] 30#30: *4 js: [guardrails] outcome=cleared  policy=  reason=
2026/04/27 02:53:11 [warn] 30#30: *4 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 187 chars)
2026/04/27 02:53:12 [warn] 30#30: *4 js: [guardrails] scan ← HTTP 200 (408ms)
2026/04/27 02:53:12 [warn] 30#30: *4 js: [guardrails] outcome=redacted  policy=  reason=
2026/04/27 02:53:12 [warn] 30#30: *4 js: [guardrails] response redacted — returning modified body to client
192.168.65.1 - - [27/Apr/2026:02:53:12 +0000] "POST /chat/completions HTTP/1.1" 200 1289 "-" "PostmanRuntime/7.53.0"
```

**Redacted response (streaming)** — streaming response scan returns `redacted` with `REDACT_RESPONSE=true`, rebuilt SSE stream with redacted content returned to client:

```
2026/04/27 02:54:01 [warn] 30#30: *5 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 38 chars)
2026/04/27 02:54:02 [warn] 30#30: *5 js: [guardrails] scan ← HTTP 200 (874ms)
2026/04/27 02:54:02 [warn] 30#30: *5 js: [guardrails] outcome=cleared  policy=  reason=
2026/04/27 02:54:05 [warn] 30#30: *5 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 195 chars)
2026/04/27 02:54:06 [warn] 30#30: *5 js: [guardrails] scan ← HTTP 200 (391ms)
2026/04/27 02:54:06 [warn] 30#30: *5 js: [guardrails] outcome=redacted  policy=  reason=
2026/04/27 02:54:06 [warn] 30#30: *5 js: [guardrails] response redacted — returning modified SSE to client
192.168.65.1 - - [27/Apr/2026:02:54:06 +0000] "POST /chat/completions HTTP/1.1" 200 643 "-" "PostmanRuntime/7.53.0"
```

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and set the values before starting the container.

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_URL` | ✅ | — | Base URL of the upstream LLM, including the `/v1` path prefix (e.g. `https://your.ollama.host:11434/v1`) |
| `OPENAI_API_KEY` | ✅ | — | API key forwarded to the upstream LLM as `Authorization: Bearer <key>` |
| `F5_AI_GUARDRAILS_API_URL` | ✅ | — | CalypsoAI API base URL (e.g. `https://www.us1.calypsoai.app/backend/v1`) |
| `F5_AI_GUARDRAILS_API_TOKEN` | ✅ | — | CalypsoAI API bearer token |
| `F5_AI_GUARDRAILS_PROJECT_ID` | ✅ | — | CalypsoAI project ID to scan against |
| `F5_AI_GUARDRAILS_SCAN_PROMPT` | | `true` | Set to `false` to disable prompt scanning |
| `F5_AI_GUARDRAILS_SCAN_RESPONSE` | | `true` | Set to `false` to disable response scanning |
| `F5_AI_GUARDRAILS_REDACT_PROMPT` | | `false` | When `true`, replace the last user/tool message with CalypsoAI's redacted version instead of passing through unchanged. Has no effect if `SCAN_PROMPT` is `false`. |
| `F5_AI_GUARDRAILS_REDACT_RESPONSE` | | `false` | When `true`, replace the last assistant response with CalypsoAI's redacted version instead of passing through unchanged. Has no effect if `SCAN_RESPONSE` is `false`. |
| `F5_AI_GUARDRAILS_FAIL_OPEN` | | `false` | Set to `true` to pass requests through to upstream when the guardrails scan API returns any error (network failure, non-2xx, or unparseable response). When `false` (default), scan errors block the request with HTTP 400. |
| `DEBUG` | | `false` | Set to `true` to log guardrails scan outcomes to the nginx error log |

## Request behaviour

### What is scanned

- **Prompt scan** — the content of the last `user` role message in the `messages` array is sent to F5 AI Guardrails before the request reaches the LLM.
- **Response scan** — the content of each `assistant` message in the LLM response's `choices` array is scanned before being returned to the client.

### Scan outcomes

| CalypsoAI outcome | Action |
|---|---|
| `cleared` | Pass through |
| `flagged` | Block — return HTTP 400 |
| `redacted` (redact disabled) | Pass through unchanged |
| `redacted` (redact enabled) | Replace flagged content with redacted version; pass through |

### Error responses

All errors are returned as JSON matching the OpenAI error shape:

```json
{
  "error": {
    "message": "Prompt blocked by AI Guardrails: flagged",
    "type": "guardrails_block",
    "code": "prompt_blocked"
  }
}
```

| Scenario | HTTP status | `code` |
|---|---|---|
| Prompt blocked | 400 | `prompt_blocked` |
| Response blocked | 400 | `response_blocked` |
| Response blocked (streaming) | 200 | `content_filter` (SSE `finish_reason`) |
| Guardrails unreachable | 400 | `guardrails_unreachable` |
| Guardrails API error | 400 | `guardrails_api_error` |
| Upstream LLM error | 502 | `bad_gateway` |

> **Fail-open mode** — when `F5_AI_GUARDRAILS_FAIL_OPEN=true`, the three guardrails error scenarios above (`guardrails_unreachable`, `guardrails_api_error`, and unparseable responses) are treated as pass-through instead of blocking. A `warn`-level log line is always emitted so the degraded operation is visible:
> ```
> [guardrails] scan error (fail-open): guardrails_api_error — passing through
> ```

## Limitations

- **Streaming buffered server-side** — streaming responses are fully buffered by the proxy before scanning. TTFB for streaming requests matches non-streaming latency. Tokens do not reach the client until the full response has been scanned.
- **Streaming redaction rebuilds the SSE stream** — when a streaming response is redacted, the proxy reconstructs an SSE stream that distributes the redacted content across the same number of chunks as the original upstream response (character-split evenly). The chunk count and all stream metadata (`id`, `model`, `created`, `finish_reason`) are preserved; granular per-token boundaries are not.
- **Last user message only** — only the most recent `user` message is submitted for prompt scanning, not the full conversation history.

## Testing

Tests spin up the proxy via Docker Compose and run against two local mock servers — one for F5 Guardrails and one for the LLM backend. No real credentials or external services are needed.

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Docker Compose

### Setup

```bash
cd tests
uv sync
```

### Running tests

```bash
uv run pytest
```

Proxy container logs stream to the terminal in real time alongside test output. The container is torn down automatically when the session ends.

To run a specific file:

```bash
uv run pytest tests/test_scan_blocked.py -v
```

### How it works

```
pytest
  ├── Flask mock /scans server  (port 9999)   ← F5 Guardrails mock
  ├── Flask mock LLM backend    (port 11435)  ← OpenAI-compatible LLM mock
  └── Docker proxy              (port 11434)  ← system under test
```

Both mock servers start automatically before the container comes up. The proxy is pointed at them via environment variables injected at startup — no changes to `docker-compose.yml` or `.env` are needed.

### Controlling mock behaviour

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

### Test files

| File | What it covers |
|---|---|
| `test_scan_cleared.py` | Prompt and response pass scanning, LLM response returned intact |
| `test_scan_blocked.py` | Prompt or response flagged — proxy returns 400 |
| `test_scan_redacted.py` | Prompt or response redacted — asterisks in content |
| `test_llm_errors.py` | LLM backend returns 500 / 503 / `content_filter` |
| `test_guardrails_errors.py` | Guardrails returns 422 or 500; fail-open passthrough behaviour |
| `test_combined.py` | Cross-cutting scenarios (e.g. blocked overrides LLM error) |