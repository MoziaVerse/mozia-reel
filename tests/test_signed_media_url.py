"""签名直链：token 的签发与校验。

这条链匿名可达（门禁按前缀整段放行），token 本身就是唯一的访问控制，所以这里锁的
是安全边界而不是便利性——「篡改必须失败」「过期必须失败」「越界路径必须签不出也
解不开」三条破了，数据根就是裸的。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from lib.app_data_dir import _reset_for_tests
from lib.signed_media_url import (
    MEDIA_URL_PREFIX,
    _b64url_encode,
    _signing_key,
    build_public_media_url,
    public_base_url,
    resolve_media_token,
    sign_media_path,
)

# 纯逻辑：无 DB、无网络、无外部进程。
pytestmark = pytest.mark.unit


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    """把数据根指到 tmp，并放一张可签的素材。"""
    monkeypatch.setenv("SESSION_COOKIE_SECRET", "s" * 40)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    _reset_for_tests()
    root = tmp_path / "data"
    asset = root / "tenants" / "user-1" / "proj" / "characters" / "hero.png"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\n")
    yield root, asset
    _reset_for_tests()


def _forge(payload: dict) -> str:
    """用真密钥签一个我们指定的 payload —— 模拟签名合法但内容越界的 token。"""
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64url_encode(hmac.new(_signing_key(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


class TestRoundTrip:
    def test_sign_then_resolve_returns_same_file(self, data_root):
        _, asset = data_root
        assert resolve_media_token(sign_media_path(asset)) == asset.resolve()

    def test_token_carries_no_absolute_path(self, data_root):
        """URL 会流到上游服务，宿主的目录结构不该跟着出去。"""
        _, asset = data_root
        body = sign_media_path(asset).partition(".")[0]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        assert payload["p"] == "tenants/user-1/proj/characters/hero.png"


class TestRejection:
    @pytest.mark.parametrize("bad", ["", "garbage", "a.b", "有中文.sig"])
    def test_malformed_rejected(self, data_root, bad):
        assert resolve_media_token(bad) is None

    def test_tampered_signature_rejected(self, data_root):
        _, asset = data_root
        body, _, sig = sign_media_path(asset).partition(".")
        assert resolve_media_token(f"{body}.{sig[::-1]}") is None

    def test_tampered_payload_rejected(self, data_root):
        """改路径而不改签名——最直接的越权尝试。"""
        _, asset = data_root
        _, _, sig = sign_media_path(asset).partition(".")
        forged_body = _b64url_encode(b'{"exp":9999999999,"p":"tenants/user-2/secret.png"}')
        assert resolve_media_token(f"{forged_body}.{sig}") is None

    def test_expired_rejected(self, data_root):
        _, asset = data_root
        assert resolve_media_token(sign_media_path(asset, ttl_seconds=-1)) is None

    def test_signed_but_escaping_path_rejected(self, data_root):
        """签名合法也不放行越界路径：token 内容同样要过 safe_join。"""
        assert resolve_media_token(_forge({"p": "../../etc/passwd", "exp": 9_999_999_999})) is None

    def test_signed_but_missing_file_rejected(self, data_root):
        assert resolve_media_token(_forge({"p": "tenants/user-1/nope.png", "exp": 9_999_999_999})) is None

    def test_signing_outside_data_root_refused(self, data_root, tmp_path):
        """数据根之外的文件压根签不出来，而不是签出来再靠校验拦。"""
        outsider = tmp_path / "outside.png"
        outsider.write_bytes(b"x")
        with pytest.raises(ValueError):
            sign_media_path(outsider)

    def test_secret_missing_resolves_to_none(self, data_root, monkeypatch):
        """secret 没配时 fail closed，而不是把异常抛给匿名请求方。"""
        _, asset = data_root
        token = sign_media_path(asset)
        monkeypatch.setenv("SESSION_COOKIE_SECRET", "")
        assert resolve_media_token(token) is None


class TestPublicBaseUrl:
    def test_https_accepted_and_trailing_slash_trimmed(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", "https://reel.example.com/")
        assert public_base_url() == "https://reel.example.com"

    @pytest.mark.parametrize("raw", ["", "http://reel.example.com"])
    def test_non_https_treated_as_unset(self, monkeypatch, raw):
        """上游只收 https；给出 http 基址只会让每次生成走到提交那步才失败。"""
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", raw)
        assert public_base_url() is None

    def test_build_url_shape(self, data_root, monkeypatch):
        _, asset = data_root
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", "https://reel.example.com")
        url = build_public_media_url(asset)
        assert url.startswith(f"https://reel.example.com{MEDIA_URL_PREFIX}")
        assert resolve_media_token(url.rsplit("/", 1)[-1]) == asset.resolve()

    def test_build_url_without_base_raises(self, data_root, monkeypatch):
        _, asset = data_root
        monkeypatch.delenv("ARCREEL_PUBLIC_BASE_URL", raising=False)
        with pytest.raises(RuntimeError, match="ARCREEL_PUBLIC_BASE_URL"):
            build_public_media_url(asset)
