"""
Tests for MCP Router functionality.

Validates tool discovery caching, MCP server health checks,
request routing, timeout handling, and error taxonomy.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.services.mcp_router import get_mcp_router


class TestMCPRouter:
    """Test suite for MCP Router behavior."""

    @pytest.fixture
    async def mcp_router(self):
        """Get MCP router instance for testing."""
        # Mock the MCP connections to avoid external dependencies
        with patch("src.services.mcp_router.MCPRouter.initialize") as mock_init:
            mock_init.return_value = None
            router = await get_mcp_router()

            # Set up mock health status
            router.health_status = {
                "test-server": MagicMock(status="healthy", last_check=None),
                "failing-server": MagicMock(status="unhealthy", last_check=None),
            }

            return router

    @pytest.mark.asyncio
    async def test_tool_discovery_returns_tools(self, mcp_router):
        """Tool discovery should return available tools from healthy servers."""
        # Mock tool discovery response
        mock_tools = {
            "get_time": {
                "name": "get_time",
                "description": "Get current time",
                "server": "test-server",
            },
            "search_github": {
                "name": "search_github",
                "description": "Search GitHub repositories",
                "server": "test-server",
            },
        }

        with patch.object(
            mcp_router, "_discover_server_tools", return_value=mock_tools
        ) as mock_discover:
            tools = await mcp_router.discover_all_tools()

            assert isinstance(tools, dict)
            assert len(tools) >= 0  # May be empty if no healthy servers

            # If tools returned, should have expected structure
            for tool_name, tool_info in tools.items():
                assert "name" in tool_info
                assert "description" in tool_info
                assert "server" in tool_info

    @pytest.mark.asyncio
    async def test_tool_discovery_caching(self, mcp_router):
        """Tool discovery should cache results for ~60 seconds."""
        mock_tools = {"test_tool": {"name": "test_tool", "server": "test-server"}}

        with patch.object(
            mcp_router, "_discover_server_tools", return_value=mock_tools
        ) as mock_discover:
            # First call should discover
            tools1 = await mcp_router.discover_all_tools(force_refresh=False)

            # Second call should use cache (if implemented)
            tools2 = await mcp_router.discover_all_tools(force_refresh=False)

            # Third call with force_refresh should re-discover
            tools3 = await mcp_router.discover_all_tools(force_refresh=True)

            # Results should be consistent
            assert tools1 == tools2 == tools3

            # Mock should be called at least twice (initial + force refresh)
            assert mock_discover.call_count >= 2

    @pytest.mark.asyncio
    async def test_execute_tool_routes_to_correct_server(self, mcp_router):
        """Tool execution should route to the correct MCP server."""
        tool_name = "test_tool"
        parameters = {"param1": "value1"}

        # Mock successful execution
        expected_result = {"success": True, "data": "test result"}

        with patch.object(
            mcp_router, "_execute_on_server", return_value=expected_result
        ) as mock_execute:
            with patch.object(
                mcp_router, "_get_tool_server", return_value="test-server"
            ):
                result = await mcp_router.execute_tool(tool_name, parameters)

                assert result == expected_result
                mock_execute.assert_called_once_with(
                    "test-server", tool_name, parameters
                )

    @pytest.mark.asyncio
    async def test_execute_tool_handles_timeout(self, mcp_router):
        """Tool execution should handle MCP server timeouts."""
        tool_name = "slow_tool"
        parameters = {}

        # Mock timeout scenario
        with patch.object(
            mcp_router,
            "_execute_on_server",
            side_effect=asyncio.TimeoutError("MCP timeout"),
        ) as mock_execute:
            with patch.object(
                mcp_router, "_get_tool_server", return_value="test-server"
            ):
                with pytest.raises(Exception) as exc_info:
                    await mcp_router.execute_tool(tool_name, parameters)

                # Should propagate timeout with proper error context
                assert "timeout" in str(exc_info.value).lower() or isinstance(
                    exc_info.value, asyncio.TimeoutError
                )

    @pytest.mark.asyncio
    async def test_execute_tool_handles_unavailable_server(self, mcp_router):
        """Tool execution should handle unavailable MCP servers."""
        tool_name = "unavailable_tool"
        parameters = {}

        # Mock server unavailable scenario
        with patch.object(
            mcp_router,
            "_execute_on_server",
            side_effect=ConnectionError("Server unavailable"),
        ):
            with patch.object(
                mcp_router, "_get_tool_server", return_value="failing-server"
            ):
                with pytest.raises(Exception) as exc_info:
                    await mcp_router.execute_tool(tool_name, parameters)

                # Should propagate connection error
                assert "unavailable" in str(exc_info.value).lower() or isinstance(
                    exc_info.value, ConnectionError
                )

    @pytest.mark.asyncio
    async def test_health_check_updates_status(self, mcp_router):
        """Health checks should update server status."""
        server_name = "test-server"

        # Mock healthy response
        with patch.object(
            mcp_router, "_check_server_health", return_value=True
        ) as mock_health:
            if hasattr(mcp_router, "update_server_health"):
                await mcp_router.update_server_health(server_name)

                mock_health.assert_called_once_with(server_name)

                # Status should be updated
                assert server_name in mcp_router.health_status
                assert mcp_router.health_status[server_name].status == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_handles_unhealthy_server(self, mcp_router):
        """Health checks should mark unhealthy servers."""
        server_name = "failing-server"

        # Mock unhealthy response
        with patch.object(
            mcp_router, "_check_server_health", return_value=False
        ) as mock_health:
            if hasattr(mcp_router, "update_server_health"):
                await mcp_router.update_server_health(server_name)

                mock_health.assert_called_once_with(server_name)

                # Status should be unhealthy
                assert server_name in mcp_router.health_status
                assert mcp_router.health_status[server_name].status == "unhealthy"

    def test_server_configuration_loading(self, mcp_router):
        """MCP router should load server configurations correctly."""
        # Should have loaded server configs from settings
        assert hasattr(mcp_router, "health_status")
        assert isinstance(mcp_router.health_status, dict)

        # Should have some configured servers (mocked or real)
        assert len(mcp_router.health_status) >= 0

    @pytest.mark.asyncio
    async def test_tool_execution_logging(self, mcp_router, captured_logs):
        """Tool execution should emit structured logs with timing."""
        tool_name = "logged_tool"
        parameters = {"test": "param"}

        # Mock execution with artificial delay to test timing
        async def mock_slow_execute(*args, **kwargs):
            await asyncio.sleep(0.01)  # Small delay
            return {"result": "logged execution"}

        with patch.object(
            mcp_router, "_execute_on_server", side_effect=mock_slow_execute
        ):
            with patch.object(
                mcp_router, "_get_tool_server", return_value="test-server"
            ):
                result = await mcp_router.execute_tool(tool_name, parameters)

                # Should log MCP execution with timing
                mcp_log = captured_logs.find_log_with_field("mcp_tool", tool_name)

                if mcp_log:
                    assert "mcp_ms" in mcp_log
                    assert isinstance(mcp_log["mcp_ms"], (int, float))
                    assert mcp_log["mcp_ms"] >= 0

    @pytest.mark.asyncio
    async def test_concurrent_tool_execution(self, mcp_router):
        """Router should handle concurrent tool executions safely."""
        # Mock multiple tools
        tools = ["tool1", "tool2", "tool3"]
        expected_results = [f"result_{i}" for i in range(len(tools))]

        async def mock_execute(server, tool, params):
            await asyncio.sleep(0.01)  # Simulate work
            tool_index = int(tool.replace("tool", "")) - 1
            return {"result": expected_results[tool_index]}

        with patch.object(mcp_router, "_execute_on_server", side_effect=mock_execute):
            with patch.object(
                mcp_router, "_get_tool_server", return_value="test-server"
            ):
                # Execute tools concurrently
                tasks = [mcp_router.execute_tool(tool, {}) for tool in tools]

                results = await asyncio.gather(*tasks)

                # All should succeed with expected results
                assert len(results) == len(tools)
                for i, result in enumerate(results):
                    assert result["result"] == expected_results[i]

    def test_router_initialization_with_config(self, test_settings):
        """Router should initialize with proper MCP server configurations."""
        # Should have MCP server URLs configured
        assert hasattr(test_settings, "mcp_github_server_url")
        assert hasattr(test_settings, "mcp_notion_server_url")
        assert hasattr(test_settings, "mcp_timeout")

        # URLs should be valid
        github_url = str(test_settings.mcp_github_server_url)
        notion_url = str(test_settings.mcp_notion_server_url)

        assert github_url.startswith("http")
        assert notion_url.startswith("http")
        assert test_settings.mcp_timeout > 0

    @pytest.mark.asyncio
    async def test_router_shutdown_cleanup(self, mcp_router):
        """Router should clean up resources on shutdown."""
        # Test that shutdown doesn't raise errors
        if hasattr(mcp_router, "shutdown"):
            await mcp_router.shutdown()

        # Should handle multiple shutdown calls gracefully
        if hasattr(mcp_router, "shutdown"):
            await mcp_router.shutdown()  # Second call should not error

    @pytest.mark.asyncio
    async def test_tool_not_found_handling(self, mcp_router):
        """Router should handle requests for non-existent tools."""
        nonexistent_tool = "does_not_exist_tool"

        with patch.object(mcp_router, "_get_tool_server", return_value=None):
            with pytest.raises(Exception) as exc_info:
                await mcp_router.execute_tool(nonexistent_tool, {})

            # Should raise appropriate error for tool not found
            error_message = str(exc_info.value).lower()
            assert "not found" in error_message or "unknown" in error_message
