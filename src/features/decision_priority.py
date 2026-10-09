"""
Decision layer: combines the ML risk prediction, the causal estimate and population/geography context into a repair priority.

The three kinds of information are kept apart and combined by an explicit, documented rule, not by multiplying raw outputs:

  XGBoost risk  - predicted night crimes per day within 100 m while the light is out (association, ex-ante features).
  Causal AI     - ONE average effect of an outage on night crime (matched-control DiD). It is not a local effect, so it
                  cannot rank outages against each other. It only decides how much the crime-risk component may count:
                  the share of dark-period crime that is attributable to the outage (`causal_weight`).
  Context       - population exposure, vulnerable share, recurrence, repair cost: each is its own component.

score_i = 100 * sum_k w_k c_ik / sum_k w_k      (sum over components available for outage i)

with components c in [0, 1] (percentile of the value within a fixed reference distribution, so one outage's score does
not depend on the other outages being scored together) and weights:

  risk        0.40 * causal_weight   (0 when the outage effect is not distinguishable from zero)
  exposure    0.25                   ward population (people who could be affected)
  vulnerable  0.15                   vulnerable-population share of the ward
  recurrence  0.10                   earlier complaints within 50 m in the previous 90 days (ex-ante), capped at 3
  efficiency  0.10                   1 - percentile of distance to the repair depot (cheaper repairs first)

The weights are judgement calls, not validated or fitted; `sensitivity()` reports how much the ranking moves when they change.
Population density, elevation, slope and rainfall are NOT scored directly: they enter only through the ML risk model, so the
same signal is not counted twice. Nothing here uses the realised outage duration or any other post-report information.
"""
import numpy as np
import pandas as pd

BASE_WEIGHTS = {"risk": 0.40, "exposure": 0.25, "vulnerable": 0.15, "recurrence": 0.10, "efficiency": 0.10}
RECURRENCE_CAP = 3
SOURCE = {  # component -> input column
    "risk": "predicted_risk", "exposure": "population", "vulnerable": "vulnerable_pop_share",
    "recurrence": "prior_complaints_90d_50m", "efficiency": "dist_depot_km",
}
LABEL = {
    "risk": "Predicted night-crime risk (XGBoost)", "exposure": "Population exposed", "vulnerable": "Vulnerable-population share",
    "recurrence": "Repeat failures nearby", "efficiency": "Repair efficiency (near depot)",
}
HIGH_TOP_SHARE, MEDIUM_TOP_SHARE = 0.10, 0.30   # tiers by score rank: top 10% High, next 20% Medium


def causal_weight(estimate, ci_lower, ci_upper, effect_per_day, mean_predicted_rate):
    """Share of dark-period night crime attributable to the outage, used as the weight multiplier on the risk component.

    0 if the effect is not distinguishable from zero (95% interval includes 0) or is not positive; otherwise
    effect_per_day / mean_predicted_rate clipped to [0, 1]. Both numerators and denominators are night crimes per day
    within 100 m, so the ratio is unit-consistent. It is a population-average share, applied as a gate, never per outage.
    """
    if not np.isfinite([estimate, effect_per_day, mean_predicted_rate]).all() or mean_predicted_rate <= 0:
        return 0.0
    if estimate <= 0 or (ci_lower is not None and ci_lower <= 0 <= (ci_upper if ci_upper is not None else 0)):
        return 0.0
    return float(np.clip(effect_per_day / mean_predicted_rate, 0.0, 1.0))


def _percentile(values, reference):
    ref = np.sort(np.asarray(reference, dtype=float)[np.isfinite(reference)])
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, np.nan)
    ok = np.isfinite(v)
    if len(ref):
        out[ok] = np.searchsorted(ref, v[ok], side="right") / len(ref)
    return out


def components(frame, reference):
    """Component values in [0, 1] (NaN where the input is missing: never silently zero)."""
    c = pd.DataFrame(index=frame.index)
    c["risk"] = _percentile(frame[SOURCE["risk"]], reference[SOURCE["risk"]])
    c["exposure"] = _percentile(frame[SOURCE["exposure"]], reference[SOURCE["exposure"]])
    c["vulnerable"] = _percentile(frame[SOURCE["vulnerable"]], reference[SOURCE["vulnerable"]])
    c["recurrence"] = np.minimum(frame[SOURCE["recurrence"]].astype(float), RECURRENCE_CAP) / RECURRENCE_CAP
    c["efficiency"] = 1.0 - _percentile(frame[SOURCE["efficiency"]], reference[SOURCE["efficiency"]])
    return c


def score(frame, reference=None, causal_w=1.0, weights=None):
    """Priority score 0-100 plus the component contributions (points) that add up to it.

    `reference` defaults to `frame` itself; pass a fixed frame to make a row's score independent of its batch.
    Returns (scores, contributions) where contributions[k] = 100 * w_k * c_k / sum(available w).
    """
    reference = frame if reference is None else reference
    w = dict(BASE_WEIGHTS if weights is None else weights)
    w["risk"] = w["risk"] * float(causal_w)
    comp = components(frame, reference)
    wv = np.array([w[k] for k in comp.columns], dtype=float)
    avail = comp.notna().to_numpy()
    num = np.where(avail, comp.fillna(0.0).to_numpy() * wv, 0.0)
    den = (avail * wv).sum(axis=1)
    contrib = pd.DataFrame(np.divide(100.0 * num, den[:, None], out=np.zeros_like(num), where=den[:, None] > 0),
                           index=frame.index, columns=comp.columns)
    total = contrib.sum(axis=1)
    total[den <= 0] = np.nan
    return total, contrib


def tiers(total):
    r = total.rank(ascending=False, method="first", pct=True)
    return pd.Series(np.where(r <= HIGH_TOP_SHARE, "High", np.where(r <= MEDIUM_TOP_SHARE, "Medium", "Low")), index=total.index).where(total.notna())


def reasons(contrib, frame, top=3):
    """Plain-language top reasons per outage, taken from the components that actually contributed the most points."""
    out = []
    names = list(contrib.columns)
    arr = contrib.to_numpy()
    for i in range(len(contrib)):
        order = np.argsort(-arr[i])[:top]
        parts = []
        for j in order:
            if arr[i, j] <= 0:
                continue
            k = names[j]
            parts.append(f"{LABEL[k]} (+{arr[i, j]:.0f})")
        out.append("; ".join(parts))
    return out


def sensitivity(frame, reference, causal_w, n_draws=30, seed=20250104, top_n=100):
    """How much the ranking moves under reasonable alternative weights (each weight scaled by U(0.6, 1.4)) and when
    each component is dropped. Reports Spearman correlation and top-N overlap with the base ranking."""
    from scipy.stats import spearmanr
    base, _ = score(frame, reference, causal_w)
    rng = np.random.default_rng(seed)
    rows = []

    def compare(label, w):
        s, _ = score(frame, reference, causal_w, w)
        top = set(base.nlargest(top_n).index) & set(s.nlargest(top_n).index)
        rows.append({"variant": label, "spearman_vs_base": float(spearmanr(base, s, nan_policy="omit").statistic),
                     f"top{top_n}_overlap": len(top) / top_n})

    for k in BASE_WEIGHTS:
        compare(f"drop {k}", {**BASE_WEIGHTS, k: 0.0})
    draws = []
    for d in range(n_draws):
        w = {k: v * rng.uniform(0.6, 1.4) for k, v in BASE_WEIGHTS.items()}
        compare(f"random weights #{d + 1}", w)
        draws.append(rows[-1])
    return {
        "top_n": top_n, "variants": rows,
        "random_weight_draws": n_draws,
        "random_spearman_min": float(min(r["spearman_vs_base"] for r in draws)),
        "random_spearman_median": float(np.median([r["spearman_vs_base"] for r in draws])),
        f"random_top{top_n}_overlap_median": float(np.median([r[f"top{top_n}_overlap"] for r in draws])),
    }
