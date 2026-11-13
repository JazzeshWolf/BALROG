"""P4: unconditional baseline B1 (02_hypotheses.md §0) - the standard contract entered on every eligible day.
NO hypothesis filters. Reads data/bt/trades; writes research/out/p4_baseline.md (<= 70 lines) + .json.

  python -I research/scripts/p4_baseline.py [--bt data/bt] [--log]     # --log appends B1 rows to trials_log.csv
Exclusions (all h): holds spanning a ca_break day, trades without an exit mark, holds past the data end.
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
GRPS = ["NIFTY", "BANKNIFTY", "SENSEX", "stocks", "other_index"]
SCRIPT = "research/scripts/p4_baseline.py"


def load(bt_dir):
    cols = ["date", "exchange", "symbol", "cls", "grp", "sample", "opt_type", "is_std", "premium", "lot", "theta",
            "half_spread"]
    for h in bt.H_LIST:
        cols += [f"{c}_h{h}" for c in ("gross_R", "net_R", "net_R2x", "net_rs", "cost", "stale", "settled", "capped",
                                       "ca_break", "nomark", "incomplete", "cal_days", "net_R_open1")]
    fs = sorted(glob.glob(os.path.join(bt_dir, "trades", "year=*.parquet")))
    t = pd.concat([pd.read_parquet(f, columns=cols, filters=[("is_std", "==", True)]) for f in fs], ignore_index=True)
    return t, fs


def cell_stats(d, h):
    ok = d[f"net_R_h{h}"].notna() & ~d[f"ca_break_h{h}"]
    d = d[ok]
    if len(d) == 0:
        return None
    ci = bt.cluster_bootstrap_ci(d[f"net_R_h{h}"].to_numpy(), d["date"].to_numpy(), n_boot=2000, seed=h)
    gci = bt.cluster_bootstrap_ci(d[f"gross_R_h{h}"].to_numpy(), d["date"].to_numpy(), n_boot=2000, seed=h)
    bm = bt.buyer_metrics(d[f"net_rs_h{h}"].to_numpy(), d[f"net_R_h{h}"].to_numpy(), d["date"].to_numpy(),
                          d["theta"].to_numpy(), d["lot"].to_numpy(), d[f"cal_days_h{h}"].to_numpy())
    return {"n": int(len(d)), "n_days": int(d["date"].nunique()), "gross_mean_R": float(gci["mean"]),
            "gross_ci": [gci["lo"], gci["hi"]], "net_mean_R": float(ci["mean"]), "ci_lo": ci["lo"], "ci_hi": ci["hi"],
            "net_mean_R_2x": float(d[f"net_R2x_h{h}"].mean()), "net_mean_R_open1": float(d[f"net_R_open1_h{h}"].mean()),
            "win_rate": bm["win_rate"], **{k: v for k, v in bm.items() if k not in ("n", "win_rate")}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--log", action="store_true")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    t, fs = load(a.bt)
    res, lines = {}, []
    for g in GRPS:
        for side in ("CE", "PE"):
            for smp in ("IS", "OOS"):
                d = t[(t.grp == g) & (t.opt_type == side) & (t["sample"] == smp)]
                for h in bt.H_LIST:
                    s = cell_stats(d, h)
                    if s:
                        res[f"{g}|{side}|{smp}|h{h}"] = s
    # pooled per class (both sides) h=3
    pooled = {}
    for g in GRPS:
        for smp in ("IS", "OOS"):
            s = cell_stats(t[(t.grp == g) & (t["sample"] == smp)], 3)
            if s:
                pooled[f"{g}|{smp}"] = s
    # cost + spread drag, % of premium (round trip, h=3 legs)
    t["spread_drag"] = 2 * t["half_spread"] / t["premium"]
    t["cost_drag"] = t["cost_h3"] / (t["premium"] * t["lot"])
    drag = t.groupby("grp")[["spread_drag", "cost_drag"]].agg(["mean", "median"]) * 100
    flags = {f"h{h}": {k: float(t[f"{k}_h{h}"].mean()) for k in ("stale", "settled", "capped", "ca_break", "nomark",
                                                                    "incomplete")} for h in bt.H_LIST}
    span = {g: [str(t.loc[t.grp == g, "date"].min())[:10], str(t.loc[t.grp == g, "date"].max())[:10]] for g in GRPS
            if (t.grp == g).any()}

    L = lines.append
    L("# P4 - Baseline B1: unconditional standard-contract buys (no signal)")
    L(f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} by `{SCRIPT}` from `data/bt/trades` ({len(fs)} year files, "
      f"{len(t):,} standard-contract rows). Protocol §0: std expiry, |delta|~0.40, floors, close+hs / close-hs fills "
      f"(p75, BANKNIFTY p90), statutory costs, 1 lot, time exit. CI = day-clustered bootstrap 95% (2,000). "
      f"Excluded: holds spanning ca_break, no exit mark, past data end. {a.note}")
    L("")
    L("Spans: " + "; ".join(f"{g} {v[0]}..{v[1]}" for g, v in span.items()))
    L("")
    L("## Pooled CE+PE, h=3 (default hold)")
    L("| class | sample | n | days | gross mean R | net mean R [95% CI] | net 2x spread | enter t+1 open | win | PF | theta share of losses |")
    L("|---|---|---|---|---|---|---|---|---|---|---|")
    for k, s in pooled.items():
        g, smp = k.split("|")
        L(f"| {g} | {smp} | {s['n']:,} | {s['n_days']} | {s['gross_mean_R']:+.3f} | {s['net_mean_R']:+.3f} "
          f"[{s['ci_lo']:+.3f}, {s['ci_hi']:+.3f}] | {s['net_mean_R_2x']:+.3f} | {s['net_mean_R_open1']:+.3f} | "
          f"{s['win_rate']:.0%} | {s['profit_factor']:.2f} | {s.get('theta_share_losers', np.nan):.0%} |")
    L("")
    L("## Net mean R by class x side x hold (gross in brackets); IS | OOS")
    L("| class | side | h=1 | h=2 | h=3 | h=5 |")
    L("|---|---|---|---|---|---|")
    for g in GRPS:
        for side in ("CE", "PE"):
            cells = []
            for h in bt.H_LIST:
                parts = []
                for smp in ("IS", "OOS"):
                    s = res.get(f"{g}|{side}|{smp}|h{h}")
                    parts.append("n/a" if s is None else f"{s['net_mean_R']:+.3f} ({s['gross_mean_R']:+.3f})")
                cells.append(" \\| ".join(parts))
            if any(c != "n/a \\| n/a" for c in cells):
                L(f"| {g} | {side} | " + " | ".join(cells) + " |")
    L("")
    L("## Net mean R h=3 with CI and win rate, by class x side x sample")
    L("| class | side | sample | n | net mean R [CI] | win | avg win / avg loss R | max DD Rs (1 lot) | longest losing streak |")
    L("|---|---|---|---|---|---|---|---|---|")
    for g in GRPS:
        for side in ("CE", "PE"):
            for smp in ("IS", "OOS"):
                s = res.get(f"{g}|{side}|{smp}|h3")
                if s:
                    L(f"| {g} | {side} | {smp} | {s['n']:,} | {s['net_mean_R']:+.3f} [{s['ci_lo']:+.3f}, {s['ci_hi']:+.3f}] | "
                      f"{s['win_rate']:.0%} | {s['avg_win_R']:+.2f} / {s['avg_loss_R']:+.2f} | {s['max_dd_rs']:,.0f} | "
                      f"{s['longest_losing_streak']} |")
    L("")
    L("## Friction: round-trip spread and statutory costs, % of entry premium (mean / median)")
    L("| class | spread | costs (h=3) |")
    L("|---|---|---|")
    for g in drag.index:
        r = drag.loc[g]
        L(f"| {g} | {r[('spread_drag', 'mean')]:.1f} / {r[('spread_drag', 'median')]:.1f} | "
          f"{r[('cost_drag', 'mean')]:.1f} / {r[('cost_drag', 'median')]:.1f} |")
    L("")
    f3, f5 = flags["h3"], flags["h5"]
    L(f"Flags (share of std rows): stale exit h3 {f3['stale']:.2%} / h5 {f5['stale']:.2%}; settle-priced exit "
      f"h3 {f3['settled']:.2%}; no exit mark h3 {f3['nomark']:.2%}; capped (expiry) h3 {f3['capped']:.2%} / h5 "
      f"{f5['capped']:.2%}; ca_break in hold h5 {f5['ca_break']:.2%}; past data end h5 {f5['incomplete']:.2%}.")
    assert len(lines) <= 70, len(lines)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "p4_baseline.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(OUT, "p4_baseline.json"), "w", encoding="utf-8") as f:
        json.dump({"cells": res, "pooled_h3": pooled, "flags": flags, "spans": span,
                   "drag_pct": {g: {f"{a}_{b}": float(drag.loc[g, (a, b)]) for a, b in drag.columns} for g in drag.index}},
                  f, indent=1, default=float)
    if a.log:
        for k, s in res.items():
            g, side, smp, hh = k.split("|")
            trials.log_trial(id="B1", variant=f"std_{side}_{hh}", registered="yes", sample=smp, cls=g, style="swing",
                             n_trades=s["n"], n_days=s["n_days"], gross_mean_R=s["gross_mean_R"],
                             net_mean_R=s["net_mean_R"], ci_lo=s["ci_lo"], ci_hi=s["ci_hi"], p_vs_b1=None,
                             net_mean_R_2x_spread=s["net_mean_R_2x"], verdict="baseline",
                             notes=f"unconditional std contract, time exit; {a.note}".strip(), script=SCRIPT)
    for k, s in pooled.items():
        print(f"{k:22s} n={s['n']:>7,} gross {s['gross_mean_R']:+.3f} net {s['net_mean_R']:+.3f} "
              f"[{s['ci_lo']:+.3f},{s['ci_hi']:+.3f}] win {s['win_rate']:.0%}")
    print(f"wrote research/out/p4_baseline.md ({len(lines)} lines) + .json; logged={a.log} ({len(res)} B1 rows)")


if __name__ == "__main__":
    main()
