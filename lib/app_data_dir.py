"""Application data root resolution.

Centralizes where ArcReel stores per-deployment data (projects, SQLite DB,
generated assets, system config). Decoupling this from the repository layout
lets the same backend code run under varied deployment shapes that don't keep
data alongside the source tree.

Resolution order:
    1. ``ARCREEL_DATA_DIR`` — explicit override
    2. ``AI_ANIME_PROJECTS`` — legacy alias kept for backward compatibility
    3. ``PROJECT_ROOT / "projects"`` — default

Relative paths resolve against :data:`lib.env_init.PROJECT_ROOT`. The directory
is created on first call.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from lib.env_init import PROJECT_ROOT

_ENV_KEYS: tuple[str, ...] = ("ARCREEL_DATA_DIR", "AI_ANIME_PROJECTS")


@functools.cache
def base_data_dir() -> Path:
    """Return the deployment-wide data root (cached), ignoring tenancy."""
    for env_key in _ENV_KEYS:
        raw = os.environ.get(env_key, "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()
    default = (PROJECT_ROOT / "projects").resolve()
    default.mkdir(parents=True, exist_ok=True)
    return default


def app_data_dir() -> Path:
    """Return the data root for the **current tenant**.

    未接入 matrix（单机自用、本地开发）时 tenant 为 None，行为与改造前一致：
    直接返回部署级根目录。接入后每个 matrix 用户拿到 ``<root>/tenants/<ssoSub>``，
    项目目录、SQLite DB、生成产物随之全部隔离 —— 与 ZeoCanvasLite 的
    ``DATA_ROOT/<tenant>/…`` 同构。

    租户目录挂在 ``tenants/`` 子目录下而不是根的直接子目录：根目录下已经躺着
    单租户时期的项目目录，混在一起会让 ``projects_root.iterdir()`` 把租户目录
    当成项目枚举出来。
    """
    from lib.tenant_context import current_tenant

    base = base_data_dir()
    tenant = current_tenant()
    if tenant is None:
        return base
    path = base / "tenants" / tenant
    # 每次都 mkdir(exist_ok=True) 而不是用 set 记住"建过了"：目录一旦被外部
    # 删除（卷挂载异常、运维清理），带缓存的版本不会重建，之后所有请求都撞
    # "unable to open database file" —— 那个报错指不到根因。mkdir 是一次幂等
    # syscall，换来的是自愈。
    path.mkdir(parents=True, exist_ok=True)
    return path


def _reset_for_tests() -> None:
    """Clear the cached value so tests can monkeypatch env between cases."""
    base_data_dir.cache_clear()
