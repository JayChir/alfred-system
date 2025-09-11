"""
Rate limiting middleware for Alfred Agent Core.

Integrates with FastAPI request pipeline to enforce rate limits before
route processing. Provides structured error responses with proper HTTP
headers and detailed logging for monitoring.

Features:
- Per-route rate limiting with different policies
- Secure API key identifier extraction and hashing
- Standard HTTP 429 responses with Retry-After headers
- Integration with existing logging and error handling patterns
- Support for trusted proxy headers (when configured)
"""

from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from src.services.rate_limiter import RateLimiterService, hash_api_key
from src.utils.logging import get_logger

logger = get_logger(__name__)


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for request rate limiting.

    Processes requests through rate limiter before routing, returning
    HTTP 429 responses for exceeded limits with proper headers and
    structured error responses.
    """

    def __init__(self, app: ASGIApp, service: RateLimiterService):
        """
        Initialize rate limiting middleware.

        Args:
            app: ASGI application
            service: Injected rate limiter service instance
        """
        super().__init__(app)
        self.rate_limiter = service

        # Paths that bypass rate limiting
        self.excluded_paths = {
            "/",  # Root info endpoint
            "/docs",  # OpenAPI documentation
            "/redoc",  # Alternative docs
            "/openapi.json",  # OpenAPI spec
        }

        logger.info("Rate limiting middleware initialized")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Process request through rate limiter before routing.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware or route handler

        Returns:
            HTTP response (429 if rate limited, otherwise from handler)
        """
        # Skip rate limiting for excluded paths and health checks
        if request.url.path in self.excluded_paths or request.url.path.startswith(
            "/healthz"
        ):
            return await call_next(request)

        # Extract secure identifier for rate limiting
        identifier = self._get_rate_limit_identifier(request)
        route_path = request.url.path

        # Check rate limit
        allowed, retry_after, metadata = await self.rate_limiter.check_rate_limit(
            identifier, route_path
        )

        if not allowed:
            # Rate limit exceeded - return 429 with structured response
            request_id = getattr(request.state, "request_id", "unknown")

            logger.warning(
                "Rate limit exceeded - blocking request",
                identifier=self._safe_identifier_log(identifier),
                route=route_path,
                method=request.method,
                retry_after=retry_after,
                limit=metadata.get("limit"),
                request_id=request_id,
            )

            return JSONResponse(
                status_code=429,
                headers={
                    "Retry-After": str(max(1, int(retry_after))),
                    "X-RateLimit-Limit": str(metadata.get("limit", 60)),
                    "X-RateLimit-Remaining": "0",
                    # Note: X-RateLimit-Reset omitted - not applicable to leaky bucket
                },
                content={
                    "error": "APP-429-RATE",
                    "message": f"Rate limit exceeded. Try again in {max(1, int(retry_after))} seconds.",
                    "origin": "app",
                    "requestId": request_id,
                    "retryAfter": max(1, int(retry_after)),
                    "limit": metadata.get("limit"),
                    "remaining": 0,
                    "route": metadata.get("route"),
                },
            )

        # Rate limit passed - log for monitoring (debug level for successful checks)
        if not metadata.get("rate_limited"):
            logger.debug(
                "Rate limit check passed",
                identifier=self._safe_identifier_log(identifier),
                route=route_path,
                remaining=metadata.get("remaining", 0),
                limit=metadata.get("limit", 60),
            )

        # Process request normally
        response = await call_next(request)

        # Add rate limit headers to successful responses
        if metadata and not metadata.get("rate_limited"):
            response.headers["X-RateLimit-Limit"] = str(metadata.get("limit", 60))
            response.headers["X-RateLimit-Remaining"] = str(
                metadata.get("remaining", 0)
            )
            # Note: X-RateLimit-Reset omitted - leaky bucket doesn't use fixed windows

        return response

    def _get_rate_limit_identifier(self, request: Request) -> str:
        """
        Extract secure identifier for rate limiting.

        Priority order:
        1. API key from Authorization header (hashed)
        2. Client IP address (with proxy header support if configured)

        Returns:
            str: Secure identifier safe for storage and logging
        """
        # Priority 1: API key from Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            raw_key = auth_header[7:].strip()
            if raw_key:  # Ensure non-empty key
                return hash_api_key(raw_key)

        # Priority 2: API key from query parameter (less secure, but supported)
        api_key = request.query_params.get("api_key")
        if api_key:
            return hash_api_key(api_key)

        # Priority 3: Client IP address
        client_ip = self._get_client_ip(request)
        return f"ip:{client_ip}"

    def _get_client_ip(self, request: Request) -> str:
        """
        Extract client IP address with proxy header support.

        Only trusts X-Forwarded-For if the application has been configured
        to trust proxy headers (detected by checking for proxy middleware).

        Args:
            request: FastAPI request object

        Returns:
            str: Client IP address
        """
        # Check if we should trust proxy headers
        # This is a simple heuristic - in production, you might want to
        # explicitly configure trusted proxy IPs
        trust_proxy = hasattr(request.app, "middleware_stack") and any(
            "TrustedHostMiddleware" in str(middleware)
            or "ProxyHeadersMiddleware" in str(middleware)
            for middleware in getattr(request.app, "middleware_stack", [])
        )

        if trust_proxy:
            # Trust X-Forwarded-For header
            forwarded_for = request.headers.get("x-forwarded-for")
            if forwarded_for:
                # Take first IP in chain (original client)
                client_ip = forwarded_for.split(",")[0].strip()
                return client_ip

        # Fallback to direct client IP
        return request.client.host if request.client else "unknown"

    def _safe_identifier_log(self, identifier: str) -> str:
        """
        Create safe version of identifier for logging.

        Since identifiers are already hashed (for API keys) or are IPs,
        they're generally safe to log. This method provides a consistent
        interface and could be enhanced for additional privacy if needed.

        Args:
            identifier: Rate limit identifier

        Returns:
            str: Safe version for logging
        """
        # For API key identifiers (already hashed), safe to log as-is
        if identifier.startswith("api:"):
            return identifier

        # For IP identifiers, could mask last octet if needed for privacy
        if identifier.startswith("ip:"):
            # Currently logging full IP, could be modified for privacy requirements
            return identifier

        return identifier


def create_rate_limiting_middleware(rate_limiter_service: RateLimiterService):
    """
    Factory function to create rate limiting middleware with injected service.

    This approach allows for clean dependency injection while maintaining
    compatibility with FastAPI's middleware system.

    Args:
        rate_limiter_service: Configured rate limiter service instance

    Returns:
        Middleware class configured with the service
    """

    class ConfiguredRateLimitingMiddleware(RateLimitingMiddleware):
        def __init__(self, app: ASGIApp):
            super().__init__(app, rate_limiter_service)

    return ConfiguredRateLimitingMiddleware
