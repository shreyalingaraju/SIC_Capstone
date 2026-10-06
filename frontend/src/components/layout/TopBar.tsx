import React, { useEffect, useRef, useState } from 'react';
import { Moon, Sun, Check, AlertTriangle } from 'lucide-react';
import { PipelineStatus } from '../../types/overview';
import { formatDate } from '../../lib/formatters';
import { useTheme } from '../../context/ThemeContext';

interface TopBarProps {
  dataStatus?: PipelineStatus;
  apiError?: boolean;
  setSlot: (el: HTMLElement | null) => void;
}

export const TopBar: React.FC<TopBarProps> = ({ dataStatus, apiError, setSlot }) => {
  const { theme, toggleTheme } = useTheme();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const ok = !!dataStatus?.all_artifacts_available && !apiError;

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

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

        <div className="relative" ref={wrap}>
          <button type="button" className="btn" aria-expanded={open} aria-haspopup="dialog" onClick={() => setOpen((v) => !v)}>
            <span className={`h-2 w-2 rounded-full ${ok ? 'bg-emerald-500' : 'bg-priority-medium'}`} aria-hidden />
            <span className="hidden md:inline">{apiError ? 'Offline' : ok ? 'Data current' : 'Data incomplete'}</span>
            <span className="sr-only md:hidden">Data status</span>
          </button>

          {open && (
            <div role="dialog" aria-label="Data status" className="absolute right-0 top-11 z-50 w-80 animate-pop-in rounded-card border border-line bg-surface-strong p-4 shadow-elevated">
              <p className="mb-2 flex items-center gap-1.5 text-xs font-bold text-ink">
                {ok ? <Check size={14} className="text-emerald-500" /> : <AlertTriangle size={14} className="text-priority-medium" />}
                {apiError ? 'The API is not reachable' : ok ? 'All data files loaded' : 'Some data files are missing'}
              </p>
              {dataStatus?.artifacts_last_modified_utc && (
                <p className="mb-2 text-xs text-ink-soft">Latest results file: {formatDate(dataStatus.artifacts_last_modified_utc)}</p>
              )}
              {dataStatus && !ok && dataStatus.missing_artifacts.length > 0 && (
                <ul className="mb-2 list-disc pl-4 text-xs text-ink-soft">
                  {dataStatus.missing_artifacts.map((m) => (
                    <li key={m} className="font-mono">{m}</li>
                  ))}
                </ul>
              )}
              {dataStatus && <p className="border-t border-line-soft pt-2 text-[11px] leading-relaxed text-ink-soft">{dataStatus.notice}</p>}
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
