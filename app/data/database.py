"""Async SQLAlchemy 2.0 engine, session factory, and declarative ``Base``.

The engine is created lazily from the environment-driven ``database_url`` so the
declarative models can be imported without requiring a live database connection
(useful for tests, Alembic autogeneration, and import checks).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """Create (once) and return the async engine from settings."""

    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_pre_ping=True,
        future=True,
    )


@lru_cache(maxsize=1)
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return a cached async session factory bound to the engine."""

    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-style dependency yielding an :class:`AsyncSession`."""

    factory = get_sessionmaker()
    async with factory() as session:
        yield session
