"""API Key 租户段的编码与解析。

这层是托管态远程 MCP 定位租户库的唯一依据：解错租户不会报错，只会让请求安静地
落到别人的库或共享数据根上，所以用例锁的是解析边界，尤其"租户标识本身含 ``-``"
——``lib.tenant_context`` 允许它，而从左侧切会把这类租户截断成前半段。
"""

from __future__ import annotations

import pytest

from lib.tenant_api_key import (
    SECRET_HEX_LEN,
    build_api_key,
    display_prefix,
    tenant_from_api_key,
)

PREFIX = "arc-"


class TestRoundTrip:
    @pytest.mark.parametrize("tenant", ["user1", "a-b-c", "with_underscore", "A" * 64])
    def test_tenant_survives_round_trip(self, tenant):
        """含 ``-`` 与下划线的租户都要原样还原，长度上限同样成立。"""
        key = build_api_key(PREFIX, tenant)
        assert tenant_from_api_key(key, PREFIX) == tenant

    def test_standalone_key_carries_no_tenant(self):
        """单机态不带租户段，形态与上游一致，解析结果是 None 而非空串。"""
        key = build_api_key(PREFIX, None)
        assert key.startswith(PREFIX)
        assert len(key) == len(PREFIX) + SECRET_HEX_LEN
        assert tenant_from_api_key(key, PREFIX) is None

    def test_secrets_differ_between_keys(self):
        assert build_api_key(PREFIX, "u") != build_api_key(PREFIX, "u")


class TestRejects:
    @pytest.mark.parametrize(
        "token",
        [
            "",
            "nope-abc",
            "sk-1234",
            "arc-",
            "arc-tenant-tooshort",
            "arc-tenant-" + "z" * SECRET_HEX_LEN,  # 非十六进制
            "arc-" + "0" * SECRET_HEX_LEN,  # 单机态 key：有效但无租户
        ],
    )
    def test_malformed_or_tenantless_returns_none(self, token):
        assert tenant_from_api_key(token, PREFIX) is None

    def test_illegal_tenant_rejected(self):
        """租户会直接当目录名用，路径穿越必须在这一层就被挡掉。"""
        forged = f"{PREFIX}../../etc-{'0' * SECRET_HEX_LEN}"
        assert tenant_from_api_key(forged, PREFIX) is None


class TestDisplayPrefix:
    def test_takes_random_segment_not_tenant(self):
        """同一租户的两把 key 必须显示成不同前缀，否则列表认不出哪把是哪把。"""
        a = build_api_key(PREFIX, "same-tenant")
        b = build_api_key(PREFIX, "same-tenant")
        assert display_prefix(a, PREFIX) != display_prefix(b, PREFIX)

    def test_same_shape_for_both_key_formats(self):
        """两种 key 形态都取随机段开头，租户段长短不影响显示宽度。"""
        for key in (build_api_key(PREFIX, None), build_api_key(PREFIX, "a-long-tenant")):
            secret = key[-SECRET_HEX_LEN:]
            assert display_prefix(key, PREFIX) == PREFIX + secret[:4]
