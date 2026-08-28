"""本地账本对平台账务的对账口径。

这套逻辑存在的理由：本地 ``cost_amount`` 是按静态价目表算的估算，与平台实扣对不上
——自定义供应商的图片/视频一律记 0，智能体那条按 Anthropic 单价算网关上的第三方模型。
用户信的是账单，所以费用只能呈现平台数字。
"""

from __future__ import annotations

import pytest

from lib.gateway_usage import GatewayRecord
from server.services.usage_reconciliation import plan_window, reconcile

pytestmark = pytest.mark.unit


def _record(request_id: str, ts: int, credits: float, model: str = "z-ai/glm-5.2") -> GatewayRecord:
    return GatewayRecord(
        request_id=request_id,
        created_at=ts,
        model=model,
        kind="consume",
        credits=credits,
        quota=credits * 5000,
        prompt_tokens=0,
        completion_tokens=0,
    )


def _row(row_id: int, *, finished: str, provider: str, model: str, request_id: str | None = None) -> dict:
    return {
        "id": row_id,
        "provider": provider,
        "model": model,
        "finished_at": finished,
        "gateway_request_id": request_id,
        "cost_amount": 999.0,  # 本地估算，任何路径下都不该出现在结果里
    }


class TestExactMatch:
    def test_request_id_hit_takes_platform_number(self):
        rows = [_row(1, finished="2026-08-28T03:20:17", provider="custom-1", model="qwen/qwen-image", request_id="r1")]
        settled = reconcile(rows, [_record("r1", 1787887217, 10.0, model="qwen/qwen-image")])
        assert settled[1].credits == 10.0
        assert settled[1].source == "exact"

    def test_local_estimate_never_leaks_in(self):
        """对不上就是对不上。显示一个看着像真值的估算，比显示「未知」有害。"""
        rows = [_row(1, finished="2026-08-28T03:20:17", provider="custom-1", model="qwen/qwen-image", request_id="r1")]
        settled = reconcile(rows, [])
        assert settled[1].credits is None
        assert settled[1].source == "unknown"

    def test_zero_credits_is_not_unknown(self):
        """失败请求平台记 0——「确实没扣」和「不知道扣了多少」是两回事。"""
        rows = [_row(1, finished="2026-08-28T03:20:17", provider="custom-1", model="m", request_id="r1")]
        settled = reconcile(rows, [_record("r1", 1787887217, 0.0, model="m")])
        assert settled[1].credits == 0.0
        assert settled[1].source == "exact"


class TestAssistantAggregation:
    """智能体的请求由 Claude Code 子进程直接发出，响应头到不了我们手里，拿不到
    request id，只能按轮次时间窗归集。"""

    def test_turn_window_sums_platform_records(self):
        rows = [_row(1, finished="2026-08-28T03:01:12", provider="anthropic", model="z-ai/glm-5.2")]
        records = [_record("a", 1787885000, 2.0), _record("b", 1787885472, 40.0)]
        settled = reconcile(rows, records)
        assert settled[1].credits == 42.0
        assert settled[1].source == "aggregated"
        assert settled[1].matched_records == 2

    def test_turns_do_not_double_count(self):
        """两轮的窗口首尾相接，同一条平台记录只能被认领一次。"""
        rows = [
            _row(1, finished="2026-08-28T03:01:12", provider="anthropic", model="z-ai/glm-5.2"),
            _row(2, finished="2026-08-28T03:15:04", provider="anthropic", model="z-ai/glm-5.2"),
        ]
        records = [_record("a", 1787885472, 40.0), _record("b", 1787886300, 7.0)]
        settled = reconcile(rows, records)
        assert settled[1].credits == 40.0
        assert settled[2].credits == 7.0
        assert settled[1].credits + settled[2].credits == sum(r.credits for r in records)

    def test_other_models_are_not_swallowed(self):
        """窗口内的图片记录不能被智能体吞掉——那条自己带着 request id，是它的。"""
        rows = [
            _row(1, finished="2026-08-28T03:01:12", provider="anthropic", model="z-ai/glm-5.2"),
            _row(2, finished="2026-08-28T03:01:10", provider="custom-1", model="qwen/qwen-image", request_id="img"),
        ]
        records = [_record("img", 1787885470, 10.0, model="qwen/qwen-image"), _record("a", 1787885471, 3.0)]
        settled = reconcile(rows, records)
        assert settled[2].credits == 10.0 and settled[2].source == "exact"
        assert settled[1].credits == 3.0 and settled[1].matched_records == 1

    def test_exact_claims_run_before_aggregation(self):
        """顺序是载荷：先逐笔认领，剩下的才轮到窗口归集。反过来会把带 id 的行挤成 unknown。"""
        rows = [
            _row(1, finished="2026-08-28T03:01:12", provider="anthropic", model="m"),
            _row(2, finished="2026-08-28T03:01:11", provider="custom-1", model="m", request_id="r"),
        ]
        settled = reconcile(rows, [_record("r", 1787885471, 5.0, model="m")])
        assert settled[2].source == "exact"
        assert settled[1].source == "unknown", "同模型的那条已被逐笔认领，轮次里就没有别的了"

    def test_turn_without_any_record_stays_unknown(self):
        rows = [_row(1, finished="2026-08-28T03:01:12", provider="anthropic", model="z-ai/glm-5.2")]
        assert reconcile(rows, [])[1].source == "unknown"


class TestWindowPlanning:
    def test_window_covers_turn_lookback(self):
        """窗口要往前留够一轮的回溯量，否则轮次起点那几条记录根本没被拉进来。"""
        rows = [_row(1, finished="2026-08-28T03:01:12", provider="anthropic", model="m")]
        window = plan_window(rows)
        assert window is not None
        start, end = window
        assert end - start >= 1800

    def test_no_timestamps_yields_no_window(self):
        assert plan_window([{"id": 1, "provider": "anthropic"}]) is None
