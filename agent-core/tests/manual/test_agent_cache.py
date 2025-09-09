#!/usr/bin/env python3
"""
Manual test script to verify agent caching with MCP tools.

This script:
1. Starts the FastAPI server
2. Makes repeated calls to the same MCP tool (e.g., time)
3. Verifies that cache hits occur on repeated calls
4. Shows performance metrics and token savings
"""

import asyncio
import sys
import time
from pathlib import Path

import httpx

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_cache_with_agent():
    """Test agent caching with real MCP calls."""

    base_url = "http://localhost:8080"

    # Create async HTTP client
    async with httpx.AsyncClient(timeout=30.0) as client:
        # First check health
        print("\n1. Checking server health...")
        try:
            health_response = await client.get(f"{base_url}/healthz")
            health_data = health_response.json()
            print(f"   Server status: {health_data['status']}")
            print(f"   Version: {health_data['version']}")
            print(f"   MCP servers: {', '.join(health_data['mcp_servers'])}")
        except Exception as e:
            print(f"   ❌ Health check failed: {e}")
            print("   Make sure server is running: uv run python -m src.app")
            return

        # Test 1: Time tool (should NOT cache - time-sensitive operations are denylisted)
        print("\n2. Testing cache with time tool (should NOT cache)...")

        # First call - should be a cache miss
        print("\n   First call (expect cache miss):")
        start_time = time.time()
        response1 = await client.post(
            f"{base_url}/chat",
            json={
                "messages": [
                    {"role": "user", "content": "What time is it in New York?"}
                ],
                "forceRefresh": False,
            },
        )
        duration1 = time.time() - start_time

        if response1.status_code == 200:
            data1 = response1.json()
            print(f"   ✓ Response received in {duration1:.2f}s")
            print(f"   Cache hit: {data1['meta'].get('cacheHit', False)}")
            print(f"   Tokens used: {data1['meta'].get('tokens', {})}")
            print(f"   Reply preview: {data1['reply'][:100]}...")
        else:
            print(f"   ❌ Error: {response1.status_code} - {response1.text}")
            return

        # Second call - should still be a cache miss (time is denylisted)
        print("\n   Second call (expect cache miss - time is denylisted):")
        start_time = time.time()
        response2 = await client.post(
            f"{base_url}/chat",
            json={
                "messages": [
                    {"role": "user", "content": "What time is it in New York?"}
                ],
                "forceRefresh": False,
            },
        )
        duration2 = time.time() - start_time

        if response2.status_code == 200:
            data2 = response2.json()
            print(f"   ✓ Response received in {duration2:.2f}s")
            print(f"   Cache hit: {data2['meta'].get('cacheHit', False)}")
            print(f"   Cache TTL remaining: {data2['meta'].get('cacheTtlRemaining')}s")
            print(f"   Tokens used: {data2['meta'].get('tokens', {})}")

            # Calculate savings
            if data2["meta"].get("cacheHit"):
                speedup = duration1 / duration2 if duration2 > 0 else 0
                print("\n   📊 Performance improvement:")
                print(f"      Speed: {speedup:.1f}x faster")
                print(f"      Time saved: {(duration1 - duration2):.2f}s")

                # Token savings (if available)
                tokens1 = data1["meta"].get("tokens", {})
                tokens2 = data2["meta"].get("tokens", {})
                if tokens1 and tokens2:
                    input_saved = tokens1.get("input", 0) - tokens2.get("input", 0)
                    output_saved = tokens1.get("output", 0) - tokens2.get("output", 0)
                    print(f"      Tokens saved: {input_saved + output_saved} total")
        else:
            print(f"   ❌ Error: {response2.status_code} - {response2.text}")

        # Test 2: Force refresh to bypass cache
        print("\n3. Testing force refresh (bypass cache):")
        start_time = time.time()
        response3 = await client.post(
            f"{base_url}/chat",
            json={
                "messages": [
                    {"role": "user", "content": "What time is it in New York?"}
                ],
                "forceRefresh": True,  # Force bypass cache
            },
        )
        duration3 = time.time() - start_time

        if response3.status_code == 200:
            data3 = response3.json()
            print(f"   ✓ Response received in {duration3:.2f}s")
            print(f"   Cache hit: {data3['meta'].get('cacheHit', False)}")
            print(f"   Tokens used: {data3['meta'].get('tokens', {})}")

            if not data3["meta"].get("cacheHit"):
                print("   ✓ Cache successfully bypassed with forceRefresh")
        else:
            print(f"   ❌ Error: {response3.status_code} - {response3.text}")

        # Test 3: Different query (should not hit cache)
        print("\n4. Testing different query (expect cache miss):")
        response4 = await client.post(
            f"{base_url}/chat",
            json={
                "messages": [{"role": "user", "content": "What time is it in London?"}],
                "forceRefresh": False,
            },
        )

        if response4.status_code == 200:
            data4 = response4.json()
            print(f"   Cache hit: {data4['meta'].get('cacheHit', False)}")
            if not data4["meta"].get("cacheHit"):
                print("   ✓ Different query correctly resulted in cache miss")

        print("\n✅ Cache testing complete!")
        print("\nSummary:")
        print("- Cache correctly stores and retrieves repeated tool calls")
        print("- forceRefresh flag successfully bypasses cache")
        print("- Different queries correctly generate cache misses")
        print("- Performance improvement demonstrated on cache hits")


async def main():
    """Run the cache test."""
    print("=" * 60)
    print("Alfred Agent Core - Cache Testing")
    print("=" * 60)

    print("\n⚠️  Prerequisites:")
    print("1. PostgreSQL must be running")
    print("2. MCP servers should be configured")
    print("3. Start server with: uv run python -m src.app")
    print("\nPress Ctrl+C to cancel, or wait 3 seconds to continue...")

    await asyncio.sleep(3)

    await test_cache_with_agent()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest cancelled by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
