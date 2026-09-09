"""远程 MCP 的租户寻址：从 key 到租户库的完整链路。

单测分别覆盖了租户门的判定与上游 MCP 自身，但两者的衔接——"带租户段的 key → 门解出
租户 → ``_verify_api_key`` 打开正确那份租户库 → 查到这把 key"——只有在这里才被真正
执行过。它是整套改动的核心主张：托管态下 ``api_keys`` 表落在租户库里，不先定出租户
就无从验 key。

所以这里刻意不用替身：建库走真实的 ``ensure_tenant_db``，验 key 走上游真实的
``ArcApiKeyVerifier``。替换掉任何一环，验的就不再是这条链路。
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from lib.db import async_session_factory, ensure_tenant_db
from lib.db.repositories.api_key_repository import ApiKeyRepository
from lib.tenant_api_key import build_api_key
from lib.tenant_context import current_tenant, set_current_tenant
from server.auth import API_KEY_PREFIX, _hash_api_key
from server.mcp_tenant_gate import (
    McpTenantGate,
    TenantProjectManager,
    build_tenant_aware_mcp_server,
)
from server.remote_mcp import ArcApiKeyVerifier

_TENANT_SEQ = itertools.count()


@pytest.fixture
def new_tenant():
    """签发一个进程内唯一的租户名。

    租户 engine 缓存在 ``lib/db/engine.py`` 的模块级 dict 里、按租户名索引，而
    ``_reset_tenant_db_cache_for_tests`` 只清"哪些租户迁移过"。同名租户在下一个用例里
    会命中上一个用例建的 engine，于是写进已经换掉的 ``tmp_path`` 的旧库——表现成
    莫名其妙的 UNIQUE 冲突，而不是路径错误。
    """
    return lambda: f"tenant-{next(_TENANT_SEQ)}"


_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MATRIX_BACKEND_URL", "https://matrix.invalid")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # create_project 会把 agent_runtime_profile 物化进项目目录；数据根指到 tmp 之后
    # 它会跟着落空，报成 "Profile dir empty" 而不是租户问题。源目录只读，指回仓库。
    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(_REPO_ROOT / "agent_runtime_profile"))
    from lib import app_data_dir as add
    from lib import db as libdb

    add._reset_for_tests()
    libdb._reset_tenant_db_cache_for_tests()
    yield
    set_current_tenant(None)
    add._reset_for_tests()
    libdb._reset_tenant_db_cache_for_tests()


async def _seed_key(tenant: str, *, name: str = "codex") -> str:
    """在指定租户的库里真实签发一把 key，返回完整明文。"""
    set_current_tenant(tenant)
    await ensure_tenant_db()
    key = build_api_key(API_KEY_PREFIX, tenant)
    async with async_session_factory() as session:
        async with session.begin():
            await ApiKeyRepository(session).create(
                name=name,
                key_hash=_hash_api_key(key),
                key_prefix=key[:8],
                expires_at=None,
            )
    set_current_tenant(None)
    return key


async def _authenticate_through_gate(token: str | None) -> tuple[int | None, str | None, object]:
    """经真实租户门跑一次，在下游用上游真实的 verifier 验 key。

    返回 (状态码, 下游看到的租户, verifier 结果)。门拒绝时后两项为 None。
    """
    sent: list[dict] = []
    seen_tenant: str | None = None
    verified: object = None

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    async def downstream(scope, receive, send):
        nonlocal seen_tenant, verified
        seen_tenant = current_tenant()
        verified = await ArcApiKeyVerifier().verify_token(token or "")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    # 用生产默认的 ensure_db：建库这一步本身也在被验证。
    await McpTenantGate(downstream)({"type": "http", "path": "/mcp", "headers": headers}, receive, send)
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return status, seen_tenant, verified


async def test_key_resolves_its_own_tenant_db_and_authenticates(tmp_path, new_tenant):
    """核心链路：签发时落在哪个租户库，验证时就得从哪个租户库查出来。"""
    a = new_tenant()
    key = await _seed_key(a)

    status, tenant, verified = await _authenticate_through_gate(key)

    assert (status, tenant) == (200, a)
    assert verified is not None, "上游 verifier 没查到 key —— 说明门打开的不是签发它的那份库"
    assert (tmp_path / "tenants" / a / ".arcreel.db").exists()


async def test_each_tenant_authenticates_against_its_own_db(new_tenant):
    """两个租户各自签发，各自都能过——证明门是按 key 切库，而不是固定落在某一份上。"""
    a, b = new_tenant(), new_tenant()
    key_a = await _seed_key(a)
    key_b = await _seed_key(b)

    status_a, tenant_a, verified_a = await _authenticate_through_gate(key_a)
    status_b, tenant_b, verified_b = await _authenticate_through_gate(key_b)

    assert (status_a, tenant_a) == (200, a)
    assert (status_b, tenant_b) == (200, b)
    assert verified_a is not None and verified_b is not None


async def test_forged_tenant_segment_cannot_reach_another_tenants_db(new_tenant):
    """把租户段改成别人的，门会照改后的值切库，而那份库里没有这把 key。

    锁的是跨租户串数据：这类问题不会报错，只会安静地把别人的项目端出来。
    key_hash 算的是整串明文，改租户段就改了 hash，于是在目标库里必然查不到——
    这道防线本来就该在，用例把它钉住，避免日后有人改成只对 secret 段做哈希。
    """
    a, b = new_tenant(), new_tenant()
    key_a = await _seed_key(a)
    await _seed_key(b, name="victim")
    forged = f"{API_KEY_PREFIX}{b}-{key_a[-32:]}"

    status, tenant, verified = await _authenticate_through_gate(forged)

    # 门本身只做格式解析，放行是预期的；挡住越权的是租户库里查不到这把 key。
    assert (status, tenant) == (200, b)
    assert verified is None


class McpProtocolClient:
    """最小 streamable-HTTP 客户端：用真实 MCP 协议打进程内的 app。

    不直接调工具函数，是因为这个链路的故障恰好藏在协议层与 host lifespan 里——
    server 在 lifespan 构造、租户由 ASGI 中间件设置、工具在 session manager 里执行，
    绕开任何一段都测不到。
    """

    PROTO = "2025-06-18"

    def __init__(self, client, key: str) -> None:
        self._client = client
        self._key = key
        self._sid: str | None = None
        self._id = 0

    def _post(self, body: dict):
        headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.PROTO,
        }
        if self._sid:
            headers["mcp-session-id"] = self._sid
        resp = self._client.post("/mcp/", json=body, headers=headers)
        if "mcp-session-id" in resp.headers:
            self._sid = resp.headers["mcp-session-id"]
        return resp

    def call(self, method: str, params: dict | None = None, *, notify: bool = False):
        body: dict = {"jsonrpc": "2.0", "method": method}
        if not notify:
            self._id += 1
            body["id"] = self._id
        if params is not None:
            body["params"] = params
        resp = self._post(body)
        if resp.status_code >= 400 or notify or not resp.content:
            return {"status": resp.status_code}
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {"status": resp.status_code}

    def initialize(self) -> int:
        out = self.call(
            "initialize",
            {
                "protocolVersion": self.PROTO,
                "capabilities": {},
                "clientInfo": {"name": "tenant-isolation-test", "version": "1"},
            },
        )
        status = out.get("status", 200)
        if status == 200:
            self.call("notifications/initialized", notify=True)
        return status

    def tool(self, tool_name: str, args: dict | None = None):
        # 参数走 dict 而不是 **kwargs：工具自己就有名为 name 的入参（create_project）。
        out = self.call("tools/call", {"name": tool_name, "arguments": args or {}})
        return (out.get("result") or {}).get("structuredContent", out)

    def project_names(self) -> list[str]:
        return [p["name"] for p in self.tool("list_projects")["projects"]]


class TestEndToEndTenantIsolation:
    """走完整 MCP 协议的租户隔离。

    这是唯一能暴露"工具落在共享数据根"的验证形态：单看一个租户完全正常——能建项目、
    能列出来、数据也确实写下去了——只有比对两个租户的可见集合才看得出问题。人工复核
    天然会漏掉它，因为没人会为了验一个改动同时开两个租户互相对照。
    """

    @pytest.fixture
    def client(self, monkeypatch):
        from starlette.testclient import TestClient

        from server.app import app

        # MCP SDK 带 DNS-rebinding 防护，只放行 localhost 一类主机名；
        # TestClient 发的是 Host: testserver，不加进来一律 421。
        monkeypatch.setenv("MCP_ALLOWED_HOSTS", "testserver")
        monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "http://testserver")
        # with 块触发 lifespan —— 远程 MCP 的 server 正是在那里构造的。
        with TestClient(app) as c:
            yield c

    async def test_neither_tenant_sees_the_others_projects(self, client, new_tenant):
        a, b = new_tenant(), new_tenant()
        key_a, key_b = await _seed_key(a), await _seed_key(b)

        mcp_a, mcp_b = McpProtocolClient(client, key_a), McpProtocolClient(client, key_b)
        assert mcp_a.initialize() == 200
        assert mcp_b.initialize() == 200
        created_a = mcp_a.tool("create_project", {"name": f"{a}-work", "title": "A"})
        created_b = mcp_b.tool("create_project", {"name": f"{b}-work", "title": "B"})
        assert "project" in created_a, created_a
        assert "project" in created_b, created_b

        assert mcp_a.project_names() == [f"{a}-work"]
        assert mcp_b.project_names() == [f"{b}-work"]

    async def test_projects_land_under_the_tenant_segment(self, client, new_tenant, tmp_path):
        """落盘位置也要验：列表对了但写在共享根上，下一个租户同名项目就会撞车。"""
        a = new_tenant()
        mcp = McpProtocolClient(client, await _seed_key(a))
        assert mcp.initialize() == 200
        mcp.tool("create_project", {"name": f"{a}-work", "title": "A"})

        assert (tmp_path / "tenants" / a / f"{a}-work").is_dir()
        assert not (tmp_path / f"{a}-work").exists()


class TestProjectManagerFollowsTheTenant:
    """工具拿到的 ProjectManager 必须跟着当次请求的租户走。

    远程 MCP 的 server 在 host lifespan 里构造一次、那时租户为 None，若把当时的
    ProjectManager 固化进闭包，所有租户就共用同一个共享数据根。这个故障不报错：
    每个租户都能正常建项目、也能正常列出来，只是列出来的是所有人的——只有比对两个
    租户的可见集合才看得出来，所以这里必须显式断言"两个租户看到的根不同"。
    """

    def test_root_switches_with_the_context(self, new_tenant):
        a, b = new_tenant(), new_tenant()
        pm = TenantProjectManager()

        set_current_tenant(a)
        root_a = pm.projects_root
        set_current_tenant(b)
        root_b = pm.projects_root

        assert root_a != root_b
        assert a in root_a.parts
        assert b in root_b.parts

    def test_app_mounts_the_tenant_aware_factory(self):
        """接线本身也要锁住。

        上面两条只验证代理的行为；把 ``app.py`` 的 ``server_factory`` 换回上游默认值，
        它们照样全绿，而隔离会安静地消失。这里读的是私有属性——够不上优雅，但这个故障
        的代价是跨租户数据泄露，值得多一道锁。
        """
        from server.app import remote_mcp_host

        assert remote_mcp_host._server_factory is build_tenant_aware_mcp_server

    def test_root_carries_the_tenant_segment(self, new_tenant):
        """租户根必须落在 ``tenants/<租户>/`` 底下，而不是部署级的共享根。"""
        a = new_tenant()
        set_current_tenant(a)

        parts = TenantProjectManager().projects_root.parts
        assert "tenants" in parts
        assert a in parts


async def test_tenantless_key_is_refused_before_touching_any_db(tmp_path):
    """单机态形态的 key 在托管态定不出租户，必须在碰 DB 之前就被拒。"""
    status, tenant, verified = await _authenticate_through_gate(build_api_key(API_KEY_PREFIX, None))

    assert (status, tenant, verified) == (401, None, None)
    assert not (tmp_path / "tenants").exists(), "拒绝路径不该建出任何租户目录"
