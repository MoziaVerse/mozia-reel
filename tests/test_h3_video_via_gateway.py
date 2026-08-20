"""经 mozia 网关调用 MiniMax H3 的契约。

这条链路的特殊性：H3 与 Sora 共用 `openai-video` endpoint（都是 /v1/videos），
但请求形态完全不同 —— 参考图上限、尺寸规则、提交编码三处都要按 model 分流。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.video_backends.openai import (
    OpenAIVideoBackend,
    _is_minimax_h3,
    _resolve_size,
)

pytestmark = pytest.mark.unit


class TestH3Detection:
    @pytest.mark.parametrize(
        "model",
        ["minimax/minimax-h3-ref2va", "minimax/minimax-h3-fl2va", "MiniMax-H3-ref2va-lora"],
    )
    def test_recognises_real_gateway_model_ids(self, model):
        """这几个 id 取自生产网关的 type=204 channel，不是构造的。"""
        assert _is_minimax_h3(model)

    @pytest.mark.parametrize("model", ["sora-2", "sora-2-pro", "", "gpt-4o"])
    def test_leaves_sora_alone(self, model):
        assert not _is_minimax_h3(model)


class TestReferenceImageCaps:
    def test_h3_gets_nine(self):
        """H3 服务端 request_images 的上限就是 9。"""
        caps = OpenAIVideoBackend.video_capabilities_for_model("minimax/minimax-h3-ref2va")
        assert caps.max_reference_images == 9

    def test_sora_stays_at_one(self):
        """同一 endpoint 上的 Sora 不能被 H3 的上限带偏 —— 它只有一个首帧槽位。"""
        caps = OpenAIVideoBackend.video_capabilities_for_model("sora-2")
        assert caps.max_reference_images == 1


class TestSizeResolution:
    @pytest.mark.parametrize(
        "raw,expected",
        [("704x1280", "704x1280"), ("1280×704", "1280x704"), (" 640 x 640 ", "640x640")],
    )
    def test_explicit_pixels_pass_through(self, raw, expected):
        """H3 的 parse_size 接受 32 的倍数、面积 ≤1344×768，档位吸附会改掉用户指定值。"""
        assert _resolve_size("minimax/minimax-h3-ref2va", raw, "9:16") == expected

    def test_sora_still_snaps_to_legal_tier(self):
        from lib.video_backends.openai import _SORA_LEGAL_SIZES

        assert _resolve_size("sora-2", None, "9:16") in _SORA_LEGAL_SIZES


class TestSubmitTimeout:
    def test_submit_timeout_is_240s(self):
        """H3 的提交是同步的（下载素材→ffmpeg→上传 ComfyUI 才返回 task_id）。

        超时不等于提交失败：服务端仍会跑完并计费，调用方却拿不到 task_id、
        永远收不到产物。60s 会稳定复现这个最坏情况。
        """
        assert OpenAIVideoBackend._H3_SUBMIT_TIMEOUT_SEC >= 240.0


class TestReferenceHostingContract:
    def test_unconfigured_hosting_raises_not_silently_skips(self):
        """没有外链托管时必须报错。

        静默跳过参考图的话，模型照样出片、照样扣费，只是画面与用户给的参考
        毫无关系 —— 比报错难查得多。
        """
        import asyncio

        from lib.reference_image_hosting import (
            ReferenceHostingNotConfigured,
            set_uploader,
            upload_reference_images,
        )

        set_uploader(None)
        with pytest.raises(ReferenceHostingNotConfigured):
            asyncio.run(upload_reference_images([Path("/tmp/a.png")]))

    def test_uploader_must_preserve_count(self):
        """URL 数量/顺序对应 prompt 里的 <Picture i>，少一张就是全体错位。"""
        import asyncio

        from lib.reference_image_hosting import set_uploader, upload_reference_images

        async def bad_uploader(paths):
            return ["https://example.com/only-one.png"]

        set_uploader(bad_uploader)
        try:
            with pytest.raises(RuntimeError):
                asyncio.run(upload_reference_images([Path("/a.png"), Path("/b.png")]))
        finally:
            set_uploader(None)


class TestPollingTimeout:
    """H3 的轮询上限必须按它真实的耗时分布来，不能沿用 Sora 那套。

    网关 tasks 表实测 ref2va 15s 档 p50 约 12 分钟、p90 89 分钟，而 Sora 的
    max(600, duration×30) 对 5 秒视频只等 600 秒 —— 必然超时。超时的代价不是
    "失败"：服务端仍会跑完并计费，用户却拿不到产物。
    """

    def _max_wait(self, model: str, duration: int) -> float:
        from lib.video_backends.openai import (
            _H3_MIN_POLL_TIMEOUT_SECONDS,
            _is_minimax_h3,
            _MIN_POLL_TIMEOUT_SECONDS,
            _POLL_TIMEOUT_PER_SECOND,
        )

        floor = _H3_MIN_POLL_TIMEOUT_SECONDS if _is_minimax_h3(model) else _MIN_POLL_TIMEOUT_SECONDS
        return max(floor, duration * _POLL_TIMEOUT_PER_SECOND)

    def test_h3_waits_past_its_p90(self):
        """p90 是 89 分钟；等不到那儿就等于给近一成的任务判死刑。"""
        assert self._max_wait("minimax/minimax-h3-ref2va", 5) >= 89 * 60

    def test_sora_timeout_unchanged(self):
        assert self._max_wait("sora-2", 5) == 600.0

    def test_h3_polls_less_often(self):
        """十几分钟的任务每 5 秒问一次，是上千次无谓请求打在网关上。"""
        from lib.video_backends.openai import _H3_POLL_INTERVAL_SECONDS, _POLL_INTERVAL_SECONDS

        assert _H3_POLL_INTERVAL_SECONDS > _POLL_INTERVAL_SECONDS
