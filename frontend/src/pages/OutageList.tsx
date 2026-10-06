import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useGlobalFilters } from '../context/FilterContext';
import { useOutages } from '../hooks/useOutages';
import { HeaderControls } from '../components/layout/HeaderSlot';
import { BoroughControl, DecisionBadge, DecisionControl, EmptyState, Pager, PageHeader, ScoreNote, SearchInput, SelectControl, TierBadge, TierControl } from '../components/ui';
import { formatDate, formatDays, formatNumber, formatScore } from '../lib/formatters';

export const OutageList: React.FC = () => {
  const navigate = useNavigate();
  const { borough, priorityTier, decision } = useGlobalFilters();
  const [search, setSearch] = useState('');
  const [scope, setScope] = useState<'scored' | 'excluded'>('scored');
  const [page, setPage] = useState(1);

  useEffect(() => setPage(1), [borough, priorityTier, decision, scope, search]);

  const { data, isLoading, isError, isFetching, refetch } = useOutages({
    borough,
    priorityTier,
    search,
    scope,
    dispatchStatus: decision === 'All' ? undefined : decision,
    page,
    pageSize: 50,
  });

  const scored = scope === 'scored';

  return (
    <div className="page flex h-full min-h-0 flex-col">
      <HeaderControls>
        <BoroughControl />
        <TierControl />
        <DecisionControl />
        <SearchInput value={search} onSearch={setSearch} placeholder="Search outage ID or street" className="w-52 shrink min-w-[9rem] xl:w-64" />
      </HeaderControls>

      <PageHeader
        title="Outages"
        description="Every reported street-light outage, ordered by priority score."
        actions={
          <SelectControl
            label="Show"
            value={scope}
            onChange={(v) => setScope(v as 'scored' | 'excluded')}
            options={[{ value: 'scored', label: 'Scored outages' }, { value: 'excluded', label: 'Not scored' }]}
          />
        }
      />

      <div className="table-wrap min-h-0 flex-1" aria-busy={isFetching}>
        <table className="data-table">
          <caption className="sr-only">Street-light outages</caption>
          <thead>
            <tr>
              <th scope="col">Outage ID</th>
              <th scope="col">Location</th>
              <th scope="col" className="hidden xl:table-cell">Borough</th>
              <th scope="col" className="hidden lg:table-cell">Created</th>
              <th scope="col" className="num">Duration</th>
              <th scope="col" className="num">Priority score</th>
              <th scope="col">Tier</th>
              <th scope="col">Decision</th>
            </tr>
          </thead>
          <tbody className={isFetching && data ? 'opacity-60 transition-opacity' : 'transition-opacity'}>
            {data?.items.map((o) => (
              <tr key={o.outage_id} className="row-link" tabIndex={0} onClick={() => navigate(`/outages/${o.outage_id}`)} onKeyDown={(e) => e.key === 'Enter' && navigate(`/outages/${o.outage_id}`)}>
                <td className="font-mono font-medium">{o.outage_id}</td>
                <td className="max-w-[220px] truncate text-ink-soft 2xl:max-w-[320px]" title={o.location_desc}>{o.location_desc}</td>
                <td className="hidden xl:table-cell">{o.borough}</td>
                <td className="hidden text-ink-soft lg:table-cell">{formatDate(o.created_date)}</td>
                <td className="num text-ink-soft">{formatDays(o.duration_days)}</td>
                <td className="num font-bold">{scored ? formatScore(o.priority_score) : '–'}</td>
                <td>{scored ? <TierBadge tier={o.priority_tier} /> : <span className="text-ink-soft">–</span>}</td>
                <td>
                  {scored ? <DecisionBadge status={o.dispatch_status} /> : <span className="text-xs text-ink-soft" title={o.exclusion_reason ?? ''}>{reasonLabel(o.exclusion_reason)}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {isLoading && <p className="px-5 py-10 text-center text-xs text-ink-soft" role="status">Loading outages…</p>}
        {isError && (
          <EmptyState tone="warn" title="Outages could not be loaded" detail="Check that the backend is running." />
        )}
        {data && data.items.length === 0 && <EmptyState title="No outages match these filters" detail="Try a different borough, tier or search term." />}
      </div>

      {isError && <div className="mt-3"><button className="btn btn-sm" type="button" onClick={() => refetch()}>Retry</button></div>}

      <div className="mt-3 space-y-2">
        {data && data.total_count > 0 && <Pager page={data.page} pages={data.total_pages} total={data.total_count} onPage={setPage} label="outages" />}
        <ScoreNote />
      </div>
    </div>
  );
};

function reasonLabel(reason: string | null): string {
  if (!reason) return 'Not scored';
  if (reason === 'lookback_not_covered_by_crime_data') return 'Crime data does not cover this period';
  return reason.replace(/_/g, ' ');
}
