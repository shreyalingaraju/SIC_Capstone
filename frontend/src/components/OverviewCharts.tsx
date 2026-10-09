import React, { useId } from 'react';
import { Link } from 'react-router-dom';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { useTheme } from '../context/ThemeContext';
import { chartColors } from '../lib/chartTheme';
import { formatDays, formatScore } from '../lib/formatters';
import { OutageItem } from '../types/outage';
import { BoroughRow } from '../types/dispatch';
import { TierBadge } from './ui';

const MINT = '#21e58a';
const MINT_LIGHT = '#8dffc4';
const VIOLET = '#b45cff';
const MAGENTA = '#e054ff';

/** Glassy tooltip shell shared by both widgets. */
const TipShell: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="rounded-xl border border-signal/30 bg-night-deep/90 px-3 py-2 text-xs shadow-[0_8px_28px_rgba(0,0,0,0.55),0_0_18px_-6px_rgb(var(--c-signal)/0.5)] backdrop-blur">
    {children}
  </div>
);

/* ------------------------------------------------------------------ top repairs */
interface Point { rank: string; score: number; item: OutageItem }

const RepairTip: React.FC<{ active?: boolean; payload?: { payload: Point }[] }> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const { item } = payload[0].payload;
  return (
    <TipShell>
      <p className="font-mono text-[11px] font-semibold text-ink">{item.outage_id}</p>
      <p className="max-w-[220px] truncate text-ink-soft">{item.location_desc}</p>
      <dl className="mt-1.5 grid grid-cols-[auto_auto] items-center gap-x-4 gap-y-0.5">
        <dt className="text-ink-soft">Rank</dt><dd className="text-right font-semibold tabular-nums">#{item.optimization_rank ?? '–'}</dd>
        <dt className="text-ink-soft">Score</dt><dd className="text-right font-bold tabular-nums" style={{ color: MINT_LIGHT }}>{formatScore(item.priority_score)}</dd>
        <dt className="text-ink-soft">Tier</dt><dd className="text-right"><TierBadge tier={item.priority_tier} /></dd>
        <dt className="text-ink-soft">Outage</dt><dd className="text-right tabular-nums">{formatDays(item.duration_days)}</dd>
      </dl>
    </TipShell>
  );
};

export const TopRepairsChart: React.FC<{ items: OutageItem[]; onOpen: (id: string) => void; max?: number }> = ({ items, onOpen, max = 10 }) => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const uid = useId().replace(/:/g, '');
  const data: Point[] = items
    .filter((o) => o.priority_score !== null)
    .slice(0, max)
    .map((o, i) => ({ rank: `#${o.optimization_rank ?? i + 1}`, score: o.priority_score as number, item: o }));
  const peak = data.reduce<Point | null>((m, p) => (m === null || p.score > m.score ? p : m), null);

  return (
    <section className="card flex flex-col" aria-labelledby="top-repairs">
      <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
        <div>
          <h2 id="top-repairs" className="card-title">Top recommended repairs</h2>
          <p className="mt-0.5 text-xs text-ink-soft">Priority score of the highest-ranked repairs in the current plan.</p>
        </div>
        <Link to="/priority" className="text-xs font-semibold text-brand transition-colors hover:underline">View all</Link>
      </div>
      {data.length === 0 ? (
        <p className="px-5 py-14 text-center text-xs text-ink-soft">No recommended repairs for this selection.</p>
      ) : (
        <div className="relative h-[320px] animate-fade-in px-2 pb-3 pt-4" role="img" aria-label={`Priority scores of the top ${data.length} recommended repairs; highest ${peak ? formatScore(peak.score) : ''}`}>
          <ResponsiveContainer>
            <AreaChart data={data} margin={{ top: 34, right: 22, bottom: 4, left: 0 }} onClick={(s) => {
              const p = (s as { activePayload?: { payload: Point }[] } | null)?.activePayload?.[0]?.payload;
              if (p) onOpen(p.item.outage_id);
            }}>
              <defs>
                <linearGradient id={`${uid}-fill`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={MINT} stopOpacity={0.5} />
                  <stop offset="55%" stopColor={MINT} stopOpacity={0.14} />
                  <stop offset="100%" stopColor={VIOLET} stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id={`${uid}-line`} x1="0" y1="0" x2="1" y2="0">
                  <stop offset="0%" stopColor={MINT_LIGHT} />
                  <stop offset="100%" stopColor={MINT} />
                </linearGradient>
                <filter id={`${uid}-glow`} x="-20%" y="-40%" width="140%" height="180%">
                  <feGaussianBlur stdDeviation="4" result="b" />
                  <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
                </filter>
              </defs>
              <CartesianGrid stroke={c.grid} strokeOpacity={0.55} vertical={false} />
              <XAxis dataKey="rank" padding={{ left: 22, right: 14 }} stroke={c.axis} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
              <YAxis domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} stroke={c.axis} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} width={34} />
              <Tooltip content={<RepairTip />} cursor={{ stroke: VIOLET, strokeOpacity: 0.55, strokeDasharray: '4 4' }} />
              <Area
                type="monotone" dataKey="score" stroke={`url(#${uid}-line)`} strokeWidth={3} fill={`url(#${uid}-fill)`}
                style={{ filter: `drop-shadow(0 0 6px ${MINT})` }}
                dot={{ r: 3.5, fill: '#06130e', stroke: MINT_LIGHT, strokeWidth: 2 }}
                activeDot={{ r: 7, fill: '#06130e', stroke: VIOLET, strokeWidth: 3, style: { filter: `drop-shadow(0 0 8px ${VIOLET})`, cursor: 'pointer' } }}
                isAnimationActive={false}
              />
              {peak && (
                <ReferenceDot
                  x={peak.rank} y={peak.score} r={8} fill="#06130e" stroke={MINT_LIGHT} strokeWidth={3}
                  style={{ filter: `drop-shadow(0 0 10px ${MINT})` }}
                  label={{ value: formatScore(peak.score), position: 'top', fill: MINT_LIGHT, fontSize: 12, fontWeight: 800 }}
                />
              )}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
};

/* ------------------------------------------------------------------ repairs by borough */
const BoroughTip: React.FC<{ active?: boolean; label?: string; payload?: { name: string; value: number; color: string }[] }> = ({ active, label, payload }) => {
  if (!active || !payload?.length) return null;
  return (
    <TipShell>
      <p className="mb-1 font-semibold text-ink">{label}</p>
      {payload.map((p) => (
        <p key={p.name} className="flex items-center gap-2 tabular-nums">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: p.color, boxShadow: `0 0 8px ${p.color}` }} />
          <span className="text-ink-soft">{p.name}</span><span className="ml-auto font-bold">{p.value}</span>
        </p>
      ))}
    </TipShell>
  );
};

export const BoroughRepairsChart: React.FC<{ rows: BoroughRow[] | null; allSatisfied?: boolean }> = ({ rows, allSatisfied }) => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const uid = useId().replace(/:/g, '');
  const data = (rows ?? []).map((r) => ({ borough: r.borough, planned: r.selected, minimum: r.quota_minimum ?? 0, hasMin: r.quota_minimum !== null }));
  const short = data.filter((d) => d.hasMin && d.planned < d.minimum);
  const plannedTotal = data.reduce((a, d) => a + d.planned, 0);
  const minTotal = data.reduce((a, d) => a + d.minimum, 0);
  const upper = Math.max(1, ...data.map((d) => Math.max(d.planned, d.minimum)));
  const met = allSatisfied ?? short.length === 0;

  return (
    <section className="card flex flex-col" aria-labelledby="alloc">
      <div className="border-b border-line px-5 py-3.5">
        <h2 id="alloc" className="card-title">Repairs by borough</h2>
        <p className="mt-0.5 text-xs text-ink-soft">Planned repairs against each borough&apos;s minimum requirement.</p>
      </div>
      {data.length === 0 ? (
        <p className="px-5 py-14 text-center text-xs text-ink-soft">Borough allocation is not available.</p>
      ) : (
        <>
          <div className="h-[260px] animate-fade-in px-2 pt-3" role="img" aria-label="Planned and minimum required repairs per borough">
            <ResponsiveContainer>
              <BarChart data={data} layout="vertical" barGap={3} barCategoryGap="22%" margin={{ top: 4, right: 20, bottom: 4, left: 4 }}>
                <defs>
                  <linearGradient id={`${uid}-p`} x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor="#14b86d" /><stop offset="100%" stopColor={MINT_LIGHT} />
                  </linearGradient>
                  <linearGradient id={`${uid}-m`} x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor="#7a35e0" /><stop offset="100%" stopColor={MAGENTA} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke={c.grid} strokeOpacity={0.55} horizontal={false} />
                <XAxis type="number" domain={[0, Math.ceil(upper * 1.1)]} allowDecimals={false} stroke={c.axis} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} />
                <YAxis type="category" dataKey="borough" stroke={c.axis} tickLine={false} axisLine={false} tick={{ fontSize: 11 }} width={78} />
                <Tooltip content={<BoroughTip />} cursor={{ fill: 'rgba(180, 92, 255, 0.08)' }} />
                <Legend verticalAlign="top" align="right" iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 11, paddingBottom: 6 }} />
                <Bar dataKey="planned" name="Planned" fill={`url(#${uid}-p)`} radius={[0, 6, 6, 0]} barSize={9}
                  style={{ filter: `drop-shadow(0 0 5px ${MINT})` }} isAnimationActive={false} />
                <Bar dataKey="minimum" name="Minimum required" fill={`url(#${uid}-m)`} radius={[0, 6, 6, 0]} barSize={9}
                  style={{ filter: `drop-shadow(0 0 5px ${VIOLET})` }} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="mt-auto flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line-soft px-5 py-3 text-xs">
            <span className={`badge ${met ? 'badge-ok' : 'badge-high'}`}>{met ? 'All minimums met' : `${short.length} below minimum`}</span>
            <span className="text-ink-soft tabular-nums">{plannedTotal} planned · {minTotal} required in total</span>
            <Link to="/priority" className="ml-auto font-semibold text-brand hover:underline">Details</Link>
          </div>
        </>
      )}
    </section>
  );
};
