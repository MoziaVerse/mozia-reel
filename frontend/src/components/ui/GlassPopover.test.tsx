import { useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GlassPopover } from "./GlassPopover";

class MockResizeObserver {
  observe() {}
  disconnect() {}
  unobserve() {}
}

function Harness() {
  const anchorRef = useRef<HTMLButtonElement>(null);
  return (
    <div>
      <button ref={anchorRef} type="button">
        anchor
      </button>
      <GlassPopover open anchorRef={anchorRef}>
        <div data-testid="panel-content">hello</div>
      </GlassPopover>
    </div>
  );
}

describe("GlassPopover", () => {
  beforeEach(() => {
    vi.stubGlobal("ResizeObserver", MockResizeObserver);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("leaves background-color to .arc-glass-panel instead of writing it inline", () => {
    // 面板底色定义在 .arc-glass-panel 里。行内样式压过类选择器，一旦 Popover
    // 往行内写 background-color（历史上写的是 "transparent"），底色就被盖掉，
    // 面板全透明、底下的界面直接透出来。
    render(<Harness />);
    const panel = screen.getByTestId("panel-content").parentElement!;
    expect(panel.className).toContain("arc-glass-panel");
    expect(panel.style.backgroundColor).toBe("");
  });
});
