"""P5: calibrate buyConviction on OUT-OF-SAMPLE trades (and IS for comparison).

    python -I research/scripts/p5_calibrate.py [--model engine/model.json] [--bt data/bt]
        [--features data/features/daily.parquet] [--tag ""] [--log]

Every OOS trade row (data/bt/trades, all delta x DTE cells, h=3, 2024-01 .. 2026-11, SENSEX OOS = 2025+, primary
exclusions, NSE-only stocks) is mapped to the engine components from bhavcopy-derived inputs. The MODELLED p75
half-spread stands in for the real bid/ask (the engine uses real quotes live). Rows with friction > 10% are not
scoreable and are counted, not bucketed. Buckets: <40, 40-50, 50-60, 60-70, 70-80, 80+.
Per bucket x class group (index, stocks, NIFTY): n, n_days, win rate, mean net R with day-clustered bootstrap CI,
gross R, expectancy Rs/lot. monotone = bucket mean net R non-decreasing over buckets with n >= 100;
top_bucket_positive = CI lower bound of the highest populated bucket > 0. --log appends ENGINE rows (registered=no).
Writes research/out/p5_calibration{tag}.{json,md}.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from research.lib import backtest as bt
from research.lib import p5_engine_vec as ev
from research.lib import trials

EDGES = [-1e9, 40, 50, 60, 70, 80, 1e9]
LABELS = ["<40", "40-50", "50-60", "60-70", "70-80", "80+"]
TIERS = [("avoid", -1e9, 40), ("neutral", 40, 60), ("least-bad", 60, 1e9)]  # engine.buy_conviction.tier_of


def tier_table(df, score, g, sample):
    """Three tiers (post-hoc grouping): trade-level means with day-clustered CI (not averages of bucket means)."""
    out = []
    sets = {"index": g == "index", "stocks": g == "stocks", "NIFTY": df["symbol"].to_numpy() == "NIFTY"}
    y = df["net_R_h3"].to_numpy(float)
    rs = df["net_rs_h3"].to_numpy(float)
    d = df["date"].to_numpy()
    for cls, sel in sets.items():
        prev = None
        for name, lo, hi in TIERS:
            m = sel & ~np.isnan(score) & (score >= lo) & (score < hi)
            if not m.any():
                continue
            ci = bt.cluster_bootstrap_ci(y[m], d[m], n_boot=1000)
            out.append({"sample": sample, "cls": cls, "tier": name, "n": int(m.sum()),
                        "n_days": int(pd.Series(d[m]).nunique()), "net_R": float(y[m].mean()), "ci_lo": ci["lo"],
                        "ci_hi": ci["hi"], "win_rate": float((rs[m] > 0).mean())})
    for cls in sets:
        v = [r["net_R"] for r in out if r["cls"] == cls]
        for r in out:
            if r["cls"] == cls:
                r["tiers_monotone"] = bool(len(v) == 3 and v[0] <= v[1] <= v[2])
    return out


def score_rows(df, model):
    C = ev.components_from_rows(df)
    g = np.where(df["cls"].to_numpy() == "stock", "stocks", "index")
    pred = np.full(len(df), np.nan)
    for grp, gm in model["groups"].items():
        m = g == grp
        X = {c: C.loc[m, c].to_numpy() for c in gm["components"]}
        pred[m], _ = ev.predict(gm, X)
    ok = C["friction"].to_numpy() <= model["gates"]["friction_max_pct"]
    pred[~ok] = np.nan
    return pred, ev.to_score(pred, model["mapping"]), g, ok


def bucket_table(df, score, g, sample):
    rows = []
    sets = {"index": g == "index", "stocks": g == "stocks", "NIFTY": df["symbol"].to_numpy() == "NIFTY"}
    b = pd.cut(score, EDGES, labels=LABELS, right=False)
    summary = {}
    for cls, sel in sets.items():
        sel = sel & ~np.isnan(score)
        means, cnts = [], []
        for lab in LABELS:
            m = sel & (np.asarray(b) == lab)
            n = int(m.sum())
            if n == 0:
                rows.append({"sample": sample, "cls": cls, "bucket": lab, "n": 0})
                continue
            y = df["net_R_h3"].to_numpy(float)[m]
            d = df["date"].to_numpy()[m]
            ci = bt.cluster_bootstrap_ci(y, d, n_boot=1000)
            r = {"sample": sample, "cls": cls, "bucket": lab, "n": n, "n_days": int(pd.Series(d).nunique()),
                 "win_rate": float((df["net_rs_h3"].to_numpy(float)[m] > 0).mean()), "net_R": float(y.mean()),
                 "ci_lo": ci["lo"], "ci_hi": ci["hi"], "gross_R": float(df["gross_R_h3"].to_numpy(float)[m].mean()),
                 "exp_inr": float(df["net_rs_h3"].to_numpy(float)[m].mean())}
            rows.append(r)
            if n >= 100:
                means.append(r["net_R"])
                cnts.append(r)
        mono = bool(len(means) >= 2 and all(b2 >= b1 for b1, b2 in zip(means, means[1:])))
        top = cnts[-1] if cnts else None
        y = df["net_R_h3"].to_numpy(float)[sel]
        rho = float(spearmanr(score[sel], y).correlation) if sel.sum() > 10 else np.nan
        summary[cls] = {"monotone": mono, "top_bucket": top["bucket"] if top else None,
                        "top_bucket_positive": bool(top is not None and top["ci_lo"] > 0),
                        "any_bucket_positive": bool(any(r["ci_lo"] > 0 for r in cnts)),
                        "any_bucket_mean_positive": bool(any(r["net_R"] > 0 for r in cnts)),
                        "spearman": rho, "n_scored": int(sel.sum())}
    return rows, summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.path.join(ROOT, "engine", "model.json"))
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--features", default=os.path.join(ROOT, "data", "features", "daily.parquet"))
    ap.add_argument("--out", default=os.path.join(ROOT, "research", "out"))
    ap.add_argument("--tag", default="")
    ap.add_argument("--log", action="store_true")
    a = ap.parse_args()
    with open(a.model, encoding="utf-8") as f:
        model = json.load(f)
    res = {"model": os.path.relpath(a.model, ROOT), "variant": model.get("variant"),
           "note": "OOS inputs are bhavcopy-derived; the modelled p75 half-spread stands in for the real bid/ask",
           "buckets": [], "IS_buckets": [], "monotone": {}, "top_bucket_positive": {}, "spearman": {},
           "IS": {}, "OOS": {}}
    for sample in ("IS", "OOS"):
        df = ev.load_rows(a.bt, a.features, sample)
        pred, score, g, ok = score_rows(df, model)
        rows, summ = bucket_table(df, score, g, sample)
        res.setdefault("tiers", [])
        res["tiers"] += tier_table(df, score, g, sample)
        res[sample] = {"n_rows": int(len(df)), "n_not_scoreable_friction": int((~ok).sum()), "summary": summ,
                       "date_range": [str(df["date"].min().date()), str(df["date"].max().date())]}
        if sample == "OOS":
            res["buckets"] = rows
            res["monotone"] = {k: v["monotone"] for k, v in summ.items()}
            res["top_bucket_positive"] = {k: v["top_bucket_positive"] for k, v in summ.items()}
            res["spearman"] = {k: v["spearman"] for k, v in summ.items()}
        else:
            res["IS_buckets"] = rows
        print(f"[{sample}] rows {len(df):,}, not scoreable (friction) {(~ok).sum():,}; " +
              "; ".join(f"{k}: mono={v['monotone']} rho={v['spearman']:+.3f} top+={v['top_bucket_positive']}"
                        for k, v in summ.items()))
        if a.log:
            for cls, v in summ.items():
                top = next((r for r in rows if r["cls"] == cls and r["bucket"] == v["top_bucket"]), None)
                if top:
                    trials.log_trial(id="ENGINE", variant=f"{model.get('variant')}_top{top['bucket']}", registered="no",
                                     sample=sample, cls=cls, style="swing", n_trades=top["n"], n_days=top["n_days"],
                                     gross_mean_R=top["gross_R"], net_mean_R=top["net_R"], ci_lo=top["ci_lo"],
                                     ci_hi=top["ci_hi"], verdict="monotone" if v["monotone"] else "not monotone",
                                     notes=f"buyConviction calibration; spearman {v['spearman']:+.3f}; top bucket",
                                     script="research/scripts/p5_calibrate.py")
    jp = os.path.join(a.out, f"p5_calibration{a.tag}.json")
    if os.path.exists(jp):
        with open(jp, encoding="utf-8") as f:
            old = json.load(f)
        for k in ("refits", "verdict"):
            if k in old:
                res[k] = old[k]
    res["tiers_monotone_OOS"] = {c: next((r["tiers_monotone"] for r in res["tiers"] if r["cls"] == c and r["sample"] == "OOS"),
                                         None) for c in ("index", "stocks", "NIFTY")}
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    L = [f"# P5 buyConviction calibration ({model.get('variant')})",
         "Score = engine/model.json applied to every OOS trade row (h=3, all delta x DTE cells). Inputs are bhavcopy-"
         "derived; **the modelled p75 half-spread stands in for the real bid/ask**. Net R CI = day-clustered bootstrap. "
         f"Friction > 10% rows are not scoreable (OOS {res['OOS']['n_not_scoreable_friction']:,} of {res['OOS']['n_rows']:,}).",
         "", "| sample | cls | bucket | n | days | win | net R [CI] | gross R | exp Rs/lot |", "|---|---|---|---|---|---|---|---|---|"]
    for r in res["buckets"] + res["IS_buckets"]:
        if r["n"] == 0 or (r["sample"] == "IS" and r["cls"] == "NIFTY"):
            continue
        L.append(f"| {r['sample']} | {r['cls']} | {r['bucket']} | {r['n']:,} | {r['n_days']} | {r['win_rate']:.0%} | "
                 f"{r['net_R']:+.3f} [{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}] | {r['gross_R']:+.3f} | {r['exp_inr']:+,.0f} |")
    L += ["", "| OOS tier (post-hoc) | cls | n | days | win | net R [CI] |", "|---|---|---|---|---|---|"]
    for r in res["tiers"]:
        if r["sample"] == "OOS":
            L.append(f"| {r['tier']} | {r['cls']} | {r['n']:,} | {r['n_days']} | {r['win_rate']:.0%} | "
                     f"{r['net_R']:+.3f} [{r['ci_lo']:+.3f}, {r['ci_hi']:+.3f}] |")
    L.append(f"- OOS tiers monotone: {res['tiers_monotone_OOS']}")
    for smp in ("OOS",):
        for k, v in res[smp]["summary"].items():
            L.append(f"- {smp} {k}: monotone {v['monotone']}, Spearman {v['spearman']:+.3f}, top bucket {v['top_bucket']} "
                     f"CI>0 {v['top_bucket_positive']}, any bucket CI>0 {v['any_bucket_positive']}.")
    if "verdict" in res:
        L.append("**Verdict (blunt).** " + res["verdict"]["text"])
    if "refits" in res:
        L.append("Refits on IS only (ENGINE, registered=no): " + "; ".join(
            f"{k}: OOS Spearman idx {v['spearman']['index']:+.3f} / stk {v['spearman']['stocks']:+.3f}"
            for k, v in res["refits"].items()) + ". Shipped: v1_full.")
    with open(os.path.join(a.out, f"p5_calibration{a.tag}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"md lines {len(L)}")


if __name__ == "__main__":
    main()
