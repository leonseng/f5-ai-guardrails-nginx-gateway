// helpers.js — Pure helper utilities shared by the guardrails NJS module.


// ---------------------------------------------------------------------------
// POST text to F5 AI Guardrails for scanning, applying failOpen to errors.
// Returns scan results only; does not modify response objects.
// blockedCode: 'prompt_blocked' | 'response_blocked'
// Return value:
//   { pass: true, redactedContent: null }           — content cleared
//   { pass: true, redactedContent: "..." }         — content redacted
//   { pass: false, error: { code, message } }      — blocked or error
// On failOpen, recoverable errors return { pass: true, redactedContent: null }.
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
        return {
            pass: false,
            error: {
                code: 'guardrails_unreachable',
                message: 'Guardrails scan error: guardrails_unreachable'
            }
        };
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
        return {
            pass: false,
            error: {
                code: 'guardrails_api_error',
                message: 'Guardrails scan error: guardrails_api_error'
            }
        };
    }

    let resp;
    try {
        resp = await scanResp.json();
    } catch (e) {
        r.error(`[guardrails] failed to parse scan response`);
        if (failOpen) {
            r.warn(`[guardrails] scan error (fail-open): guardrails_parse_error — passing through`);
            return { pass: true, redactedContent: null };
        }
        return {
            pass: false,
            error: {
                code: 'guardrails_parse_error',
                message: 'Guardrails scan error: guardrails_parse_error'
            }
        };
    }

    const outcome = resp && resp.result && resp.result.outcome;  // "cleared" | "flagged" | "redacted"

    if (debug) {
        r.warn(`[guardrails] outcome=${outcome}`);
    }

    if (outcome === 'flagged') {
        const label = blockedCode === 'prompt_blocked' ? 'Prompt' : 'Response';
        return {
            pass: false,
            error: {
                code: blockedCode,
                message: `${label} blocked by AI Guardrails: ${outcome}`
            }
        };
    }

    return {
        pass: true,
        redactedContent: outcome === 'redacted' ? resp.redactedInput : null
    };
}

// ---------------------------------------------------------------------------
// Extract the content string to scan from the last request message.
// Accepts role 'user' or 'tool' with a non-empty content field.
// Throws an Error with .code = 'no_scannable_content' otherwise.
// ---------------------------------------------------------------------------
function extractRequestScanText(messages) {
    const last = messages[messages.length - 1];
    if ((last.role === 'user' || last.role === 'tool') && last.content) {
        return last.content;
    }
    const err = new Error(`Last request message has no scannable content (role=${last.role})`);
    err.code = 'no_scannable_content';
    throw err;
}

// ---------------------------------------------------------------------------
// Extract the content string to scan from the last response choice.
// Requires role 'assistant' with a non-empty content field.
// Throws an Error with .code = 'tool_call_not_supported' when content is
// absent (e.g. a tool_call response), or 'no_scannable_content' otherwise.
// ---------------------------------------------------------------------------
function extractResponseScanText(choices) {
    const last = choices[choices.length - 1];
    const msg = last && last.message;
    if (msg && msg.role === 'assistant') {
        if (msg.content) return msg.content;
        const err = new Error('Response is a tool call — scanning not supported');
        err.code = 'tool_call_not_supported';
        throw err;
    }
    const err = new Error('Last response choice has no scannable assistant message');
    err.code = 'no_scannable_content';
    throw err;
}

// ---------------------------------------------------------------------------
// Build a standardised HTTP error response.
// ---------------------------------------------------------------------------
function passUpstreamResponse(r, upstreamReply, customBody) {
    r.headersOut['Content-Type'] = upstreamReply.headersOut['Content-Type'] || "";
    r.return(upstreamReply.status, customBody || upstreamReply.responseText);
}

// ---------------------------------------------------------------------------
// Return an OpenAI-standard content_filter response, in either streaming or
// non-streaming format depending on the isStream flag.
// ---------------------------------------------------------------------------
function contentFilterResponse(r, model, isStream) {
    if (isStream) {
        sendStreamEvent(r, {
            id: 'chatcmpl-' + Date.now(),
            object: 'chat.completion.chunk',
            model: model || '',
            choices: [{ index: 0, delta: {}, finish_reason: 'content_filter' }]
        });
    } else {
        r.headersOut['Content-Type'] = 'application/json';
        r.return(200, JSON.stringify({
            id: 'chatcmpl-' + Date.now(),
            object: 'chat.completion',
            created: Math.floor(Date.now() / 1000),
            model: model || '',
            choices: [{
                index: 0,
                message: { role: 'assistant', content: null },
                finish_reason: 'content_filter'
            }]
        }));
    }
}

// ---------------------------------------------------------------------------
// Build a standardised HTTP error response.
// ---------------------------------------------------------------------------
function errorResponse(r, httpCode, code, message) {
    r.headersOut['Content-Type'] = 'application/json';
    r.return(httpCode, JSON.stringify({
        error: {
            message: message || 'Blocked by AI Guardrails',
            type: 'guardrails_block',
            code: code
        }
    }));
}

// ---------------------------------------------------------------------------
// Extract assistant text from SSE buffer.
// Parses "data:" JSON lines and concatenates delta.content in order.
// Skips invalid or incomplete JSON payloads.
// ---------------------------------------------------------------------------
function extractSSEContent(rawText) {
    const buffer = [];
    const lines = rawText.split(/\r?\n/);

    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];

        // Strict match: "data:"
        if (!line.startsWith('data:')) continue;

        let payload = line.slice(5).trim();
        if (!payload || payload === '[DONE]') continue;

        let chunk;
        try {
            chunk = JSON.parse(payload);
        } catch (e) {
            // Likely partial JSON → skip safely
            continue;
        }

        const choices = chunk.choices;
        if (!choices) continue;

        for (let j = 0; j < choices.length; j++) {
            const delta = choices[j].delta;
            if (!delta) continue;

            // Text content
            if (delta.content) {
                buffer.push(delta.content);
            }
        }
    }

    return buffer.join('').trim();
}

// ---------------------------------------------------------------------------
// Reconstruct a valid SSE stream that delivers redactedContent distributed
// across the same number of chunks as the original upstream response.
// Metadata (id, model, created, finish_reason) and chunk count are extracted
// from the buffered raw SSE so the rebuilt stream matches the original pacing.
// ---------------------------------------------------------------------------
function rebuildSSEWithRedactedContent(rawSSE, redactedContent) {
    let id = 'chatcmpl-redacted';
    let model = '';
    let created = Math.floor(Date.now() / 1000);
    let finishReason = 'stop';
    let numContentChunks = 0;

    let firstChunk = null;

    const lines = rawSSE.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') continue;

        let chunk;
        try { chunk = JSON.parse(payload); } catch (e) { continue; }

        if (!firstChunk) firstChunk = chunk;

        const choice = chunk.choices && chunk.choices[0];
        if (!choice) continue;

        if (choice.finish_reason) {
            finishReason = choice.finish_reason;
        }
        if (choice.delta && choice.delta.content) {
            numContentChunks++;
        }
    }

    if (firstChunk) {
        if (firstChunk.id) id = firstChunk.id;
        if (firstChunk.model) model = firstChunk.model;
        if (firstChunk.created) created = firstChunk.created;
    }

    // Fall back to a single chunk if the original had no content chunks.
    if (numContentChunks === 0) numContentChunks = 1;

    // Slice redacted text into numContentChunks equal-ish character pieces.
    const total = redactedContent.length;
    const baseSize = Math.floor(total / numContentChunks);
    const remainder = total % numContentChunks;
    const slices = [];
    let pos = 0;
    for (let i = 0; i < numContentChunks; i++) {
        const size = baseSize + (i < remainder ? 1 : 0);
        slices.push(redactedContent.slice(pos, pos + size));
        pos += size;
    }

    const mkChunk = function (choices) {
        const obj = { id: id, object: 'chat.completion.chunk', created: created };
        if (model) obj.model = model;
        obj.choices = choices;
        return 'data: ' + JSON.stringify(obj) + '\n\n';
    };

    let out = '';

    // Role announcement chunk (mirrors what OpenAI sends as the first chunk).
    out += mkChunk([{ index: 0, delta: { role: 'assistant', content: '' }, finish_reason: null }]);

    // One content chunk per slice.
    for (let i = 0; i < slices.length; i++) {
        out += mkChunk([{ index: 0, delta: { content: slices[i] }, finish_reason: null }]);
    }

    // Finish chunk.
    out += mkChunk([{ index: 0, delta: {}, finish_reason: finishReason }]);

    out += 'data: [DONE]\n\n';
    return out;
}

// ---------------------------------------------------------------------------
// Replay an upstream SSE response verbatim to the client.
// ---------------------------------------------------------------------------
function replaySSE(r, rawSSE) {
    _setSSEHeaders(r);
    r.return(200, rawSSE);
}

function sendStreamEvent(r, payload) {
    _setSSEHeaders(r);
    r.return(200, `data: ${JSON.stringify(payload)}\n\ndata: [DONE]\n\n`);
}

// ---------------------------------------------------------------------------
// Get the upstream URI for chat completions.
// ---------------------------------------------------------------------------
function getUpstreamUri() {
    return '/app/chat/completions';
}

export default { scanWithGuardrails, extractRequestScanText, extractResponseScanText, extractSSEContent, errorResponse, contentFilterResponse, rebuildSSEWithRedactedContent, replaySSE, sendStreamEvent, getUpstreamUri, passUpstreamResponse };

function _setSSEHeaders(r) {
    r.headersOut['Content-Type'] = 'text/event-stream';
    r.headersOut['Cache-Control'] = 'no-cache';
    r.headersOut['X-Accel-Buffering'] = 'no';
}