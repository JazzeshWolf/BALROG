"""P4: contract selection (C1-C5) and exits (X0-X4), 02_hypotheses.md §4-§5.

Runs on the unconditional B1 population (default) or on an entry-signal subset:
  python -I research/scripts/p4_run_contract_exits.py [--bt data/bt] [--signals sig.parquet] [--tag name] [--no-log]
`--signals`: parquet with columns (date, symbol, side); side in CE/PE/call/put/both/strangle.

Lead rulings applied:
- Primary sample per hold h excludes holds spanning ca_break, past data end, settle-priced and unmarked exits.
  No winsorising; robustness line = R capped at +5.
- Groups NIFTY, BANKNIFTY, SENSEX, index (all index incl. other indices), stocks; sides CE, PE, all.
- IS/OOS per backtest.sample_label. Code order enforces "choose on IS, freeze, then touch OOS": phase 1 loads only
  IS rows, makes every selection (best delta x DTE cell, tercile cutpoints, liquidity floor, best X0 h, best X1/X2
  cell) and writes research/out/p4_contract_exits_frozen[_tag].json; only then are OOS rows loaded.
- C1/C2 at h=3 (h=1,5 shown); day-clustered bootstrap CIs; non-overlapping subsample = entry dates whose trading-day
  index (data/ref/calendar trading_day) is divisible by 3.
- C3 primary RMR uses the HAR sigma (features har_vol_h3); rmr_rv20 from the table is the variant.
- C4 primary = gamma_theta_rv (0.5 gamma (S rv20)^2 / |theta|, dimensionless); raw gamma/|theta| is the variant.
- Exits on standard-contract trades, h_max in {3,5}, paired vs X0 at the same h_max.
Trial rows are collected in memory and appended to research/trials_log.csv once, at the end (none on --no-log).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import sys
import warnings

warnings.simplefilter("ignore", category=Warning)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from research.lib import backtest as bt
from research.lib import trials

OUT = os.path.join(ROOT, "research", "out")
SCRIPT = "research/scripts/p4_run_contract_exits.py"
GROUPS = ["NIFTY", "BANKNIFTY", "SENSEX", "index", "stocks"]
SIDES = ["CE", "PE", "all"]
DELTAS = [0.15, 0.25, 0.40, 0.55, 0.70]
BUCKETS = [b[0] for b in bt.DTE_BUCKETS]
HS_C12 = (1, 3, 5)
NB = 2000
CAP = 5.0
MIN_N, MIN_D = 100, 40
SPREAD_EDGES = [0, 2, 4, 6, 8, 10, 12, 15, 20, 30, 1000]  # round-trip spread, % of premium
R_BINS = [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 1e9]
EXIT_RULES = [p for p, _, _ in bt.exit_rule_grid()]
X1_CELLS = [p for p in EXIT_RULES if p.startswith("x1_")]


# ----------------------------------------------------------------------------- loading
def _cat(df, cols):
    for c in cols:
        if c in df:
            df[c] = df[c].astype("category")
    return df


def _valid(df, h):
    return (df[f"net_R_h{h}"].notna() & ~df[f"ca_break_h{h}"].astype(bool) & ~df[f"incomplete_h{h}"].astype(bool)
            & ~df[f"settled_h{h}"].astype(bool) & ~df[f"nomark_h{h}"].astype(bool))


DROPPED = {}


def nse_stock_only(df, label):
    """Stock options: NSE only (BSE-listed stock options duplicate NSE symbols). BSE kept for SENSEX/BANKEX."""
    bad = (df["cls"].astype(str) == "stock").to_numpy() & (df["exchange"].astype(str) != "nse").to_numpy()
    DROPPED[label] = DROPPED.get(label, 0) + int(bad.sum())
    return df[~bad]


def load(bt_dir, sample, sig, years=None):
    """-> A (all delta x DTE rows; R NaN where not in the primary sample) and S (standard rows + exits)."""
    key = ["trade_id", "date", "exchange", "symbol", "cls", "grp", "opt_type", "dte_bucket", "delta_target", "is_std",
           "is_std_exp", "premium", "volume", "half_spread"]
    flags = ("ca_break", "incomplete", "settled", "nomark")
    a_cols = key + [f"{c}_h{h}" for h in HS_C12 for c in ("net_R", "gross_R") + flags] + ["net_R2x_h3"]
    s_cols = key + ["lot", "theta", "gamma", "entry_fill", "spot", "rmr_rv20", "req_move_pts", "gamma_theta",
                    "gamma_theta_rv", "h_cap"] + [f"p{k}_h" for k in range(1, 6)]
    s_cols += [f"{c}_h{h}" for h in bt.H_LIST for c in ("net_R", "gross_R", "net_rs", "net_R2x", "cal_days",
                                                         "exit_date") + flags]
    xcols = ["trade_id"] + [f"{p}_h{hm}_{s}" for p in EXIT_RULES for hm in bt.H_MAX for s in ("R", "rs", "day", "why")]
    A, S = [], []
    for f in sorted(glob.glob(os.path.join(bt_dir, "trades", "year=*.parquet"))):
        if years and not any(f"year={y}" in f for y in years):
            continue
        a = pd.read_parquet(f, columns=a_cols, filters=[("sample", "==", sample)])
        if a.empty:
            continue
        a = nse_stock_only(a, f"{sample}_grid")
        if sig is not None:
            a = filt(a, sig)
        for h in HS_C12:
            v = _valid(a, h)
            a.loc[~v, [f"net_R_h{h}", f"gross_R_h{h}"]] = np.nan
            if h == 3:
                a.loc[~v, "net_R2x_h3"] = np.nan
            a = a.drop(columns=[f"{c}_h{h}" for c in flags])
        for c in a.columns:
            if a[c].dtype == "float64" and c not in ("premium", "volume", "half_spread"):
                a[c] = a[c].astype("float32")
        A.append(_cat(a, ["symbol", "cls", "grp", "opt_type", "dte_bucket"]))
        s = pd.read_parquet(f, columns=s_cols, filters=[("sample", "==", sample), ("is_std", "==", True)])
        s = nse_stock_only(s, f"{sample}_std")
        for c in s.columns:
            if s[c].dtype == "float64" and c not in ("premium", "volume", "half_spread", "spot", "req_move_pts"):
                s[c] = s[c].astype("float32")
        if sig is not None:
            s = filt(s, sig)
        for h in bt.H_LIST:
            s[f"valid_h{h}"] = _valid(s, h)
        ef = f.replace(os.sep + "trades" + os.sep, os.sep + "exits" + os.sep).replace("/trades/", "/exits/")
        ex = pd.read_parquet(ef, columns=xcols)
        ex = ex[ex["trade_id"].isin(s["trade_id"])]
        s = s.merge(ex, on="trade_id", how="left")
        S.append(_cat(s, ["symbol", "cls", "grp", "opt_type", "dte_bucket"]))
        del ex
    A = pd.concat(A, ignore_index=True)
    S = pd.concat(S, ignore_index=True)
    for df in (A, S):
        for c in ("symbol", "cls", "grp", "opt_type", "dte_bucket"):
            df[c] = df[c].astype(str).astype("category")
    return A, S


def filt(df, sig):
    m = df.merge(sig, on=["date", "symbol", "opt_type"], how="inner")
    return m


def read_signals(path):
    s = pd.read_parquet(path)
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    side = s["side"].astype(str).str.upper().map({"CE": "CE", "CALL": "CE", "C": "CE", "PE": "PE", "PUT": "PE",
                                                   "P": "PE", "BOTH": "both", "STRANGLE": "both"})
    if side.isna().any():
        raise ValueError("unknown side values in signals")
    both = s[side == "both"]
    out = pd.concat([s.assign(opt_type=side)[side != "both"], both.assign(opt_type="CE"), both.assign(opt_type="PE")])
    return out[["date", "symbol", "opt_type"]].drop_duplicates()


def add_features(S, cal_idx):
    fe = pd.read_parquet(os.path.join(ROOT, "data", "features", "daily.parquet"), columns=["date", "symbol", "har_vol_h3"])
    fe["symbol"] = fe["symbol"].astype(str)
    S["_sym"] = S["symbol"].astype(str)
    S = S.merge(fe.rename(columns={"symbol": "_sym"}), on=["date", "_sym"], how="left").drop(columns="_sym")
    with np.errstate(invalid="ignore", divide="ignore"):
        S["rmr_har"] = S["req_move_pts"] / (S["har_vol_h3"] / np.sqrt(252.0) * np.sqrt(3.0) * S["spot"])
    S["spread_rt_pct"] = 200.0 * S["half_spread"] / S["premium"]
    # calendar days to each exit day (path day 4 interpolated between days 3 and 5)
    cd = {d: S[f"cal_days_h{d}"].to_numpy(float) for d in (1, 2, 3, 5)}
    cd[4] = (cd[3] + cd[5]) / 2
    S.attrs["cd"] = cd
    return S


def nonoverlap_mask(dates, cal_idx):
    i = cal_idx.get_indexer(pd.DatetimeIndex(dates))
    return (i >= 0) & (i % 3 == 0)


# ----------------------------------------------------------------------------- stats helpers
def gmask(df, g, side):
    if g in ("NIFTY", "BANKNIFTY", "SENSEX"):
        m = (df["grp"] == g).to_numpy()
    elif g == "index":
        m = (df["cls"] == "index").to_numpy()
    else:
        m = (df["cls"] == "stock").to_numpy()
    if side != "all":
        m &= (df["opt_type"] == side).to_numpy()
    return m


def cell(net, gross, dates, no_mask=None, seed=0, net2x=None):
    ok = ~np.isnan(net)
    if ok.sum() == 0:
        return None
    ci = bt.cluster_bootstrap_ci(net[ok], dates[ok], n_boot=NB, seed=seed)
    o = {"n": int(ok.sum()), "n_days": int(pd.unique(dates[ok]).size), "net": float(ci["mean"]), "lo": ci["lo"],
         "hi": ci["hi"], "gross": float(np.nanmean(gross[ok])), "net_cap5": float(np.minimum(net[ok], CAP).mean()),
         "gross_cap5": float(np.minimum(gross[ok], CAP).mean())}
    if net2x is not None:
        o["net_2x"] = float(np.nanmean(net2x[ok]))
    if no_mask is not None:
        k = ok & no_mask
        o["net_nonoverlap"] = float(net[k].mean()) if k.any() else None
        o["n_nonoverlap"] = int(k.sum())
    return o


def contrast(xa, da, xb, db, seed=0, no_a=None, no_b=None):
    """mean(a) - mean(b), joint day-clustered bootstrap."""
    ka, kb = ~np.isnan(xa), ~np.isnan(xb)
    if ka.sum() < 2 or kb.sum() < 2:
        return None
    r = bt.diff_vs_baseline(xa[ka], da[ka], xb[kb], db[kb], n_boot=NB, seed=seed)
    o = {"diff": float(r["diff"]), "lo": r["lo"], "hi": r["hi"], "p": r["p"],
         "n_a": int(ka.sum()), "n_b": int(kb.sum()), "days_a": int(pd.unique(da[ka]).size),
         "days_b": int(pd.unique(db[kb]).size)}
    if no_a is not None:
        a2, b2 = ka & no_a, kb & no_b
        o["diff_nonoverlap"] = float(xa[a2].mean() - xb[b2].mean()) if a2.any() and b2.any() else None
    return o


def enough(c):
    return c is not None and min(c["n_a"], c["n_b"]) >= MIN_N and min(c["days_a"], c["days_b"]) >= MIN_D


def verdict_contrast(is_c, oos_c, extra_ok=True):
    """Expected direction positive. Returns string."""
    if oos_c is None or not enough(oos_c):
        return "anecdotal"
    if oos_c["hi"] < 0:
        return "reversed"
    if oos_c["lo"] > 0 and is_c is not None and is_c["diff"] > 0 and extra_ok:
        return "supported"
    return "not supported"


TRIALS = []


def trial(id_, variant, registered, sample, grp, st, p=None, verdict="", notes=""):
    if st is None:
        return
    TRIALS.append(dict(id=id_, variant=variant, registered=registered, sample=sample, cls=grp, style="swing",
                       n_trades=st.get("n", st.get("n_a")), n_days=st.get("n_days", st.get("days_a")),
                       gross_mean_R=st.get("gross"), net_mean_R=st.get("net", st.get("diff")), ci_lo=st.get("lo"),
                       ci_hi=st.get("hi"), p_vs_b1=p, net_mean_R_2x_spread=st.get("net_2x"), verdict=verdict,
                       notes=notes, script=SCRIPT))


# ----------------------------------------------------------------------------- C1 / C2 (all rows)
def run_c12(A, smp, cal_idx, frozen, tag):
    res = {"heat": {}, "c1": {}, "c2": {}}
    d_all = A["date"].to_numpy()
    no_all = nonoverlap_mask(d_all, cal_idx)
    dt_ = A["delta_target"].to_numpy()
    bk = A["dte_bucket"].astype(str).to_numpy()
    std_exp = A["is_std_exp"].to_numpy(bool)
    for g in GROUPS:
        for side in SIDES:
            gm = gmask(A, g, side)
            for h in HS_C12:
                net = A[f"net_R_h{h}"].to_numpy(float)
                gr = A[f"gross_R_h{h}"].to_numpy(float)
                n2 = A["net_R2x_h3"].to_numpy(float) if h == 3 else None
                # heatmap delta x DTE (h=3 all sides; h=1,5 side=all only to bound the trial count)
                if h == 3:
                    for dl in DELTAS:
                        for b in BUCKETS:
                            m = gm & np.isclose(dt_, dl) & (bk == b)
                            c = cell(net[m], gr[m], d_all[m], no_all[m], seed=h, net2x=None if n2 is None else n2[m])
                            if c is None:
                                continue
                            res["heat"][f"{g}|{side}|h{h}|{dl}|{b}"] = c
                            if np.isclose(dl, 0.40) or h != 3 or side != "all":
                                continue  # 0.40 rows = C2 cells (logged there); other sides/h: JSON only
                            trial("C1C2", f"{tag}cell_d{dl}_{b}_{side}_h{h}", "no", smp, g, c,
                                  notes=f"delta x DTE heatmap cell (bucket expiry); nonoverlap {c.get('net_nonoverlap')}; "
                                        f"cap5 {c['net_cap5']:.4f}")
                # C1: std expiry, delta cells
                cm = {}
                for dl in DELTAS:
                    m = gm & std_exp & np.isclose(dt_, dl)
                    cm[dl] = m
                    c = cell(net[m], gr[m], d_all[m], no_all[m], seed=h, net2x=None if n2 is None else n2[m])
                    if c:
                        res["c1"][f"{g}|{side}|h{h}|{dl}"] = c
                        trial("C1", f"{tag}d{dl}_{side}_h{h}", "yes", smp, g, c,
                              notes=f"std expiry; nonoverlap {c.get('net_nonoverlap')}; cap5 {c['net_cap5']:.4f}")
                means = [res["c1"].get(f"{g}|{side}|h{h}|{dl}", {}).get("net", np.nan) for dl in DELTAS]
                ok = ~np.isnan(means)
                rho = float(spearmanr(np.array(DELTAS)[ok], np.array(means)[ok])[0]) if ok.sum() >= 3 else np.nan
                ct = contrast(net[cm[0.70]], d_all[cm[0.70]], net[cm[0.15]], d_all[cm[0.15]], seed=h,
                              no_a=no_all[cm[0.70]], no_b=no_all[cm[0.15]])
                if ct:
                    ct["spearman"] = rho
                    res["c1"][f"{g}|{side}|h{h}|top_minus_bottom"] = ct
                    trial("C1", f"{tag}d0.70-d0.15_{side}_h{h}", "yes", smp, g, ct, p=ct["p"],
                          notes=f"contrast; spearman {rho:.2f}; nonoverlap {ct.get('diff_nonoverlap')}")
                # C2: delta 0.40, DTE buckets
                bm = {}
                for b in BUCKETS:
                    m = gm & np.isclose(dt_, 0.40) & (bk == b)
                    bm[b] = m
                    c = cell(net[m], gr[m], d_all[m], no_all[m], seed=h, net2x=None if n2 is None else n2[m])
                    if c:
                        res["c2"][f"{g}|{side}|h{h}|{b}"] = c
                        trial("C2", f"{tag}{b}_{side}_h{h}", "yes", smp, g, c,
                              notes=f"delta .40; nonoverlap {c.get('net_nonoverlap')}; cap5 {c['net_cap5']:.4f}")
                inter = bm["11-20"] | bm["21-35"]
                for edge in ("7-10", "36+"):
                    ct = contrast(net[inter], d_all[inter], net[bm[edge]], d_all[bm[edge]], seed=h,
                                  no_a=no_all[inter], no_b=no_all[bm[edge]])
                    if ct:
                        res["c2"][f"{g}|{side}|h{h}|interior_minus_{edge}"] = ct
                        trial("C2", f"{tag}11-35_minus_{edge}_{side}_h{h}", "yes", smp, g, ct, p=ct["p"],
                              notes=f"contrast; nonoverlap {ct.get('diff_nonoverlap')}")
    if smp == "IS":  # freeze best cell (side all, h3) per group on IS
        best = {}
        for g in GROUPS:
            cands = [(v["net"], k) for k, v in res["heat"].items()
                     if k.startswith(f"{g}|all|h3|") and v["n"] >= MIN_N and v["n_days"] >= MIN_D]
            if cands:
                v, k = max(cands)
                _, _, _, dl, b = k.split("|")
                best[g] = {"delta": float(dl), "bucket": b, "is_net": v}
        frozen["best_cell"] = best
    return res


# ----------------------------------------------------------------------------- C3 / C4 / C5 (std rows)
def terciles(S, col, g, frozen_cuts, smp):
    gm = gmask(S, g, "all") & S["valid_h3"].to_numpy(bool)
    x = S[col].to_numpy(float)
    if smp == "IS":
        v = x[gm & np.isfinite(x)]
        frozen_cuts[f"{col}|{g}"] = [float(np.quantile(v, 1 / 3)), float(np.quantile(v, 2 / 3))] if len(v) > 30 else None
    cuts = frozen_cuts.get(f"{col}|{g}")
    t = np.full(len(S), -1)
    if cuts:
        fin = np.isfinite(x)
        t[fin] = np.searchsorted(cuts, x[fin], side="right")
    return t, cuts


def run_c34(S, smp, cal_idx, frozen, tag):
    res = {}
    d = S["date"].to_numpy()
    no = nonoverlap_mask(d, cal_idx)
    net = np.where(S["valid_h3"], S["net_R_h3"], np.nan).astype(float)
    gr = np.where(S["valid_h3"], S["gross_R_h3"], np.nan).astype(float)
    n2 = np.where(S["valid_h3"], S["net_R2x_h3"], np.nan).astype(float)
    spec = [("C3", "rmr_har", "primary", True), ("C3", "rmr_rv20", "variant", True),
            ("C4", "gamma_theta_rv", "primary", False), ("C4", "gamma_theta", "variant", False)]
    cuts_store = frozen.setdefault("tercile_cuts", {})
    for hid, col, role, low_good in spec:
        for g in GROUPS:
            t, cuts = terciles(S, col, g, cuts_store, smp)
            for side in SIDES:
                gm = gmask(S, g, side)
                for k in range(3):
                    m = gm & (t == k)
                    c = cell(net[m], gr[m], d[m], no[m], seed=3, net2x=n2[m])
                    if c:
                        res[f"{hid}|{col}|{g}|{side}|T{k+1}"] = c
                        trial(hid, f"{tag}{col}_T{k+1}_{side}", "yes", smp, g, c,
                              notes=f"{role}; IS cuts {cuts}; cap5 {c['net_cap5']:.4f}")
                a, b = (gm & (t == 0), gm & (t == 2)) if low_good else (gm & (t == 2), gm & (t == 0))
                ct = contrast(net[a], d[a], net[b], d[b], seed=3, no_a=no[a], no_b=no[b])
                if ct:
                    ct["cuts"] = cuts
                    lab = "T1-T3" if low_good else "T3-T1"
                    res[f"{hid}|{col}|{g}|{side}|{lab}"] = ct
                    trial(hid, f"{tag}{col}_{lab}_{side}", "yes", smp, g, ct, p=ct["p"],
                          notes=f"{role} contrast (expected > 0); nonoverlap {ct.get('diff_nonoverlap')}")
    return res


def run_c5(S, smp, frozen, tag):
    res = {"volume": {}, "spread": {}}
    d = S["date"].to_numpy()
    net = np.where(S["valid_h3"], S["net_R_h3"], np.nan).astype(float)
    gr = np.where(S["valid_h3"], S["gross_R_h3"], np.nan).astype(float)
    vol = S["volume"].to_numpy(float)
    sp = S["spread_rt_pct"].to_numpy(float)
    fz = frozen.setdefault("c5", {})
    for g in GROUPS:
        gm = gmask(S, g, "all")
        if smp == "IS":
            v = vol[gm & ~np.isnan(net)]
            fz[f"vol_edges|{g}"] = [float(q) for q in np.unique(np.quantile(v, np.linspace(0, 1, 11)))] if len(v) > 50 else None
        ve = fz.get(f"vol_edges|{g}")
        rows = []
        if ve:
            vb = np.clip(np.searchsorted(ve, vol, side="right") - 1, 0, len(ve) - 2)
            for k in range(len(ve) - 1):
                m = gm & (vb == k)
                c = cell(net[m], gr[m], d[m], seed=5)
                if c:
                    c.update(lo_edge=ve[k], hi_edge=ve[k + 1])
                    rows.append(c)
                    trial("C5", f"{tag}vol_decile{k+1}", "yes", smp, g, c, verdict="descriptive",
                          notes=f"volume contracts [{ve[k]:.0f},{ve[k+1]:.0f}]")
        res["volume"][g] = rows
        rows = []
        sb = np.clip(np.searchsorted(SPREAD_EDGES, sp, side="right") - 1, 0, len(SPREAD_EDGES) - 2)
        for k in range(len(SPREAD_EDGES) - 1):
            m = gm & (sb == k)
            c = cell(net[m], gr[m], d[m], seed=5)
            if c:
                c.update(lo_edge=SPREAD_EDGES[k], hi_edge=SPREAD_EDGES[k + 1])
                rows.append(c)
                trial("C5", f"{tag}spread_{SPREAD_EDGES[k]}-{SPREAD_EDGES[k+1]}pct", "yes", smp, g, c,
                      verdict="descriptive", notes="round-trip modelled spread % of premium")
        res["spread"][g] = rows
        if smp == "IS":
            fz[f"floor|{g}"] = liquidity_floor(res["volume"][g], res["spread"][g])
    return res


def liquidity_floor(vrows, srows, tol=0.03):
    """Volume floor: lowest decile edge from which every higher decile is within `tol` R of the top-3-decile mean.
    Spread ceiling: highest spread edge up to which every lower bucket (n>=100) is within `tol` of the mean of the
    two lowest-spread buckets."""
    out = {"tol_R": tol}
    v = [r for r in vrows if r["n"] >= MIN_N]
    if len(v) >= 4:
        plateau = np.mean([r["net"] for r in v[-3:]])
        k = len(v) - 1
        while k > 0 and v[k - 1]["net"] >= plateau - tol:
            k -= 1
        out.update(volume_floor=v[k]["lo_edge"], volume_plateau_R=float(plateau), volume_floor_decile=k + 1)
    s = [r for r in srows if r["n"] >= MIN_N]
    if len(s) >= 2:
        plateau = np.mean([r["net"] for r in s[:2]])
        k = 0
        while k + 1 < len(s) and s[k + 1]["net"] >= plateau - tol:
            k += 1
        out.update(spread_ceiling_pct=s[k]["hi_edge"], spread_plateau_R=float(plateau))
    return out


# ----------------------------------------------------------------------------- exits
def exit_metrics(S, m, R, rs, day, cd, dates, theta, lot, why=None):
    ok = m & ~np.isnan(R)
    days = np.clip(day[ok].astype(int), 1, 5)
    cal = np.choose(days - 1, [cd[1][ok], cd[2][ok], cd[3][ok], cd[4][ok], cd[5][ok]])
    bm = bt.buyer_metrics(rs[ok], R[ok], dates[ok], theta[ok], lot[ok], cal)
    bm["time_in_trade"] = {str(k): float((days == k).mean()) for k in range(1, 6)}
    if why is not None:
        w = why[ok].astype(int)
        bm["why"] = {str(k): float((w == k).mean()) for k in range(6)}
    bm["r_hist"] = np.histogram(np.clip(R[ok], -1, 1e8), bins=R_BINS)[0].tolist()
    bm["net_cap5"] = float(np.minimum(R[ok], CAP).mean()) if ok.any() else None
    return bm


def run_exits(S, smp, frozen, tag):
    res = {"x0": {}, "rules": {}, "ttt": {}}
    dates = S["date"].to_numpy()
    theta = S["theta"].to_numpy(float)
    lot = S["lot"].to_numpy(float)
    cd = S.attrs["cd"]
    hp = np.column_stack([S["premium"].to_numpy(float)] + [S[f"p{k}_h"].to_numpy(float) for k in range(1, 6)])
    entry = S["entry_fill"].to_numpy(float)
    fx = frozen.setdefault("exits", {})
    for g in GROUPS:
        for side in SIDES:
            gm = gmask(S, g, side)
            # X0 at every h
            for h in bt.H_LIST:
                v = gm & S[f"valid_h{h}"].to_numpy(bool)
                R = S[f"net_R_h{h}"].to_numpy(float)
                c = cell(np.where(v, R, np.nan), S[f"gross_R_h{h}"].to_numpy(float), dates, seed=h,
                         net2x=np.where(v, S[f"net_R2x_h{h}"].to_numpy(float), np.nan))
                if c is None:
                    continue
                c["metrics"] = exit_metrics(S, v, R, S[f"net_rs_h{h}"].to_numpy(float), np.full(len(S), h),
                                            cd, dates, theta, lot)
                res["x0"][f"{g}|{side}|h{h}"] = c
                if side == "all":
                    trial("X0", f"{tag}time_h{h}_{side}", "yes", smp, g, c, verdict="reference",
                          notes=f"PF {c['metrics']['profit_factor']:.3f}; theta share {c['metrics'].get('theta_share_losers')}")
            for hm in bt.H_MAX:
                v = gm & S[f"valid_h{hm}"].to_numpy(bool)
                x0 = np.where(v, S[f"net_R_h{hm}"].to_numpy(float), np.nan)
                for tgt in (0.3, 0.5, 1.0):
                    res["ttt"][f"{g}|{side}|h{hm}|{tgt}"] = bt.time_to_target(hp[v], entry[v], tgt, H=hm)
                for p in EXIT_RULES:
                    R = np.where(v, S[f"{p}_h{hm}_R"].to_numpy(float), np.nan)
                    pair = ~np.isnan(R) & ~np.isnan(x0)
                    if pair.sum() < 2:
                        continue
                    ct = contrast(np.where(pair, R, np.nan), dates, np.where(pair, x0, np.nan), dates, seed=hm)
                    c = cell(np.where(pair, R, np.nan), np.full(len(S), np.nan), dates, seed=hm)
                    c["vs_x0"] = ct
                    c["x0_net"] = float(np.nanmean(x0[pair]))
                    c["metrics"] = exit_metrics(S, pair, R, S[f"{p}_h{hm}_rs"].to_numpy(float),
                                                S[f"{p}_h{hm}_day"].to_numpy(float), cd, dates, theta, lot,
                                                S[f"{p}_h{hm}_why"].to_numpy(float))
                    if side != "all":  # side splits: compact, JSON only (descriptive, not logged)
                        res["rules"][f"{g}|{side}|h{hm}|{p}"] = {
                            "n": c["n"], "net": c["net"], "x0_net": c["x0_net"],
                            "vs_x0": {k: ct[k] for k in ("diff", "lo", "hi", "p")},
                            "win_rate": c["metrics"]["win_rate"], "profit_factor": c["metrics"]["profit_factor"]}
                        continue
                    res["rules"][f"{g}|{side}|h{hm}|{p}"] = c
                    xid = {"x1": "X1", "x2": "X2", "x3": "X3", "x4": "X4"}[p[:2]]
                    trial(xid, f"{tag}{p}_hmax{hm}_{side}", "yes", smp, g,
                          dict(c, gross=None), p=ct["p"],
                          notes=f"diff vs X0 {ct['diff']:+.4f} [{ct['lo']:+.4f},{ct['hi']:+.4f}]; "
                                f"PF {c['metrics']['profit_factor']:.3f}")
            if smp == "IS" and side == "all":
                for hm in bt.H_MAX:
                    k = lambda p: res["rules"].get(f"{g}|all|h{hm}|{p}", {}).get("vs_x0", {}).get("diff", -9)
                    fx[f"{g}|h{hm}|X1"] = max(X1_CELLS, key=k)
                    fx[f"{g}|h{hm}|X2"] = max(["x2_ext", "x2_sig"], key=k)
                hs = [(res["x0"].get(f"{g}|all|h{h}", {}).get("net", -9), h) for h in bt.H_LIST]
                fx[f"{g}|best_h"] = max(hs)[1]
    return res


# ----------------------------------------------------------------------------- theta bleed
def theta_bleed(S, smp_label):
    out = {}
    theta = S["theta"].to_numpy(float)
    lot = S["lot"].to_numpy(float)
    bk = S["dte_bucket"].astype(str).to_numpy()
    for g in GROUPS:
        gm = gmask(S, g, "all")
        for b in BUCKETS + ["all"]:
            m0 = gm if b == "all" else gm & (bk == b)
            for h in bt.H_LIST:
                m = m0 & S[f"valid_h{h}"].to_numpy(bool)
                if m.sum() < 30:
                    continue
                net = S[f"net_R_h{h}"].to_numpy(float)[m]
                gr = S[f"gross_R_h{h}"].to_numpy(float)[m]
                rs = S[f"net_rs_h{h}"].to_numpy(float)[m]
                cal = S[f"cal_days_h{h}"].to_numpy(float)[m]
                th_R = np.abs(theta[m]) * cal / S["entry_fill"].to_numpy(float)[m]
                out[f"{smp_label}|{g}|{b}|h{h}"] = {
                    "n": int(m.sum()), "net": float(net.mean()), "gross": float(gr.mean()),
                    "net_per_day": float(net.mean() / h), "gross_per_day": float(gr.mean() / h),
                    "theta_R_mean": float(np.nanmean(th_R)),
                    "theta_share_losers": bt.theta_share(theta[m], lot[m], cal, rs)}
    return out


# ----------------------------------------------------------------------------- verdicts
def c_verdicts(R_is, R_oos):
    v = {}
    for g in GROUPS:
        for side in SIDES:
            key = f"{g}|{side}|h3"
            ci, co = R_is["c12"]["c1"].get(f"{key}|top_minus_bottom"), R_oos["c12"]["c1"].get(f"{key}|top_minus_bottom")
            mono = co is not None and (co.get("spearman") or -1) >= 0.9 - 1e-9
            vc = verdict_contrast(ci, co, mono)
            if vc == "not supported" and verdict_contrast(ci, co) == "supported":
                vc = "partial: contrast only, not monotone"
            v[f"C1|{g}|{side}"] = {"verdict": vc, "p_raw": co and co["p"],
                                   "oos_diff": co and co["diff"], "is_diff": ci and ci["diff"],
                                   "monotone_oos": mono, "spearman_oos": co and co.get("spearman"),
                                   "ci_excl_0": bool(co and co["lo"] > 0), "is_same_sign": bool(ci and co and ci["diff"] * co["diff"] > 0)}
            parts = []
            for e in ("7-10", "36+"):
                ci = R_is["c12"]["c2"].get(f"{key}|interior_minus_{e}")
                co = R_oos["c12"]["c2"].get(f"{key}|interior_minus_{e}")
                parts.append((e, ci, co, verdict_contrast(ci, co)))
            vs = [x[3] for x in parts]
            ver = "supported" if all(x == "supported" for x in vs) else (
                "anecdotal" if all(x == "anecdotal" for x in vs) else
                ("partial: " + ", ".join(f"{e} {x}" for e, _, _, x in parts)) if "supported" in vs else
                "not supported")
            ps = [x[2]["p"] for x in parts if x[2]]
            v[f"C2|{g}|{side}"] = {"verdict": ver, "p_raw": max(ps) if ps else None,
                                   "detail": {e: {"oos": co and co["diff"], "oos_ci": co and [co["lo"], co["hi"]],
                                                  "is": ci and ci["diff"], "verdict": x} for e, ci, co, x in parts}}
            for hid, col, lab in (("C3", "rmr_har", "T1-T3"), ("C3", "rmr_rv20", "T1-T3"),
                                  ("C4", "gamma_theta_rv", "T3-T1"), ("C4", "gamma_theta", "T3-T1")):
                ci = R_is["c34"].get(f"{hid}|{col}|{g}|{side}|{lab}")
                co = R_oos["c34"].get(f"{hid}|{col}|{g}|{side}|{lab}")
                v[f"{hid}:{col}|{g}|{side}"] = {"verdict": verdict_contrast(ci, co), "p_raw": co and co["p"],
                                                "oos_diff": co and co["diff"], "oos_ci": co and [co["lo"], co["hi"]],
                                                "is_diff": ci and ci["diff"]}
    return v


def x_verdicts(R_is, R_oos, frozen):
    v = {}
    grid = [[f"x1_tp{tp}_sl{sl}" for sl in (25, 40, 60)] for tp in (30, 50, 100)]
    for g in GROUPS:
        for hm in bt.H_MAX:
            def d(R, p):
                c = R["exits"]["rules"].get(f"{g}|all|h{hm}|{p}")
                return c["vs_x0"] if c else None
            for xid, cells in (("X1", None), ("X2", ["x2_ext", "x2_sig"]), ("X3", ["x3_trail"]), ("X4", ["x4_fast"])):
                chosen = frozen["exits"].get(f"{g}|h{hm}|{xid}", cells[0] if cells else None)
                if chosen is None:
                    continue
                ci, co = d(R_is, chosen), d(R_oos, chosen)
                if xid == "X1":
                    i, j = [(a, b) for a in range(3) for b in range(3) if grid[a][b] == chosen][0]
                    nb = [grid[a][b] for a, b in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)) if 0 <= a < 3 and 0 <= b < 3]
                elif xid == "X2":
                    nb = [c for c in cells if c != chosen]
                else:
                    nb = []
                nbd = [d(R_oos, p) for p in nb]
                plateau = all(x is not None and x["diff"] > 0 for x in nbd)
                raise_ = co is not None and co["lo"] > 0 and ci is not None and ci["diff"] > 0
                ver = ("anecdotal" if co is None or co["n_a"] < MIN_N else
                       "B1 leg passes (adoption pending survivors)" if raise_ and plateau else
                       "worse than X0" if co["hi"] < 0 else "no gain over X0")
                v[f"{xid}|{g}|h{hm}"] = {"chosen_on_IS": chosen, "verdict": ver, "p_raw": co and co["p"],
                                          "oos_diff": co and co["diff"], "oos_ci": co and [co["lo"], co["hi"]],
                                          "is_diff": ci and ci["diff"], "plateau": plateau,
                                          "neighbours_oos": {p: x and x["diff"] for p, x in zip(nb, nbd)}}
    return v


# ----------------------------------------------------------------------------- report
def conclude(out):
    """Data-driven summary lines (no hand-typed numbers)."""
    O, V, fz = out["OOS"], out["verdicts"], out["frozen_IS"]
    L = []
    for g in ("index", "stocks"):
        b = out["best_cell_frozen_oos"].get(g)
        heat = {k: v for k, v in O["c12"]["heat"].items() if k.startswith(f"{g}|all|h3|") and v["n"] >= MIN_N}
        if b and b.get("oos") and heat:
            kb = max(heat, key=lambda k: heat[k]["net"])
            kw = min(heat, key=lambda k: heat[k]["net"])
            L.append(f"{g}: IS-chosen cell d{b['delta']} {b['bucket']} earns {b['oos']['net']:+.3f}R OOS "
                     f"[{b['oos']['lo']:+.3f},{b['oos']['hi']:+.3f}], n {b['oos']['n']:,}; best OOS cell (ex post) "
                     f"d{kb.split('|')[3]} {kb.split('|')[4]} {heat[kb]['net']:+.3f}R, worst d{kw.split('|')[3]} "
                     f"{kw.split('|')[4]} {heat[kw]['net']:+.3f}R (n {heat[kw]['n']:,}). "
                     f"OOS cells with net R > 0 and n>=100: {sum(v['net'] > 0 for v in heat.values())}/{len(heat)}.")
    parts = []
    for hid in ("C1", "C2", "C3:rmr_har", "C4:gamma_theta_rv"):
        sup = [g for g in GROUPS if V["C"].get(f"{hid}|{g}|all", {}).get("verdict") == "supported"]
        rev = [g for g in GROUPS if V["C"].get(f"{hid}|{g}|all", {}).get("verdict") == "reversed"]
        parts.append(f"{hid} supported in {', '.join(sup) or 'none'}" + (f", reversed in {', '.join(rev)}" if rev else ""))
    L.append("Contract hypotheses (sides pooled, h=3): " + "; ".join(parts) + ".")
    passes = [k for k, v in V["X"].items() if v["verdict"].startswith("B1 leg passes")]
    worse = [k for k, v in V["X"].items() if v["verdict"] == "worse than X0"]
    L.append(f"Exits: B1 leg passed by {passes or 'none'}; significantly worse than X0: {worse or 'none'}. "
             "No rule is adopted until it also beats X0 on surviving entries (§5).")
    for g in ("index", "stocks"):
        fl = (fz.get("c5") or {}).get(f"floor|{g}") or {}
        c3 = O["bleed"].get(f"OOS|{g}|all|h3") or {}
        c1 = O["bleed"].get(f"OOS|{g}|all|h1") or {}
        vf = fl.get("volume_floor")
        L.append(f"{g}: liquidity floor ~{vf if vf is None else round(vf):,} contracts (IS decile rule), spread ceiling {fl.get('spread_ceiling_pct')}% "
                 f"round trip; structural bleed of the std contract OOS {c1.get('net', float('nan')):+.3f}R after 1 day, "
                 f"{c3.get('net', float('nan')):+.3f}R after 3 ({c3.get('net_per_day', float('nan')):+.3f}R/day held); "
                 f"time decay explains {c3.get('theta_share_losers', float('nan')):.0%} of losers' losses at h=3.")
    return L


def write_md(out, path, logged, total_trials):
    """<= 110 lines from the chart JSON (works on the cleaned/loaded dict)."""
    I, O, V, fz = out["IS"], out["OOS"], out["verdicts"], out["frozen_IS"]
    m = out["meta"]
    L = []
    P = L.append

    def f(x, fmt="+.3f"):
        return "n/a" if x is None else format(x, fmt)

    def ci(c):
        return "n/a" if not c else f"{f(c.get('net', c.get('diff')))} [{f(c['lo'])}, {f(c['hi'])}]"

    def short(v):
        return {"supported": "SUPP", "not supported": "no", "anecdotal": "anec", "reversed": "REV",
                "partial: contrast only, not monotone": "contrast-only"}.get(v, v)

    P(f"# P4 - Contract selection (C1-C5) and exits (X0-X4)"
      f"{' - signals: ' + m['tag'] if m['tag'] else ' on B1 (unconditional)'}")
    P(f"Generated {m['generated'][:16]} by `{m['script']}`. Primary sample excludes ca_break holds, past-data-end, "
      f"settle-priced and unmarked exits; no winsorising (cap-at-+5R robustness in JSON `net_cap5`). Stock options NSE "
      f"only (BSE stock rows dropped: {m.get('dropped_bse_stock')}). CI = day-clustered bootstrap 95% ({m['n_boot']}). "
      f"IS 2019-23 / OOS 2024-26 (SENSEX IS 2024). Every selection made on IS and frozen "
      f"(`p4_contract_exits_frozen.json`) before OOS rows were loaded. h=3 unless stated. "
      f"Trial rows this run: {m['n_trial_rows_this_run']} (logged={logged}); ledger total {total_trials}.")
    P("")
    P("## Verdicts (sides pooled; CE/PE in JSON `verdicts.C`): verdict, OOS contrast [raw p]. SUPP = OOS CI excludes 0 "
      "in the expected direction and IS agrees; REV = significant opposite; C2 shows interior-minus-(7-10), p = max of both")
    P("| test | " + " | ".join(GROUPS) + " |")
    P("|---|" + "---|" * len(GROUPS))
    for lab, key in (("C1 d.70-d.15 + monotone", "C1"), ("C2 DTE 11-35 vs 7-10 & 36+", "C2"),
                     ("C3 RMR(HAR) T1-T3", "C3:rmr_har"), ("C3 RMR(rv20) T1-T3", "C3:rmr_rv20"),
                     ("C4 gamma/theta(rv) T3-T1", "C4:gamma_theta_rv"), ("C4 gamma/|theta| raw T3-T1", "C4:gamma_theta")):
        row = []
        for g in GROUPS:
            v = V["C"].get(f"{key}|{g}|all", {})
            d = v.get("oos_diff")
            if key == "C2" and v.get("detail"):
                d = v["detail"].get("7-10", {}).get("oos")
            row.append(f"{short(v.get('verdict', 'n/a'))} {f(d)} [{f(v.get('p_raw'), '.3f')}]")
        P(f"| {lab} | " + " | ".join(row) + " |")
    P("")
    P("## Delta x DTE heatmap, net mean R h=3 (bucket expiry): OOS (IS); * = OOS n < 100; gross in JSON")
    for g in ("index", "stocks"):
        P(f"| {g} | " + " | ".join(BUCKETS) + " |")
        P("|---|" + "---|" * len(BUCKETS))
        for dl in DELTAS:
            cells = []
            for b in BUCKETS:
                co, cI = O["c12"]["heat"].get(f"{g}|all|h3|{dl}|{b}"), I["c12"]["heat"].get(f"{g}|all|h3|{dl}|{b}")
                cells.append(f"{f(co and co['net'])} ({f(cI and cI['net'])})" + ("" if not co or co["n"] >= MIN_N else "*"))
            P(f"| d{dl} | " + " | ".join(cells) + " |")
    P("")
    P("## Best delta x DTE cell chosen on IS (max IS net R, n>=100), frozen, then OOS; worst OOS cell for contrast")
    P("| group | IS-chosen cell | IS net R | OOS net R [CI] | OOS n | OOS gross | worst OOS cell (n>=100) |")
    P("|---|---|---|---|---|---|---|")
    for g, b in out["best_cell_frozen_oos"].items():
        o = b.get("oos")
        cand = [(v["net"], k) for k, v in O["c12"]["heat"].items() if k.startswith(f"{g}|all|h3|") and v["n"] >= MIN_N]
        w = min(cand) if cand else None
        wl = (f"d{w[1].split('|')[3]} {w[1].split('|')[4]}: {w[0]:+.3f} (n {O['c12']['heat'][w[1]]['n']:,})"
              if w else "n/a")
        P(f"| {g} | d{b['delta']} {b['bucket']} | {f(b['is_net'])} | {ci(o)} | {o['n'] if o else 0:,} | "
          f"{f(o and o['gross'])} | {wl} |")
    P("")
    P("## C1 (std expiry, by |delta|) and C2 (delta .40, by DTE): OOS net mean R at h=1/3/5 (h=3 non-overlap sample)")
    P("| group | " + " | ".join(f"d{x}" for x in DELTAS) + " | " + " | ".join(BUCKETS) + " |")
    P("|---|" + "---|" * (len(DELTAS) + len(BUCKETS)))
    for g in GROUPS:
        c = []
        for src, keys in (("c1", DELTAS), ("c2", BUCKETS)):
            for k in keys:
                vals = [O["c12"][src].get(f"{g}|all|h{h}|{k}", {}).get("net") for h in HS_C12]
                no = O["c12"][src].get(f"{g}|all|h3|{k}", {}).get("net_nonoverlap")
                c.append("/".join(f(v, "+.2f") for v in vals) + f" ({f(no, '+.2f')})")
        P(f"| {g} | " + " | ".join(c) + " |")
    P("")
    P("## C3 / C4 terciles (std contract, IS cut-points): OOS net mean R T1/T2/T3 and contrast [CI]")
    P("| group | RMR(HAR) T1/T2/T3 | low-high RMR [CI] | gamma/theta(rv) T1/T2/T3 | high-low [CI] |")
    P("|---|---|---|---|---|")
    for g in GROUPS:
        r = []
        for hid, col, lab in (("C3", "rmr_har", "T1-T3"), ("C4", "gamma_theta_rv", "T3-T1")):
            t = [O["c34"].get(f"{hid}|{col}|{g}|all|T{k}", {}).get("net") for k in (1, 2, 3)]
            r += ["/".join(f(x) for x in t), ci(O["c34"].get(f"{hid}|{col}|{g}|all|{lab}"))]
        P(f"| {g} | " + " | ".join(r) + " |")
    P("")
    P("## C5 liquidity (std contract, h=3): floor implied on IS (bucket net R within 0.03R of plateau), OOS check")
    P("| group | volume floor, contracts (decile) | spread ceiling, RT % prem | OOS net R <= / > ceiling | "
      "OOS net R by RT spread bucket from " + "/".join(str(e) for e in SPREAD_EDGES[:-1]) + "% (. = n<100) |")
    P("|---|---|---|---|---|")
    for g in GROUPS:
        fl = (fz.get("c5") or {}).get(f"floor|{g}") or {}
        sc = fl.get("spread_ceiling_pct")
        rows = O["c5"]["spread"].get(g, [])

        def wmean(sel):
            n = sum(r["n"] for r in sel)
            return sum(r["net"] * r["n"] for r in sel) / n if n else None
        lo = wmean([r for r in rows if sc is not None and r["hi_edge"] <= sc])
        hi = wmean([r for r in rows if sc is not None and r["hi_edge"] > sc])
        byedge = {r["lo_edge"]: r for r in rows}
        bk = " ".join(f"{byedge[e]['net']:+.2f}" if e in byedge and byedge[e]["n"] >= MIN_N else "."
                      for e in SPREAD_EDGES[:-1])
        P(f"| {g} | {f(fl.get('volume_floor'), ',.0f')} (d{fl.get('volume_floor_decile')}) | <= {sc} | "
          f"{f(lo)} / {f(hi)} | {bk} |")
    P("## Exits vs X0 (std contract, sides pooled): OOS X0 net R; OOS diff vs X0 [CI] of the IS-chosen cell. "
      "Bold = B1 leg of §5 passes (OOS CI > 0, IS same sign, grid neighbours > 0); adoption still needs survivors")
    P("| group h_max | X0 net | X1 (IS cell) | X2 (IS cell) | X3 trail | X4 fast | best X0 h on IS |")
    P("|---|---|---|---|---|---|---|")
    for g in GROUPS:
        for hm in bt.H_MAX:
            x0 = O["exits"]["x0"].get(f"{g}|all|h{hm}", {}).get("net")
            if x0 is None:
                continue
            r = []
            for xid in ("X1", "X2", "X3", "X4"):
                v = V["X"].get(f"{xid}|{g}|h{hm}", {})
                d, c = v.get("oos_diff"), v.get("oos_ci") or [None, None]
                nm = (v.get("chosen_on_IS") or "").replace("x1_", "").replace("x2_", "") if xid in ("X1", "X2") else ""
                mark = "**" if v.get("verdict", "").startswith("B1 leg passes") else ""
                r.append(f"{mark}{nm} {f(d)} [{f(c[0])},{f(c[1])}]{mark}".strip())
            P(f"| {g} {hm} | {f(x0)} | " + " | ".join(r) + f" | h{(fz.get('exits') or {}).get(f'{g}|best_h')} |")
    P("")
    P("## Buyer metrics OOS, h_max=3: X0 vs IS-chosen X1 vs X4")
    P("| group rule | expectancy R | win | avg win / avg loss R | PF | theta share of losses | mean days held | P(+50% touch by d3) |")
    P("|---|---|---|---|---|---|---|---|")
    for g in ("index", "stocks"):
        ttt = O["exits"]["ttt"].get(f"{g}|all|h3|0.5", {})
        hit = 1 - ttt["never"] if ttt.get("never") is not None else None
        x1 = fz["exits"].get(f"{g}|h3|X1")
        for lab, c in (("X0", O["exits"]["x0"].get(f"{g}|all|h3")),
                       (f"X1 {x1}", O["exits"]["rules"].get(f"{g}|all|h3|{x1}")),
                       ("X4", O["exits"]["rules"].get(f"{g}|all|h3|x4_fast"))):
            if not c:
                continue
            mt = c["metrics"]
            tit = mt["time_in_trade"]
            tit = [tit[k] for k in sorted(tit, key=int)] if isinstance(tit, dict) else tit
            days = sum((i + 1) * v for i, v in enumerate(tit))
            P(f"| {g} {lab} | {f(c['net'])} | {f(mt['win_rate'], '.0%')} | {f(mt['avg_win_R'], '+.2f')} / "
              f"{f(mt['avg_loss_R'], '+.2f')} | {f(mt['profit_factor'], '.2f')} | "
              f"{f(mt.get('theta_share_losers'), '.0%')} | {days:.2f} | {f(hit, '.0%')} |")
    P("")
    P("## Theta bleed: std contract OOS net (gross) mean R by days held; net R per day at h=3; theta share of losses h=3")
    P("| group | DTE | h=1 | h=2 | h=3 | h=5 | net/day h3 | theta share |")
    P("|---|---|---|---|---|---|---|---|")
    for g in GROUPS:
        for b in (["all"] + BUCKETS if g in ("index", "stocks") else ["all"]):
            cs = [O["bleed"].get(f"OOS|{g}|{b}|h{h}") for h in bt.H_LIST]
            if not any(cs):
                continue
            c3 = cs[2] or {}
            P(f"| {g} | {b} | " + " | ".join("n/a" if not c else f"{c['net']:+.3f} ({c['gross']:+.3f})" for c in cs)
              + f" | {f(c3.get('net_per_day'))} | {f(c3.get('theta_share_losers'), '.0%')} |")
    P("## Conclusions")
    for line in out.get("conclusions", []):
        P(f"- {line}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    assert len(L) <= 110, len(L)
    return len(L)


# ----------------------------------------------------------------------------- main
def run_sample(smp, bt_dir, sig, cal_idx, frozen, tag, years=None):
    A, S = load(bt_dir, smp, sig, years)
    S = add_features(S, cal_idx)
    print(f"[{smp}] loaded {len(A):,} grid rows, {len(S):,} std rows; HAR coverage {S['rmr_har'].notna().mean():.1%}")
    R = {"c12": run_c12(A, smp, cal_idx, frozen, tag)}
    del A
    R["c34"] = run_c34(S, smp, cal_idx, frozen, tag)
    R["c5"] = run_c5(S, smp, frozen, tag)
    R["exits"] = run_exits(S, smp, frozen, tag)
    R["bleed"] = theta_bleed(S, smp)
    R["n_std"] = int(len(S))
    return R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--signals", default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--years", nargs="*", default=None, help="dev only: restrict year files (never with logging)")
    ap.add_argument("--md-only", action="store_true", help="rebuild the .md from an existing .json (no runs, no log)")
    a = ap.parse_args()
    if a.md_only:
        suf = f"_{a.tag}" if a.tag else ""
        with open(os.path.join(OUT, f"p4_contract_exits{suf}.json"), encoding="utf-8") as f:
            out = json.load(f)
        for k, v in out["verdicts"]["C"].items():  # monotone-tolerance fix (rho == 0.9 in float) without re-running
            if k.startswith("C1|") and v["verdict"].startswith("partial") and (v.get("spearman_oos") or -1) >= 0.9 - 1e-9:
                v["verdict"], v["monotone_oos"] = "supported", True
        out["conclusions"] = conclude(out)
        with open(os.path.join(OUT, f"p4_contract_exits{suf}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f)
        n = write_md(out, os.path.join(OUT, f"p4_contract_exits{suf}.md"), logged=None, total_trials=trials.n_trials())
        print(f"md rebuilt ({n} lines)")
        return
    if a.years and not a.no_log:
        raise SystemExit("--years is a dev option; use it with --no-log")
    if a.years and not a.tag:
        a.tag = "dev"
    sig = read_signals(a.signals) if a.signals else None
    tag = a.tag or (os.path.splitext(os.path.basename(a.signals))[0] if a.signals else "")
    vtag = f"sig:{tag}|" if tag else ""
    suf = f"_{tag}" if tag else ""
    cal = pd.read_parquet(os.path.join(ROOT, "data", "ref", "calendar.parquet"))
    cal_idx = pd.DatetimeIndex(sorted(pd.to_datetime(cal.loc[cal["trading_day"].astype(bool), "date"])))

    frozen = {"created": dt.datetime.now().isoformat(timespec="seconds"), "signals": a.signals}
    R_is = run_sample("IS", a.bt, sig, cal_idx, frozen, vtag, a.years)
    # ---- freeze before OOS is loaded
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"p4_contract_exits_frozen{suf}.json"), "w", encoding="utf-8") as f:
        json.dump(frozen, f, indent=1, default=float)
    frozen_snapshot = json.dumps(frozen, sort_keys=True, default=float)
    print(f"[freeze] IS selections written: best cells {frozen.get('best_cell')}")
    R_oos = run_sample("OOS", a.bt, sig, cal_idx, frozen, vtag, a.years)
    assert json.dumps(frozen, sort_keys=True, default=float) == frozen_snapshot, "OOS run modified IS selections"

    verd = {"C": c_verdicts(R_is, R_oos), "X": x_verdicts(R_is, R_oos, frozen)}
    # frozen best delta x DTE cell on OOS
    best_oos = {}
    for g, b in frozen.get("best_cell", {}).items():
        best_oos[g] = {**b, "oos": R_oos["c12"]["heat"].get(f"{g}|all|h3|{b['delta']}|{b['bucket']}")}
    out = {"meta": {"generated": dt.datetime.now().isoformat(timespec="seconds"), "script": SCRIPT,
                    "signals": a.signals, "tag": tag, "groups": GROUPS, "deltas": DELTAS, "buckets": BUCKETS,
                    "r_bins": R_bins_json(), "spread_edges_pct": SPREAD_EDGES, "n_boot": NB,
                    "n_trial_rows_this_run": len(TRIALS),
                    "dropped_bse_stock": dict(DROPPED)},
           "frozen_IS": frozen, "best_cell_frozen_oos": best_oos, "IS": R_is, "OOS": R_oos, "verdicts": verd}
    out = slim(clean(out))
    out["conclusions"] = conclude(out)
    with open(os.path.join(OUT, f"p4_contract_exits{suf}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, default=_js, allow_nan=False)
    if not a.no_log:
        for r in TRIALS:
            trials.log_trial(**r)
    nlines = write_md(out, os.path.join(OUT, f"p4_contract_exits{suf}.md"), logged=not a.no_log,
                      total_trials=trials.n_trials())
    print(f"trial rows this run: {len(TRIALS)} (logged={not a.no_log}); ledger rows now {trials.n_trials()}")
    print(f"wrote research/out/p4_contract_exits{suf}.json + .md ({nlines} lines)")


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return None if not np.isfinite(o) else float(f"{float(o):.4g}")
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


DROP_KEYS = {"gross_cap5", "n_nonoverlap", "avg_win_rs", "avg_loss_rs", "expectancy_rs"}


def slim(o, path=()):
    """Shrink the chart JSON (< ~500 KB): drop None/aux keys; CE/PE C1/C2 cells only at h=3; CE/PE X0 cells compact.
    Everything dropped here is still in the trial log rows."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if v is None or k in DROP_KEYS:
                continue
            side_split = isinstance(v, dict) and ("|CE|" in k or "|PE|" in k)
            if side_split and path and path[0] == "IS" and path[-1] in ("heat", "c1", "c2", "rules", "ttt", "x0"):
                continue  # IS side splits: trial log only (IS agreement is carried in verdicts)
            if side_split and path and path[-1] == "ttt":
                continue
            if side_split and path and path[-1] == "rules":
                v = {"n": v.get("n"), "net": v.get("net"), "vs_x0": v.get("vs_x0")}
            if len(path) >= 2 and path[-1] in ("c1", "c2") and "|all|" not in k and "|h3|" not in k:
                continue
            if len(path) >= 2 and path[-1] == "x0" and "|all|" not in k and isinstance(v, dict):
                v = {kk: v.get(kk) for kk in ("n", "net", "lo", "hi", "gross")}
            if len(path) >= 2 and path[-1] in ("heat", "c34") and "|all|" not in k and isinstance(v, dict):
                v = {kk: v.get(kk) for kk in ("n", "net", "gross", "diff", "lo", "hi", "p")}
            if k == "vs_x0" and isinstance(v, dict):
                v = {kk: v.get(kk) for kk in ("diff", "lo", "hi", "p")}
            if k in ("time_in_trade", "why") and isinstance(v, dict):
                v = [v[x] for x in sorted(v, key=int)]
            if k == "metrics" and isinstance(v, dict):
                v = {kk: vv for kk, vv in v.items() if kk not in ("n", "expectancy_R", "net_cap5")}
            out[k] = slim(v, path + (k,))
        return out
    if isinstance(o, list):
        return [slim(v, path) for v in o]
    return o


def R_bins_json():
    return [b if b < 1e8 else None for b in R_BINS]


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (pd.Timestamp, np.datetime64)):
        return str(o)
    return str(o)


if __name__ == "__main__":
    main()
