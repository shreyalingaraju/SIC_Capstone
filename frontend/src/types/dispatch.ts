import { Page, PriorityTier } from './outage';

export type QueueMethod = 'FIFO' | 'LightSafe';

export interface QueueItem {
  queue_rank: number;
  repair_day: number;
  outage_id: string;
  borough: string;
  location_desc: string | null;
  created_date: string | null;
  priority_score: number | null;
  priority_tier: PriorityTier | null;
  impact_index: number | null;
}

export interface QueueList extends Page {
  method: QueueMethod;
  items: QueueItem[];
}

export interface MilestonePair {
  lightsafe: number | null;
  fifo: number | null;
}

export interface Comparison {
  available: boolean;
  impact_unit: string;
  impact_note: string;
  repair_capacity_per_day: number;
  total_days: number;
  total_repairs: number;
  total_impact_index: number;
  day_one: {
    repairs: number;
    lightsafe: number;
    fifo: number;
    absolute_difference: number;
    improvement_pct: number | null;
  };
  milestones: {
    time_to_50pct_impact_days: MilestonePair;
    time_to_90pct_impact_days: MilestonePair;
    time_to_clear_days: MilestonePair;
  };
  curve: { day: number; cumulative_repairs: number; lightsafe: number; fifo: number }[];
}

export interface BoroughRow {
  borough: string;
  quota_minimum: number | null;
  selected: number;
  unconstrained_selected: number;
  quota_satisfied: boolean | null;
}

export interface Optimization {
  available: boolean;
  model: string;
  solver_status: string;
  objective_definition: string;
  impact_unit: string;
  impact_note: string;
  n_candidates: number;
  n_selected: number;
  n_deferred: number;
  daily_budget: number;
  repair_cost: number;
  quota_fraction: number;
  budget_used: number;
  budget_remaining: number;
  objective_value: number;
  unconstrained_objective: number;
  price_of_fairness: number;
  price_of_fairness_pct: number | null;
  all_quotas_satisfied: boolean;
  borough_table: BoroughRow[];
}
