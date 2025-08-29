"""
Chat endpoints for AI agent interaction.

Provides /chat endpoint for synchronous chat requests (Week 1)
and /chat/stream for SSE streaming (Week 4).
"""

import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.config import Settings, get_settings
from src.services.agent_orchestrator import get_agent_orchestrator
from src.utils.logging import get_logger

# Create router for chat endpoints
router = APIRouter()
logger = get_logger(__name__)


# Pydantic models for request/response validation
class Message(BaseModel):
    """Single message in a conversation."""

    role: str = Field(
        ...,
        description="Message role (user, assistant, system)",
        pattern="^(user|assistant|system)$",
        json_schema_extra={"example": "user"},
    )
    content: str = Field(
        ...,
        description="Message content",
        min_length=1,
        json_schema_extra={"example": "What is the capital of France?"},
    )


class ChatRequest(BaseModel):
    """Request model for chat endpoint following the API contract."""

    messages: List[Message] = Field(
        ...,
        description="Conversation messages",
        min_length=1,
        json_schema_extra={"example": [{"role": "user", "content": "Hello!"}]},
    )
    session: Optional[str] = Field(
        None,
        description="Optional session token for conversation continuity",
        json_schema_extra={"example": "session-123-abc"},
    )
    forceRefresh: bool = Field(
        False,
        description="Force cache bypass for fresh results",
        json_schema_extra={"example": False},
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "messages": [
                    {"role": "user", "content": "What's the weather like today?"}
                ],
                "session": "optional-session-token",
                "forceRefresh": False,
            }
        }
    }


class TokenUsage(BaseModel):
    """Token usage information for billing and limits."""

    input: int = Field(0, description="Input tokens used", ge=0)
    output: int = Field(0, description="Output tokens generated", ge=0)


class ResponseMeta(BaseModel):
    """Metadata included in chat responses."""

    cacheHit: bool = Field(False, description="Whether response was served from cache")
    cacheTtlRemaining: Optional[int] = Field(
        None, description="Seconds until cache entry expires", ge=0
    )
    tokens: TokenUsage = Field(
        default_factory=TokenUsage, description="Token usage for this request"
    )
    requestId: str = Field(
        ...,
        description="Unique request identifier for tracing",
        json_schema_extra={"example": "123e4567-e89b-12d3-a456-426614174000"},
    )


class ChatResponse(BaseModel):
    """Response model for chat endpoint following the API contract."""

    reply: str = Field(
        ...,
        description="Agent's response to the user",
        json_schema_extra={"example": "Paris is the capital of France."},
    )
    meta: ResponseMeta = Field(
        ..., description="Response metadata including cache and token info"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "reply": "The capital of France is Paris.",
                "meta": {
                    "cacheHit": False,
                    "cacheTtlRemaining": None,
                    "tokens": {"input": 15, "output": 10},
                    "requestId": "123e4567-e89b-12d3-a456-426614174000",
                },
            }
        }
    }


# Dependency for API key authentication
async def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> str:
    """
    Verify API key from request header.

    This is a simple implementation for Week 1.
    TODO: Implement proper auth with JWT in production (Week 4).
    """
    if not x_api_key or x_api_key != settings.api_key:
        logger.warning(
            "Invalid API key attempt",
            provided_key_prefix=x_api_key[:8] if x_api_key else None,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    return x_api_key


@router.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Chat with the AI agent",
    description="Send messages to the AI agent and receive a response",
    response_description="Agent's response with metadata",
)
async def chat_endpoint(
    request: Request,
    chat_request: ChatRequest,
    api_key: str = Depends(verify_api_key),
    stream: bool = False,  # Query parameter to enable streaming
) -> Any:
    """
    Main chat endpoint for AI agent interaction.

    This endpoint:
    - Accepts conversation messages
    - Routes to appropriate MCP tools via Agent Orchestrator
    - Supports both streaming and non-streaming responses
    - Returns structured response with metadata

    Args:
        request: FastAPI request object (contains request_id)
        chat_request: Validated chat request with messages
        api_key: Verified API key from header
        stream: Enable streaming response (returns SSE stream)

    Returns:
        ChatResponse for non-streaming, StreamingResponse for streaming

    Raises:
        HTTPException: For various error conditions
    """
    # Get request ID from middleware
    request_id = getattr(request.state, "request_id", "unknown")

    # Log chat request details
    logger.info(
        "Chat request received",
        request_id=request_id,
        message_count=len(chat_request.messages),
        has_session=bool(chat_request.session),
        force_refresh=chat_request.forceRefresh,
        stream=stream,
    )

    # Initialize orchestrator to None for error handling
    orchestrator = None

    try:
        # Trigger on-demand token refresh for all user connections (Phase 4 - Issue #16)
        try:
            # OAuth manager integration ready for when user auth is implemented
            # from src.config import get_settings
            # from src.services.oauth_manager import OAuthManager
            # from src.utils.crypto import CryptoService

            # settings = get_settings()
            # crypto_service = CryptoService(settings.fernet_key)
            # oauth_manager = OAuthManager(settings, crypto_service)

            # For MVP, we don't have user authentication yet, so we'll skip user-specific refresh
            # When user authentication is implemented, this would be:
            # user_id = get_current_user_id(request)
            # async with get_db() as db:
            #     await oauth_manager.ensure_token_fresh(db, user_id)

            logger.debug(
                "OAuth token refresh integration ready",
                request_id=request_id,
            )

        except Exception as refresh_e:
            # Don't fail the chat request if token refresh fails
            logger.warning(
                "OAuth token refresh failed",
                request_id=request_id,
                error=str(refresh_e),
                error_type=type(refresh_e).__name__,
            )

        # Get the agent orchestrator
        orchestrator = await get_agent_orchestrator()

        # Convert messages to simple prompt (for MVP - later will pass full history)
        # Extract the last user message as the prompt
        prompt = None
        for msg in reversed(chat_request.messages):
            if msg.role == "user":
                prompt = msg.content
                break

        if not prompt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No user message found in request",
            )

        # Process through agent orchestrator
        if stream:
            # Streaming response
            async def event_generator():
                """Generate Server-Sent Events for streaming."""
                try:
                    async for event in orchestrator.chat(
                        prompt=prompt,
                        session_id=chat_request.session,
                        stream=True,
                        force_refresh=chat_request.forceRefresh,
                    ):
                        # Format as SSE
                        yield f"data: {json.dumps(event.dict())}\n\n"
                except Exception as e:
                    error_event = {
                        "type": "error",
                        "data": str(e),
                        "request_id": request_id,
                    }
                    yield f"data: {json.dumps(error_event)}\n\n"

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Request-ID": request_id,
                },
            )

        else:
            # Non-streaming response
            result = await orchestrator.chat(
                prompt=prompt,
                session_id=chat_request.session,
                stream=False,
                force_refresh=chat_request.forceRefresh,
            )

            # Log response generation
            logger.info(
                "Chat response generated",
                request_id=request_id,
                response_length=len(result.reply),
                tool_count=len(result.meta.get("tool_calls", [])),
                tokens_input=result.meta.get("usage", {}).get("input_tokens", 0),
                tokens_output=result.meta.get("usage", {}).get("output_tokens", 0),
            )

            # Format response according to API contract
            return ChatResponse(
                reply=result.reply,
                meta=ResponseMeta(
                    cacheHit=False,  # TODO: Implement caching in Issue #10
                    cacheTtlRemaining=None,
                    tokens=TokenUsage(
                        input=result.meta.get("usage", {}).get("input_tokens", 0),
                        output=result.meta.get("usage", {}).get("output_tokens", 0),
                    ),
                    requestId=request_id,
                ),
            )

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log and convert other exceptions
        logger.error(
            "Chat endpoint error",
            request_id=request_id,
            error=str(e),
            error_type=type(e).__name__,
        )

        # Normalize error using orchestrator's error taxonomy if available
        if orchestrator and hasattr(orchestrator, "_normalize_error"):
            error_detail = orchestrator._normalize_error(e)
        else:
            error_detail = {"error": "APP_UNEXPECTED", "message": str(e)}

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error_detail,
        ) from e


@router.get(
    "/chat/stream",
    summary="Stream chat responses via SSE",
    description="Server-sent events endpoint for streaming responses",
    response_description="SSE stream of chat events",
)
async def chat_stream_endpoint(
    prompt: str,
    session: Optional[str] = None,
    api_key: str = Depends(verify_api_key),
) -> StreamingResponse:
    """
    SSE streaming endpoint for real-time chat responses.

    This provides an alternative GET endpoint for streaming.
    The POST /chat endpoint with stream=true is preferred.

    Event types:
    - text: Streaming text chunks
    - tool_call: MCP tool invocation
    - error: Error event
    - final: Stream completion with final metadata

    Args:
        prompt: User's message/prompt
        session: Optional session token
        api_key: Verified API key

    Returns:
        StreamingResponse with SSE events
    """
    # Initialize orchestrator to None for error handling
    orchestrator = None

    try:
        # Get the agent orchestrator
        orchestrator = await get_agent_orchestrator()

        # Create async generator for SSE
        async def event_generator():
            """Generate Server-Sent Events for streaming."""
            try:
                async for event in orchestrator.chat(
                    prompt=prompt,
                    session_id=session,
                    stream=True,
                ):
                    # Format as SSE
                    yield f"data: {json.dumps(event.dict())}\n\n"
            except Exception as e:
                error_event = {
                    "type": "error",
                    "data": str(e),
                }
                yield f"data: {json.dumps(error_event)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    except Exception as e:
        logger.error(
            "Stream endpoint error",
            error=str(e),
            error_type=type(e).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "STREAM_INIT_FAILED", "message": str(e)},
        ) from e
