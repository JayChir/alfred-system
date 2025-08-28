"""
Repository layer for Alfred Agent Core database operations.

This module provides repository classes that encapsulate database access patterns
and provide clean async interfaces for database operations. Repositories handle:
- CRUD operations for User and NotionConnection entities
- Encryption/decryption of sensitive data
- Query optimization and relationship management
- Transaction management and error handling

Security: All OAuth tokens are automatically encrypted/decrypted by repositories.
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..utils.crypto import CryptoServiceError, get_crypto_service
from .models import NotionConnection, User


class RepositoryError(Exception):
    """Base exception for repository operations."""

    pass


class UserNotFoundError(RepositoryError):
    """Raised when a user is not found."""

    pass


class ConnectionNotFoundError(RepositoryError):
    """Raised when a connection is not found."""

    pass


class DuplicateConnectionError(RepositoryError):
    """Raised when attempting to create a duplicate connection."""

    pass


class UsersRepository:
    """
    Repository for User entity operations.

    Provides async database operations for user management including:
    - User creation and retrieval
    - Email-based user lookup
    - User status management
    - Relationship loading (connections)
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session for database operations
        """
        self.session = session

    async def create_user(self, email: str, status: str = "active") -> User:
        """
        Create a new user.

        Args:
            email: User's email address (case-insensitive)
            status: User status (default: "active")

        Returns:
            Created User instance

        Raises:
            RepositoryError: If user creation fails (e.g., duplicate email)
        """
        try:
            user = User(
                id=uuid.uuid4(),
                email=email.lower().strip(),  # Normalize email
                status=status,
            )

            self.session.add(user)
            await self.session.flush()  # Get the ID without committing
            await self.session.refresh(user)  # Refresh with default values

            return user

        except IntegrityError as e:
            await self.session.rollback()
            # Check for unique constraint violation by SQLSTATE code (23505)
            if hasattr(e.orig, "sqlstate") and e.orig.sqlstate == "23505":
                raise RepositoryError(f"User with email {email} already exists") from e
            raise RepositoryError(f"Failed to create user: {e}") from e
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(f"Database error creating user: {e}") from e

    async def get_user_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        """
        Get user by ID.

        Args:
            user_id: UUID of the user

        Returns:
            User instance if found, None otherwise
        """
        try:
            result = await self.session.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            raise RepositoryError(f"Database error getting user: {e}") from e

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """
        Get user by email address (case-insensitive).

        Args:
            email: User's email address

        Returns:
            User instance if found, None otherwise
        """
        try:
            result = await self.session.execute(
                select(User).where(User.email == email.lower().strip())
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            raise RepositoryError(f"Database error getting user by email: {e}") from e

    async def get_user_with_connections(self, user_id: uuid.UUID) -> Optional[User]:
        """
        Get user with all their notion connections loaded.

        Args:
            user_id: UUID of the user

        Returns:
            User instance with connections loaded, None if not found
        """
        try:
            result = await self.session.execute(
                select(User)
                .where(User.id == user_id)
                .options(selectinload(User.notion_connections))
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"Database error getting user with connections: {e}"
            ) from e

    async def create_or_get_user(self, email: str) -> User:
        """
        Get existing user or create new one if not exists.

        Args:
            email: User's email address

        Returns:
            User instance (existing or newly created)
        """
        # Try to get existing user first
        user = await self.get_user_by_email(email)
        if user:
            return user

        # Create new user if not found
        return await self.create_user(email)

    async def update_user_status(self, user_id: uuid.UUID, status: str) -> bool:
        """
        Update user status.

        Args:
            user_id: UUID of the user
            status: New status (active, inactive, suspended)

        Returns:
            True if user was updated, False if not found
        """
        try:
            result = await self.session.execute(
                update(User)
                .where(User.id == user_id)
                .values(status=status, updated_at=datetime.now(timezone.utc))
            )
            return result.rowcount > 0
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(f"Database error updating user status: {e}") from e


class NotionConnectionsRepository:
    """
    Repository for NotionConnection entity operations.

    Handles OAuth connection management with automatic token encryption/decryption:
    - Connection creation with encrypted token storage
    - Active connection retrieval and filtering
    - Token refresh and expiration management
    - Connection revocation and cleanup
    """

    def __init__(self, session: AsyncSession):
        """
        Initialize repository with database session.

        Args:
            session: Async SQLAlchemy session for database operations
        """
        self.session = session
        self.crypto = get_crypto_service()

    async def create_connection(
        self,
        user_id: uuid.UUID,
        workspace_id: str,
        access_token: str,
        provider: str = "notion",
        bot_id: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        refresh_token: Optional[str] = None,
        access_token_expires_at: Optional[datetime] = None,
        refresh_token_expires_at: Optional[datetime] = None,
    ) -> NotionConnection:
        """
        Create a new OAuth connection with encrypted token storage.

        Args:
            user_id: UUID of the user
            workspace_id: Notion workspace ID
            access_token: OAuth access token (will be encrypted)
            provider: OAuth provider (default: "notion")
            bot_id: Notion bot ID (if available)
            scopes: List of granted scopes
            refresh_token: OAuth refresh token (will be encrypted if provided)
            access_token_expires_at: Access token expiration
            refresh_token_expires_at: Refresh token expiration

        Returns:
            Created NotionConnection instance

        Raises:
            DuplicateConnectionError: If connection already exists
            RepositoryError: If creation fails
        """
        try:
            # Encrypt tokens with MultiFernet (no key_version needed)
            access_token_ciphertext = self.crypto.encrypt_token(access_token)

            refresh_token_ciphertext = None
            if refresh_token:
                refresh_token_ciphertext = self.crypto.encrypt_token(refresh_token)

            connection = NotionConnection(
                id=uuid.uuid4(),
                user_id=user_id,
                provider=provider,
                workspace_id=workspace_id,
                bot_id=bot_id,
                scopes=scopes,
                access_token_ciphertext=access_token_ciphertext,
                refresh_token_ciphertext=refresh_token_ciphertext,
                access_token_expires_at=access_token_expires_at,
                refresh_token_expires_at=refresh_token_expires_at,
                key_version=1,  # Default version for compatibility (MultiFernet handles rotation internally)
            )

            self.session.add(connection)
            await self.session.flush()
            await self.session.refresh(connection)

            return connection

        except IntegrityError as e:
            await self.session.rollback()
            # Check for unique constraint violation by SQLSTATE code (23505)
            if hasattr(e.orig, "sqlstate") and e.orig.sqlstate == "23505":
                raise DuplicateConnectionError(
                    f"Connection already exists for user {user_id} "
                    f"to workspace {workspace_id}"
                ) from e
            raise RepositoryError(f"Failed to create connection: {e}") from e
        except CryptoServiceError as e:
            await self.session.rollback()
            raise RepositoryError(f"Token encryption failed: {e}") from e
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(f"Database error creating connection: {e}") from e

    async def get_connection_by_id(
        self, connection_id: uuid.UUID
    ) -> Optional[NotionConnection]:
        """
        Get connection by ID.

        Args:
            connection_id: UUID of the connection

        Returns:
            NotionConnection instance if found, None otherwise
        """
        try:
            result = await self.session.execute(
                select(NotionConnection).where(NotionConnection.id == connection_id)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            raise RepositoryError(f"Database error getting connection: {e}") from e

    async def get_active_connections_by_user(
        self, user_id: uuid.UUID
    ) -> List[NotionConnection]:
        """
        Get all active connections for a user.

        Args:
            user_id: UUID of the user

        Returns:
            List of active NotionConnection instances
        """
        try:
            result = await self.session.execute(
                select(NotionConnection)
                .where(NotionConnection.user_id == user_id)
                .where(NotionConnection.revoked_at.is_(None))
                .order_by(NotionConnection.created_at.desc())
            )
            return list(result.scalars().all())
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"Database error getting active connections: {e}"
            ) from e

    async def get_connection_for_workspace(
        self, user_id: uuid.UUID, workspace_id: str
    ) -> Optional[NotionConnection]:
        """
        Get active connection for specific user and workspace.

        Args:
            user_id: UUID of the user
            workspace_id: Notion workspace ID

        Returns:
            Active NotionConnection if found, None otherwise
        """
        try:
            result = await self.session.execute(
                select(NotionConnection)
                .where(NotionConnection.user_id == user_id)
                .where(NotionConnection.workspace_id == workspace_id)
                .where(NotionConnection.revoked_at.is_(None))
                .order_by(NotionConnection.created_at.desc())
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            raise RepositoryError(
                f"Database error getting workspace connection: {e}"
            ) from e

    async def decrypt_access_token(self, connection: NotionConnection) -> str:
        """
        Decrypt the access token for a connection using MultiFernet.

        Args:
            connection: NotionConnection instance

        Returns:
            Decrypted access token

        Raises:
            RepositoryError: If decryption fails
        """
        try:
            # MultiFernet automatically tries all available keys
            return self.crypto.decrypt_token(connection.access_token_ciphertext)
        except CryptoServiceError as e:
            raise RepositoryError(f"Failed to decrypt access token: {e}") from e

    async def decrypt_refresh_token(
        self, connection: NotionConnection
    ) -> Optional[str]:
        """
        Decrypt the refresh token for a connection using MultiFernet.

        Args:
            connection: NotionConnection instance

        Returns:
            Decrypted refresh token if available, None otherwise

        Raises:
            RepositoryError: If decryption fails
        """
        if not connection.refresh_token_ciphertext:
            return None

        try:
            # MultiFernet automatically tries all available keys
            return self.crypto.decrypt_token(connection.refresh_token_ciphertext)
        except CryptoServiceError as e:
            raise RepositoryError(f"Failed to decrypt refresh token: {e}") from e

    async def update_tokens(
        self,
        connection_id: uuid.UUID,
        access_token: str,
        refresh_token: Optional[str] = None,
        access_token_expires_at: Optional[datetime] = None,
        refresh_token_expires_at: Optional[datetime] = None,
    ) -> bool:
        """
        Update connection tokens (e.g., after refresh).

        Args:
            connection_id: UUID of the connection
            access_token: New access token
            refresh_token: New refresh token (if available)
            access_token_expires_at: New access token expiration
            refresh_token_expires_at: New refresh token expiration

        Returns:
            True if connection was updated, False if not found

        Raises:
            RepositoryError: If update fails
        """
        try:
            # Encrypt new tokens with MultiFernet
            access_token_ciphertext = self.crypto.encrypt_token(access_token)

            refresh_token_ciphertext = None
            if refresh_token:
                refresh_token_ciphertext = self.crypto.encrypt_token(refresh_token)

            # Update connection (key_version stays same as MultiFernet handles rotation internally)
            result = await self.session.execute(
                update(NotionConnection)
                .where(NotionConnection.id == connection_id)
                .values(
                    access_token_ciphertext=access_token_ciphertext,
                    refresh_token_ciphertext=refresh_token_ciphertext,
                    access_token_expires_at=access_token_expires_at,
                    refresh_token_expires_at=refresh_token_expires_at,
                    updated_at=datetime.now(timezone.utc),
                )
            )

            return result.rowcount > 0

        except CryptoServiceError as e:
            await self.session.rollback()
            raise RepositoryError(f"Token encryption failed: {e}") from e
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(f"Database error updating tokens: {e}") from e

    async def revoke_connection(self, connection_id: uuid.UUID) -> bool:
        """
        Mark a connection as revoked.

        Args:
            connection_id: UUID of the connection to revoke

        Returns:
            True if connection was revoked, False if not found
        """
        try:
            result = await self.session.execute(
                update(NotionConnection)
                .where(NotionConnection.id == connection_id)
                .values(
                    revoked_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            return result.rowcount > 0
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(f"Database error revoking connection: {e}") from e

    async def delete_connection(self, connection_id: uuid.UUID) -> bool:
        """
        Permanently delete a connection.

        Args:
            connection_id: UUID of the connection to delete

        Returns:
            True if connection was deleted, False if not found
        """
        try:
            result = await self.session.execute(
                delete(NotionConnection).where(NotionConnection.id == connection_id)
            )
            return result.rowcount > 0
        except SQLAlchemyError as e:
            await self.session.rollback()
            raise RepositoryError(f"Database error deleting connection: {e}") from e


# Factory functions for dependency injection


async def create_users_repository(session: AsyncSession) -> UsersRepository:
    """Create UsersRepository instance with database session."""
    return UsersRepository(session)


async def create_notion_connections_repository(
    session: AsyncSession,
) -> NotionConnectionsRepository:
    """Create NotionConnectionsRepository instance with database session."""
    return NotionConnectionsRepository(session)
