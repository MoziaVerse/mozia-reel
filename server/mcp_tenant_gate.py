"""远程 MCP 的租户门（纯 ASGI 中间件）。

包在 ``app.mount("/mcp", …)`` 外面，在上游的 Bearer 鉴权跑起来之前，把 key 里的
租户段解出来设进 ContextVar。之后 ``_verify_api_key`` 打开的是该租户自己的库，
三十个 ``remote_*`` 工具也随之落在该租户的数据根上。

**为什么不改 ``server/remote_mcp.py``**：租户是托管态独有的概念，上游按单用户设计
（``docs/adr/0065-remote-mcp-trusts-existing-api-keys.md``：工具调用沿用
``DEFAULT_USER_ID``），那份文件每次同步上游都要整体跟走，把改动留在本文件里冲突面最小。

**为什么 ContextVar 在 ASGI 层设就够**：它沿本请求的整个调用链生效，与
``MatrixSessionGate`` 同一套做法；MCP SDK 自己的 ``get_access_token()`` 也正是靠同一
机制把鉴权结果送进工具函数，这条链路已被上游验证是通的。

⚠️ 本中间件必须 fail closed：``MatrixSessionGate`` 对 ``/mcp`` 只做放行（该前缀自带
鉴权，不该被要求 matrix 会话），托管态下这里是唯一一道确定租户的关口。解不出租户就
必须拒绝 —— 放过去的后果不是报错而是 tenant 恒为 None，工具静默落到不带租户段的
共享数据根上。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from lib.matrix_blocklist import is_allowed
from lib.matrix_session import matrix_backend_url
from lib.tenant_api_key import tenant_from_api_key
from lib.tenant_context import set_current_tenant
from server.auth import API_KEY_PREFIX

logger = logging.getLogger(__name__)

MCP_MOUNT_PREFIX = "/mcp"


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    for key, value in headers:
        if key.lower() != b"authorization":
            continue
        raw = value.decode("latin-1").strip()
        scheme, _, token = raw.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token.strip()
    return None


class TenantProjectManager:
    """把每次属性访问转发给**当前租户**的 ProjectManager。

    远程 MCP 的 server 在 host lifespan 里构造一次，那时没有请求、租户为 None，
    ``build_remote_mcp_server`` 于是把那一个实例固化进闭包供所有租户共用——工具因此
    全部落在不带租户段的共享数据根上，读写的是同一批项目。这不会报错：每个租户都能
    正常建项目、正常列出来，只是列出来的是所有人的。

    转发代理让每次访问都按当时的 ContextVar 取实例（``get_project_manager`` 本身
    按租户缓存），构造期被固化的就只剩代理本身。与 ``lib/db/engine.py`` 的
    ``TenantEngineProxy`` 同一套做法。
    """

    def __getattr__(self, name: str) -> Any:
        from lib.project_manager import get_project_manager

        return getattr(get_project_manager(), name)

    def __repr__(self) -> str:
        from lib.tenant_context import current_tenant

        return f"<TenantProjectManager tenant={current_tenant()!r}>"


def build_tenant_aware_mcp_server():
    """``RemoteMCPHost`` 的 server 工厂：把租户感知的 ProjectManager 注进上游构造。"""
    from lib.project_manager import ProjectManager
    from server.remote_mcp import build_remote_mcp_server

    return build_remote_mcp_server(projects=cast("ProjectManager", TenantProjectManager()))


async def _ensure_tenant_db() -> None:
    """``ensure_db`` 的生产默认值：建库并跑迁移。"""
    from lib.db import ensure_tenant_db

    await ensure_tenant_db()


class McpTenantGate:
    """给远程 MCP 定租户。单机态整条让开，行为与上游一致。"""

    def __init__(self, app, *, ensure_db: Callable[[], Awaitable[None]] = _ensure_tenant_db) -> None:
        self.app = app
        self._ensure_db = ensure_db

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 未接入 matrix（本地开发、单机自用）时 tenant 恒为 None，上游那套
        # ``arc-<32 hex>`` key 继续原样工作，不该被本中间件挡住。
        if not matrix_backend_url():
            await self.app(scope, receive, send)
            return

        token = _bearer_token(scope.get("headers") or [])
        tenant = tenant_from_api_key(token, API_KEY_PREFIX) if token else None
        if tenant is None:
            # 不区分"没带 key"与"key 里没有租户段"：两者对调用方都是同一件事
            # —— 这把 key 在本站不可用，且回它 401 才能让 MCP 客户端去换一把，
            # 而不是像 404 那样让人以为端点不存在。
            await self._unauthorized(send)
            return
        if not is_allowed(tenant):
            logger.info("拒止名单内的用户被拒: sub=%s path=%s", tenant, scope.get("path", ""))
            await self._unauthorized(send)
            return

        set_current_tenant(tenant)
        # 与 ``MatrixSessionGate`` 同样的理由：带着一把老 key 直接调用的人不会再走
        # 一次握手，上线带迁移的版本后他的租户库仍停在旧 schema。``/mcp`` 不在
        # ``/api/`` 前缀下，那边按路径做的迁移兜底覆盖不到这里，须自己补一次。
        await self._ensure_db()
        await self.app(scope, receive, send)

    async def _unauthorized(self, send) -> None:
        body = json.dumps(
            {
                "error": "api_key_required",
                "message": "远程 MCP 需要本站签发的 API Key，请在设置页重新创建并绑定",
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
                    (b"www-authenticate", b'Bearer realm="arcreel-mcp"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
