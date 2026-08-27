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
import mimetypes
import os
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from lib.signed_media_url import build_public_media_url, public_base_url

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


async def _gateway_credentials() -> tuple[str, str]:
    """取当前租户的网关 base_url 与 key。

    每次调用都现读而不是启动时缓存：凭据是 per-tenant 的，且会随握手刷新。
    """
    from lib.db import safe_session_factory
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository
    from lib.matrix_session import GATEWAY_PROVIDER_DISPLAY_NAME

    async with safe_session_factory() as session:
        repo = CustomProviderRepository(session)
        provider = next(
            (p for p in await repo.list_providers() if p.display_name == GATEWAY_PROVIDER_DISPLAY_NAME),
            None,
        )
    if provider is None or not provider.base_url or not provider.api_key:
        raise ReferenceHostingNotConfigured("尚未完成 Matrix 握手，拿不到网关凭据")
    return provider.base_url, provider.api_key


def _upload_endpoint(base_url: str) -> str:
    """拼出 ``/v1/sd/upload``。

    provider 里存的 base_url 可能带 /v1 也可能不带（OpenAI SDK 侧由
    ensure_openai_base_url 兜底），两种都要能拼对，否则会打到 /v1/v1/...。
    """
    stripped = base_url.strip().rstrip("/")
    if re.search(r"/v\d+$", stripped):
        return f"{stripped}/sd/upload"
    return f"{stripped}/v1/sd/upload"


async def _upload_via_gateway(paths: list[Path]) -> list[str]:
    """走网关的 /v1/sd/upload 把本地素材换成公网直链。

    为什么是它而不是自建图床：这是平台已经提供的统一入口，所有调用方共用，
    走用户自己的 key、计费口径一致。实测返回的直链公网可读、无重定向，
    上游能直接拉取。
    """
    base_url, api_key = await _gateway_credentials()
    endpoint = _upload_endpoint(base_url)

    urls: list[str] = []
    async with httpx.AsyncClient(timeout=_UPLOAD_TIMEOUT_SEC) as client:
        for path in paths:
            if not path.is_file():
                raise RuntimeError(f"参考素材不可读: {path.name}")
            content = path.read_bytes()
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            response = await client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (path.name, content, mime_type)},
            )
            if response.status_code >= 400:
                raise RuntimeError(f"素材上传失败 HTTP {response.status_code}: {response.text[:200]}")
            payload = response.json()
            url = payload.get("file_url") if isinstance(payload, dict) else None
            if not isinstance(url, str) or not url.startswith("https://"):
                # 上游要求 https 且不能重定向；拿到别的形态就地报错，
                # 比让它一路走到生成阶段再失败好查得多。
                raise RuntimeError(f"素材上传未返回 https 直链: {str(payload)[:200]}")
            urls.append(url)
    return urls


# 单个文件的上传超时。参考视频上限 100 MiB，出网慢时几十秒并不异常。
_UPLOAD_TIMEOUT_SEC = 120.0


async def _publish_self_hosted(paths: list[Path]) -> list[str]:
    """把素材签成本站自己的短时效直链（见 :mod:`lib.signed_media_url`）。

    不搬运文件：素材本就躺在数据根内，签一条指向它的 URL 就够。省掉一次全量拷贝，
    也不留下"同一张图两份副本"这种要另外清理的债。
    """
    urls: list[str] = []
    for path in paths:
        if not path.is_file():
            raise RuntimeError(f"参考素材不可读: {path.name}")
        urls.append(build_public_media_url(path))
    return urls


def uploader_from_env() -> ReferenceImageUploader | None:
    """按环境变量 ``ARCREEL_REFERENCE_HOSTING`` 选择实现。

    ``self``    —— 本站自签直链，需要 ``ARCREEL_PUBLIC_BASE_URL``
    ``gateway`` —— 网关的 ``/v1/sd/upload``
    ``none``    —— 关掉（单机自用、不接 matrix 时没有网关可用）

    不显式指定时：配了公网基址就走 ``self``，否则回落 ``gateway``。这个默认序不是
    随手排的 —— 网关直链所在的域名 H3 上游取不到（同域下路径存不存在都回 500），
    能自托管就不该走它。
    """
    mode = os.environ.get("ARCREEL_REFERENCE_HOSTING", "").strip().lower()
    if not mode:
        mode = "self" if public_base_url() else "gateway"
    if mode == "none":
        return None
    if mode == "self":
        if public_base_url() is None:
            # 不在启动期抛：一个可选能力的配置缺失不该让整站起不来。返回"未配置"，
            # 真用到时由 ReferenceHostingNotConfigured 给出可操作的说明。
            logger.error("ARCREEL_REFERENCE_HOSTING=self 但未配置 ARCREEL_PUBLIC_BASE_URL，参考图托管按未配置处理")
            return None
        return _publish_self_hosted
    if mode == "gateway":
        return _upload_via_gateway
    logger.warning("未知的参考图托管方式 %r，按网关方式处理", mode)
    return _upload_via_gateway
