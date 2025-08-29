"""
Database module for Alfred Agent Core.

Single import point for all database functionality.
All other modules should import from here, not from individual files.
"""

from .database import (  # Advisory locks for single-flight; Primary session management; Engine and factory; Test support; Lifecycle hooks; Health & monitoring; Helpers
    advisory_key_from_uuid,
    create_tables,
    drop_tables,
    get_async_session,
    get_db,
    get_db_session,
    get_engine,
    get_pool_stats,
    get_session_factory,
    get_test_session,
    on_shutdown,
    on_startup,
    ping,
    try_advisory_lock,
    with_statement_timeout,
    with_unit_of_work,
)
from .models import Base, NotionConnection, OAuthState, User
from .repositories import (
    ConnectionNotFoundError,
    DuplicateConnectionError,
    NotionConnectionsRepository,
    RepositoryError,
    UserNotFoundError,
    UsersRepository,
    create_notion_connections_repository,
    create_users_repository,
)

__all__ = [
    # Session management
    "get_async_session",
    "get_db",
    "get_db_session",
    "with_unit_of_work",
    "get_engine",
    "get_session_factory",
    # Advisory locks
    "advisory_key_from_uuid",
    "try_advisory_lock",
    # Helpers
    "with_statement_timeout",
    # Lifecycle
    "on_startup",
    "on_shutdown",
    # Health
    "ping",
    "get_pool_stats",
    # Testing
    "get_test_session",
    "create_tables",
    "drop_tables",
    # Models
    "Base",
    "User",
    "NotionConnection",
    "OAuthState",
    # Repositories
    "UsersRepository",
    "NotionConnectionsRepository",
    "create_users_repository",
    "create_notion_connections_repository",
    "RepositoryError",
    "UserNotFoundError",
    "ConnectionNotFoundError",
    "DuplicateConnectionError",
]

