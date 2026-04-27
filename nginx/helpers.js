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

export default { extractRequestScanText, extractResponseScanText, blockResponse };
