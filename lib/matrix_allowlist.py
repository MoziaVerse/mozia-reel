"""受邀名单：把本发行版限制给指定的 matrix 用户。

**为什么按 ssoSub 而不是用户名**：ssoSub 由服务端签发、用户改不了，而且它同时
就是我们的租户键——名单与数据隔离用同一个身份，不会出现「名单放行了 A、数据
落到 B」这种错位。用户名（Casdoor 登录主键）用户可以在 matrix 个人资料页自己
改：改了就把自己锁在外面，而腾出来的旧名被别人注册后，那个人会**继承**访问权。

**为什么用文件而不是环境变量**：撤权。env 要重启容器才生效，而重启会打断正在
跑的生成任务；文件改完下一个请求就生效（按 mtime 判断是否重读）。

**没配名单 = 不限制**，保持未接入前的行为，本地与测试服不受影响。
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_ENV_KEY = "MATRIX_ALLOWLIST_FILE"

_lock = threading.Lock()
# (mtime, size, 名单)。size 一并比对：同一秒内的改动 mtime 可能不变。
_cache: tuple[float, int, frozenset[str]] | None = None
_warned_missing = False


def allowlist_path() -> str:
    return os.environ.get(_ENV_KEY, "").strip()


def _parse(text: str) -> frozenset[str]:
    """一行一条 ssoSub，支持 ``#`` 注释与行尾注释：``<sub>  # 张三``。"""
    entries = set()
    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.add(entry)
    return frozenset(entries)


def load_allowlist() -> frozenset[str] | None:
    """返回名单；``None`` 表示不限制。

    读不到文件时返回 None（放行）而不是空集（全拒）。挂载出问题、文件被误删这类
    故障下，fail closed 会把**所有**用户锁在外面，包括本来该进来的人——那是比
    "名单暂时失效"更糟的结果。故障会记 WARNING，靠日志发现。
    """
    global _cache, _warned_missing
    path = allowlist_path()
    if not path:
        return None

    try:
        stat = os.stat(path)
    except OSError as exc:
        if not _warned_missing:
            logger.warning("受邀名单文件读不到，本次不限制访问: %s (%s)", path, exc)
            _warned_missing = True
        with _lock:
            _cache = None
        return None
    _warned_missing = False

    key = (stat.st_mtime, stat.st_size)
    with _lock:
        if _cache is not None and (_cache[0], _cache[1]) == key:
            return _cache[2]

    try:
        with open(path, encoding="utf-8") as fh:
            entries = _parse(fh.read())
    except OSError as exc:
        logger.warning("受邀名单文件读取失败，本次不限制访问: %s (%s)", path, exc)
        return None

    with _lock:
        _cache = (stat.st_mtime, stat.st_size, entries)
    logger.info("受邀名单已加载: %d 人 (%s)", len(entries), path)
    return entries


def is_allowed(sso_sub: str | None) -> bool:
    """该用户是否可以使用本站。未配名单时恒为 True。"""
    entries = load_allowlist()
    if entries is None:
        return True
    return bool(sso_sub) and sso_sub in entries


def _reset_for_tests() -> None:
    global _cache, _warned_missing
    with _lock:
        _cache = None
    _warned_missing = False
