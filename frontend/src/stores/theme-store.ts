import { create } from "zustand";

/**
 * 主题模式。`system` 跟随操作系统偏好——Matrix 平台侧的默认值也是 system，
 * 所以在多数情况下本应用与平台的明暗会自然一致。
 */
export type ThemeMode = "system" | "light" | "dark";

/** 已解析出的实际外观，`system` 经系统偏好折算后只剩这两种。 */
export type ResolvedTheme = "light" | "dark";

/**
 * localStorage 键名。index.html 的首屏内联脚本读同一个键，改名时两处必须同改，
 * 否则首屏按默认主题渲染、随后被 store 纠正，会闪一下。
 */
export const THEME_STORAGE_KEY = "arcreel-theme";

const MODES: readonly ThemeMode[] = ["system", "light", "dark"];

function isThemeMode(value: unknown): value is ThemeMode {
  return typeof value === "string" && (MODES as readonly string[]).includes(value);
}

/**
 * 系统是否偏好深色。store 在模块加载时就要解析初始主题，那一刻不保证身处
 * 完整的浏览器环境（测试的 jsdom、任何预渲染场景都可能没有 matchMedia），
 * 故做特性检测——取不到偏好时按浅色处置，与 :root 的默认变量集一致。
 */
function prefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

export function resolveTheme(mode: ThemeMode): ResolvedTheme {
  if (mode === "system") return prefersDark() ? "dark" : "light";
  return mode;
}

/**
 * 落到 DOM 上。`.dark` 类驱动 Tailwind 的 dark variant 与 index.css 的深色变量集；
 * `color-scheme` 由 index.css 依 `.dark` 联动，同时是 CSS `light-dark()` 的判定依据，
 * 组件里散落的一次性色值靠它切换。
 */
function applyToDocument(resolved: ResolvedTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("dark", resolved === "dark");
}

function readStoredMode(): ThemeMode {
  try {
    if (typeof localStorage === "undefined") return "system";
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeMode(raw) ? raw : "system";
  } catch {
    // 隐私模式 / 存储被禁用：退回跟随系统，不因此让主题功能整体失效
    return "system";
  }
}

interface ThemeState {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  /** 设定模式并立即生效、写盘。 */
  setMode: (mode: ThemeMode) => void;
  /** 在 system → light → dark 之间轮换，供单按钮切换器使用。 */
  cycleMode: () => void;
  /**
   * 订阅系统偏好变化。仅 `mode === "system"` 时才跟随。
   * 返回取消订阅函数，供调用方在卸载时释放。
   */
  initSystemWatcher: () => () => void;
}

const initialMode = readStoredMode();

export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: initialMode,
  resolved: resolveTheme(initialMode),

  setMode: (mode) => {
    const resolved = resolveTheme(mode);
    applyToDocument(resolved);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // 写盘失败只影响下次冷启动的记忆，本次切换照常生效
    }
    set({ mode, resolved });
  },

  cycleMode: () => {
    const idx = MODES.indexOf(get().mode);
    get().setMode(MODES[(idx + 1) % MODES.length]);
  },

  initSystemWatcher: () => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return () => {};
    }
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      if (get().mode !== "system") return;
      const resolved = resolveTheme("system");
      applyToDocument(resolved);
      set({ resolved });
    };
    media.addEventListener("change", onChange);
    return () => {
      media.removeEventListener("change", onChange);
    };
  },
}));
