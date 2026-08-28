"""API call usage tracking ORM model."""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class ApiCall(TimestampMixin, UserOwnedMixin, Base):
    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    call_type: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(String)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    aspect_ratio: Mapped[str | None] = mapped_column(String)
    generate_audio: Mapped[bool | None] = mapped_column(Boolean, server_default=sa.true())
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)
    output_path: Mapped[str | None] = mapped_column(Text)
    segment_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_amount: Mapped[float] = mapped_column(Float, server_default="0.0")
    currency: Mapped[str] = mapped_column(String, server_default="USD")
    provider: Mapped[str] = mapped_column(String, server_default="gemini")
    usage_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 网关侧那次调用的 id（响应头 x-oneapi-request-id）。存它是为了让费用跟平台账务对得上：
    # 本地记的是"我们以为花了多少"，平台记的是实际扣了多少，两者差得很远——实测本地把
    # glm 按 Anthropic 单价估价，高估近 8 倍。有了它就能按 id 关联到真实扣费。
    #
    # 可空有两种情形：非网关供应商（本就没有这个概念）；以及智能体那条链路——它的请求由
    # Claude Agent SDK 子进程发出，响应头不经过本进程，那部分改由 request_path + 会话
    # 时间窗归集。
    gateway_request_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    __table_args__ = (
        Index("idx_api_calls_project_name", "project_name"),
        Index("idx_api_calls_call_type", "call_type"),
        Index("idx_api_calls_status", "status"),
        Index("idx_api_calls_started_at", "started_at"),
    )
