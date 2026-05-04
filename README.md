# f5-ai-guardrails-nginx-gateway

An nginx + [NJS](https://nginx.org/en/docs/njs/) reverse proxy that adds synchronous [F5 AI Guardrails](https://www.f5.com/products/ai-guardrails) sideband scanning to any OpenAI-compatible LLM API. Both the prompt and the LLM response are scanned before being passed on. Flagged content is blocked; everything else passes through.

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
  │    outcome=flagged → 200 finish_reason=content_filter
  │
  ├─ Subrequest → upstream LLM (OPENAI_API_URL)
  │    POST /chat/completions  (stream preserved as-is)
  │    ← full response buffered by NJS
  │
  ├─ [if SCAN_RESPONSE=true]
  │    POST https://<F5_AI_GUARDRAILS_API_URL>/scans
  │    Non-streaming: scans choices[].message.content
  │    Streaming:     assembles delta.content from SSE, scans assembled text
  │    outcome=flagged → 200 finish_reason=content_filter
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
- An F5 AI Guardrails account with an API token and project ID

## Quick start

```bash
# 1. Copy and fill in the environment file. See docs/configuration.md for more information
cp .env.example .env
nano .env

# 2. Build and start the proxy
docker compose up --build -d

# 3. Send a request through the proxy (same interface as OpenAI)
curl http://localhost:11434/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'

# 4. View proxylogs
docker compose logs -f
```

## Further reading

- [Configuration reference](docs/configuration.md) — environment variables, scan behaviour, and error responses
- [Testing](docs/testing.md) — running the test suite locally
- [Troubleshooting](docs/troubleshooting.md) — sample log output for common scenarios