"""
Chat endpoints for AI agent interaction with thread support.

Provides /chat endpoint for synchronous chat requests with thread persistence
and /chat/stream for SSE streaming (Week 4).
"""

import json
from typing import Any, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import Settings, get_settings
from src.db.database import get_async_session as get_db
from src.db.models import ThreadMessage
from src.services.agent_orchestrator import get_agent_orchestrator

# from src.services.device_session_service import DeviceSessionService  # Not needed for MVP
from src.services.thread_service import ThreadService
from src.utils.logging import get_logger
from src.utils.validation import context_id_adhoc, require_prefix

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
    deviceToken: Optional[str] = Field(
        None,
        description="Optional device token for metering and continuity",
        pattern="^dtok_.*",
        json_schema_extra={"example": "dtok_optional-device-token"},
    )
    forceRefresh: bool = Field(
        False,
        description="Force cache bypass for fresh results",
        json_schema_extra={"example": False},
    )
    # Thread support fields (Phase 3 - Issue #51)
    threadId: Optional[str] = Field(
        None,
        description="Thread ID for conversation continuity (UUID)",
        json_schema_extra={"example": "123e4567-e89b-12d3-a456-426614174000"},
    )
    threadToken: Optional[str] = Field(
        None,
        description="Share token for cross-device thread access",
        pattern="^thr_.*",
        json_schema_extra={"example": "thr_secure-token-here"},
    )
    clientMessageId: Optional[str] = Field(
        None,
        description="Client-provided ID for idempotency",
        max_length=100,
        json_schema_extra={"example": "client-msg-123"},
    )
    returnShareToken: bool = Field(
        False,
        description="Request a share token for cross-device access",
        json_schema_extra={"example": False},
    )
    forceRetry: bool = Field(
        False,
        description="Force retry of a previously failed request",
        json_schema_extra={"example": False},
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "messages": [
                    {"role": "user", "content": "What's the weather like today?"}
                ],
                "deviceToken": "dtok_optional-device-token",
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
    # Thread support fields (Phase 3 - Issue #51)
    threadId: Optional[str] = Field(
        None,
        description="Thread ID for conversation continuity",
        json_schema_extra={"example": "123e4567-e89b-12d3-a456-426614174000"},
    )
    shareToken: Optional[str] = Field(
        None,
        description="Share token for cross-device access (only if requested)",
        json_schema_extra={"example": "thr_secure-token-here"},
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
    description="Send messages to the AI agent and receive a response with thread support",
    response_description="Agent's response with metadata and optional thread info",
)
async def chat_endpoint(
    request: Request,
    chat_request: ChatRequest,
    api_key: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),  # noqa: B008
    stream: bool = False,  # Query parameter to enable streaming
) -> Any:
    """
    Main chat endpoint for AI agent interaction with thread support.

    This endpoint implements:
    - Persist-first pattern: saves user message before processing
    - Thread continuity: maintains conversation context across requests
    - Idempotency: handles duplicate requests via clientMessageId
    - Cross-device access: share tokens for thread access
    - Partial failure recovery: journaled tool calls can be resumed

    Args:
        request: FastAPI request object (contains request_id)
        chat_request: Validated chat request with messages and thread options
        api_key: Verified API key from header
        db: Database session for thread persistence
        stream: Enable streaming response (returns SSE stream)

    Returns:
        ChatResponse with thread ID and optional share token

    Raises:
        HTTPException: 404 (not found), 409 (retry needed), 410 (expired token)
    """
    # Get request ID from middleware
    request_id = getattr(request.state, "request_id", "unknown")

    # Initialize services
    thread_service = ThreadService()
    # device_session_service = DeviceSessionService()  # Not needed for MVP

    # Validate token prefixes
    if chat_request.deviceToken:
        require_prefix(chat_request.deviceToken, "dtok_", "deviceToken")
    if chat_request.threadToken:
        require_prefix(chat_request.threadToken, "thr_", "threadToken")

    # Extract user_id from headers (MVP - will be from JWT in production)
    user_id = request.headers.get("X-User-ID")

    # Log chat request with thread info
    logger.info(
        "Chat request received with threads",
        request_id=request_id,
        message_count=len(chat_request.messages),
        has_device_token=bool(chat_request.deviceToken),
        has_thread_id=bool(chat_request.threadId),
        has_thread_token=bool(chat_request.threadToken),
        has_client_message_id=bool(chat_request.clientMessageId),
        return_share_token=chat_request.returnShareToken,
        force_retry=chat_request.forceRetry,
        stream=stream,
    )

    # Initialize for error handling
    thread = None
    orchestrator = None

    try:
        # Phase 1: Thread Resolution
        # Handle device session if provided (simplified for MVP)
        workspace_id = None  # For MVP, we'll get this from thread if available

        # Find or create thread (priority: threadToken > threadId > create new)
        if chat_request.threadToken:
            # Validate share token and find thread
            thread = await thread_service.find_or_create_thread(
                db, share_token=chat_request.threadToken, user_id=user_id
            )
            if not thread:
                # Token was not found or expired - check which
                # The thread service would have found it if valid, so it's expired or invalid
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail={
                        "error": "TOKEN_EXPIRED",
                        "message": "Thread token has expired or is invalid",
                    },
                )

        elif chat_request.threadId:
            # Direct thread ID lookup
            thread = await thread_service.find_or_create_thread(
                db, thread_id=chat_request.threadId, user_id=user_id
            )
            if not thread:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "THREAD_NOT_FOUND",
                        "message": f"Thread {chat_request.threadId} not found",
                    },
                )

        else:
            # Create new thread
            thread = await thread_service.find_or_create_thread(
                db,
                user_id=user_id,
                workspace_id=workspace_id,
            )

        # Phase 2: Idempotency Check
        existing_user_msg = None
        existing_assistant_msg = None

        if chat_request.clientMessageId:
            # Check for existing message with this client ID
            stmt = select(ThreadMessage).where(
                and_(
                    ThreadMessage.thread_id == thread.id,
                    ThreadMessage.client_message_id == chat_request.clientMessageId,
                )
            )
            result = await db.execute(stmt)
            existing_user_msg = result.scalar_one_or_none()

            if existing_user_msg:
                # Message exists - check for assistant reply
                stmt = select(ThreadMessage).where(
                    and_(
                        ThreadMessage.thread_id == thread.id,
                        ThreadMessage.in_reply_to == existing_user_msg.id,
                        ThreadMessage.role == "assistant",
                    )
                )
                result = await db.execute(stmt)
                existing_assistant_msg = result.scalar_one_or_none()

                if existing_assistant_msg:
                    if existing_assistant_msg.status == "complete":
                        # Already processed successfully - return cached response
                        logger.info(
                            "Returning cached response for duplicate request",
                            request_id=request_id,
                            thread_id=str(thread.id),
                            client_message_id=chat_request.clientMessageId,
                        )

                        # Handle share token if requested
                        share_token = None
                        if chat_request.returnShareToken:
                            share_token = await thread_service.generate_share_token(
                                db, thread
                            )
                            await db.commit()

                        return ChatResponse(
                            reply=existing_assistant_msg.content.get("text", "")
                            if isinstance(existing_assistant_msg.content, dict)
                            else str(existing_assistant_msg.content),
                            meta=ResponseMeta(
                                cacheHit=True,  # This was a cached response
                                cacheTtlRemaining=None,
                                tokens=TokenUsage(
                                    input=existing_assistant_msg.tokens_input or 0,
                                    output=existing_assistant_msg.tokens_output or 0,
                                ),
                                requestId=request_id,
                            ),
                            threadId=str(thread.id),
                            shareToken=share_token,
                        )

                    elif (
                        existing_assistant_msg.status == "error"
                        and not chat_request.forceRetry
                    ):
                        # Previous attempt failed - require explicit retry
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail={
                                "error": "PREVIOUS_ATTEMPT_FAILED",
                                "message": "Previous attempt failed; set forceRetry=true to retry",
                                "previousError": existing_assistant_msg.content.get(
                                    "error"
                                )
                                if isinstance(existing_assistant_msg.content, dict)
                                else None,
                            },
                        )

        # Phase 3: Persist User Message (TX1)
        # Extract the last user message to persist
        user_message_content = None
        for msg in reversed(chat_request.messages):
            if msg.role == "user":
                user_message_content = msg.content
                break

        if not user_message_content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "NO_USER_MESSAGE",
                    "message": "No user message found in request",
                },
            )

        # Save user message with status="complete" (only if not duplicate)
        if not existing_user_msg:
            user_msg = await thread_service.add_message(
                db,
                thread=thread,
                role="user",
                content=user_message_content,
                client_message_id=chat_request.clientMessageId,
                request_id=UUID(request_id) if request_id != "unknown" else None,
                status="complete",
            )

            # Commit immediately for durability
            await db.commit()
            logger.info(
                "User message persisted",
                thread_id=str(thread.id),
                message_id=str(user_msg.id),
                client_message_id=chat_request.clientMessageId,
            )
        else:
            user_msg = existing_user_msg
            logger.info(
                "Using existing user message",
                thread_id=str(thread.id),
                message_id=str(user_msg.id),
                client_message_id=chat_request.clientMessageId,
            )

        # Phase 4: Load Thread History (excluding just-saved message)
        history_messages = await thread_service.get_thread_messages(
            db,
            thread.id,
            limit=50,  # Reasonable context window
        )

        # Convert to format expected by orchestrator, excluding our just-saved message
        message_history = []
        for hist_msg in history_messages:
            if hist_msg.id != user_msg.id:  # Exclude the message we just saved
                content = hist_msg.content
                if isinstance(content, dict):
                    content = content.get("text", "")
                message_history.append({"role": hist_msg.role, "content": content})

        # Phase 5: Determine Effective Workspace
        # Thread workspace takes precedence over device session workspace
        effective_workspace = thread.workspace_id or (workspace_id)

        if effective_workspace != (workspace_id):
            logger.info(
                "Using thread workspace instead of device workspace",
                thread_workspace=effective_workspace,
                device_workspace=workspace_id,
            )

        # Phase 6: Execute with Orchestrator (No TX)
        orchestrator = await get_agent_orchestrator()

        # For streaming, we need different handling
        if stream:
            # Streaming response with thread context
            async def event_generator():
                """Generate Server-Sent Events for streaming with thread support."""
                assistant_content = []
                tool_calls = []
                tokens_used = {"input": 0, "output": 0}

                try:
                    async for event in orchestrator.chat(
                        prompt=user_message_content,
                        context_id=f"ctx:thread:{thread.id}",
                        user_id=str(user_id),
                        workspace_id=effective_workspace,
                        thread_id=str(thread.id),
                        user_message_id=str(user_msg.id),
                        stream=True,
                        force_refresh=chat_request.forceRefresh,
                    ):
                        # Accumulate content for persistence
                        if event.get("type") == "text":
                            assistant_content.append(event.get("data", ""))
                        elif event.get("type") == "tool_call":
                            tool_calls.append(event.get("data"))
                        elif event.get("type") == "usage":
                            tokens_used = event.get("data", {})

                        # Add thread info to streaming events
                        event["threadId"] = str(thread.id)
                        yield f"data: {json.dumps(event)}\n\n"

                    # Save assistant message after streaming completes
                    assistant_msg = await thread_service.add_message(
                        db,
                        thread=thread,
                        role="assistant",
                        content="".join(assistant_content),
                        in_reply_to=user_msg.id,
                        request_id=UUID(request_id)
                        if request_id != "unknown"
                        else None,
                        status="complete",
                        tool_calls=tool_calls if tool_calls else None,
                        tokens=tokens_used,
                    )
                    await db.commit()

                    # Send final event with share token if requested
                    final_event = {
                        "type": "final",
                        "threadId": str(thread.id),
                        "messageId": str(assistant_msg.id),
                    }

                    if chat_request.returnShareToken:
                        share_token = await thread_service.generate_share_token(
                            db, thread
                        )
                        await db.commit()
                        final_event["shareToken"] = share_token

                    yield f"data: {json.dumps(final_event)}\n\n"

                except Exception as e:
                    # Log error and save error message
                    logger.error(
                        "Streaming error",
                        thread_id=str(thread.id),
                        error=str(e),
                        error_type=type(e).__name__,
                    )

                    # Save assistant error message
                    error_content = {
                        "text": f"Error: {str(e)}",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }

                    assistant_msg = await thread_service.add_message(
                        db,
                        thread=thread,
                        role="assistant",
                        content=error_content,
                        in_reply_to=user_msg.id,
                        request_id=UUID(request_id)
                        if request_id != "unknown"
                        else None,
                        status="error",
                        tool_calls=tool_calls if tool_calls else None,
                    )
                    await db.commit()

                    error_event = {
                        "type": "error",
                        "data": str(e),
                        "threadId": str(thread.id),
                        "request_id": request_id,
                    }
                    yield f"data: {json.dumps(error_event)}\n\n"

            return StreamingResponse(
                event_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Request-ID": request_id,
                    "X-Thread-ID": str(thread.id),
                },
            )

        else:
            # Non-streaming response
            try:
                result = await orchestrator.chat(
                    prompt=user_message_content,
                    context_id=f"ctx:thread:{thread.id}",
                    user_id=str(user_id),
                    workspace_id=effective_workspace,
                    thread_id=str(thread.id),
                    user_message_id=str(user_msg.id),
                    stream=False,
                    force_refresh=chat_request.forceRefresh,
                )

                # Phase 7: Save Assistant Message (TX2)
                assistant_msg = await thread_service.add_message(
                    db,
                    thread=thread,
                    role="assistant",
                    content=result.reply,
                    in_reply_to=user_msg.id,
                    request_id=UUID(request_id) if request_id != "unknown" else None,
                    status="complete",
                    tool_calls=result.meta.get("tool_calls"),
                    tokens={
                        "input": result.meta.get("usage", {}).get("input_tokens", 0),
                        "output": result.meta.get("usage", {}).get("output_tokens", 0),
                    },
                )

                # Commit assistant message
                await db.commit()

                logger.info(
                    "Assistant response saved",
                    thread_id=str(thread.id),
                    assistant_msg_id=str(assistant_msg.id),
                    tokens_input=assistant_msg.tokens_input,
                    tokens_output=assistant_msg.tokens_output,
                    tool_count=len(result.meta.get("tool_calls", [])),
                )

                # Phase 8: Handle Share Token
                share_token = None
                if chat_request.returnShareToken:
                    # Generate new share token (always generate fresh for security)
                    share_token = await thread_service.generate_share_token(db, thread)
                    await db.commit()

                    logger.info(
                        "Share token generated",
                        thread_id=str(thread.id),
                        token_prefix=share_token[:12] if share_token else None,
                    )

                # Return response with thread info
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
                    threadId=str(thread.id),
                    shareToken=share_token,
                )

            except Exception as e:
                # Save assistant error message for partial failure recovery
                logger.error(
                    "Orchestrator execution failed",
                    thread_id=str(thread.id) if thread else None,
                    error=str(e),
                    error_type=type(e).__name__,
                )

                # Save error stub so we know what happened
                if thread and user_msg:
                    error_content = {
                        "text": f"Error during processing: {str(e)}",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }

                    assistant_msg = await thread_service.add_message(
                        db,
                        thread=thread,
                        role="assistant",
                        content=error_content,
                        in_reply_to=user_msg.id,
                        request_id=UUID(request_id)
                        if request_id != "unknown"
                        else None,
                        status="error",
                        # Include any partial tool calls that were executed
                        tool_calls=getattr(e, "executed_tools", None),
                    )
                    await db.commit()

                # Re-raise the error for proper error handling
                raise

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Log unexpected errors
        logger.error(
            "Chat endpoint error",
            request_id=request_id,
            error=str(e),
            error_type=type(e).__name__,
            thread_id=str(thread.id) if thread else None,
        )

        # Normalize error
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
    request: Request,
    prompt: str,
    device_token: Optional[str] = None,
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
        device_token: Optional device token (dtok_...)
        api_key: Verified API key

    Returns:
        StreamingResponse with SSE events
    """
    # Initialize orchestrator to None for error handling
    orchestrator = None

    try:
        # Get the agent orchestrator
        orchestrator = await get_agent_orchestrator()

        # Extract user_id and workspace from headers
        user_id = request.headers.get("X-User-ID")
        workspace_id = request.headers.get("X-Workspace-ID")

        # Generate adhoc context for streaming (legacy endpoint, no thread support)
        request_id = getattr(request.state, "request_id", "unknown")
        context_id = context_id_adhoc(request_id)

        # Create async generator for SSE
        async def event_generator():
            """Generate Server-Sent Events for streaming."""
            try:
                async for event in orchestrator.chat(
                    prompt=prompt,
                    context_id=context_id,
                    user_id=user_id,
                    workspace_id=workspace_id,
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
