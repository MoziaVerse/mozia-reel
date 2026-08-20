"""打包音色库：库音色必须走参考音频克隆，绝不能当 preset voice 发上去。

这条边界破了不会报错，只会静默出错音色（omnivoice 忽略不认识的 voice）或
确定性 400（index-tts-v2 拒绝任何 preset voice），所以逐条钉死。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lib.audio_backends.base import AudioSynthesisRequest

pytestmark = pytest.mark.unit


@pytest.fixture
def library(tmp_path, monkeypatch):
    """造一个两条音色的库：一条带转写，一条（裁剪过的）没有。"""
    from lib import voice_library

    root = tmp_path / "voice_library"
    root.mkdir()
    (root / "a.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake-a")
    (root / "b.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake-b")
    (root / "manifest.json").write_text(
        json.dumps(
            [
                {
                    "id": "vid-with-text",
                    "name": "苍老男声",
                    "language": "中文 (普通话)",
                    "gender": "male",
                    "style": "苍老",
                    "file": "a.wav",
                    "duration_seconds": 5.9,
                    "transcript": "这条路我走了一辈子。",
                },
                {
                    "id": "vid-trimmed",
                    "name": "Toshi",
                    "language": "日语",
                    "gender": "male",
                    "style": "沉稳",
                    "file": "b.wav",
                    "duration_seconds": 8.0,
                    "transcript": None,
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(voice_library, "VOICE_LIBRARY_DIR", root)
    voice_library._reset_for_tests()
    yield root
    voice_library._reset_for_tests()


class TestManifestLoading:
    def test_loads_entries(self, library):
        from lib.voice_library import load_voice_library

        assert [v.id for v in load_voice_library()] == ["vid-with-text", "vid-trimmed"]

    def test_missing_library_is_empty_not_error(self, tmp_path, monkeypatch):
        from lib import voice_library

        monkeypatch.setattr(voice_library, "VOICE_LIBRARY_DIR", tmp_path / "nope")
        voice_library._reset_for_tests()
        assert voice_library.load_voice_library() == ()

    def test_entry_with_missing_audio_is_skipped(self, library):
        """manifest 有、文件没了 —— 跳过而不是让整库失效，也不能返回一条指向空气的音色。"""
        from lib import voice_library

        (library / "a.wav").unlink()
        voice_library._reset_for_tests()
        assert [v.id for v in voice_library.load_voice_library()] == ["vid-trimmed"]

    def test_path_traversal_in_file_is_rejected(self, library):
        """file 字段直接拼路径，`../` 就能读出库外的任意文件，必须挡住。"""
        from lib import voice_library

        manifest = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
        manifest[0]["file"] = "../../etc/passwd"
        (library / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        voice_library._reset_for_tests()
        assert [v.id for v in voice_library.load_voice_library()] == ["vid-trimmed"]

    def test_data_uri_carries_mime(self, library):
        from lib.voice_library import find_platform_voice, reference_audio_data_uri

        uri = reference_audio_data_uri(find_platform_voice("vid-with-text"))
        assert uri.startswith("data:audio/wav;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == (library / "a.wav").read_bytes()


class TestLabel:
    def test_language_appended_when_name_lacks_it(self, library):
        """补的是语种主干（中文），不是 manifest 里那串「中文 (普通话)」。"""
        from lib.voice_library import find_platform_voice

        assert find_platform_voice("vid-with-text").label == "苍老男声 · 中文"
        assert find_platform_voice("vid-trimmed").label == "Toshi · 日语"

    def test_language_not_repeated_when_name_already_says_it(self, library, monkeypatch):
        """tts-studio 那批名字自带语种（「温柔女声 · 中文」），再补一次就成了
        「温柔女声 · 中文 · 中文 (普通话)」。"""
        import json

        from lib import voice_library

        manifest = json.loads((library / "manifest.json").read_text(encoding="utf-8"))
        manifest[0]["name"] = "温柔女声 · 中文"
        (library / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        voice_library._reset_for_tests()
        assert voice_library.find_platform_voice("vid-with-text").label == "温柔女声 · 中文"


class TestSynthesisUsesReferenceAudio:
    async def _synth(self, tmp_path: Path, voice: str) -> dict:
        """走自定义供应商合成一次，返回真正发出去的请求体。"""
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.audio_backends.openai import OpenAIAudioBackend

            backend = OpenAIAudioBackend(api_key="sk", model="index-tts-v2", provider_name="custom-7")
            with patch.object(backend, "_post_speech_raw", new=AsyncMock(return_value=b"audio")) as raw:
                await backend.synthesize(
                    AudioSynthesisRequest(text="喂", output_path=tmp_path / "o.wav", voice=voice)
                )
            return raw.await_args.args[0]

    async def test_library_voice_sends_ref_audio_and_never_voice(self, library, tmp_path):
        payload = await self._synth(tmp_path, "vid-with-text")
        assert payload["ref_audio"].startswith("data:audio/wav;base64,")
        assert "voice" not in payload, "库 id 不是上游认识的 preset voice，发上去必然 400"

    async def test_transcript_is_sent_as_ref_text(self, library, tmp_path):
        payload = await self._synth(tmp_path, "vid-with-text")
        assert payload["ref_text"] == "这条路我走了一辈子。"

    async def test_trimmed_voice_omits_ref_text(self, library, tmp_path):
        """裁剪过的素材转写已与音频对不上，宁可不传 —— 错的 ref_text 比不传更伤克隆。"""
        payload = await self._synth(tmp_path, "vid-trimmed")
        assert "ref_text" not in payload

    async def test_model_default_sentinel_sends_neither(self, library, tmp_path):
        from lib.narration_delivery import MODEL_DEFAULT_VOICE

        payload = await self._synth(tmp_path, MODEL_DEFAULT_VOICE)
        assert "voice" not in payload and "ref_audio" not in payload

    async def test_unknown_voice_id_still_sent_as_preset(self, library, tmp_path):
        """不在库里的 id 维持原样当 preset voice 发 —— 官方 OpenAI 那条路不能被误伤。"""
        with patch("lib.openai_shared.AsyncOpenAI"):
            from lib.audio_backends.openai import OpenAIAudioBackend

            backend = OpenAIAudioBackend(api_key="sk", model="gpt-4o-mini-tts")
            create = AsyncMock(return_value=type("R", (), {"content": b"audio"})())
            backend._client.audio.speech.create = create
            await backend.synthesize(
                AudioSynthesisRequest(text="hi", output_path=tmp_path / "o.wav", voice="alloy")
            )
            assert create.await_args.kwargs["voice"] == "alloy"
            assert "ref_audio" not in create.await_args.kwargs
