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
# 定义在 lib.matrix_constants（零依赖）并在此再导出：低层模块只为这一个字符串
# import 整个 matrix_session 会拖进 lib.custom_provider，撞分层契约。
from lib.matrix_base import GATEWAY_PROVIDER_DISPLAY_NAME as GATEWAY_PROVIDER_DISPLAY_NAME
from lib.matrix_base import session_signing_secret as session_signing_secret


def dev_bound_account() -> dict | None:
    """「绑定生产账号」模式（scripts/bind-prod-account.sh 写入的那套 env）。

    存在的理由是个实打实的测试痛点：测试服网关与生产网关**上架的模型不一样**
    （测试服只有文本与 TTS，视频那批 channel 只在生产）。对着测试服开发，
    视频链路根本跑不到。绑定后本地直接用生产网关与生产账号，模型清单与线上一致。

    只在本地开发用：它跳过握手，把请求一律认作这个账号。
    """
    sub = os.environ.get("DEV_BOUND_SSO_SUB", "").strip()
    api_key = os.environ.get("DEV_BOUND_API_KEY", "").strip()
    gateway = os.environ.get("DEV_BOUND_GATEWAY", "").strip()
    if not (sub and api_key and gateway):
        return None
    if not is_valid_tenant_id(sub):
        logger.error("DEV_BOUND_SSO_SUB 不是合法租户标识，绑定模式未启用: %r", sub)
        return None
    return {
        "sso_sub": sub,
        "username": os.environ.get("DEV_BOUND_USERNAME", "").strip() or None,
        "api_key": api_key,
        "gateway": gateway,
        "wallet_token": os.environ.get("DEV_BOUND_WALLET_TOKEN", "").strip() or None,
    }


def is_valid_tenant_id(value: str) -> bool:
    from lib.tenant_context import is_valid_tenant

    return is_valid_tenant(value)


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
    sig = _b64url_encode(hmac.new(session_signing_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_session_cookie(value: str | None) -> dict | None:
    """校验并返回 payload；任何一步不过一律返回 None（fail closed）。"""
    if not value or "." not in value:
        return None
    body, _, sig = value.partition(".")
    try:
        expected = hmac.new(session_signing_secret(), body.encode("ascii"), hashlib.sha256).digest()
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


def endpoint_to_media_type_safe(endpoint: str) -> str | None:
    """endpoint → media_type，未知 endpoint 返回 None 而不是抛错。"""
    from lib.custom_provider.endpoints import endpoint_to_media_type

    try:
        return endpoint_to_media_type(endpoint)
    except (KeyError, ValueError):
        return None


# matrix 的 model_type → 本地 endpoint。
#
# 平台按"调用形态"分类（见 matrix backend/src/lib/model-category.ts），我们按
# endpoint 分类，两者是同一件事的不同说法。multimodal 并入 chat —— 那类模型
# 本来就能纯文本对话，只是顺带能看图。
_MODEL_TYPE_TO_ENDPOINT = {
    "chat": "openai-chat",
    "multimodal": "openai-chat",
    "image": "openai-images",
    "video": _VIDEO_ENDPOINT_ON_GATEWAY,
    "audio": "openai-tts",
}

# 本地四条生成链路用不上的类目。不是"平台没分类"，而是分类明确、但 ArcReel
# 没有对应的用法：vision 是视觉专用（不给图什么也做不了），embedding/rerank
# 更不是生成模型。收进来只会让它们出现在模型下拉里，选中必失败。
_UNUSABLE_MODEL_TYPES = {"vision", "embedding", "rerank"}


async def fetch_model_catalog(token: str) -> list[dict] | None:
    """凭 walletToken 拉平台模型目录（含 model_type）。

    拿不到返回 None，由调用方回落到按模型名猜的旧路径 —— 目录是增强项，
    不该让握手因为它失败。
    """
    base = matrix_backend_url()
    if not base:
        return None
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{base}/api/external/catalog",
                headers={"Authorization": f"Bearer {token}"},
            )
        if response.status_code >= 400:
            logger.warning("拉取平台模型目录失败: %s %s", response.status_code, response.text[:150])
            return None
        models = response.json().get("models")
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("拉取平台模型目录失败: %s", exc)
        return None
    return models if isinstance(models, list) else None


def extract_quota_sources(item: dict) -> list[str] | None:
    """目录条目 → 可消耗额度分区列表。三态与 ``CustomProviderModel.quota_sources`` 同义。

    与 matrix 前端的 ``allowsGiftQuota`` 判据同源：没有 ``access`` 就不下结论（None），
    有 ``access`` 但没列 ``required_sources`` 等于网关未命中策略、默认放行全部分区（[]）。
    ``access.available`` 不参与——它是「此刻这个钱包付不付得起」的动态状态，
    分区归属才是模型的静态属性（同 ``is_enabled`` 那段注释的取舍）。
    """
    access = item.get("access")
    if not isinstance(access, dict):
        return None
    required = access.get("required_sources")
    if not isinstance(required, list):
        return []
    return [str(source) for source in required if isinstance(source, str)]


def allows_gift_quota(quota_sources: list[str] | None) -> bool | None:
    """赠送额度能否消耗该模型；None = 目录没标注，不下结论。"""
    if quota_sources is None:
        return None
    if not quota_sources:
        return True
    return "gift" in quota_sources


def catalog_to_models(catalog: list[dict]) -> list[dict]:
    """平台目录 → 本地模型行。不认识或用不上的类目直接不收。"""
    from lib.custom_provider.duration_presets import infer_supported_durations

    rows: list[dict] = []
    skipped: dict[str, list[str]] = {}
    unaffordable: list[str] = []
    for item in catalog:
        if not isinstance(item, dict):
            continue
        name = str(item.get("model_name") or "").strip()
        if not name:
            continue
        if item.get("enabled") is False:
            unaffordable.append(name)
        model_type = str(item.get("model_type") or "").strip()
        endpoint = _MODEL_TYPE_TO_ENDPOINT.get(model_type)
        if endpoint is None:
            skipped.setdefault(model_type or "unknown", []).append(name)
            continue
        rows.append(
            {
                "model_id": name,
                "display_name": str(item.get("display_name") or "").strip() or name,
                "endpoint": endpoint,
                # 视频模型必须带 supported_durations，剧本生成会硬校验它。平台目录不发
                # 这个字段（拿不到真值，见 external-model-catalog.ts 的说明），沿用启发式。
                "supported_durations": json.dumps(infer_supported_durations(name))
                if endpoint == _VIDEO_ENDPOINT_ON_GATEWAY
                else None,
                "is_default": False,
                # 一律启用。平台目录里的 `enabled` 不是上下架标记，而是
                # access.available ——「**此刻**这个钱包付不付得起这个模型」
                # （mozia-api 的 AnnotatePricingByMoziaWalletAccess 按余额分区算，
                # matrix 原样透传成 enabled）。那是随充值、赠送到期而变的动态状态，
                # 落进 is_enabled 就成了静态事实：用户充值后模型也不会自己回来，
                # 而禁用项在模型下拉里根本不渲染，表现为「模型莫名少了一半」。
                #
                # 上下架另有判据且已在 sync_gateway_models 里：模型不在本次目录里
                # 才是真下架。付不起不该在选择阶段拦，让它在生成时按网关的
                # requires_paid_quota 失败——那里有明确原因，这里没有。
                "is_enabled": True,
                "quota_sources": extract_quota_sources(item),
            }
        )
    for model_type, names in sorted(skipped.items()):
        logger.info("模型目录跳过 %s 类 %d 个（本地无对应链路）: %s", model_type, len(names), ", ".join(sorted(names)))
    # access 不进 is_enabled，但值得留一条痕迹：用户报「这个模型调不通」时，
    # 先看握手当时它是不是就已经付不起了，能省掉一轮网关排查。
    if unaffordable:
        logger.info(
            "平台标记当前钱包付不起的模型 %d 个（仍收录为可选）: %s",
            len(unaffordable),
            ", ".join(sorted(unaffordable)),
        )
    return rows


async def sync_gateway_models(session, *, provider_id: int, rows: list[dict]) -> None:
    """把平台目录合并进已有模型表。

    刻意不用 replace_models（删表重插）：那会把用户设的默认项一起抹掉。逐项合并：

    - 平台新上架的：补进来
    - 平台已下架的：标 is_enabled=False 而不是删除。项目里可能还引用着它，
      删掉会让那些项目的模型字段指向空气；禁用是可逆的、而且用户看得见。
    - 已有的：endpoint 与 display_name 跟平台走 —— endpoint 决定请求打到哪条路径，
      错了就是必然失败，这不是"用户偏好"可以覆盖的东西。is_default 保留不动。
    """
    from lib.db.models.custom_provider import CustomProviderModel
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    if not rows:
        return
    repo = CustomProviderRepository(session)
    existing = {m.model_id: m for m in await repo.list_models(provider_id)}
    incoming = {r["model_id"]: r for r in rows}

    added = updated = disabled = 0
    for model_id, row in incoming.items():
        current = existing.get(model_id)
        if current is None:
            session.add(CustomProviderModel(provider_id=provider_id, **row))
            added += 1
            continue
        changed = False
        if current.endpoint != row["endpoint"]:
            logger.info("模型 %s 的 endpoint 按平台目录纠正: %s → %s", model_id, current.endpoint, row["endpoint"])
            current.endpoint = row["endpoint"]
            # 换了链路，时长预设也要跟着换（视频→非视频时清空）
            current.supported_durations = row["supported_durations"]
            changed = True
        if current.display_name != row["display_name"]:
            current.display_name = row["display_name"]
            changed = True
        if not current.is_enabled and row["is_enabled"]:
            # 之前因下架被禁用、现在又上架了：恢复
            current.is_enabled = True
            changed = True
        # 额度分区跟平台走：网关随时可以调整某型号能吃哪个钱包分区，存量行不跟着更新，
        # 界面上的 gift/paid 标签就会停在握手那一刻。用 get 取值——回落路径
        # （``_discover_gateway_models``）的行不带这个键，那种来源本就没有分区信息。
        incoming_quota = row.get("quota_sources")
        if current.quota_sources != incoming_quota:
            current.quota_sources = incoming_quota
            changed = True
        updated += 1 if changed else 0

    for model_id, current in existing.items():
        if model_id not in incoming and current.is_enabled:
            current.is_enabled = False
            disabled += 1
            logger.info("模型 %s 已不在平台目录，标记为禁用", model_id)

    await session.flush()
    if added or updated or disabled:
        logger.info("模型目录同步: 新增 %d, 更新 %d, 禁用 %d", added, updated, disabled)


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
        # 视频模型必须带 supported_durations：剧本生成会硬校验它来定每段时长，
        # 空值直接 fail loud（"supported_durations is empty for ..."），视频链路
        # 对每个新用户都开箱不可用。discover 不返回这个字段，用上游现成的启发式补。
        durations = None
        if endpoint_to_media_type_safe(endpoint) == "video":
            from lib.custom_provider.duration_presets import infer_supported_durations

            durations = json.dumps(infer_supported_durations(item["model_id"]))

        corrected.append(
            {
                "model_id": item["model_id"],
                "display_name": item.get("display_name") or item["model_id"],
                "endpoint": endpoint,
                "supported_durations": durations,
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

# 各媒体类型的首选默认模型，按实测**开箱可用性**排序，命中即用；都不在就回落到
# 字典序第一个。
#
# 为什么需要这张表：字典序第一个跟"能不能用"毫无关系。实测网关上 5 个图像模型里
# 只有 mozia/image-2 在 ArcReel 的默认 720P 下可用 —— doubao/seedream-4.5 与
# seedream-5.0-lite 都要求 ≥3686400 像素（4K 级），而字典序恰好把 seedream-4.5
# 排在第一，等于给每个新用户配了一个开箱即挂的默认值。
#
# ⚠️ 这张表会随平台上架/下架漂移，不是长期真相。新增条目前先实测：
#     POST {gateway}/v1/images/generations  {"model":..., "size":"720x1280"}
# 表里的模型不存在时自动跳过，不会因为它下架而让 seed 失败。
_PREFERRED_DEFAULT_MODELS: dict[str, tuple[str, ...]] = {
    "image": ("mozia/image-2",),
    # 文本这条排序同时受三个约束，缺一个都会给新用户一个开箱不可用的默认值：
    #
    # 1) 死锁：GLM-4.7 在 Agent 的多层子任务嵌套下会**静默死锁**——不报错、
    #    不超时，就是没有输出。它绝不能进这张表；而按字典序挑恰好挑中它。
    #    （它在单轮工具调用上是正常的，别拿浅层验证的通过率把它放回来。）
    # 2) 工具调用链：只收 ``AGENT_MODEL_ALLOWLIST`` 里的型号，判据见那边。
    # 3) 赠送额度：网关按模型限定可消耗的钱包分区，只有少数模型允许 gift。
    #    新用户手里通常只有赠送额度，默认值落在 paid-only 上等于开箱就欠费。
    #
    # ⚠️ 当前这张表**一个 gift 档都没有**，不是排序没做好，是三条约束交集为空：
    # 网关上允许 gift 的文本模型只有 qwen 那几个和 GLM-4.7，前者卡在 messages 里的
    # system 消息上（见 ``AGENT_MODEL_ALLOWLIST``）、后者卡在死锁上。也就是说托管态
    # 下只有赠送额度的新用户，智能体开箱即欠费。解法在网关侧——把 messages 里的
    # system 并入首条 system，qwen 三档就能回来。在那之前这里只能全 paid 兜底。
    # ⚠️ gift 授权同样会漂移，改动前先核对网关的 mozia_model_quota_policies。
    "text": (
        "z-ai/glm-5.2",
        "z-ai/glm-5.1",
        "deepseek/deepseek-v4-pro",
        "qwen/qwen3.6-plus",
    ),
    # 视频只挑 H3，与画布（ZeoCanvasLite）同口径——那边实测下来也是只用 H3 出片。
    #
    # 不是"H3 更好"，是别的型号在这条链路上根本调不通：`openai-video` endpoint 底下
    # 既有真 Sora 也有中转来的各家型号，而 `_resolve_size` 只对 H3 做了特例，其余一律
    # 套 Sora 的固定档。seedance-2.0 收到 Sora 档的 size 后被上游拒成
    # `InvalidParameter: the parameter ratio ... not valid ... in t2v`（t2v/i2v 都拒），
    # 而它恰好在字典序第一位——没有这张表时每个新租户开箱拿到的就是它。
    # 画布那边 seedance 走的是另一套 provider（`/v2/videos/generations`），本地没实现。
    #
    # H3 另外两个好处正好对上前面两条约束：网关策略 `minimax/minimax-h3` 前缀是
    # gift,paid（新用户的赠送额度花得动），且按秒计价比 seedance 各档都便宜。
    #
    # ⚠️ 只列基座两款。`-fl2va-lora` / `-ref2va-lora` 已随 H3 更新下线（网关上启用渠道
    # 归零），画布侧也已从选择列表移除、默认档回到 fl2va——这里保持一致。
    "video": (
        "minimax/minimax-h3-fl2va",
        "minimax/minimax-h3-ref2va",
    ),
}


def preferred_model(media: str, available: set[str]) -> str | None:
    """按偏好挑一个存在的模型；都不在就返回 None，由调用方回落。"""
    return next((m for m in _PREFERRED_DEFAULT_MODELS.get(media, ()) if m in available), None)


# Agent 能跑通的文本模型白名单。判据只有一条：拿真实 CLI（不是手写的等价请求）
# 跑一轮带工具的对话能不能出结果——Agent 跑的是 Claude Code harness，链断了表现成
# 「发一句话就报错」或「点了没反应」，而不是回答质量差。
#
# ⚠️ 判据必须用真实 CLI 跑。手写一个「形状相同」的 HTTP 请求验不出下面这条：
# CLI 在带工具的请求里会往 messages 塞一条 role="system" 的消息（内容是它自己
# 生成的可用 agent 类型清单，Anthropic 的 messages 规范里本没有这个角色），
# 网关原样透传给上游，而 dashscope 系强制 system 只能在首位，直接 400。
#
# 落在名单外的，各有各的坏法，都不该出现在智能体的模型下拉里：
#   - qwen/qwen3.5-397b-a17b · qwen/qwen3.8-27b · qwen/qwen3.6-35b-a3b：
#     即上面那条，`System message must be at the beginning`，一轮都跑不完。
#     同族的 qwen3.6-plus 不在此列——它走的上游渠道不校验 system 位置。
#     这条是网关侧的转换缺陷（messages 里的 system 该并入首条 system 而非透传），
#     网关修好后这三个可以放回来，届时 gift 档才重新有得选。
#   - GLM-4.7：单轮工具调用正常，但在多层子任务嵌套下**静默死锁**（见
#     ``_PREFERRED_DEFAULT_MODELS`` 的注释），浅层验证看不出来
#   - 其余非对话类目：Agent SDK 走对话协议，选中即失败
#
# ⚠️ 一次性的上游抖动不算判据。kimi-k3 / kimi-k2.6 / deepseek-v4-flash 曾因
# `upstream error: do request failed` 被划掉，换真实 CLI 复验时三个都跑得通——
# 那是当时的上游故障，不是模型的固有缺陷。排除一个型号前先确认错误可复现。
#
# ⚠️ 平台上架/下架与上游可用性都会漂移，本名单不是长期真相。增删条目前先按
# 上面那条判据实跑，不要按参数量或价格猜。
AGENT_MODEL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "qwen/qwen3.6-plus",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "moonshotai/kimi-k3",
        "moonshotai/kimi-k2.6",
        "z-ai/glm-5",
        "z-ai/glm-5.1",
        "z-ai/glm-5.2",
    }
)


def agent_model_ready(model_id: str) -> bool:
    """该模型能否承载 Agent 的工具调用链。"""
    return model_id in AGENT_MODEL_ALLOWLIST


async def backfill_video_durations(session, *, provider_id: int) -> int:
    """给已有的视频模型补 supported_durations。

    seed 逻辑修好之前握手过的租户，库里这个字段全是空的，视频链路对他们依然
    开箱不可用（剧本生成硬校验它，空值直接 fail loud）。握手时顺带回填一次。

    只补空值：用户在 UI 上改过的档位不能被覆盖。
    """
    from lib.custom_provider.duration_presets import infer_supported_durations
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    repo = CustomProviderRepository(session)
    filled = 0
    for model in await repo.list_models(provider_id):
        if model.supported_durations:
            continue
        if endpoint_to_media_type_safe(model.endpoint) != "video":
            continue
        model.supported_durations = json.dumps(infer_supported_durations(model.model_id))
        filled += 1
    if filled:
        await session.commit()
        logger.info("已回填 %d 个视频模型的时长档位", filled)
    return filled


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

    # 旁白音色显式置空：上游默认是百炼的 "Cherry"，而托管态的音频走网关自建模型
    # （index-tts-v2 等），它们不接受任何 preset voice —— 带上直接 400
    # "preset voice not allowed: Cherry"。置空后 backend 会省略该字段、用模型自带音色。
    if "audio" in by_media and not (await svc.get_setting("narration_voice", "")).strip():
        await svc.set_setting("narration_voice", "")

    applied: dict[str, str] = {}
    for media, key in _DEFAULT_BACKEND_KEYS.items():
        model_ids = sorted(by_media.get(media, []))
        if not model_ids:
            continue
        preferred = preferred_model(media, set(model_ids))
        current = (await svc.get_setting(key, "")).strip()
        if current:
            continue  # 用户已有选择，不覆盖
        option = f"{pid}/{preferred or model_ids[0]}"
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


# 上游 mozia 的 log.type（见 mozia-api model/log.go）。一条流水里混着多种性质，
# 不区分会让页面很误导：失败记录 quota=0，混在消费记录里就是一串「0 消耗」，
# 看不出那其实是失败。1=Topup 3=Manage 4=System 7=Login 不会出现在 client token 的流水里。
_LOG_KINDS = {2: "consume", 5: "error", 6: "refund"}


def _log_kind(raw: object) -> str:
    return _LOG_KINDS.get(raw, "other") if isinstance(raw, int) else "other"


def _num(value: object, fallback: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else fallback


def normalize_logs(payload: dict) -> dict:
    """把 matrix /api/external/logs 的响应归一成前端要的形状。

    单条记录字段缺失不该让整页失败：逐字段兜底，坏记录降级成「未知模型 / 0 消耗」。
    quota → 积分用 quotaPerUnit/100，与钱包页同源（matrix src/lib/billing.ts）。
    """
    per_unit = _num(payload.get("quotaPerUnit"), 500000.0) or 500000.0
    per_credit = per_unit / 100 or 1.0

    items = []
    for raw in payload.get("items") or []:
        if not isinstance(raw, dict):
            continue
        quota = _num(raw.get("quota"))
        items.append(
            {
                "id": raw.get("id"),
                # 秒级 unix 时间戳，原样交给前端格式化——服务端不做时区假设
                "created_at": int(_num(raw.get("createdAt"))),
                "model_name": str(raw.get("modelName") or "").strip() or "unknown",
                "kind": _log_kind(raw.get("type")),
                "credits": quota / per_credit,
                "quota": quota,
                "prompt_tokens": int(_num(raw.get("promptTokens"))),
                "completion_tokens": int(_num(raw.get("completionTokens"))),
                "use_time": _num(raw.get("useTime")),
                "request_id": str(raw.get("requestId") or ""),
            }
        )
    return {
        "items": items,
        "total": int(_num(payload.get("total"))),
        "page": int(_num(payload.get("page"), 1)) or 1,
        "page_size": int(_num(payload.get("pageSize"), len(items))),
        "quota_per_unit": per_unit,
    }


async def fetch_wallet_logs(token: str, params: dict) -> dict:
    """凭 walletToken 拉调用记录。

    跨应用隔离由 matrix 侧按 ``client:<clientId>`` 强制完成（clientId 取自 token
    payload），这边传不了也不该传 tokenName。
    """
    base = matrix_backend_url()
    if not base:
        raise MatrixHandoffError("MATRIX_BACKEND_URL 未配置", 500, "misconfigured")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                f"{base}/api/external/logs",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as exc:
        raise MatrixHandoffError(f"matrix 不可达: {exc}", 502, "matrix_unreachable") from exc
    if response.status_code >= 400:
        raise MatrixHandoffError(response.text[:200], response.status_code, "logs_failed")
    return normalize_logs(response.json())


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


async def seed_gateway_provider(
    session, *, gateway: str, api_key: str, sso_sub: str, wallet_token: str | None = None
) -> None:
    """把握手拿到的网关凭据写成全局唯一的 custom_provider。

    幂等：按 ``GATEWAY_PROVIDER_DISPLAY_NAME`` 认领已有行，只更新 base_url / api_key。

    模型清单每次握手都跟平台目录对一次（``wallet_token`` 可用时）。早先这里是
    "已有就不再 discover"，理由是别冲掉用户手改——代价是目录永远停在首次握手
    那一刻：平台新上架的看不到、已下架的还列着、分类修正也传不过来。现在改成
    逐项合并（见 ``sync_gateway_models``），用户设的默认项照样保留。

    ``sso_sub`` 写入 ``owner_sso_sub``：每次握手都盖一遍——既补上迁移前的存量
    NULL 行，也让该字段永远跟"这次握手认领它的到底是谁"同步，供
    ``load_custom_backend`` 在装载时核对当前租户与这行的所有者是否一致
    （见该函数 docstring）。
    """
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    repo = CustomProviderRepository(session)
    existing = next(
        (p for p in await repo.list_providers() if p.display_name == GATEWAY_PROVIDER_DISPLAY_NAME),
        None,
    )

    if existing is not None:
        if existing.base_url != gateway or existing.api_key != api_key or existing.owner_sso_sub != sso_sub:
            await repo.update_provider(existing.id, base_url=gateway, api_key=api_key, owner_sso_sub=sso_sub)
            await session.commit()
            logger.info("网关供应商凭据已更新 (provider_id=%s)", existing.id)
        # 已有供应商也要补默认值：早期握手过、但那时还没有这段逻辑的租户，
        # 以及平台后来才上架某类模型的情况，都靠这里补齐。
        await _sync_from_catalog(session, provider_id=existing.id, wallet_token=wallet_token)
        await backfill_video_durations(session, provider_id=existing.id)
        await seed_default_backends(session, provider_id=existing.id)
        await session.commit()
        return

    models = await _models_for_new_provider(gateway=gateway, api_key=api_key, wallet_token=wallet_token)
    provider = await repo.create_provider(
        display_name=GATEWAY_PROVIDER_DISPLAY_NAME,
        discovery_format="openai",
        base_url=gateway,
        api_key=api_key,
        models=models or None,
        owner_sso_sub=sso_sub,
    )
    await session.commit()
    logger.info("网关供应商已创建 (provider_id=%s, models=%d)", provider.id, len(models))
    await backfill_video_durations(session, provider_id=provider.id)
    await seed_default_backends(session, provider_id=provider.id)


async def refresh_gateway_catalog(session) -> dict:
    """按平台目录刷新当前租户的模型清单，返回刷新前后的计数。

    存在的理由：模型清单只在握手那一刻同步过一次（``seed_gateway_provider``），
    之后平台新上架的模型，存量用户一个都看不到——界面上没有任何迹象表明"你该
    重新登录一次"，用户只会觉得说好的新模型没有。让设置页在打开时调一次这里，
    把"什么时候刷新"从"下次握手"变成"用户正要挑模型的时候"。

    复用握手那条链路的逐项合并（``sync_gateway_models``）：新上架的补进来、
    已下架的标禁用而非删除、用户设的默认项保留。所以重复调用是安全的。

    任何一步拿不到东西都只是"没刷新"，不抛错——这是增强项，不该让设置页打不开。
    """
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository

    repo = CustomProviderRepository(session)
    provider = next(
        (p for p in await repo.list_providers() if p.display_name == GATEWAY_PROVIDER_DISPLAY_NAME),
        None,
    )
    if provider is None:
        return {"refreshed": False, "reason": "no_gateway_provider"}

    token = await get_wallet_token(session)
    if not token:
        return {"refreshed": False, "reason": "no_wallet_token"}

    before = len(await repo.list_models(provider.id))
    catalog = await fetch_model_catalog(token)
    if catalog is None:
        return {"refreshed": False, "reason": "catalog_unavailable"}

    await sync_gateway_models(session, provider_id=provider.id, rows=catalog_to_models(catalog))
    await backfill_video_durations(session, provider_id=provider.id)
    # 补默认值：平台后来才上架某类模型时，该媒体的默认项此前一直是空的
    await seed_default_backends(session, provider_id=provider.id)
    await session.commit()

    after = len(await repo.list_models(provider.id))
    if after != before:
        logger.info("模型目录已刷新: %d → %d", before, after)
    return {"refreshed": True, "before": before, "after": after}


async def _sync_from_catalog(session, *, provider_id: int, wallet_token: str | None) -> None:
    """有 walletToken 就按平台目录对一次账；没有就维持现状。"""
    if not wallet_token:
        return
    catalog = await fetch_model_catalog(wallet_token)
    if catalog is None:
        return
    await sync_gateway_models(session, provider_id=provider_id, rows=catalog_to_models(catalog))


async def _models_for_new_provider(*, gateway: str, api_key: str, wallet_token: str | None) -> list[dict]:
    """首次建供应商时的模型清单。

    优先用平台目录：它带 model_type，是平台侧算好的分类。拿不到才回落到
    "网关 /v1/models + 按模型名猜"——上游的 supported_endpoint_types 普遍只回
    ["openai"]，猜出来会把 TTS、embedding、OCR 混进对话模型里。
    """
    if wallet_token:
        catalog = await fetch_model_catalog(wallet_token)
        if catalog:
            rows = catalog_to_models(catalog)
            if rows:
                return rows
    return await _discover_gateway_models(base_url=gateway, api_key=api_key)


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
        chat_models = [
            m.model_id for m in await repo.list_models(provider.id) if m.endpoint == "openai-chat" and m.is_enabled
        ]
        # 与 default_text_backend 用同一张偏好表：Agent 是子任务嵌套最深的地方，
        # 挑中会死锁的那个模型，症状是"点了没反应"，最难查。
        text_model = preferred_model("text", set(chat_models)) or next(iter(sorted(chat_models)), None)
    await seed_agent_credential(session, gateway=gateway, api_key=api_key, text_model=text_model)
