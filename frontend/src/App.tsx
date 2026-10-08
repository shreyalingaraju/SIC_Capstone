import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FilterProvider } from './context/FilterContext';
import { ThemeProvider } from './context/ThemeContext';
import { AppShell } from './components/layout/AppShell';

import { Overview } from './pages/Overview';
import { MapView } from './pages/MapView';
import { OutageDetails } from './pages/OutageDetails';
import { OutageList } from './pages/OutageList';
import { Prioritization } from './pages/Prioritization';
import { CausalAnalysis } from './pages/CausalAnalysis';
import { CityReplay } from './pages/CityReplay';
import { SyntheticDashboard } from './pages/SyntheticDashboard';
import { ProfileProvider, useProfile } from './context/ProfileContext';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // a 404 is a definitive answer; only retry transient failures
      retry: (count: number, error: unknown) => count < 1 && !(error instanceof Error && error.message.startsWith('API error 404')),
    },
  },
});

const Home: React.FC = () => {
  const { synthetic, loaded } = useProfile();
  if (!loaded) return null;
  return synthetic ? <Navigate to="/synthetic" replace /> : <Overview />;
};

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
      <ProfileProvider>
      <FilterProvider>
        <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route path="/" element={<AppShell />}>
              <Route index element={<Home />} />
              <Route path="overview" element={<Overview />} />
              <Route path="synthetic" element={<SyntheticDashboard />} />
              <Route path="map" element={<MapView />} />
              <Route path="outages" element={<OutageList />} />
              <Route path="outages/:id" element={<OutageDetails />} />
              <Route path="priority" element={<Prioritization />} />
              <Route path="causal" element={<CausalAnalysis />} />
              <Route path="replay" element={<CityReplay />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </FilterProvider>
      </ProfileProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

export default App;
