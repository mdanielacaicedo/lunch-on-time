"""Database connection and initialisation for orders-ms."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base, Config

logger = logging.getLogger(__name__)

ORDERS_INIT_LOCK_ID = 819202

_engine: Optional[AsyncEngine] = None
_async_session: Optional[async_sessionmaker] = None


def _read_password() -> str:
    """Read database password from DB_PASSWORD_FILE if present, else DB_PASSWORD."""
    secret_file = os.getenv("DB_PASSWORD_FILE")
    if secret_file and os.path.exists(secret_file):
        try:
            with open(secret_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            logger.warning("Could not read password file %s: %s", secret_file, e)
    return os.getenv("DB_PASSWORD", "postgres").strip()


def get_database_url() -> str:
    """Construct database URL supporting secrets and environment variables."""
    if "DATABASE_URL" in os.environ:
        url = os.environ["DATABASE_URL"]
        secret_file = os.getenv("DB_PASSWORD_FILE")
        if secret_file and os.path.exists(secret_file) and "postgresql" in url:
            from sqlalchemy.engine import make_url

            parsed = make_url(url)
            parsed = parsed.set(password=_read_password())
            return parsed.render_as_string(hide_password=False)
        return url

    user = os.getenv("DB_USER", "postgres")
    password = _read_password()
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    db = os.getenv("DB_NAME", "orders")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = get_database_url()
        _engine = create_async_engine(
            url, echo=False, pool_pre_ping=True, pool_recycle=300
        )
    return _engine


def get_session_factory() -> async_sessionmaker:
    global _async_session
    if _async_session is None:
        _async_session = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _async_session


async def init_db(max_retries: int = 15, delay: float = 2.0) -> None:
    """Create all tables and seed default configuration with retries and advisory lock."""
    engine = get_engine()

    # Connection retry loop for cold starts
    for attempt in range(1, max_retries + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                break
        except Exception as e:
            if attempt == max_retries:
                logger.error("Failed to connect to database after %d attempts: %s", max_retries, e)
                raise
            logger.warning(
                "Database not ready yet (attempt %d/%d): %s. Retrying in %ss...",
                attempt,
                max_retries,
                e,
                delay,
            )
            await asyncio.sleep(delay)

    # Schema initialization and default config seeding with advisory lock on PostgreSQL
    async with engine.connect() as conn:
        is_postgres = conn.dialect.name == "postgresql"
        if is_postgres:
            await conn.execute(text(f"SELECT pg_advisory_lock({ORDERS_INIT_LOCK_ID})"))
        try:
            await conn.run_sync(Base.metadata.create_all)
            if is_postgres:
                await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;"))
            await conn.commit()

            # Seed default configs inside the advisory lock
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
        finally:
            if is_postgres:
                await conn.execute(text(f"SELECT pg_advisory_unlock({ORDERS_INIT_LOCK_ID})"))
                await conn.commit()


async def get_db():
    """FastAPI dependency yielding an async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session
