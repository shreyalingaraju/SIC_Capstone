import { useQuery } from '@tanstack/react-query';
import { fetchCausalOverview, fetchEventStudy } from '../lib/api';

export function useCausalOverview() {
  return useQuery({
    queryKey: ['causalOverview'],
    queryFn: () => fetchCausalOverview(),
    staleTime: 1000 * 60 * 10,
  });
}

export function useEventStudy() {
  return useQuery({
    queryKey: ['eventStudy'],
    queryFn: () => fetchEventStudy(),
    staleTime: 1000 * 60 * 10,
  });
}
