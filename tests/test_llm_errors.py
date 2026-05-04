from conf_helper import chat_request, collect_sse_chunks
from conf_helper import DATASET


CLEARED_TEXT = DATASET["CLEARED"]["text"]


class TestLLMError:
    def test_llm_error(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("error")

        resp = chat_request(CLEARED_TEXT)
        assert resp.status_code in (500, 502), (
            f"Expected 500/502 from proxy when LLM errors, got {resp.status_code}"
        )

    def test_llm_unavailable_propagated(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("unavailable")

        resp = chat_request(CLEARED_TEXT)
        assert resp.status_code in (503, 502), (
            f"Expected 503/502 from proxy when LLM unavailable, got {resp.status_code}"
        )


class TestLLMRefusal:
    def test_non_streaming(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="CLEARED", finish_reason="content_filter")

        resp = chat_request(CLEARED_TEXT)
        assert resp.status_code == 200, (
            f"Expected 200 from proxy when LLM refuses, got {resp.json()}"
        )

        body = resp.json()
        assert "choices" in body
        assert body["choices"][0]["finish_reason"] == "content_filter"

    def test_streaming_finish_reason_content_filter(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("normal")
        llm_scenario.set("normal", response_type="CLEARED", finish_reason="content_filter")

        resp = chat_request(CLEARED_TEXT, True)
        assert resp.status_code == 200

        chunks = collect_sse_chunks(resp)
        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["content_filter"]
