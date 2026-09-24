import { useTranslation } from "react-i18next";
import { QuotaSourceBadge } from "@/components/shared/QuotaSourceBadge";
import { SectionShell } from "@/components/ui/SectionShell";
import type { MatrixOverview } from "@/types";

const PAID_AGENT_MODEL = "z-ai/glm-5.2";

export function AgentModelRouting({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation("dashboard");
  const paidModel = overview.models?.find((item) => item.model_id === PAID_AGENT_MODEL);
  // 只剩赠送额度时服务端改用的档位；具体型号可由部署切换，这里按 agent_ready 展示。
  const giftModels = overview.models?.filter((item) => item.agent_ready && item.model_id !== PAID_AGENT_MODEL) ?? [];

  return (
    <SectionShell kicker="Agent Model" title={t("agent_fixed_model")} description={t("agent_fixed_model_desc")}>
      <div className="flex flex-col gap-2 text-sm text-text">
        <div className="flex items-center gap-3">
          <span>GLM 5.2</span>
          {paidModel?.quota_sources && <QuotaSourceBadge quotaSources={paidModel.quota_sources} />}
        </div>
        {giftModels.map((model) => (
          <div key={model.model_id} className="flex items-center gap-3">
            <span>{model.display_name}</span>
            {model.quota_sources && <QuotaSourceBadge quotaSources={model.quota_sources} />}
          </div>
        ))}
      </div>
    </SectionShell>
  );
}
