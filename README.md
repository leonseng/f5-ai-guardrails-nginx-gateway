# f5-ai-guardrails-nginx-gateway

An nginx + [NJS](https://nginx.org/en/docs/njs/) reverse proxy that adds synchronous [F5 AI Guardrails](https://www.f5.com/products/ai-guardrails) (CalypsoAI) sideband scanning to any OpenAI-compatible LLM API. Both the prompt and the LLM response are scanned before being passed on. Flagged content is blocked; everything else passes through.

## Architecture

```
Client
  │
  │  POST /v1/chat/completions
  ▼
nginx (port 11434)
  │
  ├─ [if SCAN_PROMPT=true]
  │    POST https://<F5_AI_GUARDRAILS_API_URL>/scans
  │    { input: "<last user message>", project: "<project_id>" }
  │    outcome=flagged → 400 prompt_blocked
  │
  ├─ Subrequest → upstream LLM (OPENAI_API_URL)
  │    POST /chat/completions
  │
  ├─ [if SCAN_RESPONSE=true]
  │    POST https://<F5_AI_GUARDRAILS_API_URL>/scans
  │    { input: "<assistant reply>", project: "<project_id>" }
  │    outcome=flagged → 400 response_blocked
  │
  └─ 200 → Client
```

All other paths (e.g. `GET /v1/models`) are proxied straight through to the upstream LLM without scanning.

> **Non-streaming only.** Requests with `"stream": true` are rejected with HTTP 400.

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

Each request logs two scan events (prompt then response).

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
| `redacted` | Pass through (redaction support coming later) |

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
| Streaming request | 400 | `streaming_not_supported` |
| Guardrails unreachable | 400 | `guardrails_unreachable` |
| Guardrails API error | 400 | `guardrails_api_error` |
| Upstream LLM error | 502 | `bad_gateway` |

## Limitations

- **Non-streaming only** — `"stream": true` requests are rejected.
- **No redaction** — `redacted` outcomes from CalypsoAI are currently treated as `cleared`. Full redaction support is planned.
- **Last user message only** — only the most recent `user` message is submitted for prompt scanning, not the full conversation history.
