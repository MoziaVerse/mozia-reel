"""Database package — ORM models, engine, and session factory."""

import asyncio
import logging
import weakref

from lib.db.base import Base
from lib.db.engine import (
    async_engine,
    async_session_factory,
    get_async_session,
    get_database_url,
    is_sqlite_backend,
    safe_session_factory,
)

_log = logging.getLogger(__name__)


async def init_db() -> None:
    """Run Alembic migrations to initialise / upgrade the database schema.

    Handles the transition from create_all to Alembic: if tables already exist
    but no alembic_version table is present, stamps the current head revision
    before running upgrade so existing databases migrate smoothly.

    使用 Config() 空构造 + set_main_option 编程式调用 alembic，
    而非 Config("alembic.ini")，避免 env.py 的 fileConfig() 覆盖应用日志配置。
    """
    import asyncio
    from pathlib import Path

    from sqlalchemy import inspect as sa_inspect
    from sqlalchemy import text

    # Detect pre-Alembic databases (tables exist but no version tracking)
    async with async_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: sa_inspect(c).get_table_names())
        has_app_tables = any(t in tables for t in ("tasks", "agent_sessions", "api_calls"))
        has_version = False
        if "alembic_version" in tables:
            row = (await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).first()
            has_version = row is not None

    need_stamp = has_app_tables and not has_version

    from alembic.config import Config

    from alembic import command

    def _run_alembic():
        # 编程式构造 Config，不读 alembic.ini，
        # 从而跳过 env.py 的 fileConfig()，保护应用日志配置
        project_root = Path(__file__).parent.parent.parent
        cfg = Config()
        cfg.set_main_option("script_location", str(project_root / "alembic"))
        if need_stamp:
            from alembic.script import ScriptDirectory

            base = ScriptDirectory.from_config(cfg).get_base()
            if base is None:
                raise RuntimeError("No base revision found in alembic migrations")
            _log.info("Detected pre-Alembic database, stamping base revision %s", base)
            command.stamp(cfg, base)
        command.upgrade(cfg, "head")

    # ⚠️ 必须把当前 Context 复制进 executor 线程：alembic 的 env.py 走
    # get_database_url() → app_data_dir() → ContextVar 取租户，而
    # run_in_executor 起的线程**不继承** ContextVar，直接跑会把租户库的迁移
    # 打到部署级默认库上 —— 建错库不报错，是静默的跨租户污染。
    import contextvars

    ctx = contextvars.copy_context()
    await asyncio.get_event_loop().run_in_executor(None, lambda: ctx.run(_run_alembic))
    _log.info("Database schema is up to date")


_initialized_tenants: set[str | None] = set()

# 迁移必须在进程内串行。alembic 的 ``context`` / ``op`` 是进程级全局代理，两个线程同时
# ``run_migrations`` 会互相覆盖对方的状态：报出来的是 "No context has been configured
# yet" / ``KeyError: 'config'`` / "table batches already exists"，留下的是 ``_alembic_tmp_*``
# 残表和「表已建但 alembic_version 没推进」的半迁移库——之后该租户每个请求重试都失败。
# SPA 首屏会并发打十几个 /api，同一租户的首次访问必然并发，所以不能只靠上面那个集合。
# 锁按事件循环各持一把：asyncio.Lock 首次争用后绑定循环，跨循环复用会抛 RuntimeError。
_migration_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = weakref.WeakKeyDictionary()


def _migration_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _migration_locks.get(loop)
    if lock is None:
        lock = _migration_locks[loop] = asyncio.Lock()
    return lock


async def ensure_tenant_db() -> None:
    """确保当前租户的库已建表（幂等，每租户只跑一次迁移）。

    新租户第一次握手时它的 SQLite 还不存在，任何查询都会撞 no such table。
    挂在握手链路上而不是 lifespan：启动时根本不知道会有哪些租户。
    """
    from lib.tenant_context import current_tenant

    tenant = current_tenant()
    if tenant in _initialized_tenants:
        return
    async with _migration_lock():
        if tenant in _initialized_tenants:
            return  # 等锁期间已由并发的另一个请求完成
        await init_db()
        _initialized_tenants.add(tenant)


def _reset_tenant_db_cache_for_tests() -> None:
    _initialized_tenants.clear()


async def close_db() -> None:
    """Dispose engine connections on shutdown.

    aiosqlite connections may already be dead when SSE tasks were cancelled,
    so we tolerate errors during pool cleanup.
    """
    try:
        await async_engine.dispose()
    except Exception:
        pass  # aiosqlite connections may already be dead after SSE task cancellation


__all__ = [
    "Base",
    "async_engine",
    "async_session_factory",
    "close_db",
    "ensure_tenant_db",
    "get_async_session",
    "get_database_url",
    "init_db",
    "is_sqlite_backend",
    "safe_session_factory",
]
