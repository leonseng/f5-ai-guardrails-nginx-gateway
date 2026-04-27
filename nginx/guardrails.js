// guardrails.js — NJS module for F5 AI Guardrails sideband scanning.
// Handles POST /v1/chat/completions. Supports both streaming and non-streaming.
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
            return { pass: true, redactedContent: null };
        }
        helpers.blockResponse(r, 'guardrails_unreachable', 'Guardrails scan error: guardrails_unreachable');
        return { pass: false, redactedContent: null };
    }

    if (debug) {
        r.warn(`[guardrails] scan ← HTTP ${scanResp.status} (${Date.now() - t0}ms)`);
    }

    if (!scanResp.ok) {
        const body = await scanResp.text().catch(() => '');
        r.error(`[guardrails] scan returned HTTP ${scanResp.status}: ${body}`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_api_error — passing through`);
            return { pass: true, redactedContent: null };
        }
        helpers.blockResponse(r, 'guardrails_api_error', 'Guardrails scan error: guardrails_api_error');
        return { pass: false, redactedContent: null };
    }

    let result;
    try {
        result = await scanResp.json();
    } catch (e) {
        r.error(`[guardrails] failed to parse scan response`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_parse_error — passing through`);
            return { pass: true, redactedContent: null };
        }
        helpers.blockResponse(r, 'guardrails_parse_error', 'Guardrails scan error: guardrails_parse_error');
        return { pass: false, redactedContent: null };
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
        return { pass: false, redactedContent: null };
    }

    if (outcome === 'redacted') {
        const redactedContent = (result.redactedInput) || null;
        return { pass: true, redactedContent };
    }

    return { pass: true, redactedContent: null };
}

// ---------------------------------------------------------------------------
// Main handler: js_content for POST /v1/chat/completions.
// Flow:
//   1. Parse and validate request body.
//   2. Scan prompt if F5_AI_GUARDRAILS_SCAN_PROMPT=true.
//   3. Delegate to handleStreamingRequest or handleNonStreamingRequest.
// ---------------------------------------------------------------------------
async function handleChatCompletions(r) {
    const scanPrompt = process.env.F5_AI_GUARDRAILS_SCAN_PROMPT === 'true';
    const scanResponse = process.env.F5_AI_GUARDRAILS_SCAN_RESPONSE === 'true';
    const failOpen = process.env.F5_AI_GUARDRAILS_FAIL_OPEN === 'true';
    const redactPrompt   = process.env.F5_AI_GUARDRAILS_REDACT_PROMPT   === 'true';
    const redactResponse = process.env.F5_AI_GUARDRAILS_REDACT_RESPONSE === 'true';

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

            if (redactPrompt && result.redactedContent !== null) {
                // Write the redacted text back into the last user/tool message,
                // mirroring the same message extractRequestScanText() read from.
                for (let i = reqBody.messages.length - 1; i >= 0; i--) {
                    if (reqBody.messages[i].role === 'user' || reqBody.messages[i].role === 'tool') {
                        reqBody.messages[i].content = result.redactedContent;
                        break;
                    }
                }
                if (process.env.DEBUG === 'true') {
                    r.warn('[guardrails] prompt redacted — forwarding modified body to upstream');
                }
            }
        }
    }

    if (reqBody.stream === true) {
        await handleStreamingRequest(r, reqBody, scanResponse, failOpen, redactResponse);
    } else {
        await handleNonStreamingRequest(r, reqBody, scanResponse, failOpen, redactResponse);
    }
}

export default { handleChatCompletions, getOpenaiUrl, getOpenaiKey };

// ---------------------------------------------------------------------------
// Non-streaming path: subrequest to upstream, scan JSON response, return to client.
// ---------------------------------------------------------------------------
async function handleNonStreamingRequest(r, reqBody, scanResponse, failOpen, redactResponse) {
    let upstreamReply;
    try {
        upstreamReply = await r.subrequest('/app/', {
            method: r.method,
            body:   JSON.stringify(reqBody)
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

            if (redactResponse && result.redactedContent !== null) {
                // Write the redacted text back into the last assistant choice,
                // mirroring the same choice extractResponseScanText() read from.
                const choices = respBody.choices || [];
                const last = choices[choices.length - 1];
                if (last && last.message) {
                    last.message.content = result.redactedContent;
                }
                if (process.env.DEBUG === 'true') {
                    r.warn('[guardrails] response redacted — returning modified body to client');
                }

                r.headersOut['Content-Type'] = 'application/json';
                r.return(200, JSON.stringify(respBody));
                return;
            }
        }
    }

    r.headersOut['Content-Type'] =
        upstreamReply.headersOut['Content-Type'] || 'application/json';
    r.return(200, upstreamReply.responseText);
}

// ---------------------------------------------------------------------------
// Streaming path: subrequest to upstream, scan buffered SSE, replay to client.
// ---------------------------------------------------------------------------
async function handleStreamingRequest(r, reqBody, scanResponse, failOpen, redactResponse) {
    let upstreamReply;
    try {
        upstreamReply = await r.subrequest('/app/', {
            method: r.method,
            body: JSON.stringify(reqBody)
        });
    } catch (e) {
        r.error(`[guardrails] upstream subrequest error: ${e}`);
        helpers.sendStreamError(r, 'bad_gateway', 'Upstream LLM request failed');
        return;
    }

    if (upstreamReply.status !== 200) {
        helpers.sendStreamError(r, 'bad_gateway', `Upstream LLM error: ${upstreamReply.status}`);
        return;
    }

    const rawSSE = upstreamReply.responseText;

    if (scanResponse) {
        let scanText;
        try {
            scanText = helpers.extractSSEContent(rawSSE);
        } catch (e) {
            if (e.code === 'tool_call_not_supported') {
                r.warn(`[guardrails] ${e.message} — skipping response scan`);
                helpers.replaySSE(r, rawSSE);
                return;
            }
            // no_scannable_content
            if (failOpen) {
                r.warn(`[guardrails] ${e.message} (fail-open) — passing through`);
                helpers.replaySSE(r, rawSSE);
            } else {
                helpers.blockResponse(r, e.code, e.message);
            }
            return;
        }

        const result = await scanWithGuardrails(r, scanText, failOpen, 'response_blocked');
        if (!result.pass) {
            helpers.sendStreamContentFilter(r);
            return;
        }

        if (redactResponse && result.redactedContent !== null) {
            if (process.env.DEBUG === 'true') {
                r.warn('[guardrails] response redacted — returning modified SSE to client');
            }
            const rebuiltSSE = helpers.rebuildSSEWithRedactedContent(rawSSE, result.redactedContent);
            helpers.replaySSE(r, rebuiltSSE);
            return;
        }
    }

    helpers.replaySSE(r, rawSSE);
}

// ---------------------------------------------------------------------------
// js_set callbacks: expose env vars as nginx variables.
// ---------------------------------------------------------------------------
function getOpenaiUrl(r) { return process.env.OPENAI_API_URL || ''; }
function getOpenaiKey(r) { return process.env.OPENAI_API_KEY || ''; }
