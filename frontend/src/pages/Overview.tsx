import React from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { useOverview } from '../hooks/useOverview';
import { useGlobalFilters } from '../context/FilterContext';
import { HeaderControls } from '../components/layout/HeaderSlot';
import { BoroughControl, DecisionBadge, ErrorState, Loading, PageHeader, ScoreNote, Stat, TierBadge } from '../components/ui';
import { formatDays, formatNumber, formatScore } from '../lib/formatters';
import { RepairPressure } from '../components/RepairPressure';
import { BoroughRepairsChart, TopRepairsChart } from '../components/OverviewCharts';
import { useBudgetUsed } from '../hooks/useDispatch';

export const Overview: React.FC = () => {
  const navigate = useNavigate();
  const { borough } = useGlobalFilters();
  const { data, isLoading, isError, refetch } = useOverview(borough, 'All');
  const used = useBudgetUsed();

  const controls = (
    <HeaderControls>
      <BoroughControl />
    </HeaderControls>
  );

  if (isLoading) return <>{controls}<Loading label="Loading overview…" /></>;
  if (isError || !data) return <>{controls}<ErrorState onRetry={() => refetch()} /></>;

  const { kpis, recommended_repairs, optimization } = data;
  const budget = kpis.daily_budget ?? 0;
  const remaining = Math.max(0, budget - used);
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
        <Stat label="Repair budget · citywide" value={`${used} / ${budget}`} hint={`${remaining} remaining`}>
          <div className="progress mt-1" role="progressbar" aria-valuenow={used} aria-valuemin={0} aria-valuemax={budget} aria-label="Repair budget used">
            <span style={{ width: `${pct}%` }} />
          </div>
        </Stat>
      </div>

      <div className="grid grid-cols-1 items-stretch gap-6 lg:grid-cols-[minmax(0,13fr)_minmax(0,7fr)]">
        <TopRepairsChart items={recommended_repairs} onOpen={(id) => navigate(`/outages/${id}`)} />
        <BoroughRepairsChart rows={optimization ? optimization.borough_table : null} allSatisfied={optimization?.all_quotas_satisfied} />
      </div>

      <RepairPressure borough={borough} />

      <ScoreNote />
    </div>
  );
};
