"""Matrix 握手落地页与换票端点。

落地页刻意由后端返回内联 HTML，而不是在前端 SPA 里加一条路由：前端是 pnpm 构建
产物，加路由要改源码 + rebuild，overlay 会变厚、与上游冲突面也更大。这一页只做
"读 hash → 换票 → 跳首页"，没有复用价值，放后端最省。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import ensure_tenant_db, get_async_session, safe_session_factory
from lib.tenant_context import is_valid_tenant, tenant_scope
from lib.matrix_session import (
    SESSION_COOKIE_NAME,
    fetch_wallet_balance,
    fetch_wallet_logs,
    get_wallet_token,
    save_wallet_token,
    MatrixHandoffError,
    cookie_secure,
    exchange_ticket,
    issue_session_cookie,
    matrix_launch_url,
    seed_agent_credential_for_gateway,
    seed_gateway_provider,
    session_ttl_seconds,
)

logger = logging.getLogger(__name__)

# 需要登录态的端点（如托管态总览），挂在 /api/v1 下并由 app 侧加认证依赖。
router = APIRouter()
# 换票端点，挂在 /api/v1 下，必须匿名可达。
public_router = APIRouter()
# 落地页，用户在地址栏直接落到 /handoff，不带 API 前缀。
page_router = APIRouter()


class InitBody(BaseModel):
    ticket: str


@public_router.post("/matrix-session/init")
async def init_session(
    body: InitBody,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_async_session),
):
    """用 matrix ticket 换网关凭据，seed 供应商，签会话 cookie。"""
    if not body.ticket.strip():
        return JSONResponse({"error": "bad_request", "message": "ticket 不能为空"}, status_code=400)

    try:
        payload = await exchange_ticket(body.ticket.strip())
    except MatrixHandoffError as exc:
        logger.warning("握手换票失败: code=%s status=%s", exc.code, exc.status)
        return JSONResponse({"error": exc.code, "message": str(exc)}, status_code=exc.status)

    user = payload.get("user") or {}
    api_key = (payload.get("apiKey") or {}).get("key") or ""
    gateway = payload.get("gateway") or ""
    if not api_key or not gateway:
        # matrix 返 200 但缺字段：属于契约破裂，明确报出来而不是让用户进到一个
        # "能进但生成全失败" 的站点——后者的报错会散落在每次生成里，指不到根因。
        logger.error("matrix session-init 响应缺少 apiKey/gateway")
        return JSONResponse(
            {"error": "handoff_incomplete", "message": "matrix 未返回网关凭据"}, status_code=502
        )

    sso_sub = user.get("ssoSub") or user.get("id") or ""
    if not is_valid_tenant(sso_sub):
        logger.error("matrix 返回的 ssoSub 不能作为租户标识: %r", sso_sub)
        return JSONResponse(
            {"error": "invalid_tenant", "message": "身份标识非法"}, status_code=502
        )

    # 切到该租户后再建库与 seed —— 否则会写到部署级默认库上。
    # 注意 seed 用的 session 是请求依赖注入的，绑在**切换前**的 engine 上，
    # 所以这里不能复用它，要在租户上下文里重新开。
    with tenant_scope(sso_sub):
        await ensure_tenant_db()
        async with safe_session_factory() as tenant_session:
            # walletToken 一并传进去：模型目录（含平台算好的 model_type）就是凭它拉的，
            # 拿不到会回落到按模型名猜，猜出来必然把 TTS / embedding / OCR 混进对话模型。
            await seed_gateway_provider(
                tenant_session,
                gateway=gateway,
                api_key=api_key,
                wallet_token=payload.get("walletToken"),
            )
            # Agent 编排走同一把 key（网关支持 Anthropic 格式的 /v1/messages）。
            # 不配的话设置页会一直挂"智能体未配置"的红点，而用户无从填写。
            await seed_agent_credential_for_gateway(tenant_session, gateway=gateway, api_key=api_key)
            # 余额凭据存服务端：cookie 只签名未加密，不放凭据。
            await save_wallet_token(tenant_session, payload.get("walletToken"))

    # 新租户首次握手：拉起它自己的生成 worker，否则它提交的任务没人处理。
    supervisor = getattr(request.app.state, "worker_supervisor", None)
    if supervisor is not None:
        await supervisor.ensure_started(sso_sub)

    cookie = issue_session_cookie(sso_sub=sso_sub, username=user.get("username"))
    response.set_cookie(
        SESSION_COOKIE_NAME,
        cookie,
        max_age=session_ttl_seconds(),
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
        path="/",
    )
    return {"ok": True, "user": {"username": user.get("username"), "ssoSub": sso_sub}}


# 落地页：ticket 在 URL fragment 里（`#h=...`），fragment 浏览器不会发给服务端，
# 所以必须由页面上的 JS 读出来再 POST——这也正是把 ticket 放 fragment 的用意：
# 它不会进入服务端访问日志、Referer 或反代日志。
_HANDOFF_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>正在进入 ArcReel</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         font: 15px/1.6 system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:#fafafa; color:#18181b; }
  @media (prefers-color-scheme: dark) { body { background:#09090b; color:#fafafa; } }
  .box { text-align:center; padding:2rem; max-width:32rem; }
  .msg { opacity:.7; }
  .err { color:#dc2626; white-space:pre-wrap; text-align:left; }
  a { color:inherit; }
</style></head>
<body><div class="box">
  <p id="msg" class="msg">正在验证身份…</p>
  <p id="err" class="err" hidden></p>
</div>
<script>
(async function () {
  var msg = document.getElementById('msg'), err = document.getElementById('err');
  function fail(text) {
    msg.hidden = true; err.hidden = false;
    err.textContent = text + '\\n\\n请回到 Matrix 应用市场重新打开。';
  }
  var hash = String(location.hash || '').replace(/^#/, '');
  var ticket = new URLSearchParams(hash).get('h');
  if (!ticket) { fail('缺少握手票据。'); return; }
  try {
    var res = await fetch('/api/v1/matrix-session/init', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticket: ticket })
    });
    var data = await res.json().catch(function () { return {}; });
    if (!res.ok) { fail('握手失败：' + (data.message || res.status)); return; }
    // 用 replace 而不是 assign：票据是一次性的，留在历史里会让"后退"落到一个
    // 必然失败的页面。
    location.replace('/');
  } catch (e) { fail('网络错误：' + e); }
})();
</script></body></html>
"""


@router.post("/matrix-session/logout")
async def logout(response: Response):
    """登出：清会话 cookie。

    只清 cookie，不动租户数据 —— 同一个人下次登录还要看到自己的项目。
    网关 key 留在服务端该租户的库里，浏览器侧本来就没有，所以不存在
    "登出后仍能拿旧 key 发起生成"的窗口。
    """
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    # 前端拿这个地址把人送回去。带 prompt=login 强制走登录表单，否则 matrix
    # 那边还留着会话，会直接把同一个账号再送回来 —— 用户就没有换人的口子。
    return {"ok": True, "login_url": matrix_launch_url(force_login=True)}


@router.get("/matrix-session/credits")
async def credits(session: AsyncSession = Depends(get_async_session)):
    """当前用户在 Matrix 的实时余额。

    走服务端存的 walletToken（scope=wallet 的只读凭据），不下发给浏览器。
    拿不到就明确回 unavailable，让前端隐藏余额而不是显示成 0 —— 后者会被
    当成"没钱了"。
    """
    token = await get_wallet_token(session)
    if not token:
        return {"available": False, "reason": "no_wallet_token"}
    try:
        data = await fetch_wallet_balance(token)
    except MatrixHandoffError as exc:
        logger.warning("拉取余额失败: %s", exc)
        return {"available": False, "reason": exc.code}
    return {"available": True, "wallet": data}


@router.get("/matrix-session/usage")
async def usage(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    """当前用户在本应用的调用记录（用量页数据源）。

    数据取自平台账务，而不是本地账本 —— 本地记的是"我们以为花了多少"，平台记的
    是"实际扣了多少"，两者对不上时用户信的当然是账单那份。

    与余额同一条链路：walletToken 是 scope=wallet 的只读凭据，只在服务端用。
    跨应用隔离由 matrix 侧按 client 过滤强制完成。
    """
    token = await get_wallet_token(session)
    if not token:
        # 与余额不同：用量页是用户主动打开的功能页，拿不到数据要让他知道原因，
        # 而不是静默显示成空列表——后者看起来像"你还没用过"。
        return JSONResponse(
            {"error": "no_wallet_token", "message": "请重新从 Matrix 进入本应用后查看"},
            status_code=409,
        )

    # 白名单转发：tokenName 之类必须由 matrix 按 token 身份决定，客户端不该有机会影响。
    allowed = ("page", "pageSize", "modelName", "startTimestamp", "endTimestamp", "type")
    params = {k: v for k, v in request.query_params.items() if k in allowed and v}
    try:
        return await fetch_wallet_logs(token, params)
    except MatrixHandoffError as exc:
        logger.warning("拉取调用记录失败: %s", exc)
        return JSONResponse(
            {"error": exc.code, "message": "暂时取不到记录"},
            status_code=409 if exc.status == 401 else 502,
        )


@router.get("/matrix-session/overview")
async def session_overview(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
):
    """设置页用的托管态总览。

    Matrix 形态下用户没有"配置供应商"这回事 —— 网关是平台发的、模型是平台
    上架的、计费在平台侧。所以这里只回可展示的事实，不回任何可编辑项：
    前端据此渲染只读卡片，而不是一个填了也没用的表单。

    非 Matrix 部署返回 enabled=false，前端退回原来的供应商配置界面。
    """
    from lib.custom_provider.endpoints import endpoint_to_media_type
    from lib.db.repositories.custom_provider_repo import CustomProviderRepository
    from lib.matrix_capabilities import matrix_mode_enabled
    from lib.matrix_session import (
        GATEWAY_PROVIDER_DISPLAY_NAME,
        dev_bound_account,
        matrix_web_url,
        verify_session_cookie,
    )

    if not matrix_mode_enabled():
        return {"enabled": False}

    # 账户信息只来自服务端已验证的来源：签名 cookie 的 payload，或本地绑定账号的
    # env。不从请求体/查询串取任何一个字段 —— 那样前端就能自报身份。
    identity = verify_session_cookie(request.cookies.get(SESSION_COOKIE_NAME)) or {}
    bound = dev_bound_account()
    user = {
        "username": (bound or {}).get("username") or identity.get("username"),
        "sso_sub": (bound or {}).get("sso_sub") or identity.get("sub"),
    }

    repo = CustomProviderRepository(session)
    provider = next(
        (p for p in await repo.list_providers() if p.display_name == GATEWAY_PROVIDER_DISPLAY_NAME),
        None,
    )

    media_counts: dict[str, int] = {"text": 0, "image": 0, "video": 0, "audio": 0}
    models: list[dict] = []
    if provider is not None:
        for m in await repo.list_models(provider.id):
            if not m.is_enabled:
                continue
            try:
                media = endpoint_to_media_type(m.endpoint)
            except (KeyError, ValueError):
                continue
            media_counts[media] = media_counts.get(media, 0) + 1
            models.append(
                {
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "media_type": media,
                }
            )
    models.sort(key=lambda x: (x["media_type"], x["model_id"]))

    return {
        "enabled": True,
        "connected": provider is not None,
        "user": user,
        # 只回主机名：网关地址不是秘密，但也没有让用户看到完整 URL 的必要，
        # 而 api_key 一律不出服务端。
        "gateway_host": _host_only(provider.base_url) if provider else None,
        "media_counts": media_counts,
        "models": models,
        "matrix_web_url": matrix_web_url(),
    }


def _host_only(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url if "://" in url else f"//{url}")
    return parsed.netloc or url


@page_router.get("/handoff", include_in_schema=False)
async def handoff_page(_request: Request) -> HTMLResponse:
    return HTMLResponse(
        _HANDOFF_HTML,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
