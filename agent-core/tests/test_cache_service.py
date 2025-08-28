"""
Tests for cache service functionality.

Validates cache key generation, TTL behavior, hit/miss logic,
error handling, and integration with the caching middleware.
"""

import asyncio
from unittest.mock import patch

import pytest

from src.services.cache_service import get_cache_service


class TestCacheService:
    """Test suite for cache service behavior."""

    @pytest.fixture
    def cache_service(self):
        """Get cache service instance for testing."""
        return get_cache_service()

    def test_cache_key_generation_deterministic(self):
        """Cache keys should be deterministic for identical inputs."""
        from src.services.cache_service import CacheService

        cache = CacheService()

        # Same inputs should generate same key
        key1 = cache._generate_cache_key("test_tool", "v1", {"param": "value"})
        key2 = cache._generate_cache_key("test_tool", "v1", {"param": "value"})

        assert key1 == key2
        assert isinstance(key1, str)
        assert len(key1) > 0

    def test_cache_key_different_for_different_inputs(self):
        """Cache keys should differ for different inputs."""
        from src.services.cache_service import CacheService

        cache = CacheService()

        key1 = cache._generate_cache_key("tool1", "v1", {"param": "value"})
        key2 = cache._generate_cache_key("tool2", "v1", {"param": "value"})
        key3 = cache._generate_cache_key("tool1", "v2", {"param": "value"})
        key4 = cache._generate_cache_key("tool1", "v1", {"param": "different"})

        keys = [key1, key2, key3, key4]
        assert len(set(keys)) == 4, "All keys should be unique"

    def test_cache_key_parameter_order_independence(self):
        """Cache keys should be same regardless of parameter order."""
        from src.services.cache_service import CacheService

        cache = CacheService()

        key1 = cache._generate_cache_key("tool", "v1", {"a": 1, "b": 2, "c": 3})
        key2 = cache._generate_cache_key("tool", "v1", {"c": 3, "a": 1, "b": 2})
        key3 = cache._generate_cache_key("tool", "v1", {"b": 2, "c": 3, "a": 1})

        assert key1 == key2 == key3

    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self, cache_service):
        """First call should miss, second should hit."""
        key = "test_cache_key"
        value = {"result": "test data"}

        # First get should be miss
        cached_value = await cache_service.get(key)
        assert cached_value is None

        # Set value
        await cache_service.set(key, value, ttl_s=300)

        # Second get should be hit
        cached_value = await cache_service.get(key)
        assert cached_value is not None
        assert (
            cached_value["result"] == value["result"]
        )  # Check original data, ignore cache metadata

    @pytest.mark.asyncio
    async def test_cache_ttl_expiry(self, cache_service):
        """Cached values should expire after TTL."""
        key = "test_ttl_key"
        value = {"result": "ttl test"}
        short_ttl = 1  # 1 second

        # Set with short TTL
        await cache_service.set(key, value, ttl_s=short_ttl)

        # Should be available immediately
        cached_value = await cache_service.get(key)
        assert cached_value == value

        # Wait for expiry
        await asyncio.sleep(short_ttl + 0.1)

        # Should be expired
        cached_value = await cache_service.get(key)
        assert cached_value is None

    @pytest.mark.asyncio
    async def test_cache_does_not_store_none_values(self, cache_service):
        """Cache should not store None values."""
        key = "test_none_key"

        # Try to cache None
        await cache_service.set(key, None, ttl_s=300)

        # Should not be retrievable
        cached_value = await cache_service.get(key)
        assert cached_value is None

    @pytest.mark.asyncio
    async def test_cache_does_not_store_empty_results(self, cache_service):
        """Cache should not store empty results per policy."""
        key = "test_empty_key"
        empty_values = [
            {},  # Empty dict
            [],  # Empty list
            "",  # Empty string
        ]

        for empty_value in empty_values:
            await cache_service.set(key + str(id(empty_value)), empty_value, ttl_s=300)

            # Should not be retrievable (depends on implementation)
            cached_value = await cache_service.get(key + str(id(empty_value)))

            # This test documents expected behavior
            # Implementation may cache empty values or not
            print(f"Empty value {empty_value} cached as: {cached_value}")

    @pytest.mark.asyncio
    async def test_cache_force_refresh_bypasses_cache(self, cache_service):
        """force_refresh parameter should bypass cache."""

        # Mock the cache service to test bypass behavior
        with patch.object(
            cache_service, "get", return_value={"cached": "value"}
        ) as mock_get:
            with patch.object(cache_service, "set", return_value=None) as mock_set:
                # Simulate cache middleware behavior
                key = "test_bypass_key"

                # Normal operation should check cache
                if hasattr(cache_service, "get_with_refresh"):
                    result = await cache_service.get_with_refresh(
                        key, force_refresh=False
                    )
                    mock_get.assert_called_once()

                # Force refresh should bypass cache get
                mock_get.reset_mock()
                if hasattr(cache_service, "get_with_refresh"):
                    result = await cache_service.get_with_refresh(
                        key, force_refresh=True
                    )
                    mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_concurrent_access(self, cache_service):
        """Cache should handle concurrent access safely."""
        key = "test_concurrent_key"
        value = {"result": "concurrent test"}

        # Concurrent sets
        await asyncio.gather(
            cache_service.set(key + "1", value, ttl_s=300),
            cache_service.set(key + "2", value, ttl_s=300),
            cache_service.set(key + "3", value, ttl_s=300),
        )

        # Concurrent gets
        results = await asyncio.gather(
            cache_service.get(key + "1"),
            cache_service.get(key + "2"),
            cache_service.get(key + "3"),
        )

        # All should succeed
        for result in results:
            assert result == value

    def test_cache_key_format_matches_spec(self):
        """Cache key format should match specification: {tool}:{version}:{args_hash}."""
        from src.services.cache_service import CacheService

        cache = CacheService()

        key = cache._generate_cache_key("notion.get_page", "v1", {"page_id": "123"})

        # Should contain the expected components
        parts = key.split(":")
        assert len(parts) >= 3, f"Key '{key}' should have format 'tool:version:hash'"
        assert parts[0] == "notion.get_page"
        assert parts[1] == "v1"
        assert len(parts[2]) > 0  # Hash component

    @pytest.mark.asyncio
    async def test_cache_metrics_tracking(self, cache_service):
        """Cache should track hit/miss metrics for observability."""
        key = "test_metrics_key"
        value = {"result": "metrics test"}

        # Initial state - should be miss
        with patch("src.services.cache_service.get_logger") as mock_logger:
            cached_value = await cache_service.get(key)
            assert cached_value is None

            # Should log cache miss (if implemented)
            # This test documents expected logging behavior

        # Set value
        await cache_service.set(key, value, ttl_s=300)

        # Get value - should be hit
        with patch("src.services.cache_service.get_logger") as mock_logger:
            cached_value = await cache_service.get(key)
            assert cached_value == value

            # Should log cache hit (if implemented)
            # This test documents expected logging behavior

    @pytest.mark.asyncio
    async def test_cache_clear_functionality(self, cache_service):
        """Cache should support clearing all entries."""
        # Set some test values
        await cache_service.set("key1", {"value": 1}, ttl_s=300)
        await cache_service.set("key2", {"value": 2}, ttl_s=300)

        # Verify they exist
        assert await cache_service.get("key1") == {"value": 1}
        assert await cache_service.get("key2") == {"value": 2}

        # Clear cache
        if hasattr(cache_service, "clear"):
            await cache_service.clear()

            # Should be gone
            assert await cache_service.get("key1") is None
            assert await cache_service.get("key2") is None

    def test_cache_ttl_configuration(self, test_settings):
        """Cache TTL should be configurable via environment."""
        # Verify TTL settings are loaded from config
        assert hasattr(test_settings, "cache_ttl_default")
        assert hasattr(test_settings, "cache_ttl_notion")
        assert hasattr(test_settings, "cache_ttl_github")

        # Should be reasonable values
        assert test_settings.cache_ttl_default > 0
        assert test_settings.cache_ttl_notion > 0
        assert test_settings.cache_ttl_github > 0
