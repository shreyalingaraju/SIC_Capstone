import { useQuery } from '@tanstack/react-query';
import { fetchOverview, OverviewData } from '../lib/api';

export function useOverview(borough?: string, priorityTier?: string) {
  return useQuery<OverviewData>({
    queryKey: ['overview', borough, priorityTier],
    queryFn: () => fetchOverview(borough, priorityTier),
    staleTime: 1000 * 60 * 2, // 2 minutes
  });
}
