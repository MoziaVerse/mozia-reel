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

import pytest

from lib.db import async_session_factory, ensure_tenant_db
from lib.db.repositories.api_key_repository import ApiKeyRepository
from lib.tenant_api_key import build_api_key
from lib.tenant_context import current_tenant, set_current_tenant
from server.auth import API_KEY_PREFIX, _hash_api_key
from server.mcp_tenant_gate import McpTenantGate
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


@pytest.fixture(autouse=True)
def _isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MATRIX_BACKEND_URL", "https://matrix.invalid")
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


async def test_tenantless_key_is_refused_before_touching_any_db(tmp_path):
    """单机态形态的 key 在托管态定不出租户，必须在碰 DB 之前就被拒。"""
    status, tenant, verified = await _authenticate_through_gate(build_api_key(API_KEY_PREFIX, None))

    assert (status, tenant, verified) == (401, None, None)
    assert not (tmp_path / "tenants").exists(), "拒绝路径不该建出任何租户目录"
