import { ArrowUpRight, Bot, Terminal } from "lucide-react";
import { useTranslation } from "react-i18next";

import { CARD_STYLE, GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";

interface AgentPageIntroProps {
  /**
   * 「配置项兼容 Claude Code 环境变量命名」那条提示。托管态下用户没有配置项可填、
   * 也接不了别家 Coding Plan API，那句话对他们不成立。
   */
  showCompatHint?: boolean;
  /**
   * 打开外部 Agent 接入指引。**不传即托管态**：那条链路整个撤掉了（入口与令牌
   * 管理都不再提供），此时该入口不渲染——留着只会把人引到走不通的地方。
   */
  onOpenExternalGuide?: () => void;
}

export function AgentPageIntro({ showCompatHint = true, onOpenExternalGuide }: AgentPageIntroProps = {}) {
  const { t } = useTranslation("dashboard");
  return (
    <div>
      <div className="flex items-start gap-4">
        <div
          className="shrink-0 rounded-[10px] border border-hairline p-3"
          style={{
            ...CARD_STYLE,
            boxShadow: "inset 0 1px 0 oklch(1 0 0 / 0.04)",
          }}
        >
          <Bot className="h-7 w-7 text-accent-2" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
            {t("agent_intro_kicker")}
          </div>
          <h2
            className="font-editorial mt-1"
            style={{
              fontWeight: 400,
              fontSize: 24,
              lineHeight: 1.1,
              letterSpacing: "-0.012em",
              color: "var(--color-text)",
            }}
          >
            {t("arcreel_agent")}
          </h2>
          <p className="mt-1.5 text-[12.5px] leading-[1.55] text-text-3">{t("agent_sdk_desc")}</p>
          {onOpenExternalGuide && (
            <button type="button" onClick={onOpenExternalGuide} className={`${GHOST_BTN_CLS} mt-3`}>
              {t("external_agent_guide")}
              <ArrowUpRight className="h-3 w-3" aria-hidden />
            </button>
          )}
        </div>
      </div>
      {showCompatHint && (
        <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2">
          <Terminal className="mt-0.5 h-3 w-3 shrink-0 text-text-4" aria-hidden />
          <p className="text-[11.5px] leading-[1.55] text-text-3">{t("claude_code_compat_hint")}</p>
        </div>
      )}
    </div>
  );
}
