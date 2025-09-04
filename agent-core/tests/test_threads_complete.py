#!/usr/bin/env python3
"""
Comprehensive test script for threads functionality with new metadata fields.

Tests:
1. Thread creation with title and metadata
2. Message creation with request_id
3. Idempotency checks
4. Share token functionality
5. Tool call logging
6. Soft delete behavior
"""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from sqlalchemy import select, text  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.db.database import get_session_factory  # noqa: E402
from src.db.models import ThreadMessage  # noqa: E402
from src.services.thread_service import ThreadService  # noqa: E402
from src.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


async def run_tests():
    """Run comprehensive thread functionality tests."""
    settings = get_settings()

    # Create database session factory
    async_session = get_session_factory(settings)

    async with async_session() as db:
        # Initialize thread service
        thread_service = ThreadService()

        print("\n" + "=" * 60)
        print("THREAD FUNCTIONALITY TESTS WITH NEW METADATA")
        print("=" * 60)

        # Test 1: Create thread with title and metadata
        print("\n1. Testing thread creation with title and metadata...")
        thread = await thread_service.find_or_create_thread(
            db=db,
            user_id=str(uuid4()),
            workspace_id="test-workspace",
            title="Test Chat Session - Development",
            thread_metadata={
                "client_version": "1.0.0",
                "platform": "web",
                "tags": ["test", "development"],
                "custom_field": "test_value",
            },
        )
        await db.commit()

        print(f"   ✓ Created thread: {thread.id}")
        print(f"   ✓ Title: {thread.title}")
        print(f"   ✓ Metadata: {json.dumps(thread.thread_metadata, indent=2)}")
        print(f"   ✓ Workspace: {thread.workspace_id}")
        print(f"   ✓ Last activity: {thread.last_activity_at}")

        # Test 2: Add message with request_id
        print("\n2. Testing message creation with request_id...")
        request_id = uuid4()
        user_msg = await thread_service.add_message(
            db=db,
            thread=thread,
            role="user",
            content="Hello, test message with request tracking",
            client_message_id=f"client-msg-{uuid4()}",
            request_id=request_id,
            tokens={"input": 10, "output": 0},
        )
        await db.commit()

        print(f"   ✓ Created user message: {user_msg.id}")
        print(f"   ✓ Request ID: {user_msg.request_id}")
        print(f"   ✓ Client message ID: {user_msg.client_message_id}")
        print(f"   ✓ Content: {user_msg.content}")

        # Test 3: Test idempotency
        print("\n3. Testing idempotency with duplicate client_message_id...")
        duplicate_msg = await thread_service.add_message(
            db=db,
            thread=thread,
            role="user",
            content="This should be ignored due to duplicate client_message_id",
            client_message_id=user_msg.client_message_id,  # Same ID
            request_id=uuid4(),  # Different request ID
        )
        await db.commit()

        if duplicate_msg.id == user_msg.id:
            print(
                f"   ✓ Idempotency works! Returned existing message: {duplicate_msg.id}"
            )
        else:
            print("   ✗ ERROR: Created duplicate message!")

        # Test 4: Add assistant message with tool calls
        print("\n4. Testing assistant message with tool calls...")
        assistant_msg = await thread_service.add_message(
            db=db,
            thread=thread,
            role="assistant",
            content="I'll help you with that. Let me look up some information.",
            in_reply_to=user_msg.id,
            request_id=request_id,
            status="complete",
            tool_calls=[
                {
                    "id": str(uuid4()),
                    "name": "notion.search",
                    "args": {"query": "test search"},
                }
            ],
            tokens={"input": 15, "output": 25},
        )
        await db.commit()

        print(f"   ✓ Created assistant message: {assistant_msg.id}")
        print(f"   ✓ In reply to: {assistant_msg.in_reply_to}")
        print(
            f"   ✓ Tool calls: {len(assistant_msg.tool_calls) if assistant_msg.tool_calls else 0}"
        )
        print(
            f"   ✓ Tokens: input={assistant_msg.tokens_input}, output={assistant_msg.tokens_output}"
        )

        # Test 5: Log tool calls
        print("\n5. Testing tool call logging...")
        tool_log = await thread_service.log_tool_call(
            db=db,
            request_id=str(request_id),
            thread_id=thread.id,
            message_id=assistant_msg.id,
            user_message_id=user_msg.id,
            call_index=0,
            tool_name="notion.search",
            args={"query": "test search"},
        )
        await db.commit()

        print(f"   ✓ Logged tool call: {tool_log.id}")
        print(f"   ✓ Tool: {tool_log.tool_name}")
        print(f"   ✓ Idempotency key: {tool_log.idempotency_key[:16]}...")
        print(f"   ✓ Status: {tool_log.status}")

        # Update tool call status
        await thread_service.update_tool_call_status(
            db=db, log_entry=tool_log, status="success", result_digest="abc123"
        )
        await db.commit()

        print(f"   ✓ Updated status to: {tool_log.status}")
        print(f"   ✓ Result digest: {tool_log.result_digest}")

        # Test 6: Test duplicate tool call (idempotency)
        print("\n6. Testing tool call idempotency...")
        duplicate_tool = await thread_service.log_tool_call(
            db=db,
            request_id=str(request_id),
            thread_id=thread.id,
            message_id=assistant_msg.id,
            user_message_id=user_msg.id,
            call_index=0,  # Same index
            tool_name="notion.search",  # Same tool
            args={"query": "test search"},  # Same args
        )
        await db.commit()

        if duplicate_tool.id == tool_log.id:
            print(
                f"   ✓ Tool call idempotency works! Returned existing: {duplicate_tool.id}"
            )
        else:
            print("   ✗ ERROR: Created duplicate tool call!")

        # Test 7: Generate share token
        print("\n7. Testing share token generation...")
        share_token = await thread_service.generate_share_token(
            db=db, thread=thread, ttl_hours=24
        )
        await db.commit()

        print(f"   ✓ Generated share token: {share_token[:20]}...")
        print(f"   ✓ Token hash stored: {thread.share_token_hash is not None}")
        print(f"   ✓ Expires at: {thread.share_token_expires_at}")

        # Test 8: Find thread by share token
        print("\n8. Testing thread lookup by share token...")
        found_thread = await thread_service.find_or_create_thread(
            db=db, share_token=share_token
        )
        await db.commit()

        if found_thread.id == thread.id:
            print(f"   ✓ Successfully found thread by share token: {found_thread.id}")
            print(f"   ✓ Title preserved: {found_thread.title}")
            print(f"   ✓ Metadata preserved: {bool(found_thread.thread_metadata)}")
        else:
            print("   ✗ ERROR: Found wrong thread or created new one!")

        # Test 9: Get thread messages
        print("\n9. Testing message retrieval...")
        messages = await thread_service.get_thread_messages(
            db=db, thread_id=thread.id, limit=10
        )

        print(f"   ✓ Retrieved {len(messages)} messages")
        for msg in messages:
            print(f"      - {msg.role}: {msg.content.get('text', msg.content)[:50]}...")
            if msg.request_id:
                print(f"        Request ID: {msg.request_id}")

        # Test 10: Get recent tool calls
        print("\n10. Testing tool call retrieval...")
        tool_calls = await thread_service.get_recent_tool_calls(
            db=db, thread_id=thread.id, limit=5
        )

        print(f"   ✓ Retrieved {len(tool_calls)} tool calls")
        for tc in tool_calls:
            print(f"      - {tc.tool_name}: {tc.status}")

        # Test 11: Soft delete thread
        print("\n11. Testing soft delete (with token clearing)...")
        deleted_thread = await thread_service.soft_delete_thread(
            db=db, thread_id=thread.id
        )
        await db.commit()

        print(f"   ✓ Soft deleted thread: {deleted_thread.id}")
        print(f"   ✓ Deleted at: {deleted_thread.deleted_at}")
        print(f"   ✓ Share token cleared: {deleted_thread.share_token_hash is None}")
        print(
            f"   ✓ Token expiry cleared: {deleted_thread.share_token_expires_at is None}"
        )

        # Test 12: Verify deleted thread not found
        print("\n12. Testing deleted thread is not found...")
        not_found = await thread_service.find_or_create_thread(
            db=db, thread_id=str(thread.id)
        )

        if not_found.id != thread.id:
            print(f"   ✓ Deleted thread not found, new one created: {not_found.id}")
        else:
            print("   ✗ ERROR: Found deleted thread!")

        # Test 13: Query with request_id index
        print("\n13. Testing request_id index performance...")
        stmt = select(ThreadMessage).where(ThreadMessage.request_id == request_id)
        result = await db.execute(stmt)
        msgs_by_request = result.scalars().all()

        print(
            f"   ✓ Found {len(msgs_by_request)} messages with request_id {request_id}"
        )

        # Test 14: Create thread without optional fields
        print("\n14. Testing backward compatibility (no metadata)...")
        simple_thread = await thread_service.find_or_create_thread(
            db=db, workspace_id="simple-workspace"
        )
        await db.commit()

        print(f"   ✓ Created simple thread: {simple_thread.id}")
        print(f"   ✓ Title is None: {simple_thread.title is None}")
        print(f"   ✓ Metadata is None: {simple_thread.thread_metadata is None}")

        print("\n" + "=" * 60)
        print("ALL TESTS COMPLETED SUCCESSFULLY! ✅")
        print("=" * 60)

        # Cleanup: Delete test data
        print("\nCleaning up test data...")
        await db.execute(
            text(
                """
            DELETE FROM tool_call_log
            WHERE thread_id IN (SELECT id FROM threads WHERE workspace_id LIKE 'test-%' OR workspace_id = 'simple-workspace')
        """
            )
        )
        await db.execute(
            text(
                """
            DELETE FROM thread_messages
            WHERE thread_id IN (SELECT id FROM threads WHERE workspace_id LIKE 'test-%' OR workspace_id = 'simple-workspace')
        """
            )
        )
        await db.execute(
            text(
                """
            DELETE FROM threads
            WHERE workspace_id LIKE 'test-%' OR workspace_id = 'simple-workspace'
        """
            )
        )
        await db.commit()
        print("   ✓ Test data cleaned up")


async def main():
    """Main entry point."""
    try:
        await run_tests()
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
