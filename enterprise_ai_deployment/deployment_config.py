"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-05
License: MIT

Description:
    YAML and environment loading for the OCI Enterprise AI deployment CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from enterprise_ai_deployment.deployment_schema import (
    DeploymentSchema,
    DeploymentSchemaError,
    DeploymentItemSchema,
    validate_deployment_schema,
)


class DeploymentConfigError(ValueError):
    """Raised when the deployment configuration cannot be loaded."""


@dataclass(frozen=True)
class ApplicationConfig:
    """Top-level application identity and OCI region settings."""

    name: str
    compartment_id: str
    region: str
    region_key: str
    compartment_name: str | None = None


@dataclass(frozen=True)
class ContainerConfig:
    """Container build and OCIR publication settings."""

    context: str
    dockerfile: str
    image_repository: str
    tag_strategy: str
    ocir_namespace: str
    tag: str | None = None


@dataclass(frozen=True)
class HostedApplicationConfig:  # pylint: disable=too-many-instance-attributes
    """Hosted Application settings rendered for OCI CLI."""

    display_name: str
    description: str | None = None
    create_if_missing: bool = True
    update_if_exists: bool = False
    scaling: dict[str, Any] = field(default_factory=dict)
    networking: dict[str, Any] = field(default_factory=dict)
    security: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HostedDeploymentConfig:
    """Hosted Deployment settings rendered for OCI CLI."""

    display_name: str
    create_new_version: bool = True
    activate: bool = True
    wait_for_state: str | None = "SUCCEEDED"


@dataclass(frozen=True)
class DeploymentUnitConfig:
    """One serial deployment inside an Enterprise Solution."""

    name: str
    container: ContainerConfig
    hosted_application: HostedApplicationConfig
    hosted_deployment: HostedDeploymentConfig


@dataclass(frozen=True)
class DeploymentConfig:
    """Complete Enterprise Solution configuration read from YAML."""

    application: ApplicationConfig
    deployments: tuple[DeploymentUnitConfig, ...]
    source_path: Path

    @property
    def container(self) -> ContainerConfig:
        """Return the first deployment container for legacy callers."""
        return self.deployments[0].container

    @property
    def hosted_application(self) -> HostedApplicationConfig:
        """Return the first Hosted Application for legacy callers."""
        return self.deployments[0].hosted_application

    @property
    def hosted_deployment(self) -> HostedDeploymentConfig:
        """Return the first Hosted Deployment for legacy callers."""
        return self.deployments[0].hosted_deployment


def load_deployment_config(
    path: str | Path, env_file: str | Path | None = None
) -> DeploymentConfig:
    """Load deployment YAML and optional local .env references."""
    config_path = Path(path).expanduser()
    if not config_path.exists():
        raise DeploymentConfigError(f"Configuration file not found: {config_path}")
    if env_file:
        env_path = Path(env_file).expanduser()
        if not env_path.exists():
            raise DeploymentConfigError(f"Environment file not found: {env_path}")
        load_dotenv(env_path, override=False)

    try:
        with config_path.open("r", encoding="utf-8") as file_handle:
            raw_config = yaml.safe_load(file_handle)
    except yaml.YAMLError as exc:
        raise DeploymentConfigError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw_config, dict):
        raise DeploymentConfigError(
            "Deployment YAML must contain a mapping at the top level."
        )

    try:
        schema = validate_deployment_schema(raw_config)
    except DeploymentSchemaError as exc:
        raise DeploymentConfigError(str(exc)) from exc

    return _parse_deployment_config(schema, config_path)


def _parse_deployment_config(
    schema: DeploymentSchema, source_path: Path
) -> DeploymentConfig:
    """Convert a raw YAML mapping into typed configuration objects."""
    if schema.enterprise_solution is not None and schema.deployments is not None:
        application = schema.enterprise_solution
        deployment_items = schema.deployments
    else:
        application = schema.application
        if application is None:
            raise DeploymentConfigError("Deployment YAML is missing application.")
        if (
            schema.container is None
            or schema.hosted_application is None
            or schema.hosted_deployment is None
        ):
            raise DeploymentConfigError("Deployment YAML is incomplete.")
        deployment_items = [
            DeploymentItemSchema(
                name=application.name,
                container=schema.container,
                hosted_application=schema.hosted_application,
                hosted_deployment=schema.hosted_deployment,
            )
        ]

    return DeploymentConfig(
        application=ApplicationConfig(
            name=application.name,
            compartment_id=application.compartment_id
            or application.compartment_name
            or "",
            region=application.region,
            region_key=application.region_key,
            compartment_name=application.compartment_name,
        ),
        deployments=tuple(
            _parse_deployment_unit(deployment) for deployment in deployment_items
        ),
        source_path=source_path,
    )


def _parse_deployment_unit(schema: DeploymentItemSchema) -> DeploymentUnitConfig:
    """Convert one schema deployment item into a typed configuration object."""
    container = schema.container
    hosted_application = schema.hosted_application
    hosted_deployment = schema.hosted_deployment
    return DeploymentUnitConfig(
        name=schema.name,
        container=ContainerConfig(
            context=container.context,
            dockerfile=container.dockerfile,
            image_repository=container.image_repository,
            tag_strategy=container.tag_strategy,
            ocir_namespace=container.ocir_namespace,
            tag=container.tag,
        ),
        hosted_application=HostedApplicationConfig(
            display_name=hosted_application.display_name,
            description=hosted_application.description,
            create_if_missing=hosted_application.create_if_missing,
            update_if_exists=hosted_application.update_if_exists,
            scaling=hosted_application.scaling,
            networking=hosted_application.networking,
            security=hosted_application.security,
            environment=hosted_application.environment,
        ),
        hosted_deployment=HostedDeploymentConfig(
            display_name=hosted_deployment.display_name,
            create_new_version=hosted_deployment.create_new_version,
            activate=hosted_deployment.activate,
            wait_for_state=hosted_deployment.wait_for_state,
        ),
    )
