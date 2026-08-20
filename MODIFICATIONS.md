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
| 上游基线 / Baseline | `v0.26.0-91-ge505735c` |
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

- 源码获取地址 / Source URL：**（待填写：面向服务使用者公开的仓库或下载地址）**
- 获取方式同样在产品内「设置 → 关于」页给出。

> ⚠️ 该地址在对外提供服务前必须填写并可访问，否则本发行版处于 AGPL §13 违约状态。

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

### 7. 构建

- 修改 `Dockerfile` — 新增 `APT_MIRROR` / `PIP_INDEX` / `NPM_REGISTRY`
  可选 build-arg，默认空值不启用，出网慢的构建机可显式传入提速

### 测试

上述改动附带的测试：`tests/test_tenant_isolation.py`、
`tests/test_matrix_session_gate.py`、`tests/test_h3_video_via_gateway.py`、
`tests/test_voice_library.py`、`tests/test_skill_md.py`，
以及 `tests/conftest.py`、`tests/test_app_module.py`、
`tests/test_auth_coverage.py`、`tests/test_custom_provider_endpoints.py`
的相应调整。
