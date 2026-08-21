"""拒止名单。

默认放行、命中即拒。这层是访问控制，破了不会报错——要么该拒的没拒，要么
把所有人挡在外面，两种都得靠用例钉住。
"""

from __future__ import annotations

import pytest

from lib import matrix_blocklist
from lib.matrix_blocklist import is_allowed, load_blocklist

pytestmark = pytest.mark.unit

SUB_A = "1f917403-5af6-4b8b-9f16-59636f3af474"
SUB_B = "6e8cbfe2-e486-4758-9c39-c5abc3c382a8"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MATRIX_BLOCKLIST_FILE", raising=False)
    matrix_blocklist._reset_for_tests()
    yield
    matrix_blocklist._reset_for_tests()


def _write(tmp_path, text, monkeypatch):
    f = tmp_path / "blocklist.txt"
    f.write_text(text, encoding="utf-8")
    monkeypatch.setenv("MATRIX_BLOCKLIST_FILE", str(f))
    matrix_blocklist._reset_for_tests()
    return f


class TestDefaultOpen:
    def test_no_env_means_everyone_allowed(self):
        assert load_blocklist() == frozenset()
        assert is_allowed(SUB_A) is True

    def test_blank_env_is_treated_as_unset(self, monkeypatch):
        """compose 里变量留空会传进来一个空串。"""
        monkeypatch.setenv("MATRIX_BLOCKLIST_FILE", "   ")
        assert load_blocklist() == frozenset()

    def test_empty_file_blocks_nobody(self, tmp_path, monkeypatch):
        """空名单是"谁都没封"，不是"谁都不许进"——方向搞反就是全站下线。"""
        _write(tmp_path, "# 还没封过谁\n", monkeypatch)
        assert is_allowed(SUB_A) is True


class TestBlocking:
    def test_listed_is_rejected_others_pass(self, tmp_path, monkeypatch):
        _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        assert is_allowed(SUB_A) is False
        assert is_allowed(SUB_B) is True

    def test_comments_and_blank_lines(self, tmp_path, monkeypatch):
        """封禁记录要写清原因和日期，没有注释根本没法维护。"""
        _write(
            tmp_path,
            f"# 封禁记录\n\n{SUB_A}   # 滥用 2026-08-21\n\n  {SUB_B}\t# 退款纠纷\n# {SUB_A}xx 整行注释掉的\n",
            monkeypatch,
        )
        assert load_blocklist() == frozenset({SUB_A, SUB_B})

    def test_missing_identity_is_not_blocked(self, tmp_path, monkeypatch):
        """空身份不可能命中名单。"是否算合法会话"归门禁验签管，不在这里混着做。"""
        _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        assert is_allowed(None) is True
        assert is_allowed("") is True


class TestFailOpen:
    def test_missing_file_blocks_nobody(self, tmp_path, monkeypatch, caplog):
        """全拒会挡住所有人，而黑名单要挡的通常只是个位数——两种故障都不好，
        但前者影响面大几个数量级。代价是被封的人在故障期间能回来，所以要告警。"""
        monkeypatch.setenv("MATRIX_BLOCKLIST_FILE", str(tmp_path / "nope.txt"))
        matrix_blocklist._reset_for_tests()
        with caplog.at_level("WARNING"):
            assert is_allowed(SUB_A) is True
        assert any("读不到" in r.message for r in caplog.records)

    def test_unreadable_file_blocks_nobody(self, tmp_path, monkeypatch):
        f = _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        f.chmod(0o000)
        matrix_blocklist._reset_for_tests()
        try:
            assert is_allowed(SUB_A) is True
        finally:
            f.chmod(0o644)


class TestHotReload:
    def test_ban_takes_effect_without_restart(self, tmp_path, monkeypatch):
        """封禁要立刻生效——重启才生效的话会打断正在跑的生成任务。"""
        f = _write(tmp_path, "# 空\n", monkeypatch)
        assert is_allowed(SUB_A) is True
        f.write_text(f"{SUB_A}\n", encoding="utf-8")
        assert is_allowed(SUB_A) is False

    def test_unban_also_takes_effect(self, tmp_path, monkeypatch):
        f = _write(tmp_path, f"{SUB_A}\n{SUB_B}\n", monkeypatch)
        assert is_allowed(SUB_B) is False
        f.write_text(f"{SUB_A}\n", encoding="utf-8")
        assert is_allowed(SUB_B) is True

    def test_unchanged_file_is_not_reread(self, tmp_path, monkeypatch):
        _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        first = load_blocklist()
        assert load_blocklist() is first
