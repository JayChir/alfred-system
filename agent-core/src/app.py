"""
FastAPI application entry point for Alfred Agent Core.

This module initializes the FastAPI app with routers, middleware,
error handlers, and OpenAPI configuration.
"""

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.routers import chat, health

# Application version - update this for releases
APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown tasks.

    Handles:
    - Resource initialization on startup
    - Cleanup on shutdown
    """
    # Startup tasks
    print(f"Starting Alfred Agent Core v{APP_VERSION}...")
    # TODO: Initialize MCP connections, cache, etc. in future issues

    yield

    # Shutdown tasks
    print("Shutting down Alfred Agent Core...")
    # TODO: Close connections, flush cache, etc.


# Initialize FastAPI application
app = FastAPI(
    title="Alfred Agent Core",
    description="FastAPI-based AI agent with MCP routing, OAuth, and caching",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",  # OpenAPI UI
    redoc_url="/redoc",  # Alternative docs UI
)


# Middleware for request ID injection
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """
    Middleware to add a unique request ID to each request.
    This ID is used for tracing and debugging across logs.
    """
    # Generate or extract request ID
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

    # Store in request state for access in handlers
    request.state.request_id = request_id

    # Process request
    start_time = time.time()
    response = await call_next(request)

    # Add request ID and timing to response headers
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{(time.time() - start_time) * 1000:.2f}ms"

    return response


# CORS configuration for development
# TODO: Restrict origins in production (Week 4)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins in dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Custom exception handlers for structured errors
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """
    Handle HTTP exceptions with structured error responses.
    Follows the error taxonomy from the playbook.
    """
    request_id = getattr(request.state, "request_id", "unknown")

    # Map status codes to error codes
    error_code_map = {
        400: "APP-400-VALIDATION",
        401: "APP-401-AUTH",
        403: "APP-403-FORBIDDEN",
        404: "APP-404-NOT-FOUND",
        429: "APP-429-RATE",
        500: "APP-500-INTERNAL",
    }

    error_code = error_code_map.get(exc.status_code, f"APP-{exc.status_code}")

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": error_code,
            "message": exc.detail,
            "origin": "app",
            "requestId": request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle Pydantic validation errors with detailed feedback.
    """
    request_id = getattr(request.state, "request_id", "unknown")

    # Extract validation error details
    errors = []
    for error in exc.errors():
        errors.append(
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
        )

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "APP-400-VALIDATION",
            "message": "Request validation failed",
            "details": errors,
            "origin": "app",
            "requestId": request_id,
        },
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Catch-all handler for unhandled exceptions.
    Logs the error and returns a generic 500 response.
    """
    request_id = getattr(request.state, "request_id", "unknown")

    # TODO: Add proper logging with structlog (Issue #7)
    print(f"Unhandled exception for request {request_id}: {exc}")

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "APP-500-INTERNAL",
            "message": "An internal error occurred",
            "origin": "app",
            "requestId": request_id,
        },
    )


# Include routers
app.include_router(health.router, tags=["health"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])


# Root endpoint for basic info
@app.get("/", include_in_schema=False)
async def root() -> Dict[str, Any]:
    """Root endpoint providing basic service information."""
    return {
        "service": "Alfred Agent Core",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/healthz",
    }
