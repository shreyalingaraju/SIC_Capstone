import React from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { ArrowLeft, CheckCircle2, Flag, MapPin, PauseCircle, RotateCcw } from 'lucide-react';
import { useOutageDetail, useOutageActionMutation } from '../hooks/useOutages';
import { ApiError } from '../lib/api';
import { DecisionBadge, EmptyState, ErrorState, Loading, ScoreNote, TierBadge } from '../components/ui';
import { formatDate, formatDays, formatImpact, formatNumber, formatScore } from '../lib/formatters';

const Row: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="flex items-baseline justify-between gap-4 border-b border-line-soft py-2 text-[13px] last:border-0">
    <dt className="text-ink-soft">{label}</dt>
    <dd className="text-right font-semibold tabular-nums text-ink">{value}</dd>
  </div>
);

export const OutageDetails: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: outage, isLoading, isError, error, refetch } = useOutageDetail(id);
  const actionMutation = useOutageActionMutation();

  const back = (
    <button type="button" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate('/outages'))} className="mb-4 inline-flex items-center gap-1.5 text-xs font-semibold text-ink-soft transition-colors hover:text-ink">
      <ArrowLeft size={14} aria-hidden /> Back
    </button>
  );

  if (isLoading) return <Loading label="Loading outage…" />;
  if (isError && !(error instanceof ApiError && error.status === 404)) {
    // network failure or a server error: the outage may well exist, so do not say it was not found
    return (
      <>
        <div className="mx-auto w-full max-w-[1400px] px-6 pt-6 lg:px-8">{back}</div>
        <ErrorState
          title="Outage details could not be loaded"
          detail="The LightSafe API did not respond, so this outage could not be checked. Make sure the LightSafe service is running, then retry."
          onRetry={() => refetch()}
        />
      </>
    );
  }
  if (isError || !outage) {
    return (
      <div className="page max-w-3xl">
        {back}
        <div className="card"><EmptyState tone="warn" title={`Outage ${id} was not found`} detail="Check the ID, or search from the Outages page." /></div>
      </div>
    );
  }

  const d = outage.decomposition;
  const act = (action: 'approve' | 'defer' | 'flag' | 'reset') => actionMutation.mutate({ outageId: outage.outage_id, action });

  return (
    <div className="page max-w-5xl space-y-5">
      <div>
        {back}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-1.5 flex flex-wrap items-center gap-2">
              {outage.scored && <TierBadge tier={outage.priority_tier} />}
              <DecisionBadge status={outage.dispatch_status} long />
              {outage.operator_note !== 'None' && <span className="badge badge-neutral">Note: {outage.operator_note}</span>}
            </div>
            <h1 className="page-title font-mono font-medium">Outage {outage.outage_id}</h1>
            <p className="page-sub flex items-center gap-1.5"><MapPin size={13} aria-hidden />{outage.location_desc}{outage.police_precinct ? ` · ${outage.police_precinct}` : ''}</p>
          </div>

          <div className="card flex items-stretch divide-x divide-line">
            <div className="px-5 py-3">
              <p className="eyebrow">Priority score</p>
              <p className="text-2xl font-extrabold tabular-nums text-ink">{outage.scored ? formatScore(outage.priority_score) : '–'}</p>
            </div>
            <div className="px-5 py-3">
              <p className="eyebrow">Plan rank</p>
              <p className="text-2xl font-extrabold tabular-nums text-ink">{outage.optimization_rank ? `#${outage.optimization_rank}` : '–'}</p>
            </div>
          </div>
        </div>
      </div>

      {!outage.scored && (
        <div className="notice notice-warn">
          <span className="font-bold">Not scored.</span>{' '}
          {outage.exclusion_reason === 'lookback_not_covered_by_crime_data'
            ? 'The crime data does not cover the period before this report, so no score was calculated.'
            : `Reason: ${(outage.exclusion_reason ?? 'not specified').replace(/_/g, ' ')}.`}{' '}
          It has no queue position or dispatch decision.
        </div>
      )}

      <div className="grid grid-cols-1 items-start gap-5 md:grid-cols-2">
        <section className="card p-5" aria-labelledby="why">
          <h2 id="why" className="card-title mb-2">Why this score</h2>
          {d ? (
            <>
              <dl>
                <Row label={`Crimes per day within 250 m (previous ${d.lookback_days} days)`} value={d.local_crime_rate?.toFixed(3)} />
                <Row label="Days the light was out" value={d.duration_factor?.toFixed(2)} />
                <Row label="Assumed crime effect of an outage" value={d.tau_net !== null ? d.tau_net.toFixed(4) : '–'} />
                <Row label="Unscaled priority" value={d.raw_priority?.toPrecision(3)} />
                <Row label="Score (scaled 0–100)" value={<span className="text-brand">{formatScore(d.priority_score)}</span>} />
              </dl>
              <p className="mt-3 text-xs leading-relaxed text-ink-soft">
                The unscaled priority multiplies these three inputs, then scores are scaled across all outages. The assumed crime effect is one value used for every outage and is not statistically distinguishable from zero (see <Link to="/causal" className="font-semibold text-brand hover:underline">Evidence</Link>), so the ranking mainly reflects recent nearby crime and how long the light has been out.
              </p>
            </>
          ) : (
            <p className="text-xs text-ink-soft">No score components, because this outage was not scored.</p>
          )}
        </section>

        <div className="space-y-5">
          <section className="card p-5" aria-labelledby="rec">
            <h2 id="rec" className="card-title mb-3">Dispatch recommendation</h2>
            {outage.dispatch_status === 'recommended' ? (
              <div className="notice badge-ok" style={{ borderColor: 'rgb(var(--t-ok-bd))' }}>
                <p className="font-bold">Repair now</p>
                <p className="mt-0.5">Selected at plan rank #{outage.optimization_rank}, within the daily budget and borough minimums.</p>
              </div>
            ) : outage.dispatch_status === 'deferred' ? (
              <div className="notice notice-info">Deferred. It is scored but did not fit in today&apos;s repair budget.</div>
            ) : (
              <div className="notice notice-info">No dispatch decision.</div>
            )}
            <dl className="mt-3">
              <Row label="Position if repaired by priority" value={outage.lightsafe_queue_rank ? `#${formatNumber(outage.lightsafe_queue_rank)}` : '–'} />
              <Row label="Position if repaired oldest first" value={outage.fifo_queue_rank ? `#${formatNumber(outage.fifo_queue_rank)}` : '–'} />
              {outage.impact_index !== null && outage.impact_index !== undefined && <Row label="Contribution to plan total" value={`${formatImpact(outage.impact_index)} pts`} />}
            </dl>
            <div className="mt-4 border-t border-line-soft pt-3">
              <p className="mb-2 text-xs text-ink-soft">Personal note for this session. It does not change the plan.</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn btn-sm" onClick={() => act('approve')}><CheckCircle2 size={13} aria-hidden /> Approve</button>
                <button type="button" className="btn btn-sm" onClick={() => act('defer')}><PauseCircle size={13} aria-hidden /> Defer</button>
                <button type="button" className="btn btn-sm" onClick={() => act('flag')}><Flag size={13} aria-hidden /> Flag</button>
                {outage.operator_note !== 'None' && <button type="button" className="btn btn-sm" onClick={() => act('reset')}><RotateCcw size={13} aria-hidden /> Clear</button>}
              </div>
            </div>
          </section>

          <section className="card p-5" aria-labelledby="rec-record">
            <h2 id="rec-record" className="card-title mb-2">Outage record</h2>
            <dl>
              <Row label="Borough" value={outage.borough} />
              <Row label="Reported" value={formatDate(outage.created_date)} />
              <Row label="Closed" value={formatDate(outage.closed_date)} />
              <Row label="Duration" value={formatDays(outage.duration_days)} />
              <Row label="Crimes within 250 m in the 14 days before" value={outage.recent_crime_count_14d ?? '–'} />
            </dl>
          </section>
        </div>
      </div>

      <ScoreNote />
    </div>
  );
};
