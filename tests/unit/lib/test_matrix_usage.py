"""平台账务流水的归一。

这一层的价值全在"把混在一条流水里的不同性质拆开"：上游把消费、失败、退款
混着发，而失败记录 quota=0——不区分的话，一串失败在页面上就是一串「0 消耗」，
看起来像什么都没发生过。
"""

from __future__ import annotations

from lib.matrix_session import normalize_logs


def _payload(*items, quota_per_unit=500000):
    return {"items": list(items), "total": len(items), "page": 1, "pageSize": 20, "quotaPerUnit": quota_per_unit}


def test_kind_mapping():
    """取值来自 mozia-api model/log.go：2=消费 5=失败 6=退款。"""
    out = normalize_logs(_payload({"type": 2}, {"type": 5}, {"type": 6}, {"type": 4}))
    assert [i["kind"] for i in out["items"]] == ["consume", "error", "refund", "other"]


def test_quota_converts_to_credits():
    """1 积分 = ¥0.01，即 quotaPerUnit/100 quota。与钱包页同源。"""
    out = normalize_logs(_payload({"type": 2, "quota": 5000}))
    assert out["items"][0]["credits"] == 1.0
    assert out["items"][0]["quota"] == 5000


def test_credits_follow_a_different_quota_per_unit():
    """汇率是上游下发的，不能写死——写死过一次就会在改价那天悄悄算错。"""
    out = normalize_logs(_payload({"type": 2, "quota": 1000}, quota_per_unit=100000))
    assert out["items"][0]["credits"] == 1.0


def test_failed_record_carries_zero_credits():
    out = normalize_logs(_payload({"type": 5, "quota": 0, "modelName": "m"}))
    item = out["items"][0]
    assert item["kind"] == "error" and item["credits"] == 0


def test_broken_record_degrades_instead_of_failing_the_page():
    """单条记录字段缺失不该让整页失败。"""
    out = normalize_logs(_payload({}, "not-a-dict", {"type": 2, "quota": 5000}))
    assert len(out["items"]) == 2  # 非 dict 被跳过
    assert out["items"][0]["model_name"] == "unknown"
    assert out["items"][0]["credits"] == 0


def test_missing_quota_per_unit_falls_back():
    """上游漏发汇率时按默认值算，而不是除零。"""
    out = normalize_logs({"items": [{"type": 2, "quota": 5000}]})
    assert out["items"][0]["credits"] == 1.0


def test_zero_quota_per_unit_does_not_divide_by_zero():
    out = normalize_logs(_payload({"type": 2, "quota": 5000}, quota_per_unit=0))
    assert out["items"][0]["credits"] == 1.0


def test_timestamps_stay_epoch_seconds():
    """不在服务端做时区假设，原值交前端格式化。"""
    out = normalize_logs(_payload({"type": 2, "createdAt": 1787276286}))
    assert out["items"][0]["created_at"] == 1787276286
