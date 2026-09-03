"""Matrix 握手会话：cookie 签名与门禁中间件的判定。

门禁配合 ``AUTH_ENABLED=false`` 使用，是这套部署下**唯一**的访问控制层，
所以这里的用例锁的是安全边界，不是便利性——尤其"篡改签名必须失败"和
"无会话的 /api 必须 401"两条，破了就是整站裸奔。
"""

from __future__ import annotations

import asyncio

import pytest

from lib.matrix_session import (
    SESSION_COOKIE_NAME,
    issue_session_cookie,
    verify_session_cookie,
)
from server.matrix_gate import MatrixSessionGate


# 纯逻辑：无 DB、无网络、无外部进程。
@pytest.fixture(autouse=True)
def _handshake_env(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECRET", "t" * 40)
    # 门禁在 MATRIX_BACKEND_URL 为空时整条关闭，用例必须显式接入才测得到拦截。
    monkeypatch.setenv("MATRIX_BACKEND_URL", "http://matrix.invalid")


class TestSessionCookie:
    def test_roundtrip(self):
        cookie = issue_session_cookie(sso_sub="user-1", username="zeo")
        assert verify_session_cookie(cookie)["sub"] == "user-1"

    @pytest.mark.parametrize("bad", [None, "", "garbage", "a.b"])
    def test_malformed_rejected(self, bad):
        assert verify_session_cookie(bad) is None

    def test_tampered_signature_rejected(self):
        cookie = issue_session_cookie(sso_sub="user-1", username="zeo")
        body, _, sig = cookie.partition(".")
        assert verify_session_cookie(f"{body}.{sig[::-1]}") is None

    def test_tampered_payload_rejected(self):
        """改 payload 而不改签名——伪造身份最直接的尝试。"""
        cookie = issue_session_cookie(sso_sub="user-1", username="zeo")
        _, _, sig = cookie.partition(".")
        forged = issue_session_cookie(sso_sub="admin", username="admin").partition(".")[0]
        assert verify_session_cookie(f"{forged}.{sig}") is None

    def test_expired_rejected(self, monkeypatch):
        monkeypatch.setenv("SESSION_TTL_SECONDS", "60")
        cookie = issue_session_cookie(sso_sub="user-1", username="zeo")
        monkeypatch.setattr("lib.matrix_session.time.time", lambda: 9_999_999_999)
        assert verify_session_cookie(cookie) is None


async def _probe(path: str, headers: list[tuple[bytes, bytes]]) -> tuple[bool, int | None]:
    """跑一次门禁，返回 (是否放行到下游, 响应状态码)。"""
    sent: list[dict] = []
    reached = False

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    async def downstream(scope, receive, send):
        nonlocal reached
        reached = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    await MatrixSessionGate(downstream)({"type": "http", "path": path, "headers": headers}, receive, send)
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return reached, status


NAV = [(b"accept", b"text/html,application/xhtml+xml")]
API = [(b"accept", b"application/json")]


def _with_session() -> list[tuple[bytes, bytes]]:
    cookie = issue_session_cookie(sso_sub="user-1", username="zeo")
    return [(b"cookie", f"{SESSION_COOKIE_NAME}={cookie}".encode())]


class TestSessionGate:
    @pytest.mark.parametrize(
        "path",
        ["/handoff", "/api/v1/matrix-session/init", "/health", "/public/media/tok"],
    )
    def test_public_paths_pass(self, path):
        """握手页与换票端点是拿到会话的前提，锁上就死锁了。

        签名直链一并在此放行，但理由不同：它的消费方是上游模型服务，cookie 带不了，
        访问控制由 URL 里的 HMAC token 承担（见 tests/test_signed_media_url.py）。
        """
        reached, status = asyncio.run(_probe(path, API))
        assert (reached, status) == (True, 200)

    def test_media_prefix_passes_regardless_of_request_shape(self):
        """直链要靠前缀放行，不能靠"非 /api 一律当静态资源"那条兜底。

        兜底只对"不像导航"的请求生效，而上游模型服务发的 Accept 头长什么样不由我们
        决定；真撞上带 text/html 的取图请求，兜底会把它重定向去 matrix 登录页，
        表现成"参考图取不到"。
        """
        assert asyncio.run(_probe("/public/media/tok", NAV))[0] is True

    def test_media_prefix_does_not_cover_siblings(self):
        """放行的是直链那一段，不是整个 /public/。"""
        assert asyncio.run(_probe("/public/other", NAV))[0] is False

    def test_sibling_endpoints_are_not_public(self):
        """白名单必须精确到换票端点本身。

        同前缀下还有 overview 这类读租户数据的端点；按前缀整段放行会让它们
        匿名可达 —— AUTH_ENABLED=false 时 FastAPI 层的依赖也不拦，这道门禁是
        唯一的访问控制。
        """
        reached, status = asyncio.run(_probe("/api/v1/matrix-session/overview", API))
        assert (reached, status) == (False, 401)

    def test_api_without_session_is_401(self):
        reached, status = asyncio.run(_probe("/api/v1/projects", API))
        assert (reached, status) == (False, 401)

    def test_navigation_without_session_redirects_to_launch_relay(self, monkeypatch):
        """必须指向 launch 中继页，不能是 matrix 首页。

        首页没有"回到本站"的路径，用户跳过去就断了——得自己想起来去应用市场
        找卡片。中继页则会自己走完 登录 → mint ticket → 跳回 /handoff。
        """
        monkeypatch.setenv("MATRIX_WEB_URL", "http://matrix.example")
        monkeypatch.setenv("EXTERNAL_CLIENT_ID", "arcreel")
        sent: list[dict] = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request"}

        async def downstream(scope, receive, send):  # pragma: no cover - 不该被走到
            raise AssertionError("无会话的导航不该进到下游")

        asyncio.run(MatrixSessionGate(downstream)({"type": "http", "path": "/", "headers": NAV}, receive, send))
        start = next(m for m in sent if m["type"] == "http.response.start")
        location = dict(start["headers"])[b"location"].decode()
        assert start["status"] == 302
        assert location == "http://matrix.example/launch/arcreel"

    def test_static_asset_passes(self):
        """静态资源不敏感；拦它只会让 SPA 外壳半截加载失败。"""
        reached, status = asyncio.run(_probe("/assets/index-abc.js", []))
        assert (reached, status) == (True, 200)

    @pytest.mark.parametrize("headers", [API, NAV])
    def test_valid_session_passes(self, headers):
        reached, status = asyncio.run(_probe("/api/v1/projects", headers + _with_session()))
        assert (reached, status) == (True, 200)

    def test_gate_disabled_without_matrix_backend(self, monkeypatch):
        """未接入 matrix 的部署（本地开发/单机自用）不该被这道门锁死。"""
        monkeypatch.setenv("MATRIX_BACKEND_URL", "")
        reached, status = asyncio.run(_probe("/api/v1/projects", API))
        assert (reached, status) == (True, 200)


class TestTenantDbIsReadyForReturningSessions:
    """带着未过期 cookie 直接回访的请求，也要保证租户库已建好并迁到当前 head。

    建库只挂在握手上是不够的：cookie 在整个 TTL 内有效，书签或旧标签页刷新都不会
    再走一次握手。上线带迁移的版本后，这些人的租户库停在旧 schema，任何碰 DB 的接口
    都 500——且只有一部分用户中招，取决于谁在 TTL 内回来过。
    """

    @pytest.fixture(autouse=True)
    def _isolated_data_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("MATRIX_BACKEND_URL", "https://matrix.invalid")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from lib import app_data_dir as add
        from lib import db as libdb

        add._reset_for_tests()
        libdb._reset_tenant_db_cache_for_tests()
        yield
        add._reset_for_tests()
        libdb._reset_tenant_db_cache_for_tests()

    def test_api_request_with_session_creates_the_tenant_db(self, tmp_path):
        reached, status = asyncio.run(_probe("/api/v1/projects", API + _with_session()))

        assert (reached, status) == (True, 200)
        # 断真实产出而不是「调用过某个函数」：库文件确实建出来了，才代表迁移真的跑了。
        assert (tmp_path / "tenants" / "user-1" / ".arcreel.db").exists()

    def test_concurrent_first_requests_migrate_once(self, monkeypatch):
        """同一租户首屏并发的多个 /api 请求只能跑一次迁移。

        alembic 的 context 是进程级全局代理，并行跑会互相踩踏，留下 _alembic_tmp_*
        残表和「表已建但版本没推进」的半迁移库，此后该租户每个请求都 500。
        """
        from lib import db as libdb

        calls = 0
        real_init_db = libdb.init_db

        async def counting_init_db():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)  # 拉开窗口，让其余请求都到达检查点
            await real_init_db()

        monkeypatch.setattr(libdb, "init_db", counting_init_db)

        async def burst():
            return await asyncio.gather(*(_probe("/api/v1/projects", API + _with_session()) for _ in range(8)))

        assert asyncio.run(burst()) == [(True, 200)] * 8
        assert calls == 1

    def test_static_request_does_not_touch_the_database(self, tmp_path):
        """静态资源与 SPA 外壳不碰 DB，不该为它们付建库的代价。"""
        reached, status = asyncio.run(_probe("/assets/app.js", _with_session()))

        assert (reached, status) == (True, 200)
        assert not (tmp_path / "tenants" / "user-1" / ".arcreel.db").exists()


class TestHandoffEndpointIsNotOpen:
    """换票端点在 auth 豁免清单里，这里给出它并非不设防的正面断言。

    对应 tests/test_auth_coverage.py::PUBLIC_OPERATIONS 的约定：豁免的每一条
    都要另有断言证明其保护来自别处。它的保护是 matrix 用共享密钥签发的
    60s HMAC ticket —— 端点本身不认 cookie，也不认任何自带凭据。
    """

    def test_rejects_empty_ticket(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from server.routers import matrix_session

        app = FastAPI()
        app.include_router(matrix_session.public_router, prefix="/api/v1")
        client = TestClient(app)
        assert client.post("/api/v1/matrix-session/init", json={"ticket": "   "}).status_code == 400

    def test_rejects_forged_ticket(self, monkeypatch):
        """伪造的 ticket 必须被 matrix 拒绝，且不会种下任何会话 cookie。"""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from lib import matrix_session as ms
        from server.routers import matrix_session

        async def _reject(ticket: str):
            raise ms.MatrixHandoffError("ticket 无效", 401, "invalid_ticket")

        monkeypatch.setattr(matrix_session, "exchange_ticket", _reject)
        app = FastAPI()
        app.include_router(matrix_session.public_router, prefix="/api/v1")
        client = TestClient(app)
        resp = client.post("/api/v1/matrix-session/init", json={"ticket": "forged"})
        assert resp.status_code == 401
        assert SESSION_COOKIE_NAME not in resp.cookies


class TestBlocklistAtTheGate:
    """名单在门禁上每请求查一次。

    只在握手时查是不够的：把人加进名单后，他手上那张 cookie 在整个 TTL 内仍然
    有效，踢不掉——而"能立刻踢掉"正是这份名单存在的唯一理由。
    """

    @pytest.fixture(autouse=True)
    def _reset(self):
        from lib import matrix_blocklist

        matrix_blocklist._reset_for_tests()
        yield
        matrix_blocklist._reset_for_tests()

    def _block(self, tmp_path, monkeypatch, *subs):
        f = tmp_path / "block.txt"
        f.write_text(("\n".join(subs) + "\n") if subs else "# 空\n", encoding="utf-8")
        monkeypatch.setenv("MATRIX_BLOCKLIST_FILE", str(f))
        return f

    def test_unblocked_user_passes(self, tmp_path, monkeypatch):
        self._block(tmp_path, monkeypatch, "someone-else")
        reached, status = asyncio.run(_probe("/api/v1/projects", API + _with_session()))
        assert reached and status == 200

    def test_blocked_user_rejected_even_with_valid_cookie(self, tmp_path, monkeypatch):
        self._block(tmp_path, monkeypatch, "user-1")
        reached, status = asyncio.run(_probe("/api/v1/projects", API + _with_session()))
        assert not reached and status == 403

    def test_rejection_page_is_a_terminus_not_a_redirect(self, tmp_path, monkeypatch):
        """不能再往 matrix 跳：那边会把他登录后原样送回来，来回弹一遍还是这一页。"""
        self._block(tmp_path, monkeypatch, "user-1")
        reached, status = asyncio.run(_probe("/", NAV + _with_session()))
        assert not reached and status == 403

    def test_no_blocklist_keeps_everything_open(self, monkeypatch):
        monkeypatch.delenv("MATRIX_BLOCKLIST_FILE", raising=False)
        reached, status = asyncio.run(_probe("/api/v1/projects", API + _with_session()))
        assert reached and status == 200

    def test_public_paths_stay_public(self, tmp_path, monkeypatch):
        """名单不该锁死握手本身。"""
        self._block(tmp_path, monkeypatch, "user-1")
        reached, status = asyncio.run(_probe("/health", API))
        assert reached and status == 200
