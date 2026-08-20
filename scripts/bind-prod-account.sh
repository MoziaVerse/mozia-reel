#!/usr/bin/env bash
#
# 把某个生产账号绑定到本地开发环境。
#
# 解决的痛点：测试服网关与生产网关**上架的模型不一样**（测试服只有文本与 TTS，
# 视频那 113 个 H3 channel 只在生产）。对着测试服开发，视频链路根本跑不到。
# 绑定之后本地直接用生产网关与生产账号，模型清单与线上一致。
#
# 安全约定（照搬 ZeoCanvasLite 的同名脚本）：
#   - ticket 的 mint 与消费**都在生产机内完成**，握手密钥不出那台机器，
#     ticket 也不经过本地磁盘
#   - 只把换到的结果回传，写进 .env.prod-account（已 gitignore）
#   - ⚠️ 该文件会存**生产网关 key 明文**，别提交、别外传
#   - ⚠️ 这套配置下生成会**真实扣该账号的钱**，验完记得切回普通 dev
#
# 用法：
#   ./scripts/bind-prod-account.sh <手机号或用户名> [--client <clientId>]
#
# --client 默认 mozia-reel。生产尚未注册该 client 时会明确报错，此时可以借用一个
# 已注册的（如 zeo-canvas）—— 本地开发要的只是那把网关 key，用哪个 client 换到的
# 在功能上等价；副作用是用量会记在那个 client 的 token 上。

set -euo pipefail

IDENT="${1:-}"
CLIENT_ID="mozia-reel"
shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --client) CLIENT_ID="${2:-}"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$IDENT" ]; then
  echo "用法: $0 <手机号或用户名> [--client <clientId>]" >&2
  exit 1
fi

ENV_FILE=".env.prod-account"

echo "正在生产 matrix 上换取 ${IDENT} 的会话（client=${CLIENT_ID}）…"

PAYLOAD=$(ssh matrix-prod 'bash -s' -- "$IDENT" "$CLIENT_ID" <<'REMOTE'
set -euo pipefail
IDENT="$1"; CLIENT_ID="$2"
cd ~/app/matrix/backend
export PATH="$HOME/.bun/bin:$PATH"

PW=$(grep -oE "^DATABASE_URL=.*" .env | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
Q() { docker exec -e PGPASSWORD="$PW" postgres-dev psql -U mozia_matrix -d mozia_matrix_db -t -A -c "$1" 2>/dev/null | tr -d '\r'; }

if [ "$(Q "select count(*) from external_client where client_id='$CLIENT_ID' and enabled")" != "1" ]; then
  echo "ERR:生产未注册或未启用 client '$CLIENT_ID'。session-init 会校验它。" >&2
  echo "ERR:可改用已注册的 client 重试，例如：--client zeo-canvas" >&2
  exit 1
fi

SUB=$(Q "select sso_sub from \"user\" where phone = '$IDENT' or username = '$IDENT' limit 1")
if [ -z "$SUB" ]; then
  echo "ERR:在生产 matrix 找不到用户 $IDENT" >&2
  exit 1
fi

TICKET=$(bun -e '
process.env.EXTERNAL_HANDOFF_SECRET = require("fs")
  .readFileSync(".env", "utf8")
  .match(/^EXTERNAL_HANDOFF_SECRET=(.*)$/m)[1].trim();
const { mintTicket } = await import("./src/lib/handoff-ticket.ts");
console.log(mintTicket({ ssoSub: process.argv[1], clientId: process.argv[2] }));
' "$SUB" "$CLIENT_ID" 2>/dev/null | tail -1)

# 就地消费 ticket 换 session（内网直连 matrix backend）
curl -sS -X POST -H 'Content-Type: application/json' \
  -d "{\"ticket\":\"$TICKET\"}" http://127.0.0.1:3257/api/external/session-init \
| bun -e '
let raw = "";
for await (const chunk of Bun.stdin.stream()) raw += new TextDecoder().decode(chunk);
const j = JSON.parse(raw);
if (!j || !j.apiKey || !j.apiKey.key) {
  console.error("ERR:session-init 未返回 apiKey：" + raw.slice(0, 200));
  process.exit(1);
}
console.log("SSO_SUB=" + j.user.ssoSub);
console.log("USERNAME=" + (j.user.username || ""));
console.log("API_KEY=" + j.apiKey.key);
console.log("GATEWAY=" + j.apiKey.endpoint);
console.log("WALLET_TOKEN=" + (j.walletToken || ""));
'
REMOTE
)

if ! echo "$PAYLOAD" | grep -q "^API_KEY="; then
  echo "换取会话失败：" >&2
  echo "$PAYLOAD" >&2
  exit 1
fi

get() { echo "$PAYLOAD" | grep "^$1=" | head -1 | cut -d= -f2-; }

upsert() {
  local key="$1" val="$2"
  touch "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE"; then
    # 用 python 改写：val 里可能含 / 与 & 之类会破坏 sed 替换的字符
    python3 - "$ENV_FILE" "$key" "$val" <<'PY'
import sys
from pathlib import Path
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
p = Path(path)
lines = p.read_text(encoding="utf-8").splitlines()
p.write_text("\n".join(f"{key}={val}" if l.startswith(f"{key}=") else l for l in lines) + "\n", encoding="utf-8")
PY
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

upsert DEV_BOUND_SSO_SUB     "$(get SSO_SUB)"
upsert DEV_BOUND_USERNAME    "$(get USERNAME)"
upsert DEV_BOUND_API_KEY     "$(get API_KEY)"
upsert DEV_BOUND_GATEWAY     "$(get GATEWAY)"
upsert DEV_BOUND_WALLET_TOKEN "$(get WALLET_TOKEN)"
upsert MATRIX_BACKEND_URL    "https://matrix.mzsjai.com"
upsert MATRIX_WEB_URL        "https://matrix.mzsjai.com"
upsert AUTH_ENABLED          "false"

chmod 600 "$ENV_FILE"

echo
echo "✓ 已绑定 $(get USERNAME) → ${ENV_FILE}"
echo "  网关: $(get GATEWAY)"
echo "  余额凭据: $([ -n "$(get WALLET_TOKEN)" ] && echo '已获取' || echo '无')"
echo
echo "启动：  ./scripts/dev-prod-account.sh"
echo "⚠️  该配置下生成会真实扣这个账号的钱，验完切回普通 dev。"
