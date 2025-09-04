"""
Agent Orchestrator using Pydantic AI with MCP tools.

This module creates and manages a Pydantic AI agent that uses MCP servers
as toolsets, processes chat requests, and handles both streaming and
non-streaming responses.
"""

import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from src.config import Settings, get_settings
from src.services.mcp_router import MCPRouter, get_mcp_router
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ChatRequest(BaseModel):
    """Request model for chat endpoint."""

    prompt: str = Field(..., description="User's message to the agent")
    context_id: Optional[str] = Field(
        None, description="Context ID for conversation history"
    )
    stream: bool = Field(False, description="Enable streaming response")
    max_tool_calls: int = Field(20, description="Maximum number of tool calls allowed")
    timeout_seconds: float = Field(
        300.0, description="Maximum execution time in seconds"
    )
    force_refresh: bool = Field(False, description="Force refresh of cached data")


class ChatResponse(BaseModel):
    """Response model for non-streaming chat."""

    reply: str = Field(..., description="Agent's response")
    meta: Dict[str, Any] = Field(..., description="Response metadata")


class StreamEvent(BaseModel):
    """Event model for streaming responses."""

    type: str = Field(..., description="Event type: text, tool_call, error, final")
    data: Any = Field(None, description="Event data")
    timestamp: float = Field(default_factory=time.time, description="Event timestamp")
    request_id: Optional[str] = Field(None, description="Request tracking ID")


class AgentOrchestrator:
    """
    Orchestrates Pydantic AI agent with MCP tools.

    This class:
    - Creates and manages a Pydantic AI agent
    - Loads MCP servers as toolsets
    - Handles chat conversations with streaming support
    - Manages session history
    - Provides error normalization
    """

    # Default safety limits
    DEFAULT_MAX_TOOL_CALLS = 20
    DEFAULT_TIMEOUT_SECONDS = 300.0  # 5 minutes

    def __init__(
        self,
        router: Optional[MCPRouter] = None,
        settings: Optional[Settings] = None,
    ):
        """
        Initialize the Agent Orchestrator.

        Args:
            router: MCP Router instance for tool management
            settings: Application settings
        """
        self.router = router
        self.settings = settings or get_settings()
        self.agent: Optional[Agent] = None

        # In-memory context storage (MVP - will move to DB in Week 3)
        self.contexts: Dict[str, List[ModelMessage]] = {}

        # Track active requests for debugging
        self.active_requests: Dict[str, float] = {}

    async def initialize(self) -> None:
        """
        Initialize the agent with MCP toolsets.

        This method:
        1. Gets the MCP router if not provided
        2. Fetches healthy toolsets
        3. Creates the Pydantic AI agent
        """
        # Get router if not provided
        if self.router is None:
            self.router = await get_mcp_router()
            logger.info("Retrieved MCP router instance")

        # Get healthy toolsets from router (base toolsets for initialization)
        toolsets = await self._get_healthy_toolsets()

        # Create agent with Anthropic model and MCP toolsets
        # Note: Using toolsets= parameter, not tools=
        # Create explicit AnthropicModel with API key to avoid environment variable issues
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        anthropic_model = AnthropicModel(
            self.settings.anthropic_model,
            provider=AnthropicProvider(api_key=self.settings.anthropic_api_key),
        )

        self.agent = Agent(
            model=anthropic_model,
            # Don't set toolsets here - we'll pass them dynamically at run time
            # to allow for live health checks and filtering
            system_prompt=self._build_system_prompt(),
        )

        logger.info(
            "Initialized agent orchestrator",
            model=self.settings.anthropic_model,
            toolset_count=len(toolsets),
        )

    async def _get_healthy_toolsets(self, user_id: Optional[str] = None) -> List[Any]:
        """
        Get only healthy MCP servers as toolsets, including user-specific ones.

        The servers already have process_tool_call hooks for caching
        configured during initialization in the MCP router.

        Args:
            user_id: Optional user ID for user-specific toolsets (e.g., Notion)

        Returns:
            List of healthy MCP server instances with cache support
        """
        if not self.router:
            return []

        # Use the router's method which handles both base and user-specific toolsets
        return await self.router.get_toolsets_for_user(user_id)

    def _build_system_prompt(self) -> str:
        """
        Build the system prompt for the agent.

        Returns:
            System prompt string
        """
        return (
            "You are Alfred, a helpful AI assistant with access to various tools. "
            "You can search information, manage tasks, and interact with external services. "
            "Be concise, accurate, and helpful. When using tools, explain what you're doing. "
            "If a tool fails, try alternatives or explain the limitation to the user."
        )

    async def chat(
        self,
        prompt: str,
        context_id: Optional[str] = None,
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        stream: bool = False,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        force_refresh: bool = False,
    ) -> Any:
        """
        Process a chat request through the agent.

        Args:
            prompt: User's message
            context_id: Optional context ID for conversation history
            user_id: Optional user ID for user-specific toolsets (e.g., Notion)
            workspace_id: Optional workspace ID for tool routing
            stream: Enable streaming response
            max_tool_calls: Maximum tool calls allowed
            timeout_seconds: Maximum execution time
            force_refresh: Force refresh of cached data

        Returns:
            ChatResponse for non-streaming, AsyncGenerator for streaming
        """
        # Generate request ID for tracking
        request_id = str(uuid.uuid4())
        self.active_requests[request_id] = time.time()

        logger.info(
            "Processing chat request",
            request_id=request_id,
            context_id=context_id,
            user_id=user_id,
            stream=stream,
            max_tool_calls=max_tool_calls,
        )

        try:
            # Get or create context history
            message_history = self.contexts.get(context_id, []) if context_id else []

            # Re-filter toolsets for this specific request (live health check + user-specific)
            current_toolsets = await self._get_healthy_toolsets(user_id)

            # Apply safety limits - Pydantic AI UsageLimits only supports request_limit and response_tokens_limit
            usage_limits = UsageLimits(
                request_limit=max_tool_calls,
                # Note: timeout_seconds parameter handled at agent level, not in UsageLimits
            )

            # Process based on streaming preference
            if stream:
                return self._stream_chat(
                    prompt=prompt,
                    message_history=message_history,
                    current_toolsets=current_toolsets,
                    usage_limits=usage_limits,
                    request_id=request_id,
                    context_id=context_id,
                )
            else:
                return await self._sync_chat(
                    prompt=prompt,
                    message_history=message_history,
                    current_toolsets=current_toolsets,
                    usage_limits=usage_limits,
                    request_id=request_id,
                    context_id=context_id,
                )

        finally:
            # Clean up request tracking
            self.active_requests.pop(request_id, None)

    async def _sync_chat(
        self,
        prompt: str,
        message_history: List[ModelMessage],
        current_toolsets: List[Any],
        usage_limits: UsageLimits,
        request_id: str,
        context_id: Optional[str],
    ) -> ChatResponse:
        """
        Process non-streaming chat request.

        Returns:
            ChatResponse with reply and metadata
        """
        start_time = time.time()

        try:
            # Run the agent with current toolsets
            result = await self.agent.run(
                prompt,
                message_history=message_history,
                toolsets=current_toolsets,  # Override with current healthy toolsets
                usage_limits=usage_limits,
            )

            # Extract tool calls from messages
            tool_calls = []
            for msg in result.all_messages():
                if hasattr(msg, "role") and msg.role == "tool":
                    tool_calls.append(
                        {
                            "tool": getattr(msg, "tool_name", "unknown"),
                            "args": getattr(msg, "tool_args", {}),
                            "result": msg.content,  # Full result for now (no placeholders)
                        }
                    )

            # Update context history if context_id provided
            if context_id:
                self.contexts[context_id] = list(result.all_messages())

            # Get usage information
            usage = result.usage()

            # Build response
            response = ChatResponse(
                reply=str(result.output),
                meta={
                    "tool_calls": tool_calls,
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "total_tokens": usage.total_tokens,
                    },
                    "request_id": request_id,
                    "duration_ms": int((time.time() - start_time) * 1000),
                    "model": self.settings.anthropic_model,
                },
            )

            logger.info(
                "Chat request completed",
                request_id=request_id,
                duration_ms=response.meta["duration_ms"],
                tool_count=len(tool_calls),
                total_tokens=usage.total_tokens,
            )

            return response

        except Exception as e:
            # Log and re-raise original exception - normalization handled at router level
            logger.error(
                "Chat request failed",
                request_id=request_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise e

    async def _stream_chat(
        self,
        prompt: str,
        message_history: List[ModelMessage],
        current_toolsets: List[Any],
        usage_limits: UsageLimits,
        request_id: str,
        context_id: Optional[str],
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Process streaming chat request.

        Yields:
            StreamEvent objects for each chunk of the response
        """
        start_time = time.time()

        try:
            # Run the agent in streaming mode
            async with self.agent.run_stream(
                prompt,
                message_history=message_history,
                toolsets=current_toolsets,
                usage_limits=usage_limits,
            ) as result:
                # Stream text chunks with debouncing
                async for chunk in result.stream(debounce_by=0.02):
                    yield StreamEvent(
                        type="text",
                        data=chunk,
                        request_id=request_id,
                    )

                # After streaming completes, send tool calls
                for msg in result.all_messages():
                    if hasattr(msg, "role") and msg.role == "tool":
                        yield StreamEvent(
                            type="tool_call",
                            data={
                                "tool": getattr(msg, "tool_name", "unknown"),
                                "args": getattr(msg, "tool_args", {}),
                                "result": msg.content,  # Full result, no placeholders
                            },
                            request_id=request_id,
                        )

                # Update context history if context_id provided
                if context_id:
                    self.contexts[context_id] = list(result.all_messages())

                # Send final event with usage stats
                usage = result.usage()
                yield StreamEvent(
                    type="final",
                    data={
                        "usage": {
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "total_tokens": usage.total_tokens,
                        },
                        "duration_ms": int((time.time() - start_time) * 1000),
                        "model": self.settings.anthropic_model,
                    },
                    request_id=request_id,
                )

                logger.info(
                    "Streaming chat completed",
                    request_id=request_id,
                    duration_ms=int((time.time() - start_time) * 1000),
                    total_tokens=usage.total_tokens,
                )

        except Exception as e:
            # Send error event
            logger.error(
                "Streaming chat failed",
                request_id=request_id,
                error=str(e),
                error_type=type(e).__name__,
            )

            yield StreamEvent(
                type="error",
                data=self._normalize_error(e),
                request_id=request_id,
            )

    def _normalize_error(self, error: Exception) -> Dict[str, Any]:
        """
        Normalize errors to our 4-bucket taxonomy.

        Error buckets:
        1. MODEL_PROVIDER_ERROR - Anthropic/model issues
        2. MCP_UNAVAILABLE - MCP server connectivity issues
        3. TOOL_EXEC_ERROR - Tool execution failures
        4. APP_UNEXPECTED - Everything else

        Args:
            error: The exception to normalize

        Returns:
            Dictionary with error details
        """
        error_str = str(error)
        error_type = type(error).__name__

        # Check for specific error types
        if isinstance(error, UnexpectedModelBehavior):
            return {
                "error": "MODEL_PROVIDER_ERROR",
                "message": "Model behavior validation failed",
                "details": error_str,
                "origin": "anthropic",
            }

        # Check for Anthropic API connection errors
        if "APIConnectionError" in error_type or "Connection error" in error_str:
            return {
                "error": "MODEL_PROVIDER_ERROR",
                "message": "Failed to connect to Anthropic API",
                "details": error_str,
                "origin": "anthropic",
            }

        # Check for MCP-related errors
        if "MCP" in error_type or "mcp" in error_str.lower():
            return {
                "error": "MCP_UNAVAILABLE",
                "message": "MCP server error",
                "details": error_str,
                "origin": "mcp",
            }

        # Check for tool execution errors
        if "tool" in error_str.lower() or "Tool" in error_type:
            return {
                "error": "TOOL_EXEC_ERROR",
                "message": "Tool execution failed",
                "details": error_str,
                "origin": "tool",
            }

        # Default to unexpected app error
        return {
            "error": "APP_UNEXPECTED",
            "message": "Unexpected error occurred",
            "details": error_str,
            "origin": "app",
        }

    def get_context_history(self, context_id: str) -> List[ModelMessage]:
        """
        Get message history for a context.

        Args:
            context_id: Context identifier

        Returns:
            List of messages in the context
        """
        return self.contexts.get(context_id, [])

    def clear_context(self, context_id: str) -> None:
        """
        Clear message history for a context.

        Args:
            context_id: Context identifier
        """
        if context_id in self.contexts:
            del self.contexts[context_id]
            logger.info(f"Cleared context: {context_id}")

    def get_active_requests(self) -> Dict[str, float]:
        """
        Get currently active requests for monitoring.

        Returns:
            Dictionary of request_id -> start_time
        """
        return self.active_requests.copy()

    async def shutdown(self) -> None:
        """
        Clean shutdown of the orchestrator.
        """
        logger.info("Shutting down agent orchestrator")

        # Clear contexts
        self.contexts.clear()

        # Clear active requests
        self.active_requests.clear()

        logger.info("Agent orchestrator shutdown complete")


# Module-level orchestrator instance (singleton pattern)
_orchestrator_instance: Optional[AgentOrchestrator] = None


async def get_agent_orchestrator() -> AgentOrchestrator:
    """
    Get the singleton Agent Orchestrator instance.

    Returns:
        The initialized AgentOrchestrator instance
    """
    global _orchestrator_instance

    if _orchestrator_instance is None:
        _orchestrator_instance = AgentOrchestrator()
        await _orchestrator_instance.initialize()

    return _orchestrator_instance
