import pytest

from conf_helper import chat_request, collect_sse_chunks, assemble_content_from_chunks
from conf_helper import DATASET

CLEARED_TEXT = DATASET["CLEARED"]["text"]
FLAGGED_TEXT = DATASET["FLAGGED"]["text"]


class TestGuardrailsBlocked:
    # --- non-streaming ---

    def test_non_streaming_blocked_prompt(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal")

        resp = chat_request(FLAGGED_TEXT)

        assert resp.status_code == 200
        assert "application/json" == resp.headers.get("Content-Type", "")

        body = resp.json()
        assert body["choices"][0]["finish_reason"] == "content_filter"

    def test_non_streaming_blocked_response(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="FLAGGED")

        resp = chat_request(CLEARED_TEXT)

        assert resp.status_code == 200
        assert "application/json" == resp.headers.get("Content-Type", "")

        body = resp.json()
        assert body["choices"][0]["finish_reason"] == "content_filter"

    def test_streaming_blocked_prompt(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal")

        resp = chat_request(FLAGGED_TEXT, True)

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        chunks = collect_sse_chunks(resp)
        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["content_filter"]

    def test_streaming_blocked_response(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="FLAGGED")
        resp = chat_request(CLEARED_TEXT, True)

        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        chunks = collect_sse_chunks(resp)
        content = assemble_content_from_chunks(chunks)

        assert content == ""

        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["content_filter"]
