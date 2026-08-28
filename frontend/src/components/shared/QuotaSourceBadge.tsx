import { useTranslation } from "react-i18next";
import type { CustomProviderInfo } from "@/types/custom-provider";
import { allowsGiftQuota, lookupQuotaSources } from "@/utils/provider-models";

/**
 * 额度分区徽标：这个模型能不能用赠送额度付。
 *
 * 只有两档而不是把 required_sources 原样列出来：用户面对的选择是「我手上这笔余额能不能
 * 花在它身上」，分区名本身不是用户词汇。没有声明（null）时不渲染——网关没标注就不下结论，
 * 猜一个标签比不标更糟。
 */
export function QuotaSourceBadge({ quotaSources }: { quotaSources: string[] | null }) {
  const { t } = useTranslation("dashboard");
  const gift = allowsGiftQuota(quotaSources);
  if (gift === null) return null;
  // 配色与 Matrix 站内的额度来源同源（Wallet.tsx 的 creditSourceMeta）：
  // 赠送/奖励走 emerald、充值走 sky。amber 在那边是「兑换码」，占用它会让
  // 两个产品里同一个颜色指两件事。账户页的余额三档用的也是这组色。
  return (
    <span
      className={`shrink-0 rounded px-1.5 py-px font-mono text-[9.5px] font-bold uppercase tracking-[0.08em] ${
        gift ? "bg-emerald-500/18 text-emerald-300" : "bg-sky-500/18 text-sky-300"
      }`}
      title={t(gift ? "quota_source_gift_hint" : "quota_source_paid_hint")}
    >
      {t(gift ? "quota_source_gift" : "quota_source_paid")}
    </span>
  );
}

/**
 * `ProviderModelSelect` 的 `renderOptionBadge` 工厂。与 `videoOptionMetaRenderer` 同形：
 * 组件只认 `provider/model` 字符串，查表留在调用方。
 */
export function quotaOptionBadgeRenderer({
  customProviders,
}: {
  customProviders: CustomProviderInfo[];
}) {
  // 具名而非匿名箭头：返回 JSX 的函数会被 react/display-name 当成组件定义，
  // 而它是渲染器不是组件（由 ProviderModelSelect 在渲染期直接调用）。
  const renderQuotaBadge = (fullValue: string) => {
    const sources = lookupQuotaSources(fullValue, customProviders);
    if (sources === null) return null;
    return <QuotaSourceBadge quotaSources={sources} />;
  };
  renderQuotaBadge.displayName = "QuotaOptionBadge";
  return renderQuotaBadge;
}
