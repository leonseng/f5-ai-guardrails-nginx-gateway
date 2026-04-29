from helpers import chat_request, stream_chat_request, collect_sse_chunks, assemble_content_from_chunks


class TestLLMError:
    def test_llm_500_propagated(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("error")
        resp = chat_request("anything")
        assert resp.status_code in (500, 502), (
            f"Expected 500/502 from proxy when LLM errors, got {resp.status_code}"
        )

    def test_llm_unavailable_propagated(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("unavailable")
        resp = chat_request("anything")
        assert resp.status_code in (503, 502), (
            f"Expected 503/502 from proxy when LLM unavailable, got {resp.status_code}"
        )


class TestLLMRefusal:
    def test_status_200(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("refusal")
        assert chat_request("anything").status_code == 200

    def test_finish_reason_content_filter(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("refusal")
        body = chat_request("anything").json()
        assert body["choices"][0]["finish_reason"] == "content_filter"

    def test_streaming_finish_reason_content_filter(self, guardrails_scenario, llm_scenario):
        guardrails_scenario.set("cleared")
        llm_scenario.set("refusal")
        chunks = collect_sse_chunks(stream_chat_request("anything"))
        finish_reasons = [
            c["choices"][0]["finish_reason"]
            for c in chunks
            if c["choices"][0].get("finish_reason")
        ]
        assert finish_reasons == ["content_filter"]
