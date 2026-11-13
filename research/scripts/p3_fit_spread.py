"""P3 - fit an option bid/ask spread model for bhavcopy backtests.

Data: data/archive/chain.parquet rows with a two-sided book (40 sessions from 2026-09-15), restricted to
usable, non-duplicate snapshots (snap.usable & ~snap.dup_prev). Quotes are post-close (17:20-18:45 IST),
so they are wider than intraday quotes -> the model is conservative for intraday fills.

Target: rel = (ask - bid) / mid. Predictors (bhavcopy-available only): group (NIFTY / BANKNIFTY / SENSEX /
stock liquidity tier), |delta| (Black-76 from the mid, parity forward), calendar DTE, premium (mid here;
bhavcopy close in use), expiry rank, contracts traded.

Two candidates, chosen out-of-sample by date (first 28 sessions fit, last 12 test):
  A. bucket   - empirical p50/p75/p90 per cell grp x |delta| x DTE x premium x rank, hierarchical
               fallback to coarser cells when n < 50.
  B. param    - per group location-scale model: log rel = Xb + exp(Xg) * e, e quantiles empirical.
The winner is refit on all 40 sessions and written to engine/spread_model.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "scripts"))
sys.path.insert(0, str(ROOT / "engine"))
from p1_synth_eod import b76_delta, implied_vol, parity_forward, R  # noqa: E402

CHAIN = ROOT / "data" / "archive" / "chain.parquet"
SNAP = ROOT / "data" / "archive" / "snap.parquet"
OUT_JSON = ROOT / "engine" / "spread_model.json"
OUT_EVAL = ROOT / "research" / "out" / "p3_spread_eval.json"
OUT_PNG = ROOT / "research" / "out" / "p3_spread_model.png"

N_TRAIN = 28
MIN_N = 50
TICK = 0.05
PCTS = (50, 75, 90)
DEL_E = [0.05, 0.15, 0.25, 0.40, 0.60, 0.80]          # |delta| bucket edges (lower bounds of 2nd..)
DTE_E = [2, 7, 20, 45]                                 # dte <=2, 3-7, 8-20, 21-45, 46+
PRM_E = [2, 5, 20, 50, 200]                            # premium <2, 2-5, 5-20, 20-50, 50-200, >200
IDX = ("NIFTY", "BANKNIFTY", "SENSEX")
# fallback levels: which keys are kept (g=group, d=delta, t=dte, p=premium, r=rank)
LEVELS = ["gdtpr", "gdpr", "gtpr", "gpr", "gp", "cp", "c"]


def bidx(x, edges):
    return np.searchsorted(np.asarray(edges, float), np.asarray(x, float), side="right")


# ------------------------------------------------------------------ data
def load() -> pd.DataFrame:
    cols = ["trade_date", "symbol", "cls", "expiry", "dte", "strike", "type", "ltp", "volume", "oi",
            "bid", "ask", "spot", "futPrice", "lotSize"]
    c = pd.read_parquet(CHAIN, columns=cols)
    for col in ["trade_date", "symbol", "cls", "expiry", "type"]:
        c[col] = c[col].astype(str)
    s = pd.read_parquet(SNAP, columns=["trade_date", "symbol", "usable", "dup_prev"])
    s["trade_date"] = s.trade_date.astype(str)
    s["symbol"] = s.symbol.astype(str)
    s = s[s.usable.astype(bool) & ~s.dup_prev.fillna(False).astype(bool)].drop_duplicates(["trade_date", "symbol"])
    c = c.merge(s[["trade_date", "symbol"]], on=["trade_date", "symbol"], how="inner")
    c["dte"] = c.dte.astype(float)
    c = c[(c.dte > 0) & (c.bid > 0) & (c.ask >= c.bid)].copy()
    c["bid"] = c.bid.astype(float)
    c["ask"] = c.ask.astype(float)
    c["mid"] = (c.bid + c.ask) / 2
    c = c[c.mid >= TICK].copy()
    c["spr"] = np.maximum(c.ask - c.bid, TICK)                 # one-tick floor on observed spread too
    c["rel"] = c.spr / c.mid
    c["T"] = c.dte / 365.0
    c["is_call"] = c.type.eq("CE").values
    lot = c.lotSize.astype(float).replace(0, np.nan)
    c["vol_c"] = (c.volume.astype(float) / lot).fillna(0).clip(lower=0)
    # expiry rank among dte>0 expiries
    ex = c[["trade_date", "symbol", "expiry"]].drop_duplicates().sort_values(["trade_date", "symbol", "expiry"])
    ex["rank"] = ex.groupby(["trade_date", "symbol"]).cumcount() + 1
    c = c.merge(ex, on=["trade_date", "symbol", "expiry"])
    # delta from mid (Black-76, parity forward)
    fw = parity_forward(c)
    c = c.merge(fw, on=["trade_date", "symbol", "expiry"], how="left")
    iv = implied_vol(c.mid.values, c.F.values, c.strike.values, c["T"].values, c.is_call.values)
    d = np.abs(b76_delta(c.F.values, c.strike.values, c["T"].values, np.nan_to_num(iv, nan=0.3), c.is_call.values))
    itm = np.where(c.is_call, c.F > c.strike, c.F < c.strike)
    d = np.where(np.isnan(iv), np.where(itm, np.exp(-R * c["T"].values), 0.0), d)
    c["adelta"] = d
    return c.reset_index(drop=True)


def stock_tiers(c: pd.DataFrame) -> dict:
    """Tier 1/2/3 = top/mid/bottom tercile of median daily option traded value (ltp x shares)."""
    st = c[c.cls == "stock"]
    tv = (st.ltp.astype(float) * st.volume.astype(float)).groupby([st.symbol, st.trade_date]).sum()
    med = tv.groupby(level=0).median().sort_values(ascending=False)
    q = np.array_split(med.index.values, 3)
    return {s: i + 1 for i, arr in enumerate(q) for s in arr}


def add_keys(c: pd.DataFrame, tiers: dict) -> pd.DataFrame:
    c["c"] = np.where(c.cls == "index", "index", "stock")
    g = np.where(c.symbol.isin(IDX), c.symbol, "STK" + c.symbol.map(tiers).fillna(2).astype(int).astype(str))
    c["g"] = g
    c["d"] = bidx(c.adelta, DEL_E)
    c["t"] = bidx(c.dte, DTE_E)
    c["p"] = bidx(c.mid, PRM_E)
    c["r"] = np.minimum(c["rank"], 3)
    return c


def key_of(df, lvl):
    cols = {"g": "g", "d": "d", "t": "t", "p": "p", "r": "r", "c": "c"}
    parts = [df[cols[ch]].astype(str) for ch in lvl]
    k = parts[0]
    for p in parts[1:]:
        k = k + "|" + p
    return lvl + ":" + k


# ------------------------------------------------------------------ model A: buckets
def fit_bucket(tr: pd.DataFrame) -> dict:
    cells = {}
    for lvl in LEVELS:
        k = key_of(tr, lvl)
        grp = tr.rel.groupby(k.values)
        qs = grp.quantile([p / 100 for p in PCTS]).unstack()
        n = grp.size()
        mv = tr.vol_c.groupby(k.values).median()          # typical contracts traded: default when volume unknown
        for key in n.index[n >= MIN_N]:
            cells[key] = [round(float(qs.loc[key, p / 100]), 5) for p in PCTS] + [int(n[key]),
                                                                                   round(float(mv[key]), 1)]
    return isotonic_premium(cells)


def isotonic_premium(cells: dict) -> dict:
    """Weighted PAVA per (level, other keys): relative-spread quantiles non-increasing in premium bucket."""
    paths = {}
    for key in cells:
        lvl, vals = key.split(":")
        if "p" not in lvl:
            continue
        v = vals.split("|")
        i = lvl.index("p")
        paths.setdefault((lvl, tuple(v[:i] + ["*"] + v[i + 1:])), []).append((int(v[i]), key))
    for _, lst in paths.items():
        keys = [k for _, k in sorted(lst)]
        for q in range(3):
            blocks = []                                   # [sum w*x, sum w, members]
            for k in keys:
                blocks.append([cells[k][q] * cells[k][3], cells[k][3], [k]])
                # violation: block to the right (pricier) wider than block to the left
                while len(blocks) > 1 and blocks[-1][0] / blocks[-1][1] > blocks[-2][0] / blocks[-2][1]:
                    a = blocks.pop()
                    blocks[-1] = [blocks[-1][0] + a[0], blocks[-1][1] + a[1], blocks[-1][2] + a[2]]
            for sx, w, mem in blocks:
                for k in mem:
                    cells[k][q] = round(sx / w, 5)
    for k in cells:                                       # keep p50 <= p75 <= p90 after smoothing
        cells[k][1] = max(cells[k][1], cells[k][0])
        cells[k][2] = max(cells[k][2], cells[k][1])
    return cells


def pred_bucket(cells: dict, df: pd.DataFrame) -> np.ndarray:
    out = np.full((len(df), 3), np.nan)
    todo = np.ones(len(df), bool)
    for lvl in LEVELS:
        k = key_of(df, lvl).values
        for i in np.where(todo)[0]:
            v = cells.get(k[i])
            if v is not None:
                out[i] = v[:3]
                todo[i] = False
    return out


# ------------------------------------------------------------------ model B: parametric location-scale
def design(df):
    lp = np.log(df.mid.values)
    ad = df.adelta.values
    return np.column_stack([np.ones(len(df)), lp, lp * lp, ad, ad * ad, np.log(df.dte.values),
                            np.log1p(df.vol_c.values), (df.r.values == 2), (df.r.values >= 3)]).astype(float)


def fit_param(tr: pd.DataFrame) -> dict:
    out = {}
    parts = list(tr.groupby("g")) + list(tr.groupby("c"))      # class-level fits = fallback for unknown symbols
    for g, d in parts:
        X = design(d)
        y = np.log(d.rel.values)
        b, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X @ b
        gam, *_ = np.linalg.lstsq(X, np.log(np.abs(res) + 1e-3), rcond=None)
        sc = np.exp(X @ gam)
        z = res / sc
        out[g] = {"b": [round(float(x), 6) for x in b], "g": [round(float(x), 6) for x in gam],
                  "zq": [round(float(np.percentile(z, p)), 5) for p in PCTS], "n": int(len(d))}
    return out


def pred_param(par: dict, df: pd.DataFrame) -> np.ndarray:
    out = np.full((len(df), 3), np.nan)
    for g, idx in df.groupby("g").indices.items():
        m = par.get(g)
        if m is None:
            continue
        X = design(df.iloc[idx])
        loc = X @ np.array(m["b"])
        sc = np.exp(X @ np.array(m["g"]))
        out[idx] = np.exp(loc[:, None] + sc[:, None] * np.array(m["zq"])[None, :])
    return np.minimum(out, 2.0)


# ------------------------------------------------------------------ evaluation
def pinball(y, q, tau):
    e = y - q
    return float(np.mean(np.maximum(tau * e, (tau - 1) * e)))


def floor_rel(pred, mid):
    return np.maximum(pred, TICK / mid[:, None])


def evaluate(te, pred):
    y = te.rel.values
    pred = floor_rel(pred, te.mid.values)
    r = {"pinball": round(np.mean([pinball(y, pred[:, i], p / 100) for i, p in enumerate(PCTS)]), 5)}
    for i, p in enumerate(PCTS):
        r[f"cov{p}"] = round(float(np.mean(y <= pred[:, i] + 1e-12)), 4)
    by = {}
    for g, idx in te.groupby("g").indices.items():
        by[g] = {"n": int(len(idx)), "cov50": round(float(np.mean(y[idx] <= pred[idx, 0] + 1e-12)), 4),
                 "cov75": round(float(np.mean(y[idx] <= pred[idx, 1] + 1e-12)), 4),
                 "cov90": round(float(np.mean(y[idx] <= pred[idx, 2] + 1e-12)), 4),
                 "pinball": round(np.mean([pinball(y[idx], pred[idx, i], p / 100) for i, p in enumerate(PCTS)]), 5)}
    r["by_group"] = by
    return r


def plot(te, pred, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    panels = [("NIFTY", te.g == "NIFTY"), ("BANKNIFTY", te.g == "BANKNIFTY"), ("SENSEX", te.g == "SENSEX"),
              ("Stocks (all tiers)", te.g.str.startswith("STK"))]
    fig, axs = plt.subplots(1, 4, figsize=(16, 4.2), sharey=True)
    bins = np.logspace(np.log10(0.5), np.log10(2000), 22)
    for ax, (name, m) in zip(axs, panels):
        d = te[m.values & (te.r == 1).values]
        pr = pred[m.values & (te.r == 1).values]
        b = np.digitize(d.mid.values, bins)
        xs, a50, a75, p50, p75 = [], [], [], [], []
        for k in np.unique(b):
            s = b == k
            if s.sum() < 30:
                continue
            xs.append(np.exp(np.mean(np.log(d.mid.values[s]))))
            a50.append(np.median(d.rel.values[s]))
            a75.append(np.percentile(d.rel.values[s], 75))
            p50.append(np.median(pr[s, 0]))
            p75.append(np.median(pr[s, 1]))
        sub = d.sample(min(len(d), 4000), random_state=0)
        ax.scatter(sub.mid, sub.rel, s=2, alpha=0.15, color="#9aa5b1", label="test quotes")
        ax.plot(xs, a50, "o-", color="#1f4e79", lw=1.5, ms=3, label="actual p50")
        ax.plot(xs, a75, "s-", color="#c0504d", lw=1.5, ms=3, label="actual p75")
        ax.plot(xs, p50, "--", color="#1f4e79", lw=1.5, label="model p50")
        ax.plot(xs, p75, "--", color="#c0504d", lw=1.5, label="model p75")
        ax.set_xscale("log"); ax.set_yscale("log"); ax.set_ylim(0.003, 2.2)
        ax.set_title(f"{name} - nearest expiry, OOS"); ax.set_xlabel("premium (mid, Rs)")
        ax.grid(alpha=0.3, which="both")
    axs[0].set_ylabel("(ask-bid)/mid"); axs[0].legend(fontsize=7, loc="lower left")
    fig.suptitle("P3 spread model: relative spread vs premium (test = last 12 sessions, post-close quotes)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)


# ------------------------------------------------------------------ main
def main() -> int:
    c = load()
    dates = sorted(c.trade_date.unique())
    tiers = stock_tiers(c[c.trade_date.isin(dates[:N_TRAIN])])
    c = add_keys(c, tiers)
    tr, te = c[c.trade_date.isin(dates[:N_TRAIN])], c[c.trade_date.isin(dates[N_TRAIN:])].reset_index(drop=True)
    print(f"rows {len(c):,}  sessions {len(dates)} ({dates[0]}..{dates[-1]})  train {len(tr):,}  test {len(te):,}")

    cells = fit_bucket(tr)
    pa = pred_bucket(cells, te)
    par = fit_param(tr)
    pb = pred_param(par, te)
    preds = {"bucket": floor_rel(pa, te.mid.values), "param": floor_rel(pb, te.mid.values)}
    preds["ens"] = np.sqrt(preds["bucket"] * preds["param"])
    ev = {k: evaluate(te, v) for k, v in preds.items()}
    for nm, e in ev.items():
        print(f"{nm:7s} pinball {e['pinball']:.5f}  cov50 {e['cov50']:.3f}  cov75 {e['cov75']:.3f}  cov90 {e['cov90']:.3f}")
    # selection: lowest OOS pinball among candidates whose overall p75 coverage is within 75% +/- 2.5pp
    ok = [k for k, e in ev.items() if abs(e["cov75"] - 0.75) <= 0.025] or list(ev)
    win = min(ok, key=lambda k: ev[k]["pinball"])
    print(f"winner: {win}  (calibrated candidates: {ok})")
    for g, v in sorted(ev[win]["by_group"].items()):
        print(f"  {g:10s} n {v['n']:>7,}  cov50 {v['cov50']:.3f}  cov75 {v['cov75']:.3f}  cov90 {v['cov90']:.3f}")
    plot(te, preds[win], OUT_PNG)

    # refit on all sessions (tiers too)
    tiers_all = stock_tiers(c)
    c = add_keys(c, tiers_all)
    model = {
        "version": 1, "model": win, "tick": TICK, "pcts": list(PCTS), "min_n": MIN_N,
        "edges": {"delta": DEL_E, "dte": DTE_E, "premium": PRM_E}, "levels": LEVELS,
        "index_groups": list(IDX), "stock_tier": tiers_all, "default_stock_tier": 2,
        "fit": {"sessions": len(dates), "first": dates[0], "last": dates[-1], "rows": int(len(c)),
                "quotes": "post-close 17:20-18:45 IST (wider than intraday; conservative)",
                "premium": "mid of bid/ask; use bhavcopy close/settle as proxy"},
        "oos": {"train_sessions": N_TRAIN, "test_sessions": len(dates) - N_TRAIN,
                "rule": "min pinball s.t. |cov75-0.75|<=0.025", **ev},
        "cells": fit_bucket(c),
        "param": fit_param(c),
        "median_vol_c": {g: float(np.median(d.vol_c)) for g, d in list(c.groupby("g")) + list(c.groupby("c"))},
    }
    OUT_JSON.write_text(json.dumps(model, separators=(",", ":")))
    print(f"wrote {OUT_JSON.relative_to(ROOT)} ({OUT_JSON.stat().st_size/1e3:.0f} kB), {len(model['cells'])} cells")

    # consistency: stdlib engine/spread.py vs pandas implementation on a sample
    import importlib
    import spread as S
    importlib.reload(S)
    smp = c.sample(3000, random_state=1).reset_index(drop=True)
    fp = {"bucket": pred_bucket(model["cells"], smp), "param": pred_param(model["param"], smp)}
    fp["ens"] = np.sqrt(fp["bucket"] * fp["param"])
    eng = np.array([S.rel_quantiles(r.cls, r.symbol, r.adelta, r.dte, r.mid, r.vol_c, r["rank"])
                    for _, r in smp.iterrows()])
    print(f"engine vs fit max |log diff| = {np.nanmax(np.abs(np.log(eng) - np.log(fp[win]))):.2e}")

    # empirical p50/p75 table, nearest expiry, by class x |delta| x DTE
    c["cl4"] = np.where(c.g.str.startswith("STK"), "stocks", c.g)
    tb = c[(c.r == 1) & c.d.between(1, 5)].groupby(["cl4", "d", "t"]).rel
    tab = pd.DataFrame({"p50": tb.median(), "p75": tb.quantile(0.75), "n": tb.size()}).reset_index()
    tab = tab[tab.n >= MIN_N]

    # representative trades: per group median |delta| and dte around each premium, nearest expiry
    ex = []
    rep = {f"STK{k}": next(s for s, v in sorted(tiers_all.items()) if v == k) for k in (1, 2, 3)}
    for g, prems in [(g, (100, 20, 5)) for g in IDX] + [(g, (20, 5, 2)) for g in ("STK1", "STK2", "STK3")]:
        for p in prems:
            m = (c.g == g) & (c.r == 1) & c.mid.between(p * 0.8, p * 1.25) & (c.adelta < 0.7)
            ad, dt = float(c.adelta[m].median()), float(c.dte[m].median())
            cls = "index" if g in IDX else "stock"
            sym = g if g in IDX else rep[g]
            r50 = S.round_trip_pct(cls, sym, ad, dt, p, pct=50)
            r75 = S.round_trip_pct(cls, sym, ad, dt, p, pct=75)
            ex.append({"g": g, "prem": p, "adelta": round(ad, 3), "dte": dt, "rt50": round(r50, 2),
                       "rt75": round(r75, 2), "emp50": round(100 * float(c.rel[m].median()), 2),
                       "emp75": round(100 * float(c.rel[m].quantile(0.75)), 2), "n": int(m.sum())})
    OUT_EVAL.write_text(json.dumps({"winner": win, "eval": ev, "examples": ex,
                                    "table": tab.round(4).to_dict(orient="records"),
                                    "n_tier": {k: sum(1 for v in tiers_all.values() if v == k) for k in (1, 2, 3)}},
                                   indent=1))
    for e in ex:
        print(f"  {e['g']:9s} Rs{e['prem']:>3}  |d| {e['adelta']:.2f} dte {e['dte']:>4.0f}  RT p50 {e['rt50']:5.1f}% "
              f"p75 {e['rt75']:5.1f}%  (emp {e['emp50']:5.1f}/{e['emp75']:5.1f}, n {e['n']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
