"""参考图外链托管：把本地素材变成上游能拉取的公网 HTTPS URL。

**为什么需要这一层**

MiniMax H3 的服务端本身既收 multipart 直传、也收 JSON + 公网 URL
（见 mozia-h3-api `parse_request`）。但我们不是直连 H3，中间隔着 mozia 网关，
而网关会把请求体反序列化成 Go 结构体（``images []string``）后重建，
**multipart 的文件部分会被整个丢弃**（2026-08-19 四组探针实测）。

所以在网关支持直传之前，参考图只能以 URL 形式提交。这里把"URL 从哪来"
抽成一个可替换的 uploader，方案定了填进来即可，不必再动调用方。

**为什么不用第三方图床**

曾评估过 ImageKit 一类免费图床，作为平台方案不可接受：用户的角色设定图、
商品图会被传到境外公网永久可读直链且无清理；免费额度是全平台共用一个账号，
几个活跃用户就打满，之后静默失败；私钥还要注入到所有用户可达的进程里。

**H3 对 URL 的硬约束**（照抄契约）：必须 https、不能重定向、必须解析到公网 IP
（服务端有 SSRF 防护并把连接 pin 到该地址）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

# uploader 签名：本地路径列表 → 公网 https URL 列表（顺序必须与入参一致，
# 参考图的顺序对应 prompt 里的 <Picture i> 标签，错位就是画面主体错位）。
ReferenceImageUploader = Callable[[list[Path]], Awaitable[list[str]]]

_uploader: ReferenceImageUploader | None = None


class ReferenceHostingNotConfigured(RuntimeError):
    """没有可用的外链托管方案。

    单独一个类型而不是笼统 RuntimeError：调用方要能把它和"上传失败"区分开，
    前者是部署没配好、后者是运行时故障，给用户的提示完全不同。
    """


def set_uploader(uploader: ReferenceImageUploader | None) -> None:
    """注册外链托管实现。传 None 表示未配置。"""
    global _uploader
    _uploader = uploader


def is_configured() -> bool:
    return _uploader is not None


async def upload_reference_images(paths: list[Path]) -> list[str]:
    """把本地参考图变成公网 https URL。

    未配置时**明确抛错**而不是静默跳过参考图：跳过的话模型照样会出片、
    照样扣费，只是画面和用户给的参考毫无关系 —— 那种失败比报错难查得多。
    """
    if _uploader is None:
        raise ReferenceHostingNotConfigured(
            "该模型的参考图需要公网 HTTPS 直链，但本部署尚未配置外链托管。\n"
            "背景：mozia 网关不透传 multipart 文件，参考图只能以 URL 提交。\n"
            "两条待选方案见 matrix docs/arcreel-onboarding-plan.md 待办 T-2。"
        )
    urls = await _uploader(paths)
    if len(urls) != len(paths):
        # 顺序与数量必须严格对应 <Picture i>，少一张就是全体错位。
        raise RuntimeError(f"外链托管返回 {len(urls)} 个 URL，与 {len(paths)} 张参考图不符")
    return urls


def uploader_from_env() -> ReferenceImageUploader | None:
    """按环境变量选择实现。当前没有内置实现，恒为 None。

    保留这个入口是为了让接线位置明确：T-2 方案定下来后，在这里按
    ``ARCREEL_REFERENCE_HOSTING`` 分派即可，调用方一行不用改。
    """
    mode = os.environ.get("ARCREEL_REFERENCE_HOSTING", "").strip().lower()
    if not mode or mode == "none":
        return None
    logger.warning("未知的参考图托管方式 %r，按未配置处理", mode)
    return None
