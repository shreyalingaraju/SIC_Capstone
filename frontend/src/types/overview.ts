import { OutageItem } from './outage';
import { BoroughRow } from './dispatch';

export interface PipelineStatus {
  status: string;
  all_artifacts_available: boolean;
  missing_artifacts: string[];
  artifacts_last_modified_utc: string | null;
  notice: string;
  impact_note: string;
  stages: { stage: number; name: string; artifact: string }[];
  artifacts: { artifact: string; file: string; available: boolean; modified_utc: string | null }[];
}

export interface OverviewData {
  kpis: {
    total_outages: number;
    scored_outages: number;
    excluded_outages: number;
    high_priority: number;
    medium_priority: number;
    low_priority: number;
    recommended_repairs: number | null;
    daily_budget: number | null;
    budget_used: number | null;
    budget_remaining: number | null;
    plan_objective_index: number | null;
  };
  plan_vs_fifo: {
    repairs: number;
    lightsafe_index: number;
    fifo_index: number;
    absolute_difference: number;
    improvement_pct: number | null;
    impact_unit: string;
  } | null;
  priority_mix: { high: number; medium: number; low: number; total: number };
  recommended_repairs: OutageItem[];
  optimization: {
    solver_status: string;
    price_of_fairness: number | null;
    borough_table: BoroughRow[];
    all_quotas_satisfied: boolean;
  } | null;
  impact_note: string;
  data_status: PipelineStatus;
}
