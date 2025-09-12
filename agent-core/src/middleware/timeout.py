"""
ASGI timeout middleware for production hardening.

This middleware implements request timeouts using proper ASGI patterns
to avoid the limitations of BaseHTTPMiddleware (streaming issues, body buffering).
Uses anyio for cross-async-library compatibility and proper task cancellation.

Key features:
- ASGI-native implementation (no BaseHTTPMiddleware)
- SSE exemption for streaming endpoints
- Clean task cancellation with anyio.move_on_after
- 408 timeout responses with proper error structure
- Request ID correlation for debugging
"""

import time
from typing import Callable

import anyio
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import Settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class TimeoutMiddleware:
    """
    ASGI timeout middleware with SSE exemption.

    Implements proper ASGI pattern to avoid BaseHTTPMiddleware limitations:
    - No body buffering or streaming interference
    - Clean task cancellation using anyio.move_on_after
    - SSE endpoints exempt from timeout
    - Proper error responses with request correlation
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        """
        Initialize timeout middleware.

        Args:
            app: ASGI application
            settings: Application settings with timeout configuration
        """
        self.app = app
        self.timeout_seconds = settings.request_timeout_seconds
        self.sse_paths = ["/api/v1/chat/stream"]  # SSE endpoints exempt from timeout

        logger.info(
            "Timeout middleware initialized",
            timeout_seconds=self.timeout_seconds,
            sse_exempt_paths=self.sse_paths,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ASGI application entry point with timeout enforcement.

        Args:
            scope: ASGI scope (connection info)
            receive: ASGI receive callable
            send: ASGI send callable
        """
        # Only apply timeout to HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check if path is exempt from timeout (SSE streaming)
        path = scope.get("path", "")
        if self._is_sse_exempt(path):
            logger.debug("SSE endpoint exempt from timeout", path=path)
            await self.app(scope, receive, send)
            return

        # Apply timeout to non-exempt requests
        request_start_time = time.monotonic()
        request_id = self._extract_request_id(scope)

        try:
            # Use anyio for proper async task cancellation
            with anyio.move_on_after(self.timeout_seconds):
                await self.app(scope, receive, send)
                return

            # Timeout occurred - send 408 response
            await self._send_timeout_response(send, request_id, request_start_time)

        except Exception as e:
            # Handle any errors in timeout processing
            logger.error(
                "Timeout middleware error",
                error=str(e),
                error_type=type(e).__name__,
                request_id=request_id,
                path=path,
            )
            # Re-raise to let outer error handlers deal with it
            raise

    def _is_sse_exempt(self, path: str) -> bool:
        """
        Check if path is exempt from timeout (SSE streaming endpoints).

        Args:
            path: Request path

        Returns:
            bool: True if path should be exempt from timeout
        """
        return any(path.startswith(sse_path) for sse_path in self.sse_paths)

    def _extract_request_id(self, scope: Scope) -> str:
        """
        Extract request ID from ASGI scope for error correlation.

        Args:
            scope: ASGI scope

        Returns:
            str: Request ID from X-Request-ID header or 'unknown' if not found
        """
        # Extract request ID from X-Request-ID header
        headers = dict(scope.get("headers", []))
        request_id_header = headers.get(b"x-request-id")

        if request_id_header:
            return request_id_header.decode("utf-8", errors="ignore")

        # Fallback to unknown if header not present
        return "unknown"

    async def _send_timeout_response(
        self, send: Send, request_id: str, request_start_time: float
    ) -> None:
        """
        Send 408 timeout response using ASGI send.

        Args:
            send: ASGI send callable
            request_id: Request identifier for correlation
            request_start_time: When request processing started
        """
        elapsed_time = time.monotonic() - request_start_time

        logger.warning(
            "Request timeout exceeded",
            request_id=request_id,
            timeout_seconds=self.timeout_seconds,
            elapsed_seconds=round(elapsed_time, 2),
        )

        # Create timeout error response matching app error format
        error_response = {
            "error": "APP-408-TIMEOUT",
            "message": f"Request timeout ({self.timeout_seconds}s exceeded)",
            "origin": "app",
            "requestId": request_id,
            "timeout": self.timeout_seconds,
        }

        response_body = JSONResponse(
            content=error_response,
            status_code=408,
        ).body

        # Send HTTP response using ASGI protocol
        await send(
            {
                "type": "http.response.start",
                "status": 408,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(response_body)).encode()],
                    [b"x-timeout", str(self.timeout_seconds).encode()],
                ],
            }
        )

        await send(
            {
                "type": "http.response.body",
                "body": response_body,
            }
        )


def create_timeout_middleware(settings: Settings) -> Callable:
    """
    Factory function to create timeout middleware with settings injection.

    Args:
        settings: Application settings

    Returns:
        Configured timeout middleware class
    """

    class ConfiguredTimeoutMiddleware(TimeoutMiddleware):
        def __init__(self, app: ASGIApp):
            super().__init__(app, settings)

    return ConfiguredTimeoutMiddleware
