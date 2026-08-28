"""按时间窗拉取平台账务流水，供本地账本对账。

与 ``matrix_session.fetch_wallet_logs`` 的分工：那条是用量页的分页浏览（用户翻到哪页
拉哪页），这条是对账用的**窗口全量**——要判定某条本地记录实际扣了多少，必须把该时刻
前后的记录都拿到手，翻页语义帮不上忙。

金额一律以积分（credits）为单位。本地账本里的 ``cost_amount`` 是按静态价目表算的估算，
与平台实扣可以差出数倍（自定义供应商的图片/视频甚至一律记 0），所以对账结果只取平台
数字，不做任何本地回落——宁可显示「未知」，也不显示一个看着像真值的估算。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from lib.matrix_session import MatrixHandoffError, matrix_backend_url

logger = logging.getLogger(__name__)

# 上游单页上限。要更大页只会被服务端截回，多发的请求纯属浪费。
_PAGE_SIZE = 100
# 单次对账最多翻多少页。窗口通常只覆盖用户当前在看的那几十条记录；真撞上上限说明
# 窗口取得太宽，截断比无节制翻页安全——调用方据 ``truncated`` 如实告知未覆盖全部。
_MAX_PAGES = 20
# 结果缓存时长。面板翻页/切项目会连续发起对账，同一窗口重复拉平台是纯浪费；
# 但也不能久——用户刚生成完就看费用，缓存太长会让新记录迟迟不出现。
_CACHE_TTL_SECONDS = 20.0


@dataclass(frozen=True)
class GatewayRecord:
    """平台账务里的一条流水。"""

    request_id: str
    created_at: int
    """秒级 unix 时间戳（上游 createdAt 就是秒，不是毫秒）。"""
    model: str
    kind: str
    """consume / error / refund / other，语义见 ``matrix_session._LOG_KINDS``。"""
    credits: float
    quota: float
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class GatewayWindow:
    """一个时间窗内的平台流水。"""

    records: tuple[GatewayRecord, ...]
    truncated: bool
    """是否因翻页上限而没取全——为真时调用方不得声称对账完整。"""


_cache: dict[tuple[str, int, int], tuple[float, GatewayWindow]] = {}


def _cache_key(token: str, start: int, end: int) -> tuple[str, int, int]:
    # token 不进 key 明文：缓存 dict 会随进程转储/日志外泄，凭据不该跟着走。
    return (str(hash(token)), start, end)


def clear_cache() -> None:
    """丢弃缓存。仅供测试与凭据轮换后强制重取。"""
    _cache.clear()


def _to_record(raw: Any, per_credit: float) -> GatewayRecord | None:
    if not isinstance(raw, dict):
        return None
    request_id = str(raw.get("requestId") or "").strip()
    quota = raw.get("quota")
    quota_value = float(quota) if isinstance(quota, (int, float)) and not isinstance(quota, bool) else 0.0
    created = raw.get("createdAt")
    from lib.matrix_session import _log_kind  # 与用量页共用同一张 type 映射

    return GatewayRecord(
        request_id=request_id,
        created_at=int(created) if isinstance(created, (int, float)) and not isinstance(created, bool) else 0,
        model=str(raw.get("modelName") or "").strip() or "unknown",
        kind=_log_kind(raw.get("type")),
        credits=quota_value / per_credit,
        quota=quota_value,
        prompt_tokens=int(raw.get("promptTokens") or 0),
        completion_tokens=int(raw.get("completionTokens") or 0),
    )


async def fetch_window(token: str, *, start: int, end: int) -> GatewayWindow:
    """拉 [start, end] 秒级窗口内的全部流水。

    跨应用隔离由 matrix 侧按 walletToken 里的 clientId 强制完成，所以这里拿到的
    已经只是本应用的记录——不需要、也没法自己按客户端过滤。
    """
    if start > end:
        start, end = end, start
    key = _cache_key(token, start, end)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _CACHE_TTL_SECONDS:
        return hit[1]

    base = matrix_backend_url()
    if not base:
        raise MatrixHandoffError("MATRIX_BACKEND_URL 未配置", 500, "misconfigured")

    records: list[GatewayRecord] = []
    truncated = False
    async with httpx.AsyncClient(timeout=20.0) as client:
        for page in range(1, _MAX_PAGES + 1):
            try:
                response = await client.get(
                    f"{base}/api/external/logs",
                    headers={"Authorization": f"Bearer {token}"},
                    params={
                        "page": page,
                        "pageSize": _PAGE_SIZE,
                        "startTimestamp": start,
                        "endTimestamp": end,
                    },
                )
            except httpx.HTTPError as exc:
                raise MatrixHandoffError(f"matrix 不可达: {exc}", 502, "matrix_unreachable") from exc
            if response.status_code >= 400:
                raise MatrixHandoffError(response.text[:200], response.status_code, "logs_failed")
            payload = response.json()
            if not isinstance(payload, dict):
                break
            per_unit = payload.get("quotaPerUnit")
            per_unit_value = (
                float(per_unit) if isinstance(per_unit, (int, float)) and not isinstance(per_unit, bool) else 500000.0
            ) or 500000.0
            per_credit = per_unit_value / 100 or 1.0
            items = payload.get("items") or []
            for raw in items:
                record = _to_record(raw, per_credit)
                if record is not None:
                    records.append(record)
            if len(items) < _PAGE_SIZE:
                break
            if page == _MAX_PAGES:
                truncated = True
                logger.warning("平台流水窗口超过 %d 页，对账结果不完整 start=%s end=%s", _MAX_PAGES, start, end)

    window = GatewayWindow(records=tuple(records), truncated=truncated)
    _cache[key] = (now, window)
    return window
