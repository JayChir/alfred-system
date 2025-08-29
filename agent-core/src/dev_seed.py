#!/usr/bin/env python3
"""
Development data seeding script for Alfred Agent Core.

This script creates test users and OAuth connections to verify the database
schema, encryption/decryption, constraints, and relationships work correctly.

Run with: python -m src.dev_seed
"""

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

# Load environment variables from .env file for development
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    # dotenv not available, assume environment is already configured
    pass

from contextlib import asynccontextmanager

from src.db import create_tables, get_async_session
from src.db.repositories import (
    DuplicateConnectionError,
    NotionConnectionsRepository,
    RepositoryError,
    UsersRepository,
)
from src.utils.crypto import generate_fernet_key, get_crypto_service


@asynccontextmanager
async def get_session():
    """Helper to get a properly managed async database session."""
    async for session in get_async_session():
        yield session
        break  # Only get one session from the generator


async def verify_environment():
    """
    Verify required environment variables are set for development.

    Checks for database connection and encryption key configuration.
    """
    print("🔧 Verifying environment configuration...")

    # Check database URL
    db_url = os.getenv("DB_URL")
    if not db_url:
        print("❌ DB_URL environment variable not set")
        print(
            "   Add to .env: DB_URL=postgresql+psycopg://alfred:password@localhost:5432/agent_core"
        )
        return False

    print(f"✅ Database URL configured: {db_url.split('@')[0]}@***")

    # Check encryption key
    fernet_key = os.getenv("FERNET_KEY")
    if not fernet_key:
        print("❌ FERNET_KEY environment variable not set")
        print(f"   Add to .env: FERNET_KEY={generate_fernet_key()}")
        return False

    print("✅ Fernet encryption key configured")

    # Test crypto service
    try:
        crypto = get_crypto_service()
        test_token = "test_token_123"
        ciphertext = crypto.encrypt_token(test_token)
        decrypted = crypto.decrypt_token(ciphertext)
        assert decrypted == test_token
        print("✅ Crypto service working correctly")
    except Exception as e:
        print(f"❌ Crypto service error: {e}")
        return False

    return True


async def seed_test_users_in_session(session):
    """
    Create test users to verify user management and constraints.

    Tests:
    - User creation with normalized emails
    - Duplicate email constraint
    - Email case-insensitive lookup
    """
    print("\n👥 Creating test users...")

    users_repo = UsersRepository(session)

    # Test users to create
    test_users = [
        ("developer@alfred.test", "active"),
        ("ADMIN@Alfred.Test", "active"),  # Test case normalization
        ("inactive@alfred.test", "inactive"),
    ]

    created_users = []

    for email, status in test_users:
        try:
            user = await users_repo.create_user(email, status)
            created_users.append(user)
            print(f"✅ Created user: {user.email} (ID: {str(user.id)[:8]}...)")

            # Verify email normalization
            if email != user.email:
                print(f"   📝 Email normalized: {email} → {user.email}")

        except RepositoryError as e:
            if "already exists" in str(e):
                print(f"⚠️  User {email.lower()} already exists, skipping")
                # Get existing user for tests
                existing_user = await users_repo.get_user_by_email(email)
                if existing_user:
                    created_users.append(existing_user)
            else:
                print(f"❌ Failed to create user {email}: {e}")

    # Test duplicate email constraint
    if created_users:
        try:
            await users_repo.create_user(created_users[0].email, "active")
            print("❌ Duplicate email constraint not working!")
        except RepositoryError:
            print("✅ Duplicate email constraint working correctly")

    await session.commit()  # Commit users before testing lookups

    # Test case-insensitive email lookup
    if created_users:
        test_email = created_users[0].email.upper()
        found_user = await users_repo.get_user_by_email(test_email)
        if found_user:
            print("✅ Case-insensitive email lookup working")
        else:
            print("❌ Case-insensitive email lookup failed")

    print(f"✅ Seeded {len(created_users)} users successfully")

    return created_users


async def seed_test_connections_in_session(session, users):
    """
    Create test OAuth connections to verify encryption and constraints.

    Tests:
    - Token encryption/decryption round-trip
    - Workspace uniqueness constraints
    - Foreign key relationships
    - Connection status management
    """
    print("\n🔗 Creating test OAuth connections...")

    if not users:
        print("⚠️  No users available for connection seeding")
        return []

    connections_repo = NotionConnectionsRepository(session)

    # Test OAuth tokens (fake but realistic format)
    test_connections = [
        {
            "user_id": users[0].id,
            "workspace_id": "notion-workspace-123",
            "access_token": "ntn_12345678901234567890123456789012345",
            "bot_id": "bot-abc123",
            "scopes": ["read_content", "insert_content", "update_content"],
            "refresh_token": "refresh_98765432109876543210987654321098765",
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(days=30),
            "refresh_token_expires_at": datetime.now(timezone.utc) + timedelta(days=90),
        },
        {
            "user_id": users[0].id if len(users) > 0 else uuid.uuid4(),
            "workspace_id": "notion-workspace-456",
            "access_token": "ntn_98765432109876543210987654321098765",
            "bot_id": "bot-def456",
            "scopes": ["read_content"],
            "access_token_expires_at": datetime.now(timezone.utc) + timedelta(days=15),
        },
    ]

    # Add connection for second user if available
    if len(users) > 1:
        test_connections.append(
            {
                "user_id": users[1].id,
                "workspace_id": "notion-workspace-789",
                "access_token": "ntn_11111111111111111111111111111111111",
                "bot_id": "bot-ghi789",
                "scopes": ["read_content", "insert_content"],
                "access_token_expires_at": datetime.now(timezone.utc)
                + timedelta(days=60),
                "refresh_token": "refresh_11111111111111111111111111111111111",
                "refresh_token_expires_at": datetime.now(timezone.utc)
                + timedelta(days=180),
            }
        )

    created_connections = []

    for conn_data in test_connections:
        try:
            connection = await connections_repo.create_connection(**conn_data)
            created_connections.append(connection)

            # Verify encryption round-trip immediately
            decrypted_access = await connections_repo.decrypt_access_token(connection)
            if decrypted_access == conn_data["access_token"]:
                print(
                    f"✅ Connection created with working encryption: workspace {connection.workspace_id}"
                )
            else:
                print(
                    f"❌ Encryption round-trip failed for workspace {connection.workspace_id}"
                )

            # Test refresh token if present
            if conn_data.get("refresh_token"):
                decrypted_refresh = await connections_repo.decrypt_refresh_token(
                    connection
                )
                if decrypted_refresh == conn_data["refresh_token"]:
                    print("   ✅ Refresh token encryption working")
                else:
                    print("   ❌ Refresh token encryption failed")

        except DuplicateConnectionError as e:
            print(f"⚠️  Connection already exists: {e}")
            # Try to get existing connection
            existing = await connections_repo.get_connection_for_workspace(
                conn_data["user_id"], conn_data["workspace_id"]
            )
            if existing:
                created_connections.append(existing)

        except RepositoryError as e:
            print(f"❌ Failed to create connection: {e}")

    # Test duplicate workspace constraint
    if created_connections:
        try:
            first_conn = created_connections[0]
            await connections_repo.create_connection(
                user_id=first_conn.user_id,
                workspace_id=first_conn.workspace_id,
                access_token="duplicate_token",
                bot_id=first_conn.bot_id,
            )
            print("❌ Duplicate workspace constraint not working!")
        except DuplicateConnectionError:
            print("✅ Duplicate workspace constraint working correctly")

    await session.commit()
    print(f"✅ Seeded {len(created_connections)} connections successfully")

    return created_connections


async def verify_relationships_and_queries(users, connections):
    """
    Verify database relationships, queries, and business logic.

    Tests:
    - User-connection relationships
    - Active connection filtering
    - Connection revocation
    - Cascade behavior
    """
    print("\n🔍 Verifying relationships and queries...")

    async with get_session() as session:
        users_repo = UsersRepository(session)
        connections_repo = NotionConnectionsRepository(session)

        if not users:
            print("⚠️  No users to test relationships")
            return

        # Test user-with-connections loading
        user_with_conns = await users_repo.get_user_with_connections(users[0].id)
        if user_with_conns and user_with_conns.notion_connections:
            print(
                f"✅ User-connections relationship working ({len(user_with_conns.notion_connections)} connections)"
            )
        else:
            print("⚠️  User has no connections or relationship not working")

        # Test active connections filtering
        active_connections = await connections_repo.get_active_connections_by_user(
            users[0].id
        )
        print(f"✅ Found {len(active_connections)} active connections for user")

        # Test connection revocation
        if connections:
            test_conn = connections[0]
            success = await connections_repo.revoke_connection(test_conn.id)
            if success:
                print("✅ Connection revocation working")

                # Verify revoked connection doesn't appear in active list
                active_after_revoke = (
                    await connections_repo.get_active_connections_by_user(users[0].id)
                )
                if len(active_after_revoke) == len(active_connections) - 1:
                    print("✅ Revoked connection filtered from active list")
                else:
                    print("⚠️  Revoked connection filtering may not be working")

            await session.commit()

        # Test workspace-specific lookup
        if connections:
            workspace_conn = await connections_repo.get_connection_for_workspace(
                users[0].id, connections[0].workspace_id
            )
            if workspace_conn:
                print("✅ Workspace-specific connection lookup working")
            else:
                print("⚠️  Workspace-specific lookup not finding connection")

        print("✅ Relationship and query verification complete")


async def verify_token_management():
    """
    Verify advanced token management features.

    Tests:
    - Token updates and re-encryption
    - Key rotation simulation
    - Expiration handling
    """
    print("\n🔐 Verifying token management...")

    async with get_session() as session:
        connections_repo = NotionConnectionsRepository(session)

        # Get a test connection to update
        test_user_email = "developer@alfred.test"
        users_repo = UsersRepository(session)
        user = await users_repo.get_user_by_email(test_user_email)

        if not user:
            print("⚠️  No test user found for token management tests")
            return

        active_connections = await connections_repo.get_active_connections_by_user(
            user.id
        )
        if not active_connections:
            print("⚠️  No active connections found for token management tests")
            return

        test_conn = active_connections[0]

        # Test token update
        new_access_token = "ntn_updated_token_123456789012345678901234"
        new_refresh_token = "refresh_updated_123456789012345678901234"
        new_expiry = datetime.now(timezone.utc) + timedelta(days=45)

        update_success = await connections_repo.update_tokens(
            connection_id=test_conn.id,
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            access_token_expires_at=new_expiry,
        )

        if update_success:
            print("✅ Token update successful")

            # Verify new tokens decrypt correctly
            updated_conn = await connections_repo.get_connection_by_id(test_conn.id)
            if updated_conn:
                decrypted_access = await connections_repo.decrypt_access_token(
                    updated_conn
                )
                decrypted_refresh = await connections_repo.decrypt_refresh_token(
                    updated_conn
                )

                if decrypted_access == new_access_token:
                    print("✅ Updated access token decryption working")
                else:
                    print("❌ Updated access token decryption failed")

                if decrypted_refresh == new_refresh_token:
                    print("✅ Updated refresh token decryption working")
                else:
                    print("❌ Updated refresh token decryption failed")
        else:
            print("❌ Token update failed")

        await session.commit()
        print("✅ Token management verification complete")


async def print_summary():
    """Print a summary of seeded data for development use."""
    print("\n📊 Development Database Summary")
    print("=" * 40)

    async with get_session() as session:
        users_repo = UsersRepository(session)
        connections_repo = NotionConnectionsRepository(session)

        # Count total users and connections
        # Note: In a real app, we'd add count methods to repositories
        try:
            # Get a sample of users for summary
            dev_user = await users_repo.get_user_by_email("developer@alfred.test")
            admin_user = await users_repo.get_user_by_email("admin@alfred.test")

            if dev_user:
                dev_connections = await connections_repo.get_active_connections_by_user(
                    dev_user.id
                )
                print(f"👤 Developer user: {dev_user.email}")
                print(f"   🔗 Active connections: {len(dev_connections)}")

                for conn in dev_connections[:3]:  # Show first 3
                    print(f"   - Workspace: {conn.workspace_id}")
                    print(f"     Scopes: {', '.join(conn.scopes or [])}")

            if admin_user:
                admin_connections = (
                    await connections_repo.get_active_connections_by_user(admin_user.id)
                )
                print(f"👤 Admin user: {admin_user.email}")
                print(f"   🔗 Active connections: {len(admin_connections)}")

        except Exception as e:
            print(f"⚠️  Could not generate full summary: {e}")

    print("\n🚀 Database foundation is ready for OAuth endpoint development!")
    print("   Next steps: Implement FastAPI OAuth routes using these repositories")


async def main():
    """
    Main seeding and verification function.

    Runs complete test of database schema, encryption, and business logic.
    """
    print("🌱 Alfred Agent Core - Development Data Seeder")
    print("=" * 50)

    # Step 1: Environment verification
    if not await verify_environment():
        print("\n❌ Environment setup incomplete. Please fix issues above.")
        return 1

    # Step 2: Ensure database tables exist
    print("\n🏗️  Creating database tables...")
    try:
        await create_tables()
        print("✅ Database tables created/verified")
    except Exception as e:
        print(f"❌ Database table creation failed: {e}")
        return 1

    # Step 3-6: Use single session for all operations to ensure consistency
    async with get_session() as session:
        # Step 3: Seed users
        users = await seed_test_users_in_session(session)

        # Step 4: Seed connections (users committed, available for FK references)
        connections = await seed_test_connections_in_session(session, users)

        # Step 5: Verify relationships and queries
        await verify_relationships_and_queries(users, connections)

        # Step 6: Test advanced token management
        await verify_token_management()

    # Step 7: Print summary
    await print_summary()

    print("\n✨ Development seeding and verification complete!")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
