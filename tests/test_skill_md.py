"""`/skill.md` 渲染。

这份文档是外部 Agent 唯一的入口说明，而它由后端在**运行期**渲染，拿不到前端
构建期的 VITE_BRAND_NAME。两边一旦脱节，用户被告知"了解如何使用 MoziaReel"，
打开文档看到的却是"ArcReel Skill"，会以为拿错了地址——这类不一致没人盯着，
所以钉在测试里。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import app

pytestmark = pytest.mark.integration


@pytest.fixture
def client() -> TestClient:
    # 不进 lifespan：本端点只读模板 + 环境变量，与 DB / worker 无关。
    return TestClient(app, raise_server_exceptions=False)


def test_brand_follows_env(client, monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "MoziaReel")
    body = client.get("/skill.md").text
    assert "MoziaReel Skill" in body
    assert "ArcReel" not in body, "改名后不该再有上游品牌残留"


def test_falls_back_to_upstream_brand_when_unset(client, monkeypatch):
    """未改名的部署行为不变。"""
    monkeypatch.delenv("BRAND_NAME", raising=False)
    assert "ArcReel Skill" in client.get("/skill.md").text


def test_blank_brand_is_treated_as_unset(client, monkeypatch):
    """Dockerfile 里 ARG 留空会传进来一个空串，不能渲染成没有名字的文档。"""
    monkeypatch.setenv("BRAND_NAME", "   ")
    assert "ArcReel Skill" in client.get("/skill.md").text


def test_base_url_comes_from_the_request(client, monkeypatch):
    monkeypatch.delenv("BRAND_NAME", raising=False)
    body = client.get("/skill.md").text
    assert "{{BASE_URL}}" not in body
    assert "http://testserver/api/v1/projects" in body


def test_no_placeholder_survives_rendering(client, monkeypatch):
    """漏填的占位符会原样出现在给 Agent 看的文档里。"""
    monkeypatch.setenv("BRAND_NAME", "MoziaReel")
    body = client.get("/skill.md").text
    assert "{{" not in body


def test_served_without_auth(client, monkeypatch):
    """外部 Agent 是先读文档才拿得到 key 的，这一页必须匿名可达。"""
    monkeypatch.delenv("BRAND_NAME", raising=False)
    res = client.get("/skill.md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
