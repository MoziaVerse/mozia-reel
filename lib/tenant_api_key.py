"""API Key 里的租户段：编码与解析。

托管态一租户一库（``app_data_dir`` / DB engine 都按 ``current_tenant()`` 分流），
而 ``api_keys`` 表就落在租户库里 —— 于是"验 key 得先选库、选库得先知道租户"成环。
破环的办法是让 key 自己带上租户：``arc-<tenant>-<32 hex>``。持 key 的请求因此能在
碰 DB 之前定出租户，``_verify_api_key`` 打开的就是正确那一份库。

单机态（未接入 matrix）不带租户段，仍是上游的 ``arc-<32 hex>``：那里 tenant 恒为
None，多一段只会让既有 key 全部失效。两种形态靠"末段是否恰好 32 位十六进制"区分，
解析一律从右侧切，因为租户标识本身允许含 ``-``（见 ``lib.tenant_context``），
从左侧切会把 ``ab-cd`` 这类租户截断成 ``ab``。
"""

from __future__ import annotations

import re
import secrets

from lib.tenant_context import is_valid_tenant

SECRET_HEX_LEN = 32
"""随机段长度（``secrets.token_hex(16)`` 的输出），解析时用它定位切点。"""

_SECRET_RE = re.compile(rf"^[0-9a-f]{{{SECRET_HEX_LEN}}}$")


def generate_secret() -> str:
    """生成 key 的随机段。"""
    return secrets.token_hex(SECRET_HEX_LEN // 2)


def build_api_key(prefix: str, tenant: str | None) -> str:
    """拼出完整 API Key；``tenant`` 为 None 时退回单机态格式。"""
    secret = generate_secret()
    if tenant is None:
        return f"{prefix}{secret}"
    return f"{prefix}{tenant}-{secret}"


def display_prefix(token: str, prefix: str) -> str:
    """列表里用来认 key 的短前缀。

    取随机段开头而不是整串开头：托管态的租户段对同一用户恒定，按整串切会让他所有
    key 显示成同一个前缀，列表就失去了区分作用。
    """
    return f"{prefix}{token[-SECRET_HEX_LEN:][:4]}"


def tenant_from_api_key(token: str, prefix: str) -> str | None:
    """从 key 解出租户；单机态 key、格式不符或租户非法时返回 None。

    只做格式解析，不校验 key 是否真实存在 —— 那要查库，而查哪个库正是本函数的输出
    决定的。调用方拿到租户后仍须走完整的 key 校验。
    """
    if not token.startswith(prefix):
        return None
    body = token[len(prefix) :]
    tenant, sep, secret = body.rpartition("-")
    if not sep or not _SECRET_RE.match(secret):
        return None
    return tenant if is_valid_tenant(tenant) else None
