/* City replay payload (backend/services/operations_service.py get_replay). */

export interface DailySeries {
  date: string[];
  new_jobs: number[];
  dispatched: number[];
  resolved: number[];
  backlog: number[];
  in_service: number[];
}

export interface ReplayData {
  capacity_k: number;
  policy: string;
  service_days: number;
  overload_start: string;
  horizon_end: string;
  n_jobs: number;
  n_jobs_without_coordinates: number;
  boroughs: string[];
  series: DailySeries;
  jobs: {
    lon: number[];
    lat: number[];
    has_coords: number[];
    known_day: number[];
    dispatch_day: number[];
    borough: number[];
  };
}
