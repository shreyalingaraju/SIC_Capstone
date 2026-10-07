import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { fetchReplay } from '../lib/api';

const TEN_MIN = 1000 * 60 * 10; // cached simulation runs are static

export function useReplay(k: number) {
  return useQuery({ queryKey: ['replay', k], queryFn: () => fetchReplay(k), staleTime: TEN_MIN, placeholderData: keepPreviousData });
}
