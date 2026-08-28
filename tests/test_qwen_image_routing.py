"""Qwen 图像族按参考图自动路由。

网关把这一族拆成两个 model id（文生图 / 图片编辑），端点也不同。选哪个取决于
**这次调用有没有参考图**，不是用户偏好——生产实测：选中编辑模型却不给参考图，
上游直接回 500。让用户自己在下拉里挑对是把这个约束推给了他。
"""

from __future__ import annotations

import pytest

from lib.image_backends.base import ImageGenerationRequest, ReferenceImage
from lib.image_backends.openai import OpenAIImageBackend, _resolve_openai_params
from lib.image_backends.qwen_image_traits import (
    QWEN_IMAGE_DEFAULT_SIZE,
    QWEN_IMAGE_EDIT_MODEL,
    QWEN_IMAGE_MODEL,
    QWEN_IMAGE_SIZES,
    is_qwen_image_model,
    resolve_qwen_image_model,
    resolve_qwen_image_size,
)

pytestmark = pytest.mark.unit


class TestModelRouting:
    @pytest.mark.parametrize(
        ("selected", "has_refs", "expected"),
        [
            # 选文生却挂了参考图 → 转编辑
            (QWEN_IMAGE_MODEL, True, QWEN_IMAGE_EDIT_MODEL),
            # 选编辑却没给图 → 退回文生（不退的话上游 500）
            (QWEN_IMAGE_EDIT_MODEL, False, QWEN_IMAGE_MODEL),
            # 选对了就不动
            (QWEN_IMAGE_MODEL, False, QWEN_IMAGE_MODEL),
            (QWEN_IMAGE_EDIT_MODEL, True, QWEN_IMAGE_EDIT_MODEL),
        ],
    )
    def test_routes_by_references_not_by_selection(self, selected, has_refs, expected):
        assert resolve_qwen_image_model(selected, has_references=has_refs) == expected

    @pytest.mark.parametrize("model", ["mozia/image-2", "doubao/seedream-4.5", "", None])
    def test_non_qwen_models_pass_through(self, model):
        """调用方无脑套一层，别的模型不能被动到。"""
        assert resolve_qwen_image_model(model, has_references=True) == (model or "")
        assert resolve_qwen_image_model(model, has_references=False) == (model or "")

    def test_family_membership(self):
        assert is_qwen_image_model(QWEN_IMAGE_MODEL)
        assert is_qwen_image_model(QWEN_IMAGE_EDIT_MODEL)
        assert not is_qwen_image_model("qwen/qwen3.8-27b")
        assert not is_qwen_image_model(None)


class TestSizeWhitelist:
    """size 是白名单不是建议值，传别的会 400。"""

    @pytest.mark.parametrize("aspect", ["1:1", "3:4", "4:3", "9:16", "16:9", "2:3", "3:2"])
    def test_every_exposed_aspect_maps_into_the_whitelist(self, aspect):
        size = resolve_qwen_image_size(image_size=None, aspect_ratio=aspect)
        assert size in QWEN_IMAGE_SIZES

    def test_unknown_aspect_falls_back_to_square(self):
        """不做就近匹配：上游只认白名单，猜个近似档位不如给个确定能出图的。"""
        assert resolve_qwen_image_size(image_size=None, aspect_ratio="21:9") == QWEN_IMAGE_DEFAULT_SIZE

    def test_foreign_size_is_replaced_not_passed_through(self):
        """别处算出来的尺寸（如分镜链路的 4:7）透传过去就是 400。"""
        assert resolve_qwen_image_size(image_size="1472x2576", aspect_ratio="9:16") in QWEN_IMAGE_SIZES

    def test_whitelisted_size_is_honoured(self):
        assert resolve_qwen_image_size(image_size="1664x928", aspect_ratio="1:1") == "1664x928"

    def test_params_bypass_generic_short_edge_math_for_qwen(self):
        """Qwen 不走通用的短边推算，也不带 quality。"""
        qwen = _resolve_openai_params("1080p", "9:16", QWEN_IMAGE_MODEL)
        assert qwen == {"size": "928x1664"}

        other = _resolve_openai_params("1080p", "9:16", "mozia/image-2")
        assert other["size"] not in QWEN_IMAGE_SIZES or "quality" in other


class TestModelRewriteIsPerRequest:
    """改写必须按请求生效并还原——backend 实例会被缓存复用。"""

    def _backend(self, model: str) -> OpenAIImageBackend:
        return OpenAIImageBackend(api_key="k", base_url="https://gw.invalid/v1", model=model)

    def test_rewrite_restores_after_use(self):
        backend = self._backend(QWEN_IMAGE_MODEL)
        with backend._effective_model(has_refs=True):
            assert backend.model == QWEN_IMAGE_EDIT_MODEL
        # 不还原的话，第一次带参考图的调用会把后续所有文生请求钉在编辑模型上
        assert backend.model == QWEN_IMAGE_MODEL

    def test_rewrite_restores_even_on_error(self):
        backend = self._backend(QWEN_IMAGE_MODEL)
        with pytest.raises(RuntimeError):
            with backend._effective_model(has_refs=True):
                raise RuntimeError("boom")
        assert backend.model == QWEN_IMAGE_MODEL

    def test_non_qwen_model_untouched(self):
        backend = self._backend("mozia/image-2")
        with backend._effective_model(has_refs=True):
            assert backend.model == "mozia/image-2"


class TestEndToEndKwargs:
    """走一遍 generate()，确认下发的 model 与 size 都对。"""

    @pytest.mark.asyncio
    async def test_text_to_image_sends_base_model(self, monkeypatch, tmp_path):
        backend = OpenAIImageBackend(api_key="k", base_url="https://gw.invalid/v1", model=QWEN_IMAGE_EDIT_MODEL)
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            raise _StopHere()

        monkeypatch.setattr(backend._client.images, "generate", fake_create)
        with pytest.raises(_StopHere):
            await backend.generate(
                ImageGenerationRequest(prompt="p", output_path=tmp_path / "o.png", aspect_ratio="9:16")
            )
        # 选了编辑模型但没给参考图 → 必须退回文生，否则上游 500
        assert captured["model"] == QWEN_IMAGE_MODEL
        assert captured["size"] == "928x1664"

    @pytest.mark.asyncio
    async def test_image_to_image_sends_edit_model(self, monkeypatch, tmp_path):
        src = tmp_path / "ref.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        backend = OpenAIImageBackend(api_key="k", base_url="https://gw.invalid/v1", model=QWEN_IMAGE_MODEL)
        captured: dict = {}

        async def fake_edit(**kwargs):
            captured.update(kwargs)
            raise _StopHere()

        monkeypatch.setattr(backend._client.images, "edit", fake_edit)
        with pytest.raises(_StopHere):
            await backend.generate(
                ImageGenerationRequest(
                    prompt="p",
                    output_path=tmp_path / "o.png",
                    aspect_ratio="1:1",
                    reference_images=[ReferenceImage(path=str(src))],
                )
            )
        # 选了文生模型但挂了参考图 → 必须转编辑
        assert captured["model"] == QWEN_IMAGE_EDIT_MODEL
        assert captured["size"] == QWEN_IMAGE_DEFAULT_SIZE


class _StopHere(Exception):
    """在真正发请求前中断，只为拿到装配好的 kwargs。"""


class TestHiddenFromSelection:
    """编辑变体不进选择列表，但仍是真正会被下发的模型。"""

    def test_edit_variant_is_hidden(self):
        from lib.image_backends.qwen_image_traits import is_hidden_variant

        assert is_hidden_variant(QWEN_IMAGE_EDIT_MODEL)
        assert not is_hidden_variant(QWEN_IMAGE_MODEL)
        assert not is_hidden_variant("mozia/image-2")
        assert not is_hidden_variant(None)

    def test_hiding_does_not_disable_the_model(self):
        """隐藏的只是下拉选项：路由仍然会选中它，否则图生图就没法用了。"""
        from lib.image_backends.qwen_image_traits import is_hidden_variant

        assert is_hidden_variant(QWEN_IMAGE_EDIT_MODEL)
        assert resolve_qwen_image_model(QWEN_IMAGE_MODEL, has_references=True) == QWEN_IMAGE_EDIT_MODEL


class TestGatewayRequestIdCapture:
    """网关那次调用的 id 必须一路带到账本。

    没有它，费用只能靠本地估算——实测本地把 glm 按 Anthropic 单价算、高估近 8 倍，
    而自定义供应商的图片/视频一律记 0。存下 id 才能跟平台账务逐笔对上。
    """

    def test_settlement_carries_request_id_across_all_channels(self):
        from lib.ledger import _settlement_from_result

        class _R:
            gateway_request_id = "req-123"
            usage_tokens = quality = None
            image_input_tokens = image_output_tokens = None
            text_input_tokens = text_output_tokens = None
            characters = 0
            generate_audio = None
            duration_seconds = 1
            input_tokens = output_tokens = None

        for channel in ("image", "audio", "video", "text"):
            s = _settlement_from_result(channel, _R())
            assert s.gateway_request_id == "req-123", f"{channel} 通道漏掉了 request id"

    def test_absent_on_backends_without_the_concept(self):
        """直连厂商 / 本地 backend 没有这个概念，不能因为取不到就报错。"""
        from lib.ledger import _settlement_from_result

        class _R:
            input_tokens = output_tokens = None

        assert _settlement_from_result("text", _R()).gateway_request_id is None

    def test_take_is_destructive(self):
        """取出即清空：留着会让下一次拿不到 id 的调用误取到上一次的，费用记错行。"""
        from lib.openai_shared import _last_gateway_request_id, take_gateway_request_id

        _last_gateway_request_id.set("req-abc")
        assert take_gateway_request_id() == "req-abc"
        assert take_gateway_request_id() is None
