# 修改说明 / Statement of Modifications

本文件依据 GNU Affero General Public License v3.0 第 5(a) 条与第 7(c) 条，
标明本发行版相对上游 ArcReel 所做的修改。

This file marks the modifications made by this distribution relative to
upstream ArcReel, pursuant to GNU AGPL v3.0 §5(a) and §7(c).

---

## 基本信息 / Identification

| 项 | 值 |
|---|---|
| 上游项目 / Upstream | ArcReel — https://github.com/ArcReel/ArcReel |
| 上游基线 / Baseline | `v0.27.0-62-g6117f437` |
| 本发行版名称 / Distribution | MoziaReel |
| 许可证 / License | GNU AGPL v3.0（与上游一致，未变更） |

MoziaReel 是 ArcReel 的**修改版本**，由第三方独立分发，**不是** ArcReel 官方
版本，亦不代表 ArcReel 项目或其贡献者的立场。ArcReel 名称与标识为其权利人所有，
不在 AGPL-3.0 授权范围内。

MoziaReel is a **modified version** of ArcReel, distributed independently.
It is **not** an official ArcReel release and does not represent the ArcReel
project or its contributors. The ArcReel name and logo are not licensed under
AGPL-3.0.

## 署名保留 / Attribution retained

依 `NOTICE` 中的 AGPLv3 §7(b) 附加条款，以下署名句与仓库链接在本发行版的用户
界面中逐字保留，位于「设置 → 关于」页的法律声明区块
（`frontend/src/components/pages/settings/AboutSection.tsx`）：

> Copyright © 2026 Pollo3470 and ArcReel contributors
>
> Powered by ArcReel — https://github.com/ArcReel/ArcReel

品牌化只改变产品显示名（`VITE_BRAND_*`），**不触及**上述署名——它不走品牌占位符。

## 源码获取 / Source availability (AGPL §13)

本发行版以网络服务形态提供。依 AGPL v3.0 第 13 条，任何通过网络与本服务交互的
使用者，均有权取得本发行版对应的完整源代码（含下列全部修改）。

Users interacting with this service over a network are entitled to the
complete corresponding source code of this modified version.

- 源码获取地址 / Source URL：<https://github.com/MoziaVerse/mozia-reel>
- 获取方式同样在产品内「设置 → 关于」页给出（构建期经 `VITE_SOURCE_URL` 注入）。

该仓库公开。本发行版对平台上的所有使用者开放（未在应用市场列出，但那是隐蔽
而非访问控制），使用者不是一个可枚举的小集合，因此只有公开仓库能稳妥履行
"使用者能取得对应源码"的要求。

> ⚠️ 该地址须与线上运行的版本对应。发版后未推送，同样构成违约。

## 修改清单 / List of changes

以下为相对上游基线的全部改动，按主题归类。

### 1. 接入 Matrix 平台握手协议

以独立 SaaS 形态服役于 Matrix 应用市场，访问控制由握手门禁承担，替代上游自带的
用户名密码登录。

- 新增 `lib/matrix_session.py` — 会话签发与校验（HMAC 签名 cookie）
- 新增 `server/matrix_gate.py` — `MatrixSessionGate` 中间件，未握手的 API 请求
  返回 401、浏览器导航重定向到平台 launch 中继页
- 新增 `server/routers/matrix_session.py` — `/handoff` 握手端点
- 修改 `server/app.py` — 装配上述中间件与路由
- 新增 `deploy/matrix/` — 该形态专用的 compose 与环境变量模板

### 2. 多租户数据隔离

上游为单租户自托管设计。本发行版以平台侧的 `ssoSub` 作为租户维度，使单实例可
服务多个互不可见的用户。

- 新增 `lib/tenant_context.py` — 基于 `ContextVar` 的租户上下文与租户名校验
- 修改 `lib/app_data_dir.py`、`lib/db/engine.py`、`lib/db/__init__.py` —
  数据目录与数据库连接按租户分流（每租户独立库）
- 修改 `lib/project_manager.py`、`lib/system_config.py` — 数据入口跟随租户
- 修改 `lib/generation_queue.py`，新增 `lib/worker_supervisor.py` —
  生成任务队列与 worker 按租户拆分，任务入队时记录租户、执行时显式恢复

### 3. 经 Mozia 网关接入视频模型

- 修改 `lib/video_backends/openai.py` — 支持经网关调用 MiniMax H3
- 新增 `lib/reference_image_hosting.py` — 参考图对外可达地址的托管
- 修改 `lib/custom_provider/endpoints.py`、`server/routers/providers.py`、
  `server/routers/custom_providers.py` — 相应的供应商声明与路由适配

### 4. 品牌化与视觉体系 / White-labeling and visual system

仅改变用户界面的外观，不改变功能，不移除任何署名。

视觉上对齐 Matrix 平台的设计体系（配色、字体、圆角与其同族），并新增浅色主题：

- 修改 `frontend/src/index.css` — 色板重构为 `@theme inline` + `:root`（浅色）
  / `.dark`（深色）两套变量，取值对应 matrix 的 shadcn 变量；字体改用 Sora，
  圆角改用 matrix 的 `--radius` 派生档
- 新增 `frontend/src/stores/theme-store.ts` 与
  `frontend/src/components/ui/ThemeToggle.tsx` — 主题在「跟随系统 / 浅色 /
  深色」间切换并记忆；`frontend/index.html` 加首屏内联脚本消除主题闪烁
- 修改 `frontend/vite.config.ts`，新增 `frontend/src/brand-defaults.ts` —
  构建期重写 `index.html` 的标题与描述，默认值与运行时共用一处
- 组件内散落的一次性色值改用 CSS `light-dark()` 就地给出明暗两套取值

品牌名称与标识：

- 修改 `Dockerfile` — 新增 `VITE_BRAND_NAME` / `VITE_BRAND_TAGLINE` /
  `VITE_BRAND_DESCRIPTION` / `VITE_SOURCE_URL` 四个 build-arg，经上游既有的品牌覆盖机制
  （`frontend/src/branding.ts`）注入；默认空值时行为与上游完全一致
- 替换 `frontend/public/` 下的 logo、favicon 与 PWA 图标资源
- 修改 `frontend/public/site.webmanifest` — `name` / `short_name`
- 修改 `.dockerignore` — 排除本地 `frontend/.env`，保证镜像构建可复现

### 5. 平台音色库与参考音频克隆

上游按 OpenAI 官方 schema 假设 TTS 用 preset voice（`alloy` 等）。中转网关上的
自建 TTS 不成立：`index-tts-v2` 对任何 preset voice 返回 400
`preset voice not allowed`，omnivoice 则静默忽略——音色下拉里一个能用的都没有。
这类模型表达音色的方式是声音克隆（请求带 `ref_audio` 参考音频）。

- 新增 `lib/voice_library.py` 与 `voice_library/` — 随发行版打包的平台音色库
  （27 条参考音频 + manifest），id 稳定以免影响上游既有的 TTS 产物新鲜度指纹
- 修改 `lib/audio_backends/openai.py`：
  - `list_voices()` 对自定义供应商改为返回「模型自带音色」+ 库音色，不再返回
    必然失败的官方 preset 目录；官方 OpenAI 通路（含 legacy 收窄）行为不变
  - `_request_speech()` 对库音色发 `ref_audio`（及可信时的 `ref_text`）而非
    `voice`；`_post_speech_without_voice` 更名 `_post_speech_raw`，承载
    SDK 表达不了的请求体
- 修改 `lib/i18n/{zh,en,vi}/assets.py` — 新增 `voice_label_model_default`
- 修改 `Dockerfile` — 复制 `voice_library/`

音色库为空时退化为只剩「模型自带音色」，不影响可用性。

### 6. 设置页按托管态重排

上游设置页以「供应商 / 渠道」为组织轴，那是给自己配 key 的用户设计的。托管态下
网关只有一个、由平台下发，用户既选不了也换不了 —— 把它当成可管理对象展示，只会
让人去找不存在的开关。

- 新增 `frontend/src/components/pages/settings/AccountSection.tsx` — 账户页，
  字段与积分口径（1 积分 = ¥0.01）对齐 Matrix 站内个人资料页；只读，改动跳回 Matrix
- 修改 `server/routers/matrix_session.py` — 总览补 `user`（用户名 / ssoSub），
  只取自服务端已验证来源（签名 cookie 或绑定账号 env）
- 修改 `frontend/src/api.ts`、`frontend/src/types/matrix.ts` — 接上既有的
  `/matrix-session/credits` 与 `/logout`（此前只有后端实现，前端从未消费）
- 重写 `frontend/src/components/pages/settings/MatrixGatewaySection.tsx` —
  连接状态 / 网关主机名 / 媒体计数那套渠道卡片删除，改为按媒体分组的可用模型清单，
  并入模型页
- 修改 `frontend/src/components/pages/SystemConfigPage.tsx` — 托管态与独立部署
  两套侧栏；托管态无供应商页，存量 `?section=providers` 书签落到账户页
- 修改 `frontend/src/components/pages/settings/MediaModelSection.tsx` —
  分组标题去掉 "Channel" 措辞
- 修改 `frontend/src/components/ui/ProviderModelSelect.tsx` — 候选只有一家供应商
  且回退值也属于它时省略前缀；回退值来自候选之外时保留（那时供应商恰是关键信息）
- 新增 `frontend/src/components/agent/AgentModelRouting.tsx` — 托管态智能体页
  只保留 SDK 各档位的模型路由，候选取网关文本模型目录；上游那套凭证 CRUD
  （选供应商 / 代理地址 / API 密钥 / 增删改 / 连接测试）在托管态下一个都不成立
- 修改 `frontend/src/components/agent/AgentPageIntro.tsx` — Claude Code 兼容提示
  改为可关，托管态下不显示（用户没有配置项可填）
- 修改 `public/skill.md.template`、`server/app.py`、`Dockerfile` —
  文档里的产品名改用 `{{BRAND}}` 占位符，运行期从 `BRAND_NAME` 填充。此前前端
  已改名而这份文档仍写上游名，外部 Agent 会看到两个不一致的产品
- 新增 `lib/tenant_api_key.py` 与 `server/mcp_tenant_gate.py` — 托管态支持「外部
  Agent 经远程 MCP 驱动本站」：签发的 API Key 带上租户段（`arc-<tenant>-<32 hex>`，
  单机态仍是上游的 `arc-<32 hex>`），租户门在上游那套 Bearer 鉴权之前把它解出来设进
  ContextVar。上游按单用户设计（ADR 0065，工具调用沿用 `DEFAULT_USER_ID`），而托管态
  一租户一库、`api_keys` 表本身就落在租户库里——不先定出租户就无从验 key。租户逻辑
  全部留在这两个新文件内，`server/remote_mcp.py` 一行未动
- 修改 `frontend/src/stores/config-status-store.ts` — 暴露 `managed`，
  各处复用同一次总览请求判断托管态
- 新增 `lib/matrix_blocklist.py` — 可选的拒止名单，按 ssoSub 封禁指定用户。
  默认全部放行（本站靠"不在应用市场列出"控制传播，那是隐蔽而非访问控制），
  名单提供的是"出事能立刻踢人"。文件形式（改完下一个请求即生效，不必重启，
  也就不会打断在跑的生成任务），握手与门禁两处执行；文件读不到时谁都不拒
  并告警——全拒会挡住所有人，而名单要挡的通常只是个位数
- 新增 `frontend/src/components/pages/settings/MatrixUsageSection.tsx` 与
  `GET /matrix-session/usage`（代理 matrix `/api/external/logs`）—— 托管态用量页
  改用平台账务数据。本地账本记的是「我们以为花了多少」，平台记的是实际扣费，
  两者并列只会让人怀疑哪个都不准。消费 / 失败 / 退款分开统计：上游把三者混在
  一条流水里，而失败记录 quota=0，不区分就是一串「0 消耗」
- 修改 `lib/matrix_session.py` — 模型目录改从 matrix `/api/external/catalog` 取，
  用平台算好的 `model_type` 分桶，不再按模型名猜（上游 `supported_endpoint_types`
  普遍只回 `["openai"]`，猜的结果会把 TTS / embedding / OCR 混进对话模型）。
  vision / embedding / rerank 本地无对应链路，不收。
  握手时逐项合并而非删表重插：新增补进、下架标禁用（项目里可能还引用着）、
  endpoint 跟平台走（错了就是必然失败），用户设的默认项保留

### 7. 构建

- 修改 `Dockerfile` — 新增 `APT_MIRROR` / `PIP_INDEX` / `NPM_REGISTRY`
  可选 build-arg，默认空值不启用，出网慢的构建机可显式传入提速

### 8. 同步上游 v0.27 时的取舍

- 删除 `README.md` 的赞助位 —— 上游那段的注册链接带 `?s=arc` 推荐码。第三方发行版
  转载它会把本发行版的用户导到上游的返佣下，与"独立分发"的定位不符。上游其余
  README 内容照旧沿用
- `server/matrix_gate.py` 放行 `/mcp` 与 `/agent-installation-guide.md` —— 两者的
  访问方都是用户自己的 Agent 宿主，带不了会话 cookie；前者自带 API Key 鉴权，后者
  只讲怎么接线、不含租户数据。`/mcp` 的租户由 `McpTenantGate` 承担，它 fail closed：
  门禁那条"静态资源放行"的兜底分支只看 `/api/` 前缀与是否浏览器导航，MCP 客户端的
  POST 两条都不满足，漏过去不会报错，只会让租户恒为空、工具静默写进共享数据根
- 上游把 `/skill.md` 改名为 `/agent-installation-guide.md`，本发行版的品牌替换
  随之迁到新端点；`public/agent-installation-guide.md` 中的产品名参数化为
  `{{BRAND}}`，但 `ArcReel/skills`、`setup-arcreel-skills` 这类指向上游实际仓库与包
  的标识符保持原样
- 不采纳上游 `test_list_voices_returns_full_catalog_for_custom_openai_tts_endpoint`
  与 `test_list_voices_legacy_narrowing_only_applies_to_official_openai` —— 它们断言
  自定义 endpoint 保持全量音色目录，而本发行版的网关自建 TTS 对任何 preset voice 都
  返回 400（见第 5 节）
- H3 的 `text_to_video` 按型号细分：生产网关实测只有 `ref2va` 强制要求参考素材
  （不带图提交返回 400 `MoziaH3 ref2va task requires reference material`），
  `t2va` / `fl2va` / `2k` 都受理纯文生。按 `minimax-h3` 前缀一刀切会把三个能纯文生的
  型号封在提交之前
- 调整上游 `test_endpoint_declaring_int_cap_rebuilds_capabilities` —— 本发行版把
  `openai-video` 改为按 model 读 backend caps（该 endpoint 上同时挂着 Sora 与 H3，
  参考图上限不同），endpoint 维度不再声明硬上限
- 调整上游 `test_alembic_tasks_index_preservation` 的全链走查 —— 本发行版的迁移链上
  有一个合并节点（`0a4b49210d55`，把上游链与 matrix 链并起来），走查改为按
  `alembic_version` 的实际状态推进。用合并节点而非把本分支重接到上游 head：已部署的库
  停在本分支 head，重接后上游那一支会落在"当前版本之下"被静默跳过
- 页头 `AgentPageIntro` 保留本发行版的单块形态（不露出底层供应商，见第 6 节），
  上游改成的「内嵌 / 外部」并列双卡片不采纳；上游新增的外部接入指引作为可选入口
  挂在页头里，托管态不传该回调即不渲染。上游两条断言双卡片的用例随之改为断言本形态
- 新增 `lib/matrix_base.py` —— 零依赖地存放 `GATEWAY_PROVIDER_DISPLAY_NAME` 与
  `session_signing_secret()`。此前低层模块只为这两样就 import 整个 `lib.matrix_session`，
  把 `lib.custom_provider` 拖进 `lib.video_backends`，撞上游的 import 分层契约

### 测试

上述改动附带的测试位于 `tests/unit|integration/` 的源码镜像目录下（上游 v0.27 起
`tests/` 根目录不再允许平铺测试，档位 marker 由目录注入）：
`test_tenant_isolation.py`、`test_matrix_session_gate.py`、`test_h3_video_via_gateway.py`、
`test_voice_library.py`、`test_agent_installation_guide.py`、`test_matrix_usage.py`、
`test_matrix_model_catalog.py`、`test_matrix_blocklist.py`、`test_matrix_account.py`、
`test_matrix_capabilities.py`、`test_matrix_default_backends.py`、
`test_narration_voice_backend.py`、`test_public_media_router.py`、
`test_qwen_image_routing.py`、`test_signed_media_url.py`、
`test_alembic_custom_provider_owner_sso_sub.py`，以及 `tests/conftest.py`、
`test_app_module.py`、`test_auth_coverage.py`、`test_custom_provider_endpoints.py`
的相应调整。
