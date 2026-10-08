import React, { useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { HeaderSlotContext } from './HeaderSlot';
import { useProfile } from '../../context/ProfileContext';

export const AppShell: React.FC = () => {
  const location = useLocation();
  const [slot, setSlot] = useState<HTMLElement | null>(null);
  const { synthetic, label } = useProfile();

  return (
    <HeaderSlotContext.Provider value={slot}>
      <div className="flex h-screen w-screen flex-col overflow-hidden bg-paper">
        <TopBar setSlot={setSlot} />
        {synthetic && (
          <div role="note" className="flex shrink-0 items-center justify-center gap-2 border-b border-priority-medium/40 bg-priority-medium/15 px-4 py-1 text-[11.5px] font-bold text-ink">
            <span className="badge badge-medium">SYNTHETIC</span>
            {label ?? 'Synthetic Dataset — Demonstration / Simulation'}. Not real-world measurements.
          </div>
        )}
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
