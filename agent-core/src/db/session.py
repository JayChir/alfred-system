"""
Database session management for Alfred Agent Core.

This module provides async database session management with SQLAlchemy,
connection pooling, and transaction management for the agent core.
"""

import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .models import Base


class DatabaseConfig:
    """Database configuration from environment variables."""

    def __init__(self):
        """Initialize database configuration from environment."""
        self.database_url = os.getenv("DB_URL")
        if not self.database_url:
            raise ValueError(
                "DB_URL environment variable is required. "
                "Format: postgresql+psycopg://user:pass@host:port/dbname"
            )

        # Convert sync URL to async URL if needed
        if "postgresql://" in self.database_url:
            self.database_url = self.database_url.replace(
                "postgresql://", "postgresql+psycopg://"
            )
        elif not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError(
                "DB_URL must use postgresql+psycopg:// driver for async operations"
            )


# Global database configuration
_config = None
_engine = None
_session_factory = None


def get_database_config() -> DatabaseConfig:
    """Get global database configuration."""
    global _config
    if _config is None:
        _config = DatabaseConfig()
    return _config


def get_async_engine():
    """Get or create async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        config = get_database_config()
        _engine = create_async_engine(
            config.database_url,
            poolclass=NullPool,  # Use NullPool for development simplicity
            echo=False,  # Set to True for SQL query logging
            future=True,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create async session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_async_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get async database session for dependency injection.

    This function provides database sessions for FastAPI dependency injection
    and ensures proper session cleanup.

    Yields:
        AsyncSession: Database session
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


async def create_tables():
    """Create all database tables (for testing/development)."""
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_tables():
    """Drop all database tables (for testing/development)."""
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def close_database():
    """Close database connections (for application shutdown)."""
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None
