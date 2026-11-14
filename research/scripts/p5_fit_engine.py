"""P5: fit the buyConviction additive expected-net-R model on IN-SAMPLE trades only -> engine/model.json.

    python -I research/scripts/p5_fit_engine.py [--bt data/bt] [--features data/features/daily.parquet]
        [--out engine/model.json] [--variant v1] [--components ...] [--coarse]

Data: data/bt/trades IS rows (all delta x DTE cells, h=3), NSE-only stocks, primary exclusions, joined to
data/features/daily.parquet. Outcome y = net_R_h3 (bhavcopy close +- modelled p75 half-spread, statutory costs).
Rows with friction > 10% are outside the engine's scoreable set and are not used.

Model per class group (index, stocks): y = intercept + sum_j f_j(x_j). Lead's rulings decide which j enter:
  index : friction (C5), delta (C1), dte (C2), rmr (C3)
  stocks: friction, delta, dte, rmr, ivpct (V2), vrp (V1), v3 (V3 veto)
Fit = backfitting with linear-interpolation (hat) bases on fixed knots (IS quantiles / fixed grid), each sweep:
  raw knot means of partial residuals -> centre -> empirical-Bayes shrink toward 0 with the day-clustered SE
  (factor tau^2 / (tau^2 + se_k^2), tau^2 = between-knot variance minus mean se^2) -> isotonic (PAV) where the
  hypothesis is monotone (friction, rmr, ivpct, vrp decreasing; v3 penalty <= 0) -> re-centre on the rows.
Component weight W_j = IS std of f_j (used only for pro-rata redistribution of missing components).
Mapping to 0-100: piecewise-linear through IS quantiles of predicted net R pooled with equal weight per class group
(score = IS percentile: 1st pct -> 1, median -> 50, 99th -> 99, clipped to [0, 100]).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.lib import p5_engine_vec as ev
from research.lib import rates

FRICTION_MAX = 10.0
DTE_LEVELS = ["7-10", "11-20", "21-35", "36+"]
DELTA_KNOTS = [0.15, 0.25, 0.40, 0.55, 0.70]
IVPCT_KNOTS = [5.0, 15.0, 30.0, 50.0, 70.0, 90.0]
QK = [0.02, 0.15, 0.35, 0.55, 0.75, 0.95]
SPEC = {
    "friction": {"type": "linear", "mono": "dec", "hyp": "C5 liquidity/friction floor (descriptive; dominant)"},
    "delta": {"type": "linear", "mono": None, "hyp": "C1 |delta| (lottery premium on far OTM)"},
    "dte": {"type": "cat", "mono": None, "hyp": "C2 DTE bucket"},
    "rmr": {"type": "linear", "mono": "dec", "hyp": "C3 required-move ratio (HAR sigma)"},
    "ivpct": {"type": "linear", "mono": "dec", "hyp": "V2 IV percentile 252d (less bad, not edge)"},
    "vrp": {"type": "linear", "mono": "dec", "hyp": "V1 IV / forecast RV (less bad, not edge)"},
    "v3": {"type": "binary", "mono": "veto", "hyp": "V3 inverted term structure (veto)"},
}
DEFAULT_COMPONENTS = {"index": ["friction", "delta", "dte", "rmr"],
                      "stocks": ["friction", "delta", "dte", "rmr", "ivpct", "vrp", "v3"]}


def pav(y, w, increasing=True):
    y = np.asarray(y, float).copy()
    w = np.asarray(w, float).copy()
    if not increasing:
        return -pav(-y, w, True)
    blocks = [[y[i], w[i], 1] for i in range(len(y))]
    out = []
    for b in blocks:
        out.append(b)
        while len(out) > 1 and out[-2][0] > out[-1][0]:
            v2, w2, n2 = out.pop()
            v1, w1, n1 = out.pop()
            ww = w1 + w2
            out.append([(v1 * w1 + v2 * w2) / ww if ww > 0 else (v1 + v2) / 2, ww, n1 + n2])
    res = []
    for v, _, n in out:
        res += [v] * n
    return np.array(res)


class Basis:
    """Row -> (two knot indices, two weights) design for one component; missing rows have zero weight."""

    def __init__(self, name, spec, x, knots=None, levels=None):
        self.name, self.spec = name, spec
        n = len(x)
        if spec["type"] == "linear":
            self.knots = list(knots)
            lo, hi, w = ev.interp_weights(np.asarray(x, float), self.knots)
            ok = lo >= 0
            self.i1, self.i2 = np.where(ok, lo, 0), hi
            self.w1, self.w2 = np.where(ok, 1 - w, 0.0), np.where(ok, w, 0.0)
            self.K = len(self.knots)
        else:
            self.levels = levels
            if spec["type"] == "cat":
                lv = {k: i for i, k in enumerate(levels)}
                idx = np.array([lv.get(v, -1) for v in x])
            else:
                v = np.asarray(x, float)
                idx = np.where(np.isnan(v), -1, (v > 0.5).astype(int))
            ok = idx >= 0
            self.i1 = self.i2 = np.where(ok, idx, 0)
            self.w1 = np.where(ok, 1.0, 0.0)
            self.w2 = np.zeros(n)
            self.K = len(levels)
        self.avail = (self.w1 + self.w2) > 0
        self.f = np.zeros(self.K)

    def fitted(self):
        return self.w1 * self.f[self.i1] + self.w2 * self.f[self.i2]

    def update(self, r, day_code, n_days, shrink=True):
        K = self.K
        num = np.bincount(self.i1, self.w1 * r, K) + np.bincount(self.i2, self.w2 * r, K)
        den = np.bincount(self.i1, self.w1, K) + np.bincount(self.i2, self.w2, K)
        raw = np.where(den > 0, num / np.where(den > 0, den, 1), 0.0)
        # day-clustered SE of each knot mean
        e1 = self.w1 * (r - raw[self.i1])
        e2 = self.w2 * (r - raw[self.i2])
        S = np.bincount(day_code * K + self.i1, e1, n_days * K) + np.bincount(day_code * K + self.i2, e2, n_days * K)
        se2 = np.where(den > 0, (S.reshape(n_days, K) ** 2).sum(0) / np.where(den > 0, den, 1) ** 2, np.inf)
        wm = (raw * den).sum() / den.sum()
        f = raw - wm
        if shrink:
            ok = np.isfinite(se2) & (den > 0)
            tau2 = max(float(np.average(f[ok] ** 2, weights=den[ok]) - np.average(se2[ok], weights=den[ok])), 1e-6)
            f = np.where(ok, f * tau2 / (tau2 + se2), 0.0)
        mono = self.spec["mono"]
        if mono == "dec":
            f = pav(f, den, increasing=False)
        elif mono == "veto":
            if f[1] > f[0]:
                f = np.zeros(2)
        self.f = f
        fit = self.fitted()
        self.f = self.f - fit[self.avail].mean()  # centre on the rows
        self.den, self.se = den, np.sqrt(np.where(np.isfinite(se2), se2, np.nan))
        return self


def fit_group(C, y, dates, comps, sweeps=15, qk=None, coarse=False):
    qk = qk or QK
    day_code, uniq = pd.factorize(pd.Series(dates))
    n_days = len(uniq)
    B = {}
    for c in comps:
        sp = SPEC[c]
        x = C[c].to_numpy()
        if sp["type"] == "linear":
            if c == "delta":
                knots = [0.15, 0.40, 0.70] if coarse else DELTA_KNOTS
            elif c == "ivpct":
                knots = [10.0, 50.0, 90.0] if coarse else IVPCT_KNOTS
            else:
                v = np.asarray(x, float)
                knots = np.unique(np.round(np.nanquantile(v[~np.isnan(v)], qk), 4)).tolist()
            B[c] = Basis(c, sp, x, knots=knots)
        elif sp["type"] == "cat":
            B[c] = Basis(c, sp, x, levels=DTE_LEVELS)
        else:
            B[c] = Basis(c, sp, x, levels=["no", "yes"])
    intercept = float(y.mean())
    for _ in range(sweeps):
        for c in comps:
            other = sum((B[k].fitted() for k in comps if k != c), np.zeros(len(y)))
            B[c].update(y - intercept - other, day_code, n_days)
    W = {c: float(np.std(B[c].fitted()[B[c].avail])) for c in comps}
    out = {"intercept": intercept, "n": int(len(y)), "n_days": int(n_days), "components": {}}
    for c in comps:
        b = B[c]
        d = {"type": SPEC[c]["type"], "hyp": SPEC[c]["hyp"], "monotone": SPEC[c]["mono"],
             "effects": [round(float(v), 5) for v in b.f], "n_eff": [round(float(v), 1) for v in b.den],
             "se": [None if not np.isfinite(v) else round(float(v), 5) for v in b.se], "weight": round(W[c], 5),
             "coverage": round(float(b.avail.mean()), 4)}
        if SPEC[c]["type"] == "linear":
            d["knots"] = [round(float(k), 4) for k in b.knots]
        else:
            d["levels"] = b.levels
        out["components"][c] = d
    return out


def weighted_quantiles(x, w, qs):
    o = np.argsort(x)
    x, w = x[o], w[o]
    cw = np.cumsum(w) / w.sum()
    return np.interp(qs, cw, x)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--features", default=os.path.join(ROOT, "data", "features", "daily.parquet"))
    ap.add_argument("--out", default=os.path.join(ROOT, "engine", "model.json"))
    ap.add_argument("--variant", default="v1_full")
    ap.add_argument("--index-components", default=",".join(DEFAULT_COMPONENTS["index"]))
    ap.add_argument("--stock-components", default=",".join(DEFAULT_COMPONENTS["stocks"]))
    ap.add_argument("--coarse", action="store_true", help="3 knots per linear component (q10/q50/q90)")
    a = ap.parse_args()
    comps = {"index": a.index_components.split(","), "stocks": a.stock_components.split(",")}

    df = ev.load_rows(a.bt, a.features, "IS")
    C = ev.components_from_rows(df)
    g = np.where(df["cls"].to_numpy() == "stock", "stocks", "index")
    keep = C["friction"].to_numpy() <= FRICTION_MAX
    print(f"IS rows {len(df):,}; friction > {FRICTION_MAX}% dropped: {(~keep).sum():,} "
          f"(index {((~keep) & (g == 'index')).sum():,}, stocks {((~keep) & (g == 'stocks')).sum():,})")
    model = {"name": "BALROG buyConviction", "variant": a.variant, "coarse": bool(a.coarse),
             "edge_claim": "none - loss-avoidance ranking (no entry hypothesis passed 02_hypotheses §0)",
             "created": dt.datetime.now().isoformat(timespec="seconds"), "script": "research/scripts/p5_fit_engine.py",
             "fit": {"sample": "IS", "h": 3, "outcome": "net_R_h3 (bhavcopy close +- modelled p75 half-spread, statutory costs)",
                     "date_range": [str(df["date"].min().date()), str(df["date"].max().date())],
                     "rows_dropped_friction_gt_10": int((~keep).sum())},
             "r": float(np.asarray(rates.repo_rate(pd.to_datetime(["2026-11-12"])))[0]),
             "gates": {"friction_max_pct": FRICTION_MAX, "dte_min": 7, "delta_range": [0.05, 0.80], "min_premium": 2.0,
                       "vol_floor_contracts": {"index": 500, "stocks": 50}, "ivhist_min_points": 20},
             "dte_levels": DTE_LEVELS, "excluded": ["GEX (D1/D4)", "OI walls (D2/D3)", "futures structure (M1/M1b)",
                                                    "ADX (M2)", "breakouts (M3)", "relative strength (M5)",
                                                    "earnings (E1-E3)", "intraday ORB/momentum (M6/M7)", "C4 gamma/theta"],
             "watchlist_zero_weight": {"watch_gap_hold": "M4 index: open gap > 1 sigma_daily that holds into the close",
                                       "watch_low_ivpct": "V2 index: IV percentile < 20"},
             "groups": {}}
    preds, wts = [], []
    for grp in ("index", "stocks"):
        m = keep & (g == grp)
        Cg = C[m].reset_index(drop=True)
        y = df.loc[m, "net_R_h3"].to_numpy(float)
        gm = fit_group(Cg, y, df.loc[m, "date"].to_numpy(), comps[grp], qk=[0.10, 0.50, 0.90] if a.coarse else None,
                       coarse=a.coarse)
        model["groups"][grp] = gm
        p, _ = ev.predict(gm, {c: Cg[c].to_numpy() for c in comps[grp]})
        preds.append(p)
        wts.append(np.full(len(p), 1.0 / len(p)))
        print(f"[{grp}] n {gm['n']:,} days {gm['n_days']} intercept {gm['intercept']:+.4f}; pred R p5/p50/p95 "
              f"{np.quantile(p, .05):+.3f}/{np.quantile(p, .5):+.3f}/{np.quantile(p, .95):+.3f}; corr(pred,y) "
              f"{np.corrcoef(p, y)[0, 1]:.3f}")
        for c, d in gm["components"].items():
            lab = d.get("knots") or d.get("levels")
            print(f"   {c:8s} W={d['weight']:.4f} cov={d['coverage']:.2f} " +
                  " ".join(f"{k}:{e:+.3f}" for k, e in zip(lab, d["effects"])))
    P = np.concatenate(preds)
    Wt = np.concatenate(wts)
    qs = np.array([0, 1, 5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 99, 100], float)
    R = weighted_quantiles(P, Wt, qs / 100)
    R = np.maximum.accumulate(R + np.arange(len(R)) * 1e-9)
    model["mapping"] = {"R": [round(float(v), 6) for v in R], "score": qs.tolist(),
                        "rule": "score = piecewise-linear interpolation of predicted net R through IS quantiles "
                                "(class groups equally weighted); score ~ IS percentile; clipped to [0, 100]"}
    model["fit"]["n"] = {grp: model["groups"][grp]["n"] for grp in model["groups"]}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(model, f, indent=1)
    print("mapping R knots: " + " ".join(f"{s:.0f}:{r:+.3f}" for s, r in zip(qs, R)))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
