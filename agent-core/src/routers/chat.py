"""
Chat endpoints for AI agent interaction.

Provides /chat endpoint for synchronous chat requests (Week 1)
and /chat/stream for SSE streaming (Week 4).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

# Create router for chat endpoints
router = APIRouter()


# Pydantic models for request/response validation
class Message(BaseModel):
    """Single message in a conversation."""

    role: str = Field(
        ...,
        description="Message role (user, assistant, system)",
        example="user",
        pattern="^(user|assistant|system)$",
    )
    content: str = Field(
        ...,
        description="Message content",
        example="What is the capital of France?",
        min_length=1,
    )


class ChatRequest(BaseModel):
    """Request model for chat endpoint following the API contract."""

    messages: List[Message] = Field(
        ...,
        description="Conversation messages",
        min_items=1,
        example=[{"role": "user", "content": "Hello!"}],
    )
    session: Optional[str] = Field(
        None,
        description="Optional session token for conversation continuity",
        example="session-123-abc",
    )
    forceRefresh: bool = Field(
        False, description="Force cache bypass for fresh results", example=False
    )

    class Config:
        """Pydantic model configuration."""

        schema_extra = {
            "example": {
                "messages": [
                    {"role": "user", "content": "What's the weather like today?"}
                ],
                "session": "optional-session-token",
                "forceRefresh": False,
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
        example="123e4567-e89b-12d3-a456-426614174000",
    )


class ChatResponse(BaseModel):
    """Response model for chat endpoint following the API contract."""

    reply: str = Field(
        ...,
        description="Agent's response to the user",
        example="Paris is the capital of France.",
    )
    meta: ResponseMeta = Field(
        ..., description="Response metadata including cache and token info"
    )

    class Config:
        """Pydantic model configuration."""

        schema_extra = {
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


# Dependency for API key authentication
async def verify_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> str:
    """
    Verify API key from request header.

    This is a simple implementation for Week 1.
    TODO: Implement proper auth in production (Week 4).
    """
    # For MVP, we'll check against env var
    # TODO: Load from config properly (Issue #6)
    import os

    expected_key = os.getenv("API_KEY", "dev-api-key-change-for-production")

    if not x_api_key or x_api_key != expected_key:
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
) -> ChatResponse:
    """
    Main chat endpoint for AI agent interaction.

    This endpoint:
    - Accepts conversation messages
    - Routes to appropriate MCP tools (Week 1, Issue #8)
    - Checks cache for repeated queries (Week 1, Issue #10)
    - Returns structured response with metadata

    Args:
        request: FastAPI request object (contains request_id)
        chat_request: Validated chat request with messages
        api_key: Verified API key from header

    Returns:
        ChatResponse with agent reply and metadata

    Raises:
        HTTPException: For various error conditions
    """
    # Get request ID from middleware
    request_id = getattr(request.state, "request_id", "unknown")

    # TODO: Implement actual agent logic (Issue #9)
    # For now, return a stubbed response

    # Extract the last user message for stub response
    last_user_message = None
    for msg in reversed(chat_request.messages):
        if msg.role == "user":
            last_user_message = msg.content
            break

    # Stubbed response for MVP Week 1
    stub_reply = f"I received your message: '{last_user_message}'. This is a stubbed response - actual agent integration coming in Issue #9."

    # TODO: Implement these features in subsequent issues:
    # - MCP router integration (Issue #8)
    # - Pydantic AI agent orchestration (Issue #9)
    # - Cache checking and storage (Issue #10)
    # - Session management (Week 3, Issue #23)
    # - Token counting (Week 3, Issue #26)

    return ChatResponse(
        reply=stub_reply,
        meta=ResponseMeta(
            cacheHit=False,  # Always false for stub
            cacheTtlRemaining=None,
            tokens=TokenUsage(input=0, output=0),  # Will implement counting later
            requestId=request_id,
        ),
    )


@router.get(
    "/chat/stream",
    summary="Stream chat responses via SSE",
    description="Server-sent events endpoint for streaming responses (Week 4)",
    response_description="SSE stream of chat events",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
async def chat_stream_endpoint(
    session: Optional[str] = None,
    api_key: str = Depends(verify_api_key),
) -> Dict[str, Any]:
    """
    SSE streaming endpoint for real-time chat responses.

    This will be implemented in Week 4 (Issue #29).

    Event types:
    - token: Streaming text tokens
    - tool_call: MCP tool invocation
    - tool_result: Tool execution result
    - warning: Context limits or other warnings
    - done: Stream completion with final metadata

    Args:
        session: Optional session token
        api_key: Verified API key

    Returns:
        Currently returns 501 Not Implemented
    """
    # TODO: Implement in Week 4 (Issue #29)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="SSE streaming will be implemented in Week 4",
    )
