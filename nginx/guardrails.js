// guardrails.js — NJS module for F5 AI Guardrails sideband scanning.
// Handles POST /v1/chat/completions only. Non-streaming requests only.
// Guardrails calls are made via NJS ngx.fetch() (available in the http module context).

import helpers from './helpers.js';

// ---------------------------------------------------------------------------
// POST text to F5 AI Guardrails for scanning, applying failOpen to errors.
// Returns { pass: true } to continue, or { pass: false } when a response has
// already been sent (blocked or unrecoverable infrastructure error).
// blockedCode: 'prompt_blocked' | 'response_blocked'
// Outcome mapping: "flagged" → blocked; "cleared" | "redacted" → pass.
// ---------------------------------------------------------------------------
async function scanWithGuardrails(r, text, failOpen, blockedCode) {
    const debug = process.env.DEBUG === 'true';
    const scanUrl = `${process.env.F5_AI_GUARDRAILS_API_URL.replace(/\/$/, '')}/scans`;

    const payload = JSON.stringify({
        input: text,
        project: process.env.F5_AI_GUARDRAILS_PROJECT_ID,
        verbose: false
    });

    if (debug) {
        r.warn(`[guardrails] scan → POST ${scanUrl} (input: ${text.length} chars)`);
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
        });
    } catch (e) {
        r.error(`[guardrails] fetch error: ${e}`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_unreachable — passing through`);
            return { pass: true };
        }
        helpers.blockResponse(r, 'guardrails_unreachable', 'Guardrails scan error: guardrails_unreachable');
        return { pass: false };
    }

    if (debug) {
        r.warn(`[guardrails] scan ← HTTP ${scanResp.status} (${Date.now() - t0}ms)`);
    }

    if (!scanResp.ok) {
        const body = await scanResp.text().catch(() => '');
        r.error(`[guardrails] scan returned HTTP ${scanResp.status}: ${body}`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_api_error — passing through`);
            return { pass: true };
        }
        helpers.blockResponse(r, 'guardrails_api_error', 'Guardrails scan error: guardrails_api_error');
        return { pass: false };
    }

    let result;
    try {
        result = await scanResp.json();
    } catch (e) {
        r.error(`[guardrails] failed to parse scan response`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_parse_error — passing through`);
            return { pass: true };
        }
        helpers.blockResponse(r, 'guardrails_parse_error', 'Guardrails scan error: guardrails_parse_error');
        return { pass: false };
    }

    const outcome = result && result.result && result.result.outcome;  // "cleared" | "flagged" | "redacted"

    if (debug) {
        const policy = (result && result.result && result.result.policy) || '';
        const reason = (result && result.result && result.result.reason) || '';
        r.warn(`[guardrails] outcome=${outcome}  policy=${policy}  reason=${reason}`);
    }

    if (outcome === 'flagged') {
        const label = blockedCode === 'prompt_blocked' ? 'Prompt' : 'Response';
        helpers.blockResponse(r, blockedCode, `${label} blocked by AI Guardrails: ${outcome}`);
        return { pass: false };
    }

    return { pass: true };
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
    const failOpen = process.env.F5_AI_GUARDRAILS_FAIL_OPEN === 'true';

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

        let promptText;
        try {
            promptText = helpers.extractRequestScanText(reqBody.messages);
        } catch (e) {
            if (failOpen) {
                r.warn(`[guardrails] ${e.message} (fail-open) — passing through`);
            } else {
                helpers.blockResponse(r, e.code, e.message);
                return;
            }
        }

        if (promptText) {
            const result = await scanWithGuardrails(r, promptText, failOpen, 'prompt_blocked');
            if (!result.pass) return;
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

        let responseText;
        try {
            responseText = helpers.extractResponseScanText(respBody.choices || []);
        } catch (e) {
            if (failOpen) {
                r.warn(`[guardrails] ${e.message} (fail-open) — passing through`);
            } else {
                helpers.blockResponse(r, e.code, e.message);
                return;
            }
        }

        if (responseText) {
            const result = await scanWithGuardrails(r, responseText, failOpen, 'response_blocked');
            if (!result.pass) return;
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
