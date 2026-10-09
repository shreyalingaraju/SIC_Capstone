import React, { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import {
  Bar, BarChart, CartesianGrid, ErrorBar, Legend, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis, ReferenceLine,
} from 'recharts';
import {
  fetchSynCausal, fetchSynMethodology, fetchSynOverview, fetchSynPopulation, fetchSynPrioritization, fetchSynRelationships,
  fetchSynRiskModel, fetchSynScenarios, fetchSynWards, fetchSynDecision, fetchSynPopulationRisk,
} from '../lib/api';
import { EmptyState, Loading, PageHeader, Pager, SelectControl, Stat, Tabs } from '../components/ui';
import { useTheme } from '../context/ThemeContext';
import { useProfile } from '../context/ProfileContext';
import { chartColors } from '../lib/chartTheme';
import { formatNumber } from '../lib/formatters';
import type { PrioItem, Ward } from '../types/synthetic';
import { RepairPressure } from '../components/RepairPressure';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'geo', label: 'Geographic view' },
  { id: 'risk', label: 'Risk & impact' },
  { id: 'causal', label: 'Causal vs prediction' },
  { id: 'priority', label: 'Prioritization' },
  { id: 'scenario', label: 'Scenarios' },
  { id: 'method', label: 'Methodology' },
] as const;
type TabId = (typeof TABS)[number]['id'];

const BASEMAP = {
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
};
const RISK_COLOR: Record<string, string> = { High: '#f06f51', Moderate: '#eeb64b', Low: '#6fa6a0', 'n/a': '#94a3b8' };

const n1 = (v: number | null | undefined, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? '–' : v.toFixed(d));
const n3 = (v: number | null | undefined) => n1(v, 3);
const pct = (v: number | null | undefined) => (v === null || v === undefined ? '–' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`);

const SyntheticTag: React.FC = () => (
  <span className="badge badge-medium" title="All values on this page are simulated">Synthetic data</span>
);

const Card: React.FC<{ title: string; sub?: React.ReactNode; children: React.ReactNode; className?: string }> = ({ title, sub, children, className = '' }) => (
  <section className={`card ${className}`}>
    <div className="border-b border-line px-5 py-3.5">
      <h2 className="card-title">{title}</h2>
      {sub && <p className="mt-0.5 text-xs text-ink-soft">{sub}</p>}
    </div>
    <div className="p-5">{children}</div>
  </section>
);

function useSyn<T>(key: string, fn: () => Promise<T>) {
  return useQuery<T>({ queryKey: ['syn', key], queryFn: fn, staleTime: 1000 * 60 * 5 });
}

/* ------------------------------------------------------------------ overview */
const OverviewTab: React.FC = () => {
  const { data, isLoading, isError } = useSyn('overview', fetchSynOverview);
  const { data: risk } = useSyn('riskmodel', fetchSynRiskModel);
  if (isLoading) return <Loading />;
  if (isError || !data || !data.available) return <EmptyState tone="warn" title="Synthetic outputs not found" detail={data?.message ?? 'Run scripts/synthetic/run_pipeline.py, then restart the API with LIGHTSAFE_PROFILE=karnataka_synthetic.'} />;
  const k = data.kpis;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Stat label="Population covered" value={formatNumber(k.population_covered)} hint={`${k.wards} wards in ${k.cities} cities (simulated)`} />
        <Stat label="Outages analysed" value={formatNumber(k.outages_scored)} hint={`${formatNumber(k.outages_cleaned)} cleaned, ${k.data_period[0]} to ${k.data_period[1]}`} />
        <Stat label="High-risk wards" value={formatNumber(k.high_risk_wards)} hint="Top third by ML risk score" />
        <Stat label="High / medium priority" value={formatNumber(k.high_medium_priority_outages)} hint={`${k.high_priority_outages} high; decision-layer score`} />
        <Stat label="Repairs recommended per day" value={formatNumber(k.repairs_recommended_per_day)} hint={`Constrained plan, capacity ${k.daily_repair_capacity_k}/day`} />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Stat label="Causal AI: average effect of an outage" value={`+${n3(k.tau_value)} crimes`} hint={`Extra night crimes within 100 m while dark, per outage (average, matched-control DiD).`} />
        <Stat label="Predicted impact, day 30 (index points)" value={formatNumber(Math.round(k.predicted_impact_priority_points_day30 ?? 0))} hint={`Priority queue vs ${formatNumber(Math.round(k.fifo_impact_priority_points_day30 ?? 0))} for FIFO. Index points, not crimes.`} />
        <Stat label="Estimated improvement vs FIFO (benchmark)" value={pct(k.benchmark_vs_fifo_pct_stress)} hint={`Change in planted extra crime exposure under a stressed scenario; ${pct(k.benchmark_vs_fifo_pct_baseline)} at baseline capacity. Uses synthetic ground truth.`} />
      </div>
      <div className="notice notice-info space-y-1.5">
        <p><b>How to read this.</b> <b>Causal AI</b> estimates the average effect of an outage on night crime (+{n3(k.tau_value)} per outage here). <b>XGBoost</b> predicts where night crime is high
          around an outage{risk && risk.available ? ` (held-out MAE ${n3(risk.mae)} vs ${n3(risk.mae_baseline_mean_rate)} for a constant guess; top 10% of predictions hold ${n1(risk.top_decile_capture * 100, 0)}% of crime)` : ''} and is a pattern, not a cause.
          The <b>decision layer</b> ranks repairs using the XGBoost risk (weighted by how much of that crime the causal estimate attributes to outages: {n1(k.causal_weight * 100, 0)}%), plus population exposure, vulnerability, repeat failures and repair efficiency.</p>
        <p>All values are from a synthetic Karnataka dataset: demonstration outputs, not Karnataka public-safety findings.</p>
      </div>
      <RepairPressure borough="All" />
    </div>
  );
};

/* ------------------------------------------------------------------ geographic */
const GeoTab: React.FC = () => {
  const { theme } = useTheme();
  const { mapCenter, mapZoom } = useProfile();
  const { data, isLoading } = useSyn('wards', fetchSynWards);
  const [city, setCity] = useState('All');
  const [sel, setSel] = useState<Ward | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  const items = useMemo(() => (data?.items ?? []).filter((w) => city === 'All' || w.city === city), [data, city]);
  const cities = useMemo(() => ['All', ...Array.from(new Set((data?.items ?? []).map((w) => w.city))).sort()], [data]);

  useEffect(() => {
    if (!ref.current || mapRef.current) return;
    let mounted = true;
    let ro: ResizeObserver | null = null;
    (async () => {
      const mgl = await import('maplibre-gl');
      if (!mounted || !ref.current) return;
      const map = new mgl.Map({ container: ref.current, style: BASEMAP[themeRef.current], center: mapCenter, zoom: mapZoom, attributionControl: false });
      map.addControl(new mgl.NavigationControl({ showCompass: false }), 'bottom-right');
      ro = new ResizeObserver(() => map.resize());
      ro.observe(ref.current);
      const add = () => {
        if (map.getSource('wards')) return;
        map.addSource('wards', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
        map.addLayer({
          id: 'wards-halo', type: 'circle', source: 'wards',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'population']], 100, 10, 400, 20, 800, 34],
            'circle-color': ['match', ['get', 'risk_category'], 'High', RISK_COLOR.High, 'Moderate', RISK_COLOR.Moderate, 'Low', RISK_COLOR.Low, RISK_COLOR['n/a']],
            'circle-opacity': 0.2, 'circle-blur': 1,
          },
        });
        map.addLayer({
          id: 'wards-layer', type: 'circle', source: 'wards',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['sqrt', ['get', 'population']], 100, 4, 400, 9, 800, 16],
            'circle-color': ['match', ['get', 'risk_category'], 'High', RISK_COLOR.High, 'Moderate', RISK_COLOR.Moderate, 'Low', RISK_COLOR.Low, RISK_COLOR['n/a']],
            'circle-opacity': 0.85, 'circle-stroke-width': 1, 'circle-stroke-color': 'rgba(15,29,25,0.8)',
          },
        });
        map.on('click', 'wards-layer', (e: any) => setSel(e.features?.[0]?.properties ? ({ ...e.features[0].properties } as Ward) : null));
        setReady((r) => !r || r);
      };
      map.on('load', () => { add(); mapRef.current = map; setReady(true); });
      map.on('styledata', () => { try { add(); } catch { /* retried on next event */ } });
    })();
    return () => { mounted = false; ro?.disconnect(); mapRef.current?.remove(); mapRef.current = null; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const src = map.getSource('wards');
    if (!src) return;
    src.setData({
      type: 'FeatureCollection',
      features: items.map((w) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [w.longitude, w.latitude] }, properties: w })),
    });
    if (city !== 'All' && items.length) {
      const lon = items.reduce((a, w) => a + w.longitude, 0) / items.length;
      const lat = items.reduce((a, w) => a + w.latitude, 0) / items.length;
      map.flyTo({ center: [lon, lat], zoom: 10.5 });
    } else map.flyTo({ center: mapCenter, zoom: mapZoom });
  }, [items, ready, city]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { mapRef.current?.setStyle?.(BASEMAP[theme]); }, [theme]);

  if (isLoading) return <Loading />;
  const top = [...items].sort((a, b) => (b.risk_score ?? -1) - (a.risk_score ?? -1)).slice(0, 12);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <SelectControl label="City" value={city} onChange={setCity} options={cities.map((c) => ({ value: c, label: c === 'All' ? 'All cities' : c }))} />
        <span className="flex items-center gap-3 text-xs text-ink-soft">
          Colour = ML risk category; size = population.
          {(['High', 'Moderate', 'Low'] as const).map((r) => (
            <span key={r} className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: RISK_COLOR[r] }} />{r}</span>
          ))}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="card relative h-[520px] overflow-hidden xl:col-span-2">
          <div ref={ref} className="h-full w-full" aria-label="Map of wards" />
        </div>
        <div className="card p-4 text-sm">
          {sel ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between"><h3 className="card-title">{String(sel.ward_id)} · {sel.city}</h3><SyntheticTag /></div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                {([
                  ['Population', formatNumber(Number(sel.population))], ['Density /km²', formatNumber(Number(sel.pop_density_per_km2))],
                  ['Area class', String(sel.area_class)], ['Income index', n1(Number(sel.income_index))],
                  ['Vulnerable share', n1(Number(sel.vulnerable_pop_share) * 100, 0) + '%'], ['Rainfall mm/yr', formatNumber(Number(sel.rainfall_mm_year))],
                  ['Elevation m', formatNumber(Number(sel.elevation_m))], ['Pole age (yr)', n1(Number(sel.pole_age_years))],
                  ['Dist. to road km', n1(Number(sel.dist_main_road_km), 2)], ['Dist. to depot km', n1(Number(sel.dist_depot_km), 1)],
                  ['Outages', formatNumber(Number(sel.outages))], ['Mean outage days', n1(Number(sel.mean_outage_days))],
                  ['ML risk score', n1(Number(sel.risk_score), 0)], ['Risk category', String(sel.risk_category)],
                  ['Mean priority', n1(Number(sel.mean_priority_score))], ['Repair now', String(sel.repair_now)],
                ] as [string, string][]).map(([a, b]) => (<React.Fragment key={a}><dt className="text-ink-soft">{a}</dt><dd className="text-right font-semibold tabular-nums">{b}</dd></React.Fragment>))}
              </dl>
            </div>
          ) : (
            <EmptyState title="Click a ward" detail="Shows its population, geography, weather, infrastructure, risk and priority." />
          )}
        </div>
      </div>
      <Card title="Highest-risk wards" sub="Ranked by the ML risk score (mean predicted night-crime rate near outages).">
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Ward</th><th>City</th><th className="num">Population</th><th className="num">Density /km²</th><th className="num">Rain mm</th><th className="num">Pole age</th><th className="num">Outages</th><th className="num">Risk</th><th className="num">Mean priority</th><th className="num">Repair now</th></tr></thead>
            <tbody>
              {top.map((w) => (
                <tr key={w.ward_id} className="row-link" onClick={() => setSel(w)}>
                  <td className="font-mono">{w.ward_id}</td><td>{w.city}</td><td className="num">{formatNumber(w.population)}</td>
                  <td className="num">{formatNumber(w.pop_density_per_km2)}</td><td className="num">{formatNumber(w.rainfall_mm_year)}</td>
                  <td className="num">{n1(w.pole_age_years)}</td><td className="num">{w.outages}</td><td className="num font-bold">{n1(w.risk_score, 0)}</td>
                  <td className="num">{n1(w.mean_priority_score)}</td><td className="num">{w.repair_now}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};

/* ------------------------------------------------------------------ risk & impact */
const AXIS_LABEL: Record<string, string> = {
  pop_density_per_km2: 'Population density (per km²)', income_index: 'Income index', vulnerable_pop_share: 'Vulnerable share',
  rainfall_mm_year: 'Annual rainfall (mm)', pole_age_years: 'Pole age (years)', dist_main_road_km: 'Distance to main road (km)',
  dist_depot_km: 'Distance to repair depot (km)', outages_per_km2: 'Outages per km²', night_crimes_per_km2: 'Night crimes per km²',
  mean_predicted_risk: 'ML risk (night crimes/day near outage)', mean_priority_score: 'Mean priority score', mean_outage_days: 'Mean outage length (days)',
};

const RiskTab: React.FC = () => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const { data: rel, isLoading } = useSyn('rel', fetchSynRelationships);
  const { data: pop } = useSyn('pop', fetchSynPopulation);
  const [idx, setIdx] = useState(0);
  const [group, setGroup] = useState('Population density');
  const [metric, setMetric] = useState<'mean_predicted_risk' | 'outages_per_10k_pop' | 'mean_priority_score' | 'mean_outage_days'>('mean_predicted_risk');
  if (isLoading || !rel || !rel.available) return <Loading />;
  const r = rel.relations[idx];
  const pts = rel.points.map((p) => ({ x: Number(p[r.x]), y: Number(p[r.y]), ward: String(p.ward_id), size: Number(p.population) })).filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
  const groups = pop?.groups ?? {};
  const rows = (groups[group] ?? []).map((g) => ({ ...g }));
  const metricLabel = { mean_predicted_risk: 'ML risk', outages_per_10k_pop: 'Outages per 10,000 residents', mean_priority_score: 'Mean priority score', mean_outage_days: 'Mean outage length (days)' }[metric];
  return (
    <div className="space-y-6">
      <Card title="What is associated with risk, outages and priority?" sub="Each dot is a ward (simulated). Associations only: confounded by density, which drives many of these together.">
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <SelectControl label="Relationship" value={String(idx)} onChange={(v) => setIdx(Number(v))} options={rel.relations.map((x, i) => ({ value: String(i), label: x.label }))} />
          <span className="badge badge-neutral">Spearman ρ = {n3(r.spearman)} · {r.n_wards} wards</span>
        </div>
        <div className="h-[340px]" role="img" aria-label={r.label}>
          <ResponsiveContainer>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 28, left: 8 }}>
              <CartesianGrid stroke={c.grid} />
              <XAxis dataKey="x" type="number" name={AXIS_LABEL[r.x]} stroke={c.axis} tick={{ fontSize: 11 }} domain={['auto', 'auto']} label={{ value: AXIS_LABEL[r.x] ?? r.x, position: 'bottom', fontSize: 11, fill: c.axis }} />
              <YAxis dataKey="y" type="number" name={AXIS_LABEL[r.y]} stroke={c.axis} tick={{ fontSize: 11 }} domain={['auto', 'auto']} label={{ value: AXIS_LABEL[r.y] ?? r.y, angle: -90, position: 'insideLeft', fontSize: 11, fill: c.axis }} />
              <ZAxis dataKey="size" range={[24, 110]} name="Population" />
              <Tooltip contentStyle={c.tooltip} cursor={{ strokeDasharray: '3 3' }} formatter={(v: number) => (Math.abs(v) >= 100 ? formatNumber(Math.round(v)) : v.toFixed(3))} />
              <Scatter isAnimationActive={false} data={pts} fill={c.primary} fillOpacity={0.65} stroke={c.zero} strokeWidth={0.5} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <Card title="How results vary across the population" sub="Wards grouped by a population or economic characteristic. Outage and risk values are generated by the pipeline.">
        <div className="mb-3 flex flex-wrap gap-3">
          <SelectControl label="Group by" value={group} onChange={setGroup} options={Object.keys(groups).map((g) => ({ value: g, label: g }))} />
          <SelectControl label="Measure" value={metric} onChange={(v) => setMetric(v as typeof metric)} options={[
            { value: 'mean_predicted_risk', label: 'ML risk' }, { value: 'outages_per_10k_pop', label: 'Outages per 10,000 residents' },
            { value: 'mean_priority_score', label: 'Mean priority score' }, { value: 'mean_outage_days', label: 'Mean outage length (days)' }]} />
        </div>
        <div className="h-[280px]">
          <ResponsiveContainer>
            <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              <XAxis dataKey="group" stroke={c.axis} tick={{ fontSize: 11 }} />
              <YAxis stroke={c.axis} tick={{ fontSize: 11 }} label={{ value: metricLabel, angle: -90, position: 'insideLeft', fontSize: 11, fill: c.axis }} />
              <Tooltip contentStyle={c.tooltip} formatter={(v: number) => v.toFixed(3)} />
              <Bar isAnimationActive={false} dataKey={metric} fill={c.primary} radius={[4, 4, 0, 0]} name={metricLabel} />
            </BarChart>
          </ResponsiveContainer>
        </div>
        <div className="table-wrap mt-4">
          <table className="data-table">
            <thead><tr><th>Group</th><th className="num">Wards</th><th className="num">Population</th><th className="num">Outages</th><th className="num">Per 10k residents</th><th className="num">ML risk</th><th className="num">Mean priority</th><th className="num">High/Med priority</th><th className="num">Repair now</th></tr></thead>
            <tbody>
              {rows.map((g) => (
                <tr key={g.group}><td className="font-semibold">{g.group}</td><td className="num">{g.wards}</td><td className="num">{formatNumber(g.population)}</td><td className="num">{formatNumber(g.outages)}</td>
                  <td className="num">{n1(g.outages_per_10k_pop)}</td><td className="num">{n3(g.mean_predicted_risk)}</td><td className="num">{n1(g.mean_priority_score)}</td><td className="num">{g.high_medium_priority}</td><td className="num">{g.repair_now}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};

/* ------------------------------------------------------------------ causal vs prediction */
const CausalTab: React.FC = () => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const { data, isLoading } = useSyn('causal', fetchSynCausal);
  const { data: risk } = useSyn('riskmodel', fetchSynRiskModel);
  const { data: dec } = useSyn('decision', fetchSynDecision);
  if (isLoading || !data || !data.available) return <Loading />;
  const eff = data.estimates.map((e) => ({ name: e.effect.replace('_', ' '), estimate: e.estimate, err: [e.estimate - (e.ci_lower ?? e.estimate), (e.ci_upper ?? e.estimate) - e.estimate] }));
  const direct = data.estimates.find((e) => e.effect === 'direct_during');
  const chk = data.planted_effect_check;
  const dens = data.effect_by_density.map((d) => ({ group: d.group, estimate: d.estimate, truth: d.planted_truth ?? null, err: 1.96 * d.se_unclustered }));
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="card border-l-4 p-5" style={{ borderLeftColor: c.primary }}>
          <span className="eyebrow">Causal AI</span>
          <h3 className="mt-1 text-sm font-bold">“What does a dark light do to nearby night crime?”</h3>
          <p className="mt-2 text-xs leading-relaxed text-ink-soft">{data.reading_guide.causal}</p>
          {direct && (
            <>
              <p className="mt-3 text-2xl font-extrabold tabular-nums">+{n3(direct.estimate)} <span className="text-sm font-semibold text-ink-soft">night crimes per outage</span></p>
              <p className="text-xs text-ink-soft">within 100 m while the light is out. 95% range {n3(direct.ci_lower)} to {n3(direct.ci_upper)}; p = {direct.p_value !== null ? direct.p_value.toFixed(4) : '–'}; {direct.n_pairs} matched outage–control pairs.</p>
              <p className="mt-2 text-xs leading-relaxed">In plain words: compared with a similar street that stayed lit, a street with a light out saw about {n3(direct.estimate)} more night crimes nearby during the outage, on average.</p>
            </>
          )}
          <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-ink-soft">
            <li><b>Average effect</b> over matched outages. It is not a separate estimate for each ward and is not applied ward by ward.</li>
            <li>Assumes matched control sites would have followed the same crime trend (parallel trends); the event-study pre-trend test is on the Legacy DiD page.</li>
            <li>Controls are matched on prior local crime; ward conditions are not used as adjustments beyond that.</li>
            <li>Naive before/after at treated sites only: {n3(data.naive_before_after.mean_change)} (SE {n3(data.naive_before_after.se)}). Without a matched control the effect is missed.</li>
          </ul>
        </div>
        <div className="card border-l-4 p-5" style={{ borderLeftColor: c.accent }}>
          <span className="eyebrow">XGBoost (prediction)</span>
          <h3 className="mt-1 text-sm font-bold">“Where is night crime high around an outage?”</h3>
          <p className="mt-2 text-xs leading-relaxed text-ink-soft">{risk?.available ? risk.interpretation : 'Loading model summary…'}</p>
          {risk && risk.available && (
            <>
              <div className="mt-3 grid grid-cols-2 gap-3 text-xs">
                <div><span className="eyebrow">MAE (held-out wards)</span><p className="text-lg font-extrabold tabular-nums">{n3(risk.mae)}</p><p className="text-ink-soft">vs {n3(risk.mae_baseline_mean_rate)} predicting the mean ({n1(risk.mae_improvement_pct, 0)}% better)</p></div>
                <div><span className="eyebrow">RMSE (held-out wards)</span><p className="text-lg font-extrabold tabular-nums">{n3(risk.rmse)}</p><p className="text-ink-soft">vs {n3(risk.rmse_baseline_mean_rate)} ({n1(risk.rmse_improvement_pct, 0)}% better)</p></div>
              </div>
              <ul className="mt-2 space-y-0.5 text-xs text-ink-soft">
                <li>Unit: {risk.metrics_unit}. Target: {risk.target}.</li>
                <li>Rank correlation {n3(risk.spearman_predicted_vs_observed)}; top 10% of predictions hold {n1(risk.top_decile_capture * 100, 0)}% of crime (chance 10%).</li>
                <li>Validation: {risk.validation}.</li>
              </ul>
              <p className="mt-3 text-xs font-semibold">Features used (all known when the outage is reported)</p>
              <div className="h-[200px]">
                <ResponsiveContainer>
                  <BarChart data={risk.feature_importance.slice(0, 8)} layout="vertical" margin={{ left: 40, right: 12 }}>
                    <CartesianGrid stroke={c.grid} horizontal={false} />
                    <XAxis type="number" stroke={c.axis} tick={{ fontSize: 10 }} />
                    <YAxis dataKey="feature" type="category" stroke={c.axis} tick={{ fontSize: 10 }} width={110} />
                    <Tooltip contentStyle={c.tooltip} formatter={(v: number) => v.toFixed(4)} />
                    <Bar isAnimationActive={false} dataKey="importance" fill={c.accent} name="Held-out importance (deviance increase)" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="text-[11px] text-ink-soft">Importance describes what the model relies on, not what causes crime.</p>
            </>
          )}
        </div>
      </div>

      {dec && dec.available && (
        <Card title="How the two are combined in the repair decision" sub="They stay separate; the decision layer combines them by a documented rule, not by multiplying outputs.">
          <ol className="list-decimal space-y-1.5 pl-5 text-[13px] leading-relaxed">
            <li><b>XGBoost</b> ranks outages by predicted night-crime risk (crimes/day within 100 m while dark).</li>
            <li><b>Causal AI</b> supplies one average effect. Dividing it by outage length ({n1(dec.causal_weight.mean_outage_days_matched)} days) gives {n3(dec.causal_weight.effect_per_outage_day)} crimes per outage-day, against a mean predicted rate of {n3(dec.causal_weight.mean_predicted_rate_matched_treated)} at the matched outages: about <b>{n1(dec.causal_weight.value * 100, 0)}%</b> of dark-period crime is attributable to the outage.</li>
            <li>That share becomes the <b>weight on the risk component</b> ({n1(dec.components[0].base_weight * 100, 0)}% → {n1(dec.components[0].effective_weight * 100, 0)}%). If the effect were not distinguishable from zero the weight would be 0: crime risk would then not justify repair priority.</li>
            <li>Population exposure, vulnerability, repeat failures and repair efficiency are scored separately. Density, elevation, slope and rainfall act only through XGBoost.</li>
          </ol>
          <p className="mt-2 text-xs text-ink-soft">{dec.causal_weight.rule}</p>
        </Card>
      )}

      <Card title="Causal estimates (matched-control difference-in-differences)" sub="Change in night-crime count per outage versus matched controls. Bars show 95% ranges. Positive = more crime.">
        <div className="h-[260px]">
          <ResponsiveContainer>
            <BarChart data={eff} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              <XAxis dataKey="name" stroke={c.axis} tick={{ fontSize: 11 }} />
              <YAxis stroke={c.axis} tick={{ fontSize: 11 }} />
              <ReferenceLine y={0} stroke={c.zero} />
              <Tooltip contentStyle={c.tooltip} formatter={(v: number) => v.toFixed(3)} />
              <Bar isAnimationActive={false} dataKey="estimate" fill={c.primary} radius={[4, 4, 0, 0]} name="Estimate"><ErrorBar dataKey="err" stroke={c.zero} width={4} /></Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-2 text-xs text-ink-soft">“direct” = within 100 m; “displacement” = 100–250 m ring; “net” = both. “during” = while dark; “post” = after repair. Only “direct, during” is clearly distinguishable from zero here; the ring estimates are noisy.</p>
      </Card>
      {chk && (
        <Card title="Synthetic check: does the method recover the planted effect?" sub="Possible only because the data is simulated: the generator knows how many extra crimes it planted. This file is never read by the pipeline.">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <Stat label="Planted (truth)" value={n3(chk.expected_direct_effect_in_matched_sample)} hint="Mean over the matched treated outages" />
            <Stat label="Estimated" value={n3(chk.estimated_direct_during)} hint={`95% range ${n3(chk.estimated_ci[0])} to ${n3(chk.estimated_ci[1])}`} />
            <Stat label="Truth inside range?" value={chk.ci_contains_truth ? 'Yes' : 'No'} hint={`${chk.n_matched_treated_with_truth} matched outages`} />
          </div>
          <div className="mt-4 h-[220px]">
            <ResponsiveContainer>
              <BarChart data={dens} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
                <CartesianGrid stroke={c.grid} vertical={false} />
                <XAxis dataKey="group" stroke={c.axis} tick={{ fontSize: 11 }} />
                <YAxis stroke={c.axis} tick={{ fontSize: 11 }} />
                <Tooltip contentStyle={c.tooltip} formatter={(v: number) => v.toFixed(3)} />
                <Legend />
                <Bar isAnimationActive={false} dataKey="estimate" fill={c.primary} name="Estimated (indicative)" radius={[4, 4, 0, 0]}><ErrorBar dataKey="err" stroke={c.zero} width={4} /></Bar>
                <Bar isAnimationActive={false} dataKey="truth" fill={c.secondary} name="Planted truth" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-xs text-ink-soft">Splitting by ward density is exploratory: the 95% bars overlap, so this does <b>not</b> establish that the effect differs by density, and no ward-specific causal effect is estimated.</p>
        </Card>
      )}
    </div>
  );
};

/* ------------------------------------------------------------------ prioritization */
const COMPONENT_KEYS = ['risk', 'exposure', 'vulnerable', 'recurrence', 'efficiency'] as const;
const COMPONENT_NAME: Record<(typeof COMPONENT_KEYS)[number], string> = {
  risk: 'XGBoost risk', exposure: 'Population exposed', vulnerable: 'Vulnerable share', recurrence: 'Repeat failures', efficiency: 'Repair efficiency',
};

const WhyPanel: React.FC<{ o: PrioItem }> = ({ o }) => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const parts = COMPONENT_KEYS.map((k) => ({ name: COMPONENT_NAME[k], points: o[`contrib_${k}` as keyof PrioItem] as number }));
  return (
    <div className="card space-y-2 p-4 text-xs">
      <div className="flex items-center justify-between"><h3 className="card-title">Why this location? #{o.rank} · {o.ward_id}</h3><SyntheticTag /></div>
      <p className="leading-relaxed">Priority <b>{n1(o.priority_score)}</b> / 100 ({o.priority_tier}). Main reasons: {o.priority_reasons || '–'}.</p>
      <div className="h-[150px]">
        <ResponsiveContainer>
          <BarChart data={parts} layout="vertical" margin={{ left: 50, right: 12 }}>
            <XAxis type="number" stroke={c.axis} tick={{ fontSize: 10 }} />
            <YAxis dataKey="name" type="category" stroke={c.axis} tick={{ fontSize: 10 }} width={110} />
            <Tooltip contentStyle={c.tooltip} formatter={(v: number) => `${v.toFixed(1)} points`} />
            <Bar isAnimationActive={false} dataKey="points" fill={c.primary} radius={[0, 4, 4, 0]} name="Points" />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-0.5">
        {([
          ['Population (ward)', formatNumber(o.population)], ['Density /km²', formatNumber(o.pop_density_per_km2)],
          ['Vulnerable share', `${n1(o.vulnerable_pop_share * 100, 0)}%`], ['Predicted risk (crimes/day)', n3(o.predicted_risk)],
          ['Pre-outage local crime rate', n3(o.local_crime_rate)], ['Earlier complaints within 50 m (90 d)', String(o.prior_complaints_90d_50m)],
          ['Elevation / slope', `${formatNumber(o.elevation_m)} m / ${n1(o.slope_pct)}%`], ['Rainfall', `${formatNumber(o.rainfall_mm_year)} mm/yr`],
          ['Distance to depot', `${n1(o.dist_depot_km)} km`], ['Stage 12 index (uses realised duration)', n1(o.stage12_score)],
        ] as [string, string][]).map(([a, b]) => (<React.Fragment key={a}><dt className="text-ink-soft">{a}</dt><dd className="text-right font-semibold tabular-nums">{b}</dd></React.Fragment>))}
      </dl>
      <p className="text-ink-soft">XGBoost drivers of this prediction (SHAP, log scale): {o.xgb_top_features}. Elevation, slope and rainfall affect the score only through the XGBoost prediction; they are not scored directly. The causal effect is an average and does not differ by location.</p>
    </div>
  );
};

const PopulationRiskCard: React.FC = () => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const { data } = useSyn('poprisk', fetchSynPopulationRisk);
  if (!data || !data.available) return null;
  const rows = data.items.slice(0, 15);
  return (
    <Card title="Population exposure vs predicted risk vs final priority" sub={`Top wards by mean priority. ${data.scale}`}>
      <div className="h-[320px]">
        <ResponsiveContainer>
          <BarChart data={rows} margin={{ top: 8, right: 16, bottom: 36, left: 8 }}>
            <CartesianGrid stroke={c.grid} vertical={false} />
            <XAxis dataKey="ward_id" stroke={c.axis} tick={{ fontSize: 10 }} angle={-35} textAnchor="end" interval={0} />
            <YAxis domain={[0, 100]} stroke={c.axis} tick={{ fontSize: 11 }} label={{ value: 'Percentile among wards', angle: -90, position: 'insideLeft', fontSize: 11, fill: c.axis }} />
            <Tooltip contentStyle={c.tooltip} formatter={(v: number) => `${v.toFixed(0)}th percentile`} />
            <Legend verticalAlign="top" />
            <Bar isAnimationActive={false} dataKey="population_pct" name="Population" fill={c.secondary} radius={[3, 3, 0, 0]} />
            <Bar isAnimationActive={false} dataKey="risk_pct" name="Predicted risk (XGBoost)" fill={c.accent} radius={[3, 3, 0, 0]} />
            <Bar isAnimationActive={false} dataKey="priority_pct" name="Final priority" fill={c.primary} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-xs text-ink-soft">Population does not decide the order alone: wards where the three bars differ show risk, vulnerability, repeat failures or repair efficiency changing the rank.</p>
    </Card>
  );
};

const PriorityTab: React.FC = () => {
  const { boroughs } = useProfile();
  const [city, setCity] = useState('All');
  const [action, setAction] = useState('All');
  const [sort, setSort] = useState('priority_score');
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<PrioItem | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ['syn', 'prio', city, action, sort, page],
    queryFn: () => fetchSynPrioritization({ city, action, sort, page, pageSize: 15 }),
    placeholderData: (prev) => prev,
  });
  useEffect(() => { setPage(1); setSel(null); }, [city, action, sort]);
  const shown = sel ?? data?.items?.[0] ?? null;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <SelectControl label="City" value={city} onChange={setCity} options={['All', ...boroughs].map((b) => ({ value: b, label: b === 'All' ? 'All cities' : b }))} />
        <SelectControl label="Action" value={action} onChange={setAction} options={['All', 'Repair now', 'Schedule next', 'Monitor'].map((b) => ({ value: b, label: b === 'All' ? 'All actions' : b }))} />
        <SelectControl label="Rank by" value={sort} onChange={setSort} options={[
          { value: 'priority_score', label: 'Decision priority score' }, { value: 'predicted_risk', label: 'XGBoost risk only' },
          { value: 'priority_ex_ante', label: 'Causal effect × pre-outage crime rate' }, { value: 'stage12_score', label: 'Stage 12 index (hindsight)' }]} />
      </div>
      {isLoading || !data ? <Loading /> : !data.available ? <EmptyState title="Not available" /> : (
        <>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <div className="space-y-3 xl:col-span-2">
              <div className="table-wrap">
                <table className="data-table">
                  <thead><tr><th>Rank</th><th>Outage</th><th>Ward</th><th className="num">Population</th><th className="num">Density /km²</th><th className="num">Vulnerable</th><th className="num">XGBoost risk</th><th className="num">Priority</th><th>Tier</th><th>Action</th></tr></thead>
                  <tbody>
                    {data.items.map((o) => (
                      <tr key={o.unique_key} className="row-link" tabIndex={0} aria-selected={shown?.unique_key === o.unique_key}
                        onClick={() => setSel(o)} onKeyDown={(e) => e.key === 'Enter' && setSel(o)}>
                        <td className="tabular-nums text-ink-soft">#{o.rank}</td><td className="font-mono">{o.unique_key}</td><td>{o.ward_id} · {o.city}</td>
                        <td className="num">{formatNumber(o.population)}</td><td className="num">{formatNumber(o.pop_density_per_km2)}</td>
                        <td className="num">{n1(o.vulnerable_pop_share * 100, 0)}%</td><td className="num">{n3(o.predicted_risk)}</td>
                        <td className="num font-bold">{n1(o.priority_score)}</td>
                        <td><span className={`badge ${o.priority_tier === 'High' ? 'badge-high' : o.priority_tier === 'Medium' ? 'badge-medium' : 'badge-low'}`}>{o.priority_tier}</span></td>
                        <td><span className={`badge ${o.recommended_action === 'Repair now' ? 'badge-ok' : 'badge-neutral'}`}>{o.recommended_action}</span></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <Pager page={page} pages={Math.max(1, Math.ceil(data.total / data.page_size))} total={data.total} onPage={setPage} label="outages" />
              <p className="text-xs text-ink-soft">{data.note} “Repair now” = chosen by the constrained plan (daily budget + city quotas, Stage 14 ILP on this score); “Schedule next” = High or Medium tier but not chosen today; “Monitor” = Low tier. Click a row for its explanation.</p>
            </div>
            {shown && <WhyPanel o={shown} />}
          </div>
        </>
      )}
      <PopulationRiskCard />
    </div>
  );
};

/* ------------------------------------------------------------------ scenarios */
const ScenarioTab: React.FC = () => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const [scenario, setScenario] = useState('baseline');
  const { data } = useQuery({ queryKey: ['syn', 'scen', scenario], queryFn: () => fetchSynScenarios(scenario), placeholderData: (p) => p });
  if (!data || !data.available) return <Loading />;
  const sc = data.scenarios.find((s) => s.scenario === data.selected);
  const rows = data.policies.map((p) => ({ ...p, short: p.policy.replace(' (existing baseline)', '').replace(' (Stage 12 score)', ' (Stage 12)').replace(' (historical closures in the data)', '') }));
  const sim = rows.filter((r) => !r.policy.startsWith('Observed'));
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <SelectControl label="Scenario" value={scenario} onChange={setScenario} options={data.scenarios.map((s) => ({ value: s.scenario, label: s.scenario_label }))} />
        {sc && <span className="text-xs text-ink-soft">{sc.scenario_description} {formatNumber(sc.outages)} outages, capacity {sc.capacity_k_per_day} repairs/day.</span>}
      </div>
      <Card title="Planted extra-crime exposure by dispatch policy" sub="Sum over outages of (planted extra night crimes per day while dark × days waited). Lower is better. Synthetic ground-truth benchmark.">
        <div className="h-[300px]">
          <ResponsiveContainer>
            <BarChart data={sim} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              <XAxis dataKey="short" stroke={c.axis} tick={{ fontSize: 11 }} />
              <YAxis stroke={c.axis} tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={c.tooltip} formatter={(v: number) => formatNumber(Math.round(v))} />
              <Bar isAnimationActive={false} dataKey="benchmark_extra_crimes" fill={c.primary} name="Extra crimes (benchmark)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>
      <div className="table-wrap">
        <table className="data-table">
          <thead><tr><th>Policy</th><th className="num">Mean wait (d)</th><th className="num">90th pct wait (d)</th><th className="num">Priority-weighted wait (d)</th><th className="num">Extra crimes (benchmark)</th><th className="num">vs FIFO</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.policy}>
                <td className="font-semibold">{r.policy}{r.uses_hindsight && <span className="badge badge-medium ml-2" title="Uses the realised outage duration">hindsight</span>}</td>
                <td className="num">{n1(r.mean_wait_days)}</td><td className="num">{n1(r.p90_wait_days)}</td><td className="num">{n1(r.priority_weighted_wait_days)}</td>
                <td className="num">{formatNumber(Math.round(r.benchmark_extra_crimes ?? 0))}</td><td className="num font-bold">{r.policy.startsWith('FIFO') || r.policy.startsWith('Observed') ? '–' : pct(r.benchmark_vs_fifo_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="list-disc space-y-1 pl-5 text-xs text-ink-soft">{data.notes.map((n) => <li key={n}>{n}</li>)}
        <li>When capacity is ample every outage is repaired quickly and the order barely matters; ordering helps when demand outstrips capacity. Prioritising can lengthen the wait of low-priority outages (see the 90th percentile).</li></ul>
    </div>
  );
};

/* ------------------------------------------------------------------ methodology */
const MethodTab: React.FC = () => {
  const { data } = useSyn('method', fetchSynMethodology);
  if (!data || !data.available) return <Loading />;
  const list = (title: string, items: string[]) => (
    <Card title={title}><ul className="list-disc space-y-1.5 pl-5 text-[13px] leading-relaxed">{items.map((i) => <li key={i}>{i}</li>)}</ul></Card>
  );
  return (
    <div className="space-y-5">
      <div className="notice notice-warn"><b>{data.label}.</b> {data.why_synthetic}</div>
      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        {list('What was simulated', data.simulated)}
        {list('Relationships assumed in the simulation', data.assumed_relationships)}
        {list('Model-derived outputs', data.model_derived)}
        {list('Synthetic values', data.synthetic_values)}
      </div>
      {list('Limitations', data.limitations)}
      <Card title="Where each metric comes from" sub="UI metric → API endpoint → output file → source data columns.">
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>UI metric</th><th>Endpoint</th><th>Output file</th><th>Source data</th></tr></thead>
            <tbody>{data.traceability.map((t) => (
              <tr key={t.ui_metric}><td className="font-semibold">{t.ui_metric}</td><td className="font-mono text-xs">{t.endpoint}</td><td className="whitespace-normal text-xs">{t.file}</td><td className="whitespace-normal text-xs text-ink-soft">{t.source}</td></tr>
            ))}</tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};

/* ------------------------------------------------------------------ page */
export const SyntheticDashboard: React.FC = () => {
  const [params] = useSearchParams();
  const initial = TABS.find((t) => t.id === params.get('tab'))?.id ?? 'overview';
  const [tab, setTab] = useState<TabId>(initial);
  const { synthetic, loaded } = useProfile();
  if (loaded && !synthetic) {
    return <div className="page"><div className="card"><EmptyState title="Synthetic demonstration not active" detail="Start the API with LIGHTSAFE_PROFILE=karnataka_synthetic to serve the synthetic Karnataka dataset." /></div></div>;
  }
  return (
    <div className="page space-y-5">
      <PageHeader
        title="Karnataka synthetic demonstration"
        description="Synthetic data → existing pipeline (matched controls, causal estimate, priority index, constrained dispatch) → API → this dashboard. Every number is generated, none is a real measurement."
        actions={<SyntheticTag />}
      />
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Dashboard sections" />
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === 'overview' && <OverviewTab />}
        {tab === 'geo' && <GeoTab />}
        {tab === 'risk' && <RiskTab />}
        {tab === 'causal' && <CausalTab />}
        {tab === 'priority' && <PriorityTab />}
        {tab === 'scenario' && <ScenarioTab />}
        {tab === 'method' && <MethodTab />}
      </div>
    </div>
  );
};
