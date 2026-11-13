"""P4 Stage 2 - SYNTHETIC option P&L for M6 (opening-range breakout), M7 (first half-hour -> last
half-hour) and the intraday baseline B2 (buy the standard call AND put at 09:20, sell 15:15, every day).

Every option price here is SYNTHETIC: P1 model b (previous-day OTM smile in log K/F, forward
F_p * U_m / U_p from the real 1-min underlying), Black-76, TRADING-TIME theta (the close(p)->close(d)
decay is spent linearly over the 375 session minutes; calendar-time run logged as registered=no).
Standard contract: nearest expiry with >= 8 calendar days (stocks: monthly), liquid previous-day strike
(vol >= 500 idx / 50 stk contracts, close >= Rs2) with model |delta| closest to 0.40 at the entry minute.
Fills: buy mid + half-spread, sell mid - half-spread (engine/spread.py p75; p90 BANKNIFTY), full
costs (engine/costs.py), 1 lot (data/ref/lots.parquet).  R = net P&L / (premium paid + costs).

B1 (random entry, the §0 comparison): M6 -> every eligible instrument-day, (entry minute, hold) drawn
jointly from that variant's trades of the same class, coin-flip side.  M7 -> coin-flip side at 15:00.
Outputs: data/bt/intraday/{m6,m7,b2}.parquet (+ m6_random, m7_random), research/out/p4_intraday.json
(key "stage2"), research/out/p4_intraday_{stage1,equity,heatmap}.png, research/trials_log.csv rows.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm, skew, kurtosis

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "research" / "scripts"))
import lib.p4_intraday as P  # noqa: E402
import p4_intraday_stage1 as S1  # noqa: E402

OUT = ROOT / "research" / "out"
OUTJ = OUT / "p4_intraday.json"
TRIALS = ROOT / "research" / "trials_log.csv"
HEADER = ["id", "variant", "registered", "sample", "cls", "style", "n_trades", "n_days", "gross_mean_R",
          "net_mean_R", "ci_lo", "ci_hi", "p_vs_b1", "net_mean_R_2x_spread", "verdict", "notes", "run_ts", "script"]
HAIRCUT = 0.10
GRID_L = (10, 15, 30)
GRID_X = {"1445": 330, "1515": 360}
RUN_TS = time.strftime("%Y-%m-%dT%H:%M:%S")


def finish(ev, info):
    ev = S1.annotate(ev, info)
    ev = ev[(ev["sample"] != "pre") & ev["net_R"].notna()].copy()
    ev["net_R_2x"] = P.net_from(ev, 2.0)
    ev["net_R_hc"] = ev["net_R"] - HAIRCUT
    return ev.sort_values(["date", "entry_m", "symbol"]).reset_index(drop=True)


def b2_trades(mats, contracts, entry_m=5, exit_m=360):
    rows = []
    for s, (days, _) in mats.items():
        d = np.array([x for x in days if x in contracts.get(s, {})], dtype="datetime64[D]")
        for sd in (1, -1):
            rows.append(pd.DataFrame({"symbol": s, "date": d, "side": sd, "entry_m": entry_m, "exit_m": exit_m,
                                      "reason": "time"}))
    return pd.concat(rows, ignore_index=True)


def m7_random(m7, seed=7):
    rng = np.random.default_rng(seed)
    r = m7[["symbol", "date", "entry_m", "exit_m"]].copy()
    r["side"] = rng.choice([-1, 1], len(r))
    r["reason"] = "random"
    return r


def buyer_metrics(g):
    x = g["net_R"].values
    if not len(x):
        return {}
    w, l = x[x > 0], x[x <= 0]
    cum = np.cumsum(x)
    dd = float((np.maximum.accumulate(np.r_[0, cum])[1:] - cum).max())
    streak = best = 0
    for v in x:
        streak = streak + 1 if v <= 0 else 0
        best = max(best, streak)
    return {"win_rate": float(len(w) / len(x)), "avg_win": float(w.mean()) if len(w) else 0.0,
            "avg_loss": float(l.mean()) if len(l) else 0.0,
            "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else float("inf"),
            "max_dd_R": dd, "longest_losing_streak": int(best),
            "hold_hist_30min": np.histogram(g["hold_min"], np.arange(0, 390, 30))[0].tolist(),
            "mean_hold_min": float(g["hold_min"].mean())}


def stat(g, base=None):
    m, lo, hi, _ = P.cluster_boot(g["net_R"].values, g["date"].values, seed=11)
    o = {"n_trades": int(len(g)), "n_days": int(g["date"].nunique()), "gross_mean_R": float(g["gross_R"].mean()),
         "net_mean_R": m, "ci_lo": lo, "ci_hi": hi, "net_mean_R_2x": float(np.nanmean(g["net_R_2x"])),
         "net_mean_R_haircut": float(g["net_R_hc"].mean()), "median_R": float(g["net_R"].median())}
    if base is not None and len(base):
        d, dlo, dhi, p = P.cluster_boot_diff(g["net_R"], g["date"], base["net_R"], base["date"], seed=12)
        o.update({"diff_vs_b1": d, "diff_lo": dlo, "diff_hi": dhi, "p_vs_b1": p})
    # deflated-Sharpe inputs (day-level mean R)
    dm = g.groupby("date")["net_R"].mean().values
    o["sr_day"] = float(dm.mean() / dm.std(ddof=1)) if len(dm) > 2 and dm.std() > 0 else float("nan")
    o["sr_skew"] = float(skew(dm)) if len(dm) > 2 else float("nan")
    o["sr_kurt"] = float(kurtosis(dm, fisher=False)) if len(dm) > 2 else float("nan")
    o["sr_T"] = int(len(dm))
    return o


def groups(ev, base=None):
    """stats by (sample, cls) incl. cls='all'."""
    out = {}
    for smp in ("IS", "OOS"):
        for c in ("index", "stock", "all"):
            g = ev[(ev["sample"] == smp) & ((ev["cls"] == c) | (c == "all"))]
            b = None
            if base is not None:
                b = base[(base["sample"] == smp) & ((base["cls"] == c) | (c == "all"))]
            if len(g):
                out[f"{smp}|{c}"] = stat(g, b)
    return out


def dsr(sr, T, sk, ku, n_trials, var_sr):
    if not (np.isfinite(sr) and T > 2 and n_trials > 1):
        return float("nan")
    g = 0.5772156649
    sr0 = np.sqrt(var_sr) * ((1 - g) * norm.ppf(1 - 1 / n_trials) + g * norm.ppf(1 - 1 / (n_trials * np.e)))
    den = np.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr * sr, 1e-9))
    return float(norm.cdf((sr - sr0) * np.sqrt(T - 1) / den))


def verdict(st, key_is, key_oos, plateau_ok, stage1_ok=True):
    """§0 bar on OOS."""
    o, i = st.get(key_oos, {}), st.get(key_is, {})
    if not o:
        return "no data"
    if o["n_trades"] < 100 or o["n_days"] < 40:
        return "anecdotal"
    c1 = o["net_mean_R"] > 0 and o["ci_lo"] > 0
    c2 = o.get("p_vs_b1", 1) < 0.05
    c3 = i and np.sign(i["net_mean_R"]) == np.sign(o["net_mean_R"]) and o["net_mean_R"] >= 0.5 * i["net_mean_R"]
    c6 = o["net_mean_R_2x"] >= 0
    hc = o["net_mean_R_haircut"] > 0
    if c1 and c2 and c3 and plateau_ok and c6 and hc and stage1_ok:
        return "PASS"
    if c2 and o["net_mean_R"] <= 0:
        return "less bad, not an edge"
    return "FAIL"


def monthly(ev):
    m = ev.assign(month=pd.to_datetime(ev["date"]).dt.strftime("%Y-%m"))
    out = {}
    for c in ("index", "stock"):
        g = m[m["cls"] == c].groupby("month")["net_R"].agg(["sum", "mean", "count"])
        out[c] = {"month": g.index.tolist(), "sum_R": g["sum"].round(4).tolist(),
                  "cum_R": g["sum"].cumsum().round(4).tolist(), "mean_R": g["mean"].round(4).tolist(),
                  "n": g["count"].astype(int).tolist()}
    return out


RBINS = np.round(np.arange(-1.0, 2.0001, 0.05), 2)


def rdist(ev):
    return {f"{smp}|{c}": np.histogram(ev.loc[(ev["sample"] == smp) & (ev["cls"] == c), "net_R"].clip(-1, 2),
                                       RBINS)[0].tolist() for smp in ("IS", "OOS") for c in ("index", "stock")}


def log_rows(rows):
    from lib.trials import log_trial
    for r in rows:
        log_trial(**{h: r.get(h, "") for h in HEADER})


def trial_rows(hid, variant, registered, st, verdicts, notes, two_x=True):
    rows = []
    for k, v in st.items():
        smp, c = k.split("|")
        rows.append({"id": hid, "variant": variant, "registered": registered, "sample": smp, "cls": c,
                     "style": "intraday_synth", "n_trades": v["n_trades"], "n_days": v["n_days"],
                     "gross_mean_R": round(v["gross_mean_R"], 5), "net_mean_R": round(v["net_mean_R"], 5),
                     "ci_lo": round(v["ci_lo"], 5), "ci_hi": round(v["ci_hi"], 5),
                     "p_vs_b1": round(v["p_vs_b1"], 4) if isinstance(v.get("p_vs_b1"), float) else "",
                     "net_mean_R_2x_spread": round(v["net_mean_R_2x"], 5) if two_x else "",
                     "verdict": verdicts.get(c, "") if smp == "OOS" else "", "notes": notes, "run_ts": RUN_TS,
                     "script": "research/scripts/p4_intraday_stage2.py"})
    return rows


def main():
    t0 = time.time()
    syms = P.universe()
    mats, info, lc = P.load_all(syms)
    contracts, _ = P.load_contracts(syms, mats)
    lots = pd.read_parquet(ROOT / "data" / "ref" / "lots.parquet")
    ev = lambda tr, **kw: finish(P.evaluate(tr, mats, contracts, lc, lots, **kw), info)
    res, rows = {}, []

    # ---- M6 grid (central = L15 / 15:15) + per-cell random baseline
    m6_all, r6_all, grid = [], [], {}
    for L in GRID_L:
        for xn, xm in GRID_X.items():
            v = f"L{L}_x{xn}"
            central = (L == 15 and xn == "1515")
            tr = S1.m6_trades(mats, L=L, exit_m=xm)
            m6 = ev(tr, path_stats=central, need_req=central)
            rnd = ev(S1.random_trades(m6, mats, contracts, exit_m=xm, seed=100 + L + xm))
            st = groups(m6, rnd)
            grid[v] = {"m6": st, "rand": groups(rnd)}
            m6_all.append(m6.assign(variant=v))
            r6_all.append(rnd.assign(variant=v))
            print(f"M6 {v}: n={len(m6)} OOS idx {st.get('OOS|index', {}).get('net_mean_R', np.nan):+.4f} "
                  f"stk {st.get('OOS|stock', {}).get('net_mean_R', np.nan):+.4f} ({time.time() - t0:.0f}s)")
    M6 = pd.concat(m6_all, ignore_index=True)
    R6 = pd.concat(r6_all, ignore_index=True)
    c6 = M6[M6["variant"] == "L15_x1515"]
    cr6 = R6[R6["variant"] == "L15_x1515"]

    # ---- B2 and M7
    B2 = ev(b2_trades(mats, contracts), path_stats=True)
    m7tr = S1.m7_trades(mats)
    M7 = ev(m7tr[m7tr["side"] != 0], need_req=True)
    R7 = ev(m7_random(m7tr))
    # ---- calendar-time theta (unregistered sensitivity)
    c6cal = ev(S1.m6_trades(mats), theta="calendar")
    B2cal = ev(b2_trades(mats, contracts), theta="calendar")
    print(f"evaluated all ({time.time() - t0:.0f}s)")

    st6, st7, stb2 = grid["L15_x1515"]["m6"], groups(M7, R7), groups(B2)
    # M6 vs B2 and M7 vs B2
    vs_b2 = {}
    for nm, X in (("m6", c6), ("m7", M7), ("rand6", cr6)):
        for smp in ("IS", "OOS"):
            for c in ("index", "stock"):
                a = X[(X["sample"] == smp) & (X["cls"] == c)]
                b = B2[(B2["sample"] == smp) & (B2["cls"] == c)]
                d, lo, hi, p = P.cluster_boot_diff(a["net_R"], a["date"], b["net_R"], b["date"], seed=13)
                vs_b2[f"{nm}|{smp}|{c}"] = {"diff": d, "lo": lo, "hi": hi, "p_one_sided": p}

    # plateau: every other grid cell keeps net > 0 and >= 50% of central (OOS)
    def plateau(c):
        cen = st6.get(f"OOS|{c}", {}).get("net_mean_R", np.nan)
        cells = [grid[v]["m6"].get(f"OOS|{c}", {}).get("net_mean_R", np.nan) for v in grid if v != "L15_x1515"]
        return bool(cen > 0 and all(x > 0 and x >= 0.5 * cen for x in cells))

    s1 = json.loads(OUTJ.read_text())["stage1"] if OUTJ.exists() else {}
    s1_ok = {c: s1.get("m6", {}).get("by_cls_sample", {}).get(f"{c}|OOS", {}).get("move_over_req_means", 0) > 1
             for c in ("index", "stock")}
    s1_ok["all"] = s1.get("m6", {}).get("all", {}).get("move_over_req_means", 0) > 1
    v6 = {c: verdict(st6, f"IS|{c}", f"OOS|{c}", plateau(c) if c != "all" else (plateau("index") and plateau("stock")),
                     s1_ok[c]) for c in ("index", "stock", "all")}
    v7 = {c: verdict(st7, f"IS|{c}", f"OOS|{c}", True) for c in ("index", "stock", "all")}

    # ---- trials log rows
    for v in grid:
        cen = v == "L15_x1515"
        rows += trial_rows("M6", v + ("" if cen else "_plateau"), "yes", grid[v]["m6"], v6 if cen else {},
                           "central" if cen else "plateau cell (§0 check 4)")
        rows += trial_rows("B1_M6", v + "_random_entry", "yes", grid[v]["rand"], {}, "random-entry baseline for M6")
    hc = {k: dict(v, net_mean_R=v["net_mean_R_haircut"], ci_lo=v["ci_lo"] - HAIRCUT, ci_hi=v["ci_hi"] - HAIRCUT,
                  net_mean_R_2x=v["net_mean_R_2x"] - HAIRCUT, p_vs_b1="") for k, v in st6.items()}
    rows += trial_rows("M6", "L15_x1515_haircut10pct", "yes", hc, v6, "net R minus 0.10 per trade (P1 synthetic error)")
    rows += trial_rows("M7", "central", "yes", st7, v7, "B1 = coin-flip side 15:00->15:25")
    rows += trial_rows("B1_M7", "random_side", "yes", groups(R7), {}, "random-side baseline for M7")
    rows += trial_rows("B2", "0920_1515_both_sides", "yes", stb2, {}, "intraday baseline")
    rows += trial_rows("M6", "L15_x1515_calendar_theta", "no", groups(c6cal), {}, "theta convention sensitivity")
    rows += trial_rows("B2", "calendar_theta", "no", groups(B2cal), {}, "theta convention sensitivity")
    if "--no-log" not in sys.argv:
        log_rows(rows)
    from lib.trials import n_trials as _nt
    n_trials = max(_nt(), len(rows))
    srs = [r for r in rows if r["sample"] == "OOS"]
    all_st = {**{f"M6|{k}": v for k, v in st6.items()}, **{f"M7|{k}": v for k, v in st7.items()},
              **{f"B2|{k}": v for k, v in stb2.items()}}
    var_sr = np.nanvar([v["sr_day"] for k, v in all_st.items() if "OOS" in k]) or 1e-4
    for k, v in all_st.items():
        v["dsr"] = dsr(v["sr_day"], v["sr_T"], v["sr_skew"], v["sr_kurt"], n_trials, var_sr)

    # ---- buyer metrics / chart data
    bm = {}
    for nm, X in (("m6", c6), ("m7", M7), ("b2", B2), ("rand6", cr6)):
        for smp in ("IS", "OOS"):
            for c in ("index", "stock"):
                bm[f"{nm}|{smp}|{c}"] = buyer_metrics(X[(X["sample"] == smp) & (X["cls"] == c)])
    heat = {f"{smp}|{c}|{k}": [[grid[f"L{L}_x{xn}"]["m6"].get(f"{smp}|{c}", {}).get(k, None) for xn in GRID_X]
                                for L in GRID_L]
            for smp in ("IS", "OOS") for c in ("index", "stock") for k in ("net_mean_R", "net_mean_R_2x")}
    res = {"conventions": {"theta": "trading-time (close-to-close decay spent over 375 session minutes)",
                           "model": "P1 model b sticky-moneyness, previous-day smile", "haircut": HAIRCUT,
                           "R": "net pnl / (premium paid at ask + all costs)", "SYNTHETIC": True},
           "m6_grid": grid, "m6": st6, "m7": st7, "b2": stb2, "vs_b2": vs_b2,
           "calendar_theta": {"m6": groups(c6cal), "b2": groups(B2cal)},
           "verdicts": {"M6": v6, "M7": v7, "stage1_ok": s1_ok,
                        "plateau_ok": {c: plateau(c) for c in ("index", "stock")}},
           "buyer_metrics": bm, "heatmap": {"rows_L": list(GRID_L), "cols_exit": list(GRID_X), "cells": heat},
           "equity_monthly": {"m6": monthly(c6), "m7": monthly(M7), "b2": monthly(B2), "rand6": monthly(cr6)},
           "r_dist": {"bins": RBINS.tolist(), "m6": rdist(c6), "m7": rdist(M7), "b2": rdist(B2), "rand6": rdist(cr6)},
           "m6_min_premium_drawdown_median": float((c6["min_P"] / c6["P_e"] - 1).median()),
           "n_trials_rows": n_trials, "deflated_sharpe": {k: v["dsr"] for k, v in all_st.items()},
           "exit_reason_share": {f"{a}|{b}": float(v) for (a, b), v in
                                 c6.groupby("cls")["reason"].value_counts(normalize=True).round(4).items()}}
    j = json.loads(OUTJ.read_text()) if OUTJ.exists() else {}
    j["stage2"] = res
    j["stage2_run"] = RUN_TS
    OUTJ.write_text(json.dumps(j, indent=1, default=lambda o: None if o is None else (
        float(o) if np.isscalar(o) else str(o))))

    # ---- trade tables
    keep = ["symbol", "cls", "sample", "date", "side", "entry_m", "exit_m", "reason", "hold_min", "K", "dte",
            "absdelta_e", "iv_e", "P_e", "P_x", "hs_e", "hs_x", "lot", "cost", "U_e", "U_x", "move", "req", "sigma",
            "gross_R", "net_R", "net_R_2x", "net_R_hc", "min_P"]
    sel = lambda X, extra=(): X[[c for c in keep + list(extra) if c in X.columns]]
    sel(M6, ["variant"]).to_parquet(P.BT / "m6.parquet", index=False)
    sel(R6, ["variant"]).to_parquet(P.BT / "m6_random.parquet", index=False)
    sel(M7, ["r1", "r2"]).to_parquet(P.BT / "m7.parquet", index=False)
    sel(R7).to_parquet(P.BT / "m7_random.parquet", index=False)
    sel(B2).to_parquet(P.BT / "b2.parquet", index=False)
    charts(s1, res)

    print("SYNTHETIC option P&L - net mean R [95% day-clustered CI] | 2x spread | -10% haircut | p vs B1")
    for nm, st in (("M6", st6), ("M7", st7), ("B2", stb2)):
        for k, v in st.items():
            print(f"{nm} {k:10s} n={v['n_trades']:6d} d={v['n_days']:4d} gross {v['gross_mean_R']:+.4f} "
                  f"net {v['net_mean_R']:+.4f} [{v['ci_lo']:+.4f},{v['ci_hi']:+.4f}] 2x {v['net_mean_R_2x']:+.4f} "
                  f"hc {v['net_mean_R_haircut']:+.4f} p {v.get('p_vs_b1', float('nan')):.3f}")
    print("verdicts", v6, v7, "trials rows", n_trials, f"({time.time() - t0:.0f}s)")


def charts(s1, res):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # 1 stage-1 distributions
    if s1:
        b = np.array(s1["dist"]["bins_move_sig"])
        x = 0.5 * (b[1:] + b[:-1])
        fig, ax = plt.subplots(1, 2, figsize=(11, 3.8), sharey=False)
        for a, c in zip(ax, ("index", "stock")):
            for k, lab, col in (("m6|%s|move_sig", "M6 realised move", "C0"), ("rand|%s|move_sig", "random entry", "C7"),
                                ("m6|%s|req_sig", "required move (SYNTHETIC)", "C3")):
                h = np.array(s1["dist"][k % c], float)
                a.plot(x, h / h.sum(), label=lab, color=col)
            a.axvline(0, color="k", lw=0.5)
            a.set_title(f"Stage 1 {c}: entry->exit move in sigma_daily units")
            a.set_xlabel("move / sigma_daily (signed in trade direction)")
        ax[0].legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(OUT / "p4_intraday_stage1.png", dpi=110)
        plt.close(fig)
    # 2 equity
    fig, ax = plt.subplots(1, 2, figsize=(12, 4))
    for a, c in zip(ax, ("index", "stock")):
        for nm, col in (("m6", "C0"), ("rand6", "C7"), ("m7", "C2"), ("b2", "C3")):
            e = res["equity_monthly"][nm][c]
            a.plot(pd.to_datetime(e["month"]), np.array(e["cum_R"]) / 1, label=nm.upper(), color=col)
        a.axvline(pd.Timestamp("2024-01-01"), color="k", ls="--", lw=0.8)
        a.set_title(f"SYNTHETIC option P&L, {c}: cumulative net R (1 lot/trade)")
        a.set_ylabel("sum of net R")
    ax[0].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(OUT / "p4_intraday_equity.png", dpi=110)
    plt.close(fig)
    # 3 heatmap
    fig, ax = plt.subplots(1, 4, figsize=(13, 3.2))
    for a, (smp, c) in zip(ax, [(s, c) for s in ("IS", "OOS") for c in ("index", "stock")]):
        g = np.array([[np.nan if v is None else v for v in r] for r in res["heatmap"]["cells"][f"{smp}|{c}|net_mean_R"]])
        im = a.imshow(g, cmap="RdBu", vmin=-0.2, vmax=0.2)
        for i in range(g.shape[0]):
            for k in range(g.shape[1]):
                a.text(k, i, f"{g[i, k]:+.3f}", ha="center", va="center", fontsize=8)
        a.set_xticks(range(len(GRID_X)), [f"exit {x[:2]}:{x[2:]}" for x in GRID_X])
        a.set_yticks(range(len(GRID_L)), [f"OR {L}m" for L in GRID_L])
        a.set_title(f"SYNTHETIC M6 net R {smp} {c}", fontsize=9)
    fig.colorbar(im, ax=ax, shrink=0.8)
    fig.savefig(OUT / "p4_intraday_heatmap.png", dpi=110, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    main()
