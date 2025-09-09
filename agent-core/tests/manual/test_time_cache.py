#!/usr/bin/env python3
"""
Verify that time MCP responses are NOT cached (time-sensitive).
"""

import asyncio
import os
import time
from typing import Any, Dict

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_URL = "http://localhost:8080"
API_KEY = os.getenv("API_KEY", "test-api-key-123456789012345678901234567890")


async def check_health() -> Dict[str, Any]:
    """Check server health."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{BASE_URL}/healthz")
        return response.json()


async def test_time_cache():
    """Test cache with time MCP queries."""
    headers = {"X-API-Key": API_KEY}

    # Test query using time MCP
    request_data = {
        "messages": [{"role": "user", "content": "What time is it in UTC?"}],
        "deviceToken": "dtok_test_cache_001",
    }

    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        # First call (should be cache miss)
        print("   First call (expect cache miss):")
        start = time.time()
        response = await client.post(f"{BASE_URL}/api/v1/chat", json=request_data)
        first_duration = time.time() - start

        if response.status_code == 200:
            result = response.json()
            cache_hit = result.get("meta", {}).get("cacheHit", False)
            print(f"   ✓ Response received in {first_duration:.2f}s")
            print(f"   Cache hit: {cache_hit} (expected: False)")
            print(f"   Reply preview: {result.get('reply', '')[:100]}...")

            # Wait a moment
            await asyncio.sleep(1)

            # Second call (should still be cache miss - time is denylisted)
            print("\n   Second call (expect cache miss - time is denylisted):")
            start = time.time()
            response2 = await client.post(f"{BASE_URL}/api/v1/chat", json=request_data)
            second_duration = time.time() - start

            if response2.status_code == 200:
                result2 = response2.json()
                cache_hit2 = result2.get("meta", {}).get("cacheHit", False)
                print(f"   ✓ Response received in {second_duration:.2f}s")
                print(f"   Cache hit: {cache_hit2} (expected: False)")
                print(f"   Reply preview: {result2.get('reply', '')[:100]}...")

                # Both calls should have similar duration (no caching)
                if abs(second_duration - first_duration) < 0.5:
                    print(
                        "\n   ✓ Both calls took similar time (no caching as expected)"
                    )

                # Verify cache metadata
                ttl = result2.get("meta", {}).get("cacheTtlRemaining")
                if ttl:
                    print(f"   Cache TTL remaining: {ttl}s")

                return not cache_hit2  # Should be False => test passes
            else:
                print(f"   ❌ Error: {response2.status_code} - {response2.text}")
                return False
        else:
            print(f"   ❌ Error: {response.status_code} - {response.text}")
            return False


async def main():
    """Main test runner."""
    print("=" * 60)
    print("Alfred Agent Core - Time MCP Cache Testing")
    print("=" * 60)
    print("\n⚠️  Prerequisites:")
    print("1. Server must be running (make run)")
    print("2. Time MCP server should be configured")
    print("\nStarting tests...\n")

    # Check health
    print("1. Checking server health...")
    try:
        health = await check_health()
        print(f"   Server status: {health.get('status')}")
        print(f"   Version: {health.get('version')}")
        print(f"   MCP servers available: {health.get('mcp_servers', [])}")
    except Exception as e:
        print(f"   ❌ Health check failed: {e}")
        return

    # Test cache with time MCP
    print("\n2. Testing cache with time MCP queries...")
    try:
        cache_worked = await test_time_cache()

        if cache_worked:
            print("\n✅ Test PASSED! Time operations are correctly NOT cached.")
        else:
            print("\n❌ Test FAILED! Time operations should not be cached.")
    except Exception as e:
        print(f"\n   ❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
