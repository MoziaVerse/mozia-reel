"""Async engine and session factory configuration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Suppress noisy pool/connection errors caused by SSE task cancellation.
# When an SSE client disconnects, Starlette cancels the response task.
# aiosqlite connections that are being returned to the pool at that moment
# fail with CancelledError or "no active connection" during rollback.
# These are harmless — the connection was going to be discarded anyway.
logging.getLogger("sqlalchemy.pool.impl").setLevel(logging.CRITICAL)


def get_database_url() -> str:
    """Resolve DATABASE_URL from environment or default to SQLite."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    from lib.app_data_dir import app_data_dir

    db_path = app_data_dir() / ".arcreel.db"
    return f"sqlite+aiosqlite:///{db_path}"


def is_sqlite_backend() -> bool:
    """Check whether the configured backend is SQLite."""
    return get_database_url().startswith("sqlite")


def _create_engine():
    url = get_database_url()
    _is_sqlite = url.startswith("sqlite")

    connect_args = {}
    kwargs = {}
    if _is_sqlite:
        connect_args["timeout"] = 30
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=3600)

    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
        **kwargs,
    )

    if _is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


# ── 租户感知的 engine / session factory ──────────────────────────
#
# 31 个模块直接 `from lib.db import async_session_factory` 之类，import 时就把
# 对象绑死了 —— 想按租户切库，要么改这 31 处，要么让这几个名字本身变成代理。
# 选后者：它们的用法只有"调用"（`async_session_factory()`）和"取属性"
# （`async_engine.sync_engine`），代理能完整覆盖，改动收敛在本文件内。

_engines: dict[str | None, object] = {}
_factories: dict[str | None, async_sessionmaker] = {}


def _tenant_key() -> str | None:
    """当前租户；DATABASE_URL 显式配置时恒为 None。

    显式配了 DATABASE_URL 意味着指向一个外部库（PostgreSQL 等），此时
    app_data_dir() 不参与选库，按租户分裂 engine 只会建出一堆连同一个库的
    连接池 —— 反而掩盖了"这套部署还没做租户隔离"的事实。
    """
    if os.environ.get("DATABASE_URL", "").strip():
        return None
    from lib.tenant_context import current_tenant

    return current_tenant()


def get_engine():
    """当前租户的 engine（按租户缓存）。"""
    key = _tenant_key()
    engine = _engines.get(key)
    if engine is None:
        engine = _create_engine()
        _engines[key] = engine
    return engine


def get_session_factory() -> async_sessionmaker:
    """当前租户的 session factory（按租户缓存）。"""
    key = _tenant_key()
    factory = _factories.get(key)
    if factory is None:
        factory = async_sessionmaker(get_engine(), expire_on_commit=False)
        _factories[key] = factory
    return factory


def all_engines() -> list:
    """已建立的全部 engine，供 dispose 一类的全局操作遍历。"""
    return list(_engines.values())


class _TenantEngineProxy:
    """把属性访问转发到当前租户的 engine。

    存在的理由：`async_engine.connect()` / `.sync_engine` / `.dispose()` 这些
    调用散布在 31 个模块里，代理让它们无需改写就跟着租户走。
    """

    def __getattr__(self, name: str):
        return getattr(get_engine(), name)

    def __repr__(self) -> str:
        return f"<TenantEngineProxy tenant={_tenant_key()!r}>"


class _TenantSessionFactoryProxy:
    """调用时返回当前租户的 AsyncSession。"""

    def __call__(self, *args, **kwargs) -> AsyncSession:
        return get_session_factory()(*args, **kwargs)

    def __repr__(self) -> str:
        return f"<TenantSessionFactoryProxy tenant={_tenant_key()!r}>"


async_engine = _TenantEngineProxy()
async_session_factory = _TenantSessionFactoryProxy()


class _SafeSessionFactory:
    """A session factory whose context manager suppresses close() errors.

    When SSE clients disconnect, Starlette cancels the response task.
    aiosqlite connections that are mid-flight at that point raise
    ``OperationalError: no active connection`` during the implicit
    rollback inside ``AsyncSession.close()``.  This is harmless — the
    connection was going to be discarded anyway — so we swallow it.

    Usage is identical to ``async_session_factory``::

        async with safe_session_factory() as session:
            ...
    """

    def __call__(self) -> _SafeSessionContext:
        return _SafeSessionContext(async_session_factory())


class _SafeSessionContext:
    """Async context manager wrapping AsyncSession with safe close."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        with contextlib.suppress(OperationalError, asyncio.CancelledError):
            await self._session.close()
        return False


safe_session_factory = _SafeSessionFactory()


def dispose_pool() -> None:
    """Dispose the connection pool so a fresh event loop gets fresh connections.

    ``asyncio.run()`` creates a new event loop each time, but the module-level
    ``async_engine`` persists.  Stale pool connections may hold Futures bound
    to a now-closed loop, causing "Future attached to a different loop".
    Call this before ``asyncio.run()`` in sync wrappers.
    """
    # 遍历全部租户的 engine：只 dispose 当前租户会给其它租户留下绑在旧 loop 上的连接。
    for engine in all_engines():
        engine.sync_engine.dispose()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends generator for per-request AsyncSession."""
    async with async_session_factory() as session:
        yield session
