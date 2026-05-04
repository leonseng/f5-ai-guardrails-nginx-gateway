from conf_helper import chat_request, collect_sse_chunks, assemble_content_from_chunks
from conf_helper import DATASET

CLEARED_TEXT = DATASET["CLEARED"]["text"]
REDACTABLE_TEXT = DATASET["REDACTABLE"]["text"]
REDACTED_TEXT = DATASET["REDACTED"]["text"]


class TestGuardrailsRedacted:
    def test_non_streaming_redacted_prompt(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="REDACTED")
        resp = chat_request(REDACTABLE_TEXT)
        assert resp.status_code == 200

        body = resp.json()
        assert body["choices"][0]["finish_reason"] == "stop"

        content = body["choices"][0]["message"]["content"]
        assert content == REDACTED_TEXT

    def test_non_streaming_redacted_response(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="REDACTABLE")
        resp = chat_request(CLEARED_TEXT)
        assert resp.status_code == 200

        body = resp.json()
        assert body["choices"][0]["finish_reason"] == "stop"

        content = body["choices"][0]["message"]["content"]
        assert content == REDACTED_TEXT

    def test_streaming_redacted(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="REDACTABLE")
        resp = chat_request(CLEARED_TEXT, True)
        assert resp.status_code == 200

        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        chunks = collect_sse_chunks(resp)

        content = assemble_content_from_chunks(chunks)
        assert content == REDACTED_TEXT

        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["stop"]
