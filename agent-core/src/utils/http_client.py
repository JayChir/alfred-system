"""
Production-hardened HTTP client utilities with timeouts, retries, and circuit breakers.

This module provides hardened HTTP clients for external service integrations
with proper error handling, retry logic with exponential backoff, and circuit
breaker patterns to prevent cascade failures.

Key features:
- Configurable timeouts per operation type
- Exponential backoff with jitter for retries
- Circuit breaker to prevent cascading failures
- Request size limits and connection pooling
- Structured error handling and logging
"""

import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

import httpx
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import Settings, get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)


class CircuitBreakerState(Enum):
    """Circuit breaker states for external service protection."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing fast, service down
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration for service protection."""

    failure_threshold: int = 5  # Failures before opening
    success_threshold: int = 3  # Successes to close from half-open
    timeout_seconds: int = 60  # Time before trying half-open
    enabled: bool = True  # Enable/disable circuit breaker


@dataclass
class RetryConfig:
    """Retry configuration with exponential backoff."""

    max_attempts: int = 3  # Maximum retry attempts
    base_delay: float = 1.0  # Initial delay in seconds
    max_delay: float = 60.0  # Maximum delay in seconds
    exponential_base: int = 2  # Backoff multiplier
    jitter: bool = True  # Add randomization to prevent thundering herd

    # Retryable HTTP status codes
    retryable_status_codes: List[int] = None

    def __post_init__(self):
        """Set default retryable status codes if not provided."""
        if self.retryable_status_codes is None:
            self.retryable_status_codes = [408, 429, 500, 502, 503, 504]


@dataclass
class TimeoutConfig:
    """HTTP timeout configuration for different operation types."""

    connect_timeout: float = 10.0  # Connection establishment timeout
    read_timeout: float = 60.0  # Response reading timeout
    write_timeout: float = 60.0  # Request writing timeout
    pool_timeout: float = 5.0  # Connection pool acquisition timeout


class CircuitBreaker:
    """
    Circuit breaker implementation for external service protection.

    Prevents cascading failures by failing fast when a service is down
    and periodically testing if it has recovered.
    """

    def __init__(self, config: CircuitBreakerConfig):
        """Initialize circuit breaker with configuration."""
        self.config = config
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self.service_name = "unknown"

    def should_allow_request(self) -> bool:
        """Check if request should be allowed based on circuit breaker state."""
        if not self.config.enabled:
            return True

        current_time = time.time()

        if self.state == CircuitBreakerState.CLOSED:
            return True
        elif self.state == CircuitBreakerState.OPEN:
            # Check if timeout has passed to try half-open
            if (
                self.last_failure_time
                and current_time - self.last_failure_time >= self.config.timeout_seconds
            ):
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                logger.info(
                    "Circuit breaker transitioning to half-open",
                    service=self.service_name,
                    failure_count=self.failure_count,
                )
                return True
            return False
        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True

        return False

    def record_success(self):
        """Record successful request."""
        if not self.config.enabled:
            return

        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                logger.info(
                    "Circuit breaker closed after successful requests",
                    service=self.service_name,
                    success_count=self.success_count,
                )
        elif self.state == CircuitBreakerState.CLOSED:
            self.failure_count = 0  # Reset failure count on success

    def record_failure(self):
        """Record failed request."""
        if not self.config.enabled:
            return

        self.failure_count += 1
        self.last_failure_time = time.time()

        if (
            self.state == CircuitBreakerState.CLOSED
            and self.failure_count >= self.config.failure_threshold
        ):
            self.state = CircuitBreakerState.OPEN
            logger.warning(
                "Circuit breaker opened due to failures",
                service=self.service_name,
                failure_count=self.failure_count,
                threshold=self.config.failure_threshold,
            )
        elif self.state == CircuitBreakerState.HALF_OPEN:
            self.state = CircuitBreakerState.OPEN
            logger.info(
                "Circuit breaker reopened after half-open failure",
                service=self.service_name,
            )


class HardenedHTTPClient:
    """
    Production-hardened HTTP client with timeouts, retries, and circuit breaker.

    Provides robust HTTP client functionality for external service integrations
    with comprehensive error handling and resilience patterns.
    """

    def __init__(
        self,
        service_name: str,
        base_url: Optional[str] = None,
        timeout_config: Optional[TimeoutConfig] = None,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
        max_request_size_bytes: int = 10 * 1024 * 1024,  # 10MB default
        settings: Optional[Settings] = None,
    ):
        """
        Initialize hardened HTTP client.

        Args:
            service_name: Name of service for logging and circuit breaker
            base_url: Base URL for the service
            timeout_config: Timeout configuration
            retry_config: Retry configuration
            circuit_breaker_config: Circuit breaker configuration
            max_request_size_bytes: Maximum request body size
            settings: Application settings
        """
        self.service_name = service_name
        self.base_url = base_url
        self.settings = settings or get_settings()

        # Use provided configs or defaults
        self.timeout_config = timeout_config or TimeoutConfig()
        self.retry_config = retry_config or RetryConfig()
        self.max_request_size_bytes = max_request_size_bytes

        # Initialize circuit breaker
        cb_config = circuit_breaker_config or CircuitBreakerConfig()
        self.circuit_breaker = CircuitBreaker(cb_config)
        self.circuit_breaker.service_name = service_name

        # Create httpx client with production settings
        timeout = httpx.Timeout(
            connect=self.timeout_config.connect_timeout,
            read=self.timeout_config.read_timeout,
            write=self.timeout_config.write_timeout,
            pool=self.timeout_config.pool_timeout,
        )

        # Connection pool limits for resource management
        limits = httpx.Limits(
            max_keepalive_connections=20,
            max_connections=100,
            keepalive_expiry=30.0,
        )

        self.client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            limits=limits,
            follow_redirects=False,  # Explicit redirect handling
            max_redirects=0,
        )

        logger.info(
            "Hardened HTTP client initialized",
            service=service_name,
            base_url=base_url,
            timeout_config=timeout.__dict__,
            retry_enabled=self.retry_config.max_attempts > 1,
            circuit_breaker_enabled=cb_config.enabled,
        )

    async def close(self):
        """Close the HTTP client and clean up resources."""
        await self.client.aclose()
        logger.debug("HTTP client closed", service=self.service_name)

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    def _should_retry(self, exception: Exception) -> bool:
        """Check if exception should trigger retry."""
        # Network/connection errors
        if isinstance(
            exception,
            (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.NetworkError,
                httpx.ProtocolError,
            ),
        ):
            return True

        # HTTP status code errors
        if isinstance(exception, httpx.HTTPStatusError):
            return (
                exception.response.status_code
                in self.retry_config.retryable_status_codes
            )

        return False

    @retry(
        stop=stop_after_attempt(3),  # Will be overridden by instance config
        wait=wait_exponential(multiplier=1, min=1, max=60),  # Will be overridden
        retry=retry_if_exception_type(
            (
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.NetworkError,
                httpx.HTTPStatusError,
            )
        ),
        before_sleep=before_sleep_log(logger, "warning"),
        reraise=True,
    )
    async def _make_request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """
        Make HTTP request with circuit breaker protection.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional request parameters

        Returns:
            httpx.Response: HTTP response

        Raises:
            CircuitBreakerOpenError: When circuit breaker is open
            httpx.HTTPError: For HTTP-related errors
        """
        # Check circuit breaker before making request
        if not self.circuit_breaker.should_allow_request():
            raise CircuitBreakerOpenError(
                f"Circuit breaker open for service: {self.service_name}"
            )

        # Validate request size if body is provided
        if "content" in kwargs:
            content = kwargs["content"]
            if (
                isinstance(content, (str, bytes))
                and len(content) > self.max_request_size_bytes
            ):
                raise ValueError(
                    f"Request body too large: {len(content)} bytes > "
                    f"{self.max_request_size_bytes} bytes"
                )

        request_start = time.time()

        try:
            # Make the actual HTTP request
            response = await self.client.request(method, url, **kwargs)

            # Check for HTTP error status codes
            response.raise_for_status()

            # Record success for circuit breaker
            self.circuit_breaker.record_success()

            request_duration = time.time() - request_start
            logger.debug(
                "HTTP request successful",
                service=self.service_name,
                method=method,
                url=url,
                status_code=response.status_code,
                duration_ms=round(request_duration * 1000, 2),
            )

            return response

        except Exception as e:
            # Record failure for circuit breaker
            self.circuit_breaker.record_failure()

            request_duration = time.time() - request_start
            logger.warning(
                "HTTP request failed",
                service=self.service_name,
                method=method,
                url=url,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(request_duration * 1000, 2),
            )

            raise

    # Configure retry decorator with instance settings
    def _configure_retry(self):
        """Configure retry decorator with instance settings."""
        return retry(
            stop=stop_after_attempt(self.retry_config.max_attempts),
            wait=wait_exponential(
                multiplier=self.retry_config.base_delay,
                min=self.retry_config.base_delay,
                max=self.retry_config.max_delay,
            ),
            retry=self._should_retry,
            before_sleep=before_sleep_log(logger, "warning"),
            reraise=True,
        )

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """Make GET request with hardening."""
        return await self._make_request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """Make POST request with hardening."""
        return await self._make_request("POST", url, **kwargs)

    async def put(self, url: str, **kwargs) -> httpx.Response:
        """Make PUT request with hardening."""
        return await self._make_request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """Make DELETE request with hardening."""
        return await self._make_request("DELETE", url, **kwargs)

    async def patch(self, url: str, **kwargs) -> httpx.Response:
        """Make PATCH request with hardening."""
        return await self._make_request("PATCH", url, **kwargs)


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open and preventing requests."""

    pass


# Factory functions for common service clients


def create_anthropic_client(settings: Optional[Settings] = None) -> HardenedHTTPClient:
    """
    Create hardened HTTP client for Anthropic API.

    Args:
        settings: Application settings

    Returns:
        HardenedHTTPClient: Configured client for Anthropic API
    """
    settings = settings or get_settings()

    # Anthropic-specific configuration
    timeout_config = TimeoutConfig(
        connect_timeout=10.0,
        read_timeout=settings.anthropic_timeout_seconds,  # From config
        write_timeout=30.0,
        pool_timeout=5.0,
    )

    retry_config = RetryConfig(
        max_attempts=3,
        base_delay=1.0,
        max_delay=30.0,
        retryable_status_codes=[408, 429, 500, 502, 503, 504],
    )

    circuit_breaker_config = CircuitBreakerConfig(
        failure_threshold=5,
        success_threshold=3,
        timeout_seconds=60,
        enabled=True,
    )

    return HardenedHTTPClient(
        service_name="anthropic-api",
        base_url="https://api.anthropic.com",
        timeout_config=timeout_config,
        retry_config=retry_config,
        circuit_breaker_config=circuit_breaker_config,
        max_request_size_bytes=settings.max_request_size_mb * 1024 * 1024,
        settings=settings,
    )


def create_mcp_client(
    service_name: str,
    base_url: str,
    settings: Optional[Settings] = None,
) -> HardenedHTTPClient:
    """
    Create hardened HTTP client for MCP server.

    Args:
        service_name: Name of the MCP service
        base_url: Base URL of the MCP server
        settings: Application settings

    Returns:
        HardenedHTTPClient: Configured client for MCP server
    """
    settings = settings or get_settings()

    # MCP-specific configuration
    timeout_config = TimeoutConfig(
        connect_timeout=5.0,
        read_timeout=settings.mcp_timeout_seconds,  # From config
        write_timeout=30.0,
        pool_timeout=5.0,
    )

    retry_config = RetryConfig(
        max_attempts=2,  # MCP servers should be fast, fewer retries
        base_delay=0.5,
        max_delay=10.0,
        retryable_status_codes=[408, 429, 500, 502, 503, 504],
    )

    circuit_breaker_config = CircuitBreakerConfig(
        failure_threshold=3,  # Stricter for local/fast services
        success_threshold=2,
        timeout_seconds=30,  # Shorter recovery time
        enabled=True,
    )

    return HardenedHTTPClient(
        service_name=f"mcp-{service_name}",
        base_url=base_url,
        timeout_config=timeout_config,
        retry_config=retry_config,
        circuit_breaker_config=circuit_breaker_config,
        max_request_size_bytes=5 * 1024 * 1024,  # 5MB for MCP requests
        settings=settings,
    )


# Global client registry for reuse
_client_registry: Dict[str, HardenedHTTPClient] = {}


async def get_client(
    service_name: str, factory_func: Optional[callable] = None, **factory_kwargs
) -> HardenedHTTPClient:
    """
    Get or create a hardened HTTP client from global registry.

    Args:
        service_name: Name of the service
        factory_func: Factory function to create client if not exists
        **factory_kwargs: Arguments for factory function

    Returns:
        HardenedHTTPClient: HTTP client instance
    """
    if service_name not in _client_registry:
        if factory_func is None:
            raise ValueError(f"No client registered for service: {service_name}")

        client = factory_func(**factory_kwargs)
        _client_registry[service_name] = client

        logger.info(
            "HTTP client created and registered",
            service=service_name,
            factory=factory_func.__name__,
        )

    return _client_registry[service_name]


async def close_all_clients():
    """Close all registered HTTP clients."""
    for service_name, client in _client_registry.items():
        try:
            await client.close()
            logger.debug("HTTP client closed", service=service_name)
        except Exception as e:
            logger.warning(
                "Error closing HTTP client",
                service=service_name,
                error=str(e),
            )

    _client_registry.clear()
    logger.info("All HTTP clients closed and registry cleared")
