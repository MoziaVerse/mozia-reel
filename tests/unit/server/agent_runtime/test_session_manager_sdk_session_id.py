"""Unit tests for SessionManager._on_sdk_session_id_received during streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from lib.db.models.api_call import ApiCall
from server.agent_runtime.session_actor import SessionActor
from server.agent_runtime.session_manager import ManagedSession
from tests.fakes import FakeSDKClient


class StreamEvent:
    def __init__(self, session_id: str, uuid: str = "stream-1"):
        self.uuid = uuid
        self.session_id = session_id
        self.event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}
        self.parent_tool_use_id = None


class ResultMessage:
    def __init__(self, session_id: str, subtype: str = "success"):
        self.subtype = subtype
        self.duration_ms = 1
        self.duration_api_ms = 1
        self.is_error = subtype == "error"
        self.num_turns = 1
        self.session_id = session_id
        self.total_cost_usd = None
        self.usage = None
        self.result = None
        self.structured_output = None


def _make_managed(**overrides) -> ManagedSession:
    """Construct a ManagedSession with a dummy actor that is never started."""
    dummy_client = FakeSDKClient()

    @asynccontextmanager
    async def _factory():
        async with dummy_client as c:
            yield c

    actor = SessionActor(client_factory=_factory, on_message=lambda msg: None)
    kwargs = {
        "session_id": "temp-id",
        "actor": actor,
        "status": "running",
        "project_name": "demo",
    }
    kwargs.update(overrides)
    return ManagedSession(**kwargs)


class TestSessionManagerSdkSessionId:
    async def test_on_sdk_session_id_received_creates_db_record(self, session_manager, meta_store):
        """For new sessions, _on_sdk_session_id_received creates DB record and signals event."""
        sdk_session_id = "sdk-new-123"
        managed = _make_managed()

        await session_manager._on_sdk_session_id_received(
            managed, StreamEvent(sdk_session_id), {"session_id": sdk_session_id}
        )

        assert managed.resolved_sdk_id == sdk_session_id
        assert managed.sdk_id_event.is_set()
        # DB record should exist
        meta = await meta_store.get(sdk_session_id)
        assert meta is not None
        assert meta.project_name == "demo"
        assert meta.status == "running"

    async def test_finalize_turn_records_assistant_usage(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-usage-789")
        managed = _make_managed(session_id=meta.id, project_name="demo", assistant_model="claude-sonnet-4")
        managed.last_user_prompt = "hello assistant"
        session_manager._user_id = "assistant-user"  # type: ignore[attr-defined]

        await session_manager._finalize_turn(
            managed,
            {
                "type": "result",
                "session_status": "completed",
                "model": "claude-sonnet-4",
                "total_cost_usd": 0.1234,
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_creation_input_tokens": 50,
                },
            },
        )

        async with meta_store._session_factory() as session:  # noqa: SLF001 - test fixture exposes shared DB factory
            row = (
                await session.execute(
                    select(ApiCall).where(
                        ApiCall.project_name == "demo",
                        ApiCall.model == "claude-sonnet-4",
                        ApiCall.prompt == "hello assistant",
                    )
                )
            ).scalar_one()

        assert row.project_name == "demo"
        assert row.user_id == "assistant-user"
        assert row.provider == "anthropic"
        assert row.call_type == "text"
        assert row.model == "claude-sonnet-4"
        assert row.prompt == "hello assistant"
        assert row.input_tokens == 1050
        assert row.output_tokens == 200
        assert row.usage_tokens == 1250
        assert row.cost_amount == pytest.approx(0.1234)
        assert row.currency == "USD"
        refreshed = await meta_store.get(meta.id)
        assert refreshed is not None
        assert refreshed.status == "completed"

    async def test_finalize_turn_preserves_sdk_cost_for_failed_status(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-usage-failed-789")
        managed = _make_managed(session_id=meta.id, project_name="demo", assistant_model="claude-sonnet-4")
        managed.last_user_prompt = "failed but billed"

        await session_manager._finalize_turn(
            managed,
            {
                "type": "result",
                "session_status": "error",
                "model": "claude-sonnet-4",
                "total_cost_usd": 0.0456,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )

        async with meta_store._session_factory() as session:  # noqa: SLF001 - test fixture exposes shared DB factory
            row = (
                await session.execute(
                    select(ApiCall).where(
                        ApiCall.project_name == "demo",
                        ApiCall.model == "claude-sonnet-4",
                        ApiCall.prompt == "failed but billed",
                    )
                )
            ).scalar_one()

        assert row.status == "failed"
        assert row.cost_amount == pytest.approx(0.0456)
        assert row.currency == "USD"
        refreshed = await meta_store.get(meta.id)
        assert refreshed is not None
        assert refreshed.status == "error"

    async def test_finalize_turn_uses_model_usage_cost_when_total_cost_missing(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-model-usage-cost-789")
        managed = _make_managed(session_id=meta.id, project_name="demo", assistant_model="claude-sonnet-4")
        managed.last_user_prompt = "model usage cost"

        await session_manager._finalize_turn(
            managed,
            {
                "type": "result",
                "session_status": "completed",
                "model": "claude-sonnet-4",
                "usage": {"input_tokens": 100, "output_tokens": 20},
                "model_usage": {
                    "claude-sonnet-4": {"costUSD": "0.0123"},
                    "claude-haiku-4-5": {"costUSD": 0.0045},
                },
            },
        )

        async with meta_store._session_factory() as session:  # noqa: SLF001 - test fixture exposes shared DB factory
            row = (
                await session.execute(
                    select(ApiCall).where(
                        ApiCall.project_name == "demo",
                        ApiCall.model == "claude-sonnet-4",
                        ApiCall.prompt == "model usage cost",
                    )
                )
            ).scalar_one()

        assert row.cost_amount == pytest.approx(0.0168)
        assert row.currency == "USD"

    async def test_finalize_turn_uses_model_usage_tokens_when_usage_missing(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-model-usage-tokens-789")
        managed = _make_managed(session_id=meta.id, project_name="demo")
        managed.last_user_prompt = "model usage tokens"

        await session_manager._finalize_turn(
            managed,
            {
                "type": "result",
                "session_status": "completed",
                "model_usage": {
                    "claude-sonnet-4": {
                        "inputTokens": 100,
                        "outputTokens": 20,
                        "cacheCreationInputTokens": 30,
                        "cacheReadInputTokens": 40,
                        "costUSD": 0.0123,
                    }
                },
            },
        )

        async with meta_store._session_factory() as session:  # noqa: SLF001 - test fixture exposes shared DB factory
            row = (
                await session.execute(
                    select(ApiCall).where(
                        ApiCall.project_name == "demo",
                        ApiCall.model == "claude-sonnet-4",
                        ApiCall.prompt == "model usage tokens",
                    )
                )
            ).scalar_one()

        assert row.input_tokens == 170
        assert row.output_tokens == 20
        assert row.usage_tokens == 190
        assert row.cost_amount == pytest.approx(0.0123)

    async def test_finalize_turn_usage_failure_does_not_override_status(self, session_manager, meta_store, monkeypatch):
        meta = await meta_store.create("demo", "sdk-usage-error-789")
        managed = _make_managed(session_id=meta.id, project_name="demo")
        called = False

        async def _raise_usage_error(*_args, **_kwargs):
            nonlocal called
            called = True
            raise RuntimeError("usage db unavailable")

        monkeypatch.setattr(session_manager, "_record_assistant_usage", _raise_usage_error)

        await session_manager._finalize_turn(
            managed,
            {"type": "result", "session_status": "completed", "model": "claude-sonnet-4", "usage": {"input_tokens": 1}},
        )

        assert called is True
        refreshed = await meta_store.get(meta.id)
        assert refreshed is not None
        assert refreshed.status == "completed"

    async def test_on_sdk_session_id_received_noop_when_already_registered(self, session_manager, meta_store):
        """For sessions with resolved_sdk_id already set, it's a no-op."""
        managed = _make_managed(session_id="sdk-existing", resolved_sdk_id="sdk-existing")
        managed.sdk_id_event.set()

        await session_manager._on_sdk_session_id_received(
            managed, StreamEvent("sdk-existing"), {"session_id": "sdk-existing"}
        )
        # Should not create duplicate DB record
        meta = await meta_store.get("sdk-existing")
        assert meta is None  # No DB record was created

    async def test_process_inbox_triggers_on_sdk_session_id_received(self, session_manager, meta_store):
        """_process_inbox drains messages and calls _on_sdk_session_id_received + _finalize_turn."""
        sdk_session_id = "sdk-consume-456"
        managed = _make_managed(session_id=sdk_session_id)
        session_manager.sessions[sdk_session_id] = managed

        # Push stream event dict + result dict onto the inbox (mimicking on_actor_message).
        managed._inbox.put_nowait({"type": "stream_event", "session_id": sdk_session_id, "uuid": "u1"})
        managed._inbox.put_nowait(
            {
                "type": "result",
                "subtype": "success",
                "session_id": sdk_session_id,
                "duration_ms": 1,
                "duration_api_ms": 1,
                "is_error": False,
                "num_turns": 1,
            }
        )
        managed._inbox.put_nowait(None)  # sentinel to end processing

        await session_manager._process_inbox(managed)

        assert managed.resolved_sdk_id == sdk_session_id
        assert managed.sdk_id_event.is_set()
        # DB record should have been created by _on_sdk_session_id_received
        meta = await meta_store.get(sdk_session_id)
        assert meta is not None
        assert meta.project_name == "demo"


class TestExtractFromInitMessage:
    """``system/init`` 是会话最早、也最确定携带 id 的一条，必须认得出来。

    SDK 把没有专属类型的 system 消息统一收敛成 ``SystemMessage(subtype, data)``，
    id 只在 ``data`` 里；只认顶层的话新会话得等第一条 assistant 消息才认领得到 id。
    模型正常时那几秒掩盖了缺口，上游持续报错时（网关把余额不足回成 500、CLI 按可
    重试错误退避十次）整个等待窗口内没有任何一条带顶层 id 的消息，已经连上的会话
    被判成「SDK 会话创建超时」。
    """

    def _extract(self, message, msg_dict):
        from server.agent_runtime.session_manager import SessionManager

        return SessionManager._extract_sdk_session_id(message, msg_dict)

    def test_reads_id_nested_under_data(self):
        from claude_agent_sdk.types import SystemMessage

        from server.agent_runtime.message_serialization import message_to_dict

        msg = SystemMessage(subtype="init", data={"subtype": "init", "session_id": "sdk-init-1"})
        msg_dict = message_to_dict(msg)
        assert msg_dict.get("session_id") is None, "前提：init 的 id 不在顶层"
        assert self._extract(msg, msg_dict) == "sdk-init-1"

    def test_api_retry_also_yields_the_id(self):
        """上游连续 5xx 时，重试通告是这段窗口里唯一还在流动的消息。"""
        from claude_agent_sdk.types import SystemMessage

        from server.agent_runtime.message_serialization import message_to_dict

        msg = SystemMessage(
            subtype="api_retry",
            data={"subtype": "api_retry", "attempt": 3, "error_status": 500, "session_id": "sdk-retry-1"},
        )
        assert self._extract(msg, message_to_dict(msg)) == "sdk-retry-1"

    def test_top_level_still_wins(self):
        """带顶层 id 的消息（assistant / result / hook）行为不变。"""
        assert self._extract(None, {"session_id": "top", "data": {"session_id": "nested"}}) == "top"

    def test_absent_everywhere_stays_none(self):
        assert self._extract(None, {"subtype": "init", "data": {"cwd": "/x"}}) is None


class TestApiRetryIsLogged:
    """CLI 对 5xx 退避重试十次要几分钟，期间完全静默——不记下来就分不清
    「等等就好」和「充值才好」（网关把余额不足也回成 500）。"""

    def test_retry_notice_is_logged(self, caplog):
        import logging

        from server.agent_runtime.session_manager import SessionManager

        managed = _make_managed()
        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
            SessionManager._log_api_retry(
                managed,
                {
                    "type": "system",
                    "subtype": "api_retry",
                    "data": {"subtype": "api_retry", "attempt": 4, "max_retries": 10, "error_status": 500},
                },
            )
        record = next(r for r in caplog.records if r.message == "assistant upstream api retry")
        assert record.error_status == 500
        assert record.attempt == 4

    def test_ordinary_messages_are_not_logged(self, caplog):
        import logging

        from server.agent_runtime.session_manager import SessionManager

        managed = _make_managed()
        with caplog.at_level(logging.WARNING, logger="server.agent_runtime.session_manager"):
            SessionManager._log_api_retry(managed, {"type": "system", "subtype": "init", "data": {}})
        assert not [r for r in caplog.records if r.message == "assistant upstream api retry"]
