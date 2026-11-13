"""P4 Stage 1 - M6 (opening-range breakout) and M7 (first half-hour -> last half-hour) on the REAL
1-minute underlying.  The only option input is the break-even "required move" of the standard
contract (delta 0.40, nearest expiry >= 8 days, previous-day smile, Black-76 model b, trading-time
theta, 75th-pct spread (90th BANKNIFTY) both legs, full costs, 1 lot) - that number is SYNTHETIC.

M6: range = bars 09:15-09:29; entry = first 1-min close beyond the range from the 09:29 bar's
    close (09:30) on, last entry 15:00; exit 15:15 or first close back through the range midpoint.
Random baseline: every eligible instrument-day, (entry minute, holding minutes) drawn jointly from
    the M6 trades of the same class, side = coin flip, exit capped at 15:15.
M7: side = sign(price 09:45 / open 09:15 - 1); entry 15:00, exit 15:25.

Outputs: research/out/p4_intraday.json (key "stage1"), data/bt/intraday/stage1_{m6,rand,m7}.parquet
Usage:   python research/scripts/p4_intraday_stage1.py [--refresh-chains]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
import lib.p4_intraday as P  # noqa: E402

OUTJ = ROOT / "research" / "out" / "p4_intraday.json"
SEED = 20261113


def m6_trades(mats, L=15, exit_m=360):
    rows = []
    for s, (days, m) in mats.items():
        sg = P.orb_signals(m["c"], m["h"], m["l"], L=L, exit_m=exit_m)
        ok = sg["side"] != 0
        rows.append(pd.DataFrame({"symbol": s, "date": days[ok], "side": sg["side"][ok],
                                  "entry_m": sg["entry_m"][ok], "exit_m": sg["exit_m"][ok],
                                  "reason": sg["reason"][ok]}))
    return pd.concat(rows, ignore_index=True)


def random_trades(base: pd.DataFrame, mats, contracts, exit_m=360, seed=SEED):
    """Same joint (entry minute, hold) distribution as `base` per class; random side; every eligible day."""
    rng = np.random.default_rng(seed)
    base = base[base["net_R"].notna()] if "net_R" in base else base
    pools = {c: g[["entry_m", "exit_m"]].values for c, g in base.assign(cls=base.symbol.map(P.cls_of)).groupby("cls")}
    rows = []
    for s, (days, _) in mats.items():
        d = np.array([x for x in days if x in contracts.get(s, {})], dtype="datetime64[D]")
        pool = pools.get(P.cls_of(s))
        if not len(d) or pool is None:
            continue
        pick = pool[rng.integers(0, len(pool), len(d))]
        e = pick[:, 0]
        x = np.minimum(e + (pick[:, 1] - pick[:, 0]), exit_m)
        rows.append(pd.DataFrame({"symbol": s, "date": d, "side": rng.choice([-1, 1], len(d)),
                                  "entry_m": e, "exit_m": x, "reason": "random"}))
    return pd.concat(rows, ignore_index=True)


def m7_trades(mats):
    rows = []
    for s, (days, m) in mats.items():
        sg = P.m7_signals(m["o"], m["c"])
        rows.append(pd.DataFrame({"symbol": s, "date": days, "side": sg["side"], "entry_m": sg["entry_m"],
                                  "exit_m": sg["exit_m"], "r1": sg["r1"], "r2": sg["r2"]}))
    return pd.concat(rows, ignore_index=True)


def annotate(ev, info):
    ev = ev.merge(info, on=["symbol", "date"], how="left")
    ev["cls"] = ev["symbol"].map(P.cls_of)
    ev["sample"] = ""
    for s, g in ev.groupby("symbol"):
        ev.loc[g.index, "sample"] = P.sample_of(s, g["date"].values)
    ev["move_sig"] = ev["move"] / ev["sigma"]
    ev["req_sig"] = ev["req"] / ev["sigma"]
    ev["excess_bp"] = (ev["move"] - ev["req"]) * 1e4
    ev["clears"] = ev["move"] > ev["req"]
    gs = ev["gap"] / ev["sigma"]
    ev["gap_regime"] = np.where(gs > 0.5, "gap_up", np.where(gs < -0.5, "gap_down", "flat"))
    ev["gap_aligned"] = np.where(ev["gap_regime"] == "flat", "flat",
                                 np.where(np.sign(ev["gap"]) == ev["side"], "with_gap", "against_gap"))
    ev["hold_min"] = ev["exit_m"] - ev["entry_m"]
    return ev


def summ(g):
    g = g[np.isfinite(g["req"]) & g["move"].notna() & g["sigma"].notna()]
    if not len(g):
        return {"n": 0}
    return {"n": int(len(g)), "n_days": int(g["date"].nunique()),
            "hit_rate": float((g["move"] > 0).mean()),
            "clear_rate": float(g["clears"].mean()),
            "mean_move_bp": float(g["move"].mean() * 1e4), "median_move_bp": float(g["move"].median() * 1e4),
            "mean_req_bp": float(g["req"].mean() * 1e4), "median_req_bp": float(g["req"].median() * 1e4),
            "mean_move_sig": float(g["move_sig"].mean()), "median_move_sig": float(g["move_sig"].median()),
            "mean_req_sig": float(g["req_sig"].mean()),
            "move_over_req_means": float(g["move"].mean() / g["req"].mean()),
            "mean_move_in_req_units": float((g["move"] / g["req"]).mean()),
            "median_move_in_req_units": float((g["move"] / g["req"]).median()),
            "mean_excess_bp": float(g["excess_bp"].mean()),
            "mean_hold_min": float(g["hold_min"].mean())}


def table(ev, keys):
    out = {}
    for k, g in ev.groupby(keys):
        out["|".join(map(str, k if isinstance(k, tuple) else (k,)))] = summ(g)
    return out


def main():
    t0 = time.time()
    syms = P.universe()
    mats, info, lc = P.load_all(syms)
    contracts, ch = P.load_contracts(syms, mats, refresh="--refresh-chains" in sys.argv)
    lots = pd.read_parquet(ROOT / "data" / "ref" / "lots.parquet")
    print(f"loaded {len(syms)} symbols, {sum(len(v) for v in contracts.values())} contract-days "
          f"({time.time() - t0:.0f}s)")
    cov = {s: int(len(contracts[s])) for s in syms}

    m6 = P.evaluate(m6_trades(mats), mats, contracts, lc, lots, need_req=True)
    m6 = annotate(m6, info)
    rnd = P.evaluate(random_trades(m6, mats, contracts), mats, contracts, lc, lots, need_req=True)
    rnd = annotate(rnd, info)
    m7 = P.evaluate(m7_trades(mats), mats, contracts, lc, lots, need_req=True)
    m7 = annotate(m7, info)
    P.BT.mkdir(parents=True, exist_ok=True)
    for name, df in (("m6", m6), ("rand", rnd), ("m7", m7)):
        df.to_parquet(P.BT / f"stage1_{name}.parquet", index=False)

    ok = lambda d: d[(d["sample"] != "pre") & d["req"].notna()]
    A, R, M = ok(m6), ok(rnd), ok(m7)
    res = {"n_instrument_days": int(sum(len(v[0]) for v in mats.values())), "contract_days": cov,
           "m6": {"all": summ(A), "by_cls_sample": table(A, ["cls", "sample"]),
                  "by_cls_side": table(A, ["cls", "side"]), "by_cls_gap": table(A, ["cls", "gap_regime"]),
                  "by_cls_gap_aligned": table(A, ["cls", "gap_aligned"]),
                  "by_cls_exit_reason": table(A, ["cls", "reason"]), "by_symbol": table(A, ["symbol"])},
           "rand": {"all": summ(R), "by_cls_sample": table(R, ["cls", "sample"])}}
    # M6 vs random (move in sigma and excess over required move), day-clustered
    cmp = {}
    for (c, smp), g in A.groupby(["cls", "sample"]):
        h = R[(R["cls"] == c) & (R["sample"] == smp)]
        for col in ("move_sig", "excess_bp"):
            gg, hh = g[np.isfinite(g[col])], h[np.isfinite(h[col])]
            d, lo, hi, p = P.cluster_boot_diff(gg[col], gg["date"], hh[col], hh["date"], seed=1)
            cmp[f"{c}|{smp}|{col}"] = {"diff": d, "lo": lo, "hi": hi, "p_one_sided": p}
    res["m6_vs_rand"] = cmp
    # distributions (chart-ready)
    bins_sig = np.linspace(-3, 3, 61)
    res["dist"] = {"bins_move_sig": bins_sig.tolist()}
    for nm, d in (("m6", A), ("rand", R)):
        for c in ("index", "stock"):
            x = d.loc[d["cls"] == c, "move_sig"].clip(-3, 3).dropna()
            res["dist"][f"{nm}|{c}|move_sig"] = np.histogram(x, bins_sig)[0].tolist()
            r = d.loc[d["cls"] == c, "req_sig"].clip(-3, 3).dropna()
            res["dist"][f"{nm}|{c}|req_sig"] = np.histogram(r, bins_sig)[0].tolist()
    res["m6_entry_minute_hist"] = {c: np.histogram(A.loc[A.cls == c, "entry_m"], np.arange(15, 350, 15))[0].tolist()
                                   for c in ("index", "stock")}
    # M7
    m7s = {}
    for (c, smp), g in M.groupby(["cls", "sample"]):
        g = g[(g["side"] != 0) & g["r2"].notna()]
        sr = g["side"] * g["r2"]
        mean, lo, hi, _ = P.cluster_boot(sr * 1e4, g["date"].values, seed=2)
        m7s[f"{c}|{smp}"] = {"n": int(len(g)), "corr_r1_r2": float(np.corrcoef(g["r1"], g["r2"])[0, 1]),
                             "hit_rate": float((sr > 0).mean()), "mean_signed_bp": mean, "ci_lo_bp": lo,
                             "ci_hi_bp": hi, "median_req_bp": float(g["req"].median() * 1e4),
                             "mean_req_bp": float(g["req"].mean() * 1e4),
                             "clear_rate": float(g["clears"].mean())}
    res["m7"] = m7s
    old = json.loads(OUTJ.read_text()) if OUTJ.exists() else {}
    old["stage1"] = res
    old["stage1_run"] = time.strftime("%Y-%m-%d %H:%M")
    OUTJ.write_text(json.dumps(old, indent=1, default=float))

    print("M6 Stage 1 (real underlying; required move SYNTHETIC)")
    print("cls   sample  n      hit   mean_mv_bp req_bp  mv/req  mv_sig  excess_bp clear%")
    for k, v in res["m6"]["by_cls_sample"].items():
        print(f"{k:13s} {v['n']:6d} {v['hit_rate']:.3f} {v['mean_move_bp']:8.1f} {v['mean_req_bp']:7.1f} "
              f"{v['move_over_req_means']:6.2f} {v['mean_move_sig']:6.3f} {v['mean_excess_bp']:8.1f} {v['clear_rate']:.3f}")
    print("random baseline")
    for k, v in res["rand"]["by_cls_sample"].items():
        print(f"{k:13s} {v['n']:6d} {v['hit_rate']:.3f} {v['mean_move_bp']:8.1f} {v['mean_req_bp']:7.1f} "
              f"{v['move_over_req_means']:6.2f} {v['mean_move_sig']:6.3f} {v['mean_excess_bp']:8.1f} {v['clear_rate']:.3f}")
    print("M6 - random (move_sig): " + "; ".join(f"{k.rsplit('|',1)[0]} {v['diff']:+.3f} [{v['lo']:+.3f},{v['hi']:+.3f}]"
                                                for k, v in cmp.items() if k.endswith("move_sig")))
    print("M7: cls|sample n corr hit mean_bp [CI] req_bp")
    for k, v in m7s.items():
        print(f"  {k:13s} {v['n']:6d} {v['corr_r1_r2']:+.3f} {v['hit_rate']:.3f} {v['mean_signed_bp']:+.2f} "
              f"[{v['ci_lo_bp']:+.2f},{v['ci_hi_bp']:+.2f}] {v['mean_req_bp']:.1f}")
    print(f"done {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
