"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-02
License: MIT

Description:
    Validation rules for declarative OCI Enterprise AI deployments.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from enterprise_ai_deployment.config import COMPARTMENT_OCID_PREFIX
from enterprise_ai_deployment.deployment_config import (
    DeploymentConfig,
    DeploymentUnitConfig,
)

SENSITIVE_TOKENS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "client_secret",
    "authorization",
)
SUPPORTED_AUTH_TYPES = {"IDCS_AUTH_CONFIG", "NO_AUTH"}


class DeploymentValidationError(ValueError):
    """Raised when deployment configuration validation fails."""


def validate_deployment_config(config: DeploymentConfig) -> None:
    """Validate a loaded deployment configuration."""
    errors: list[str] = []

    if not config.application.compartment_id.startswith(COMPARTMENT_OCID_PREFIX):
        errors.append("application.compartment_id must be a compartment OCID.")
    if not config.application.region:
        errors.append("application.region is required.")
    if not config.application.region_key:
        errors.append("application.region_key is required.")

    seen_app_names: set[str] = set()
    for deployment in config.deployments:
        _validate_deployment(config, deployment, seen_app_names, errors)

    if errors:
        raise DeploymentValidationError("\n".join(f"- {error}" for error in errors))


def _validate_deployment(
    config: DeploymentConfig,
    deployment: DeploymentUnitConfig,
    seen_app_names: set[str],
    errors: list[str],
) -> None:
    """Validate one serial deployment."""
    prefix = _deployment_prefix(config, deployment)
    dockerfile = _resolve_path(
        config.source_path,
        deployment.container.context,
        deployment.container.dockerfile,
    )
    if not dockerfile.exists():
        errors.append(f"{prefix}container.dockerfile does not exist: {dockerfile}")

    if deployment.container.tag_strategy not in {"git_sha", "timestamp", "explicit"}:
        errors.append(
            f"{prefix}container.tag_strategy must be one of: "
            "git_sha, timestamp, explicit."
        )
    if deployment.container.tag_strategy == "explicit" and not deployment.container.tag:
        errors.append(
            f"{prefix}container.tag is required when tag_strategy is explicit."
        )

    display_name = deployment.hosted_application.display_name
    if display_name in seen_app_names:
        errors.append(
            f"{prefix}hosted_application.display_name must be unique within "
            "the Enterprise Solution."
        )
    seen_app_names.add(display_name)

    _validate_security(deployment.hosted_application.security, errors, prefix)
    _validate_environment(deployment.hosted_application.environment, errors, prefix)


def _deployment_prefix(
    config: DeploymentConfig, deployment: DeploymentUnitConfig
) -> str:
    """Return a validation message prefix for multi-deployment YAML."""
    if len(config.deployments) == 1:
        return ""
    return f"deployments.{deployment.name}."


def _resolve_path(source_path: Path, context: str, dockerfile: str) -> Path:
    """Resolve Dockerfile relative to YAML location and build context."""
    base_dir = source_path.parent
    context_path = Path(context).expanduser()
    if not context_path.is_absolute():
        context_path = base_dir / context_path
    dockerfile_path = Path(dockerfile).expanduser()
    if dockerfile_path.is_absolute():
        return dockerfile_path
    return context_path / dockerfile_path


def _validate_security(
    security: dict[str, Any], errors: list[str], prefix: str = ""
) -> None:
    """Validate supported inbound auth settings."""
    auth_type = str(security.get("auth_type", "NO_AUTH")).upper()
    if auth_type not in SUPPORTED_AUTH_TYPES:
        errors.append(
            f"{prefix}hosted_application.security.auth_type must be one of: "
            "IDCS_AUTH_CONFIG, NO_AUTH."
        )
        return
    if auth_type == "NO_AUTH":
        return
    for field_name in ("issuer_url", "audience"):
        if not str(security.get(field_name, "")).strip():
            errors.append(
                f"{prefix}hosted_application.security."
                f"{field_name} is required for IDCS_AUTH_CONFIG."
            )
    scopes = security.get("scopes")
    if not isinstance(scopes, list) or not any(str(scope).strip() for scope in scopes):
        errors.append(
            f"{prefix}hosted_application.security.scopes must contain at least one "
            "scope for IDCS_AUTH_CONFIG."
        )


def _validate_environment(
    environment: dict[str, Any], errors: list[str], prefix: str = ""
) -> None:
    """Validate environment variables and secret references."""
    variables = environment.get("variables", {})
    if variables is not None and not isinstance(variables, dict):
        errors.append(
            f"{prefix}hosted_application.environment.variables must be a mapping."
        )
    if isinstance(variables, dict):
        _validate_plaintext_variables(variables, errors, prefix)

    secrets = environment.get("secrets", {})
    if secrets is None:
        return
    if not isinstance(secrets, dict):
        errors.append(
            f"{prefix}hosted_application.environment.secrets must be a mapping."
        )
        return
    for secret_name, secret_config in secrets.items():
        _validate_secret_reference(str(secret_name), secret_config, errors, prefix)


def _validate_plaintext_variables(
    variables: dict[str, Any], errors: list[str], prefix: str = ""
) -> None:
    """Validate non-secret environment variables."""
    for name, value in variables.items():
        variable_name = str(name)
        if _looks_sensitive(variable_name):
            errors.append(
                f"{prefix}hosted_application.environment.variables must not contain "
                f"sensitive-looking key {variable_name!r}; use secrets instead."
            )
        if isinstance(value, str) and _looks_like_hardcoded_secret(
            variable_name, value
        ):
            errors.append(
                f"{prefix}hosted_application.environment.variables."
                f"{variable_name} looks like a hardcoded secret."
            )


def _validate_secret_reference(
    secret_name: str,
    secret_config: object,
    errors: list[str],
    prefix: str = "",
) -> None:
    """Validate one secret reference."""
    if not isinstance(secret_config, dict):
        errors.append(
            f"{prefix}hosted_application.environment.secrets."
            f"{secret_name} must reference a secret source."
        )
        return
    source = str(secret_config.get("source", "")).lower()
    if source == "vault":
        _validate_vault_secret(secret_name, secret_config, errors, prefix)
        return
    if source == "local_env":
        _validate_local_env_secret(secret_name, secret_config, errors, prefix)
        return
    errors.append(
        f"{prefix}hosted_application.environment.secrets."
        f"{secret_name}.source must be vault or local_env."
    )


def _validate_vault_secret(
    secret_name: str,
    secret_config: dict[str, Any],
    errors: list[str],
    prefix: str = "",
) -> None:
    """Validate one Vault secret reference."""
    if not str(secret_config.get("secret_ocid", "")).startswith("ocid1."):
        errors.append(
            f"{prefix}hosted_application.environment.secrets."
            f"{secret_name}.secret_ocid is required."
        )


def _validate_local_env_secret(
    secret_name: str,
    secret_config: dict[str, Any],
    errors: list[str],
    prefix: str = "",
) -> None:
    """Validate one local environment secret reference."""
    env_name = str(secret_config.get("env_name", "")).strip()
    if not env_name:
        errors.append(
            f"{prefix}hosted_application.environment.secrets."
            f"{secret_name}.env_name is required."
        )
    elif env_name not in os.environ:
        errors.append(
            f"local_env secret {secret_name!r} references missing "
            f"environment variable {env_name!r}."
        )


def _looks_sensitive(name: str) -> bool:
    """Return True for names that should be represented as secret references."""
    lowered = name.lower()
    return any(token in lowered for token in SENSITIVE_TOKENS)


def _looks_like_hardcoded_secret(name: str, value: str) -> bool:
    """Detect obvious secret-like inline values."""
    if _looks_sensitive(name):
        return True
    stripped = value.strip()
    return (
        len(stripped) >= 24
        and " " not in stripped
        and not stripped.startswith("ocid1.")
    )
