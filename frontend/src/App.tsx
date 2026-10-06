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

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // a 404 is a definitive answer; only retry transient failures
      retry: (count: number, error: unknown) => count < 1 && !(error instanceof Error && error.message.startsWith('API error 404')),
    },
  },
});

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
      <FilterProvider>
        <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <Routes>
            <Route path="/" element={<AppShell />}>
              <Route index element={<Overview />} />
              <Route path="map" element={<MapView />} />
              <Route path="outages" element={<OutageList />} />
              <Route path="outages/:id" element={<OutageDetails />} />
              <Route path="priority" element={<Prioritization />} />
              <Route path="causal" element={<CausalAnalysis />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </FilterProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
};

export default App;
