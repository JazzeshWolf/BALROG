"""P4: registered volatility-pricing (V1-V5), dealer-positioning (D1-D4) and event (E1-E3) hypotheses.

Protocol: research/02_hypotheses.md §0 + lead rulings (see RULINGS below). Swing, standard contract, real bhavcopy.
  python -I research/scripts/p4_run_vol_dealer_events.py [--log] [--relog] [--boot 2000]
Writes research/out/p4_vol_dealer_events.{json,md}. With --log appends every run to research/trials_log.csv
(refuses if rows from this script already exist unless --relog).

Order is enforced: Phase A sees only IS rows (grid choice, plateau step sizes) and writes the frozen choice to the JSON;
Phase B (OOS) runs after that file is on disk.
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

from research.lib import backtest as bt
from research.lib import trials

SCRIPT = "research/scripts/p4_run_vol_dealer_events.py"
OUT = os.path.join(ROOT, "research", "out")
JSON_PATH = os.path.join(OUT, "p4_vol_dealer_events.json")
MD_PATH = os.path.join(OUT, "p4_vol_dealer_events.md")
HS = (1, 2, 3, 5)
HV = 9  # pseudo-hold: per-row hold (D4)
CAP = 5.0
GROUPS = ["NIFTY", "BANKNIFTY", "SENSEX", "INDEX", "STOCKS"]
JUDGED = ["INDEX", "STOCKS"]
RULINGS = ("primary excludes ca_break-in-hold, past-data-end, settle-priced and unmarked exits; no winsorising (cap +5 gross "
           "as robustness); one open position per symbol x hypothesis x side (re-entry allowed at the prior exit close); "
           "D2 primary = cross of previous day's wall; E: one event per symbol-quarter, unknown time = after close; "
           "BANKNIFTY spread p90; grid cell chosen on IS (max IS net mean R at the registered hold, n>=30) and frozen.")
HIST_EDGES = [-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0, 2.0, 5.0, np.inf]


def gmask(grp, g):
    grp = np.asarray(grp).astype(str)
    if g == "INDEX":
        return grp != "stocks"
    if g == "STOCKS":
        return grp == "stocks"
    return grp == g


# ----------------------------------------------------------------------------- data
def load_units(bt_dir):
    cols = ["date", "exchange", "symbol", "cls", "grp", "sample", "opt_type", "premium", "lot", "entry_fill", "theta",
            "c3_buy_leg", "half_spread", "half_spread_2x", "p1_o"]
    for h in HS:
        cols += [f"{c}_h{h}" for c in ("exit_date", "gross_R", "gross_rs", "net_rs", "net_R", "net_R2x", "net_R_open1",
                                       "settled", "incomplete", "ca_break", "cal_days")]
    fs = sorted(glob.glob(os.path.join(bt_dir, "trades", "year=*.parquet")))
    t = pd.concat([pd.read_parquet(f, columns=cols, filters=[("is_std", "==", True)]) for f in fs], ignore_index=True)
    t["exchange"] = t["exchange"].astype(str).str.lower()
    bse_stk = (t["cls"].astype(str) == "stock") & (t["exchange"] != "nse")
    n_bse_stk = int(bse_stk.sum())
    t = t[~bse_stk].reset_index(drop=True)  # stocks: NSE only (BSE stock options duplicate NSE symbols)
    dup = int(t.duplicated(["date", "symbol", "opt_type"]).sum())
    assert dup == 0, f"duplicated std trades: {dup}"
    t["opt_type"] = t["opt_type"].astype(str)
    for c in ("symbol", "grp", "sample", "exchange", "cls"):
        t[c] = t[c].astype(str)
    lot = t["lot"].to_numpy(float)
    bl = t["c3_buy_leg"].to_numpy(float)
    prem = t["premium"].to_numpy(float)
    t["den"] = t["entry_fill"] * lot + bl
    t["den2x"] = (prem + t["half_spread_2x"]) * lot + bl
    t["deno1"] = (t["p1_o"].fillna(t["premium"]) + t["half_spread"]) * lot + bl
    t["gden"] = prem * lot
    t["theta_rs"] = t["theta"] * lot
    for h in HS:
        t[f"valid_h{h}"] = (t[f"net_R_h{h}"].notna() & ~t[f"ca_break_h{h}"].astype(bool)
                            & ~t[f"incomplete_h{h}"].astype(bool) & ~t[f"settled_h{h}"].astype(bool)
                            & t[f"exit_date_h{h}"].notna())
        t[f"rs2x_h{h}"] = t[f"net_R2x_h{h}"] * t["den2x"]
        t[f"rso1_h{h}"] = t[f"net_R_open1_h{h}"] * t["deno1"]
        t[f"rscap_h{h}"] = t[f"net_rs_h{h}"] - np.maximum(t[f"gross_R_h{h}"] - CAP, 0) * t["gden"]
    keys = ["date", "exchange", "symbol", "cls", "grp", "sample"]
    sumc = ["den", "den2x", "deno1", "gden", "theta_rs"] + [f"{c}_h{h}" for h in HS for c in
                                                             ("gross_rs", "net_rs", "rs2x", "rso1", "rscap")]
    # singles
    s = t[keys + ["opt_type"] + sumc + [f"valid_h{h}" for h in HS] + [f"exit_date_h{h}" for h in HS]
          + [f"cal_days_h{h}" for h in HS] + [f"net_R_open1_h{h}" for h in HS]].rename(columns={"opt_type": "side"})
    # strangles: both std legs, same day
    ce = s[s.side == "CE"].drop(columns="side").set_index(keys)
    pe = s[s.side == "PE"].drop(columns="side").set_index(keys)
    j = ce.join(pe, how="inner", lsuffix="_c", rsuffix="_p")
    st = pd.DataFrame(index=j.index)
    for c in sumc:
        st[c] = j[c + "_c"] + j[c + "_p"]
    for h in HS:
        st[f"valid_h{h}"] = j[f"valid_h{h}_c"] & j[f"valid_h{h}_p"]
        st[f"exit_date_h{h}"] = np.maximum(j[f"exit_date_h{h}_c"], j[f"exit_date_h{h}_p"])
        st[f"cal_days_h{h}"] = np.maximum(j[f"cal_days_h{h}_c"], j[f"cal_days_h{h}_p"])
    st = st.reset_index()
    st["side"] = "STR"
    s = s.drop(columns=[f"net_R_open1_h{h}" for h in HS])
    u = pd.concat([s, st], ignore_index=True)
    for h in HS:
        u[f"R_h{h}"] = u[f"net_rs_h{h}"] / u["den"]
        u[f"gR_h{h}"] = u[f"gross_rs_h{h}"] / u["gden"]
        u[f"R2x_h{h}"] = u[f"rs2x_h{h}"] / u["den2x"]
        u[f"Ro1_h{h}"] = u[f"rso1_h{h}"] / u["deno1"]
        u[f"Rcap_h{h}"] = u[f"rscap_h{h}"] / u["den"]
        u[f"rs_h{h}"] = u[f"net_rs_h{h}"]
    keep = ["date", "exchange", "symbol", "cls", "grp", "sample", "side", "theta_rs"] + [
        f"{c}_h{h}" for h in HS for c in ("R", "gR", "R2x", "Ro1", "Rcap", "rs", "valid", "exit_date", "cal_days")]
    return u[keep].copy(), len(t), len(st), fs, n_bse_stk


FEAT = ["vrp_ratio_h5", "iv_pctile_252", "term_spread", "vix_pctile_252", "vix_chg5", "gex_pctile_252",
        "cross_call_wall_prev", "cross_put_wall_prev", "broke_call_wall", "broke_put_wall", "call_wall_dist_pct",
        "put_wall_dist_pct", "call_wall_oi_chg", "put_wall_oi_chg", "max_oi_dist_sigma", "near_dte_trd",
        "results_today_tradable", "results_time_unknown", "days_to_results", "results_move_sigma"]


def load_features(path):
    f = pd.read_parquet(path, columns=["date", "symbol"] + FEAT)
    f["symbol"] = f["symbol"].astype(str)
    assert not f.duplicated(["date", "symbol"]).any()
    # event attribution: each pre-results row -> its reaction day (next results_today_tradable row, strictly later)
    re = f.loc[f["results_today_tradable"].eq(True), ["date", "symbol", "results_time_unknown"]].rename(
        columns={"date": "ev_date", "results_time_unknown": "ev_unknown"}).sort_values("ev_date")
    f = f.sort_values("date")
    f = pd.merge_asof(f, re, left_on="date", right_on="ev_date", by="symbol", direction="forward",
                      allow_exact_matches=False)
    rx = f["results_today_tradable"].eq(True)  # reaction day itself (E3)
    f["ev3_date"] = f["date"].where(rx)
    f["ev3_unknown"] = f["results_time_unknown"].where(rx)
    return f


# ----------------------------------------------------------------------------- selection
def pos_rule(df, h):
    if len(df) == 0:
        return df
    df = df.sort_values(["symbol", "side", "date"], kind="mergesort")
    key = pd.factorize(df["symbol"] + "|" + df["side"])[0]
    d = df["date"].to_numpy("datetime64[D]").astype(np.int64)
    e = pd.to_datetime(df[f"exit_date_h{h}"]).to_numpy("datetime64[D]").astype(np.int64)
    keep = np.zeros(len(df), bool)
    last_k, last_e = -1, -10 ** 12
    for i in range(len(df)):
        if key[i] != last_k:
            last_k, last_e = key[i], -10 ** 12
        if d[i] >= last_e:  # prior position closed at (or before) this close
            keep[i] = True
            last_e = e[i]
    return df[keep]


def with_hv(J, hvec):
    """Add *_h9 columns = the per-row hold hvec (values in HS or 0 = no trade)."""
    J = J.copy()
    hvec = np.asarray(hvec)
    for c in ("R", "gR", "R2x", "Ro1", "Rcap", "rs", "valid", "exit_date", "cal_days"):
        if c == "valid":
            out = np.zeros(len(J), bool)
        elif c == "exit_date":
            out = np.full(len(J), np.datetime64("NaT"), dtype="datetime64[ns]")
        else:
            out = np.full(len(J), np.nan)
        for h in HS:
            m = hvec == h
            out[m] = J[f"{c}_h{h}"].to_numpy()[m]
        J[f"{c}_h{HV}"] = out
    return J


def matched_base(S, B, h):
    """B1 R array/clusters reweighted (row duplication) to the signal's side mix (directional hypotheses)."""
    R = B[f"R_h{h}"].to_numpy(float)
    cl = B["date"].to_numpy()
    sides_s = S["side"].value_counts(normalize=True)
    if len(S) == 0 or "STR" in sides_s.index:
        return R, cl
    sides_b = B["side"].value_counts(normalize=True)
    w = {k: sides_s.get(k, 0) / sides_b[k] for k in sides_b.index}
    mx = max(w.values())
    rr, cc = [], []
    for k, v in w.items():
        m = (B["side"] == k).to_numpy()
        for _ in range(int(round(10 * v / mx))):
            rr.append(R[m]); cc.append(cl[m])
    return np.concatenate(rr), np.concatenate(cc)


def mean_or_nan(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return float(x.mean()) if len(x) else np.nan


def quick(S, B, h, boot, seed=0):
    """Cell statistics used for every logged run."""
    n = len(S)
    out = {"n": n, "n_days": int(S["date"].nunique()) if n else 0}
    if n == 0:
        return out | {"net_mean_R": np.nan, "gross_mean_R": np.nan, "ci_lo": np.nan, "ci_hi": np.nan, "net_2x": np.nan,
                      "open1": np.nan, "cap5": np.nan, "b1_mean": np.nan, "b1_2x": np.nan, "diff": np.nan, "diff_lo": np.nan, "diff_hi": np.nan, "p": np.nan}
    ci = bt.cluster_bootstrap_ci(S[f"R_h{h}"].to_numpy(), S["date"].to_numpy(), n_boot=boot, seed=seed)
    br, bc = matched_base(S, B, h)
    d = bt.diff_vs_baseline(S[f"R_h{h}"].to_numpy(), S["date"].to_numpy(), br, bc, n_boot=boot, seed=seed)
    return out | {"net_mean_R": float(ci["mean"]), "gross_mean_R": mean_or_nan(S[f"gR_h{h}"]), "ci_lo": ci["lo"],
                  "ci_hi": ci["hi"], "net_2x": mean_or_nan(S[f"R2x_h{h}"]), "open1": mean_or_nan(S[f"Ro1_h{h}"]),
                  "cap5": mean_or_nan(S[f"Rcap_h{h}"]), "b1_mean": mean_or_nan(br),
                  "b1_2x": mean_or_nan(B[f"R2x_h{h}"]) if len(B) else np.nan,
                  "diff": float(d["diff"]), "diff_lo": d["lo"], "diff_hi": d["hi"], "p": d["p"]}


def full(S, h, boot, seed=0):
    """Chart-level statistics of one trade set (signal or B1)."""
    if len(S) == 0:
        return {"n": 0, "n_days": 0}
    R, rs = S[f"R_h{h}"].to_numpy(float), S[f"rs_h{h}"].to_numpy(float)
    dates = S["date"].to_numpy()
    ci = bt.cluster_bootstrap_ci(R, dates, n_boot=boot, seed=seed)
    gci = bt.cluster_bootstrap_ci(S[f"gR_h{h}"].to_numpy(), dates, n_boot=boot, seed=seed)
    bm = bt.buyer_metrics(rs, R, dates, S["theta_rs"].to_numpy(float), np.ones(len(S)),
                          S[f"cal_days_h{h}"].to_numpy(float))
    mon = pd.Series(rs, index=pd.to_datetime(dates)).groupby(pd.to_datetime(dates).to_period("M")).sum().cumsum()
    hist = np.histogram(np.clip(R[~np.isnan(R)], -1.0, 1e9), bins=HIST_EDGES)[0]
    yr = S.assign(y=pd.to_datetime(S["date"]).dt.year).groupby("y")[f"R_h{h}"].agg(["mean", "count"])
    vt = pd.cut(S["vix_pctile_252"], [-1, 100 / 3, 200 / 3, 101], labels=["low", "mid", "high"])
    vx = S.groupby(vt, observed=False)[f"R_h{h}"].agg(["mean", "count"])
    return {"n": int(len(S)), "n_days": int(S["date"].nunique()), "win_rate": bm["win_rate"],
            "avg_win_R": bm["avg_win_R"], "avg_loss_R": bm["avg_loss_R"],
            "gross_mean_R": float(gci["mean"]), "gross_ci": [gci["lo"], gci["hi"]],
            "net_mean_R": float(ci["mean"]), "net_ci": [ci["lo"], ci["hi"]],
            "expectancy_rs_per_lot": bm["expectancy_rs"], "profit_factor": bm["profit_factor"],
            "max_dd_rs": bm["max_dd_rs"], "longest_losing_streak": bm["longest_losing_streak"],
            "theta_share_losers": bm.get("theta_share_losers"),
            "net_mean_R_2x": mean_or_nan(S[f"R2x_h{h}"]), "net_mean_R_open1": mean_or_nan(S[f"Ro1_h{h}"]),
            "net_mean_R_cap5": mean_or_nan(S[f"Rcap_h{h}"]),
            "equity_monthly": [[str(k), float(v)] for k, v in mon.items()],
            "hist": {"edges": [e if np.isfinite(e) else "inf" for e in HIST_EDGES], "counts": hist.tolist()},
            "by_year": {str(k): {"net_mean_R": float(r["mean"]), "n": int(r["count"])} for k, r in yr.iterrows()},
            "by_vix_tercile": {str(k): {"net_mean_R": float(r["mean"]) if r["count"] else None, "n": int(r["count"])}
                               for k, r in vx.iterrows()}}


# ----------------------------------------------------------------------------- hypotheses
def sig_V1(J, p):
    return (J["side"] == "STR") & (J["vrp_ratio_h5"] < p["c"])


def sig_V2(J, p):
    return (J["side"] == "STR") & (J["iv_pctile_252"] < p["c"])


def sig_V3(J, p):
    return (J["side"] == "STR") & (J["term_spread"] > p["c"])


def sig_V4(J, p):
    return (J["side"] == "STR") & (J["vix_pctile_252"] < p["t"]) & (J["vix_chg5"] > p["c"])


def sig_D1(J, p):
    return (J["side"] == "STR") & (J["gex_pctile_252"] < p["c"])


def sig_D2(J, p):
    c, q = ("broke_call_wall", "broke_put_wall") if p.get("moving") else ("cross_call_wall_prev", "cross_put_wall_prev")
    return ((J["side"] == "CE") & J[c].eq(True)) | ((J["side"] == "PE") & J[q].eq(True))


def sig_D3(J, p):
    ce = (J["side"] == "CE") & (J["call_wall_dist_pct"] > 0) & (J["call_wall_dist_pct"] <= p["prox"]) & (
        J["call_wall_oi_chg"] <= -p["c"] / 100)
    pe = (J["side"] == "PE") & (J["put_wall_dist_pct"] < 0) & (J["put_wall_dist_pct"] >= -p["prox"]) & (
        J["put_wall_oi_chg"] <= -p["c"] / 100)
    return ce | pe


def d4_hold(J, dte_max):
    n = J["near_dte_trd"].to_numpy(float) - 1
    h = np.select([n >= 5, n == 4, n == 3, n == 2, n == 1], [5, 3, 3, 2, 1], 0)
    h[J["near_dte_trd"].to_numpy(float) > dte_max] = 0
    h[np.isnan(n)] = 0
    return h


def sig_D4(J, p):
    return ((J["side"] == "STR") & (J["gex_pctile_252"] >= 100 - p["q"]) & (J["max_oi_dist_sigma"].abs() <= p["k"])
            & (J["near_dte_trd"] <= p["dte"]) & (J["near_dte_trd"] >= 2))


def sig_E1(J, p):
    m = (J["side"] == "STR") & (J["days_to_results"] == 0) & J["ev_date"].notna()
    return m & (~J["ev_unknown"].eq(True) if p.get("known_only") else True)


def sig_E2(J, p):
    m = (J["side"] == "STR") & (J["days_to_results"] == 5) & J["ev_date"].notna()
    return m & (~J["ev_unknown"].eq(True) if p.get("known_only") else True)


def sig_E3(J, p):
    ms = J["results_move_sigma"]
    m = J["ev3_date"].notna() & (((J["side"] == "CE") & (ms > p["c"])) | ((J["side"] == "PE") & (ms < -p["c"])))
    return m & (~J["ev3_unknown"].eq(True) if p.get("known_only") else True)


# id: (signal fn, kind STR/DIR, hold, veto, grid key, grid, central params, classes, base extra filter)
HYP = {
    "V1": dict(fn=sig_V1, kind="STR", h=5, veto=False, key="c", grid=[0.80, 0.90, 1.00], p={}),
    "V2": dict(fn=sig_V2, kind="STR", h=5, veto=False, key="c", grid=[10, 20, 30], p={}),
    "V3": dict(fn=sig_V3, kind="STR", h=3, veto=True, key=None, grid=None, p={"c": 0.0}),
    "V4": dict(fn=sig_V4, kind="STR", h=5, veto=False, key=None, grid=None, p={"t": 100 / 3, "c": 0.0}, index_only=True),
    "V5": dict(fn=None, kind="STR", h=5, veto=False, key=None, grid=None, p={}),
    "D1": dict(fn=sig_D1, kind="STR", h=3, veto=False, key="c", grid=[10, 20, 30], p={}),
    "D2": dict(fn=sig_D2, kind="DIR", h=2, veto=False, key=None, grid=None, p={}),
    "D3": dict(fn=sig_D3, kind="DIR", h=3, veto=False, key="c", grid=[5, 10, 15], p={"prox": 1.0}),
    "D4": dict(fn=sig_D4, kind="STR", h=HV, veto=True, key=None, grid=None, p={"q": 20, "k": 0.5, "dte": 5}),
    "E1": dict(fn=sig_E1, kind="STR", h=1, veto=True, key=None, grid=None, p={}, stock_only=True, event="ev_date"),
    "E2": dict(fn=sig_E2, kind="STR", h=5, veto=False, key=None, grid=None, p={}, stock_only=True, event="ev_date"),
    "E3": dict(fn=sig_E3, kind="DIR", h=3, veto=False, key=None, grid=None, p={"c": 2.0}, stock_only=True,
               event="ev3_date"),
}


def perturbations(hid, p, step):
    """±20% of each threshold (percentile tails: ±20% of tail width; zero thresholds: ±20% of IS median |feature|)."""
    out = []
    if hid in ("V1", "V2", "D1", "E3"):
        out = [{"c": p["c"] * 0.8}, {"c": p["c"] * 1.2}]
    elif hid == "V3":
        out = [{"c": -step["term_spread"]}, {"c": step["term_spread"]}]
    elif hid == "V4":
        out = [{"t": p["t"] * 0.8}, {"t": p["t"] * 1.2}, {"c": -step["vix_chg5"]}, {"c": step["vix_chg5"]}]
    elif hid == "D3":
        out = [{"c": p["c"] * 0.8}, {"c": p["c"] * 1.2}, {"prox": p["prox"] * 0.8}, {"prox": p["prox"] * 1.2}]
    elif hid == "D4":
        out = [{"q": 16}, {"q": 24}, {"k": 0.4}, {"k": 0.6}, {"dte": 4}, {"dte": 6}]
    return [{**p, **o} for o in out]


class Runner:
    def __init__(self, J, boot):
        self.J = J
        self.boot = boot

    def subset(self, g):
        return self.J[gmask(self.J["grp"], g)]

    def trades(self, hid, p, g, h=None):
        H = HYP[hid]
        J = self.subset(g)
        hh = H["h"] if h is None else h
        if hid == "D4" and hh == HV:
            J = with_hv(J, d4_hold(J, p["dte"]))
        if hid == "V5":
            m = J["side"] == "STR"
        else:
            m = H["fn"](J, p)
        S = J[m.to_numpy(bool) & J[f"valid_h{hh}"].to_numpy(bool)]
        if H.get("event"):  # one result per symbol-quarter
            q = pd.to_datetime(S[H["event"]]).dt.to_period("Q").astype(str)
            S = S.assign(_q=q.values).sort_values("date").drop_duplicates(["symbol", "_q"]).drop(columns="_q")
        if hid != "V5":
            S = pos_rule(S, hh)
        return S, hh

    def base(self, hid, g, h, p=None):
        H = HYP[hid]
        J = self.subset(g)
        if hid == "D4":
            ndt = J["near_dte_trd"]
            J = J[(ndt >= 2) & (ndt <= (p or H["p"])["dte"])]
            if h == HV:
                J = with_hv(J, d4_hold(J, (p or H["p"])["dte"]))
        sides = ["STR"] if H["kind"] == "STR" else ["CE", "PE"]
        return J[J["side"].isin(sides).to_numpy() & J[f"valid_h{h}"].to_numpy(bool)]

    def cell(self, hid, p, g, h=None, boot=None, seed=0):
        S, hh = self.trades(hid, p, g, h)
        B = self.base(hid, g, hh, p)
        return quick(S, B, hh, boot or self.boot, seed), S, B, hh


# ----------------------------------------------------------------------------- verdicts
def criteria(hid, isq, oos, plateau, veto):
    n_ok = oos["n"] >= 100 and oos["n_days"] >= 40
    if not veto:
        c1 = bool(oos["net_mean_R"] > 0 and oos["ci_lo"] > 0)
        c2 = bool(oos["diff"] > 0 and oos["p"] < 0.05)
        c3 = bool(np.sign(oos["net_mean_R"]) == np.sign(isq["net_mean_R"]) and isq["net_mean_R"] > 0
                  and oos["net_mean_R"] >= 0.5 * isq["net_mean_R"])
        c6 = bool(oos["net_2x"] >= 0)
    else:
        c1 = bool(oos["diff"] < 0 and oos["diff_hi"] < 0)
        c2 = bool(oos["diff"] < 0 and oos["p"] < 0.05)
        c3 = bool(isq["diff"] < 0 and oos["diff"] <= 0.5 * isq["diff"])
        c6 = bool(oos["net_2x"] - oos["b1_2x"] < 0)
    c = {"1": c1, "2": c2, "3": c3, "4": bool(plateau), "5": bool(n_ok), "6": c6}
    if not n_ok:
        v = "ANECDOTAL"
    elif all(c.values()):
        v = "VETO-PASS" if veto else "PASS"
    elif not veto and c2 and oos["net_mean_R"] <= 0:
        v = "LESS-BAD-NOT-EDGE"
    else:
        v = "FAIL"
    return c, v


def plateau_ok(central, perts, veto):
    if not perts:
        return True, "no threshold"
    if veto:
        ok = all(q["n"] > 0 and q["diff"] < 0 and q["diff"] <= 0.5 * central["diff"] for q in perts)
    else:
        ok = all(q["n"] > 0 and q["net_mean_R"] > 0 and q["net_mean_R"] >= 0.5 * central["net_mean_R"] for q in perts)
    return bool(ok), ""


def clean(o):
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o


def dump(obj):
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(clean(obj), f, indent=None, separators=(",", ":"))


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--features", default=os.path.join(ROOT, "data", "features", "daily.parquet"))
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--relog", action="store_true")
    ap.add_argument("--only", default="", help="comma list of hypothesis ids (debug; no logging allowed)")
    a = ap.parse_args()
    if a.log and not a.relog and os.path.exists(trials.LOG_PATH):
        prior = pd.read_csv(trials.LOG_PATH, usecols=["script"])
        if (prior["script"] == SCRIPT).any():
            sys.exit("trials_log already has rows from this script; use --relog to append another run")
    if a.only:
        assert not a.log, "--only is for debugging; log only full runs"
        for k in [k for k in HYP if k not in a.only.split(",")]:
            HYP.pop(k)
    U, n_legs, n_str, fs, n_bse = load_units(a.bt)
    F = load_features(a.features)
    J = U.merge(F, on=["date", "symbol"], how="left", validate="many_to_one")
    assert len(J) == len(U)
    print(f"dropped {n_bse:,} BSE stock-option std legs; units: {n_legs:,} std legs, {n_str:,} strangles; feature match {J['near_dte_trd'].notna().mean():.1%}")
    logs = []  # (row dict) appended at the very end

    def logrow(hid, variant, reg, smp, g, q, verdict, notes=""):
        logs.append(dict(id=hid, variant=variant, registered=reg, sample=smp, cls=g, style="swing", n_trades=q["n"],
                         n_days=q["n_days"], gross_mean_R=q.get("gross_mean_R"), net_mean_R=q.get("net_mean_R"),
                         ci_lo=q.get("ci_lo"), ci_hi=q.get("ci_hi"), p_vs_b1=q.get("p"),
                         net_mean_R_2x_spread=q.get("net_2x"), verdict=verdict, notes=notes, script=SCRIPT))

    def groups_for(hid):
        H = HYP[hid]
        if H.get("stock_only"):
            return ["STOCKS"]
        return GROUPS

    # ================= Phase A: IS only =================
    RA = Runner(J[J["sample"] == "IS"], a.boot)
    Jis = RA.J
    step = {g: {"term_spread": 0.2 * float(Jis.loc[gmask(Jis.grp, g) & (Jis.side == "STR"), "term_spread"].abs().median()),
                "vix_chg5": 0.2 * float(Jis.loc[gmask(Jis.grp, g) & (Jis.side == "STR"), "vix_chg5"].abs().median())}
            for g in JUDGED}
    frozen, gridIS = {}, {}
    for hid, H in HYP.items():
        frozen[hid] = {}
        for jg in JUDGED:
            if H.get("stock_only") and jg != "STOCKS":
                continue
            p = dict(H["p"])
            if H["grid"]:
                sc = {}
                for c in H["grid"]:
                    S, hh = RA.trades(hid, {**p, H["key"]: c}, jg)
                    sc[c] = (S[f"R_h{hh}"].mean() if len(S) >= 30 else -np.inf, len(S))
                best = max(H["grid"], key=lambda c: sc[c][0])
                if not np.isfinite(sc[best][0]):
                    best = H["grid"][1]
                p[H["key"]] = best
                gridIS[f"{hid}|{jg}"] = {str(k): {"net_mean_R_h_reg": v[0] if np.isfinite(v[0]) else None, "n": v[1]}
                                         for k, v in sc.items()}
            frozen[hid][jg] = {"params": p, "perturbations": perturbations(hid, p, step[jg])}
    meta = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "script": SCRIPT, "rulings": RULINGS,
            "inputs": {"trade_files": len(fs), "std_legs": n_legs, "strangles": n_str, "bse_stock_legs_dropped": n_bse},
            "frozen_choice": frozen, "frozen_is_grid_scores": gridIS, "plateau_steps_IS": step,
            "phase": "A (IS only; OOS not yet run)"}
    os.makedirs(OUT, exist_ok=True)
    dump(meta)  # frozen choice on disk BEFORE any OOS computation
    print("frozen:", {h: {g: v["params"] for g, v in d.items()} for h, d in frozen.items() if HYP[h]["grid"]})

    # ================= Phase B: IS + OOS evaluation with frozen params =================
    runners = {"IS": RA, "OOS": Runner(J[J["sample"] == "OOS"], a.boot)}
    res = {}
    for hid, H in HYP.items():
        res[hid] = {}
        for g in groups_for(hid):
            jg = "STOCKS" if g == "STOCKS" else "INDEX"
            if jg not in frozen[hid]:
                continue
            reg_cls = not (H.get("index_only") and g == "STOCKS")
            reg = "yes" if reg_cls else "no"
            p = frozen[hid][jg]["params"]
            perts = frozen[hid][jg]["perturbations"]
            entry = {"params": p, "registered": reg_cls}
            for smp, R in runners.items():
                q, S, B, hh = R.cell(hid, p, g, seed=1)
                e = {"stats": q, "signal": full(S, hh, a.boot, 1), "b1": full(B, hh, a.boot, 1)}
                e["diff_vs_b1"] = {"diff": q["diff"], "ci": [q["diff_lo"], q["diff_hi"]], "p_raw": q["p"]}
                e["_R"] = S[f"R_h{hh}"].to_numpy(float)  # for deflated Sharpe after logging (dropped from JSON)
                # grid heatmap: threshold x h
                cells = H["grid"] if H["grid"] else [None]
                hm = {}
                for c in cells:
                    pc = {**p, H["key"]: c} if c is not None else p
                    for h in HS:
                        if c == (p.get(H["key"]) if H["key"] else None) and h == hh:
                            qq = q
                        else:
                            qq, _, _, _ = R.cell(hid, pc, g, h=h, boot=1000, seed=2)
                        hm[f"{c}|{h}"] = {"net_mean_R": qq["net_mean_R"], "n": qq["n"], "p": qq["p"]}
                        is_grid_reg = (h == H["h"]) and reg_cls
                        if not (c == (p.get(H["key"]) if H["key"] else None) and h == hh):
                            logrow(hid, f"{H['key'] or 'thr'}={c}_h{h}", "yes" if is_grid_reg else "no", smp, g, qq,
                                   "grid" if is_grid_reg else "h-sensitivity")
                e["grid_heatmap"] = hm
                # plateau
                pl = []
                for pp in perts:
                    qq, _, _, _ = R.cell(hid, pp, g, boot=1000, seed=3)
                    pl.append({"params": pp, **{k: qq[k] for k in ("n", "net_mean_R", "diff", "p", "net_2x")}})
                    logrow(hid, "plateau " + ",".join(f"{k}={v:.4g}" for k, v in pp.items()), "no", smp, g, qq,
                           "plateau")
                e["plateau"] = pl
                e["sens"] = {"net_2x": q["net_2x"], "open1": q["open1"], "cap5": q["cap5"]}
                for nm in ("open1", "cap5"):
                    logrow(hid, f"chosen_{nm}", "no", smp, g, {**q, "net_mean_R": q[nm]}, "sensitivity")
                entry[smp] = e
            # extra registered=no variants
            if hid == "D2" or (hid in ("E1", "E2", "E3")):
                vp = {**p, "moving": True} if hid == "D2" else {**p, "known_only": True}
                vn = "moving_wall(broke_*)" if hid == "D2" else "known_time_only"
                entry["variant"] = {"name": vn}
                for smp, R in runners.items():
                    qq, _, _, _ = R.cell(hid, vp, g, seed=4)
                    entry["variant"][smp] = qq
                    logrow(hid, vn, "no", smp, g, qq, "variant")
            # verdict
            isq, oq = entry["IS"]["stats"], entry["OOS"]["stats"]
            pl_ok, why = plateau_ok(oq, [x for x in entry["OOS"]["plateau"]], H["veto"])
            if hid == "V5":
                entry["criteria"], entry["verdict"] = {}, "see V5 contrast"
            else:
                crit, verdict = criteria(hid, isq, oq, pl_ok, H["veto"])
                if not reg_cls:
                    verdict = "N/A (index-only rule; stock run unregistered: " + verdict + ")"
                entry["criteria"], entry["verdict"] = crit, verdict
            entry["plateau_note"] = why
            res[hid][g] = entry
            for smp in ("IS", "OOS"):
                q = entry[smp]["stats"]
                logrow(hid, f"chosen {json.dumps(p, default=str)} h{HYP[hid]['h'] if hid != 'D4' else 'to-expiry-1'}",
                       reg, smp, g, q, entry["verdict"] if smp == "OOS" else "IS",
                       f"diff vs B1 {q['diff']:+.4f} [{q['diff_lo']:+.4f},{q['diff_hi']:+.4f}]" if q["n"] else "n=0")
            print(f"[{dt.datetime.now():%H:%M:%S}] {hid} {g:9s} IS n={isq['n']:>6} net {isq['net_mean_R']:+.3f} | OOS n={oq['n']:>6} "
                  f"net {oq['net_mean_R']:+.3f} diff {oq['diff']:+.3f} p={oq['p']:.3f} -> {entry['verdict']}")

    if "V5" not in HYP:
        HYP["V5"] = dict(fn=None, kind="STR", h=5, veto=False, key=None, grid=None, p={})
        res["V5"] = {}
    # V5 contrast: stocks minus indices, unconditional strangle h5
    v5 = {}
    for smp, R in runners.items():
        Ss, _ = R.trades("V5", {}, "STOCKS")
        Si, _ = R.trades("V5", {}, "INDEX")
        d = bt.diff_vs_baseline(Ss["R_h5"].to_numpy(), Ss["date"].to_numpy(), Si["R_h5"].to_numpy(),
                                Si["date"].to_numpy(), n_boot=a.boot, seed=5)
        v5[smp] = {"stocks": mean_or_nan(Ss["R_h5"]), "index": mean_or_nan(Si["R_h5"]), "diff": d["diff"],
                   "ci": [d["lo"], d["hi"]], "p": d["p"], "n_stocks": len(Ss), "n_index": len(Si),
                   "n_days": int(pd.concat([Ss["date"], Si["date"]]).nunique())}
        logrow("V5", "stocks_minus_index_strangle_h5", "yes", smp, "stocks-index",
               {"n": len(Ss) + len(Si), "n_days": v5[smp]["n_days"], "net_mean_R": d["diff"], "ci_lo": d["lo"],
                "ci_hi": d["hi"], "p": d["p"]}, "contrast")
    o, i = v5["OOS"], v5["IS"]
    v5_pass = bool(o["diff"] > 0 and o["ci"][0] > 0 and i["diff"] > 0)
    v5["both_le_0_OOS"] = bool(o["stocks"] <= 0 and o["index"] <= 0)
    v5["verdict"] = "PASS" if v5_pass else "FAIL"
    for g in res["V5"]:
        res["V5"][g]["verdict"] = v5["verdict"] + " (contrast)"
    logs[-1]["verdict"] = v5["verdict"]
    print(f"V5 contrast OOS stocks {o['stocks']:+.3f} index {o['index']:+.3f} diff {o['diff']:+.3f} "
          f"[{o['ci'][0]:+.3f},{o['ci'][1]:+.3f}] -> {v5['verdict']}")

    # within-family BH (indicative; the lead applies BH across all families)
    keyp = [(h, g) for h in res for g in res[h] if h != "V5" and res[h][g]["registered"]]
    ps = [res[h][g]["OOS"]["stats"]["p"] for h, g in keyp]
    qs = bt.bh_adjust(ps)
    for (h, g), qv in zip(keyp, qs):
        res[h][g]["OOS"]["p_bh_within_family"] = qv

    if a.log:
        for r in logs:
            trials.log_trial(**r)
    ntr = trials.n_trials()
    for h in res:  # deflated Sharpe, N = total rows in trials_log.csv
        for g in res[h]:
            for smp in ("IS", "OOS"):
                r = res[h][g][smp].pop("_R")
                r = r[~np.isnan(r)]
                res[h][g][smp]["deflated_sharpe"] = (bt.deflated_sharpe(returns=r, n_trials=max(ntr, 1))
                                                     if len(r) > 3 and r.std() > 0 else None)
    meta.update({"phase": "complete", "results": res, "V5_contrast": v5, "n_logged_this_run": len(logs) if a.log else 0,
                 "n_trials_total": ntr})
    dump(meta)
    write_md(res, v5, frozen, len(logs), a.log, ntr)
    print(f"logged {len(logs) if a.log else 0} rows (prepared {len(logs)}); trials_log rows now {ntr}")


def write_md(res, v5, frozen, nlog, logged, ntr):
    L = []
    A = L.append
    A("# P4 - Volatility, dealer-positioning and event hypotheses (V1-V5, D1-D4, E1-E3)")
    A(f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} by `{SCRIPT}`. Swing, §0 standard contract (|delta| 0.40, std "
      f"expiry), real bhavcopy, close fills ± modelled half-spread, statutory costs, 1 lot. Strangle R = combined net ₹ ÷ "
      f"combined premium paid incl. costs. CI = day-clustered bootstrap 95%; p = raw two-sided vs B1 (same class/side "
      f"mix/hold; BH across all families pending). {RULINGS}")
    A("")
    A("Criteria (OOS): 1 net>0 CI excl 0 | 2 beats B1 p<.05 (raw) | 3 OOS ≥ 50% IS, same sign | 4 ±20% plateau | "
      "5 n≥100 on ≥40 days | 6 2× spread net ≥ 0. Veto rows: 1-2 = worse than B1, 3 = IS diff same sign, 6 = still worse "
      "at 2×.")
    A("")
    A("| hyp | class | params | IS net (gross) | OOS net (gross) | OOS vs B1 [CI] p | n / days OOS | 1 2 3 4 5 6 | verdict |")
    A("|---|---|---|---|---|---|---|---|---|")
    for h in res:
        for g in res[h]:
            e = res[h][g]
            i, o = e["IS"]["stats"], e["OOS"]["stats"]
            pr = ",".join(f"{k}={v:.3g}" for k, v in e["params"].items() if not isinstance(v, bool)) or "-"
            cr = " ".join("✓" if e["criteria"].get(k) else "✗" for k in "123456") if e["criteria"] else "-"
            f = lambda q: "n/a" if not q["n"] else f"{q['net_mean_R']:+.3f} ({q['gross_mean_R']:+.3f})"
            dd = "n/a" if not o["n"] else f"{o['diff']:+.3f} [{o['diff_lo']:+.3f},{o['diff_hi']:+.3f}] {o['p']:.3f}"
            A(f"| {h} | {g} | {pr} | {f(i)} | {f(o)} | {dd} | {o['n']} / {o['n_days']} | {cr} | {e['verdict']} |")
    A("")
    o = v5["OOS"]; i = v5["IS"]
    A(f"**V5 contrast** (unconditional strangle h5, stocks − indices): IS {i['stocks']:+.3f} vs {i['index']:+.3f} "
      f"(diff {i['diff']:+.3f}); OOS {o['stocks']:+.3f} vs {o['index']:+.3f}, diff {o['diff']:+.3f} "
      f"[{o['ci'][0]:+.3f},{o['ci'][1]:+.3f}] p={o['p']:.3f} -> **{v5['verdict']}**; both ≤ 0 OOS: {v5['both_le_0_OOS']}.")
    A("")
    A(f"Trials: {nlog} rows {'appended' if logged else 'prepared (not logged)'}; trials_log.csv now {ntr} rows.")
    A("")
    A("## Reading")
    A("<!-- NOTES -->")
    assert len(L) <= 110, len(L)
    with open(MD_PATH, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
