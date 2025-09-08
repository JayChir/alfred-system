"""
Device token generation and hashing utilities.

This module provides secure device token generation and validation utilities
for the device session system. Tokens use a consistent dtok_ prefix and
256-bit entropy for security.

Security considerations:
- Tokens are generated with cryptographically secure random data
- Only SHA-256 hashes are stored in the database (never raw tokens)
- Token format validation prevents processing invalid tokens
- Consistent prefix enables easy identification and routing
"""

import hashlib
import secrets
from typing import Final

# Consistent prefix for all device tokens
PREFIX: Final[str] = "dtok_"


def new_device_token() -> str:
    """
    Generate a new device token with 256-bit entropy.

    Uses cryptographically secure random generation with URL-safe base64
    encoding. The token format is: dtok_{32-byte-base64}

    Returns:
        Secure device token with dtok_ prefix

    Example:
        >>> token = new_device_token()
        >>> token.startswith("dtok_")
        True
        >>> len(token) > 40  # Prefix + 32 bytes base64 encoded
        True
    """
    # Generate 32 bytes (256 bits) of cryptographically secure random data
    # URL-safe base64 encoding produces ~43 characters from 32 bytes
    entropy = secrets.token_urlsafe(32)
    return f"{PREFIX}{entropy}"


def hash_device_token(token: str) -> bytes:
    """
    Hash device token for secure storage using SHA-256.

    Only the hash is stored in the database to prevent token leakage.
    The hash is exactly 32 bytes (256 bits) which matches our database
    schema for device_token_hash.

    Args:
        token: Raw device token starting with dtok_

    Returns:
        SHA-256 hash as 32 bytes

    Raises:
        ValueError: If token doesn't have correct prefix

    Example:
        >>> token = new_device_token()
        >>> hash_bytes = hash_device_token(token)
        >>> len(hash_bytes)
        32
    """
    if not token.startswith(PREFIX):
        raise ValueError(f"Device token must start with '{PREFIX}'")

    # UTF-8 encoding ensures consistent hashing across platforms
    return hashlib.sha256(token.encode("utf-8")).digest()


def validate_token_format(token: str) -> bool:
    """
    Validate token format without performing expensive hashing.

    Checks for correct prefix and reasonable structure without full
    cryptographic validation. Use this for early filtering before
    database queries.

    Args:
        token: Token string to validate

    Returns:
        True if token has valid format, False otherwise

    Example:
        >>> validate_token_format("dtok_abc123xyz")
        True
        >>> validate_token_format("invalid_token")
        False
        >>> validate_token_format("")
        False
    """
    if not token or not isinstance(token, str):
        return False

    if not token.startswith(PREFIX):
        return False

    # Extract the entropy portion after the prefix
    entropy_part = token[len(PREFIX) :]

    # Basic length check - should be substantial entropy
    if len(entropy_part) < 32:  # Minimum reasonable entropy length
        return False

    # Check that entropy contains only valid URL-safe base64 characters
    # URL-safe base64 uses: A-Z, a-z, 0-9, -, _
    valid_chars = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    return all(c in valid_chars for c in entropy_part)


def extract_token_prefix(token: str, prefix_length: int = 12) -> str:
    """
    Extract token prefix for safe logging and debugging.

    Returns only the beginning of the token for debugging purposes
    without exposing sensitive entropy. Used for structured logging
    where we need to correlate requests without security risks.

    Args:
        token: Full device token
        prefix_length: Number of characters to include (default: 12)

    Returns:
        Safe prefix string for logging

    Example:
        >>> token = "dtok_abcdefghijklmnopqrstuvwxyz123456"
        >>> extract_token_prefix(token)
        'dtok_abcdefg'
    """
    if not token or len(token) <= prefix_length:
        return token

    return token[:prefix_length] + "..."


# Type hints for external use
DeviceToken = str  # Type alias for device token strings
TokenHash = bytes  # Type alias for token hashes
