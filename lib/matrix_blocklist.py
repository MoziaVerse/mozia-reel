"""拒止名单：默认所有 matrix 用户可用，命中名单的拒绝。

**为什么是黑名单不是白名单**：本站没有推广入口，靠"不在应用市场列出"控制传播；
matrix 那边已有 2500+ 用户且仍在增长，逐个加白名单既维护不动，也挡不住什么——
真正需要的能力是"出事了能把某个人踢掉"，那就是黑名单。

**为什么按 ssoSub**：服务端签发、用户改不了，而且它同时就是我们的租户键——
封禁与数据隔离用同一个身份，不会出现"封了 A、数据落到 B"。用户名是 Casdoor
登录主键，用户能自己改，改完就绕过了。

**为什么用文件**：封禁要立刻生效。env 得重启容器，而重启会打断正在跑的生成
任务；文件改完下一个请求就生效（按 mtime+size 判断是否重读）。
"""

from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

_ENV_KEY = "MATRIX_BLOCKLIST_FILE"

_lock = threading.Lock()
# (mtime, size, 名单)。size 一并比对：同一秒内的改动 mtime 可能不变。
_cache: tuple[float, int, frozenset[str]] | None = None
_warned_missing = False


def blocklist_path() -> str:
    return os.environ.get(_ENV_KEY, "").strip()


def _parse(text: str) -> frozenset[str]:
    """一行一条 ssoSub，支持 ``#`` 注释与行尾注释：``<sub>  # 滥用/2026-08-21``。"""
    entries = set()
    for line in text.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            entries.add(entry)
    return frozenset(entries)


def load_blocklist() -> frozenset[str]:
    """返回被拒的 ssoSub 集合。未配置或读不到时返回空集（即不拒绝任何人）。

    读不到文件时放行而不是全拒：全拒会把**所有**用户挡在外面，而黑名单要挡的
    通常只是个位数的人。两种故障都不好，但前者的影响面小几个数量级。
    代价是被封的人在文件故障期间能回来——所以故障要记 WARNING，靠日志发现。
    """
    global _cache, _warned_missing
    path = blocklist_path()
    if not path:
        return frozenset()

    try:
        stat = os.stat(path)
    except OSError as exc:
        if not _warned_missing:
            logger.warning("拒止名单文件读不到，本次不拒绝任何人: %s (%s)", path, exc)
            _warned_missing = True
        with _lock:
            _cache = None
        return frozenset()
    _warned_missing = False

    key = (stat.st_mtime, stat.st_size)
    with _lock:
        if _cache is not None and (_cache[0], _cache[1]) == key:
            return _cache[2]

    try:
        with open(path, encoding="utf-8") as fh:
            entries = _parse(fh.read())
    except OSError as exc:
        logger.warning("拒止名单文件读取失败，本次不拒绝任何人: %s (%s)", path, exc)
        return frozenset()

    with _lock:
        _cache = (stat.st_mtime, stat.st_size, entries)
    logger.info("拒止名单已加载: %d 人 (%s)", len(entries), path)
    return entries


def is_allowed(sso_sub: str | None) -> bool:
    """该用户是否可以使用本站。默认可以，命中拒止名单则不可以。

    身份缺失时返回 True —— 空身份不可能命中名单，"是否算合法会话"不归这里管，
    那是门禁验签与租户校验的职责。在这里顺手拒掉会把两件事混在一起，日后改动
    任一边都要连带想另一边。
    """
    blocked = load_blocklist()
    return not (sso_sub and sso_sub in blocked)


def _reset_for_tests() -> None:
    global _cache, _warned_missing
    with _lock:
        _cache = None
    _warned_missing = False
