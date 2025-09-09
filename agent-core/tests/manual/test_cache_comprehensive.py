#!/usr/bin/env python3
"""
Comprehensive test script for PostgreSQL cache implementation.

This script tests all aspects of the cache including:
1. Database setup and migrations
2. Cache operations (get/set/delete)
3. TTL and expiration
4. Tag-based invalidation
5. Concurrent access patterns
6. Size limits
7. Integration with cache service
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.db.models import Base  # noqa: E402
from src.services.cache_service import (  # noqa: E402
    DEFAULT_TTL_POLICIES,
    MAX_CACHE_ENTRY_SIZE,
    get_cache_service,
)
from src.services.postgres_cache import (  # noqa: E402
    PostgreSQLInvokeCache,
    canonical_args_hash,
    derive_tags_for_tool,
    make_cache_key,
)


class Colors:
    """ANSI color codes for terminal output."""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_test(name: str, status: str = "RUNNING"):
    """Print test status with colors."""
    if status == "PASS":
        print(f"{Colors.GREEN}✓{Colors.ENDC} {name}")
    elif status == "FAIL":
        print(f"{Colors.RED}✗{Colors.ENDC} {name}")
    elif status == "RUNNING":
        print(f"{Colors.BLUE}⟳{Colors.ENDC} {name}...", end=" ", flush=True)
    else:
        print(f"  {name}")


async def setup_test_database():
    """Create test database and tables."""
    print(f"\n{Colors.BOLD}Setting up test database...{Colors.ENDC}")

    # Use SQLite for testing (replace with PostgreSQL in production)
    engine = create_async_engine("sqlite+aiosqlite:///test_cache.db", echo=False)

    # Drop existing tables if any
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    print(f"{Colors.GREEN}✓{Colors.ENDC} Database setup complete")
    return engine


async def test_basic_operations(session: AsyncSession):
    """Test basic cache get/set/delete operations."""
    print(f"\n{Colors.BOLD}Testing basic cache operations...{Colors.ENDC}")

    cache = PostgreSQLInvokeCache(session)

    # Test 1: Set and get
    print_test("Set and get value", "RUNNING")
    key = "test:basic:1"
    value = {"data": "test_value", "timestamp": datetime.now().isoformat()}

    success = await cache.set(key, value, ttl_s=3600)
    assert success, "Failed to set cache value"

    result = await cache.get(key)
    assert result is not None, "Failed to get cache value"
    assert result["data"] == value["data"], "Value mismatch"
    print_test("Set and get value", "PASS")

    # Test 2: Cache miss
    print_test("Cache miss for non-existent key", "RUNNING")
    result = await cache.get("nonexistent:key")
    assert result is None, "Should return None for missing key"
    print_test("Cache miss for non-existent key", "PASS")

    # Test 3: Delete
    print_test("Delete cache entry", "RUNNING")
    deleted = await cache.delete(key)
    assert deleted, "Failed to delete cache entry"

    result = await cache.get(key)
    assert result is None, "Entry should be deleted"
    print_test("Delete cache entry", "PASS")

    # Test 4: Stats
    print_test("Cache statistics", "RUNNING")
    stats = cache.stats()
    assert stats["hits"] == 1, f"Expected 1 hit, got {stats['hits']}"
    assert stats["misses"] == 2, f"Expected 2 misses, got {stats['misses']}"
    assert stats["sets"] == 1, f"Expected 1 set, got {stats['sets']}"
    print_test("Cache statistics", "PASS")


async def test_ttl_expiration(session: AsyncSession):
    """Test TTL and expiration behavior."""
    print(f"\n{Colors.BOLD}Testing TTL and expiration...{Colors.ENDC}")

    cache = PostgreSQLInvokeCache(session)

    # Test 1: Short TTL expiration
    print_test("TTL expiration (2 second TTL)", "RUNNING")
    key = "test:ttl:1"
    value = {"data": "expires_soon"}

    await cache.set(key, value, ttl_s=2)

    # Should exist immediately
    result = await cache.get(key)
    assert result is not None, "Value should exist immediately"
    assert result["_cache_ttl_remaining_s"] <= 2, "TTL should be <= 2"

    # Wait for expiration
    await asyncio.sleep(3)

    result = await cache.get(key)
    assert result is None, "Value should be expired"
    print_test("TTL expiration (2 second TTL)", "PASS")

    # Test 2: Max age constraint
    print_test("Max age constraint", "RUNNING")
    key = "test:maxage:1"
    value = {"data": "old_value"}

    await cache.set(key, value, ttl_s=3600)

    # Should exist with normal get
    result = await cache.get(key)
    assert result is not None, "Value should exist"

    # Should be rejected with max_age_s=0
    result = await cache.get(key, max_age_s=0)
    assert result is None, "Value should be too old"
    print_test("Max age constraint", "PASS")

    # Test 3: Stale-if-error
    print_test("Stale-if-error fallback", "RUNNING")
    key = "test:stale:1"
    value = {"data": "stale_value"}

    await cache.set(key, value, ttl_s=1)
    await asyncio.sleep(2)  # Let it expire

    # Should return stale with allow_stale=True
    result = await cache.get(key, allow_stale=True)
    assert result is not None, "Should return stale value"
    assert result.get("_cache_stale") is True, "Should be marked as stale"
    assert "_cache_warning" in result, "Should have warning"
    print_test("Stale-if-error fallback", "PASS")


async def test_tag_invalidation(session: AsyncSession):
    """Test tag-based cache invalidation."""
    print(f"\n{Colors.BOLD}Testing tag-based invalidation...{Colors.ENDC}")

    cache = PostgreSQLInvokeCache(session)

    print_test("Setting up tagged entries", "RUNNING")

    # Create entries with different tags
    await cache.set(
        "notion:1",
        {"page": "1"},
        ttl_s=3600,
        labels=["notion:page:123", "notion:ws:abc"],
    )
    await cache.set(
        "notion:2",
        {"page": "2"},
        ttl_s=3600,
        labels=["notion:page:456", "notion:ws:abc"],
    )
    await cache.set(
        "notion:3",
        {"page": "3"},
        ttl_s=3600,
        labels=["notion:page:789", "notion:ws:def"],
    )
    await cache.set(
        "github:1", {"repo": "1"}, ttl_s=3600, labels=["github:repo:owner/repo"]
    )
    print_test("Setting up tagged entries", "PASS")

    # Test 1: Invalidate by workspace tag
    print_test("Invalidate by workspace tag", "RUNNING")
    count = await cache.invalidate_by_tags(["notion:ws:abc"])
    assert count == 2, f"Expected 2 invalidated, got {count}"

    # Check what remains
    assert await cache.get("notion:1") is None
    assert await cache.get("notion:2") is None
    assert await cache.get("notion:3") is not None
    assert await cache.get("github:1") is not None
    print_test("Invalidate by workspace tag", "PASS")

    # Test 2: Invalidate multiple tags
    print_test("Invalidate multiple tags", "RUNNING")
    count = await cache.invalidate_by_tags(
        ["notion:page:789", "github:repo:owner/repo"]
    )
    assert count == 2, f"Expected 2 invalidated, got {count}"

    assert await cache.get("notion:3") is None
    assert await cache.get("github:1") is None
    print_test("Invalidate multiple tags", "PASS")


async def test_size_limits(session: AsyncSession):
    """Test cache entry size limits."""
    print(f"\n{Colors.BOLD}Testing size limits...{Colors.ENDC}")

    cache = PostgreSQLInvokeCache(session)

    # Test 1: Normal size entry
    print_test("Normal size entry (< 250KB)", "RUNNING")
    normal_value = {"data": "x" * 1000}  # ~1KB
    success = await cache.set("size:normal", normal_value, ttl_s=3600)
    assert success, "Should accept normal size"
    print_test("Normal size entry (< 250KB)", "PASS")

    # Test 2: Large entry rejection
    print_test("Large entry rejection (> 250KB)", "RUNNING")
    large_value = {"data": "x" * (260 * 1024)}  # 260KB
    success = await cache.set("size:large", large_value, ttl_s=3600)
    assert not success, "Should reject large entry"

    # Verify not stored
    result = await cache.get("size:large")
    assert result is None, "Large entry should not be stored"

    stats = cache.stats()
    assert stats["size_exceeded"] == 1, "Should track size exceeded"
    print_test("Large entry rejection (> 250KB)", "PASS")


async def test_concurrent_access(session: AsyncSession):
    """Test concurrent access patterns with advisory locks."""
    print(f"\n{Colors.BOLD}Testing concurrent access...{Colors.ENDC}")

    cache = PostgreSQLInvokeCache(session)

    print_test("Singleflight pattern", "RUNNING")

    call_count = 0

    async def expensive_operation():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.5)  # Simulate expensive operation
        return {"result": f"expensive_data_{call_count}", "count": call_count}

    # Launch concurrent requests for the same key
    cache_key = "concurrent:test"

    async def make_request():
        result, was_cached = await cache.with_cache_fill_lock(
            cache_key, expensive_operation
        )
        return result, was_cached

    # Start 5 concurrent requests
    tasks = [make_request() for _ in range(5)]
    results = await asyncio.gather(*tasks)

    # All should get the same result
    first_result = results[0][0]
    for result, _ in results:
        assert result["result"] == first_result["result"], "All should get same result"

    # Only one should have executed the expensive operation
    assert call_count == 1, f"Expected 1 call, got {call_count}"

    print_test("Singleflight pattern", "PASS")


async def test_cache_key_generation():
    """Test deterministic cache key generation."""
    print(f"\n{Colors.BOLD}Testing cache key generation...{Colors.ENDC}")

    # Test 1: Canonical args hash
    print_test("Canonical args hash", "RUNNING")
    args1 = {"b": 2, "a": 1, "c": {"d": 3, "e": 4}}
    args2 = {"a": 1, "c": {"e": 4, "d": 3}, "b": 2}

    hash1 = canonical_args_hash(args1)
    hash2 = canonical_args_hash(args2)

    assert hash1 == hash2, "Same args should produce same hash"
    assert len(hash1) == 64, "Should be SHA-256 hex digest"
    print_test("Canonical args hash", "PASS")

    # Test 2: Cache key format
    print_test("Cache key format", "RUNNING")
    key = make_cache_key(
        namespace="notion:ws_123",
        tool="get_page",
        version="v1",
        args={"page_id": "abc", "properties": ["title"]},
    )

    assert key.startswith("notion:ws_123:get_page:v1:"), "Wrong key format"

    # Same args, different order
    key2 = make_cache_key(
        namespace="notion:ws_123",
        tool="get_page",
        version="v1",
        args={"properties": ["title"], "page_id": "abc"},
    )

    assert key == key2, "Order should not matter"
    print_test("Cache key format", "PASS")

    # Test 3: Tag derivation
    print_test("Tag derivation", "RUNNING")

    # Notion tags
    tags = derive_tags_for_tool(
        "notion", "get_page", {"page_id": "123", "workspace_id": "ws_456"}
    )
    assert "notion:page:123" in tags
    assert "notion:ws:ws_456" in tags

    # GitHub tags
    tags = derive_tags_for_tool(
        "github", "get_file", {"owner": "octocat", "repo": "hello", "path": "README.md"}
    )
    assert "github:repo:octocat/hello" in tags
    assert "github:file:octocat/hello:README.md" in tags

    print_test("Tag derivation", "PASS")


async def test_cleanup_operations(session: AsyncSession):
    """Test cleanup of expired entries."""
    print(f"\n{Colors.BOLD}Testing cleanup operations...{Colors.ENDC}")

    cache = PostgreSQLInvokeCache(session)

    print_test("Batch cleanup of expired entries", "RUNNING")

    # Create 10 entries with 1 second TTL
    for i in range(10):
        await cache.set(f"cleanup:{i}", {"data": i}, ttl_s=1)

    # Wait for expiration
    await asyncio.sleep(2)

    # Clean up in batches of 5
    cleaned1 = await cache.cleanup_expired(batch_size=5)
    assert cleaned1 == 5, f"Expected 5 cleaned, got {cleaned1}"

    cleaned2 = await cache.cleanup_expired(batch_size=5)
    assert cleaned2 == 5, f"Expected 5 cleaned, got {cleaned2}"

    cleaned3 = await cache.cleanup_expired(batch_size=5)
    assert cleaned3 == 0, f"Expected 0 cleaned, got {cleaned3}"

    print_test("Batch cleanup of expired entries", "PASS")


async def test_backend_selector(session: AsyncSession):
    """Test cache backend selection."""
    print(f"\n{Colors.BOLD}Testing backend selector...{Colors.ENDC}")

    # Test 1: Memory backend (default)
    print_test("Memory backend selection", "RUNNING")
    cache = get_cache_service(backend="memory")
    assert cache is not None, "Should return memory cache"
    assert hasattr(cache, "store"), "Should be MemoryInvokeCache"
    print_test("Memory backend selection", "PASS")

    # Test 2: PostgreSQL backend
    print_test("PostgreSQL backend selection", "RUNNING")
    cache = get_cache_service(backend="postgres", db_session=session)
    assert cache is not None, "Should return PostgreSQL cache"
    assert hasattr(cache, "db"), "Should be PostgreSQLInvokeCache"
    print_test("PostgreSQL backend selection", "PASS")

    # Test 3: PostgreSQL without session (should fail)
    print_test("PostgreSQL without session (error case)", "RUNNING")
    try:
        cache = get_cache_service(backend="postgres")
        raise AssertionError("Should raise ValueError")
    except ValueError as e:
        assert "requires a database session" in str(e)
    print_test("PostgreSQL without session (error case)", "PASS")


async def test_ttl_policies():
    """Test TTL policy configuration."""
    print(f"\n{Colors.BOLD}Testing TTL policies...{Colors.ENDC}")

    print_test("Default TTL policies", "RUNNING")

    # Check some default policies
    assert DEFAULT_TTL_POLICIES["notion:get_page"] == 14400  # 4 hours
    assert DEFAULT_TTL_POLICIES["notion:get_database"] == 86400  # 24 hours
    assert DEFAULT_TTL_POLICIES["github:search"] == 3600  # 1 hour
    assert DEFAULT_TTL_POLICIES["*"] == 3600  # Default fallback

    print_test("Default TTL policies", "PASS")

    print_test("Cache entry size limit", "RUNNING")
    assert MAX_CACHE_ENTRY_SIZE == 250 * 1024  # 250KB
    print_test("Cache entry size limit", "PASS")


async def main():
    """Run all tests."""
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}PostgreSQL Cache Comprehensive Test Suite{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")

    # Setup database
    engine = await setup_test_database()

    # Create session
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    failed_tests = []

    try:
        async with async_session() as session:
            # Run all test suites
            test_suites = [
                ("Basic Operations", test_basic_operations),
                ("TTL and Expiration", test_ttl_expiration),
                ("Tag Invalidation", test_tag_invalidation),
                ("Size Limits", test_size_limits),
                ("Concurrent Access", test_concurrent_access),
                ("Cleanup Operations", test_cleanup_operations),
                ("Backend Selector", test_backend_selector),
            ]

            # Tests that don't need a session
            standalone_tests = [
                ("Cache Key Generation", test_cache_key_generation),
                ("TTL Policies", test_ttl_policies),
            ]

            # Run session-based tests
            for name, test_func in test_suites:
                try:
                    if asyncio.iscoroutinefunction(test_func):
                        await test_func(session)
                    else:
                        test_func(session)
                    await session.commit()  # Commit after each test suite
                except Exception as e:
                    print(f"\n{Colors.RED}✗ {name} failed: {e}{Colors.ENDC}")
                    failed_tests.append(name)
                    await session.rollback()

            # Run standalone tests
            for name, test_func in standalone_tests:
                try:
                    if asyncio.iscoroutinefunction(test_func):
                        await test_func()
                    else:
                        test_func()
                except Exception as e:
                    print(f"\n{Colors.RED}✗ {name} failed: {e}{Colors.ENDC}")
                    failed_tests.append(name)

    finally:
        await engine.dispose()

        # Clean up test database file
        try:
            os.remove("test_cache.db")
        except FileNotFoundError:
            pass

    # Summary
    print(f"\n{Colors.BOLD}{'='*60}{Colors.ENDC}")
    print(f"{Colors.BOLD}Test Summary{Colors.ENDC}")
    print(f"{Colors.BOLD}{'='*60}{Colors.ENDC}")

    if failed_tests:
        print(f"{Colors.RED}Failed tests:{Colors.ENDC}")
        for test in failed_tests:
            print(f"  {Colors.RED}✗ {test}{Colors.ENDC}")
        print(f"\n{Colors.RED}FAILED: {len(failed_tests)} test(s) failed{Colors.ENDC}")
        return 1
    else:
        print(f"{Colors.GREEN}✓ All tests passed!{Colors.ENDC}")
        return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
