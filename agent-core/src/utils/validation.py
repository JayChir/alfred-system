"""
Validation utilities for token prefixes and other input validation.

This module provides helpers for validating API inputs, particularly
token prefixes to ensure clear naming and prevent confusion.
"""

from typing import Optional

from fastapi import HTTPException, status


def require_prefix(value: Optional[str], prefix: str, field_name: str) -> Optional[str]:
    """
    Validate that a token starts with the required prefix.

    Args:
        value: The token value to validate
        prefix: Required prefix (e.g., "dtok_", "thr_")
        field_name: Name of the field for error messages

    Returns:
        The validated value

    Raises:
        HTTPException: If value exists but doesn't have correct prefix
    """
    if value and not value.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} must start with '{prefix}'",
        )
    return value


def context_id_for_thread(thread_id: str) -> str:
    """
    Generate orchestrator context ID from thread ID.

    This creates a clear namespace for thread-backed contexts
    in the orchestrator's in-memory storage.

    Args:
        thread_id: Thread UUID

    Returns:
        Context ID in format "ctx:thread:{thread_id}"
    """
    return f"ctx:thread:{thread_id}"


def context_id_adhoc(request_id: str) -> str:
    """
    Generate context ID for non-threaded (adhoc) requests.

    Args:
        request_id: Request UUID

    Returns:
        Context ID in format "ctx:adhoc:{request_id}"
    """
    return f"ctx:adhoc:{request_id}"
