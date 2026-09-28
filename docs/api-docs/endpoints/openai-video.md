# openai-video

- 协议：[OpenAI video generation](https://developers.openai.com/api/docs/guides/video-generation)
- 计费：[API pricing](https://developers.openai.com/api/docs/pricing)
- 代码：`lib/custom_provider/endpoints.py::ENDPOINT_REGISTRY["openai-video"]`、`lib/video_backends/openai.py::OpenAIVideoBackend`
- 任务状态：官方 Sora 只发协议文档列出的状态串，代理网关转发非 Sora 型号时会透传底层厂商的写法，故过共享归一 `lib/video_backends/base.py::normalize_provider_status`
- MiniMax H3（经 Mozia 网关，同一 `/v1/videos` 端点）：[MiniMax H3 视频生成 API](https://matrix.mzsjai.com/docs/matrix/models/minimax-h3)；依赖其中的对外型号与素材角色（t2va / fl2va / ref2va）、size 可识别取值、旧字段兼容（`images` 在 fl2va 按首尾帧解释）。代码：`lib/video_backends/openai.py::_h3_variant`、`OpenAIVideoBackend.video_capabilities_for_model`、`_resolve_size`
