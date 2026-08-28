"""Matrix 形态下暴露给用户的能力面。

这里锁的是"不该出现的东西不出现"：厂商原生端点打到中转网关会返回 HTML 首页，
内置供应商要求用户自备厂商 key —— 两者露在设置页里，用户都要到生成失败时
才知道用不了。
"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoints import ENDPOINT_REGISTRY
from lib.matrix_capabilities import (
    GATEWAY_SUPPORTED_ENDPOINTS,
    builtin_provider_visible,
    matrix_mode_enabled,
    visible_endpoint_keys,
)


@pytest.fixture
def matrix_mode(monkeypatch):
    monkeypatch.setenv("MATRIX_BACKEND_URL", "https://matrix.invalid")


@pytest.fixture
def standalone_mode(monkeypatch):
    monkeypatch.delenv("MATRIX_BACKEND_URL", raising=False)


class TestModeDetection:
    def test_enabled_when_matrix_configured(self, matrix_mode):
        assert matrix_mode_enabled()

    def test_disabled_standalone(self, standalone_mode):
        assert not matrix_mode_enabled()


class TestEndpointVisibility:
    def test_standalone_keeps_everything(self, standalone_mode):
        """单机自用不该被裁剪 —— 那时用户自备厂商 key，原生端点是能用的。"""
        assert visible_endpoint_keys(ENDPOINT_REGISTRY.keys()) == list(ENDPOINT_REGISTRY.keys())

    def test_matrix_keeps_only_gateway_paths(self, matrix_mode):
        visible = visible_endpoint_keys(ENDPOINT_REGISTRY.keys())
        assert set(visible) <= GATEWAY_SUPPORTED_ENDPOINTS
        assert "openai-chat" in visible
        assert "openai-video" in visible
        assert "openai-tts" in visible

    @pytest.mark.parametrize(
        "vendor_native",
        ["minimax-video", "kling-video", "ark-seedance", "gemini-generate", "dashscope-image"],
    )
    def test_matrix_hides_vendor_native_endpoints(self, matrix_mode, vendor_native):
        """这些是厂商原生路径。中转网关上不存在，打过去会拿到 SPA 首页 HTML。"""
        assert vendor_native not in visible_endpoint_keys(ENDPOINT_REGISTRY.keys())

    def test_whitelist_entries_all_exist(self):
        """白名单不能引用已改名/删除的 endpoint，否则会静默少下发一条。"""
        assert GATEWAY_SUPPORTED_ENDPOINTS <= set(ENDPOINT_REGISTRY.keys())

    def test_every_media_type_still_reachable(self, matrix_mode):
        """四类媒体都得留下至少一条，否则该类模型在 UI 里彻底配不出来。"""
        from lib.custom_provider.endpoints import endpoint_to_media_type

        media = {endpoint_to_media_type(k) for k in visible_endpoint_keys(ENDPOINT_REGISTRY.keys())}
        assert {"text", "image", "video", "audio"} <= media


class TestBuiltinProviderVisibility:
    @pytest.mark.parametrize("provider", ["gemini-aistudio", "ark", "openai", "kling", "minimax"])
    def test_matrix_hides_builtin_providers(self, matrix_mode, provider):
        assert not builtin_provider_visible(provider)

    @pytest.mark.parametrize("provider", ["gemini-aistudio", "ark", "openai"])
    def test_standalone_shows_builtin_providers(self, standalone_mode, provider):
        assert builtin_provider_visible(provider)
