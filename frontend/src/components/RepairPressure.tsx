import React from 'react';
import { useMlRegime } from '../hooks/useMlRegime';
import { RegimeBorough } from '../types/ml';
import { formatDate, formatNumber } from '../lib/formatters';

const Position: React.FC<{ b: RegimeBorough }> = ({ b }) => {
  const pct = Math.max(0, Math.min(100, b.relative_score ?? 0));
  return (
    <div className="flex items-center gap-3">
      <div
        className="progress w-28 shrink-0 sm:w-40"
        role="img"
        aria-label={`${b.borough}: ${b.above_reference_range ? 'above the whole 2024–2025 range' : `at the ${Math.round(pct)}th percentile of 2024–2025`}`}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-ink-soft">
        {b.above_reference_range ? 'Above 2024–25 range' : `${Math.round(pct)}th percentile`}
      </span>
    </div>
  );
};

/** Borough repair-pressure context. Neutral styling on purpose: it must not read as a crime or priority alert. */
export const RepairPressure: React.FC<{ borough: string }> = ({ borough }) => {
  const { data, isLoading, isError, refetch } = useMlRegime();
  const rows = (data?.boroughs ?? []).filter((b) => borough === 'All' || b.borough === borough);

  return (
    <section className="card" aria-labelledby="pressure">
      <div className="border-b border-line px-5 py-3.5">
        <h2 id="pressure" className="card-title">Repair pressure</h2>
        <p className="mt-0.5 text-xs text-ink-soft">
          How slowly street-light complaints are being resolved, compared with each borough&apos;s own 2024–2025 history.
          {data?.window && <> Based on complaints from {formatDate(data.window.start)} to {formatDate(data.window.end)}.</>}
        </p>
      </div>

      {isLoading && <p className="px-5 py-8 text-center text-xs text-ink-soft" role="status">Loading repair pressure…</p>}

      {isError && (
        <div className="px-5 py-8 text-center text-xs text-ink-soft">
          <p>Repair pressure could not be loaded.</p>
          <button type="button" className="btn btn-sm mt-3" onClick={() => refetch()}>Retry</button>
        </div>
      )}

      {data && !data.available && (
        <p className="px-5 py-8 text-center text-xs text-ink-soft">{data.note ?? 'Repair pressure is not available.'}</p>
      )}

      {data?.available && (
        <>
          {rows.length === 0 ? (
            <p className="px-5 py-8 text-center text-xs text-ink-soft">No repair-pressure reading for {borough}.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Borough</th>
                    <th scope="col">Repair pressure</th>
                    <th scope="col">Position in 2024–2025 history</th>
                    <th scope="col" className="num">Complaints in window</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.borough}>
                      <td className="font-semibold">{b.borough}</td>
                      {b.available ? (
                        <>
                          <td><span className="badge badge-neutral">{b.category}</span></td>
                          <td><Position b={b} /></td>
                        </>
                      ) : (
                        <td colSpan={2} className="text-ink-soft">Not enough recent complaints</td>
                      )}
                      <td className="num text-ink-soft">{formatNumber(b.observation_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="border-t border-line-soft px-5 py-3 text-xs leading-relaxed text-ink-soft">
            {data.explanation} Repair pace has slowed since 2024, so several boroughs can read High at once.
          </p>
        </>
      )}
    </section>
  );
};
