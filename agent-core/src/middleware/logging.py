"""
Logging middleware for request tracking and performance monitoring.

This middleware adds structured logging to all HTTP requests, including:
- Request ID generation and propagation
- Request/response timing
- Error tracking with context
- Route and method logging
"""

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.utils.logging import clear_request_context, get_logger, set_request_context

logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for structured request logging.

    Adds request tracking, timing, and contextual logging to all requests.
    Ensures every request has a unique ID for tracing through the system.
    """

    def __init__(self, app: ASGIApp):
        """Initialize the logging middleware."""
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process each request with logging context.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or route handler

        Returns:
            The HTTP response with added headers
        """
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())

        # Store request ID in request state for access in handlers
        request.state.request_id = request_id

        # Set up request context for logging
        start_time = time.time()
        set_request_context(
            request_id=request_id,
            method=request.method,
            path=str(request.url.path),
            client_ip=request.client.host if request.client else None,
            start_time=start_time,
        )

        # Log request start
        logger.info(
            "Request started",
            query_params=dict(request.query_params),
            headers={
                k: v
                for k, v in request.headers.items()
                if k.lower() not in ["authorization", "cookie"]
            },
        )

        # Track response status and handle errors
        response = None

        try:
            # Process the request
            response = await call_next(request)

            # Calculate request duration
            duration_ms = (time.time() - start_time) * 1000

            # Log successful request completion
            logger.info(
                "Request completed",
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

            # Add headers to response
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time"] = f"{duration_ms:.2f}ms"

            return response

        except Exception as e:
            # Log error with full context
            duration_ms = (time.time() - start_time) * 1000

            logger.error(
                "Request failed with exception",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 2),
                exc_info=True,  # Include stack trace
            )

            # Re-raise the exception for FastAPI's error handlers
            raise

        finally:
            # Clear request context to prevent memory leaks
            clear_request_context()


class PerformanceLoggingMiddleware(BaseHTTPMiddleware):
    """
    Specialized middleware for performance monitoring.

    Tracks detailed performance metrics including:
    - Database query times
    - External API call durations
    - Cache hit/miss ratios
    - Memory usage patterns
    """

    def __init__(self, app: ASGIApp, slow_request_threshold_ms: float = 1000):
        """
        Initialize performance logging middleware.

        Args:
            app: The ASGI application
            slow_request_threshold_ms: Threshold for logging slow requests (default 1000ms)
        """
        super().__init__(app)
        self.slow_request_threshold_ms = slow_request_threshold_ms

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Monitor request performance.

        Args:
            request: The incoming HTTP request
            call_next: The next middleware or route handler

        Returns:
            The HTTP response
        """
        # Track detailed timing
        start_time = time.perf_counter()

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Log slow requests with additional context
        if duration_ms > self.slow_request_threshold_ms:
            logger.warning(
                "Slow request detected",
                duration_ms=round(duration_ms, 2),
                threshold_ms=self.slow_request_threshold_ms,
                method=request.method,
                path=str(request.url.path),
                status_code=response.status_code,
            )

        # Add performance header
        response.headers["X-Server-Timing"] = f"total;dur={duration_ms:.2f}"

        return response
