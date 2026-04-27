// guardrails.js — NJS module for F5 AI Guardrails sideband scanning.
// Handles POST /v1/chat/completions only. Non-streaming requests only.
// Guardrails calls are made via NJS ngx.fetch() (available in the http module context).

// ---------------------------------------------------------------------------
// Scan the last user message in `messages` against F5 AI Guardrails.
// Returns { blocked: bool, reason: string }
// Outcome mapping: "flagged" → blocked; "cleared" | "redacted" → pass.
// ---------------------------------------------------------------------------
async function scanWithGuardrails(r, messages) {
    let lastUser;
    for (let i = messages.length - 1; i >= 0; i--) {
        if (messages[i].role === 'user') {
            lastUser = messages[i];
            break;
        }
    }
    const inputText = lastUser
        ? lastUser.content
        : messages.map(m => m.content).join('\n');

    const debug = process.env.DEBUG === 'true';
    const failOpen = process.env.F5_AI_GUARDRAILS_FAIL_OPEN === 'true';
    const scanUrl = `${process.env.F5_AI_GUARDRAILS_API_URL.replace(/\/$/, '')}/scans`;

    const payload = JSON.stringify({
        input: inputText,
        project: process.env.F5_AI_GUARDRAILS_PROJECT_ID,
        verbose: false
    });

    if (debug) {
        r.warn(`[guardrails] scan → POST ${scanUrl} (input: ${inputText.length} chars)`);
    }

    const t0 = Date.now();
    let scanResp;
    try {
        scanResp = await ngx.fetch(scanUrl, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${process.env.F5_AI_GUARDRAILS_API_TOKEN}`,
                'Content-Type': 'application/json'
            },
            body: payload
        }
        );
    } catch (e) {
        r.error(`[guardrails] fetch error: ${e}`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_unreachable — passing through`);
            return { blocked: false, reason: 'fail_open' };
        }
        return { blocked: true, reason: 'guardrails_unreachable' };
    }

    if (debug) {
        r.warn(`[guardrails] scan ← HTTP ${scanResp.status} (${Date.now() - t0}ms)`);
    }

    if (!scanResp.ok) {
        const text = await scanResp.text().catch(() => '');
        r.error(`[guardrails] scan returned HTTP ${scanResp.status}: ${text}`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_api_error — passing through`);
            return { blocked: false, reason: 'fail_open' };
        }
        return { blocked: true, reason: 'guardrails_api_error' };
    }

    let result;
    try {
        result = await scanResp.json();
    } catch (e) {
        r.error(`[guardrails] failed to parse scan response`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_parse_error — passing through`);
            return { blocked: false, reason: 'fail_open' };
        }
        return { blocked: true, reason: 'guardrails_parse_error' };
    }

    const outcome = result && result.result && result.result.outcome;  // "cleared" | "flagged" | "redacted"
    const blocked = outcome === 'flagged';

    if (debug) {
        const policy = (result && result.result && result.result.policy) || '';
        const reason = (result && result.result && result.result.reason) || '';
        r.warn(`[guardrails] outcome=${outcome}  policy=${policy}  reason=${reason}`);
    }

    return { blocked, reason: outcome || '' };
}

// ---------------------------------------------------------------------------
// Build a standardised HTTP 400 JSON error body.
// ---------------------------------------------------------------------------
function blockResponse(r, code, message) {
    r.headersOut['Content-Type'] = 'application/json';
    r.return(400, JSON.stringify({
        error: {
            message: message || 'Blocked by AI Guardrails',
            type: 'guardrails_block',
            code: code
        }
    }));
}

// ---------------------------------------------------------------------------
// Main handler: js_content for POST /v1/chat/completions.
// Flow:
//   1. Parse and validate request body.
//   2. Reject streaming (not supported).
//   3. Scan prompt if F5_AI_GUARDRAILS_SCAN_PROMPT=true.
//   4. Forward to upstream LLM via subrequest to /app/.
//   5. Scan response if F5_AI_GUARDRAILS_SCAN_RESPONSE=true.
//   6. Return upstream response to client.
// ---------------------------------------------------------------------------
async function handleChatCompletions(r) {
    const scanPrompt = process.env.F5_AI_GUARDRAILS_SCAN_PROMPT === 'true';
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
                type: 'invalid_request_error',
                code: 'bad_request'
            }
        }));
        return;
    }

    // 2. Reject streaming — not supported.
    if (reqBody.stream === true) {
        r.headersOut['Content-Type'] = 'application/json';
        r.return(400, JSON.stringify({
            error: {
                message: 'Streaming is not supported by this proxy',
                type: 'invalid_request_error',
                code: 'streaming_not_supported'
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
                    type: 'invalid_request_error',
                    code: 'bad_request'
                }
            }));
            return;
        }

        const scan = await scanWithGuardrails(r, reqBody.messages);
        if (scan.blocked) {
            const apiErrors = ['guardrails_unreachable', 'guardrails_api_error', 'guardrails_parse_error'];
            if (apiErrors.includes(scan.reason)) {
                blockResponse(r, scan.reason, `Guardrails scan error: ${scan.reason}`);
            } else {
                blockResponse(r, 'prompt_blocked', `Prompt blocked by AI Guardrails: ${scan.reason}`);
            }
            return;
        }
    }

    // 4. Forward to upstream LLM.
    let upstreamReply;
    try {
        upstreamReply = await r.subrequest('/app/', {
            method: r.method,
            body: r.requestText
        });
    } catch (e) {
        r.error(`[guardrails] upstream subrequest error: ${e}`);
        r.headersOut['Content-Type'] = 'application/json';
        r.return(502, JSON.stringify({
            error: {
                message: 'Upstream LLM request failed',
                type: 'upstream_error',
                code: 'bad_gateway'
            }
        }));
        return;
    }

    if (upstreamReply.status !== 200) {
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
            r.warn('[guardrails] upstream response is not JSON, skipping response scan');
            r.headersOut['Content-Type'] =
                upstreamReply.headersOut['Content-Type'] || 'application/json';
            r.return(200, upstreamReply.responseText);
            return;
        }

        const choices = respBody.choices || [];
        const assistantMessages = choices
            .filter(c => c.message && c.message.content)
            .map(c => ({ role: 'assistant', content: c.message.content }));

        if (assistantMessages.length > 0) {
            const scan = await scanWithGuardrails(r, assistantMessages);
            if (scan.blocked) {
                const apiErrors = ['guardrails_unreachable', 'guardrails_api_error', 'guardrails_parse_error'];
                if (apiErrors.includes(scan.reason)) {
                    blockResponse(r, scan.reason, `Guardrails scan error: ${scan.reason}`);
                } else {
                    blockResponse(r, 'response_blocked', `Response blocked by AI Guardrails: ${scan.reason}`);
                }
                return;
            }
        }
    }

    // 6. Pass clean upstream response to client.
    r.headersOut['Content-Type'] =
        upstreamReply.headersOut['Content-Type'] || 'application/json';
    r.return(200, upstreamReply.responseText);
}

export default { handleChatCompletions, getOpenaiUrl, getOpenaiKey };

// ---------------------------------------------------------------------------
// js_set callbacks: expose env vars as nginx variables.
// ---------------------------------------------------------------------------
function getOpenaiUrl(r) { return process.env.OPENAI_API_URL || ''; }
function getOpenaiKey(r) { return process.env.OPENAI_API_KEY || ''; }
