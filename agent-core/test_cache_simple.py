#!/usr/bin/env python3
"""
Simple test to verify cache components are working correctly.
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.services.cache_service import (  # noqa: E402
    DEFAULT_TTL_POLICIES,
    MAX_CACHE_ENTRY_SIZE,
    MemoryInvokeCache,
    canonical_json,
    get_cache_service,
    make_cache_key,
)
from src.services.postgres_cache import (  # noqa: E402
    canonical_args_hash,
    derive_tags_for_tool,
)
from src.services.postgres_cache import (  # noqa: E402
    make_cache_key as pg_make_cache_key,
)


def test_imports():
    """Test that all imports work correctly."""
    print("✓ All imports successful")


def test_ttl_policies():
    """Test TTL policies are defined correctly."""
    assert DEFAULT_TTL_POLICIES["notion:get_page"] == 14400
    assert DEFAULT_TTL_POLICIES["github:search"] == 3600
    assert DEFAULT_TTL_POLICIES["*"] == 3600
    print("✓ TTL policies configured correctly")


def test_cache_size_limit():
    """Test cache size limit constant."""
    assert MAX_CACHE_ENTRY_SIZE == 250 * 1024
    print("✓ Cache size limit set to 250KB")


def test_canonical_json():
    """Test canonical JSON generation."""
    # Different order should produce same JSON
    obj1 = {"b": 2, "a": 1, "c": [3, 4]}
    obj2 = {"a": 1, "c": [3, 4], "b": 2}

    json1 = canonical_json(obj1)
    json2 = canonical_json(obj2)

    assert json1 == json2
    assert json1 == '{"a":1,"b":2,"c":[3,4]}'
    print("✓ Canonical JSON working correctly")


def test_canonical_args_hash():
    """Test deterministic hash generation."""
    args1 = {"b": 2, "a": 1, "c": {"d": 3, "e": 4}}
    args2 = {"a": 1, "c": {"e": 4, "d": 3}, "b": 2}

    hash1 = canonical_args_hash(args1)
    hash2 = canonical_args_hash(args2)

    assert hash1 == hash2
    assert len(hash1) == 64  # SHA-256 hex
    print("✓ Canonical args hash working correctly")


def test_cache_key_generation():
    """Test cache key generation."""
    # Test cache_service.make_cache_key
    key1 = make_cache_key(
        server="notion",
        tool="get_page",
        args={"page_id": "123"},
        user_scope="global",
        tool_version="v1",
    )

    assert key1.startswith("mcp:notion:get_page:v1:")

    # Test postgres_cache.make_cache_key
    key2 = pg_make_cache_key(
        namespace="notion:ws_123",
        tool="get_page",
        version="v1",
        args={"page_id": "123"},
    )

    assert key2.startswith("notion:ws_123:get_page:v1:")

    print("✓ Cache key generation working correctly")


def test_tag_derivation():
    """Test tag derivation for tools."""
    # Notion tags
    tags = derive_tags_for_tool(
        "notion", "get_page", {"page_id": "abc123", "workspace_id": "ws_456"}
    )
    assert "notion:page:abc123" in tags
    assert "notion:ws:ws_456" in tags

    # GitHub tags
    tags = derive_tags_for_tool(
        "github", "get_file", {"owner": "octocat", "repo": "hello", "path": "README.md"}
    )
    assert "github:repo:octocat/hello" in tags
    assert "github:file:octocat/hello:README.md" in tags

    print("✓ Tag derivation working correctly")


def test_memory_cache_instance():
    """Test memory cache singleton."""
    cache1 = get_cache_service(backend="memory")
    cache2 = get_cache_service(backend="memory")

    assert cache1 is cache2  # Should be same instance
    assert isinstance(cache1, MemoryInvokeCache)
    print("✓ Memory cache singleton working correctly")


def test_postgres_cache_requires_session():
    """Test PostgreSQL cache requires session."""
    try:
        get_cache_service(backend="postgres")
        raise AssertionError("Should raise ValueError")
    except ValueError as e:
        assert "requires a database session" in str(e)
    print("✓ PostgreSQL cache correctly requires session")


async def test_memory_cache_operations():
    """Test basic memory cache operations."""

    cache = MemoryInvokeCache()

    # Test set and get
    key = "test:key"
    value = {"data": "test"}

    await cache.set(key, value, ttl_s=3600)
    result = await cache.get(key)

    assert result is not None
    assert result["data"] == "test"
    assert "_cache_age_s" in result
    assert "_cache_ttl_remaining_s" in result

    # Test delete
    await cache.delete(key)
    result = await cache.get(key)
    assert result is None

    # Test stats
    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["sets"] == 1
    assert stats["deletes"] == 1

    print("✓ Memory cache operations working correctly")


def main():
    """Run all tests."""
    print("\n=== Testing Cache Components ===\n")

    # Run sync tests
    test_imports()
    test_ttl_policies()
    test_cache_size_limit()
    test_canonical_json()
    test_canonical_args_hash()
    test_cache_key_generation()
    test_tag_derivation()
    test_memory_cache_instance()
    test_postgres_cache_requires_session()

    # Run async test
    import asyncio

    asyncio.run(test_memory_cache_operations())

    print("\n✅ All tests passed!\n")


if __name__ == "__main__":
    main()
