"""
Database connection and session management for Alfred Agent Core.

This module is the SINGLE source of truth for all database operations:
- Engine and session factory management (singletons)
- FastAPI dependency injection for sessions
- Application lifecycle hooks (startup/shutdown)
- PostgreSQL advisory locks for cross-process coordination
- Health checks and observability
- Test support with transactional isolation

All other modules should import database functions from here or from
src.db.__init__, never directly from session.py.
"""

import hashlib
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from uuid import UUID

import structlog
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from ..config import Settings, get_settings

logger = structlog.get_logger(__name__)

# Global singletons
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None
_advisory_locks_supported: Optional[bool] = None  # Memoized support check


def get_database_url(settings: Settings) -> str:
    """
    Get and validate database URL from settings.

    Converts postgresql:// to postgresql+psycopg:// for async operations.

    Args:
        settings: Application settings

    Returns:
        Async-compatible database URL

    Raises:
        ValueError: If database URL is missing or invalid
    """
    database_url = settings.database_url

    if not database_url:
        raise ValueError(
            "DATABASE_URL environment variable is required. "
            "Format: postgresql+psycopg://user:pass@host:port/dbname"
        )

    # Convert PostgresDsn to string
    database_url_str = str(database_url)

    # Convert sync URL to async URL if needed
    if "postgresql://" in database_url_str:
        database_url_str = database_url_str.replace(
            "postgresql://", "postgresql+psycopg://"
        )
    elif not database_url_str.startswith("postgresql+psycopg://"):
        raise ValueError(
            "DATABASE_URL must use postgresql+psycopg:// driver for async operations. "
            "SQLite is not supported due to PostgreSQL-specific features (CITEXT, advisory locks, etc.)"
        )

    return database_url_str


def get_engine(settings: Optional[Settings] = None) -> AsyncEngine:
    """
    Get or create the global async SQLAlchemy engine.

    Uses connection pooling in production, NullPool in development/test.
    Configures per-connection settings for timezone, timeouts, and application name.

    Args:
        settings: Optional settings override (defaults to global settings)

    Returns:
        AsyncEngine singleton
    """
    global _engine

    if _engine is None:
        if settings is None:
            settings = get_settings()

        database_url = get_database_url(settings)

        # Configure pooling based on environment
        if settings.app_env == "production":
            # Production: Use connection pooling (let SQLAlchemy choose the pool class)
            pool_kwargs = {
                "pool_size": settings.database_pool_size,
                "max_overflow": 10,
                "pool_timeout": settings.database_pool_timeout,
                "pool_recycle": 1800,  # Recycle connections after 30 minutes
                "pool_pre_ping": True,  # Test connections before using (important!)
            }
        else:
            # Development/Test: No pooling for easier debugging
            pool_kwargs = {"poolclass": NullPool}

        # Create engine with proper connect args for PostgreSQL
        _engine = create_async_engine(
            database_url,
            echo=settings.log_level == "DEBUG",  # SQL logging in debug mode
            future=True,
            connect_args={
                # psycopg3 uses options parameter for server settings
                "options": (
                    f"-c application_name={settings.app_name.replace(' ', '_')}-{settings.app_version} "
                    "-c TimeZone=UTC "
                    "-c lock_timeout=5000 "  # 5 second lock timeout
                    "-c idle_in_transaction_session_timeout=60000 "  # 60 seconds
                    "-c statement_timeout=60000"  # 60 seconds
                ),
                # Connection timeout (TCP handshake)
                "connect_timeout": 5,  # 5 second connection timeout
            },
            **pool_kwargs,
        )

        # Register event to set session defaults on every new connection
        @event.listens_for(_engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_conn, connection_record):
            """Set connection-level defaults for each new connection."""
            # This is called for each new connection in the pool
            # Additional settings can be added here if needed
            pass

        logger.info(
            "Database engine created",
            pool_class=pool_kwargs.get("poolclass", AsyncAdaptedQueuePool).__name__
            if "poolclass" in pool_kwargs
            else "AsyncAdaptedQueuePool",
            database_url=database_url.split("@")[0] + "@***",  # Hide credentials
        )

    return _engine


def get_session_factory(
    settings: Optional[Settings] = None,
) -> async_sessionmaker[AsyncSession]:
    """
    Get or create the global async session factory.

    Args:
        settings: Optional settings override

    Returns:
        async_sessionmaker singleton
    """
    global _session_factory

    if _session_factory is None:
        engine = get_engine(settings)
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,  # Don't expire objects after commit
        )
        logger.info("Session factory created")

    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get async database session for FastAPI dependency injection.

    This is the PRIMARY way to get database sessions in the application.
    Does NOT auto-commit - caller must explicitly commit when needed.
    Ensures proper rollback on exceptions and cleanup.

    Yields:
        AsyncSession for database operations (caller controls commit)
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Compatibility aliases for existing code
get_db = get_async_session
get_db_session = get_async_session


# PostgreSQL Advisory Locks for Single-Flight Pattern
def advisory_key_from_uuid(connection_id: UUID, namespace: str = "oauth") -> int:
    """
    Convert UUID to stable 32-bit integer for advisory lock.

    Uses first 4 bytes of MD5 hash for consistent mapping.
    Includes namespace to avoid collisions between different lock uses.

    Args:
        connection_id: UUID to convert
        namespace: Lock namespace (default: "oauth")

    Returns:
        32-bit integer key for pg_advisory_lock functions
    """
    # Hash the namespace + UUID to get consistent integer
    combined = f"{namespace}:{connection_id}"
    hash_bytes = hashlib.md5(combined.encode()).digest()
    # Take first 4 bytes as signed 32-bit integer
    return int.from_bytes(hash_bytes[:4], byteorder="big", signed=True)


async def try_advisory_lock(
    session: AsyncSession, connection_id: UUID, namespace: str = "oauth"
) -> bool:
    """
    Try to acquire a PostgreSQL advisory lock for a connection.

    Uses transaction-scoped locks that auto-release on commit/rollback.
    This prevents multiple processes from refreshing the same token.

    Args:
        session: Active database session (must be in transaction)
        connection_id: Connection UUID to lock
        namespace: Lock namespace for different use cases

    Returns:
        True if lock acquired, False if another process has it
    """
    global _advisory_locks_supported

    # Check if we've already determined advisory locks aren't supported
    if _advisory_locks_supported is False:
        return True  # Always succeed if not supported

    lock_key = advisory_key_from_uuid(connection_id, namespace)

    try:
        result = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key}
        )
        locked = result.scalar()

        if locked:
            logger.debug(
                "Advisory lock acquired",
                connection_id=str(connection_id),
                namespace=namespace,
                lock_key=lock_key,
            )
        else:
            logger.debug(
                "Advisory lock unavailable (another process has it)",
                connection_id=str(connection_id),
                namespace=namespace,
                lock_key=lock_key,
            )

        # Memoize that advisory locks are supported
        if _advisory_locks_supported is None:
            _advisory_locks_supported = True

        return bool(locked)

    except Exception as e:
        error_msg = str(e).lower()
        # Check if the error is because the function doesn't exist
        if "function" in error_msg and "does not exist" in error_msg:
            logger.info("Advisory locks not supported (non-PostgreSQL database)")
            _advisory_locks_supported = False
            return True  # Always succeed if not supported

        logger.error("Advisory lock error", error=str(e))
        return False


# Statement timeout helper for hot paths
async def with_statement_timeout(session: AsyncSession, timeout_ms: int):
    """
    Set a statement timeout for the current transaction.

    Useful for hot paths like health checks and refresh sweeps
    to guard against slow queries.

    Args:
        session: Active database session
        timeout_ms: Timeout in milliseconds
    """
    await session.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))


# Application Lifecycle Hooks
async def on_startup():
    """
    Initialize database on application startup.

    Called by FastAPI app startup event.
    Creates engine and prepares for connections.
    Extensions should be created via Alembic migrations, not here.
    """
    settings = get_settings()
    engine = get_engine(settings)

    logger.info("Database initialized", engine_url=str(engine.url).split("@")[0])


async def on_shutdown():
    """
    Clean up database connections on application shutdown.

    Called by FastAPI app shutdown event.
    Properly disposes of engine and connection pool.
    """
    global _engine, _session_factory, _advisory_locks_supported

    if _engine:
        await _engine.dispose()
        logger.info("Database engine disposed")
        _engine = None
        _session_factory = None
        _advisory_locks_supported = None


# Health & Observability
async def ping() -> float:
    """
    Test database connectivity and measure latency.

    Returns:
        Response time in milliseconds

    Raises:
        Exception if database is unreachable
    """
    import time

    engine = get_engine()
    start = time.time()

    async with engine.connect() as conn:
        # Simple SELECT 1, no commit needed
        await conn.execute(text("SELECT 1"))

    latency_ms = (time.time() - start) * 1000
    return latency_ms


async def get_pool_stats() -> dict:
    """
    Get connection pool statistics for monitoring.

    Returns:
        Dict with pool metrics (size, checked_in, checked_out, overflow, total)
    """
    engine = get_engine()
    pool = engine.pool

    # NullPool doesn't have stats
    if isinstance(pool, NullPool) or pool is None:
        return {"pool_type": "NullPool", "stats_available": False}

    # Use supported pool attributes
    size = pool.size()
    checked_out = pool.checkedout()
    overflow = pool.overflow()
    checked_in = max(0, size - checked_out)
    total = size + overflow

    return {
        "pool_type": pool.__class__.__name__,
        "size": size,
        "checked_in": checked_in,
        "checked_out": checked_out,
        "overflow": overflow,
        "total": total,
    }


# Test Support
@asynccontextmanager
async def get_test_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a test database session with transactional isolation.

    Each test runs in a transaction that's rolled back after the test,
    providing fast test isolation without recreating the database.

    Note: Requires PostgreSQL. SQLite is not supported due to
    PostgreSQL-specific features in the schema.

    Yields:
        AsyncSession wrapped in a rollback-only transaction
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        # Start outer transaction
        async with session.begin():
            # Start a savepoint
            nested = await session.begin_nested()

            try:
                yield session
            finally:
                # Always rollback the savepoint
                await nested.rollback()
                # The outer transaction will also be rolled back


# Migration Support
async def create_tables():
    """
    Create all database tables (for testing/development).

    Should not be used in production - use Alembic migrations instead.
    """
    from .models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables created")


async def drop_tables():
    """
    Drop all database tables (for testing/development).

    WARNING: Destructive operation - only for test cleanup.
    """
    from .models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    logger.info("Database tables dropped")


# Unit of Work helper for services that need auto-commit
@asynccontextmanager
async def with_unit_of_work():
    """
    Provide a session with automatic commit on success.

    Use this in services/routes that should auto-commit their changes.
    The base get_async_session dependency does NOT auto-commit.

    Yields:
        AsyncSession that commits on successful exit
    """
    session_factory = get_session_factory()

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Public exports
__all__ = [
    # Primary session management
    "get_async_session",
    "get_db",
    "get_db_session",
    "with_unit_of_work",
    # Engine and factory
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
]
