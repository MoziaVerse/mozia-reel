"""参考素材的公网直链：本站自签的短时效 URL。

H3 这类经网关中转的上游不收 multipart，参考图只能以「上游自己能拉到的公网 https
URL」提交（约束见 :mod:`lib.reference_image_hosting`）。这里提供那条 URL。

**为什么不用网关的 /v1/sd/upload** —— 它产出的直链落在 ``cdn.mjapi.cc.cd`` 域下，
而 H3 上游取不到该域名：同域下无论路径存在与否都回 500，连「图不存在」这一步都没
走到；换成任意其它公网 https 图，同一条请求即可提交成功。所以参考图改由本站自己
出链，与画布走 ``canvas.mzsjai.com`` 的形态同构 —— 两者都解析到部署所在的公网
地址，是上游取得到的那一类。

**为什么不复用 ``server/auth.py`` 的下载 token** —— 那套的校验在
``AUTH_ENABLED=false`` 时直接放行任意 token，而本发行版恰恰是关掉自带 auth、由
``MatrixSessionGate`` 承担访问控制的形态，复用等于这条链完全不设防。这里改用握手
cookie 的同一把 secret，并从中派生独立子键做域分离：媒体 token 与会话 cookie 互不
可冒充。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from pathlib import Path

from lib.app_data_dir import base_data_dir
from lib.path_safety import PathTraversalError, safe_join

logger = logging.getLogger(__name__)

# 直链的 URL 路径前缀。门禁按前缀放行匿名访问，改这里要同步 server/matrix_gate.py
# （那边直接 import 本常量，不另写一份字面量）。
MEDIA_URL_PREFIX = "/public/media/"

# token 有效期。两头夹：URL 在提交那一刻才签发，但上游的提交是同步的（收到请求后
# 要把素材下载下来、归一、上传给推理集群），短了会在提交途中过期；长了则拉长直链
# 泄露之后的可读窗口。
DEFAULT_TTL_SECONDS = 6 * 60 * 60

# 派生子键的用途串：与会话 cookie 共用一把 secret，靠它把两者的签名空间隔开。
_KEY_INFO = b"arcreel:signed-media-url:v1"


def public_base_url() -> str | None:
    """本站对外可达的 https 基址（形如 ``https://reel.example.com``）。

    没配就返回 ``None``：自托管直链需要一个上游能拉到的公网地址，这个值猜不出来，
    也不该猜 —— 猜错的后果是每次生成都要走到提交那一步才失败。
    """
    raw = os.environ.get("ARCREEL_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not raw:
        return None
    if not raw.startswith("https://"):
        logger.warning("ARCREEL_PUBLIC_BASE_URL 必须是 https 基址，当前为 %r，按未配置处理", raw)
        return None
    return raw


def _signing_key() -> bytes:
    from lib.matrix_session import session_signing_secret

    return hmac.new(session_signing_secret(), _KEY_INFO, hashlib.sha256).digest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign_media_path(path: Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """把数据根内的一个文件签成 ``<payload>.<sig>`` token。

    路径以「相对数据根」的形式入 token：租户目录本就在这条相对路径里，校验时再拼
    回去即可，既不必额外带租户字段，也不会把宿主的绝对路径写进对外 URL。

    Raises:
        ValueError: 目标不在数据根内 —— 那种路径不该被签出去。
    """
    root = base_data_dir()
    try:
        resolved = safe_join(root, path, require_file=True)
    except PathTraversalError as exc:
        raise ValueError(f"参考素材不在数据根内，无法出链: {path}") from exc
    payload = {"p": resolved.relative_to(root).as_posix(), "exp": int(time.time()) + ttl_seconds}
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def resolve_media_token(token: str) -> Path | None:
    """校验 token 并返回磁盘路径；任何一步不过一律返回 ``None``（fail closed）。

    不区分「签名不对」「已过期」「文件不在」三种失败：调用方一律回 404，免得响应
    差异变成数据根内的文件存在性探针。
    """
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    try:
        encoded_body = body.encode("ascii")
    except UnicodeEncodeError:
        return None
    try:
        expected = hmac.new(_signing_key(), encoded_body, hashlib.sha256).digest()
    except RuntimeError:
        # secret 没配好时不要把异常抛给匿名请求方：这条链本就该 fail closed。
        logger.warning("SESSION_COOKIE_SECRET 未配置，签名直链一律判为无效")
        return None
    # compare_digest：避免按字节提前返回泄露签名前缀。
    if not hmac.compare_digest(_b64url_encode(expected), sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    # 走到这里 payload 已被签名认领，取值不必再做防御性解析。
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    relative = payload.get("p")
    if not isinstance(relative, str) or not relative:
        return None
    try:
        return safe_join(base_data_dir(), relative, require_file=True)
    except (PathTraversalError, FileNotFoundError, TypeError):
        return None


def build_public_media_url(path: Path, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """签出完整的公网直链。基址未配置时明确报错，不返回半截 URL。"""
    base = public_base_url()
    if base is None:
        raise RuntimeError("自托管参考图直链需要配置 ARCREEL_PUBLIC_BASE_URL（本站对外的公网 https 基址）")
    return f"{base}{MEDIA_URL_PREFIX}{sign_media_path(path, ttl_seconds=ttl_seconds)}"
