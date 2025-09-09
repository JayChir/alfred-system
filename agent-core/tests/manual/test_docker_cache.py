#!/usr/bin/env python3
"""
Test the PostgreSQL cache mechanism with the dockerized agent.
Makes repeated Notion tool calls to verify caching behavior.
"""

import asyncio
from datetime import datetime

import httpx

API_URL = "http://localhost:8080/api/v1/chat"
API_KEY = "test-api-key-123456789012345678901234567890"


async def test_notion_cache():
    """Test that repeated Notion tool calls are cached."""

    async with httpx.AsyncClient(timeout=30.0) as client:
        headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

        # Test 1: First call to search Notion (should be cache miss)
        print("\n=== Test 1: Initial Notion search (expect cache miss) ===")
        request1 = {
            "messages": [
                {
                    "role": "user",
                    "content": "Search my Notion workspace for information about 'AI Assistant Guidelines'",
                }
            ],
            "deviceToken": "test-cache-001",
        }

        start = datetime.now()
        response1 = await client.post(API_URL, json=request1, headers=headers)
        duration1 = (datetime.now() - start).total_seconds()

        if response1.status_code == 200:
            data1 = response1.json()
            print(f"✓ Response received in {duration1:.2f}s")
            print(f"  Cache hit: {data1.get('meta', {}).get('cacheHit', False)}")
            print(f"  Response preview: {data1.get('reply', '')[:200]}...")
            if data1.get("meta", {}).get("tokens"):
                print(f"  Tokens used: {data1['meta']['tokens']}")
        else:
            print(f"✗ Error: {response1.status_code}")
            print(f"  Response: {response1.text}")
            return

        # Test 2: Exact same query (should be cache hit)
        print("\n=== Test 2: Repeat exact same search (expect cache hit) ===")
        request2 = {
            "messages": [
                {
                    "role": "user",
                    "content": "Search my Notion workspace for information about 'AI Assistant Guidelines'",
                }
            ],
            "deviceToken": "test-cache-001",
        }

        start = datetime.now()
        response2 = await client.post(API_URL, json=request2, headers=headers)
        duration2 = (datetime.now() - start).total_seconds()

        if response2.status_code == 200:
            data2 = response2.json()
            print(f"✓ Response received in {duration2:.2f}s")
            print(f"  Cache hit: {data2.get('meta', {}).get('cacheHit', False)}")
            print(f"  Speed improvement: {duration1/duration2:.1f}x faster")

            # Verify it's a cache hit
            if data2.get("meta", {}).get("cacheHit"):
                print("  ✓ CACHE HIT CONFIRMED!")
                if data2.get("meta", {}).get("cacheTtlRemaining"):
                    print(f"  TTL remaining: {data2['meta']['cacheTtlRemaining']}s")
            else:
                print("  ✗ Expected cache hit but got miss")
        else:
            print(f"✗ Error: {response2.status_code}")
            print(f"  Response: {response2.text}")

        # Test 3: Similar but different query (should be cache miss)
        print("\n=== Test 3: Different Notion search (expect cache miss) ===")
        request3 = {
            "messages": [
                {
                    "role": "user",
                    "content": "Search my Notion workspace for 'Claude Session Log'",
                }
            ],
            "deviceToken": "test-cache-001",
        }

        start = datetime.now()
        response3 = await client.post(API_URL, json=request3, headers=headers)
        duration3 = (datetime.now() - start).total_seconds()

        if response3.status_code == 200:
            data3 = response3.json()
            print(f"✓ Response received in {duration3:.2f}s")
            print(f"  Cache hit: {data3.get('meta', {}).get('cacheHit', False)}")
            print(f"  Response preview: {data3.get('reply', '')[:200]}...")
        else:
            print(f"✗ Error: {response3.status_code}")
            print(f"  Response: {response3.text}")

        # Test 4: Force refresh on cached query
        print("\n=== Test 4: Force refresh on cached query ===")
        request4 = {
            "messages": [
                {
                    "role": "user",
                    "content": "Search my Notion workspace for information about 'AI Assistant Guidelines'",
                }
            ],
            "deviceToken": "test-cache-001",
            "forceRefresh": True,
        }

        start = datetime.now()
        response4 = await client.post(API_URL, json=request4, headers=headers)
        duration4 = (datetime.now() - start).total_seconds()

        if response4.status_code == 200:
            data4 = response4.json()
            print(f"✓ Response received in {duration4:.2f}s")
            print(f"  Cache hit: {data4.get('meta', {}).get('cacheHit', False)}")
            print(
                f"  Force refresh bypassed cache: {not data4.get('meta', {}).get('cacheHit', False)}"
            )
        else:
            print(f"✗ Error: {response4.status_code}")
            print(f"  Response: {response4.text}")

        # Summary
        print("\n=== Cache Test Summary ===")
        cache_hits = sum(
            [
                data2.get("meta", {}).get("cacheHit", False)
                if response2.status_code == 200
                else False,
            ]
        )
        total_calls = 4
        print(
            f"Cache hits: {cache_hits}/{total_calls-1} eligible calls (excluding force refresh)"
        )
        print(f"Average speedup on cache hits: {duration1/duration2:.1f}x")

        return cache_hits > 0


if __name__ == "__main__":
    success = asyncio.run(test_notion_cache())
    exit(0 if success else 1)
