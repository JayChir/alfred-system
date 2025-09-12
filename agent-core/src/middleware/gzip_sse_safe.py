"""
GZip middleware with SSE exclusion for production hardening.

Extends Starlette's GZipMiddleware to exclude Server-Sent Events endpoints
from compression, as SSE requires immediate response streaming and compression
interferes with the real-time nature of event streams.

Key features:
- Excludes text/event-stream responses from compression
- Excludes SSE endpoints by path matching
- Maintains all other GZip functionality unchanged
- Production-ready with proper logging
"""

from typing import List

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

from src.utils.logging import get_logger

logger = get_logger(__name__)


class SSESafeGZipMiddleware(GZipMiddleware):
    """
    GZip middleware that excludes SSE endpoints from compression.

    Server-Sent Events require immediate streaming without buffering,
    which compression middleware interferes with. This middleware
    excludes SSE paths and content types from compression.
    """

    def __init__(self, app: ASGIApp, minimum_size: int = 500, compresslevel: int = 6):
        """
        Initialize SSE-safe GZip middleware.

        Args:
            app: ASGI application
            minimum_size: Minimum response size to compress (bytes)
            compresslevel: Compression level (1-9, higher = more compression)
        """
        super().__init__(app, minimum_size, compresslevel)

        # SSE endpoints that should never be compressed
        self.sse_paths: List[str] = [
            "/api/v1/chat/stream",
        ]

        # Content types that should never be compressed
        self.sse_content_types: List[str] = [
            "text/event-stream",
            "application/stream+json",
        ]

        logger.info(
            "SSE-safe GZip middleware initialized",
            minimum_size=minimum_size,
            compresslevel=compresslevel,
            sse_exempt_paths=self.sse_paths,
            sse_exempt_content_types=self.sse_content_types,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        ASGI application entry point with SSE exclusion logic.

        Args:
            scope: ASGI scope (connection info)
            receive: ASGI receive callable
            send: ASGI send callable
        """
        # Skip compression for SSE endpoints
        if scope["type"] == "http" and self._is_sse_endpoint(scope):
            logger.debug(
                "Skipping GZip compression for SSE endpoint",
                path=scope.get("path", ""),
            )
            await self.app(scope, receive, send)
            return

        # Use parent GZip middleware for non-SSE requests
        await super().__call__(scope, receive, send)

    def _is_sse_endpoint(self, scope: Scope) -> bool:
        """
        Check if request is for an SSE endpoint.

        Args:
            scope: ASGI scope

        Returns:
            bool: True if request is for SSE endpoint
        """
        path = scope.get("path", "")

        # Check path-based exclusions
        for sse_path in self.sse_paths:
            if path.startswith(sse_path):
                return True

        # Check Accept header for SSE content type
        headers = dict(scope.get("headers", []))
        accept_header = headers.get(b"accept", b"").decode().lower()

        if "text/event-stream" in accept_header:
            return True

        return False
