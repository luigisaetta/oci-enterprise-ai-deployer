"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-02
License: MIT

Description:
    OCIR image tag and URI helpers for Enterprise AI deployments.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

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
        f"{config.application.region_key}.ocir.io/"
        f"{resolved_namespace}/{container.repository}/{container.image_name}"
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
