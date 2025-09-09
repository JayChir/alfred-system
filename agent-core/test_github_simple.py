import asyncio

import httpx

BASE_URL = "http://localhost:8080"
API_KEY = "test-api-key-123456789012345678901234567890"


async def test_simple_github():
    """Test with a simpler GitHub query."""
    print("Testing simple GitHub query...")

    request_data = {
        "messages": [{"role": "user", "content": "List 3 Python repositories"}],
        "deviceToken": "dtok_test_simple_github",
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

        print("\n1. First call:")
        try:
            response1 = await client.post(
                f"{BASE_URL}/api/v1/chat", json=request_data, headers=headers
            )
            if response1.status_code == 200:
                data1 = response1.json()
                print(f"   Cache hit: {data1['meta']['cacheHit']}")
                print(f"   Reply preview: {data1['reply'][:100]}...")
            else:
                print(f"   Error: {response1.status_code}")
                print(f"   Response: {response1.text[:500]}")
                return False
        except Exception as e:
            print(f"   Exception: {e}")
            return False

        print("\n2. Second call (should be cached):")
        try:
            response2 = await client.post(
                f"{BASE_URL}/api/v1/chat", json=request_data, headers=headers
            )
            if response2.status_code == 200:
                data2 = response2.json()
                print(f"   Cache hit: {data2['meta']['cacheHit']}")
                print(f"   Reply preview: {data2['reply'][:100]}...")

                if data2["meta"]["cacheHit"]:
                    print("\n✅ Cache is working!")
                    return True
                else:
                    print("\n❌ Cache not working - second call wasn't cached")
                    return False
            else:
                print(f"   Error: {response2.status_code}")
                print(f"   Response: {response2.text[:500]}")
                return False
        except Exception as e:
            print(f"   Exception: {e}")
            return False


if __name__ == "__main__":
    asyncio.run(test_simple_github())
