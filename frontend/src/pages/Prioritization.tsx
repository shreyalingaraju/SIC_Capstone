import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { Download } from 'lucide-react';
import { useGlobalFilters } from '../context/FilterContext';
import { useComparison, useOptimization, useOptimizationPlan, useQueue } from '../hooks/useDispatch';
import { HeaderControls } from '../components/layout/HeaderSlot';
import { BoroughControl, DecisionBadge, EmptyState, ErrorState, Loading, PageHeader, Pager, ScoreNote, Stat, Tabs, TierBadge } from '../components/ui';
import { formatDays, formatImpact, formatNumber, formatPct, formatScore } from '../lib/formatters';
import { OperatorView } from '../lib/api';
import { QueueMethod } from '../types/dispatch';
import { OutageItem } from '../types/outage';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';

type Tab = 'plan' | 'compare' | 'approved' | 'deferred' | 'flagged';
// Outage-list views over the session operator actions: the Repair list holds outages not yet acted on.
const LIST_VIEWS: Partial<Record<Tab, OperatorView>> = { plan: 'none', approved: 'approved', deferred: 'deferred', flagged: 'flagged' };
const LIST_EMPTY: Record<OperatorView, string> = {
  none: 'No outages left to act on in this borough',
  approved: 'No approved outages',
  deferred: 'No deferred outages',
  flagged: 'No flagged outages',
};

const OutageRows: React.FC<{ items: OutageItem[] }> = ({ items }) => {
  const navigate = useNavigate();
  return (
    <div className="table-wrap">
      <table className="data-table">
        <thead>
          <tr>
            <th scope="col">Rank</th>
            <th scope="col">Outage ID</th>
            <th scope="col">Location</th>
            <th scope="col">Borough</th>
            <th scope="col" className="num">Duration</th>
            <th scope="col" className="num">Priority score</th>
            <th scope="col">Tier</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((o) => (
            <tr key={o.outage_id} className="row-link" tabIndex={0} onClick={() => navigate(`/outages/${o.outage_id}`)} onKeyDown={(e) => e.key === 'Enter' && navigate(`/outages/${o.outage_id}`)}>
              <td className="tabular-nums text-ink-soft">{o.optimization_rank ? `#${o.optimization_rank}` : '–'}</td>
              <td className="font-mono font-medium">{o.outage_id}</td>
              <td className="max-w-[300px] truncate text-ink-soft" title={o.location_desc}>{o.location_desc}</td>
              <td>{o.borough}</td>
              <td className="num text-ink-soft">{formatDays(o.duration_days)}</td>
              <td className="num font-bold">{formatScore(o.priority_score)}</td>
              <td><TierBadge tier={o.priority_tier} /></td>
              <td><DecisionBadge status={o.dispatch_status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

export const Prioritization: React.FC = () => {
  const { borough } = useGlobalFilters();
  const { theme } = useTheme();
  const colors = chartColors(theme);
  const [tab, setTab] = useState<Tab>('plan');
  const [listPage, setListPage] = useState(1);
  const [method, setMethod] = useState<QueueMethod>('LightSafe');
  const [queuePage, setQueuePage] = useState(1);

  const { data: opt, isLoading, isError, refetch } = useOptimization();
  const { data: plan } = useOptimizationPlan({ decision: 'recommended', borough, pageSize: 50 });
  // Repair list: recommended repairs in plan-rank order, then deferred outages in priority order (existing backend order),
  // minus outages with an operator action. Approved / Deferred / Flagged: in the order the actions were taken.
  const view = LIST_VIEWS[tab];
  const { data: list } = useOptimizationPlan({ borough, page: listPage, pageSize: 50, action: view ?? 'none' });
  // Tab counts (one row per request; total_count only).
  const { data: nNone } = useOptimizationPlan({ borough, page: 1, pageSize: 1, action: 'none' });
  const { data: nApproved } = useOptimizationPlan({ borough, page: 1, pageSize: 1, action: 'approved' });
  const { data: nDeferred } = useOptimizationPlan({ borough, page: 1, pageSize: 1, action: 'deferred' });
  const { data: nFlagged } = useOptimizationPlan({ borough, page: 1, pageSize: 1, action: 'flagged' });
  const { data: comparison } = useComparison();
  const { data: queue } = useQueue({ method, borough, page: queuePage, pageSize: 20 });

  // A new view (see onChange below) or borough starts on its first page.
  useEffect(() => setListPage(1), [borough]);
  // If the current page no longer exists (e.g. outages were acted on), move to the last valid page.
  useEffect(() => {
    if (list && list.total_pages > 0 && listPage > list.total_pages) setListPage(list.total_pages);
  }, [list, listPage]);

  const count = (d?: { total_count: number }) => (d ? ` (${formatNumber(d.total_count)})` : '');
  const tabs = [
    { id: 'plan', label: `Repair list${count(nNone)}` },
    { id: 'compare', label: 'Compare with FIFO' },
    { id: 'approved', label: `Approved${count(nApproved)}` },
    { id: 'deferred', label: `Deferred${count(nDeferred)}` },
    { id: 'flagged', label: `Flagged${count(nFlagged)}` },
  ] as { id: Tab; label: string }[];

  const controls = <HeaderControls><BoroughControl /></HeaderControls>;

  const exportPlan = () => {
    if (!plan || !opt) return;
    const blob = new Blob([JSON.stringify({ summary: opt, recommended_repairs: plan.items }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `lightsafe_recommended_repairs_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (isLoading) return <>{controls}<Loading label="Loading dispatch plan…" /></>;
  if (isError || !opt) return <>{controls}<ErrorState onRetry={() => refetch()} /></>;
  if (!opt.available) {
    return <>{controls}<div className="page"><div className="card"><EmptyState tone="warn" title="No dispatch plan available" detail="The dispatch plan results are not available right now. Check that the LightSafe service is running." /></div></div></>;
  }

  const pct = opt.daily_budget > 0 ? Math.min(100, (opt.budget_used / opt.daily_budget) * 100) : 0;
  const ms = comparison?.available ? comparison.milestones : null;

  return (
    <div className="page space-y-5">
      {controls}
      <PageHeader
        title="Dispatch Plan"
        description={`With a daily budget of ${opt.daily_budget} repairs and a minimum in every borough, these are the outages to repair first. Everything else is deferred.`}
        actions={<button type="button" className="btn" onClick={exportPlan}><Download size={14} aria-hidden /> Export plan</button>}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Stat label="Recommended repairs" value={formatNumber(opt.n_selected)} hint="Selected for repair now" />
        <Stat label="Budget used" value={`${opt.budget_used} / ${opt.daily_budget}`} hint={`${opt.budget_remaining} remaining`}>
          <div className="progress mt-1" role="progressbar" aria-valuenow={opt.budget_used} aria-valuemin={0} aria-valuemax={opt.daily_budget} aria-label="Budget used"><span style={{ width: `${pct}%` }} /></div>
        </Stat>
        <Stat label="Deferred" value={formatNumber(opt.n_deferred)} hint="Scored outages not in this plan" />
      </div>

      <Tabs tabs={tabs} value={tab} onChange={(t) => { setTab(t); setListPage(1); }} label="Dispatch plan sections" />

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="space-y-4 animate-fade-in" key={tab}>
        {view && (
          list && list.items.length > 0 ? (
            <>
              <OutageRows items={list.items} />
              <Pager page={list.page} pages={list.total_pages} total={list.total_count} onPage={setListPage} label="outages" />
            </>
          ) : list ? <div className="card"><EmptyState title={LIST_EMPTY[view]} /></div> : <Loading label="Loading outages…" />
        )}

        {tab === 'compare' && (
          !comparison?.available ? <div className="card"><EmptyState title="Comparison not available" detail="The queue comparison file was not found." /></div> : (
            <>
              <p className="text-[13px] text-ink-soft">
                Same repair capacity ({comparison.repair_capacity_per_day} per day), two ways of choosing what to fix first: LightSafe works down the priority score; FIFO fixes the oldest report first. Both clear the same {formatNumber(comparison.total_repairs)} outages.
                On day one LightSafe collects {formatImpact(comparison.day_one.lightsafe)} priority points against {formatImpact(comparison.day_one.fifo)} for FIFO
                {comparison.day_one.improvement_pct !== null ? ` (${formatPct(comparison.day_one.improvement_pct)})` : ''}.
              </p>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {ms && ([
                  ['Days to collect half of all priority points', ms.time_to_50pct_impact_days],
                  ['Days to collect 90% of all priority points', ms.time_to_90pct_impact_days],
                ] as const).map(([label, p]) => (
                  <div key={label} className="card p-4">
                    <p className="eyebrow">{label}</p>
                    <div className="mt-2 flex items-baseline gap-6">
                      <div><p className="text-2xl font-extrabold tabular-nums text-ink">{formatNumber(p.lightsafe)}</p><p className="text-xs text-ink-soft">LightSafe</p></div>
                      <div><p className="text-2xl font-extrabold tabular-nums text-ink-soft">{formatNumber(p.fifo)}</p><p className="text-xs text-ink-soft">FIFO</p></div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="card p-5">
                <h2 className="card-title">Priority points collected over time</h2>
                <div className="mt-3 h-72 w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={comparison.curve} margin={{ top: 4, right: 16, left: 4, bottom: 20 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                      <XAxis dataKey="day" type="number" domain={[0, 'dataMax']} tick={{ fontSize: 11, fill: colors.axis }} stroke={colors.grid} tickCount={8} label={{ value: 'Days', position: 'insideBottom', offset: -12, fontSize: 11, fill: colors.axis }} />
                      <YAxis tick={{ fontSize: 11, fill: colors.axis }} stroke={colors.grid} tickFormatter={(v) => formatNumber(v)} width={64} />
                      <Tooltip contentStyle={colors.tooltip} formatter={(v: number) => formatImpact(v)} labelFormatter={(d) => `Day ${d}`} />
                      <Legend verticalAlign="top" align="right" height={28} wrapperStyle={{ fontSize: 12, color: colors.axis }} />
                      <Line type="monotone" dataKey="lightsafe" name="LightSafe" stroke={colors.primary} strokeWidth={2.25} dot={false} />
                      <Line type="monotone" dataKey="fifo" name="FIFO" stroke={colors.secondary} strokeWidth={2.25} dot={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              <details className="card group">
                <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-3.5 text-sm font-bold text-ink transition-colors hover:bg-line-soft/50">
                  Browse the repair queue order
                  <span className="text-xs font-medium text-ink-soft group-open:hidden">Show</span>
                  <span className="hidden text-xs font-medium text-ink-soft group-open:inline">Hide</span>
                </summary>
                <div className="space-y-3 border-t border-line p-5">
                  <div className="flex gap-2" role="group" aria-label="Queue order">
                    {(['LightSafe', 'FIFO'] as QueueMethod[]).map((m) => (
                      <button key={m} type="button" aria-pressed={method === m} className={`btn btn-sm ${method === m ? 'btn-primary' : ''}`} onClick={() => { setMethod(m); setQueuePage(1); }}>
                        {m === 'LightSafe' ? 'LightSafe (by priority)' : 'FIFO (oldest first)'}
                      </button>
                    ))}
                  </div>
                  {queue && queue.items.length > 0 ? (
                    <>
                      <div className="table-wrap">
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th scope="col" className="num">Queue position</th>
                              <th scope="col" className="num">Repair day</th>
                              <th scope="col">Outage ID</th>
                              <th scope="col">Location</th>
                              <th scope="col" className="num">Priority score</th>
                            </tr>
                          </thead>
                          <tbody>
                            {queue.items.map((q) => (
                              <tr key={q.outage_id}>
                                <td className="num tabular-nums">{formatNumber(q.queue_rank)}</td>
                                <td className="num text-ink-soft">{q.repair_day}</td>
                                <td className="font-mono font-medium">{q.outage_id}</td>
                                <td className="max-w-[280px] truncate text-ink-soft" title={q.location_desc ?? ''}>{q.location_desc}</td>
                                <td className="num font-bold">{formatScore(q.priority_score)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <Pager page={queue.page} pages={queue.total_pages} total={queue.total_count} onPage={setQueuePage} label="queued outages" />
                    </>
                  ) : <EmptyState title="Queue is empty for this filter" />}
                </div>
              </details>
            </>
          )
        )}
      </div>

      <ScoreNote />
    </div>
  );
};
