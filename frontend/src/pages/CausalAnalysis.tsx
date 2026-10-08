import React from 'react';
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useCausalOverview } from '../hooks/useCausal';
import { EmptyState, ErrorState, Loading, PageHeader } from '../components/ui';
import { DidFormulas } from '../components/DidFormulas';
import { EffectEstimate } from '../types/causal';
import { formatNumber } from '../lib/formatters';
import { chartColors } from '../lib/chartTheme';
import { useTheme } from '../context/ThemeContext';

const EFFECT_LABELS: Record<string, string> = { direct: 'Within 100 m', displacement: '100–250 m ring', net: 'Combined (net)' };
const PERIOD_LABELS: Record<string, string> = { post: 'after the outage', during: 'during the outage' };
/** e.g. "net_post" -> "Combined (net), after the outage"; unknown names are shown as stored. */
const effectLabel = (name: string) => {
  const [effect, period] = name.split('_');
  return EFFECT_LABELS[effect] && PERIOD_LABELS[period] ? `${EFFECT_LABELS[effect]}, ${PERIOD_LABELS[period]}` : name;
};

const f = (v: number | null | undefined, d = 4) => (v === null || v === undefined ? '–' : v.toFixed(d));

const Finding: React.FC<{ title: string; range: string; e?: EffectEstimate }> = ({ title, range, e }) => (
  <div className="card flex flex-col gap-1 p-4">
    <span className="eyebrow">{title}</span>
    <span className="text-xl font-extrabold tabular-nums text-ink">{e ? f(e.estimate) : '–'}</span>
    {e && (
      <>
        <span className="text-xs tabular-nums text-ink-soft">95% range {f(e.ci_lower)} to {f(e.ci_upper)}</span>
        <span className={`badge mt-1 w-fit max-w-full whitespace-normal ${e.significant_at_5pct ? 'badge-medium' : 'badge-neutral'}`}>
          {e.significant_at_5pct ? `Distinguishable from zero (p = ${f(e.p_value, 3)})` : `Not distinguishable from zero (p = ${f(e.p_value, 2)})`}
        </span>
      </>
    )}
    <span className="mt-1 text-xs text-ink-soft">{range}</span>
  </div>
);

export const CausalAnalysis: React.FC = () => {
  const { data: causal, isLoading, isError, refetch } = useCausalOverview();
  const { theme } = useTheme();
  const colors = chartColors(theme);

  if (isLoading) return <Loading label="Loading evidence…" />;
  if (isError) return <ErrorState onRetry={() => refetch()} />;
  if (!causal || !causal.available) {
    return <div className="page"><div className="card"><EmptyState tone="warn" title="Evidence tables are not available" detail="The evidence results are not available right now." /></div></div>;
  }

  const { direct, displacement, net, displacement_proportion } = causal.periods.post;
  const ds = causal.dataset_summary;
  const chartData = causal.event_study.map((p) => ({
    week: p.rel_week,
    coefficient: p.coefficient,
    lower: p.ci_lower,
    upper: p.ci_upper,
  }));

  return (
    <div className="page max-w-5xl space-y-8">
      <PageHeader
        title="Legacy DiD Summary"
        description="Causal evidence from LightSafe's earlier paired difference-in-differences design: what the data say about street-light outages and nearby crime, and how much weight the priority score should carry. These results are provisional and are shown exactly as stored."
      />

      <DidFormulas />

      <section aria-labelledby="found" className="space-y-3">
        <h2 id="found" className="text-base font-bold text-ink">What the analysis found</h2>
        <p className="text-[13px] text-ink-soft">
          Locations near an outage were compared with similar locations without one. Figures are the change in the crime count per location from the 14-day window before the outage was reported to the 14-day window after it was closed (positive means more crime).
        </p>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <Finding title="Within 100 m" range="Directly around the light" e={direct} />
          <Finding title="100–250 m ring" range="Nearby area (possible displacement)" e={displacement} />
          <Finding title="Combined (net)" range="Within 250 m overall" e={net} />
        </div>
      </section>

      <section aria-labelledby="means" className="space-y-2">
        <h2 id="means" className="text-base font-bold text-ink">What it means</h2>
        <ul className="list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed text-ink">
          {direct && (
            <li>
              Within 100 m the estimate is {f(direct.estimate)}, {direct.significant_at_5pct ? 'which is statistically distinguishable from zero' : 'which cannot be told apart from zero'}.
            </li>
          )}
          {net && (
            <li>
              Once the surrounding ring is included, the combined estimate is {f(net.estimate)} with a range of {f(net.ci_lower)} to {f(net.ci_upper)}.{' '}
              {net.ci_includes_zero ? <strong>That range includes zero, so these data do not show that outages change crime around them.</strong> : 'That range excludes zero.'}
            </li>
          )}
          <li>
            Because the combined estimate is used as a single constant in the priority score, the ranking is driven mostly by recent crime near the outage and how long it has lasted, not by a measured effect of the light itself.
          </li>
        </ul>
      </section>

      <section aria-labelledby="limits" className="space-y-2">
        <h2 id="limits" className="text-base font-bold text-ink">Important limitations</h2>
        <div className="notice notice-warn">
          <ul className="list-disc space-y-1.5 pl-5">
            <li>These results show association in historical data. They do not prove that fixing a light prevents crime.</li>
            <li>The priority score is a ranking tool. It is not a probability of crime and not a forecast of crimes prevented.</li>
            <li>Outage start and end times come from 311 reports, which can differ from when a light actually failed or was fixed.</li>
            <li>Results are provisional. Use them alongside local knowledge and judgement.</li>
          </ul>
        </div>
      </section>

      <details className="card group">
        <summary className="flex cursor-pointer list-none items-center justify-between px-5 py-3.5 text-sm font-bold text-ink transition-colors hover:bg-line-soft/50">
          Technical details
          <span className="text-xs font-medium text-ink-soft group-open:hidden">Show</span>
          <span className="hidden text-xs font-medium text-ink-soft group-open:inline">Hide</span>
        </summary>
        <div className="space-y-6 border-t border-line p-5">
          <div className="grid grid-cols-2 gap-x-8 gap-y-1 text-[13px] sm:grid-cols-4">
            {ds.rows !== undefined && <div><p className="eyebrow">Observations</p><p className="font-semibold tabular-nums">{formatNumber(ds.rows)}</p></div>}
            {ds.unique_pairs !== undefined && <div><p className="eyebrow">Matched pairs</p><p className="font-semibold tabular-nums">{formatNumber(ds.unique_pairs)}</p></div>}
            {displacement_proportion !== null && <div><p className="eyebrow">Displacement ratio</p><p className="font-semibold tabular-nums">{f(displacement_proportion, 3)}</p></div>}
            {causal.method && <div className="col-span-2 sm:col-span-1"><p className="eyebrow">Method</p><p className="font-semibold">Paired difference-in-differences</p></div>}
          </div>

          {chartData.length > 0 && (
            <div>
              <h3 className="card-title">Weekly estimates around the outage</h3>
              <p className="mb-2 text-xs text-ink-soft">Values near zero before week 0 are what the method expects if treated and comparison locations were on similar paths.</p>
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 8, right: 16, left: 4, bottom: 18 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                    <XAxis dataKey="week" tick={{ fontSize: 11, fill: colors.axis }} stroke={colors.grid} label={{ value: 'Weeks relative to outage', position: 'insideBottom', offset: -10, fontSize: 11, fill: colors.axis }} />
                    <YAxis tick={{ fontSize: 11, fill: colors.axis }} stroke={colors.grid} width={56} />
                    <Tooltip contentStyle={colors.tooltip} formatter={(v: number) => f(v, 5)} labelFormatter={(w) => `Week ${w}`} />
                    <ReferenceLine y={0} stroke={colors.axis} strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="upper" name="Upper 95%" stroke={colors.band} strokeWidth={1} dot={false} />
                    <Line type="monotone" dataKey="lower" name="Lower 95%" stroke={colors.band} strokeWidth={1} dot={false} />
                    <Line type="monotone" dataKey="coefficient" name="Estimate" stroke={colors.accent} strokeWidth={2.25} dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Estimate</th>
                  <th scope="col" className="num">Value</th>
                  <th scope="col" className="num">Std. error</th>
                  <th scope="col" className="num">p-value</th>
                </tr>
              </thead>
              <tbody>
                {causal.estimates_table.map((r) => (
                  <tr key={r.effect_name}>
                    <td className="text-xs">{effectLabel(r.effect_name)}</td>
                    <td className="num">{f(r.estimate, 5)}</td>
                    <td className="num text-ink-soft">{f(r.standard_error, 5)}</td>
                    <td className="num text-ink-soft">{f(r.p_value, 3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>
    </div>
  );
};
