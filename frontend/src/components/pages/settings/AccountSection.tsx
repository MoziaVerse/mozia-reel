import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Check,
  Copy,
  ExternalLink,
  Hash,
  LogOut,
  RefreshCw,
  User,
  Wallet,
} from "lucide-react";
import { API } from "@/api";
import type { MatrixOverview, MatrixCredits } from "@/types";

/**
 * 托管态「账户」页。字段与 Matrix 站内个人资料页对齐（用户名 / 用户 ID / 余额），
 * 用户在两边看到的是同一套口径，不必换算也不必猜哪个才是真的。
 *
 * 这里刻意**不做任何修改入口**：昵称、手机号、充值都归 Matrix 管，在这儿放一份
 * 会写不回去、或与那边并存两个真相源。要改就跳过去改。
 */

/** 1 积分 = ¥0.01；每积分对应的 quota = quotaPerUnit / 100。与 matrix lib/billing.ts 同源。 */
function quotaToCredits(quota: number, quotaPerUnit: number): number {
  const perCredit = quotaPerUnit / 100 || 1;
  return quota / perCredit;
}

function formatCredits(quota: number, quotaPerUnit: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(
    quotaToCredits(quota, quotaPerUnit),
  );
}

function Row({
  Icon,
  label,
  children,
  hint,
}: {
  Icon: React.ComponentType<{ className?: string }>;
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2 text-[12px] font-medium text-text-3">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      {children}
      {hint && <p className="text-[11px] text-text-4">{hint}</p>}
    </div>
  );
}

export function AccountSection({ overview }: { overview: MatrixOverview }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [credits, setCredits] = useState<MatrixCredits | null>(null);
  // 初值即 true：挂载就要拉一次。写成 false 再由 effect 同步置 true 会触发级联渲染
  // （react-hooks/set-state-in-effect），且首帧会闪一下"已加载完"的空态。
  const [refreshing, setRefreshing] = useState(true);
  const [copied, setCopied] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  const fetchCredits = useCallback(
    () =>
      API.getMatrixCredits()
        .then(setCredits)
        .catch(() => setCredits({ available: false }))
        .finally(() => setRefreshing(false)),
    [],
  );

  // 手动刷新才需要把 refreshing 拨回 true —— 挂载那次由初值承担。
  const loadCredits = useCallback(() => {
    setRefreshing(true);
    void fetchCredits();
  }, [fetchCredits]);

  useEffect(() => {
    void fetchCredits();
  }, [fetchCredits]);

  const ssoSub = overview.user?.sso_sub ?? "";

  const copyId = async () => {
    if (!ssoSub) return;
    try {
      await navigator.clipboard.writeText(ssoSub);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // 剪贴板被拒（非安全上下文/权限）不算故障：id 就在旁边，用户可以手选。
    }
  };

  const logout = async () => {
    setLoggingOut(true);
    try {
      const { login_url } = await API.matrixLogout();
      window.location.replace(login_url);
    } catch {
      setLoggingOut(false);
    }
  };

  const wallet = credits?.available ? credits.wallet : null;
  const perUnit = wallet?.quotaPerUnit ?? 500000;

  return (
    <div className="space-y-6">
      {/* ── 基本信息 ── */}
      <section className="rounded-[10px] border border-hairline bg-bg-grad-a/40 p-5">
        <h3 className="mb-4 font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
          {t("dashboard:account_identity")}
        </h3>
        <div className="space-y-4">
          <Row Icon={User} label={t("dashboard:account_username")}>
            <p className="truncate text-[13px] text-text">
              {overview.user?.username || t("dashboard:account_username_unset")}
            </p>
          </Row>

          <div className="h-px bg-hairline-soft" />

          <Row Icon={Hash} label={t("dashboard:account_user_id")} hint={t("dashboard:account_user_id_hint")}>
            <div className="flex items-center gap-2">
              <p className="min-w-0 flex-1 truncate font-mono text-[12.5px] text-text">{ssoSub || "—"}</p>
              <button
                type="button"
                onClick={() => void copyId()}
                disabled={!ssoSub}
                aria-label={t("dashboard:account_copy_id")}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-hairline-soft px-2.5 py-1.5 text-[11.5px] text-text-3 transition-colors hover:border-hairline hover:text-text disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
                <span className="hidden sm:inline">
                  {copied ? t("common:copied") : t("common:copy")}
                </span>
              </button>
            </div>
          </Row>
        </div>
      </section>

      {/* ── 余额 ── */}
      <section className="rounded-[10px] border border-hairline bg-bg-grad-a/40 p-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
            {t("dashboard:account_balance")}
          </h3>
          <button
            type="button"
            onClick={loadCredits}
            disabled={refreshing}
            aria-label={t("common:refresh")}
            className="inline-flex items-center gap-1.5 rounded-md border border-hairline-soft px-2.5 py-1.5 text-[11.5px] text-text-3 transition-colors hover:border-hairline hover:text-text disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <RefreshCw className={"h-3.5 w-3.5" + (refreshing ? " animate-spin" : "")} />
            <span className="hidden sm:inline">{t("common:refresh")}</span>
          </button>
        </div>

        {credits == null ? (
          <p className="text-[12px] text-text-4">{t("common:loading")}</p>
        ) : wallet == null ? (
          // 明确说"取不到"，不显示 0：后者会被当成余额耗尽而去充值。
          <p className="text-[12px] text-text-3">{t("dashboard:account_balance_unavailable")}</p>
        ) : (
          <>
            <div className="flex items-baseline gap-2">
              <Wallet className="h-4 w-4 shrink-0 text-accent-2" />
              <span className="font-editorial text-[30px] leading-none tabular-nums text-text">
                {/* 口径与 Matrix 钱包页一致：可用积分 = 订阅 + 充值 + 获赠。
                    不用 usableQuota（只含充值与历史余额）——赠送额度并非不能用，
                    只是限于部分模型，而哪些模型能用已经在模型下拉上逐项标了出来。
                    把它从总数里剔掉，会让人以为那 2000 多积分是笔坏账。 */}
                {formatCredits(wallet.wallet.total, perUnit)}
              </span>
              <span className="text-[12px] text-text-3">{t("dashboard:account_credits_unit")}</span>
            </div>
            {wallet.wallet.gift > 0 && (
              // 不解释的话，"可用 1,684 / 赠送 3,981" 看起来就像算错了。口径与
              // Matrix 钱包页一致：赠送额度长期有效但只能调用有限模型。
              <p className="mt-2.5 text-[11.5px] leading-[1.6] text-text-4">
                {t("dashboard:account_gift_limited_hint")}
              </p>
            )}
            {/* 三档配色与 Matrix 站内同源（Wallet.tsx 的 creditSourceMeta）：
                赠送=emerald、充值=sky、订阅=violet。模型下拉上的「赠送可用 /
                需充值」徽标用的是同一组色，两处对上才能一眼看出「手里这笔钱
                能不能花在那个模型上」。 */}
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
              {(
                [
                  ["account_balance_gift", wallet.wallet.gift, "text-emerald-300", "border-emerald-500/25"],
                  [
                    "account_balance_paid",
                    wallet.wallet.paid + wallet.wallet.legacy,
                    "text-sky-300",
                    "border-sky-500/25",
                  ],
                  [
                    "account_balance_subscription",
                    wallet.wallet.subscription,
                    "text-violet-300",
                    "border-violet-500/25",
                  ],
                ] as const
              ).map(([key, quota, valueCls, borderCls]) => (
                <div key={key} className={`rounded-[8px] border px-3 py-2.5 ${borderCls}`}>
                  <p className="text-[11px] text-text-4">{t(`dashboard:${key}`)}</p>
                  <p className={`mt-1 text-[15px] tabular-nums ${valueCls}`}>
                    {formatCredits(quota, perUnit)}
                  </p>
                </div>
              ))}
            </div>
          </>
        )}
      </section>

      {/* ── 去 Matrix 管理 ── */}
      <section className="rounded-[10px] border border-hairline bg-bg-grad-a/40 p-5">
        <h3 className="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-text-4">
          {t("dashboard:account_manage")}
        </h3>
        <p className="mt-2 text-[12px] leading-[1.6] text-text-3">
          {t("dashboard:account_manage_hint")}
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {overview.matrix_web_url && (
            <a
              href={overview.matrix_web_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 rounded-md border border-hairline-soft px-3 py-1.5 text-[12px] text-text-2 transition-colors hover:border-hairline hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            >
              {t("dashboard:account_open_console")}
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
          <button
            type="button"
            onClick={() => void logout()}
            disabled={loggingOut}
            className="inline-flex items-center gap-1.5 rounded-md border border-hairline-soft px-3 py-1.5 text-[12px] text-text-3 transition-colors hover:border-hairline hover:text-text disabled:opacity-40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <LogOut className="h-3.5 w-3.5" />
            {t("dashboard:account_switch")}
          </button>
        </div>
      </section>
    </div>
  );
}
