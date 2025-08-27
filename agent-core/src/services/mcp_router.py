"""
MCP Router for tool discovery and connection management.

This module connects to remote MCP servers using Pydantic AI's MCP client,
discovers their tools, caches results, and provides health monitoring.
"""

import asyncio
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
import logfire
from pydantic_ai.mcp import MCPServerSSE, MCPServerStreamableHTTP

from src.config import Settings, get_settings
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Configure logfire for observability (optional)
# This gives us automatic tracing of MCP operations
try:
    logfire.configure(service_name="alfred-agent-core")
except Exception:
    # Logfire is optional, continue without it
    pass


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    url: str
    transport: str = "streamable_http"  # or "sse"
    tool_prefix: Optional[str] = None  # Prefix to avoid tool name collisions
    health_check_interval: int = 30  # seconds
    enabled: bool = True


@dataclass
class MCPServerStatus:
    """Health status for an MCP server."""

    server_name: str
    status: str  # "healthy", "unhealthy", "connecting"
    last_ping_time: Optional[datetime] = None
    last_success_time: Optional[datetime] = None
    ping_latency_ms: Optional[float] = None
    error_message: Optional[str] = None
    consecutive_failures: int = 0


@dataclass
class ToolCacheEntry:
    """Cached tool discovery result."""

    tools: List[Any]  # List of tool definitions
    cached_at: datetime
    cache_hit_count: int = 0


@dataclass
class ToolDef:
    """Normalized tool definition across all servers."""

    server: str  # Which server this tool belongs to
    name: str  # Tool name (possibly prefixed)
    original_name: str  # Original unprefixed name
    description: Optional[str]
    input_schema: Optional[Dict[str, Any]]
    output_schema: Optional[Dict[str, Any]]


class MCPRouter:
    """
    Router for managing connections to multiple MCP servers.

    This class:
    - Maintains connections to configured MCP servers using Pydantic AI
    - Caches tool discovery for performance (5-15 min TTL)
    - Provides health monitoring via MCP ping
    - Returns unified toolsets for Pydantic AI agents
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        cache_ttl_minutes: int = 10,
        http_timeout_seconds: float = 100.0,
    ):
        """
        Initialize the MCP Router.

        Args:
            settings: Application settings with MCP server URLs
            cache_ttl_minutes: Tool cache TTL in minutes (default 10)
            http_timeout_seconds: HTTP timeout for MCP requests
        """
        self.settings = settings or get_settings()
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.http_timeout = http_timeout_seconds

        # Server instances: server_name -> MCPServer instance
        self.servers: Dict[str, Any] = {}

        # Tool cache: server_name -> ToolCacheEntry
        self.tool_cache: Dict[str, ToolCacheEntry] = {}

        # Health status: server_name -> MCPServerStatus
        self.health_status: Dict[str, MCPServerStatus] = {}

        # Background tasks for health monitoring
        self.health_check_tasks: Dict[str, asyncio.Task] = {}

        # Context manager for server lifecycles
        self.exit_stack: Optional[AsyncExitStack] = None

        # Initialize server configurations from environment
        self.server_configs = self._load_server_configs()

    def _create_http_client(self) -> httpx.AsyncClient:
        """
        Create HTTP client with enterprise-friendly settings.

        Each MCP server gets its own client instance to avoid lifecycle conflicts
        while maintaining our custom timeout and proxy/header support.

        Returns:
            Configured httpx.AsyncClient with custom settings
        """
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                self.http_timeout
            ),  # 100 seconds for long MCP operations
            follow_redirects=True,  # Handle redirects automatically
            # Add enterprise-friendly settings:
            # headers={"User-Agent": f"Alfred-Agent-Core/{self.settings.app_version}"},
            # proxies=self.settings.http_proxy if hasattr(self.settings, 'http_proxy') else None,
            # verify=True,  # TLS certificate verification
        )

    def _load_server_configs(self) -> List[MCPServerConfig]:
        """
        Load MCP server configurations from settings.

        Returns:
            List of MCPServerConfig objects
        """
        configs = []

        # Map of server names to their production URLs
        # Based on MCP_PROXY_IMPLEMENTATION.md - all endpoints use /mcp path
        server_mapping = {
            "time": "https://mcp-time.artemsys.ai/mcp",
            "github-personal": "https://mcp-github-personal.artemsys.ai/mcp",
            "github-work": "https://mcp-github-work.artemsys.ai/mcp",
            "notion": "https://mcp-notion.artemsys.ai/mcp",
            "fetch": "https://mcp-fetch.artemsys.ai/mcp",
            "sequential-thinking": "https://mcp-sequential.artemsys.ai/mcp",
            "filesystem": "https://mcp-filesystem.artemsys.ai/mcp",
            "playwright": "https://mcp-playwright.artemsys.ai/mcp",
            "memory": "https://mcp-memory.artemsys.ai/mcp",
            "atlassian": "https://mcp-atlassian.artemsys.ai/mcp",
        }

        # For MVP Week 1, start with a subset
        enabled_servers = ["time", "github-personal", "notion"]

        for server_name in enabled_servers:
            if server_name in server_mapping:
                configs.append(
                    MCPServerConfig(
                        name=server_name,
                        url=server_mapping[server_name],
                        transport="streamable_http",  # All use Streamable HTTP via mcp-proxy
                        tool_prefix=f"{server_name}_",  # Prefix tools to avoid collisions
                        health_check_interval=30,
                        enabled=True,
                    )
                )

        logger.info(
            "Loaded MCP server configurations",
            server_count=len(configs),
            servers=[c.name for c in configs],
        )

        return configs

    async def initialize(self) -> None:
        """
        Initialize connections to all configured MCP servers.

        This method:
        1. Creates MCPServer instances for each server
        2. Opens connections using async context managers
        3. Starts health monitoring tasks
        """
        logger.info("Initializing MCP Router", server_count=len(self.server_configs))

        # Create async exit stack for managing server lifecycles
        self.exit_stack = AsyncExitStack()

        # Create server instances
        for config in self.server_configs:
            if not config.enabled:
                continue

            try:
                # Create appropriate server instance based on transport
                if config.transport == "streamable_http":
                    # Streamable HTTP endpoint - config.url already includes /mcp path
                    # Create custom HTTP client with enterprise settings for each server
                    server = MCPServerStreamableHTTP(
                        url=config.url,  # Don't add /mcp - already in config.url
                        tool_prefix=config.tool_prefix,
                        http_client=self._create_http_client(),  # Custom client with 100s timeout
                    )
                else:
                    # SSE endpoint - replace /mcp with /sse in the URL
                    sse_url = config.url.replace("/mcp", "/sse")
                    server = MCPServerSSE(
                        url=sse_url,
                        tool_prefix=config.tool_prefix,
                        http_client=self._create_http_client(),  # Custom client with 100s timeout
                    )

                # Store server instance
                self.servers[config.name] = server

                # Enter context manager to initialize connection
                # Pydantic MCP handles protocol initialization internally
                await self.exit_stack.enter_async_context(server)

                # Initialize health status
                self.health_status[config.name] = MCPServerStatus(
                    server_name=config.name,
                    status="healthy",
                    last_success_time=datetime.now(),
                )

                logger.info(
                    "Successfully connected to MCP server",
                    server=config.name,
                    url=config.url,
                    transport=config.transport,
                )

            except Exception as e:
                logger.error(
                    "Failed to connect to MCP server",
                    server=config.name,
                    url=config.url,
                    transport=config.transport,
                    error=str(e),
                    error_type=type(e).__name__,
                    # Include more context for debugging
                    timeout_seconds=self.http_timeout,
                    exc_info=True,  # Include full stack trace
                )
                self.health_status[config.name] = MCPServerStatus(
                    server_name=config.name,
                    status="unhealthy",
                    error_message=f"{type(e).__name__}: {str(e)}",
                    consecutive_failures=1,
                )

        # Perform initial tool discovery
        await self.discover_all_tools(force_refresh=True)

        # Start health monitoring for connected servers
        for server_name in self.servers:
            self._start_health_monitoring(server_name)

    async def _discover_server_tools(
        self, server_name: str, force_refresh: bool = False
    ) -> List[ToolDef]:
        """
        Discover tools from a specific MCP server with caching.

        Args:
            server_name: Name of the server
            force_refresh: Bypass cache if True

        Returns:
            List of normalized ToolDef objects
        """
        # Check cache first
        if not force_refresh and server_name in self.tool_cache:
            cache_entry = self.tool_cache[server_name]
            cache_age = datetime.now() - cache_entry.cached_at

            if cache_age < self.cache_ttl:
                # Cache hit
                cache_entry.cache_hit_count += 1
                logger.debug(
                    "Tool cache hit",
                    server=server_name,
                    cache_age_seconds=cache_age.total_seconds(),
                    hit_count=cache_entry.cache_hit_count,
                )
                return cache_entry.tools

        # Cache miss or forced refresh - discover tools
        logger.info(
            "Discovering tools from MCP server",
            server=server_name,
            force_refresh=force_refresh,
        )

        server = self.servers.get(server_name)
        if not server:
            logger.warning("No connection for server", server=server_name)
            return []

        try:
            # Use Pydantic MCP's list_tools method
            # This handles the protocol details and returns typed tools
            tools = await server.list_tools()

            # Normalize tools to our ToolDef format
            tool_defs = []
            for tool in tools:
                # Tools from Pydantic MCP have these attributes
                tool_def = ToolDef(
                    server=server_name,
                    name=tool.name,  # Already prefixed by Pydantic MCP if configured
                    original_name=(
                        tool.name.removeprefix(f"{server_name}_")
                        if tool.name.startswith(f"{server_name}_")
                        else tool.name
                    ),
                    description=getattr(tool, "description", None),
                    input_schema=getattr(tool, "parameters_json_schema", None),
                    output_schema=None,  # MCP tools don't typically have output schemas
                )
                tool_defs.append(tool_def)

            # Cache the result
            self.tool_cache[server_name] = ToolCacheEntry(
                tools=tool_defs,
                cached_at=datetime.now(),
            )

            logger.info(
                "Discovered tools",
                server=server_name,
                tool_count=len(tool_defs),
                tool_names=[t.name for t in tool_defs] if tool_defs else [],
            )

            return tool_defs

        except Exception as e:
            logger.error(
                "Failed to discover tools",
                server=server_name,
                error=str(e),
            )
            return []

    async def discover_all_tools(
        self, force_refresh: bool = False
    ) -> Dict[str, List[ToolDef]]:
        """
        Discover tools from all connected MCP servers.

        Args:
            force_refresh: Bypass cache if True

        Returns:
            Dictionary mapping server names to tool lists
        """
        logger.info(
            "Discovering tools from all servers",
            server_count=len(self.servers),
            force_refresh=force_refresh,
        )

        # Discover tools from each server concurrently
        tasks = {
            server_name: self._discover_server_tools(server_name, force_refresh)
            for server_name in self.servers
        }

        results = {}
        for server_name, task_coro in tasks.items():
            try:
                results[server_name] = await task_coro
            except Exception as e:
                logger.error(
                    "Failed to discover tools from server",
                    server=server_name,
                    error=str(e),
                )
                results[server_name] = []

        return results

    def get_unified_toolsets(self) -> List[Any]:
        """
        Get all MCP server instances as toolsets for Pydantic AI agents.

        Returns:
            List of MCP server instances (each is a toolset)
        """
        toolsets = []

        for server_name, server in self.servers.items():
            status = self.health_status.get(server_name)
            if status and status.status == "healthy":
                toolsets.append(server)
            else:
                logger.debug(
                    "Skipping unhealthy server",
                    server=server_name,
                )

        logger.info(
            "Providing unified toolsets",
            toolset_count=len(toolsets),
            servers=[name for name, s in self.servers.items() if s in toolsets],
        )

        return toolsets

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, Any],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Call a tool on a specific MCP server.

        Args:
            server_name: Name of the server
            tool_name: Name of the tool (without prefix)
            arguments: Tool arguments
            metadata: Optional metadata for the call

        Returns:
            Tool execution result
        """
        server = self.servers.get(server_name)
        if not server:
            raise ValueError(f"No server found: {server_name}")

        # Add server prefix to tool name if configured
        config = next((c for c in self.server_configs if c.name == server_name), None)
        if config and config.tool_prefix:
            prefixed_name = f"{config.tool_prefix}{tool_name}"
        else:
            prefixed_name = tool_name

        logger.info(
            "Calling tool",
            server=server_name,
            tool=prefixed_name,
            args_keys=list(arguments.keys()),
        )

        try:
            # Use Pydantic MCP's direct_call_tool method
            # This handles the protocol and returns the result
            result = await server.direct_call_tool(
                prefixed_name,
                arguments,
                metadata=metadata,
            )

            logger.info(
                "Tool call successful",
                server=server_name,
                tool=prefixed_name,
            )

            return result

        except Exception as e:
            logger.error(
                "Tool call failed",
                server=server_name,
                tool=prefixed_name,
                error=str(e),
            )
            raise

    def _start_health_monitoring(self, server_name: str) -> None:
        """
        Start background health monitoring for a server.

        Args:
            server_name: Name of the server to monitor
        """
        # Cancel existing task if any
        if server_name in self.health_check_tasks:
            self.health_check_tasks[server_name].cancel()

        # Start new monitoring task
        task = asyncio.create_task(self._health_check_loop(server_name))
        self.health_check_tasks[server_name] = task

        logger.debug(
            "Started health monitoring",
            server=server_name,
        )

    async def _health_check_loop(self, server_name: str) -> None:
        """
        Background task for periodic health checks.

        Args:
            server_name: Name of the server to monitor
        """
        config = next((c for c in self.server_configs if c.name == server_name), None)

        if not config:
            logger.error("No config for health check", server=server_name)
            return

        while True:
            try:
                # Wait for next check interval with some jitter
                jitter = (
                    asyncio.current_task().get_name().encode()[0] % 5
                )  # 0-4 seconds
                await asyncio.sleep(config.health_check_interval + jitter)

                # Perform health check
                await self._check_server_health(server_name)

            except asyncio.CancelledError:
                logger.debug("Health check cancelled", server=server_name)
                break
            except Exception as e:
                logger.error(
                    "Health check error",
                    server=server_name,
                    error=str(e),
                )

    async def _check_server_health(self, server_name: str) -> None:
        """
        Check health of a single MCP server using ping.

        Args:
            server_name: Name of the server to check
        """
        status = self.health_status.get(server_name)
        if not status:
            return

        server = self.servers.get(server_name)
        if not server:
            status.status = "unhealthy"
            status.error_message = "No connection"
            return

        try:
            # Measure ping latency
            start_time = time.perf_counter()

            # Pydantic MCP doesn't expose ping directly, but we can use list_tools
            # as a lightweight health check (it's cached anyway)
            # Alternatively, we could make a direct ping request through the client
            await server.list_tools()

            # Calculate latency
            latency_ms = (time.perf_counter() - start_time) * 1000

            # Update status
            status.status = "healthy"
            status.last_ping_time = datetime.now()
            status.last_success_time = datetime.now()
            status.ping_latency_ms = latency_ms
            status.consecutive_failures = 0
            status.error_message = None

            logger.debug(
                "Health check successful",
                server=server_name,
                latency_ms=round(latency_ms, 2),
            )

        except Exception as e:
            # Update failure status
            status.status = "unhealthy"
            status.last_ping_time = datetime.now()
            status.consecutive_failures += 1
            status.error_message = str(e)

            logger.warning(
                "Health check failed",
                server=server_name,
                consecutive_failures=status.consecutive_failures,
                error=str(e),
            )

    async def get_health_summary(self) -> Dict[str, Any]:
        """
        Get aggregated health status for all MCP servers.

        Returns:
            Dictionary with overall status and per-server details
        """
        healthy_count = sum(
            1 for s in self.health_status.values() if s.status == "healthy"
        )
        total_count = len(self.health_status)

        # Calculate average latency for healthy servers
        latencies = [
            s.ping_latency_ms
            for s in self.health_status.values()
            if s.status == "healthy" and s.ping_latency_ms is not None
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else None

        # Overall status
        if healthy_count == total_count:
            overall_status = "healthy"
        elif healthy_count > 0:
            overall_status = "degraded"
        else:
            overall_status = "unhealthy"

        return {
            "status": overall_status,
            "healthy_servers": healthy_count,
            "total_servers": total_count,
            "average_latency_ms": round(avg_latency, 2) if avg_latency else None,
            "servers": {
                name: {
                    "status": status.status,
                    "last_success": (
                        status.last_success_time.isoformat()
                        if status.last_success_time
                        else None
                    ),
                    "latency_ms": status.ping_latency_ms,
                    "error": status.error_message,
                }
                for name, status in self.health_status.items()
            },
        }

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring.

        Returns:
            Dictionary with cache metrics
        """
        total_entries = len(self.tool_cache)
        total_hits = sum(entry.cache_hit_count for entry in self.tool_cache.values())

        # Calculate cache ages
        now = datetime.now()
        cache_ages = {
            server: (now - entry.cached_at).total_seconds()
            for server, entry in self.tool_cache.items()
        }

        return {
            "total_entries": total_entries,
            "total_hits": total_hits,
            "ttl_seconds": self.cache_ttl.total_seconds(),
            "entries": {
                server: {
                    "age_seconds": age,
                    "hit_count": self.tool_cache[server].cache_hit_count,
                    "expired": age > self.cache_ttl.total_seconds(),
                }
                for server, age in cache_ages.items()
            },
        }

    async def shutdown(self) -> None:
        """
        Clean shutdown of all connections and background tasks.
        """
        logger.info("Shutting down MCP Router")

        # Cancel all health check tasks
        for task in self.health_check_tasks.values():
            task.cancel()

        # Wait for tasks to complete
        await asyncio.gather(*self.health_check_tasks.values(), return_exceptions=True)

        # Close all server connections via exit stack
        if self.exit_stack:
            await self.exit_stack.aclose()

        # Note: No shared HTTP client to close - each server manages its own

        # Clear registries
        self.servers.clear()
        self.tool_cache.clear()
        self.health_status.clear()
        self.health_check_tasks.clear()

        logger.info("MCP Router shutdown complete")


# Module-level router instance (singleton pattern)
_router_instance: Optional[MCPRouter] = None


async def get_mcp_router() -> MCPRouter:
    """
    Get the singleton MCP Router instance.

    Returns:
        The initialized MCPRouter instance
    """
    global _router_instance

    if _router_instance is None:
        _router_instance = MCPRouter()
        await _router_instance.initialize()

    return _router_instance
