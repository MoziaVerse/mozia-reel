"""Matrix 接入的零依赖基础件（常量与纯 env 读取）。

单独成模块而不是留在 ``lib.matrix_session`` 里：``lib.reference_image_hosting`` 只为取
``GATEWAY_PROVIDER_DISPLAY_NAME`` 一个字符串就得 import 整个 ``matrix_session``，而后者
向上依赖 ``lib.custom_provider``。这条链把 ``lib.video_backends.openai``（H3 走参考图外链
托管）也一并拖过去，撞上 ``lib.config < lib.*_backends < lib.custom_provider`` 分层契约。

同理 ``lib.signed_media_url`` 只为 ``session_signing_secret`` 一个函数就把 matrix_session
拖进了签名直链这条链路，而 H3 的参考图外链托管正走它。

本模块只依赖标准库，任何层都可以安全引用它。
"""

from __future__ import annotations

import os

#: 握手时在 custom_provider 表里认领/创建的网关行的 display_name。
#: 它同时是查回该行的键——改名会让已有部署认不出自己那行，于是又建一行、
#: 旧行的凭据成为孤儿。
GATEWAY_PROVIDER_DISPLAY_NAME = "Matrix 网关"


def session_signing_secret() -> bytes:
    """握手 cookie 的签名密钥，同时是签名直链派生子键的来源（见 lib/signed_media_url）。

    刻意不自动生成兜底值：单实例重启后 secret 变了会让所有人被登出，而这种
    "偶发全员掉线" 排查起来指不到根因。缺配置就明确拒绝启动握手功能。
    """
    secret = os.environ.get("SESSION_COOKIE_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError("SESSION_COOKIE_SECRET 必须配置且不短于 32 字符（生成：openssl rand -base64 32）")
    return secret.encode("utf-8")
