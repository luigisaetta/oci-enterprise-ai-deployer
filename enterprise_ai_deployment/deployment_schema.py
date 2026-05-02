"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-02
License: MIT

Description:
    Pydantic schema for declarative OCI Enterprise AI deployment YAML.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DeploymentSchemaError(ValueError):
    """Raised when deployment YAML does not match the expected schema."""


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class ApplicationSchema(StrictModel):
    """Application identity and OCI region settings."""

    name: NonEmptyString
    compartment_id: NonEmptyString
    region: NonEmptyString
    region_key: NonEmptyString


class ContainerSchema(StrictModel):
    """Container build and OCIR publication settings."""

    context: NonEmptyString
    dockerfile: NonEmptyString
    image_name: NonEmptyString
    repository: NonEmptyString
    tag_strategy: Literal["git_sha", "timestamp", "explicit"]
    ocir_namespace: NonEmptyString
    tag: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_explicit_tag(self) -> "ContainerSchema":
        if self.tag_strategy == "explicit" and not self.tag:
            raise ValueError("container.tag is required when tag_strategy is explicit")
        return self


class HostedApplicationSchema(StrictModel):
    """Hosted Application settings."""

    display_name: NonEmptyString
    description: NonEmptyString | None = None
    create_if_missing: bool = True
    update_if_exists: bool = False
    scaling: dict[str, Any] = Field(default_factory=dict)
    networking: dict[str, Any] = Field(default_factory=dict)
    security: dict[str, Any] = Field(default_factory=dict)
    environment: dict[str, Any] = Field(default_factory=dict)


class HostedDeploymentSchema(StrictModel):
    """Hosted Deployment settings."""

    display_name: NonEmptyString
    create_new_version: bool = True
    activate: bool = True
    wait_for_state: NonEmptyString | None = "SUCCEEDED"


class DeploymentItemSchema(StrictModel):
    """One deployment inside an Enterprise Solution."""

    name: NonEmptyString
    container: ContainerSchema
    hosted_application: HostedApplicationSchema
    hosted_deployment: HostedDeploymentSchema


class DeploymentSchema(StrictModel):
    """Complete deployment YAML schema."""

    enterprise_solution: ApplicationSchema | None = None
    deployments: list[DeploymentItemSchema] | None = None
    application: ApplicationSchema | None = None
    container: ContainerSchema | None = None
    hosted_application: HostedApplicationSchema | None = None
    hosted_deployment: HostedDeploymentSchema | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "DeploymentSchema":
        """Accept either Enterprise Solution or legacy single-deployment YAML."""
        has_solution = (
            self.enterprise_solution is not None or self.deployments is not None
        )
        has_legacy = any(
            value is not None
            for value in (
                self.application,
                self.container,
                self.hosted_application,
                self.hosted_deployment,
            )
        )
        if has_solution and has_legacy:
            raise ValueError(
                "Use either enterprise_solution/deployments or legacy "
                "application/container/hosted_application/hosted_deployment, not both"
            )
        if has_solution:
            if self.enterprise_solution is None:
                raise ValueError("enterprise_solution is required")
            if not self.deployments:
                raise ValueError("deployments must contain at least one item")
            names = [deployment.name for deployment in self.deployments]
            duplicates = sorted({name for name in names if names.count(name) > 1})
            if duplicates:
                raise ValueError(
                    "deployments names must be unique: " + ", ".join(duplicates)
                )
            return self
        missing = [
            field_name
            for field_name in (
                "application",
                "container",
                "hosted_application",
                "hosted_deployment",
            )
            if getattr(self, field_name) is None
        ]
        if missing:
            raise ValueError(
                "legacy deployment YAML is missing required fields: "
                + ", ".join(missing)
            )
        return self


def validate_deployment_schema(raw_config: dict[str, Any]) -> DeploymentSchema:
    """Validate raw YAML content and return a typed schema model."""
    try:
        return DeploymentSchema.model_validate(raw_config)
    except ValidationError as exc:
        message = _format_validation_error(exc)
        missing_fields = _missing_legacy_top_level_fields(raw_config)
        if missing_fields:
            message = "\n".join(
                [
                    message,
                    *(
                        f"- {field_name}: Field required"
                        for field_name in missing_fields
                    ),
                ]
            )
        raise DeploymentSchemaError(message) from exc


def _missing_legacy_top_level_fields(raw_config: dict[str, Any]) -> list[str]:
    """Return legacy top-level fields missing from a partial legacy YAML."""
    if "enterprise_solution" in raw_config or "deployments" in raw_config:
        return []
    legacy_fields = (
        "application",
        "container",
        "hosted_application",
        "hosted_deployment",
    )
    if not any(field_name in raw_config for field_name in legacy_fields):
        return []
    return [field_name for field_name in legacy_fields if field_name not in raw_config]


def _format_validation_error(exc: ValidationError) -> str:
    """Render Pydantic errors as stable, readable messages."""
    lines = ["Deployment YAML schema validation failed:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        if not location:
            location = "<root>"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)
