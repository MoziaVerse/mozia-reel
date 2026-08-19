import { useTranslation } from "react-i18next";
import { PHASE_ORDER } from "@/types";

interface PhaseStepperProps {
  currentPhase: string | undefined;
}

/**
 * 顶栏阶段步进器：胶囊样式（圆形号 + 标签 + 短分隔线）。
 * 当前阶段高亮 accent 紫色，已完成阶段显示弱化的连接线。
 */
export function PhaseStepper({ currentPhase }: PhaseStepperProps) {
  const { t } = useTranslation("dashboard");
  const currentIdx = PHASE_ORDER.findIndex((p) => p === currentPhase);

  return (
    <nav aria-label={t("workflow_phases")}>
      <div
        className="inline-flex items-center gap-px rounded-full p-[3px]"
        style={{
          background: "light-dark(oklch(0.995 0.004 260 / 0.6), oklch(0.15 0.02 260 / 0.6))",
          border: "1px solid var(--color-hairline)",
          boxShadow: "inset 0 1px 2px oklch(0 0 0 / 0.25)",
        }}
      >
        {PHASE_ORDER.map((phase, idx) => {
          const isActive = currentIdx === idx;
          const isPastOrActive = currentIdx >= 0 && currentIdx >= idx;
          const nextIsActive = currentIdx === idx + 1;
          return (
            <div key={phase} className="flex items-center">
              <div
                aria-current={isActive ? "step" : undefined}
                className="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors"
                style={
                  isActive
                    ? {
                        color: "var(--color-text)",
                        background: "linear-gradient(180deg, light-dark(oklch(0.88 0.012 260), oklch(0.28 0.03 260)), light-dark(oklch(0.91 0.012 260), oklch(0.24 0.03 260)))",
                        boxShadow:
                          "0 0 0 1px var(--color-hairline-strong), 0 1px 2px oklch(0 0 0 / 0.3)",
                      }
                    : { color: "var(--color-text-3)", background: "transparent" }
                }
              >
                <span
                  className="num inline-grid h-[15px] w-[15px] place-items-center rounded-full text-[10px] font-bold"
                  style={
                    isActive
                      ? {
                          background: "var(--color-accent)",
                          color: "oklch(0.12 0 0)",
                          boxShadow: "0 0 8px -1px var(--color-accent-glow)",
                        }
                      : {
                          background: "light-dark(oklch(0.88 0.012 260), oklch(0.3 0.03 260))",
                          color: "var(--color-text-3)",
                        }
                  }
                >
                  {idx + 1}
                </span>
                <span className="whitespace-nowrap">{t(`phase_${phase}`)}</span>
              </div>
              {idx < PHASE_ORDER.length - 1 && (
                <div
                  aria-hidden="true"
                  className="mx-0.5 h-px w-1.5"
                  style={{
                    background:
                      isPastOrActive || nextIsActive
                        ? "var(--color-accent-soft)"
                        : "var(--color-hairline-soft)",
                  }}
                />
              )}
            </div>
          );
        })}
      </div>
    </nav>
  );
}
