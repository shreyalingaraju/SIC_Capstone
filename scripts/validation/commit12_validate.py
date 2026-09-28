"""
Commit 12 validation for Stage 7 (H10, H11, H13, H16_post, outputs,
determinism, failure diagnostics, legacy backup).

Run from the repository root with the project venv:
    python scripts/validation/commit12_validate.py

Runtime about 6-8 minutes (three in-process pipelines, three command-line
runs). Temporary files go to a fresh tempfile directory; nothing in
data/processed is written. Expected last line: "FAILS: none".
"""

import contextlib
import dataclasses
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, "src/features")
import match_controls as m  # noqa: E402

FAILS = []
SCRATCH = Path(tempfile.mkdtemp(prefix="c12_"))


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{' - ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)


def pipeline(params):
    with contextlib.redirect_stdout(io.StringIO()):
        c, rs = m.load_raw_complaints(params)
        t, n_s3 = m.load_treatments(params, c)
        t = m._finalise_treatments(t)
        cr, cov, _ = m.load_crime(params, rs)
        c, cov, _ = m.build_darkness_intervals(params, c, cov)
        s, c, _ = m.build_sites(params, c)
        e, c, s, _, es = m.build_episodes(params, c, s)
        idx, _ = m.build_spatial_indexes(params, c, s, cr)
        t = m.compute_windows(params, t)
        t, el, rej, att = m.apply_treatment_rules(params, t, c, s, idx, cov)
        el, _ = m.compute_treatment_baselines(params, el, idx, cr.drop(columns=["latitude", "longitude"]))
        el, sc = m.standardise(params, el)
        reg = m.init_reuse_registry(params, el)
        idx, _ = m.build_band_candidates(params, el, s, idx)
        match, ms = m.match_treatments(params, el, s, idx, sc, reg)
        pairs = m.assemble_pairs(params, match, el, s, reg)
    return dict(c=c, s=s, e=e, t=t, el=el, match=match, ms=ms, pairs=pairs, att=att, n_s3=n_s3)


def recheck(params, pairs):
    with contextlib.redirect_stdout(io.StringIO()):
        return m.independent_recheck(params, pairs)


base = m.parse_args([])
P = pipeline(base)
pairs, t, c = P["pairs"], P["t"], P["c"]

# ---- H10/H11 on the canonical pairs.
h10, h11, summary = recheck(base, pairs)
check("H10 passes (canonical sample)", h10.passed, f"sample {summary['sample_n']}, flags {summary['h10']}")
check("H11 passes (canonical sample)", h11.passed, str(summary["h11"]))
check("canonical own-episode exemption exercised", summary["n_s4_pre_own_episode_exempted"] > 0,
      f"{summary['n_s4_pre_own_episode_exempted']} sampled treatments")
check("recheck artifact threshold = production", summary["universe"]["artifact_threshold"] == 44
      and summary["universe"]["n_artifact_sites"] == int(P["s"].is_artifact.sum()),
      str(summary["universe"]))

# ---- R2: rebuilt own episodes equal production episodes (all eligible treatments with >1 member).
with contextlib.redirect_stdout(io.StringIO()):
    table, _, _, _, _ = m._recheck_complaints(base)
import geopandas as gpd  # noqa: E402
sindex = gpd.GeoSeries(gpd.points_from_xy(table.x, table.y)).sindex
key_row = pd.Index(table.unique_key.to_numpy())
ep = c.episode_id.to_numpy()
multi = pd.Series(ep).value_counts()
rng = np.random.default_rng(1)
el_keys = P["el"].treatment_key.to_numpy()
el_ep = ep[pd.Index(c.unique_key.to_numpy()).get_indexer(el_keys)]
cand = el_keys[np.isin(el_ep, multi[multi > 1].index)]
sample = rng.choice(cand, min(300, len(cand)), replace=False)
bad = 0
for key in sample:
    members = m._recheck_own_episode(key_row.get_loc(key), table, sindex, base.episode_merge_radius_m)
    rebuilt = set(table.unique_key.to_numpy()[sorted(members)].tolist())
    prod_ep = ep[c.unique_key.to_numpy() == key][0]
    production = set(c.unique_key.to_numpy()[ep == prod_ep].tolist())
    bad += rebuilt != production
check("R2 rebuilt own episode == production episode (300 multi-complaint)", bad == 0, f"{bad} mismatches")

# ---- H10/H11 negative tests on a 40-row frame, one mutation each.
small = pairs.head(40).reset_index(drop=True)
p_all = dataclasses.replace(base, recheck_sample_n=10_000)


def negative(name, frame, params, expect, check_id="H10"):
    r10, r11, sm = recheck(params, frame)
    got = {k: v for k, v in sm["h10" if check_id == "H10" else "h11"].items() if v}
    ok = all(got.get(k, 0) == v for k, v in expect.items())
    other_ok = (r11.passed if check_id == "H10" else r10.passed)
    check(f"negative {name}", ok, f"{check_id} flags {got}; other check passed: {other_ok}")


f = small.copy(); f.loc[0:4, "treatment_x_m"] += 1e-6
negative("H10 treatment coordinates", f, p_all, {"treatment_coordinates_differ": 5})
f = small.copy(); f.loc[5:7, "window_end"] += pd.Timedelta(seconds=1)
negative("H10 window_end", f, p_all, {"window_end_differs": 3})
f = small.copy()
for i in (8, 9):
    xy = f.loc[i, "treatment_site_id"][1:].split("_N")
    f.loc[i, ["control_site_id", "control_x_m", "control_y_m"]] = [f.loc[i, "treatment_site_id"], float(xy[0]), float(xy[1])]
negative("H10 S-3 control at the dark treatment site", f, p_all, {"s3_dark_control": 2})
f = small.copy(); f.loc[10:11, "control_x_m"] += 0.5
negative("H10 control not a rebuilt site", f, p_all, {"control_site_not_rebuilt": 2, "control_site_id_differs": 2})
f = small.copy(); f.loc[12, "control_site_id"] = "E0_N0"
negative("H10 control_site_id text", f, p_all, {"control_site_id_differs": 1})

cols = ["treatment_key", "x_m", "y_m", "created_date", "closed_date", "window_start", "window_end",
        "baseline_start", "baseline_end"]
for code, flag_name in (("S4_PRE_DIRTY", "s4_pre_dirty"), ("S4_POST_DIRTY", "s4_post_dirty")):
    rej = t.loc[t.reason_code == code, cols].head(5).reset_index(drop=True)
    f = small.head(5).copy()
    f[["treatment_key", "treatment_x_m", "treatment_y_m", "created_date", "closed_date", "window_start",
       "window_end", "baseline_start", "baseline_end"]] = rej.to_numpy()
    f = f.astype({k: pairs[k].dtype for k in f.columns})
    negative(f"H10 {code} treatment injected", f, p_all, {flag_name: 5})

r10, _, sm = recheck(dataclasses.replace(p_all, artifact_quantile=0.5), small)
check("negative H10 artifact flags (quantile 0.5)", not r10.passed and sm["h10"].get("treatment_site_is_artifact", 0) > 0
      and sm["h10"].get("control_site_is_artifact", 0) > 0, str({k: v for k, v in sm["h10"].items() if v}))

f = small.copy(); f.loc[13:14, "control_base_100m"] += 1; f.loc[15, "treatment_base_250m"] -= 1
negative("H11 base counts", f, p_all, {"control_base_100m": 2, "treatment_base_250m": 1}, check_id="H11")

# E2: universe violations from a corrupted copy of the raw CSV.
raw = pd.read_csv(m.RAW_COMPLAINTS_FILE, low_memory=False)
inside = raw.index[raw.latitude.between(40.6, 40.8) & raw.longitude.between(-74.0, -73.8)]
bad_raw = pd.concat([raw, raw.loc[[inside[0]]]], ignore_index=True)
bad_raw.loc[inside[1], "created_date"] = "not a date"
tmp_csv = SCRATCH / "raw_bad.csv"
bad_raw.to_csv(tmp_csv, index=False)
original = m.RAW_COMPLAINTS_FILE
m.RAW_COMPLAINTS_FILE = tmp_csv
r10, _, sm = recheck(p_all, small)
m.RAW_COMPLAINTS_FILE = original
check("negative E2 universe violations", not r10.passed and sm["universe_problems"]["duplicate_unique_key"] == 2
      and sm["universe_problems"]["unparseable_created_date"] == 1, str(sm["universe_problems"]))

# ---- H13 and H16_post.
with contextlib.redirect_stdout(io.StringIO()):
    unmatched = m.build_unmatched(t, P["el"], P["match"], pairs, P["s"])
attr = m.full_attrition(P["att"], P["ms"])
ok = m._check_h13(P["n_s3"], t, pairs, unmatched, attr)
check("H13 passes", ok.passed, str(ok.examples))
check("unmatched schema and reasons", list(unmatched.columns) == list(m.UNMATCHED_DTYPES)
      and set(unmatched.reason_step.unique()) == set(range(1, 13))
      and unmatched.loc[unmatched.reason_step <= 8, "treatment_base_100m"].isna().all()
      and unmatched.loc[unmatched.reason_step >= 9, "treatment_base_100m"].notna().all())
r = m._check_h13(P["n_s3"], t, pairs, unmatched.iloc[1:], attr)
check("negative H13 dropped unmatched row", not r.passed, str(r.examples[:3]))
bad_attr = [dict(x) for x in attr]; bad_attr[5]["dropped"] += 1
r = m._check_h13(P["n_s3"], t, pairs, unmatched, bad_attr)
check("negative H13 attrition", not r.passed, str(r.examples[:2]))
r = m._check_h13(P["n_s3"] + 1, t, pairs, unmatched, attr)
check("negative H13 Stage 3 total", not r.passed, str(r.examples[:1]))
r = m._check_h16_post(base, pairs, c)
check("H16_post not applicable (canonical)", r.passed and "not applicable" in r.examples[0])

placebo = dataclasses.replace(base, placebo_shift_days=90)
Q = pipeline(placebo)
r = m._check_h16_post(placebo, Q["pairs"], Q["c"])
check("H16_post passes (placebo)", r.passed, str(r.examples))
f = Q["pairs"].copy(); f.loc[0, "created_date"] += pd.Timedelta(days=1)
r = m._check_h16_post(placebo, f, Q["c"])
check("negative H16_post shifted date", not r.passed, str(r.examples[:1]))
c2 = Q["c"].copy(); c2.loc[0, "dark_start_s"] -= 1
r = m._check_h16_post(placebo, Q["pairs"], c2)
check("negative H16_post darkness interval", not r.passed, str(r.examples[:1]))
r10, r11, sm = recheck(placebo, Q["pairs"])
check("H10/H11 pass (placebo)", r10.passed and r11.passed and sm["n_s4_pre_own_episode_exempted"] == 0,
      f"{sm['h10']}, exempted {sm['n_s4_pre_own_episode_exempted']}")
pre_only = dataclasses.replace(base, control_selection="pre_only")
Q = pipeline(pre_only)
r10, r11, sm = recheck(pre_only, Q["pairs"])
check("H10/H11 pass (pre_only)", r10.passed and r11.passed, str(sm["h10"]))

# ---- Command-line runs: outputs, diagnostics, determinism, failure file, skip-recheck.
py = sys.executable


def run(*args):
    proc = subprocess.run([py, "src/features/match_controls.py", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


a, b = SCRATCH / "a", SCRATCH / "b"
code_a, out_a = run("--out-dir", str(a), "--check-determinism")
code_b, out_b = run("--out-dir", str(b))
check("CLI canonical run exit 0 (twice, separate processes)", code_a == 0 and code_b == 0, f"{code_a}, {code_b}")
da = json.loads((a / m.DIAGNOSTICS_FILENAME).read_text(encoding="utf-8"),
                parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
db = json.loads((b / m.DIAGNOSTICS_FILENAME).read_text(encoding="utf-8"))
check("diagnostics strict JSON with all sections", set(m.DIAGNOSTICS_SECTIONS) <= set(da) and da["status"] == "completed",
      f"missing {set(m.DIAGNOSTICS_SECTIONS) - set(da)}; non_finite {da['non_finite']}")
check("determinism second pass identical", da["determinism"]["second_pass"]["identical"])
check("pair-list hash equal across processes", da["determinism"]["pair_list_sha256"] == db["determinism"]["pair_list_sha256"],
      da["determinism"]["pair_list_sha256"][:16])
same = {name: sha(a / name) == sha(b / name) for name in (m.PAIRS_FILENAME, m.SITES_FILENAME, m.UNMATCHED_FILENAME)}
check("parquet files byte-identical across processes", all(same.values()), str(same))
schema_ok = all(not m._arrow_schema_problems(a / name, dt) for name, dt in (
    (m.PAIRS_FILENAME, m.PAIRS_DTYPES), (m.SITES_FILENAME, m.SITES_DTYPES), (m.UNMATCHED_FILENAME, m.UNMATCHED_DTYPES)))
check("pyarrow schemas match", schema_ok)
checks = da["hard_checks"]
check("hard checks all pass in diagnostics",
      all(v["status"] == "pass" for v in checks.values()) and {"H0", "H1", "H10", "H11", "H13", "H16", "H16_post"} <= set(checks),
      str({k: v["status"] for k, v in checks.items()}))
sites_out = pd.read_parquet(a / m.SITES_FILENAME)
pairs_out = pd.read_parquet(a / m.PAIRS_FILENAME)
check("site role counts (U7)", int(sites_out.n_times_control.sum()) == len(pairs_out)
      and int(sites_out.n_times_treatment.sum()) == len(pairs_out))
check("no temporary or failure files left", not list(a.glob("*.tmp")) and not (a / m.FAILED_DIAGNOSTICS_FILENAME).exists())

f_dir = SCRATCH / "fail"
code, _ = run("--out-dir", str(f_dir), "--caliper-sd", "nan")
fd = json.loads((f_dir / m.FAILED_DIAGNOSTICS_FILENAME).read_text(encoding="utf-8"))
check("H0 failure writes failed diagnostics (exit 1)", code == 1 and fd["status"] == "failed"
      and fd["hard_checks"]["H0"]["status"] == "fail", fd.get("error", "")[:80])
code, _ = run("--out-dir", str(f_dir), "--skip-recheck")
ds = json.loads((f_dir / m.DIAGNOSTICS_FILENAME).read_text(encoding="utf-8"))
check("success removes stale failure file; --skip-recheck recorded as skipped",
      code == 0 and not (f_dir / m.FAILED_DIAGNOSTICS_FILENAME).exists()
      and ds["hard_checks"]["H10"]["status"] == "skipped" and ds["recheck"]["skipped"],
      str({k: ds["hard_checks"][k]["status"] for k in ("H10", "H11")}))

# ---- Legacy backup (X3).
lg = SCRATCH / "legacy"; lg.mkdir()
shutil.copy2("data/processed/control_area_pairs.parquet", lg / m.PAIRS_FILENAME)
first = m.preserve_legacy_pairs(lg)
second = m.preserve_legacy_pairs(lg)
check("legacy backup created with equal sha256", first["action"] == "backed_up"
      and first["sha256"] == sha("data/processed/control_area_pairs.parquet"))
check("existing backup never overwritten", second["action"] == "existing_backup_kept" and second["sha256"] == first["sha256"])
nb = SCRATCH / "newschema"; nb.mkdir()
shutil.copy2(a / m.PAIRS_FILENAME, nb / m.PAIRS_FILENAME)
check("new-schema file not backed up", m.preserve_legacy_pairs(nb)["action"] == "existing_file_is_new_schema_not_backed_up")

print(f"\nscratch: {SCRATCH}")
print("FAILS:", FAILS or "none")
