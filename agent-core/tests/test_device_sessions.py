"""
Integration tests for device session management (Issue #23).

Tests the complete device session lifecycle including:
- Token creation and validation
- Session expiry and renewal
- Token metering and usage tracking
- Workspace binding and resolution
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeviceSession
from src.services.device_session_service import DeviceSessionService
from src.services.workspace_resolver import WorkspaceResolver


@pytest.mark.asyncio
async def test_create_device_session(db_session: AsyncSession):
    """Test creating a new device session with secure token."""
    # Initialize service
    service = DeviceSessionService()

    # Create a device session
    user_id = uuid4()
    workspace_id = "test-workspace"

    token = await service.create_device_session(
        db_session, user_id=user_id, workspace_id=workspace_id
    )

    # Verify token format (dtok_ prefix + 43 chars base64)
    assert token.startswith("dtok_")
    assert len(token) == 48  # 5 (prefix) + 43 (base64)

    # Verify session was created in database
    result = await db_session.execute(
        select(DeviceSession).where(DeviceSession.user_id == user_id)
    )
    session = result.scalar_one()

    assert session.workspace_id == workspace_id
    assert session.expires_at > datetime.now(timezone.utc)
    assert session.hard_expires_at > session.expires_at
    assert session.revoked_at is None
    assert session.tokens_input == 0
    assert session.tokens_output == 0


@pytest.mark.asyncio
async def test_validate_device_token(db_session: AsyncSession):
    """Test token validation with sliding expiry renewal."""
    service = DeviceSessionService()

    # Create a session
    user_id = uuid4()
    token = await service.create_device_session(db_session, user_id=user_id)

    # Validate the token
    context = await service.validate_device_token(db_session, token)

    assert context is not None
    assert context.user_id == user_id
    assert context.workspace_id is None
    assert context.session_id is not None

    # Validate with invalid token
    invalid_context = await service.validate_device_token(
        db_session, "dtok_invalid_token_123"
    )
    assert invalid_context is None


@pytest.mark.asyncio
async def test_token_expiry_sliding_window(db_session: AsyncSession):
    """Test that token expiry slides forward on access."""
    service = DeviceSessionService()

    # Create a session
    user_id = uuid4()
    token = await service.create_device_session(db_session, user_id=user_id)

    # Get initial expiry
    result1 = await db_session.execute(
        select(DeviceSession).where(DeviceSession.user_id == user_id)
    )
    session1 = result1.scalar_one()
    initial_expiry = session1.expires_at
    initial_access = session1.last_accessed

    # Wait a moment and validate (which should update expiry)
    await service.validate_device_token(db_session, token)

    # Check expiry was extended
    result2 = await db_session.execute(
        select(DeviceSession).where(DeviceSession.user_id == user_id)
    )
    session2 = result2.scalar_one()

    assert session2.expires_at >= initial_expiry
    assert session2.last_accessed > initial_access
    assert session2.request_count == 1


@pytest.mark.asyncio
async def test_update_token_usage(db_session: AsyncSession):
    """Test token usage metering for billing."""
    service = DeviceSessionService()

    # Create a session
    user_id = uuid4()
    token = await service.create_device_session(db_session, user_id=user_id)

    # Get session context
    context = await service.validate_device_token(db_session, token)
    assert context is not None

    # Update token usage
    await service.update_token_usage(
        db_session, session_id=context.session_id, tokens_input=1500, tokens_output=800
    )

    # Verify usage was recorded
    result = await db_session.execute(
        select(DeviceSession).where(DeviceSession.session_id == context.session_id)
    )
    session = result.scalar_one()

    assert session.tokens_input == 1500
    assert session.tokens_output == 800


@pytest.mark.asyncio
async def test_revoke_device_session(db_session: AsyncSession):
    """Test revoking a device session."""
    service = DeviceSessionService()

    # Create a session
    user_id = uuid4()
    token = await service.create_device_session(db_session, user_id=user_id)

    # Get session context
    context = await service.validate_device_token(db_session, token)
    assert context is not None

    # Revoke the session
    success = await service.revoke_device_session(
        db_session, session_id=context.session_id
    )
    assert success is True

    # Verify session is revoked
    result = await db_session.execute(
        select(DeviceSession).where(DeviceSession.session_id == context.session_id)
    )
    session = result.scalar_one()
    assert session.revoked_at is not None

    # Validation should now fail
    invalid_context = await service.validate_device_token(db_session, token)
    assert invalid_context is None


@pytest.mark.asyncio
async def test_workspace_resolver_precedence(db_session: AsyncSession):
    """Test workspace resolution with proper precedence."""
    resolver = WorkspaceResolver()

    # Test 1: Thread workspace takes precedence
    context1 = await resolver.resolve_workspace(
        db_session, thread_id=uuid4(), device_workspace="device-ws"
    )
    # Would return thread workspace if thread had one
    # Since we don't have a real thread, returns device workspace
    assert context1.workspace_id == "device-ws"
    assert context1.source == "device"

    # Test 2: Device workspace when no thread
    context2 = await resolver.resolve_workspace(
        db_session, thread_id=None, device_workspace="device-ws"
    )
    assert context2.workspace_id == "device-ws"
    assert context2.source == "device"

    # Test 3: No workspace
    context3 = await resolver.resolve_workspace(
        db_session, thread_id=None, device_workspace=None
    )
    assert context3.workspace_id is None
    assert context3.source == "none"


@pytest.mark.asyncio
async def test_get_session_stats(db_session: AsyncSession):
    """Test retrieving session statistics."""
    service = DeviceSessionService()

    # Create a session
    user_id = uuid4()
    token = await service.create_device_session(
        db_session, user_id=user_id, workspace_id="test-workspace"
    )

    # Get session context
    context = await service.validate_device_token(db_session, token)
    assert context is not None

    # Update some usage
    await service.update_token_usage(
        db_session, session_id=context.session_id, tokens_input=2000, tokens_output=1000
    )

    # Get stats
    stats = await service.get_session_stats(db_session, context.session_id)

    assert stats is not None
    assert stats["session_id"] == str(context.session_id)
    assert stats["user_id"] == str(user_id)
    assert stats["workspace_id"] == "test-workspace"
    assert stats["tokens_input"] == 2000
    assert stats["tokens_output"] == 1000
    assert stats["total_tokens"] == 3000
    assert stats["request_count"] == 1  # From validation
    assert stats["is_active"] is True
    assert "created_at" in stats
    assert "expires_at" in stats
    assert "last_accessed" in stats


@pytest.mark.asyncio
async def test_concurrent_token_creation(db_session: AsyncSession):
    """Test that concurrent token creation doesn't cause conflicts."""
    service = DeviceSessionService()
    user_id = uuid4()

    # Create multiple sessions concurrently
    import asyncio

    tokens = await asyncio.gather(
        service.create_device_session(db_session, user_id=user_id),
        service.create_device_session(db_session, user_id=user_id),
        service.create_device_session(db_session, user_id=user_id),
    )

    # All tokens should be unique
    assert len(set(tokens)) == 3

    # All tokens should be valid
    for token in tokens:
        context = await service.validate_device_token(db_session, token)
        assert context is not None
        assert context.user_id == user_id
