# Configuration

## Environment variables

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

## Error responses

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