import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import { API } from "@/api";
import { ModelCombobox } from "@/components/ui/ModelCombobox";
import { QuotaSourceBadge } from "@/components/shared/QuotaSourceBadge";
import { SectionShell } from "@/components/ui/SectionShell";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import type { AgentCredential } from "@/types/agent-credential";
import type { MatrixOverview } from "@/types";
import { errMsg } from "@/utils/async";

/**
 * 托管态下的智能体模型路由。
 *
 * 上游这一页是一套凭证 CRUD：选供应商、填代理地址、填 API 密钥、增删改、连接测试。
 * 托管态下这些**一个都不成立** —— 凭证是握手时由平台下发并 seed 好的唯一一条，
 * 地址和密钥用户既不知道也不该改，"选供应商"那一排更是选了也没用（网关只有一个）。
 * 留着它们的唯一效果是让人以为自己漏配了什么。
 *
 * 真正可调、也值得调的只有一件事：SDK 的各能力档位分别路由到网关上的哪个模型。
 * 候选直接取网关的文本模型目录，因此不需要"获取模型列表"那步（它要用户提供密钥）。
 */

interface TierField {
  key: "model" | "haiku_model" | "sonnet_model" | "opus_model" | "subagent_model";
  labelKey: string;
  captionKey: string;
}

const TIERS: TierField[] = [
  { key: "haiku_model", labelKey: "agent_tier_haiku", captionKey: "agent_tier_haiku_desc" },
  { key: "sonnet_model", labelKey: "agent_tier_sonnet", captionKey: "agent_tier_sonnet_desc" },
  { key: "opus_model", labelKey: "agent_tier_opus", captionKey: "agent_tier_opus_desc" },
  { key: "subagent_model", labelKey: "agent_tier_subagent", captionKey: "agent_tier_subagent_desc" },
];

type Draft = Record<TierField["key"], string>;

function toDraft(cred: AgentCredential): Draft {
  return {
    model: cred.model ?? "",
    haiku_model: cred.haiku_model ?? "",
    sonnet_model: cred.sonnet_model ?? "",
    opus_model: cred.opus_model ?? "",
    subagent_model: cred.subagent_model ?? "",
  };
}

export function AgentModelRouting({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [cred, setCred] = useState<AgentCredential | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saved, setSaved] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  // 候选先收窄到文本模型（Agent 走对话协议，图像/视频模型选中即失败），再收窄到
  // 服务端标了 agent_ready 的那些——工具调用链打不通的模型列出来只会让人选中即坏。
  //
  // 两条兜底不能省：
  //   1) 已保存的值即使不在名单里也留在候选中，否则用户看到的是「我配的模型不见了」，
  //      而下拉又不显示当前值，等于逼他改一个自己没打算改的设置；
  //   2) 名单与本租户目录的交集为空时不过滤——空下拉让人一个都选不了，
  //      比让他选到未验证的模型更糟。
  const options = useMemo(() => {
    const textModels = (overview.models ?? []).filter((m) => m.media_type === "text");
    const ready = textModels.filter((m) => m.agent_ready).map((m) => m.model_id);
    if (ready.length === 0) return textModels.map((m) => m.model_id);
    const savedValues = Object.values(draft ?? {}).filter((v): v is string => !!v);
    return Array.from(new Set([...ready, ...savedValues]));
  }, [overview.models, draft]);

  const quotaByModel = useMemo(() => {
    const map = new Map<string, string[] | null>();
    for (const m of overview.models ?? []) map.set(m.model_id, m.quota_sources ?? null);
    return map;
  }, [overview.models]);

  const renderBadge = useCallback(
    (option: string) => {
      const sources = quotaByModel.get(option);
      if (sources === undefined || sources === null) return null;
      return <QuotaSourceBadge quotaSources={sources} />;
    },
    [quotaByModel],
  );

  const load = useCallback(async () => {
    try {
      const { credentials } = await API.listAgentCredentials();
      // 托管态恒是一条（握手时 seed）。真出现多条时取生效的那条，不在这里做取舍。
      const active = credentials.find((c) => c.is_active) ?? credentials[0] ?? null;
      setCred(active);
      const d = active ? toDraft(active) : null;
      setDraft(d);
      setSaved(d);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  const isDirty =
    draft != null && saved != null && (Object.keys(draft) as TierField["key"][]).some((k) => draft[k] !== saved[k]);

  const handleSave = async () => {
    if (cred == null || draft == null) return;
    setSaving(true);
    try {
      await API.updateAgentCredential(cred.id, { ...draft });
      setSaved(draft);
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

  if (cred == null || draft == null) {
    // 平台没 seed 出凭证：这是握手侧的问题，用户在这一页做不了任何补救，
    // 所以只说明现状并指路，不给一个填了也不生效的表单。
    return (
      <SectionShell kicker="Model Routing" title={t("dashboard:agent_models")}>
        <p className="text-[12.5px] text-text-3">{t("dashboard:agent_credential_missing")}</p>
      </SectionShell>
    );
  }

  return (
    <SectionShell
      kicker="Model Routing"
      title={t("dashboard:agent_models")}
      description={t("dashboard:agent_models_desc")}
    >
      <div className="space-y-4">
        <div>
          <label
            htmlFor="agent-default-model"
            className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
          >
            {t("dashboard:agent_tier_default")}
          </label>
          <ModelCombobox
            id="agent-default-model"
            value={draft.model}
            onChange={(v) => setDraft((p) => (p ? { ...p, model: v } : p))}
            options={options}
            renderOptionBadge={renderBadge}
            aria-label={t("dashboard:agent_tier_default")}
            clearable
          />
          <p className="mt-1 text-[11px] text-text-4">{t("dashboard:agent_tier_default_desc")}</p>
        </div>

        {TIERS.map(({ key, labelKey, captionKey }) => (
          <div key={key}>
            <label
              htmlFor={`agent-${key}`}
              className="mb-1.5 block font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4"
            >
              {t(`dashboard:${labelKey}`)}
            </label>
            <ModelCombobox
              id={`agent-${key}`}
              value={draft[key]}
              onChange={(v) => setDraft((p) => (p ? { ...p, [key]: v } : p))}
              options={options}
              renderOptionBadge={renderBadge}
              placeholder={t("dashboard:agent_tier_inherit")}
              aria-label={t(`dashboard:${labelKey}`)}
              clearable
            />
            <p className="mt-1 text-[11px] text-text-4">{t(`dashboard:${captionKey}`)}</p>
          </div>
        ))}

        {isDirty && (
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
              onClick={() => setDraft(saved)}
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
