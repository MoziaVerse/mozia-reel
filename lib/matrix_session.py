"""Matrix 平台握手会话：换票、签名 cookie、网关供应商 seed。

与 `mozia-tts-studio` 同一套协议（见其 README 的架构图）：

    matrix /api/external/launch/<clientId>   → ticket（60s HMAC）
    本站 /handoff#h=<ticket>                 → POST /api/v1/matrix-session/init
    本站 → matrix /api/external/session-init → { user, apiKey, gateway, balance }

Mozia key **不进浏览器**，只落本进程的 DB（custom_provider 表）与内存。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "arcreel_matrix_session"

# 阶段一为单租户：全局唯一的网关供应商用这个 display_name 认领，
# 避免每次握手都新建一行。多租户改造（待办 T-1）时这里要改成按 user 维度。
GATEWAY_PROVIDER_DISPLAY_NAME = "Matrix 网关"


class MatrixHandoffError(RuntimeError):
    """换票失败。``status`` 用于原样回给前端，便于区分票据过期与 matrix 不可达。"""

    def __init__(self, message: str, status: int = 502, code: str = "handoff_failed") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


# ── 环境配置 ──────────────────────────────────────────────────────


def matrix_backend_url() -> str:
    return os.environ.get("MATRIX_BACKEND_URL", "").strip().rstrip("/")


def matrix_web_url() -> str:
    """matrix 前端地址；未握手的浏览器导航会被引回这里。"""
    return os.environ.get("MATRIX_WEB_URL", "https://matrix.mzsjai.com").strip().rstrip("/")


def external_client_id() -> str:
    return os.environ.get("EXTERNAL_CLIENT_ID", "mozia-reel").strip()


def matrix_launch_url(*, force_login: bool = False) -> str:
    """未握手的浏览器该被送去哪。

    送 matrix 的 launch 中继页而不是站点首页：中继页会自己处理"未登录先登录、
    已登录直接 mint ticket 跳回来"，形成闭环。送首页的话用户跳过去就没有回来的
    路了——得自己想起来去应用市场找卡片。

    force_login 用于"切换账号"：matrix 那边还留着会话时，默认路径会直接把同一个
    账号再送回来，用户没有换人的口子；带上 prompt=login 才会强制走登录表单。
    """
    suffix = "?prompt=login" if force_login else ""
    return f"{matrix_web_url()}/launch/{external_client_id()}{suffix}"


def session_ttl_seconds() -> int:
    raw = os.environ.get("SESSION_TTL_SECONDS", "").strip()
    try:
        value = int(raw) if raw else 604800
    except ValueError:
        value = 604800
    return max(60, value)


def _session_secret() -> bytes:
    """cookie 签名密钥。

    刻意不自动生成兜底值：单实例重启后 secret 变了会让所有人被登出，而这种
    "偶发全员掉线" 排查起来指不到根因。缺配置就明确拒绝启动握手功能。
    """
    secret = os.environ.get("SESSION_COOKIE_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "SESSION_COOKIE_SECRET 必须配置且不短于 32 字符"
            "（生成：openssl rand -base64 32）"
        )
    return secret.encode("utf-8")


def cookie_secure() -> bool:
    """生产走 https 时必须 Secure；本地 http 联调下 Secure cookie 种不上。"""
    return os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes"}


# ── 签名 cookie ────────────────────────────────────────────────────


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_session_cookie(*, sso_sub: str, username: str | None) -> str:
    """签一张 ``<payload>.<sig>`` 的会话票。

    只放身份与过期时间——**绝不放网关 key**：cookie 会到浏览器，key 必须留在服务端。
    """
    payload = {
        "sub": sso_sub,
        "name": username or "",
        "exp": int(time.time()) + session_ttl_seconds(),
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session_cookie(value: str | None) -> dict | None:
    """校验并返回 payload；任何一步不过一律返回 None（fail closed）。"""
    if not value or "." not in value:
        return None
    body, _, sig = value.partition(".")
    try:
        expected = hmac.new(_session_secret(), body.encode("ascii"), hashlib.sha256).digest()
    except RuntimeError:
        # secret 没配好时不要把异常抛进中间件——那会让整站 500 而不是引导去登录。
        logger.warning("SESSION_COOKIE_SECRET 未配置，会话一律判为未登录")
        return None
    # 比较用 compare_digest：避免按字节提前返回泄露签名前缀。
    if not hmac.compare_digest(_b64url_encode(expected), sig):
        return None
    try:
        payload = json.loads(_b64url_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    return payload


# ── 与 matrix 换票 ─────────────────────────────────────────────────


async def exchange_ticket(ticket: str) -> dict:
    """把 ticket 交给 matrix 换 ``{user, apiKey, gateway, balance}``。"""
    base = matrix_backend_url()
    if not base:
        raise MatrixHandoffError("MATRIX_BACKEND_URL 未配置", 500, "misconfigured")
    url = f"{base}/api/external/session-init"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json={"ticket": ticket})
    except httpx.HTTPError as exc:
        raise MatrixHandoffError(f"matrix 不可达: {exc}", 502, "matrix_unreachable") from exc
    if response.status_code >= 400:
        code, message = "handoff_failed", response.text[:200]
        try:
            body = response.json()
            code = body.get("error") or code
            message = body.get("message") or message
        except ValueError:
            pass
        raise MatrixHandoffError(message, response.status_code, code)
    return response.json()


# ── 把网关凭据 seed 成 custom_provider ─────────────────────────────

# mozia 网关是 new-api 中转，视频统一走 /v1/videos。而 infer_endpoint 是 content-first
# 启发式，会按模型名把 minimax-* 推到 minimax-video、seedance-* 推到 ark-seedance ——
# 那些都是**厂商原生**端点，打到中转站上是不存在的路径。所以视频类一律改写。
# 文本/图像/音频的推断结果与中转站形态一致，不动。
_VIDEO_ENDPOINT_ON_GATEWAY = "openai-video"


def _correct_endpoint(endpoint: str) -> str:
    """把厂商原生端点纠偏回网关实际提供的那条。"""
    from lib.custom_provider.endpoints import endpoint_to_media_type

    try:
        media_type = endpoint_to_media_type(endpoint)
    except (KeyError, ValueError):
        return endpoint
    if media_type == "video" and endpoint != _VIDEO_ENDPOINT_ON_GATEWAY:
        logger.info("endpoint 纠偏: %s → %s（网关是中转站，非厂商原生）", endpoint, _VIDEO_ENDPOINT_ON_GATEWAY)
        return _VIDEO_ENDPOINT_ON_GATEWAY
    return endpoint


async def _discover_gateway_models(*, base_url: str, api_key: str) -> list[dict]:
    """拉网关模型列表并纠偏 endpoint。失败不致命——供应商先建起来，用户可在 UI 手动补。"""
    from lib.custom_provider.discovery import discover_models

    try:
        models = await discover_models(discovery_format="openai", base_url=base_url, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 — 发现失败不该挡住握手
        logger.warning("网关模型发现失败，供应商将不带模型创建: %s", exc)
        return []

    corrected: list[dict] = []
    for item in models:
        endpoint = _correct_endpoint(item.get("endpoint", ""))
        corrected.append(
            {
                "model_id": item["model_id"],
                "display_name": item.get("display_name") or item["model_id"],
                "endpoint": endpoint,
                # is_default 交给用户在 UI 里定：discover 的默认标记按厂商习惯来，
                # 中转站上一批同类模型谁该是默认没有客观答案，硬塞一个反而误导。
                "is_default": False,
                "is_enabled": True,
            }
        )
    return corrected


# 各媒体类型的默认模型 setting key。细分档位（i2v/r2v/t2i/i2i/simple/complex）
# 刻意留空：它们的读取路径都会回落到这里的主默认值，预填反而会把"用户没选过"
# 和"用户选了同一个"混为一谈，日后想调主默认时细分档位会悄悄拦住。
_DEFAULT_BACKEND_KEYS = {
    "text": "default_text_backend",
    "image": "default_image_backend",
    "video": "default_video_backend",
    "audio": "default_audio_backend",
}


async def seed_default_backends(session, *, provider_id: int) -> dict[str, str]:
    """把各媒体类型的默认模型配好，让用户登录即可用。

    不配的话四个 default_*_backend 全是空串，生成入口会以"未配置模型"拒绝执行 ——
    而用户在托管态下并不知道要自己去设置页点一遍。这是"开箱即用"的关键一步。

    只在**当前为空**时写入：用户手动改过的选择不能被下次握手冲掉。
    """
    from lib.config.service import ConfigService
    from lib.custom_provider import make_provider_id
    from lib.custom_provider.endpoints import endpoint_to_media_type
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    repo = CustomProviderRepository(session)
    svc = ConfigService(session)
    pid = make_provider_id(provider_id)

    by_media: dict[str, list[str]] = {}
    for model in await repo.list_models(provider_id):
        if not model.is_enabled:
            continue
        try:
            media = endpoint_to_media_type(model.endpoint)
        except (KeyError, ValueError):
            continue
        by_media.setdefault(media, []).append(model.model_id)

    applied: dict[str, str] = {}
    for media, key in _DEFAULT_BACKEND_KEYS.items():
        model_ids = sorted(by_media.get(media, []))
        if not model_ids:
            continue
        current = (await svc.get_setting(key, "")).strip()
        if current:
            continue  # 用户已有选择，不覆盖
        option = f"{pid}/{model_ids[0]}"
        await svc.set_setting(key, option)
        applied[media] = option

    if applied:
        await session.commit()
        logger.info("默认模型已配置: %s", applied)
    return applied


# matrix 下发的长期只读余额凭据。存服务端而不是塞进 cookie：cookie 只做了签名
# 没有加密，payload 可被解出来 —— 身份信息无所谓，凭据不行。
# （canvas 把 key 和 walletToken 一起放 cookie，靠 AES-256-GCM 加密兜底；
#  我们的 key 本来就在 DB，没必要为一个 token 引入加密体系。）
_WALLET_TOKEN_SETTING = "matrix_wallet_token"


async def save_wallet_token(session, token: str | None) -> None:
    """保存余额凭据。matrix 未下发时清掉旧值，避免拿过期 token 一直查失败。"""
    from lib.config.service import ConfigService

    svc = ConfigService(session)
    await svc.set_setting(_WALLET_TOKEN_SETTING, token or "")
    await session.commit()


async def get_wallet_token(session) -> str | None:
    from lib.config.service import ConfigService

    value = (await ConfigService(session).get_setting(_WALLET_TOKEN_SETTING, "")).strip()
    return value or None


async def fetch_wallet_balance(token: str) -> dict:
    """凭 walletToken 拉实时余额。"""
    base = matrix_backend_url()
    if not base:
        raise MatrixHandoffError("MATRIX_BACKEND_URL 未配置", 500, "misconfigured")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"{base}/api/external/wallet",
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise MatrixHandoffError(f"matrix 不可达: {exc}", 502, "matrix_unreachable") from exc
    if response.status_code >= 400:
        raise MatrixHandoffError(response.text[:200], response.status_code, "wallet_failed")
    return response.json()


AGENT_CREDENTIAL_DISPLAY_NAME = "Matrix 网关"


async def seed_agent_credential(session, *, gateway: str, api_key: str, text_model: str | None) -> None:
    """把网关凭据配成 Agent 的 Anthropic 端点。

    网关支持 Anthropic 格式的 /v1/messages（已实测），所以 Agent 编排可以直接
    走同一把 key。不 seed 的话设置页会一直挂着"智能体未配置 API Key"的告警，
    而用户在托管态下根本没有可填的东西 —— 那个红点只会让人以为是自己漏配了。
    """
    from lib.db.repositories.agent_credential_repo import AgentCredentialRepository

    repo = AgentCredentialRepository(session)
    existing = next(
        (c for c in await repo.list_for_user() if c.display_name == AGENT_CREDENTIAL_DISPLAY_NAME),
        None,
    )
    # Anthropic SDK 要的是不带 /v1 的根地址，它自己会拼 /v1/messages。
    base = gateway.strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]

    if existing is not None:
        if existing.base_url != base or existing.api_key != api_key:
            await repo.update(existing.id, base_url=base, api_key=api_key)
            await session.commit()
        return

    cred = await repo.create(
        preset_id="__custom__",
        display_name=AGENT_CREDENTIAL_DISPLAY_NAME,
        base_url=base,
        api_key=api_key,
        # 各档统一指向网关上的文本模型：中转站不认 claude-* 的型号名，
        # 留空会让 SDK 用默认 claude 型号打过去，必然 404。
        model=text_model,
        haiku_model=text_model,
        sonnet_model=text_model,
        opus_model=text_model,
        subagent_model=text_model,
    )
    await repo.set_active(cred.id)
    await session.commit()
    logger.info("Agent 凭据已配置 (cred_id=%s, model=%s)", cred.id, text_model)


def pick_text_model(models: list[dict]) -> str | None:
    """从发现结果里挑一个文本模型给 Agent 用。

    取排序后的第一个而不是"最强的那个"：中转站上的型号名没有统一的强弱标记，
    猜错不如给个确定的默认值，用户可在设置页改。
    """
    text_ids = sorted(m["model_id"] for m in models if m.get("endpoint") == "openai-chat")
    return text_ids[0] if text_ids else None


async def seed_gateway_provider(session, *, gateway: str, api_key: str) -> None:
    """把握手拿到的网关凭据写成全局唯一的 custom_provider。

    幂等：按 ``GATEWAY_PROVIDER_DISPLAY_NAME`` 认领已有行，只更新 base_url / api_key，
    不重复 discover——用户可能已经在 UI 里手工调过模型的 endpoint 与默认项，
    每次握手都重刷会把这些调整冲掉。
    """
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    repo = CustomProviderRepository(session)
    existing = next(
        (p for p in await repo.list_providers() if p.display_name == GATEWAY_PROVIDER_DISPLAY_NAME),
        None,
    )

    if existing is not None:
        if existing.base_url != gateway or existing.api_key != api_key:
            await repo.update_provider(existing.id, base_url=gateway, api_key=api_key)
            await session.commit()
            logger.info("网关供应商凭据已更新 (provider_id=%s)", existing.id)
        # 已有供应商也要补默认值：早期握手过、但那时还没有这段逻辑的租户，
        # 以及平台后来才上架某类模型的情况，都靠这里补齐。
        await seed_default_backends(session, provider_id=existing.id)
        return

    models = await _discover_gateway_models(base_url=gateway, api_key=api_key)
    provider = await repo.create_provider(
        display_name=GATEWAY_PROVIDER_DISPLAY_NAME,
        discovery_format="openai",
        base_url=gateway,
        api_key=api_key,
        models=models or None,
    )
    await session.commit()
    logger.info("网关供应商已创建 (provider_id=%s, models=%d)", provider.id, len(models))
    await seed_default_backends(session, provider_id=provider.id)


async def seed_agent_credential_for_gateway(session, *, gateway: str, api_key: str) -> None:
    """便捷入口：读回该租户已 seed 的模型清单，挑一个文本模型配给 Agent。"""
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    repo = CustomProviderRepository(session)
    provider = next(
        (p for p in await repo.list_providers() if p.display_name == GATEWAY_PROVIDER_DISPLAY_NAME),
        None,
    )
    text_model = None
    if provider is not None:
        text_model = next(
            (
                m.model_id
                for m in sorted(await repo.list_models(provider.id), key=lambda x: x.model_id)
                if m.endpoint == "openai-chat" and m.is_enabled
            ),
            None,
        )
    await seed_agent_credential(session, gateway=gateway, api_key=api_key, text_model=text_model)
