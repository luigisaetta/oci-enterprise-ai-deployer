"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-03
License: MIT

Description:
    Tests for the non-interactive OCI Enterprise AI deployment CLI.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from enterprise_ai_deployment.deployment_config import (
    DeploymentConfigError,
    load_deployment_config,
)
from enterprise_ai_deployment.deployment_renderer import render_artifacts
from enterprise_ai_deployment.deployment_runner import format_command, main
from enterprise_ai_deployment.deployment_validation import (
    DeploymentValidationError,
    validate_deployment_config,
)
from enterprise_ai_deployment.ocir import (
    ImageReference,
    build_image_reference,
    docker_login_exists,
)


def test_load_deployment_config_reads_yaml_and_env_file(tmp_path, monkeypatch) -> None:
    """Deployment YAML is parsed and local_env references can come from .env."""
    monkeypatch.delenv("MY_AGENT_API_KEY", raising=False)
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    env_path = tmp_path / ".env"
    env_path.write_text("MY_AGENT_API_KEY=local-secret-value\n", encoding="utf-8")
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    config = load_deployment_config(config_path, env_file=env_path)

    assert config.application.name == "demo-agent"
    assert config.hosted_application.environment["secrets"]["API_KEY"]["env_name"] == (
        "MY_AGENT_API_KEY"
    )
    validate_deployment_config(config)


def test_load_deployment_config_rejects_unknown_yaml_fields(tmp_path) -> None:
    """Schema validation catches typo-like unknown fields before deployment."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(
        _valid_yaml(tmp_path).replace(
            "dockerfile: Dockerfile", "dockefile: Dockerfile"
        ),
        encoding="utf-8",
    )

    with pytest.raises(DeploymentConfigError) as exc_info:
        load_deployment_config(config_path)

    message = str(exc_info.value)
    assert "Deployment YAML schema validation failed:" in message
    assert "container.dockerfile" in message
    assert "container.dockefile" in message


def test_load_deployment_config_requires_explicit_tag(tmp_path) -> None:
    """Schema validation enforces cross-field container rules."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(
        _valid_yaml(tmp_path).replace('  tag: "20260429"\n', ""),
        encoding="utf-8",
    )

    with pytest.raises(DeploymentConfigError) as exc_info:
        load_deployment_config(config_path)

    assert "container.tag is required when tag_strategy is explicit" in str(
        exc_info.value
    )


def test_load_deployment_config_rejects_legacy_image_fields(tmp_path) -> None:
    """Legacy repository/image_name fields are no longer accepted."""
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(
        _valid_yaml(tmp_path).replace(
            "image_repository: ai-agents/demo-agent",
            "image_name: demo-agent\n  repository: ai-agents",
        ),
        encoding="utf-8",
    )

    with pytest.raises(DeploymentConfigError) as exc_info:
        load_deployment_config(config_path)

    message = str(exc_info.value)
    assert "container.image_repository" in message
    assert "container.image_name" in message
    assert "container.repository" in message


def test_load_enterprise_solution_config_reads_multiple_deployments(
    tmp_path, monkeypatch
) -> None:
    """Enterprise Solution YAML supports multiple serial deployments."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "solution.yaml"
    config_path.write_text(_valid_solution_yaml(tmp_path), encoding="utf-8")

    config = load_deployment_config(config_path)

    assert config.application.name == "demo-solution"
    assert config.application.compartment_id == "ocid1.compartment.oc1..example"
    assert [deployment.name for deployment in config.deployments] == [
        "agent-api",
        "mcp-server",
    ]
    assert config.deployments[0].hosted_application.display_name == "agent-api"
    assert config.deployments[1].hosted_application.display_name == "mcp-server"
    validate_deployment_config(config)


def test_render_artifacts_do_not_write_local_secret_values(
    tmp_path, monkeypatch
) -> None:
    """Generated JSON contains secret references but not clear-text local secrets."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "super-secret-local-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    config = load_deployment_config(config_path)

    artifacts = render_artifacts(
        config,
        ImageReference(
            container_uri="fra.ocir.io/ns/ai-agents/demo-agent",
            tag="abc1234",
        ),
        tmp_path / "generated",
    )

    environment_payload = json.loads(
        artifacts.environment_variables.read_text(encoding="utf-8")
    )
    auth_payload = json.loads(artifacts.inbound_auth_config.read_text(encoding="utf-8"))
    scaling_payload = json.loads(artifacts.scaling_config.read_text(encoding="utf-8"))
    networking_payload = json.loads(
        artifacts.networking_config.read_text(encoding="utf-8")
    )
    artifact_payload = json.loads(artifacts.active_artifact.read_text(encoding="utf-8"))
    deployment_payload = json.loads(
        artifacts.hosted_deployment_create.read_text(encoding="utf-8")
    )

    assert {
        "name": "LOG_LEVEL",
        "type": "PLAINTEXT",
        "value": "INFO",
    } in environment_payload
    assert all(item["name"] != "API_KEY" for item in environment_payload)
    assert auth_payload["inboundAuthConfigType"] == "IDCS_AUTH_CONFIG"
    assert auth_payload["idcsConfig"]["scope"] == "demo-agent/.default"
    assert "jwksUrl" not in auth_payload
    assert scaling_payload["minReplica"] == 1
    assert scaling_payload["maxReplica"] == 2
    assert scaling_payload["scalingType"] == "CPU"
    assert scaling_payload["targetCpuThreshold"] == 70
    assert networking_payload["inboundNetworkingConfig"]["endpointMode"] == "PUBLIC"
    assert networking_payload["outboundNetworkingConfig"]["networkMode"] == "MANAGED"
    assert artifact_payload == {
        "artifactType": "SIMPLE_DOCKER_ARTIFACT",
        "containerUri": "fra.ocir.io/ns/ai-agents/demo-agent",
        "tag": "abc1234",
    }
    assert "super-secret-local-value" not in artifacts.environment_variables.read_text(
        encoding="utf-8"
    )
    assert (
        deployment_payload["imageUri"] == "fra.ocir.io/ns/ai-agents/demo-agent:abc1234"
    )


def test_render_artifacts_writes_no_auth_inbound_auth_config(tmp_path) -> None:
    """NO_AUTH deployments pass the Limited Availability inbound auth enum."""
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_no_auth_yaml(tmp_path), encoding="utf-8")
    config = load_deployment_config(config_path)

    artifacts = render_artifacts(
        config,
        ImageReference(
            container_uri="fra.ocir.io/ns/ai-agents/demo-agent",
            tag="abc1234",
        ),
        tmp_path / "generated",
    )

    assert artifacts.inbound_auth_config is not None
    auth_payload = json.loads(artifacts.inbound_auth_config.read_text(encoding="utf-8"))
    hosted_application_payload = json.loads(
        artifacts.hosted_application_create.read_text(encoding="utf-8")
    )

    assert auth_payload == {"inboundAuthConfigType": "NO_AUTH_CONFIG"}
    assert (
        hosted_application_payload["jsonFiles"]["inboundAuthConfig"]
        == str(artifacts.inbound_auth_config)
    )


def test_render_enterprise_solution_writes_per_deployment_artifacts(
    tmp_path, monkeypatch
) -> None:
    """Multi-deployment render writes artifacts under one directory per deployment."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "solution.yaml"
    config_path.write_text(_valid_solution_yaml(tmp_path), encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "--dry-run",
            "render",
        ]
    )

    assert exit_code == 0
    assert (
        tmp_path / "generated" / "agent-api" / "create-hosted-application.json"
    ).exists()
    assert (
        tmp_path / "generated" / "mcp-server" / "create-hosted-deployment.json"
    ).exists()


def test_build_image_reference_uses_explicit_tag(tmp_path, monkeypatch) -> None:
    """Explicit tags build stable OCIR image references."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    config = load_deployment_config(config_path)

    image_reference = build_image_reference(config, namespace="mytenancy")

    assert image_reference.container_uri == "fra.ocir.io/mytenancy/ai-agents/demo-agent"
    assert image_reference.tag == "20260429"
    assert (
        image_reference.image_uri
        == "fra.ocir.io/mytenancy/ai-agents/demo-agent:20260429"
    )


def test_docker_login_exists_reads_docker_auth_config(tmp_path) -> None:
    """Docker auth config is enough to detect an existing OCIR login."""
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    (docker_config / "config.json").write_text(
        json.dumps({"auths": {"fra.ocir.io": {"auth": "encoded"}}}),
        encoding="utf-8",
    )

    assert docker_login_exists("fra.ocir.io", docker_config)
    assert not docker_login_exists("iad.ocir.io", docker_config)


def test_validate_requires_ocir_docker_login(tmp_path, monkeypatch, capsys) -> None:
    """CLI validate reports a missing Docker login for the target OCIR registry."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "validate",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Docker is not logged in to the target OCIR registry 'fra.ocir.io'" in (
        captured.out
    )
    assert "docker login fra.ocir.io" in captured.out


def test_validate_accepts_ocir_docker_login(tmp_path, monkeypatch, capsys) -> None:
    """CLI validate succeeds when Docker config contains target OCIR credentials."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    docker_config = tmp_path / "docker"
    docker_config.mkdir()
    (docker_config / "config.json").write_text(
        json.dumps({"auths": {"fra.ocir.io": {"auth": "encoded"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "validate",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Configuration is valid." in captured.out


def test_build_dry_run_renders_docker_command(tmp_path, monkeypatch, capsys) -> None:
    """Dry-run build renders the Docker command without running subprocesses."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be called in dry-run mode")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--dry-run",
            "build",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "OCI CLI command: Build Container Image" in captured.out
    assert "docker build --platform linux/amd64" in captured.out
    assert (
        "-t 'fra.ocir.io/<resolved-ocir-namespace>/ai-agents/demo-agent:20260429'"
        in (captured.out)
    )
    assert "Dry run: command not executed." in captured.out


def test_build_resolves_namespace_and_runs_docker(tmp_path, monkeypatch) -> None:
    """Build resolves auto OCIR namespace and runs docker build with the full tag."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[-3:] == ["os", "ns", "get"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": "mytenancy"}),
                stderr="",
            )
        if command[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "build",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 2
    assert calls[0][-3:] == ["os", "ns", "get"]
    assert calls[1] == [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-f",
        str(dockerfile),
        "-t",
        "fra.ocir.io/mytenancy/ai-agents/demo-agent:20260429",
        str(tmp_path),
    ]


def test_push_dry_run_renders_docker_push_command(
    tmp_path, monkeypatch, capsys
) -> None:
    """Dry-run push renders the Docker push command without subprocesses."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be called in dry-run mode")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--dry-run",
            "push",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "OCI CLI command: Push Container Image" in captured.out
    assert (
        "docker push 'fra.ocir.io/<resolved-ocir-namespace>/ai-agents/demo-agent:20260429'"
        in captured.out
    )
    assert "Dry run: command not executed." in captured.out


def test_push_resolves_namespace_and_runs_docker_push(tmp_path, monkeypatch) -> None:
    """Push ensures the OCIR repository exists before docker push."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[-3:] == ["os", "ns", "get"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": "mytenancy"}),
                stderr="",
            )
        if "repository" in command and "list" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"items": []}}),
                stderr="",
            )
        if "repository" in command and "create" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"id": "ocid1.containerrepo.oc1..repo"}}),
                stderr="",
            )
        if command[:2] == ["docker", "push"]:
            return subprocess.CompletedProcess(command, 0, stdout="pushed\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "push",
        ]
    )

    assert exit_code == 0
    assert calls == [
        [
            "oci",
            "--region",
            "eu-frankfurt-1",
            "--output",
            "json",
            "os",
            "ns",
            "get",
        ],
        [
            "oci",
            "--region",
            "eu-frankfurt-1",
            "--output",
            "json",
            "artifacts",
            "container",
            "repository",
            "list",
            "--compartment-id",
            "ocid1.compartment.oc1..example",
            "--display-name",
            "ai-agents/demo-agent",
            "--all",
        ],
        [
            "oci",
            "--region",
            "eu-frankfurt-1",
            "--output",
            "json",
            "artifacts",
            "container",
            "repository",
            "create",
            "--compartment-id",
            "ocid1.compartment.oc1..example",
            "--display-name",
            "ai-agents/demo-agent",
            "--is-public",
            "false",
            "--wait-for-state",
            "AVAILABLE",
        ],
        ["docker", "push", "fra.ocir.io/mytenancy/ai-agents/demo-agent:20260429"],
    ]


def test_validate_rejects_unsupported_auth_type(tmp_path, monkeypatch) -> None:
    """Only OCI-supported auth types are accepted."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(
        _valid_yaml(tmp_path).replace("IDCS_AUTH_CONFIG", "oauth2"),
        encoding="utf-8",
    )
    config = load_deployment_config(config_path)

    try:
        validate_deployment_config(config)
    except DeploymentValidationError as exc:
        assert "IDCS_AUTH_CONFIG, NO_AUTH" in str(exc)
    else:
        raise AssertionError("Expected DeploymentValidationError")


def test_create_application_dry_run_renders_command(
    tmp_path, monkeypatch, capsys
) -> None:
    """Dry-run create-application renders artifacts and never calls OCI CLI."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be called in dry-run mode")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "--dry-run",
            "create-application",
        ]
    )

    captured = capsys.readouterr()
    scaling_payload = json.loads(
        (tmp_path / "generated" / "hosted-application-scaling-config.json").read_text(
            encoding="utf-8"
        )
    )

    assert exit_code == 0
    assert "\nOCI CLI command: Create Hosted Application\n\noci " in captured.out
    assert "--scaling-config" in captured.out
    assert scaling_payload["targetCpuThreshold"] == 70
    assert "--wait-for-state SUCCEEDED\n\nDry run" in captured.out
    assert "hosted-application create" in captured.out
    assert "Dry run: command not executed." in captured.out


def test_deploy_dry_run_renders_application_and_deployment_commands(
    tmp_path, monkeypatch, capsys
) -> None:
    """Dry-run deploy shows both OCI commands without calling OCI."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be called in dry-run mode")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "--dry-run",
            "deploy",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "OCI CLI command: Create Hosted Application" in captured.out
    assert "OCI CLI command: Create Hosted Deployment" in captured.out
    assert "hosted-application create" in captured.out
    assert "hosted-deployment create-hosted-deployment-single-docker-artifact" in (
        captured.out
    )
    assert "--hosted-application-id '<created-hosted-application-id>'" in captured.out
    assert "Dry run: no OCI commands were executed." in captured.out


def test_deploy_accepts_dry_run_after_subcommand(
    tmp_path, monkeypatch, capsys
) -> None:
    """Documented deploy --dry-run ordering is accepted by the CLI."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be called in dry-run mode")

    monkeypatch.setattr(subprocess, "run", fail_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "deploy",
            "--dry-run",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run: no OCI commands were executed." in captured.out


def test_deploy_creates_application_then_deployment(tmp_path, monkeypatch) -> None:
    """Deploy passes the created Hosted Application OCID to deployment creation."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[-3:] == ["os", "ns", "get"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": "mytenancy"}),
                stderr="",
            )
        if command[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")
        if "repository" in command and "list" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"items": []}}),
                stderr="",
            )
        if "repository" in command and "create" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"id": "ocid1.containerrepo.oc1..repo"}}),
                stderr="",
            )
        if command[:2] == ["docker", "push"]:
            return subprocess.CompletedProcess(command, 0, stdout="pushed\n", stderr="")
        if "list-hosted-applications" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"items": []}}),
                stderr="",
            )
        if "hosted-application" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "id": "ocid1.workrequest.oc1..createapp",
                            "compartment-id": "ocid1.compartment.oc1..wrong",
                            "compartmentId": "ocid1.compartment.oc1..wrongCamel",
                            "identifier": "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..app",
                        }
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"data": {"id": "ocid1.hosteddeployment.oc1..dep"}}),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "deploy",
        ]
    )

    assert exit_code == 0
    assert len(calls) == 8
    assert calls[0][-3:] == ["os", "ns", "get"]
    assert calls[1][:2] == ["docker", "build"]
    assert "fra.ocir.io/mytenancy/ai-agents/demo-agent:20260429" in calls[1]
    assert "repository" in calls[2]
    assert "list" in calls[2]
    assert "ai-agents/demo-agent" in calls[2]
    assert "repository" in calls[3]
    assert "create" in calls[3]
    assert "ai-agents/demo-agent" in calls[3]
    assert calls[4] == [
        "docker",
        "push",
        "fra.ocir.io/mytenancy/ai-agents/demo-agent:20260429",
    ]
    assert "list-hosted-applications" in calls[5]
    assert "hosted-application" in calls[6]
    assert "hosted-deployment" in calls[7]
    assert "--hosted-application-id" in calls[7]
    assert "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..app" in calls[7]


def test_enterprise_solution_deploy_stops_on_first_failed_deployment(
    tmp_path, monkeypatch, capsys
) -> None:
    """Serial Enterprise Solution deploy stops and reports the failed deployment."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "solution.yaml"
    config_path.write_text(_valid_solution_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[-3:] == ["os", "ns", "get"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": "mytenancy"}),
                stderr="",
            )
        if command[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(command, 0, stdout="built\n", stderr="")
        if "repository" in command and "list" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"items": []}}),
                stderr="",
            )
        if "repository" in command and "create" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"data": {"id": "ocid1.containerrepo.oc1..repo"}}),
                stderr="",
            )
        if command[:2] == ["docker", "push"] and "agent-api:20260429" in command[-1]:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="denied")
        raise AssertionError(f"unexpected command after failure: {command}")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "deploy",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "deployment 'agent-api'" in captured.out
    assert "push-container failed" in captured.out
    assert "mcp-server" in captured.out
    assert not any(
        command[:2] == ["docker", "build"] and "mcp-server:20260429" in command
        for command in calls
    )


def test_create_application_reuses_existing_display_name(
    tmp_path, monkeypatch, capsys
) -> None:
    """Existing Hosted Applications are reused instead of duplicated."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "list-hosted-applications" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "items": [
                                {
                                    "display-name": "demo-agent",
                                    "id": "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..existing",
                                }
                            ]
                        }
                    }
                ),
                stderr="",
            )
        raise AssertionError("create hosted application must not be called")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "create-application",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(calls) == 1
    assert "list-hosted-applications" in calls[0]
    assert "Using existing Hosted Application 'demo-agent'" in captured.out
    assert (
        "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..existing"
        in captured.out
    )


def test_create_application_ignores_deleted_display_name_match(
    tmp_path, monkeypatch, capsys
) -> None:
    """Deleted Hosted Applications do not block creating a replacement."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "list-hosted-applications" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "items": [
                                {
                                    "display-name": "demo-agent",
                                    "id": "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..deleted",
                                    "lifecycle-state": "DELETED",
                                }
                            ]
                        }
                    }
                ),
                stderr="",
            )
        if "hosted-application" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "id": "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..replacement"
                        }
                    }
                ),
                stderr="",
            )
        raise AssertionError("unexpected command")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "create-application",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(calls) == 2
    assert "list-hosted-applications" in calls[0]
    assert "hosted-application" in calls[1]
    assert "Using existing Hosted Application" not in captured.out
    assert (
        "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..replacement"
        in captured.out
    )


def test_create_application_prefers_active_over_deleted_display_name_match(
    tmp_path, monkeypatch, capsys
) -> None:
    """When duplicates exist by name, a deleted match is skipped."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if "list-hosted-applications" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "data": {
                            "items": [
                                {
                                    "display-name": "demo-agent",
                                    "id": "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..deleted",
                                    "lifecycle-state": "DELETED",
                                },
                                {
                                    "displayName": "demo-agent",
                                    "id": "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..active",
                                    "lifecycleState": "ACTIVE",
                                },
                            ]
                        }
                    }
                ),
                stderr="",
            )
        raise AssertionError("create hosted application must not be called")

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "create-application",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(calls) == 1
    assert "Using existing Hosted Application 'demo-agent'" in captured.out
    assert (
        "ocid1.generativeaihostedapplication.oc1.eu-frankfurt-1..active" in captured.out
    )


def test_create_deployment_requires_hosted_application_id(
    tmp_path, monkeypatch, capsys
) -> None:
    """Standalone deployment creation requires an existing application OCID."""
    monkeypatch.setenv("MY_AGENT_API_KEY", "local-secret-value")
    (tmp_path / "Dockerfile").write_text("FROM python:3.11-slim\n", encoding="utf-8")
    config_path = tmp_path / "deploy.yaml"
    config_path.write_text(_valid_yaml(tmp_path), encoding="utf-8")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(tmp_path / "generated"),
            "create-deployment",
        ]
    )

    captured = capsys.readouterr()

    assert exit_code == 1
    assert "requires --hosted-application-id" in captured.out


def test_cli_help_lists_required_commands(capsys) -> None:
    """CLI help exposes the first supported command set."""
    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    captured = capsys.readouterr()

    assert "create-application" in captured.out
    assert "create-deployment" in captured.out
    assert "--env-file" in captured.out


def test_format_command_quotes_description_with_comma_and_spaces() -> None:
    """Displayed commands remain copy/paste-safe for descriptive text."""
    command = [
        "oci",
        "generative-ai",
        "hosted-application",
        "create",
        "--description",
        "Agent application, test 01",
    ]

    formatted = format_command(command)

    assert "--description 'Agent application, test 01'" in formatted


def _valid_yaml(tmp_path) -> str:
    """Return a minimal valid deployment YAML."""
    return f"""
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
  ocir_namespace: auto

hosted_application:
  display_name: demo-agent
  description: Demo agent
  create_if_missing: true
  update_if_exists: false
  scaling:
    min_instances: 1
    max_instances: 2
    metric: cpu
    threshold: 70
  networking:
    mode: public
  security:
    auth_type: IDCS_AUTH_CONFIG
    issuer_url: https://issuer.example.com
    audience: demo-agent
    scopes:
      - demo-agent/.default
  environment:
    variables:
      LOG_LEVEL: INFO
      MCP_SERVER_PORT: "8080"
    secrets:
      API_KEY:
        source: local_env
        env_name: MY_AGENT_API_KEY

hosted_deployment:
  display_name: demo-agent-deployment
  create_new_version: true
  activate: true
  wait_for_state: SUCCEEDED
"""


def _no_auth_yaml(tmp_path) -> str:
    """Return a minimal valid deployment YAML with explicit NO_AUTH."""
    return f"""
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
  ocir_namespace: auto

hosted_application:
  display_name: demo-agent
  security:
    auth_type: NO_AUTH

hosted_deployment:
  display_name: demo-agent-deployment
  create_new_version: true
  activate: true
  wait_for_state: SUCCEEDED
"""


def _valid_solution_yaml(tmp_path) -> str:
    """Return a minimal valid Enterprise Solution YAML."""
    return f"""
enterprise_solution:
  name: demo-solution
  compartment_id: ocid1.compartment.oc1..example
  region: eu-frankfurt-1
  region_key: fra

deployments:
  - name: agent-api
    container:
      context: {tmp_path}
      dockerfile: Dockerfile
      image_repository: ai-agents/agent-api
      tag_strategy: explicit
      tag: "20260429"
      ocir_namespace: auto
    hosted_application:
      display_name: agent-api
      description: Agent API
      create_if_missing: true
      update_if_exists: false
      scaling:
        min_instances: 1
        max_instances: 2
        metric: cpu
        threshold: 70
      networking:
        mode: public
      security:
        auth_type: IDCS_AUTH_CONFIG
        issuer_url: https://issuer.example.com
        audience: agent-api
        scopes:
          - agent-api/.default
      environment:
        variables:
          LOG_LEVEL: INFO
        secrets:
          API_KEY:
            source: local_env
            env_name: MY_AGENT_API_KEY
    hosted_deployment:
      display_name: agent-api-deployment
      create_new_version: true
      activate: true
      wait_for_state: SUCCEEDED

  - name: mcp-server
    container:
      context: {tmp_path}
      dockerfile: Dockerfile
      image_repository: ai-agents/mcp-server
      tag_strategy: explicit
      tag: "20260429"
      ocir_namespace: auto
    hosted_application:
      display_name: mcp-server
      description: MCP Server
      create_if_missing: true
      update_if_exists: false
      networking:
        mode: public
      security:
        auth_type: NO_AUTH
      environment:
        variables:
          LOG_LEVEL: INFO
    hosted_deployment:
      display_name: mcp-server-deployment
      create_new_version: true
      activate: true
      wait_for_state: SUCCEEDED
"""
