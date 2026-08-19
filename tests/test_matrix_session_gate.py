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
pytestmark = pytest.mark.unit


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

    await MatrixSessionGate(downstream)(
        {"type": "http", "path": path, "headers": headers}, receive, send
    )
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
        ["/handoff", "/api/v1/matrix-session/init", "/health", "/skill.md"],
    )
    def test_public_paths_pass(self, path):
        """握手页与换票端点是拿到会话的前提，锁上就死锁了。"""
        reached, status = asyncio.run(_probe(path, API))
        assert (reached, status) == (True, 200)

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

        asyncio.run(
            MatrixSessionGate(downstream)(
                {"type": "http", "path": "/", "headers": NAV}, receive, send
            )
        )
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
