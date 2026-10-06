import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { X } from 'lucide-react';
import { fetchBufferRings, fetchMapCrimes, fetchMapOutages } from '../lib/api';
import { useGlobalFilters } from '../context/FilterContext';
import { useTheme } from '../context/ThemeContext';
import { HeaderControls } from '../components/layout/HeaderSlot';
import { BoroughControl, DecisionBadge, DecisionControl, Loading, SearchInput, TierBadge, TierControl } from '../components/ui';
import { formatDate, formatDays, formatScore } from '../lib/formatters';
import { MAP_CENTER, MAP_DEFAULT_ZOOM, TIER_MAP_COLORS } from '../lib/constants';
import { DispatchStatus, PriorityTier } from '../types/outage';

interface OutageFeature {
  id: string;
  score: number | null;
  tier: PriorityTier | null;
  dispatch_status: DispatchStatus | null;
  optimization_rank: number | null;
  duration_days: number | null;
  local_crime_rate: number | null;
  borough: string;
  location: string;
  created_date: string | null;
}

// Keyless vector basemaps; the style is swapped when the theme changes.
const BASEMAP = {
  dark: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
  light: 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json',
};

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
const tierColor = (t: string | null) => (t && t in TIER_MAP_COLORS ? TIER_MAP_COLORS[t as PriorityTier] : TIER_MAP_COLORS.Low);

function addOverlays(map: any) {
  if (map.getSource('outages')) return;
  map.addSource('crimes', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'crimes-layer', type: 'circle', source: 'crimes', layout: { visibility: 'none' }, paint: { 'circle-radius': 2.5, 'circle-color': '#a78bfa', 'circle-opacity': 0.55 } });

  map.addSource('buffers', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'buffer-outer', type: 'fill', source: 'buffers', filter: ['==', ['get', 'type'], 'outer_ring'], paint: { 'fill-color': '#a78bfa', 'fill-opacity': 0.08 } });
  map.addLayer({ id: 'buffer-outer-line', type: 'line', source: 'buffers', filter: ['==', ['get', 'type'], 'outer_ring'], paint: { 'line-color': '#a78bfa', 'line-opacity': 0.7, 'line-width': 1.25, 'line-dasharray': [3, 2] } });
  map.addLayer({ id: 'buffer-inner', type: 'fill', source: 'buffers', filter: ['==', ['get', 'type'], 'inner_ring'], paint: { 'fill-color': '#c9f45b', 'fill-opacity': 0.14 } });
  map.addLayer({ id: 'buffer-inner-line', type: 'line', source: 'buffers', filter: ['==', ['get', 'type'], 'inner_ring'], paint: { 'line-color': '#c9f45b', 'line-opacity': 0.9, 'line-width': 1.5 } });

  map.addSource('outages', { type: 'geojson', data: EMPTY });
  map.addLayer({
    id: 'outages-layer', type: 'circle', source: 'outages',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['coalesce', ['get', 'score'], 0], 0, 4, 40, 6, 80, 8.5, 100, 11],
      'circle-color': ['match', ['get', 'tier'], 'High', TIER_MAP_COLORS.High, 'Medium', TIER_MAP_COLORS.Medium, TIER_MAP_COLORS.Low],
      'circle-opacity': 0.92,
      'circle-stroke-width': ['case', ['==', ['get', 'dispatch_status'], 'recommended'], 2.5, 1],
      'circle-stroke-color': ['case', ['==', ['get', 'dispatch_status'], 'recommended'], '#c9f45b', 'rgba(15,29,25,0.8)'],
    },
  });
  map.addSource('selected', { type: 'geojson', data: EMPTY });
  map.addLayer({ id: 'selected-ring', type: 'circle', source: 'selected', paint: { 'circle-radius': 16, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-width': 2.5, 'circle-stroke-color': '#ffffff' } });
}

const Toggle: React.FC<{ label: string; checked: boolean; onChange: (v: boolean) => void }> = ({ label, checked, onChange }) => (
  <label className="flex cursor-pointer select-none items-center gap-2 text-xs font-semibold text-ink">
    <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="peer sr-only" />
    <span className="relative h-4 w-7 rounded-full bg-line transition-colors peer-checked:bg-signal-deep peer-focus-visible:ring-2 peer-focus-visible:ring-signal-deep/50">
      <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-transform ${checked ? 'translate-x-3.5' : 'translate-x-0.5'}`} />
    </span>
    {label}
  </label>
);

export const MapView: React.FC = () => {
  const navigate = useNavigate();
  const { theme } = useTheme();
  const { borough, priorityTier, decision } = useGlobalFilters();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const [ready, setReady] = useState(false);
  const [styleVersion, setStyleVersion] = useState(0);
  const themeRef = useRef(theme);
  themeRef.current = theme;

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showCrimes, setShowCrimes] = useState(false);
  const [showBuffers, setShowBuffers] = useState(true);
  const [search, setSearch] = useState('');

  const { data: outagesGeo, isLoading, isError } = useQuery({
    queryKey: ['mapOutages', borough, priorityTier, decision],
    queryFn: () => fetchMapOutages(borough, priorityTier, 500, decision === 'All' ? undefined : decision),
    staleTime: 1000 * 60 * 2,
  });
  const { data: crimesGeo } = useQuery({
    queryKey: ['mapCrimes'],
    queryFn: () => fetchMapCrimes(true, 800),
    enabled: showCrimes,
    staleTime: 1000 * 60 * 10,
  });
  const { data: buffersGeo } = useQuery({
    queryKey: ['mapBuffers', selectedId],
    queryFn: () => fetchBufferRings(selectedId!),
    enabled: !!selectedId,
    staleTime: Infinity,
  });

  const features = outagesGeo?.features ?? [];
  const selected = useMemo<OutageFeature | null>(
    () => (selectedId ? (features.find((f: any) => f.properties?.id === selectedId)?.properties as OutageFeature) ?? null : null),
    [features, selectedId]
  );
  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return features;
    return features.filter((f: any) => {
      const p = f.properties as OutageFeature;
      return p.id.toLowerCase().includes(q) || (p.location ?? '').toLowerCase().includes(q);
    });
  }, [features, search]);

  /* ---- create the map once */
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let mounted = true;
    let ro: ResizeObserver | null = null;
    (async () => {
      const mgl = await import('maplibre-gl');
      if (!mounted || !containerRef.current) return;
      const map = new mgl.Map({ container: containerRef.current, style: BASEMAP[themeRef.current], center: MAP_CENTER, zoom: MAP_DEFAULT_ZOOM, attributionControl: false });
      map.addControl(new mgl.NavigationControl({ showCompass: false }), 'bottom-right');
      map.addControl(new mgl.AttributionControl({ compact: true }), 'bottom-left');
      ro = new ResizeObserver(() => map.resize());
      ro.observe(containerRef.current);

      map.on('load', () => {
        if (!mounted) return;
        addOverlays(map);
        setStyleVersion((v) => v + 1);
        // After a basemap swap the new style has no overlay sources; re-add them as soon as it can accept them.
        map.on('styledata', () => {
          if (map.getSource('outages')) return;
          try {
            addOverlays(map);
            setStyleVersion((v) => v + 1);
          } catch {
            /* style not ready yet; the next styledata event retries */
          }
        });
        map.on('click', 'outages-layer', (e: any) => {
          const id = e.features?.[0]?.properties?.id;
          if (id) setSelectedId(String(id));
        });
        map.on('mouseenter', 'outages-layer', () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', 'outages-layer', () => { map.getCanvas().style.cursor = ''; });
        mapRef.current = map;
        setReady(true);
      });
    })();
    return () => {
      mounted = false;
      ro?.disconnect();
      mapRef.current?.remove();
      mapRef.current = null;
      setReady(false);
    };
  }, []);

  /* ---- theme: swap basemap, then re-add overlays */
  const appliedTheme = useRef<string | null>(null);
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    if (appliedTheme.current === null) appliedTheme.current = theme; // initial style already matches
    if (appliedTheme.current !== theme) {
      appliedTheme.current = theme;
      map.setStyle(BASEMAP[theme]);
    }
  }, [theme, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !map.getLayer('selected-ring')) return;
    map.setPaintProperty('selected-ring', 'circle-stroke-color', theme === 'dark' ? '#ffffff' : '#132c27');
    map.setPaintProperty('buffer-inner-line', 'line-color', theme === 'dark' ? '#c9f45b' : '#5f7f10');
    map.setPaintProperty('buffer-inner', 'fill-color', theme === 'dark' ? '#c9f45b' : '#7fa51d');
    map.setPaintProperty('outages-layer', 'circle-stroke-color', ['case', ['==', ['get', 'dispatch_status'], 'recommended'], theme === 'dark' ? '#c9f45b' : '#3f5f08', theme === 'dark' ? 'rgba(15,29,25,0.8)' : 'rgba(255,255,255,0.9)']);
  }, [theme, ready, styleVersion]);

  /* ---- data sync */
  useEffect(() => {
    if (ready && outagesGeo) mapRef.current.getSource('outages')?.setData({ type: 'FeatureCollection', features: visible });
  }, [ready, outagesGeo, visible, styleVersion]);
  useEffect(() => {
    if (ready && crimesGeo) mapRef.current.getSource('crimes')?.setData(crimesGeo);
  }, [ready, crimesGeo, styleVersion]);
  useEffect(() => {
    if (ready && mapRef.current.getLayer('crimes-layer')) mapRef.current.setLayoutProperty('crimes-layer', 'visibility', showCrimes ? 'visible' : 'none');
  }, [ready, showCrimes, styleVersion]);
  useEffect(() => {
    if (!ready) return;
    const vis = showBuffers ? 'visible' : 'none';
    ['buffer-outer', 'buffer-outer-line', 'buffer-inner', 'buffer-inner-line'].forEach((l) => mapRef.current.getLayer(l) && mapRef.current.setLayoutProperty(l, 'visibility', vis));
  }, [ready, showBuffers, styleVersion]);
  useEffect(() => {
    if (ready) mapRef.current.getSource('buffers')?.setData(buffersGeo && selectedId ? buffersGeo : EMPTY);
  }, [ready, buffersGeo, selectedId, styleVersion]);

  /* ---- selection: highlight (re-applied after a basemap swap) + fly */
  useEffect(() => {
    if (!ready || !mapRef.current.getSource('selected')) return;
    const f: any = selectedId ? features.find((x: any) => x.properties?.id === selectedId) : null;
    mapRef.current.getSource('selected').setData(f ? { type: 'FeatureCollection', features: [f] } : EMPTY);
  }, [ready, selectedId, styleVersion, features]);

  useEffect(() => {
    if (!ready || !selectedId) return;
    const f: any = features.find((x: any) => x.properties?.id === selectedId);
    if (f?.geometry?.type === 'Point') {
      mapRef.current.flyTo({ center: f.geometry.coordinates, zoom: Math.max(mapRef.current.getZoom(), 14), duration: 700 });
    }
  }, [ready, selectedId]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetView = useCallback(() => mapRef.current?.flyTo({ center: MAP_CENTER, zoom: MAP_DEFAULT_ZOOM, duration: 700 }), []);

  return (
    <div className="flex h-full min-h-0">
      <h1 className="sr-only">City Map</h1>
      <HeaderControls>
        <BoroughControl />
        <TierControl />
        <DecisionControl />
      </HeaderControls>

      {/* list panel */}
      <aside className="hidden w-72 shrink-0 flex-col border-r border-line bg-surface md:flex" aria-label="Outages on the map">
        <div className="space-y-2 border-b border-line p-3">
          <SearchInput value={search} onSearch={setSearch} placeholder="Filter by ID or street" />
          <p className="text-[11px] text-ink-soft" role="status">
            {isLoading ? 'Loading…' : `${visible.length.toLocaleString('en-US')} shown${outagesGeo ? ` of ${outagesGeo.total_matching.toLocaleString('en-US')} matching` : ''}`}
          </p>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {isError && <li className="p-4 text-xs text-ink-soft">Outages could not be loaded.</li>}
          {visible.map((f: any) => {
            const p = f.properties as OutageFeature;
            const active = p.id === selectedId;
            return (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(p.id)}
                  aria-current={active}
                  className={`flex w-full items-center justify-between gap-3 border-b border-line-soft px-3 py-2.5 text-left transition-colors hover:bg-line-soft/60 ${active ? 'bg-line-soft' : ''}`}
                >
                  <span className="min-w-0">
                    <span className="block font-mono text-xs font-medium text-ink">{p.id}</span>
                    <span className="block truncate text-[11px] text-ink-soft">{p.location}</span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block text-xs font-bold tabular-nums text-ink">{formatScore(p.score)}</span>
                    <span className="mt-0.5 block"><TierBadge tier={p.tier} /></span>
                  </span>
                </button>
              </li>
            );
          })}
          {!isLoading && visible.length === 0 && !isError && <li className="p-4 text-xs text-ink-soft">No outages match these filters.</li>}
        </ul>
      </aside>

      {/* map */}
      <div className="relative min-w-0 flex-1">
        <div ref={containerRef} className="absolute inset-0 h-full w-full" aria-label="Map of street-light outages" />
        {isLoading && <div className="absolute inset-0 z-10 flex items-center justify-center bg-paper/50"><Loading label="Loading map data…" /></div>}

        <div className="absolute left-3 top-3 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-line bg-surface-strong/95 px-3 py-2 shadow-subtle backdrop-blur">
          <Toggle label="100 m / 250 m rings" checked={showBuffers} onChange={setShowBuffers} />
          <Toggle label="Night crimes (sample)" checked={showCrimes} onChange={setShowCrimes} />
          <button type="button" className="text-xs font-semibold text-ink-soft transition-colors hover:text-ink" onClick={resetView}>Reset view</button>
        </div>

        <div className="absolute bottom-8 left-3 z-10 rounded-lg border border-line bg-surface-strong/95 px-3 py-2.5 text-[11px] text-ink shadow-subtle backdrop-blur">
          <p className="mb-1.5 font-bold">Priority tier</p>
          <ul className="space-y-1">
            {(['High', 'Medium', 'Low'] as PriorityTier[]).map((t) => (
              <li key={t} className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: TIER_MAP_COLORS[t] }} aria-hidden />{t}</li>
            ))}
            <li className="flex items-center gap-2 border-t border-line-soft pt-1.5"><span className="h-2.5 w-2.5 rounded-full border-2" style={{ borderColor: 'rgb(var(--c-signal-deep))', background: 'transparent' }} aria-hidden />Recommended for repair</li>
          </ul>
        </div>

        {selected && (
          <aside className="absolute right-3 top-3 z-20 w-72 animate-pop-in rounded-card border border-line bg-surface-strong p-4 shadow-modal" aria-label="Selected outage">
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="mb-1 flex flex-wrap items-center gap-1.5"><TierBadge tier={selected.tier} /><DecisionBadge status={selected.dispatch_status} long /></div>
                <p className="font-mono text-sm font-medium text-ink">{selected.id}</p>
                <p className="mt-0.5 text-xs text-ink-soft">{selected.location}</p>
              </div>
              <button type="button" aria-label="Close" className="rounded p-1 text-ink-soft transition-colors hover:bg-line-soft hover:text-ink" onClick={() => setSelectedId(null)}><X size={14} /></button>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3 border-t border-line-soft pt-3 text-xs">
              <div><dt className="eyebrow">Priority score</dt><dd className="text-lg font-extrabold tabular-nums text-ink">{formatScore(selected.score)}</dd></div>
              <div><dt className="eyebrow">Plan rank</dt><dd className="text-lg font-extrabold tabular-nums text-ink">{selected.optimization_rank ? `#${selected.optimization_rank}` : '–'}</dd></div>
              <div><dt className="eyebrow">Duration</dt><dd className="font-semibold tabular-nums text-ink">{formatDays(selected.duration_days)}</dd></div>
              <div><dt className="eyebrow">Reported</dt><dd className="font-semibold text-ink">{formatDate(selected.created_date)}</dd></div>
            </dl>
            <button type="button" className="btn btn-primary mt-4 w-full" onClick={() => navigate(`/outages/${selected.id}`)}>View outage details</button>
          </aside>
        )}
      </div>
    </div>
  );
};
