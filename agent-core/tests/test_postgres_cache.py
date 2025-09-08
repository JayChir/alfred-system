"""
Integration tests for PostgreSQL cache backend.

Tests the core functionality of the PostgreSQL cache including:
- Basic get/set operations with TTL
- Atomic hit counting
- Tag-based invalidation
- Stale-if-error fallback
- Size limit enforcement
- Advisory lock singleflight pattern
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from src.services.postgres_cache import (
    PostgreSQLInvokeCache,
    canonical_args_hash,
    derive_tags_for_tool,
    make_cache_key,
)


@pytest.fixture
async def db_session():
    """Create a test database session."""
    # Use in-memory SQLite for testing (replace with test PostgreSQL in production)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # Create tables
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
            CREATE TABLE agent_cache (
                cache_key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_hash TEXT,
                idempotent BOOLEAN DEFAULT 1,
                expires_at TIMESTAMP NOT NULL,
                hit_count INTEGER DEFAULT 0,
                size_bytes INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
            )
        )

        await conn.execute(
            text(
                """
            CREATE TABLE agent_cache_tags (
                cache_key TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (cache_key, tag),
                FOREIGN KEY (cache_key) REFERENCES agent_cache(cache_key) ON DELETE CASCADE
            )
        """
            )
        )

        await conn.execute(
            text(
                """
            CREATE INDEX idx_agent_cache_tags_tag ON agent_cache_tags (tag)
        """
            )
        )

    # Create session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_cache_get_set_happy_path(db_session):
    """Test basic cache get/set operations with hit count and TTL metadata."""
    cache = PostgreSQLInvokeCache(db_session)

    # Set a value
    key = "test:key:1"
    value = {"data": "test_value", "timestamp": "2024-01-01"}
    ttl_s = 3600  # 1 hour

    success = await cache.set(key, value, ttl_s=ttl_s)
    assert success is True

    # Get the value
    result = await cache.get(key)
    assert result is not None
    assert result["data"] == "test_value"
    assert result["timestamp"] == "2024-01-01"

    # Check metadata
    assert "_cache_ttl_remaining_s" in result
    assert result["_cache_ttl_remaining_s"] > 3500  # Should be close to 3600
    assert "_cache_age_s" in result
    assert result["_cache_age_s"] < 1  # Just cached

    # Check hit count incremented
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 0
    assert stats["sets"] == 1


@pytest.mark.asyncio
async def test_cache_size_limit_rejection(db_session):
    """Test that entries exceeding size limit are rejected."""
    cache = PostgreSQLInvokeCache(db_session)

    # Create large value exceeding 250KB
    large_value = {"data": "x" * (260 * 1024)}  # 260KB

    success = await cache.set("large:key", large_value, ttl_s=3600)
    assert success is False

    # Verify not stored
    result = await cache.get("large:key")
    assert result is None

    # Check stats
    stats = cache.stats()
    assert stats["size_exceeded"] == 1


@pytest.mark.asyncio
async def test_cache_invalidate_by_tags(db_session):
    """Test tag-based invalidation for multiple tags."""
    cache = PostgreSQLInvokeCache(db_session)

    # Set multiple entries with tags
    await cache.set(
        "notion:page:1",
        {"page": "1"},
        ttl_s=3600,
        labels=["notion:page:abc123", "notion:ws:workspace1"],
    )
    await cache.set(
        "notion:page:2",
        {"page": "2"},
        ttl_s=3600,
        labels=["notion:page:def456", "notion:ws:workspace1"],
    )
    await cache.set(
        "github:repo:1", {"repo": "1"}, ttl_s=3600, labels=["github:repo:owner/repo"]
    )

    # Verify all exist
    assert await cache.get("notion:page:1") is not None
    assert await cache.get("notion:page:2") is not None
    assert await cache.get("github:repo:1") is not None

    # Invalidate by workspace tag
    count = await cache.invalidate_by_tags(["notion:ws:workspace1"])
    assert count == 2  # Should delete both Notion entries

    # Verify Notion entries deleted, GitHub entry remains
    assert await cache.get("notion:page:1") is None
    assert await cache.get("notion:page:2") is None
    assert await cache.get("github:repo:1") is not None


@pytest.mark.asyncio
async def test_cache_cleanup_expired(db_session):
    """Test cleanup of expired entries in batches."""
    cache = PostgreSQLInvokeCache(db_session)

    # Set entries with very short TTL
    for i in range(5):
        await cache.set(f"expire:key:{i}", {"data": i}, ttl_s=1)

    # Wait for expiry
    await asyncio.sleep(2)

    # Run cleanup
    cleaned = await cache.cleanup_expired(batch_size=3)
    assert cleaned == 3  # First batch of 3

    cleaned = await cache.cleanup_expired(batch_size=3)
    assert cleaned == 2  # Remaining 2

    # Verify all expired entries removed
    for i in range(5):
        assert await cache.get(f"expire:key:{i}") is None


@pytest.mark.asyncio
async def test_cache_stale_if_error(db_session):
    """Test stale-if-error fallback behavior."""
    cache = PostgreSQLInvokeCache(db_session)

    # Set a value with short TTL
    key = "stale:test"
    value = {"data": "original"}
    await cache.set(key, value, ttl_s=1)

    # Wait for it to expire but within grace period
    await asyncio.sleep(1.5)

    # Get with stale allowed should return stale entry
    result = await cache.get(key, allow_stale=True)
    assert result is not None
    assert result["data"] == "original"
    assert result.get("_cache_stale") is True
    assert "_cache_warning" in result

    # Check stats
    stats = cache.stats()
    assert stats["stale_served"] == 1


@pytest.mark.asyncio
async def test_cache_advisory_lock_singleflight(db_session):
    """Test advisory lock prevents concurrent cache fills."""
    cache = PostgreSQLInvokeCache(db_session)

    call_count = 0

    async def expensive_operation():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)  # Simulate expensive operation
        return {"result": "expensive_data", "count": call_count}

    # Simulate concurrent requests for same key
    async def request(cache_key: str):
        result, was_cached = await cache.with_cache_fill_lock(
            cache_key, expensive_operation
        )
        return result, was_cached

    # Launch multiple concurrent requests
    cache_key = "singleflight:test"
    tasks = [request(cache_key) for _ in range(3)]
    results = await asyncio.gather(*tasks)

    # All should get same result
    for result, _ in results:
        assert result["result"] == "expensive_data"

    # Only one should have executed the expensive operation
    assert call_count == 1

    # At least one should be a cache hit
    cache_hits = sum(1 for _, was_cached in results if was_cached)
    assert cache_hits >= 1


def test_canonical_args_hash():
    """Test deterministic hash generation for arguments."""
    # Same args, different order should produce same hash
    args1 = {"b": 2, "a": 1, "c": {"d": 3, "e": 4}}
    args2 = {"a": 1, "c": {"e": 4, "d": 3}, "b": 2}

    hash1 = canonical_args_hash(args1)
    hash2 = canonical_args_hash(args2)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex digest

    # Different args should produce different hash
    args3 = {"a": 1, "b": 3}
    hash3 = canonical_args_hash(args3)
    assert hash3 != hash1


def test_make_cache_key():
    """Test cache key generation with deterministic format."""
    key = make_cache_key(
        namespace="notion:ws_ABC123",
        tool="get_page",
        version="v1",
        args={"page_id": "123", "properties": ["title", "content"]},
    )

    # Should have expected format
    assert key.startswith("notion:ws_ABC123:get_page:v1:")

    # Same inputs should produce same key
    key2 = make_cache_key(
        namespace="notion:ws_ABC123",
        tool="get_page",
        version="v1",
        args={"properties": ["title", "content"], "page_id": "123"},  # Different order
    )
    assert key == key2


def test_derive_tags_for_tool():
    """Test tag derivation for different tool types."""
    # Notion tags
    tags = derive_tags_for_tool(
        "notion", "get_page", {"page_id": "abc123", "workspace_id": "ws_456"}
    )
    assert "notion:page:abc123" in tags
    assert "notion:ws:ws_456" in tags

    # GitHub tags
    tags = derive_tags_for_tool(
        "github",
        "get_file",
        {"owner": "octocat", "repo": "hello-world", "path": "README.md"},
    )
    assert "github:repo:octocat/hello-world" in tags
    assert "github:file:octocat/hello-world:README.md" in tags

    # Unknown provider
    tags = derive_tags_for_tool("unknown", "tool", {"key": "value"})
    assert tags == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
