"""
conftest.py — pytest session fixtures for F5 Guardrails integration tests.

Architecture:
    pytest
      ├── Flask mock /scans server     (host, port 9999)   ← F5 guardrails mock
      ├── Flask mock LLM backend       (host, port 11435)  ← OpenAI-compatible LLM mock
      └── Docker proxy container       (host, port 11434)  ← system under test
            ├── calls /scans via F5_AI_GUARDRAILS_API_URL env var
            └── calls LLM via OPENAI_API_URL env var

Scenario control (per-test fixtures):
    def test_something(guardrails_scenario, llm_scenario):
        guardrails_scenario.set("blocked")   # "cleared" | "blocked" | "redacted" | "422"
        llm_scenario.set("normal")           # "normal" | "refusal" | "error" | "unavailable"
"""

import os
import subprocess
import threading
import time

import pytest
from flask import Flask

from mock_servers import (
    guardrails_controller, llm_controller,
    _guardrails_app, _llm_app
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONTAINER_PORT = 11434
COMPOSE_FILE = "docker-compose.yml"
GUARDRAILS_MOCK_PORT = 9999
LLM_MOCK_PORT = 11435
GUARDRAILS_URL_FOR_CONTAINER = (
    f"http://host.docker.internal:{GUARDRAILS_MOCK_PORT}/backend/v1"
)
LLM_URL_FOR_CONTAINER = f"http://host.docker.internal:{LLM_MOCK_PORT}"
PROXY_BASE_URL = "http://localhost:11434"

# ---------------------------------------------------------------------------
# Server thread helpers
# ---------------------------------------------------------------------------


def _run_server(app: Flask, port: int) -> None:
    app.run(host="0.0.0.0", port=port, threaded=True)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def mock_guardrails_server():
    """Start the F5 guardrails mock on port 9999 for the whole test session."""
    t = threading.Thread(
        target=_run_server, args=(_guardrails_app, GUARDRAILS_MOCK_PORT), daemon=True
    )
    t.start()
    time.sleep(0.5)
    yield


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Start the mock LLM backend on port 11435 for the whole test session."""
    t = threading.Thread(
        target=_run_server, args=(_llm_app, LLM_MOCK_PORT), daemon=True
    )
    t.start()
    time.sleep(0.5)
    yield


def _stream_compose_logs(env):
    """Stream docker compose logs to stdout in a background thread."""
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "up", "--build"],
        env=env,
    )


def _base_env() -> dict:
    """Base env vars shared by all container startups."""
    return {
        **os.environ,
        "OPENAI_API_URL": LLM_URL_FOR_CONTAINER,
        "OPENAI_API_KEY": "your-openai-or-ollama-api-key",
        "F5_AI_GUARDRAILS_API_URL": GUARDRAILS_URL_FOR_CONTAINER,
        "F5_AI_GUARDRAILS_API_TOKEN": "your_guardrails_api_token",
        "F5_AI_GUARDRAILS_PROJECT_ID": "your_project_id",
        "F5_AI_GUARDRAILS_SCAN_PROMPT": "true",
        "F5_AI_GUARDRAILS_SCAN_RESPONSE": "true",
        "F5_AI_GUARDRAILS_REDACT_PROMPT": "true",
        "F5_AI_GUARDRAILS_REDACT_RESPONSE": "true",
        "DEBUG": "true",
    }


def _restart_container(env: dict) -> None:
    """Tear down and bring up the proxy with the given env, then wait for readiness."""
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "down", "--remove-orphans"],
        check=True, env=env,
    )
    log_thread = threading.Thread(
        target=_stream_compose_logs, args=(env,), daemon=True
    )
    log_thread.start()
    print(f"\n[conftest] Waiting 5s for proxy on port {CONTAINER_PORT}...")
    time.sleep(5)


@pytest.fixture(scope="session", autouse=True)
def docker_compose_up(mock_guardrails_server, mock_llm_server):
    """
    Start the proxy with FAIL_OPEN=false (default/strict mode) for the session.
    Tests that need FAIL_OPEN=true use the fail_open_container fixture instead,
    which temporarily restarts the container and restores it afterwards.
    """
    env = {**_base_env(), "F5_AI_GUARDRAILS_FAIL_OPEN": "false"}

    print("\n[conftest] Tearing down existing containers...")
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "down", "--remove-orphans"],
        check=True, env=env,
    )

    print("[conftest] Starting containers (fail_open=false)...")
    _restart_container(env)

    yield

    print("\n[conftest] Tearing down containers...")
    subprocess.run(
        ["docker", "compose", "-f", COMPOSE_FILE, "down", "--remove-orphans"],
        check=True, env=env,
    )


@pytest.fixture(scope="class")
def fail_open_container():
    """
    Restart the proxy with F5_AI_GUARDRAILS_FAIL_OPEN=true once for the
    entire TestFailOpen class, then restore FAIL_OPEN=false afterwards.

    Request guardrails_scenario and llm_scenario directly in each test —
    do not add them as parameters here (scope mismatch: class > function).
    """
    fail_open_env = {**_base_env(), "F5_AI_GUARDRAILS_FAIL_OPEN": "true"}
    strict_env = {**_base_env(), "F5_AI_GUARDRAILS_FAIL_OPEN": "false"}

    print("\n[conftest] Restarting proxy with FAIL_OPEN=true...")
    _restart_container(fail_open_env)

    yield

    print("\n[conftest] Restoring proxy with FAIL_OPEN=false...")
    _restart_container(strict_env)


# ---------------------------------------------------------------------------
# Per-test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def guardrails_scenario():
    """
    Control what POST /backend/v1/scans returns for one test.

    Scenarios:
        "cleared"  (default) — scan passed, input unmodified
        "blocked"            — scan flagged the input
        "redacted"           — scan redacted keywords from input
        "422"                — guardrails returned a validation error

    Usage:
        def test_something(guardrails_scenario, llm_scenario):
            guardrails_scenario.set("blocked")
            llm_scenario.set("normal")
            resp = chat_request("anything")
    """
    yield guardrails_controller
    guardrails_controller.reset()


@pytest.fixture()
def llm_scenario():
    """
    Control what the mock LLM backend returns for one test.

    Scenarios:
        "normal"      (default) — 200 well-formed chat completion
        "refusal"               — 200 with finish_reason=content_filter
        "error"                 — 500 internal server error
        "unavailable"           — 503 service unavailable

    Usage:
        def test_llm_down(guardrails_scenario, llm_scenario):
            guardrails_scenario.set("cleared")
            llm_scenario.set("error")
            resp = chat_request("anything")
            assert resp.status_code == 500
    """
    yield llm_controller
    llm_controller.reset()
