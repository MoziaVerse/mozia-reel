"""`/agent-installation-guide.md` 渲染。

这份文档是外部 Agent 唯一的入口说明，而它由后端在**运行期**渲染，拿不到前端
构建期的 VITE_BRAND_NAME。两边一旦脱节，用户被告知"了解如何使用 MoziaReel"，
打开文档看到的却是"ArcReel 外部 Agent 接入任务"，会以为拿错了地址——这类不一致没人盯着，
所以钉在测试里。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import app


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # 不进 lifespan：本端点只读模板 + 环境变量，与 DB / worker 无关。
    # 默认按独立部署跑；托管态由单独的用例显式打开。
    monkeypatch.delenv("MATRIX_BACKEND_URL", raising=False)
    return TestClient(app, raise_server_exceptions=False)


def test_brand_follows_env(client, monkeypatch):
    monkeypatch.setenv("BRAND_NAME", "MoziaReel")
    body = client.get("/agent-installation-guide.md").text
    assert "MoziaReel 外部 Agent 接入任务" in body
    # `ArcReel/skills` 是上游真实的 skill 包名，指向具体仓库，不跟随品牌改写；
    # 除它之外不该再有上游品牌残留。
    assert "ArcReel" not in body.replace("ArcReel/skills", "")


def test_falls_back_to_upstream_brand_when_unset(client, monkeypatch):
    """未改名的部署行为不变。"""
    monkeypatch.delenv("BRAND_NAME", raising=False)
    assert "ArcReel 外部 Agent 接入任务" in client.get("/agent-installation-guide.md").text


def test_blank_brand_is_treated_as_unset(client, monkeypatch):
    """Dockerfile 里 ARG 留空会传进来一个空串，不能渲染成没有名字的文档。"""
    monkeypatch.setenv("BRAND_NAME", "   ")
    assert "ArcReel 外部 Agent 接入任务" in client.get("/agent-installation-guide.md").text


def test_base_url_comes_from_the_request(client, monkeypatch):
    monkeypatch.delenv("BRAND_NAME", raising=False)
    body = client.get("/agent-installation-guide.md").text
    assert "{{BASE_URL}}" not in body
    assert "http://testserver/app/settings" in body


def test_no_placeholder_survives_rendering(client, monkeypatch):
    """漏填的占位符会原样出现在给 Agent 看的文档里。"""
    monkeypatch.setenv("BRAND_NAME", "MoziaReel")
    body = client.get("/agent-installation-guide.md").text
    assert "{{" not in body


def test_served_without_auth(client, monkeypatch):
    """外部 Agent 是先读文档才拿得到 key 的，这一页必须匿名可达。"""
    monkeypatch.delenv("BRAND_NAME", raising=False)
    res = client.get("/agent-installation-guide.md")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")


def test_served_in_hosted_mode(client, monkeypatch):
    """托管态同样提供：外部 Agent 经远程 MCP 驱动本站那条链路在这里是通的。

    托管态签发的 API Key 自带租户段，``McpTenantGate`` 据此定位租户库，指引里
    ``?section=api-keys`` 那个链接也确实落得到设置页。这一页由 Agent 宿主自己拉取、
    带不了会话 cookie，门禁按前缀放行（见 server/matrix_gate.py）。
    """
    monkeypatch.setenv("MATRIX_BACKEND_URL", "https://matrix.example.com")
    assert client.get("/agent-installation-guide.md").status_code == 200
