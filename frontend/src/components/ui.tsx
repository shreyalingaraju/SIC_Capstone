import React, { useEffect, useState } from 'react';
import { AlertTriangle, Search, X } from 'lucide-react';
import { BOROUGHS, DISPATCH_STATUS, PRIORITY_TIERS, TIER_BADGE } from '../lib/constants';
import { DispatchStatus, PriorityTier } from '../types/outage';
import { DecisionFilter, useGlobalFilters } from '../context/FilterContext';
import { formatNumber } from '../lib/formatters';

/* ------------------------------------------------------------------ badges */
export const TierBadge: React.FC<{ tier: PriorityTier | null | undefined }> = ({ tier }) =>
  tier ? <span className={`badge ${TIER_BADGE[tier]}`}>{tier}</span> : <span className="text-ink-soft">–</span>;

export const DecisionBadge: React.FC<{ status: DispatchStatus | null | undefined; long?: boolean }> = ({ status, long }) => {
  const s = DISPATCH_STATUS[status ?? 'not_scored'];
  return <span className={`badge ${s.badge}`}>{long ? s.long : s.label}</span>;
};

/* ------------------------------------------------------------------ page chrome */
export const PageHeader: React.FC<{ title: string; description?: string; actions?: React.ReactNode }> = ({ title, description, actions }) => (
  <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
    <div className="min-w-0">
      <h1 className="page-title">{title}</h1>
      {description && <p className="page-sub max-w-3xl">{description}</p>}
    </div>
    {actions && <div className="flex items-center gap-2">{actions}</div>}
  </div>
);

export const Stat: React.FC<{ label: string; value: React.ReactNode; hint?: React.ReactNode; children?: React.ReactNode }> = ({ label, value, hint, children }) => (
  <div className="card flex flex-col gap-1 p-4">
    <span className="eyebrow">{label}</span>
    <span className="text-2xl font-extrabold leading-tight tracking-tight text-ink tabular-nums">{value}</span>
    {children}
    {hint && <span className="text-xs leading-snug text-ink-soft">{hint}</span>}
  </div>
);

export const Loading: React.FC<{ label?: string }> = ({ label = 'Loading…' }) => (
  <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 text-ink-soft" role="status" aria-live="polite">
    <div className="h-7 w-7 animate-spin rounded-full border-[3px] border-line border-t-signal-deep" />
    <span className="text-xs font-medium">{label}</span>
  </div>
);

export const EmptyState: React.FC<{ title: string; detail?: string; tone?: 'neutral' | 'warn' }> = ({ title, detail, tone = 'neutral' }) => (
  <div className={`flex min-h-[160px] flex-col items-center justify-center gap-1 px-6 py-8 text-center ${tone === 'warn' ? 'text-ink' : 'text-ink-soft'}`}>
    {tone === 'warn' && <AlertTriangle size={20} className="mb-1 text-priority-medium" aria-hidden />}
    <p className="text-sm font-semibold text-ink">{title}</p>
    {detail && <p className="max-w-md text-xs leading-relaxed">{detail}</p>}
  </div>
);

export const ErrorState: React.FC<{ title?: string; detail?: string; onRetry?: () => void }> = ({
  title = 'Data could not be loaded',
  detail = 'The LightSafe API did not respond. Check that the LightSafe service is running, then retry.',
  onRetry,
}) => (
  <div className="page">
    <div className="card mx-auto max-w-lg">
      <EmptyState tone="warn" title={title} detail={detail} />
      {onRetry && (
        <div className="flex justify-center pb-6">
          <button type="button" className="btn" onClick={onRetry}>Retry</button>
        </div>
      )}
    </div>
  </div>
);

/** One-line reminder shown wherever scores appear. */
export const ScoreNote: React.FC = () => (
  <p className="text-xs leading-relaxed text-ink-soft">
    Priority scores are a 0–100 ranking index for deciding repair order. They are not a probability of crime or a prediction of crimes prevented.
  </p>
);

/* ------------------------------------------------------------------ controls */
export const Pager: React.FC<{ page: number; pages: number; total: number; onPage: (p: number) => void; label?: string }> = ({ page, pages, total, onPage, label = 'records' }) => (
  <nav className="flex items-center justify-between gap-3 text-xs text-ink-soft" aria-label="Pagination">
    <span>{formatNumber(total)} {label}</span>
    <div className="flex items-center gap-2">
      <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</button>
      <span className="min-w-[88px] text-center tabular-nums">Page {formatNumber(page)} of {formatNumber(pages)}</span>
      <button type="button" className="btn btn-sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</button>
    </div>
  </nav>
);

export const SelectControl: React.FC<{ label: string; value: string; onChange: (v: string) => void; options: readonly { value: string; label: string }[] }> = ({ label, value, onChange, options }) => (
  <label className="control">
    <span className="control-label">{label}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>{o.label}</option>
      ))}
    </select>
  </label>
);

export const BoroughControl: React.FC = () => {
  const { borough, setBorough } = useGlobalFilters();
  return <SelectControl label="Borough" value={borough} onChange={setBorough} options={BOROUGHS.map((b) => ({ value: b, label: b === 'All' ? 'All boroughs' : b }))} />;
};

export const TierControl: React.FC = () => {
  const { priorityTier, setPriorityTier } = useGlobalFilters();
  return <SelectControl label="Tier" value={priorityTier} onChange={setPriorityTier} options={PRIORITY_TIERS.map((t) => ({ value: t, label: t === 'All' ? 'All tiers' : t }))} />;
};

export const DecisionControl: React.FC = () => {
  const { decision, setDecision } = useGlobalFilters();
  return (
    <SelectControl
      label="Decision"
      value={decision}
      onChange={(v) => setDecision(v as DecisionFilter)}
      options={[
        { value: 'All', label: 'All decisions' },
        { value: 'recommended', label: 'Repair' },
        { value: 'deferred', label: 'Deferred' },
      ]}
    />
  );
};

/** Debounced search box with clear button. */
export const SearchInput: React.FC<{ value: string; onSearch: (v: string) => void; placeholder: string; className?: string }> = ({ value, onSearch, placeholder, className = '' }) => {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  useEffect(() => {
    if (text.trim() === value) return;
    const t = setTimeout(() => onSearch(text.trim()), 300);
    return () => clearTimeout(t);
  }, [text]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={`control ${className}`} role="search">
      <Search size={14} className="shrink-0 text-ink-soft" aria-hidden />
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onSearch(text.trim());
          if (e.key === 'Escape') { setText(''); onSearch(''); }
        }}
        placeholder={placeholder}
        aria-label={placeholder}
      />
      {text && (
        <button type="button" aria-label="Clear search" className="rounded p-0.5 text-ink-soft transition-colors hover:text-ink" onClick={() => { setText(''); onSearch(''); }}>
          <X size={13} />
        </button>
      )}
    </div>
  );
};

/** Accessible tab list. */
export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: readonly { id: T; label: string }[]; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" id={`tab-${t.id}`} aria-selected={value === t.id} aria-controls={`panel-${t.id}`} className="tab" onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}
