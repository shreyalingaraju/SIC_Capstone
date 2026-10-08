import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { fetchComparison, fetchOptimization, fetchOptimizationPlan, fetchQueue, OperatorView } from '../lib/api';
import { QueueMethod } from '../types/dispatch';

const TEN_MIN = 1000 * 60 * 10; // artifacts are static between pipeline runs

export function useOptimization() {
  return useQuery({ queryKey: ['optimization'], queryFn: fetchOptimization, staleTime: TEN_MIN });
}

export function useOptimizationPlan(params: {
  decision?: 'recommended' | 'deferred';
  action?: OperatorView;
  borough?: string;
  page?: number;
  pageSize?: number;
}) {
  return useQuery({
    queryKey: ['optimizationPlan', params],
    queryFn: () => fetchOptimizationPlan(params),
    staleTime: TEN_MIN,
    // Keep the previous page while the next loads, but never show one action view's rows under another.
    placeholderData: (prev, prevQuery) =>
      (prevQuery?.queryKey[1] as typeof params | undefined)?.action === params.action ? keepPreviousData(prev) : undefined,
  });
}

/**
 * Repair budget used = citywide outages the operator has approved, deferred or flagged this session
 * (one row per request; total_count only), not the plan's selection. Shared by Overview and Dispatch Plan.
 */
export function useBudgetUsed() {
  const { data: nApproved } = useOptimizationPlan({ page: 1, pageSize: 1, action: 'approved' });
  const { data: nDeferred } = useOptimizationPlan({ page: 1, pageSize: 1, action: 'deferred' });
  const { data: nFlagged } = useOptimizationPlan({ page: 1, pageSize: 1, action: 'flagged' });
  return (nApproved?.total_count ?? 0) + (nDeferred?.total_count ?? 0) + (nFlagged?.total_count ?? 0);
}

export function useComparison() {
  return useQuery({ queryKey: ['comparison'], queryFn: () => fetchComparison(150), staleTime: TEN_MIN });
}

export function useQueue(params: {
  method: QueueMethod;
  borough?: string;
  priorityTier?: string;
  page?: number;
  pageSize?: number;
}) {
  return useQuery({
    queryKey: ['queue', params],
    queryFn: () => fetchQueue(params),
    staleTime: TEN_MIN,
    placeholderData: keepPreviousData,
  });
}
