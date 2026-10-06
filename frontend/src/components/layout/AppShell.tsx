import React, { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { HeaderSlotContext } from './HeaderSlot';
import { useOverview } from '../../hooks/useOverview';

export const AppShell: React.FC = () => {
  const location = useLocation();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  // The status popover shares the cached overview request with the Overview page.
  const { data, isError } = useOverview('All', 'All');

  return (
    <HeaderSlotContext.Provider value={slot}>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-paper">
        <TopBar dataStatus={data?.data_status} apiError={isError} setSlot={setSlot} />
        <div className="flex min-h-0 flex-1">
          <Sidebar />
          <main key={location.pathname} id="main" className="min-w-0 flex-1 animate-fade-in overflow-y-auto overflow-x-hidden">
            <Outlet />
          </main>
        </div>
      </div>
    </HeaderSlotContext.Provider>
  );
};
