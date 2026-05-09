"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-09
License: MIT

Description:
    Shared preflight checks for the CLI and web console.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from enterprise_ai_deployment.config import OciCliConfig
from enterprise_ai_deployment.deployment_config import DeploymentConfig
from enterprise_ai_deployment.ocir import build_ocir_registry, docker_login_exists

REQUIRED_OCI_CLI_VERSION = "3.81.0"


@dataclass(frozen=True)
class PreflightCheck:
    """One preflight check result."""

    name: str
    status: str
    message: str

    @property
    def passed(self) -> bool:
        """Return True when this check is not an error."""
        return self.status != "error"


@dataclass(frozen=True)
class PreflightReport:
    """Complete preflight result."""

    checks: tuple[PreflightCheck, ...]

    @property
    def success(self) -> bool:
        """Return True when no check failed."""
        return all(check.passed for check in self.checks)


def run_preflight_checks(
    config: DeploymentConfig,
    cli_config: OciCliConfig,
    *,
    timeout_seconds: int = 20,
) -> PreflightReport:
    """Run non-mutating checks required before deployment operations."""
    checks = [
        _check_executable("docker", "Docker CLI"),
        _check_docker_daemon(timeout_seconds),
        _check_docker_login(config),
        _check_executable("oci", "OCI CLI"),
        _check_oci_cli_version(timeout_seconds),
        _check_oci_namespace(cli_config, timeout_seconds),
        _check_oci_command_group(
            cli_config,
            ("generative-ai", "hosted-application", "--help"),
            "OCI Hosted Application command",
            timeout_seconds,
        ),
        _check_oci_command_group(
            cli_config,
            ("generative-ai", "hosted-deployment", "--help"),
            "OCI Hosted Deployment command",
            timeout_seconds,
        ),
    ]
    return PreflightReport(checks=tuple(checks))


def format_preflight_report(report: PreflightReport) -> str:
    """Return a readable CLI/web representation of a preflight report."""
    lines = ["Preflight checks:"]
    for check in report.checks:
        marker = {"ok": "OK", "warning": "WARN", "error": "ERROR"}[check.status]
        lines.append(f"- [{marker}] {check.name}: {check.message}")
    if report.success:
        lines.append("Preflight completed successfully.")
    else:
        lines.append("Preflight failed. Fix the ERROR checks before deploy.")
    return "\n".join(lines)


def _check_executable(executable: str, label: str) -> PreflightCheck:
    """Check that an executable is available on PATH."""
    path = shutil.which(executable)
    if path:
        return PreflightCheck(label, "ok", f"found at {path}")
    return PreflightCheck(label, "error", f"{executable!r} was not found on PATH.")


def _check_docker_daemon(timeout_seconds: int) -> PreflightCheck:
    """Check that the Docker daemon responds to the CLI."""
    if not shutil.which("docker"):
        return PreflightCheck(
            "Docker daemon",
            "error",
            "Docker CLI is not available, so daemon status cannot be checked.",
        )
    result = _run_command(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        timeout_seconds,
    )
    if result.returncode == 0 and result.stdout.strip():
        return PreflightCheck(
            "Docker daemon",
            "ok",
            f"server version {result.stdout.strip()}",
        )
    return PreflightCheck(
        "Docker daemon",
        "error",
        _failure_message(result, "Docker daemon did not respond."),
    )


def _check_docker_login(config: DeploymentConfig) -> PreflightCheck:
    """Check Docker credentials for the target OCIR registry."""
    registry = build_ocir_registry(config.application.region_key)
    if docker_login_exists(registry):
        return PreflightCheck(
            "Docker OCIR login",
            "ok",
            f"credentials found for {registry}",
        )
    return PreflightCheck(
        "Docker OCIR login",
        "error",
        f"credentials for {registry} were not found. Run 'docker login {registry}'.",
    )


def _check_oci_cli_version(timeout_seconds: int) -> PreflightCheck:
    """Check the installed OCI CLI version."""
    if not shutil.which("oci"):
        return PreflightCheck(
            "OCI CLI version",
            "error",
            "OCI CLI is not available, so version cannot be checked.",
        )
    result = _run_command(["oci", "--version"], timeout_seconds)
    version = result.stdout.strip() or result.stderr.strip()
    if result.returncode != 0:
        return PreflightCheck(
            "OCI CLI version",
            "error",
            _failure_message(result, "Unable to read OCI CLI version."),
        )
    if version == REQUIRED_OCI_CLI_VERSION:
        return PreflightCheck("OCI CLI version", "ok", version)
    return PreflightCheck(
        "OCI CLI version",
        "warning",
        f"{version}; project expects {REQUIRED_OCI_CLI_VERSION}",
    )


def _check_oci_namespace(
    cli_config: OciCliConfig, timeout_seconds: int
) -> PreflightCheck:
    """Check OCI auth/config through a read-only namespace lookup."""
    command = _oci_command(cli_config, ("os", "ns", "get"))
    result = _run_command(command, timeout_seconds)
    if result.returncode == 0:
        namespace = result.stdout.strip()
        if namespace:
            return PreflightCheck(
                "OCI read access", "ok", "Object Storage namespace resolved."
            )
        return PreflightCheck(
            "OCI read access", "ok", "OCI namespace command succeeded."
        )
    return PreflightCheck(
        "OCI read access",
        "error",
        _failure_message(result, "Unable to resolve Object Storage namespace."),
    )


def _check_oci_command_group(
    cli_config: OciCliConfig,
    suffix: tuple[str, ...],
    label: str,
    timeout_seconds: int,
) -> PreflightCheck:
    """Check that an OCI CLI command group is available."""
    result = _run_command(_oci_command(cli_config, suffix), timeout_seconds)
    if result.returncode == 0:
        return PreflightCheck(label, "ok", "command group is available.")
    return PreflightCheck(
        label,
        "error",
        _failure_message(result, "command group is not available."),
    )


def _oci_command(config: OciCliConfig, suffix: tuple[str, ...]) -> list[str]:
    """Build a read-only OCI CLI command with profile and region options."""
    command = ["oci"]
    if config.profile:
        command.extend(["--profile", config.profile])
    if config.region:
        command.extend(["--region", config.region])
    command.extend(suffix)
    return command


def _run_command(
    command: list[str], timeout_seconds: int
) -> subprocess.CompletedProcess[str]:
    """Run a preflight subprocess command."""
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=str(exc))


def _failure_message(result: subprocess.CompletedProcess[str], fallback: str) -> str:
    """Return a compact failure message from a subprocess result."""
    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        return detail
    return fallback
