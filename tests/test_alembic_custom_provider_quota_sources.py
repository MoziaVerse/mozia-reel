"""Alembic 迁移：custom_provider_model.quota_sources 的 upgrade / downgrade。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command


@pytest.fixture
def alembic_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    """指向项目 alembic 脚本，DB 用临时 sqlite（env.py 经 DATABASE_URL 读取）。

    刻意不传 alembic.ini 路径：env.py 在 config_file_name 为 None 时跳过 fileConfig()，
    避免 alembic.ini 的 logging section 在测试中重置 root logger。
    """
    repo_root = Path(__file__).resolve().parent.parent
    cfg = Config()
    cfg.set_main_option("script_location", str(repo_root / "alembic"))
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    cfg.attributes["_test_db_path"] = str(db_path)
    return cfg


@pytest.fixture
def revisions() -> tuple[str, str]:
    """读出加列迁移的 (revision, down_revision)。"""
    repo_root = Path(__file__).resolve().parent.parent
    versions_dir = repo_root / "alembic" / "versions"
    matches = list(versions_dir.glob("*_custom_provider_model_quota_sources.py"))
    assert len(matches) == 1, f"找到 {len(matches)} 个加列迁移文件，期望 1"
    text = matches[0].read_text(encoding="utf-8")
    revision: str | None = None
    down_revision: str | None = None
    for line in text.splitlines():
        if line.startswith("revision: str ="):
            revision = line.split("=")[1].strip().strip('"').strip("'")
        elif line.startswith("down_revision:"):
            down_revision = line.split("=")[1].strip().strip('"').strip("'")
    if not revision or not down_revision:
        raise RuntimeError("未在迁移文件中找到 revision / down_revision")
    return revision, down_revision


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


@pytest.mark.integration
def test_upgrade_adds_column_existing_row_null(alembic_cfg: Config, revisions: tuple[str, str]):
    """存量行升级后为 NULL —— 即「目录没标注」，界面不渲染标签，等下次目录刷新填上。"""
    revision_id, parent_id = revisions
    command.upgrade(alembic_cfg, parent_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert _COL not in _columns(engine), "加列前不应存在该列"
        with engine.begin() as conn:
            conn.execute(sa.text(_INSERT_PROVIDER))
            conn.execute(sa.text(_INSERT_MODEL))

        command.upgrade(alembic_cfg, revision_id)

        assert _COL in _columns(engine)
        with engine.begin() as conn:
            value = conn.execute(sa.text(f"SELECT {_COL} FROM custom_provider_model WHERE id = 1")).scalar_one()
        assert value is None
    finally:
        engine.dispose()


@pytest.mark.integration
def test_empty_list_and_populated_list_are_distinguishable(alembic_cfg: Config, revisions: tuple[str, str]):
    """空数组（网关默认放行全部分区）与非空数组是两种状态，存储层必须能区分。"""
    revision_id, _ = revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
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


@pytest.mark.integration
def test_downgrade_drops_column(alembic_cfg: Config, revisions: tuple[str, str]):
    revision_id, parent_id = revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        assert _COL in _columns(engine)
        command.downgrade(alembic_cfg, parent_id)
        assert _COL not in _columns(engine)
    finally:
        engine.dispose()
