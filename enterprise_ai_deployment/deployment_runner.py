"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-05
License: MIT

Description:
    Non-interactive orchestration for the OCI Enterprise AI deployment CLI.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from enterprise_ai_deployment.cli_commands import (
    HostedApplicationCreateRequest,
    HostedApplicationJsonOptions,
    HostedDeploymentCreateRequest,
    build_create_container_repository_command,
    build_create_hosted_application_command,
    build_create_hosted_deployment_command,
    build_list_container_repositories_command,
    build_list_hosted_applications_command,
)
from enterprise_ai_deployment.compartments import resolve_deployment_config_compartment
from enterprise_ai_deployment.config import OciCliConfig
from enterprise_ai_deployment.deployment_config import (
    DeploymentConfig,
    DeploymentConfigError,
    DeploymentUnitConfig,
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
from enterprise_ai_deployment.ocir import (
    ImageReference,
    build_image_reference,
    build_ocir_registry,
    require_docker_login,
)


@dataclass(frozen=True)
class DeploymentContext:
    """Resolved deployment inputs shared by command handlers."""

    config: DeploymentConfig
    deployments: tuple["DeploymentExecutionContext", ...]

    @property
    def first(self) -> "DeploymentExecutionContext":
        """Return the first deployment for legacy single-deployment commands."""
        return self.deployments[0]


@dataclass(frozen=True)
class DeploymentExecutionContext:
    """Resolved inputs for one serial deployment."""

    deployment: DeploymentUnitConfig
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
    parser.add_argument("--profile", help="OCI CLI profile used for OCI lookups.")
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
        _add_common_subcommand_options(subparsers.add_parser(command_name))
    create_deployment = subparsers.add_parser("create-deployment")
    _add_common_subcommand_options(create_deployment)
    create_deployment.add_argument(
        "--hosted-application-id",
        help="Existing Hosted Application OCID to attach the deployment to.",
    )
    rollback = subparsers.add_parser("rollback")
    _add_common_subcommand_options(rollback)
    rollback.add_argument("--to-tag", required=True)
    return parser


def _add_common_subcommand_options(parser: argparse.ArgumentParser) -> None:
    """Accept common global flags after the subcommand as a convenience."""
    parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument(
        "--non-interactive", action="store_true", default=argparse.SUPPRESS
    )
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)


def main(argv: list[str] | None = None) -> int:
    """Run the deployment CLI."""
    _configure_streaming_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_command(args)
    except (DeploymentConfigError, DeploymentValidationError, RuntimeError) as exc:
        print(f"Error: {exc}")
        return 1


def _configure_streaming_output() -> None:
    """Prefer line-buffered output when the CLI is streamed by the web API."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except AttributeError:
        return


def run_command(args: argparse.Namespace) -> int:
    """Dispatch one parsed CLI command."""
    context = _prepare_context(
        args,
        render=args.command
        in {"render", "create-application", "create-deployment", "deploy"},
    )

    if args.command == "validate":
        _validate_local_prerequisites(context)
        print("Configuration is valid.")
    elif args.command == "render":
        _print_all_rendered(context)
    elif args.command == "build":
        for deployment_context in context.deployments:
            build_container_image(context, args, deployment_context)
    elif args.command == "push":
        for deployment_context in context.deployments:
            push_container_image(context, args, deployment_context)
    elif args.command == "create-application":
        for deployment_context in context.deployments:
            create_hosted_application(context, args, deployment_context)
    elif args.command == "create-deployment":
        _run_create_deployment_command(context, args)
    elif args.command == "deploy":
        _run_deploy_command(context, args)
    elif args.command == "rollback":
        _run_rollback_command(context, args)
    else:
        print(f"Command {args.command!r} is not implemented in this first task.")
    return 0


def _run_create_deployment_command(
    context: DeploymentContext, args: argparse.Namespace
) -> None:
    """Run standalone Hosted Deployment creation."""
    if len(context.deployments) > 1:
        raise RuntimeError(
            "create-deployment with --hosted-application-id is only supported "
            "for single-deployment YAML. Use deploy for Enterprise Solutions."
        )
    hosted_application_id = args.hosted_application_id
    if not hosted_application_id:
        raise RuntimeError(
            "create-deployment requires --hosted-application-id. "
            "Use deploy to create the application and deployment together."
        )
    create_hosted_deployment(context, args, hosted_application_id=hosted_application_id)


def _run_deploy_command(context: DeploymentContext, args: argparse.Namespace) -> None:
    """Run the complete deploy flow."""
    _print_plan(context)
    for deployment_context in context.deployments:
        _run_single_deployment(context, deployment_context, args)
    if args.dry_run:
        print("Dry run: no OCI commands were executed.")


def _run_rollback_command(context: DeploymentContext, args: argparse.Namespace) -> None:
    """Create new Hosted Deployments that point to a previous immutable tag."""
    _print_rollback_plan(context, args.to_tag)
    for deployment_context in context.deployments:
        _run_single_rollback(context, deployment_context, args)
    if args.dry_run:
        print("Dry run: no OCI commands were executed.")


def _run_single_rollback(
    context: DeploymentContext,
    deployment_context: DeploymentExecutionContext,
    args: argparse.Namespace,
) -> None:
    """Rollback one deployment and stop the solution on the first failure."""
    print()
    print(f"Starting rollback: {deployment_context.deployment.name}")
    try:
        if args.dry_run:
            create_hosted_deployment(
                context,
                args,
                hosted_application_id="<existing-hosted-application-id>",
                deployment_context=deployment_context,
            )
            return
        hosted_application_id = _find_hosted_application_id_by_name(
            _context_oci_cli_config(context, args),
            context.config.application.compartment_id,
            deployment_context.deployment.hosted_application.display_name,
        )
        if not hosted_application_id:
            raise RuntimeError(
                "Hosted Application "
                f"{deployment_context.deployment.hosted_application.display_name!r} "
                "was not found. Rollback requires an existing Hosted Application."
            )
        create_hosted_deployment(
            context,
            args,
            hosted_application_id,
            deployment_context=deployment_context,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "Rollback failed for Enterprise Solution "
            f"{context.config.application.name!r}, deployment "
            f"{deployment_context.deployment.name!r}: {exc}"
        ) from exc


def _run_single_deployment(
    context: DeploymentContext,
    deployment_context: DeploymentExecutionContext,
    args: argparse.Namespace,
) -> None:
    """Run one deployment and stop the whole solution on the first failure."""
    print()
    print(f"Starting deployment: {deployment_context.deployment.name}")
    try:
        build_container_image(context, args, deployment_context)
        push_container_image(context, args, deployment_context)
        if args.dry_run:
            create_hosted_application(context, args, deployment_context)
            create_hosted_deployment(
                context,
                args,
                hosted_application_id="<created-hosted-application-id>",
                deployment_context=deployment_context,
            )
            return
        hosted_application_id = create_hosted_application(
            context, args, deployment_context
        )
        create_hosted_deployment(
            context,
            args,
            hosted_application_id,
            deployment_context=deployment_context,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            "Deployment failed for Enterprise Solution "
            f"{context.config.application.name!r}, deployment "
            f"{deployment_context.deployment.name!r}: {exc}"
        ) from exc


def build_container_image(
    context: DeploymentContext,
    args: argparse.Namespace,
    deployment_context: DeploymentExecutionContext | None = None,
) -> None:
    """Build the Docker image that will be used by the Hosted Deployment."""
    deployment_context = deployment_context or context.first
    command = build_docker_build_command(context, deployment_context)
    _print_oci_command("Build Container Image", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return
    _run_process_command(
        command,
        "build-container",
        "Verify Docker is running and the Dockerfile path is correct.",
    )


def build_docker_build_command(
    context: DeploymentContext,
    deployment_context: DeploymentExecutionContext | None = None,
) -> list[str]:
    """Build the Docker image build command from deployment configuration."""
    deployment_context = deployment_context or context.first
    return [
        "docker",
        "build",
        "--platform",
        "linux/amd64",
        "-f",
        str(_resolve_dockerfile_path(context.config, deployment_context.deployment)),
        "-t",
        deployment_context.image_reference.image_uri,
        str(
            _resolve_container_context_path(
                context.config, deployment_context.deployment
            )
        ),
    ]


def push_container_image(
    context: DeploymentContext,
    args: argparse.Namespace,
    deployment_context: DeploymentExecutionContext | None = None,
) -> None:
    """Push the Docker image to OCIR."""
    deployment_context = deployment_context or context.first
    ensure_ocir_repository(context, args, deployment_context)
    command = build_docker_push_command(deployment_context)
    _print_oci_command("Push Container Image", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return
    _run_process_command(
        command,
        "push-container",
        "Verify Docker is logged in to OCIR and the repository exists or can be created.",
    )


def build_docker_push_command(context: DeploymentExecutionContext) -> list[str]:
    """Build the Docker image push command."""
    return ["docker", "push", context.image_reference.image_uri]


def ensure_ocir_repository(
    context: DeploymentContext,
    args: argparse.Namespace,
    deployment_context: DeploymentExecutionContext | None = None,
) -> None:
    """Ensure the target OCIR repository exists before pushing an image."""
    deployment_context = deployment_context or context.first
    repository_name = build_ocir_repository_name(deployment_context.deployment)
    cli_config = _context_oci_cli_config(context, args)
    create_command = build_create_container_repository_command(
        cli_config,
        context.config.application.compartment_id,
        repository_name,
    )
    if args.dry_run:
        _print_oci_command("Ensure OCIR Repository", create_command)
        print("Dry run: command not executed.")
        return

    if _find_ocir_repository_id(
        cli_config,
        context.config.application.compartment_id,
        repository_name,
    ):
        print(f"Using existing OCIR repository {repository_name!r}.")
        return

    _print_oci_command("Create OCIR Repository", create_command)
    _run_oci_command(create_command, "create-ocir-repository")


def build_ocir_repository_name(deployment: DeploymentUnitConfig) -> str:
    """Return the OCIR repository display name for a deployment image."""
    return deployment.container.image_repository


def create_hosted_application(
    context: DeploymentContext,
    args: argparse.Namespace,
    deployment_context: DeploymentExecutionContext | None = None,
) -> str:
    """Create the Hosted Application through OCI CLI, or print it in dry-run mode."""
    deployment_context = deployment_context or context.first
    if deployment_context.artifacts is None:
        raise RuntimeError(
            "Artifacts must be rendered before creating a Hosted Application."
        )
    config = context.config
    deployment = deployment_context.deployment
    if not args.dry_run:
        existing_hosted_application_id = _find_hosted_application_id_by_name(
            _context_oci_cli_config(context, args),
            config.application.compartment_id,
            deployment.hosted_application.display_name,
        )
        if existing_hosted_application_id:
            print(
                "Using existing Hosted Application "
                f"{deployment.hosted_application.display_name!r}: "
                f"{existing_hosted_application_id}"
            )
            return existing_hosted_application_id

    artifacts = deployment_context.artifacts
    command = build_create_hosted_application_command(
        _context_oci_cli_config(context, args),
        HostedApplicationCreateRequest(
            display_name=deployment.hosted_application.display_name,
            compartment_id=config.application.compartment_id,
            description=deployment.hosted_application.description,
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
            wait=deployment.hosted_deployment.wait_for_state is not None,
        ),
    )
    _print_oci_command("Create Hosted Application", command)
    if args.dry_run:
        print("Dry run: command not executed.")
        return "<created-hosted-application-id>"

    result = _run_oci_command(command, "create-application")
    hosted_application_id = _extract_created_resource_identifier(
        result.stdout,
        ocid_prefix="ocid1.generativeaihostedapplication.",
    )
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
    deployment_context: DeploymentExecutionContext | None = None,
) -> str:
    """Create the Hosted Deployment through OCI CLI, or print it in dry-run mode."""
    config = context.config
    deployment_context = deployment_context or context.first
    deployment = deployment_context.deployment
    command = build_create_hosted_deployment_command(
        _context_oci_cli_config(context, args),
        HostedDeploymentCreateRequest(
            hosted_application_id=hosted_application_id,
            display_name=deployment.hosted_deployment.display_name,
            compartment_id=config.application.compartment_id,
            container_uri=deployment_context.image_reference.container_uri,
            artifact_tag=deployment_context.image_reference.tag,
            wait=deployment.hosted_deployment.wait_for_state is not None,
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
    cli_config = _build_oci_cli_config(config, args)
    config = resolve_deployment_config_compartment(config, cli_config)
    validate_deployment_config(config)
    namespace = (
        _resolve_ocir_namespace(cli_config)
        if _needs_runtime_image_reference(args)
        and any(
            deployment.container.ocir_namespace == "auto"
            for deployment in config.deployments
        )
        else None
    )
    deployments = tuple(
        _prepare_deployment_context(config, deployment, namespace, args, render)
        for deployment in config.deployments
    )
    return DeploymentContext(config=config, deployments=deployments)


def _build_oci_cli_config(
    config: DeploymentConfig, args: argparse.Namespace
) -> OciCliConfig:
    """Build OCI CLI options from loaded config and global CLI flags."""
    return OciCliConfig(profile=args.profile, region=config.application.region)


def _context_oci_cli_config(
    context: DeploymentContext, args: argparse.Namespace
) -> OciCliConfig:
    """Build OCI CLI options from an already prepared deployment context."""
    return OciCliConfig(
        profile=args.profile,
        region=context.config.application.region,
    )


def _validate_local_prerequisites(context: DeploymentContext) -> None:
    """Validate local prerequisites that can be checked without remote changes."""
    registry = build_ocir_registry(context.config.application.region_key)
    require_docker_login(registry)


def _prepare_deployment_context(
    config: DeploymentConfig,
    deployment: DeploymentUnitConfig,
    namespace: str | None,
    args: argparse.Namespace,
    render: bool,
) -> DeploymentExecutionContext:
    """Resolve image and optionally render artifacts for one deployment."""
    image_reference = build_image_reference(
        config,
        namespace=namespace,
        deployment=deployment,
        tag_override=args.to_tag if args.command == "rollback" else None,
    )
    artifacts = (
        render_artifacts(
            config,
            image_reference,
            _deployment_output_dir(args.output_dir, config, deployment),
            deployment=deployment,
        )
        if render
        else None
    )
    return DeploymentExecutionContext(
        deployment=deployment,
        image_reference=image_reference,
        artifacts=artifacts,
    )


def _deployment_output_dir(
    output_dir: str | Path,
    config: DeploymentConfig,
    deployment: DeploymentUnitConfig,
) -> Path:
    """Return the artifact directory for one deployment."""
    base_dir = Path(output_dir).expanduser()
    if len(config.deployments) == 1:
        return base_dir
    return base_dir / deployment.name


def _print_all_rendered(context: DeploymentContext) -> None:
    """Print generated artifact paths for all deployments."""
    for deployment_context in context.deployments:
        if len(context.deployments) > 1:
            print(f"Deployment {deployment_context.deployment.name}:")
        _print_rendered(deployment_context.artifacts)


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
    print(f"- enterprise solution: {context.config.application.name}")
    print(f"- compartment: {context.config.application.compartment_id}")
    print(f"- region: {context.config.application.region}")
    print(f"- deployments: {len(context.deployments)}")
    for deployment_context in context.deployments:
        print(
            f"- deployment: {deployment_context.deployment.name} "
            f"({deployment_context.image_reference.image_uri})"
        )
        _print_rendered(deployment_context.artifacts)


def _print_rollback_plan(context: DeploymentContext, tag: str) -> None:
    """Print a concise rollback plan."""
    print("Rollback plan:")
    print(f"- enterprise solution: {context.config.application.name}")
    print(f"- compartment: {context.config.application.compartment_id}")
    print(f"- region: {context.config.application.region}")
    print(f"- rollback tag: {tag}")
    print(f"- deployments: {len(context.deployments)}")
    for deployment_context in context.deployments:
        print(
            f"- deployment: {deployment_context.deployment.name} "
            f"({deployment_context.image_reference.image_uri})"
        )


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
        "rollback",
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


def _resolve_container_context_path(
    config: DeploymentConfig,
    deployment: DeploymentUnitConfig | None = None,
) -> Path:
    """Resolve the Docker build context relative to the YAML location."""
    deployment = deployment or config.deployments[0]
    context_path = Path(deployment.container.context).expanduser()
    if context_path.is_absolute():
        return context_path
    return (config.source_path.parent / context_path).resolve()


def _resolve_dockerfile_path(
    config: DeploymentConfig,
    deployment: DeploymentUnitConfig | None = None,
) -> Path:
    """Resolve the Dockerfile path relative to the build context."""
    deployment = deployment or config.deployments[0]
    dockerfile_path = Path(deployment.container.dockerfile).expanduser()
    if dockerfile_path.is_absolute():
        return dockerfile_path
    return (
        _resolve_container_context_path(config, deployment) / dockerfile_path
    ).resolve()


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


def _find_ocir_repository_id(
    cli_config: OciCliConfig, compartment_id: str, display_name: str
) -> str | None:
    """Return an existing OCIR repository OCID with the requested display name."""
    command = build_list_container_repositories_command(
        cli_config,
        compartment_id,
        display_name,
    )
    _print_oci_command("Find Existing OCIR Repository", command)
    result = _run_oci_command(command, "list-ocir-repositories")
    items = _extract_list_items(result.stdout)
    matches = [
        item
        for item in items
        if _first_string(item, "display-name", "displayName", "name") == display_name
        and _first_string(item, "id")
        and _first_string(item, "lifecycle-state", "lifecycleState").upper()
        not in {"DELETED", "DELETING"}
    ]
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


def _extract_created_resource_identifier(
    stdout: str, ocid_prefix: str | None = None
) -> str | None:
    """Extract the created resource OCID, preferring OCI identifier fields."""
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError:
        return None
    return _find_ocid(
        payload,
        preferred_keys=("identifier", "resourceId", "resource-id"),
        ocid_prefix=ocid_prefix,
    )


def _find_ocid(
    value: object,
    preferred_keys: tuple[str, ...] = ("id", "identifier", "resourceId", "resource-id"),
    ocid_prefix: str | None = None,
) -> str | None:
    """Find the first OCI resource identifier in a nested response payload."""
    if isinstance(value, str) and value.startswith(ocid_prefix or "ocid1."):
        return value
    if isinstance(value, dict):
        for key in preferred_keys:
            found = _find_ocid(
                value.get(key),
                preferred_keys=preferred_keys,
                ocid_prefix=ocid_prefix,
            )
            if found:
                return found
        for child in value.values():
            found = _find_ocid(
                child,
                preferred_keys=preferred_keys,
                ocid_prefix=ocid_prefix,
            )
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_ocid(
                child,
                preferred_keys=preferred_keys,
                ocid_prefix=ocid_prefix,
            )
            if found:
                return found
    return None


def _pretty_json(text: str) -> str:
    """Pretty-print JSON output when possible."""
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=True)
    except json.JSONDecodeError:
        return text.strip()
