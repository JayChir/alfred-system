"""
ASGI size limit middleware for production hardening.

This middleware implements request size limits using proper ASGI patterns
to handle both Content-Length and chunked transfer encoding scenarios.
Tracks actual bytes received rather than relying on headers alone.

Key features:
- ASGI-native implementation with receive wrapper
- Works with chunked transfers (no Content-Length header)
- Per-endpoint size limits for different use cases
- Immediate 413 response when limit exceeded
- Proper error structure with request correlation
"""

from typing import Callable, Dict

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.config import Settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SizeLimitMiddleware:
    """
    ASGI size limit middleware with per-endpoint configuration.

    Implements ASGI receive wrapper to track actual bytes received,
    working correctly with both Content-Length and chunked transfers.
    Provides different size limits for different endpoint types.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        """
        Initialize size limit middleware.

        Args:
            app: ASGI application
            settings: Application settings with size limit configuration
        """
        self.app = app
        self.default_max_bytes = settings.max_request_size_mb * 1024 * 1024

        # Per-endpoint size limits (in bytes)
        self.endpoint_limits: Dict[str, int] = {
            # Chat endpoints - moderate JSON payloads
            "/api/v1/chat": 5 * 1024 * 1024,  # 5MB for chat with history
            # Health endpoints - tiny responses
            "/healthz": 1024,  # 1KB
            # Future file endpoints could have higher limits
            # "/api/v1/upload": 50 * 1024 * 1024,  # 50MB for file uploads
        }

        logger.info(
            "Size limit middleware initialized",
            default_max_mb=settings.max_request_size_mb,
            endpoint_limits={
                k: f"{v//1024//1024}MB" for k, v in self.endpoint_limits.items()
            },
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ASGI application entry point with size limit enforcement.

        Args:
            scope: ASGI scope (connection info)
            receive: ASGI receive callable
            send: ASGI send callable
        """
        # Only apply size limits to HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Determine size limit for this endpoint
        path = scope.get("path", "")
        max_bytes = self._get_size_limit(path)

        # Create size-limiting receive wrapper
        size_limiter = SizeTrackingReceive(receive, max_bytes, path)

        try:
            await self.app(scope, size_limiter.receive, send)
        except PayloadTooLargeException as e:
            # Send 413 response when size limit exceeded
            await self._send_payload_too_large_response(
                send, scope, e.max_bytes, e.received_bytes, e.path
            )

    def _get_size_limit(self, path: str) -> int:
        """
        Get size limit for specific endpoint path.

        Args:
            path: Request path

        Returns:
            int: Size limit in bytes
        """
        # Check for exact path matches first
        if path in self.endpoint_limits:
            return self.endpoint_limits[path]

        # Check for prefix matches (e.g., /api/v1/chat/stream)
        for endpoint_path, limit in self.endpoint_limits.items():
            if path.startswith(endpoint_path):
                return limit

        # Return default limit
        return self.default_max_bytes

    def _extract_request_id(self, scope: dict) -> str:
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

    async def _send_payload_too_large_response(
        self, send: Send, scope: dict, max_bytes: int, received_bytes: int, path: str
    ) -> None:
        """
        Send 413 Payload Too Large response using ASGI send.

        Args:
            send: ASGI send callable
            scope: ASGI scope for extracting request ID
            max_bytes: Maximum allowed bytes
            received_bytes: Bytes received before limit hit
            path: Request path
        """
        # Extract request ID from headers
        request_id = self._extract_request_id(scope)

        logger.warning(
            "Request size limit exceeded",
            path=path,
            max_bytes=max_bytes,
            received_bytes=received_bytes,
            max_mb=round(max_bytes / 1024 / 1024, 1),
            received_mb=round(received_bytes / 1024 / 1024, 1),
            request_id=request_id,
        )

        # Create error response matching app error format
        error_response = {
            "error": "APP-413-PAYLOAD",
            "message": f"Request payload too large (max {max_bytes // 1024 // 1024}MB)",
            "origin": "app",
            "requestId": request_id,
            "maxBytes": max_bytes,
            "receivedBytes": received_bytes,
        }

        import json

        response_body = json.dumps(error_response).encode("utf-8")

        # Send HTTP response using ASGI protocol
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(response_body)).encode()],
                    [b"retry-after", b"60"],  # Suggest retry after 1 minute
                ],
            }
        )

        await send(
            {
                "type": "http.response.body",
                "body": response_body,
            }
        )


class PayloadTooLargeException(Exception):
    """Exception raised when request payload exceeds size limit."""

    def __init__(self, max_bytes: int, received_bytes: int, path: str):
        self.max_bytes = max_bytes
        self.received_bytes = received_bytes
        self.path = path
        super().__init__(f"Payload too large: {received_bytes} > {max_bytes} bytes")


class SizeTrackingReceive:
    """
    ASGI receive wrapper that tracks cumulative bytes received.

    Handles both Content-Length and chunked transfer scenarios by
    summing actual bytes from http.request.body messages.
    """

    def __init__(self, original_receive: Receive, max_bytes: int, path: str):
        """
        Initialize size tracking receive wrapper.

        Args:
            original_receive: Original ASGI receive callable
            max_bytes: Maximum bytes allowed
            path: Request path for logging
        """
        self.original_receive = original_receive
        self.max_bytes = max_bytes
        self.path = path
        self.bytes_received = 0

    async def receive(self) -> Message:
        """
        Receive ASGI message with size tracking.

        Returns:
            Message: ASGI message

        Raises:
            PayloadTooLargeException: When size limit exceeded
        """
        message = await self.original_receive()

        # Track bytes for HTTP request body messages
        if message["type"] == "http.request" and "body" in message:
            body = message.get("body", b"")
            self.bytes_received += len(body)

            # Check size limit
            if self.bytes_received > self.max_bytes:
                raise PayloadTooLargeException(
                    self.max_bytes, self.bytes_received, self.path
                )

        return message


def create_size_limit_middleware(settings: Settings) -> Callable:
    """
    Factory function to create size limit middleware with settings injection.

    Args:
        settings: Application settings

    Returns:
        Configured size limit middleware class
    """

    class ConfiguredSizeLimitMiddleware(SizeLimitMiddleware):
        def __init__(self, app: ASGIApp):
            super().__init__(app, settings)

    return ConfiguredSizeLimitMiddleware
