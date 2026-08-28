import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { SystemConfigPage } from "@/components/pages/SystemConfigPage";
import type { GetSystemConfigResponse, GetSystemVersionResponse, ProviderInfo } from "@/types";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeConfigResponse(
  overrides?: Partial<GetSystemConfigResponse["settings"]>,
): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "gemini/veo-3",
      default_image_backend: "gemini/imagen-4",
      default_text_backend: "",
      text_backend_simple: "",
      text_backend_complex: "",
      video_generate_audio: true,
      video_poll_timeout_seconds: 3600,
      anthropic_api_key: { is_set: true, masked: "sk-ant-***" },
      anthropic_base_url: "",
      anthropic_model: "",
      anthropic_default_haiku_model: "",
      anthropic_default_opus_model: "",
      anthropic_default_sonnet_model: "",
      claude_code_subagent_model: "",
      agent_session_cleanup_delay_seconds: 300,
      agent_max_concurrent_sessions: 5,
      ...overrides,
    },
    options: {
      video_backends: ["gemini/veo-3"],
      image_backends: ["gemini/imagen-4"],
      text_backends: [],
    },
  };
}

function makeProviders(overrides?: Partial<ProviderInfo>): { providers: ProviderInfo[] } {
  return {
    providers: [
      {
        id: "gemini",
        display_name: "Google Gemini",
        description: "Google Gemini API",
        status: "ready",
        media_types: ["image", "video", "text"],
        capabilities: [],
        configured_keys: ["api_key"],
        missing_keys: [],
        models: {},
        ...overrides,
      },
    ],
  };
}

function makeVersionResponse(overrides?: Partial<GetSystemVersionResponse>): GetSystemVersionResponse {
  return {
    current: { version: "0.9.0" },
    latest: {
      version: "0.9.1",
      tag_name: "v0.9.1",
      name: "0.9.1",
      body: "## What's Changed\n- add about tab",
      html_url: "https://github.com/example/ArcReel/releases/tag/v0.9.1",
      published_at: "2026-04-21T08:00:00Z",
    },
    has_update: true,
    checked_at: "2026-04-21T09:00:00Z",
    update_check_error: null,
    ...overrides,
  };
}

function renderPage(path = "/app/settings") {
  const location = memoryLocation({ path, record: true });
  return render(
    <Router hook={location.hook}>
      <SystemConfigPage />
    </Router>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SystemConfigPage", () => {
  beforeEach(() => {
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();

    // Default: silence child section network calls so tests don't hang
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders());
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemVersion").mockResolvedValue(makeVersionResponse());
    vi.spyOn(API, "getProviderConfig").mockResolvedValue({
      id: "gemini",
      display_name: "Google Gemini",
      status: "ready",
      media_types: ["image", "video"],
      capabilities: [],
      fields: [],
      supports_base_url: false,
    } as never);
    vi.spyOn(API, "listCredentials").mockResolvedValue({ credentials: [] });
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue({ stats: [], period: { start: "", end: "" } });
  });

  it("renders the page header", () => {
    renderPage();
    expect(screen.getByText("设置")).toBeInTheDocument();
    expect(screen.getByText("系统配置与 API 访问管理")).toBeInTheDocument();
  });

  it("renders all 6 sidebar sections", () => {
    renderPage();
    expect(screen.getByRole("button", { name: /Agent/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /供应商/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /模型选择/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /用量统计/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /API 令牌/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /关于/ })).toBeInTheDocument();
  });

  it("defaults to the 供应商 section", () => {
    renderPage();
    const providersButton = screen.getByRole("button", { name: /供应商/ });
    // Active sidebar item carries aria-current="page" (Darkroom redesign)
    expect(providersButton).toHaveAttribute("aria-current", "page");
  });

  it("clicking 供应商 makes it the active section", async () => {
    renderPage();
    const providersButton = screen.getByRole("button", { name: /供应商/ });
    fireEvent.click(providersButton);
    await waitFor(() => {
      expect(providersButton).toHaveAttribute("aria-current", "page");
    });
  });

  it("clicking 模型选择 makes it the active section", async () => {
    renderPage();
    const mediaButton = screen.getByRole("button", { name: /模型选择/ });
    fireEvent.click(mediaButton);
    await waitFor(() => {
      expect(mediaButton).toHaveAttribute("aria-current", "page");
    });
  });

  it("clicking 用量统计 makes it the active section", async () => {
    renderPage();
    const usageButton = screen.getByRole("button", { name: /用量统计/ });
    fireEvent.click(usageButton);
    await waitFor(() => {
      expect(usageButton).toHaveAttribute("aria-current", "page");
    });
  });

  it("does not show warnings when only the embedded-agent credential is missing", async () => {
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(
      makeConfigResponse({ anthropic_api_key: { is_set: false, masked: null } }),
    );
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders({ status: "ready" }));

    // Banner renders inside non-providers content panes (providers section has its own UI),
    // so land on agent to assert it.
    renderPage("/app/settings?section=agent");

    await screen.findByText("Agent Runtime");

    expect(screen.queryByText("当前配置存在以下问题，可能会影响部分功能：")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("配置未完成")).not.toBeInTheDocument();
  });

  it("does not show warning banner when config is complete", async () => {
    renderPage();

    // Give time for config status to load
    await waitFor(() => {
      expect(API.getProviders).toHaveBeenCalled();
    });

    expect(screen.queryByText("当前配置存在以下问题，可能会影响部分功能：")).not.toBeInTheDocument();
  });

  it("renders the back link that navigates to projects", () => {
    renderPage();
    const link = screen.getByRole("link", { name: "返回" });
    expect(link).toBeInTheDocument();
    expect(link).toHaveAttribute("href", "/app/projects");
  });

  it("loads version info when entering the about section", async () => {
    renderPage("/app/settings?section=about");

    expect(await screen.findByText("0.9.0")).toBeInTheDocument();
    expect(await screen.findByText(/最新版本：0.9.1/)).toBeInTheDocument();
    expect(await screen.findByText("发现新版本")).toBeInTheDocument();
    expect(await screen.findByText("Release Notes")).toBeInTheDocument();
    expect(await screen.findByText(/add about tab/)).toBeInTheDocument();
  });

  it("rechecks updates when clicking the refresh button", async () => {
    const getSystemVersion = vi.spyOn(API, "getSystemVersion").mockResolvedValue(
      makeVersionResponse({ latest: null, has_update: false, update_check_error: "boom" }),
    );

    renderPage("/app/settings?section=about");

    const button = await screen.findByRole("button", { name: /检查更新/ });
    fireEvent.click(button);

    await waitFor(() => {
      expect(getSystemVersion).toHaveBeenCalledTimes(2);
    });
  });
});

describe("SystemConfigPage · 托管态", () => {
  beforeEach(() => {
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
    vi.spyOn(API, "getProviders").mockResolvedValue(makeProviders());
    vi.spyOn(API, "listCustomProviders").mockResolvedValue({ providers: [] });
    vi.spyOn(API, "getSystemVersion").mockResolvedValue(makeVersionResponse());
    vi.spyOn(API, "getUsageStatsGrouped").mockResolvedValue({ stats: [], period: { start: "", end: "" } });
    vi.spyOn(API, "getMatrixCredits").mockResolvedValue({ available: false });
    vi.spyOn(API, "getMatrixOverview").mockResolvedValue({
      enabled: true,
      connected: true,
      gateway_host: "gw.example.com",
      media_counts: { text: 1, image: 1, video: 1, audio: 1 },
      models: [{ model_id: "m/one", display_name: "One", media_type: "video" }],
      matrix_web_url: "https://matrix.example.com",
      user: { username: "zeo", sso_sub: "sub-123" },
    });
  });

  it("把供应商入口整个撤掉——网关由平台下发，用户既选不了也换不了", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: /账户/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /供应商/ })).not.toBeInTheDocument();
  });

  it("默认落在账户页，并按 Matrix 站内口径展示用户名与用户 ID", async () => {
    renderPage();
    expect(await screen.findByText("zeo")).toBeInTheDocument();
    expect(screen.getByText("sub-123")).toBeInTheDocument();
  });

  it("存量的 ?section=providers 书签落到账户页，而不是一个空壳", async () => {
    renderPage("/app/settings?section=providers");
    expect(await screen.findByText("sub-123")).toBeInTheDocument();
  });

  it("智能体页只留模型路由，凭证 CRUD 整个撤掉", async () => {
    vi.spyOn(API, "listAgentCredentials").mockResolvedValue({
      credentials: [
        {
          id: 1,
          preset_id: "__custom__",
          display_name: "Matrix 网关",
          icon_key: null,
          base_url: "https://gw.example.com",
          api_key_masked: "abcd…wxyz",
          model: "m/one",
          haiku_model: "",
          sonnet_model: "",
          opus_model: "",
          subagent_model: "",
          is_active: true,
          created_at: "2026-08-20T00:00:00",
        },
      ],
    } as never);

    renderPage("/app/settings?section=agent");

    expect(await screen.findByText("模型路由")).toBeInTheDocument();
    // 地址与密钥由平台下发，用户改不了也不该看到入口
    expect(screen.queryByText(/添加供应商/)).not.toBeInTheDocument();
    expect(screen.queryByText(/连接测试/)).not.toBeInTheDocument();
    expect(screen.queryByText(/gw\.example\.com/)).not.toBeInTheDocument();
  });

  it("撤掉 API 令牌入口——外部 Agent 那条链路整个不提供", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: /账户/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /API 令牌/ })).not.toBeInTheDocument();
  });

  it("存量的 ?section=api-keys 书签同样落到账户页", async () => {
    renderPage("/app/settings?section=api-keys");
    expect(await screen.findByText("sub-123")).toBeInTheDocument();
  });

  it("模型页在选择器之外附一份网关可用模型清单", async () => {
    renderPage("/app/settings?section=media");
    expect(await screen.findByText("One")).toBeInTheDocument();
  });

  it("余额取不到时说明情况，不显示成 0——后者会被当成余额耗尽", async () => {
    renderPage();
    expect(await screen.findByText(/暂时取不到余额/)).toBeInTheDocument();
  });
});
