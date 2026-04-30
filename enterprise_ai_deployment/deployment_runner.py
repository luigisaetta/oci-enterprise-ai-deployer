"""
Author: L. Saetta
Last modified: 2026-04-29
License: MIT

Description:
    Non-interactive orchestration for the OCI Enterprise AI deployment CLI.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from enterprise_ai_deployment.cli_commands import (
    HostedApplicationCreateRequest,
    HostedApplicationJsonOptions,
    HostedDeploymentCreateRequest,
    build_create_hosted_application_command,
    build_create_hosted_deployment_command,
    build_list_hosted_applications_command,
)
from enterprise_ai_deployment.config import OciCliConfig
from enterprise_ai_deployment.deployment_config import (
    DeploymentConfig,
    DeploymentConfigError,
    load_deployment_config,
)
from enterprise_ai_deployment.deployment_renderer import (
    RenderedArtifacts,
    render_artifacts,
)
from enterprise_ai_deployment.deployment_validation import (
    DeploymentValidationError,
    validate_deployment_config,
)
from enterprise_ai_deployment.ocir import ImageReference, build_image_reference


@dataclass(frozen=True)
class DeploymentContext:
    """Resolved deployment inputs shared by command handlers."""

    config: DeploymentConfig
    image_reference: ImageReference
    artifacts: RenderedArtifacts | None = None


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive deployment CLI parser."""
    parser = argparse.ArgumentParser(
        description="Deploy OCI Enterprise AI Hosted Applications from YAML."
    )
    parser.add_argument("--config", required=True, help="Path to deployment YAML.")
    parser.add_argument(
        "--env-file", help="Optional local .env file with secret references."
    )
    parser.add_argument("--output-dir", default="enterprise_ai_deployment/generated")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    subparsers = parser.add_subparsers(dest="command", required=True)
    for command_name in (
        "validate",
        "render",
        "build",
        "push",
        "create-application",
        "deploy",
    ):
        subparsers.add_parser(command_name)
    create_deployment = subparsers.add_parser("create-deployment")
    create_deployment.add_argument(
        "--hosted-application-id",
        help="Existing Hosted Application OCID to attach the deployment to.",
    )
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--to-tag", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the deployment CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_command(args)
    except (DeploymentConfigError, DeploymentValidationError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1


def run_command(args: argparse.Namespace) -> int:
    """Dispatch one parsed CLI command."""
    context = _prepare_context(
        args,
        render=args.command
        in {"render", "create-application", "create-deployment", "deploy"},
    )

    if args.command == "validate":
        print("Configuration is valid.")
    elif args.command == "render":
        _print_rendered(context.artifacts)
    elif args.command == "build":
        build_container_image(context, args)
    elif args.command == "push":
        push_container_image(context, args)
    elif args.command == "create-application":
        create_hosted_application(context, args)
    elif args.command == "create-deployment":
        _run_create_deployment_command(context, args)
    elif args.command == "deploy":
        _run_deploy_command(context, args)
    else:
        print(f"Command {args.command!r} is not implemented in this first task.")
    return 0


def _run_create_deployment_command(
    context: DeploymentContext, args: argparse.Namespace
) -> None:
    """Run standalone Hosted Deployment creation."""
    hosted_application_id = args.hosted_application_id
    if not hosted_application_id:
        raise RuntimeError(
            "create-deployment requires --hosted-application-id. "
            "Use deploy to create the application and deployment together."
        )
    create_hosted_deployment(context, args, hosted_application_id)


def _run_deploy_command(context: DeploymentContext, args: argparse.Namespace) -> None:
    """Run the complete deploy flow."""
    _print_plan(context)
    build_container_image(context, args)
    push_container_image(context, args)
    if args.dry_run:
        create_hosted_application(context, args)
        create_hosted_deployment(
            context,
            args,
            hosted_application_id="<created-hosted-application-id>",
        )
        print("Dry run: no OCI commands were executed.")
        return
    hosted_application_id = create_hosted_application(context, args)
    create_hosted_deployment(context, args, hosted_application_id)


def build_container_image(context: DeploymentContext, args: argparse.Namespace) -> None:
    """Build the Docker image that will be used by the Hosted Deployment."""
    command = build_docker_build_command(context)
    _print_oci_command("Build Container Image", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return
    _run_process_command(
        command,
        "build-container",
        "Verify Docker is running and the Dockerfile path is correct.",
    )


def build_docker_build_command(context: DeploymentContext) -> list[str]:
    """Build the Docker image build command from deployment configuration."""
    return [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-f",
        str(_resolve_dockerfile_path(context.config)),
        "-t",
        context.image_reference.image_uri,
        str(_resolve_container_context_path(context.config)),
    ]


def push_container_image(context: DeploymentContext, args: argparse.Namespace) -> None:
    """Push the Docker image to OCIR."""
    command = build_docker_push_command(context)
    _print_oci_command("Push Container Image", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return
    _run_process_command(
        command,
        "push-container",
        "Verify Docker is logged in to OCIR and the repository exists or can be created.",
    )


def build_docker_push_command(context: DeploymentContext) -> list[str]:
    """Build the Docker image push command."""
    return ["docker", "push", context.image_reference.image_uri]


def create_hosted_application(
    context: DeploymentContext, args: argparse.Namespace
) -> str:
    """Create the Hosted Application through OCI CLI, or print it in dry-run mode."""
    if context.artifacts is None:
        raise RuntimeError(
            "Artifacts must be rendered before creating a Hosted Application."
        )
    config = context.config
    if not args.dry_run:
        existing_hosted_application_id = _find_hosted_application_id_by_name(
            OciCliConfig(region=config.application.region),
            config.application.compartment_id,
            config.hosted_application.display_name,
        )
        if existing_hosted_application_id:
            print(
                "Using existing Hosted Application "
                f"{config.hosted_application.display_name!r}: "
                f"{existing_hosted_application_id}"
            )
            return existing_hosted_application_id

    artifacts = context.artifacts
    command = build_create_hosted_application_command(
        OciCliConfig(region=config.application.region),
        HostedApplicationCreateRequest(
            display_name=config.hosted_application.display_name,
            compartment_id=config.application.compartment_id,
            description=config.hosted_application.description,
            json_options=HostedApplicationJsonOptions(
                scaling_config=(
                    str(artifacts.scaling_config) if artifacts.scaling_config else None
                ),
                inbound_auth_config=(
                    str(artifacts.inbound_auth_config)
                    if artifacts.inbound_auth_config
                    else None
                ),
                networking_config=(
                    str(artifacts.networking_config)
                    if artifacts.networking_config
                    else None
                ),
                environment_variables=(
                    str(artifacts.environment_variables)
                    if artifacts.environment_variables
                    else None
                ),
            ),
            wait=config.hosted_deployment.wait_for_state is not None,
        ),
    )
    _print_oci_command("Create Hosted Application", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return "<created-hosted-application-id>"

    result = _run_oci_command(command, "create-application")
    hosted_application_id = _extract_resource_id(result.stdout)
    if not hosted_application_id:
        raise RuntimeError(
            "create-application succeeded but no Hosted Application OCID was found "
            "in the OCI CLI response."
        )
    return hosted_application_id


def create_hosted_deployment(
    context: DeploymentContext,
    args: argparse.Namespace,
    hosted_application_id: str,
) -> str:
    """Create the Hosted Deployment through OCI CLI, or print it in dry-run mode."""
    config = context.config
    command = build_create_hosted_deployment_command(
        OciCliConfig(region=config.application.region),
        HostedDeploymentCreateRequest(
            hosted_application_id=hosted_application_id,
            display_name=config.hosted_deployment.display_name,
            compartment_id=config.application.compartment_id,
            container_uri=context.image_reference.container_uri,
            artifact_tag=context.image_reference.tag,
            wait=config.hosted_deployment.wait_for_state is not None,
        ),
    )
    _print_oci_command("Create Hosted Deployment", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return "<created-hosted-deployment-id>"

    result = _run_oci_command(command, "create-deployment")
    return _extract_resource_id(result.stdout) or ""


def _prepare_context(args: argparse.Namespace, render: bool) -> DeploymentContext:
    """Load, validate, resolve image reference, and optionally render artifacts."""
    config = load_deployment_config(args.config, env_file=args.env_file)
    validate_deployment_config(config)
    namespace = (
        _resolve_ocir_namespace(OciCliConfig(region=config.application.region))
        if _needs_runtime_image_reference(args)
        and config.container.ocir_namespace == "auto"
        else None
    )
    image_reference = build_image_reference(config, namespace=namespace)
    artifacts = (
        render_artifacts(config, image_reference, args.output_dir) if render else None
    )
    return DeploymentContext(
        config=config, image_reference=image_reference, artifacts=artifacts
    )


def _print_rendered(artifacts: RenderedArtifacts | None) -> None:
    """Print generated artifact paths."""
    if artifacts is None:
        return
    print("Generated OCI CLI JSON artifacts:")
    for path in _artifact_paths(artifacts):
        print(f"- {path}")


def _print_plan(context: DeploymentContext) -> None:
    """Print a concise deployment plan."""
    print("Deployment plan:")
    print(f"- application: {context.config.hosted_application.display_name}")
    print(f"- compartment: {context.config.application.compartment_id}")
    print(f"- image: {context.image_reference.image_uri}")
    _print_rendered(context.artifacts)


def _print_oci_command(title: str, command: list[str]) -> None:
    """Print one OCI CLI command with copy-friendly spacing."""
    print()
    print(f"OCI CLI command: {title}\n")
    print(format_command(command))
    print()


def _artifact_paths(artifacts: RenderedArtifacts) -> list[Path]:
    """Return non-empty artifact paths in a stable order."""
    return [
        path
        for path in (
            artifacts.hosted_application_create,
            artifacts.hosted_deployment_create,
            artifacts.scaling_config,
            artifacts.inbound_auth_config,
            artifacts.networking_config,
            artifacts.environment_variables,
            artifacts.active_artifact,
        )
        if path is not None
    ]


def format_command(command: list[str]) -> str:
    """Return a shell-safe display form for a command argument list."""
    return shlex.join(command)


def _run_oci_command(
    command: list[str], phase: str
) -> subprocess.CompletedProcess[str]:
    """Run one OCI CLI command and print captured output."""
    return _run_process_command(
        command,
        phase,
        "Verify OCI CLI configuration, IAM policies, compartment, and generated JSON payloads.",
        pretty_json_stdout=True,
    )


def _run_process_command(
    command: list[str],
    phase: str,
    failure_hint: str,
    pretty_json_stdout: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run one subprocess command and print captured output."""
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.stdout:
        print(
            _pretty_json(result.stdout) if pretty_json_stdout else result.stdout.strip()
        )
    if result.stderr:
        print(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(f"{phase} failed. {failure_hint}")
    return result


def _needs_runtime_image_reference(args: argparse.Namespace) -> bool:
    """Return True when the command needs a Docker/OCIR image URI to execute."""
    if args.command == "create-deployment" and not args.hosted_application_id:
        return False
    return not args.dry_run and args.command in {
        "build",
        "push",
        "create-deployment",
        "deploy",
    }


def _resolve_ocir_namespace(cli_config: OciCliConfig) -> str:
    """Resolve the current tenancy namespace through OCI CLI."""
    command = _build_get_ocir_namespace_command(cli_config)
    _print_oci_command("Resolve OCIR Namespace", command)
    result = _run_oci_command(command, "resolve-ocir-namespace")
    namespace = _extract_namespace(result.stdout)
    if not namespace:
        raise RuntimeError(
            "resolve-ocir-namespace succeeded but no namespace was found "
            "in the OCI CLI response."
        )
    return namespace


def _build_get_ocir_namespace_command(config: OciCliConfig) -> list[str]:
    """Build the OCI CLI command that returns the Object Storage namespace."""
    command = ["oci"]
    if config.profile:
        command.extend(["--profile", config.profile])
    if config.region:
        command.extend(["--region", config.region])
    command.extend(["--output", "json", "os", "ns", "get"])
    return command


def _extract_namespace(stdout: str) -> str | None:
    """Extract an OCIR/Object Storage namespace from OCI CLI output."""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        value = stdout.strip()
        return value or None
    if isinstance(payload, str):
        return payload.strip() or None
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, str) and data.strip():
        return data.strip()
    if isinstance(data, dict):
        return _first_string(data, "namespace", "name", "value")
    return _first_string(payload, "namespace", "name", "value")


def _resolve_container_context_path(config: DeploymentConfig) -> Path:
    """Resolve the Docker build context relative to the YAML location."""
    context_path = Path(config.container.context).expanduser()
    if context_path.is_absolute():
        return context_path
    return (config.source_path.parent / context_path).resolve()


def _resolve_dockerfile_path(config: DeploymentConfig) -> Path:
    """Resolve the Dockerfile path relative to the build context."""
    dockerfile_path = Path(config.container.dockerfile).expanduser()
    if dockerfile_path.is_absolute():
        return dockerfile_path
    return (_resolve_container_context_path(config) / dockerfile_path).resolve()


def _find_hosted_application_id_by_name(
    cli_config: OciCliConfig, compartment_id: str, display_name: str
) -> str | None:
    """Return an existing Hosted Application OCID with the requested display name."""
    command = build_list_hosted_applications_command(cli_config, compartment_id)
    _print_oci_command("Find Existing Hosted Application", command)
    result = _run_oci_command(command, "list-hosted-applications")
    items = _extract_list_items(result.stdout)
    matches = [
        item
        for item in items
        if _first_string(item, "display-name", "displayName", "name") == display_name
        and _first_string(item, "id")
        and not _is_deleted_hosted_application(item)
    ]
    if len(matches) > 1:
        print(
            "Warning: multiple Hosted Applications found with display name "
            f"{display_name!r}; using the first returned by OCI."
        )
    if not matches:
        return None
    return _first_string(matches[0], "id")


def _extract_list_items(stdout: str) -> list[dict[str, object]]:
    """Extract OCI CLI list items from common response shapes."""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        items = data.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    return []


def _first_string(item: dict[str, object], *keys: str) -> str:
    """Return the first non-empty string value for the given keys."""
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _is_deleted_hosted_application(item: dict[str, object]) -> bool:
    """Return True when a listed Hosted Application is already deleted."""
    lifecycle_state = _first_string(item, "lifecycle-state", "lifecycleState").upper()
    return lifecycle_state in {"DELETED", "DELETING"}


def _extract_resource_id(stdout: str) -> str | None:
    """Extract a resource OCID from common OCI CLI response shapes."""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    return _find_ocid(payload)


def _find_ocid(value: object) -> str | None:
    """Find the first OCI resource identifier in a nested response payload."""
    if isinstance(value, str) and value.startswith("ocid1."):
        return value
    if isinstance(value, dict):
        for key in ("id", "identifier", "resourceId", "resource-id"):
            found = _find_ocid(value.get(key))
            if found:
                return found
        for child in value.values():
            found = _find_ocid(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_ocid(child)
            if found:
                return found
    return None


def _pretty_json(text: str) -> str:
    """Pretty-print JSON output when possible."""
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=True)
    except json.JSONDecodeError:
        return text.strip()
