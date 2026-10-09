export type PressureCategory = 'High' | 'Moderate' | 'Low';

export interface RegimeBorough {
  borough: string;
  available: boolean;
  category?: PressureCategory;
  /** Percentile (0-100) of the current score within the borough's own 2024-2025 history. */
  relative_score?: number;
  /** Current score is higher than every historical window. */
  above_reference_range?: boolean;
  observation_count: number;
  /** Synthetic profile only: measured share of complaints unresolved after 7 days, and median days to close. */
  slow_share?: number;
  median_days_to_close?: number | null;
}

export interface MlRegime {
  available: boolean;
  note?: string;
  model_status: { state: 'ready' | 'disabled' | 'unavailable'; reason: string | null; model: string | null };
  window?: { days: number; start: string; end: string };
  reference?: { start: string; end: string; description: string };
  boroughs: RegimeBorough[];
  explanation?: string;
  limitations?: string[];
  /** Profile-specific wording: what a row is called, what history it is ranked against, and whether it is a model or a measurement. */
  labels?: { unit: string; reference: string; kind: 'model' | 'observed_synthetic'; footnote: string };
}
