"""P4 archive cross-check: re-price B1 standard-contract trades (h=3) with REAL post-close bid/ask from the
xerxes-eod-archive cache and compare with the bhavcopy + modelled-spread backtest.

    python -I research/scripts/p4_archive_crosscheck.py [--bt data/bt] [--archive data/archive] [--out research/out]

Window 2026-09-15 .. 2026-11-12 (archive days with a book). Trades: data/bt/trades is_std rows (both sides), primary
h=3 exclusions, NSE-only stocks. Entry = archive ask on the entry date, exit = archive bid on exit_date_h3, both on
the same (symbol, expiry, strike, type). A trade is dropped when either day's snapshot for the symbol is not `usable`
in data/archive/snap.parquet (holiday re-publish / duplicate / frozen / pre-close) or either quote is not two-sided.
Costs: engine statutory schedule at the real fills (research/lib/backtest.costs_vec), 1 lot.
Three prices per trade: (A) backtest = bhavcopy close +- modelled half-spread (as in every P4 result);
(B) archive mid +- the SAME modelled half-spread (isolates close-vs-mid level); (C) archive ask in / bid out.
Writes research/out/p4_archive_crosscheck.{json,md}.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.lib import backtest as bt

D0, D1 = "2026-09-15", "2026-11-12"


def load_trades(bt_dir):
    cols = ["trade_id", "date", "exchange", "symbol", "cls", "grp", "opt_type", "expiry", "strike", "lot", "premium",
            "half_spread", "exit_date_h3", "exit_mark_h3", "hs_exit_h3", "net_R_h3", "gross_R_h3", "net_rs_h3",
            "ca_break_h3", "incomplete_h3", "settled_h3", "nomark_h3"]
    fs = [f for f in glob.glob(os.path.join(bt_dir, "trades", "year=2026.parquet"))]
    t = pd.concat([pd.read_parquet(f, columns=cols, filters=[("is_std", "==", True)]) for f in fs], ignore_index=True)
    t = t[(t["date"] >= D0) & (t["date"] <= D1)]
    t = t[~((t["cls"].astype(str) == "stock") & (t["exchange"].astype(str) != "nse"))]
    ok = (t["net_R_h3"].notna() & ~t["ca_break_h3"].astype(bool) & ~t["incomplete_h3"].astype(bool)
          & ~t["settled_h3"].astype(bool) & ~t["nomark_h3"].astype(bool))
    return t[ok].copy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--archive", default=os.path.join(ROOT, "data", "archive"))
    ap.add_argument("--out", default=os.path.join(ROOT, "research", "out"))
    a = ap.parse_args()

    t = load_trades(a.bt)
    n_bt = len(t)
    snap = pd.read_parquet(os.path.join(a.archive, "snap.parquet"), columns=["trade_date", "symbol", "usable"])
    good = set(zip(snap.loc[snap["usable"], "trade_date"], snap.loc[snap["usable"], "symbol"].str.upper()))
    ch = pd.read_parquet(os.path.join(a.archive, "chain.parquet"),
                         columns=["trade_date", "symbol", "expiry", "strike", "type", "bid", "ask", "ltp"])
    for c in ("trade_date", "symbol", "expiry", "type"):
        ch[c] = ch[c].astype(str)
    ch["symbol"] = ch["symbol"].str.upper()
    ch = ch[(ch["trade_date"] >= D0) & (ch["trade_date"] <= D1)]
    ch = ch.drop_duplicates(["trade_date", "symbol", "expiry", "strike", "type"])

    t["d_in"] = pd.to_datetime(t["date"]).dt.strftime("%Y-%m-%d")
    t["d_out"] = pd.to_datetime(t["exit_date_h3"]).dt.strftime("%Y-%m-%d")
    t["exp"] = pd.to_datetime(t["expiry"]).dt.strftime("%Y-%m-%d")
    t["sym"] = t["symbol"].astype(str).str.upper()
    t["typ"] = t["opt_type"].astype(str)
    in_arch_sym = t["sym"].isin(set(ch["symbol"]))
    us_in = np.array([(d, s) in good for d, s in zip(t["d_in"], t["sym"])])
    us_out = np.array([(d, s) in good for d, s in zip(t["d_out"], t["sym"])])
    drop = {"symbol_not_in_archive": int((~in_arch_sym).sum()),
            "entry_or_exit_snapshot_unusable": int((in_arch_sym & ~(us_in & us_out)).sum())}
    t = t[in_arch_sym & us_in & us_out]
    ch = ch.rename(columns={"symbol": "sym", "expiry": "exp", "strike": "kk", "type": "typ"})
    t["kk"] = t["strike"].astype(float)
    e = ch.rename(columns={"trade_date": "d_in", "bid": "bid_in", "ask": "ask_in", "ltp": "ltp_in"})
    x = ch.rename(columns={"trade_date": "d_out", "bid": "bid_out", "ask": "ask_out", "ltp": "ltp_out"})
    t = t.merge(e, on=["d_in", "sym", "exp", "kk", "typ"], how="left")
    t = t.merge(x, on=["d_out", "sym", "exp", "kk", "typ"], how="left")
    two = (t["bid_in"] > 0) & (t["ask_in"] >= t["bid_in"]) & (t["bid_out"] > 0) & (t["ask_out"] >= t["bid_out"])
    drop["contract_not_found_or_one_sided"] = int((~two).sum())
    t = t[two].copy()
    lot = t["lot"].to_numpy(float)
    ex = t["exchange"].astype(str).str.upper().to_numpy()
    mid_in = (t["bid_in"] + t["ask_in"]).to_numpy(float) / 2
    mid_out = (t["bid_out"] + t["ask_out"]).to_numpy(float) / 2

    def R(buy, sell):
        c = bt.costs_vec(t["date"].to_numpy(), t["exit_date_h3"].to_numpy(), ex, buy, sell, lot)
        return bt.net_r(buy, sell, lot, c)[1]

    t["R_model"] = t["net_R_h3"].to_numpy(float)
    t["R_mid_model_hs"] = R(mid_in + t["half_spread"].to_numpy(float),
                            np.maximum(mid_out - t["hs_exit_h3"].to_numpy(float), 0))
    t["R_real"] = R(t["ask_in"].to_numpy(float), t["bid_out"].to_numpy(float))
    t["gross_real_mid"] = mid_out / mid_in - 1
    t["rt_model_pct"] = 100 * (t["half_spread"] + t["hs_exit_h3"]) / t["premium"]
    t["rt_real_pct"] = 100 * ((t["ask_in"] - t["bid_in"]) / 2 + (t["ask_out"] - t["bid_out"]) / 2) / mid_in
    t["close_vs_mid_pct"] = 100 * (t["premium"] / mid_in - 1)
    t["g"] = np.where(t["cls"].astype(str) == "stock", "stocks", "index")

    res = {"window": [D0, D1], "n_bt_std_trades_in_window": int(n_bt), "dropped": drop, "by_class": {}}
    L = ["# P4 archive cross-check - modelled spread on bhavcopy vs REAL post-close bid/ask",
         f"Generated by `research/scripts/p4_archive_crosscheck.py`. B1 standard-contract trades, h=3, entries {D0} .. {D1} "
         f"({n_bt:,} backtest trades in window; dropped: {drop}). R: (A) backtest = bhavcopy close +- modelled p75 "
         "half-spread; (B) archive mid +- the same modelled half-spread; (C) REAL: buy archive ask, sell archive bid. "
         "Same statutory costs. CI = day-clustered bootstrap 95%. Archive quotes are post-close books (16:15-22:30 IST).",
         "", "| class | n matched / days | (A) net R model [CI] | (B) mid + model hs | (C) net R REAL [CI] | C - A [CI] | "
         "corr(A,C) | RT spread % model / real (mean, median) | close vs mid % (median) |",
         "|---|---|---|---|---|---|---|---|---|"]
    for g, sel in (("index", t["g"] == "index"), ("NIFTY", t["sym"] == "NIFTY"), ("BANKNIFTY", t["sym"] == "BANKNIFTY"),
                   ("SENSEX", t["sym"] == "SENSEX"), ("stocks", t["g"] == "stocks")):
        s = t[sel]
        if s.empty:
            continue
        d = s["date"].to_numpy()
        A = bt.cluster_bootstrap_ci(s["R_model"], d)
        C = bt.cluster_bootstrap_ci(s["R_real"], d)
        B = bt.cluster_bootstrap_ci(s["R_mid_model_hs"], d, n_boot=200)
        DF = bt.cluster_bootstrap_ci(s["R_real"] - s["R_model"], d)
        corr = float(np.corrcoef(s["R_model"], s["R_real"])[0, 1]) if len(s) > 2 else np.nan
        r = {"n": int(len(s)), "n_days": int(pd.Series(d).nunique()), "net_R_model": A["mean"],
             "net_R_model_ci": [A["lo"], A["hi"]], "net_R_mid_model_hs": B["mean"], "net_R_real": C["mean"],
             "net_R_real_ci": [C["lo"], C["hi"]], "diff_real_minus_model": DF["mean"], "diff_ci": [DF["lo"], DF["hi"]],
             "corr": corr, "rt_spread_model_mean": float(s["rt_model_pct"].mean()),
             "rt_spread_model_median": float(s["rt_model_pct"].median()),
             "rt_spread_real_mean": float(s["rt_real_pct"].mean()), "rt_spread_real_median": float(s["rt_real_pct"].median()),
             "close_vs_mid_median_pct": float(s["close_vs_mid_pct"].median()),
             "gross_model_close": float(s["gross_R_h3"].mean()), "gross_real_mid": float(s["gross_real_mid"].mean()),
             "share_real_worse": float((s["R_real"] < s["R_model"]).mean())}
        res["by_class"][g] = r
        L.append(f"| {g} | {r['n']:,} / {r['n_days']} | {A['mean']:+.3f} [{A['lo']:+.3f}, {A['hi']:+.3f}] | {B['mean']:+.3f} | "
                 f"{C['mean']:+.3f} [{C['lo']:+.3f}, {C['hi']:+.3f}] | {DF['mean']:+.3f} [{DF['lo']:+.3f}, {DF['hi']:+.3f}] | "
                 f"{corr:.2f} | {r['rt_spread_model_mean']:.1f} / {r['rt_spread_real_mean']:.1f} "
                 f"({r['rt_spread_model_median']:.1f} / {r['rt_spread_real_median']:.1f}) | {r['close_vs_mid_median_pct']:+.1f} |")
    L.append("")
    for g in ("index", "stocks"):
        r = res["by_class"].get(g)
        if not r:
            continue
        sign = "worse" if r["diff_real_minus_model"] < 0 else "better"
        sig = r["diff_ci"][0] > 0 or r["diff_ci"][1] < 0
        L.append(f"- **{g}**: real quotes make the buyer {sign} by {r['diff_real_minus_model']:+.3f} R per trade "
                 f"({'significant' if sig else 'CI includes 0'}); modelled RT spread {r['rt_spread_model_mean']:.1f}% vs real "
                 f"{r['rt_spread_real_mean']:.1f}% (means); gross close-to-close {r['gross_model_close']:+.3f} vs real mid-to-mid "
                 f"{r['gross_real_mid']:+.3f}; real worse on {100 * r['share_real_worse']:.0f}% of trades.")
    L.append("- One 2-month, low-vol window (40 sessions with a book); overlapping 3-day holds, so the effective sample is "
             "small. Post-close books are wider than intraday books, so (C) is a conservative fill.")
    with open(os.path.join(a.out, "p4_archive_crosscheck.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1)
    with open(os.path.join(a.out, "p4_archive_crosscheck.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
