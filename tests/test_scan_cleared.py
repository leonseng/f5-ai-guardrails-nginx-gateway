import requests
from conf_helper import chat_request, collect_sse_chunks, assemble_content_from_chunks
from conf_helper import PROXY_BASE_URL, DATASET

CLEARED_TEXT = DATASET["CLEARED"]["text"]


class TestCleared:
    # --- non-streaming ---

    def test_non_streaming_cleared(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal")
        resp = chat_request(CLEARED_TEXT)
        assert resp.status_code == 200

        body = resp.json()
        assert body["choices"][0]["finish_reason"] == "stop"

        content = body["choices"][0]["message"]["content"]
        assert content == CLEARED_TEXT

    def test_models_endpoint(self, guardrails_scenario, llm_scenario):
        resp = requests.get(f"{PROXY_BASE_URL}/models", headers={"Content-Type": "application/json"}, timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and len(body["data"]) > 0

    # --- streaming ---

    def test_streaming_cleared(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal")
        resp = chat_request(CLEARED_TEXT, True)
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")

        chunks = collect_sse_chunks(resp)
        content = assemble_content_from_chunks(chunks)
        assert content == CLEARED_TEXT

        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["stop"]
