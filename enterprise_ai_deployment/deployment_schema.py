"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-04-30
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


class DeploymentSchema(StrictModel):
    """Complete deployment YAML schema."""

    application: ApplicationSchema
    container: ContainerSchema
    hosted_application: HostedApplicationSchema
    hosted_deployment: HostedDeploymentSchema


def validate_deployment_schema(raw_config: dict[str, Any]) -> DeploymentSchema:
    """Validate raw YAML content and return a typed schema model."""
    try:
        return DeploymentSchema.model_validate(raw_config)
    except ValidationError as exc:
        raise DeploymentSchemaError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    """Render Pydantic errors as stable, readable messages."""
    lines = ["Deployment YAML schema validation failed:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        if not location:
            location = "<root>"
        lines.append(f"- {location}: {error['msg']}")
    return "\n".join(lines)
