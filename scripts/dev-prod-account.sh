#!/usr/bin/env bash
#
# 以绑定的生产账号启动本地开发服务（先跑 ./scripts/bind-prod-account.sh）。
#
# ⚠️ 这套配置下生成会真实扣该账号的钱，只在需要验证真实数据/真实模型时用。
set -euo pipefail

ENV_FILE=".env.prod-account"
[ -f "$ENV_FILE" ] || { echo "缺少 $ENV_FILE，先跑 ./scripts/bind-prod-account.sh <手机号或用户名>" >&2; exit 1; }

set -a; . "./$ENV_FILE"; set +a

# 本地数据独立，不碰生产：租户目录仍按 ssoSub 分，但根目录在本地。
export ARCREEL_DATA_DIR="${ARCREEL_DATA_DIR:-./.dev-data}"
export SESSION_COOKIE_SECRET="${SESSION_COOKIE_SECRET:-local-dev-only-$(printf '%040d' 0)}"
# skill.md 由后端运行期渲染，本地也得给品牌名，否则文档里还写着上游名字。
if [ -z "${BRAND_NAME:-}" ] && [ -f frontend/.env ]; then
  BRAND_NAME="$(sed -n 's/^VITE_BRAND_NAME=//p' frontend/.env | head -1)"
fi
export BRAND_NAME="${BRAND_NAME:-}"

echo "以 ${DEV_BOUND_USERNAME:-$DEV_BOUND_SSO_SUB} 身份启动 · 网关 ${DEV_BOUND_GATEWAY}"
echo "数据目录 ${ARCREEL_DATA_DIR}"
# --reload-dir 必须限定：不限定的话 watchfiles 会把 node_modules / .venv / .git /
# .worktrees 一起扫进去（十几万个文件），reloader 进程能吃掉一整核。
exec uv run uvicorn server.app:app --host 127.0.0.1 --port "${PORT:-1241}" \
  --reload --reload-dir server --reload-dir lib
