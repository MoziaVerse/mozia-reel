import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useThemeStore, type ThemeMode } from "@/stores/theme-store";

const ICONS: Record<ThemeMode, typeof Sun> = {
  system: Monitor,
  light: Sun,
  dark: Moon,
};

const LABEL_KEYS: Record<ThemeMode, string> = {
  system: "theme_system",
  light: "theme_light",
  dark: "theme_dark",
};

interface ThemeToggleProps {
  /**
   * `compact` 渲染成纯图标按钮，供 GlobalHeader 那排图标使用；
   * 默认形态带文字，与设置页顶栏的语言切换器并排时视觉一致。
   */
  compact?: boolean;
  className?: string;
}

/**
 * 主题切换：单按钮在 跟随系统 → 浅色 → 深色 之间轮换，与语言切换器同一交互口径。
 * 图标反映当前**模式**而非解析结果——跟随系统时显示显示器图标，用户才分得清
 * 「现在是深色」和「跟随系统且系统恰好是深色」。
 */
export function ThemeToggle({ compact = false, className = "" }: ThemeToggleProps) {
  const { t } = useTranslation("dashboard");
  const mode = useThemeStore((s) => s.mode);
  const cycleMode = useThemeStore((s) => s.cycleMode);

  const Icon = ICONS[mode];
  const label = t(LABEL_KEYS[mode]);

  if (compact) {
    return (
      <button
        type="button"
        onClick={cycleMode}
        className={`grid h-[30px] w-[30px] place-items-center rounded-md transition-colors focus-ring ${className}`}
        style={{ color: "var(--color-text-3)" }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background =
            "light-dark(oklch(0.91 0.012 260 / 0.6), oklch(0.26 0.03 260 / 0.6))";
          e.currentTarget.style.color = "var(--color-text)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = "transparent";
          e.currentTarget.style.color = "var(--color-text-3)";
        }}
        title={`${t("theme_setting")} · ${label}`}
        aria-label={t("theme_setting")}
      >
        <Icon className="h-4 w-4" aria-hidden />
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={cycleMode}
      className={`inline-flex items-center gap-2 rounded-md border border-hairline-soft bg-bg-grad-a/45 px-2.5 py-1.5 text-[12px] text-text-3 transition-colors hover:border-hairline hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${className}`}
      title={label}
      aria-label={t("theme_setting")}
    >
      <Icon className="h-3.5 w-3.5" aria-hidden />
      <span className="font-mono text-[10.5px] font-bold uppercase tracking-[0.14em]">{label}</span>
    </button>
  );
}
