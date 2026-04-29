import pytest

from helpers import chat_request, stream_chat_request, collect_sse_chunks, assemble_content_from_chunks


class TestGuardrailsValidationError:
    def test_422_handled(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("422")
        llm_scenario.set("normal")
        resp = chat_request("anything")
        assert resp.status_code in (422, 500) or "error" in resp.json()


@pytest.mark.last
class TestFailOpen:
    """
    Tests for F5_AI_GUARDRAILS_FAIL_OPEN=true behaviour.

    When the guardrails service returns a non-200 response, the proxy should
    pass the request through to the LLM rather than blocking the caller.
    Each test uses the fail_open_container fixture which restarts the proxy
    with FAIL_OPEN=true before the test and restores FAIL_OPEN=false after.
    """

    def test_passthrough_on_scan_500(self, fail_open_container, guardrails_scenario, llm_scenario):
        """Proxy should forward to LLM when /scans returns 500."""
        guardrails_scenario.set("guardrails_error")
        llm_scenario.set("normal")
        resp = chat_request("anything")
        assert resp.status_code == 200

    def test_passthrough_returns_llm_content(self, fail_open_container, guardrails_scenario, llm_scenario):
        """LLM response should be returned intact when guardrails is bypassed."""
        guardrails_scenario.set("guardrails_error")
        llm_scenario.set("normal")
        body = chat_request("anything").json()
        assert "choices" in body
        assert body["choices"][0]["message"]["content"] == "This is a normal mock LLM response."

    def test_passthrough_on_scan_422(self, fail_open_container, guardrails_scenario, llm_scenario):
        """Proxy should also pass through when /scans returns 422."""
        guardrails_scenario.set("422")
        llm_scenario.set("normal")
        resp = chat_request("anything")
        assert resp.status_code == 200

    def test_streaming_passthrough_assembles_content(self, fail_open_container, guardrails_scenario, llm_scenario):
        """Streamed LLM content should arrive intact when guardrails is bypassed."""
        guardrails_scenario.set("guardrails_error")
        llm_scenario.set("normal")
        chunks = collect_sse_chunks(stream_chat_request("anything"))
        assembled = assemble_content_from_chunks(chunks).strip()
        assert len(assembled) > 0

    def test_fail_open_does_not_affect_blocked(self, fail_open_container, guardrails_scenario, llm_scenario):
        """FAIL_OPEN only applies to scan errors — a 200 blocked response must still block."""
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        resp = chat_request("bad prompt")
        assert resp.status_code == 400
        assert resp.json()["error"]["type"] == "guardrails_block"
