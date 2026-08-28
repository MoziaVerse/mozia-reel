"""
OpenAI 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- OPENAI_RETRYABLE_ERRORS — 可重试错误类型
- create_openai_client — AsyncOpenAI 客户端工厂
- OPENAI_IMAGE_QUALITY_MAP — image_size 档位 → quality 映射，供 image_backends.openai 消费。
  尺寸不再用静态 (image_size, aspect_ratio) → "WxH" 表，改由 lib.aspect_size 按比例精确计算
  （比例优先、清晰度其次），见 docs/adr/0011。
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

import httpx
from openai import AsyncOpenAI

from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL

logger = logging.getLogger(__name__)

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

OPENAI_IMAGE_QUALITY_MAP: dict[str, str] = {
    "512px": "low",
    "1K": "medium",
    "2K": "high",
    "4K": "high",
}

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    OPENAI_RETRYABLE_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except ImportError:
    pass  # openai 是必装依赖，此分支仅作防御性保护；回退到空 tuple


# ── 网关 request id 捕获 ────────────────────────────────────────────
#
# mozia 网关在响应头 x-oneapi-request-id 里回本次调用的 id，存下它费用才能跟平台
# 账务对上（本地估算实测把 glm 按 Anthropic 单价算，高估近 8 倍）。
#
# 用 httpx 的 event_hook 在客户端层捕获，而不是把调用点改成
# ``with_raw_response``：后者会改变调用形态，让既有测试里对 ``images.generate``
# 的 mock 全部失效（实测挂 44 个）。hook 不动调用签名，backend 照常 await 原方法。
#
# ContextVar 而非实例属性：同一个 client 会被并发请求共用，实例属性会串；
# ContextVar 在每个 asyncio task 内独立，天然按请求隔离。
GATEWAY_REQUEST_ID_HEADER = "x-oneapi-request-id"

_last_gateway_request_id: ContextVar[str | None] = ContextVar("mozia_gateway_request_id", default=None)


def take_gateway_request_id() -> str | None:
    """取出并清空本上下文最近一次网关调用的 id。

    取出即清空：拿到的必须是"刚才那一次"，留着会让下一次拿不到 id 的调用误取到上一次的，
    把费用记到错误的调用上。
    """
    value = _last_gateway_request_id.get()
    _last_gateway_request_id.set(None)
    return value


async def _capture_gateway_request_id(response) -> None:
    try:
        rid = response.headers.get(GATEWAY_REQUEST_ID_HEADER)
    except Exception:  # noqa: BLE001 —— 取不到 id 不该影响已经成功的请求
        return
    if rid:
        _last_gateway_request_id.set(rid)


def _client_with_capture() -> httpx.AsyncClient:
    return httpx.AsyncClient(event_hooks={"response": [_capture_gateway_request_id]})


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    max_retries: int | None = None,
) -> AsyncOpenAI:
    """创建 AsyncOpenAI 客户端，统一处理 api_key 和 base_url。

    base_url 为空（None/空白）时显式回填官方端点：AsyncOpenAI 对空 base_url
    会回落读取 OPENAI_BASE_URL 环境变量，环境残留将静默覆盖 DB 配置。base_url
    的唯一来源是 DB，此处兜死显式值断掉该回落路径。
    """
    kwargs: dict = {
        "base_url": (base_url or "").strip() or OFFICIAL_OPENAI_BASE_URL,
        "http_client": _client_with_capture(),
    }
    if api_key:
        kwargs["api_key"] = api_key
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)
