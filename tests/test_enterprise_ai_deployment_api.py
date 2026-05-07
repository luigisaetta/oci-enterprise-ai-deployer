"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-07
License: MIT

Description:
    Tests for the FastAPI web console backend.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from enterprise_ai_deployment import api
from enterprise_ai_deployment.api import RUNS, StoredRun, create_app
from enterprise_ai_deployment.compartments import clear_compartment_cache


def test_cors_origins_are_configurable(monkeypatch) -> None:
    """CORS origins can be configured for remote web console hosts."""
    monkeypatch.setenv(
        "DEPLOYER_WEB_CORS_ORIGINS",
        "http://192.168.1.25:3000, http://localhost:3000",
    )
    client = TestClient(create_app())

    response = client.options(
        "/api/runs",
        headers={
            "Origin": "http://192.168.1.25:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://192.168.1.25:3000"


def test_cors_origins_can_allow_all_clients(monkeypatch) -> None:
    """CORS can be opened for development networks when explicitly configured."""
    monkeypatch.setenv("DEPLOYER_WEB_CORS_ORIGINS", "*")
    client = TestClient(create_app())

    response = client.options(
        "/api/runs",
        headers={
            "Origin": "http://192.168.1.25:3000",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_api_key_is_required_when_configured(monkeypatch) -> None:
    """Protected API endpoints reject requests without the shared key."""
    RUNS.clear()
    monkeypatch.setenv("DEPLOYER_WEB_API_KEY", "test-key")
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "validate",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


def test_api_key_rejects_wrong_value(monkeypatch) -> None:
    """Protected API endpoints reject requests with the wrong shared key."""
    RUNS.clear()
    monkeypatch.setenv("DEPLOYER_WEB_API_KEY", "test-key")
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        headers={"X-API-Key": "wrong-key"},
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "validate",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key"


def test_api_key_allows_run_create_and_stream(monkeypatch) -> None:
    """Protected API endpoints accept requests with the configured key."""
    RUNS.clear()
    monkeypatch.setenv("DEPLOYER_WEB_API_KEY", "test-key")
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        headers={"X-API-Key": "test-key"},
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

    stream_response = client.get(
        f"/api/runs/{run_id}/events",
        headers={"X-API-Key": "test-key"},
    )

    assert stream_response.status_code == 200
    assert "event: done" in stream_response.text


def test_legacy_preview_endpoint_still_creates_run(tmp_path, monkeypatch) -> None:
    """The old preview endpoint remains as a compatibility alias."""
    RUNS.clear()
    _set_docker_login(tmp_path, monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/actions/preview",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "validate",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated",
        },
    )

    assert response.status_code == 200
    assert response.json()["run_id"] in RUNS


def test_create_action_run_and_stream_validation_events(tmp_path, monkeypatch) -> None:
    """Action runs return a run id and stream real validation progress events."""
    RUNS.clear()
    _set_docker_login(tmp_path, monkeypatch)
    client = TestClient(create_app())
    output_dir = tmp_path / "generated"

    response = client.post(
        "/api/runs",
        json={
            "yaml": _valid_web_yaml().replace(
                "ocir_namespace: auto", "ocir_namespace: mytenancy"
            ),
            "env": "LOG_LEVEL=INFO\n",
            "action": "dry-run",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": str(output_dir),
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
    assert "Docker login detected for target OCIR registry fra.ocir.io." in body
    assert "Deployment plan:" in body
    assert "Generated executable deploy script:" in body
    assert "deploy.sh" in body
    assert "docker build --platform linux/amd64" in body
    assert (
        "Dry run: no Docker build/push or OCI resource mutation commands were executed."
        in body
    )
    assert "CLI dry-run completed successfully." in body
    assert (output_dir / "deploy.sh").exists()

    script_response = client.get(f"/api/runs/{run_id}/deploy-script")

    assert script_response.status_code == 200
    assert script_response.headers["content-type"].startswith("text/x-shellscript")
    assert "fra.ocir.io/mytenancy/ai-agents/demo-agent:dev" in script_response.text
    assert "<resolved-ocir-namespace>" not in script_response.text
    assert "hosted-deployment create-hosted-deployment-single-docker-artifact" in (
        script_response.text
    )


def test_action_run_streams_validation_failure() -> None:
    """Invalid YAML/configuration streams a failed validation result."""
    RUNS.clear()
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
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


def test_validate_run_reports_missing_ocir_docker_login(tmp_path, monkeypatch) -> None:
    """Validate action reports when Docker is not logged in to target OCIR."""
    RUNS.clear()
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
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
    assert "Docker is not logged in to the target OCIR registry 'fra.ocir.io'" in body
    assert "docker login fra.ocir.io" in body


def test_web_validation_resolves_compartment_name(tmp_path, monkeypatch) -> None:
    """Web validation resolves compartment_name with the requested OCI profile."""
    RUNS.clear()
    clear_compartment_cache()
    _set_docker_login(tmp_path, monkeypatch)
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "data": [
                        {
                            "name": "agent-demo",
                            "id": "ocid1.compartment.oc1..resolved",
                        }
                    ]
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(
        "enterprise_ai_deployment.compartments.subprocess.run", fake_run
    )
    run = StoredRun(
        run_id="run",
        yaml=_valid_web_yaml_by_compartment_name(),
        env="",
        action="validate",
        profile="PROD",
        region="eu-frankfurt-1",
        output_dir="generated",
    )

    result = api._validate_uploaded_inputs(run)

    assert result.error is None
    assert calls
    assert "--profile" in calls[0]
    assert "PROD" in calls[0]
    assert "--name" in calls[0]
    assert "agent-demo" in calls[0]


def test_render_run_streams_real_cli_render(tmp_path, monkeypatch) -> None:
    """Render action streams output from the real CLI render command."""
    RUNS.clear()
    _set_docker_login(tmp_path, monkeypatch)
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "render",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated/web-test",
        },
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())

    assert "Starting real CLI render." in body
    assert "Generated OCI CLI JSON artifacts:" in body
    assert "generated/web-test/create-hosted-application.json" in body
    assert "CLI render completed successfully." in body


def test_build_run_uses_real_cli_build_streamer(tmp_path, monkeypatch) -> None:
    """Build action routes to the real CLI build streamer without pushing."""
    RUNS.clear()
    _set_docker_login(tmp_path, monkeypatch)
    seen = {}

    async def fake_stream_cli_command(run, **kwargs):
        seen.update(kwargs)
        yield api._to_sse(
            "done",
            {
                "state": "succeeded",
                "step": "complete",
                "message": "fake build complete",
            },
        )

    monkeypatch.setattr(api, "_stream_cli_command", fake_stream_cli_command)
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "build",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated/web-test",
        },
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())

    assert "fake build complete" in body
    assert seen["cli_command"] == "build"
    assert seen["step"] == "cli-build"
    assert seen["dry_run"] is False
    assert "push" not in seen["cli_command"]


def test_deploy_run_uses_real_cli_deploy_streamer(tmp_path, monkeypatch) -> None:
    """Deploy action routes to the real CLI deployment streamer."""
    RUNS.clear()
    _set_docker_login(tmp_path, monkeypatch)
    seen = {}

    async def fake_stream_cli_command(run, **kwargs):
        seen.update(kwargs)
        yield api._to_sse(
            "done",
            {
                "state": "succeeded",
                "step": "complete",
                "message": "fake deploy complete",
            },
        )

    monkeypatch.setattr(api, "_stream_cli_command", fake_stream_cli_command)
    client = TestClient(create_app())

    response = client.post(
        "/api/runs",
        json={
            "yaml": _valid_web_yaml(),
            "env": "LOG_LEVEL=INFO\n",
            "action": "deploy",
            "profile": "DEFAULT",
            "region": "eu-frankfurt-1",
            "output_dir": "generated/web-test",
        },
    )

    assert response.status_code == 200
    run_id = response.json()["run_id"]

    with client.stream("GET", f"/api/runs/{run_id}/events") as stream:
        body = "".join(stream.iter_text())

    assert "fake deploy complete" in body
    assert seen["cli_command"] == "deploy"
    assert seen["step"] == "cli-deploy"
    assert seen["dry_run"] is False


def test_cli_streamer_runs_python_unbuffered(monkeypatch) -> None:
    """CLI subprocess output is unbuffered so SSE logs arrive while it runs."""
    seen = {}

    class FakeStdout:
        def __init__(self) -> None:
            self._lines = [
                b"live line\n",
                (
                    b"Encountered error while waiting for work request to enter "
                    b"the specified state. Outputting last known resource state\n"
                ),
                b"",
            ]

        async def readline(self) -> bytes:
            return self._lines.pop(0)

    class FakeProcess:
        stdout = FakeStdout()

        async def wait(self) -> int:
            return 0

    async def fake_create_subprocess_exec(*command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(
        api.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    run = StoredRun(
        run_id="run",
        yaml=_valid_web_yaml(),
        env="",
        action="deploy",
        profile="DEFAULT",
        region="eu-frankfurt-1",
        output_dir="generated",
    )

    async def collect_events() -> list[str]:
        return [
            event
            async for event in api._stream_cli_command(
                run,
                cli_command="deploy",
                step="cli-deploy",
                start_message="start",
                success_message="done",
                failure_message="failed",
                dry_run=False,
                script_file=Path("generated/deploy.sh"),
            )
        ]

    events = asyncio.run(collect_events())

    assert seen["command"][1] == "-u"
    assert "--script-file" in seen["command"]
    assert "generated/deploy.sh" in seen["command"]
    assert seen["env"]["PYTHONUNBUFFERED"] == "1"
    assert any("live line" in event for event in events)
    assert any('"level": "warning"' in event for event in events)


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
  image_repository: ai-agents/demo-agent
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


def _valid_web_yaml_by_compartment_name() -> str:
    """Return web YAML that uses a compartment display name."""
    return _valid_web_yaml().replace(
        "  compartment_id: ocid1.compartment.oc1..example",
        "  compartment_name: agent-demo",
    )


def _set_docker_login(tmp_path: Path, monkeypatch) -> None:
    """Create a temporary Docker config with target OCIR credentials."""
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    (docker_config / "config.json").write_text(
        json.dumps({"auths": {"fra.ocir.io": {"auth": "encoded"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
