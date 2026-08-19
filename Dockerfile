# ============================================================
# Stage 1: 构建前端
# ============================================================
FROM node:22-slim AS frontend-builder

WORKDIR /build/frontend

# 启用 corepack；pnpm 版本由 frontend/package.json 的 packageManager 字段固定
# 关闭交互式下载确认，否则 docker build 这种非 TTY 环境会卡在
# "Corepack is about to download ..." 直到超时
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

# 可选镜像源：默认空 = 走官方源，行为与上游一致。
# 出网慢的构建机传 --build-arg NPM_REGISTRY=... 提速，不影响其它人。
ARG NPM_REGISTRY=""
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi

# 先复制依赖文件，利用缓存（corepack 按 packageManager 字段自动下载对应 pnpm）
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# 复制前端源码
COPY frontend/ ./

# 品牌（下游发行版白牌化）。默认空 = 保持上游 ArcReel 品牌，构建行为与上游一致。
# 值写进 .env.production 而不是 ENV，是因为 Vite 的 envPrefix 过滤只对 .env 文件
# 与 process.env 二者都生效，写文件这条路径不依赖 Vite 版本间的差异。
# ⚠️ 这里只改产品显示名；NOTICE §7(b) 要求的 "Powered by ArcReel" 署名句与仓库
#    链接在 AboutSection.tsx 内逐字硬编码，不走品牌占位，任何发行版都不得移除。
ARG VITE_BRAND_NAME=""
ARG VITE_BRAND_TAGLINE=""
ARG VITE_BRAND_DESCRIPTION=""
# AGPL §13：本运行版本对应源码的公开地址，修改版必须传。空 = 指向上游仓库。
ARG VITE_SOURCE_URL=""
RUN : > .env.production && \
    if [ -n "$VITE_BRAND_NAME" ]; then \
      echo "VITE_BRAND_NAME=\"$VITE_BRAND_NAME\"" >> .env.production; fi && \
    if [ -n "$VITE_BRAND_TAGLINE" ]; then \
      echo "VITE_BRAND_TAGLINE=\"$VITE_BRAND_TAGLINE\"" >> .env.production; fi && \
    if [ -n "$VITE_BRAND_DESCRIPTION" ]; then \
      echo "VITE_BRAND_DESCRIPTION=\"$VITE_BRAND_DESCRIPTION\"" >> .env.production; fi && \
    if [ -n "$VITE_SOURCE_URL" ]; then \
      echo "VITE_SOURCE_URL=\"$VITE_SOURCE_URL\"" >> .env.production; fi && \
    cat .env.production

RUN pnpm build

# ============================================================
# Stage 2: 生产镜像
# ============================================================
FROM python:3.12-slim AS production

# 安装系统依赖
# 同上：默认空 = deb.debian.org。python:3.12-slim (trixie) 用 deb822 格式的
# /etc/apt/sources.list.d/debian.sources，不是老的 sources.list。
ARG APT_MIRROR=""
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org|$APT_MIRROR|g" \
        /etc/apt/sources.list.d/debian.sources; \
    fi
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    bubblewrap \
    socat \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# 升级基础镜像预装的 pip：依赖全部由 uv 安装、运行时不调用 pip，
# 但 python:3.12-slim 自带的旧 pip 会被镜像扫描器报 CVE，升级以清除这些告警
RUN python -m pip install --no-cache-dir --upgrade pip

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 禁用 Python 输出缓冲，确保日志实时输出到 Docker logs
ENV PYTHONUNBUFFERED=1

# 默认时区，可由 docker-compose / 运行时 -e TZ=... 覆盖
ENV TZ=Asia/Shanghai

# 先复制依赖和包元数据文件，利用缓存
COPY pyproject.toml uv.lock README.md ./
# 同上：默认空 = pypi.org
ARG PIP_INDEX=""
ENV UV_DEFAULT_INDEX=${PIP_INDEX}
RUN uv sync --no-dev --no-install-project

# 复制应用代码
COPY lib/ lib/
COPY server/ server/
COPY alembic/ alembic/
COPY alembic.ini ./
COPY scripts/ scripts/
COPY agent_runtime_profile/ agent_runtime_profile/
COPY public/ public/

# 复制前端构建产物
COPY --from=frontend-builder /build/frontend/dist/ frontend/dist/

# 创建运行时目录
RUN mkdir -p projects vertex_keys

# 暴露端口
EXPOSE 1241

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:1241/health || exit 1

# 启动命令
CMD ["uv", "run", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "1241"]
