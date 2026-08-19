"""握手后各媒体类型的默认模型必须配好。

不配的话四个 default_*_backend 全是空串，生成入口以"未配置模型"拒绝执行 ——
而托管态的用户并不知道要自己去设置页点一遍，表现就是"功能全都用不了"。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db.base import Base
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.matrix_session import seed_default_backends

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _make_provider(session: AsyncSession, models: list[tuple[str, str]]) -> int:
    repo = CustomProviderRepository(session)
    provider = await repo.create_provider(
        display_name="Matrix 网关",
        discovery_format="openai",
        base_url="https://gw.invalid",
        api_key="sk-test",
        models=[
            {
                "model_id": mid,
                "display_name": mid,
                "endpoint": endpoint,
                "is_default": False,
                "is_enabled": True,
            }
            for mid, endpoint in models
        ],
    )
    await session.commit()
    return provider.id


class TestDefaultBackendSeeding:
    async def test_fills_every_media_type(self, session):
        pid = await _make_provider(
            session,
            [
                ("gpt-x", "openai-chat"),
                ("img-x", "openai-images-generations"),
                ("vid-x", "openai-video"),
                ("tts-x", "openai-tts"),
            ],
        )
        applied = await seed_default_backends(session, provider_id=pid)
        assert set(applied) == {"text", "image", "video", "audio"}

        svc = ConfigService(session)
        assert await svc.get_setting("default_text_backend") == f"custom-{pid}/gpt-x"
        assert await svc.get_setting("default_video_backend") == f"custom-{pid}/vid-x"
        assert await svc.get_setting("default_audio_backend") == f"custom-{pid}/tts-x"

    async def test_does_not_overwrite_user_choice(self, session):
        """用户改过的默认值不能被下次握手冲掉。"""
        pid = await _make_provider(session, [("a-model", "openai-chat"), ("z-model", "openai-chat")])
        svc = ConfigService(session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/z-model")
        await session.commit()

        applied = await seed_default_backends(session, provider_id=pid)

        assert "text" not in applied
        assert await svc.get_setting("default_text_backend") == f"custom-{pid}/z-model"

    async def test_skips_media_types_without_models(self, session):
        """平台没上架的媒体类型不该被填一个不存在的值。"""
        pid = await _make_provider(session, [("gpt-x", "openai-chat")])
        applied = await seed_default_backends(session, provider_id=pid)

        assert set(applied) == {"text"}
        svc = ConfigService(session)
        assert await svc.get_setting("default_video_backend", "") == ""

    async def test_ignores_disabled_models(self, session):
        repo = CustomProviderRepository(session)
        pid = await _make_provider(session, [("on", "openai-chat"), ("off", "openai-chat")])
        models = await repo.list_models(pid)
        off = next(m for m in models if m.model_id == "off")
        await repo.replace_models(
            pid,
            [
                {
                    "model_id": m.model_id,
                    "display_name": m.display_name,
                    "endpoint": m.endpoint,
                    "is_default": False,
                    "is_enabled": m.model_id != "off",
                }
                for m in models
            ],
        )
        await session.commit()
        assert off is not None

        applied = await seed_default_backends(session, provider_id=pid)
        assert applied["text"] == f"custom-{pid}/on"
