# Configuration

## Environment variables

All configuration is via environment variables. Copy `.env.example` to `.env` and set the values before starting the container.

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_URL` | Yes | — | Base URL of the upstream LLM, including the `/v1` path prefix (e.g. `https://your.ollama.host:11434/v1`) |
| `F5_AI_GUARDRAILS_API_URL` | Yes | — | CalypsoAI API base URL (e.g. `https://www.us1.calypsoai.app/backend/v1`) |
| `F5_AI_GUARDRAILS_API_TOKEN` | Yes | — | CalypsoAI API bearer token |
| `F5_AI_GUARDRAILS_PROJECT_ID` | Yes | — | CalypsoAI project ID to scan against |
| `F5_AI_GUARDRAILS_SCAN_PROMPT` | | `true` | Set to `false` to disable prompt scanning |
| `F5_AI_GUARDRAILS_SCAN_RESPONSE` | | `true` | Set to `false` to disable response scanning |
| `F5_AI_GUARDRAILS_REDACT_PROMPT` | | `false` | When `true`, replace the last user/tool message with CalypsoAI's redacted version instead of passing through unchanged. Has no effect if `SCAN_PROMPT` is `false`. |
| `F5_AI_GUARDRAILS_REDACT_RESPONSE` | | `false` | When `true`, replace the last assistant response with CalypsoAI's redacted version instead of passing through unchanged. Has no effect if `SCAN_RESPONSE` is `false`. |
| `F5_AI_GUARDRAILS_FAIL_OPEN` | | `false` | Set to `true` to pass requests through to upstream when the guardrails scan API returns any error (network failure, non-2xx, or unparseable response). When `false` (default), scan errors block the request with HTTP 502. |
| `DEBUG` | | `false` | Set to `true` to log guardrails scan outcomes to the nginx error log |

## Request behaviour

### What is scanned

- **Prompt scan** — the content of the last `user` role message in the `messages` array is sent to F5 AI Guardrails before the request reaches the LLM.
- **Response scan** — the content of each `assistant` message in the LLM response's `choices` array is scanned before being returned to the client.

### Scan outcomes

| CalypsoAI outcome | Action |
|---|---|
| `cleared` | Pass through |
| `flagged` | Block — return HTTP 200 with `finish_reason: content_filter` |
| `redacted` (redact disabled) | Pass through unchanged |
| `redacted` (redact enabled) | Replace flagged content with redacted version; pass through |

## Responses

### Blocked content

When guardrails flags a prompt or response, the proxy returns HTTP 200 with a standard chat completion body. The `finish_reason` is set to `content_filter` and `content` is `null`:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "choices": [{
    "index": 0,
    "message": { "role": "assistant", "content": null },
    "finish_reason": "content_filter"
  }]
}
```

For streaming responses, a single SSE chunk is sent with the same `finish_reason: content_filter` and an empty delta:

```
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","model":"...","choices":[{"index":0,"delta":{},"finish_reason":"content_filter"}]}

data: [DONE]
```

### Guardrails errors

When the guardrails service is unreachable or returns an unexpected response, the proxy returns HTTP 502 with an OpenAI-shaped error body:

```json
{
  "error": {
    "message": "Guardrails scan error: guardrails_api_error",
    "type": "guardrails_block",
    "code": "guardrails_api_error"
  }
}
```

| Scenario | HTTP status | `code` |
|---|---|---|
| Guardrails unreachable | 502 | `guardrails_unreachable` |
| Guardrails API error (non-2xx) | 502 | `guardrails_api_error` |
| Guardrails response unparseable | 502 | `guardrails_parse_error` |

> **Fail-open mode** — when `F5_AI_GUARDRAILS_FAIL_OPEN=true`, all three scenarios above are treated as pass-through instead of blocking. A `warn`-level log line is always emitted so the degraded operation is visible:
> ```
> [guardrails] scan error (fail-open): guardrails_api_error — passing through
> ```

### Upstream LLM errors

Non-200 responses from the upstream LLM are passed through to the client unchanged.

## Limitations

- **Streaming buffered server-side** — streaming responses are fully buffered by the proxy before scanning. TTFB for streaming requests matches non-streaming latency. Tokens do not reach the client until the full response has been scanned.
- **Streaming redaction rebuilds the SSE stream** — when a streaming response is redacted, the proxy reconstructs an SSE stream that distributes the redacted content across the same number of chunks as the original upstream response (character-split evenly). The chunk count and all stream metadata (`id`, `model`, `created`, `finish_reason`) are preserved; granular per-token boundaries are not.
- **Last user message only** — only the most recent `user` message is submitted for prompt scanning, not the full conversation history.