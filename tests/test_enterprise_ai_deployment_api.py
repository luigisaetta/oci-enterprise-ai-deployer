"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-04-30
License: MIT

Description:
    Tests for the FastAPI web console backend.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from enterprise_ai_deployment.api import RUNS, create_app


def test_create_preview_run_and_stream_validation_events() -> None:
    """Preview runs return a run id and stream real validation progress events."""
    RUNS.clear()
    client = TestClient(create_app())

    response = client.post(
        "/api/actions/preview",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "dry-run",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated",
        },
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert run_id

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())

    assert "event: status" in body
    assert "event: log" in body
    assert "event: done" in body
    assert "passed real backend validation." in body
    assert "Deployment plan:" in body
    assert "docker build --platform linux/amd64" in body
    assert "Dry run: no OCI commands were executed." in body
    assert "CLI dry-run completed successfully." in body


def test_preview_run_streams_validation_failure() -> None:
    """Invalid YAML/configuration streams a failed validation result."""
    RUNS.clear()
    client = TestClient(create_app())

    response = client.post(
        "/api/actions/preview",
        json={
            "yaml": "application:\n  name: demo\n",
            "env": "",
            "action": "validate",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated",
        },
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())

    assert "event: done" in body
    assert '"state": "failed"' in body
    assert "Deployment YAML schema validation failed:" in body
    assert "container: Field required" in body


def test_unknown_run_stream_returns_404() -> None:
    """Unknown run ids produce a clear 404."""
    client = TestClient(create_app())

    response = client.get("/api/runs/missing/events")

    assert response.status_code == 404


def _valid_web_yaml() -> str:
    """Return a deployment YAML valid when resolved from the repository root."""
    return """
application:
  name: demo-agent
  compartment_id: ocid1.compartment.oc1..example
  region: eu-frankfurt-1
  region_key: fra

container:
  context: examples/hello_world_container
  dockerfile: Dockerfile
  repository: ai-agents
  image_name: demo-agent
  tag_strategy: explicit
  ocir_namespace: auto
  tag: dev

hosted_application:
  display_name: Demo Agent
  security:
    auth_type: NO_AUTH

hosted_deployment:
  display_name: Demo Agent Deployment
"""
