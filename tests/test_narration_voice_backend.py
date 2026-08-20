"""旁白音色的默认值必须跟着音频 backend 走。

_DEFAULT_NARRATION_VOICE = "Cherry" 是百炼的音色名，只对内置 provider 成立。
中转网关上的自建 TTS（index-tts-v2 等）不接受任何 preset voice —— 带上就是
400 "preset voice not allowed: Cherry"（生产网关实测）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db.base import Base

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def svc() -> ConfigService:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield ConfigService(session)
    await engine.dispose()


class TestNarrationVoiceDefault:
    async def test_custom_backend_gets_model_default_sentinel(self, svc):
        """网关自建 TTS：回「模型自带音色」哨兵。

        不能回空串 —— voice 参与 TTS 产物的新鲜度判定，空串会让不同模型的产物
        看起来同源；也不能回 Cherry —— 那会被网关 400 拒掉。
        """
        from lib.narration_delivery import MODEL_DEFAULT_VOICE

        await svc.set_setting("default_audio_backend", "custom-1/index-tts-v2")
        assert await svc.get_narration_voice() == MODEL_DEFAULT_VOICE

    async def test_builtin_backend_keeps_preset(self, svc):
        """内置 provider 行为不变，仍用上游默认音色。"""
        await svc.set_setting("default_audio_backend", "dashscope/cosyvoice-v1")
        assert await svc.get_narration_voice() == "Cherry"

    async def test_explicit_voice_always_wins(self, svc):
        """用户显式配了音色就用它，与 backend 无关。"""
        await svc.set_setting("default_audio_backend", "custom-1/index-tts-v2")
        await svc.set_setting("narration_voice", "my-voice")
        assert await svc.get_narration_voice() == "my-voice"


class TestVoiceOmittedFromPayload:
    async def test_empty_voice_is_not_sent(self):
        """哨兵音色要整个省略字段，绝不能把哨兵本身下发给上游。"""
        from pathlib import Path

        from lib.audio_backends.base import AudioSynthesisRequest
        from lib.audio_backends.openai import OpenAIAudioBackend

        backend = OpenAIAudioBackend(api_key="k", base_url="https://gw.invalid/v1", model="index-tts-v2")
        captured: dict = {}

        async def fake_create(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop before network")

        backend._client = type("C", (), {"audio": type("A", (), {"speech": type("S", (), {"create": staticmethod(fake_create)})()})()})()
        from lib.narration_delivery import MODEL_DEFAULT_VOICE

        req = AudioSynthesisRequest(
            text="你好", output_path=Path("/tmp/x.mp3"), voice=MODEL_DEFAULT_VOICE
        )
        with pytest.raises(Exception):
            await backend._request_speech(req)
        assert "voice" not in captured
