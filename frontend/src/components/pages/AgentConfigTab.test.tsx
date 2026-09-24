import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import "@/i18n";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useConfigStatusStore } from "@/stores/config-status-store";
import { AgentConfigTab } from "@/components/pages/AgentConfigTab";
import type { GetSystemConfigResponse } from "@/types";
import type {
  AgentCredential,
  PresetProvider,
} from "@/types/agent-credential";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeConfigResponse(): GetSystemConfigResponse {
  return {
    settings: {
      default_video_backend: "",
      default_image_backend: "",
      default_text_backend: "",
      text_backend_simple: "",
      text_backend_complex: "",
      video_generate_audio: true,
      anthropic_api_key: { is_set: false, masked: null },
      anthropic_base_url: "",
      anthropic_model: "",
      anthropic_default_haiku_model: "",
      anthropic_default_opus_model: "",
      anthropic_default_sonnet_model: "",
      claude_code_subagent_model: "",
      agent_session_cleanup_delay_seconds: 300,
      agent_max_concurrent_sessions: 5,
    },
    options: {
      video_backends: [],
      image_backends: [],
      text_backends: [],
    },
  } as unknown as GetSystemConfigResponse;
}

function makePreset(overrides?: Partial<PresetProvider>): PresetProvider {
  return {
    id: "anthropic",
    display_name: "Anthropic",
    icon_key: "anthropic",
    messages_url: "https://api.anthropic.com",
    discovery_url: "https://api.anthropic.com/v1/models",
    default_model: "claude-sonnet-4",
    suggested_models: ["claude-sonnet-4", "claude-haiku-4-5"],
    docs_url: null,
    api_key_url: null,
    notes: null,
    api_key_pattern: null,
    is_recommended: true,
    ...overrides,
  };
}

function makeCredential(overrides?: Partial<AgentCredential>): AgentCredential {
  return {
    id: 1,
    preset_id: "anthropic",
    display_name: "Anthropic 主号",
    icon_key: "anthropic",
    base_url: "https://api.anthropic.com",
    api_key_masked: "sk-ant-***",
    model: "claude-sonnet-4",
    haiku_model: null,
    sonnet_model: null,
    opus_model: null,
    subagent_model: null,
    is_active: true,
    created_at: "2026-04-21T00:00:00Z",
    ...overrides,
  };
}

function setupBaseMocks(opts?: { credentials?: AgentCredential[] }) {
  vi.spyOn(API, "getSystemConfig").mockResolvedValue(makeConfigResponse());
  vi.spyOn(API, "listAgentCredentials").mockResolvedValue({
    credentials: opts?.credentials ?? [],
  });
  vi.spyOn(API, "listAgentPresetProviders").mockResolvedValue({
    providers: [makePreset()],
    custom_sentinel_id: "__custom__",
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("AgentConfigTab — credentials directory", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useConfigStatusStore.setState(useConfigStatusStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  const MANAGED_OVERVIEW = {
    enabled: true,
    agent_default_model: "deepseek/deepseek-v4-flash-w8a8",
    models: [
      { model_id: "z-ai/glm-5.2", display_name: "GLM 5.2", media_type: "text" as const, quota_sources: ["paid"], agent_ready: true },
      {
        model_id: "deepseek/deepseek-v4-flash-w8a8",
        display_name: "Deepseek V4 Flash W8A8",
        media_type: "text" as const,
        quota_sources: ["gift", "paid"],
        agent_ready: true,
      },
      { model_id: "qwen/qwen3.6-plus", display_name: "Qwen3.6 Plus", media_type: "text" as const, quota_sources: ["paid"], agent_ready: false },
    ],
  };
  const managedCredential = (model: string) =>
    makeCredential({ preset_id: "__custom__", display_name: "Matrix 网关", model });

  it("managed mode offers only agent-ready models and preselects the gift default for unsupported stored values", async () => {
    setupBaseMocks({ credentials: [managedCredential("qwen/qwen3.8-27b")] });
    render(<AgentConfigTab visible matrixOverview={MANAGED_OVERVIEW} />);

    const gift = await screen.findByRole("radio", { name: /Deepseek V4 Flash W8A8/ });
    expect(gift).toBeChecked();
    expect(screen.getByRole("radio", { name: /GLM 5.2/ })).not.toBeChecked();
    expect(screen.queryByText("Qwen3.6 Plus")).not.toBeInTheDocument();
    expect(screen.getByText("默认")).toBeInTheDocument();
    // 地址、密钥与并发等部署参数不在托管态暴露
    expect(screen.queryByRole("spinbutton")).not.toBeInTheDocument();
    expect(API.getSystemConfig).not.toHaveBeenCalled();
  });

  it("managed mode writes the chosen model to every tier of the agent credential", async () => {
    setupBaseMocks({ credentials: [managedCredential("deepseek/deepseek-v4-flash-w8a8")] });
    const update = vi
      .spyOn(API, "updateAgentCredential")
      .mockResolvedValue(managedCredential("z-ai/glm-5.2") as never);
    const user = userEvent.setup();
    render(<AgentConfigTab visible matrixOverview={MANAGED_OVERVIEW} />);

    await user.click(await screen.findByRole("radio", { name: /GLM 5.2/ }));
    await user.click(screen.getByRole("button", { name: "保存" }));

    expect(update).toHaveBeenCalledWith(1, {
      model: "z-ai/glm-5.2",
      haiku_model: "z-ai/glm-5.2",
      sonnet_model: "z-ai/glm-5.2",
      opus_model: "z-ai/glm-5.2",
      subagent_model: "z-ai/glm-5.2",
    });
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });

  it("renders empty hint when no credentials are present", async () => {
    setupBaseMocks();
    render(<AgentConfigTab visible />);

    expect(
      await screen.findByTestId("credential-list-empty"),
    ).toBeInTheDocument();
  });

  it('shows the "+ Add credential" button in Section 1', async () => {
    setupBaseMocks();
    render(<AgentConfigTab visible />);

    // Use translated text + leading "+"
    const btn = await screen.findByRole("button", { name: /\+ 添加供应商/ });
    expect(btn).toBeInTheDocument();
  });

  it("offers the external agent guide from the page intro", async () => {
    // 本发行版的页头是单块介绍（不露出底层供应商），外部接入作为其中一个入口，
    // 而不是上游那种「内嵌 / 外部」并列双卡片。
    setupBaseMocks();
    render(<AgentConfigTab visible />);

    expect(await screen.findByText("Agent Runtime")).toBeInTheDocument();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "外部智能体接入" }));
    expect(screen.getByRole("dialog", { name: "外部智能体接入" })).toBeInTheDocument();
  });

  it("renders existing credentials in the list", async () => {
    setupBaseMocks({ credentials: [makeCredential()] });
    render(<AgentConfigTab visible />);

    expect(await screen.findByText("Anthropic 主号")).toBeInTheDocument();
    expect(
      screen.getByText(/sk-ant-\*\*\*/),
    ).toBeInTheDocument();
  });

  it("opens edit modal when edit button clicked", async () => {
    setupBaseMocks({ credentials: [makeCredential()] });
    render(<AgentConfigTab visible />);

    // 等待列表渲染
    await screen.findByText("Anthropic 主号");

    const user = userEvent.setup();
    const editBtn = screen.getByRole("button", {
      name: /edit|编辑|Chỉnh sửa/i,
    });
    await user.click(editBtn);

    // edit modal 出现，标题应为 edit_credential 翻译
    expect(
      await screen.findByRole("heading", {
        name: /edit[_ ]credential|编辑凭证|Chỉnh sửa xác thực/i,
      }),
    ).toBeInTheDocument();
  });
});
