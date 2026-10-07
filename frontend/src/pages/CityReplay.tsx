import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pause, Play, RotateCcw, StepBack, StepForward } from 'lucide-react';
import { useReplay } from '../hooks/useExplorer';
import { ErrorState, Loading, PageHeader } from '../components/ui';
import { useTheme } from '../context/ThemeContext';
import { chartColors } from '../lib/chartTheme';
import { formatNumber } from '../lib/formatters';
import { MAP_CENTER } from '../lib/constants';
import { ReplayData } from '../types/explorer';

const BASEMAP = {
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
};
const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
const K_OPTIONS = [50, 55, 60, 65, 70, 75, 80, 85, 90];
const SPEEDS = [
  { v: 2, label: '2 days/s' },
  { v: 7, label: '1 week/s' },
  { v: 30, label: '1 month/s' },
];
// Sequential one-hue ramp for waiting age (days since the job became known).
const AGE_STOPS = { light: ['#fdba74', '#ea580c', '#7c2d12'], dark: ['#fed7aa', '#f97316', '#c2410c'] };

/** Pending jobs (known, not yet dispatched) and jobs dispatched at decision d. */
function frameData(r: ReplayData, d: number) {
  const j = r.jobs;
  const pending: GeoJSON.Feature[] = [];
  const today: GeoJSON.Feature[] = [];
  const byBorough = new Array(r.boroughs.length).fill(0);
  for (let i = 0; i < j.known_day.length; i++) {
    if (j.known_day[i] > d) break; // jobs are in arrival order
    const disp = j.dispatch_day[i];
    if (disp < d) continue;
    if (disp === d) {
      if (j.has_coords[i]) today.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [j.lon[i], j.lat[i]] }, properties: {} });
      continue;
    }
    byBorough[j.borough[i]]++;
    if (j.has_coords[i]) pending.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [j.lon[i], j.lat[i]] }, properties: { age: d - j.known_day[i] } });
  }
  return { pending: { type: 'FeatureCollection', features: pending } as GeoJSON.FeatureCollection, today: { type: 'FeatureCollection', features: today } as GeoJSON.FeatureCollection, byBorough };
}

/* ------------------------------------------------------------------ map */
const ReplayMap: React.FC<{ pending: GeoJSON.FeatureCollection; today: GeoJSON.FeatureCollection }> = ({ pending, today }) => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let mounted = true;
    let ro: ResizeObserver | null = null;
    (async () => {
      const mgl = await import('maplibre-gl');
      if (!mounted || !ref.current) return;
      const map = new mgl.Map({ container: ref.current, style: BASEMAP[theme], center: MAP_CENTER, zoom: 9.6, attributionControl: false });
      map.addControl(new mgl.NavigationControl({ showCompass: false }), 'bottom-right');
      map.addControl(new mgl.AttributionControl({ compact: true }), 'bottom-left');
      ro = new ResizeObserver(() => map.resize());
      ro.observe(ref.current);
      map.on('load', () => {
        if (!mounted) return;
        const stops = AGE_STOPS[theme];
        map.addSource('pending', { type: 'geojson', data: EMPTY });
        map.addLayer({
          id: 'pending', type: 'circle', source: 'pending',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 1.8, 13, 4],
            'circle-color': ['interpolate', ['linear'], ['get', 'age'], 0, stops[0], 14, stops[1], 60, stops[2]],
            'circle-opacity': 0.85,
          },
        });
        map.addSource('today', { type: 'geojson', data: EMPTY });
        map.addLayer({
          id: 'today', type: 'circle', source: 'today',
          paint: {
            'circle-radius': ['interpolate', ['linear'], ['zoom'], 9, 3, 13, 6],
            'circle-color': c.backlog, 'circle-opacity': 0.95,
            'circle-stroke-width': 1, 'circle-stroke-color': c.tooltip.background as string,
          },
        });
        mapRef.current = map;
        setReady(true);
      });
    })();
    return () => {
      mounted = false;
      ro?.disconnect();
      mapRef.current?.remove();
      mapRef.current = null;
    };
    // the map is rebuilt when the theme changes (parent keys this component by theme)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!ready || !mapRef.current) return;
    mapRef.current.getSource('pending')?.setData(pending);
    mapRef.current.getSource('today')?.setData(today);
  }, [ready, pending, today]);

  return <div ref={ref} className="h-full w-full" />;
};

/* ------------------------------------------------------------------ backlog sparkline with cursor */
const Sparkline: React.FC<{ values: number[]; day: number; onSeek: (d: number) => void; horizonIdx: number; overloadIdx: number }> = ({ values, day, onSeek, horizonIdx, overloadIdx }) => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const W = 1000, H = 90;
  const max = Math.max(1, ...values);
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * W;
  const path = useMemo(() => values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${(H - (v / max) * (H - 6)).toFixed(1)}`).join(''), [values, max]);
  const seek = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onSeek(Math.round(((e.clientX - rect.left) / rect.width) * (values.length - 1)));
  };
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-20 w-full cursor-pointer" onClick={seek} role="img" aria-label="Backlog over time; click to seek">
      {overloadIdx >= 0 && <rect x={x(overloadIdx)} y={0} width={x(horizonIdx) - x(overloadIdx)} height={H} fill={c.demand} opacity={0.07} />}
      <path d={`${path}L${W},${H}L0,${H}Z`} fill={c.backlog} opacity={0.18} />
      <path d={path} fill="none" stroke={c.backlog} strokeWidth={2} vectorEffect="non-scaling-stroke" />
      {horizonIdx >= 0 && <line x1={x(horizonIdx)} x2={x(horizonIdx)} y1={0} y2={H} stroke={c.axis} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />}
      <line x1={x(day)} x2={x(day)} y1={0} y2={H} stroke={c.zero} strokeWidth={2} vectorEffect="non-scaling-stroke" />
    </svg>
  );
};

/* ------------------------------------------------------------------ page */
export const CityReplay: React.FC = () => {
  const { theme } = useTheme();
  const c = chartColors(theme);
  const [k, setK] = useState(65);
  const { data, isLoading, isError, refetch, isFetching } = useReplay(k);
  const [day, setDay] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(7);
  const dayRef = useRef(0);
  dayRef.current = day;

  const n = data?.series.date.length ?? 0;
  const cum = useMemo(() => {
    if (!data) return { arrived: [] as number[], resolved: [] as number[], dispatched: [] as number[] };
    const acc = (xs: number[]) => { let s = 0; return xs.map((x) => (s += x)); };
    return { arrived: acc(data.series.new_jobs), resolved: acc(data.series.resolved), dispatched: acc(data.series.dispatched) };
  }, [data]);

  // clamp when the run (and so its length) changes
  useEffect(() => { if (n && day > n - 1) setDay(n - 1); }, [n, day]);

  // playback loop: advance `speed` decision-days per second
  useEffect(() => {
    if (!playing || !n) return;
    let raf = 0;
    let last = performance.now();
    let acc = dayRef.current;
    const tick = (now: number) => {
      acc += ((now - last) / 1000) * speed;
      last = now;
      const next = Math.min(n - 1, Math.floor(acc));
      if (next !== dayRef.current) setDay(next);
      if (next >= n - 1) { setPlaying(false); return; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, n]);

  const frame = useMemo(() => (data ? frameData(data, Math.min(day, n - 1)) : null), [data, day, n]);

  if (isLoading && !data) return <Loading label="Loading replay…" />;
  if (isError || !data || !frame) return <ErrorState onRetry={() => refetch()} />;

  const s = data.series;
  const d = Math.min(day, n - 1);
  const horizonIdx = s.date.findIndex((x) => x >= data.horizon_end);
  const overloadIdx = s.date.findIndex((x) => x >= data.overload_start);
  const pendingTotal = s.backlog[d];
  const stats = [
    { label: 'New jobs this decision', value: formatNumber(s.new_jobs[d]), color: c.demand },
    { label: 'Dispatched this decision', value: `${formatNumber(s.dispatched[d])} / ${data.capacity_k}`, color: c.dispatch },
    { label: 'Pending (backlog)', value: formatNumber(pendingTotal), color: c.demand },
    { label: 'Repaired so far (resolved)', value: formatNumber(cum.resolved[d]), color: c.backlog },
    { label: 'Arrived so far', value: formatNumber(cum.arrived[d]) },
  ];

  return (
    <div className="page max-w-[1400px] space-y-4">
      <PageHeader
        title="City replay"
        description="The frozen FIFO dispatch simulation replayed one daily 08:00 decision at a time: reports arrive, join the queue, the oldest K are dispatched, and the backlog changes."
      />

      <div className="card flex flex-wrap items-center gap-3 p-3">
        <div className="flex items-center gap-1">
          <button type="button" className="btn btn-sm btn-primary" onClick={() => { if (d >= n - 1) setDay(0); setPlaying((p) => !p); }} aria-label={playing ? 'Pause' : 'Play'}>
            {playing ? <Pause size={14} /> : <Play size={14} />} {playing ? 'Pause' : 'Play'}
          </button>
          <button type="button" className="btn btn-sm" onClick={() => { setPlaying(false); setDay(0); }} aria-label="Reset"><RotateCcw size={14} /> Reset</button>
          <button type="button" className="btn btn-sm" onClick={() => setDay(Math.max(0, d - 1))} aria-label="Previous day"><StepBack size={14} /></button>
          <button type="button" className="btn btn-sm" onClick={() => setDay(Math.min(n - 1, d + 1))} aria-label="Next day"><StepForward size={14} /></button>
        </div>
        <label className="flex items-center gap-1.5 text-xs font-semibold text-ink">
          Speed
          <select className="rounded border border-line bg-surface px-1.5 py-1 text-xs" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-xs font-semibold text-ink">
          Capacity K
          <select className="rounded border border-line bg-surface px-1.5 py-1 text-xs" value={k} onChange={(e) => setK(Number(e.target.value))}>
            {K_OPTIONS.map((o) => <option key={o} value={o}>{o}{o === 65 ? ' (baseline)' : ''}</option>)}
          </select>
        </label>
        <span className="badge badge-neutral">Policy: {data.policy}</span>
        {isFetching && <span className="text-xs text-ink-soft">re-simulating…</span>}
        <span className="ml-auto text-lg font-extrabold tabular-nums text-ink">{s.date[d]}</span>
        <span className="text-xs text-ink-soft">decision {d + 1} of {n}{s.date[d] >= data.horizon_end ? ' · drain (no new arrivals)' : ''}</span>
      </div>

      <input type="range" min={0} max={n - 1} value={d} onChange={(e) => { setPlaying(false); setDay(Number(e.target.value)); }} className="w-full" style={{ accentColor: c.dispatch }} aria-label="Timeline" />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
        <div className="card relative h-[540px] overflow-hidden">
          <ReplayMap key={theme} pending={frame.pending} today={frame.today} />
          <div className="pointer-events-none absolute left-3 top-3 rounded-lg px-3 py-2 text-[11px] shadow" style={c.tooltip}>
            <p className="mb-1 font-bold">Legend</p>
            <p className="flex items-center gap-2">
              <span className="inline-block h-2 w-16 rounded" style={{ background: `linear-gradient(90deg, ${AGE_STOPS[theme].join(',')})` }} />
              pending: 0 → 60+ days waiting
            </p>
            <p className="flex items-center gap-2"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: c.backlog }} />dispatched at this decision</p>
          </div>
        </div>
        <div className="space-y-3">
          {stats.map((st) => (
            <div key={st.label} className="card flex items-center justify-between p-3">
              <span className="flex items-center gap-2 text-xs font-semibold text-ink-soft">
                {st.color && <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: st.color }} />}
                {st.label}
              </span>
              <span className="text-lg font-extrabold tabular-nums text-ink">{st.value}</span>
            </div>
          ))}
          <div className="card p-3">
            <p className="eyebrow mb-2">Pending by borough</p>
            {data.boroughs.map((b, i) => (
              <div key={b} className="mb-1.5">
                <div className="flex justify-between text-xs"><span>{b === '<missing>' ? 'No borough recorded' : b.charAt(0) + b.slice(1).toLowerCase()}</span><span className="tabular-nums">{formatNumber(frame.byBorough[i])}</span></div>
                <div className="h-1.5 rounded-full bg-line-soft">
                  <div className="h-1.5 rounded-full" style={{ width: `${pendingTotal ? (100 * frame.byBorough[i]) / pendingTotal : 0}%`, background: c.demand }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card p-3">
        <div className="flex justify-between text-xs text-ink-soft">
          <span className="eyebrow">Backlog over the whole run (click to jump)</span>
          <span>shaded: overload regime · dashed: arrivals stop {s.date[horizonIdx]}</span>
        </div>
        <Sparkline values={s.backlog} day={d} onSeek={(x) => { setPlaying(false); setDay(Math.max(0, Math.min(n - 1, x))); }} horizonIdx={horizonIdx} overloadIdx={overloadIdx} />
      </div>

      <p className="text-[11px] leading-relaxed text-ink-soft">
        Each dot is one simulated complaint-job at its streetlight site; no crews, vehicles or routes are simulated. A job is known at the first 08:00 decision after its 311 report; "repaired" means resolved one day after dispatch under the Stage 13 1-day service assumption, not an observed repair.
        {data.n_jobs_without_coordinates > 0 && ` ${data.n_jobs_without_coordinates} jobs have no site coordinates and are counted but not drawn.`}
      </p>
    </div>
  );
};
