import { OutageList } from './outage';

export interface PrioritySummary {
  available: boolean;
  total_outages: number;
  total_scored: number;
  total_excluded: number;
  exclusion_reasons: Record<string, number>;
  tier_counts: Record<string, number>;
  borough_counts: Record<string, number>;
  score_stats: { min: number; max: number; mean: number; median: number };
  tier_definition: string;
}

export interface PriorityResponse extends OutageList {
  summary: PrioritySummary;
}
