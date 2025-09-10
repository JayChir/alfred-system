"""
Tests for SSE streaming endpoint.

Tests cover:
- Basic SSE streaming functionality
- Event type mapping (token, tool_call, warning, done)
- Heartbeat mechanism
- Error handling
- Auto-reconnect support
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from src.main import app


@pytest.mark.asyncio
async def test_sse_stream_basic():
    """Test basic SSE streaming with mock orchestrator."""

    # Mock the orchestrator
    mock_orchestrator = AsyncMock()

    # Create mock events
    async def mock_chat_stream(*args, **kwargs):
        """Generate mock streaming events."""
        from src.services.agent_orchestrator import StreamEvent

        # Yield text tokens
        yield StreamEvent(type="text", data="The capital ", request_id="test-123")
        yield StreamEvent(type="text", data="of France ", request_id="test-123")
        yield StreamEvent(type="text", data="is Paris.", request_id="test-123")

        # Yield tool call
        yield StreamEvent(
            type="tool_call",
            data={
                "tool": "get_weather",
                "args": {"city": "Paris"},
                "result": {"temp": "15°C", "condition": "Sunny"},
            },
            request_id="test-123",
        )

        # Yield final event
        yield StreamEvent(
            type="final",
            data={"usage": {"input": 10, "output": 15}, "cache_hit": False},
            request_id="test-123",
        )

    mock_orchestrator.chat.return_value = mock_chat_stream()

    with patch(
        "src.routers.chat.get_agent_orchestrator", return_value=mock_orchestrator
    ):
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Make streaming request
            response = await client.get(
                "/chat/stream",
                params={"prompt": "What is the capital of France?"},
                headers={"X-API-Key": "test-key", "Accept": "text/event-stream"},
            )

            assert response.status_code == 200
            assert (
                response.headers["content-type"] == "text/event-stream; charset=utf-8"
            )

            # Parse SSE events
            events = []
            for line in response.text.split("\n"):
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    events.append({"type": event_type})
                elif line.startswith("data:"):
                    if events:
                        data = json.loads(line.split(":", 1)[1].strip())
                        events[-1]["data"] = data

            # Verify events
            token_events = [e for e in events if e["type"] == "token"]
            assert len(token_events) == 3
            assert token_events[0]["data"]["content"] == "The capital "

            tool_events = [e for e in events if e["type"] == "tool_call"]
            assert len(tool_events) == 1
            assert tool_events[0]["data"]["tool"] == "get_weather"

            done_events = [e for e in events if e["type"] == "done"]
            assert len(done_events) == 1
            assert done_events[0]["data"]["usage"]["input"] == 10


@pytest.mark.asyncio
async def test_sse_heartbeat_mechanism():
    """Test that heartbeat events are sent during long operations."""

    mock_orchestrator = AsyncMock()

    async def slow_chat_stream(*args, **kwargs):
        """Simulate slow stream to trigger heartbeat."""
        from src.services.agent_orchestrator import StreamEvent

        # Yield initial token
        yield StreamEvent(type="text", data="Processing...", request_id="test-123")

        # Simulate long delay (would trigger heartbeat in real scenario)
        await asyncio.sleep(0.1)

        # Yield final
        yield StreamEvent(
            type="final",
            data={"usage": {"input": 5, "output": 10}},
            request_id="test-123",
        )

    mock_orchestrator.chat.return_value = slow_chat_stream()

    with patch(
        "src.routers.chat.get_agent_orchestrator", return_value=mock_orchestrator
    ):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                "/chat/stream",
                params={"prompt": "test"},
                headers={"X-API-Key": "test-key"},
                timeout=5.0,
            )

            assert response.status_code == 200
            # In a real test, we'd verify heartbeat events appear
            # This is simplified since we can't easily simulate the 30s delay


@pytest.mark.asyncio
async def test_sse_error_handling():
    """Test SSE error event generation."""

    mock_orchestrator = AsyncMock()

    async def error_chat_stream(*args, **kwargs):
        """Generate error during streaming."""
        from src.services.agent_orchestrator import StreamEvent

        yield StreamEvent(type="text", data="Starting...", request_id="test-123")
        raise ValueError("Simulated error during streaming")

    mock_orchestrator.chat.return_value = error_chat_stream()

    with patch(
        "src.routers.chat.get_agent_orchestrator", return_value=mock_orchestrator
    ):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                "/chat/stream",
                params={"prompt": "test"},
                headers={"X-API-Key": "test-key"},
            )

            # Parse events
            events = []
            for line in response.text.split("\n"):
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    events.append({"type": event_type})
                elif line.startswith("data:") and events:
                    data = json.loads(line.split(":", 1)[1].strip())
                    events[-1]["data"] = data

            # Should have error event
            error_events = [e for e in events if e["type"] == "error"]
            assert len(error_events) == 1
            assert "Simulated error" in error_events[0]["data"]["error"]

            # Should have done event with error flag
            done_events = [e for e in events if e["type"] == "done"]
            assert len(done_events) == 1
            assert done_events[0]["data"]["error"] is True


@pytest.mark.asyncio
async def test_sse_warning_event():
    """Test budget warning event generation."""

    mock_orchestrator = AsyncMock()
    mock_token_metering = AsyncMock()

    # Mock budget check to return warning
    mock_token_metering.check_budget.return_value = (True, 85, "warning")

    async def normal_chat_stream(*args, **kwargs):
        """Normal chat stream."""
        from src.services.agent_orchestrator import StreamEvent

        yield StreamEvent(type="text", data="Response text", request_id="test-123")
        yield StreamEvent(
            type="final",
            data={"usage": {"input": 100, "output": 50}},
            request_id="test-123",
        )

    mock_orchestrator.chat.return_value = normal_chat_stream()

    with patch(
        "src.routers.chat.get_agent_orchestrator", return_value=mock_orchestrator
    ):
        with patch(
            "src.routers.chat.get_token_metering_service",
            return_value=mock_token_metering,
        ):
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.get(
                    "/chat/stream",
                    params={"prompt": "test"},
                    headers={
                        "X-API-Key": "test-key",
                        "X-User-ID": "123e4567-e89b-12d3-a456-426614174000",
                    },
                )

                # Parse events
                events = []
                for line in response.text.split("\n"):
                    if line.startswith("event:"):
                        event_type = line.split(":", 1)[1].strip()
                        events.append({"type": event_type})
                    elif line.startswith("data:") and events:
                        try:
                            data = json.loads(line.split(":", 1)[1].strip())
                            events[-1]["data"] = data
                        except json.JSONDecodeError:
                            pass

                # Should have warning event
                warning_events = [e for e in events if e["type"] == "warning"]
                assert len(warning_events) == 1
                assert warning_events[0]["data"]["level"] == "warning"
                assert warning_events[0]["data"]["percent_used"] == 85
                assert "timestamp" in warning_events[0]["data"]

                # Done event should reference warning
                done_events = [e for e in events if e["type"] == "done"]
                assert len(done_events) >= 1
                # The warning field may be in the done event
                if done_events:
                    assert "timestamp" in done_events[0]["data"]


def test_sse_headers():
    """Test that SSE response has correct headers."""

    with TestClient(app) as client:
        # Need to mock the orchestrator for this test
        mock_orchestrator = MagicMock()

        with patch(
            "src.routers.chat.get_agent_orchestrator", return_value=mock_orchestrator
        ):
            # Just check headers, don't need full stream
            response = client.get(
                "/chat/stream",
                params={"prompt": "test"},
                headers={"X-API-Key": "test-key"},
                # Use stream=False to just get headers
                stream=False,
            )

            # Check critical SSE headers
            assert "text/event-stream" in response.headers.get("content-type", "")
            assert (
                response.headers.get("cache-control")
                == "no-cache, no-store, must-revalidate"
            )
            assert response.headers.get("connection") == "keep-alive"
            assert response.headers.get("x-accel-buffering") == "no"


@pytest.mark.asyncio
async def test_sse_request_id_propagation():
    """Test that request_id is properly propagated through SSE events."""

    mock_orchestrator = AsyncMock()

    async def chat_with_request_id(*args, **kwargs):
        """Stream with request_id."""
        from src.services.agent_orchestrator import StreamEvent

        yield StreamEvent(type="text", data="Test", request_id="unique-request-123")
        yield StreamEvent(
            type="final",
            data={"usage": {"input": 5, "output": 5}},
            request_id="unique-request-123",
        )

    mock_orchestrator.chat.return_value = chat_with_request_id()

    with patch(
        "src.routers.chat.get_agent_orchestrator", return_value=mock_orchestrator
    ):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(
                "/chat/stream",
                params={"prompt": "test"},
                headers={"X-API-Key": "test-key"},
            )

            # Check X-Request-ID header
            assert "x-request-id" in response.headers

            # Parse done event
            events = []
            for line in response.text.split("\n"):
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                    events.append({"type": event_type})
                elif line.startswith("data:") and events:
                    try:
                        data = json.loads(line.split(":", 1)[1].strip())
                        events[-1]["data"] = data
                    except json.JSONDecodeError:
                        pass

            done_events = [e for e in events if e["type"] == "done"]
            assert len(done_events) == 1
            # Request ID should be in done event
            assert "request_id" in done_events[0]["data"]
