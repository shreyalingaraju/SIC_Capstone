export interface EventStudyPoint {
  rel_week: number;
  coefficient: number | null;
  std_error: number | null;
  ci_lower: number | null;
  ci_upper: number | null;
  p_value: number | null;
}

export interface EffectEstimate {
  effect_name: string;
  estimate: number | null;
  standard_error: number | null;
  ci_lower: number | null;
  ci_upper: number | null;
  p_value: number | null;
  significant_at_5pct: boolean;
  ci_includes_zero: boolean;
  n_observations: number | null;
  n_pairs: number | null;
  outcome_ring: string | null;
  model: string | null;
  sign_convention: string | null;
}

export interface PeriodEffects {
  direct?: EffectEstimate;
  displacement?: EffectEstimate;
  net?: EffectEstimate;
  displacement_proportion: number | null;
}

export interface CausalOverview {
  available: boolean;
  periods: { post: PeriodEffects; during: PeriodEffects };
  method: string | null;
  specification: string | null;
  sign_convention: string | null;
  dataset_summary: {
    rows?: number;
    unique_pairs?: number;
    unique_locations?: number;
    cluster_variable?: string;
  };
  event_study: EventStudyPoint[];
  estimates_table: EffectEstimate[];
  did_specifications: Record<string, unknown>;
  score_input: string;
}
