#!/usr/bin/env bash
# 干净重启本地开发服务。
#
# 为什么不直接靠 --reload：uvicorn 的 reload 子进程是 multiprocessing spawn 出来的，
# 命令行长成 `python -c from multiprocessing.spawn import ...`。父进程一旦被按名字
# kill（匹配 "uvicorn server.app"），这些子进程匹配不上、会活成孤儿——它们仍在轮询
# 同一个 SQLite 任务队列，于是**改完的代码不生效，任务还被旧进程消费掉**，报错指向
# 的行号和文件对不上，极难定位。这里按可执行文件路径杀干净。
set -euo pipefail

cd "$(dirname "$0")/.."

pkill -9 -f "$PWD/.venv/bin/python" 2>/dev/null || true
pkill -9 -f "uv run uvicorn" 2>/dev/null || true
sleep 1

nohup ./scripts/dev-prod-account.sh > /tmp/arcreel-dev.log 2>&1 &

for _ in $(seq 1 40); do
  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:"${PORT:-1241}"/health; then
    echo "已就绪 http://127.0.0.1:${PORT:-1241}"
    exit 0
  fi
  sleep 2
done
echo "启动超时，看 /tmp/arcreel-dev.log" >&2
exit 1
