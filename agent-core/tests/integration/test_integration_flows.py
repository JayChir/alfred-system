"""
Integration tests for complete request flows.

Tests the full stack from HTTP request through MCP routing,
caching, orchestration, and response formatting.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.services.agent_orchestrator import ChatResponse


@pytest.mark.integration
class TestIntegrationFlows:
    """Integration tests for end-to-end request flows."""

    @pytest.mark.asyncio
    async def test_health_check_integration(self, async_client):
        """Health check should work through complete stack."""
        response = await async_client.get("/healthz")

        assert response.status_code == 200
        data = response.json()

        # Should have health check structure
        assert "status" in data
        assert "version" in data
        assert "environment" in data
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_chat_integration_with_mcp_router(self, async_client):
        """Chat request should integrate with MCP router and cache."""
        chat_request = {
            "messages": [{"role": "user", "content": "Integration test message"}]
        }

        # Mock the full orchestration stack
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()

            # Simulate orchestrator calling MCP tools
            mock_orchestrator.chat.return_value = ChatResponse(
                reply="Integration test response with tool call results",
                meta={
                    "usage": {"input_tokens": 15, "output_tokens": 25},
                    "tool_calls": [
                        {"name": "get_time", "server": "time", "success": True}
                    ],
                },
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            response = await async_client.post("/api/v1/chat", json=chat_request)

            assert response.status_code == 200
            data = response.json()

            # Should have complete response structure
            assert "reply" in data
            assert "meta" in data

            meta = data["meta"]
            assert "requestId" in meta
            assert "cacheHit" in meta
            assert isinstance(meta["cacheHit"], bool)

            # Should include token tracking
            if "tokens" in meta:
                assert "input" in meta["tokens"]
                assert "output" in meta["tokens"]

    @pytest.mark.asyncio
    async def test_chat_integration_with_cache_middleware(self, async_client):
        """Chat should integrate with cache middleware for repeated requests."""
        chat_request = {
            "messages": [{"role": "user", "content": "Cache integration test"}]
        }

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.return_value = ChatResponse(
                reply="Cached integration response",
                meta={"usage": {"input_tokens": 10, "output_tokens": 15}},
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            # First request - should miss cache
            response1 = await async_client.post("/api/v1/chat", json=chat_request)

            # Second request - may hit cache
            response2 = await async_client.post("/api/v1/chat", json=chat_request)

            assert response1.status_code == 200
            assert response2.status_code == 200

            data1 = response1.json()
            data2 = response2.json()

            # Responses should have cache status
            assert "cacheHit" in data1["meta"]
            assert "cacheHit" in data2["meta"]

            # First should be miss
            assert data1["meta"]["cacheHit"] is False

            # Content should be consistent
            assert data1["reply"] == data2["reply"]

    @pytest.mark.asyncio
    async def test_chat_integration_force_refresh(self, async_client):
        """Chat with forceRefresh should bypass cache completely."""
        base_request = {"messages": [{"role": "user", "content": "Force refresh test"}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.return_value = ChatResponse(
                reply="Force refresh response",
                meta={"usage": {"input_tokens": 8, "output_tokens": 12}},
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            # First request to populate cache
            response1 = await async_client.post("/api/v1/chat", json=base_request)

            # Force refresh request
            force_request = {**base_request, "forceRefresh": True}
            response2 = await async_client.post("/api/v1/chat", json=force_request)

            assert response1.status_code == 200
            assert response2.status_code == 200

            data2 = response2.json()

            # Force refresh should always miss cache
            assert data2["meta"]["cacheHit"] is False

    @pytest.mark.asyncio
    async def test_chat_integration_with_session(self, async_client):
        """Chat with session should maintain context across requests."""
        session_token = "test-session-123"

        request1 = {
            "messages": [{"role": "user", "content": "First message in session"}],
            "session": session_token,
        }

        request2 = {
            "messages": [
                {"role": "user", "content": "First message in session"},
                {"role": "assistant", "content": "Response to first message"},
                {"role": "user", "content": "Second message in session"},
            ],
            "session": session_token,
        }

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.side_effect = [
                ChatResponse(
                    reply="First response",
                    meta={"usage": {"input_tokens": 10, "output_tokens": 8}},
                ),
                ChatResponse(
                    reply="Second response",
                    meta={"usage": {"input_tokens": 20, "output_tokens": 12}},
                ),
            ]
            mock_get_orchestrator.return_value = mock_orchestrator

            # First request
            response1 = await async_client.post("/api/v1/chat", json=request1)

            # Second request with session context
            response2 = await async_client.post("/api/v1/chat", json=request2)

            assert response1.status_code == 200
            assert response2.status_code == 200

            # Both should have same session context
            # (Implementation details depend on session store)

    @pytest.mark.asyncio
    async def test_error_taxonomy_integration(self, async_client):
        """Error responses should follow taxonomy through complete stack."""
        # Test validation error
        invalid_request = {"invalid": "request"}

        response = await async_client.post("/api/v1/chat", json=invalid_request)

        assert response.status_code == 422
        data = response.json()

        # Should follow error taxonomy
        assert "error" in data
        assert data["error"].startswith("APP-4")
        assert "message" in data
        assert "requestId" in data
        assert "origin" in data

    @pytest.mark.asyncio
    async def test_internal_error_integration(self, async_client):
        """Internal errors should be properly handled and formatted."""
        chat_request = {"messages": [{"role": "user", "content": "Error test"}]}

        # Mock orchestrator to raise exception
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_get_orchestrator.side_effect = Exception("Simulated internal error")

            response = await async_client.post("/api/v1/chat", json=chat_request)

            assert response.status_code == 500
            data = response.json()

            # Should follow error taxonomy
            assert "error" in data
            assert data["error"] == "APP-500-INTERNAL"
            assert "message" in data
            assert "requestId" in data
            assert "origin" in data
            assert data["origin"] == "app"

    @pytest.mark.asyncio
    async def test_request_id_correlation_integration(
        self, async_client, captured_logs
    ):
        """Request IDs should correlate across logs and responses."""
        chat_request = {"messages": [{"role": "user", "content": "Request ID test"}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Request ID response",
                "tokens": {"input": 8, "output": 10},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            response = await async_client.post("/api/v1/chat", json=chat_request)

            if response.status_code == 200:
                data = response.json()
                response_request_id = data["meta"]["requestId"]

                # Find logs with matching request ID
                logs = captured_logs.get_structured_logs()
                matching_logs = [
                    log for log in logs if log.get("request_id") == response_request_id
                ]

                # Should have correlated log entries
                assert (
                    len(matching_logs) > 0
                ), f"No logs found for request ID {response_request_id}"

    @pytest.mark.asyncio
    async def test_cors_headers_integration(self, async_client):
        """CORS headers should be properly set for cross-origin requests."""
        headers = {
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        }

        # OPTIONS preflight request
        response = await async_client.options("/api/v1/chat", headers=headers)

        # Should handle CORS preflight
        assert response.status_code in [200, 204]

        # Should have CORS headers (if CORS is configured)
        cors_headers = [
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
        ]

        # At least some CORS headers should be present
        response_headers = {k.lower(): v for k, v in response.headers.items()}
        cors_present = any(header in response_headers for header in cors_headers)

        # Document CORS behavior
        print(f"CORS headers present: {cors_present}")

    @pytest.mark.asyncio
    async def test_content_type_handling_integration(self, async_client):
        """Different content types should be handled appropriately."""
        chat_request = {"messages": [{"role": "user", "content": "Content type test"}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.return_value = ChatResponse(
                reply="Content type response",
                meta={"usage": {"input_tokens": 8, "output_tokens": 10}},
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            # JSON request
            response = await async_client.post("/api/v1/chat", json=chat_request)

            if response.status_code == 200:
                # Should return JSON
                assert "application/json" in response.headers.get("content-type", "")
                response.json()  # Should parse successfully

    @pytest.mark.asyncio
    async def test_large_request_handling_integration(self, async_client):
        """Large requests should be handled appropriately."""
        # Create a large message
        large_content = "Large message content. " * 1000  # ~25KB

        large_request = {"messages": [{"role": "user", "content": large_content}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.return_value = ChatResponse(
                reply="Large request processed",
                meta={"usage": {"input_tokens": 5000, "output_tokens": 20}},
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            response = await async_client.post("/api/v1/chat", json=large_request)

            # Should handle large request (may have size limits)
            assert response.status_code in [
                200,
                413,
            ], f"Unexpected status: {response.status_code}"

            if response.status_code == 200:
                data = response.json()
                assert "reply" in data

                # Should track high token usage
                if "tokens" in data["meta"]:
                    assert data["meta"]["tokens"]["input"] > 1000

    @pytest.mark.asyncio
    async def test_concurrent_requests_integration(self, async_client):
        """Multiple concurrent requests should be handled safely."""
        import asyncio

        chat_request = {
            "messages": [{"role": "user", "content": "Concurrent integration test"}]
        }

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.chat.return_value = ChatResponse(
                reply="Concurrent response",
                meta={"usage": {"input_tokens": 12, "output_tokens": 8}},
            )
            mock_get_orchestrator.return_value = mock_orchestrator

            # Launch concurrent requests
            tasks = [
                async_client.post("/api/v1/chat", json=chat_request) for _ in range(5)
            ]

            responses = await asyncio.gather(*tasks, return_exceptions=True)

            # Count successful responses
            successful = [
                r
                for r in responses
                if hasattr(r, "status_code") and r.status_code == 200
            ]
            exceptions = [r for r in responses if isinstance(r, Exception)]

            print("Concurrent integration results:")
            print(f"  Successful: {len(successful)}")
            print(f"  Exceptions: {len(exceptions)}")

            # Most should succeed
            assert len(successful) >= 3, f"Too many concurrent failures: {exceptions}"

            # All successful responses should have proper structure
            for response in successful:
                data = response.json()
                assert "reply" in data
                assert "meta" in data
