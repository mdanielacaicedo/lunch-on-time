"""Pytest fixtures for orders-ms.

Runs tests against real PostgreSQL in a dedicated orders_test database (orders-db at localhost:5433).
Never touches the development 'orders' database to prevent data deletion.
Uses NullPool to avoid asyncpg connection leaking across event loops.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time
from typing import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.db import get_db
from app.main import BOGOTA_TZ, app
from app.models import Base, Config, Order, OrderItem


def get_test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        base = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5433/orders_test",
        )
        if base.endswith("/orders"):
            url = base[:-7] + "/orders_test"
        elif "/orders?" in base:
            url = base.replace("/orders?", "/orders_test?")
        else:
            url = base
    if url.endswith("/orders") or "/orders?" in url:
        url = url.replace("/orders", "/orders_test")
    return url


TEST_DATABASE_URL = get_test_database_url()


async def ensure_test_database_exists(target_url: str) -> None:
    """Ensure orders_test database exists, creating it if needed."""
    admin_url = re.sub(r"/([^/?]+)(\?.*)?$", r"/postgres\2", target_url)
    db_name_match = re.search(r"/([^/?]+)(\?.*)?$", target_url)
    if not db_name_match:
        return
    db_name = db_name_match.group(1)

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    try:
        async with admin_engine.connect() as conn:
            res = await conn.execute(text(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'"))
            if not res.scalar():
                await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    finally:
        await admin_engine.dispose()


_test_db_ensured = False


@pytest.fixture(autouse=True)
def default_bogota_time():
    """Default simulated time to 11:00 AM Bogota time today so standard lunch slots (11:30-14:00) are valid."""
    today = date.today()
    default_now = datetime.combine(today, time(11, 0), tzinfo=BOGOTA_TZ)
    with patch("app.main.get_now_bogota", return_value=default_now):
        yield default_now


@pytest_asyncio.fixture()
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a fresh DB session for tests and clean up tables."""
    global _test_db_ensured
    if not _test_db_ensured:
        await ensure_test_database_exists(TEST_DATABASE_URL)
        _test_db_ensured = True

    assert "orders_test" in TEST_DATABASE_URL, f"Tests must only run against orders_test, got: {TEST_DATABASE_URL}"
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivered_at TIMESTAMPTZ;"))

    async with session_factory() as session:
        await session.execute(delete(OrderItem))
        await session.execute(delete(Order))

        default_configs = {
            "slot_capacity": "25",
            "start_time": "11:30",
            "end_time": "14:00",
        }
        for key, value in default_configs.items():
            cfg = await session.get(Config, key)
            if cfg is None:
                session.add(Config(key=key, value=value))
            else:
                cfg.value = value
        await session.commit()

        yield session

        await session.execute(delete(OrderItem))
        await session.execute(delete(Order))
        await session.commit()

    await engine.dispose()


@pytest_asyncio.fixture()
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client wired to FastAPI with real PostgreSQL session."""
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    await engine.dispose()
