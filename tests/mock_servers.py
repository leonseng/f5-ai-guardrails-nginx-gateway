import logging
import json
import threading
from flask import Flask, jsonify, request


# ---------------------------------------------------------------------------
# Guardrails canned responses
# ---------------------------------------------------------------------------

BLOCKED_RESPONSE = {
    "id": "019dd656-44a6-70b5-b9a2-93c1042f1521",
    "result": {
        "scannerResults": [
            {
                "scannerId": "019a7598-8cc7-7091-9520-b73c9c627f23",
                "scannerVersionMeta": {
                    "id": "019a7598-8cc7-70a7-a527-a27cf83e8c56",
                    "createdAt": "2025-11-12T01:05:23.143682+00:00",
                    "createdBy": "auth0|68d3a0462a5394006b349021",
                    "name": "v_1",
                    "published": True,
                    "description": "",
                },
                "outcome": "failed",
                "data": {"type": "custom"},
                "customConfig": False,
                "startedDate": "2026-04-28T23:06:42.590482+00:00",
                "completedDate": "2026-04-28T23:06:42.650378+00:00",
                "scanDirection": "request",
            }
        ],
        "outcome": "flagged",
    },
    "redactedInput": "Are grocery prices at Woolworths cheaper than those at Aldi",
}

REDACTED_RESPONSE = {
    "id": "019dd657-7e2b-706b-acc3-df48f4848f17",
    "result": {
        "scannerResults": [
            {
                "scannerId": "01999a5a-9b8f-70ba-9625-516f734166c8",
                "scannerVersionMeta": {
                    "id": "019a80d9-ce81-7057-977f-48ccc1dcd6cc",
                    "createdAt": "2025-11-14T05:32:29.185378+00:00",
                    "createdBy": "auth0|68d3a0462a5394006b349021",
                    "name": "v_2",
                    "published": True,
                    "description": "",
                },
                "outcome": "failed",
                "data": {
                    "type": "keyword",
                    "matches": {
                        "blueberry": [[12, 21]],
                        "Blueberry": [[12, 21]],
                    },
                },
                "customConfig": False,
                "startedDate": "2026-04-28T23:05:51.406807+00:00",
                "completedDate": "2026-04-28T23:05:51.407002+00:00",
                "scanDirection": "request",
            }
        ],
        "outcome": "redacted",
    },
    "redactedInput": "do you like *********?",
}

CLEARED_RESPONSE = {
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
                    "published": True,
                    "description": "",
                },
                "outcome": "passed",
                "data": {"type": "keyword", "matches": {}},
                "customConfig": False,
                "startedDate": "2026-04-28T23:07:28.176544+00:00",
                "completedDate": "2026-04-28T23:07:28.176753+00:00",
                "scanDirection": "request",
            }
        ],
        "outcome": "cleared",
    },
    "redactedInput": "do you like apple?",
}

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

# ---------------------------------------------------------------------------
# LLM backend canned responses
# ---------------------------------------------------------------------------

LLM_NORMAL_RESPONSE = {
    "id": "chatcmpl-mock-normal",
    "object": "chat.completion",
    "created": 1714348800,
    "model": "mock-llm",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "This is a normal mock LLM response.",
            },
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 9, "total_tokens": 19},
}

LLM_REFUSAL_RESPONSE = {
    "id": "chatcmpl-mock-refusal",
    "object": "chat.completion",
    "created": 1714348800,
    "model": "mock-llm",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "I'm sorry, I can't help with that.",
            },
            "finish_reason": "content_filter",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
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
# Scenario controllers
# ---------------------------------------------------------------------------


class _ScenarioController:
    def __init__(self, valid: set, default: str):
        self._lock = threading.Lock()
        self._state = {"value": default}
        self.VALID = valid
        self._default = default

    def set(self, scenario: str) -> None:
        if scenario not in self.VALID:
            raise ValueError(
                f"Unknown scenario '{scenario}'. Choose from {self.VALID}"
            )
        with self._lock:
            self._state["value"] = scenario

    def get(self) -> str:
        with self._lock:
            return self._state["value"]

    def reset(self) -> None:
        self.set(self._default)


guardrails_controller = _ScenarioController(
    valid={"cleared", "blocked", "redacted", "422", "guardrails_error"},
    default="cleared",
)

llm_controller = _ScenarioController(
    valid={"normal", "refusal", "error", "unavailable"},
    default="normal",
)


# ---------------------------------------------------------------------------
# Flask mock servers
# ---------------------------------------------------------------------------

logging.getLogger("werkzeug").setLevel(logging.ERROR)

# --- Guardrails mock ---

_guardrails_app = Flask("guardrails_mock")
_guardrails_app.logger.disabled = True


@_guardrails_app.post("/backend/v1/scans")
def scans():
    scenario = guardrails_controller.get()
    if scenario == "blocked":
        return jsonify(BLOCKED_RESPONSE), 200
    if scenario == "redacted":
        return jsonify(REDACTED_RESPONSE), 200
    if scenario == "422":
        return jsonify(GUARDRAILS_VALIDATION_ERROR), 422
    if scenario == "guardrails_error":
        return jsonify(GUARDRAILS_ERROR_RESPONSE), 500
    return jsonify(CLEARED_RESPONSE), 200


# --- LLM backend mock ---

_llm_app = Flask("llm_mock")
_llm_app.logger.disabled = True


@_llm_app.get("/v1/models")
@_llm_app.get("/models")
def models():
    """Model listing — always succeeds regardless of scenario."""
    return jsonify(LLM_MODELS_RESPONSE), 200


def _sse_chunk(delta: dict, finish_reason=None) -> str:
    """Format a single SSE data line as the OpenAI streaming protocol requires."""
    payload = {
        "id": "chatcmpl-mock-stream",
        "object": "chat.completion.chunk",
        "created": 1714348800,
        "model": "mock-llm",
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _stream_response(content: str, finish_reason: str = "stop"):
    """
    Yield an OpenAI-compatible SSE stream:
      - one chunk with role
      - one chunk per word of content
      - one final chunk with finish_reason and empty delta
      - the terminal 'data: [DONE]' line
    """
    yield _sse_chunk({"role": "assistant", "content": ""})
    for word in content.split():
        yield _sse_chunk({"content": word + " "})
    yield _sse_chunk({}, finish_reason=finish_reason)
    yield "data: [DONE]\n\n"


@_llm_app.post("/v1/chat/completions")
@_llm_app.post("/chat/completions")
def chat_completions():
    from flask import Response, stream_with_context

    scenario = llm_controller.get()
    streaming = request.get_json(silent=True, force=True).get("stream", False)

    # Error scenarios are the same regardless of streaming mode.
    if scenario == "error":
        return jsonify(LLM_ERROR_RESPONSE), 500
    if scenario == "unavailable":
        return jsonify(LLM_UNAVAILABLE_RESPONSE), 503

    # Determine content and finish_reason for the scenario.
    if scenario == "refusal":
        content = "I'm sorry, I can't help with that."
        finish_reason = "content_filter"
    else:  # "normal"
        content = "This is a normal mock LLM response."
        finish_reason = "stop"

    if streaming:
        return Response(
            stream_with_context(_stream_response(content, finish_reason)),
            status=200,
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering if present
            },
        )

    # Non-streaming — pick the right canned response.
    if scenario == "refusal":
        return jsonify(LLM_REFUSAL_RESPONSE), 200
    return jsonify(LLM_NORMAL_RESPONSE), 200


@_llm_app.get("/health")
def llm_health():
    return jsonify({"status": "ok"}), 200
