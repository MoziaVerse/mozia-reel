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

### 5. 构建

- 修改 `Dockerfile` — 新增 `APT_MIRROR` / `PIP_INDEX` / `NPM_REGISTRY`
  可选 build-arg，默认空值不启用，出网慢的构建机可显式传入提速

### 测试

上述改动附带的测试：`tests/test_tenant_isolation.py`、
`tests/test_matrix_session_gate.py`、`tests/test_h3_video_via_gateway.py`，
以及 `tests/conftest.py`、`tests/test_app_module.py`、
`tests/test_auth_coverage.py`、`tests/test_custom_provider_endpoints.py`
的相应调整。
