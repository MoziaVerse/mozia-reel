# MoziaReel 生产部署 SOP

对齐生产上 canvas 的做法（`~/app/mozia-canvas`），差别只在 canvas 跑裸进程、
我们跑容器。

## 存储

数据目录**放在代码树之外**：

```
MOZIA_REEL_DATA_DIR=/home/server/mozia-reel-data
```

canvas 用的是 `ZEO_DATA_DIR=/home/server/zeo-canvas-data`，同一套理由：上线要
重建镜像、拉新代码、必要时整目录换一份，数据挂在代码目录底下迟早被某次
"清干净重来"连坐。放外面之后，部署动的是代码，数据一动不动。

容器内路径不变（`/app/projects`），租户仍在 `projects/tenants/<ssoSub>/`。

**规模参考**：canvas 生产 411 个租户共 23G，最重的单租户 2.1G。视频比图更占
地方。上线前确认目标盘余量——写这份文档时 `/` 只剩 62G。

## 首次上线

```bash
# 1) 目录
mkdir -p /home/server/mozia-reel-data/{projects,logs,claude}
mkdir -p ~/app/arcreel && cd ~/app/arcreel        # 代码用传输，不是 git clone

# 2) 环境（.env 在 deploy/matrix/ 下，不入库）
#    必填：MATRIX_BACKEND_URL / MATRIX_WEB_URL / SESSION_COOKIE_SECRET
#    生产必须 SESSION_COOKIE_SECURE=true（https 下 cookie 才发得出去）
#    MOZIA_REEL_DATA_DIR=/home/server/mozia-reel-data

# 3) 构建并起
cd deploy/matrix && docker compose up -d --build
curl -sf http://localhost:1241/health && echo OK

# 4) Caddy：把 Caddyfile.mozia-reel 的内容写进 /etc/caddy/Caddyfile
#    （该文件属主是 server，不需要 sudo），然后：
caddy reload --config /etc/caddy/Caddyfile --force
#    ⚠️ 不要用 systemctl reload caddy —— 会挂在 reloading 状态。
```

## 蓝绿切换（版本更新）

沿用 canvas 的做法，切换零中断、回滚一步到位：

1. 新版本起到临时端口 1242（改 compose 的 ports 与容器名）
2. `curl -sf http://localhost:1242/health` 通过后，改 Caddyfile 的
   `reverse_proxy` → 1242，`caddy reload --force`
3. 停旧容器
4. **回滚 = Caddy 指回 1241**，旧容器还在，不需要重建

生成任务是入队执行的，切换瞬间在跑的任务会随旧容器停止而中断——低峰期切，或
先看任务面板确认没有在途任务。

## 入口

`external_client` 注册 `mozia-reel` 即可握手；**不建 `project` 行**就不会出现在
应用市场卡片里（卡片来自 `project` 表，握手只看 `external_client`）。

注意这是"不列出"，不是访问控制：拿到 URL 的任何 matrix 账号都能进来用，花的是
他自己的积分。要限人得另加白名单。

## 受邀名单（可选）

限定只有指定的 matrix 用户能用。**不配就是不限制**，本地与测试服不受影响。

```bash
# 宿主上准备名单，一行一个 ssoSub（# 后是注释）
cat > /home/server/mozia-reel-data/allowlist.txt <<'LIST'
# 灰度名单
1f917403-5af6-4b8b-9f16-59636f3af474   # 张三
6e8cbfe2-e486-4758-9c39-c5abc3c382a8   # 李四
LIST

# .env 里指向它
MOZIA_REEL_ALLOWLIST=/home/server/mozia-reel-data/allowlist.txt
MATRIX_ALLOWLIST_FILE=/app/allowlist.txt
```

用 ssoSub 而不是用户名：ssoSub 由服务端签发、用户改不了，而且它同时就是租户键。
用户名是 Casdoor 登录主键，用户能在 matrix 个人资料页自己改——改了就把自己锁在
外面，腾出来的旧名被别人注册后那个人会继承访问权。

ssoSub 在「设置 → 账户 → 用户 ID」页面上，用户自己就能复制给你。

两处执行：握手时拒绝（给明确文案），门禁每请求再查一次（**移出名单立即生效**，
不必重启，也就不会打断正在跑的生成任务）。

文件读不到时**放行并告警**，不是全拒——挂载出问题会把所有人锁在外面，比名单
暂时失效更糟。靠日志里的 WARNING 发现。

⚠️ `DEV_BOUND_*`（绑定生产账号模式）会跳过整条门禁，名单一并失效。那是本地
开发用的，生产上绝不能配。

## AGPL

`VITE_SOURCE_URL` 必须填**本发行版自己**的源码公开地址。留空会渲染成上游仓库
地址——对修改版而言那不是"对应源码"，等于违约（§13）。
