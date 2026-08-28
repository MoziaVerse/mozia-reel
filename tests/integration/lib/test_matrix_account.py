"""登出、切换账号与余额 —— 对照 ZeoCanvasLite 的登录系统补齐的部分。

canvas 把网关 key 与 walletToken 一起塞进 AES-256-GCM 加密的 cookie；我们的
key 本来就在服务端 DB，所以 cookie 只签名不加密、只装身份。凭据一律不进 cookie，
这几条用例锁的就是这个边界。
"""

from __future__ import annotations

import pytest

from lib.matrix_session import (
    get_wallet_token,
    issue_session_cookie,
    matrix_launch_url,
    save_wallet_token,
)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECRET", "s" * 40)
    monkeypatch.setenv("MATRIX_BACKEND_URL", "https://matrix.invalid")
    monkeypatch.setenv("MATRIX_WEB_URL", "https://web.invalid")
    monkeypatch.setenv("EXTERNAL_CLIENT_ID", "mozia-reel")


class TestSwitchAccount:
    def test_plain_launch_url(self):
        assert matrix_launch_url() == "https://web.invalid/launch/mozia-reel"

    def test_force_login_for_account_switch(self):
        """不带 prompt=login 的话 matrix 会把同一个账号直接送回来，用户换不了人。"""
        assert matrix_launch_url(force_login=True).endswith("/launch/mozia-reel?prompt=login")


class TestWalletTokenStorage:
    @pytest.mark.asyncio
    async def test_roundtrip(self, db_session):
        await save_wallet_token(db_session, "wt-abc")
        assert await get_wallet_token(db_session) == "wt-abc"

    @pytest.mark.asyncio
    async def test_cleared_when_matrix_omits_it(self, db_session):
        """matrix 不再下发时要清掉旧值，否则会拿着过期 token 一直查失败。"""
        await save_wallet_token(db_session, "wt-abc")
        await save_wallet_token(db_session, None)
        assert await get_wallet_token(db_session) is None

    def test_wallet_token_never_enters_cookie(self):
        """凭据不进 cookie —— cookie 只签名未加密，payload 可被解出来。"""
        import base64
        import json

        cookie = issue_session_cookie(sso_sub="user-1", username="zeo")
        body = cookie.partition(".")[0]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert set(payload) == {"sub", "name", "exp"}
        assert "wt-abc" not in cookie


class TestOverviewUsername:
    """账户页的用户名取自 cookie，而 cookie 里那个字段叫 name 不叫 username。

    取错不报错，只是每个真实用户的账户页都显示"未设置"——本地绑定账号模式
    恰好从 env 拿得到用户名，会把这个错盖住，所以专门钉一条。
    """

    def test_cookie_carries_username_under_name(self):
        from lib.matrix_session import issue_session_cookie, verify_session_cookie

        payload = verify_session_cookie(issue_session_cookie(sso_sub="s-1", username="Zeo"))
        assert payload["name"] == "Zeo"
        assert "username" not in payload
