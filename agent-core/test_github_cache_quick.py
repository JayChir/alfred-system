#!/usr/bin/env python3
"""
Quick test to verify GitHub operations are cached with the narrowed denylist.
"""

import asyncio
import time

import httpx

BASE_URL = "http://localhost:8080"
API_KEY = "test-api-key-123456789012345678901234567890"


async def test_github_cache():
    """Test that GitHub read operations are properly cached."""
    headers = {"X-API-Key": API_KEY}

    # Query that should trigger GitHub tool
    request_data = {
        "messages": [{"role": "user", "content": "search github for pydantic"}],
        "deviceToken": "dtok_test_github_cache",
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        print("Testing GitHub cache with narrowed denylist...")

        # First call - should be cache miss
        print("\n1. First call (expect cache miss):")
        start = time.time()
        response1 = await client.post(f"{BASE_URL}/api/v1/chat", json=request_data)
        duration1 = time.time() - start

        if response1.status_code == 200:
            result1 = response1.json()
            cache_hit1 = result1.get("meta", {}).get("cacheHit", False)
            print(f"   Response in {duration1:.2f}s")
            print(f"   Cache hit: {cache_hit1} (expected: False)")
            print(f"   Reply preview: {result1.get('reply', '')[:80]}...")
        else:
            print(f"   Error: {response1.status_code}")
            return False

        # Wait a moment
        await asyncio.sleep(1)

        # Second call - should be cache hit (GitHub search is not denylisted)
        print("\n2. Second call (expect cache HIT):")
        start = time.time()
        response2 = await client.post(f"{BASE_URL}/api/v1/chat", json=request_data)
        duration2 = time.time() - start

        if response2.status_code == 200:
            result2 = response2.json()
            cache_hit2 = result2.get("meta", {}).get("cacheHit", False)
            print(f"   Response in {duration2:.2f}s")
            print(f"   Cache hit: {cache_hit2} (expected: True)")
            print(f"   Reply preview: {result2.get('reply', '')[:80]}...")

            if cache_hit2:
                speedup = duration1 / duration2 if duration2 > 0 else 0
                print(f"\n   ✓ Cache HIT! Speedup: {speedup:.1f}x")
                print(f"   Time saved: {duration1 - duration2:.2f}s")
                ttl = result2.get("meta", {}).get("cacheTtlRemaining")
                if ttl:
                    print(f"   TTL remaining: {ttl}s")
                return True
            else:
                print("   ✗ Cache miss - GitHub operations should be cached!")
                return False
        else:
            print(f"   Error: {response2.status_code}")
            return False


async def main():
    success = await test_github_cache()
    if success:
        print("\n✅ GitHub cache test PASSED!")
    else:
        print("\n❌ GitHub cache test FAILED!")


if __name__ == "__main__":
    asyncio.run(main())
