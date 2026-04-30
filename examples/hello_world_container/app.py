"""
Author: L. Saetta
Last modified: 2026-04-30
License: MIT

Description:
    Minimal FastAPI application used as a sample container deployment target.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Input payload accepted by the sample endpoint."""

    name: str | None = None
    user_request: str | None = None


app = FastAPI(title="OCI Enterprise AI Deployer Hello World")


@app.get("/health")
def health() -> dict[str, str]:
    """Return a basic health response."""
    return {"status": "ok"}


@app.get("/ready")
def ready() -> dict[str, str]:
    """Return a basic readiness response."""
    return {"status": "ready"}


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    """Return a simple hello response."""
    name = (request.name or request.user_request or "world").strip() or "world"
    return {"output_text": f"Hello, {name}!"}
