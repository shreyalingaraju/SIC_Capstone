export type PriorityTier = 'High' | 'Medium' | 'Low';
export type DispatchStatus = 'recommended' | 'deferred' | 'not_scored';
export type OperatorNote = 'None' | 'Approved' | 'Deferred' | 'Flagged';

export interface Page {
  total_count: number;
  page: number;
  page_size: number;
  total_pages: number;
}

/** One outage record as served by /api/outages, /api/priority and /api/optimization/plan. */
export interface OutageItem {
  outage_id: string;
  borough: string;
  police_precinct: string | null;
  location_desc: string;
  latitude: number | null;
  longitude: number | null;
  created_date: string | null;
  closed_date: string | null;
  outage_duration_hours: number | null;
  duration_days: number | null;
  scored: boolean;
  exclusion_reason: string | null;
  /** Crimes per day within 250 m over the 14-day look-back. */
  local_crime_rate: number | null;
  /** Decision-support priority index, 0-100. Not a probability. */
  priority_score: number | null;
  priority_tier: PriorityTier | null;
  dispatch_status: DispatchStatus | null;
  optimization_rank: number | null;
  /** Session-local annotation; does not change the dispatch plan. */
  operator_note: OperatorNote;
  /** Contribution to the dispatch plan, in priority index points. */
  impact_index?: number | null;
}

export interface OutageList extends Page {
  items: OutageItem[];
}

export interface ScoreDecomposition {
  tau_net: number | null;
  tau_net_source: string | null;
  local_crime_rate: number | null;
  lookback_days: number;
  duration_factor: number | null;
  outage_duration_hours: number | null;
  raw_priority: number | null;
  priority_score: number | null;
  priority_tier: PriorityTier | null;
}

export interface OutageDetail extends OutageItem {
  incident_address: string | null;
  cross_street_1: string | null;
  cross_street_2: string | null;
  intersection_street_1: string | null;
  intersection_street_2: string | null;
  fifo_queue_rank: number | null;
  lightsafe_queue_rank: number | null;
  recent_crime_count_14d: number | null;
  decomposition: ScoreDecomposition | null;
}
