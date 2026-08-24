"""握手后各媒体类型的默认模型必须配好。

不配的话四个 default_*_backend 全是空串，生成入口以"未配置模型"拒绝执行 ——
而托管态的用户并不知道要自己去设置页点一遍，表现就是"功能全都用不了"。
"""

from __future__ import annotations

import json

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


class TestPreferredDefaults:
    """默认模型要挑**开箱可用**的，而不是字典序第一个。

    实测网关 5 个图像模型里只有 mozia/image-2 在 ArcReel 默认的 720P 下可用；
    seedream 系列要求 ≥3686400 像素（4K 级），而字典序恰好把 seedream-4.5 排第一。
    照字典序选等于给每个新用户配一个开箱即挂的默认值。
    """

    @pytest.mark.asyncio
    async def test_prefers_known_good_image_model(self, session):
        pid = await _make_provider(
            session,
            [
                ("doubao/seedream-4.5", "openai-images-generations"),
                ("mozia/image-2", "openai-images-generations"),
            ],
        )
        applied = await seed_default_backends(session, provider_id=pid)
        assert applied["image"] == f"custom-{pid}/mozia/image-2"

    @pytest.mark.asyncio
    async def test_falls_back_when_preferred_absent(self, session):
        """首选下架时不能卡住，回落字典序第一个。"""
        pid = await _make_provider(session, [("zzz-model", "openai-images-generations")])
        applied = await seed_default_backends(session, provider_id=pid)
        assert applied["image"] == f"custom-{pid}/zzz-model"

    def test_preference_table_is_documented_as_drifting(self):
        """这张表依赖平台上架情况，必须留下"会漂、需实测"的提示。"""
        from pathlib import Path

        src = Path("lib/matrix_session.py").read_text(encoding="utf-8")
        idx = src.index("_PREFERRED_DEFAULT_MODELS")
        assert "实测" in src[max(0, idx - 1200) : idx]


class TestVideoDurations:
    """视频模型必须带 supported_durations，否则视频链路开箱不可用。

    剧本生成会硬校验它来定每段时长，空值直接 fail loud
    （"supported_durations is empty for ..."）。discover 不返回这个字段，
    seed 时必须用上游的启发式补上。
    """

    @pytest.mark.asyncio
    async def test_backfill_fills_only_video_models(self, session):
        from lib.matrix_session import backfill_video_durations

        pid = await _make_provider(
            session,
            [
                ("minimax/minimax-h3-ref2va", "openai-video"),
                ("gpt-x", "openai-chat"),
            ],
        )
        filled = await backfill_video_durations(session, provider_id=pid)
        assert filled == 1

        repo = CustomProviderRepository(session)
        by_id = {m.model_id: m for m in await repo.list_models(pid)}
        assert json.loads(by_id["minimax/minimax-h3-ref2va"].supported_durations)
        assert not by_id["gpt-x"].supported_durations

    @pytest.mark.asyncio
    async def test_backfill_preserves_user_edits(self, session):
        """用户在 UI 上改过的档位不能被回填覆盖。"""
        from lib.matrix_session import backfill_video_durations

        pid = await _make_provider(session, [("doubao/seedance-2.0", "openai-video")])
        repo = CustomProviderRepository(session)
        model = (await repo.list_models(pid))[0]
        model.supported_durations = json.dumps([7])
        await session.commit()

        assert await backfill_video_durations(session, provider_id=pid) == 0
        assert json.loads((await repo.list_models(pid))[0].supported_durations) == [7]

    def test_h3_durations_match_contract(self):
        """H3 的契约是 4–15 秒。"""
        from lib.custom_provider.duration_presets import infer_supported_durations

        d = infer_supported_durations("minimax/minimax-h3-ref2va")
        assert min(d) == 4 and max(d) == 15


class TestTextModelPreference:
    """默认文本模型不能按字典序挑，也不能落在只收 paid 的模型上。

    字典序第一个恰好是 GLM-4.7，而它在 Agent 的多层子任务嵌套下会**静默死锁**
    ——不报错、不超时，就是没有输出。按字典序挑等于每个新用户开箱就踩，症状
    还是最难查的那种。

    另一条约束是钱包分区：新用户手里通常只有赠送额度，而网关只给少数模型开了
    gift 授权，默认值落在 paid-only 的模型上等于开箱就欠费。
    """

    def test_prefers_gift_capable_over_alphabetical_first(self):
        from lib.matrix_session import preferred_model

        available = {"GLM-4.7", "qwen/qwen3.8-27b", "z-ai/glm-5.2"}
        assert preferred_model("text", available) == "qwen/qwen3.8-27b"

    def test_never_picks_the_deadlocking_model(self):
        """GLM-4.7 虽然也允许 gift，但它会静默死锁，不能进偏好表。"""
        from lib.matrix_session import preferred_model

        assert preferred_model("text", {"GLM-4.7", "z-ai/glm-5.1"}) == "z-ai/glm-5.1"

    def test_falls_back_to_paid_only_when_no_gift_model_listed(self):
        """gift 档都没上架时回落到 paid-only 的兜底项，而不是返回 None。"""
        from lib.matrix_session import preferred_model

        available = {"GLM-4.7", "z-ai/glm-5.2", "deepseek/deepseek-v4-pro"}
        assert preferred_model("text", available) == "z-ai/glm-5.2"

    def test_returns_none_when_no_preference_available(self):
        """偏好项一个都没上架时交给调用方回落，而不是硬塞一个不存在的模型。"""
        from lib.matrix_session import preferred_model

        assert preferred_model("text", {"GLM-4.7", "some/other"}) is None

    def test_unknown_media_has_no_preference(self):
        from lib.matrix_session import preferred_model

        assert preferred_model("nosuch", {"a", "b"}) is None
