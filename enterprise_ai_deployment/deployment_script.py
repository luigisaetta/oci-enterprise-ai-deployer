"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-07
License: MIT

Description:
    POSIX shell script rendering for OCI Enterprise AI deployment plans.
"""

from __future__ import annotations

import shlex
import stat
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RawShellArg:
    """Shell argument emitted without quoting, for intentional variables."""

    value: str


ScriptArg = str | RawShellArg


@dataclass(frozen=True)
class DeploymentScriptCommand:
    """One command rendered into the generated deployment shell script."""

    title: str
    command: tuple[ScriptArg, ...]
    capture_stdout: Path | None = None


def write_deployment_script(
    script_path: str | Path,
    commands: list[DeploymentScriptCommand],
) -> Path:
    """Write an executable POSIX shell script for the deployment commands."""
    target = Path(script_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_deployment_script(commands), encoding="utf-8")
    current_mode = target.stat().st_mode
    target.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return target


def render_deployment_script(commands: list[DeploymentScriptCommand]) -> str:
    """Render a deploy plan as a Linux/macOS compatible shell script."""
    lines = [
        "#!/usr/bin/env bash",
        "# Author: L. Saetta",
        "# Version: 0.1.0",
        "# Last modified: 2026-05-07",
        "# License: MIT",
        "",
        "set -euo pipefail",
        "",
        'require_command() { command -v "$1" >/dev/null 2>&1 || '
        '{ echo "Missing required command: $1" >&2; exit 127; }; }',
        "",
        "require_command docker",
        "require_command oci",
        "require_command python3",
        "",
    ]

    for item in commands:
        lines.extend(_render_command_block(item))

    lines.append('echo "Deployment script completed."')
    lines.append("")
    return "\n".join(lines)


def format_script_command(command: tuple[ScriptArg, ...]) -> str:
    """Return a shell-safe command string, preserving explicit shell variables."""
    parts = []
    for argument in command:
        if isinstance(argument, RawShellArg):
            parts.append(argument.value)
        else:
            parts.append(shlex.quote(argument))
    return " ".join(parts)


def _render_command_block(item: DeploymentScriptCommand) -> list[str]:
    """Render one titled shell command block."""
    lines = [
        f'echo "==> {item.title}"',
        format_script_command(item.command)
        + (
            f" | tee {shlex.quote(str(item.capture_stdout))}"
            if item.capture_stdout is not None
            else ""
        ),
    ]
    if item.capture_stdout is not None:
        lines.extend(
            [
                "HOSTED_APPLICATION_ID=$(python3 -c "
                + shlex.quote(_HOSTED_APPLICATION_ID_EXTRACTOR)
                + f" {shlex.quote(str(item.capture_stdout))})",
                'echo "Hosted Application ID: ${HOSTED_APPLICATION_ID}"',
            ]
        )
    lines.append("")
    return lines


_HOSTED_APPLICATION_ID_EXTRACTOR = r"""
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as file:
    payload = json.load(file)

prefix = "ocid1.generativeaihostedapplication."
preferred_keys = ("identifier", "resourceId", "resource-id", "id")


def find_ocid(value):
    if isinstance(value, str) and value.startswith(prefix):
        return value
    if isinstance(value, dict):
        for key in preferred_keys:
            found = find_ocid(value.get(key))
            if found:
                return found
        for child in value.values():
            found = find_ocid(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = find_ocid(child)
            if found:
                return found
    return None


resource_id = find_ocid(payload)
if not resource_id:
    raise SystemExit("Hosted Application OCID not found in OCI CLI response.")
print(resource_id)
""".strip()
