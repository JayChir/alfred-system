#!/usr/bin/env python3
"""
Integration test with actual PostgreSQL database.
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from src.services.postgres_cache import PostgreSQLInvokeCache  # noqa: E402


async def test_with_postgres():
    """Test cache with actual PostgreSQL database."""

    # Database URL from environment or default
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "postgresql+asyncpg://alfred:password@localhost:5432/agent_core"
    )

    print(f"Connecting to: {DATABASE_URL}")

    try:
        # Create engine
        engine = create_async_engine(DATABASE_URL, echo=False)

        # Create tables
        async with engine.begin() as conn:
            # Drop and recreate cache tables for clean test
            await conn.execute(text("DROP TABLE IF EXISTS agent_cache_tags CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS agent_cache CASCADE"))

            # Create cache tables
            await conn.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS agent_cache (
                    cache_key TEXT PRIMARY KEY,
                    content JSONB NOT NULL,
                    content_hash TEXT,
                    idempotent BOOLEAN DEFAULT true,
                    expires_at TIMESTAMPTZ NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    size_bytes INTEGER,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                )
            """
                )
            )

            await conn.execute(
                text(
                    """
                CREATE TABLE IF NOT EXISTS agent_cache_tags (
                    cache_key TEXT NOT NULL REFERENCES agent_cache(cache_key) ON DELETE CASCADE,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (cache_key, tag)
                )
            """
                )
            )

            await conn.execute(
                text(
                    """
                CREATE INDEX IF NOT EXISTS idx_agent_cache_tags_tag
                ON agent_cache_tags (tag)
            """
                )
            )

            await conn.execute(
                text(
                    """
                CREATE INDEX IF NOT EXISTS idx_agent_cache_expires
                ON agent_cache (expires_at)
            """
                )
            )

        print("✓ Database tables created")

        # Create session
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )

        async with async_session() as session:
            cache = PostgreSQLInvokeCache(session)

            # Test 1: Basic set/get
            print("\nTest 1: Basic set/get")
            key = "test:postgres:1"
            value = {"data": "test_value", "timestamp": datetime.utcnow().isoformat()}

            success = await cache.set(key, value, ttl_s=3600)
            print(f"  Set result: {success}")
            assert success, "Failed to set value"

            result = await cache.get(key)
            print(f"  Get result: {result is not None}")
            assert result is not None, "Failed to get value"
            assert result["data"] == value["data"]
            print("  ✓ Basic set/get working")

            # Test 2: Hit counting
            print("\nTest 2: Hit counting")
            await cache.get(key)  # Second get
            await cache.get(key)  # Third get

            stats = cache.stats()
            print(f"  Stats: hits={stats['hits']}, misses={stats['misses']}")
            assert stats["hits"] == 3, f"Expected 3 hits, got {stats['hits']}"
            print("  ✓ Hit counting working")

            # Test 3: Tag-based invalidation
            print("\nTest 3: Tag-based invalidation")
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

            count = await cache.invalidate_by_tags(["notion:ws:abc"])
            print(f"  Invalidated {count} entries")
            assert count == 2, f"Expected 2 invalidated, got {count}"

            # Verify deleted
            assert await cache.get("notion:1") is None
            assert await cache.get("notion:2") is None
            print("  ✓ Tag invalidation working")

            # Test 4: Size limit
            print("\nTest 4: Size limit")
            large_value = {"data": "x" * (260 * 1024)}  # 260KB
            success = await cache.set("large:key", large_value, ttl_s=3600)
            print(f"  Large entry rejected: {not success}")
            assert not success, "Should reject large entry"
            print("  ✓ Size limit working")

            # Test 5: Advisory lock (singleflight)
            print("\nTest 5: Advisory lock")
            call_count = 0

            async def expensive_op():
                nonlocal call_count
                call_count += 1
                await asyncio.sleep(0.1)
                return {"result": "data", "count": call_count}

            # First call should execute and cache the result
            lock_key = "lock:test:key"
            result, was_cached = await cache.with_cache_fill_lock(
                lock_key, expensive_op
            )
            print(f"  First call: was_cached={was_cached}, call_count={call_count}")
            assert not was_cached
            assert call_count == 1

            # Now manually cache the result for the second test
            await cache.set(lock_key, result, ttl_s=3600)

            # Second call should find cached
            result2, was_cached2 = await cache.with_cache_fill_lock(
                lock_key, expensive_op
            )
            print(f"  Second call: was_cached={was_cached2}, call_count={call_count}")
            assert was_cached2
            assert call_count == 1  # Should not increment
            print("  ✓ Advisory lock working")

            # Test 6: Cleanup
            print("\nTest 6: Cleanup")
            # Create expired entries
            for i in range(5):
                await session.execute(
                    text(
                        """
                    INSERT INTO agent_cache (cache_key, content, expires_at)
                    VALUES (:key, :content, NOW() - INTERVAL '1 hour')
                """
                    ),
                    {"key": f"expired:{i}", "content": "{}"},
                )
            await session.commit()

            cleaned = await cache.cleanup_expired(batch_size=10)
            print(f"  Cleaned {cleaned} expired entries")
            assert cleaned == 5, f"Expected 5 cleaned, got {cleaned}"
            print("  ✓ Cleanup working")

        print("\n✅ All PostgreSQL integration tests passed!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()
        return 1

    finally:
        await engine.dispose()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(test_with_postgres())
    sys.exit(exit_code)
