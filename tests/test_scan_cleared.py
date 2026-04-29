import requests
from helpers import chat_request, stream_chat_request, collect_sse_chunks, assemble_content_from_chunks
from helpers import HEADERS, MODELS_URL


class TestClearedNormal:
    # --- non-streaming ---

    def test_status_200(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        assert chat_request("hello").status_code == 200

    def test_finish_reason_stop(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        body = chat_request("hello").json()
        assert body["choices"][0]["finish_reason"] == "stop"

    def test_content_not_redacted(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        body = chat_request("hello").json()
        content = body["choices"][0]["message"]["content"]
        assert "*" not in content, "Cleared scan should not redact content"

    def test_models_endpoint(self, guardrails_scenario, llm_scenario):
        resp = requests.get(MODELS_URL, headers=HEADERS, timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and len(body["data"]) > 0

    # --- streaming ---

    def test_streaming_status_200(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        resp = stream_chat_request("hello")
        assert resp.status_code == 200

    def test_streaming_content_type_sse(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        resp = stream_chat_request("hello")
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

    def test_streaming_assembles_content(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        chunks = collect_sse_chunks(stream_chat_request("hello"))
        assert len(assemble_content_from_chunks(chunks).strip()) > 0

    def test_streaming_content_not_redacted(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        chunks = collect_sse_chunks(stream_chat_request("hello"))
        assembled = assemble_content_from_chunks(chunks)
        assert "*" not in assembled, "Cleared scan should not redact streamed content"

    def test_streaming_finish_reason_stop(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("normal")
        chunks = collect_sse_chunks(stream_chat_request("hello"))
        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["stop"]
