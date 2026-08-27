"""签名直链端点：把数据根内的素材以短时效 URL 匿名放出。

唯一的消费方是上游模型服务（H3 之流）——它们在提交时自己来拉参考图，带不了会话
cookie，也带不了 Authorization header。凭据因此完全落在 URL 的 HMAC token 上：
token 认领了具体的相对路径与过期时刻，签名不对、过期、文件不在，三种情况一律 404
（不做区分，免得响应差异变成数据根内的文件存在性探针）。

⚠️ 本路由在 ``MatrixSessionGate`` 的公开前缀内，即匿名可达。新增端点不要挂到
``MEDIA_URL_PREFIX`` 底下 —— 那等于一并放行。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from lib.signed_media_url import MEDIA_URL_PREFIX, resolve_media_token

router = APIRouter()


@router.get(MEDIA_URL_PREFIX + "{token}", include_in_schema=False)
async def serve_signed_media(token: str) -> FileResponse:
    path = await asyncio.to_thread(resolve_media_token, token)
    if path is None:
        raise HTTPException(status_code=404)
    # 上游明确拒收重定向，所以这里必须是直接的 200 + 文件体。
    # 缓存标记为 private：URL 本身就是凭据，不该躺进任何共享缓存。
    return FileResponse(path, headers={"Cache-Control": "private, max-age=300"})
