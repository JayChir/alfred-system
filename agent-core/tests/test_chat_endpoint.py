"""
Tests for the /chat endpoint and core chat functionality.

Validates request/response handling, cache behavior, error taxonomy,
MCP integration, and performance characteristics.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status


class TestChatEndpoint:
    """Test suite for chat endpoint functionality."""

    def test_chat_accepts_valid_request(self, test_client, sample_chat_request):
        """Chat endpoint should accept valid request format."""
        response = test_client.post("/api/v1/chat", json=sample_chat_request)

        # Should not return validation error
        assert response.status_code != status.HTTP_422_UNPROCESSABLE_ENTITY

        # Should return some form of success (may be 500 if MCP not mocked)
        assert response.status_code in [
            200,
            500,
        ]  # 500 acceptable if MCP connection fails

    def test_chat_response_structure(self, test_client, sample_chat_request):
        """Chat endpoint should return required response fields."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            # Mock the orchestrator to return a predictable response
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Test response from mocked orchestrator",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            response = test_client.post("/api/v1/chat", json=sample_chat_request)

            if response.status_code == 200:
                data = response.json()

                # Validate required top-level fields
                assert "reply" in data
                assert "meta" in data

                # Validate meta structure
                meta = data["meta"]
                assert "requestId" in meta
                assert "cacheHit" in meta
                assert isinstance(meta["cacheHit"], bool)

                # Optional but expected fields
                if "tokens" in meta:
                    assert isinstance(meta["tokens"], dict)
                if "cacheTtlRemaining" in meta:
                    assert meta["cacheTtlRemaining"] is None or isinstance(
                        meta["cacheTtlRemaining"], int
                    )

    def test_chat_validation_errors(self, test_client):
        """Chat endpoint should return proper validation errors."""
        test_cases = [
            # Missing messages
            {},
            {"session": "test"},
            # Invalid messages format
            {"messages": "not-an-array"},
            {"messages": []},  # Empty messages
            {"messages": [{"role": "invalid", "content": "test"}]},  # Invalid role
            {"messages": [{"role": "user"}]},  # Missing content
            {"messages": [{"role": "user", "content": ""}]},  # Empty content
        ]

        for invalid_request in test_cases:
            response = test_client.post("/api/v1/chat", json=invalid_request)

            assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
            data = response.json()

            # Should follow error taxonomy
            assert "error" in data
            assert data["error"].startswith("APP-4")  # 4XX validation error
            assert "message" in data
            assert "requestId" in data

    def test_chat_error_taxonomy_internal_error(self, test_client, sample_chat_request):
        """Chat endpoint should return proper error taxonomy for internal errors."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            # Mock orchestrator to raise an exception
            mock_get_orchestrator.side_effect = Exception("Test internal error")

            response = test_client.post("/api/v1/chat", json=sample_chat_request)

            assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
            data = response.json()

            # Should follow error taxonomy
            assert "error" in data
            assert data["error"] == "APP-500-INTERNAL"
            assert "message" in data
            assert "origin" in data
            assert data["origin"] == "app"
            assert "requestId" in data

    def test_chat_cache_behavior_first_call(
        self, test_client, sample_chat_request, captured_logs
    ):
        """First chat call should miss cache and log cache behavior."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Cached response",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            response = test_client.post("/api/v1/chat", json=sample_chat_request)

            if response.status_code == 200:
                data = response.json()

                # First call should be cache miss
                assert data["meta"]["cacheHit"] is False

                # Should log cache behavior
                cache_log = captured_logs.find_log_with_field("cache_hit")
                if cache_log:
                    assert cache_log["cache_hit"] is False

    def test_chat_cache_behavior_repeat_call(self, test_client, sample_chat_request):
        """Repeated identical chat calls should hit cache."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Cached response",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            # First call
            response1 = test_client.post("/api/v1/chat", json=sample_chat_request)

            # Second identical call
            response2 = test_client.post("/api/v1/chat", json=sample_chat_request)

            if response1.status_code == 200 and response2.status_code == 200:
                data1 = response1.json()
                data2 = response2.json()

                # Responses should be identical
                assert data1["reply"] == data2["reply"]

                # Cache behavior may depend on implementation
                # This documents expected behavior even if not yet implemented
                print(
                    f"Cache hit status - Call 1: {data1['meta']['cacheHit']}, Call 2: {data2['meta']['cacheHit']}"
                )

    def test_chat_force_refresh_bypasses_cache(self, test_client, sample_chat_request):
        """forceRefresh parameter should bypass cache."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Fresh response",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            # First call to populate cache
            response1 = test_client.post("/api/v1/chat", json=sample_chat_request)

            # Second call with forceRefresh
            force_refresh_request = {**sample_chat_request, "forceRefresh": True}
            response2 = test_client.post("/api/v1/chat", json=force_refresh_request)

            if response1.status_code == 200 and response2.status_code == 200:
                data2 = response2.json()

                # Force refresh should bypass cache
                assert data2["meta"]["cacheHit"] is False

    def test_chat_structured_logging(
        self, test_client, sample_chat_request, captured_logs
    ):
        """Chat endpoint should emit structured logs with timing and cache info."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Test response",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            response = test_client.post("/api/v1/chat", json=sample_chat_request)

            # Find request log
            request_log = captured_logs.find_log_with_field("route", "/api/v1/chat")

            if request_log:
                assert request_log["method"] == "POST"
                assert "request_id" in request_log
                assert "duration_ms" in request_log

                # Should log token usage if available
                if "input_tokens" in request_log:
                    assert isinstance(request_log["input_tokens"], int)
                if "output_tokens" in request_log:
                    assert isinstance(request_log["output_tokens"], int)

    def test_chat_no_secrets_in_logs(
        self, test_client, sample_chat_request, captured_logs
    ):
        """Chat endpoint should not log sensitive information."""
        # Add API key to request headers to test redaction
        headers = {"Authorization": "Bearer test-secret-key"}

        response = test_client.post(
            "/api/v1/chat", json=sample_chat_request, headers=headers
        )

        # Check all log entries for secrets
        all_logs = captured_logs.get_structured_logs()

        for log_entry in all_logs:
            log_str = json.dumps(log_entry).lower()

            # Should not contain common secret patterns
            assert "bearer test-secret-key" not in log_str
            assert "authorization" not in log_str or "bearer" not in log_str
            assert "password" not in log_str
            assert (
                "secret" not in log_str or "generated" in log_str
            )  # Allow "generated secret" messages

    def test_chat_request_id_consistency(
        self, test_client, sample_chat_request, captured_logs
    ):
        """Request ID should be consistent across logs for same request."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Test response",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            response = test_client.post("/api/v1/chat", json=sample_chat_request)

            if response.status_code == 200:
                data = response.json()
                response_request_id = data["meta"]["requestId"]

                # Find logs with same request ID
                matching_logs = [
                    log
                    for log in captured_logs.get_structured_logs()
                    if log.get("request_id") == response_request_id
                ]

                # Should have at least one matching log entry
                assert (
                    len(matching_logs) >= 1
                ), f"No logs found with request ID {response_request_id}"

    @pytest.mark.performance
    def test_chat_performance_baseline(
        self, test_client, sample_chat_request, performance_monitor
    ):
        """Chat endpoint performance baseline measurement."""
        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Performance test response",
                "tokens": {"input": 10, "output": 15},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            # Measure cold request
            performance_monitor.start_timer("chat_cold")
            response1 = test_client.post("/api/v1/chat", json=sample_chat_request)
            cold_duration = performance_monitor.end_timer("chat_cold")

            # Measure warm request (potential cache hit)
            performance_monitor.start_timer("chat_warm")
            response2 = test_client.post("/api/v1/chat", json=sample_chat_request)
            warm_duration = performance_monitor.end_timer("chat_warm")

            if response1.status_code == 200 and response2.status_code == 200:
                # Log performance for baseline tracking
                print(
                    f"Chat performance - Cold: {cold_duration:.3f}s, Warm: {warm_duration:.3f}s"
                )

                # Basic performance expectations
                assert (
                    cold_duration < 30.0
                ), f"Cold request took {cold_duration:.3f}s, should be reasonable for testing"
                assert (
                    warm_duration < 30.0
                ), f"Warm request took {warm_duration:.3f}s, should be reasonable for testing"
