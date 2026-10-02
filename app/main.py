"""VisionAssist Backend — FastAPI application entry point.

This module wires together all routers, configures structured logging,
and registers global exception handlers.

Architecture summary
─────────────────────
Flutter App  ──HTTPS──▶  FastAPI (this file)
                              │
                              ├─ POST /v1/qa/answer          → qa router
                              ├─ POST /v1/ingredients/summarize → ingredients router
                              └─ GET  /v1/health              → health router
                              │
                         LangflowClient  ──internal──▶  Langflow service
                                                              │
                                                        Configurable LLM
                                                    (watsonx / OpenAI / Ollama)
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .routers import health, ingredients, qa

# ────────────────────────────────────────────────────────────────────────────
# Logging setup
# ────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Lifespan
# ────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("startup", extra={"provider": settings.model_provider})
    yield
    logger.info("shutdown")


# ────────────────────────────────────────────────────────────────────────────
# Application factory
# ────────────────────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="VisionAssist Backend",
        description=(
            "AI proxy that powers the optional remote features of the "
            "VisionAssist AI Flutter app. "
            "All LLM credentials live only in this service — none are "
            "bundled in the mobile app."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # ---------------------------------------------------------------------- #
    # CORS — locked down to no origins by default.
    # The Flutter mobile app does not require CORS (not a browser).
    # To add a web origin, set the ``CORS_ORIGINS`` env var or edit here:
    #   origins=["https://your-web-app.example.com"]
    # ---------------------------------------------------------------------- #
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------------------------------------------------------------------- #
    # Request-latency logging middleware
    # Logs: method, path, status_code, latency_ms — never request body content.
    # ---------------------------------------------------------------------- #
    @app.middleware("http")
    async def log_requests(request: Request, call_next: object) -> Response:
        start = time.perf_counter()
        response: Response = await call_next(request)  # type: ignore[operator]
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        return response

    # ---------------------------------------------------------------------- #
    # Global exception handlers
    # ---------------------------------------------------------------------- #
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("unhandled_exception", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected error occurred."},
        )

    # ---------------------------------------------------------------------- #
    # Routers
    # ---------------------------------------------------------------------- #
    app.include_router(health.router, prefix="/v1")
    app.include_router(qa.router, prefix="/v1")
    app.include_router(ingredients.router, prefix="/v1")

    return app


app = create_app()
