import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, CheckCircle2, AlertTriangle, ExternalLink, Type, Image as ImageIcon, Film, AudioLines } from "lucide-react";
import { API } from "@/api";
import type { MatrixOverview } from "@/types";

/**
 * 托管态下的「模型服务」页。
 *
 * 这里刻意**没有任何可编辑项**：网关地址与密钥由 Matrix 在握手时下发，模型目录
 * 由平台上架，计费也在平台侧。给出一个填了不生效的表单，只会让人以为配错了而
 * 反复折腾。所以这一页只回答三个问题：连上了没、能用哪些模型、去哪管账户。
 */

const MEDIA_META = [
  { key: "text", Icon: Type, labelKey: "matrix_media_text" },
  { key: "image", Icon: ImageIcon, labelKey: "matrix_media_image" },
  { key: "video", Icon: Film, labelKey: "matrix_media_video" },
  { key: "audio", Icon: AudioLines, labelKey: "matrix_media_audio" },
] as const;

export function MatrixGatewaySection({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const counts = overview.media_counts ?? {};
  const models = overview.models ?? [];

  return (
    <div className="space-y-6">
      {/* 连接状态 */}
      <section className="rounded-xl border border-border bg-card p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {overview.connected ? (
                <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
              ) : (
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-500" />
              )}
              <h3 className="truncate text-sm font-medium">
                {overview.connected ? t("dashboard:matrix_connected") : t("dashboard:matrix_disconnected")}
              </h3>
            </div>
            <p className="mt-1.5 text-xs text-muted-foreground">
              {overview.connected
                ? t("dashboard:matrix_connected_hint")
                : t("dashboard:matrix_disconnected_hint")}
            </p>
            {overview.gateway_host && (
              <p className="mt-2 font-mono text-xs text-muted-foreground/80">{overview.gateway_host}</p>
            )}
          </div>
          {overview.matrix_web_url && (
            <a
              href={overview.matrix_web_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-accent"
            >
              {t("dashboard:matrix_open_console")}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </section>

      {/* 各媒体类型可用模型数 */}
      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {MEDIA_META.map(({ key, Icon, labelKey }) => (
          <div key={key} className="rounded-xl border border-border bg-card p-4">
            <Icon className="h-4 w-4 text-muted-foreground" />
            <p className="mt-2 text-2xl font-semibold tabular-nums">{counts[key] ?? 0}</p>
            <p className="text-xs text-muted-foreground">{t(`dashboard:${labelKey}`)}</p>
          </div>
        ))}
      </section>

      {/* 模型清单（只读） */}
      <section className="rounded-xl border border-border bg-card">
        <div className="border-b border-border px-5 py-3">
          <h3 className="text-sm font-medium">{t("dashboard:matrix_models_title")}</h3>
          <p className="mt-1 text-xs text-muted-foreground">{t("dashboard:matrix_models_hint")}</p>
        </div>
        {models.length === 0 ? (
          <p className="px-5 py-8 text-center text-xs text-muted-foreground">
            {t("dashboard:matrix_models_empty")}
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {models.map((m) => (
              <li key={`${m.media_type}:${m.model_id}`} className="flex items-center gap-3 px-5 py-2.5">
                <span className="w-14 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">
                  {m.media_type}
                </span>
                <span className="truncate text-sm">{m.display_name}</span>
                <span className="ml-auto truncate font-mono text-xs text-muted-foreground/70">{m.model_id}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** 拉取托管态总览；enabled=false 时由调用方回退到原供应商界面。 */
export function useMatrixOverview() {
  const [overview, setOverview] = useState<MatrixOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    API.getMatrixOverview()
      .then((data) => {
        if (alive) setOverview(data);
      })
      .catch(() => {
        // 拿不到就按独立部署处理：宁可多显示配置项，也不要把设置页整个变空白。
        if (alive) setOverview({ enabled: false });
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return { overview, loading };
}

export function MatrixSectionLoading() {
  return (
    <div className="flex items-center justify-center py-16">
      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    </div>
  );
}
