"""
API 调用统计路由

提供调用记录查询和统计摘要接口。

费用一律以**积分**呈现，且只呈现平台账务的实扣数字（见
``server.services.usage_reconciliation``）。本地账本的 ``cost_amount`` 仍然照写，
但它是估算，不进对外的费用字段——对不上的行如实标 ``unknown``。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import async_session_factory
from lib.db.repositories.usage_repo import UsageRepository
from lib.matrix_session import get_wallet_token
from lib.providers import CallType
from server.services.usage_reconciliation import ReconciledCost, reconcile_rows

router = APIRouter()

# 统计口径下参与对账的最多行数。总额要精确就得把每一行都对上，但也不能为了一个
# 汇总数字把整张表拉进内存；超出部分计入 ``unsettled_count``，前端据此如实提示。
_STATS_RECONCILE_LIMIT = 500


async def _reconcile(session: AsyncSession, rows: list[dict[str, Any]]) -> dict[int, ReconciledCost] | None:
    """对账结果；本部署没有平台账本时返回 None（不是空 dict）。

    两者必须分开：空 dict 是「对过了，一条都没对上」，None 是「这个部署压根没有平台
    账本可对」。自建供应商部署下本地估算是**唯一**可得的口径，也确实按用户自己配的
    价目表算，把它换成一列「未知」是纯粹的倒退——那种部署继续走原有的货币展示。
    """
    token = await get_wallet_token(session)
    if not token:
        return None
    return await reconcile_rows(rows, wallet_token=token)


def _attach_costs(rows: list[dict[str, Any]], settled: dict[int, ReconciledCost] | None) -> list[dict[str, Any]]:
    """把对账结果贴回每一行。

    ``credits=None`` 与 ``credits=0`` 是两回事：前者是「不知道花了多少」，后者是
    「确实没花钱」（失败请求平台记 0）。前端据此分别渲染，不能合并。
    """
    if settled is None:
        return rows
    for row in rows:
        cost = settled.get(row.get("id"))  # pyright: ignore[reportArgumentType]
        row["credits"] = None if cost is None else cost.credits
        row["credits_source"] = "unknown" if cost is None else cost.source
    return rows


@router.get("/usage/stats")
async def get_stats(
    project_name: str | None = Query(None, description="项目名称（可选）"),
    provider: str | None = Query(None, description="按供应商筛选"),
    start_date: str | None = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    group_by: str | None = Query(None, description="分组方式: provider"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    async with async_session_factory() as session:
        repo = UsageRepository(session)
        if group_by == "provider":
            return await repo.get_stats_grouped_by_provider(
                project_name=project_name,
                provider=provider,
                start_date=start,
                end_date=end,
            )
        stats = await repo.get_stats(
            project_name=project_name,
            provider=provider,
            start_date=start,
            end_date=end,
        )
        page = await repo.get_calls(
            project_name=project_name,
            start_date=start,
            end_date=end,
            page=1,
            page_size=_STATS_RECONCILE_LIMIT,
        )
        rows: list[dict[str, Any]] = page["items"]
        settled = await _reconcile(session, rows)

    if settled is None:
        return stats
    known = [cost for cost in settled.values() if cost.credits is not None]
    stats["total_credits"] = round(sum(cost.credits or 0.0 for cost in known), 4)
    # 没对上的行数：总额少算了多少笔，用户有权知道，不能让一个偏小的合计冒充全部。
    stats["unsettled_count"] = max(int(stats.get("total_count") or 0) - len(known), 0)
    return stats


@router.get("/usage/calls")
async def get_calls(
    project_name: str | None = Query(None, description="项目名称"),
    call_type: CallType | None = Query(None, description="调用类型 (image/video/text)"),
    status: str | None = Query(None, description="状态 (success/failed)"),
    start_date: str | None = Query(None, description="开始日期 (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="结束日期 (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页记录数"),
):
    start = datetime.fromisoformat(start_date) if start_date else None
    end = datetime.fromisoformat(end_date) if end_date else None

    async with async_session_factory() as session:
        repo = UsageRepository(session)
        result = await repo.get_calls(
            project_name=project_name,
            call_type=call_type,
            status=status,
            start_date=start,
            end_date=end,
            page=page,
            page_size=page_size,
        )
        # 对账要连智能体在本页之外的相邻轮次一起看：轮次窗口以「上一轮结束」为起点，
        # 只拿本页会让本页最早那一轮的起点退到兜底回溯上限，把上一轮的开销吞进来。
        context = await repo.get_calls(
            project_name=project_name,
            start_date=start,
            end_date=end,
            page=1,
            page_size=min(page * page_size + page_size, _STATS_RECONCILE_LIMIT),
        )
        settled = await _reconcile(session, context["items"])
    result["items"] = _attach_costs(result["items"], settled)
    return result


@router.get("/usage/projects")
async def get_projects_list():
    async with async_session_factory() as session:
        projects = await UsageRepository(session).get_projects_list()
    return {"projects": projects}
