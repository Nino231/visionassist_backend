"""GET /v1/health — service health-check.

No API-key required.  Used by:
  • Docker HEALTHCHECK command.
  • CI/CD readiness probes.
  • Flutter app's optional "test connection" action.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings
from ..langflow_client import LangflowClient

router = APIRouter()


class HealthResponse(BaseModel):
    status: Literal["ok"]
    langflow: Literal["reachable", "unreachable"]


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    tags=["Health"],
)
async def health() -> HealthResponse:
    """Returns ``{"status": "ok", "langflow": "reachable"|"unreachable"}``.

    Always returns HTTP 200 — callers should inspect the ``langflow`` field
    to decide whether remote AI features are available.
    """
    client = LangflowClient(settings)
    langflow_ok = await client.health_check()
    return HealthResponse(
        status="ok",
        langflow="reachable" if langflow_ok else "unreachable",
    )
