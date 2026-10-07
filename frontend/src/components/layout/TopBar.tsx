import React from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from '../../context/ThemeContext';

interface TopBarProps {
  setSlot: (el: HTMLElement | null) => void;
}

export const TopBar: React.FC<TopBarProps> = ({ setSlot }) => {
  const { theme, toggleTheme } = useTheme();
  const night = theme === 'dark';

  return (
    <header className="z-30 flex h-14 shrink-0 items-center border-b border-line bg-surface-strong">
      {/* brand: width matches the sidebar below */}
      <div className="flex h-full w-14 shrink-0 items-center justify-center gap-3 border-r border-line xl:w-60 xl:justify-start xl:px-5">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-night" aria-hidden>
          <div className="h-4 w-2.5 rounded-t-full rounded-b-sm bg-signal" />
        </div>
        <div className="hidden min-w-0 leading-tight xl:block">
          <p className="text-[13px] font-extrabold tracking-wide text-ink">LIGHTSAFE</p>
          <p className="truncate text-[10.5px] font-medium text-ink-soft">Street-light repair decision support</p>
        </div>
      </div>

      {/* page-specific controls are portalled here */}
      <div ref={setSlot} className="flex min-w-0 flex-1 flex-nowrap items-center gap-2 overflow-hidden px-4 xl:px-6" />

      <div className="flex shrink-0 items-center gap-2 pr-4 xl:pr-6">
        <button
          type="button"
          onClick={toggleTheme}
          aria-pressed={night}
          title={night ? 'Switch to light mode' : 'Switch to night mode'}
          className="btn"
        >
          {night ? <Sun size={14} aria-hidden /> : <Moon size={14} aria-hidden />}
          <span className="sr-only xl:not-sr-only">Night mode</span>
        </button>
      </div>
    </header>
  );
};
