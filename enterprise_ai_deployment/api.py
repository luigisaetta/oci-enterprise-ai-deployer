"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-05-07
License: MIT

Description:
    FastAPI backend for the OCI Enterprise AI deployer web console.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from enterprise_ai_deployment.compartments import resolve_deployment_config_compartment
from enterprise_ai_deployment.config import OciCliConfig
from enterprise_ai_deployment.deployment_config import (
    DeploymentConfigError,
    load_deployment_config,
)
from enterprise_ai_deployment.deployment_validation import (
    DeploymentValidationError,
    validate_deployment_config,
)
from enterprise_ai_deployment.ocir import build_ocir_registry, require_docker_login

RunAction = Literal["validate", "render", "dry-run", "build", "deploy"]
OCI_WAIT_WARNING = (
    "Encountered error while waiting for work request to enter the specified state"
)


class RunRequest(BaseModel):
    """Request sent by the web console to start an action run."""

    yaml: str = Field(min_length=1)
    env: str = ""
    action: RunAction
    profile: str = Field(min_length=1)
    region: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)


class RunCreated(BaseModel):
    """Response returned when a run has been accepted."""

    run_id: str


@dataclass(frozen=True)
class StoredRun:
    """In-memory run metadata used by the streaming backend."""

    run_id: str
    yaml: str
    env: str
    action: str
    profile: str
    region: str
    output_dir: str


@dataclass(frozen=True)
class ValidationResult:
    """Result of uploaded input validation for the web console."""

    error: str | None = None
    ocir_registry: str | None = None


RUNS: dict[str, StoredRun] = {}
DEPLOY_SCRIPTS: dict[str, Path] = {}
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000",
    "http://127.0.0.1:3000",
)


def _get_cors_origins() -> list[str]:
    """Return browser origins allowed to call the development API."""
    value = os.environ.get("DEPLOYER_WEB_CORS_ORIGINS")
    if not value:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in value.split(",") if origin.strip()]


def _get_required_api_key() -> str | None:
    """Return the configured web API key, when API protection is enabled."""
    value = os.environ.get("DEPLOYER_WEB_API_KEY", "").strip()
    return value or None


def _verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Validate the optional shared key used by the Web UI."""
    required_key = _get_required_api_key()
    if required_key is None:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, required_key):
        raise HTTPException(status_code=401, detail="Invalid API key")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="OCI Enterprise AI Deployer API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_get_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def _create_action_run(request: RunRequest) -> RunCreated:
        run_id = uuid.uuid4().hex
        RUNS[run_id] = StoredRun(run_id=run_id, **request.model_dump())
        DEPLOY_SCRIPTS.pop(run_id, None)
        return RunCreated(run_id=run_id)

    @app.post(
        "/api/runs",
        response_model=RunCreated,
        dependencies=[Depends(_verify_api_key)],
    )
    def create_action_run(request: RunRequest) -> RunCreated:
        return _create_action_run(request)

    @app.post(
        "/api/actions/preview",
        response_model=RunCreated,
        dependencies=[Depends(_verify_api_key)],
    )
    def create_legacy_preview_run(request: RunRequest) -> RunCreated:
        return _create_action_run(request)

    @app.get("/api/runs/{run_id}", dependencies=[Depends(_verify_api_key)])
    def get_run(run_id: str) -> dict[str, object]:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return asdict(run)

    @app.get("/api/runs/{run_id}/events", dependencies=[Depends(_verify_api_key)])
    async def stream_run_events(run_id: str) -> StreamingResponse:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return StreamingResponse(
            _fake_run_event_stream(run),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get(
        "/api/runs/{run_id}/deploy-script",
        dependencies=[Depends(_verify_api_key)],
    )
    def get_deploy_script(run_id: str) -> FileResponse:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if run.action != "dry-run":
            raise HTTPException(status_code=404, detail="Deploy script not available")
        script_path = DEPLOY_SCRIPTS.get(run_id)
        if script_path is None:
            raise HTTPException(status_code=404, detail="Deploy script not available")
        if not script_path.exists():
            raise HTTPException(status_code=404, detail="Deploy script not available")
        return FileResponse(
            script_path,
            media_type="text/x-shellscript",
            filename=script_path.name,
        )

    return app


async def _fake_run_event_stream(run: StoredRun):
    """Yield SSE messages for real validation and CLI-backed actions."""
    yaml_lines = len(run.yaml.splitlines())
    env_lines = len(run.env.splitlines())
    initial_steps = [
        ("status", {"state": "running", "step": "accepted"}),
        (
            "log",
            {
                "level": "info",
                "message": f"Accepted {run.action} action for profile {run.profile}.",
            },
        ),
        (
            "log",
            {
                "level": "info",
                "message": f"Loaded YAML input with {yaml_lines} lines.",
            },
        ),
        (
            "log",
            {
                "level": "info",
                "message": f"Loaded environment input with {env_lines} lines.",
            },
        ),
        (
            "status",
            {"state": "running", "step": "validate"},
        ),
    ]

    for event_name, payload in initial_steps:
        yield _to_sse(event_name, payload)
        await asyncio.sleep(0.45)

    validation_result = _validate_uploaded_inputs(run)
    if validation_result.error:
        yield _to_sse(
            "log",
            {
                "level": "error",
                "message": validation_result.error,
            },
        )
        yield _to_sse(
            "done",
            {
                "state": "failed",
                "step": "validate",
                "message": "Validation failed. No Docker or OCI command was executed.",
            },
        )
        return

    yield _to_sse(
        "log",
        {
            "level": "success",
            "message": "YAML and environment inputs passed real backend validation.",
        },
    )
    await asyncio.sleep(0.45)
    if validation_result.ocir_registry:
        yield _to_sse(
            "log",
            {
                "level": "success",
                "message": (
                    "Docker login detected for target OCIR registry "
                    f"{validation_result.ocir_registry}."
                ),
            },
        )
        await asyncio.sleep(0.45)

    if run.action == "dry-run":
        async for event in _stream_cli_command(
            run,
            cli_command="deploy",
            step="cli-dry-run",
            start_message=(
                "Starting real CLI dry-run. No Docker or OCI command will be executed."
            ),
            success_message="CLI dry-run completed successfully.",
            failure_message="CLI dry-run failed",
            dry_run=True,
            script_file=_dry_run_script_path(run),
        ):
            yield event
        return

    if run.action == "render":
        async for event in _stream_cli_command(
            run,
            cli_command="render",
            step="cli-render",
            start_message="Starting real CLI render.",
            success_message="CLI render completed successfully.",
            failure_message="CLI render failed",
            dry_run=False,
        ):
            yield event
        return

    if run.action == "build":
        async for event in _stream_cli_command(
            run,
            cli_command="build",
            step="cli-build",
            start_message="Starting real CLI container build.",
            success_message="CLI container build completed successfully.",
            failure_message="CLI container build failed",
            dry_run=False,
        ):
            yield event
        return

    if run.action == "deploy":
        async for event in _stream_cli_command(
            run,
            cli_command="deploy",
            step="cli-deploy",
            start_message=(
                "Starting real CLI deployment. Docker build, Docker push, and OCI "
                "resource commands may be executed."
            ),
            success_message="CLI deployment completed successfully.",
            failure_message="CLI deployment failed",
            dry_run=False,
        ):
            yield event
        return

    follow_up_steps = [
        (
            "status",
            {"state": "running", "step": "prepare-command"},
        ),
        (
            "log",
            {
                "level": "info",
                "message": (
                    "Prepared UI-only command preview for "
                    f"{run.region}; output directory is {run.output_dir}."
                ),
            },
        ),
        (
            "done",
            {
                "state": "succeeded",
                "step": "complete",
                "message": "Preview complete. No Docker or OCI command was executed.",
            },
        ),
    ]
    for event_name, payload in follow_up_steps:
        yield _to_sse(event_name, payload)
        await asyncio.sleep(0.45)


def _validate_uploaded_inputs(run: StoredRun) -> ValidationResult:
    """Validate uploaded YAML and env content with the existing Python rules."""
    repo_root = Path.cwd()
    try:
        with tempfile.TemporaryDirectory(prefix="deployer-web-") as temp_dir:
            temp_path = Path(temp_dir)
            yaml_path = temp_path / "deployment.yaml"
            env_path = temp_path / "deployment.env"
            yaml_path.write_text(run.yaml, encoding="utf-8")
            env_path.write_text(run.env, encoding="utf-8")

            config = load_deployment_config(yaml_path, env_file=env_path)
            config = replace(config, source_path=repo_root / "deployment.yaml")
            config = resolve_deployment_config_compartment(
                config,
                OciCliConfig(profile=run.profile, region=run.region),
            )
            validate_deployment_config(config)
            ocir_registry = build_ocir_registry(config.application.region_key)
            require_docker_login(ocir_registry)
    except (DeploymentConfigError, DeploymentValidationError, RuntimeError) as exc:
        return ValidationResult(error=str(exc))
    return ValidationResult(ocir_registry=ocir_registry)


async def _stream_cli_command(
    run: StoredRun,
    *,
    cli_command: str,
    step: str,
    start_message: str,
    success_message: str,
    failure_message: str,
    dry_run: bool,
    script_file: Path | None = None,
):
    """Run a real CLI command and stream its output as SSE."""
    repo_root = Path.cwd()
    yaml_path: Path | None = None
    env_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=repo_root,
            prefix=".deployer-web-",
            suffix=".yaml",
            delete=False,
        ) as yaml_file:
            yaml_file.write(run.yaml)
            yaml_path = Path(yaml_file.name)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=repo_root,
            prefix=".deployer-web-",
            suffix=".env",
            delete=False,
        ) as env_file:
            env_file.write(run.env)
            env_path = Path(env_file.name)

        command = [
            sys.executable,
            "-u",
            "oci_ai_deploy.py",
            "--config",
            str(yaml_path),
            "--env-file",
            str(env_path),
            "--output-dir",
            run.output_dir,
            "--profile",
            run.profile,
        ]
        if script_file is not None:
            command.extend(["--script-file", str(script_file)])
        if dry_run:
            command.append("--dry-run")
        command.append(cli_command)

        yield _to_sse(
            "status",
            {"state": "running", "step": step},
        )
        yield _to_sse(
            "log",
            {
                "level": "info",
                "message": start_message,
            },
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=repo_root,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if process.stdout is None:
            raise RuntimeError("Unable to capture CLI dry-run output.")

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            message = line.decode("utf-8", errors="replace").rstrip()
            if message:
                yield _to_sse(
                    "log",
                    {
                        "level": _cli_log_level(message),
                        "message": message,
                    },
                )

        return_code = await process.wait()
        if return_code == 0:
            if script_file is not None and script_file.exists():
                DEPLOY_SCRIPTS[run.run_id] = script_file
            yield _to_sse(
                "done",
                {
                    "state": "succeeded",
                    "step": "complete",
                    "message": success_message,
                },
            )
        else:
            yield _to_sse(
                "done",
                {
                    "state": "failed",
                    "step": step,
                    "message": f"{failure_message} with exit code {return_code}.",
                },
            )
    except OSError as exc:
        yield _to_sse(
            "done",
            {
                "state": "failed",
                "step": step,
                "message": f"Unable to start CLI command: {exc}",
            },
        )
    finally:
        for path in (yaml_path, env_path):
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


def _cli_log_level(message: str) -> str:
    """Return the UI log severity for one CLI output line."""
    if OCI_WAIT_WARNING in message:
        return "warning"
    return "info"


def _dry_run_script_path(run: StoredRun) -> Path:
    """Return the deploy script path generated for a web dry-run."""
    output_dir = Path(run.output_dir).expanduser()
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    return output_dir / "deploy.sh"


def _to_sse(event_name: str, payload: dict[str, str]) -> str:
    """Serialize one SSE event."""
    return f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"


app = create_app()


def main() -> None:
    """Run the API with uvicorn for local development."""
    import uvicorn

    uvicorn.run(
        "enterprise_ai_deployment.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
