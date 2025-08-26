#!/usr/bin/env python3
"""Test script for structured logging."""

import os

# Set minimal test configuration
os.environ["API_KEY"] = "test-api-key-with-minimum-32-characters-long"
os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-key"
os.environ["APP_ENV"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from src.app import app  # noqa: E402

# Create test client
client = TestClient(app)

# Test health endpoint
print("\n=== Testing Health Endpoint ===")
response = client.get("/healthz")
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
print(f"Headers: {dict(response.headers)}")

# Test chat endpoint without auth
print("\n=== Testing Chat Without Auth ===")
response = client.post(
    "/api/v1/chat", json={"messages": [{"role": "user", "content": "Hello"}]}
)
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")

# Test chat endpoint with auth
print("\n=== Testing Chat With Auth ===")
response = client.post(
    "/api/v1/chat",
    json={"messages": [{"role": "user", "content": "Hello"}]},
    headers={"X-API-Key": "test-api-key-with-minimum-32-characters-long"},
)
print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
print(f"Request ID: {response.headers.get('X-Request-ID')}")
print(f"Response Time: {response.headers.get('X-Response-Time')}")

print("\n✓ All logging tests completed")
