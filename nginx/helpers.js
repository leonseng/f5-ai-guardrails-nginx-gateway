// helpers.js — Pure helper utilities shared by the guardrails NJS module.

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
// Extract the full assembled assistant content string from a buffered SSE
// response body (text/event-stream). Iterates all `data:` lines, parses
// each chunk JSON, and concatenates delta.content values in order.
// Throws with .code = 'no_scannable_content' if no content tokens found.
// Throws with .code = 'tool_call_not_supported' if only tool_call deltas.
// ---------------------------------------------------------------------------
function extractSSEContent(rawText) {
    let assembled = '';
    let hasToolCall = false;

    const lines = rawText.split('\n');
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') continue;

        let chunk;
        try { chunk = JSON.parse(payload); }
        catch (e) { continue; }

        const delta = chunk.choices && chunk.choices[0] && chunk.choices[0].delta;
        if (!delta) continue;

        if (delta.tool_calls) { hasToolCall = true; continue; }
        if (delta.content) assembled += delta.content;
    }

    if (assembled.length > 0) return assembled;

    if (hasToolCall) {
        const err = new Error('Response is a tool call — scanning not supported');
        err.code = 'tool_call_not_supported';
        throw err;
    }

    const err = new Error('SSE response has no scannable assistant content');
    err.code = 'no_scannable_content';
    throw err;
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
        if (firstChunk.id)      id      = firstChunk.id;
        if (firstChunk.model)   model   = firstChunk.model;
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

    const mkChunk = function(choices) {
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
    r.headersOut['Content-Type'] = 'text/event-stream';
    r.headersOut['Cache-Control'] = 'no-cache';
    r.headersOut['X-Accel-Buffering'] = 'no';
    r.return(200, rawSSE);
}

// ---------------------------------------------------------------------------
// Send a terminal SSE event signalling content was filtered.
// Mirrors the finish_reason OpenAI itself uses for safety-blocked streams.
// ---------------------------------------------------------------------------
function sendStreamContentFilter(r) {
    const chunk = JSON.stringify({
        id: 'chatcmpl-blocked',
        object: 'chat.completion.chunk',
        choices: [{ index: 0, delta: {}, finish_reason: 'content_filter' }]
    });
    r.headersOut['Content-Type'] = 'text/event-stream';
    r.headersOut['Cache-Control'] = 'no-cache';
    r.headersOut['X-Accel-Buffering'] = 'no';
    r.return(200, `data: ${chunk}\n\ndata: [DONE]\n\n`);
}

// ---------------------------------------------------------------------------
// Send an SSE-formatted error for infrastructure failures (upstream down, etc).
// Uses HTTP 200 with an error payload so SSE clients don't mishandle it.
// ---------------------------------------------------------------------------
function sendStreamError(r, code, message) {
    const payload = JSON.stringify({
        error: { message, type: 'guardrails_block', code }
    });
    r.headersOut['Content-Type'] = 'text/event-stream';
    r.headersOut['Cache-Control'] = 'no-cache';
    r.headersOut['X-Accel-Buffering'] = 'no';
    r.return(200, `data: ${payload}\n\ndata: [DONE]\n\n`);
}

export default { extractRequestScanText, extractResponseScanText, extractSSEContent, blockResponse, rebuildSSEWithRedactedContent, replaySSE, sendStreamContentFilter, sendStreamError };
