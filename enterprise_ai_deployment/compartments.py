"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-05
License: MIT

Description:
    Common OCI compartment name resolution helpers.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import replace

from enterprise_ai_deployment.cli_commands import (
    build_list_compartments_by_name_command,
)
from enterprise_ai_deployment.config import COMPARTMENT_OCID_PREFIX, OciCliConfig
from enterprise_ai_deployment.deployment_config import (
    ApplicationConfig,
    DeploymentConfig,
)

CompartmentChooser = Callable[[list[dict[str, object]]], dict[str, object]]
CommandLogger = Callable[[list[str]], None]

_COMPARTMENT_CACHE: dict[tuple[str | None, str | None, str], str] = {}


def resolve_deployment_config_compartment(
    config: DeploymentConfig,
    cli_config: OciCliConfig,
    *,
    choose_match: CompartmentChooser | None = None,
    log_command: CommandLogger | None = None,
) -> DeploymentConfig:
    """Return a config whose application compartment is always an OCID."""
    name_or_ocid = (
        config.application.compartment_name or config.application.compartment_id
    )
    compartment_id = resolve_compartment_id(
        cli_config,
        name_or_ocid,
        choose_match=choose_match,
        log_command=log_command,
    )
    if compartment_id == config.application.compartment_id:
        return config
    return replace(
        config,
        application=replace(
            config.application,
            compartment_id=compartment_id,
        ),
    )


def resolve_compartment_id(
    config: OciCliConfig,
    name_or_ocid: str,
    *,
    choose_match: CompartmentChooser | None = None,
    log_command: CommandLogger | None = None,
) -> str:
    """Resolve a compartment OCID from either an OCID or a display name."""
    value = name_or_ocid.strip()
    if value.startswith(COMPARTMENT_OCID_PREFIX):
        return value

    cache_key = (config.profile, config.region, value)
    cached_id = _COMPARTMENT_CACHE.get(cache_key)
    if cached_id:
        return cached_id

    command = build_list_compartments_by_name_command(config, value)
    if log_command:
        log_command(command)
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip()
        if detail:
            raise RuntimeError(
                f"Unable to resolve compartment name {value!r}: {detail}"
            )
        raise RuntimeError(f"Unable to resolve compartment name: {value}")

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "OCI CLI returned invalid JSON while resolving compartment."
        ) from exc

    matches = [
        item
        for item in _extract_items(payload)
        if str(item.get("name") or "") == value and item.get("id")
    ]
    if not matches:
        raise RuntimeError(f"No compartment found with name: {value}")
    if len(matches) == 1:
        compartment_id = str(matches[0]["id"])
        _COMPARTMENT_CACHE[cache_key] = compartment_id
        return compartment_id
    if choose_match is None:
        labels = ", ".join(_compartment_label(item) for item in matches)
        raise RuntimeError(
            f"Multiple compartments found with name {value!r}: {labels}. "
            "Use compartment_id in the YAML to disambiguate."
        )

    selected = choose_match(matches)
    compartment_id = str(selected["id"])
    _COMPARTMENT_CACHE[cache_key] = compartment_id
    return compartment_id


def clear_compartment_cache() -> None:
    """Clear cached compartment name resolutions."""
    _COMPARTMENT_CACHE.clear()


def _extract_items(payload: object) -> list[dict[str, object]]:
    """Extract OCI CLI list items from common response shapes."""
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


def _compartment_label(compartment: dict[str, object]) -> str:
    """Return a compact display label for one compartment."""
    name = str(compartment.get("name") or "<unnamed>")
    compartment_id = str(compartment.get("id") or "<missing id>")
    lifecycle_state = compartment.get("lifecycle-state")
    state_suffix = f", {lifecycle_state}" if lifecycle_state else ""
    return f"{name} ({compartment_id}{state_suffix})"
