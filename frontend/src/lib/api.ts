import { OutageDetail, OutageList } from '../types/outage';
import { PriorityResponse, PrioritySummary } from '../types/priority';
import { CausalOverview, EventStudyPoint } from '../types/causal';
import { Comparison, Optimization, QueueList, QueueMethod } from '../types/dispatch';
import { OverviewData } from '../types/overview';

export type { OverviewData };

const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

export interface OutageFilters {
  borough?: string;
  priorityTier?: string;
  search?: string;
  dispatchStatus?: string;
  scope?: 'scored' | 'excluded' | 'all';
  page?: number;
  pageSize?: number;
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errorText = await res.text();
    throw new Error(`API error ${res.status}: ${errorText || res.statusText}`);
  }
  return res.json();
}

function query(params: Record<string, string | number | boolean | undefined>): string {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === '' || v === 'All') return;
    q.set(k, String(v));
  });
  const s = q.toString();
  return s ? `?${s}` : '';
}

function outageParams(p: OutageFilters) {
  return {
    borough: p.borough,
    priority_tier: p.priorityTier,
    search: p.search,
    dispatch_status: p.dispatchStatus,
    scope: p.scope,
    page: p.page,
    page_size: p.pageSize,
  };
}

const get = <T>(path: string) => fetch(`${BASE_URL}${path}`).then((r) => handleResponse<T>(r));

export const fetchOverview = (borough?: string, priorityTier?: string) =>
  get<OverviewData>(`/overview${query({ borough, priority_tier: priorityTier })}`);

export const fetchOutages = (p: OutageFilters) => get<OutageList>(`/outages${query(outageParams(p))}`);

export const fetchOutageDetail = (id: string) => get<OutageDetail>(`/outages/${encodeURIComponent(id)}`);

export async function updateOutageAction(
  outageId: string,
  action: 'approve' | 'defer' | 'flag' | 'reset'
): Promise<{ success: boolean; operator_note: string }> {
  const res = await fetch(`${BASE_URL}/outages/${encodeURIComponent(outageId)}/${action}`, { method: 'POST' });
  return handleResponse(res);
}

export const fetchPrioritySummary = () => get<PrioritySummary>('/priority/summary');

export const fetchPriorityQueue = (p: OutageFilters) =>
  get<PriorityResponse>(`/priority${query(outageParams(p))}`);

export const fetchQueue = (p: { method: QueueMethod; borough?: string; priorityTier?: string; page?: number; pageSize?: number }) =>
  get<QueueList>(`/queue${query({ method: p.method, borough: p.borough, priority_tier: p.priorityTier, page: p.page, page_size: p.pageSize })}`);

export const fetchComparison = (points = 150) => get<Comparison>(`/comparison${query({ points })}`);

export const fetchOptimization = () => get<Optimization>('/optimization');

export const fetchOptimizationPlan = (p: { decision?: 'recommended' | 'deferred'; borough?: string; page?: number; pageSize?: number }) =>
  get<OutageList>(`/optimization/plan${query({ decision: p.decision, borough: p.borough, page: p.page, page_size: p.pageSize })}`);

export const fetchMapOutages = (borough?: string, priorityTier?: string, limit = 500, dispatchStatus?: string) =>
  get<GeoJSON.FeatureCollection & { total_matching: number; returned: number }>(
    `/map/outages${query({ borough, priority_tier: priorityTier, dispatch_status: dispatchStatus, limit })}`
  );

export const fetchMapCrimes = (nightOnly = true, limit = 600) =>
  get<GeoJSON.FeatureCollection>(`/map/crimes${query({ night_only: nightOnly, limit })}`);

export const fetchBufferRings = (outageId: string) =>
  get<GeoJSON.FeatureCollection>(`/map/buffers/${encodeURIComponent(outageId)}`);

export const fetchCausalOverview = () => get<CausalOverview>('/causal/overview');

export const fetchEventStudy = () => get<EventStudyPoint[]>('/causal/event-study');
