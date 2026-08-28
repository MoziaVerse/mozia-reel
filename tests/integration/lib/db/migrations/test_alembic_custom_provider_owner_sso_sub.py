"""Alembic 迁移：custom_provider 加 owner_sso_sub 租户指纹列的 upgrade / downgrade。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_provider)")).fetchall()
    return {r[1] for r in rows}


def test_upgrade_adds_column_existing_row_null(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    """升到加列前插一行，升级后列存在且该行为 NULL（存量行零回归，靠下次握手回填）。"""
    cfg, db_path = alembic_cfg
    revision_id, parent_id = migration_revisions("*_add_owner_sso_sub_to_custom_provider.py")
    command.upgrade(cfg, parent_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert "owner_sso_sub" not in _columns(engine), "加列前不应存在该列"
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider "
                    "(id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
                    "VALUES (1, 'P', 'openai', 'https://x', 'k', "
                    "'2026-08-24 00:00:00', '2026-08-24 00:00:00')"
                )
            )

        command.upgrade(cfg, revision_id)

        assert "owner_sso_sub" in _columns(engine)
        with engine.begin() as conn:
            value = conn.execute(sa.text("SELECT owner_sso_sub FROM custom_provider WHERE id = 1")).scalar_one()
        assert value is None
    finally:
        engine.dispose()


def test_downgrade_drops_column(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    """downgrade 回退后该列消失，其余数据保留。"""
    cfg, db_path = alembic_cfg
    revision_id, parent_id = migration_revisions("*_add_owner_sso_sub_to_custom_provider.py")
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO custom_provider "
                    "(id, display_name, discovery_format, base_url, api_key, owner_sso_sub, "
                    "created_at, updated_at) "
                    "VALUES (1, 'P', 'openai', 'https://x', 'k', 'tenant-a', "
                    "'2026-08-24 00:00:00', '2026-08-24 00:00:00')"
                )
            )

        command.downgrade(cfg, parent_id)

        assert "owner_sso_sub" not in _columns(engine)
        with engine.begin() as conn:
            name = conn.execute(sa.text("SELECT display_name FROM custom_provider WHERE id = 1")).scalar_one()
        assert name == "P"
    finally:
        engine.dispose()
