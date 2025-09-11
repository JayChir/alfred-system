"""
Rate limiting service with leaky bucket algorithm for Alfred Agent Core.

Provides thread-safe, monotonic-time-based rate limiting with per-route
policies and secure identifier handling. Uses in-memory storage with
automatic cleanup to prevent memory leaks.

Key features:
- Leaky bucket algorithm for smooth traffic shaping
- Per-route and per-API-key configuration
- SHA256-based secure API key handling
- Automatic bucket cleanup and DoS protection
- Monotonic time for clock-jump immunity
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from time import monotonic
from typing import Dict, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting policies."""

    requests_per_minute: int = 60  # Requests allowed per minute
    burst_capacity: int = 10  # Maximum tokens in leaky bucket
    enabled: bool = True  # Whether rate limiting is active

    def __post_init__(self):
        """Validate configuration values."""
        if self.requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        if self.burst_capacity <= 0:
            raise ValueError("burst_capacity must be positive")


@dataclass
class LeakyBucket:
    """
    Leaky bucket implementation for smooth rate limiting.

    Uses monotonic time to avoid issues with system clock adjustments.
    Thread-safe through external locking in RateLimiterService.
    """

    capacity: float  # Maximum tokens in bucket
    leak_rate: float  # Tokens per second leak rate
    tokens: float = 0.0  # Current token count
    last_update: float = field(
        default_factory=monotonic
    )  # Last update time (monotonic)

    def allow_request(self) -> tuple[bool, float]:
        """
        Check if request is allowed and return retry delay if rejected.

        Returns:
            tuple[bool, float]: (allowed, retry_after_seconds)
        """
        now = monotonic()
        elapsed = now - self.last_update

        # Leak tokens at configured rate
        self.tokens = max(0.0, self.tokens - elapsed * self.leak_rate)
        self.last_update = now

        # Check if bucket has capacity for new request
        if self.tokens + 1.0 <= self.capacity:
            self.tokens += 1.0
            return True, 0.0

        # Calculate wait time for next available slot
        retry_after = (self.tokens - self.capacity + 1.0) / self.leak_rate
        return False, max(0.0, retry_after)


class RateLimiterService:
    """
    Production-ready rate limiting service with comprehensive policies.

    Features:
    - Per-route rate limiting with different policies
    - Per-API-key overrides for trusted clients
    - Secure API key hashing for identifier safety
    - Memory management with LRU eviction and cleanup
    - Background cleanup to prevent memory leaks
    """

    def __init__(self):
        """Initialize rate limiter with default configuration."""
        self.buckets: Dict[str, LeakyBucket] = {}
        self.default_config = RateLimitConfig()
        self.route_configs: Dict[str, RateLimitConfig] = {}
        self.key_configs: Dict[str, RateLimitConfig] = {}  # Keyed by hashed identifier

        # DoS protection settings
        self.max_buckets = 10000
        self.cleanup_interval = 300  # 5 minutes

        # Background cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()  # Single async lock for all operations
        self._setup_default_route_policies()

    def _setup_default_route_policies(self):
        """Configure default per-route rate limiting policies."""
        # Chat endpoints - expensive AI operations
        self.route_configs["/api/v1/chat"] = RateLimitConfig(
            requests_per_minute=30, burst_capacity=5, enabled=True
        )

        # SSE streaming - very expensive, connection-creation only
        self.route_configs["/api/v1/chat/stream"] = RateLimitConfig(
            requests_per_minute=6, burst_capacity=2, enabled=True
        )

        # Device operations - lightweight
        self.route_configs["/api/v1/device"] = RateLimitConfig(
            requests_per_minute=120, burst_capacity=20, enabled=True
        )

        # Health checks - no limits
        self.route_configs["/healthz"] = RateLimitConfig(enabled=False)

    async def start(self):
        """Start background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            "Rate limiter service started",
            max_buckets=self.max_buckets,
            cleanup_interval=self.cleanup_interval,
            default_rpm=self.default_config.requests_per_minute,
            route_policies=len(self.route_configs),
        )

    async def stop(self):
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        logger.info("Rate limiter service stopped")

    def configure_route(self, route_path: str, config: RateLimitConfig):
        """Configure rate limits for specific route."""
        self.route_configs[route_path] = config
        logger.info(
            "Route rate limit configured",
            route=route_path,
            rpm=config.requests_per_minute,
            burst=config.burst_capacity,
            enabled=config.enabled,
        )

    def configure_key(self, hashed_identifier: str, config: RateLimitConfig):
        """Configure rate limits for specific API key (using hashed identifier)."""
        self.key_configs[hashed_identifier] = config
        logger.info(
            "API key rate limit configured",
            identifier=hashed_identifier,
            rpm=config.requests_per_minute,
            burst=config.burst_capacity,
            enabled=config.enabled,
        )

    def load_overrides_from_json(self, route_overrides: str, key_overrides: str):
        """Load rate limit overrides from JSON configuration strings."""
        # Parse route overrides
        if route_overrides and route_overrides != "{}":
            try:
                routes = json.loads(route_overrides)
                for route_path, limits in routes.items():
                    config = RateLimitConfig(
                        requests_per_minute=limits.get("requests_per_minute", 60),
                        burst_capacity=limits.get("burst_capacity", 10),
                        enabled=limits.get("enabled", True),
                    )
                    self.configure_route(route_path, config)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.error("Failed to parse route overrides", error=str(e))

        # Parse key overrides
        if key_overrides and key_overrides != "{}":
            try:
                keys = json.loads(key_overrides)
                for hashed_key, limits in keys.items():
                    config = RateLimitConfig(
                        requests_per_minute=limits.get("requests_per_minute", 60),
                        burst_capacity=limits.get("burst_capacity", 10),
                        enabled=limits.get("enabled", True),
                    )
                    self.configure_key(hashed_key, config)
            except (json.JSONDecodeError, ValueError, KeyError) as e:
                logger.error("Failed to parse key overrides", error=str(e))

    async def check_rate_limit(
        self, identifier: str, route: str = None
    ) -> tuple[bool, float, Dict]:
        """
        Check if request from identifier is allowed.

        Args:
            identifier: Secure identifier (hashed API key or IP)
            route: Request route path for policy selection

        Returns:
            tuple[bool, float, Dict]: (allowed, retry_after_seconds, metadata)
        """
        async with self._lock:
            # Get appropriate configuration
            config = self._get_config_for_route_and_key(identifier, route)

            if not config.enabled:
                return (
                    True,
                    0.0,
                    {"rate_limited": False, "reason": "disabled", "route": route},
                )

            # Create or get bucket for this identifier
            if identifier not in self.buckets:
                # Implement LRU eviction if approaching max buckets
                if len(self.buckets) >= self.max_buckets:
                    oldest_key = min(
                        self.buckets.keys(), key=lambda k: self.buckets[k].last_update
                    )
                    del self.buckets[oldest_key]
                    logger.warning(
                        "Rate limiter bucket evicted (LRU)",
                        evicted_key=self._safe_log_identifier(oldest_key),
                        bucket_count=len(self.buckets),
                    )

                # Create new leaky bucket
                leak_rate = config.requests_per_minute / 60.0  # Convert to per-second
                self.buckets[identifier] = LeakyBucket(
                    capacity=float(config.burst_capacity), leak_rate=leak_rate
                )

            # Check bucket for request allowance
            bucket = self.buckets[identifier]
            allowed, retry_after = bucket.allow_request()

            # Prepare response metadata
            metadata = {
                "rate_limited": not allowed,
                "retry_after": retry_after,
                "remaining": max(0, int(bucket.capacity - bucket.tokens)),
                "limit": config.requests_per_minute,
                "bucket_capacity": bucket.capacity,
                "bucket_tokens": bucket.tokens,
                "route": route,
            }

            # Log rate limit events for monitoring
            if not allowed:
                logger.warning(
                    "Rate limit exceeded",
                    identifier=self._safe_log_identifier(identifier),
                    route=route,
                    retry_after=retry_after,
                    limit=config.requests_per_minute,
                )
            else:
                logger.debug(
                    "Rate limit check passed",
                    identifier=self._safe_log_identifier(identifier),
                    route=route,
                    remaining=metadata["remaining"],
                )

            return allowed, retry_after, metadata

    def _get_config_for_route_and_key(
        self, identifier: str, route: str = None
    ) -> RateLimitConfig:
        """
        Get rate limit configuration with precedence order:
        1. Key-specific override (highest priority)
        2. Route-specific configuration
        3. Route prefix match (for nested routes)
        4. Default configuration (fallback)
        """
        # 1. Key-specific override
        if identifier in self.key_configs:
            return self.key_configs[identifier]

        # 2. Exact route match
        if route and route in self.route_configs:
            return self.route_configs[route]

        # 3. Route prefix matching (for sub-routes)
        if route:
            for route_pattern, config in self.route_configs.items():
                if route.startswith(route_pattern):
                    return config

        # 4. Default configuration
        return self.default_config

    def _safe_log_identifier(self, identifier: str) -> str:
        """
        Create safe version of identifier for logging.

        Since identifiers are already hashed, they're safe to log as-is.
        This method exists for consistency and future enhancement.
        """
        return identifier  # Already safe - identifiers are hashed

    async def _cleanup_loop(self):
        """Background task to remove unused buckets."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_buckets()
            except asyncio.CancelledError:
                logger.info("Rate limiter cleanup loop cancelled")
                break
            except Exception as e:
                logger.error(
                    "Rate limiter cleanup error",
                    error=str(e),
                    error_type=type(e).__name__,
                )

    async def _cleanup_buckets(self):
        """Remove buckets that haven't been used recently."""
        cutoff_time = monotonic() - (self.cleanup_interval * 2)  # 2x cleanup interval

        async with self._lock:
            expired_keys = [
                key
                for key, bucket in self.buckets.items()
                if bucket.last_update < cutoff_time
            ]

            for key in expired_keys:
                del self.buckets[key]

            if expired_keys:
                logger.info(
                    "Rate limiter bucket cleanup completed",
                    expired_count=len(expired_keys),
                    remaining_buckets=len(self.buckets),
                    cleanup_interval=self.cleanup_interval,
                )


def hash_api_key(raw_key: str) -> str:
    """
    Create secure, consistent identifier from raw API key.

    Uses SHA256 to create a collision-resistant hash that's safe
    to store and log while maintaining consistency across requests.

    Args:
        raw_key: Raw API key from Authorization header

    Returns:
        str: Safe identifier in format "api:hash_prefix"
    """
    h = hashlib.sha256(raw_key.encode()).hexdigest()
    return f"api:{h[:12]}"  # 12 chars provides good uniqueness


# Singleton instance for dependency injection
_rate_limiter_service: Optional[RateLimiterService] = None


def get_rate_limiter_service() -> RateLimiterService:
    """Get or create singleton rate limiter service instance."""
    global _rate_limiter_service

    if _rate_limiter_service is None:
        _rate_limiter_service = RateLimiterService()

    return _rate_limiter_service
