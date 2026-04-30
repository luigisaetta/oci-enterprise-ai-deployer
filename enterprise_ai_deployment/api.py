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
import uuid
from dataclasses import asdict, dataclass
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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
    """Yield deterministic SSE messages for the UI preview workflow."""
    yaml_lines = len(run.yaml.splitlines())
    env_lines = len(run.env.splitlines())
    steps = [
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
        (
            "log",
            {
                "level": "success",
                "message": "Fake validation completed without errors.",
            },
        ),
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

    for event_name, payload in steps:
        yield _to_sse(event_name, payload)
        await asyncio.sleep(0.45)


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
        reload=True,
    )


if __name__ == "__main__":
    main()
