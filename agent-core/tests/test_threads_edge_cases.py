"""
Edge case tests for threads-lite functionality (Issue #51 Phase 5).

Tests idempotency, partial failures, token expiry, and race conditions.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Thread, ThreadMessage
from src.services.thread_service import ThreadService


class TestIdempotency:
    """Test idempotency with clientMessageId."""

    @pytest.mark.asyncio
    async def test_duplicate_client_message_id_returns_same_response(
        self, db_session: AsyncSession, test_client
    ):
        """Duplicate clientMessageId should return cached response without re-processing."""
        # Create initial request with clientMessageId
        request = {
            "messages": [{"role": "user", "content": "Test message"}],
            "clientMessageId": "test-idempotent-123",
        }

        # First request - should process normally
        response1 = await test_client.post("/chat", json=request)
        assert response1.status_code == 200
        result1 = response1.json()

        # Check message was saved
        stmt = select(ThreadMessage).where(
            ThreadMessage.client_message_id == "test-idempotent-123"
        )
        msg = (await db_session.execute(stmt)).scalar_one()
        assert msg is not None

        # Second request with same clientMessageId - should return cached
        response2 = await test_client.post("/chat", json=request)
        assert response2.status_code == 200
        result2 = response2.json()

        # Should have same thread ID (idempotent)
        assert result1["threadId"] == result2["threadId"]

        # Should still only have one message with this clientMessageId
        messages = (await db_session.execute(stmt)).scalars().all()
        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_client_message_id_per_thread_isolation(
        self, db_session: AsyncSession, test_client
    ):
        """Same clientMessageId should work across different threads."""
        client_msg_id = "shared-client-msg-123"

        # Create two different threads
        thread1 = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        thread2 = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add_all([thread1, thread2])
        await db_session.commit()

        # Send message to thread1
        request1 = {
            "messages": [{"role": "user", "content": "Message to thread 1"}],
            "threadId": str(thread1.id),
            "clientMessageId": client_msg_id,
        }
        response1 = await test_client.post("/chat", json=request1)
        assert response1.status_code == 200

        # Send message to thread2 with same clientMessageId
        request2 = {
            "messages": [{"role": "user", "content": "Message to thread 2"}],
            "threadId": str(thread2.id),
            "clientMessageId": client_msg_id,
        }
        response2 = await test_client.post("/chat", json=request2)
        assert response2.status_code == 200

        # Both threads should have their messages
        stmt = select(ThreadMessage).where(
            ThreadMessage.client_message_id == client_msg_id
        )
        messages = (await db_session.execute(stmt)).scalars().all()
        assert len(messages) == 2
        assert {msg.thread_id for msg in messages} == {thread1.id, thread2.id}

    @pytest.mark.asyncio
    async def test_retry_with_force_retry_flag(
        self, db_session: AsyncSession, test_client
    ):
        """forceRetry should bypass idempotency check."""
        client_msg_id = "retry-test-456"

        # First request
        request = {
            "messages": [{"role": "user", "content": "Initial message"}],
            "clientMessageId": client_msg_id,
        }
        response1 = await test_client.post("/chat", json=request)
        assert response1.status_code == 200

        # Retry with forceRetry flag
        request["forceRetry"] = True
        with patch(
            "src.services.agent_orchestrator.AgentOrchestrator.chat"
        ) as mock_chat:
            mock_chat.return_value = AsyncMock(
                return_value={"reply": "Retry response", "meta": {}}
            )

            response2 = await test_client.post("/chat", json=request)
            assert response2.status_code == 200

            # Should have called orchestrator despite duplicate clientMessageId
            mock_chat.assert_called_once()


class TestShareTokens:
    """Test share token functionality and edge cases."""

    @pytest.mark.asyncio
    async def test_expired_share_token_returns_410(
        self, db_session: AsyncSession, test_client
    ):
        """Expired share token should return 410 Gone."""
        # Create thread with expired token
        thread_service = ThreadService()
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        # Generate token with 0 TTL (immediately expired)
        token = await thread_service.generate_share_token(
            db_session, thread, ttl_hours=0
        )

        # Manually expire the token
        thread.share_token_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db_session.commit()

        # Try to use expired token
        request = {
            "messages": [{"role": "user", "content": "Test"}],
            "threadToken": token,
        }
        response = await test_client.post("/chat", json=request)
        assert response.status_code == 410
        assert "expired" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_invalid_share_token_returns_404(
        self, db_session: AsyncSession, test_client
    ):
        """Invalid share token should return 404."""
        request = {
            "messages": [{"role": "user", "content": "Test"}],
            "threadToken": "thr_invalid_token_xyz",
        }
        response = await test_client.post("/chat", json=request)
        assert response.status_code == 404
        assert "not found" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_share_token_reuse_when_requested(
        self, db_session: AsyncSession, test_client
    ):
        """returnShareToken should reuse existing valid token."""
        # Create thread with valid token
        thread_service = ThreadService()
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        original_token = await thread_service.generate_share_token(
            db_session, thread, ttl_hours=24
        )
        await db_session.commit()

        # Request with returnShareToken
        request = {
            "messages": [{"role": "user", "content": "Test"}],
            "threadId": str(thread.id),
            "returnShareToken": True,
        }
        response = await test_client.post("/chat", json=request)
        assert response.status_code == 200

        returned_token = response.json()["shareToken"]
        # Should return the same token if still valid
        assert returned_token == original_token


class TestToolJournaling:
    """Test tool call journaling for partial failure recovery."""

    @pytest.mark.asyncio
    async def test_tool_call_idempotency(self, db_session: AsyncSession):
        """Tool calls with same idempotency key should not execute twice."""
        thread_service = ThreadService()

        # Create thread and message
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        user_msg = ThreadMessage(
            thread_id=thread.id,
            role="user",
            content={"text": "Test"},
        )
        db_session.add(user_msg)
        await db_session.flush()

        # Log first tool call
        request_id = str(uuid.uuid4())
        tool_args = {"query": "weather in SF"}

        log1 = await thread_service.log_tool_call(
            db=db_session,
            request_id=request_id,
            thread_id=thread.id,
            message_id=None,
            user_message_id=user_msg.id,
            call_index=0,
            tool_name="weather.get",
            args=tool_args,
        )
        await db_session.commit()

        # Try to log same tool call again (same args, same context)
        log2 = await thread_service.log_tool_call(
            db=db_session,
            request_id=request_id,
            thread_id=thread.id,
            message_id=None,
            user_message_id=user_msg.id,
            call_index=0,
            tool_name="weather.get",
            args=tool_args,
        )

        # Should return the same log entry (idempotent)
        assert log1.id == log2.id
        assert log1.idempotency_key == log2.idempotency_key

    @pytest.mark.asyncio
    async def test_partial_failure_recovery(self, db_session: AsyncSession):
        """System should recover from partial tool execution failures."""
        thread_service = ThreadService()

        # Create thread
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        user_msg = ThreadMessage(
            thread_id=thread.id,
            role="user",
            content={"text": "Get weather and news"},
        )
        db_session.add(user_msg)
        await db_session.flush()

        request_id = str(uuid.uuid4())

        # Log successful weather tool call
        weather_log = await thread_service.log_tool_call(
            db=db_session,
            request_id=request_id,
            thread_id=thread.id,
            message_id=None,
            user_message_id=user_msg.id,
            call_index=0,
            tool_name="weather.get",
            args={"city": "SF"},
        )
        await thread_service.update_tool_call_status(
            db=db_session,
            log_entry=weather_log,
            status="success",
        )

        # Log failed news tool call
        news_log = await thread_service.log_tool_call(
            db=db_session,
            request_id=request_id,
            thread_id=thread.id,
            message_id=None,
            user_message_id=user_msg.id,
            call_index=1,
            tool_name="news.get",
            args={"topic": "tech"},
        )
        await thread_service.update_tool_call_status(
            db=db_session,
            log_entry=news_log,
            status="failed",
            error="API timeout",
        )
        await db_session.commit()

        # Check recovery - get recent tool calls
        recent_calls = await thread_service.get_recent_tool_calls(
            db=db_session,
            thread_id=thread.id,
        )

        # Should see both calls with their statuses
        assert len(recent_calls) == 2
        success_calls = [c for c in recent_calls if c.status == "success"]
        failed_calls = [c for c in recent_calls if c.status == "failed"]

        assert len(success_calls) == 1
        assert success_calls[0].tool_name == "weather.get"

        assert len(failed_calls) == 1
        assert failed_calls[0].tool_name == "news.get"
        assert "timeout" in failed_calls[0].error.lower()


class TestRaceConditions:
    """Test handling of concurrent requests and race conditions."""

    @pytest.mark.asyncio
    async def test_concurrent_messages_to_same_thread(
        self, db_session: AsyncSession, test_client
    ):
        """Concurrent messages to same thread should be handled safely."""
        # Create a thread
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.commit()

        # Send multiple concurrent requests to same thread
        async def send_message(index: int):
            request = {
                "messages": [{"role": "user", "content": f"Message {index}"}],
                "threadId": str(thread.id),
                "clientMessageId": f"concurrent-{index}",
            }
            return await test_client.post("/chat", json=request)

        # Send 5 concurrent messages
        tasks = [send_message(i) for i in range(5)]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed
        for resp in responses:
            if not isinstance(resp, Exception):
                assert resp.status_code == 200

        # Check all messages were saved
        stmt = select(ThreadMessage).where(ThreadMessage.thread_id == thread.id)
        messages = (await db_session.execute(stmt)).scalars().all()

        # Should have 5 user messages + assistant responses
        user_messages = [m for m in messages if m.role == "user"]
        assert len(user_messages) == 5

    @pytest.mark.asyncio
    async def test_concurrent_thread_creation_with_same_token(
        self, db_session: AsyncSession, test_client
    ):
        """Concurrent requests with same share token should resolve to same thread."""
        # Create thread with share token
        thread_service = ThreadService()
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        token = await thread_service.generate_share_token(db_session, thread)
        await db_session.commit()

        # Send concurrent requests with same token
        async def send_with_token(index: int):
            request = {
                "messages": [{"role": "user", "content": f"Concurrent {index}"}],
                "threadToken": token,
            }
            return await test_client.post("/chat", json=request)

        tasks = [send_with_token(i) for i in range(3)]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # All should succeed and use same thread
        thread_ids = []
        for resp in responses:
            if not isinstance(resp, Exception):
                assert resp.status_code == 200
                thread_ids.append(resp.json()["threadId"])

        # All should have same thread ID
        assert len(set(thread_ids)) == 1
        assert thread_ids[0] == str(thread.id)


class TestErrorScenarios:
    """Test various error conditions and recovery."""

    @pytest.mark.asyncio
    async def test_workspace_mismatch_returns_403(
        self, db_session: AsyncSession, test_client
    ):
        """Thread with different workspace than device should return 403."""
        # Create thread with specific workspace
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            workspace_id="workspace-A",
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.commit()

        # Try to access with different workspace in device token
        request = {
            "messages": [{"role": "user", "content": "Test"}],
            "threadId": str(thread.id),
            "deviceToken": "dtok_different_workspace_xyz",  # Would resolve to workspace-B
        }

        with patch(
            "src.services.device_session_service.DeviceSessionService.get_or_create"
        ) as mock_device:
            mock_device.return_value = MagicMock(workspace_id="workspace-B")

            response = await test_client.post("/chat", json=request)
            assert response.status_code == 403
            assert "workspace mismatch" in response.json()["message"].lower()

    @pytest.mark.asyncio
    async def test_deleted_thread_returns_404(
        self, db_session: AsyncSession, test_client
    ):
        """Soft-deleted thread should return 404."""
        # Create and soft-delete thread
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
            deleted_at=datetime.now(timezone.utc),  # Soft deleted
        )
        db_session.add(thread)
        await db_session.commit()

        request = {
            "messages": [{"role": "user", "content": "Test"}],
            "threadId": str(thread.id),
        }
        response = await test_client.post("/chat", json=request)
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_malformed_thread_id_returns_400(self, test_client):
        """Malformed thread ID should return 400."""
        request = {
            "messages": [{"role": "user", "content": "Test"}],
            "threadId": "not-a-uuid",
        }
        response = await test_client.post("/chat", json=request)
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_database_error_returns_500(
        self, db_session: AsyncSession, test_client
    ):
        """Database errors should return 500 with proper error message."""
        request = {
            "messages": [{"role": "user", "content": "Test message"}],
        }

        with patch("src.db.session.get_db") as mock_db:
            mock_db.side_effect = Exception("Database connection failed")

            response = await test_client.post("/chat", json=request)
            assert response.status_code == 500
            assert "database" in response.json()["message"].lower()


class TestMessageHistory:
    """Test message history loading and ordering."""

    @pytest.mark.asyncio
    async def test_message_history_excludes_current_message(
        self, db_session: AsyncSession, test_client
    ):
        """Message history should exclude the just-saved user message."""
        # Create thread with existing messages
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        # Add historical messages
        old_msg = ThreadMessage(
            thread_id=thread.id,
            role="user",
            content={"text": "Old message"},
            client_message_id="old-msg-123",
        )
        db_session.add(old_msg)
        await db_session.commit()

        # Send new message
        request = {
            "messages": [{"role": "user", "content": "New message"}],
            "threadId": str(thread.id),
            "clientMessageId": "new-msg-456",
        }

        with patch(
            "src.services.agent_orchestrator.AgentOrchestrator.chat"
        ) as mock_chat:
            # Capture the message_history passed to orchestrator
            captured_history = None

            async def capture_history(*args, **kwargs):
                nonlocal captured_history
                captured_history = kwargs.get("message_history", [])
                return {"reply": "Test response", "meta": {}}

            mock_chat.side_effect = capture_history

            response = await test_client.post("/chat", json=request)
            assert response.status_code == 200

            # History should include old message but not the new one
            assert captured_history is not None
            assert len(captured_history) == 1
            assert "Old message" in str(captured_history[0])
            assert "New message" not in str(captured_history[0])

    @pytest.mark.asyncio
    async def test_message_history_chronological_order(self, db_session: AsyncSession):
        """Message history should be in chronological order."""
        thread_service = ThreadService()

        # Create thread
        thread = Thread(
            owner_user_id=uuid.uuid4(),
            last_activity_at=datetime.now(timezone.utc),
        )
        db_session.add(thread)
        await db_session.flush()

        # Add messages in random order
        for i in [2, 0, 3, 1]:
            msg = ThreadMessage(
                thread_id=thread.id,
                role="user" if i % 2 == 0 else "assistant",
                content={"text": f"Message {i}"},
                created_at=datetime.now(timezone.utc) + timedelta(minutes=i),
            )
            db_session.add(msg)
        await db_session.commit()

        # Get thread messages
        messages = await thread_service.get_thread_messages(
            db=db_session,
            thread_id=thread.id,
        )

        # Should be in chronological order
        for i, msg in enumerate(messages):
            assert f"Message {i}" in str(msg.content)
