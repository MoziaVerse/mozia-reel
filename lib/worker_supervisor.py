"""按租户管理 GenerationWorker。

改造前是一个进程级 worker 轮询唯一的库。DB 按租户分裂后，那个 worker 只看得见
部署级默认库 —— 租户提交的任务会永远 pending，且**不报错**。

这里给每个租户起一份 worker。选 per-tenant 实例而不是"一个 worker 轮流查各租户库"，
是因为 worker 的内部状态（lease 持有、orphan 已扫描标记、并发占用台账）都是绑定
单一队列的；让一个实例服务多个队列会让这些状态互相串。

租户上下文靠 ``asyncio.create_task`` 复制调用方 Context 传进循环：在 ``tenant_scope``
里调 ``start()``，整个 ``_run_loop`` 及其派生的任务就都在该租户下。

⚠️ 并发语义：容量表是每个 worker 一份，所以上限是**每租户**的，总并发 =
活跃租户数 × 单租户上限。网关侧另有限流、费用也计到各自头上，但租户多起来时
这个乘积需要重新评估（见 matrix docs/arcreel-onboarding-plan.md 待办）。
"""

from __future__ import annotations

import logging

from lib.tenant_context import is_valid_tenant, tenant_scope

logger = logging.getLogger(__name__)


class WorkerSupervisor:
    """租户 → GenerationWorker 的注册表，负责启停。"""

    def __init__(self, factory) -> None:
        # factory 而非直接 import GenerationWorker：便于测试替身，也避免
        # 这个模块在 import 期就把生成栈整条拉起来。
        self._factory = factory
        self._workers: dict[str | None, object] = {}

    async def ensure_started(self, tenant: str | None) -> object:
        """确保该租户的 worker 在跑（幂等）。"""
        if tenant is not None and not is_valid_tenant(tenant):
            raise ValueError(f"非法租户标识: {tenant!r}")
        worker = self._workers.get(tenant)
        if worker is not None:
            return worker
        # 必须在 scope 内构造并 start：worker 的 __init__ 会拿队列（进而绑定
        # 当前租户的 session factory），create_task 也在这里复制 Context。
        with tenant_scope(tenant):
            from lib.generation_queue import get_generation_queue

            worker = self._factory()
            # cancel 回调必须在 start() 之前注入，否则有窗口期 callback 为 None、
            # cancel running 的信号会被丢弃（ADR 0006 要求秒级响应）。
            # 队列本身也是按租户取的，所以这里绑定的是该租户自己的队列。
            get_generation_queue().set_worker_cancel_callback(worker.request_cancel)
            await worker.start()
        self._workers[tenant] = worker
        logger.info("已为租户启动 GenerationWorker: %s", tenant or "(默认)")
        return worker

    async def start_existing_tenants(self, tenant_ids) -> None:
        """进程启动时为已存在的租户拉起 worker。

        不这么做的话，重启前排队的任务要等到该用户下次握手才会被处理 ——
        用户看到的是"任务卡住不动"，而且没有任何报错指向原因。
        """
        for tenant in tenant_ids:
            try:
                await self.ensure_started(tenant)
            except Exception:
                # 单个租户起不来不该拖垮整个启动流程，其它租户照常服务。
                logger.exception("为租户启动 worker 失败: %s", tenant)

    async def stop_all(self) -> None:
        from lib.generation_queue import get_generation_queue

        for tenant, worker in list(self._workers.items()):
            try:
                # try/finally 保证 callback 清理必达：worker.stop 抛错时也要把
                # callback 清空，免得污染后续生命周期与测试。
                try:
                    await worker.stop()
                finally:
                    with tenant_scope(tenant):
                        get_generation_queue().set_worker_cancel_callback(None)
            except Exception:
                logger.exception("停止 worker 失败: %s", tenant)
        self._workers.clear()

    def get(self, tenant: str | None):
        return self._workers.get(tenant)

    def all_workers(self) -> list:
        return list(self._workers.values())


def discover_tenants() -> list[str]:
    """枚举数据根下已存在的租户目录。"""
    from lib.app_data_dir import base_data_dir

    root = base_data_dir() / "tenants"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and is_valid_tenant(d.name))
