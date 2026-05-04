// guardrails.js — NJS module for F5 AI Guardrails sideband scanning.
// Handles POST /v1/chat/completions. Supports both streaming and non-streaming.
// Guardrails calls are made via NJS ngx.fetch() (available in the http module context).

import helpers from './helpers.js';


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
    const redactPrompt = process.env.F5_AI_GUARDRAILS_REDACT_PROMPT === 'true';
    const redactResponse = process.env.F5_AI_GUARDRAILS_REDACT_RESPONSE === 'true';

    let reqBody;
    try {
        reqBody = JSON.parse(r.requestText);
    } catch (e) {
        helpers.errorResponse(r, 400, 'invalid_request_error', 'Invalid JSON in request body');
        return;
    }

    if (scanPrompt) {
        if (!Array.isArray(reqBody.messages) || reqBody.messages.length === 0) {
            helpers.errorResponse(r, 400, 'invalid_request_error', 'Request body must contain a non-empty messages array');
            return;
        }

        let promptText;
        try {
            promptText = helpers.extractRequestScanText(reqBody.messages);
        } catch (e) {
            helpers.errorResponse(r, 400, e.code, e.message);
            return;
        }

        if (promptText) {
            const result = await helpers.scanWithGuardrails(r, promptText, failOpen, 'prompt_blocked');
            if (!result.pass) {
                if (result.error.code === 'prompt_blocked' || result.error.code === 'response_blocked') {
                    helpers.contentFilterResponse(r, reqBody.model, reqBody.stream === true);
                    return;
                } else {
                    helpers.errorResponse(r, 400, result.error.code, result.error.message);
                    return;
                }
            }

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

// ---------------------------------------------------------------------------
// Non-streaming path: subrequest to upstream, scan JSON response, return to client.
// ---------------------------------------------------------------------------
async function handleNonStreamingRequest(r, reqBody, scanResponse, failOpen, redactResponse) {
    let upstreamReply;
    try {
        upstreamReply = await r.subrequest(helpers.getUpstreamUri(), {
            method: r.method,
            body: JSON.stringify(reqBody)
        });
    } catch (e) {
        r.error(`[guardrails] upstream subrequest error: ${e}`);
        helpers.errorResponse(r, 502, 'upstream_error', 'Upstream LLM request failed');
        return;
    }

    // passthrough non-200 responses from upstream
    if (upstreamReply.status !== 200) {
        helpers.passUpstreamResponse(r, upstreamReply);
        return;
    }

    if (scanResponse) {
        let respBody;
        try {
            respBody = JSON.parse(upstreamReply.responseText);
        } catch (e) {
            r.warn('[guardrails] upstream response is not JSON, skipping response scan');
            helpers.errorResponse(r, 400, 'invalid_response_error', 'Response scanning requires a JSON response body.');
            return;
        }

        let responseText;
        try {
            responseText = helpers.extractResponseScanText(respBody.choices || []);
        } catch (e) {
            if (failOpen) {
                r.warn(`[guardrails] ${e.message} (fail-open) — passing through`);
            } else {
                helpers.errorResponse(r, 400, e.code, e.message);
                return;
            }
        }

        if (responseText) {
            const result = await helpers.scanWithGuardrails(r, responseText, failOpen, 'response_blocked');
            if (!result.pass) {
                helpers.contentFilterResponse(r, reqBody.model);
                return;
            }

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

                helpers.passUpstreamResponse(r, upstreamReply, JSON.stringify(respBody));
                return;
            }
        }
    }

    helpers.passUpstreamResponse(r, upstreamReply);
}

// ---------------------------------------------------------------------------
// Streaming path: subrequest to upstream, scan buffered SSE, replay to client.
// ---------------------------------------------------------------------------
async function handleStreamingRequest(r, reqBody, scanResponse, failOpen, redactResponse) {
    let upstreamReply;
    try {
        upstreamReply = await r.subrequest(helpers.getUpstreamUri(), {
            method: r.method,
            body: JSON.stringify(reqBody)
        });
    } catch (e) {
        r.error(`[guardrails] upstream subrequest error: ${e}`);
        helpers.sendStreamEevent(r, { error: { code: 'upstream_error', message: 'Upstream LLM request failed' } });
        return;
    }

    if (upstreamReply.status !== 200) {
        helpers.sendStreamEevent(r, { error: { code: 'bad_gateway', message: `Upstream LLM error: ${upstreamReply.status}` } });
        return;
    }

    const rawSSE = upstreamReply.responseText;

    if (scanResponse) {
        let scanText;
        try {
            scanText = helpers.extractSSEContent(rawSSE);
        } catch (e) {
            helpers.errorResponse(r, 400, e.code, e.message);
            return;
        }

        if (scanText) {
            const result = await helpers.scanWithGuardrails(r, scanText, failOpen, 'response_blocked');
            if (!result.pass) {
                if (result.error.code === 'response_blocked') {
                    helpers.contentFilterResponse(r, reqBody.model, true);
                } else {
                    helpers.sendStreamEvent(r, { error: result.error });
                }
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
    }

    helpers.replaySSE(r, rawSSE);
}

export default { handleChatCompletions };