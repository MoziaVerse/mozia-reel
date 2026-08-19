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
    MatrixHandoffError,
    cookie_secure,
    exchange_ticket,
    issue_session_cookie,
    seed_gateway_provider,
    session_ttl_seconds,
)

logger = logging.getLogger(__name__)

# 换票端点，挂在 /api/v1 下。
public_router = APIRouter()
# 落地页，用户在地址栏直接落到 /handoff，不带 API 前缀。
page_router = APIRouter()


class InitBody(BaseModel):
    ticket: str


@public_router.post("/matrix-session/init")
async def init_session(
    body: InitBody,
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
            await seed_gateway_provider(tenant_session, gateway=gateway, api_key=api_key)

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


@page_router.get("/handoff", include_in_schema=False)
async def handoff_page(_request: Request) -> HTMLResponse:
    return HTMLResponse(
        _HANDOFF_HTML,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
