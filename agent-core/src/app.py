"""
FastAPI application entry point for Alfred Agent Core.

This module initializes the FastAPI app with routers, middleware,
error handlers, and OpenAPI configuration. All configuration is loaded
from environment variables via the config module.
"""

import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config import Settings, get_settings
from src.routers import chat, health

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


def validate_configuration() -> Settings:
    """
    Load and validate application configuration.

    This function will exit the application if configuration is invalid,
    providing clear error messages about what's missing or incorrect.

    Returns:
        Settings: Validated configuration object
    """
    try:
        settings = get_settings()
        logger.info(
            "Configuration validated successfully",
            app_env=settings.app_env,
            app_version=settings.app_version,
            log_level=settings.log_level,
        )
        return settings
    except ValidationError as e:
        logger.error("Configuration validation failed")
        print("\n" + "=" * 60)
        print("CONFIGURATION ERROR - Application cannot start")
        print("=" * 60)

        for error in e.errors():
            field = ".".join(str(x) for x in error["loc"])
            msg = error["msg"]
            print(f"\n❌ {field}: {msg}")

            # Provide helpful hints for common issues
            if "api_key" in field.lower():
                print("   → Set API_KEY environment variable (min 32 chars)")
                print(
                    '   → Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
            elif "anthropic" in field.lower():
                print("   → Set ANTHROPIC_API_KEY environment variable")
                print("   → Get from: https://console.anthropic.com/account/keys")
            elif "database_url" in field.lower():
                print("   → Format: postgresql://user:password@host:port/database")

        print("\n" + "=" * 60)
        print("Fix the above errors in your .env file or environment variables")
        print("See .env.example for a complete template")
        print("=" * 60 + "\n")

        sys.exit(1)
    except Exception as e:
        logger.error("Unexpected error during configuration", error=str(e))
        print(f"\n❌ Unexpected configuration error: {e}")
        sys.exit(1)


# Load and validate configuration before creating app
settings = validate_configuration()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager for startup and shutdown tasks.

    Handles:
    - Configuration validation and logging
    - Resource initialization on startup
    - Cleanup on shutdown
    """
    # Startup tasks
    logger.info(
        f"Starting {settings.app_name}",
        version=settings.app_version,
        environment=settings.app_env,
    )

    # Log non-sensitive configuration
    settings.log_config()

    # Validate production requirements if applicable
    try:
        settings.validate_required_for_production()
    except ValueError as e:
        if settings.app_env == "production":
            logger.error("Production validation failed", error=str(e))
            sys.exit(1)

    # TODO: Initialize MCP connections, cache, etc. in future issues

    yield

    # Shutdown tasks
    logger.info(f"Shutting down {settings.app_name}...")
    # TODO: Close connections, flush cache, etc.


# Initialize FastAPI application with settings
app = FastAPI(
    title=settings.app_name,
    description="FastAPI-based AI agent with MCP routing, OAuth, and caching",
    version=settings.app_version,
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


# CORS configuration from environment settings
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # Loaded from environment
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
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
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/healthz",
    }
