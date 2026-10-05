"""
Stage 11: spatial displacement analysis (direct, displacement and net effects).

Reads the canonical Stage 8 causal_panel.parquet and writes
displacement_estimates.csv to --out (default outputs/). Nothing upstream is
modified: the panel is read-only and Stage 9 definitions are imported, not
copied (did_model.load_panel, paired_differences, mean_with_cluster_se).

Specification (identical for both rings, per Stage 9 D20 / two-way FE):
- Per pair, dd_period = (T_period - T_pre) - (C_period - C_pre), for
  period in {during, post}, on each ring outcome. In the balanced 1:1 panel
  the mean of dd equals the two-way unit/period FE coefficient (asserted by
  Stage 9; re-checked here against the Stage 9 estimator).
- Direct effect:        tau_direct = mean dd of crime_100m   (d <= 100 m)
- Displacement effect:  tau_disp   = mean dd of crime_250m   (100 < d <= 250 m)
- Standard errors: CR1 cluster-robust on treatment_h3_res7 (D13), the same
  estimator and reference distribution (normal) as Stage 9.

Sign convention (SIGN_CONVENTION below). Both outcomes are raw crime counts,
so every tau is a change in crimes per unit-window; positive = more crime.
The two rings are disjoint and together cover 0-250 m, so the total change
inside 0-250 m is the sum:

    tau_net = tau_direct + tau_displacement

The "minus" form is correct only if "displacement" is defined as a
positively-signed amount of crime moved *into* the ring while tau_direct is a
reduction; it is not used here because tau_displacement is the signed ring
effect, not that quantity.

Net variance includes the covariance between the two estimates. Both are
means of per-pair quantities from the same pairs and clusters, so the
covariance is estimated with the same cluster-robust formula:
    Var(net) = Var(direct) + Var(disp) + 2 Cov(direct, disp)
which equals the cluster-robust variance of the mean of dd_100 + dd_250.

Displacement proportion = -tau_disp / tau_direct: the share of the direct
effect offset by an opposite-signed ring effect (equivalently
1 - tau_net / tau_direct). Undefined (NaN) when tau_direct is zero.
0 = no displacement, 1 = full offset, > 1 = over-displacement,
< 0 = the ring moves the same way as the direct zone (diffusion of the
effect, not displacement). Its SE is a delta-method approximation and is
unreliable when tau_direct is close to zero.
"""

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.models import did_model  # noqa: E402  (accepted Stage 9 definitions)


# ============================================================
# Paths and constants
# ============================================================

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

CAUSAL_FILE = PROCESSED_DIR / "causal_panel.parquet"
OUTPUT_FILENAME = "displacement_estimates.csv"

DIRECT_OUTCOME = "crime_100m"
DISPLACEMENT_OUTCOME = "crime_250m"
PERIODS = ("during", "post")

Z_95 = 1.959963984540054  # normal reference, as Stage 9's cluster covariance

# A direct effect smaller than this in absolute value is treated as zero.
ZERO_TOLERANCE = 1e-12

MODEL = "paired-difference DiD (= two-way FE, unit_id x period)"
CLUSTER = did_model.PRIMARY_CLUSTER
PAIRED_FE_TOLERANCE = did_model.PAIRED_FE_TOLERANCE

SIGN_CONVENTION = (
    "tau = change in crime counts (positive = more crime); "
    "net = direct + displacement; "
    "displacement_proportion = -displacement / direct"
)

OUTCOME_RINGS = {
    "direct": "0-100 m (crime_100m)",
    "displacement": "100-250 m ring (crime_250m)",
    "net": "0-250 m (crime_100m + crime_250m)",
    "displacement_proportion": "100-250 m relative to 0-100 m",
}

COLUMNS = [
    "effect_name", "estimate", "standard_error", "variance", "ci_lower",
    "ci_upper", "p_value", "n_observations", "n_units", "n_pairs",
    "n_clusters", "model", "specification", "outcome_ring",
    "sign_convention", "note",
]


# ============================================================
# Estimation
# ============================================================

def cluster_moments(x, y, clusters):
    """
    CR1 cluster-robust (co)variance matrix of the means of x and y.

    For a mean the score is e_i = x_i - mean(x); cluster sums of the scores
    give V = G/(G-1) * sum_g s_g s_g' / n^2, the same quantity statsmodels
    reports for an OLS on a constant with cov_type="cluster" (K = 1).
    Returns (means, 2x2 covariance, number of clusters).
    """

    values = np.column_stack([np.asarray(x, float), np.asarray(y, float)])
    n = len(values)
    groups = pd.factorize(np.asarray(clusters), sort=True)[0]
    g = int(groups.max()) + 1
    if g < 2:
        raise ValueError("at least 2 clusters are required")

    means = values.mean(axis=0)
    sums = np.zeros((g, 2))
    np.add.at(sums, groups, values - means)
    cov = (g / (g - 1)) * (sums.T @ sums) / n ** 2
    return means, cov, g


def combine_net(direct, displacement, var_direct, var_displacement, cov):
    """Net effect and its variance, including the covariance (see module doc)."""

    return (
        direct + displacement,
        var_direct + var_displacement + 2.0 * cov,
    )


def confidence_interval(estimate, variance, z=Z_95):
    """Normal-reference interval; NaN when the variance is not usable."""

    if not (math.isfinite(estimate) and math.isfinite(variance)) or variance < 0:
        return float("nan"), float("nan")
    half = z * math.sqrt(variance)
    return estimate - half, estimate + half


def displacement_proportion(direct, displacement, var_direct,
                            var_displacement, cov):
    """
    -displacement / direct with a delta-method variance.

    Returns (proportion, variance). NaN for both when |direct| is below
    ZERO_TOLERANCE (the ratio is undefined, not zero or infinite).
    """

    if not math.isfinite(direct) or abs(direct) < ZERO_TOLERANCE:
        return float("nan"), float("nan")

    proportion = -displacement / direct
    d_direct = displacement / direct ** 2
    d_displacement = -1.0 / direct
    variance = (
        d_direct ** 2 * var_direct
        + d_displacement ** 2 * var_displacement
        + 2.0 * d_direct * d_displacement * cov
    )
    return proportion, max(variance, 0.0)


def p_value(estimate, variance):
    if not (math.isfinite(estimate) and math.isfinite(variance)) or variance <= 0:
        return float("nan")
    return math.erfc(abs(estimate) / math.sqrt(variance) / math.sqrt(2.0))


def estimate_period(pairs, period):
    """All Stage 11 quantities for one period from the paired differences."""

    x = pairs[f"{DIRECT_OUTCOME}_dd_{period}"].to_numpy()
    y = pairs[f"{DISPLACEMENT_OUTCOME}_dd_{period}"].to_numpy()
    means, cov, g = cluster_moments(x, y, pairs["cluster"].to_numpy())

    direct, displacement = (float(m) for m in means)
    v_direct, v_disp, c = float(cov[0, 0]), float(cov[1, 1]), float(cov[0, 1])
    net, v_net = combine_net(direct, displacement, v_direct, v_disp, c)
    prop, v_prop = displacement_proportion(direct, displacement, v_direct, v_disp, c)

    return {
        "direct": (direct, v_direct),
        "displacement": (displacement, v_disp),
        "net": (net, v_net),
        "displacement_proportion": (prop, v_prop),
        "covariance": c,
        "n_clusters": g,
    }


def crosscheck_with_stage9(pairs, result, period):
    """Direct/displacement SEs must equal Stage 9's mean_with_cluster_se."""

    for key, outcome in (("direct", DIRECT_OUTCOME),
                         ("displacement", DISPLACEMENT_OUTCOME)):
        ref = did_model.mean_with_cluster_se(
            pairs[f"{outcome}_dd_{period}"], pairs["cluster"].to_numpy()
        )
        est, var = result[key]
        if (abs(est - ref["estimate"]) > PAIRED_FE_TOLERANCE
                or abs(math.sqrt(var) - ref["std_error"]) > 1e-9):
            raise AssertionError(
                f"{key} {period}: ({est}, {math.sqrt(var)}) differs from "
                f"Stage 9 ({ref['estimate']}, {ref['std_error']})"
            )

    ref_net = did_model.mean_with_cluster_se(
        pairs[f"{DIRECT_OUTCOME}_dd_{period}"]
        + pairs[f"{DISPLACEMENT_OUTCOME}_dd_{period}"],
        pairs["cluster"].to_numpy(),
    )
    est, var = result["net"]
    if (abs(est - ref_net["estimate"]) > PAIRED_FE_TOLERANCE
            or abs(math.sqrt(var) - ref_net["std_error"]) > 1e-9):
        raise AssertionError(
            f"net {period}: variance with covariance differs from the "
            "cluster-robust variance of the summed paired differences"
        )


def crosscheck_with_fe(df, pairs, result, period):
    """The paired means must equal the Stage 9 two-way FE coefficients."""

    groups = did_model.cluster_schemes(df)[CLUSTER]
    term = {"during": "treatment_x_during", "post": "treatment_x_post_period"}[period]
    for key, outcome in (("direct", DIRECT_OUTCOME),
                         ("displacement", DISPLACEMENT_OUTCOME)):
        fe = did_model.fit_fe_model(df, outcome, groups)
        if abs(float(fe.params[term]) - result[key][0]) > PAIRED_FE_TOLERANCE:
            raise AssertionError(f"{key} {period}: paired mean != FE coefficient")


# ============================================================
# Output
# ============================================================

def build_table(result_by_period, n_observations, n_units, n_pairs):
    rows = []
    for period, result in result_by_period.items():
        specification = (
            f"{period} vs pre; per-pair DiD, CR1 SE clustered on {CLUSTER} "
            f"({result['n_clusters']} clusters)"
        )
        for name in ("direct", "displacement", "net", "displacement_proportion"):
            estimate, variance = result[name]
            lower, upper = confidence_interval(estimate, variance)
            note = ""
            if name == "net":
                note = (
                    f"variance includes covariance "
                    f"{result['covariance']:.6g} between direct and "
                    "displacement estimates"
                )
            elif name == "displacement_proportion":
                note = (
                    "delta-method SE; NaN if direct effect is 0; "
                    "unreliable when the direct effect is near zero"
                )
            rows.append({
                "effect_name": f"{name}_{period}",
                "estimate": estimate,
                "standard_error": math.sqrt(variance) if math.isfinite(variance) else float("nan"),
                "variance": variance,
                "ci_lower": lower,
                "ci_upper": upper,
                "p_value": p_value(estimate, variance),
                "n_observations": n_observations,
                "n_units": n_units,
                "n_pairs": n_pairs,
                "n_clusters": result["n_clusters"],
                "model": MODEL,
                "specification": specification,
                "outcome_ring": OUTCOME_RINGS[name],
                "sign_convention": SIGN_CONVENTION,
                "note": note,
            })
    return pd.DataFrame(rows, columns=COLUMNS)


def run(panel_path):
    df = did_model.load_panel(panel_path)
    pairs = did_model.paired_differences(df)
    if pairs["cluster"].nunique() < did_model.MIN_CLUSTERS:
        raise ValueError(
            f"{CLUSTER} has fewer than {did_model.MIN_CLUSTERS} clusters (D13)"
        )

    results = {}
    for period in PERIODS:
        result = estimate_period(pairs, period)
        crosscheck_with_stage9(pairs, result, period)
        crosscheck_with_fe(df, pairs, result, period)
        results[period] = result

    table = build_table(
        results, int(len(df)), int(df["unit_id"].nunique()),
        int(df["pair_id"].nunique()),
    )
    return table, results, pairs


# ============================================================
# Main
# ============================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 11 spatial displacement")
    parser.add_argument("--panel", type=Path, default=CAUSAL_FILE,
                        help="Stage 8 causal_panel.parquet")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR,
                        help="output directory for displacement_estimates.csv")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("Loading causal panel...")
    table, _, _ = run(args.panel)

    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / OUTPUT_FILENAME
    table.to_csv(path, index=False)

    with pd.option_context("display.width", 200, "display.max_columns", 8):
        print(table[["effect_name", "estimate", "standard_error",
                     "ci_lower", "ci_upper", "p_value"]].to_string(index=False))
    print(f"\nDisplacement estimates:\n{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
