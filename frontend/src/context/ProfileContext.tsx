import React, { createContext, useContext } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchProfile } from '../lib/api';
import { BOROUGHS, MAP_CENTER, MAP_DEFAULT_ZOOM } from '../lib/constants';
import type { Profile } from '../types/synthetic';

/** Which dataset the API serves. Falls back to the NYC defaults while loading or if the endpoint is missing. */
interface ProfileValue {
  synthetic: boolean;
  label: string | null;
  region: string;
  boroughs: string[];
  mapCenter: [number, number];
  mapZoom: number;
  loaded: boolean;
}

const DEFAULT: ProfileValue = {
  synthetic: false, label: null, region: 'New York City', boroughs: [...BOROUGHS].slice(1),
  mapCenter: MAP_CENTER, mapZoom: MAP_DEFAULT_ZOOM, loaded: false,
};
const Ctx = createContext<ProfileValue>(DEFAULT);

export const ProfileProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { data } = useQuery<Profile>({ queryKey: ['profile'], queryFn: fetchProfile, staleTime: Infinity, retry: 1 });
  const value: ProfileValue = data
    ? {
        synthetic: data.synthetic, label: data.synthetic_label, region: data.region,
        boroughs: data.synthetic ? data.boroughs : DEFAULT.boroughs,
        mapCenter: data.synthetic ? data.map_center : MAP_CENTER,
        mapZoom: data.synthetic ? data.map_zoom : MAP_DEFAULT_ZOOM, loaded: true,
      }
    : DEFAULT;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

export const useProfile = () => useContext(Ctx);
