"""受邀名单。

这层是访问控制，破了不会报错、只会静默放行或静默锁人，所以每条边界都钉住。
"""

from __future__ import annotations

import pytest

from lib import matrix_allowlist
from lib.matrix_allowlist import is_allowed, load_allowlist

pytestmark = pytest.mark.unit

SUB_A = "1f917403-5af6-4b8b-9f16-59636f3af474"
SUB_B = "6e8cbfe2-e486-4758-9c39-c5abc3c382a8"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MATRIX_ALLOWLIST_FILE", raising=False)
    matrix_allowlist._reset_for_tests()
    yield
    matrix_allowlist._reset_for_tests()


def _write(tmp_path, text, monkeypatch):
    f = tmp_path / "allowlist.txt"
    f.write_text(text, encoding="utf-8")
    monkeypatch.setenv("MATRIX_ALLOWLIST_FILE", str(f))
    matrix_allowlist._reset_for_tests()
    return f


class TestUnconfigured:
    def test_no_env_means_no_restriction(self):
        """未配名单要维持接入前的行为，否则本地与测试服会被一并锁死。"""
        assert load_allowlist() is None
        assert is_allowed(SUB_A) is True
        assert is_allowed(None) is True

    def test_blank_env_is_treated_as_unset(self):
        """compose 里 ARG 留空会传进来一个空串。"""
        import os

        os.environ["MATRIX_ALLOWLIST_FILE"] = "   "
        try:
            assert load_allowlist() is None
        finally:
            del os.environ["MATRIX_ALLOWLIST_FILE"]


class TestParsing:
    def test_one_per_line(self, tmp_path, monkeypatch):
        _write(tmp_path, f"{SUB_A}\n{SUB_B}\n", monkeypatch)
        assert load_allowlist() == frozenset({SUB_A, SUB_B})

    def test_comments_and_blank_lines(self, tmp_path, monkeypatch):
        """名单里全是 UUID，没有注释根本没法维护。"""
        _write(
            tmp_path,
            f"# 灰度名单\n\n{SUB_A}   # 张三\n\n  {SUB_B}\t# 李四\n# {SUB_A}xxx 整行注释掉的\n",
            monkeypatch,
        )
        assert load_allowlist() == frozenset({SUB_A, SUB_B})

    def test_empty_file_locks_everyone_out(self, tmp_path, monkeypatch):
        """空文件是"名单为空"，与"没配名单"不同——前者显式拒绝所有人。"""
        _write(tmp_path, "# 谁都不放\n", monkeypatch)
        assert load_allowlist() == frozenset()
        assert is_allowed(SUB_A) is False


class TestDecision:
    def test_listed_passes_unlisted_rejected(self, tmp_path, monkeypatch):
        _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        assert is_allowed(SUB_A) is True
        assert is_allowed(SUB_B) is False

    def test_missing_sub_is_rejected_when_list_is_active(self, tmp_path, monkeypatch):
        _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        assert is_allowed(None) is False
        assert is_allowed("") is False


class TestFailOpen:
    def test_missing_file_does_not_lock_everyone_out(self, tmp_path, monkeypatch, caplog):
        """挂载出问题、文件被误删时 fail closed 会把所有人锁在外面，包括本来该进来的。
        那比"名单暂时失效"更糟，所以放行 + 告警，靠日志发现。"""
        monkeypatch.setenv("MATRIX_ALLOWLIST_FILE", str(tmp_path / "nope.txt"))
        matrix_allowlist._reset_for_tests()
        with caplog.at_level("WARNING"):
            assert load_allowlist() is None
            assert is_allowed(SUB_B) is True
        assert any("读不到" in r.message for r in caplog.records)

    def test_unreadable_file_falls_open(self, tmp_path, monkeypatch):
        f = _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        f.chmod(0o000)
        matrix_allowlist._reset_for_tests()
        try:
            assert load_allowlist() is None
        finally:
            f.chmod(0o644)


class TestHotReload:
    def test_edit_takes_effect_without_restart(self, tmp_path, monkeypatch):
        """撤权要立刻生效——重启才生效的话会打断正在跑的生成任务。"""
        f = _write(tmp_path, f"{SUB_A}\n{SUB_B}\n", monkeypatch)
        assert is_allowed(SUB_B) is True

        # 同一秒内改动 mtime 可能不变，缓存键一并比 size，所以内容变短要能被发现
        f.write_text(f"{SUB_A}\n", encoding="utf-8")
        assert is_allowed(SUB_B) is False
        assert is_allowed(SUB_A) is True

    def test_unchanged_file_is_not_reread(self, tmp_path, monkeypatch):
        """每请求都读盘的话，热路径上多一次 syscall。"""
        _write(tmp_path, f"{SUB_A}\n", monkeypatch)
        first = load_allowlist()
        assert load_allowlist() is first  # 同一个 frozenset 对象 = 命中缓存
