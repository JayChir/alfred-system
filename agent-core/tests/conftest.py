"""
Shared test fixtures and configuration for agent-core tests.

This module provides reusable fixtures for testing FastAPI endpoints,
MCP router functionality, cache behavior, and logging assertions.
"""

import json
import os
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Set test environment before importing app - use actual .env values but override specific test settings
os.environ.update(
    {
        "APP_ENV": "test",  # Override to test environment
        "LOG_LEVEL": "DEBUG",  # More verbose logging for tests
        "CACHE_BACKEND": "memory",  # Use in-memory cache for tests
        "CACHE_DEFAULT_TTL_SECONDS": "300",
        "MCP_TIMEOUT": "10000",  # Shorter timeout for tests
    }
)

# Load from .env file to get actual API keys and settings
from dotenv import load_dotenv

load_dotenv()

from src.app import app
from src.config import get_settings


@pytest.fixture(scope="function")
def test_client():
    """
    FastAPI TestClient for synchronous endpoint testing.

    Uses test environment configuration and provides clean state
    for each test function.
    """
    with TestClient(app) as client:
        # Add default headers for API authentication
        client.headers.update({"X-API-Key": os.getenv("API_KEY")})
        yield client


@pytest.fixture(scope="function")
async def async_client():
    """
    Async HTTP client for testing async endpoints and SSE streams.

    Provides async context for testing streaming endpoints and
    concurrent request scenarios.
    """
    headers = {"X-API-Key": os.getenv("API_KEY")}
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
        yield client


@pytest.fixture(scope="function")
def mock_mcp_router():
    """
    Mock MCP router for isolating endpoint tests from external MCPs.

    Returns a mock with predefined tool discovery and execution responses
    that can be customized per test case.
    """
    router_mock = AsyncMock()

    # Default mock responses
    router_mock.discover_all_tools.return_value = {
        "test_tool": {
            "name": "test_tool",
            "description": "A test tool",
            "server": "test-server",
        }
    }

    router_mock.execute_tool.return_value = {
        "success": True,
        "result": "Test tool executed successfully",
    }

    router_mock.health_status = {
        "test-server": MagicMock(status="healthy", last_check=None)
    }

    return router_mock


@pytest.fixture(scope="function")
def mock_cache_service():
    """
    Mock cache service for testing cache behavior in isolation.

    Provides controllable cache hit/miss scenarios and TTL testing
    without requiring actual cache backend.
    """
    cache_mock = AsyncMock()

    # Default cache behavior - miss on first call, hit on subsequent
    cache_mock._cache = {}

    async def mock_get(key: str):
        return cache_mock._cache.get(key)

    async def mock_set(key: str, value: Any, ttl: int = 300):
        cache_mock._cache[key] = {
            "value": value,
            "ttl": ttl,
            "cached_at": "2024-01-01T00:00:00Z",
        }

    cache_mock.get = mock_get
    cache_mock.set = mock_set
    cache_mock.clear = AsyncMock()

    return cache_mock


@pytest.fixture(scope="function")
def sample_chat_request():
    """
    Standard chat request payload for endpoint testing.

    Provides valid request structure that matches API contract
    for consistent test data across test cases.
    """
    return {
        "messages": [{"role": "user", "content": "Hello, this is a test message"}],
        "session": None,
        "forceRefresh": False,
    }


@pytest.fixture(scope="function")
def sample_chat_response():
    """
    Expected chat response structure for validation testing.

    Defines the contract that all chat endpoints should return
    including required metadata fields.
    """
    return {
        "reply": "Hello! I'm a test response.",
        "meta": {
            "requestId": "test-request-123",
            "cacheHit": False,
            "cacheTtlRemaining": None,
            "tokens": {"input": 10, "output": 15},
        },
    }


@pytest.fixture(scope="function")
def captured_logs(caplog):
    """
    Structured log capture for testing logging behavior.

    Provides JSON log parsing utilities and assertion helpers
    for validating log output structure and content.
    """
    caplog.set_level("DEBUG")

    def get_structured_logs():
        """Parse captured logs as JSON objects."""
        logs = []
        for record in caplog.records:
            try:
                log_data = json.loads(record.getMessage())
                logs.append(log_data)
            except (json.JSONDecodeError, AttributeError):
                # Handle non-JSON log messages
                logs.append(
                    {
                        "level": record.levelname,
                        "message": record.getMessage(),
                        "raw": True,
                    }
                )
        return logs

    def find_log_with_field(field_name: str, field_value: str = None):
        """Find first log entry with specified field."""
        for log in get_structured_logs():
            if field_name in log:
                if field_value is None or log[field_name] == field_value:
                    return log
        return None

    caplog.get_structured_logs = get_structured_logs
    caplog.find_log_with_field = find_log_with_field

    return caplog


@pytest.fixture(scope="function")
def fake_mcp_server():
    """
    Minimal fake MCP server for integration testing.

    Provides controllable MCP responses for testing router behavior,
    timeouts, and error scenarios without external dependencies.
    """

    class FakeMCPServer:
        def __init__(self):
            self.tools = {
                "get_time": {
                    "name": "get_time",
                    "description": "Get current time",
                    "parameters": {"type": "object", "properties": {}},
                }
            }
            self.responses = {}
            self.delays = {}
            self.errors = {}

        def set_response(self, tool_name: str, response: Dict[str, Any]):
            """Set mock response for a tool."""
            self.responses[tool_name] = response

        def set_delay(self, tool_name: str, delay_seconds: float):
            """Set artificial delay for a tool."""
            self.delays[tool_name] = delay_seconds

        def set_error(self, tool_name: str, error_type: str):
            """Set error response for a tool."""
            self.errors[tool_name] = error_type

        async def execute_tool(self, tool_name: str, parameters: Dict[str, Any]):
            """Mock tool execution with configurable responses."""
            if tool_name in self.errors:
                error_type = self.errors[tool_name]
                if error_type == "timeout":
                    raise TimeoutError("MCP request timed out")
                elif error_type == "unavailable":
                    raise ConnectionError("MCP server unavailable")
                elif error_type == "bad_request":
                    raise ValueError("Invalid tool parameters")

            if tool_name in self.delays:
                import asyncio

                await asyncio.sleep(self.delays[tool_name])

            return self.responses.get(tool_name, {"result": f"{tool_name} executed"})

    return FakeMCPServer()


@pytest.fixture(scope="session")
def test_settings():
    """
    Test-specific configuration settings.

    Provides access to test environment settings for validation
    and ensures consistent configuration across test suite.
    """
    return get_settings()


# Performance testing utilities
@pytest.fixture(scope="function")
def performance_monitor():
    """
    Simple performance monitoring for smoke tests.

    Provides timing utilities and threshold validation for
    testing cache performance and latency targets.
    """
    import time

    class PerformanceMonitor:
        def __init__(self):
            self.timings = {}

        def start_timer(self, name: str):
            """Start timing an operation."""
            self.timings[name] = {"start": time.time()}

        def end_timer(self, name: str):
            """End timing and calculate duration."""
            if name in self.timings and "start" in self.timings[name]:
                self.timings[name]["duration"] = (
                    time.time() - self.timings[name]["start"]
                )
                return self.timings[name]["duration"]
            return 0.0

        def get_duration(self, name: str) -> float:
            """Get recorded duration for an operation."""
            return self.timings.get(name, {}).get("duration", 0.0)

        def assert_faster_than(
            self, operation1: str, operation2: str, factor: float = 2.0
        ):
            """Assert operation1 is faster than operation2 by specified factor."""
            dur1 = self.get_duration(operation1)
            dur2 = self.get_duration(operation2)
            assert dur1 > 0 and dur2 > 0, "Both operations must be timed"
            assert (
                dur2 < dur1 / factor
            ), f"{operation2} ({dur2:.3f}s) should be {factor}x faster than {operation1} ({dur1:.3f}s)"

    return PerformanceMonitor()
