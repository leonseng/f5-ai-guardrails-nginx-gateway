import json
import requests
from string import Template


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTAINER_PORT = 11434
COMPOSE_FILE = "docker-compose.yml"

GUARDRAILS_MOCK_PORT = 9999
LLM_MOCK_PORT = 11435
GUARDRAILS_URL_FOR_CONTAINER = f"http://host.docker.internal:{GUARDRAILS_MOCK_PORT}/backend/v1"
LLM_URL_FOR_CONTAINER = f"http://host.docker.internal:{LLM_MOCK_PORT}"

PROXY_BASE_URL = "http://localhost:11434"


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

DATASET = {
    "CLEARED": {
        "text": "do you like apple?",
        "scan_outcome": "cleared"
    },
    "FLAGGED": {
        "text": "are grocery prices at Woolworths cheaper than those at Aldi?",
        "scan_outcome": "flagged"
    },
    "REDACTABLE": {
        "text": "do you like blueberry?",
        "scan_outcome": "redacted"
    },
    "REDACTED": {
        "text": "do you like *********?",
        "scan_outcome": "redacted"
    }
}

# ---------------------------------------------------------------------------
# Response templates
# ---------------------------------------------------------------------------

GUARDRAILS_RESPONSE_TEMPLATE = Template("""
    {
        "id": "019dd658-f82f-7004-a9bd-ba7a792dfa76",
        "result": {
            "scannerResults": [
                {
                    "scannerId": "01999a5a-9b8f-70ba-9625-516f734166c8",
                    "scannerVersionMeta": {
                        "id": "019a80d9-ce81-7057-977f-48ccc1dcd6cc",
                        "createdAt": "2025-11-14T05:32:29.185378+00:00",
                        "createdBy": "auth0|68d3a0462a5394006b349021",
                        "name": "v_2",
                        "published": true,
                        "description": ""
                    },
                    "outcome": "passed",
                    "data": {"type": "keyword", "matches": {}},
                    "customConfig": false,
                    "startedDate": "2026-04-28T23:07:28.176544+00:00",
                    "completedDate": "2026-04-28T23:07:28.176753+00:00",
                    "scanDirection": "request"
                }
            ],
            "outcome": "$outcome"
        },
        "redactedInput": "$redactedInput"
    }
""")

LLM_RESPONSE_TEMPLATE = Template("""
    {
        "id": "chatcmpl-mock-normal",
        "object": "chat.completion",
        "created": 1714348800,
        "model": "mock-llm",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "$content"
                },
                "finish_reason": "$finish_reason"
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 9, "total_tokens": 19}
    }
""")


# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------

GUARDRAILS_ERROR_RESPONSE = {
    "detail": "Internal server error",
}

GUARDRAILS_VALIDATION_ERROR = {
    "detail": [
        {
            "loc": ["body", "input"],
            "msg": "field required",
            "type": "value_error.missing",
        }
    ]
}

LLM_ERROR_RESPONSE = {
    "error": {
        "message": "Internal server error from mock LLM backend.",
        "type": "server_error",
        "code": 500,
    }
}

LLM_UNAVAILABLE_RESPONSE = {
    "error": {
        "message": "Service unavailable.",
        "type": "server_error",
        "code": 503,
    }
}

LLM_MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {"id": "mock-llm", "object": "model", "created": 1714348800, "owned_by": "mock"},
        {"id": "mock-llm-v2", "object": "model", "created": 1714348800, "owned_by": "mock"},
    ],
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def chat_request(content: str, stream: bool = False) -> requests.Response:
    return requests.post(
        f"{PROXY_BASE_URL}/chat/completions",
        json={"model": "mock-llm", "messages": [{"role": "user", "content": content}], "stream": stream},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )


def collect_sse_chunks(resp: requests.Response) -> list[dict]:
    """Parse SSE lines into a list of chunk payloads, stopping at [DONE]."""
    chunks = []
    for raw_line in resp.iter_lines():
        line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        chunks.append(json.loads(data))
    return chunks


def assemble_content_from_chunks(chunks: list[dict]) -> str:
    """Concatenate delta.content across all SSE chunks."""
    return "".join(
        c["choices"][0]["delta"].get("content", "") for c in chunks
    ).strip()
