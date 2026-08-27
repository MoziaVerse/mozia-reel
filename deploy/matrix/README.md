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
#    ARCREEL_PUBLIC_BASE_URL=https://reel.mzsjai.com  ← 见下节「参考图直链」

# 3) 构建并起
cd deploy/matrix && docker compose up -d --build
curl -sf http://localhost:1241/health && echo OK

# 4) Caddy：把 Caddyfile.mozia-reel 的内容写进 /etc/caddy/Caddyfile
#    （该文件属主是 server，不需要 sudo），然后：
caddy reload --config /etc/caddy/Caddyfile --force
#    ⚠️ 不要用 systemctl reload caddy —— 会挂在 reloading 状态。
```

## 构建踩过的坑

**构建失败报 `failed to compute cache key: short read ... unexpected EOF`**
= buildkit 缓存里的层坏了，不是网络问题。清掉重来：

```bash
docker builder prune -af
```

⚠️ 顺带说个量级：生产上第一次跑这条清出了 **216GB**。那些陈年缓存在
`docker images` 里完全看不见（镜像实存 `/var/lib/containerd`，`docker rmi` 与
`docker system prune` 都回收不到），部署前值得先看一眼 `docker system df`。

**基础镜像 `ghcr.io/astral-sh/uv` 拉得很慢**（实测 ~30KB/s）。apt / pip / npm
都走了阿里源，只有这个没有镜像源可用，首次构建要等。已构建过就命中缓存。

**容器里的 `python3` 不是应用的解释器**。应用跑在 uv 管理的 venv 里，要用
`/app/.venv/bin/python`；直接 `python3` 会 `ModuleNotFoundError: pydantic`。

```bash
docker exec -w /app matrix-mozia-reel-1 /app/.venv/bin/python -c '...'
```

## Caddy 的真实情况

`caddy validate` / `caddy reload` 以 `server` 身份跑时，会因为读不了 caddy 用户
拥有的日志文件而报 `permission denied` —— **那不代表配置有问题**。reload 的
provisioning 发生在运行中的 caddy 进程里（caddy 身份），照样能成功。判断是否
真的生效，看运行中的配置而不是 validate 的输出：

```bash
curl -s http://127.0.0.1:2019/config/apps/http/servers | python3 -c "
import json,sys
hosts=set()
for srv in json.load(sys.stdin).values():
    for r in srv.get('routes',[]):
        for m in r.get('match',[]): hosts.update(m.get('host',[]))
print(sorted(hosts))"
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

## 参考图直链（必配）

视频模型（H3）不收 multipart，参考图只能以**上游自己能拉到的公网 https URL** 提交。
这条链由本站自签短时效直链承担，需要告诉它自己对外是什么地址：

```bash
ARCREEL_PUBLIC_BASE_URL=https://reel.mzsjai.com
```

配了它，`ARCREEL_REFERENCE_HOSTING` 就默认走 `self`（本站出链）；不配则回落到网关的
`/v1/sd/upload`。⚠️ **别回落**：网关那条产出的直链落在 `cdn.mjapi.cc.cd` 域下，H3 上游
取不到该域名——同域下无论路径是否存在都回 500，报出来只有一句 `Internal Server Error`，
指不到根因。换成本站域名的直链即可正常提交（与画布走 `canvas.mzsjai.com` 同一形态）。

直链是 `/public/media/<token>`，匿名可达但每条 token 只认领一个文件、6 小时过期，
签名密钥复用 `SESSION_COOKIE_SECRET`。所以那个 secret 一变，在途任务手里的直链会一起失效
（只影响还没被上游拉走的参考图，重试即可）。

## 拒止名单（可选）

**默认所有 matrix 用户都能用**，靠"不在应用市场列出"控制传播——那是隐蔽，不是
访问控制。这份名单提供的是另一件事：**出事了能把某个人立刻踢掉**。

```bash
# 宿主上准备名单，一行一个 ssoSub（# 后写原因和日期）
mkdir -p /home/server/mozia-reel-data/access
cat > /home/server/mozia-reel-data/access/blocklist.txt <<'LIST'
# 封禁记录
# 6e8cbfe2-…   # 滥用 2026-08-21
LIST

# .env 里指向它（挂的是目录，不是文件）
MOZIA_REEL_ACCESS_DIR=/home/server/mozia-reel-data/access
MATRIX_BLOCKLIST_FILE=/app/access/blocklist.txt
```

⚠️ **挂目录不挂文件**。单文件 bind mount 绑的是 inode，任何"写新文件再替换"式的
编辑（`mv` / `sed -i` / vim 默认写法）都会换掉 inode，容器从此一直读那个已删除的
旧文件——**封禁看着生效、解封永远不生效，而且没有任何报错**。

用 ssoSub 而不是用户名：ssoSub 由服务端签发、用户改不了，而且它同时就是租户键。
用户名是 Casdoor 登录主键，用户能自己改，改完就绕过了。ssoSub 在「设置 → 账户 →
用户 ID」页面上。

两处执行：握手时拒绝（给明确文案），门禁每请求再查一次（**封禁立即生效**，
不必重启，也就不会打断正在跑的生成任务）。

文件读不到时**谁都不拒**，不是全拒——全拒会把所有人挡在外面，而名单要挡的通常
只是个位数。代价是被封的人在故障期间能回来，靠日志里的 WARNING 发现。

⚠️ `DEV_BOUND_*`（绑定生产账号模式）会跳过整条门禁，名单一并失效。那是本地
开发用的，生产上绝不能配。

## AGPL

`VITE_SOURCE_URL` 指向 <https://github.com/MoziaVerse/mozia-reel>（公开仓）。

本站对所有 matrix 用户开放，使用者不是一个可枚举的小集合，所以只有公开仓能稳妥
履约。⚠️ 地址须与线上运行的版本对应——**发版后忘了推同样构成违约**。
