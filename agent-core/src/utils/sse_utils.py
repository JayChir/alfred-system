"""
SSE (Server-Sent Events) utility functions for proper wire format.

This module provides utilities for formatting SSE messages correctly
to avoid common bugs with newlines and ensure proper event structure.
"""

import json
from typing import Any, Dict, Optional


def sse_format(
    data: Any,
    event: Optional[str] = None,
    id: Optional[str] = None,
    retry: Optional[int] = None,
) -> str:
    """
    Format data as a Server-Sent Event with proper wire format.

    This helper ensures proper SSE formatting to avoid common bugs:
    - Handles multi-line data correctly
    - Ensures double newline termination
    - Properly escapes newlines in JSON data
    - Supports all SSE fields (event, data, id, retry)

    Args:
        data: The data to send (will be JSON-encoded if not a string)
        event: Optional event type (e.g., "token", "done", "error")
        id: Optional event ID for client-side tracking
        retry: Optional retry interval in milliseconds

    Returns:
        Properly formatted SSE message string

    Examples:
        >>> sse_format({"content": "Hello"}, event="token")
        'event: token\\ndata: {"content": "Hello"}\\n\\n'

        >>> sse_format("heartbeat", event="ping")
        'event: ping\\ndata: heartbeat\\n\\n'
    """
    lines = []

    # Add event type if specified
    if event:
        lines.append(f"event: {event}")

    # Add ID if specified (for client-side duplicate detection)
    if id:
        lines.append(f"id: {id}")

    # Add retry interval if specified (tells client when to reconnect)
    if retry is not None:
        lines.append(f"retry: {retry}")

    # Format data (JSON encode if not already a string)
    if isinstance(data, str):
        data_str = data
    else:
        # JSON encode with no newlines to avoid SSE format issues
        data_str = json.dumps(data, separators=(",", ":"))

    # Handle multi-line data (each line needs "data: " prefix)
    for line in data_str.split("\n"):
        lines.append(f"data: {line}")

    # SSE events must end with double newline
    return "\n".join(lines) + "\n\n"


def sse_heartbeat() -> str:
    """
    Generate a minimal SSE heartbeat comment.

    Uses SSE comment format (line starting with ':') which is
    more efficient than sending full events and is ignored by
    the EventSource API but keeps the connection alive.

    Returns:
        SSE comment for keepalive

    Example:
        >>> sse_heartbeat()
        ': hb\\n\\n'
    """
    return ": hb\n\n"


def sse_retry(interval_ms: int = 5000) -> str:
    """
    Generate an SSE retry directive.

    Tells the client how long to wait before reconnecting
    if the connection is lost.

    Args:
        interval_ms: Retry interval in milliseconds (default 5000ms = 5s)

    Returns:
        SSE retry directive

    Example:
        >>> sse_retry(3000)
        'retry: 3000\\n\\n'
    """
    return f"retry: {interval_ms}\n\n"


def truncate_tool_data(
    data: Dict[str, Any],
    max_length: int = 1000,
    fields_to_truncate: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Truncate tool call data to prevent excessive streaming.

    Protects against tools that return massive amounts of data
    by truncating specified fields to a maximum length.

    Args:
        data: Tool call or result data
        max_length: Maximum length for truncated fields
        fields_to_truncate: List of field names to truncate (default: ["result", "output", "content"])

    Returns:
        Data with truncated fields

    Example:
        >>> data = {"tool": "search", "result": "x" * 2000}
        >>> truncated = truncate_tool_data(data)
        >>> len(truncated["result"])
        1003  # 1000 chars + "..."
    """
    if fields_to_truncate is None:
        fields_to_truncate = ["result", "output", "content", "response", "data"]

    # Create a copy to avoid modifying original
    result = data.copy()

    for field in fields_to_truncate:
        if field in result:
            value = result[field]

            # Handle string truncation
            if isinstance(value, str) and len(value) > max_length:
                result[field] = value[:max_length] + "..."
                result[f"{field}_truncated"] = True
                result[f"{field}_original_length"] = len(value)

            # Handle nested dict/list by converting to string representation
            elif isinstance(value, (dict, list)):
                value_str = json.dumps(value)
                if len(value_str) > max_length:
                    # Just truncate the string representation, don't try to parse
                    result[field] = value_str[:max_length] + "..."
                    result[f"{field}_truncated"] = True
                    result[f"{field}_original_length"] = len(value_str)

    return result


def redact_sensitive_fields(
    data: Dict[str, Any],
    sensitive_patterns: Optional[list] = None,
) -> Dict[str, Any]:
    """
    Redact sensitive information from tool data before streaming.

    Replaces sensitive field values with redacted placeholders
    to prevent leaking secrets through SSE streams.

    Args:
        data: Tool call or result data
        sensitive_patterns: Patterns to redact (default: common secret field names)

    Returns:
        Data with sensitive fields redacted

    Example:
        >>> data = {"tool": "api_call", "api_key": "sk-123", "result": "ok"}
        >>> redacted = redact_sensitive_fields(data)
        >>> redacted["api_key"]
        '***REDACTED***'
    """
    if sensitive_patterns is None:
        sensitive_patterns = [
            "api_key",
            "api_secret",
            "token",
            "password",
            "secret",
            "private_key",
            "access_token",
            "refresh_token",
            "bearer",
            "authorization",
            "auth",
            "credential",
            "session_id",
            "ssn",
            "credit_card",
            "card_number",
            "cvv",
            "pin",
            "passwd",
            "pwd",
        ]

    def _redact_recursive(obj: Any) -> Any:
        """Recursively redact sensitive fields."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                # Check if key matches sensitive patterns
                key_lower = key.lower()
                if any(pattern in key_lower for pattern in sensitive_patterns):
                    result[key] = "***REDACTED***"
                else:
                    result[key] = _redact_recursive(value)
            return result
        elif isinstance(obj, list):
            return [_redact_recursive(item) for item in obj]
        else:
            return obj

    return _redact_recursive(data)
