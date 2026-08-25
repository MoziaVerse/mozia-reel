"""OpenAIVideoBackend — OpenAI Sora 视频生成后端。"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import httpx

from lib.aspect_size import VIDEO_TIER_SHORT_EDGE, parse_aspect_ratio, resolution_to_short_edge
from lib.logging_utils import format_kwargs_for_log
from lib.openai_shared import OPENAI_RETRYABLE_ERRORS, create_openai_client
from lib.providers import PROVIDER_OPENAI
from lib.reference_image_hosting import upload_reference_images
from lib.retry import DOWNLOAD_BACKOFF_SECONDS, DOWNLOAD_MAX_ATTEMPTS, with_retry_async
from lib.video_backends.base import (
    IMAGE_MIME_TYPES,
    TERMINAL_PROVIDER_STATUSES,
    ProviderJobIdPersistenceMixin,
    ProviderJobStatus,
    ResumeExpiredError,
    VideoCapabilities,
    VideoGenerationRequest,
    VideoGenerationResult,
    normalize_provider_status,
    poll_with_retry,
)

_POLL_INTERVAL_SECONDS = 5.0
_MIN_POLL_TIMEOUT_SECONDS = 600.0
_POLL_TIMEOUT_PER_SECOND = 30.0

# MiniMax H3 的耗时与 Sora 不是一个量级：网关 tasks 表实测 ref2va 15s 档
# p50 约 12 分钟、p90 89 分钟、max 142 分钟，且长尾与时长无关（5s 也出现过 96 分钟）。
# 按 Sora 那套 max(600, duration×30) 算，5 秒视频只等 600 秒 —— 必然超时。
#
# 超时的代价不是"失败"这么简单：服务端仍会跑完并计费，用户却拿不到产物。
# Canvas 踩过同一个坑，那边把任务过期放宽到 3 小时（当时 10% 的任务超 60 分钟且已扣费），
# 这里取同一口径。
_H3_MIN_POLL_TIMEOUT_SECONDS = 3 * 60 * 60.0
# 动辄十几分钟的任务不值得每 5 秒问一次：那是上千次无谓请求打在网关上。
_H3_POLL_INTERVAL_SECONDS = 15.0

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sora-2"

# sora 合法 size 按 model 能力 + 分辨率档分级（OpenAI 官方 changelog / 模型页）：
# - sora-2（base）：仅 720p —— 9:16 720x1280 / 16:9 1280x720。
# - sora-2-pro：720p 同上 +（2026-03 起）1080p —— 9:16 1080x1920 / 16:9 1920x1080。
# SDK 的 VideoSize Literal 滞后（只列 720/1024 档、漏 1080），以官方模型页为准；
# 不再保留 1024x1792 / 1792x1024（4:7，违背比例优先，且已被 1080p 精确档取代）。
# 非任意 WxH，只能吸附合法档：比例优先选档，分辨率档只决定 720p vs 1080p 子集（清晰度其次）。
_SORA_SIZES_720P: tuple[str, ...] = ("720x1280", "1280x720")
_SORA_SIZES_1080P: tuple[str, ...] = ("1080x1920", "1920x1080")
# 向后兼容的并集导出（外部/测试引用「全部合法档」）。
_SORA_LEGAL_SIZES: tuple[str, ...] = _SORA_SIZES_720P + _SORA_SIZES_1080P
_SORA_1080P_MIN_SHORT = 1080
_CUSTOM_SIZE_RE = re.compile(r"^\s*(\d+)\s*[xX×*]\s*(\d+)\s*$")


# H3 走 OpenAI 兼容网关的 /v1/videos，但请求形态与 Sora 不同：参考图要 JSON 里的
# 公网 URL 数组，不是 SDK 的 multipart 文件槽。按 model id 判定而不是按 endpoint：
# 同一个 endpoint 上既有真 Sora 也有中转过来的 H3。
def _is_minimax_h3(model: str) -> bool:
    return "minimax-h3" in (model or "").lower()


def _video_status(video: object) -> ProviderJobStatus:
    """SDK Video 对象 → canonical 状态。

    本端点同时服务内置 Sora 与自定义供应商的 openai-video 协议：官方 Sora 只发
    ``queued`` / ``in_progress`` / ``completed`` / ``failed``，而 OpenAI 兼容代理网关转发
    非 Sora 型号时会透传底层厂商的状态串（如 ``succeeded``），故一律过共享归一。
    """
    return normalize_provider_status(getattr(video, "status", None))


def _video_error_message(video: object) -> str:
    """SDK Video 对象 → 供应商失败原因文本；取不到返回 unknown。

    这句话原样落进 ``task.error_message``，是用户在任务面板读到的全部原因，故显式取字段而不是
    插值整个对象：``Video.error`` 是带 ``code`` / ``message`` 的模型，直接插值会把类名与字段名
    一并写给用户；``None``（网关只给 status 不给 error 的常见形态）会写出一句没有原因的失败。
    代理网关透传的裸 dict / 裸字符串同样认，认不出的形态一律 unknown。
    """
    err = getattr(video, "error", None)
    if err is None:
        return "unknown"
    if isinstance(err, str):
        return err.strip() or "unknown"
    if isinstance(err, dict):
        candidates = (err.get("message"), err.get("code"))
    else:
        candidates = (getattr(err, "message", None), getattr(err, "code", None))
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
        # 数字错误码（网关常见的裸 HTTP 码）也是原因，别因为不是字符串就丢掉
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    # 认不出的形态说不出原因就说 unknown：把对象自身的 repr 写进任务面板等于没说
    return "unknown"


# H3 的 parse_size 只认 32 的倍数，且面积不超过 1344×768。9:16 的 720P 档算出来是
# 720x1280 —— 720 不是 32 的倍数，直接 400。所以 H3 的尺寸必须自己算，不能借用
# Sora 的固定档。
_H3_SIZE_MULTIPLE = 32
_H3_MAX_AREA = 1344 * 768


def _snap_h3_size(width: int, height: int) -> str:
    """吸附到 32 的倍数，并在超面积时等比缩到 H3 的上限内。"""

    def _snap(v: int) -> int:
        return max(_H3_SIZE_MULTIPLE, round(v / _H3_SIZE_MULTIPLE) * _H3_SIZE_MULTIPLE)

    w, h = _snap(width), _snap(height)
    while w * h > _H3_MAX_AREA:
        # 每次退一档而不是一次性算比例：吸附后仍要落在 32 的倍数上。
        if w >= h:
            w -= _H3_SIZE_MULTIPLE
        else:
            h -= _H3_SIZE_MULTIPLE
        w, h = max(w, _H3_SIZE_MULTIPLE), max(h, _H3_SIZE_MULTIPLE)
    return f"{w}x{h}"


def _h3_size_for_aspect(aspect_ratio: str) -> str:
    """按目标比例算一个 H3 合法的尺寸，尽量贴近面积上限。"""
    aw, ah = parse_aspect_ratio(aspect_ratio)
    ratio = aw / ah
    # 以面积上限为基准反解边长，再交给 _snap_h3_size 收口。
    height = (_H3_MAX_AREA / ratio) ** 0.5
    return _snap_h3_size(round(height * ratio), round(height))


def _resolve_size(model: str, resolution: str | None, aspect_ratio: str) -> str:
    """比例优先：在 model+分辨率档对应的 sora 合法档中选比例最接近 aspect_ratio 的；并列取像素更高者。

    分辨率档只决定 720p vs 1080p 子集、不决定比例：sora-2-pro 选 1080p 时 9:16→1080x1920（精确且高清），
    sora-2（base）或缺分辨率时落 720p（缺分辨率默认 720P，不擅自升 1080p 以免超额计费）。size 必传以锁定
    比例，绝不出现「不传 size → 上游默认比例」。其它比例（1:1/21:9 等）sora 无对应档，吸附后告警。
    """
    # 显式的「宽×高」原样下发 —— **仅对 H3**：它的 parse_size 接受 32 的倍数、
    # 面积 ≤1344×768，档位吸附会把用户指定的分辨率改掉。
    # Sora 必须继续走吸附：它只认固定档，透传自定义值会被上游拒绝，
    # 这也是上游 test_custom_resolution_value_ignored_uses_legal_size 锁的行为。
    if _is_minimax_h3(model):
        if resolution is not None and (match := _CUSTOM_SIZE_RE.match(resolution)):
            return _snap_h3_size(int(match.group(1)), int(match.group(2)))
        # 没给显式尺寸时也不能落回 Sora 档位：那套档里的 720 不是 32 的倍数，
        # H3 会直接 400（invalid_size: must use width/height multiples of 32）。
        return _h3_size_for_aspect(aspect_ratio)

    aw, ah = parse_aspect_ratio(aspect_ratio)
    target = aw / ah
    is_pro = "pro" in model.lower()
    short = resolution_to_short_edge(resolution, tier_map=VIDEO_TIER_SHORT_EDGE)
    # sora 支持的短边档：base 仅 720；pro 增 1080。按「最近档」选（等距取更高档），避免把
    # short=1000 这类自定义分辨率值无故降到 720p（floor 会误降，最近档不会）。
    supported_shorts = [720, _SORA_1080P_MIN_SHORT] if is_pro else [720]
    achieved_short = min(supported_shorts, key=lambda s: (abs(s - short), -s))
    legal = _SORA_SIZES_1080P if achieved_short == _SORA_1080P_MIN_SHORT else _SORA_SIZES_720P
    if short > achieved_short:
        # 请求高于模型可达档（base 请 1080p、或 pro 请 4K）：封顶并提示清晰度让位
        logger.warning(
            "OpenAI video: model=%s 无法满足分辨率请求 %s（短边 %d），输出封顶到 %dp 档（清晰度让位比例）",
            model,
            resolution,
            short,
            achieved_short,
        )

    def _score(size: str) -> tuple[float, int]:
        w, h = (int(x) for x in size.split("x"))
        return abs(w / h - target), -(w * h)  # 比例差小优先；并列取像素更多

    chosen = min(legal, key=_score)
    # 9:16 / 16:9 精确命中；其它比例（如 1:1 / 21:9）sora 无对应档，吸附后比例必然偏差，
    # 与上游协议无关地告警，避免静默产出错比例视频（图片路径同样在超界时告警）。
    cw, ch = (int(x) for x in chosen.split("x"))
    if abs(cw / ch - target) > 0.01:
        logger.warning(
            "OpenAI video: aspect_ratio=%s 无精确 sora 档，吸附到 %s（比例偏差，输出非项目设定比例）",
            aspect_ratio,
            chosen,
        )
    # 档位路径的后置不变量：只返回 sora 合法档全集内的尺寸（自定义像素已在上面提前返回）。
    assert chosen in _SORA_LEGAL_SIZES, f"_resolve_size produced illegal sora size: {chosen}"
    return chosen


class OpenAIVideoBackend(ProviderJobIdPersistenceMixin):
    """OpenAI Sora 视频生成后端。"""

    def __init__(self, *, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self._client = create_openai_client(api_key=api_key, base_url=base_url)
        # H3 分支绕开 SDK 自己发请求，需要原始凭据与地址。
        self._api_key = api_key
        self._base_url = (base_url or "").strip().rstrip("/")
        self._model = model or DEFAULT_MODEL

    @property
    def name(self) -> str:
        return PROVIDER_OPENAI

    @property
    def model(self) -> str:
        return self._model

    @staticmethod
    def video_capabilities_for_model(model: str) -> VideoCapabilities:
        """按 model_id 纯计算 caps —— 不构造 SDK client（无需 api_key）。

        Sora input_reference 为单张首帧图，参考图上限为 1；首帧与参考共享该单槽位。
        经中转网关过来的 MiniMax H3 走同一个 endpoint 但契约不同，上限是 9
        （见 mozia-h3-api 的 request_images）。所以这里必须按 model_id 分支，
        不能在 endpoint 上写死一个数 —— 写死会让真 Sora 也声称支持 9 张。
        instance property 委托至此，保持 backend 为单一真相源。
        """
        if _is_minimax_h3(model):
            return VideoCapabilities(max_reference_images=9)
        return VideoCapabilities(max_reference_images=1)

    @property
    def video_capabilities(self) -> VideoCapabilities:
        return self.video_capabilities_for_model(self._model)

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "seconds": str(request.duration_seconds),
        }
        # 始终下传合法 size 以锁定比例（sora 固定档，不传则上游用自家默认比例）
        kwargs["size"] = _resolve_size(self._model, request.resolution, request.aspect_ratio)

        # 收集所有参考图：start_image + reference_images
        ref_paths: list[Path] = []
        if request.start_image and Path(request.start_image).exists():
            ref_paths.append(Path(request.start_image))
        if request.reference_images:
            for ref_path in request.reference_images:
                p = Path(ref_path) if not isinstance(ref_path, Path) else ref_path
                if p.exists():
                    ref_paths.append(p)

        is_h3 = _is_minimax_h3(self._model)
        if ref_paths and is_h3:
            # H3 经中转网关时只认 JSON 里的公网 https URL：网关会把 body 反序列化
            # 成 Go 结构体再重建，multipart 的文件部分会被丢弃（实测）。
            kwargs["images"] = await upload_reference_images(ref_paths)
        elif ref_paths:
            refs = [_encode_start_image(path) for path in ref_paths]
            # 单张图时保持 tuple 格式（API 兼容），多张时用 list
            kwargs["input_reference"] = refs[0] if len(refs) == 1 else refs

        logger.info("OpenAI 视频生成开始: model=%s, seconds=%s", self._model, kwargs["seconds"])
        logger.info("调用 %s 视频 SDK kwargs=%s", self.name, format_kwargs_for_log(kwargs))

        video = await (self._create_h3_video(**kwargs) if is_h3 else self._create_video(**kwargs))
        # submit 成功立即持久化 job_id；持久化失败抛 → finally mark_failed。
        # 非 worker 路径（grid / 直生 / 测试）request.task_id 为 None，统一点内跳过持久化。
        await self._persist_provider_job_id(request, video.id, provider=PROVIDER_OPENAI)
        final = await self._poll_until_complete(video.id, request.duration_seconds)

        # generate 路径下 expired 是「provider 异常 / 输入参数过期」类失败，
        # 抛 RuntimeError 让 worker mark_failed（不带 [resume_expired] 前缀）。
        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise RuntimeError(f"OpenAI Sora job expired during generate: {final.id}")

        return await self._download_and_build_result(final, request, kwargs)

    async def resume_video(self, job_id: str, request: VideoGenerationRequest) -> VideoGenerationResult:
        """接续已 submit 的 OpenAI job：仅 poll + 下载，不调 videos.create。"""
        try:
            final = await self._poll_until_complete(job_id, request.duration_seconds)
        except Exception as exc:
            if _is_openai_not_found(exc):
                raise ResumeExpiredError(job_id=job_id, provider=PROVIDER_OPENAI) from exc
            raise

        # resume 路径下 expired = provider 端已忘 / 输入资产过期，归类
        # [resume_expired] 让 worker 错误前缀化、不再尝试重启自愈
        if _video_status(final) is ProviderJobStatus.EXPIRED:
            raise ResumeExpiredError(
                job_id=job_id,
                provider=PROVIDER_OPENAI,
                message=f"OpenAI Sora job expired: {final.id}",
            )

        return await self._download_and_build_result(final, request, {"seconds": str(request.duration_seconds)})

    async def _download_and_build_result(
        self, final, request: VideoGenerationRequest, kwargs: dict
    ) -> VideoGenerationResult:
        content = await self._download_content_with_retry(final.id)

        def _write():
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(content.content)

        await asyncio.to_thread(_write)

        logger.info("OpenAI 视频下载完成: %s", request.output_path)

        return VideoGenerationResult(
            video_path=request.output_path,
            provider=PROVIDER_OPENAI,
            model=self._model,
            duration_seconds=int(
                final.seconds if final.seconds is not None else kwargs.get("seconds") or request.duration_seconds
            ),
            task_id=final.id,
        )

    @with_retry_async(retryable_errors=OPENAI_RETRYABLE_ERRORS)
    async def _create_video(self, **kwargs):
        """仅创建视频任务（带重试）；轮询交由 _poll_until_complete 自管。"""
        return await self._client.videos.create(**kwargs)

    # ⚠️ 240s 而不是常见的 60s：H3 的 /v1/videos **提交是同步的** —— 服务端收到
    # 请求后要下载素材 → ffmpeg 归一 → 上传 ComfyUI，做完才返回 task_id。
    # 超时的后果不是"提交失败"：调用方 abort 后服务端仍会跑完并提交任务，
    # 用户被扣了费、片子照出，调用方却永远拿不到 task_id 收不到产物。
    # Canvas 踩过同一个坑，那边也是提到 240s 才稳。
    _H3_SUBMIT_TIMEOUT_SEC = 240.0

    @with_retry_async(retryable_errors=(httpx.NetworkError, httpx.TimeoutException))
    async def _create_h3_video(self, **kwargs):
        """以 H3 要求的 JSON 合同提交，绕开 Sora SDK 固定的 multipart 编码。"""
        if not self._base_url or not self._api_key:
            raise RuntimeError("MiniMax H3 需要显式的 base_url 与 API key")
        # 时长字段与画布（ZeoCanvasLite）对齐：用 `duration` 且必须是数字。
        # 网关 adaptor 的 resolveDuration 依次取 duration → seconds → metadata.duration，
        # duration 是第一优先级；而 `seconds` 沿用 Sora SDK 的字符串写法会被 Go 侧拒成
        # `cannot unmarshal string into Go struct field clientRequest.seconds of type float64`
        # ——H3 提交必 400，且错误只在响应体里、httpx 的 raise_for_status 不带出来。
        payload = {
            "prompt": kwargs["prompt"],
            "model": kwargs["model"],
            "duration": int(kwargs["seconds"]),
            "size": kwargs["size"],
        }
        if kwargs.get("images"):
            payload["images"] = kwargs["images"]
        async with httpx.AsyncClient(timeout=self._H3_SUBMIT_TIMEOUT_SEC) as client:
            response = await client.post(
                f"{self._base_url}/videos",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.is_error:
                # 网关把拒绝原因只放响应体（如 `conditions requires at least one entry`、
                # 字段类型不符），raise_for_status 只带状态码——不附上就得靠抓包才能查。
                raise RuntimeError(f"MiniMax H3 提交失败: HTTP {response.status_code} {response.text[:400]}")
            body = response.json()
        # 中转网关对 id 字段的写法不统一，两种都收。
        task_id = (body.get("id") or body.get("task_id")) if isinstance(body, dict) else None
        if not isinstance(task_id, str) or not task_id:
            raise RuntimeError(f"MiniMax H3 响应里没有 task id: {str(body)[:200]}")
        # 后续轮询/下载复用 Sora 那条路径，只需要一个带 .id 的对象。
        return SimpleNamespace(id=task_id)

    async def _poll_until_complete(self, video_id: str, duration_seconds: int):
        """轮询任务直到状态归一到终态。

        不复用 SDK 的 client.videos.poll：它仅识别 in_progress/queued/completed/failed，
        对接返回非标状态（如 NOT_START）的 OpenAI 兼容网关时会提前退出，导致下载未就绪任务。
        """
        if _is_minimax_h3(self._model):
            max_wait = max(_H3_MIN_POLL_TIMEOUT_SECONDS, float(duration_seconds) * _POLL_TIMEOUT_PER_SECOND)
            poll_interval = _H3_POLL_INTERVAL_SECONDS
        else:
            max_wait = max(_MIN_POLL_TIMEOUT_SECONDS, float(duration_seconds) * _POLL_TIMEOUT_PER_SECOND)
            poll_interval = _POLL_INTERVAL_SECONDS

        # is_done 是纯谓词：成功 / 失败 / 过期三档都视为「已终态」让 poll 返回。
        # caller (generate / resume_video) 拿到 result 后再分流：
        #   - succeeded → 下载
        #   - failed    → is_failed 已抛 RuntimeError
        #   - expired   → 在 caller 处按 generate vs resume 上下文抛 RuntimeError / ResumeExpiredError
        # 关键不变量：is_failed 不识别 expired，避免覆盖 caller 分流。
        return await poll_with_retry(
            poll_fn=lambda: self._client.videos.retrieve(video_id),
            is_done=lambda v: _video_status(v) in TERMINAL_PROVIDER_STATUSES,
            is_failed=lambda v: (
                f"Sora 视频生成失败: {_video_error_message(v)}"
                if _video_status(v) is ProviderJobStatus.FAILED
                else None
            ),
            poll_interval=poll_interval,
            max_wait=max_wait,
            retryable_errors=OPENAI_RETRYABLE_ERRORS,
            label="OpenAI",
            on_progress=lambda v, elapsed: logger.info(
                "OpenAI 视频生成中... 状态: %s, 已等待 %d 秒", v.status, int(elapsed)
            ),
        )

    @with_retry_async(
        max_attempts=DOWNLOAD_MAX_ATTEMPTS,
        backoff_seconds=DOWNLOAD_BACKOFF_SECONDS,
        retryable_errors=OPENAI_RETRYABLE_ERRORS,
    )
    async def _download_content_with_retry(self, video_id: str):
        """单独重试内容下载，避免因下载失败重新触发视频生成。"""
        return await self._client.videos.download_content(video_id)


def _encode_start_image(image_path: Path) -> tuple[str, bytes, str]:
    mime = IMAGE_MIME_TYPES.get(image_path.suffix.lower(), "image/png")
    return (image_path.name, image_path.read_bytes(), mime)


def _is_openai_not_found(exc: BaseException) -> bool:
    """识别 OpenAI/Sora 「job 不存在」响应（NotFoundError / HTTP 404）。

    不再做 ``"not found"`` / ``"expired"`` 子串兜底：``status='expired'`` 已在
    ``_poll_until_complete`` 内直接抛 ``ResumeExpiredError`` 处理（fix #5），
    宽泛字串会把诸如 ``"file not found in storage"`` 等业务错误误判为幽灵任务。
    """
    try:
        from openai import NotFoundError  # pyright: ignore[reportMissingImports]
    except ImportError:
        NotFoundError = None  # noqa: N806

    if NotFoundError is not None and isinstance(exc, NotFoundError):
        return True
    status_code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    return status_code == 404
