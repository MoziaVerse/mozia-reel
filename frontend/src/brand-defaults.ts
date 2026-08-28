// Upstream brand defaults — single source of truth shared by the runtime
// (src/branding.ts) and the build (vite.config.ts, which rewrites index.html).
//
// Kept free of `import.meta.env` on purpose: vite.config.ts imports this module
// in Node, where `import.meta.env` does not exist. Environment overrides are
// applied by each consumer, not here.
//
// 上游品牌默认值的唯一真相源，运行时（src/branding.ts）与构建期
// （vite.config.ts 重写 index.html）共用，避免两处各写一份而漂移。
// 本模块刻意不碰 `import.meta.env`——vite.config.ts 在 Node 侧导入它，
// 那里没有 `import.meta.env`；环境变量覆盖由各自的调用方施加。

export const BRAND_DEFAULTS = {
  name: "ArcReel",
  tagline: "Agent 驱动的 AI 视频创作工作台",
  description: "AI 视频创作工作台，统一管理项目、脚本、分镜、视频生成与 Agent 对话。",
  /** AGPL-3.0 §13：默认指向上游仓库；修改版须经 VITE_SOURCE_URL 覆盖。 */
  sourceUrl: "https://github.com/ArcReel/ArcReel",
} as const;

/** 浏览器标签标题的组装规则，运行时与构建期须一致。 */
export function brandDocumentTitle(name: string, tagline: string): string {
  return `${name} · ${tagline}`;
}
