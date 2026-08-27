import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import { WelcomeCanvas } from "@/components/canvas/WelcomeCanvas";
import { API } from "@/api";
import i18n from "@/i18n";
import { useAppStore } from "@/stores/app-store";

describe("WelcomeCanvas", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("shows the project title instead of the internal project name", async () => {
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });

    render(
      <WelcomeCanvas
        projectName="halou-92d19a04"
        projectTitle="哈喽项目"
      />,
    );

    expect(await screen.findByText("欢迎来到 哈喽项目！")).toBeInTheDocument();
    expect(screen.queryByText("欢迎来到 halou-92d19a04！")).not.toBeInTheDocument();
  });
});

function renderWelcome(props: Partial<Parameters<typeof WelcomeCanvas>[0]>) {
  return render(
    <I18nextProvider i18n={i18n}>
      <WelcomeCanvas
        projectName="p"
        onUpload={props.onUpload ?? vi.fn().mockResolvedValue(undefined)}
        onAnalyze={props.onAnalyze ?? vi.fn().mockResolvedValue(undefined)}
        {...props}
      />
    </I18nextProvider>,
  );
}

describe("WelcomeCanvas auto-analyze on first upload", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("triggers onAnalyze automatically after first upload from idle", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "novel.txt", { type: "text/plain" });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
    await waitFor(() => expect(onAnalyze).toHaveBeenCalledTimes(1));
  });

  it("does NOT auto-trigger analyze when uploading from has_sources", async () => {
    vi.spyOn(API, "listFiles").mockResolvedValue({
      files: { source: [{ name: "existing.txt", size: 10, url: "/x" }] },
    });
    const onUpload = vi.fn().mockResolvedValue(undefined);
    const onAnalyze = vi.fn();
    renderWelcome({ onUpload, onAnalyze });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "second.docx");
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalled());
    expect(onAnalyze).not.toHaveBeenCalled();
  });
});

describe("WelcomeCanvas accept extension", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("accepts .docx, .epub, .pdf in input accept attribute", async () => {
    renderWelcome({});
    const input = (await screen.findByLabelText(/upload|上传/i)) as HTMLInputElement;
    expect(input.accept).toContain(".docx");
    expect(input.accept).toContain(".epub");
    expect(input.accept).toContain(".pdf");
  });
});

describe("WelcomeCanvas unsupported file validation", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({ files: { source: [] } });
  });

  it("shows an error and does not upload when an unsupported file is dropped", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload });

    const dropZone = (await screen.findByText("拖拽文件到此处")).closest("button");
    expect(dropZone).not.toBeNull();
    const file = new File(["x"], "cover.png", { type: "image/png" });
    fireEvent.drop(dropZone as HTMLElement, { dataTransfer: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("不支持的文件类型：cover.png");
    expect(onUpload).not.toHaveBeenCalled();
  });

  it("shows an error and does not upload when an unsupported file is picked", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload });

    const input = await screen.findByLabelText(/upload|上传/i);
    const file = new File(["x"], "cover.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(await screen.findByRole("alert")).toHaveTextContent("不支持的文件类型：cover.png");
    expect(onUpload).not.toHaveBeenCalled();
  });

  it("uploads when a supported file is dropped", async () => {
    const onUpload = vi.fn().mockResolvedValue(undefined);
    renderWelcome({ onUpload });

    const dropZone = (await screen.findByText("拖拽文件到此处")).closest("button");
    const file = new File(["x"], "novel.txt", { type: "text/plain" });
    fireEvent.drop(dropZone as HTMLElement, { dataTransfer: { files: [file] } });

    await waitFor(() => expect(onUpload).toHaveBeenCalledWith(file));
  });
});

describe("WelcomeCanvas 分析进行态跨卸载存活", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.spyOn(API, "listFiles").mockResolvedValue({
      files: { source: [{ name: "1.txt", size: 1, url: "/s/1.txt" }] },
    });
  });

  it("重新挂载时按 store 的进行态直接回到 analyzing，而不是退回 CTA", async () => {
    // 分析请求不带 signal，切走后仍在后端跑完。若退回 CTA，用户会以为中断了，
    // 再点一次就是重复付费。
    useAppStore.getState().setProjectAnalyzing("p", true);

    renderWelcome({});

    expect(await screen.findByText(/AI 正在分析小说内容/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /开始 AI 分析/ })).not.toBeInTheDocument();
  });

  it("未在分析的项目不受影响，照常显示 CTA", async () => {
    useAppStore.getState().setProjectAnalyzing("other-project", true);

    renderWelcome({});

    expect(await screen.findByRole("button", { name: /开始 AI 分析/ })).toBeInTheDocument();
  });

  it("重新挂载后分析结束时接管收尾", async () => {
    useAppStore.getState().setProjectAnalyzing("p", true);
    renderWelcome({});
    expect(await screen.findByText(/AI 正在分析小说内容/)).toBeInTheDocument();

    // 原来那次 startAnalysis 的 await 属于已卸载的实例，没人再把 phase 推到 done
    useAppStore.getState().setProjectAnalyzing("p", false);

    await waitFor(() => {
      expect(screen.getByText("分析完成！正在加载项目概述...")).toBeInTheDocument();
    });
  });
});
