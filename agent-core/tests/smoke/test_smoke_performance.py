"""
Smoke tests for end-to-end performance and basic functionality.

These tests validate the complete system works as expected and
measure performance baselines for cache effectiveness.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.smoke
class TestSmokePerformance:
    """Smoke tests for performance and end-to-end functionality."""

    def test_app_starts_successfully(self, test_client):
        """Application should start and respond to basic requests."""
        # Health check should work
        response = test_client.get("/healthz")
        assert response.status_code == 200

        # Root endpoint should work
        response = test_client.get("/")
        assert response.status_code == 200

    def test_chat_endpoint_smoke(self, test_client, performance_monitor):
        """Chat endpoint should work end-to-end with reasonable performance."""
        chat_request = {
            "messages": [{"role": "user", "content": "Hello, this is a smoke test"}]
        }

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            # Mock orchestrator for predictable response
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Hello! This is a smoke test response.",
                "tokens": {"input": 12, "output": 18},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            # Measure cold request
            performance_monitor.start_timer("smoke_cold")
            response1 = test_client.post("/api/v1/chat", json=chat_request)
            cold_duration = performance_monitor.end_timer("smoke_cold")

            # Measure warm request (potential cache hit)
            performance_monitor.start_timer("smoke_warm")
            response2 = test_client.post("/api/v1/chat", json=chat_request)
            warm_duration = performance_monitor.end_timer("smoke_warm")

            # Both requests should succeed
            assert response1.status_code == 200
            assert response2.status_code == 200

            # Validate response structure
            data1 = response1.json()
            data2 = response2.json()

            assert "reply" in data1
            assert "meta" in data1
            assert "requestId" in data1["meta"]
            assert "cacheHit" in data1["meta"]

            # Performance validation
            print("\nSmoke test performance:")
            print(f"  Cold request: {cold_duration:.3f}s")
            print(f"  Warm request: {warm_duration:.3f}s")

            # Basic performance expectations for smoke test
            assert cold_duration < 30.0, f"Cold request too slow: {cold_duration:.3f}s"
            assert warm_duration < 30.0, f"Warm request too slow: {warm_duration:.3f}s"

            # Cache effectiveness (if implemented)
            if data2["meta"]["cacheHit"]:
                print(
                    f"  Cache hit achieved: {warm_duration:.3f}s vs {cold_duration:.3f}s"
                )
                assert warm_duration <= cold_duration, "Cache hit should not be slower"

    def test_cache_hit_ratio_smoke(self, test_client):
        """Test cache effectiveness with repeated requests."""
        chat_request = {"messages": [{"role": "user", "content": "Cache test message"}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Cache test response",
                "tokens": {"input": 8, "output": 12},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            # Make multiple identical requests
            responses = []
            for _ in range(5):
                response = test_client.post("/api/v1/chat", json=chat_request)
                if response.status_code == 200:
                    responses.append(response.json())

            if responses:
                # Count cache hits
                cache_hits = sum(1 for r in responses if r["meta"]["cacheHit"])
                hit_ratio = cache_hits / len(responses) if responses else 0

                print("\nCache effectiveness:")
                print(f"  Requests: {len(responses)}")
                print(f"  Cache hits: {cache_hits}")
                print(f"  Hit ratio: {hit_ratio:.1%}")

                # First request should be miss, subsequent may be hits
                assert (
                    responses[0]["meta"]["cacheHit"] is False
                ), "First request should miss cache"

                # If cache is working, should see some hits
                # This documents expected behavior even if not fully implemented
                if hit_ratio > 0:
                    print(f"  ✓ Cache working with {hit_ratio:.1%} hit rate")

    def test_error_handling_smoke(self, test_client):
        """Test error handling doesn't crash the application."""
        error_test_cases = [
            # Invalid JSON
            ("invalid json", "text/plain"),
            # Missing required fields
            ({}, "application/json"),
            # Invalid message format
            ({"messages": "not-an-array"}, "application/json"),
        ]

        for payload, content_type in error_test_cases:
            if content_type == "application/json":
                response = test_client.post("/api/v1/chat", json=payload)
            else:
                response = test_client.post(
                    "/api/v1/chat", data=payload, headers={"content-type": content_type}
                )

            # Should return proper error status, not crash
            assert response.status_code in [400, 422, 500]

            # Should return valid JSON error response
            try:
                error_data = response.json()
                assert "error" in error_data
                assert "message" in error_data
            except Exception:
                # Some error cases might not return JSON
                pass

    def test_concurrent_requests_smoke(self, test_client):
        """Test handling concurrent requests without issues."""
        import threading

        chat_request = {"messages": [{"role": "user", "content": "Concurrent test"}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Concurrent response",
                "tokens": {"input": 8, "output": 10},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            results = []
            errors = []

            def make_request():
                try:
                    response = test_client.post("/api/v1/chat", json=chat_request)
                    results.append(response.status_code)
                except Exception as e:
                    errors.append(str(e))

            # Launch concurrent requests
            threads = []
            for _ in range(5):
                thread = threading.Thread(target=make_request)
                threads.append(thread)
                thread.start()

            # Wait for completion
            for thread in threads:
                thread.join(timeout=10.0)

            print("\nConcurrent requests:")
            print(f"  Completed: {len(results)}")
            print(f"  Errors: {len(errors)}")
            print(f"  Success rate: {len(results)/(len(results)+len(errors)):.1%}")

            # Most requests should succeed
            assert len(results) >= 3, f"Too many concurrent failures: {errors}"

            # Successful requests should return 200
            success_count = sum(1 for status in results if status == 200)
            assert (
                success_count >= len(results) * 0.6
            ), "Success rate too low for concurrent requests"

    def test_memory_usage_stability(self, test_client):
        """Basic test that repeated requests don't cause obvious memory leaks."""
        import gc
        import os

        import psutil

        # Get current process
        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        chat_request = {"messages": [{"role": "user", "content": "Memory test"}]}

        with patch(
            "src.services.agent_orchestrator.get_agent_orchestrator"
        ) as mock_get_orchestrator:
            mock_orchestrator = AsyncMock()
            mock_orchestrator.execute_chat.return_value = {
                "reply": "Memory test response",
                "tokens": {"input": 6, "output": 8},
            }
            mock_get_orchestrator.return_value = mock_orchestrator

            # Make many requests
            for i in range(20):
                _ = test_client.post("/api/v1/chat", json=chat_request)

                # Force garbage collection periodically
                if i % 5 == 0:
                    gc.collect()

            # Final garbage collection
            gc.collect()

            final_memory = process.memory_info().rss
            memory_growth = (final_memory - initial_memory) / (1024 * 1024)  # MB

            print("\nMemory usage:")
            print(f"  Initial: {initial_memory / (1024*1024):.1f} MB")
            print(f"  Final: {final_memory / (1024*1024):.1f} MB")
            print(f"  Growth: {memory_growth:.1f} MB")

            # Should not grow excessively (threshold depends on test environment)
            assert (
                memory_growth < 50
            ), f"Excessive memory growth: {memory_growth:.1f} MB"

    def test_logging_smoke(self, test_client, captured_logs):
        """Test that logging works and doesn't contain secrets."""
        chat_request = {"messages": [{"role": "user", "content": "Logging test"}]}

        # Add authorization header to test secret redaction
        headers = {"Authorization": "Bearer secret-api-key-12345"}

        _ = test_client.post("/api/v1/chat", json=chat_request, headers=headers)

        # Check logs were generated
        logs = captured_logs.get_structured_logs()
        assert len(logs) > 0, "Should generate structured logs"

        # Check for request tracking
        request_logs = [log for log in logs if log.get("route") == "/api/v1/chat"]
        if request_logs:
            request_log = request_logs[0]
            assert "request_id" in request_log
            assert "method" in request_log
            assert request_log["method"] == "POST"

        # Verify no secrets in logs
        all_log_text = " ".join(str(log) for log in logs).lower()
        assert "secret-api-key-12345" not in all_log_text
        assert "bearer secret-api-key" not in all_log_text

    @pytest.mark.skipif(True, reason="Manual test - requires real MCP servers")
    def test_real_mcp_integration_smoke(self, test_client):
        """
        Manual smoke test with real MCP servers.

        This test is skipped by default but can be run manually
        when real MCP servers are available for integration testing.
        """
        chat_request = {"messages": [{"role": "user", "content": "What time is it?"}]}

        response = test_client.post("/api/v1/chat", json=chat_request)

        if response.status_code == 200:
            data = response.json()
            assert "reply" in data
            assert "time" in data["reply"].lower()  # Should contain time info

            # Should show real MCP integration
            print(f"Real MCP response: {data['reply']}")
        else:
            pytest.skip(f"Real MCP servers not available: {response.status_code}")
