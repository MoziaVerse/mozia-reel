"""lib.reference_compression 单元测试。

仿 tests/test_image_utils.py 用 PIL 生成图片字节，验证按角色压缩、降档梯子、tempfile
生命周期与非回归透传。
"""

from __future__ import annotations

import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lib.reference_compression import (
    DEFAULT_SINGLE_MAX_BYTES,
    DEFAULT_TOTAL_MAX_BYTES,
    LADDER_STEPS,
    CompressedRef,
    PayloadLimits,
    ReferencePayloadFloorError,
    ReferenceSpec,
    RefRole,
    compress_single_at_step,
    compressed_reference_payload,
    select_ladder_step,
)

pytestmark = pytest.mark.unit


def _noise_jpeg_bytes(w: int, h: int) -> bytes:
    """高熵噪声图（压缩体积随分辨率变化），存为 JPEG。"""
    img = Image.effect_noise((w, h), 80).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _noise_png_bytes(w: int, h: int) -> bytes:
    img = Image.effect_noise((w, h), 80).convert("RGB")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _solid_jpeg_bytes(w: int, h: int, color=(200, 100, 50)) -> bytes:
    img = Image.new("RGB", (w, h), color=color)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _solid_png_bytes(w: int, h: int, color=(200, 100, 50)) -> bytes:
    img = Image.new("RGB", (w, h), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _dims(data: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(data)) as img:
        return img.size


def _fmt(data: bytes) -> str:
    with Image.open(BytesIO(data)) as img:
        return (img.format or "").upper()


# ── 常量一致性 ──────────────────────────────────────────────────────────────


def test_default_constants_match_service():
    from lib.config import service

    assert DEFAULT_TOTAL_MAX_BYTES == service._DEFAULT_REFERENCE_TOTAL_MAX_BYTES
    assert DEFAULT_SINGLE_MAX_BYTES == service._DEFAULT_REFERENCE_SINGLE_MAX_BYTES


# ── compress_single_at_step ─────────────────────────────────────────────────


def test_array_step0_scales_to_2048():
    raw = _noise_jpeg_bytes(3000, 3000)
    out = compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert max(_dims(out)) == 2048
    assert _fmt(out) == "JPEG"


def test_array_floor_step_scales_to_1024():
    raw = _noise_jpeg_bytes(3000, 3000)
    out = compress_single_at_step(raw, RefRole.ARRAY, LADDER_STEPS, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert max(_dims(out)) == 1024


def test_array_step0_passthrough_small_jpeg():
    raw = _solid_jpeg_bytes(800, 600)  # 已是 JPEG、小、尺寸合规
    out = compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out is raw  # 原样透传，不二压


def test_array_step0_png_is_reencoded():
    raw = _solid_png_bytes(800, 600)  # 重格式 → 不透传
    out = compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out != raw
    assert _fmt(out) == "JPEG"


def test_frame_never_resizes_even_above_2048():
    # 重格式触发重编码，但 FRAME 永不缩尺寸：输出尺寸保持 3000x2000
    raw = _noise_png_bytes(3000, 2000)
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert _dims(out) == (3000, 2000)
    assert _fmt(out) == "JPEG"


def test_frame_small_jpeg_passthrough():
    raw = _solid_jpeg_bytes(1200, 800)
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out is raw


def test_frame_small_heavy_format_passthrough():
    # 重格式（PNG）但在 single 预算内 → FRAME 透传不改写（护首/尾帧像素，ADR「不使 Sora 恶化」），
    # 不因格式强转 JPEG（与 ARRAY 不同）。
    raw = _solid_png_bytes(1200, 800)
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    assert out is raw


def test_frame_over_single_budget_reencoded_no_resize():
    raw = _solid_jpeg_bytes(2048, 2048)
    # single_max 设极小 → 超预算才重编码（仍不缩尺寸）
    out = compress_single_at_step(raw, RefRole.FRAME, 0, single_max_bytes=1)
    assert _dims(out) == (2048, 2048)


# ── select_ladder_step ──────────────────────────────────────────────────────


def test_ladder_fits_at_step0():
    raws = [_solid_jpeg_bytes(512, 512)]
    landed, out = select_ladder_step(raws, [RefRole.ARRAY], PayloadLimits(), start_step=0)
    assert landed == 0
    assert len(out) == 1


def test_ladder_downgrades_when_over_total():
    raw = _noise_jpeg_bytes(2048, 2048)
    size0 = len(compress_single_at_step(raw, RefRole.ARRAY, 0, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES))
    # total 卡在 step0 体积之下 → 必须至少降一档
    limits = PayloadLimits(total_max_bytes=size0 - 1, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    landed, out = select_ladder_step([raw], [RefRole.ARRAY], limits, start_step=0)
    assert landed >= 1
    assert sum(len(b) for b in out) <= limits.total_max_bytes


def test_ladder_floor_still_over_raises():
    raw = _noise_jpeg_bytes(2048, 2048)
    limits = PayloadLimits(total_max_bytes=1, single_max_bytes=1)
    with pytest.raises(ReferencePayloadFloorError):
        select_ladder_step([raw], [RefRole.ARRAY], limits, start_step=0)


def test_ladder_floor_error_has_code():
    err = ReferencePayloadFloorError()
    assert err.code == "ref_payload_floor_exceeded"
    assert err.params == {}


def test_ladder_single_over_budget_raises_floor():
    raw = _noise_jpeg_bytes(2048, 2048)
    # total 充裕但 single=1 → effective_single=1，floor 仍超 → 抛 floor
    limits = PayloadLimits(total_max_bytes=DEFAULT_TOTAL_MAX_BYTES, single_max_bytes=1)
    with pytest.raises(ReferencePayloadFloorError):
        select_ladder_step([raw], [RefRole.ARRAY], limits, start_step=0)


def test_ladder_frame_over_single_does_not_floor():
    # 单张大 FRAME 超 single 预算但仍 ≤ total：FRAME 不参与 single 硬门控 → 不在 provider
    # 被调前 floor 掉（交被动 413 兜底），只要不超 total 就放行（会重编码但不缩尺寸、不抛）。
    raw = _noise_jpeg_bytes(2048, 2048)
    single = len(raw) - 1  # 强制超 single → FRAME 会重编码（但不 floor）
    limits = PayloadLimits(total_max_bytes=DEFAULT_TOTAL_MAX_BYTES, single_max_bytes=single)
    landed, out = select_ladder_step([raw], [RefRole.FRAME], limits, start_step=0)
    assert landed == 0
    assert len(out) == 1
    assert _dims(out[0]) == (2048, 2048)  # 永不缩尺寸


def test_ladder_frame_over_total_still_floors():
    # FRAME 连 total 都超 → 仍 floor（真·数据过多）
    raw = _noise_jpeg_bytes(2048, 2048)
    limits = PayloadLimits(total_max_bytes=1, single_max_bytes=DEFAULT_SINGLE_MAX_BYTES)
    with pytest.raises(ReferencePayloadFloorError):
        select_ladder_step([raw], [RefRole.FRAME], limits, start_step=0)


def test_ladder_start_step_respected():
    raws = [_solid_jpeg_bytes(512, 512)]
    landed, _ = select_ladder_step(raws, [RefRole.ARRAY], PayloadLimits(), start_step=2)
    assert landed >= 2
    assert landed == 2  # 小图在 step2 即满足，不再下压


def test_ladder_empty_input():
    landed, out = select_ladder_step([], [], PayloadLimits(), start_step=3)
    assert landed == 3
    assert out == []


# ── compressed_reference_payload ────────────────────────────────────────────


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_payload_writes_and_cleans_tempfiles(tmp_path: Path):
    a = _write(tmp_path, "a.png", _solid_png_bytes(800, 600))
    b = _write(tmp_path, "b.png", _solid_png_bytes(800, 600))
    specs = [
        ReferenceSpec(source=a, label="图1", role=RefRole.ARRAY),
        ReferenceSpec(source=b, label="图2", role=RefRole.ARRAY),
    ]
    written: list[Path] = []
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (landed, refs):
        assert landed == 0
        assert [r.label for r in refs] == ["图1", "图2"]
        assert all(isinstance(r, CompressedRef) for r in refs)
        for r in refs:
            assert r.path.exists()
            written.append(r.path)
    # 退出后临时文件清理
    for p in written:
        assert not p.exists()
    # 源文件未被改动
    assert a.exists() and b.exists()


def test_payload_preserves_order_and_count(tmp_path: Path):
    paths = [_write(tmp_path, f"r{i}.png", _solid_png_bytes(400, 400)) for i in range(5)]
    specs = [ReferenceSpec(source=p, label=f"图{i}", role=RefRole.ARRAY) for i, p in enumerate(paths)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert len(refs) == 5
        assert [r.label for r in refs] == [f"图{i}" for i in range(5)]


def test_payload_empty_noop():
    with compressed_reference_payload([], limits=PayloadLimits(), start_step=2) as (landed, refs):
        assert landed == 2
        assert refs == []


def test_payload_passthrough_non_local_source_no_raise(tmp_path: Path):
    real = _write(tmp_path, "real.png", _solid_png_bytes(800, 600))
    missing = tmp_path / "does-not-exist.png"
    specs = [
        ReferenceSpec(source=real, label="图0", role=RefRole.ARRAY),
        ReferenceSpec(source=missing, label="图1", role=RefRole.ARRAY),
    ]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert len(refs) == 2
        # 透传项保留原路径、序位不变（[图N] 对齐）
        assert refs[1].path == missing
        # 可压缩项写成临时文件
        assert refs[0].path != real
        assert refs[0].path.exists()


def test_payload_passthrough_undecodable_source(tmp_path: Path):
    bad = _write(tmp_path, "bad.png", b"not an image at all")
    specs = [ReferenceSpec(source=bad, label="图0", role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert len(refs) == 1
        assert refs[0].path == bad  # 不可解码 → 透传原路径，不 raise


def test_payload_passthrough_excluded_from_budget(tmp_path: Path):
    # 透传项不计入字节预算：即便 total 极小，只要可压缩子集为空也不抛
    missing = tmp_path / "missing.png"
    specs = [ReferenceSpec(source=missing, label="图0", role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits(total_max_bytes=1, single_max_bytes=1)) as (
        _landed,
        refs,
    ):
        assert refs[0].path == missing


def test_payload_tempfiles_cleaned_on_floor_error(tmp_path: Path):
    # 主动预检在 __enter__ 内抛 floor，select_ladder_step 在写 tempfile 之前 → 无泄漏
    real = _write(tmp_path, "big.jpg", _noise_jpeg_bytes(2048, 2048))
    specs = [ReferenceSpec(source=real, label="图0", role=RefRole.ARRAY)]
    before = set(Path(tempfile.gettempdir()).glob("refcomp-*"))
    with pytest.raises(ReferencePayloadFloorError):
        with compressed_reference_payload(specs, limits=PayloadLimits(total_max_bytes=1, single_max_bytes=1)) as (
            _landed,
            _refs,
        ):
            pass
    after = set(Path(tempfile.gettempdir()).glob("refcomp-*"))
    assert after == before


def test_payload_reencoded_tempfile_preserves_source_stem(tmp_path: Path):
    # 重编码副本的文件名沿用源 stem（如 张三），让 gemini 按文件名推断参考图名仍生效。
    src = _write(tmp_path, "张三.png", _solid_png_bytes(800, 600))
    specs = [ReferenceSpec(source=src, label="", role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path != src  # 是临时副本
        assert refs[0].path.stem == "张三"  # 但保留源 stem
        assert refs[0].path.suffix == ".jpg"


def test_payload_passthrough_uses_original_path_not_copy(tmp_path: Path):
    # 已是小 JPEG 的 ARRAY → 透传：用原始源路径，不写临时副本（省拷贝、避免后缀错配、最大保真）。
    src = _write(tmp_path, "small.jpg", _solid_jpeg_bytes(600, 400))
    specs = [ReferenceSpec(source=src, label="图0", role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path == src


def test_payload_frame_under_budget_uses_original_path(tmp_path: Path):
    # FRAME 在预算内 → 透传原始路径（含 PNG，不强转 JPEG），护首/尾帧像素
    src = _write(tmp_path, "frame.png", _solid_png_bytes(1000, 1600))
    specs = [ReferenceSpec(source=src, label="", role=RefRole.FRAME)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path == src


def test_payload_truncated_image_passthrough_no_raise(tmp_path: Path):
    # 截断 JPEG：能过 verify() 但 load() 会失败 → 透传原路径，绝不让 compress 的 ValueError 逃逸
    full = _solid_jpeg_bytes(1200, 900)
    truncated = full[: len(full) // 2]
    src = _write(tmp_path, "truncated.jpg", truncated)
    specs = [ReferenceSpec(source=src, label="图0", role=RefRole.ARRAY)]
    with compressed_reference_payload(specs, limits=PayloadLimits()) as (_landed, refs):
        assert refs[0].path == src  # 透传，不 raise


class TestStagingLocationIsSignable:
    """压缩副本必须落在数据根内。

    H3 这类只收公网 URL 的后端要把参考图签成本站直链，而签名只认数据根内的文件。
    副本落在系统 ``/tmp`` 时整条参考视频链路在出链那一步就失败（"参考素材不在数据根内，
    无法出链: /tmp/refcomp-.../000-reference_image.jpg"），而且只在素材大到需要重编码时
    才触发——小图透传用原路径、天然在数据根内，所以线上症状是间歇性的。
    """

    def _reencoded_spec(self, tmp_path: Path) -> tuple[list[ReferenceSpec], PayloadLimits]:
        """PNG 源必被重编码成 JPEG，于是一定写临时副本（透传项不写）。"""
        source = tmp_path / "000-reference_image.png"
        source.write_bytes(_solid_png_bytes(800, 600))
        return [ReferenceSpec(source=source, label="", role=RefRole.ARRAY)], PayloadLimits()

    def test_copies_land_under_the_given_parent(self, tmp_path):
        specs, limits = self._reencoded_spec(tmp_path)
        staging = tmp_path / "data" / ".refcomp"
        with compressed_reference_payload(specs, limits=limits, temp_parent=staging) as (_, refs):
            assert refs[0].path != specs[0].source, "前提：这张图大到必须重编码，不是透传"
            assert staging in refs[0].path.parents

    def test_signed_url_can_be_issued_for_the_copy(self, tmp_path, monkeypatch):
        """真正要守的不是目录名，而是「副本签得出直链」。"""
        from lib.app_data_dir import _reset_for_tests
        from lib.signed_media_url import sign_media_path

        root = tmp_path / "data"
        monkeypatch.setenv("SESSION_COOKIE_SECRET", "s" * 40)
        monkeypatch.setenv("ARCREEL_DATA_DIR", str(root))
        monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
        _reset_for_tests()
        try:
            specs, limits = self._reencoded_spec(tmp_path)
            with compressed_reference_payload(specs, limits=limits, temp_parent=root / ".refcomp") as (_, refs):
                assert sign_media_path(refs[0].path)
        finally:
            _reset_for_tests()

    def test_default_still_uses_system_tempdir(self, tmp_path):
        """不指定时行为不变——压缩层本身不该知道数据根这回事。"""
        specs, limits = self._reencoded_spec(tmp_path)
        with compressed_reference_payload(specs, limits=limits) as (_, refs):
            # 两侧都 resolve：macOS 的 gettempdir() 是 /var/folders/...，而它是
            # /private/var/... 的符号链接，只 resolve 一侧永远对不上。
            assert Path(tempfile.gettempdir()).resolve() in refs[0].path.resolve().parents

    def test_staging_dir_is_created_on_demand(self, tmp_path):
        staging = tmp_path / "not" / "yet" / "there"
        specs, limits = self._reencoded_spec(tmp_path)
        with compressed_reference_payload(specs, limits=limits, temp_parent=staging) as (_, refs):
            assert refs[0].path.is_file()

    def test_copies_are_cleaned_up_on_exit(self, tmp_path):
        """副本留在数据根内就会被当成用户数据一路存着——退出必须清干净。"""
        staging = tmp_path / "data" / ".refcomp"
        specs, limits = self._reencoded_spec(tmp_path)
        with compressed_reference_payload(specs, limits=limits, temp_parent=staging) as (_, refs):
            leaked = refs[0].path
            assert leaked.is_file()
        assert not leaked.exists()


class TestGeneratorPassesASignableStagingDir:
    """光让压缩层「支持」放在哪没用——真正的生产缺陷是调用方没指定，于是落到了 /tmp。"""

    def test_staging_dir_is_inside_the_data_root(self, tmp_path, monkeypatch):
        from lib.app_data_dir import _reset_for_tests, base_data_dir
        from lib.media_generator import _reference_staging_dir

        monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
        _reset_for_tests()
        try:
            staging = _reference_staging_dir().resolve()
            assert base_data_dir().resolve() in staging.parents
        finally:
            _reset_for_tests()

    def test_generator_hands_that_dir_to_the_compressor(self, tmp_path, monkeypatch):
        """签名只认数据根内的文件，调用方漏传 temp_parent 就等于整条参考视频链路挂掉。"""
        import asyncio
        import contextlib as _contextlib

        import lib.reference_compression as rc
        from lib.app_data_dir import _reset_for_tests, base_data_dir
        from lib.media_generator import MediaGenerator

        monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
        _reset_for_tests()
        captured: dict = {}

        @_contextlib.contextmanager
        def _spy(specs, *, limits, start_step=0, temp_parent=None):
            captured["temp_parent"] = temp_parent
            yield start_step, []

        monkeypatch.setattr(rc, "compressed_reference_payload", _spy)

        class _Stub:
            async def _reference_limits(self, provider_id):
                return PayloadLimits()

        async def _call(_refs):
            return "done"

        source = _write(tmp_path, "ref.png", _solid_png_bytes(64, 64))
        try:
            result = asyncio.run(
                MediaGenerator._run_with_reference_compression(
                    _Stub(),  # pyright: ignore[reportArgumentType]
                    specs=[ReferenceSpec(source=source, label="", role=RefRole.ARRAY)],
                    provider_id=None,
                    build_and_call=_call,
                )
            )
            assert result == "done"
            parent = captured["temp_parent"]
            assert parent is not None, "没传 temp_parent，副本会落到系统 /tmp，签不出直链"
            assert base_data_dir().resolve() in parent.resolve().parents
        finally:
            _reset_for_tests()


class TestStagingSweep:
    """换到数据根之后残骸才成为问题：``/tmp`` 有系统与容器重启兜底，数据卷没有。

    正常路径由压缩层的 finally 当场清干净，留下来的只可能是进程被杀那一刻的残骸。
    """

    def _staging(self, tmp_path, monkeypatch):
        from lib.app_data_dir import _reset_for_tests

        monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.delenv("AI_ANIME_PROJECTS", raising=False)
        _reset_for_tests()
        from lib.media_generator import _reference_staging_dir

        return _reference_staging_dir()

    def test_old_leftovers_are_removed(self, tmp_path, monkeypatch):
        import os
        import time

        from lib.app_data_dir import _reset_for_tests
        from lib.media_generator import _STAGING_TTL_SECONDS, _reference_staging_dir

        staging = self._staging(tmp_path, monkeypatch)
        try:
            stale = staging / "refcomp-dead"
            stale.mkdir(parents=True)
            (stale / "a.jpg").write_bytes(b"x")
            old = time.time() - _STAGING_TTL_SECONDS - 60
            os.utime(stale, (old, old))
            _reference_staging_dir()
            assert not stale.exists()
        finally:
            _reset_for_tests()

    def test_in_flight_copies_are_left_alone(self, tmp_path, monkeypatch):
        """副本要活过整轮生成——H3 轮询上限约 90 分钟，扫早了是把正在用的参考图删掉。"""
        from lib.app_data_dir import _reset_for_tests
        from lib.media_generator import _reference_staging_dir

        staging = self._staging(tmp_path, monkeypatch)
        try:
            fresh = staging / "refcomp-live"
            fresh.mkdir(parents=True)
            _reference_staging_dir()
            assert fresh.is_dir()
        finally:
            _reset_for_tests()

    def test_ttl_outlives_the_slowest_generation(self):
        from lib.media_generator import _STAGING_TTL_SECONDS

        assert _STAGING_TTL_SECONDS > 90 * 60

    def test_sweep_never_fails_a_generation(self, tmp_path, monkeypatch):
        """清理不是功能：删不掉旧目录也不该让这次生成挂掉。"""
        from lib.media_generator import _sweep_stale_staging

        monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(OSError("boom")))
        _sweep_stale_staging(tmp_path)  # 不抛即通过
