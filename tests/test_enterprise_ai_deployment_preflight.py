"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-09
License: MIT

Description:
    Tests for shared CLI/Web preflight checks.
"""

from __future__ import annotations

import json
import subprocess

from enterprise_ai_deployment.config import OciCliConfig
from enterprise_ai_deployment.deployment_config import load_deployment_config
from enterprise_ai_deployment.preflight import (
    format_preflight_report,
    run_preflight_checks,
)


def test_preflight_report_succeeds_when_environment_is_ready(
    tmp_path, monkeypatch
) -> None:
    """Shared preflight checks cover Docker, OCIR login, and OCI CLI readiness."""
    _write_docker_login(tmp_path, monkeypatch)
    config_path = _write_config(tmp_path)

    monkeypatch.setattr(
        "enterprise_ai_deployment.preflight.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    def fake_run(command, **_kwargs):
        if command[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(command, 0, stdout="27.0.0\n", stderr="")
        if command == ["oci", "--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="3.81.0\n", stderr="")
        if command[-3:] == ["os", "ns", "get"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"data": "mytenancy"}), stderr=""
            )
        if command[-3:] == ["generative-ai", "hosted-application", "--help"]:
            return subprocess.CompletedProcess(command, 0, stdout="help", stderr="")
        if command[-3:] == ["generative-ai", "hosted-deployment", "--help"]:
            return subprocess.CompletedProcess(command, 0, stdout="help", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("enterprise_ai_deployment.preflight.subprocess.run", fake_run)

    report = run_preflight_checks(
        load_deployment_config(config_path),
        OciCliConfig(profile="DEFAULT", region="eu-frankfurt-1"),
    )

    assert report.success is True
    assert {check.name for check in report.checks} == {
        "Docker CLI",
        "Docker daemon",
        "Docker OCIR login",
        "OCI CLI",
        "OCI CLI version",
        "OCI read access",
        "OCI Hosted Application command",
        "OCI Hosted Deployment command",
    }
    assert "Preflight completed successfully." in format_preflight_report(report)


def test_preflight_report_fails_when_docker_login_is_missing(
    tmp_path, monkeypatch
) -> None:
    """Missing Docker credentials are reported by the shared preflight core."""
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    config_path = _write_config(tmp_path)

    monkeypatch.setattr(
        "enterprise_ai_deployment.preflight.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        "enterprise_ai_deployment.preflight.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout="3.81.0\n", stderr=""
        ),
    )

    report = run_preflight_checks(
        load_deployment_config(config_path),
        OciCliConfig(profile="DEFAULT", region="eu-frankfurt-1"),
    )

    assert report.success is False
    assert any(
        check.name == "Docker OCIR login"
        and check.status == "error"
        and "docker login fra.ocir.io" in check.message
        for check in report.checks
    )


def _write_docker_login(tmp_path, monkeypatch) -> None:
    """Create a Docker config with OCIR credentials."""
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    (docker_config / "config.json").write_text(
        json.dumps({"auths": {"fra.ocir.io": {"auth": "encoded"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))


def _write_config(tmp_path) -> str:
    """Write a minimal valid deployment config and return its path."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(
        f"""
application:
  name: demo-agent
  compartment_id: ocid1.compartment.oc1..example
  region: eu-frankfurt-1
  region_key: fra

container:
  context: {tmp_path}
  dockerfile: Dockerfile
  image_repository: ai-agents/demo-agent
  tag_strategy: explicit
  tag: "20260429"
  ocir_namespace: mytenancy

hosted_application:
  display_name: demo-agent
  security:
    auth_type: NO_AUTH

hosted_deployment:
  display_name: demo-agent-deployment
""",
        encoding="utf-8",
    )
    return str(config_path)
