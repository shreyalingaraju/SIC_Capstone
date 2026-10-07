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
}
