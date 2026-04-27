# Implementation Plan: NJS-based OpenAI Proxy with F5 AI Guardrails

## Overview

Extend `leonseng/openai-api-auth-proxy` (nginx + NJS) to add synchronous sideband F5 AI Guardrails scans on both the request and response for `/v1/chat/completions`. Non-streaming only. No redaction — scan and block only.

```
client → nginx (NJS) → [scan prompt] → /app/ → upstream LLM → [scan response] → client
                              ↕                                       ↕
                      F5 AI Guardrails                        F5 AI Guardrails
```

---

## Repo Structure

Start from `leonseng/openai-api-auth-proxy`. Final layout:

```
.
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
└── nginx/
    ├── nginx.conf
    └── guardrails.js        ← new/replaced NJS module
```

---

## Environment Variables

`.env.example` — extend the existing file with the new variables:

```bash
# Existing
OPENAI_API_URL=https://your.ollama.com:11434
OPENAI_API_KEY=your-actual-api-key
DEBUG=false

# New
F5_AI_GUARDRAILS_API_URL=https://www.us1.calypsoai.app/backend/v1
F5_AI_GUARDRAILS_API_TOKEN=your_token_here
F5_AI_GUARDRAILS_PROJECT_ID=your_project_id_here
F5_AI_GUARDRAILS_SCAN_PROMPT=true
F5_AI_GUARDRAILS_SCAN_RESPONSE=true
```

---

## File 1: `nginx/nginx.conf`

Complete file, replacing the existing one.

### Top-level: load NJS module and expose env vars

```nginx
load_module modules/ngx_http_js_module.so;

# All env vars that NJS reads via process.env must be declared here,
# at the top-level context, outside http {}.
env OPENAI_API_URL;
env OPENAI_API_KEY;
env F5_AI_GUARDRAILS_API_URL;
env F5_AI_GUARDRAILS_API_TOKEN;
env F5_AI_GUARDRAILS_PROJECT_ID;
env F5_AI_GUARDRAILS_SCAN_PROMPT;
env F5_AI_GUARDRAILS_SCAN_RESPONSE;
env DEBUG;

events {}

http {
    js_path "/etc/nginx/njs/";
    js_import main from guardrails.js;

    # Required for resolver when proxy_pass uses a variable.
    # 127.0.0.11 is Docker's embedded DNS resolver.
    # Replace with your cluster DNS (e.g. kube-dns ClusterIP) for Ingress.
    resolver 127.0.0.11 valid=10s;

    # Ensure full request body is buffered before NJS reads r.requestText.
    client_body_buffer_size  10m;
    client_max_body_size     10m;

    server {
        listen 11434;

        # Pass all non-completions traffic straight through (e.g. /v1/models).
        # Auth header injected here too for consistency.
        location / {
            set $openai_url  $OPENAI_API_URL;
            set $openai_key  $OPENAI_API_KEY;
            proxy_pass              $openai_url;
            proxy_set_header        Authorization "Bearer $openai_key";
            proxy_set_header        Host $proxy_host;
        }

        # Guardrail-aware completions — NJS takes full control of this location.
        location = /v1/chat/completions {
            js_content main.handleChatCompletions;
        }

        # Upstream LLM backend.
        # In docker-compose: proxy_pass is set here explicitly.
        # In NGINX Ingress Controller: this location is owned by the Ingress
        # resource — do NOT emit it in snippets. The subrequest will find it
        # as defined by the Ingress. Remove proxy_pass and headers below when
        # migrating; keep only the location shell if needed.
        location /app/ {
            set $openai_url $OPENAI_API_URL;
            set $openai_key $OPENAI_API_KEY;
            proxy_pass              $openai_url;
            proxy_set_header        Authorization "Bearer $openai_key";
            proxy_set_header        Host $proxy_host;
            proxy_set_header        Content-Type "application/json";
            proxy_pass_request_body on;
        }

        # Internal proxy to the F5 AI Guardrails scan endpoint.
        # Kept internal — only reachable via NJS subrequest, never by clients.
        location /_guardrails_scan {
            internal;
            set $guardrails_url $F5_AI_GUARDRAILS_API_URL;
            set $guardrails_token $F5_AI_GUARDRAILS_API_TOKEN;
            proxy_pass              $guardrails_url/scan;
            proxy_set_header        Authorization "Bearer $guardrails_token";
            proxy_set_header        Content-Type "application/json";
            proxy_pass_request_headers off;
            proxy_pass_request_body on;
        }
    }
}
```

### Notes on variable usage in `proxy_pass`

nginx does not allow bare `$ENV_VAR` directly in `proxy_pass`. The pattern is:

```nginx
set $openai_url $OPENAI_API_URL;
proxy_pass $openai_url;
```

This also requires the `resolver` directive to be present, which is why `resolver 127.0.0.11` is set at the `http {}` block level.

---

## File 2: `nginx/guardrails.js`

Complete NJS module.

```javascript
// guardrails.js
// NJS module for F5 AI Guardrails sideband scanning.
// Handles POST /v1/chat/completions only. Non-streaming requests only.

// ---------------------------------------------------------------------------
// Helper: call F5 AI Guardrails scan endpoint via internal nginx subrequest.
// messages: array of {role, content} objects (prompt or response).
// Returns: { blocked: bool, reason: string }
// ---------------------------------------------------------------------------
async function scanWithGuardrails(r, messages) {
    const project = process.env.F5_AI_GUARDRAILS_PROJECT_ID;

    const payload = JSON.stringify({
        messages:   messages,
        project_id: project
    });

    let scanReply;
    try {
        scanReply = await r.subrequest('/_guardrails_scan', {
            method: 'POST',
            body:   payload
        });
    } catch (e) {
        // Subrequest itself failed (network error, timeout, etc.).
        // Fail closed: treat as blocked.
        r.error(`[guardrails] subrequest error: ${e}`);
        return { blocked: true, reason: 'guardrails_unreachable' };
    }

    if (scanReply.status !== 200) {
        r.error(`[guardrails] scan returned HTTP ${scanReply.status}: ${scanReply.responseText}`);
        // Fail closed.
        return { blocked: true, reason: 'guardrails_api_error' };
    }

    let result;
    try {
        result = JSON.parse(scanReply.responseText);
    } catch (e) {
        r.error(`[guardrails] failed to parse scan response: ${scanReply.responseText}`);
        return { blocked: true, reason: 'guardrails_parse_error' };
    }

    // NOTE TO IMPLEMENTING AGENT:
    // Confirm the actual field names by reading src/guardrails.py in
    // leonseng/f5-ai-guardrails-openai-api-integration before finalising
    // this parser. The assumed shape is { verdict: "pass"|"block", reason: "..." }.
    const blocked = result.verdict === 'block';
    const reason  = result.reason || '';

    if (process.env.DEBUG === 'true') {
        r.warn(`[guardrails] verdict=${result.verdict} reason=${reason}`);
    }

    return { blocked, reason };
}

// ---------------------------------------------------------------------------
// Helper: build a standardised HTTP 400 JSON error body, matching the
// shape the Python FastAPI version returns.
// ---------------------------------------------------------------------------
function blockResponse(r, code, message) {
    r.headersOut['Content-Type'] = 'application/json';
    r.return(400, JSON.stringify({
        error: {
            message: message || 'Blocked by AI Guardrails',
            type:    'guardrails_block',
            code:    code
        }
    }));
}

// ---------------------------------------------------------------------------
// Main handler: called by js_content for POST /v1/chat/completions.
// Flow:
//   1. Parse and validate request body.
//   2. Reject streaming requests (not supported).
//   3. Scan prompt if F5_AI_GUARDRAILS_SCAN_PROMPT=true.
//   4. Forward to upstream via subrequest to /app/.
//   5. Scan response if F5_AI_GUARDRAILS_SCAN_RESPONSE=true.
//   6. Return upstream response to client.
// ---------------------------------------------------------------------------
async function handleChatCompletions(r) {
    const scanPrompt   = process.env.F5_AI_GUARDRAILS_SCAN_PROMPT   === 'true';
    const scanResponse = process.env.F5_AI_GUARDRAILS_SCAN_RESPONSE === 'true';

    // 1. Parse request body.
    let reqBody;
    try {
        reqBody = JSON.parse(r.requestText);
    } catch (e) {
        r.headersOut['Content-Type'] = 'application/json';
        r.return(400, JSON.stringify({
            error: {
                message: 'Invalid JSON request body',
                type:    'invalid_request_error',
                code:    'bad_request'
            }
        }));
        return;
    }

    // 2. Reject streaming — not supported in this implementation.
    if (reqBody.stream === true) {
        r.headersOut['Content-Type'] = 'application/json';
        r.return(400, JSON.stringify({
            error: {
                message: 'Streaming is not supported by this proxy',
                type:    'invalid_request_error',
                code:    'streaming_not_supported'
            }
        }));
        return;
    }

    // 3. Scan prompt.
    if (scanPrompt) {
        if (!Array.isArray(reqBody.messages) || reqBody.messages.length === 0) {
            r.headersOut['Content-Type'] = 'application/json';
            r.return(400, JSON.stringify({
                error: {
                    message: 'Request body must contain a non-empty messages array',
                    type:    'invalid_request_error',
                    code:    'bad_request'
                }
            }));
            return;
        }

        const scan = await scanWithGuardrails(r, reqBody.messages);
        if (scan.blocked) {
            blockResponse(r, 'prompt_blocked', scan.reason || 'Prompt blocked by AI Guardrails');
            return;
        }
    }

    // 4. Forward to upstream LLM via /app/.
    let upstreamReply;
    try {
        upstreamReply = await r.subrequest('/app/', {
            method: r.method,
            body:   r.requestText
        });
    } catch (e) {
        r.error(`[guardrails] upstream subrequest error: ${e}`);
        r.headersOut['Content-Type'] = 'application/json';
        r.return(502, JSON.stringify({
            error: {
                message: 'Upstream LLM request failed',
                type:    'upstream_error',
                code:    'bad_gateway'
            }
        }));
        return;
    }

    if (upstreamReply.status !== 200) {
        // Pass upstream errors straight through to the client.
        r.headersOut['Content-Type'] =
            upstreamReply.headersOut['Content-Type'] || 'application/json';
        r.return(upstreamReply.status, upstreamReply.responseText);
        return;
    }

    // 5. Scan response.
    if (scanResponse) {
        let respBody;
        try {
            respBody = JSON.parse(upstreamReply.responseText);
        } catch (e) {
            // Upstream returned non-JSON — pass through without scanning.
            r.warn('[guardrails] upstream response is not JSON, skipping response scan');
            r.headersOut['Content-Type'] =
                upstreamReply.headersOut['Content-Type'] || 'application/json';
            r.return(200, upstreamReply.responseText);
            return;
        }

        // Extract the assistant message content for scanning.
        // Handles both single-choice and multi-choice responses; scans all choices.
        const choices = respBody.choices || [];
        if (choices.length === 0) {
            // No choices to scan — pass through.
            r.headersOut['Content-Type'] =
                upstreamReply.headersOut['Content-Type'] || 'application/json';
            r.return(200, upstreamReply.responseText);
            return;
        }

        const assistantMessages = choices
            .filter(c => c.message && c.message.content)
            .map(c => ({ role: 'assistant', content: c.message.content }));

        if (assistantMessages.length > 0) {
            const scan = await scanWithGuardrails(r, assistantMessages);
            if (scan.blocked) {
                blockResponse(r, 'response_blocked', scan.reason || 'Response blocked by AI Guardrails');
                return;
            }
        }
    }

    // 6. Pass clean upstream response to client.
    r.headersOut['Content-Type'] =
        upstreamReply.headersOut['Content-Type'] || 'application/json';
    r.return(200, upstreamReply.responseText);
}

export default { handleChatCompletions };
```

---

## File 3: `Dockerfile`

```dockerfile
FROM nginx:1.27-alpine

# Install NJS module (not always present on Alpine-based images).
RUN apk add --no-cache nginx-module-njs

# Copy nginx config and NJS script.
COPY nginx/nginx.conf      /etc/nginx/nginx.conf
COPY nginx/guardrails.js   /etc/nginx/njs/guardrails.js
```

### Note on `load_module`

The official `nginx:1.27-alpine` image ships NJS as a dynamic module. The `load_module modules/ngx_http_js_module.so;` directive at the top of `nginx.conf` is required. If the implementing agent uses a different base image (e.g. `nginx:1.27` on Debian), the package name changes to `libnginx-mod-http-js` and the module path may differ — verify with `nginx -V` inside the container.

---

## File 4: `docker-compose.yml`

Minimal changes from the base repo — add the new env vars:

```yaml
services:
  proxy:
    build: .
    ports:
      - "11434:11434"
    environment:
      - OPENAI_API_URL=${OPENAI_API_URL}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - F5_AI_GUARDRAILS_API_URL=${F5_AI_GUARDRAILS_API_URL}
      - F5_AI_GUARDRAILS_API_TOKEN=${F5_AI_GUARDRAILS_API_TOKEN}
      - F5_AI_GUARDRAILS_PROJECT_ID=${F5_AI_GUARDRAILS_PROJECT_ID}
      - F5_AI_GUARDRAILS_SCAN_PROMPT=${F5_AI_GUARDRAILS_SCAN_PROMPT:-true}
      - F5_AI_GUARDRAILS_SCAN_RESPONSE=${F5_AI_GUARDRAILS_SCAN_RESPONSE:-true}
      - DEBUG=${DEBUG:-false}
```

---

## Sequence Diagram

```
Client            nginx/NJS           /_guardrails_scan       /app/ (upstream LLM)
  |                   |                      |                        |
  | POST /v1/chat/    |                      |                        |
  | completions       |                      |                        |
  |------------------>|                      |                        |
  |                   |                      |                        |
  |              parse body                  |                        |
  |              reject if stream:true       |                        |
  |                   |                      |                        |
  |                   | POST /_guardrails_scan                        |
  |                   | body: {messages, project_id}                  |
  |                   |--------------------->|                        |
  |                   |<-- 200 {verdict, reason}                      |
  |                   |                      |                        |
  |              if blocked:                 |                        |
  |<-- 400 prompt_blocked                    |                        |
  |                   |                      |                        |
  |                   | POST /app/           |                        |
  |                   | body: original request body                   |
  |                   |---------------------------------------------->|
  |                   |<-- 200 {choices:[...]} -----------------------|
  |                   |                      |                        |
  |                   | POST /_guardrails_scan                        |
  |                   | body: {messages:[{role:assistant,...}]}       |
  |                   |--------------------->|                        |
  |                   |<-- 200 {verdict, reason}                      |
  |                   |                      |                        |
  |              if blocked:                 |                        |
  |<-- 400 response_blocked                  |                        |
  |                   |                      |                        |
  |<-- 200 {choices:[...]}                   |                        |
```

---

## Flags for the Implementing Agent

These are the open items that require verification before the code is finalised:

**1. F5 scan response schema** — The JS parser assumes `{ verdict: "pass"|"block", reason: "..." }`. Before writing the final parser, read `src/guardrails.py` from `leonseng/f5-ai-guardrails-openai-api-integration` and confirm the actual field names returned by the CalypsoAI API. Adjust `result.verdict` and `result.reason` references in `scanWithGuardrails()` accordingly.

**2. NJS version and `fetch()` availability** — Run `nginx -V` or check the package version inside the chosen base image. If njs ≥ 0.8.0 is available, the `/_guardrails_scan` internal location can be replaced with a direct `fetch()` call in JS, which is simpler:
```javascript
const scanReply = await fetch(`${process.env.F5_AI_GUARDRAILS_API_URL}/scan`, {
    method:  'POST',
    headers: {
        'Authorization': `Bearer ${process.env.F5_AI_GUARDRAILS_API_TOKEN}`,
        'Content-Type':  'application/json'
    },
    body: payload
});
```
If using `fetch()`, remove `location /_guardrails_scan` from `nginx.conf` entirely.

**3. Subrequest body forwarding to `/app/`** — Confirm that `r.subrequest('/app/', { method, body })` correctly passes the body through the `proxy_pass` in that location. If the upstream receives an empty body, the fallback is to write the original request body to a nginx variable and read it inside the `/app/` location using `$request_body` via `proxy_set_body`.

**4. `/app/` path rewriting** — The subrequest to `/app/` will hit the upstream at path `/app/` unless rewritten. The upstream LLM expects `/v1/chat/completions`. Add a `rewrite` or `proxy_pass` with path suffix inside `location /app/` to strip the `/app/` prefix and append the correct path. In the standalone docker-compose version:
```nginx
location /app/ {
    rewrite ^/app/(.*)$ /v1/chat/completions break;
    proxy_pass $openai_url;
    ...
}
```
In the Ingress Controller version, the Ingress resource controls this rewrite — confirm the path the backend service expects and align the subrequest URI accordingly (e.g. call `/app/v1/chat/completions` if that's what the Ingress routes).

**5. Security: `/app/` is externally reachable** — Without `internal;`, clients can POST directly to `/app/` and bypass guardrails. In docker-compose this is acceptable if the port is not publicly exposed. For the Ingress migration, ensure the `/app/` Ingress rule is on an internal-only listener or has its own auth annotation. Flag this to the operator.

**6. `load_module` path** — On `nginx:1.27-alpine` the correct path is typically `/usr/lib/nginx/modules/ngx_http_js_module.so`. Verify inside the container with `find / -name ngx_http_js_module.so` if nginx fails to start with a module-not-found error.