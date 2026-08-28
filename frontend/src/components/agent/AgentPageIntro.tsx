import ClaudeColor from "@lobehub/icons/es/Claude/components/Color";
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
   * 管理都不再提供），此时整张外部 Agent 卡片不渲染——留着只会把人引到走不通的
   * 地方。
   */
  onOpenExternalGuide?: () => void;
}

export function AgentPageIntro({ showCompatHint = true, onOpenExternalGuide }: AgentPageIntroProps = {}) {
  const { t } = useTranslation("dashboard");
  return (
    <section aria-labelledby="agent-access-title">
      <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-accent-2">
        {t("agent_access_kicker")}
      </div>
      <h2 id="agent-access-title" className="font-editorial mt-1 text-2xl text-text">
        {t("agent_access_title")}
      </h2>
      <p className="mt-1.5 max-w-2xl text-[12.5px] leading-[1.55] text-text-3">
        {t("agent_access_desc")}
      </p>

      <div
        className={`mt-4 grid overflow-hidden rounded-[10px] border border-hairline ${onOpenExternalGuide ? "sm:grid-cols-2" : ""}`}
        style={CARD_STYLE}
      >
        <div
          className={`flex gap-3.5 p-4 ${onOpenExternalGuide ? "border-b border-hairline-soft sm:border-b-0 sm:border-r" : ""}`}
        >
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-[8px] border border-hairline-soft bg-bg-grad-a/55">
            <ClaudeColor size={22} />
          </div>
          <div className="min-w-0">
            <h3 className="text-[13.5px] font-medium text-text">{t("embedded_agent")}</h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-text-3">
              {t("embedded_agent_desc")}
            </p>
          </div>
        </div>

        {onOpenExternalGuide && (
        <div className="flex gap-3.5 p-4">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-[8px] border border-accent/25 bg-accent-dim text-accent-2">
            <Bot className="h-4 w-4" aria-hidden />
          </div>
          <div className="min-w-0">
            <h3 className="text-[13.5px] font-medium text-text">{t("external_agent")}</h3>
            <p className="mt-1 text-[11.5px] leading-[1.55] text-text-3">
              {t("external_agent_desc")}
            </p>
            <button
              type="button"
              onClick={onOpenExternalGuide}
              className={`${GHOST_BTN_CLS} mt-3`}
            >
              {t("external_agent_guide")}
              <ArrowUpRight className="h-3 w-3" aria-hidden />
            </button>
          </div>
        </div>
        )}
      </div>
      {showCompatHint && (
        <div className="mt-3 flex items-start gap-2 rounded-[8px] border border-hairline-soft bg-bg-grad-a/45 px-3 py-2">
          <Terminal className="mt-0.5 h-3 w-3 shrink-0 text-text-4" aria-hidden />
          <p className="text-[11.5px] leading-[1.55] text-text-3">
            {t("claude_code_compat_hint")}
          </p>
        </div>
      )}
    </section>
  );
}
