"""
Cryptographic utilities for Alfred Agent Core.

This module provides secure encryption/decryption services for sensitive data,
particularly OAuth tokens. Uses Fernet symmetric encryption with key rotation support.

Security features:
- Fernet encryption (AES 128 in CBC mode with HMAC-SHA256 authentication)
- Key rotation support with versioning
- Environment-based key management
- No plaintext token logging (automatic redaction)
"""

import os
from typing import List, Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class CryptoServiceError(Exception):
    """Base exception for CryptoService operations."""

    pass


class InvalidKeyVersionError(CryptoServiceError):
    """Raised when an invalid key version is requested."""

    pass


class DecryptionError(CryptoServiceError):
    """Raised when decryption fails (invalid ciphertext, wrong key, etc.)."""

    pass


class CryptoService:
    """
    Provides secure encryption/decryption services with automatic key rotation support.

    This service uses MultiFernet for seamless key rotation without manual version management.
    MultiFernet tries all keys during decryption and always uses the first (newest) key for encryption.

    Key Management:
    - Primary key from FERNET_KEY environment variable (newest, used for encryption)
    - Additional old keys from FERNET_KEYS for backward compatibility (comma-separated)
    - Automatic key rotation with no version tracking needed

    Usage:
        crypto = CryptoService()
        ciphertext = crypto.encrypt_token("sensitive-token")
        plaintext = crypto.decrypt_token(ciphertext)
    """

    def __init__(self, primary_key_b64: Optional[str] = None):
        """Initialize CryptoService with a provided key or environment variables.

        Args:
            primary_key_b64: Optional base64-encoded Fernet key. If not provided,
                           will load from FERNET_KEY environment variable.
        """
        # Use provided key or load from environment
        primary_key_b64 = primary_key_b64 or os.getenv("FERNET_KEY")
        if not primary_key_b64:
            raise CryptoServiceError(
                "FERNET_KEY environment variable is required. "
                "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )

        keys: List[Fernet] = []

        try:
            # Add primary key first (MultiFernet uses first key for encryption)
            primary_key = Fernet(primary_key_b64.encode())
            keys.append(primary_key)
        except Exception as e:
            raise CryptoServiceError(f"Invalid FERNET_KEY: {e}") from e

        # Load additional old keys from FERNET_KEYS for decryption compatibility
        # Format: comma-separated base64 keys (no version numbers needed)
        # Example: "old_key_1,old_key_2,old_key_3"
        additional_keys = os.getenv("FERNET_KEYS", "")
        if additional_keys:
            keys.extend(self._load_additional_keys(additional_keys))

        # Create MultiFernet with all keys (first key used for encryption, all tried for decryption)
        self._multi_fernet = MultiFernet(keys)
        self._key_count = len(keys)

    def _load_additional_keys(self, keys_string: str) -> List[Fernet]:
        """Load additional keys for rotation from environment variable."""
        additional_keys = []

        for key_b64 in keys_string.split(","):
            key_b64 = key_b64.strip()
            if not key_b64:
                continue

            try:
                additional_keys.append(Fernet(key_b64.encode()))
            except Exception as e:
                raise CryptoServiceError(f"Invalid key in FERNET_KEYS: {e}") from e

        return additional_keys

    def encrypt_token(self, plaintext_token: str) -> bytes:
        """
        Encrypt a token using MultiFernet (automatically uses newest key).

        Args:
            plaintext_token: The token to encrypt (will not be logged)

        Returns:
            Encrypted token bytes

        Raises:
            CryptoServiceError: If encryption fails

        Security: The plaintext token is never logged or stored in memory longer than necessary.
        """
        if not plaintext_token:
            raise CryptoServiceError("Cannot encrypt empty token")

        try:
            # MultiFernet automatically uses the first (newest) key for encryption
            ciphertext = self._multi_fernet.encrypt(plaintext_token.encode("utf-8"))

            # Clear plaintext from memory (best effort)
            plaintext_token = "REDACTED"

            return ciphertext

        except Exception as e:
            raise CryptoServiceError(f"Encryption failed: {e}") from e

    def decrypt_token(self, ciphertext: bytes) -> str:
        """
        Decrypt a token using MultiFernet (automatically tries all keys).

        Args:
            ciphertext: The encrypted token bytes

        Returns:
            The decrypted plaintext token

        Raises:
            DecryptionError: If decryption fails with all available keys
        """
        if not ciphertext:
            raise DecryptionError("Cannot decrypt empty ciphertext")

        try:
            # MultiFernet automatically tries all keys until one works
            plaintext_bytes = self._multi_fernet.decrypt(ciphertext)
            return plaintext_bytes.decode("utf-8")

        except InvalidToken as e:
            raise DecryptionError(
                f"Failed to decrypt token with any of the {self._key_count} available keys. "
                "Token may be corrupted or encrypted with a key not in the current key set."
            ) from e
        except Exception as e:
            raise DecryptionError(f"Decryption failed: {e}") from e

    def get_key_count(self) -> int:
        """Get the number of available keys (for monitoring and diagnostics)."""
        return self._key_count

    def rotate_token(self, old_ciphertext: bytes) -> bytes:
        """
        Re-encrypt data with the current (newest) key.

        This is useful for key rotation - MultiFernet will decrypt with any available key
        and re-encrypt with the newest (first) key.

        Args:
            old_ciphertext: Data encrypted with any available key

        Returns:
            New ciphertext encrypted with the current key

        Raises:
            DecryptionError: If decryption with any key fails
            CryptoServiceError: If re-encryption fails
        """
        # Decrypt with any available key, re-encrypt with newest key
        plaintext = self.decrypt_token(old_ciphertext)
        return self.encrypt_token(plaintext)


# Utility functions for key generation and validation


def generate_fernet_key() -> str:
    """
    Generate a new Fernet key for encryption.

    Returns:
        Base64-encoded Fernet key suitable for environment variables

    Usage:
        key = generate_fernet_key()
        # Set as FERNET_KEY=<key> in environment
    """
    return Fernet.generate_key().decode()


def redact_token_for_logging(token: str) -> str:
    """
    Redact a token for safe logging.

    Shows only the first 8 and last 4 characters for debugging purposes.

    Args:
        token: The token to redact

    Returns:
        Redacted token safe for logging

    Example:
        redact_token_for_logging("sk-ant-api-very-long-secret-key")
        # Returns: "sk-ant-a...key"
    """
    if not token or len(token) < 12:
        return "***REDACTED***"

    return f"{token[:8]}...{token[-4:]}"


# Global instance for dependency injection
_crypto_service: Optional[CryptoService] = None


def get_crypto_service() -> CryptoService:
    """
    Get the global CryptoService instance (singleton pattern).

    This provides a consistent interface for dependency injection throughout the application.
    The service is initialized once with environment variables.

    Returns:
        CryptoService instance

    Raises:
        CryptoServiceError: If service cannot be initialized
    """
    global _crypto_service

    if _crypto_service is None:
        _crypto_service = CryptoService()

    return _crypto_service
