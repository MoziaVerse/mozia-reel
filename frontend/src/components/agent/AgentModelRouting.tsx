import { useTranslation } from "react-i18next";
import { QuotaSourceBadge } from "@/components/shared/QuotaSourceBadge";
import { SectionShell } from "@/components/ui/SectionShell";
import type { MatrixOverview } from "@/types";

export function AgentModelRouting({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation("dashboard");
  const model = overview.models?.find((item) => item.model_id === "z-ai/glm-5.2");

  return (
    <SectionShell kicker="Agent Model" title={t("agent_fixed_model")} description={t("agent_fixed_model_desc")}>
      <div className="flex items-center gap-3 text-sm text-text">
        <span>GLM 5.2</span>
        {model?.quota_sources && <QuotaSourceBadge quotaSources={model.quota_sources} />}
      </div>
    </SectionShell>
  );
}
