import logging
import json

import threading
from typing import Optional
from flask import Flask, request, Response, stream_with_context

from conf_helper import (
    DATASET,
    GUARDRAILS_RESPONSE_TEMPLATE,
    LLM_RESPONSE_TEMPLATE,
    GUARDRAILS_VALIDATION_ERROR,
    GUARDRAILS_ERROR_RESPONSE,
    LLM_ERROR_RESPONSE,
    LLM_UNAVAILABLE_RESPONSE,
    LLM_MODELS_RESPONSE
)

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

    def get_scenario(self) -> str:
        with self._lock:
            return self._state["value"]

    def reset(self) -> None:
        self.set(self._default)


class _LLMScenarioController(_ScenarioController):
    def set(self, scenario: str, response_type: Optional[str] = None, finish_reason: Optional[str] = None) -> None:
        if scenario not in self.VALID:
            raise ValueError(
                f"Unknown scenario '{scenario}'. Choose from {self.VALID}"
            )
        with self._lock:
            self._state["value"] = scenario
            self._state["response_type"] = response_type or ""
            self._state["finish_reason"] = finish_reason or ""

    def get_response(self) -> str:
        with self._lock:
            return DATASET.get(self._state["response_type"], DATASET["CLEARED"])["text"]

    def get_finish_reason(self) -> str:
        with self._lock:
            return self._state["finish_reason"] if self._state["finish_reason"] else "stop"


guardrails_controller = _ScenarioController(
    valid={"normal", "422", "error"},
    default="normal",
)

llm_controller = _LLMScenarioController(
    valid={"normal", "refusal", "error", "unavailable"},
    default="normal",
)


# ---------------------------------------------------------------------------
# Flask mock servers
# ---------------------------------------------------------------------------

logging.getLogger("werkzeug").setLevel(logging.ERROR)

# --- Guardrails mock ---

_guardrails_app = Flask("guardrails_mock")
# _guardrails_app.logger.disabled = True


@_guardrails_app.post("/backend/v1/scans")
def scans():
    scenario = guardrails_controller.get_scenario()

    if scenario == "422":
        return Response(json.dumps(GUARDRAILS_VALIDATION_ERROR), status=422, mimetype='application/json')

    if scenario == "error":
        return Response(json.dumps(GUARDRAILS_ERROR_RESPONSE), status=500, mimetype='application/json')

    if scenario == "normal":
        scan_input = (request.get_json(silent=True, force=True) or {}).get("input", "")

        # if scan_input matches one of our known test inputs, return the corresponding canned outcome; otherwise default to "cleared"
        for key, entry in DATASET.items():
            if scan_input == entry["text"]:
                return Response(GUARDRAILS_RESPONSE_TEMPLATE.substitute(
                    outcome=entry["scan_outcome"],
                    redactedInput=DATASET["REDACTED"]["text"] if key == "REDACTABLE" else entry["text"]
                ), status=200, mimetype='application/json')

        return Response(GUARDRAILS_RESPONSE_TEMPLATE.substitute(
            outcome="cleared",
            redactedInput=scan_input
        ), status=200, mimetype='application/json')

    return Response(json.dumps({"error": f"Unknown scenario '{scenario}'"}), status=500, mimetype='application/json')


# --- LLM backend mock ---

_llm_app = Flask("llm_mock")
# _llm_app.logger.disabled = True


@_llm_app.get("/v1/models")
@_llm_app.get("/models")
def models():
    """Model listing — always succeeds regardless of scenario."""
    return Response(json.dumps(LLM_MODELS_RESPONSE), status=200, mimetype='application/json')


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
    scenario = llm_controller.get_scenario()
    response = llm_controller.get_response()
    finish_reason = llm_controller.get_finish_reason()

    streaming = (request.get_json(silent=True, force=True) or {}).get("stream", False)

    # Error scenarios are the same regardless of streaming mode.
    if scenario == "error":
        return Response(json.dumps(LLM_ERROR_RESPONSE), status=500, mimetype='application/json')
    if scenario == "unavailable":
        return Response(json.dumps(LLM_UNAVAILABLE_RESPONSE), status=503, mimetype='application/json')

    if streaming:
        return Response(
            stream_with_context(_stream_response(response, finish_reason)),
            status=200,
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # disable nginx buffering if present
            },
        )

    # Non-streaming — pick the right canned response.
    return Response(LLM_RESPONSE_TEMPLATE.substitute(content=response, finish_reason=finish_reason), status=200, mimetype='application/json')


@_llm_app.get("/health")
def llm_health():
    return Response(json.dumps({"status": "ok"}), status=200, mimetype='application/json')
