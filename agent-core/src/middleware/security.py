"""
Security headers middleware for production hardening.

Implements comprehensive security headers following modern web security best practices:
- Content Security Policy (CSP) with per-path configuration
- HSTS for HTTPS production environments
- CORP/COEP headers for isolation
- Security headers (X-Content-Type-Options, X-Frame-Options, etc.)
- Development/production modes with appropriate relaxation

Key features:
- Per-path CSP configuration (docs vs API vs SSE)
- Report-only mode for initial CSP deployment
- Production HSTS with subdomain support
- Development mode relaxation
- No deprecated headers (X-XSS-Protection removed)
"""

from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from src.config import Settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Security headers middleware with path-aware CSP configuration.

    Applies comprehensive security headers to all responses with
    intelligent per-path configuration for different endpoint types.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        """
        Initialize security headers middleware.

        Args:
            app: ASGI application
            settings: Application settings with security configuration
        """
        super().__init__(app)
        self.settings = settings

        logger.info(
            "Security headers middleware initialized",
            security_headers_enabled=settings.security_headers_enabled,
            hsts_enabled=settings.hsts_enabled,
            csp_enabled=settings.csp_enabled,
            csp_report_only=settings.csp_report_only,
            app_env=settings.app_env,
        )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """
        Add security headers to all responses.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware or route handler

        Returns:
            HTTP response with security headers added
        """
        # Process request through app
        response = await call_next(request)

        # Skip if security headers disabled
        if not self.settings.security_headers_enabled:
            return response

        # Add base security headers
        self._add_base_security_headers(response)

        # Add CSP headers if enabled
        if self.settings.csp_enabled:
            self._add_csp_header(request, response)

        # Add HSTS in production HTTPS environments
        if self._should_add_hsts():
            self._add_hsts_header(response)

        return response

    def _add_base_security_headers(self, response: Response) -> None:
        """
        Add base security headers to response.

        Args:
            response: HTTP response to modify
        """
        # Core security headers
        response.headers.update(
            {
                # Prevent MIME type sniffing
                "X-Content-Type-Options": "nosniff",
                # Prevent embedding in frames
                "X-Frame-Options": "DENY",
                # Control referrer information
                "Referrer-Policy": "strict-origin-when-cross-origin",
                # Restrict dangerous browser features
                "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
                # Cross-Origin isolation headers
                "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-origin",
            }
        )

        # Note: X-XSS-Protection removed as it's deprecated and can be harmful

    def _add_csp_header(self, request: Request, response: Response) -> None:
        """
        Add Content Security Policy header based on request path.

        Args:
            request: HTTP request for path context
            response: HTTP response to modify
        """
        path = request.url.path
        csp_directive = self._build_csp_for_path(path)

        # Use report-only mode if configured
        header_name = (
            "Content-Security-Policy-Report-Only"
            if self.settings.csp_report_only
            else "Content-Security-Policy"
        )

        response.headers[header_name] = csp_directive

    def _build_csp_for_path(self, path: str) -> str:
        """
        Build Content Security Policy directive for specific path.

        Args:
            path: Request path

        Returns:
            str: CSP directive string
        """
        # API documentation endpoints need relaxed CSP for inline styles/scripts
        if (
            path.startswith("/docs")
            or path.startswith("/redoc")
            or path == "/openapi.json"
        ):
            return self._get_docs_csp()

        # SSE endpoints need special handling for streaming
        elif path.startswith("/api/v1/chat/stream"):
            return self._get_sse_csp()

        # API endpoints get strict CSP
        elif path.startswith("/api/"):
            return self._get_api_csp()

        # Default strict CSP for everything else
        else:
            return self._get_default_csp()

    def _get_docs_csp(self) -> str:
        """Get CSP for API documentation (relaxed for inline styles)."""
        directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline'",  # Swagger/ReDoc needs inline scripts
            "style-src 'self' 'unsafe-inline'",  # Swagger/ReDoc needs inline styles
            "img-src 'self' data: https:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "media-src 'none'",
            "frame-ancestors 'none'",
        ]

        # Add development relaxations if enabled
        if self._is_development_mode():
            directives.append(
                "connect-src 'self' ws: wss:"
            )  # Allow dev server websockets

        return "; ".join(directives)

    def _get_sse_csp(self) -> str:
        """Get CSP for Server-Sent Events endpoints."""
        directives = [
            "default-src 'none'",  # Very restrictive for SSE
            "connect-src 'self'",  # Allow SSE connections
            "object-src 'none'",
            "frame-ancestors 'none'",
        ]
        return "; ".join(directives)

    def _get_api_csp(self) -> str:
        """Get strict CSP for API endpoints."""
        directives = [
            "default-src 'none'",
            # Merge into a single connect-src directive
            "connect-src 'self' https://api.anthropic.com https://*.artemsys.ai",
            "object-src 'none'",
            "frame-ancestors 'none'",
        ]
        return "; ".join(directives)

    def _get_default_csp(self) -> str:
        """Get default strict CSP."""
        directives = [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "media-src 'none'",
            "frame-ancestors 'none'",
        ]
        return "; ".join(directives)

    def _should_add_hsts(self) -> bool:
        """
        Determine if HSTS header should be added.

        Returns:
            bool: True if HSTS should be added
        """
        # Only add HSTS if explicitly enabled and in production
        return self.settings.hsts_enabled and self.settings.app_env == "production"

    def _add_hsts_header(self, response: Response) -> None:
        """
        Add HSTS header for HTTPS production environments.

        Args:
            response: HTTP response to modify
        """
        # HSTS with 1 year max-age and includeSubDomains
        # Note: Be very careful with includeSubDomains in production
        response.headers[
            "Strict-Transport-Security"
        ] = "max-age=31536000; includeSubDomains"

        logger.debug("HSTS header added (production HTTPS)")

    def _is_development_mode(self) -> bool:
        """
        Check if running in development mode with relaxed security.

        Returns:
            bool: True if development mode relaxations should apply
        """
        return (
            self.settings.app_env == "development"
            and self.settings.dev_mode_relaxed_security
        )


def create_security_headers_middleware(settings: Settings) -> Callable:
    """
    Factory function to create security headers middleware with settings injection.

    Args:
        settings: Application settings

    Returns:
        Configured security headers middleware class
    """

    class ConfiguredSecurityHeadersMiddleware(SecurityHeadersMiddleware):
        def __init__(self, app: ASGIApp):
            super().__init__(app, settings)

    return ConfiguredSecurityHeadersMiddleware
