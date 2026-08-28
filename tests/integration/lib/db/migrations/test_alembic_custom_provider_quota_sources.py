"""Alembic 迁移：custom_provider_model.quota_sources 的 upgrade / downgrade。"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_COL = "quota_sources"

_INSERT_PROVIDER = (
    "INSERT INTO custom_provider "
    "(id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
    "VALUES (1, 'P', 'openai', 'https://x', 'k', '2026-08-28 00:00:00', '2026-08-28 00:00:00')"
)

_INSERT_MODEL = (
    "INSERT INTO custom_provider_model "
    "(id, provider_id, model_id, display_name, endpoint, is_default, is_enabled, created_at, updated_at) "
    "VALUES (1, 1, 'z-ai/glm-5.2', 'GLM', 'openai-chat', 0, 1, "
    "'2026-08-28 00:00:00', '2026-08-28 00:00:00')"
)


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_provider_model)")).fetchall()
    return {r[1] for r in rows}


def test_upgrade_adds_column_existing_row_null(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    """存量行升级后为 NULL —— 即「目录没标注」，界面不渲染标签，等下次目录刷新填上。"""
    cfg, db_path = alembic_cfg
    revision_id, parent_id = migration_revisions("*_custom_provider_model_quota_sources.py")
    command.upgrade(cfg, parent_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert _COL not in _columns(engine), "加列前不应存在该列"
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT_PROVIDER))
            conn.execute(sa.text(_INSERT_MODEL))

        command.upgrade(cfg, revision_id)

        assert _COL in _columns(engine)
        with engine.begin() as conn:
            value = conn.execute(sa.text(f"SELECT {_COL} FROM custom_provider_model WHERE id = 1")).scalar_one()
        assert value is None
    finally:
        engine.dispose()


def test_empty_list_and_populated_list_are_distinguishable(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    """空数组（网关默认放行全部分区）与非空数组是两种状态，存储层必须能区分。"""
    cfg, db_path = alembic_cfg
    revision_id, _ = migration_revisions("*_custom_provider_model_quota_sources.py")
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT_PROVIDER))
            conn.execute(sa.text(_INSERT_MODEL))
            conn.execute(
                sa.text(f"UPDATE custom_provider_model SET {_COL} = :v WHERE id = 1"),
                {"v": json.dumps([])},
            )
            empty = conn.execute(sa.text(f"SELECT {_COL} FROM custom_provider_model WHERE id = 1")).scalar_one()
            conn.execute(
                sa.text(f"UPDATE custom_provider_model SET {_COL} = :v WHERE id = 1"),
                {"v": json.dumps(["gift", "paid"])},
            )
            listed = conn.execute(sa.text(f"SELECT {_COL} FROM custom_provider_model WHERE id = 1")).scalar_one()
        assert json.loads(empty) == []
        assert json.loads(listed) == ["gift", "paid"]
    finally:
        engine.dispose()


def test_downgrade_drops_column(
    alembic_cfg: tuple[Config, Path], migration_revisions: Callable[[str], tuple[str, str]]
):
    cfg, db_path = alembic_cfg
    revision_id, parent_id = migration_revisions("*_custom_provider_model_quota_sources.py")
    command.upgrade(cfg, revision_id)

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert _COL in _columns(engine)
        command.downgrade(cfg, parent_id)
        assert _COL not in _columns(engine)
    finally:
        engine.dispose()
