"""
Structured logging configuration for Alfred Agent Core.

This module provides centralized logging configuration using structlog,
with support for request context tracking, performance metrics, and
environment-specific formatting.
"""

import logging
import sys
import time
from contextvars import ContextVar
from typing import Any, Dict

import structlog
from structlog.types import EventDict, Processor

# Context variable for request-scoped data
request_context: ContextVar[Dict[str, Any]] = ContextVar(
    "request_context", default=None
)


class RequestContextProcessor:
    """
    Add request context to all log entries.

    This processor extracts request-scoped context (request_id, user_id, etc.)
    from context variables and adds them to every log entry within that request.
    """

    def __call__(
        self, logger: Any, method_name: str, event_dict: EventDict
    ) -> EventDict:
        """Add request context to the event dict."""
        ctx = request_context.get()
        if ctx is not None:
            # Add request context fields to the log entry
            event_dict.update(ctx)
        return event_dict


class PerformanceProcessor:
    """
    Add performance metrics to log entries.

    Calculates and adds timing information for requests and operations.
    """

    def __call__(
        self, logger: Any, method_name: str, event_dict: EventDict
    ) -> EventDict:
        """Add performance metrics if available."""
        # Add current timestamp if not present
        if "timestamp" not in event_dict:
            event_dict["timestamp"] = time.time()

        # Calculate duration if start_time is in context
        ctx = request_context.get()
        if ctx and "start_time" in ctx:
            duration_ms = (time.time() - ctx["start_time"]) * 1000
            event_dict["duration_ms"] = round(duration_ms, 2)

        return event_dict


class EnvironmentProcessor:
    """
    Add environment-specific fields to log entries.

    Includes app version, environment, and other deployment context.
    """

    def __init__(self, app_env: str, app_version: str):
        """Initialize with environment settings."""
        self.app_env = app_env
        self.app_version = app_version

    def __call__(
        self, logger: Any, method_name: str, event_dict: EventDict
    ) -> EventDict:
        """Add environment context to the event dict."""
        event_dict["env"] = self.app_env
        event_dict["version"] = self.app_version
        return event_dict


def filter_sensitive_data(
    logger: Any, method_name: str, event_dict: EventDict
) -> EventDict:
    """
    Filter sensitive data from log entries.

    Removes or masks sensitive fields like passwords, tokens, and API keys
    to prevent accidental exposure in logs.
    """
    sensitive_keys = [
        "password",
        "api_key",
        "secret",
        "authorization",
        "access_token",
        "refresh_token",
        "fernet_key",
        "jwt_secret",
        # Note: Removed generic "token" to allow token usage metrics
        # Only specific auth tokens are now redacted
    ]

    for key in list(event_dict.keys()):
        # Check if the key contains sensitive terms
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in sensitive_keys):
            if isinstance(event_dict[key], str) and len(event_dict[key]) > 8:
                # Mask the value, showing only first and last 4 characters
                value = event_dict[key]
                event_dict[key] = f"{value[:4]}...{value[-4:]}"
            else:
                event_dict[key] = "***REDACTED***"

    return event_dict


def configure_logging(
    log_level: str = "INFO",
    app_env: str = "development",
    app_version: str = "0.1.0",
    json_format: bool = None,
) -> None:
    """
    Configure structured logging for the application.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        app_env: Application environment (development, staging, production)
        app_version: Application version for tracking
        json_format: Force JSON output (None = auto-detect based on environment)

    This configures structlog with appropriate processors for the environment:
    - Development: Human-readable console output with colors
    - Production: JSON output for log aggregation systems
    """
    # Auto-detect JSON format based on environment if not specified
    if json_format is None:
        json_format = app_env in ["staging", "production"]

    # Configure Python's standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Build processor chain
    processors: list[Processor] = [
        # Add standard logging information
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        # Add callsite parameters (filename, line number, function)
        structlog.processors.CallsiteParameterAdder(
            parameters=[
                structlog.processors.CallsiteParameter.FILENAME,
                structlog.processors.CallsiteParameter.LINENO,
                structlog.processors.CallsiteParameter.FUNC_NAME,
            ]
        ),
        # Add custom processors
        RequestContextProcessor(),
        PerformanceProcessor(),
        EnvironmentProcessor(app_env, app_version),
        filter_sensitive_data,
        # Standard processors
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Add environment-specific renderer
    if json_format:
        # Production: JSON output for log aggregation
        processors.append(structlog.processors.JSONRenderer())
    else:
        # Development: Human-readable console output
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    # Configure structlog
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = None) -> structlog.stdlib.BoundLogger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (usually __name__ from the calling module)

    Returns:
        Configured structlog logger instance
    """
    return structlog.get_logger(name)


def set_request_context(**kwargs: Any) -> None:
    """
    Set request-scoped context that will be included in all logs.

    Args:
        **kwargs: Key-value pairs to add to request context
                 Common fields: request_id, user_id, session_id, method, path

    Example:
        set_request_context(request_id="123", user_id="456", method="POST", path="/api/chat")
    """
    ctx = request_context.get()
    if ctx is None:
        ctx = {}
    ctx.update(kwargs)
    request_context.set(ctx)


def clear_request_context() -> None:
    """Clear the request context (should be called at the end of each request)."""
    request_context.set(None)


def log_with_context(
    logger: structlog.stdlib.BoundLogger, level: str, message: str, **kwargs: Any
) -> None:
    """
    Log a message with additional context.

    Args:
        logger: The logger instance
        level: Log level (debug, info, warning, error)
        message: Log message
        **kwargs: Additional context to include in the log entry
    """
    log_method = getattr(logger, level.lower())
    log_method(message, **kwargs)
