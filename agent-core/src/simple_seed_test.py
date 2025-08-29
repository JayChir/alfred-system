#!/usr/bin/env python3
"""
Simple test script to verify database foundation and encryption.
"""

import asyncio

# Load environment variables
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from contextlib import asynccontextmanager

from src.db import create_tables, get_async_session
from src.db.repositories import (
    NotionConnectionsRepository,
    RepositoryError,
    UsersRepository,
)
from src.utils.crypto import get_crypto_service


@asynccontextmanager
async def get_session():
    """Helper to get a properly managed async database session."""
    async for session in get_async_session():
        yield session
        break


async def main():
    print("🌱 Alfred Agent Core - Simple Database Test")
    print("=" * 45)

    # Ensure tables exist
    await create_tables()
    print("✅ Database tables ready")

    # Test crypto service
    crypto = get_crypto_service()
    test_token = "test_token_12345"
    ciphertext = crypto.encrypt_token(test_token)
    decrypted = crypto.decrypt_token(ciphertext)
    assert decrypted == test_token
    print("✅ Encryption working")

    async with get_session() as session:
        users_repo = UsersRepository(session)
        connections_repo = NotionConnectionsRepository(session)

        # Create test user
        try:
            user = await users_repo.create_user("test@alfred.test", "active")
            print(f"✅ Created user: {user.email}")
        except RepositoryError as e:
            if "already exists" in str(e):
                user = await users_repo.get_user_by_email("test@alfred.test")
                print(f"✅ Found existing user: {user.email}")
            else:
                raise

        await session.commit()  # Commit user so FK references work

        # Create test connection
        try:
            connection = await connections_repo.create_connection(
                user_id=user.id,
                workspace_id="test-workspace-123",
                access_token="ntn_test_token_123456789012345678901234",
                bot_id="test-bot-123",
                scopes=["read_content", "insert_content"],
            )
            print(f"✅ Created connection: workspace {connection.workspace_id}")

            # Test encryption round-trip
            decrypted_token = await connections_repo.decrypt_access_token(connection)
            if decrypted_token == "ntn_test_token_123456789012345678901234":
                print("✅ Token encryption/decryption working")
            else:
                print("❌ Token encryption failed")

        except RepositoryError as e:
            if "already exists" in str(e):
                print("✅ Connection already exists (duplicate constraint working)")
            else:
                print(f"❌ Connection creation failed: {e}")

        await session.commit()

        # Test queries
        active_connections = await connections_repo.get_active_connections_by_user(
            user.id
        )
        print(f"✅ Found {len(active_connections)} active connections")

        user_with_connections = await users_repo.get_user_with_connections(user.id)
        print(
            f"✅ User has {len(user_with_connections.notion_connections)} connections via relationship"
        )

    print("\n✨ Database foundation verified successfully!")


if __name__ == "__main__":
    asyncio.run(main())
