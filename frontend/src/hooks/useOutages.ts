import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { fetchOutages, fetchOutageDetail, updateOutageAction, OutageFilters } from '../lib/api';

export function useOutages(params: OutageFilters) {
  return useQuery({
    queryKey: ['outages', params],
    queryFn: () => fetchOutages(params),
    staleTime: 1000 * 60,
    placeholderData: keepPreviousData,
  });
}

export function useOutageDetail(outageId?: string) {
  return useQuery({
    queryKey: ['outage', outageId],
    queryFn: () => (outageId ? fetchOutageDetail(outageId) : Promise.reject('No ID')),
    enabled: Boolean(outageId),
    staleTime: 1000 * 60,
  });
}

export function useOutageActionMutation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ outageId, action }: { outageId: string; action: 'approve' | 'defer' | 'flag' | 'reset' }) =>
      updateOutageAction(outageId, action),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['outages'] });
      queryClient.invalidateQueries({ queryKey: ['outage', variables.outageId] });
      queryClient.invalidateQueries({ queryKey: ['overview'] });
      queryClient.invalidateQueries({ queryKey: ['priority'] });
    },
  });
}
