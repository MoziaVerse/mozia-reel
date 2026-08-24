import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Router } from "wouter";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { FailureObservation } from "@/types";
import { copyText } from "@/utils/clipboard";
import { AgentFailureCard } from "./AgentFailureCard";

vi.mock("@/utils/clipboard", () => ({
  copyText: vi.fn().mockResolvedValue(undefined),
}));

const turnFailure: FailureObservation = {
  version: 1,
  phase: "turn",
  timestamp: "2026-07-23T01:02:03Z",
  project_name: "demo",
  session_id: "session-1",
  summary: {
    source: "sdk_assistant",
    type: "invalid_request",
    status: 403,
    message: "There's an issue with the selected model (gpt-5.6-sol).",
  },
  raw: {
    assistant_message: {
      error: "invalid_request",
      content: [{ type: "text", text: "There's an issue with the selected model (gpt-5.6-sol)." }],
      upstream_unknown: { reason_code: "vendor-17" },
    },
  },
};

describe("AgentFailureCard", () => {
  beforeEach(() => {
    vi.mocked(copyText).mockResolvedValue(undefined);
  });

  it("shows observed facts, preserves raw details, and has no log download action", async () => {
    render(<AgentFailureCard failure={turnFailure} />);

    expect(screen.getByRole("alert")).toHaveTextContent("智能体本轮运行失败");
    expect(screen.getByText("以下为系统实际观测信息，不等同于问题根因。")).toBeInTheDocument();
    expect(screen.getByText("sdk_assistant")).toBeInTheDocument();
    expect(screen.getByText("invalid_request")).toBeInTheDocument();
    expect(screen.getByText("403")).toBeInTheDocument();
    expect(screen.getByText(turnFailure.summary.message!)).toBeInTheDocument();

    const details = screen.getByText("完整观测信息").closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("完整观测信息"));
    expect(details).toHaveAttribute("open");
    expect(screen.getByTestId("failure-observation-json")).toHaveTextContent("vendor-17");

    fireEvent.click(screen.getByRole("button", { name: "复制观测信息" }));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith(JSON.stringify(turnFailure, null, 2));
    });

    expect(screen.getByRole("link", { name: "打开智能体设置" }))
      .toHaveAttribute("href", "/app/settings?section=agent");
    expect(screen.queryByText(/下载.*日志/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
  });

  it("在工作区嵌套路由下，设置入口仍指向站点根的设置页", () => {
    // 本卡片实际渲染在 router.tsx 的 `/app/projects/:projectName` nest 路由内。
    // nest 里的 href 是相对 base 解析的，写成 "/app/settings" 会得到
    // /app/projects/demo/app/settings —— 用户点了跳进 404。带 `~` 才跳出 base。
    render(
      <Router base="/app/projects/demo">
        <AgentFailureCard failure={turnFailure} />
      </Router>,
    );

    expect(screen.getByRole("link", { name: "打开智能体设置" }))
      .toHaveAttribute("href", "/app/settings?section=agent");
  });

  it("offers retry only when the caller supplies a startup retry", () => {
    const onRetry = vi.fn();
    render(<AgentFailureCard failure={{ ...turnFailure, phase: "startup" }} onRetry={onRetry} />);

    fireEvent.click(screen.getByRole("button", { name: "重试启动" }));

    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert")).toHaveTextContent("智能体启动失败");
  });
});
