"""随发行版打包的平台音色库。

## 为什么是"参考音频"而不是"音色 id"

中转网关上的自建 TTS（omnivoice / index-tts-v2）**不接受任何 preset voice** ——
带上 ``voice=alloy`` 就是 400 ``preset voice not allowed``。它们表达音色的方式是
声音克隆：请求里带一段参考音频（``ref_audio``），输出复刻这段音频的音色。

上游 omnivoice 另有一套 ``POST /v1/voices`` 注册接口，能把参考音频换成稳定的
``vc_`` id。这条路走不通，两个独立原因：

1. 网关不路由该接口（``404 Invalid URL (POST /v1/voices)``），只透传
   ``/v1/audio/speech``；
2. 就算能注册，上游的 registry 是**进程内存字典**，生产 TTS 池子是多实例，
   注册只命中其中一个进程，之后合成轮询到别的实例就是 ``404 unknown voice_id``，
   实例一重启还全丢。

所以音色的**身份与素材都留在本地**，每次合成把参考音频随请求发出。上游对
``ref_audio`` 做了按内容哈希的透明缓存，各实例首次 miss、之后命中，跨实例自愈。

## 音色 id 的稳定性

id 参与 TTS 产物的新鲜度指纹（见 ``lib.narration_delivery.TtsSynthesisSettings``），
**换了音色要重新合成**。因此 id 必须稳定：manifest 里的 id 是音色入库时生成的
uuid，不随文件名、排序或展示名变化，改名不会让既有产物失效。
"""

from __future__ import annotations

import base64
import functools
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from lib.env_init import PROJECT_ROOT

logger = logging.getLogger(__name__)

VOICE_LIBRARY_DIR = PROJECT_ROOT / "voice_library"
_MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class PlatformVoice:
    """一条平台音色：展示元数据 + 落盘的参考音频。"""

    id: str
    name: str
    language: str
    gender: str | None
    style: str | None
    path: Path
    duration_seconds: float | None
    # 参考音频的转写文本。裁剪过的素材没有可信转写（文本已与音频对不上），
    # 此时为 None —— 上游 ``ref_text`` 可选，宁可不传也不能传错的。
    transcript: str | None

    @property
    def label(self) -> str:
        """下拉展示名。语种只在名字里没提过时才补，避免「温柔女声 · 中文 · 中文 (普通话)」。

        比对用语种的主干（``中文 (普通话)`` → ``中文``）：库里的名字写的是主干，
        拿完整语种串去判包含永远不命中，那正是重复的来源。
        """
        if not self.language:
            return self.name
        base = self.language.split("(")[0].split(" ")[0].strip() or self.language
        return self.name if base in self.name else f"{self.name} · {base}"


@functools.cache
def load_voice_library() -> tuple[PlatformVoice, ...]:
    """读取打包音色库（进程内缓存）。库缺失或损坏时返回空，不抛错。

    返回空是有意的：音色库是增强项而非必需项，缺了应当退化成"只有模型自带音色"，
    而不是让整条 TTS 链路不可用。
    """
    manifest_path = VOICE_LIBRARY_DIR / _MANIFEST_NAME
    if not manifest_path.is_file():
        return ()
    try:
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("平台音色库 manifest 读取失败，按空库处理: %s", manifest_path)
        return ()
    if not isinstance(rows, list):
        logger.error("平台音色库 manifest 不是数组，按空库处理: %s", manifest_path)
        return ()

    voices: list[PlatformVoice] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        vid = str(row.get("id") or "").strip()
        name = str(row.get("name") or "").strip()
        rel = str(row.get("file") or "").strip()
        if not vid or not name or not rel:
            continue
        # 只认 manifest 同级目录下的普通文件名，挡住 ../ 之类的越界引用
        if Path(rel).name != rel:
            logger.warning("平台音色 %s 的 file 含路径分隔符，跳过: %r", vid, rel)
            continue
        path = VOICE_LIBRARY_DIR / rel
        if not path.is_file():
            logger.warning("平台音色 %s 的参考音频缺失，跳过: %s", vid, path)
            continue
        duration = row.get("duration_seconds")
        transcript = row.get("transcript")
        voices.append(
            PlatformVoice(
                id=vid,
                name=name,
                language=str(row.get("language") or "").strip(),
                gender=(str(row["gender"]).strip() or None) if row.get("gender") else None,
                style=(str(row["style"]).strip() or None) if row.get("style") else None,
                path=path,
                duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
                transcript=transcript.strip() or None if isinstance(transcript, str) else None,
            )
        )
    return tuple(voices)


def find_platform_voice(voice_id: str) -> PlatformVoice | None:
    """按 id 取音色；不是库里的 id 返回 None（调用方据此判定该走 preset voice）。"""
    target = (voice_id or "").strip()
    if not target:
        return None
    for voice in load_voice_library():
        if voice.id == target:
            return voice
    return None


# data URI 里 ``audio/`` 后面那截子类型。
#
# ⚠️ 这里**不能**用 ``mimetypes.guess_type``：上游解析 data URI 后，把子类型原样
# 当作临时文件的扩展名（``/tmp/ref_….%s`` % fmt）。而 mimetypes 的结果随系统注册表
# 变化——本机 ``.wav`` 猜出来是 ``audio/x-wav``，落盘就成了 ``ref_….x-wav``，音频库
# 认不出格式。规范 MIME 在这里也不对：``.mp3`` 的规范类型是 ``audio/mpeg``，当扩展名
# 用同样是错的。所以按后缀直接映射到"上游拿去当扩展名正好对"的那个词。
_SUBTYPE_BY_SUFFIX = {".wav": "wav", ".mp3": "mp3", ".flac": "flac", ".ogg": "ogg", ".opus": "opus"}
_FALLBACK_SUBTYPE = "wav"


def reference_audio_data_uri(voice: PlatformVoice) -> str:
    """把参考音频编成 data URI —— 上游 ``ref_audio`` 接受 base64 或 data URI。

    用 data URI 而非裸 base64：裸 base64 上游拿不到格式提示，一律按 wav 落盘。
    """
    subtype = _SUBTYPE_BY_SUFFIX.get(voice.path.suffix.lower(), _FALLBACK_SUBTYPE)
    payload = base64.b64encode(voice.path.read_bytes()).decode("ascii")
    return f"data:audio/{subtype};base64,{payload}"


def _reset_for_tests() -> None:
    load_voice_library.cache_clear()
