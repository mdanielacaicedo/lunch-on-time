"""Database connection and initialisation for orders-ms."""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, Config

_engine: Optional[AsyncEngine] = None
_async_session: Optional[async_sessionmaker] = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5433/orders",
        )
        _engine = create_async_engine(url, echo=False)
    return _engine


def get_session_factory() -> async_sessionmaker:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session


async def init_db() -> None:
    """Create all tables and seed default configuration if not present."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed default configs
    factory = get_session_factory()
    async with factory() as session:
        default_configs = {
            "slot_capacity": "10",
            "start_time": "11:30",
            "end_time": "14:00",
        }
        for key, value in default_configs.items():
            existing = await session.execute(select(Config).where(Config.key == key))
            if existing.scalars().first() is None:
                session.add(Config(key=key, value=value))
        await session.commit()


async def get_db():
    """FastAPI dependency yielding an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
