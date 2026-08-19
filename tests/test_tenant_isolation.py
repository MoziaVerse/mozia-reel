"""多租户隔离边界。

这套用例锁的是"A 租户绝对看不到 B 租户的数据"。破了不会报错、只会串数据，
所以每条都直接断言物理路径/URL 不同，而不是断言某个函数被调用过。
"""

from __future__ import annotations

import pytest

from lib.app_data_dir import _reset_for_tests, app_data_dir, base_data_dir
from lib.db.engine import get_database_url
from lib.project_manager import _reset_project_manager_for_tests, get_project_manager
from lib.tenant_context import current_tenant, is_valid_tenant, set_current_tenant, tenant_scope

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    _reset_for_tests()
    _reset_project_manager_for_tests()
    set_current_tenant(None)
    yield
    _reset_for_tests()
    _reset_project_manager_for_tests()
    set_current_tenant(None)


class TestTenantValidation:
    @pytest.mark.parametrize("good", ["abc", "A-1_z", "6e8cbfe2-e486-4758-9c39-c5abc3c382a8", "x" * 64])
    def test_accepts_sane_ids(self, good):
        assert is_valid_tenant(good)

    @pytest.mark.parametrize(
        "bad",
        ["", "..", "../etc", "a/b", "a\\b", "x" * 65, "a.b", "a b", None, 123],
    )
    def test_rejects_traversal_and_junk(self, bad):
        """租户名会成为目录名，放过 `..` 或 `/` 就是路径穿越。"""
        assert not is_valid_tenant(bad)

    def test_invalid_tenant_downgrades_to_none(self):
        """非法值降级为未登录，而不是抛错——抛错会把身份异常变成 500。"""
        set_current_tenant("../etc/passwd")
        assert current_tenant() is None

    def test_scope_rejects_invalid(self):
        with pytest.raises(ValueError):
            with tenant_scope("../evil"):
                pass


class TestDataRootIsolation:
    def test_no_tenant_uses_base(self):
        assert app_data_dir() == base_data_dir()

    def test_tenants_get_distinct_roots(self):
        with tenant_scope("alice"):
            a = app_data_dir()
        with tenant_scope("bob"):
            b = app_data_dir()
        assert a != b
        assert a.name == "alice" and b.name == "bob"

    def test_tenant_root_is_under_base(self):
        with tenant_scope("alice"):
            assert base_data_dir() in app_data_dir().parents

    def test_tenant_dirs_not_mistaken_for_projects(self):
        """租户目录挂在 tenants/ 下：直接放根下会被 projects_root.iterdir() 当成项目。"""
        with tenant_scope("alice"):
            assert app_data_dir().parent.name == "tenants"

    def test_scope_restores_previous(self):
        with tenant_scope("alice"):
            with tenant_scope("bob"):
                assert current_tenant() == "bob"
            assert current_tenant() == "alice"
        assert current_tenant() is None


class TestDerivedResourcesFollowTenant:
    def test_database_url_differs_per_tenant(self):
        with tenant_scope("alice"):
            a = get_database_url()
        with tenant_scope("bob"):
            b = get_database_url()
        assert a != b
        assert "alice" in a and "bob" in b

    def test_explicit_database_url_disables_split(self, monkeypatch):
        """显式配了外部库时不该按租户分裂 engine——那只会建一堆连同一个库的池。"""
        monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
        with tenant_scope("alice"):
            a = get_database_url()
        with tenant_scope("bob"):
            b = get_database_url()
        assert a == b

    def test_project_manager_roots_differ(self):
        with tenant_scope("alice"):
            a = get_project_manager().projects_root
        with tenant_scope("bob"):
            b = get_project_manager().projects_root
        assert a != b

    def test_projects_do_not_leak_across_tenants(self):
        """端到端：alice 建的项目目录不出现在 bob 的项目列表里。"""
        with tenant_scope("alice"):
            pm = get_project_manager()
            (pm.projects_root / "alice-secret").mkdir(parents=True, exist_ok=True)
            assert "alice-secret" in pm.list_projects()
        with tenant_scope("bob"):
            assert "alice-secret" not in get_project_manager().list_projects()
