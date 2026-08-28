"""把本地账本的每一行对到平台账务的实扣积分上。

存在的理由：本地 ``cost_amount`` 是按静态价目表算的**估算**，与平台实扣对不上——
自定义供应商的图片/视频一律记 0，智能体那条把网关上的第三方模型按 Anthropic 单价
算。用户看账单信的当然是平台那份，所以展示层只呈现平台数字。

两种对法，能力不同、如实标注，不互相冒充：

``exact``
    调用时从响应头拿到了网关 request id，逐笔命中平台流水。图片/视频/音频，以及
    经 backend 发出的文本调用都走这条。

``aggregated``
    智能体的调用由 Claude Code 子进程直接发出，响应头到不了我们手里，拿不到
    request id。改为按**轮次时间窗**归集：一次轮次内平台上新增的、没被任何本地行
    逐笔认领的同模型记录，都算这一轮的开销。项目合计因此仍然精确（这是对平台记录
    集合做划分，不是估算），但单条不声称对应某一次具体请求。

``unknown``
    对不上。历史数据（那时还没存 request id）、直连厂商而非经网关的调用都在此列。
    **不回落本地估算**——显示一个看着像真值的错数字，比显示「未知」有害得多。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from lib.gateway_usage import GatewayRecord, fetch_window
from lib.providers import PROVIDER_ANTHROPIC

logger = logging.getLogger(__name__)

CostSource = Literal["exact", "aggregated", "unknown"]

# 窗口两端的余量：本地时钟与平台时钟不必然同步，取记录时刻恰好压在边界上会漏。
_WINDOW_SLACK_SECONDS = 120
# 单个智能体轮次向前回溯的上限。轮次以「上一轮结束」为起点，首轮没有上一轮，
# 用它兜底；比这更长的单轮不现实，放宽只会把别的轮次的开销吞进来。
_MAX_TURN_LOOKBACK_SECONDS = 1800


@dataclass(frozen=True)
class ReconciledCost:
    """一行本地记录的对账结果。"""

    credits: float | None
    source: CostSource
    matched_records: int = 0
    """归集了几条平台记录；``exact`` 恒为 1，``unknown`` 恒为 0。"""


UNRECONCILED = ReconciledCost(credits=None, source="unknown")


def _parse_ts(value: Any) -> int | None:
    """ISO 字符串 → 秒级 unix 时间戳。库里存的是 naive UTC。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def _row_time(row: dict[str, Any]) -> int | None:
    return _parse_ts(row.get("finished_at")) or _parse_ts(row.get("created_at")) or _parse_ts(row.get("started_at"))


def _is_assistant_row(row: dict[str, Any]) -> bool:
    """智能体那条链路的记账行。

    判据是 provider 而不是 call_type：项目文本模型的调用同样是 ``text``，但它经
    backend 发出、带得到 request id，属于逐笔可对的那一类。
    """
    return row.get("provider") == PROVIDER_ANTHROPIC


def plan_window(rows: Sequence[dict[str, Any]]) -> tuple[int, int] | None:
    """算出覆盖这批行所需的平台流水窗口。"""
    stamps = [ts for ts in (_row_time(row) for row in rows) if ts]
    if not stamps:
        return None
    return (
        min(stamps) - _MAX_TURN_LOOKBACK_SECONDS - _WINDOW_SLACK_SECONDS,
        max(stamps) + _WINDOW_SLACK_SECONDS,
    )


def reconcile(rows: Sequence[dict[str, Any]], records: Sequence[GatewayRecord]) -> dict[int, ReconciledCost]:
    """把本地行对到平台记录上。纯函数，取数与它分开以便单测。

    顺序是载荷：先让所有带 request id 的行逐笔认领，剩下的才轮到智能体按窗口归集。
    反过来的话，智能体窗口会把窗口内的图片/视频记录一并吞掉，那些行反而对不上了。
    """
    result: dict[int, ReconciledCost] = {}
    by_request_id: dict[str, GatewayRecord] = {r.request_id: r for r in records if r.request_id}
    claimed: set[str] = set()

    assistant_rows: list[dict[str, Any]] = []
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, int):
            continue
        request_id = (row.get("gateway_request_id") or "").strip()
        record = by_request_id.get(request_id) if request_id else None
        if record is not None:
            claimed.add(record.request_id)
            result[row_id] = ReconciledCost(credits=record.credits, source="exact", matched_records=1)
        elif _is_assistant_row(row):
            assistant_rows.append(row)
        else:
            result[row_id] = UNRECONCILED

    # 轮次按时间排序后首尾相接：一轮的起点就是上一轮的终点。轮次在同一会话里是串行的，
    # 这样切出来的窗口不重叠，同一条平台记录不会被两轮同时认领。
    ordered = sorted(assistant_rows, key=lambda r: _row_time(r) or 0)
    previous_end: int | None = None
    for row in ordered:
        row_id = row["id"]
        end = _row_time(row)
        if end is None:
            result[row_id] = UNRECONCILED
            continue
        start = (
            max(previous_end + 1, end - _MAX_TURN_LOOKBACK_SECONDS)
            if previous_end
            else end - _MAX_TURN_LOOKBACK_SECONDS
        )
        previous_end = end
        model = (row.get("model") or "").strip()
        matched = [
            r
            for r in records
            if r.request_id not in claimed and start <= r.created_at <= end + _WINDOW_SLACK_SECONDS and r.model == model
        ]
        if not matched:
            result[row_id] = UNRECONCILED
            continue
        claimed.update(r.request_id for r in matched)
        result[row_id] = ReconciledCost(
            credits=sum(r.credits for r in matched),
            source="aggregated",
            matched_records=len(matched),
        )
    return result


async def reconcile_rows(rows: Sequence[dict[str, Any]], *, wallet_token: str | None) -> dict[int, ReconciledCost]:
    """取平台流水并对账。拿不到平台数据时整批标 unknown，不抛给调用方。

    对账是展示增强，不是功能主线：平台不可达就该让费用列显示「未知」，
    而不是把整个用量面板拖挂。
    """
    if not wallet_token or not rows:
        return {}
    window = plan_window(rows)
    if window is None:
        return {}
    try:
        fetched = await fetch_window(wallet_token, start=window[0], end=window[1])
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：对账失败不该冒泡
        logger.warning("平台流水对账失败: %s", exc)
        return {}
    return reconcile(rows, fetched.records)
