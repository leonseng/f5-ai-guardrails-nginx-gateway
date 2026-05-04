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
        guardrails_scenario.set("normal")   # "cleared" | "flagged" | "redacted" | "422"
        llm_scenario.set("cleared")           # "normal" | "refusal" | "error" | "unavailable"
"""

import os
import subprocess
import threading
import time

import pytest
from flask import Flask

from mock_servers import (llm_controller, guardrails_controller,
                          _guardrails_app, _llm_app
                          )
from conf_helper import (
    GUARDRAILS_MOCK_PORT,
    LLM_MOCK_PORT,
    LLM_URL_FOR_CONTAINER,
    GUARDRAILS_URL_FOR_CONTAINER,
    COMPOSE_FILE,
    CONTAINER_PORT
)

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


def _base_env() -> dict:
    """Base env vars shared by all container startups."""
    return {
        **os.environ,
        "OPENAI_API_URL": LLM_URL_FOR_CONTAINER,
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

    def _start_container(env):
        """Stream docker compose logs to stdout in a background thread."""
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "--build"],
            env=env,
        )

    log_thread = threading.Thread(
        target=_start_container, args=(env,), daemon=True
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
        "normal"  (default)  — scan passed, input unmodified
        "flagged"            — scan flagged the input
        "redacted"           — scan redacted keywords from input
        "422"                — guardrails returned a validation error

    Usage:
        def test_something(guardrails_scenario, llm_scenario):
            guardrails_scenario.set("normal")
            llm_scenario.set("cleared")
            resp = chat_request("anything")
    """
    yield guardrails_controller
    guardrails_controller.reset()


@pytest.fixture()
def llm_scenario():
    """
    Control what the mock LLM backend returns for one test.

    Scenarios:
        "cleared"      (default) — 200 well-formed chat completion
        "refusal"               — 200 with finish_reason=content_filter
        "error"                 — 500 internal server error
        "unavailable"           — 503 service unavailable

    Usage:
        def test_llm_down(guardrails_scenario, llm_scenario):
            guardrails_scenario.set("normal")
            llm_scenario.set("error")
            resp = chat_request("anything")
            assert resp.status_code == 500
    """
    yield llm_controller
    llm_controller.reset()
