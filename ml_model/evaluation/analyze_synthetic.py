"""Post-hoc analysis of the frozen model on the synthetic set (diagnostics only; nothing is fit or tuned).

Run after:  python -m evaluation.evaluate_synthetic --data synthetic_data/data/synthetic_streetlight_complaints.csv
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score

from evaluation.metrics import domain_shift_table, evaluate, plot_confusion, plot_curves
from features.build_features import (CAT_FEATURES, FEATURES, NUM_FEATURES, add_labels,
                                     engineer_features, standardize)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "synthetic"
HISTORY_READY_FROM = pd.Timestamp("2024-03-01")      # = generator OUT_START + 60 days (ground_truth.history_ready)


def auc(y, p):
    return float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else np.nan


def main():
    card = json.loads((ROOT / "models/model_card.json").read_text())
    thr = card["decision_threshold"]
    model = joblib.load(ROOT / "models/frozen_model.joblib")
    raw = pd.read_csv(ROOT / "synthetic_data/data/synthetic_streetlight_complaints.csv")
    gt = pd.read_csv(ROOT / "synthetic_data/validation/ground_truth.csv", parse_dates=["created_date"])
    df = engineer_features(add_labels(standardize(raw)))
    k = df[df.label_known].copy()
    k["y"] = k.slow_repair.astype(int)
    k["p"] = model.predict_proba(k[FEATURES])[:, 1]
    pred = pd.read_csv(OUT / "synthetic_predictions.csv")
    assert np.allclose(pred.p_slow_repair.values, k.p.values), "recomputed scores differ from saved predictions"
    k["history_ready"] = k.created_date >= HISTORY_READY_FROM
    # cross-check flag against generator ground truth
    g = gt.drop_duplicates("complaint_id").set_index("complaint_id")
    k = k.join(g[["history_ready", "slow_repair"]].rename(columns={"history_ready": "gt_hr", "slow_repair": "gt_slow"}), on="complaint_id")
    assert (k.history_ready == k.gt_hr).all() and (k.y == k.gt_slow).all()

    groups = {"all": k, "history_ready": k[k.history_ready], "warmup_only": k[~k.history_ready],
              "history_ready_deduplicated": k[k.history_ready].drop_duplicates("complaint_id")}
    res = {n: evaluate(g_.y, g_.p, thr) for n, g_ in groups.items()}
    for n, m in res.items():
        m["pr_auc_minus_prevalence"] = m["pr_auc"] - m["prevalence"]
        m["pr_auc_lift_over_prevalence"] = m["pr_auc"] / m["prevalence"]
        m["brier_prevalence_only"] = m["prevalence"] * (1 - m["prevalence"])
    nyc = card["nyc_test_metrics"]
    nyc["pr_auc_minus_prevalence"] = nyc["pr_auc"] - nyc["prevalence"]
    nyc["pr_auc_lift_over_prevalence"] = nyc["pr_auc"] / nyc["prevalence"]
    nyc["brier_prevalence_only"] = nyc["prevalence"] * (1 - nyc["prevalence"])
    (OUT / "metrics_all.json").write_text(json.dumps(res["all"], indent=2, default=float))
    (OUT / "metrics_history_ready.json").write_text(json.dumps(res["history_ready"], indent=2, default=float))
    (OUT / "metrics_all_groups.json").write_text(json.dumps(res, indent=2, default=float))

    metrics = ["n", "prevalence", "roc_auc", "pr_auc", "pr_auc_minus_prevalence", "pr_auc_lift_over_prevalence",
               "accuracy", "balanced_accuracy", "precision", "recall", "f1", "brier", "brier_prevalence_only",
               "mean_predicted_prob", "tn", "fp", "fn", "tp"]
    comp = pd.DataFrame({"NYC test": {m: nyc[m] for m in metrics},
                         "Synthetic all": {m: res["all"][m] for m in metrics},
                         "Synthetic history-ready": {m: res["history_ready"][m] for m in metrics}})
    comp["Δ all vs NYC"] = comp["Synthetic all"] - comp["NYC test"]
    comp["Δ history-ready vs NYC"] = comp["Synthetic history-ready"] - comp["NYC test"]
    comp.to_csv(OUT / "nyc_vs_synthetic_comparison.csv")

    # plots for history-ready + all
    hr = groups["history_ready"]
    plot_confusion(res["history_ready"], OUT / "synthetic_history_ready_confusion_matrix.png", "Synthetic, history-ready")
    plot_curves({"NYC test": (pd.read_csv(ROOT / "outputs/nyc_test_predictions.csv").y_true,
                              pd.read_csv(ROOT / "outputs/nyc_test_predictions.csv").p_slow_repair),
                 "Synthetic all": (k.y, k.p), "Synthetic history-ready": (hr.y, hr.p)},
                OUT / "roc_nyc_vs_synthetic_all_and_history_ready.png", OUT / "pr_nyc_vs_synthetic_all_and_history_ready.png")

    # ---- calibration (diagnostic only; no recalibration applied) ----
    cal = {}
    for n in ["all", "history_ready"]:
        g_ = groups[n].copy()
        g_["bin"] = pd.qcut(g_.p.rank(method="first"), 10, labels=False)
        t = g_.groupby("bin").agg(n=("y", "size"), mean_pred=("p", "mean"), observed=("y", "mean"), p_min=("p", "min"), p_max=("p", "max"))
        t.to_csv(OUT / f"calibration_deciles_{n}.csv")
        cal[n] = {"mean_pred": float(g_.p.mean()), "observed": float(g_.y.mean()),
                  "ece_decile": float((t.n * (t.mean_pred - t.observed).abs()).sum() / t.n.sum()),
                  "share_predicted_positive": float((g_.p >= thr).mean()),
                  "observed_rate_in_pred_pos": float(g_.y[g_.p >= thr].mean()),
                  "observed_rate_in_pred_neg": float(g_.y[g_.p < thr].mean())}
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4.8, 4.4))
    for n in ["all", "history_ready"]:
        t = pd.read_csv(OUT / f"calibration_deciles_{n}.csv")
        ax.plot(t.mean_pred, t.observed, "o-", label=f"synthetic {n}")
    nyc_p = pd.read_csv(ROOT / "outputs/nyc_test_predictions.csv")
    nyc_p["bin"] = pd.qcut(nyc_p.p_slow_repair.rank(method="first"), 10, labels=False)
    tn = nyc_p.groupby("bin").agg(mp=("p_slow_repair", "mean"), ob=("y_true", "mean"))
    ax.plot(tn.mp, tn.ob, "s--", label="NYC test", color="gray")
    ax.plot([0, 1], [0, 1], "k:", lw=1); ax.set_xlabel("mean predicted probability"); ax.set_ylabel("observed slow-repair rate")
    ax.set_title("Calibration by decile (not recalibrated)"); ax.legend(fontsize=8); fig.tight_layout()
    fig.savefig(OUT / "calibration_curve.png", dpi=140); plt.close(fig)

    # ---- behaviour: borough-level analysis ----
    brow = []
    for b, g_ in hr.groupby("borough"):
        brow.append({"borough": b, "n": len(g_), "prevalence": g_.y.mean(), "mean_pred": g_.p.mean(),
                     "within_borough_roc_auc": auc(g_.y, g_.p)})
    bt = pd.DataFrame(brow)
    bt.to_csv(OUT / "by_borough_history_ready.csv", index=False)
    wb = float(np.average(bt.within_borough_roc_auc, weights=bt.n))
    # borough x month regime tracking
    hr = hr.assign(ym=hr.created_date.dt.to_period("M").astype(str))
    bm = hr.groupby(["borough", "ym"]).agg(n=("y", "size"), observed=("y", "mean"), mean_pred=("p", "mean")).reset_index()
    bm = bm[bm.n >= 50]
    bm.to_csv(OUT / "borough_month_tracking.csv", index=False)
    track = {"n_borough_months": int(len(bm)),
             "pearson_pred_vs_observed": float(bm.observed.corr(bm.mean_pred)),
             "spearman_pred_vs_observed": float(bm.observed.corr(bm.mean_pred, method="spearman"))}
    # within a borough-month (removes regime), does the model rank individual complaints?
    hr["bm"] = hr.borough + hr.ym
    ok = hr.groupby("bm").y.transform(lambda s: s.nunique() == 2) & (hr.groupby("bm").y.transform("size") >= 50)
    sub = hr[ok]
    track["within_borough_month_roc_auc_weighted"] = float(np.average(
        [auc(g_.y, g_.p) for _, g_ in sub.groupby("bm")], weights=[len(g_) for _, g_ in sub.groupby("bm")]))
    track["within_borough_roc_auc_weighted"] = wb
    # single raw-feature scores (no fitting): how much of the ranking is the NYC 'regime' feature?
    singles = {}
    for f in ["boro_share_gt168_14d", "boro_median_dur_14d", "city_median_dur_14d", "boro_backlog_60d", "boro_created_7d",
              "loc_prior_90d", "loc_days_since_prev"]:
        x = hr[[f, "y"]].dropna()
        singles[f] = {"roc_auc_of_raw_feature": auc(x.y, x[f]), "pearson_with_model_score": float(hr[f].corr(hr.p))}
    track["single_feature_scores"] = singles
    # NYC counterpart for the same single-feature diagnostic
    ref = pd.read_pickle(ROOT / "data/processed/nyc_features.pkl")
    tst = ref[ref.label_known & (ref.created_date >= card["split"]["test"]["from"])]
    track["nyc_test_single_feature_scores"] = {
        f: auc(tst.dropna(subset=[f]).slow_repair, tst.dropna(subset=[f])[f]) for f in singles}
    track["nyc_test_within_borough_roc_auc"] = {
        b: auc(g_.slow_repair, model.predict_proba(g_[FEATURES])[:, 1]) for b, g_ in tst.groupby("borough")
        if g_.slow_repair.nunique() == 2 and len(g_) >= 100}

    # permutation importance of the frozen model on synthetic (diagnostic; compare with NYC file)
    pi = permutation_importance(model, hr[FEATURES], hr.y, scoring="roc_auc", n_repeats=3, random_state=42, n_jobs=1)
    imp = pd.DataFrame({"feature": FEATURES, "auc_drop_synthetic": pi.importances_mean})
    nyc_imp = pd.read_csv(ROOT / "outputs/feature_importance.csv").rename(columns={"auc_drop": "auc_drop_nyc_val"})
    imp = imp.merge(nyc_imp[["feature", "auc_drop_nyc_val"]], on="feature").sort_values("auc_drop_synthetic", ascending=False)
    imp.to_csv(OUT / "feature_importance_synthetic_vs_nyc.csv", index=False)

    # ---- domain shift vs NYC train, history-ready and all ----
    tr = card["split"]["train"]
    ref_tr = ref[ref.label_known & (ref.created_date >= tr["from"]) & (ref.created_date <= tr["to"])]
    for n in ["all", "history_ready"]:
        sh = domain_shift_table(ref_tr, groups[n], NUM_FEATURES, CAT_FEATURES)
        sh["flag"] = np.where(sh.psi > 0.25, "major", np.where(sh.psi > 0.1, "moderate", "ok"))
        sh.to_csv(OUT / f"domain_shift_psi{'' if n == 'all' else '_history_ready'}.csv", index=False)
    # NYC val/test vs NYC train, to show how much shift the model already faced within NYC
    for n, sset in [("nyc_val", ref[ref.label_known & (ref.created_date >= card["split"]["val"]["from"]) & (ref.created_date <= card["split"]["val"]["to"])]),
                    ("nyc_test", tst)]:
        sh = domain_shift_table(ref_tr, sset, NUM_FEATURES, CAT_FEATURES)
        sh.to_csv(OUT / f"domain_shift_psi_{n}_vs_train.csv", index=False)
    # score distribution
    score = {"nyc_train_mean_p": float(model.predict_proba(ref_tr[FEATURES])[:, 1].mean()),
             "nyc_train_prevalence": float(ref_tr.slow_repair.mean()),
             "nyc_test_mean_p": nyc["mean_predicted_prob"], "nyc_test_prevalence": nyc["prevalence"],
             "syn_all_mean_p": cal["all"]["mean_pred"], "syn_all_prevalence": res["all"]["prevalence"]}

    # informational only: threshold that would maximise Youden J on synthetic (NOT used, NOT saved to model)
    from evaluation.metrics import youden_threshold
    info = {"synthetic_youden_threshold_info_only": youden_threshold(hr.y, hr.p),
            "frozen_threshold": thr,
            "metrics_at_that_threshold_info_only": {m: evaluate(hr.y, hr.p, youden_threshold(hr.y, hr.p))[m]
                                                    for m in ["balanced_accuracy", "precision", "recall", "f1"]}}
    extra = {"calibration": cal, "tracking": track, "score_shift": score, "info_only": info,
             "by_borough_history_ready": bt.round(4).to_dict("records")}
    (OUT / "behaviour_and_calibration.json").write_text(json.dumps(extra, indent=2, default=float))
    pd.set_option("display.width", 200)
    print(comp.round(4).to_string())
    print(json.dumps(extra, indent=1, default=float)[:6000])
    print(imp.head(10).round(4).to_string())
    print(pd.read_csv(OUT / "domain_shift_psi_history_ready.csv").round(3).to_string())


if __name__ == "__main__":
    main()
