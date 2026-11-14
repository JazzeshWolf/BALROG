"""P4: pre-registered momentum / structure hypotheses M1, M1b, M2, M3, M4, M5, M8 (02_hypotheses.md §3), swing.

  python -I research/scripts/p4_run_momentum.py [--bt data/bt] [--features data/features/daily.parquet] [--no-log]

Protocol (02_hypotheses.md §0 + lead rulings):
  * signal from features at the close of entry day t (join on date, symbol); standard contract (is_std) of the side
    the rule says, entered at the t close, time exit after h days (capped the day before expiry, as built).
  * primary sample excludes holds spanning ca_break, past-data-end (incomplete), settle-priced and unmarked exits;
    no winsorising (robustness line: gross R capped at +5).
  * one open position per symbol x hypothesis-variant x side: a signal is skipped while a prior trade of the same key is
    open (re-entry allowed at the close of the prior exit day).
  * B1 = same class / hold / standard contract on ALL eligible days (same exclusions). diff vs B1 is side- and
    group-matched: each R is centred on the B1 mean of its (grp, side) cell, then a joint day-clustered bootstrap.
  * classes NIFTY, BANKNIFTY, SENSEX, index (all indices pooled), stocks; verdicts on index and stocks.
  * grid cell chosen on IS only (max IS net mean R at the registered hold, per verdict group), written to
    research/out/p4_momentum.json under "frozen" BEFORE any OOS trade is loaded; OOS run once.
Outputs research/out/p4_momentum.{json,md}; every evaluated cell -> research/trials_log.csv (style=swing).
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

OUT = os.path.join(ROOT, "research", "out")
SCRIPT = "research/scripts/p4_run_momentum.py"
H_LIST = list(bt.H_LIST)
CLASSES = ["NIFTY", "BANKNIFTY", "SENSEX", "index", "stocks"]
VGROUPS = ["index", "stocks"]
HYPS = ["M1", "M1b", "M2", "M3", "M4", "M5"]
PRIMARY_H = {"M1": 3, "M1b": 3, "M2": 3, "M3": 3, "M4": 2, "M5": 5}
EXPECT = {"M1": "+", "M1b": "<= M1", "M2": "+ inside band", "M3": "+", "M4": "+", "M5": "+", "M8": "- on expiry day"}
NB_GRID, NB_FULL = 1000, 2000
COMMENT = {   # qualitative reading of the frozen run (seeded bootstrap -> deterministic)
    "M1": "Index IS looked good (+0.08, NIFTY +0.17) and vanished OOS - classic IS mirage. Stocks: calls do beat puts "
          "on the signal day (directional content exists) but not by enough to clear theta + spread.",
    "M1b": "Not worse than M1 as registered: both are indistinguishable from B1. 'Exhaustion' label carries no information.",
    "M2": "Trend-band entries are just B1 with a side choice; band 25-45 is no better than ADX<25 and the 'late' ADX>45 "
          "group is too thin on indices to say anything. Index holds spanning an expiry are worse than those that "
          "do not (IS -0.135 vs +0.013, OOS -0.036 vs +0.000), but per-index signs disagree. Stocks never span an expiry with the monthly std contract.",
    "M3": "Index breakouts are anecdotal (80 OOS trades); on stocks breakouts are, if anything, worse than B1 "
          "(-0.05, p=0.10) and C-P is negative: no follow-through for a buyer - the registered '+' is contradicted.",
    "M4": "The only live one: index gap-and-hold OOS +0.135 R, +0.20 vs B1, C-P +0.39 (p=0.013), 2x spread still "
          "positive, plateau holds, IS same sign - but CI includes 0 and q(BH) is far above 0.05 with n=141/75 days. "
          "Candidate for a fresh OOS / engine bonus, not an edge. Stocks: gross +0.07 eaten by costs.",
    "M5": "Relative-strength stocks bleed like B1 (-0.11 R); no persistence an option buyer can monetise in 5 days.",
    "M8": "Stocks: zero expiry-day entries by construction (std stock contract = nearest monthly with >= 7 DTE, "
          "none exists on the monthly expiry day) - untestable, not anecdotal by sample.",
}
DROPPED = {}   # BSE-listed stock-option std rows dropped per sample (features use NSE chains for stocks)
HIST_EDGES = [-np.inf, -0.9, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, np.inf]
FEAT_COLS = ["date", "symbol", "cls", "ohlc_src", "fut_dprice_z", "fut_doi_z", "fut_structure", "adx14", "plus_di",
             "minus_di", "day_range_atr20", "gap_sigma", "close_vs_open", "rs20", "rs20_decile", "nifty_above_20dma",
             "is_expiry_day", "vix_pctile_252"] + [f"brk_{s}{n}" for s in ("hi", "lo") for n in (10, 20, 40)]


# ----------------------------------------------------------------------------- variants (registered rules)
def grid_variants(hyp):
    """(name, params) of the registered grid; the central / fixed cell is listed even when there is no grid."""
    if hyp in ("M1", "M1b"):
        return [("z0.5", {"z": 0.5})]
    if hyp == "M2":
        return [(f"adx{lo}-{hi}", {"lo": lo, "hi": hi}) for lo in (20, 25, 30) for hi in (40, 45, 50)]
    if hyp == "M3":
        return [(f"lb{n}_atr1.5", {"n": n, "m": 1.5}) for n in (10, 20, 40)]
    if hyp == "M4":
        return [("gap1.0", {"g": 1.0})]
    if hyp == "M5":
        return [("q0.10", {"q": 0.10})]
    raise KeyError(hyp)


def plateau_variants(hyp, p):
    """Each threshold moved -20% / +20% around the frozen cell."""
    r = lambda x, f: round(x * f, 4)
    if hyp in ("M1", "M1b"):
        return [(f"z{r(p['z'], f)}", {"z": r(p["z"], f)}) for f in (0.8, 1.2)]
    if hyp == "M2":
        out = [(f"adx{r(p['lo'], f)}-{p['hi']}", {"lo": r(p["lo"], f), "hi": p["hi"]}) for f in (0.8, 1.2)]
        return out + [(f"adx{p['lo']}-{r(p['hi'], f)}", {"lo": p["lo"], "hi": r(p["hi"], f)}) for f in (0.8, 1.2)]
    if hyp == "M3":
        out = [(f"lb{p['n']}_atr{r(p['m'], f)}", {"n": p["n"], "m": r(p["m"], f)}) for f in (0.8, 1.2)]
        return out + [(f"lb{int(round(p['n'] * f))}_atr{p['m']}", {"n": int(round(p["n"] * f)), "m": p["m"]})
                      for f in (0.8, 1.2)]
    if hyp == "M4":
        return [(f"gap{r(p['g'], f)}", {"g": r(p["g"], f)}) for f in (0.8, 1.2)]
    if hyp == "M5":
        return [(f"q{r(p['q'], f)}", {"q": r(p["q"], f)}) for f in (0.8, 1.2)]
    raise KeyError(hyp)


def add_donchian(F, und_path):
    """brk_{hi,lo}{n} for the M3 plateau lookbacks (8, 12, 16, 24, 32, 48) - same construction as
    research/lib/features.py (adjusted H/L of sessions t-n..t-1 vs adjusted close t). Rows without underlying H/L
    (SENSEX/BANKEX: OHLC built from the near future in features) take the nearest registered lookback."""
    u = pd.read_parquet(und_path, columns=["date", "symbol", "adj_high", "adj_low", "adj_close"])
    u = u.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = u.groupby("symbol", sort=False)
    new = {}
    for n in (8, 12, 16, 20, 24, 32, 48):
        hi = g["adj_high"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).max())
        lo = g["adj_low"].transform(lambda s: s.shift(1).rolling(n, min_periods=n).min())
        ok = hi.notna() & lo.notna()
        new[f"xbrk_hi{n}"] = np.where(ok, u["adj_close"] > hi, np.nan)
        new[f"xbrk_lo{n}"] = np.where(ok, u["adj_close"] < lo, np.nan)
    u = pd.concat([u[["date", "symbol"]], pd.DataFrame(new)], axis=1)
    F = F.merge(u, on=["date", "symbol"], how="left")
    chk = F[["brk_hi20", "xbrk_hi20"]].dropna()
    match = float((chk["brk_hi20"].astype(bool) == chk["xbrk_hi20"].astype(bool)).mean()) if len(chk) else np.nan
    near = {8: 10, 12: 10, 16: 20, 24: 20, 32: 40, 48: 40}
    for n, m in near.items():
        for s in ("hi", "lo"):
            x = F[f"xbrk_{s}{n}"]
            F[f"brk_{s}{n}"] = np.where(x.notna(), x == 1, F[f"brk_{s}{m}"].fillna(False).astype(bool))
    return F.drop(columns=[c for c in F.columns if c.startswith("xbrk_")]), match


def signal_side(F, hyp, p):
    """Series aligned with F: 'CE', 'PE' or None."""
    side = np.full(len(F), None, dtype=object)
    if hyp in ("M1", "M1b"):
        zp, zo = F["fut_dprice_z"].to_numpy(float), F["fut_doi_z"].to_numpy(float)
        z = p["z"]
        up, dn = zp >= z, zp <= -z
        oi = zo >= z if hyp == "M1" else zo <= -z           # M1 OI build-up; M1b covering / unwinding
        side[up & oi] = "CE"
        side[dn & oi] = "PE"
    elif hyp == "M2":
        a = F["adx14"].to_numpy(float)
        pdi, mdi = F["plus_di"].to_numpy(float), F["minus_di"].to_numpy(float)
        lo, hi = p.get("lo", -np.inf), p.get("hi", np.inf)
        band = (a >= lo) & (a <= hi) if p.get("mode", "band") == "band" else \
            ((a < lo) if p["mode"] == "below" else (a > hi))
        side[band & (pdi > mdi)] = "CE"
        side[band & (mdi > pdi)] = "PE"
    elif hyp == "M3":
        n, m = p["n"], p["m"]
        rng = F["day_range_atr20"].to_numpy(float) > m
        side[F[f"brk_hi{n}"].fillna(False).to_numpy(bool) & rng] = "CE"
        side[F[f"brk_lo{n}"].fillna(False).to_numpy(bool) & rng] = "PE"
    elif hyp == "M4":
        gs, cvo = F["gap_sigma"].to_numpy(float), F["close_vs_open"].to_numpy(float)
        side[(gs > p["g"]) & (cvo > 0)] = "CE"
        side[(gs < -p["g"]) & (cvo < 0)] = "PE"
    elif hyp == "M5":
        stk = (F["cls"] == "stock").to_numpy()
        r = F["rs20_pct"].to_numpy(float)
        nf = F["nifty_above_20dma"].to_numpy(float)
        side[stk & (r > 1 - p["q"]) & (nf == 1)] = "CE"
        side[stk & (r <= p["q"]) & (nf == 0)] = "PE"
    return side


# ----------------------------------------------------------------------------- trades
def load_trades(bt_dir, sample):
    cols = ["trade_id", "date", "symbol", "cls", "grp", "sample", "opt_type", "premium", "lot", "theta"]
    cols += [f"p{k}_h" for k in range(1, 6)]
    for h in H_LIST:
        cols += [f"{c}_h{h}" for c in ("exit_date", "gross_R", "net_R", "net_R2x", "net_rs", "ca_break", "incomplete",
                                       "settled", "nomark", "cal_days", "net_R_open1")]
    fs = sorted(glob.glob(os.path.join(bt_dir, "trades", "year=*.parquet")))
    t = pd.concat([pd.read_parquet(f, columns=cols + ["exchange"], filters=[("is_std", "==", True),
                                                                             ("sample", "==", sample)])
                   for f in fs], ignore_index=True)
    bse_stk = (t["cls"] == "stock") & (t["exchange"] != "nse")   # BSE-listed stock options duplicate NSE std rows
    DROPPED[sample] = int(bse_stk.sum())
    t = t[~bse_stk]
    dup = int(t.duplicated(["date", "symbol", "opt_type"]).sum())
    assert dup == 0, f"duplicate standard trades: {dup}"
    assert t["trade_id"].is_unique
    return t.reset_index(drop=True)


class Book:
    """One sample's standard trades + per-h arrays for fast signal evaluation."""

    def __init__(self, T, F):
        self.T = T
        self.day = T["date"].to_numpy("datetime64[D]").astype(np.int64)
        sym = pd.factorize(T["symbol"])[0]
        self.key = sym * 2 + (T["opt_type"].to_numpy() == "PE")
        self.valid, self.exitd = {}, {}
        for h in H_LIST:
            v = T[f"net_R_h{h}"].notna() & ~T[f"ca_break_h{h}"].astype(bool) & ~T[f"incomplete_h{h}"].astype(bool) \
                & ~T[f"settled_h{h}"].astype(bool) & ~T[f"nomark_h{h}"].astype(bool)
            self.valid[h] = v.to_numpy()
            e = T[f"exit_date_h{h}"].to_numpy("datetime64[D]")
            ei = e.astype(np.int64)
            ei = np.where(np.isnat(e), self.day + int(np.ceil(h * 7 / 5)) + 1, ei)   # unknown exit: ~h sessions
            self.exitd[h] = ei
            T[f"net_R_cap5_h{h}"] = T[f"net_R_h{h}"] - np.maximum(T[f"gross_R_h{h}"] - 5.0, 0.0)
        # row lookup (date, symbol, side) -> trade row
        self.lk = pd.Series(np.arange(len(T)), index=pd.MultiIndex.from_arrays([T["date"], T["symbol"], T["opt_type"]]))
        f = F[["date", "symbol", "is_expiry_day", "vix_pctile_252"]]
        m = T[["date", "symbol"]].merge(f, on=["date", "symbol"], how="left")
        T["entry_expiry_day"] = m["is_expiry_day"].fillna(False).astype(bool).to_numpy()
        T["vix_pct"] = m["vix_pctile_252"].to_numpy()
        T["vix_terc"] = pd.cut(T["vix_pct"], [-1, 100 / 3, 200 / 3, 101], labels=["low", "mid", "high"]).astype(str)
        T["year"] = T["date"].dt.year
        T["month"] = T["date"].dt.strftime("%Y-%m")
        # holds spanning an expiry day of the instrument: any expiry in (t, exit]
        fe = F.loc[F["is_expiry_day"].fillna(False).astype(bool), ["symbol", "date"]]
        ed = {s: np.sort(g["date"].to_numpy("datetime64[D]").astype(np.int64)) for s, g in fe.groupby("symbol")}
        syms = T["symbol"].to_numpy()
        for h in H_LIST:
            sp = np.zeros(len(T), bool)
            for s, idx in pd.Series(np.arange(len(T))).groupby(syms):
                arr = ed.get(s)
                if arr is None:
                    continue
                ix = idx.to_numpy()
                a = np.searchsorted(arr, self.day[ix], side="right")
                b = np.searchsorted(arr, self.exitd[h][ix], side="right")
                sp[ix] = b > a
            T[f"spans_exp_h{h}"] = sp
        self.cls_mask = {"NIFTY": (T["grp"] == "NIFTY").to_numpy(), "BANKNIFTY": (T["grp"] == "BANKNIFTY").to_numpy(),
                         "SENSEX": (T["grp"] == "SENSEX").to_numpy(), "index": (T["cls"] == "index").to_numpy(),
                         "stocks": (T["cls"] == "stock").to_numpy()}
        self._b1mu = {}

    def rows(self, F, side):
        ok = side != None  # noqa: E711
        if not ok.any():
            return np.array([], int)
        mi = pd.MultiIndex.from_arrays([F["date"].to_numpy()[ok], F["symbol"].to_numpy()[ok], side[ok]])
        r = self.lk.reindex(mi).to_numpy()
        return r[~np.isnan(r)].astype(int)

    def position_rule(self, rows, h):
        """Keep a signal only if no trade of the same symbol x side is open (entry >= previous kept exit day)."""
        if len(rows) == 0:
            return rows
        k, d, e = self.key[rows], self.day[rows], self.exitd[h][rows]
        o = np.lexsort((d, k))
        rows, k, d, e = rows[o], k[o], d[o], e[o]
        keep = np.zeros(len(rows), bool)
        lastk, laste = -1, -(10 ** 12)
        for i in range(len(rows)):
            if k[i] != lastk:
                lastk, laste = k[i], -(10 ** 12)
            if d[i] >= laste:
                keep[i] = True
                laste = e[i]
        return rows[keep]

    def opposite(self, rows):
        T = self.T.iloc[rows]
        opp = np.where(T["opt_type"].to_numpy() == "CE", "PE", "CE")
        r = self.lk.reindex(pd.MultiIndex.from_arrays([T["date"], T["symbol"], opp])).to_numpy()
        return r

    def b1(self, cls, h):
        return np.flatnonzero(self.cls_mask[cls] & self.valid[h])

    def b1mu(self, cls, h, col):
        k = (cls, h, col)
        if k not in self._b1mu:
            b = self.T.iloc[self.b1(cls, h)]
            self._b1mu[k] = b.groupby(["grp", "opt_type"])[col].mean()
        return self._b1mu[k]


# ----------------------------------------------------------------------------- statistics (research.lib.backtest)
def centred(book, df, cls, h, col):
    mu = book.b1mu(cls, h, col)
    return df[col].to_numpy(float) - mu.reindex(pd.MultiIndex.from_arrays([df["grp"], df["opt_type"]])).to_numpy()


def quick(book, rows, cls, h, nb=NB_GRID, seed=0):
    rows = rows[book.cls_mask[cls][rows] & book.valid[h][rows]]
    if len(rows) == 0:
        return None
    S = book.T.iloc[rows]
    B = book.T.iloc[book.b1(cls, h)]
    col = f"net_R_h{h}"
    ci = bt.cluster_bootstrap_ci(S[col].to_numpy(), S["date"].to_numpy(), n_boot=nb, seed=seed)
    d = bt.diff_vs_baseline(centred(book, S, cls, h, col), S["date"].to_numpy(), centred(book, B, cls, h, col),
                            B["date"].to_numpy(), n_boot=nb, seed=seed)
    return {"n": int(len(S)), "n_days": int(S["date"].nunique()), "share_CE": float((S["opt_type"] == "CE").mean()),
            "gross_mean_R": float(S[f"gross_R_h{h}"].mean()), "net_mean_R": float(ci["mean"]),
            "ci": [ci["lo"], ci["hi"]], "diff_vs_b1": float(d["diff"]), "diff_ci": [d["lo"], d["hi"]], "p_vs_b1": d["p"],
            "net_mean_R_2x": float(S[f"net_R2x_h{h}"].mean()), "net_mean_R_open1": float(S[f"net_R_open1_h{h}"].mean()),
            "net_mean_R_cap5": float(S[f"net_R_cap5_h{h}"].mean())}


def describe(book, df, h, nb, seed, cls=None):
    """Full buyer metrics of a trade set (signal or B1)."""
    col = f"net_R_h{h}"
    ci = bt.cluster_bootstrap_ci(df[col].to_numpy(), df["date"].to_numpy(), n_boot=nb, seed=seed)
    gci = bt.cluster_bootstrap_ci(df[f"gross_R_h{h}"].to_numpy(), df["date"].to_numpy(), n_boot=nb, seed=seed)
    bm = bt.buyer_metrics(df[f"net_rs_h{h}"].to_numpy(), df[col].to_numpy(), df["date"].to_numpy(),
                          df["theta"].to_numpy(), df["lot"].to_numpy(), df[f"cal_days_h{h}"].to_numpy())
    P = df[[f"p{k}_h" for k in range(1, 6)]].to_numpy(float)
    has = ~np.isnan(P).all(1)
    pk = np.where(has, np.nanargmax(np.where(np.isnan(P), -np.inf, P), axis=1) + 1, 0)
    ttp = {str(k): float((pk[has] == k).mean()) if has.any() else None for k in range(1, 6)}
    hp = np.column_stack([df["premium"].to_numpy(float), P])
    ttt = bt.time_to_target(hp, df["premium"].to_numpy(float), target=0.5)
    hist = np.histogram(np.clip(df[col].to_numpy(float), -50, 50), bins=np.array(HIST_EDGES[1:-1]))[0]
    hist = [int((df[col] < HIST_EDGES[1]).sum())] + hist.tolist() + [int((df[col] >= HIST_EDGES[-2]).sum())]
    eq = df.groupby("month")[f"net_rs_h{h}"].sum().sort_index()
    by = lambda k: {str(a): {"n": int(len(g)), "net_mean_R": float(g[col].mean())} for a, g in df.groupby(k)}
    out = {"n": int(len(df)), "n_days": int(df["date"].nunique()), "win_rate": bm["win_rate"],
           "avg_win_R": bm["avg_win_R"], "avg_loss_R": bm["avg_loss_R"], "gross_mean_R": float(gci["mean"]),
           "gross_ci": [gci["lo"], gci["hi"]], "net_mean_R": float(ci["mean"]), "ci": [ci["lo"], ci["hi"]],
           "expectancy_rs": bm["expectancy_rs"], "profit_factor": bm["profit_factor"], "max_dd_rs": bm["max_dd_rs"],
           "longest_losing_streak": bm["longest_losing_streak"], "theta_share_losers": bm.get("theta_share_losers"),
           "time_to_peak_day": ttp, "time_to_plus50pct": ttt,
           "net_mean_R_2x": float(df[f"net_R2x_h{h}"].mean()), "net_mean_R_open1": float(df[f"net_R_open1_h{h}"].mean()),
           "net_mean_R_cap5": float(df[f"net_R_cap5_h{h}"].mean()),
           "hist_edges": [None if not np.isfinite(x) else x for x in HIST_EDGES], "hist_counts": hist,
           "equity_month": {"month": eq.index.tolist(), "cum_net_rs": eq.cumsum().round(0).tolist()},
           "by_year": by("year"), "by_vix_tercile": by("vix_terc")}
    return out


def full(book, rows, cls, h, seed=1):
    rows = rows[book.cls_mask[cls][rows] & book.valid[h][rows]]
    if len(rows) == 0:
        return None
    S = book.T.iloc[rows]
    B = book.T.iloc[book.b1(cls, h)]
    col = f"net_R_h{h}"
    res = {"signal": describe(book, S, h, NB_FULL, seed), "b1": describe(book, B, h, NB_FULL, seed)}
    res["signal"]["share_CE"] = float((S["opt_type"] == "CE").mean())
    d = bt.diff_vs_baseline(centred(book, S, cls, h, col), S["date"].to_numpy(), centred(book, B, cls, h, col),
                            B["date"].to_numpy(), n_boot=NB_FULL, seed=seed)
    draw = bt.diff_vs_baseline(S[col].to_numpy(), S["date"].to_numpy(), B[col].to_numpy(), B["date"].to_numpy(),
                               n_boot=NB_FULL, seed=seed)
    res["diff_vs_b1"] = {"diff": d["diff"], "ci": [d["lo"], d["hi"]], "p": d["p"], "method": "side/grp-matched"}
    res["diff_vs_b1_unmatched"] = {"diff": draw["diff"], "ci": [draw["lo"], draw["hi"]], "p": draw["p"]}
    per_side = {}
    for sd in ("CE", "PE"):
        s = S[S["opt_type"] == sd]
        b = B[B["opt_type"] == sd]
        if len(s):
            x = bt.diff_vs_baseline(s[col].to_numpy(), s["date"].to_numpy(), b[col].to_numpy(), b["date"].to_numpy(),
                                    n_boot=NB_GRID, seed=seed)
            per_side[sd] = {"n": int(len(s)), "net_mean_R": float(s[col].mean()), "b1_net_mean_R": float(b[col].mean()),
                            "diff": x["diff"], "p": x["p"]}
    res["per_side"] = per_side
    # opposite side on the same signal days (signal side minus opposite side; C-P per direction)
    opp = book.opposite(rows)
    ok = ~np.isnan(opp)
    oi = opp[ok].astype(int)
    ok2 = book.valid[h][oi]
    sr, orow = rows[ok][ok2], oi[ok2]
    if len(sr):
        S2, O2 = book.T.iloc[sr], book.T.iloc[orow]
        x = bt.diff_vs_baseline(S2[col].to_numpy(), S2["date"].to_numpy(), O2[col].to_numpy(), O2["date"].to_numpy(),
                                n_boot=NB_FULL, seed=seed)
        cp = {}
        for sd in ("CE", "PE"):
            m = S2["opt_type"].to_numpy() == sd
            if m.any():
                c = S2[col].to_numpy()[m] if sd == "CE" else O2[col].to_numpy()[m]
                p_ = O2[col].to_numpy()[m] if sd == "CE" else S2[col].to_numpy()[m]
                cp[{"CE": "bullish_signals", "PE": "bearish_signals"}[sd]] = {"n": int(m.sum()),
                                                                              "call_minus_put": float(c.mean() - p_.mean())}
        res["signal_minus_opposite"] = {"n_pairs": int(len(sr)), "diff": x["diff"], "ci": [x["lo"], x["hi"]],
                                        "p": x["p"], **cp}
    dsr = bt.deflated_sharpe(returns=S[col].to_numpy(), n_trials=max(trials.n_trials(), 2)) if len(S) > 10 else None
    res["deflated_sharpe"] = dsr
    return res


# ----------------------------------------------------------------------------- engine
class Runner:
    def __init__(self, F, log, note):
        self.F, self.log, self.note = F, log, note
        self._sig = {}
        self.n_logged = 0

    def side(self, hyp, p):
        k = (hyp, tuple(sorted(p.items())))
        if k not in self._sig:
            self._sig[k] = signal_side(self.F, hyp, p)
        return self._sig[k]

    def kept(self, book, hyp, p, h):
        return book.position_rule(book.rows(self.F, self.side(hyp, p)), h)

    def classes(self, hyp):
        return ["stocks"] if hyp == "M5" else CLASSES

    def grid(self, book, hyp, variants, sample, registered="yes", tag=""):
        out = {}
        for vname, p in variants:
            for h in H_LIST:
                rows = self.kept(book, hyp, p, h)
                for cls in self.classes(hyp):
                    s = quick(book, rows, cls, h)
                    out.setdefault(cls, {}).setdefault(vname, {})[f"h{h}"] = s
                    if s and self.log:
                        self._log(hyp, f"{vname}_h{h}{tag}", registered, sample, cls, s,
                                  verdict="grid" if not tag else tag.strip("_"))
        return out

    def _log(self, hyp, variant, registered, sample, cls, s, verdict, notes=""):
        trials.log_trial(id=hyp, variant=variant, registered=registered, sample=sample, cls=cls, style="swing",
                         n_trades=s["n"], n_days=s["n_days"], gross_mean_R=s["gross_mean_R"],
                         net_mean_R=s["net_mean_R"], ci_lo=s["ci"][0], ci_hi=s["ci"][1], p_vs_b1=s["p_vs_b1"],
                         net_mean_R_2x_spread=s["net_mean_R_2x"], verdict=verdict,
                         notes=(notes or f"std contract, time exit, 1 pos/sym/side, diff vs B1 side-matched; {self.note}").strip(),
                         script=SCRIPT)
        self.n_logged += 1


def m2_extras(R, book, frozen, sample):
    """ADX < 25 / > 45 groups (same DI rule) and frozen band with / without holds spanning an expiry day."""
    out = {}
    h = PRIMARY_H["M2"]
    for name, p in (("adx_lt25", {"lo": 25, "mode": "below"}), ("adx_gt45", {"hi": 45, "mode": "above"}),
                    ("band25-45", {"lo": 25, "hi": 45})):
        rows = R.kept(book, "M2", p, h)
        for cls in CLASSES:
            s = quick(book, rows, cls, h)
            out.setdefault(cls, {})[name] = s
            if s and R.log and name != "band25-45":
                R._log("M2", f"{name}_h{h}", "yes", sample, cls, s, verdict="comparison group")
    for vg in VGROUPS:
        p = frozen["M2"][vg]["params"]
        rows = R.kept(book, "M2", p, h)
        sp = book.T[f"spans_exp_h{h}"].to_numpy()
        for cls in (CLASSES if vg == "index" else ["stocks"]):
            if vg == "index" and cls == "stocks":
                continue
            for nm, m in (("spans_expiry", sp[rows]), ("no_expiry_in_hold", ~sp[rows])):
                s = quick(book, rows[m], cls, h)
                out.setdefault(cls, {})[f"frozen_{nm}"] = s
                if s and R.log:
                    R._log("M2", f"{frozen['M2'][vg]['variant']}_h{h}_{nm}", "yes", sample, cls, s,
                           verdict="expiry split")
    return out


def m8(R, book, frozen, sample):
    """Pool the frozen-cell entries of M1, M1b, M2, M3, M4, M5 (own hold); split by entry on an expiry day."""
    out = {}
    for vg in VGROUPS:
        parts = []
        for hyp in HYPS:
            if hyp == "M5" and vg == "index":
                continue
            h = PRIMARY_H[hyp]
            rows = R.kept(book, hyp, frozen[hyp][vg]["params"], h)
            rows = rows[book.cls_mask[vg][rows] & book.valid[h][rows]]
            t = book.T.iloc[rows][["trade_id", "date", "grp", "opt_type", "entry_expiry_day"]].copy()
            t["R"] = book.T[f"net_R_h{h}"].to_numpy()[rows]
            t["R2x"] = book.T[f"net_R2x_h{h}"].to_numpy()[rows]
            t["gR"] = book.T[f"gross_R_h{h}"].to_numpy()[rows]
            t["hyp"] = hyp
            parts.append(t)
        P = pd.concat(parts, ignore_index=True).drop_duplicates("trade_id")
        e, ne = P[P["entry_expiry_day"]], P[~P["entry_expiry_day"]]
        res = {"n_pooled": int(len(P))}
        for nm, d in (("expiry_day", e), ("other_days", ne)):
            if len(d):
                ci = bt.cluster_bootstrap_ci(d["R"].to_numpy(), d["date"].to_numpy(), n_boot=NB_FULL, seed=8)
                res[nm] = {"n": int(len(d)), "n_days": int(d["date"].nunique()), "net_mean_R": ci["mean"],
                           "ci": [ci["lo"], ci["hi"]], "gross_mean_R": float(d["gR"].mean()),
                           "net_mean_R_2x": float(d["R2x"].mean()),
                           "by_hyp": {k: int(v) for k, v in d["hyp"].value_counts().items()}}
        if len(e) and len(ne):
            x = bt.diff_vs_baseline(e["R"].to_numpy(), e["date"].to_numpy(), ne["R"].to_numpy(), ne["date"].to_numpy(),
                                    n_boot=NB_FULL, seed=8)
            res["diff_expiry_minus_other"] = {"diff": x["diff"], "ci": [x["lo"], x["hi"]], "p": x["p"]}
            if R.log:
                s = {"n": res["expiry_day"]["n"], "n_days": res["expiry_day"]["n_days"],
                     "gross_mean_R": res["expiry_day"]["gross_mean_R"], "net_mean_R": res["expiry_day"]["net_mean_R"],
                     "ci": res["expiry_day"]["ci"], "p_vs_b1": x["p"], "net_mean_R_2x": res["expiry_day"]["net_mean_R_2x"]}
                R._log("M8", "pooled_M1-M5_expiry_vs_other", "yes", sample, vg, s, verdict="expiry split",
                       notes=f"p = expiry-day minus other-day entries (not vs B1); diff {x['diff']:+.4f}; {R.note}".strip())
        out[vg] = res
    return out


# ----------------------------------------------------------------------------- criteria / verdict
def criteria(hyp, vg, IS, OOS, plateau_oos, central_oos, q_bh):
    s_is, s_oos = IS["signal"], OOS["signal"]
    m_is, m_oos = s_is["net_mean_R"], s_oos["net_mean_R"]
    c = {}
    c["c1_net_pos_ci"] = bool(m_oos > 0 and s_oos["ci"][0] > 0)
    c["c2_beats_b1_bh"] = bool(OOS["diff_vs_b1"]["diff"] > 0 and q_bh is not None and q_bh < 0.05)
    c["c3_is_oos_agree"] = bool(np.sign(m_is) == np.sign(m_oos) and m_is != 0 and m_oos / m_is >= 0.5)
    pl = [v for v in plateau_oos.values() if v is not None]
    c["c4_plateau"] = bool(central_oos > 0 and len(pl) == len(plateau_oos) and
                           all(v["net_mean_R"] > 0 and v["net_mean_R"] >= 0.5 * central_oos for v in pl))
    c["c5_sample"] = bool(s_oos["n"] >= 100 and s_oos["n_days"] >= 40)
    c["c6_2x_spread"] = bool(s_oos["net_mean_R_2x"] >= 0)
    if not c["c5_sample"]:
        v = "ANECDOTAL"
    elif all(c.values()):
        v = "PASS"
    elif c["c2_beats_b1_bh"] and m_oos <= 0:
        v = "LESS-BAD-NOT-EDGE"
    else:
        v = "FAIL"
    return c, v


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--features", default=os.path.join(ROOT, "data", "features", "daily.parquet"))
    ap.add_argument("--underlying", default=os.path.join(ROOT, "data", "underlying", "daily.parquet"))
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    log = not a.no_log
    n_log0 = trials.n_trials()

    F = pd.read_parquet(a.features, columns=FEAT_COLS)
    assert not F.duplicated(["date", "symbol"]).any(), "duplicate feature keys"
    F, donch_match = add_donchian(F, a.underlying)
    stk = F["cls"] == "stock"
    F["rs20_pct"] = np.nan
    F.loc[stk, "rs20_pct"] = F.loc[stk].groupby("date")["rs20"].rank(pct=True)
    dec_chk = float(((np.ceil(F.loc[stk, "rs20_pct"] * 10).clip(1, 10)) == F.loc[stk, "rs20_decile"])[
        F.loc[stk, "rs20_decile"].notna()].mean())
    lab = np.full(len(F), "none", dtype=object)  # consistency check of the M1 rule vs features' label
    s05 = signal_side(F, "M1", {"z": 0.5})
    lab_chk = float(((s05 == "CE") == (F["fut_structure"] == "long_buildup")).mean())
    R = Runner(F, log, a.note)
    J = {"meta": {"generated": dt.datetime.now().isoformat(timespec="seconds"), "script": SCRIPT,
                  "primary_h": PRIMARY_H, "expected": EXPECT, "n_boot": NB_FULL,
                  "checks": {"donchian20_recompute_match": donch_match, "rs20_decile_match": dec_chk,
                             "m1_label_match": lab_chk},
                  "notes": ["diff vs B1 is side/group-matched (R centred on the B1 mean of its grp x side cell)",
                            "position rule: re-entry allowed at the close of the previous exit day",
                            "M3 lookback plateau: SENSEX/BANKEX rows (futures-derived OHLC) use the nearest registered lookback",
                            "M5 plateau moves the decile cut (top/bottom 8% and 12%); 20d return / 20-DMA windows unchanged",
                            "criterion 2 uses BH within this family (provisional; final BH across all families pending)",
                            "index pool = all indices incl. FINNIFTY/MIDCPNIFTY/BANKEX/NIFTYNXT50; SENSEX & BSE indices IS = 2024"]}}

    # ------------------------------------------------ IS stage
    T_is = load_trades(a.bt, "IS")
    bk = Book(T_is, F)
    grid_is = {h: R.grid(bk, h, grid_variants(h), "IS") for h in HYPS}
    frozen = {}
    for hyp in HYPS:
        frozen[hyp] = {}
        hp = PRIMARY_H[hyp]
        for vg in VGROUPS:
            if hyp == "M5" and vg == "index":
                continue
            best = None
            for vname, p in grid_variants(hyp):
                s = grid_is[hyp][vg][vname][f"h{hp}"]
                m = s["net_mean_R"] if s else -np.inf
                if best is None or m > best[0]:
                    best = (m, vname, p)
            frozen[hyp][vg] = {"variant": best[1], "params": best[2], "is_net_mean_R": best[0], "h": hp}
    J["frozen"] = frozen
    J["frozen_written_before_oos"] = dt.datetime.now().isoformat(timespec="seconds")
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "p4_momentum.json"), "w", encoding="utf-8") as f:
        json.dump(J, f, indent=1, default=float)

    def stage(book, sample, grid):
        res = {}
        for hyp in HYPS:
            hp = PRIMARY_H[hyp]
            res[hyp] = {}
            for vg in VGROUPS:
                if hyp == "M5" and vg == "index":
                    continue
                fz = frozen[hyp][vg]
                pv = plateau_variants(hyp, fz["params"])
                pg = R.grid(book, hyp, pv, sample, tag="_plateau")
                rows = R.kept(book, hyp, fz["params"], hp)
                for cls in ([vg] if vg == "stocks" else ["NIFTY", "BANKNIFTY", "SENSEX", "index"]):
                    r = full(book, rows, cls, hp)
                    if r is None:
                        res[hyp][cls] = None
                        continue
                    r["frozen"] = fz
                    r["grid"] = {v: {hh: (s["net_mean_R"] if s else None) for hh, s in d.items()}
                                 for v, d in grid[hyp].get(cls, {}).items()}
                    r["grid_n"] = {v: {hh: (s["n"] if s else 0) for hh, s in d.items()}
                                   for v, d in grid[hyp].get(cls, {}).items()}
                    r["plateau"] = {v: d[f"h{hp}"] for v, d in pg.get(cls, {}).items()}
                    res[hyp][cls] = r
                    if log:
                        s = r["signal"]
                        q = {"n": s["n"], "n_days": s["n_days"], "gross_mean_R": s["gross_mean_R"],
                             "net_mean_R": s["net_mean_R"], "ci": s["ci"], "p_vs_b1": r["diff_vs_b1"]["p"],
                             "net_mean_R_2x": s["net_mean_R_2x"]}
                        R._log(hyp, f"{fz['variant']}_h{hp}_frozen_open1", "yes", sample, cls,
                               {**q, "net_mean_R": s["net_mean_R_open1"], "ci": [None, None]},
                               verdict="sensitivity t+1 open")
                        R._log(hyp, f"{fz['variant']}_h{hp}_frozen_cap5", "yes", sample, cls,
                               {**q, "net_mean_R": s["net_mean_R_cap5"], "ci": [None, None]},
                               verdict="robustness gross cap +5")
        res["M2_extras"] = m2_extras(R, book, frozen, sample)
        res["M8"] = m8(R, book, frozen, sample)
        return res

    res_is = stage(bk, "IS", grid_is)
    del bk, T_is

    # ------------------------------------------------ OOS stage (run once, frozen cells)
    T_oos = load_trades(a.bt, "OOS")
    bo = Book(T_oos, F)
    grid_oos = {h: R.grid(bo, h, grid_variants(h), "OOS") for h in HYPS}
    res_oos = stage(bo, "OOS", grid_oos)

    # ------------------------------------------------ criteria + verdicts
    tests = []
    for hyp in HYPS:
        for cls in CLASSES:
            if res_oos[hyp].get(cls) and cls in VGROUPS:
                tests.append((hyp, cls, res_oos[hyp][cls]["diff_vs_b1"]["p"]))
    for vg in VGROUPS:
        d = res_oos["M8"][vg].get("diff_expiry_minus_other")
        if d:
            tests.append(("M8", vg, d["p"]))
    q = bt.bh_adjust([t[2] for t in tests])
    qmap = {(t[0], t[1]): float(qq) for t, qq in zip(tests, q)}

    H = {}
    for hyp in HYPS:
        H[hyp] = {}
        hp = PRIMARY_H[hyp]
        for cls in CLASSES:
            I, O = res_is[hyp].get(cls), res_oos[hyp].get(cls)
            if I is None and O is None:
                continue
            e = {"IS": I, "OOS": O}
            if I and O:
                c, v = criteria(hyp, cls, I, O, O["plateau"], O["signal"]["net_mean_R"], qmap.get((hyp, cls)))
                if cls not in VGROUPS:   # sub-classes: descriptive, criterion 2 on raw p
                    c["c2_beats_b1_bh"] = bool(O["diff_vs_b1"]["diff"] > 0 and O["diff_vs_b1"]["p"] < 0.05)
                    _, v = criteria(hyp, cls, I, O, O["plateau"], O["signal"]["net_mean_R"],
                                    O["diff_vs_b1"]["p"])
                e["criteria"], e["verdict"] = c, v
                e["official"] = cls in VGROUPS
                e["q_bh_family"] = qmap.get((hyp, cls))
                e["p_raw_oos"] = O["diff_vs_b1"]["p"]
            elif O is None or I is None:
                s = (O or I)["signal"]
                e["verdict"] = "ANECDOTAL"
            H[hyp][cls] = e
    M8 = {"IS": res_is["M8"], "OOS": res_oos["M8"], "verdict": {}}
    for vg in VGROUPS:
        o, i = res_oos["M8"][vg], res_is["M8"][vg]
        ex = o.get("expiry_day")
        d, di = o.get("diff_expiry_minus_other"), i.get("diff_expiry_minus_other")
        if not ex or ex["n"] < 100 or ex["n_days"] < 40:
            v = "ANECDOTAL"
        elif d["diff"] < 0 and qmap.get(("M8", vg), 1) < 0.05 and di and di["diff"] < 0:
            v = "VETO-PASS"
        else:
            v = "FAIL"
        M8["verdict"][vg] = {"verdict": v, "p_raw": d["p"] if d else None, "q_bh_family": qmap.get(("M8", vg))}
    J.update({"hypotheses": H, "M2_extras": {"IS": res_is["M2_extras"], "OOS": res_oos["M2_extras"]}, "M8": M8,
              "grid_full": {"IS": grid_is, "OOS": grid_oos}, "bh_family_tests": [list(t) + [qmap[(t[0], t[1])]]
                                                                                 for t in tests]})
    J["meta"]["bse_stock_rows_dropped"] = DROPPED
    J["meta"]["trials_logged_this_run"] = R.n_logged
    J["meta"]["trials_log_rows_total"] = trials.n_trials()
    with open(os.path.join(OUT, "p4_momentum.json"), "w", encoding="utf-8") as f:
        json.dump(J, f, indent=1, default=lambda x: None if x is None else float(x) if np.isscalar(x) else str(x))
    write_md(J)
    # ---- console summary (<= 50 lines)
    print(f"checks: donch20 match {donch_match:.4f}, rs20 decile match {dec_chk:.4f}, M1 label match {lab_chk:.4f}")
    print(f"frozen: " + "; ".join(f"{h}/{vg}={frozen[h][vg]['variant']}" for h in HYPS for vg in frozen[h]))
    for hyp in HYPS:
        for vg in VGROUPS:
            e = H[hyp].get(vg)
            if not e or not e.get("OOS"):
                continue
            s, b = e["OOS"]["signal"], e["OOS"]["diff_vs_b1"]
            print(f"{hyp:4s} {vg:6s} OOS n={s['n']:>6} d={s['n_days']:>4} net {s['net_mean_R']:+.3f} "
                  f"[{s['ci'][0]:+.3f},{s['ci'][1]:+.3f}] IS {e['IS']['signal']['net_mean_R']:+.3f} "
                  f"dB1 {b['diff']:+.3f} p={b['p']:.3f} -> {e['verdict']}")
    for vg in VGROUPS:
        print(f"M8   {vg:6s} -> {M8['verdict'][vg]}")
    print(f"trials logged this run: {R.n_logged} (log rows {n_log0} -> {trials.n_trials()})")


# ----------------------------------------------------------------------------- markdown
def write_md(J):
    H, L = J["hypotheses"], []
    A = L.append
    f3 = lambda x: "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:+.3f}"
    A("# P4 - Momentum / structure hypotheses M1, M1b, M2, M3, M4, M5, M8 (swing)")
    A(f"Generated {J['meta']['generated'][:16]} by `{SCRIPT}`. Standard contract, close fills + p75 half-spread, "
      "statutory costs, time exit; excluded: ca_break / past-end / settle / no-mark exits; 1 position per "
      "symbol x hypothesis x side. diff vs B1 = side- and group-matched, day-clustered bootstrap (2,000). Grid cells "
      "frozen on IS (written to JSON before OOS was loaded). c2 = BH within this family (provisional). "
      f"Trials logged this run: {J['meta']['trials_logged_this_run']}.")
    A("")
    A("Frozen: " + "; ".join(f"{h} {vg}: {v['variant']} (h={v['h']})" for h, d in J["frozen"].items()
                             for vg, v in d.items()))
    A("")
    A("| hyp | class | IS net (gross) | OOS net (gross) [CI] | OOS vs B1 [CI] p | C-P* | n / days OOS | c1 c2 c3 c4 c5 c6 | verdict |")
    A("|---|---|---|---|---|---|---|---|---|")
    ck = lambda b: "✓" if b else "✗"
    for hyp in HYPS:
        for cls in CLASSES:
            e = H[hyp].get(cls)
            if not e or not e.get("OOS") or not e.get("IS"):
                continue
            si, so, d = e["IS"]["signal"], e["OOS"]["signal"], e["OOS"]["diff_vs_b1"]
            cp = e["OOS"].get("signal_minus_opposite", {})
            c = e["criteria"]
            lbl = f"**{cls}**" if cls in VGROUPS else cls
            A(f"| {hyp} | {lbl} | {f3(si['net_mean_R'])} ({f3(si['gross_mean_R'])}) | {f3(so['net_mean_R'])} "
              f"({f3(so['gross_mean_R'])}) [{f3(so['ci'][0])}, {f3(so['ci'][1])}] | {f3(d['diff'])} "
              f"[{f3(d['ci'][0])}, {f3(d['ci'][1])}] {d['p']:.3f} | {f3(cp.get('diff'))} | {so['n']:,} / {so['n_days']} | "
              + " ".join(ck(c[k]) for k in sorted(c)) + f" | {e['verdict'] if cls in VGROUPS else '(' + e['verdict'] + ')'} |")
    A("")
    A("*C-P = signal side minus opposite side on the same signal days (OOS). Sub-class verdicts in brackets are "
      "descriptive (raw p). c4 plateau and c6 2x spread evaluated OOS.")
    A("")
    A("## M2 side groups and expiry-spanning holds (net mean R, h=3; IS | OOS; n OOS)")
    A("| class | ADX<25 | band 25-45 | ADX>45 | frozen band, hold spans expiry | frozen band, no expiry in hold |")
    A("|---|---|---|---|---|---|")
    X = J["M2_extras"]
    for cls in CLASSES:
        cells = []
        for k in ("adx_lt25", "band25-45", "adx_gt45", "frozen_spans_expiry", "frozen_no_expiry_in_hold"):
            i, o = X["IS"].get(cls, {}).get(k), X["OOS"].get(cls, {}).get(k)
            cells.append(f"{f3(i['net_mean_R'] if i else None)} \\| {f3(o['net_mean_R'] if o else None)} "
                         f"({o['n'] if o else 0})")
        A(f"| {cls} | " + " | ".join(cells) + " |")
    A("")
    A("## M8 - pooled M-signal entries: expiry-day vs other days (net mean R; OOS)")
    A("| group | expiry day n / days | expiry-day net | other days net | diff [CI] p | IS diff | verdict |")
    A("|---|---|---|---|---|---|---|")
    for vg in VGROUPS:
        o, i = J["M8"]["OOS"][vg], J["M8"]["IS"][vg]
        ex, ot, d = o.get("expiry_day"), o.get("other_days"), o.get("diff_expiry_minus_other")
        di = i.get("diff_expiry_minus_other")
        A(f"| {vg} | {ex['n'] if ex else 0} / {ex['n_days'] if ex else 0} | {f3(ex['net_mean_R'] if ex else None)} | "
          f"{f3(ot['net_mean_R'] if ot else None)} | {f3(d['diff'] if d else None)} "
          f"[{f3(d['ci'][0] if d else None)}, {f3(d['ci'][1] if d else None)}] {d['p'] if d else float('nan'):.3f} | "
          f"{f3(di['diff'] if di else None)} | {J['M8']['verdict'][vg]['verdict']} |")
    A("")
    A("## Reading (blunt)")
    g = lambda hyp, cls, k="net_mean_R": (H[hyp].get(cls) or {}).get("OOS", {}) and H[hyp][cls]["OOS"]["signal"][k]
    dB = lambda hyp, cls: H[hyp][cls]["OOS"]["diff_vs_b1"]
    cp = lambda hyp, cls: H[hyp][cls]["OOS"].get("signal_minus_opposite", {}).get("diff")
    for hyp in HYPS:
        parts = []
        for vg in VGROUPS:
            if not (H[hyp].get(vg) or {}).get("OOS"):
                continue
            d = dB(hyp, vg)
            parts.append(f"{vg} OOS {f3(g(hyp, vg))} R (IS {f3(H[hyp][vg]['IS']['signal']['net_mean_R'])}), "
                         f"vs B1 {f3(d['diff'])} p={d['p']:.2f}, C-P {f3(cp(hyp, vg))} -> {H[hyp][vg]['verdict']}")
        A(f"- **{hyp}** (expected {EXPECT[hyp]}): " + "; ".join(parts) + ". " + COMMENT.get(hyp, ""))
    o = J["M8"]["OOS"]["index"].get("diff_expiry_minus_other", {})
    i = J["M8"]["IS"]["index"].get("diff_expiry_minus_other", {})
    A(f"- **M8** (expected {EXPECT['M8']}): index expiry-day entries minus other days OOS {f3(o.get('diff'))} "
      f"(raw p={o.get('p', float('nan')):.3f}) but IS {f3(i.get('diff'))} - sign flips, so FAIL. " + COMMENT["M8"])
    path = os.path.join(OUT, "p4_momentum.md")
    assert len(L) <= 110, len(L)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
