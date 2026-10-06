import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { fetchPriorityQueue, fetchPrioritySummary, OutageFilters } from '../lib/api';

export function usePrioritySummary() {
  return useQuery({
    queryKey: ['prioritySummary'],
    queryFn: () => fetchPrioritySummary(),
    staleTime: 1000 * 60 * 2,
  });
}

export function usePriorityQueue(params: OutageFilters) {
  return useQuery({
    queryKey: ['priorityQueue', params],
    queryFn: () => fetchPriorityQueue(params),
    staleTime: 1000 * 60,
    placeholderData: keepPreviousData,
  });
}
