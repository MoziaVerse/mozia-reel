"""签名直链端点与参考图托管方式的选择。

端点匿名可达，唯一凭据是 URL 里的 token；同时上游明确拒收重定向，所以「200 直接
带文件体」也是要锁住的行为，不只是"能取到"。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.app_data_dir import _reset_for_tests
from lib.reference_image_hosting import _publish_self_hosted, uploader_from_env
from lib.signed_media_url import MEDIA_URL_PREFIX, sign_media_path
from server.routers import public_media

# 无 DB、无网络：只跑路由与本地文件。
pytestmark = pytest.mark.unit


@pytest.fixture()
def asset(tmp_path, monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECRET", "s" * 40)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
    _reset_for_tests()
    path = tmp_path / "data" / "tenants" / "user-1" / "proj" / "hero.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    yield path
    _reset_for_tests()


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(public_media.router)
    return TestClient(app)


class TestServeSignedMedia:
    def test_valid_token_returns_file_without_redirect(self, client, asset):
        response = client.get(f"{MEDIA_URL_PREFIX}{sign_media_path(asset)}", follow_redirects=False)
        assert response.status_code == 200
        assert response.content == asset.read_bytes()
        assert response.headers["content-type"] == "image/png"

    def test_response_is_not_shared_cacheable(self, client, asset):
        """URL 本身就是凭据，不该躺进任何共享缓存。"""
        response = client.get(f"{MEDIA_URL_PREFIX}{sign_media_path(asset)}")
        assert response.headers["cache-control"].startswith("private")

    @pytest.mark.parametrize("token", ["garbage", "a.b"])
    def test_bad_token_is_404(self, client, asset, token):
        assert client.get(f"{MEDIA_URL_PREFIX}{token}").status_code == 404

    def test_expired_token_is_404(self, client, asset):
        """过期与「文件不存在」回同一个码：响应差异会变成数据根内的存在性探针。"""
        token = sign_media_path(asset, ttl_seconds=-1)
        assert client.get(f"{MEDIA_URL_PREFIX}{token}").status_code == 404


class TestUploaderSelection:
    def test_defaults_to_self_when_public_base_url_set(self, monkeypatch):
        """配了公网基址就自托管——网关直链所在的域名 H3 上游取不到。"""
        monkeypatch.delenv("ARCREEL_REFERENCE_HOSTING", raising=False)
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", "https://reel.example.com")
        assert uploader_from_env() is _publish_self_hosted

    def test_defaults_to_gateway_without_public_base_url(self, monkeypatch):
        from lib.reference_image_hosting import _upload_via_gateway

        monkeypatch.delenv("ARCREEL_REFERENCE_HOSTING", raising=False)
        monkeypatch.delenv("ARCREEL_PUBLIC_BASE_URL", raising=False)
        assert uploader_from_env() is _upload_via_gateway

    def test_self_without_base_url_is_treated_as_unconfigured(self, monkeypatch):
        """缺配置时返回"未配置"，让调用点抛出可操作的说明，而不是签出半截 URL。"""
        monkeypatch.setenv("ARCREEL_REFERENCE_HOSTING", "self")
        monkeypatch.delenv("ARCREEL_PUBLIC_BASE_URL", raising=False)
        assert uploader_from_env() is None

    def test_none_disables_hosting(self, monkeypatch):
        monkeypatch.setenv("ARCREEL_REFERENCE_HOSTING", "none")
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", "https://reel.example.com")
        assert uploader_from_env() is None


class TestPublishSelfHosted:
    async def test_returns_https_urls_in_order(self, asset, monkeypatch, tmp_path):
        """顺序必须与入参一致：参考图的序号对应 prompt 里的引用标签。"""
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", "https://reel.example.com")
        second = asset.with_name("prop.png")
        second.write_bytes(b"\x89PNG\r\n\x1a\nx")

        urls = await _publish_self_hosted([asset, second])

        assert all(url.startswith("https://reel.example.com") for url in urls)
        assert len(set(urls)) == 2

    async def test_missing_file_raises(self, asset, monkeypatch):
        monkeypatch.setenv("ARCREEL_PUBLIC_BASE_URL", "https://reel.example.com")
        with pytest.raises(RuntimeError, match="不可读"):
            await _publish_self_hosted([asset.with_name("absent.png")])
