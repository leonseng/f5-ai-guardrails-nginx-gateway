from helpers import chat_request, stream_chat_request, collect_sse_chunks, assemble_content_from_chunks


class TestGuardrailsRedacted:
    # --- non-streaming ---

    def test_status_200(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("redacted")
        llm_scenario.set("normal")
        assert chat_request("do you like blueberry?").status_code == 200

    def test_redacted_content_contains_asterisks(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("redacted")
        llm_scenario.set("normal")
        body = chat_request("do you like blueberry?").json()
        content = body["choices"][0]["message"]["content"]
        assert "*" in content, f"Expected asterisks in redacted content, got: {content!r}"

    # --- streaming ---

    def test_streaming_status_200(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("redacted")
        llm_scenario.set("normal")
        resp = stream_chat_request("do you like blueberry?")
        assert resp.status_code == 200

    def test_streaming_redacted_content_contains_asterisks(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("redacted")
        llm_scenario.set("normal")
        chunks = collect_sse_chunks(stream_chat_request("do you like blueberry?"))
        assembled = assemble_content_from_chunks(chunks)
        assert "*" in assembled, (
            f"Expected asterisks in assembled streamed content, got: {assembled!r}"
        )
