import React, { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { HeaderSlotContext } from './HeaderSlot';
import { CursorGlow } from './CursorGlow';

export const AppShell: React.FC = () => {
  const location = useLocation();
  const [slot, setSlot] = useState<HTMLElement | null>(null);

  return (
    <HeaderSlotContext.Provider value={slot}>
      <CursorGlow />
      <div className="relative z-10 flex h-screen w-screen flex-col overflow-hidden">
        <TopBar setSlot={setSlot} />
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
