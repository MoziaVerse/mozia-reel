"""OpenAIAudioBackend — OpenAI 兼容语音合成后端（同步 ``/v1/audio/speech``）。

请求体携带 ``model`` / ``input`` / ``voice``（官方必填）与可选 ``response_format`` / ``speed``，
响应直接返回音频字节（无需二段下载）。schema 依据 OpenAI 官方 API 参考核实。

中转网关上的自建 TTS 偏离官方 schema 两处，本后端据此分流（见 ``list_voices`` /
``_request_speech``）：它们不接受任何 preset voice，且用非官方的 ``ref_audio``
字段做声音克隆——这两种请求体 SDK 都表达不了，走 ``_post_speech_raw``。
主要服务自定义供应商通路：任意 OpenAI 兼容 TTS（Fish Audio、自托管 shim、中转站）
经 ``openai-tts`` endpoint 包装为 ``CustomAudioBackend`` 后接入。
"""

from __future__ import annotations

import logging
from pathlib import Path

from lib.audio_backends.base import (
    AudioCapability,
    AudioSynthesisRequest,
    AudioSynthesisResult,
    VoiceOption,
)
from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.retry import with_retry_async

logger = logging.getLogger(__name__)

# /v1/audio/speech 支持的输出格式（官方 schema），用于按落盘扩展名选 response_format。
# 长文本合成可能几十秒，给足余量。
_SPEECH_TIMEOUT_SEC = 180.0

_SUPPORTED_RESPONSE_FORMATS = frozenset({"mp3", "opus", "aac", "flac", "wav", "pcm"})
_FALLBACK_RESPONSE_FORMAT = "wav"

# 官方内置音色（gpt-4o-mini-tts，含 tts-1/tts-1-hd legacy 子集），出处见
# docs/api-docs/endpoints/openai-tts.md 所列 OpenAI 官方文档。
# 官方文档未给出性别/描述信息，故 label 仅取 id 本身——不编造。
# 经自定义供应商 openai-tts endpoint 接入的第三方兼容服务音色集合可能与本目录不同，
# 边界说明见上述文档。
_VOICE_CATALOG: tuple[VoiceOption, ...] = tuple(
    VoiceOption(id=voice_id, label=voice_id)
    for voice_id in (
        "alloy",
        "ash",
        "ballad",
        "coral",
        "echo",
        "fable",
        "nova",
        "onyx",
        "sage",
        "shimmer",
        "verse",
        "marin",
        "cedar",
    )
)

# legacy 模型（tts-1 / tts-1-hd）不支持的音色子集，出处同上文档：这四个音色仅
# gpt-4o-mini-tts 支持。legacy 模型下若仍暴露它们，用户选中即会在合成阶段确定性失败。
_LEGACY_MODELS = frozenset({"tts-1", "tts-1-hd"})
_LEGACY_UNSUPPORTED_VOICE_IDS = frozenset({"ballad", "verse", "marin", "cedar"})


def _response_format_for(output_path: Path) -> str:
    """按落盘扩展名选输出格式，保证文件内容与扩展名一致（资源路径约定 .wav）。"""
    suffix = output_path.suffix.lstrip(".").lower()
    return suffix if suffix in _SUPPORTED_RESPONSE_FORMATS else _FALLBACK_RESPONSE_FORMAT


class OpenAIAudioBackend:
    """OpenAI 兼容语音合成后端（同步 ``/v1/audio/speech``）。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str,
        provider_name: str = PROVIDER_OPENAI,
    ) -> None:
        # 禁用 SDK 内置重试，由本层 synthesize() 统一管理重试策略
        self._client = create_openai_client(api_key=api_key, base_url=base_url, max_retries=0)
        self._model = model
        # 复用 OpenAI 兼容协议的 provider（自定义供应商包装层覆盖 name）须用真实 provider 记账
        self._provider_name = provider_name

    @property
    def name(self) -> str:
        return self._provider_name

    @property
    def model(self) -> str:
        return self._model

    @property
    def capabilities(self) -> set[AudioCapability]:
        return {AudioCapability.TEXT_TO_SPEECH}

    def list_voices(self) -> list[VoiceOption]:
        if self._provider_name == PROVIDER_OPENAI:
            # legacy 收窄只对官方 OpenAI 生效。
            if self._model in _LEGACY_MODELS:
                return [v for v in _VOICE_CATALOG if v.id not in _LEGACY_UNSUPPORTED_VOICE_IDS]
            return list(_VOICE_CATALOG)

        # 自定义供应商（中转网关上的自建 TTS）不接受官方 preset voice —— 列出 alloy 那一批
        # 等于给用户一个选哪个都 400 的下拉。改为「模型自带音色」+ 打包音色库（走参考音频
        # 克隆，见 lib.voice_library）。库为空时退化成只剩前者，仍是可用状态。
        from lib.narration_delivery import MODEL_DEFAULT_VOICE
        from lib.voice_library import load_voice_library

        options = [VoiceOption(id=MODEL_DEFAULT_VOICE, label="voice_label_model_default")]
        options.extend(VoiceOption(id=v.id, label=v.label) for v in load_voice_library())
        return options

    async def synthesize(self, request: AudioSynthesisRequest) -> AudioSynthesisResult:
        # language_type 是 DashScope 特有字段，/v1/audio/speech 无对应参数（语种随输入文本），不发送。
        # 计费调用与写盘分离：重试只包 API 调用，写盘瞬态失败绝不回头重跑会再次计费的合成请求。
        audio_bytes = await self._request_speech(request)
        request.output_path.write_bytes(audio_bytes)

        logger.info("OpenAI 兼容语音合成完成: %s", request.output_path)

        return AudioSynthesisResult(
            provider=self._provider_name,
            model=self._model,
            characters=len(request.text),
            output_path=request.output_path,
        )


    async def _post_speech_raw(self, payload: dict) -> bytes:
        """绕开 SDK 直发 /audio/speech。

        用在 SDK 表达不了的请求体上：SDK 把 voice 声明成必填、也不认识 ref_audio
        这类非官方字段。其余情况仍走 SDK，以保留它的鉴权、重试与错误归一。
        """
        import httpx

        base = str(self._client.base_url).rstrip("/")
        api_key = self._client.api_key
        async with httpx.AsyncClient(timeout=_SPEECH_TIMEOUT_SEC) as client:
            response = await client.post(
                f"{base}/audio/speech",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            content = response.content
        if not content:
            raise RuntimeError("OpenAI 兼容语音合成返回空响应体")
        return content

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _request_speech(self, request: AudioSynthesisRequest) -> bytes:
        """提交合成请求（计费段），返回音频字节。"""
        kwargs: dict = {
            "model": self._model,
            "input": request.text,
            "response_format": _response_format_for(request.output_path),
        }
        # 空值或「模型自带音色」哨兵一律省略该字段：OpenAI 官方 TTS 必填 voice，
        # 但中转网关上的自建模型（如 index-tts-v2）不接受任何 preset voice ——
        # 带上就是 400 "preset voice not allowed"，不带才用自己的默认音色。
        from lib.narration_delivery import MODEL_DEFAULT_VOICE
        from lib.voice_library import find_platform_voice, reference_audio_data_uri

        voice = (request.voice or "").strip()
        # 库音色的"音色"就是那段参考音频：发 ref_audio 让上游克隆，绝不能把库 id 当
        # preset voice 发上去（上游不认，必然 400）。二者互斥。
        platform_voice = find_platform_voice(voice) if voice and voice != MODEL_DEFAULT_VOICE else None
        if platform_voice is not None:
            kwargs["ref_audio"] = reference_audio_data_uri(platform_voice)
            # 裁剪过的素材没有可信转写，此时不传 —— 错的 ref_text 比不传更伤克隆质量。
            if platform_voice.transcript:
                kwargs["ref_text"] = platform_voice.transcript
        elif voice and voice != MODEL_DEFAULT_VOICE:
            kwargs["voice"] = voice
        if request.speed is not None:
            kwargs["speed"] = request.speed

        logger.info(
            "调用 %s 语音合成 API model=%s voice=%s format=%s chars=%d",
            self.name,
            self._model,
            request.voice,
            kwargs["response_format"],
            len(request.text),
        )
        if platform_voice is not None:
            logger.info("使用平台音色参考音频克隆: %s (%s)", platform_voice.name, platform_voice.path.name)
        if "voice" not in kwargs:
            # 两种情况都落在这里：①「模型自带音色」——SDK 把 voice 声明成必填关键字
            # 参数省略不掉（TypeError），而网关上的自建 TTS 恰恰要求不带它；②库音色
            # ——请求体里的 ref_audio 是非官方字段，SDK 不认。
            return await self._post_speech_raw(kwargs)

        response = await self._client.audio.speech.create(**kwargs)
        if not response.content:
            # 宽松 shim 可能 200 + 空体；不落 0 字节文件、不计成功。该次合成已在供应商侧
            # 发生，重试等于再次计费，故直接抛错交由任务层失败（重生成廉价）。
            raise RuntimeError("OpenAI 兼容语音合成返回空响应体")
        return response.content
