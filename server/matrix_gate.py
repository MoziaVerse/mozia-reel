"""Matrix 会话门禁（纯 ASGI 中间件）。

配合 ``AUTH_ENABLED=false`` 使用：ArcReel 自带的 auth 整条关掉，访问控制完全由这里
承担。好处是不动上游 auth 代码，overlay 保持薄；代价是本中间件必须 fail closed。

纯 ASGI 而非 BaseHTTPMiddleware：这是作用于全部请求的全局中间件，
BaseHTTPMiddleware 的 anyio TaskGroup + contextvars 复制会给每个请求加固定开销
（与同文件的 SPAShellNoCacheMiddleware 同样的取舍）。
"""

from __future__ import annotations

import json
import logging

from lib.matrix_blocklist import is_allowed
from lib.matrix_session import (
    SESSION_COOKIE_NAME,
    dev_bound_account,
    matrix_backend_url,
    matrix_launch_url,
    verify_session_cookie,
)
from lib.signed_media_url import MEDIA_URL_PREFIX
from lib.tenant_context import set_current_tenant

logger = logging.getLogger(__name__)

# 无需会话即可访问。握手页与换票端点必须公开——它们正是"拿到会话"的前提。
# ⚠️ 只放握手本身，不要放整个 /api/v1/matrix-session/ 前缀：同一前缀下还有
# overview 这类读取租户数据的端点，整段放行会让它们匿名可达（AUTH_ENABLED=false
# 时 FastAPI 层的依赖也不拦，这道门禁是唯一的访问控制）。
#
# 签名直链（MEDIA_URL_PREFIX）同样公开，但公开的理由不同：它的消费方是上游模型
# 服务，会话 cookie 与 Authorization header 都带不了，访问控制改由 URL 里的 HMAC
# token 承担。放行的是「持有有效 token」而不是「这段路径」——门禁在此让位，校验
# 落到端点内（见 server/routers/public_media.py）。
# 注意本分支会提前返回，即 tenant 不会被设置；直链的租户维度写在 token 的相对路径
# 里，端点自行拼回数据根，不依赖 ContextVar。
_PUBLIC_PREFIXES = (
    "/handoff",
    "/api/v1/matrix-session/init",
    "/health",
    "/skill.md",
    MEDIA_URL_PREFIX,
)


def _cookie_value(headers: list[tuple[bytes, bytes]], name: str) -> str | None:
    for key, value in headers:
        if key.lower() != b"cookie":
            continue
        for part in value.decode("latin-1").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
    return None


def _is_browser_navigation(headers: dict[bytes, bytes]) -> bool:
    """页面导航才适合 302 去登录。

    脚本 / SDK 拿到 302 会跟着跳，最后收到一坨 HTML，表现成"JSON 解析失败"这种
    指不到根因的错误——对它们回 401 更有用。判据与 matrix 的 instance-authz 一致：
    Sec-Fetch-Mode 更准但老浏览器和部分代理不发，所以两个都认。
    """
    if headers.get(b"sec-fetch-mode") == b"navigate":
        return True
    return b"text/html" in headers.get(b"accept", b"")


class MatrixSessionGate:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 绑定生产账号模式：本地开发直接以该账号身份运行，不走握手。
        # 注意它也跳过受邀名单——这条分支本来就把所有访问者当成同一个账号，
        # 名单在这里没有意义。**它只该出现在本地开发**：生产上一旦配了
        # DEV_BOUND_*，整条访问控制（不止名单）就形同虚设。
        # 放在最前面 —— 它同时也配了 MATRIX_BACKEND_URL（要用生产网关），
        # 落到下面的常规分支会被要求握手，而本地拿不到 ticket。
        bound = dev_bound_account()
        if bound is not None:
            set_current_tenant(bound["sso_sub"])
            await self.app(scope, receive, send)
            return

        # 未接入 matrix（本地开发、单机自用）时整条门禁关闭，避免把无关部署锁死。
        if not matrix_backend_url():
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path.startswith(_PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        payload = verify_session_cookie(_cookie_value(raw_headers, SESSION_COOKIE_NAME))
        if payload:
            # 租户 = ssoSub，由服务端从签名 cookie 解出，前端伪造不了。
            # 设在这里而不是路由层：ContextVar 沿本请求的整个调用链生效，
            # app_data_dir / DB engine / ProjectManager 会自动指向该租户的数据。
            sub = payload.get("sub")
            # 每请求查一遍（而不是只在握手时查）：否则把人加进拒止名单后，
            # 他手上那张 cookie 在整个 TTL 内仍然有效，踢不掉——而"能立刻踢掉"
            # 正是这份名单存在的唯一理由。
            if not is_allowed(sub):
                logger.info("拒止名单内的用户被拒: sub=%s path=%s", sub, path)
                headers = {k.lower(): v for k, v in raw_headers}
                if _is_browser_navigation(headers):
                    await self._access_denied_page(send)
                else:
                    await self._access_denied(send)
                return
            set_current_tenant(sub)
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in raw_headers}

        # 静态资源不敏感，放行以免 SPA 外壳半截加载失败；真正的数据都在 /api 下。
        if not path.startswith("/api/") and not _is_browser_navigation(headers):
            await self.app(scope, receive, send)
            return

        if _is_browser_navigation(headers):
            await self._redirect_to_matrix(send)
            return
        await self._unauthorized(send)

    async def _redirect_to_matrix(self, send) -> None:
        # 送 matrix 的 launch 中继页（/launch/<clientId>）：它会处理"未登录先登录、
        # 已登录直接 mint ticket 跳回本站 /handoff"，用户直接访问本站域名也能进来。
        # 早先送的是 matrix 首页，结果是"跳过去就没有回来的路"。
        await send(
            {
                "type": "http.response.start",
                "status": 302,
                "headers": [
                    (b"location", matrix_launch_url().encode("utf-8")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    # 被拒的人不能再往 matrix 跳：那边会把他登录后原样送回来，来回弹一遍还是这一页。
    # 所以直接给一个说明白的终点页 / JSON。
    _ACCESS_DENIED_HTML = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>访问受限</title><style>:root{color-scheme:light dark}"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
        "font:15px/1.6 system-ui,-apple-system,'PingFang SC',sans-serif;background:#fafafa;color:#18181b}"
        "@media(prefers-color-scheme:dark){body{background:#09090b;color:#fafafa}}"
        ".b{text-align:center;padding:2rem;max-width:30rem}.m{opacity:.7;font-size:13px;margin-top:.5rem}"
        '</style></head><body><div class="b"><p>本应用当前仅对受邀用户开放。</p>'
        '<p class="m">如需使用，请联系管理员开通。</p></div></body></html>'
    ).encode()

    async def _access_denied_page(self, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(self._ACCESS_DENIED_HTML)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": self._ACCESS_DENIED_HTML})

    async def _access_denied(self, send) -> None:
        body = json.dumps(
            {"error": "access_denied", "message": "你的账号暂时无法使用本应用"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _unauthorized(self, send) -> None:
        body = json.dumps(
            {
                "error": "matrix_session_required",
                "message": "会话缺失或已过期，请从 Matrix 应用市场重新打开 ArcReel",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
