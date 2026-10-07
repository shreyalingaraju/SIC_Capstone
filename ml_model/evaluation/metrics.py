"""Shared evaluation helpers (used for NYC test and for the synthetic external test)."""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
                             brier_score_loss, confusion_matrix, f1_score, log_loss,
                             precision_score, recall_score, roc_auc_score, roc_curve,
                             precision_recall_curve)


def evaluate(y, p, threshold):
    y = np.asarray(y).astype(int)
    pred = (np.asarray(p) >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    single = len(np.unique(y)) < 2
    return {
        "n": int(len(y)), "prevalence": float(y.mean()), "threshold": float(threshold),
        "accuracy": accuracy_score(y, pred), "balanced_accuracy": balanced_accuracy_score(y, pred),
        "precision": precision_score(y, pred, zero_division=0),
        "recall": recall_score(y, pred, zero_division=0),
        "f1": f1_score(y, pred, zero_division=0),
        "roc_auc": np.nan if single else roc_auc_score(y, p),
        "pr_auc": np.nan if single else average_precision_score(y, p),
        "brier": brier_score_loss(y, p),
        "log_loss": log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1]),
        "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
        "mean_predicted_prob": float(np.mean(p)),
    }


def youden_threshold(y, p):
    """Threshold maximising TPR - FPR (balanced accuracy); invariant to class prevalence."""
    fpr, tpr, thr = roc_curve(y, p)
    return float(thr[np.argmax(tpr - fpr)])


def plot_confusion(m, path, title):
    cm = np.array([[m["tn"], m["fp"]], [m["fn"], m["tp"]]])
    fig, ax = plt.subplots(figsize=(4.2, 3.8))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, f"{v:,}", ha="center", va="center", color="white" if v > cm.max() / 2 else "black")
    ax.set_xticks([0, 1], ["fast", "slow"]); ax.set_yticks([0, 1], ["fast", "slow"])
    ax.set_xlabel("predicted"); ax.set_ylabel("actual"); ax.set_title(title)
    fig.tight_layout(); fig.savefig(path, dpi=140); plt.close(fig)


def plot_curves(sets, roc_path, pr_path):
    """sets: {label: (y, p)}"""
    fig, ax = plt.subplots(figsize=(5, 4.2))
    for name, (y, p) in sets.items():
        fpr, tpr, _ = roc_curve(y, p)
        ax.plot(fpr, tpr, label=f"{name} (AUC {roc_auc_score(y, p):.3f})")
    ax.plot([0, 1], [0, 1], "k:", lw=1); ax.set_xlabel("FPR"); ax.set_ylabel("TPR")
    ax.set_title("ROC"); ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(roc_path, dpi=140); plt.close(fig)
    fig, ax = plt.subplots(figsize=(5, 4.2))
    for name, (y, p) in sets.items():
        pr, rc, _ = precision_recall_curve(y, p)
        ax.plot(rc, pr, label=f"{name} (AP {average_precision_score(y, p):.3f}, prev {np.mean(y):.2f})")
    ax.set_xlabel("recall"); ax.set_ylabel("precision"); ax.set_title("Precision-Recall")
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(pr_path, dpi=140); plt.close(fig)


def psi(ref, new, bins=10):
    """Population stability index for a numeric feature (quantile bins from ref)."""
    ref, new = pd.Series(ref).dropna(), pd.Series(new).dropna()
    if len(ref) < 20 or len(new) < 20:
        return np.nan
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return np.nan
    edges[0], edges[-1] = -np.inf, np.inf
    a = np.histogram(ref, edges)[0] / len(ref)
    b = np.histogram(new, edges)[0] / len(new)
    a, b = np.clip(a, 1e-4, None), np.clip(b, 1e-4, None)
    return float(np.sum((b - a) * np.log(b / a)))


def cat_psi(ref, new):
    a = pd.Series(ref).fillna("missing").astype(str).value_counts(normalize=True)
    b = pd.Series(new).fillna("missing").astype(str).value_counts(normalize=True)
    idx = a.index.union(b.index)
    a, b = a.reindex(idx, fill_value=0).clip(lower=1e-4), b.reindex(idx, fill_value=0).clip(lower=1e-4)
    return float(np.sum((b - a) * np.log(b / a)))


def domain_shift_table(ref_df, new_df, num_features, cat_features):
    rows = []
    for c in num_features:
        rows.append({"feature": c, "type": "numeric", "psi": psi(ref_df[c], new_df[c]),
                     "ref_missing": ref_df[c].isna().mean(), "new_missing": new_df[c].isna().mean(),
                     "ref_mean": ref_df[c].mean(), "new_mean": new_df[c].mean()})
    for c in cat_features:
        rows.append({"feature": c, "type": "categorical", "psi": cat_psi(ref_df[c], new_df[c]),
                     "ref_missing": ref_df[c].isna().mean(), "new_missing": new_df[c].isna().mean(),
                     "ref_mean": np.nan, "new_mean": np.nan})
    return pd.DataFrame(rows).sort_values("psi", ascending=False)
