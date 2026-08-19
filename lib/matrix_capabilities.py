"""Matrix 部署下暴露给用户的能力面。

ArcReel 原生支持十来家厂商的**原生** API（Gemini / Vertex / 火山 / 可灵 / 百炼 …），
每家都要用户自己填厂商 key。但在 Matrix 形态下用户只有一把平台网关的 key，
那些入口既填不了也用不了 —— 放在设置页里只会让人以为"配了就能用"，
填完却在生成时才失败。

所以这里定义一份白名单，由后端在下发目录时过滤。前端的供应商列表与 endpoint
下拉本来就是"后端单一真相源"派生的（见 EndpointSelect 的注释），
因此不需要改任何前端代码。

⚠️ 白名单依据的是 **mozia 网关实际暴露的路径**，不是"这个模型存不存在"：
网关是 new-api 中转，只提供 OpenAI 兼容那几条 + Anthropic 的 /v1/messages。
厂商原生路径（如 MiniMax 的 /video_generation）打到网关上会返回中转站的
SPA 首页 HTML，表现成"JSON 解析失败"，指不到根因 —— 这正是要在 UI 上
提前隐藏它们的原因。
"""

from __future__ import annotations

import os

# 网关实测可用的 endpoint（2026-08-19 逐条探测确认）：
#   /v1/chat/completions · /v1/images/* · /v1/videos · /v1/audio/speech · /v1/messages
GATEWAY_SUPPORTED_ENDPOINTS: frozenset[str] = frozenset(
    {
        "openai-chat",
        "openai-images",
        "openai-images-generations",
        "openai-images-edits",
        "openai-video",
        "openai-tts",
    }
)

# 内置供应商全部要求厂商自有凭据，Matrix 形态下一律隐藏；
# 用户只通过"自定义供应商"里那条已 seed 好的 Matrix 网关来用模型。
GATEWAY_SUPPORTED_BUILTIN_PROVIDERS: frozenset[str] = frozenset()


def matrix_mode_enabled() -> bool:
    """是否处于 Matrix 托管形态。

    复用 MATRIX_BACKEND_URL 而不是再加一个开关：能力面收窄与握手接入是同一件
    事的两面，分成两个开关只会出现"接了 matrix 却还露着填不了的厂商入口"
    这种半配状态。
    """
    return bool(os.environ.get("MATRIX_BACKEND_URL", "").strip())


def visible_endpoint_keys(all_keys) -> list[str]:
    """过滤 endpoint 目录。非 Matrix 形态（单机自用）原样返回。"""
    keys = list(all_keys)
    if not matrix_mode_enabled():
        return keys
    return [k for k in keys if k in GATEWAY_SUPPORTED_ENDPOINTS]


def builtin_provider_visible(provider_id: str) -> bool:
    """内置供应商是否该出现在设置页。"""
    if not matrix_mode_enabled():
        return True
    return provider_id in GATEWAY_SUPPORTED_BUILTIN_PROVIDERS
