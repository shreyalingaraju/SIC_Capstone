import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useOverview } from '../hooks/useOverview';
import { useGlobalFilters } from '../context/FilterContext';
import { HeaderControls } from '../components/layout/HeaderSlot';
import { BoroughControl, DecisionBadge, ErrorState, Loading, PageHeader, ScoreNote, Stat, TierBadge } from '../components/ui';
import { formatDays, formatNumber, formatScore } from '../lib/formatters';
import { RepairPressure } from '../components/RepairPressure';

export const Overview: React.FC = () => {
  const navigate = useNavigate();
  const { borough } = useGlobalFilters();
  const { data, isLoading, isError, refetch } = useOverview(borough, 'All');

  const controls = (
    <HeaderControls>
      <BoroughControl />
    </HeaderControls>
  );

  if (isLoading) return <>{controls}<Loading label="Loading overview…" /></>;
  if (isError || !data) return <>{controls}<ErrorState onRetry={() => refetch()} /></>;

  const { kpis, recommended_repairs, optimization } = data;
  const used = kpis.budget_used ?? 0;
  const budget = kpis.daily_budget ?? 0;
  const pct = budget > 0 ? Math.min(100, (used / budget) * 100) : 0;

  return (
    <div className="page space-y-6">
      {controls}
      <PageHeader
        title="Overview"
        description={
          borough === 'All'
            ? 'Which street-light outages to repair first, given the daily repair budget.'
            : `Which street-light outages to repair first. Outage counts and the repair list are filtered to ${borough}; the repair budget and recommended-repair total are citywide.`
        }
        actions={
          <button type="button" className="btn btn-primary" onClick={() => navigate('/priority')}>
            Open dispatch plan <ArrowRight size={14} aria-hidden />
          </button>
        }
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat
          label={borough === 'All' ? 'Outages to prioritize' : `Outages to prioritize · ${borough}`}
          value={formatNumber(kpis.scored_outages)}
          hint={kpis.excluded_outages > 0 && borough === 'All' ? `${formatNumber(kpis.excluded_outages)} more could not be scored` : undefined}
        />
        <Stat
          label={borough === 'All' ? 'High priority' : `High priority · ${borough}`}
          value={formatNumber(kpis.high_priority)}
          hint={`${formatNumber(kpis.medium_priority)} medium · ${formatNumber(kpis.low_priority)} low`}
        />
        <Stat label="Recommended repairs · citywide" value={formatNumber(kpis.recommended_repairs ?? 0)} hint="Selected for the current plan" />
        <Stat label="Repair budget · citywide" value={`${used} / ${budget}`} hint={`${kpis.budget_remaining ?? 0} remaining`}>
          <div className="progress mt-1" role="progressbar" aria-valuenow={used} aria-valuemin={0} aria-valuemax={budget} aria-label="Repair budget used">
            <span style={{ width: `${pct}%` }} />
          </div>
        </Stat>
      </div>

      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-3">
        <section className="card xl:col-span-2" aria-labelledby="top-repairs">
          <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
            <h2 id="top-repairs" className="card-title">Top recommended repairs</h2>
            <Link to="/priority" className="text-xs font-semibold text-brand transition-colors hover:underline">View all</Link>
          </div>
          {recommended_repairs.length === 0 ? (
            <p className="px-5 py-10 text-center text-xs text-ink-soft">No recommended repairs for this borough.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Rank</th>
                    <th scope="col">Outage</th>
                    <th scope="col">Location</th>
                    <th scope="col" className="num">Score</th>
                    <th scope="col">Tier</th>
                    <th scope="col" className="num">Outage duration</th>
                  </tr>
                </thead>
                <tbody>
                  {recommended_repairs.slice(0, 8).map((o) => (
                    <tr key={o.outage_id} className="row-link" tabIndex={0} onClick={() => navigate(`/outages/${o.outage_id}`)} onKeyDown={(e) => e.key === 'Enter' && navigate(`/outages/${o.outage_id}`)}>
                      <td className="tabular-nums text-ink-soft">#{o.optimization_rank}</td>
                      <td className="font-mono font-medium">{o.outage_id}</td>
                      <td className="max-w-[260px] truncate text-ink-soft" title={o.location_desc}>{o.location_desc}</td>
                      <td className="num font-bold">{formatScore(o.priority_score)}</td>
                      <td><TierBadge tier={o.priority_tier} /></td>
                      <td className="num text-ink-soft">{formatDays(o.duration_days)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="card" aria-labelledby="alloc">
          <div className="border-b border-line px-5 py-3.5">
            <h2 id="alloc" className="card-title">Repairs by borough</h2>
          </div>
          {optimization ? (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Borough</th>
                    <th scope="col" className="num">Minimum</th>
                    <th scope="col" className="num">Planned</th>
                  </tr>
                </thead>
                <tbody>
                  {optimization.borough_table.filter((b) => b.selected > 0 || (b.quota_minimum ?? 0) > 0).map((b) => (
                    <tr key={b.borough}>
                      <td className="font-semibold">{b.borough}</td>
                      <td className="num text-ink-soft">{b.quota_minimum ?? '–'}</td>
                      <td className="num font-bold">{b.selected}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="border-t border-line-soft px-5 py-3 text-xs text-ink-soft">
                {optimization.all_quotas_satisfied ? 'Every borough minimum is met.' : 'Some borough minimums are not met.'}{' '}
                <Link to="/priority" className="font-semibold text-brand hover:underline">Details</Link>
              </p>
            </>
          ) : (
            <p className="px-5 py-10 text-center text-xs text-ink-soft">Borough allocation is not available.</p>
          )}
        </section>
      </div>

      <RepairPressure borough={borough} />

      <ScoreNote />
    </div>
  );
};
