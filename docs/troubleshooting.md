# Troubleshooting

## Sample log output

**Cleared requests** — prompt and response both pass scanning, upstream returns 200:

```
2026/04/27 02:50:29 [warn] 30#30: *2 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 32 chars)
2026/04/27 02:50:30 [warn] 30#30: *2 js: [guardrails] scan ← HTTP 200 (968ms)
2026/04/27 02:50:30 [warn] 30#30: *2 js: [guardrails] outcome=cleared  policy=  reason=
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 150 chars)
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] headers → Authorization: Bearer ***, Content-Type: application/json
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] scan ← HTTP 200 (383ms)
2026/04/27 02:50:33 [warn] 30#30: *2 js: [guardrails] outcome=cleared  policy=  reason=
192.168.65.1 - - [27/Apr/2026:02:50:33 +0000] "POST /chat/completions HTTP/1.1" 200 1376 "-" "PostmanRuntime/7.53.0"
```

**Flagged request** — prompt scan returns `flagged`, request is blocked with HTTP 400:

```
2026/04/27 02:51:04 [warn] 30#30: *2 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 61 chars)
2026/04/27 02:51:05 [warn] 30#30: *2 js: [guardrails] scan ← HTTP 200 (902ms)
2026/04/27 02:51:05 [warn] 30#30: *2 js: [guardrails] outcome=flagged  policy=  reason=
192.168.65.1 - - [27/Apr/2026:02:51:05 +0000] "POST /chat/completions HTTP/1.1" 400 114 "-" "PostmanRuntime/7.53.0"
```

**Redacted prompt** — prompt scan returns `redacted` with `REDACT_PROMPT=true`, modified body forwarded to upstream:

```
2026/04/27 02:52:11 [warn] 30#30: *3 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 54 chars)
2026/04/27 02:52:12 [warn] 30#30: *3 js: [guardrails] scan ← HTTP 200 (874ms)
2026/04/27 02:52:12 [warn] 30#30: *3 js: [guardrails] outcome=redacted  policy=  reason=
2026/04/27 02:52:12 [warn] 30#30: *3 js: [guardrails] prompt redacted — forwarding modified body to upstream
2026/04/27 02:52:14 [warn] 30#30: *3 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 143 chars)
2026/04/27 02:52:15 [warn] 30#30: *3 js: [guardrails] scan ← HTTP 200 (391ms)
2026/04/27 02:52:15 [warn] 30#30: *3 js: [guardrails] outcome=cleared  policy=  reason=
192.168.65.1 - - [27/Apr/2026:02:52:15 +0000] "POST /chat/completions HTTP/1.1" 200 1341 "-" "PostmanRuntime/7.53.0"
```

**Redacted response (non-streaming)** — response scan returns `redacted` with `REDACT_RESPONSE=true`, modified response returned to client:

```
2026/04/27 02:53:07 [warn] 30#30: *4 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 38 chars)
2026/04/27 02:53:08 [warn] 30#30: *4 js: [guardrails] scan ← HTTP 200 (921ms)
2026/04/27 02:53:08 [warn] 30#30: *4 js: [guardrails] outcome=cleared  policy=  reason=
2026/04/27 02:53:11 [warn] 30#30: *4 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 187 chars)
2026/04/27 02:53:12 [warn] 30#30: *4 js: [guardrails] scan ← HTTP 200 (408ms)
2026/04/27 02:53:12 [warn] 30#30: *4 js: [guardrails] outcome=redacted  policy=  reason=
2026/04/27 02:53:12 [warn] 30#30: *4 js: [guardrails] response redacted — returning modified body to client
192.168.65.1 - - [27/Apr/2026:02:53:12 +0000] "POST /chat/completions HTTP/1.1" 200 1289 "-" "PostmanRuntime/7.53.0"
```

**Redacted response (streaming)** — streaming response scan returns `redacted` with `REDACT_RESPONSE=true`, rebuilt SSE stream with redacted content returned to client:

```
2026/04/27 02:54:01 [warn] 30#30: *5 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 38 chars)
2026/04/27 02:54:02 [warn] 30#30: *5 js: [guardrails] scan ← HTTP 200 (874ms)
2026/04/27 02:54:02 [warn] 30#30: *5 js: [guardrails] outcome=cleared  policy=  reason=
2026/04/27 02:54:05 [warn] 30#30: *5 js: [guardrails] scan → POST https://www.us1.calypsoai.app/backend/v1/scans (input: 195 chars)
2026/04/27 02:54:06 [warn] 30#30: *5 js: [guardrails] scan ← HTTP 200 (391ms)
2026/04/27 02:54:06 [warn] 30#30: *5 js: [guardrails] outcome=redacted  policy=  reason=
2026/04/27 02:54:06 [warn] 30#30: *5 js: [guardrails] response redacted — returning modified SSE to client
192.168.65.1 - - [27/Apr/2026:02:54:06 +0000] "POST /chat/completions HTTP/1.1" 200 643 "-" "PostmanRuntime/7.53.0"
```
