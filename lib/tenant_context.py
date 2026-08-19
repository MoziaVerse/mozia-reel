"""租户上下文：把 matrix 的 ssoSub 作为数据隔离维度。

与 ZeoCanvasLite 同一套模型（见其 `src/server/auth/tenant.ts`）：
**tenant = ssoSub**，数据落 `DATA_ROOT/<tenant>/…`，租户由服务端从会话
cookie 解出并强制，前端无法伪造。

Python 侧用 ContextVar 承载：ArcReel 的数据入口（app_data_dir / DB engine /
ProjectManager）都是全局单例，把"当前租户"放进 ContextVar 后，这些入口按租户
分流即可，**不必给 200 处调用点逐个传参**。

⚠️ ContextVar 只在同一执行上下文内继承。后台 worker、agent 子任务不在请求
上下文里，必须在任务入队时把租户记下来、执行时用 `tenant_scope()` 显式恢复，
否则会落到共享的默认根 —— 那是静默的跨租户串数据，不会报错。
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterator
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# 租户名会直接作为目录名，必须防路径穿越 —— 只允许 [A-Za-z0-9_-]，长度 1~64。
# 与 canvas 的 TENANT_RE 保持一致。
_TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_current_tenant: ContextVar[str | None] = ContextVar("arcreel_tenant", default=None)


def is_valid_tenant(value: object) -> bool:
    return isinstance(value, str) and bool(_TENANT_RE.match(value))


def current_tenant() -> str | None:
    """当前请求的租户；未接入 matrix 或不在请求上下文时为 None。"""
    return _current_tenant.get()


def set_current_tenant(tenant: str | None) -> None:
    """设置当前上下文的租户。非法值一律降级为 None（fail closed）。

    降级而不是抛错：中间件在解析阶段调用它，抛错会把"身份异常"变成 500，
    而正确的表现是"当作未登录"，由门禁去引导重新握手。
    """
    if tenant is not None and not is_valid_tenant(tenant):
        logger.warning("非法租户标识，按未登录处理: %r", tenant)
        tenant = None
    _current_tenant.set(tenant)


@contextlib.contextmanager
def tenant_scope(tenant: str | None) -> Iterator[None]:
    """在一段代码内临时切换租户，退出时还原。

    给后台任务用：worker 从任务记录里取出租户，包住整段执行，
    这样 app_data_dir / DB / ProjectManager 才会指向该租户的数据。
    """
    if tenant is not None and not is_valid_tenant(tenant):
        raise ValueError(f"非法租户标识: {tenant!r}")
    token = _current_tenant.set(tenant)
    try:
        yield
    finally:
        _current_tenant.reset(token)
