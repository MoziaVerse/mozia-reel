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

    def test_recreates_root_if_deleted(self):
        """目录被外部删掉后要能自愈。

        带缓存的版本会记住"建过了"而不再重建，之后所有请求撞
        "unable to open database file" —— 那个报错指不到根因。
        """
        import shutil

        with tenant_scope("alice"):
            first = app_data_dir()
            shutil.rmtree(first)
            assert not first.exists()
            assert app_data_dir().exists()

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


class TestProjectEventServiceFollowsTenant:
    """SSE 事件服务是启动期构造的单例，最容易把"那一刻的根"钉死。

    钉死的后果不是串数据而是全员 404：租户项目全在 ``tenants/<sub>/`` 下，
    部署级根里一个都没有，每个项目的事件流都查无此项目。
    """

    def test_pm_follows_current_tenant(self):
        from server.services.project_events import ProjectEventService

        service = ProjectEventService()
        with tenant_scope("alice"):
            a = service.pm.projects_root
        with tenant_scope("bob"):
            b = service.pm.projects_root
        assert a != b

    def test_explicit_root_stays_pinned(self, tmp_path):
        """显式给根的调用方（测试 fixture / 单租户旧契约）不受租户影响。"""
        from server.services.project_events import ProjectEventService

        pinned = tmp_path / "pinned"
        service = ProjectEventService(projects_root=pinned)
        with tenant_scope("alice"):
            assert service.pm.projects_root == pinned.resolve()

    def test_channels_are_keyed_per_tenant(self):
        """同名项目在两个租户下必须是两条独立通道，否则 A 的变更会广播给 B。"""
        from server.services.project_events import ProjectEventService

        service = ProjectEventService()
        with tenant_scope("alice"):
            a = service._key("shared-name")
        with tenant_scope("bob"):
            b = service._key("shared-name")
        assert a != b


class _FakeWorker:
    """记录启停与所处租户的 worker 替身。"""

    instances: list[_FakeWorker] = []

    def __init__(self):
        from lib.tenant_context import current_tenant

        self.started_under = None
        self.stopped = False
        self.constructed_under = current_tenant()
        _FakeWorker.instances.append(self)

    async def start(self):
        from lib.tenant_context import current_tenant

        self.started_under = current_tenant()

    async def stop(self):
        self.stopped = True

    def request_cancel(self, *a, **kw):
        return None


class TestWorkerSupervisor:
    @pytest.fixture(autouse=True)
    def _clear(self):
        _FakeWorker.instances.clear()
        yield
        _FakeWorker.instances.clear()

    def test_worker_starts_under_its_tenant(self):
        """worker 必须在租户上下文里构造并启动——否则它绑的是默认库的队列，
        租户任务永远 pending 且不报错。"""
        import asyncio

        from lib.worker_supervisor import WorkerSupervisor

        sup = WorkerSupervisor(_FakeWorker)
        asyncio.run(sup.ensure_started("alice"))
        w = _FakeWorker.instances[-1]
        assert w.constructed_under == "alice"
        assert w.started_under == "alice"

    def test_each_tenant_gets_own_worker(self):
        import asyncio

        from lib.worker_supervisor import WorkerSupervisor

        async def scenario():
            sup = WorkerSupervisor(_FakeWorker)
            await sup.ensure_started("alice")
            await sup.ensure_started("bob")
            return sup

        sup = asyncio.run(scenario())
        assert sup.get("alice") is not sup.get("bob")
        assert len(sup.all_workers()) == 2

    def test_ensure_started_is_idempotent(self):
        import asyncio

        from lib.worker_supervisor import WorkerSupervisor

        async def scenario():
            sup = WorkerSupervisor(_FakeWorker)
            a = await sup.ensure_started("alice")
            b = await sup.ensure_started("alice")
            return a, b

        a, b = asyncio.run(scenario())
        assert a is b
        assert len(_FakeWorker.instances) == 1

    def test_rejects_invalid_tenant(self):
        import asyncio

        from lib.worker_supervisor import WorkerSupervisor

        sup = WorkerSupervisor(_FakeWorker)
        with pytest.raises(ValueError):
            asyncio.run(sup.ensure_started("../evil"))

    def test_stop_all_stops_every_tenant(self):
        import asyncio

        from lib.worker_supervisor import WorkerSupervisor

        async def scenario():
            sup = WorkerSupervisor(_FakeWorker)
            await sup.ensure_started("alice")
            await sup.ensure_started("bob")
            await sup.stop_all()
            return sup

        sup = asyncio.run(scenario())
        assert all(w.stopped for w in _FakeWorker.instances)
        assert sup.all_workers() == []

    def test_discover_tenants_lists_existing_dirs(self):
        """重启后要能把已有租户的 worker 拉起来，靠的就是这个枚举。"""
        from lib.app_data_dir import base_data_dir
        from lib.worker_supervisor import discover_tenants

        root = base_data_dir() / "tenants"
        (root / "alice").mkdir(parents=True, exist_ok=True)
        (root / "bob").mkdir(parents=True, exist_ok=True)
        (root / "..bad").mkdir(parents=True, exist_ok=True)
        found = discover_tenants()
        assert "alice" in found and "bob" in found
        assert "..bad" not in found  # 非法目录名不能被当成租户
