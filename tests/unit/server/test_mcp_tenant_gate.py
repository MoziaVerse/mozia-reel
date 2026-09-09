"""远程 MCP 租户门：托管态下确定租户的唯一关口。

``MatrixSessionGate`` 对 ``/mcp`` 只做放行（该前缀自带 API Key 鉴权，不该被要求
matrix 会话），所以这里破了不会表现成报错——请求照样通过，只是 tenant 恒为 None，
三十个 ``remote_*`` 工具静默落到不带租户段的共享数据根上。用例锁的正是"解不出租户
必须拒绝"这条边界。
"""

from __future__ import annotations

import asyncio
from typing import NamedTuple

import pytest

from lib.tenant_api_key import build_api_key
from lib.tenant_context import current_tenant, set_current_tenant
from server.auth import API_KEY_PREFIX
from server.mcp_tenant_gate import McpTenantGate


@pytest.fixture(autouse=True)
def _managed_env(monkeypatch):
    # 门禁在 MATRIX_BACKEND_URL 为空时整条让开，用例必须显式接入才测得到拦截。
    monkeypatch.setenv("MATRIX_BACKEND_URL", "http://matrix.invalid")


@pytest.fixture(autouse=True)
def _clean_tenant():
    set_current_tenant(None)
    yield
    set_current_tenant(None)


class Probe(NamedTuple):
    reached: bool
    """是否放行到下游。"""
    status: int | None
    tenant: str | None
    """下游看到的租户——工具真正落库的依据。"""
    ensured: bool
    """是否跑过建库/迁移。"""


async def _probe(headers: list[tuple[bytes, bytes]]) -> Probe:
    sent: list[dict] = []
    reached = False
    seen_tenant: str | None = None
    ensured = False

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    async def downstream(scope, receive, send):
        nonlocal reached, seen_tenant
        reached = True
        seen_tenant = current_tenant()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def ensure_db():
        nonlocal ensured
        ensured = True

    gate = McpTenantGate(downstream, ensure_db=ensure_db)
    await gate({"type": "http", "path": "/mcp", "headers": headers}, receive, send)
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return Probe(reached, status, seen_tenant, ensured)


def _bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


class TestRejects:
    @pytest.mark.parametrize(
        "headers",
        [
            [],
            [(b"authorization", b"Bearer ")],
            [(b"authorization", b"Basic abc")],
            [(b"authorization", b"Bearer sk-not-ours")],
        ],
        ids=["no-header", "empty-token", "wrong-scheme", "foreign-key"],
    )
    def test_missing_or_foreign_credentials_get_401(self, headers):
        result = asyncio.run(_probe(headers))
        assert (result.reached, result.status, result.tenant) == (False, 401, None)

    def test_tenantless_key_rejected_in_managed_mode(self):
        """单机态形态的 key 在托管态无法定位租户，放过去就是落到共享数据根。"""
        result = asyncio.run(_probe(_bearer(build_api_key(API_KEY_PREFIX, None))))
        assert (result.reached, result.status) == (False, 401)

    def test_illegal_tenant_rejected(self):
        """租户会直接当目录名用，路径穿越必须在这一层就被挡掉。"""
        forged = f"{API_KEY_PREFIX}../escape-{'0' * 32}"
        assert asyncio.run(_probe(_bearer(forged))).reached is False

    def test_blocked_tenant_rejected(self, monkeypatch, tmp_path):
        """拒止名单对 MCP 同样生效——否则被踢的人换条链路就能继续用。"""
        blocklist = tmp_path / "blocklist.txt"
        blocklist.write_text("blocked-user\n", encoding="utf-8")
        monkeypatch.setenv("MATRIX_BLOCKLIST_FILE", str(blocklist))
        result = asyncio.run(_probe(_bearer(build_api_key(API_KEY_PREFIX, "blocked-user"))))
        assert (result.reached, result.status) == (False, 401)

    def test_rejected_request_never_touches_the_db(self):
        """拒绝路径不该建库：否则任何人拿伪造 key 就能凭空造出租户目录。"""
        assert asyncio.run(_probe([])).ensured is False


class TestPasses:
    def test_valid_key_sets_tenant_and_prepares_db(self):
        """下游（含上游那套 Bearer 鉴权与全部工具）必须看到已设好的租户。"""
        result = asyncio.run(_probe(_bearer(build_api_key(API_KEY_PREFIX, "user-1"))))
        assert (result.reached, result.status, result.tenant) == (True, 200, "user-1")
        # 新租户第一次直接调 MCP 时它的库还不存在，放行必须伴随建库检查。
        assert result.ensured is True

    def test_tenant_with_dashes_survives(self):
        assert asyncio.run(_probe(_bearer(build_api_key(API_KEY_PREFIX, "a-b-c")))).tenant == "a-b-c"

    def test_standalone_deployment_passes_through_untouched(self, monkeypatch):
        """未接入 matrix 时整条让开，上游单机行为不受本中间件影响。"""
        monkeypatch.delenv("MATRIX_BACKEND_URL", raising=False)
        result = asyncio.run(_probe([]))
        assert (result.reached, result.status, result.tenant) == (True, 200, None)
        # 单机态没有租户概念，不该顺手触发建库。
        assert result.ensured is False
