from pathlib import Path
import json

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf


# ============================================================
# Paths
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUT_DIR = PROJECT_ROOT / "outputs"

CAUSAL_FILE = PROCESSED_DIR / "causal_panel.parquet"

RESULTS_FILE = OUTPUT_DIR / "did_regression_results.txt"
SUMMARY_FILE = OUTPUT_DIR / "did_summary.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Load causal panel
# ============================================================

print("Loading causal panel...")

df = pd.read_parquet(CAUSAL_FILE)

print("Panel shape:", df.shape)


# ============================================================
# Validate required columns
# ============================================================

required_columns = [
    "pair_id",
    "location_key",
    "period",
    "treatment",
    "post",
    "treatment_x_post",
    "crime_100m",
    "crime_250m",
    "baseline_crime_intensity",
]

missing_columns = [
    col for col in required_columns
    if col not in df.columns
]

if missing_columns:
    raise ValueError(
        f"Missing required columns: {missing_columns}"
    )


# ============================================================
# Create separate DURING and POST indicators
# ============================================================

df["during"] = (
    df["period"] == "during"
).astype(int)

df["post_period"] = (
    df["period"] == "post"
).astype(int)

df["treatment_x_during"] = (
    df["treatment"] * df["during"]
)

df["treatment_x_post_period"] = (
    df["treatment"] * df["post_period"]
)


# ============================================================
# Cluster variable
#
# The project guide uses a spatial-location cluster.
# The actual causal panel contains location_key rather than
# h3_index, so location_key is used.
# ============================================================

cluster_groups = df["location_key"]


# ============================================================
# Model 1:
# Basic DiD OLS
#
# This follows the guide's baseline specification:
#
# crime_count ~ treatment + post + treatment:post
#
# We use the actual panel variable treatment_x_post.
# ============================================================

print("\nFitting basic DiD OLS models...")

ols_100m = smf.ols(
    formula="""
        crime_100m ~ treatment
        + post
        + treatment_x_post
        + baseline_crime_intensity
    """,
    data=df,
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)

ols_250m = smf.ols(
    formula="""
        crime_250m ~ treatment
        + post
        + treatment_x_post
        + baseline_crime_intensity
    """,
    data=df,
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)


# ============================================================
# Model 2:
# Separate DURING / POST DiD
#
# PRE is the reference period.
# ============================================================

print("Fitting separate DURING/POST DiD models...")

separate_100m = smf.ols(
    formula="""
        crime_100m ~ treatment
        + during
        + post_period
        + treatment_x_during
        + treatment_x_post_period
        + baseline_crime_intensity
    """,
    data=df,
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)

separate_250m = smf.ols(
    formula="""
        crime_250m ~ treatment
        + during
        + post_period
        + treatment_x_during
        + treatment_x_post_period
        + baseline_crime_intensity
    """,
    data=df,
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)


# ============================================================
# Model 3:
# Location and time fixed-effects DiD
#
# treatment itself and baseline_crime_intensity are time
# invariant and therefore absorbed by location fixed effects.
#
# To avoid constructing tens of thousands of dummy variables,
# use the within transformation for location and period FE.
# ============================================================

print("Fitting location/time fixed-effects DiD models...")


def two_way_demean(data, columns):
    """
    Remove location and period fixed effects using
    the two-way within transformation.

    For a balanced panel:

        x_it - x_i. - x_.t + x_..

    This avoids explicitly constructing thousands of
    location dummy variables.
    """

    result = data[columns].copy()

    location_means = (
        data.groupby("location_key")[columns]
        .transform("mean")
    )

    period_means = (
        data.groupby("period")[columns]
        .transform("mean")
    )

    overall_means = data[columns].mean()

    result = (
        data[columns]
        - location_means
        - period_means
        + overall_means
    )

    return result


def fit_fe_model(outcome):
    variables = [
        outcome,
        "treatment_x_during",
        "treatment_x_post_period",
    ]

    demeaned = two_way_demean(
        df,
        variables,
    )

    fe_model = sm.OLS(
        demeaned[outcome],
        sm.add_constant(
            demeaned[
                [
                    "treatment_x_during",
                    "treatment_x_post_period",
                ]
            ]
        ),
    ).fit(
        cov_type="cluster",
        cov_kwds={"groups": cluster_groups},
    )

    return fe_model


fe_100m = fit_fe_model("crime_100m")
fe_250m = fit_fe_model("crime_250m")


# ============================================================
# Model 4:
# Poisson DiD
#
# Crime is a non-negative count outcome with many zeros.
# ============================================================

print("Fitting Poisson DiD models...")

poisson_100m = smf.glm(
    formula="""
        crime_100m ~ treatment
        + during
        + post_period
        + treatment_x_during
        + treatment_x_post_period
        + baseline_crime_intensity
    """,
    data=df,
    family=sm.families.Poisson(),
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)

poisson_250m = smf.glm(
    formula="""
        crime_250m ~ treatment
        + during
        + post_period
        + treatment_x_during
        + treatment_x_post_period
        + baseline_crime_intensity
    """,
    data=df,
    family=sm.families.Poisson(),
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)


# ============================================================
# Model 5:
# Negative Binomial DiD
#
# NB2 is useful as an additional count-data specification.
# ============================================================

print("Fitting Negative Binomial DiD models...")

nb_100m = smf.glm(
    formula="""
        crime_100m ~ treatment
        + during
        + post_period
        + treatment_x_during
        + treatment_x_post_period
        + baseline_crime_intensity
    """,
    data=df,
    family=sm.families.NegativeBinomial(),
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)

nb_250m = smf.glm(
    formula="""
        crime_250m ~ treatment
        + during
        + post_period
        + treatment_x_during
        + treatment_x_post_period
        + baseline_crime_intensity
    """,
    data=df,
    family=sm.families.NegativeBinomial(),
).fit(
    cov_type="cluster",
    cov_kwds={"groups": cluster_groups},
)


# ============================================================
# Helper function for extracting coefficients
# ============================================================

def extract_result(model, term):
    ci = model.conf_int().loc[term]

    return {
        "coefficient": float(model.params[term]),
        "std_error": float(model.bse[term]),
        "p_value": float(model.pvalues[term]),
        "ci_lower": float(ci.iloc[0]),
        "ci_upper": float(ci.iloc[1]),
    }


# ============================================================
# Build machine-readable summary
# ============================================================

summary = {
    "dataset": {
        "rows": int(len(df)),
        "unique_pairs": int(df["pair_id"].nunique()),
        "unique_locations": int(df["location_key"].nunique()),
        "cluster_variable": "location_key",
    },

    "basic_did_ols": {
        "crime_100m": extract_result(
            ols_100m,
            "treatment_x_post",
        ),
        "crime_250m": extract_result(
            ols_250m,
            "treatment_x_post",
        ),
    },

    "separate_period_did": {
        "crime_100m": {
            "during": extract_result(
                separate_100m,
                "treatment_x_during",
            ),
            "post": extract_result(
                separate_100m,
                "treatment_x_post_period",
            ),
        },
        "crime_250m": {
            "during": extract_result(
                separate_250m,
                "treatment_x_during",
            ),
            "post": extract_result(
                separate_250m,
                "treatment_x_post_period",
            ),
        },
    },

    "fixed_effects_did": {
        "crime_100m": {
            "during": extract_result(
                fe_100m,
                "treatment_x_during",
            ),
            "post": extract_result(
                fe_100m,
                "treatment_x_post_period",
            ),
        },
        "crime_250m": {
            "during": extract_result(
                fe_250m,
                "treatment_x_during",
            ),
            "post": extract_result(
                fe_250m,
                "treatment_x_post_period",
            ),
        },
    },

    "poisson_did": {
        "crime_100m": {
            "during": extract_result(
                poisson_100m,
                "treatment_x_during",
            ),
            "post": extract_result(
                poisson_100m,
                "treatment_x_post_period",
            ),
        },
        "crime_250m": {
            "during": extract_result(
                poisson_250m,
                "treatment_x_during",
            ),
            "post": extract_result(
                poisson_250m,
                "treatment_x_post_period",
            ),
        },
    },

    "negative_binomial_did": {
        "crime_100m": {
            "during": extract_result(
                nb_100m,
                "treatment_x_during",
            ),
            "post": extract_result(
                nb_100m,
                "treatment_x_post_period",
            ),
        },
        "crime_250m": {
            "during": extract_result(
                nb_250m,
                "treatment_x_during",
            ),
            "post": extract_result(
                nb_250m,
                "treatment_x_post_period",
            ),
        },
    },
}


# ============================================================
# Save JSON summary
# ============================================================

with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
    json.dump(
        summary,
        f,
        indent=2,
    )


# ============================================================
# Save human-readable regression output
# ============================================================

with open(
    RESULTS_FILE,
    "w",
    encoding="utf-8",
) as f:

    f.write(
        "LightSafe Stage 9 - "
        "Difference-in-Differences Regression Results\n"
    )

    f.write("=" * 80 + "\n\n")

    f.write(
        "Dataset\n"
        "-------\n"
    )

    f.write(
        f"Rows: {len(df):,}\n"
    )

    f.write(
        f"Unique pairs: {df['pair_id'].nunique():,}\n"
    )

    f.write(
        f"Unique locations: "
        f"{df['location_key'].nunique():,}\n"
    )

    f.write(
        "Cluster variable: location_key\n\n"
    )

    f.write(
        "=" * 80 + "\n"
        "BASIC DiD OLS - 0-100m\n"
        + "=" * 80 + "\n"
    )

    f.write(
        ols_100m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "BASIC DiD OLS - 100-250m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        ols_250m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "SEPARATE DURING/POST DiD - 0-100m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        separate_100m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "SEPARATE DURING/POST DiD - 100-250m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        separate_250m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "LOCATION/TIME FIXED-EFFECTS DiD - 0-100m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        fe_100m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "LOCATION/TIME FIXED-EFFECTS DiD - 100-250m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        fe_250m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "POISSON DiD - 0-100m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        poisson_100m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "POISSON DiD - 100-250m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        poisson_250m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "NEGATIVE BINOMIAL DiD - 0-100m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        nb_100m.summary().as_text()
    )

    f.write(
        "\n\n"
        + "=" * 80
        + "\n"
        "NEGATIVE BINOMIAL DiD - 100-250m\n"
        + "=" * 80
        + "\n"
    )

    f.write(
        nb_250m.summary().as_text()
    )


# ============================================================
# Final message
# ============================================================

print("\nStage 9 model estimation complete.")

print(
    f"Regression results:\n{RESULTS_FILE}"
)

print(
    f"JSON summary:\n{SUMMARY_FILE}"
)