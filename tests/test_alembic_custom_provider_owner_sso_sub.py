"""Alembic 迁移：custom_provider 加 owner_sso_sub 租户指纹列的 upgrade / downgrade。"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

pytestmark = pytest.mark.unit


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
    matches = list(versions_dir.glob("*_add_owner_sso_sub_to_custom_provider.py"))
    assert len(matches) == 1, f"找到 {len(matches)} 个加列迁移文件，期望 1"
    text = matches[0].read_text()
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


def _columns(engine: sa.Engine) -> set[str]:
    with engine.begin() as conn:
        rows = conn.execute(sa.text("PRAGMA table_info(custom_provider)")).fetchall()
    return {r[1] for r in rows}


def test_upgrade_adds_column_existing_row_null(alembic_cfg: Config, revisions: tuple[str, str]):
    """升到加列前插一行，升级后列存在且该行为 NULL（存量行零回归，靠下次握手回填）。"""
    revision_id, parent_id = revisions
    command.upgrade(alembic_cfg, parent_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
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

        command.upgrade(alembic_cfg, revision_id)

        assert "owner_sso_sub" in _columns(engine)
        with engine.begin() as conn:
            value = conn.execute(sa.text("SELECT owner_sso_sub FROM custom_provider WHERE id = 1")).scalar_one()
        assert value is None
    finally:
        engine.dispose()


def test_downgrade_drops_column(alembic_cfg: Config, revisions: tuple[str, str]):
    """downgrade 回退后该列消失，其余数据保留。"""
    revision_id, parent_id = revisions
    command.upgrade(alembic_cfg, revision_id)

    db_path = alembic_cfg.attributes["_test_db_path"]
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

        command.downgrade(alembic_cfg, parent_id)

        assert "owner_sso_sub" not in _columns(engine)
        with engine.begin() as conn:
            name = conn.execute(sa.text("SELECT display_name FROM custom_provider WHERE id = 1")).scalar_one()
        assert name == "P"
    finally:
        engine.dispose()
