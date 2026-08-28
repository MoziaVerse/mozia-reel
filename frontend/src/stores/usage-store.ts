import { create } from "zustand";
import type { CallType } from "@/types/provider";

interface UsageFilters {
  project_name?: string;
  media_type?: string;
  status?: string;
}

/** 对账口径：credits 来自平台账务，unknown 表示对不上（不回落本地估算）。 */
export type CreditsSource = "exact" | "aggregated" | "unknown";

export interface UsageStats {
  total_cost: number;
  /** 平台实扣积分合计。只累计对得上的行，对不上的计入 unsettled_count。 */
  total_credits?: number;
  /** 没能对上平台流水的记录数；> 0 时合计是不完整的，必须如实告知。 */
  unsettled_count?: number;
  cost_by_currency: Record<string, number>;
  image_count: number;
  video_count: number;
  text_count: number;
  audio_count: number;
  failed_count: number;
  total_count: number;
}

export interface UsageCall {
  id: string;
  project_name: string;
  call_type: CallType;
  model: string;
  status: string;
  cost_amount: number;
  currency: string;
  provider: string;
  output_path: string | null;
  resolution: string | null;
  duration_seconds: number | null;
  duration_ms: number | null;
  error_message: string | null;
  started_at: string;
  created_at: string;
  usage_tokens: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  /** 平台实扣积分。null = 对不上；0 = 确实没扣（失败请求平台记 0），两者不可合并。 */
  credits?: number | null;
  credits_source?: CreditsSource;
}

interface UsageState {
  projects: string[];
  filters: UsageFilters;
  stats: UsageStats | null;
  calls: UsageCall[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;

  setProjects: (projects: string[]) => void;
  setFilters: (filters: UsageFilters) => void;
  setStats: (stats: UsageStats | null) => void;
  setCalls: (calls: UsageCall[], total: number) => void;
  setPage: (page: number) => void;
  setLoading: (loading: boolean) => void;
}

export const useUsageStore = create<UsageState>((set) => ({
  projects: [],
  filters: {},
  stats: null,
  calls: [],
  total: 0,
  page: 1,
  pageSize: 20,
  loading: false,

  setProjects: (projects) => set({ projects }),
  setFilters: (filters) => set({ filters }),
  setStats: (stats) => set({ stats }),
  setCalls: (calls, total) => set({ calls, total }),
  setPage: (page) => set({ page }),
  setLoading: (loading) => set({ loading }),
}));
