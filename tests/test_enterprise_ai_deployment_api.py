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


def test_create_preview_run_and_stream_fake_events() -> None:
    """Preview runs return a run id and stream fake SSE progress events."""
    RUNS.clear()
    client = TestClient(create_app())

    response = client.post(
        "/api/actions/preview",
        json={
            "yaml": "application:\n  name: demo\n",
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
    assert "Fake validation completed without errors." in body
    assert "No Docker or OCI command was executed." in body


def test_unknown_run_stream_returns_404() -> None:
    """Unknown run ids produce a clear 404."""
    client = TestClient(create_app())

    response = client.get("/api/runs/missing/events")

    assert response.status_code == 404
