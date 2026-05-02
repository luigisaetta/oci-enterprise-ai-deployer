"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-02
License: MIT

Description:
    OCIR image tag and URI helpers for Enterprise AI deployments.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from enterprise_ai_deployment.deployment_config import (
    DeploymentConfig,
    DeploymentUnitConfig,
)


@dataclass(frozen=True)
class ImageReference:
    """Calculated image reference values."""

    container_uri: str
    tag: str

    @property
    def image_uri(self) -> str:
        """Return the complete image URI including tag."""
        return f"{self.container_uri}:{self.tag}"


def build_ocir_registry(region_key: str) -> str:
    """Return the OCIR registry hostname for an OCI region key."""
    return f"{region_key}.ocir.io"


def docker_login_exists(registry: str, docker_config_dir: Path | None = None) -> bool:
    """Return True when Docker config contains credentials for a registry."""
    config_path = _docker_config_path(docker_config_dir)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(config, dict):
        return False

    auths = config.get("auths", {})
    if isinstance(auths, dict) and _registry_in_auths(registry, auths):
        return True

    credential_helpers = config.get("credHelpers", {})
    if isinstance(credential_helpers, dict) and registry in credential_helpers:
        return True

    return False


def require_docker_login(registry: str) -> None:
    """Raise a readable error when Docker is not logged in to a registry."""
    if docker_login_exists(registry):
        return
    raise RuntimeError(
        "Docker is not logged in to the target OCIR registry "
        f"{registry!r}. Run 'docker login {registry}' before push or deploy."
    )


def build_image_reference(
    config: DeploymentConfig,
    namespace: str | None = None,
    deployment: DeploymentUnitConfig | None = None,
) -> ImageReference:
    """Build the OCIR image URI and tag from deployment configuration."""
    deployment_config = deployment or config.deployments[0]
    container = deployment_config.container
    resolved_namespace = namespace or container.ocir_namespace
    if resolved_namespace == "auto":
        resolved_namespace = "<resolved-ocir-namespace>"
    tag = resolve_image_tag(config, deployment=deployment_config)
    container_uri = (
        f"{build_ocir_registry(config.application.region_key)}/"
        f"{resolved_namespace}/{container.image_repository}"
    )
    return ImageReference(container_uri=container_uri, tag=tag)


def resolve_image_tag(
    config: DeploymentConfig, deployment: DeploymentUnitConfig | None = None
) -> str:
    """Resolve the Docker image tag strategy."""
    container = (deployment or config.deployments[0]).container
    if container.tag:
        return container.tag
    if container.tag_strategy == "timestamp":
        return datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    if container.tag_strategy == "git_sha":
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        return "unknown-git-sha"
    return container.tag_strategy


def _docker_config_path(docker_config_dir: Path | None = None) -> Path:
    """Return the Docker CLI config path to inspect for registry credentials."""
    if docker_config_dir is not None:
        return docker_config_dir / "config.json"
    configured_dir = os.getenv("DOCKER_CONFIG", "").strip()
    if configured_dir:
        return Path(configured_dir).expanduser() / "config.json"
    return Path.home() / ".docker" / "config.json"


def _registry_in_auths(registry: str, auths: dict[object, object]) -> bool:
    """Return True when Docker auths include the exact registry hostname."""
    registry_variants = {registry, f"https://{registry}", f"http://{registry}"}
    for auth_registry, auth_config in auths.items():
        if str(auth_registry).rstrip("/") not in registry_variants:
            continue
        if isinstance(auth_config, dict):
            return True
    return False
