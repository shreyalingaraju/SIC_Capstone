import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { fetchComparison, fetchOptimization, fetchOptimizationPlan, fetchQueue } from '../lib/api';
import { QueueMethod } from '../types/dispatch';

const TEN_MIN = 1000 * 60 * 10; // artifacts are static between pipeline runs

export function useOptimization() {
  return useQuery({ queryKey: ['optimization'], queryFn: fetchOptimization, staleTime: TEN_MIN });
}

export function useOptimizationPlan(params: {
  decision?: 'recommended' | 'deferred';
  borough?: string;
  page?: number;
  pageSize?: number;
}) {
  return useQuery({
    queryKey: ['optimizationPlan', params],
    queryFn: () => fetchOptimizationPlan(params),
    staleTime: TEN_MIN,
    placeholderData: keepPreviousData,
  });
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
