"""
Author: L. Saetta
Version: 0.1.0
Last modified: 2026-04-30
License: MIT

Description:
    FastAPI backend for the OCI Enterprise AI deployer web console.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from enterprise_ai_deployment.deployment_config import (
    DeploymentConfigError,
    load_deployment_config,
)
from enterprise_ai_deployment.deployment_validation import (
    DeploymentValidationError,
    validate_deployment_config,
)

RunAction = Literal["validate", "render", "dry-run", "deploy"]


class RunRequest(BaseModel):
    """Request sent by the web console to start a fake streamed run."""

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
    """In-memory run metadata used by the fake streaming backend."""

    run_id: str
    yaml: str
    env: str
    action: str
    profile: str
    region: str
    output_dir: str


RUNS: dict[str, StoredRun] = {}


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="OCI Enterprise AI Deployer API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/actions/preview", response_model=RunCreated)
    def create_preview_run(request: RunRequest) -> RunCreated:
        run_id = uuid.uuid4().hex
        RUNS[run_id] = StoredRun(run_id=run_id, **request.model_dump())
        return RunCreated(run_id=run_id)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return asdict(run)

    @app.get("/api/runs/{run_id}/events")
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

    return app


async def _fake_run_event_stream(run: StoredRun):
    """Yield SSE messages for real validation and fake downstream actions."""
    yaml_lines = len(run.yaml.splitlines())
    env_lines = len(run.env.splitlines())
    initial_steps = [
        ("status", {"state": "running", "step": "accepted"}),
        (
            "log",
            {
                "level": "info",
                "message": f"Accepted {run.action} preview for profile {run.profile}.",
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

    validation_error = _validate_uploaded_inputs(run)
    if validation_error:
        yield _to_sse(
            "log",
            {
                "level": "error",
                "message": validation_error,
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

    if run.action == "dry-run":
        async for event in _stream_cli_dry_run(run):
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


def _validate_uploaded_inputs(run: StoredRun) -> str | None:
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
            validate_deployment_config(config)
    except (DeploymentConfigError, DeploymentValidationError) as exc:
        return str(exc)
    return None


async def _stream_cli_dry_run(run: StoredRun):
    """Run the real CLI dry-run command and stream its output as SSE."""
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
            "oci_ai_deploy.py",
            "--config",
            str(yaml_path),
            "--env-file",
            str(env_path),
            "--output-dir",
            run.output_dir,
            "--dry-run",
            "deploy",
        ]

        yield _to_sse(
            "status",
            {"state": "running", "step": "cli-dry-run"},
        )
        yield _to_sse(
            "log",
            {
                "level": "info",
                "message": "Starting real CLI dry-run. No Docker or OCI command will be executed.",
            },
        )

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=repo_root,
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
                yield _to_sse("log", {"level": "info", "message": message})

        return_code = await process.wait()
        if return_code == 0:
            yield _to_sse(
                "done",
                {
                    "state": "succeeded",
                    "step": "complete",
                    "message": "CLI dry-run completed successfully.",
                },
            )
        else:
            yield _to_sse(
                "done",
                {
                    "state": "failed",
                    "step": "cli-dry-run",
                    "message": f"CLI dry-run failed with exit code {return_code}.",
                },
            )
    except OSError as exc:
        yield _to_sse(
            "done",
            {
                "state": "failed",
                "step": "cli-dry-run",
                "message": f"Unable to start CLI dry-run: {exc}",
            },
        )
    finally:
        for path in (yaml_path, env_path):
            if path is not None:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


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
