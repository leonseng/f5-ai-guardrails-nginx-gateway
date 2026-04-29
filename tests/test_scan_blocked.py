from helpers import chat_request, stream_chat_request


class TestGuardrailsBlocked:
    # --- non-streaming ---

    def test_status_400(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        assert chat_request("bad prompt").status_code == 400

    def test_error_type_guardrails_block(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        body = chat_request("bad prompt").json()
        assert body["error"]["type"] == "guardrails_block"

    def test_error_code_prompt_blocked(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        body = chat_request("bad prompt").json()
        assert body["error"]["code"] == "prompt_blocked"

    def test_error_message_contains_flagged(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        body = chat_request("bad prompt").json()
        assert "flagged" in body["error"]["message"]

    def test_no_choices_in_blocked_response(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        body = chat_request("bad prompt").json()
        assert "choices" not in body, "Blocked response should not contain choices"

    # --- streaming ---

    def test_streaming_status_400(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        resp = stream_chat_request("bad prompt")
        assert resp.status_code == 400

    def test_streaming_error_type_guardrails_block(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        body = stream_chat_request("bad prompt").json()
        assert body["error"]["type"] == "guardrails_block"

    def test_streaming_error_code_prompt_blocked(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        body = stream_chat_request("bad prompt").json()
        assert body["error"]["code"] == "prompt_blocked"

    def test_streaming_no_sse_chunks_emitted(self, guardrails_scenario, llm_scenario):
        """Blocked requests should not emit any SSE chunks at all."""
        guardrails_scenario.set("blocked")
        llm_scenario.set("normal")
        resp = stream_chat_request("bad prompt")
        # Response is not SSE — should be plain JSON error, no data: lines
        assert "text/event-stream" not in resp.headers.get("Content-Type", "")
