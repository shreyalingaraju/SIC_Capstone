"""
Stage 9: difference-in-differences models (Issue 4 panel).

Reads causal_panel.parquet (Stage 8) and writes did_summary.json and
did_regression_results.txt to --out (default outputs/).

Model specifications are unchanged: basic DiD OLS, separate during/post
DiD, two-way fixed-effects DiD (within transformation), Poisson and
Negative Binomial (NB2) DiD. baseline_crime_intensity stays a covariate
where it was (a known bad control, deferred as M8), so every estimate is
provisional.

Issue 4 changes (docs/issue4_migration_s8_s10.md, S9-1 to S9-6):
- D15: the fixed-effects unit is unit_id (pair x role), so each unit has
  exactly 3 periods and constant treatment status.
- D13: standard errors are clustered on treatment_h3_res7, the H3 res-7
  cell of the pair's treatment site (both rows of a pair share it). The
  primary scheme must have at least MIN_CLUSTERS clusters. Robustness
  clusterings for the basic OLS and FE models: pair_id; two-way
  (treatment_h3_res7, control_h3_res7); treatment police precinct
  (missing precinct = its own cluster). Cluster counts are reported.
- D20: the paired-difference estimate, per pair
      dd_during = (T_during - T_pre) - (C_during - C_pre)
      dd_post   = (T_post   - T_pre) - (C_post   - C_pre)
  for both outcomes, as a mean with SEs clustered on treatment_h3_res7,
  overall, by tercile of the treatment's log1p(base_100m) (edges at the
  1/3 and 2/3 quantiles; T1 <= q1 < T2 <= q2 < T3) and by created year.
  In a balanced 1:1 panel the overall mean equals the two-way FE
  coefficient; the run asserts this.
"""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf


# ============================================================
# Paths and constants
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from src import profile as _profile  # noqa: E402  (dataset profile: paths, bbox, CRS)

PROCESSED_DIR = PROJECT_ROOT / _profile.PROCESSED_DIR
OUTPUT_DIR = PROJECT_ROOT / _profile.OUTPUTS_DIR

CAUSAL_FILE = PROCESSED_DIR / "causal_panel.parquet"

RESULTS_FILENAME = "did_regression_results.txt"
SUMMARY_FILENAME = "did_summary.json"

PRIMARY_CLUSTER = "treatment_h3_res7"

# D13: the primary clustering needs at least this many clusters.
MIN_CLUSTERS = 50

# The FE coefficient and the mean paired difference must agree (D20).
PAIRED_FE_TOLERANCE = 1e-9

OUTCOMES = ("crime_100m", "crime_250m")

REQUIRED_COLUMNS = (
    "pair_id",
    "unit_id",
    "role",
    "location_key",
    "period",
    "treatment",
    "post",
    "treatment_x_post",
    "crime_100m",
    "crime_250m",
    "baseline_crime_intensity",
    "base_100m",
    "created_date",
    "treatment_h3_res7",
    "control_h3_res7",
    "treatment_police_precinct",
)

BASIC_FORMULA = """
    {outcome} ~ treatment
    + post
    + treatment_x_post
    + baseline_crime_intensity
"""

SEPARATE_FORMULA = """
    {outcome} ~ treatment
    + during
    + post_period
    + treatment_x_during
    + treatment_x_post_period
    + baseline_crime_intensity
"""

PERIOD_TERMS = ("treatment_x_during", "treatment_x_post_period")


# ============================================================
# Data
# ============================================================

def load_panel(path):
    df = pd.read_parquet(path)

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}. Is {path} an Issue 4 "
            "Stage 8 panel?"
        )

    periods = df.groupby("unit_id")["period"].nunique()
    if not (periods.eq(3).all() and df.groupby("unit_id").size().eq(3).all()):
        raise ValueError("every unit_id must have exactly one row per period")

    df["during"] = (df["period"] == "during").astype(int)
    df["post_period"] = (df["period"] == "post").astype(int)
    df["treatment_x_during"] = df["treatment"] * df["during"]
    df["treatment_x_post_period"] = df["treatment"] * df["post_period"]

    return df


def cluster_schemes(df):
    """Group arrays for each clustering scheme (D13)."""

    def codes(column, fill=None):
        values = df[column].astype(object)
        if fill is not None:
            values = values.where(values.notna(), fill)
        return pd.factorize(values, sort=True)[0]

    return {
        "treatment_h3_res7": codes("treatment_h3_res7"),
        "pair_id": codes("pair_id"),
        "twoway_treatment_control_h3_res7": np.column_stack(
            [codes("treatment_h3_res7"), codes("control_h3_res7")]
        ),
        "treatment_police_precinct": codes(
            "treatment_police_precinct", fill="<missing>"
        ),
    }


def n_clusters(groups):
    groups = np.asarray(groups)
    if groups.ndim == 2:
        return [int(len(np.unique(groups[:, k]))) for k in range(groups.shape[1])]
    return int(len(np.unique(groups)))


# ============================================================
# Models
# ============================================================

def fit_formula(formula, df, groups, family=None):
    if family is None:
        model = smf.ols(formula=formula, data=df)
    else:
        model = smf.glm(formula=formula, data=df, family=family)
    return model.fit(cov_type="cluster", cov_kwds={"groups": groups})


def two_way_demean(data, columns):
    """
    Remove unit and period fixed effects with the two-way within
    transformation (balanced panel):

        x_it - x_i. - x_.t + x_..

    The unit is unit_id (D15).
    """

    unit_means = data.groupby("unit_id")[columns].transform("mean")
    period_means = data.groupby("period")[columns].transform("mean")
    overall_means = data[columns].mean()

    return data[columns] - unit_means - period_means + overall_means


def fit_fe_model(df, outcome, groups):
    variables = [outcome, *PERIOD_TERMS]
    demeaned = two_way_demean(df, variables)

    return sm.OLS(
        demeaned[outcome],
        sm.add_constant(demeaned[list(PERIOD_TERMS)]),
    ).fit(cov_type="cluster", cov_kwds={"groups": groups})


def extract_result(model, term):
    ci = model.conf_int().loc[term]

    return {
        "coefficient": float(model.params[term]),
        "std_error": float(model.bse[term]),
        "p_value": float(model.pvalues[term]),
        "ci_lower": float(ci.iloc[0]),
        "ci_upper": float(ci.iloc[1]),
    }


def fit_models(df, groups):
    models = {}
    for outcome in OUTCOMES:
        models[("basic", outcome)] = fit_formula(
            BASIC_FORMULA.format(outcome=outcome), df, groups
        )
        models[("separate", outcome)] = fit_formula(
            SEPARATE_FORMULA.format(outcome=outcome), df, groups
        )
        models[("fe", outcome)] = fit_fe_model(df, outcome, groups)
        models[("poisson", outcome)] = fit_formula(
            SEPARATE_FORMULA.format(outcome=outcome), df, groups,
            family=sm.families.Poisson(),
        )
        models[("nb", outcome)] = fit_formula(
            SEPARATE_FORMULA.format(outcome=outcome), df, groups,
            family=sm.families.NegativeBinomial(),
        )
    return models


def robustness_clustering(df, schemes):
    """Basic OLS and FE DiD under every clustering scheme (D13)."""

    results = {}
    for name, groups in schemes.items():
        entry = {"n_clusters": n_clusters(groups)}
        for outcome in OUTCOMES:
            basic = fit_formula(BASIC_FORMULA.format(outcome=outcome), df, groups)
            fe = fit_fe_model(df, outcome, groups)
            entry[outcome] = {
                "basic_treatment_x_post": extract_result(basic, "treatment_x_post"),
                "fe_during": extract_result(fe, "treatment_x_during"),
                "fe_post": extract_result(fe, "treatment_x_post_period"),
            }
        results[name] = entry
    return results


# ============================================================
# D20: paired differences
# ============================================================

def paired_differences(df):
    """One row per pair with dd_during / dd_post for both outcomes."""

    wide = df.pivot_table(
        index="pair_id",
        columns=["role", "period"],
        values=list(OUTCOMES),
        aggfunc="first",
    )

    pairs = df[df["role"] == "T"].drop_duplicates("pair_id").set_index("pair_id")
    out = pd.DataFrame(index=wide.index)
    out["cluster"] = pairs.loc[out.index, PRIMARY_CLUSTER].astype(str)
    out["log_base_100m"] = np.log1p(pairs.loc[out.index, "base_100m"].astype(float))
    out["year"] = pd.to_datetime(pairs.loc[out.index, "created_date"]).dt.year

    for outcome in OUTCOMES:
        for period in ("during", "post"):
            out[f"{outcome}_dd_{period}"] = (
                (wide[(outcome, "T", period)] - wide[(outcome, "T", "pre")])
                - (wide[(outcome, "C", period)] - wide[(outcome, "C", "pre")])
            )

    return out.reset_index()


def mean_with_cluster_se(values, clusters):
    values = np.asarray(values, dtype=float)
    n = len(values)
    entry = {"n_pairs": int(n), "n_clusters": int(len(np.unique(clusters)))}

    if n == 0:
        return {**entry, "estimate": None, "std_error": None,
                "ci_lower": None, "ci_upper": None, "p_value": None}

    if entry["n_clusters"] < 2:
        return {**entry, "estimate": float(values.mean()), "std_error": None,
                "ci_lower": None, "ci_upper": None, "p_value": None}

    fit = sm.OLS(values, np.ones(n)).fit(
        cov_type="cluster",
        cov_kwds={"groups": pd.factorize(clusters, sort=True)[0]},
    )
    ci = fit.conf_int()[0]
    return {
        **entry,
        "estimate": float(fit.params[0]),
        "std_error": float(fit.bse[0]),
        "ci_lower": float(ci[0]),
        "ci_upper": float(ci[1]),
        "p_value": float(fit.pvalues[0]),
    }


def paired_estimates(pd_frame):
    q1, q2 = np.quantile(pd_frame["log_base_100m"], [1 / 3, 2 / 3])
    tercile = np.where(
        pd_frame["log_base_100m"] <= q1, "T1",
        np.where(pd_frame["log_base_100m"] <= q2, "T2", "T3"),
    )

    columns = [f"{o}_dd_{p}" for o in OUTCOMES for p in ("during", "post")]

    def estimate(frame):
        return {
            column: mean_with_cluster_se(frame[column], frame["cluster"].to_numpy())
            for column in columns
        }

    return {
        "definition": (
            "dd_period = (T_period - T_pre) - (C_period - C_pre) per pair; "
            "mean with SEs clustered on treatment_h3_res7"
        ),
        "overall": estimate(pd_frame),
        "tercile_edges_log1p_base_100m": [float(q1), float(q2)],
        "by_baseline_tercile": {
            name: estimate(pd_frame[tercile == name]) for name in ("T1", "T2", "T3")
        },
        "by_year": {
            int(year): estimate(frame)
            for year, frame in pd_frame.groupby("year", sort=True)
        },
    }


# ============================================================
# Output
# ============================================================

def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return value if math.isfinite(value) else None
    return value


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(2 ** 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build_summary(df, panel_path, schemes, models, robustness, paired):
    pairs = df[df["role"] == "T"].drop_duplicates("pair_id")
    controls = df[df["role"] == "C"].drop_duplicates("pair_id")
    uses = controls["location_key"].value_counts()

    def periods(kind, outcome):
        model = models[(kind, outcome)]
        return {
            "during": extract_result(model, "treatment_x_during"),
            "post": extract_result(model, "treatment_x_post_period"),
        }

    return {
        "status": "provisional (M8 open: exposure normalisation, "
                  "baseline_crime_intensity, event-study -7..0 gap)",
        "panel": {"path": Path(panel_path).as_posix(), "sha256": _sha256(panel_path)},
        "dataset": {
            "rows": int(len(df)),
            "unique_pairs": int(df["pair_id"].nunique()),
            "unique_units": int(df["unit_id"].nunique()),
            "unique_locations": int(df["location_key"].nunique()),
            "unique_treatment_sites": int(pairs["location_key"].nunique()),
            "unique_control_sites": int(controls["location_key"].nunique()),
            "control_reuse_mean": float(uses.mean()),
            "control_reuse_max": int(uses.max()),
            "cluster_variable": PRIMARY_CLUSTER,
            "fixed_effects_unit": "unit_id",
            "n_clusters": {name: n_clusters(groups) for name, groups in schemes.items()},
        },
        "basic_did_ols": {
            outcome: extract_result(models[("basic", outcome)], "treatment_x_post")
            for outcome in OUTCOMES
        },
        "separate_period_did": {o: periods("separate", o) for o in OUTCOMES},
        "fixed_effects_did": {o: periods("fe", o) for o in OUTCOMES},
        "poisson_did": {o: periods("poisson", o) for o in OUTCOMES},
        "negative_binomial_did": {o: periods("nb", o) for o in OUTCOMES},
        "robustness_clustering": robustness,
        "paired_difference": paired,
    }


MODEL_TITLES = (
    ("basic", "BASIC DiD OLS"),
    ("separate", "SEPARATE DURING/POST DiD"),
    ("fe", "UNIT/PERIOD FIXED-EFFECTS DiD (unit_id)"),
    ("poisson", "POISSON DiD"),
    ("nb", "NEGATIVE BINOMIAL DiD"),
)

OUTCOME_LABELS = {"crime_100m": "0-100m", "crime_250m": "100-250m"}


def write_results(path, df, summary, models):
    dataset = summary["dataset"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("LightSafe Stage 9 - Difference-in-Differences Regression Results\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Status: {summary['status']}\n\n")
        f.write("Dataset\n-------\n")
        f.write(f"Rows: {dataset['rows']:,}\n")
        f.write(f"Unique pairs: {dataset['unique_pairs']:,}\n")
        f.write(f"Unique units (unit_id): {dataset['unique_units']:,}\n")
        f.write(f"Unique locations: {dataset['unique_locations']:,}\n")
        f.write(f"Unique control sites: {dataset['unique_control_sites']:,} "
                f"(reuse mean {dataset['control_reuse_mean']:.3f}, "
                f"max {dataset['control_reuse_max']})\n")
        f.write(f"Cluster variable: {dataset['cluster_variable']} "
                f"({dataset['n_clusters'][PRIMARY_CLUSTER]:,} clusters)\n")
        f.write(f"Clusters per scheme: {dataset['n_clusters']}\n\n")

        for kind, title in MODEL_TITLES:
            for outcome in OUTCOMES:
                f.write("=" * 80 + "\n")
                f.write(f"{title} - {OUTCOME_LABELS[outcome]}\n")
                f.write("=" * 80 + "\n")
                f.write(models[(kind, outcome)].summary().as_text())
                f.write("\n\n")

        f.write("=" * 80 + "\nROBUSTNESS CLUSTERING (basic OLS and FE DiD)\n" + "=" * 80 + "\n")
        for scheme, entry in summary["robustness_clustering"].items():
            f.write(f"\n{scheme} (clusters: {entry['n_clusters']})\n")
            for outcome in OUTCOMES:
                for term, result in entry[outcome].items():
                    f.write(f"  {outcome} {term}: {result['coefficient']:+.5f} "
                            f"(SE {result['std_error']:.5f}, p {result['p_value']:.4f})\n")

        paired = summary["paired_difference"]
        f.write("\n" + "=" * 80 + "\nPAIRED-DIFFERENCE ESTIMATES (D20)\n" + "=" * 80 + "\n")
        f.write(paired["definition"] + "\n")
        groups = [("overall", paired["overall"])]
        groups += [(f"tercile {k}", v) for k, v in paired["by_baseline_tercile"].items()]
        groups += [(f"year {k}", v) for k, v in paired["by_year"].items()]
        for label, entry in groups:
            f.write(f"\n{label}\n")
            for column, result in entry.items():
                se = result["std_error"]
                est = result["estimate"]
                f.write(f"  {column}: {'NA' if est is None else f'{est:+.5f}'} "
                        f"(SE {'NA' if se is None else f'{se:.5f}'}, "
                        f"pairs {result['n_pairs']:,}, clusters {result['n_clusters']})\n")


# ============================================================
# Main
# ============================================================

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Stage 9 DiD models")
    parser.add_argument("--panel", type=Path, default=CAUSAL_FILE,
                        help="Stage 8 causal_panel.parquet")
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR,
                        help="output directory for the summary and results")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    print("Loading causal panel...")
    df = load_panel(args.panel)
    print("Panel shape:", df.shape)

    schemes = cluster_schemes(df)
    primary = schemes[PRIMARY_CLUSTER]
    counts = {name: n_clusters(groups) for name, groups in schemes.items()}
    print("Clusters per scheme:", counts)
    if counts[PRIMARY_CLUSTER] < MIN_CLUSTERS:
        raise ValueError(
            f"{PRIMARY_CLUSTER} has {counts[PRIMARY_CLUSTER]} clusters; "
            f"at least {MIN_CLUSTERS} are required (D13)"
        )

    print("Fitting models (clustered on treatment_h3_res7)...")
    models = fit_models(df, primary)

    print("Fitting robustness clusterings...")
    robustness = robustness_clustering(df, schemes)

    print("Computing paired differences (D20)...")
    pd_frame = paired_differences(df)
    paired = paired_estimates(pd_frame)

    for outcome in OUTCOMES:
        fe = models[("fe", outcome)]
        for period, term in (("during", "treatment_x_during"),
                             ("post", "treatment_x_post_period")):
            mean = paired["overall"][f"{outcome}_dd_{period}"]["estimate"]
            if abs(mean - float(fe.params[term])) > PAIRED_FE_TOLERANCE:
                raise AssertionError(
                    f"paired mean {mean} differs from FE {term} "
                    f"{float(fe.params[term])} for {outcome}"
                )
    print("Paired-difference means equal the FE coefficients.")

    summary = build_summary(df, args.panel, schemes, models, robustness, paired)

    args.out.mkdir(parents=True, exist_ok=True)
    summary_path = args.out / SUMMARY_FILENAME
    results_path = args.out / RESULTS_FILENAME
    summary_path.write_text(
        json.dumps(_json_safe(summary), indent=2, allow_nan=False),
        encoding="utf-8",
    )
    write_results(results_path, df, summary, models)

    print(f"\nRegression results:\n{results_path}")
    print(f"JSON summary:\n{summary_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
