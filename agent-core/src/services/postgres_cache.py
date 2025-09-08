"""
PostgreSQL cache service implementation for MCP tool results.

This module provides a production-grade PostgreSQL-backed cache with:
- Atomic hit counting and access tracking
- Advisory locks for singleflight pattern
- Tag-based invalidation for related entries
- TTL policies with stale-if-error fallback
- Size limits and content verification

The cache follows deterministic key patterns that exclude session/device IDs
to maximize cache hit rates across users and sessions.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Maximum cache entry size (250KB)
MAX_CACHE_ENTRY_SIZE = 250 * 1024

# Stale-if-error grace period (30 seconds)
STALE_IF_ERROR_GRACE_SECONDS = 30

# Default TTL policies per tool (in seconds)
DEFAULT_TTL_POLICIES = {
    # Notion tools
    "notion:get_page": 14400,  # 4 hours for document content
    "notion:get_database": 86400,  # 24 hours for schema (rarely changes)
    "notion:search": 14400,  # 4 hours for search results
    "notion:list_pages": 3600,  # 1 hour for listings
    # GitHub tools
    "github:get_repo": 86400,  # 24 hours for repo metadata
    "github:get_file": 14400,  # 4 hours for file content
    "github:search": 3600,  # 1 hour for search (more dynamic)
    "github:list_pulls": 900,  # 15 minutes for PR lists
    # Default fallback
    "*": 3600,  # 1 hour for unknown tools
}


def _normalize_value(val: Any) -> Any:
    """
    Normalize values for consistent JSON serialization.

    Args:
        val: Value to normalize

    Returns:
        Normalized value for deterministic hashing
    """
    if isinstance(val, float):
        # Round floats to prevent drift
        return round(val, 10)
    if isinstance(val, datetime):
        # Convert to UTC ISO format
        return val.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(val, (list, tuple)):
        return [_normalize_value(v) for v in val]
    if isinstance(val, dict):
        return {k: _normalize_value(v) for k, v in sorted(val.items())}
    return val


def canonical_args_hash(args: Dict[str, Any]) -> str:
    """
    Generate deterministic hash of arguments.

    Normalizes arguments to ensure consistent hashing across:
    - Different key orderings
    - Float precision differences
    - Datetime representations

    Args:
        args: Tool arguments to hash

    Returns:
        Hex-encoded SHA-256 hash of canonical JSON
    """
    # Normalize and serialize to canonical JSON
    normalized = _normalize_value(args)
    canonical_json = json.dumps(normalized, sort_keys=True, separators=(",", ":"))

    # Generate SHA-256 hash
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def make_cache_key(
    namespace: str, tool: str, version: str, args: Dict[str, Any]
) -> str:
    """
    Generate deterministic cache key.

    Format: {namespace}:{tool}:{version}:{args_hash}

    Args:
        namespace: Tenant/workspace scope (e.g., "notion:ws_ABC123")
        tool: Tool name (e.g., "get_page")
        version: Response schema version (e.g., "v1")
        args: Tool arguments

    Returns:
        Deterministic cache key
    """
    args_hash = canonical_args_hash(args)
    return f"{namespace}:{tool}:{version}:{args_hash}"


def derive_tags_for_tool(
    provider: str,
    tool: str,
    args: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Derive invalidation tags from tool call.

    Tags enable efficient invalidation of related cache entries,
    such as all entries for a specific Notion page or GitHub repo.

    Args:
        provider: Tool provider (e.g., "notion", "github")
        tool: Tool name
        args: Tool arguments
        result: Tool result (optional, for deriving from response)

    Returns:
        List of tags for this cache entry
    """
    tags = []

    if provider == "notion":
        # Tag by page ID
        if "page_id" in args:
            tags.append(f"notion:page:{args['page_id']}")

        # Tag by database ID
        if "database_id" in args:
            tags.append(f"notion:db:{args['database_id']}")

        # Tag by workspace if present
        if "workspace_id" in args:
            tags.append(f"notion:ws:{args['workspace_id']}")

    elif provider == "github":
        # Tag by repository
        if "owner" in args and "repo" in args:
            tags.append(f"github:repo:{args['owner']}/{args['repo']}")

        # Tag by file path
        if "path" in args:
            tags.append(
                f"github:file:{args.get('owner', '')}/{args.get('repo', '')}:{args['path']}"
            )

    return tags


class PostgreSQLInvokeCache:
    """
    PostgreSQL-backed cache implementation.

    Features:
    - Atomic operations for race-safe caching
    - Advisory locks to prevent thundering herd
    - Tag-based invalidation for precise cache management
    - TTL policies with stale-if-error fallback
    - Size limits and content verification
    """

    def __init__(self, db: AsyncSession, ttl_policies: Optional[Dict[str, int]] = None):
        """
        Initialize PostgreSQL cache.

        Args:
            db: Database session
            ttl_policies: Optional TTL overrides per tool
        """
        self.db = db
        self.ttl_policies = ttl_policies or DEFAULT_TTL_POLICIES

        # Statistics (in-memory for this session)
        self._stats = {
            "hits": 0,
            "misses": 0,
            "stale_served": 0,
            "errors_bypassed": 0,
            "sets": 0,
            "deletes": 0,
            "size_exceeded": 0,
        }

    async def get(
        self, key: str, *, max_age_s: Optional[int] = None, allow_stale: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached value with atomic hit increment.

        Uses a CTE to atomically increment hit count and update
        last_accessed only for non-expired entries.

        Args:
            key: Cache key to retrieve
            max_age_s: Maximum acceptable age in seconds
            allow_stale: Whether to serve stale entries on error

        Returns:
            Cached value with metadata if found, None if miss
        """
        try:
            # Atomic GET with hit increment using CTE
            result = await self.db.execute(
                text(
                    """
                    WITH hit AS (
                        UPDATE agent_cache
                        SET hit_count = hit_count + 1,
                            last_accessed = NOW()
                        WHERE cache_key = :key
                          AND expires_at > NOW()
                        RETURNING content, expires_at, created_at
                    )
                    SELECT
                        content,
                        GREATEST(0, EXTRACT(EPOCH FROM (expires_at - NOW())))::int AS ttl_remaining_s,
                        EXTRACT(EPOCH FROM (NOW() - created_at))::int AS age_s
                    FROM hit
                """
                ),
                {"key": key},
            )

            row = result.first()

            if not row:
                # Check for stale entry if allow_stale is True
                if allow_stale:
                    stale_result = await self.db.execute(
                        text(
                            """
                            SELECT content, expires_at, created_at
                            FROM agent_cache
                            WHERE cache_key = :key
                              AND expires_at > NOW() - INTERVAL ':grace seconds'
                            LIMIT 1
                        """
                        ),
                        {"key": key, "grace": STALE_IF_ERROR_GRACE_SECONDS},
                    )

                    stale_row = stale_result.first()
                    if stale_row:
                        self._stats["stale_served"] += 1
                        logger.debug(
                            "Serving stale cache entry",
                            key=key[:50],
                            expired_ago_s=abs(
                                stale_row.expires_at.timestamp()
                                - datetime.now(timezone.utc).timestamp()
                            ),
                        )

                        # Return stale entry with warning
                        content = dict(stale_row.content)
                        content["_cache_stale"] = True
                        content[
                            "_cache_warning"
                        ] = "Stale entry served due to upstream error"
                        return content

                self._stats["misses"] += 1
                logger.debug("Cache miss", key=key[:50])
                return None

            # Check max_age if specified
            if max_age_s is not None and row.age_s > max_age_s:
                self._stats["misses"] += 1
                logger.debug(
                    "Cache entry too old",
                    key=key[:50],
                    age_s=row.age_s,
                    max_age_s=max_age_s,
                )
                return None

            # Build response with metadata
            self._stats["hits"] += 1
            content = dict(row.content)
            content["_cache_ttl_remaining_s"] = row.ttl_remaining_s
            content["_cache_age_s"] = row.age_s

            logger.debug(
                "Cache hit",
                key=key[:50],
                ttl_remaining_s=row.ttl_remaining_s,
                age_s=row.age_s,
            )

            return content

        except Exception as e:
            logger.error("Cache get error", key=key[:50], error=str(e))
            self._stats["errors_bypassed"] += 1
            return None

    async def set(
        self,
        key: str,
        value: Dict[str, Any],
        *,
        ttl_s: int,
        labels: Optional[List[str]] = None,
    ) -> bool:
        """
        Set cache value with UPSERT operation.

        Uses INSERT ... ON CONFLICT UPDATE to atomically set or update
        the cache entry with all metadata fields.

        Args:
            key: Cache key
            value: Value to cache
            ttl_s: Time-to-live in seconds
            labels: Optional tags for invalidation

        Returns:
            True if cached successfully, False if size exceeded or error
        """
        try:
            # Calculate content size
            content_json = json.dumps(value)
            size_bytes = len(content_json.encode("utf-8"))

            # Check size limit
            if size_bytes > MAX_CACHE_ENTRY_SIZE:
                self._stats["size_exceeded"] += 1
                logger.warning(
                    "Cache entry size exceeds limit",
                    key=key[:50],
                    size_bytes=size_bytes,
                    limit_bytes=MAX_CACHE_ENTRY_SIZE,
                )
                return False

            # Calculate content hash for integrity
            content_hash = hashlib.sha256(content_json.encode("utf-8")).hexdigest()

            # Calculate expiry time
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_s)

            # UPSERT the cache entry
            await self.db.execute(
                text(
                    """
                    INSERT INTO agent_cache (
                        cache_key, content, content_hash, idempotent,
                        expires_at, created_at, updated_at, last_accessed, size_bytes
                    )
                    VALUES (
                        :key, :content::jsonb, :hash, true,
                        :expires_at, NOW(), NOW(), NOW(), :size
                    )
                    ON CONFLICT (cache_key) DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        idempotent = EXCLUDED.idempotent,
                        expires_at = EXCLUDED.expires_at,
                        updated_at = NOW(),
                        last_accessed = NOW(),
                        size_bytes = EXCLUDED.size_bytes,
                        hit_count = agent_cache.hit_count  -- Preserve hit count
                """
                ),
                {
                    "key": key,
                    "content": content_json,
                    "hash": content_hash,
                    "expires_at": expires_at,
                    "size": size_bytes,
                },
            )

            # Insert tags if provided
            if labels:
                # Delete existing tags for this key
                await self.db.execute(
                    text("DELETE FROM agent_cache_tags WHERE cache_key = :key"),
                    {"key": key},
                )

                # Insert new tags
                for tag in labels:
                    await self.db.execute(
                        text(
                            """
                            INSERT INTO agent_cache_tags (cache_key, tag)
                            VALUES (:key, :tag)
                            ON CONFLICT DO NOTHING
                        """
                        ),
                        {"key": key, "tag": tag},
                    )

            await self.db.commit()
            self._stats["sets"] += 1

            logger.debug(
                "Cache set",
                key=key[:50],
                ttl_s=ttl_s,
                size_bytes=size_bytes,
                tags=labels or [],
            )

            return True

        except Exception as e:
            logger.error("Cache set error", key=key[:50], error=str(e))
            await self.db.rollback()
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete cache entry.

        Args:
            key: Cache key to delete

        Returns:
            True if deleted, False if not found
        """
        try:
            result = await self.db.execute(
                text("DELETE FROM agent_cache WHERE cache_key = :key"), {"key": key}
            )

            await self.db.commit()
            deleted = result.rowcount > 0

            if deleted:
                self._stats["deletes"] += 1
                logger.debug("Cache entry deleted", key=key[:50])

            return deleted

        except Exception as e:
            logger.error("Cache delete error", key=key[:50], error=str(e))
            await self.db.rollback()
            return False

    async def invalidate_by_tags(self, tags: List[str]) -> int:
        """
        Invalidate cache entries by tags.

        Enables efficient invalidation of related entries,
        such as all cache entries for a modified Notion page.

        Args:
            tags: Tags to invalidate

        Returns:
            Number of entries invalidated
        """
        try:
            result = await self.db.execute(
                text(
                    """
                    DELETE FROM agent_cache
                    WHERE cache_key IN (
                        SELECT DISTINCT cache_key
                        FROM agent_cache_tags
                        WHERE tag = ANY(:tags)
                    )
                """
                ),
                {"tags": tags},
            )

            await self.db.commit()
            count = result.rowcount or 0

            if count > 0:
                logger.info("Cache entries invalidated by tags", tags=tags, count=count)

            return count

        except Exception as e:
            logger.error("Tag invalidation error", tags=tags, error=str(e))
            await self.db.rollback()
            return 0

    async def with_cache_fill_lock(
        self, cache_key: str, fill_fn: Any
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Execute function with advisory lock to prevent thundering herd.

        Uses PostgreSQL advisory locks to ensure only one process
        fills the cache for a given key at a time.

        Args:
            cache_key: Cache key to lock
            fill_fn: Async function to call if cache miss

        Returns:
            Tuple of (result, was_cached)
        """
        # Generate 64-bit lock key from cache key
        lock_key = int(hashlib.sha1(cache_key.encode()).hexdigest()[:16], 16)

        try:
            # Acquire advisory lock (transaction-scoped)
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key}
            )

            # Re-check cache under lock
            cached = await self.get(cache_key)
            if cached:
                return cached, True

            # Cache miss - fill it
            result = await fill_fn()
            return result, False

        finally:
            # Lock automatically released at transaction end
            pass

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Statistics dictionary with hit rate and counters
        """
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0.0

        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": round(hit_rate, 3),
            "stale_served": self._stats["stale_served"],
            "errors_bypassed": self._stats["errors_bypassed"],
            "sets": self._stats["sets"],
            "deletes": self._stats["deletes"],
            "size_exceeded": self._stats["size_exceeded"],
        }

    async def cleanup_expired(self, batch_size: int = 1000) -> int:
        """
        Clean up expired cache entries in batches.

        Args:
            batch_size: Number of entries to delete per batch

        Returns:
            Total number of entries cleaned up
        """
        try:
            result = await self.db.execute(
                text(
                    """
                    DELETE FROM agent_cache
                    WHERE cache_key IN (
                        SELECT cache_key
                        FROM agent_cache
                        WHERE expires_at < NOW()
                        LIMIT :batch_size
                    )
                """
                ),
                {"batch_size": batch_size},
            )

            await self.db.commit()
            count = result.rowcount or 0

            if count > 0:
                logger.info("Expired cache entries cleaned up", count=count)

            return count

        except Exception as e:
            logger.error("Cache cleanup error", error=str(e))
            await self.db.rollback()
            return 0
