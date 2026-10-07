import { useQuery } from '@tanstack/react-query';
import { fetchMlRegime } from '../lib/api';
import { MlRegime } from '../types/ml';

export function useMlRegime() {
  return useQuery<MlRegime>({
    queryKey: ['mlRegime'],
    queryFn: fetchMlRegime,
    staleTime: 1000 * 60 * 10, // a static snapshot between pipeline runs
  });
}
