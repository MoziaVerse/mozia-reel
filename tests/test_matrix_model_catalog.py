"""平台模型目录 → 本地模型表。

这一层解决的是一个具体故障：此前目录靠"网关 /v1/models + 按模型名猜类目"得来，
而上游的 supported_endpoint_types 普遍只回 ["openai"]，猜的结果必然把 TTS、
embedding、OCR 混进对话模型里——它们会出现在模型下拉里，选中必失败。
"""

from __future__ import annotations

import json

import pytest

from lib.matrix_session import catalog_to_models

pytestmark = pytest.mark.unit


def _m(name, model_type, **extra):
    return {"model_name": name, "model_type": model_type, **extra}


class TestCatalogToModels:
    def test_maps_each_type_to_its_endpoint(self):
        rows = catalog_to_models(
            [
                _m("z-ai/glm-5.2", "chat"),
                _m("mozia/image-2", "image"),
                _m("minimax/minimax-h3-ref2va", "video"),
                _m("voxcpm2", "audio"),
            ]
        )
        assert {r["model_id"]: r["endpoint"] for r in rows} == {
            "z-ai/glm-5.2": "openai-chat",
            "mozia/image-2": "openai-images",
            "minimax/minimax-h3-ref2va": "openai-video",
            "voxcpm2": "openai-tts",
        }

    def test_multimodal_counts_as_chat(self):
        """那类模型本来就能纯文本对话，只是顺带能看图。"""
        rows = catalog_to_models([_m("qwen/qwen3.8-27b", "multimodal")])
        assert rows[0]["endpoint"] == "openai-chat"

    @pytest.mark.parametrize("bad_type", ["vision", "embedding", "rerank"])
    def test_drops_types_with_no_local_lane(self, bad_type):
        """不是"平台没分类"，是分类明确但本地没有对应用法。收进来只会让人选中即失败。"""
        assert catalog_to_models([_m("x/y", bad_type)]) == []

    def test_drops_unknown_type_instead_of_guessing(self):
        """平台日后新增类目时，宁可不收也不要猜一个 endpoint 出来。"""
        assert catalog_to_models([_m("x/y", "something-new")]) == []

    def test_video_gets_durations_and_others_do_not(self):
        """视频模型缺 supported_durations 会让剧本生成 fail loud。"""
        rows = catalog_to_models([_m("minimax/minimax-h3-ref2va", "video"), _m("z/chat", "chat")])
        video, chat = rows[0], rows[1]
        assert json.loads(video["supported_durations"])
        assert chat["supported_durations"] is None

    def test_unaffordable_model_stays_selectable(self):
        """平台的 enabled 是 access.available（此刻付不付得起），不是上下架。

        把它写进 is_enabled 会让「余额不够」固化成「模型被禁用」：禁用项在下拉里
        不渲染，用户充值后也不会自己回来，表现为模型莫名少了一半。付不起要在生成
        时按网关的 requires_paid_quota 失败，那里才有明确原因。
        """
        rows = catalog_to_models([_m("x/y", "chat", enabled=False)])
        assert rows[0]["is_enabled"] is True

    @pytest.mark.parametrize("enabled", [True, False, None])
    def test_is_enabled_never_tracks_platform_access(self, enabled):
        """收录即可选，与平台给的 access 值无关——上下架另由「不在目录里」判定。"""
        item = _m("x/y", "chat")
        if enabled is None:
            item.pop("enabled", None)
        else:
            item["enabled"] = enabled
        assert catalog_to_models([item])[0]["is_enabled"] is True

    def test_display_name_falls_back_to_model_id(self):
        rows = catalog_to_models([_m("x/y", "chat"), _m("a/b", "chat", display_name="  ")])
        assert [r["display_name"] for r in rows] == ["x/y", "a/b"]

    def test_junk_entries_do_not_break_the_batch(self):
        """一条坏记录不该让整份目录作废。"""
        rows = catalog_to_models(["nope", {}, {"model_name": "  ", "model_type": "chat"}, _m("ok/x", "chat")])
        assert [r["model_id"] for r in rows] == ["ok/x"]

    def test_is_default_never_comes_from_the_platform(self):
        """哪个当默认是用户的选择，平台目录不该替他定。"""
        rows = catalog_to_models([_m("x/y", "chat", is_default=True)])
        assert rows[0]["is_default"] is False


# ---------------------------------------------------------------------------
# 差异合并
# ---------------------------------------------------------------------------

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from lib.db.base import Base  # noqa: E402
from lib.db.repositories.custom_provider_repo import CustomProviderRepository  # noqa: E402
from lib.matrix_session import sync_gateway_models  # noqa: E402


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _provider(session, models):
    repo = CustomProviderRepository(session)
    p = await repo.create_provider(
        display_name="Matrix 网关",
        discovery_format="openai",
        base_url="https://gw.example.com",
        api_key="k",
        models=models or None,
    )
    await session.flush()
    return p, repo


def _row(model_id, endpoint="openai-chat", **extra):
    return {
        "model_id": model_id,
        "display_name": model_id,
        "endpoint": endpoint,
        "supported_durations": None,
        "is_default": False,
        "is_enabled": True,
        **extra,
    }


class TestSyncGatewayModels:
    async def test_adds_newly_listed_models(self, session):
        p, repo = await _provider(session, [_row("old/a")])
        await sync_gateway_models(session, provider_id=p.id, rows=[_row("old/a"), _row("new/b")])
        assert {m.model_id for m in await repo.list_models(p.id)} == {"old/a", "new/b"}

    async def test_delisted_model_is_disabled_not_deleted(self, session):
        """项目里可能还引用着它，删掉会让那些项目的模型字段指向空气。"""
        p, repo = await _provider(session, [_row("gone/x"), _row("stay/y")])
        await sync_gateway_models(session, provider_id=p.id, rows=[_row("stay/y")])
        by_id = {m.model_id: m for m in await repo.list_models(p.id)}
        assert set(by_id) == {"gone/x", "stay/y"}
        assert by_id["gone/x"].is_enabled is False
        assert by_id["stay/y"].is_enabled is True

    async def test_endpoint_follows_the_platform(self, session):
        """endpoint 决定请求打到哪条路径，错了就是必然失败——不是用户偏好能覆盖的。"""
        p, repo = await _provider(session, [_row("voxcpm2", endpoint="openai-chat")])
        await sync_gateway_models(session, provider_id=p.id, rows=[_row("voxcpm2", endpoint="openai-tts")])
        assert (await repo.list_models(p.id))[0].endpoint == "openai-tts"

    async def test_user_default_choice_survives_sync(self, session):
        """哪个当默认是用户设的，对账不该把它抹掉。"""
        p, repo = await _provider(session, [_row("z/chat", is_default=True)])
        await sync_gateway_models(session, provider_id=p.id, rows=[_row("z/chat")])
        assert (await repo.list_models(p.id))[0].is_default is True

    async def test_relisted_model_is_re_enabled(self, session):
        p, repo = await _provider(session, [_row("back/x", is_enabled=False)])
        await sync_gateway_models(session, provider_id=p.id, rows=[_row("back/x")])
        assert (await repo.list_models(p.id))[0].is_enabled is True

    async def test_durations_follow_the_endpoint_change(self, session):
        """从视频改判成非视频时，时长预设要一并清掉，否则留着一份对不上的数据。"""
        p, repo = await _provider(session, [_row("x/y", endpoint="openai-video", supported_durations="[4, 8]")])
        await sync_gateway_models(session, provider_id=p.id, rows=[_row("x/y", endpoint="openai-chat")])
        m = (await repo.list_models(p.id))[0]
        assert m.endpoint == "openai-chat" and m.supported_durations is None

    async def test_empty_catalog_is_a_no_op(self, session):
        """目录拉取失败时给的是空列表，不能被当成"平台把模型全下架了"。"""
        p, repo = await _provider(session, [_row("keep/x")])
        await sync_gateway_models(session, provider_id=p.id, rows=[])
        assert (await repo.list_models(p.id))[0].is_enabled is True
