import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Type, Image as ImageIcon, Film, AudioLines } from "lucide-react";
import { API } from "@/api";
import type { MatrixOverview } from "@/types";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";

/**
 * 托管态下网关**实际供得出来**的模型清单，按媒体类型分组。
 *
 * 这里刻意不再画"连接状态 / 网关主机名 / 供应商"那套渠道卡片：托管态下网关只有
 * 一个，既选不了也换不了，把它当成一个可管理的对象展示，只会让人去找根本不存在
 * 的开关。真正有用的事实只有一件 —— 现在能用哪些模型。至于连没连上，模型清单为空
 * 本身就是答案，不需要额外一张卡片复述。
 *
 * 与上面的默认模型选择器是同一批数据的两面：那边决定"默认用哪个"（可调），
 * 这边回答"一共有哪些"（只读）。
 */

const MEDIA_META = [
  { key: "text", Icon: Type, labelKey: "matrix_media_text" },
  { key: "image", Icon: ImageIcon, labelKey: "matrix_media_image" },
  { key: "video", Icon: Film, labelKey: "matrix_media_video" },
  { key: "audio", Icon: AudioLines, labelKey: "matrix_media_audio" },
] as const;

export function GatewayModelCatalog({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const models = overview.models ?? [];

  return (
    <div className="rounded-[10px] border border-hairline p-5" style={CARD_STYLE}>
      <div className="mb-4">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          Catalog
        </div>
        <h4 className="mt-1.5 text-[14px] font-medium text-text">
          {t("dashboard:matrix_models_title")}
        </h4>
        <p className="mt-1 text-[12px] leading-[1.55] text-text-3">
          {t("dashboard:matrix_models_hint")}
        </p>
      </div>

      {models.length === 0 ? (
        <p className="py-6 text-center text-[12px] text-text-4">
          {t("dashboard:matrix_models_empty")}
        </p>
      ) : (
        <div className="space-y-4">
          {MEDIA_META.map(({ key, Icon, labelKey }) => {
            const group = models.filter((m) => m.media_type === key);
            if (group.length === 0) return null;
            return (
              <div key={key}>
                <div className="mb-1.5 flex items-center gap-2">
                  <Icon className="h-3.5 w-3.5 text-text-4" />
                  <span className="text-[12px] font-medium text-text-2">
                    {t(`dashboard:${labelKey}`)}
                  </span>
                  <span className="font-mono text-[11px] tabular-nums text-text-4">
                    {group.length}
                  </span>
                </div>
                <ul className="space-y-0.5">
                  {group.map((m) => (
                    <li
                      key={m.model_id}
                      className="flex items-baseline gap-3 rounded-[6px] px-2 py-1 hover:bg-bg-grad-a/50"
                    >
                      <span className="truncate text-[12.5px] text-text-2">{m.display_name}</span>
                      {m.display_name !== m.model_id && (
                        <span className="ml-auto truncate font-mono text-[11px] text-text-4">
                          {m.model_id}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
      )}
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
