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


class TestTextToVideoCapability:
    """纯文生只对 ref2va 关闭。

    取值来自生产网关实测（不带 images 提交 /v1/videos）：
    ref2va 返回 400 ``MoziaH3 ref2va task requires reference material``，
    t2va / fl2va 直接受理，2k 受理但要求显式 ratio。按 "minimax-h3" 前缀一刀切
    会把三个能纯文生的型号一并封在提交之前。
    """

    @pytest.mark.parametrize("model", ["minimax/minimax-h3-t2va", "minimax/minimax-h3-fl2va", "minimax/minimax-h3-2k"])
    def test_other_h3_models_keep_text_to_video(self, model):
        assert OpenAIVideoBackend.video_capabilities_for_model(model).text_to_video

    def test_ref2va_declares_no_text_to_video(self):
        caps = OpenAIVideoBackend.video_capabilities_for_model("minimax/minimax-h3-ref2va")
        assert not caps.text_to_video

    def test_sora_keeps_text_to_video(self):
        assert OpenAIVideoBackend.video_capabilities_for_model("sora-2").text_to_video


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


class TestSubmitPayloadContract:
    """提交体的时长字段：名字是 duration、值是数字，与画布（ZeoCanvasLite）同口径。

    网关 adaptor 的 resolveDuration 依次取 duration → seconds → metadata.duration。
    沿用 Sora SDK 的 `"seconds": "5"`（字符串）会被 Go 侧拒成 `cannot unmarshal string
    into Go struct field clientRequest.seconds of type float64`，H3 提交必 400。
    """

    def _captured_payload(self, monkeypatch) -> dict:
        import asyncio

        captured: dict = {}

        class _Resp:
            is_error = False
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"task_id": "t-1"}

        class _Client:
            def __init__(self, *a, **kw) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a) -> bool:
                return False

            async def post(self, _url, *, headers=None, json=None):
                captured.update(json or {})
                return _Resp()

        # 先构造 backend 再 patch：patch 的是全局 httpx.AsyncClient（模块对象共享），
        # 而 backend 构造时 OpenAI SDK 也会经它建自己的 http client——顺序反了会让
        # SDK 拿到这个只实现了 post 的替身。
        backend = OpenAIVideoBackend(
            model="minimax/minimax-h3-fl2va",
            api_key="k",
            base_url="https://example.invalid/v1",
        )
        monkeypatch.setattr("lib.video_backends.openai.httpx.AsyncClient", _Client)
        asyncio.run(
            backend._create_h3_video(prompt="p", model="minimax/minimax-h3-fl2va", seconds="5", size="768x1344")
        )
        return captured

    def test_duration_is_numeric_not_string(self, monkeypatch):
        payload = self._captured_payload(monkeypatch)
        assert payload["duration"] == 5
        assert not isinstance(payload["duration"], str)

    def test_seconds_key_is_not_sent(self, monkeypatch):
        """留着字符串版 seconds 会被 adaptor 当次优先级读到，等于把坑留在原地。"""
        assert "seconds" not in self._captured_payload(monkeypatch)


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
    """H3 的轮询预算必须按它真实的耗时分布来，不能直接采用全局设置。

    网关 tasks 表实测 ref2va 15s 档 p50 约 12 分钟、p90 89 分钟，而全局轮询超时默认
    1 小时 —— 直接采用会让近一成任务被判超时。超时的代价不是"失败"：服务端仍会跑完
    并计费，用户却拿不到产物。
    """

    def test_h3_waits_past_its_p90_on_the_default_setting(self):
        """p90 是 89 分钟；等不到那儿就等于给近一成的任务判死刑。"""
        from lib.config.service import DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS
        from lib.video_backends.openai import _resolve_poll_budget

        max_wait, _ = _resolve_poll_budget("minimax/minimax-h3-ref2va", DEFAULT_VIDEO_POLL_TIMEOUT_SECONDS)
        assert max_wait >= 89 * 60

    def test_h3_honours_a_user_setting_above_its_floor(self):
        """下限是兜底，不是覆盖：用户把全局超时调得更高时以用户的为准。"""
        from lib.video_backends.openai import _resolve_poll_budget

        max_wait, _ = _resolve_poll_budget("minimax/minimax-h3-ref2va", 5 * 60 * 60)
        assert max_wait == 5 * 60 * 60

    def test_sora_follows_the_global_setting_verbatim(self):
        """收窄只针对 H3；其余 model 原样采用全局设置。"""
        from lib.video_backends.openai import _resolve_poll_budget

        assert _resolve_poll_budget("sora-2", 1234)[0] == 1234.0

    def test_h3_polls_less_often_than_the_shared_default(self):
        """动辄十几分钟的任务不值得按默认间隔问 —— 那是上千次无谓请求打在网关上。"""
        from lib.video_backends.base import VIDEO_POLL_INTERVAL_SECONDS
        from lib.video_backends.openai import _resolve_poll_budget

        _, h3_interval = _resolve_poll_budget("minimax/minimax-h3-ref2va", 3600)
        assert h3_interval > VIDEO_POLL_INTERVAL_SECONDS


class TestH3SizeConstraints:
    """H3 的 parse_size 只认 32 的倍数、面积 ≤1344×768。

    Sora 的 9:16 720P 档算出来是 720x1280 —— 720 不是 32 的倍数，H3 直接 400
    (invalid_size: must use width/height multiples of 32)。所以 H3 的尺寸必须
    自己算，不能借用 Sora 的固定档。这几条尺寸都在生产网关上实测过。
    """

    MAX_AREA = 1344 * 768

    def _check(self, size: str) -> tuple[int, int]:
        w, h = (int(x) for x in size.split("x"))
        assert w % 32 == 0 and h % 32 == 0, f"{size} 不是 32 的倍数"
        assert w * h <= self.MAX_AREA, f"{size} 超出面积上限"
        return w, h

    @pytest.mark.parametrize("aspect", ["9:16", "16:9", "1:1", "4:3"])
    def test_derived_size_is_always_legal(self, aspect):
        self._check(_resolve_size("minimax/minimax-h3-ref2va", None, aspect))

    def test_portrait_matches_probed_value(self):
        """9:16 应得到实测可用的 768x1344。"""
        assert _resolve_size("minimax/minimax-h3-ref2va", None, "9:16") == "768x1344"

    def test_illegal_explicit_size_is_snapped(self):
        """用户给的 720x1280 含非法的 720，要吸附成合法值而不是原样下发。"""
        assert _resolve_size("minimax/minimax-h3-ref2va", "720x1280", "9:16") == "704x1280"

    def test_oversized_request_is_scaled_down(self):
        self._check(_resolve_size("minimax/minimax-h3-ref2va", "4096x4096", "1:1"))

    def test_sora_still_uses_its_own_tiers(self):
        from lib.video_backends.openai import _SORA_LEGAL_SIZES

        assert _resolve_size("sora-2", None, "9:16") in _SORA_LEGAL_SIZES
