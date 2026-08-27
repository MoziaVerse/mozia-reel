"""Qwen 图像族（``qwen/qwen-image`` / ``qwen/qwen-image-edit``）的模型特性。

网关把这一族拆成两个 model id：文生图与图片编辑各一个，端点也不同
（``/v1/images/generations`` vs ``/v1/images/edits``）。让用户自己在两者间挑是错的：
选中哪个取决于**这次调用有没有参考图**，而不是取决于偏好——选了编辑却不给图，
上游直接 500；选了文生却挂参考图，同样不成立。

所以这一族对外只当**一个模型**：下拉里只留 ``qwen/qwen-image``，实际下发的 id 按
本次请求有无参考图改写。与画布（ZeoCanvasLite ``config/models/qwenImageTraits.ts``）
同口径，那边也是这么收敛的。

⚠️ 两者价差 2.5 倍（0.04 / 0.1 元每次），所以改写必须发生在**计费口径之前**，
估价按改写后的 id 算，否则按钮上写一个价、账单扣另一个价。
"""

from __future__ import annotations

# 文生图：POST /v1/images/generations
QWEN_IMAGE_MODEL = "qwen/qwen-image"
# 图片编辑：POST /v1/images/edits（multipart）
QWEN_IMAGE_EDIT_MODEL = "qwen/qwen-image-edit"

QWEN_IMAGE_FAMILY = frozenset({QWEN_IMAGE_MODEL, QWEN_IMAGE_EDIT_MODEL})

# 上游放开的全部尺寸档位，**这是白名单不是建议值**——传 1024x1024 之类会被 400。
QWEN_IMAGE_SIZES: tuple[str, ...] = (
    "1328x1328",
    "1664x928",
    "928x1664",
    "1472x1104",
    "1104x1472",
    "1584x1056",
    "1056x1584",
)

QWEN_IMAGE_DEFAULT_SIZE = "1328x1328"

# 比例 → 档位。只有 4:3 / 3:4 / 3:2 / 2:3 精确命中；16:9 实际拿到 1664x928
# （1.793 vs 1.778，差 0.9%），肉眼无感但别当精确值用。
QWEN_IMAGE_SIZE_BY_ASPECT: dict[str, str] = {
    "1:1": "1328x1328",
    "3:4": "1104x1472",
    "4:3": "1472x1104",
    "9:16": "928x1664",
    "16:9": "1664x928",
    "2:3": "1056x1584",
    "3:2": "1584x1056",
}

# 编辑端点的参考图上限
QWEN_IMAGE_EDIT_MAX_REFERENCES = 8


def is_qwen_image_model(model: str | None) -> bool:
    """是否属于 Qwen 图像族（两个变体都算）。"""
    return bool(model) and model in QWEN_IMAGE_FAMILY


def resolve_qwen_image_model(model: str | None, *, has_references: bool) -> str:
    """按本次请求有无参考图定下真正要下发的 model id。

    两个方向都要改写，不只是「文生 + 有图 → 编辑」：用户若选中了编辑模型却没给图，
    退回文生同样是必需的，否则上游 500。

    非 Qwen 图像族原样返回，调用方可以无脑套一层。
    """
    if not is_qwen_image_model(model):
        return model or ""
    return QWEN_IMAGE_EDIT_MODEL if has_references else QWEN_IMAGE_MODEL


def qwen_image_size_for_aspect(aspect_ratio: str | None) -> str:
    """比例换尺寸。**未登记的比例一律回落方图**，不做就近匹配——上游只认白名单，
    猜一个近似档位不如给一个确定能出图的。"""
    if not aspect_ratio:
        return QWEN_IMAGE_DEFAULT_SIZE
    return QWEN_IMAGE_SIZE_BY_ASPECT.get(aspect_ratio.strip(), QWEN_IMAGE_DEFAULT_SIZE)


def resolve_qwen_image_size(*, image_size: str | None, aspect_ratio: str | None) -> str:
    """定下最终下发的 size。

    之所以要**校验**而不是直接透传上层给的值：调用方不止一处，分镜、宫格、参考视频
    各自带着按别的模型算出来的尺寸进来，透传过去就是一个 400，回落到合法档位至少能出图。
    """
    size = (image_size or "").strip().lower()
    if size in QWEN_IMAGE_SIZES:
        return size
    return qwen_image_size_for_aspect(aspect_ratio)


def is_hidden_variant(model: str | None) -> bool:
    """该 model 是否只是同族里的从属变体，不该出现在选择列表里。

    Qwen 图像族对外当一个模型：下拉里只留 ``qwen/qwen-image``，编辑变体由
    ``resolve_qwen_image_model`` 按有无参考图自动选中。

    隐藏的只是**下拉选项**，不是模型支持——编辑变体仍会被真正下发，DB 里那行也保留着
    （模型目录同步、能力查询、计费口径都还要用它）。
    """
    return model == QWEN_IMAGE_EDIT_MODEL
