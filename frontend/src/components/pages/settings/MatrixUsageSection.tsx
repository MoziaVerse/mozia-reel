import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";

import { API } from "@/api";
import { CARD_STYLE } from "@/components/ui/darkroom-tokens";
import type { MatrixUsageItem } from "@/types";
import { errMsg } from "@/utils/async";

/**
 * 托管态用量页。
 *
 * 数据取自**平台账务**而非本地账本：本地记的是"我们以为花了多少"（按模型单价估算），
 * 平台记的是"实际扣了多少"。两者对不上时用户信的当然是账单那一份，页面上并排两套
 * 数字只会让人怀疑哪个都不准。
 *
 * 性质必须分开统计。上游把消费、失败、退款混在一条流水里，而失败记录 quota=0——
 * 不区分的话，一串失败在页面上就是一串「0 消耗」，看起来像什么都没发生。
 */

/** 一次拉多少页。够覆盖常规用量，又不至于把账务接口当成批量导出用。 */
const PAGE_SIZE = 100;
const MAX_PAGES = 10;

const RANGES = [
  { days: 7, labelKey: "usage_range_7d" },
  { days: 30, labelKey: "usage_range_30d" },
  { days: 0, labelKey: "usage_range_all" },
] as const;

interface ModelRow {
  model: string;
  consume: number;
  errors: number;
  credits: number;
}

function formatCredits(v: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: v < 10 ? 2 : 0 }).format(v);
}

export function MatrixUsageSection() {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  const [days, setDays] = useState<number>(7);
  const [items, setItems] = useState<MatrixUsageItem[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const dateFmt = useMemo(
    () =>
      new Intl.DateTimeFormat(i18n.language, {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      }),
    [i18n.language],
  );

  const load = useCallback(async (rangeDays: number) => {
    setLoading(true);
    setError(null);
    try {
      const now = Math.floor(Date.now() / 1000);
      const base = rangeDays > 0 ? { startTimestamp: now - rangeDays * 86400, endTimestamp: now } : {};
      const acc: MatrixUsageItem[] = [];
      let grandTotal = 0;
      let cut = false;
      for (let page = 1; page <= MAX_PAGES; page++) {
        const res = await API.getMatrixUsage({ ...base, page, pageSize: PAGE_SIZE });
        grandTotal = res.total;
        acc.push(...res.items);
        if (res.items.length < PAGE_SIZE || acc.length >= res.total) break;
        if (page === MAX_PAGES && acc.length < res.total) cut = true;
      }
      setItems(acc);
      setTotal(grandTotal);
      setTruncated(cut);
    } catch (err) {
      setError(errMsg(err));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(days);
  }, [days, load]);

  const summary = useMemo(() => {
    let credits = 0;
    let consume = 0;
    let errors = 0;
    let refunds = 0;
    const byModel = new Map<string, ModelRow>();
    for (const it of items) {
      const row = byModel.get(it.model_name) ?? { model: it.model_name, consume: 0, errors: 0, credits: 0 };
      if (it.kind === "consume") {
        credits += it.credits;
        consume += 1;
        row.consume += 1;
        row.credits += it.credits;
      } else if (it.kind === "error") {
        errors += 1;
        row.errors += 1;
      } else if (it.kind === "refund") {
        // 退款抵扣消费，否则"花了多少"会虚高
        credits -= it.credits;
        refunds += 1;
        row.credits -= it.credits;
      }
      byModel.set(it.model_name, row);
    }
    const models = [...byModel.values()].sort((a, b) => b.credits - a.credits || b.consume - a.consume);
    return { credits, consume, errors, refunds, models };
  }, [items]);

  const kpis: Array<[string, string]> = [
    ["usage_kpi_credits", formatCredits(summary.credits)],
    ["usage_kpi_calls", String(summary.consume)],
    ["usage_kpi_errors", String(summary.errors)],
  ];

  return (
    <div className="space-y-6">
      <div>
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.16em] text-accent-2">
          Billing Records
        </div>
        <h3 className="font-editorial mt-1" style={{ fontWeight: 400, fontSize: 22, lineHeight: 1.1 }}>
          {t("dashboard:usage")}
        </h3>
        <p className="mt-1.5 text-[12.5px] leading-[1.6] text-text-3">{t("dashboard:usage_source_hint")}</p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {RANGES.map(({ days: d, labelKey }) => (
          <button
            key={d}
            type="button"
            onClick={() => setDays(d)}
            aria-pressed={days === d}
            className={
              "rounded-md border px-3 py-1.5 text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent " +
              (days === d
                ? "border-accent/35 bg-accent-dim text-text"
                : "border-hairline-soft text-text-3 hover:border-hairline hover:text-text")
            }
          >
            {t(`dashboard:${labelKey}`)}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-14">
          <Loader2 className="h-4 w-4 animate-spin text-text-3" aria-hidden />
        </div>
      ) : error ? (
        <div className="flex items-start gap-2 rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warm-bright" aria-hidden />
          <p className="text-[12.5px] text-text-2">{error}</p>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-3">
            {kpis.map(([key, value]) => (
              <div key={key} className="rounded-[10px] border border-hairline p-4" style={CARD_STYLE}>
                <p className="text-[11px] text-text-4">{t(`dashboard:${key}`)}</p>
                <p className="mt-1.5 font-editorial text-[24px] leading-none tabular-nums text-text">{value}</p>
              </div>
            ))}
          </div>

          {truncated && (
            <p className="text-[11.5px] text-text-4">
              {t("dashboard:usage_truncated", { shown: items.length, total })}
            </p>
          )}

          <div className="rounded-[10px] border border-hairline" style={CARD_STYLE}>
            <div className="border-b border-hairline-soft px-5 py-3">
              <h4 className="text-[13px] font-medium text-text">{t("dashboard:usage_by_model")}</h4>
            </div>
            {summary.models.length === 0 ? (
              <p className="px-5 py-10 text-center text-[12px] text-text-4">{t("dashboard:usage_empty")}</p>
            ) : (
              <ul className="divide-y divide-hairline-soft">
                {summary.models.map((m) => (
                  <li key={m.model} className="flex items-center gap-3 px-5 py-2.5">
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-text-2">{m.model}</span>
                    <span className="shrink-0 font-mono text-[11px] tabular-nums text-text-4">
                      {t("dashboard:usage_calls_n", { count: m.consume })}
                    </span>
                    {m.errors > 0 && (
                      <span className="shrink-0 font-mono text-[11px] tabular-nums text-warm-bright">
                        {t("dashboard:usage_errors_n", { count: m.errors })}
                      </span>
                    )}
                    <span className="w-20 shrink-0 text-right text-[12.5px] tabular-nums text-text">
                      {formatCredits(m.credits)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-[10px] border border-hairline" style={CARD_STYLE}>
            <div className="border-b border-hairline-soft px-5 py-3">
              <h4 className="text-[13px] font-medium text-text">{t("dashboard:usage_records")}</h4>
            </div>
            {items.length === 0 ? (
              <p className="px-5 py-10 text-center text-[12px] text-text-4">{t("dashboard:usage_empty")}</p>
            ) : (
              <ul className="divide-y divide-hairline-soft">
                {items.slice(0, 50).map((it, idx) => (
                  <li key={it.id ?? `${it.request_id}-${idx}`} className="flex items-center gap-3 px-5 py-2">
                    <span className="w-24 shrink-0 font-mono text-[11px] text-text-4">
                      {dateFmt.format(new Date(it.created_at * 1000))}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12px] text-text-2">{it.model_name}</span>
                    {it.kind !== "consume" && (
                      <span
                        className="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                        style={
                          it.kind === "error"
                            ? { background: "var(--color-warm-tint)", color: "var(--color-warm-bright)" }
                            : { background: "var(--color-accent-dim)", color: "var(--color-accent-2)" }
                        }
                      >
                        {t(`dashboard:usage_kind_${it.kind}`)}
                      </span>
                    )}
                    <span className="w-20 shrink-0 text-right text-[12px] tabular-nums text-text-2">
                      {it.kind === "consume" ? formatCredits(it.credits) : "—"}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
