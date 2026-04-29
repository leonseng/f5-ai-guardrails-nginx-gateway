import json
import requests

from conftest import PROXY_BASE_URL


CHAT_URL = f"{PROXY_BASE_URL}/chat/completions"
MODELS_URL = f"{PROXY_BASE_URL}/models"

HEADERS = {
    "Content-Type": "application/json",
}


def chat_request(content: str) -> requests.Response:
    return requests.post(
        CHAT_URL,
        json={"model": "mock-llm", "messages": [{"role": "user", "content": content}]},
        headers=HEADERS,
        timeout=30,
    )


def stream_chat_request(content: str) -> requests.Response:
    return requests.post(
        CHAT_URL,
        json={
            "model": "mock-llm",
            "messages": [{"role": "user", "content": content}],
            "stream": True,
        },
        headers=HEADERS,
        stream=True,
        timeout=30,
    )


def collect_sse_chunks(resp: requests.Response) -> list[dict]:
    """Parse SSE lines into a list of chunk payloads, stopping at [DONE]."""
    chunks = []
    for raw_line in resp.iter_lines():
        line = raw_line.decode() if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data:"):
            continue
        data = line[len("data:"):].strip()
        if data == "[DONE]":
            break
        chunks.append(json.loads(data))
    return chunks


def assemble_content_from_chunks(chunks: list[dict]) -> str:
    """Concatenate delta.content across all SSE chunks."""
    return "".join(
        c["choices"][0]["delta"].get("content", "") for c in chunks
    )
