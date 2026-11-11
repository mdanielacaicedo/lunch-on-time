"""Database connection and table initialisation for menu-ms.

Uses async SQLAlchemy with asyncpg for PostgreSQL.
Falls back to aiosqlite for testing when DATABASE_URL points to SQLite.

The engine and session factory are created lazily so that tests can
override DATABASE_URL (or inject their own session) before any import
tries to connect to PostgreSQL.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base

_engine: Optional[AsyncEngine] = None
_async_session: Optional[async_sessionmaker] = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/menu",
        )
        _engine = create_async_engine(url, echo=False)
    return _engine


def _get_session_factory() -> async_sessionmaker:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            _get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session


async def init_db() -> None:
    """Create all tables if they do not exist."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    """FastAPI dependency that yields an async session."""
    factory = _get_session_factory()
    async with factory() as session:
        yield session
