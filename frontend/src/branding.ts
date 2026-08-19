// Brand configuration — single source of truth for product naming.
// Override at build time via Vite env vars
// (VITE_BRAND_NAME / VITE_BRAND_TAGLINE / VITE_BRAND_DESCRIPTION).
//
// Source code references BRAND.name (or the [[brand]] placeholder in i18n
// resources) so the displayed product name is not hardcoded across files.
// Defaults preserve the upstream brand; downstream distributions can override
// via frontend/.env without code changes.

import { BRAND_DEFAULTS, brandDocumentTitle } from "@/brand-defaults";

const env = import.meta.env as Record<string, string | undefined>;

function fallback(value: string | undefined, defaultValue: string): string {
  // Trim + empty check so VITE_BRAND_NAME="" (or whitespace) falls back to the
  // default, matching the documented "Empty = upstream defaults" contract.
  if (typeof value !== "string") return defaultValue;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : defaultValue;
}

// AGPL-3.0 §13：以网络服务形态提供本程序时，必须让使用者能取得本运行版本对应的
// 完整源代码。默认指向上游仓库（未修改的部署由此即已合规）；**修改版发行时必须**
// 经 VITE_SOURCE_URL 覆盖为自己那份改动的公开地址，否则处于违约状态。
export const SOURCE_URL = fallback(env.VITE_SOURCE_URL, BRAND_DEFAULTS.sourceUrl);

export const BRAND = {
  name: fallback(env.VITE_BRAND_NAME, BRAND_DEFAULTS.name),
  tagline: fallback(env.VITE_BRAND_TAGLINE, BRAND_DEFAULTS.tagline),
  description: fallback(env.VITE_BRAND_DESCRIPTION, BRAND_DEFAULTS.description),
} as const;

export const BRAND_DOCUMENT_TITLE = brandDocumentTitle(BRAND.name, BRAND.tagline);

