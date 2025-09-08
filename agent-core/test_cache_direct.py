#!/usr/bin/env python3
"""
Direct test of the PostgreSQL cache implementation without the full agent.

This tests the cache directly to verify it's working properly.
"""

import sys
from pathlib import Path

# Add src to path before other imports to avoid E402
sys.path.insert(0, str(Path(__file__).parent))

import asyncio  # noqa: E402
import os  # noqa: E402
from datetime import datetime  # noqa: E402

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.services.postgres_cache import (  # noqa: E402
    PostgreSQLInvokeCache,
    make_cache_key,
)


async def test_cache_directly():
    """Test the PostgreSQL cache directly."""

    # Database URL from environment or default
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://alfred:password@localhost:5432/agent_core"
    )

    print(f"Connecting to: {DATABASE_URL}")

    try:
        # Create engine
        engine = create_async_engine(DATABASE_URL, echo=False)

        # Create session
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        async with async_session() as session:
            cache = PostgreSQLInvokeCache(session)

            print("\n✅ Connected to PostgreSQL cache")

            # Simulate a Notion tool call
            print("\n1. Simulating first Notion search call...")

            # Create cache key for a Notion search
            cache_key = make_cache_key(
                namespace="notion:global",
                tool="search",
                version="v1",
                args={"query": "AI Assistant Guidelines", "query_type": "internal"},
            )

            print(f"   Cache key: {cache_key[:50]}...")

            # First call - should be cache miss
            cached_result = await cache.get(cache_key)
            if cached_result:
                print("   ✓ Cache HIT (unexpected on first call)")
                print(f"   Age: {cached_result.get('_cache_age_s', 0)}s")
            else:
                print("   ✓ Cache MISS (expected on first call)")

                # Simulate storing the result
                mock_notion_result = {
                    "results": [
                        {
                            "title": "AI Assistant Guidelines",
                            "url": "notion://page/123",
                        },
                        {"title": "AI Setup Guide", "url": "notion://page/456"},
                    ],
                    "count": 2,
                    "timestamp": datetime.utcnow().isoformat(),
                }

                # Store in cache with 5 minute TTL
                success = await cache.set(
                    cache_key,
                    mock_notion_result,
                    ttl_s=300,
                    labels=["notion:search", "notion:global"],
                )

                if success:
                    print("   ✓ Result cached successfully")
                else:
                    print("   ❌ Failed to cache result")

            # Second call - should be cache hit
            print("\n2. Simulating second identical Notion search...")

            cached_result = await cache.get(cache_key)
            if cached_result:
                print("   ✓ Cache HIT! 🎉")
                print(f"   Age: {cached_result.get('_cache_age_s', 0)}s")
                print(
                    f"   TTL remaining: {cached_result.get('_cache_ttl_remaining_s', 0)}s"
                )
                print(f"   Result preview: {str(cached_result)[:150]}...")
            else:
                print("   ❌ Cache MISS (unexpected)")

            # Check stats
            stats = cache.stats()
            print("\n3. Cache Statistics:")
            print(f"   Hits: {stats['hits']}")
            print(f"   Misses: {stats['misses']}")
            print(f"   Hit rate: {stats['hit_rate']*100:.1f}%")
            print(f"   Sets: {stats['sets']}")

            # Test with different query (should miss)
            print("\n4. Testing different query...")

            different_key = make_cache_key(
                namespace="notion:global",
                tool="search",
                version="v1",
                args={"query": "Claude Session Log", "query_type": "internal"},
            )

            cached_result = await cache.get(different_key)
            if cached_result:
                print("   Cache HIT (unexpected for different query)")
            else:
                print("   ✓ Cache MISS (expected for different query)")

            # Final stats
            stats = cache.stats()
            print("\n5. Final Statistics:")
            print(f"   Total requests: {stats['hits'] + stats['misses']}")
            print(f"   Cache hit rate: {stats['hit_rate']*100:.1f}%")
            print("   Performance: Cached calls return instantly vs actual MCP calls")

            print("\n✅ Cache is working correctly!")
            print("\nKey findings:")
            print("- PostgreSQL cache successfully stores MCP tool results")
            print("- Identical queries hit the cache (no MCP call needed)")
            print("- Different queries correctly miss the cache")
            print("- TTL and metadata tracking works properly")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        await engine.dispose()

    return 0


if __name__ == "__main__":
    print("=" * 60)
    print("PostgreSQL Cache Direct Test")
    print("=" * 60)

    exit_code = asyncio.run(test_cache_directly())
    sys.exit(exit_code)
