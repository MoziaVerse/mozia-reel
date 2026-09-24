import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import { API } from "@/api";
import { QuotaSourceBadge } from "@/components/shared/QuotaSourceBadge";
import { SectionShell } from "@/components/ui/SectionShell";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import type { AgentCredential } from "@/types/agent-credential";
import type { MatrixOverview } from "@/types";
import { errMsg } from "@/utils/async";

/**
 * 托管态下的智能体模型选择。
 *
 * 候选只取服务端标了 agent_ready 的文本模型（验证过能跑通智能体工具链的档位）；
 * 选中的模型写进托管凭证的全部档位，主会话与子智能体一致。未选或存量值不在候选
 * 里时，服务端实际使用 overview.agent_default_model（可消耗赠送额度的档位）。
 */
export function AgentModelRouting({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [cred, setCred] = useState<AgentCredential | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [saved, setSaved] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const candidates = useMemo(
    () => (overview.models ?? []).filter((m) => m.media_type === "text" && m.agent_ready),
    [overview.models],
  );
  const defaultModel = overview.agent_default_model ?? "";

  const load = useCallback(async () => {
    try {
      const { credentials } = await API.listAgentCredentials();
      // 托管态恒是一条（握手时 seed）。真出现多条时取生效的那条，不在这里做取舍。
      const active = credentials.find((c) => c.is_active) ?? credentials[0] ?? null;
      setCred(active);
      const stored = active?.model ?? "";
      const effective = candidates.some((m) => m.model_id === stored) ? stored : defaultModel;
      setSelected(effective);
      setSaved(effective);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setLoading(false);
    }
  }, [candidates, defaultModel]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const handleSave = async () => {
    if (cred == null || !selected) return;
    setSaving(true);
    try {
      await API.updateAgentCredential(cred.id, {
        model: selected,
        haiku_model: selected,
        sonnet_model: selected,
        opus_model: selected,
        subagent_model: selected,
      });
      setSaved(selected);
      useAppStore.getState().pushToast(t("dashboard:agent_config_saved"), "success");
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <Loader2 className="h-4 w-4 animate-spin text-text-3" aria-hidden />
      </div>
    );
  }

  if (cred == null) {
    // 平台没 seed 出凭证：握手侧的问题，用户在这一页做不了补救，只说明现状并指路。
    return (
      <SectionShell kicker="Agent Model" title={t("dashboard:agent_fixed_model")}>
        <p className="text-[12.5px] text-text-3">{t("dashboard:agent_credential_missing")}</p>
      </SectionShell>
    );
  }

  return (
    <SectionShell
      kicker="Agent Model"
      title={t("dashboard:agent_fixed_model")}
      description={t("dashboard:agent_fixed_model_desc")}
    >
      <div className="space-y-4">
        <div role="radiogroup" aria-label={t("dashboard:agent_fixed_model")} className="flex flex-col gap-2">
          {candidates.map((model) => {
            const checked = selected === model.model_id;
            return (
              <label
                key={model.model_id}
                className={`flex cursor-pointer items-center gap-3 rounded-[8px] border px-3 py-2 text-sm text-text transition-colors ${
                  checked ? "border-accent bg-bg-grad-a" : "border-hairline hover:border-hairline-strong"
                }`}
              >
                <input
                  type="radio"
                  name="agent-model"
                  value={model.model_id}
                  checked={checked}
                  onChange={() => setSelected(model.model_id)}
                  className="accent-[var(--color-accent)]"
                />
                <span>{model.display_name}</span>
                {model.model_id === defaultModel && (
                  <span className="text-[11px] text-text-4">{t("dashboard:agent_model_default_tag")}</span>
                )}
                {model.quota_sources && <QuotaSourceBadge quotaSources={model.quota_sources} />}
              </label>
            );
          })}
        </div>

        {selected !== saved && (
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className={ACCENT_BTN_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {saving ? <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden /> : null}
              {saving ? t("common:saving") : t("common:save")}
            </button>
            <button
              type="button"
              onClick={() => setSelected(saved)}
              className="rounded-[8px] border border-hairline bg-bg-grad-a/55 px-4 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {t("common:reset")}
            </button>
          </div>
        )}
      </div>
    </SectionShell>
  );
}
