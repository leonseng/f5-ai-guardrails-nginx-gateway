import pytest

from conf_helper import chat_request, collect_sse_chunks, assemble_content_from_chunks
from conf_helper import DATASET


CLEARED_TEXT = DATASET["CLEARED"]["text"]
FLAGGED_TEXT = DATASET["FLAGGED"]["text"]


class TestGuardrailsValidationError:
    def test_422_handled(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("422")
        llm_scenario.set("normal")
        resp = chat_request(CLEARED_TEXT)
        assert resp.status_code == 400
        assert "error" in resp.json()


@pytest.mark.last
class TestFailOpen:
    """
    Tests for F5_AI_GUARDRAILS_FAIL_OPEN=true behaviour.

    When the guardrails service returns a non-200 response, the proxy should
    pass the request through to the LLM rather than blocking the caller.
    Each test uses the fail_open_container fixture which restarts the proxy
    with FAIL_OPEN=true before the test and restores FAIL_OPEN=false after.
    """

    @pytest.mark.parametrize("scan_outcome", ["error", "422"])
    def test_non_streaming_passthrough_on_scan_failure(self, fail_open_container, guardrails_scenario, llm_scenario, scan_outcome):
        guardrails_scenario.set(scan_outcome)
        llm_scenario.set("normal", response_type="FLAGGED")
        resp = chat_request(FLAGGED_TEXT)
        assert resp.status_code == 200

        body = resp.json()
        assert "choices" in body
        assert body["choices"][0]["message"]["content"] == FLAGGED_TEXT

        content = body["choices"][0]["message"]["content"]
        assert content == FLAGGED_TEXT

    def test_non_streaming_fail_open_does_not_affect_blocked(self, fail_open_container, guardrails_scenario, llm_scenario):
        """FAIL_OPEN only applies to scan errors — a 200 blocked response must still block."""
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="FLAGGED")
        resp = chat_request(FLAGGED_TEXT)

        assert resp.status_code == 200
        assert "application/json" == resp.headers.get("Content-Type", "")

    @pytest.mark.parametrize("scan_outcome", ["error", "422"])
    def test_streaming_passthrough_on_scan_failure(self, fail_open_container, guardrails_scenario, llm_scenario, scan_outcome):
        guardrails_scenario.set(scan_outcome)
        llm_scenario.set("normal", response_type="FLAGGED")
        resp = chat_request(FLAGGED_TEXT, True)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        chunks = collect_sse_chunks(resp)
        content = assemble_content_from_chunks(chunks)
        assert content == FLAGGED_TEXT

        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["stop"]

    def test_streaming_fail_open_does_not_affect_blocked(self, fail_open_container, guardrails_scenario, llm_scenario):
        """FAIL_OPEN only applies to scan errors — a 200 blocked response must still block."""
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="FLAGGED")
        resp = chat_request(FLAGGED_TEXT, True)

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
