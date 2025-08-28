"""
In-memory cache service with TTL support and future Redis compatibility.

This module provides a transport-agnostic cache interface using Python Protocol
for easy migration to Redis or other backends. The memory implementation uses
cachetools TTLCache with per-entry TTL support, singleflight pattern for
thundering herd prevention, and comprehensive metrics.
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from cachetools import TTLCache

from src.utils.logging import get_logger

logger = get_logger(__name__)


class InvokeCache(Protocol):
    """
    Transport-agnostic cache interface for tool invocation results.

    This Protocol defines the contract for cache implementations,
    allowing seamless migration between memory, Redis, or other backends.
    """

    async def get(
        self, key: str, *, max_age_s: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve a cached value if it exists and is fresh.

        Args:
            key: Cache key to look up
            max_age_s: Maximum acceptable age in seconds (None = use default TTL)

        Returns:
            Cached value dict if found and fresh, None otherwise
        """
        ...

    async def set(
        self,
        key: str,
        value: Dict[str, Any],
        *,
        ttl_s: int,
        labels: Optional[List[str]] = None,
    ) -> None:
        """
        Store a value in the cache with TTL.

        Args:
            key: Cache key for storage
            value: Value dict to cache
            ttl_s: Time-to-live in seconds
            labels: Optional labels for filtering/metrics (e.g., ["notion", "read"])
        """
        ...

    async def delete(self, key: str) -> None:
        """
        Remove a value from the cache.

        Args:
            key: Cache key to delete
        """
        ...

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring.

        Returns:
            Dict with hits, misses, hit_rate, entries, etc.
        """
        ...


@dataclass
class CacheEntry:
    """
    Wrapper for cached values with metadata.

    Stores the actual value along with expiration time and labels
    to support per-entry TTL and filtering.
    """

    value: Dict[str, Any]
    expires_at: float
    cached_at: float
    labels: List[str] = field(default_factory=list)

    def is_expired(self) -> bool:
        """Check if this entry has expired."""
        return time.time() > self.expires_at

    def age_seconds(self) -> float:
        """Get age of this entry in seconds."""
        return time.time() - self.cached_at

    def ttl_remaining(self) -> float:
        """Get remaining TTL in seconds."""
        return max(0, self.expires_at - time.time())


class MemoryInvokeCache:
    """
    In-memory cache implementation with TTL and singleflight support.

    Features:
    - Per-entry TTL support (different TTLs for different tools)
    - Singleflight pattern to prevent thundering herd
    - Size-based eviction with LRU when full
    - Comprehensive metrics for monitoring
    - Thread-safe operations with asyncio
    """

    def __init__(self, maxsize: int = 2000, default_ttl: int = 300):
        """
        Initialize memory cache with size limits.

        Args:
            maxsize: Maximum number of entries (LRU eviction when exceeded)
            default_ttl: Default TTL in seconds if not specified
        """
        # TTLCache provides size limit and global TTL (we handle per-entry TTL)
        self.store: TTLCache = TTLCache(maxsize=maxsize, ttl=3600)  # 1hr max global
        self.default_ttl = default_ttl

        # Singleflight tracking for concurrent requests
        self.singleflight: Dict[str, asyncio.Future] = {}

        # Statistics tracking
        self._stats = {
            "hits": 0,
            "misses": 0,
            "singleflight": 0,
            "evictions": 0,
            "sets": 0,
            "deletes": 0,
        }

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        logger.info(
            "Memory cache initialized", maxsize=maxsize, default_ttl=default_ttl
        )

    async def get(
        self, key: str, *, max_age_s: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached value if fresh.

        Args:
            key: Cache key to retrieve
            max_age_s: Maximum acceptable age (overrides entry TTL)

        Returns:
            Cached value with metadata if fresh, None if miss/expired
        """
        async with self._lock:
            # Check if key exists
            if key not in self.store:
                self._stats["misses"] += 1
                logger.debug("Cache miss", key=key[:32] + "...")
                return None

            entry: CacheEntry = self.store[key]

            # Check expiration
            if entry.is_expired():
                # Remove expired entry
                del self.store[key]
                self._stats["misses"] += 1
                self._stats["evictions"] += 1
                logger.debug(
                    "Cache expired", key=key[:32] + "...", age_s=entry.age_seconds()
                )
                return None

            # Check max age if specified
            if max_age_s is not None and entry.age_seconds() > max_age_s:
                self._stats["misses"] += 1
                logger.debug(
                    "Cache too old",
                    key=key[:32] + "...",
                    age_s=entry.age_seconds(),
                    max_age_s=max_age_s,
                )
                return None

            # Cache hit!
            self._stats["hits"] += 1

            # Add cache metadata to response
            result = entry.value.copy()
            result["_cached_at"] = entry.cached_at
            result["_cache_age_s"] = entry.age_seconds()
            result["_cache_ttl_remaining_s"] = entry.ttl_remaining()

            logger.debug(
                "Cache hit",
                key=key[:32] + "...",
                age_s=entry.age_seconds(),
                ttl_remaining_s=entry.ttl_remaining(),
            )

            return result

    async def set(
        self,
        key: str,
        value: Dict[str, Any],
        *,
        ttl_s: int,
        labels: Optional[List[str]] = None,
    ) -> None:
        """
        Store value in cache with TTL.

        Args:
            key: Cache key for storage
            value: Value to cache (will be copied)
            ttl_s: Time-to-live in seconds
            labels: Labels for filtering/metrics
        """
        async with self._lock:
            # Create entry with expiration
            now = time.time()
            entry = CacheEntry(
                value=value.copy(),  # Copy to prevent mutation
                cached_at=now,
                expires_at=now + ttl_s,
                labels=labels or [],
            )

            # Store in cache (may evict LRU if full)
            old_size = len(self.store)
            self.store[key] = entry
            new_size = len(self.store)

            # Track eviction
            if old_size > 0 and new_size <= old_size and key not in self.store:
                self._stats["evictions"] += 1

            self._stats["sets"] += 1

            logger.debug(
                "Cache set",
                key=key[:32] + "...",
                ttl_s=ttl_s,
                labels=labels or [],
                cache_size=new_size,
            )

    async def delete(self, key: str) -> None:
        """
        Remove entry from cache.

        Args:
            key: Cache key to delete
        """
        async with self._lock:
            if key in self.store:
                del self.store[key]
                self._stats["deletes"] += 1
                logger.debug("Cache delete", key=key[:32] + "...")

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Statistics including hit rate, size, and operation counts
        """
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0.0

        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": round(hit_rate, 3),
            "singleflight": self._stats["singleflight"],
            "evictions": self._stats["evictions"],
            "sets": self._stats["sets"],
            "deletes": self._stats["deletes"],
            "entries": len(self.store),
            "maxsize": self.store.maxsize,
        }

    async def invoke_with_singleflight(
        self,
        key: str,
        call_fn: Callable,
        ttl_s: int,
        labels: Optional[List[str]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Execute function with singleflight pattern to prevent thundering herd.

        If multiple concurrent requests need the same key, only one executes
        the actual function while others wait for the result.

        Args:
            key: Cache key for deduplication
            call_fn: Async function to call if not cached
            ttl_s: TTL for caching the result
            labels: Labels for the cache entry

        Returns:
            Tuple of (result, cache_metadata)
        """
        # Check cache first
        if cached := await self.get(key):
            return cached, {
                "cacheHit": True,
                "cacheAge": cached.get("_cache_age_s", 0),
                "cacheTtlRemaining": cached.get("_cache_ttl_remaining_s", 0),
            }

        # Check if another request is already fetching
        if future := self.singleflight.get(key):
            self._stats["singleflight"] += 1
            logger.debug("Singleflight wait", key=key[:32] + "...")
            try:
                result = await future
                # Return as cache hit since we got it from another request
                return result, {"cacheHit": True, "singleflight": True}
            except Exception:
                # If the original request failed, try ourselves
                pass

        # We're the first/only request - create future for others
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.singleflight[key] = future

        try:
            # Execute the actual function
            start_time = time.time()
            result = await call_fn()
            duration_ms = (time.time() - start_time) * 1000

            # Cache the result
            await self.set(key, result, ttl_s=ttl_s, labels=labels)

            # Resolve future for waiters
            if not future.done():
                future.set_result(result)

            logger.debug(
                "Singleflight executed", key=key[:32] + "...", duration_ms=duration_ms
            )

            return result, {"cacheHit": False, "duration_ms": duration_ms}

        except Exception as e:
            # Set exception for waiters
            if not future.done():
                future.set_exception(e)
            raise

        finally:
            # Clean up singleflight tracking
            self.singleflight.pop(key, None)


def canonical_json(value: Any) -> str:
    """
    Convert value to canonical JSON for consistent cache keys.

    Normalizes:
    - Dict keys are sorted
    - Strings are trimmed
    - Lists maintain order (semantic)
    - None becomes null

    Args:
        value: Any JSON-serializable value

    Returns:
        Canonical JSON string
    """

    def normalize(v):
        if isinstance(v, dict):
            return {k: normalize(v[k]) for k in sorted(v.keys())}
        elif isinstance(v, list):
            return [normalize(x) for x in v]
        elif isinstance(v, str):
            return v.strip()
        else:
            return v

    normalized = normalize(value)
    return json.dumps(
        normalized, separators=(",", ":"), ensure_ascii=False, sort_keys=True
    )


def make_cache_key(
    server: str,
    tool: str,
    args: Dict[str, Any],
    *,
    user_scope: str = "global",
    tool_version: str = "v1",
    schema_fingerprint: Optional[str] = None,
) -> str:
    """
    Generate cache key for MCP tool invocation.

    Format: mcp:{server}:{tool}:{version}:{schema}:{scope}:{args_hash}
    Example: mcp:notion:get_page:v1:abc123:global:def456

    Args:
        server: MCP server name (e.g., "notion", "github")
        tool: Tool name (e.g., "get_page", "search")
        args: Tool arguments to hash
        user_scope: User/workspace scope for isolation (default "global")
        tool_version: Tool version for cache invalidation
        schema_fingerprint: Hash of tool schema for compatibility

    Returns:
        Cache key string
    """
    # Generate canonical args hash
    canonical_args = canonical_json(args)
    args_hash = hashlib.sha256(canonical_args.encode()).hexdigest()[:16]

    # Use first 8 chars of schema fingerprint if provided
    schema_part = schema_fingerprint[:8] if schema_fingerprint else "noschema"

    # Build cache key
    key = f"mcp:{server}:{tool}:{tool_version}:{schema_part}:{user_scope}:{args_hash}"

    return key


# Module-level cache instance (singleton)
_cache_instance: Optional[MemoryInvokeCache] = None


def get_cache_service() -> MemoryInvokeCache:
    """
    Get the singleton cache service instance.

    Returns:
        The global MemoryInvokeCache instance
    """
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = MemoryInvokeCache()
    return _cache_instance
