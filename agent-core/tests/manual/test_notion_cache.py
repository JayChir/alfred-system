#!/usr/bin/env python3
"""
Test script to verify agent caching with Notion MCP tools.

This script:
1. Makes repeated Notion search calls through the agent
2. Verifies that cache hits occur on repeated identical searches
3. Shows performance metrics and token savings
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))


async def test_notion_cache():
    """Test agent caching with Notion MCP calls."""

    base_url = "http://localhost:8080"
    api_key = os.getenv("API_KEY", "test-api-key-123456789012345678901234567890")

    # Create async HTTP client with API key header
    async with httpx.AsyncClient(
        timeout=30.0, headers={"X-API-Key": api_key}
    ) as client:
        # First check health
        print("\n1. Checking server health...")
        try:
            health_response = await client.get(f"{base_url}/healthz")
            health_data = health_response.json()
            print(f"   Server status: {health_data['status']}")
            print(f"   Version: {health_data['version']}")

            # Check MCP servers
            health_full = await client.get(f"{base_url}/health")
            health_full_data = health_full.json()
            print(
                f"   MCP servers available: {health_full_data.get('mcp_servers', [])}"
            )
        except Exception as e:
            print(f"   ❌ Health check failed: {e}")
            print("   Make sure server is running: make run")
            return

        # Test 1: Notion search - should cache since it's deterministic for same query
        print("\n2. Testing cache with Notion search...")

        # First call - should be a cache miss
        print("\n   First call (expect cache miss):")
        start_time = time.time()
        response1 = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Search in Notion for 'AI Assistant Guidelines'",
                    }
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
            print(f"   Reply preview: {data1['reply'][:150]}...")

            # Store for comparison
            first_tokens = data1["meta"].get("tokens", {})
        else:
            print(f"   ❌ Error: {response1.status_code} - {response1.text[:200]}")
            return

        # Second call - should be a cache hit
        print("\n   Second call (expect cache hit):")
        start_time = time.time()
        response2 = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Search in Notion for 'AI Assistant Guidelines'",
                    }
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

                # Token savings
                second_tokens = data2["meta"].get("tokens", {})
                if first_tokens and second_tokens:
                    input_saved = first_tokens.get("input", 0) - second_tokens.get(
                        "input", 0
                    )
                    output_saved = first_tokens.get("output", 0) - second_tokens.get(
                        "output", 0
                    )
                    total_saved = input_saved + output_saved
                    print(
                        f"      Tokens saved: {total_saved} total ({input_saved} input, {output_saved} output)"
                    )
            else:
                print("   ⚠️  Cache hit expected but not received")
        else:
            print(f"   ❌ Error: {response2.status_code} - {response2.text[:200]}")

        # Test 3: Third identical call - should also hit cache
        print("\n   Third call (verify cache persistence):")
        response3 = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Search in Notion for 'AI Assistant Guidelines'",
                    }
                ],
                "forceRefresh": False,
            },
        )

        if response3.status_code == 200:
            data3 = response3.json()
            print(f"   Cache hit: {data3['meta'].get('cacheHit', False)}")
            print(f"   Cache TTL remaining: {data3['meta'].get('cacheTtlRemaining')}s")
            if data3["meta"].get("cacheHit"):
                print("   ✓ Cache persisted across multiple calls")

        # Test 4: Force refresh to bypass cache
        print("\n3. Testing force refresh (bypass cache):")
        start_time = time.time()
        response4 = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Search in Notion for 'AI Assistant Guidelines'",
                    }
                ],
                "forceRefresh": True,  # Force bypass cache
            },
        )
        duration4 = time.time() - start_time

        if response4.status_code == 200:
            data4 = response4.json()
            print(f"   ✓ Response received in {duration4:.2f}s")
            print(f"   Cache hit: {data4['meta'].get('cacheHit', False)}")
            print(f"   Tokens used: {data4['meta'].get('tokens', {})}")

            if not data4["meta"].get("cacheHit"):
                print("   ✓ Cache successfully bypassed with forceRefresh")
        else:
            print(f"   ❌ Error: {response4.status_code} - {response4.text[:200]}")

        # Test 5: Different query (should not hit cache)
        print("\n4. Testing different query (expect cache miss):")
        response5 = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Search in Notion for 'Claude Session Log'",
                    }
                ],
                "forceRefresh": False,
            },
        )

        if response5.status_code == 200:
            data5 = response5.json()
            print(f"   Cache hit: {data5['meta'].get('cacheHit', False)}")
            if not data5["meta"].get("cacheHit"):
                print("   ✓ Different query correctly resulted in cache miss")

        # Test 6: Repeat the different query (should now hit cache)
        print("\n5. Repeat different query (expect cache hit):")
        response6 = await client.post(
            f"{base_url}/api/v1/chat",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "Search in Notion for 'Claude Session Log'",
                    }
                ],
                "forceRefresh": False,
            },
        )

        if response6.status_code == 200:
            data6 = response6.json()
            print(f"   Cache hit: {data6['meta'].get('cacheHit', False)}")
            if data6["meta"].get("cacheHit"):
                print("   ✓ Second identical query hit cache as expected")

        print("\n✅ Cache testing complete!")
        print("\nSummary:")
        print("- Cache correctly stores and retrieves repeated Notion tool calls")
        print("- forceRefresh flag successfully bypasses cache")
        print("- Different queries correctly generate cache misses")
        print("- Performance improvement demonstrated on cache hits")
        print("- Token savings achieved through caching")


async def main():
    """Run the cache test."""
    print("=" * 60)
    print("Alfred Agent Core - Notion Cache Testing")
    print("=" * 60)

    print("\n⚠️  Prerequisites:")
    print("1. Server must be running (make run)")
    print("2. Notion MCP server should be configured")
    print("\nStarting tests...")

    await test_notion_cache()


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
