"""P1 Test 1 - synthetic option price error on the EOD archive (BALROG).

Question: if we only know day-t's EOD chain and the underlying at t+h, how well does
Black-76 + an IV rule reproduce the real t+h option mid, and the holding-period return?

Inputs : data/archive/chain.parquet, data/archive/snap.parquet  (read only)
Outputs: research/out/p1_synth_error.json  (key "test1"; other keys preserved)
         research/out/p1_synth_eod_heatmap.png
Usage  : python research/scripts/p1_synth_eod.py

Conventions (all re-derived here; vendor iv/delta are NOT used - vendor IV is computed
from a stale-prone LTP and does not reprice the mid):
  * "real" price = mid of two-sided bid/ask (bid>0, ask>=bid, spread/mid <= 0.40).
  * Forward F per (date, symbol, expiry) from put-call parity on the 3 strikes nearest the
    future (fallback: future price). Black-76, r = 6.5 %, T = dte/365 (calendar).
  * Own IV inverted from the mid; own forward delta. Expiry-day rows (dte=0) are dropped.
  * Snapshots flagged dup_prev / not usable in snap.parquet are dropped (stale copies).
  * Proxy forward at t+h (what a backtest would know): F_t * Fut_{t+h}/Fut_t when the
    near-future contract is unchanged, else F_t * S_{t+h}/S_t.
Models: a sticky-strike (own day-t IV); b sticky-moneyness (day-t OTM smile interpolated in
  log(K/F) at the t+h moneyness); c = b * exp(beta * ln(Fhat_{t+h}/F_t)), beta by class fitted
  by OLS through the origin on pairs whose t'+h <= t only (expanding window, beta=0 until 5
  dates); d oracle = contract's own true t+h IV (priced with the proxy forward).
Only 2026-09-15..11-12 has bid/ask (August snapshots are LTP-only) -> 40 usable dates.
Horizon h counts archive days; pairs spanning the Aug-26..Sep-14 archive gap are dropped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[2]
CHAIN = ROOT / "data" / "archive" / "chain.parquet"
SNAP = ROOT / "data" / "archive" / "snap.parquet"
OUTJ = ROOT / "research" / "out" / "p1_synth_error.json"
OUTP = ROOT / "research" / "out" / "p1_synth_eod_heatmap.png"
R = 0.065
HORIZONS = (1, 2, 3, 5)
MODELS = ("a", "b", "c", "d")
DTE_B = [(-1, 2, "0-2"), (2, 7, "3-7"), (7, 20, "8-20"), (20, 9999, "21+")]
DEL_B = [(0.10, 0.25, "10-25"), (0.25, 0.40, "25-40"), (0.40, 0.60, "40-60"), (0.60, 0.80, "60-80")]
MIN_BETA_DATES = 5


# ------------------------------------------------------------------ pricing helpers
def b76(F, K, T, v, is_call):
    F, K, T, v = (np.asarray(x, float) for x in (F, K, T, v))
    sq = v * np.sqrt(np.maximum(T, 1e-8))
    d1 = (np.log(F / K) + 0.5 * sq * sq) / sq
    d2 = d1 - sq
    disc = np.exp(-R * T)
    c = disc * (F * norm.cdf(d1) - K * norm.cdf(d2))
    p = disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
    return np.where(is_call, c, p)


def b76_delta(F, K, T, v, is_call):
    sq = np.asarray(v, float) * np.sqrt(np.maximum(np.asarray(T, float), 1e-8))
    d1 = (np.log(np.asarray(F, float) / np.asarray(K, float)) + 0.5 * sq * sq) / sq
    disc = np.exp(-R * np.asarray(T, float))
    return np.where(is_call, disc * norm.cdf(d1), -disc * norm.cdf(-d1))


def implied_vol(price, F, K, T, is_call, lo=1e-3, hi=5.0, it=60):
    price, F, K, T = (np.asarray(x, float) for x in (price, F, K, T))
    disc = np.exp(-R * T)
    intrinsic = disc * np.where(is_call, np.maximum(F - K, 0), np.maximum(K - F, 0))
    ok = (price > intrinsic + 1e-9) & (T > 0) & np.isfinite(price) & np.isfinite(F)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    for _ in range(it):
        m = 0.5 * (a + b)
        pm = b76(F, K, T, m, is_call)
        up = pm < price
        a = np.where(up, m, a)
        b = np.where(up, b, m)
    v = 0.5 * (a + b)
    v[~ok | (v > hi * 0.999) | (v < lo * 1.001)] = np.nan
    return v


def bucket(x, edges):
    out = np.full(len(x), None, dtype=object)
    for lo, hi, lab in edges:
        out[(x > lo) & (x <= hi)] = lab
    return out


# ------------------------------------------------------------------ data prep
def load_chain() -> pd.DataFrame:
    c = pd.read_parquet(CHAIN, columns=["trade_date", "symbol", "cls", "expiry", "dte", "strike", "type",
                                        "bid", "ask", "spot", "futPrice"])
    for col in ["trade_date", "symbol", "cls", "expiry", "type"]:
        c[col] = c[col].astype(str)
    s = pd.read_parquet(SNAP, columns=["trade_date", "symbol", "usable", "fut_expiry"])
    s["trade_date"] = s.trade_date.astype(str)
    s["symbol"] = s.symbol.astype(str)
    s = s[s.usable.astype(bool)].drop_duplicates(["trade_date", "symbol"])
    s["fut_expiry"] = s.fut_expiry.astype(str)
    c = c.merge(s[["trade_date", "symbol", "fut_expiry"]], on=["trade_date", "symbol"], how="inner")
    c["dte"] = c.dte.astype(float)
    c = c[(c.dte > 0) & (c.bid > 0) & (c.ask >= c.bid)].copy()
    c["mid"] = (c.bid.astype(float) + c.ask.astype(float)) / 2
    c["hs"] = (c.ask.astype(float) - c.bid.astype(float)) / 2
    c = c[(2 * c.hs / c.mid) <= 0.40]
    c["T"] = c.dte / 365.0
    c["is_call"] = c.type.eq("CE").values
    return c.reset_index(drop=True)


def parity_forward(c: pd.DataFrame) -> pd.DataFrame:
    key = ["trade_date", "symbol", "expiry"]
    ce = c[c.is_call][key + ["strike", "mid", "T", "futPrice"]]
    pe = c[~c.is_call][key + ["strike", "mid"]]
    m = ce.merge(pe, on=key + ["strike"], suffixes=("_c", "_p"))
    m["dist"] = (m.strike - m.futPrice).abs()
    m = m.sort_values(key + ["dist"]).groupby(key).head(3)
    m["F"] = m.strike + (m.mid_c - m.mid_p) * np.exp(R * m["T"])
    f = m.groupby(key).F.median().rename("Fpar").reset_index()
    out = c[key + ["futPrice"]].drop_duplicates(key).merge(f, on=key, how="left")
    bad = out.Fpar.isna() | ((out.Fpar / out.futPrice - 1).abs() > 0.05)
    out["F"] = np.where(bad, out.futPrice, out.Fpar)
    out["F_from_parity"] = ~bad
    return out[key + ["F", "F_from_parity"]]


def build_smiles(c: pd.DataFrame) -> dict:
    """(date, symbol, expiry) -> (k sorted, iv) from OTM options."""
    otm = c[((c.is_call) & (c.strike >= c.F)) | ((~c.is_call) & (c.strike < c.F))]
    otm = otm[otm.iv.notna()]
    sm = {}
    for key, g in otm.groupby(["trade_date", "symbol", "expiry"], sort=False):
        if len(g) < 3:
            continue
        k = np.log(g.strike.values / g.F.values)
        o = np.argsort(k)
        sm["|".join(key)] = (k[o], g.iv.values[o])
    return sm


def smile_iv(sm: dict, keys, k):
    out = np.full(len(k), np.nan)
    keys = pd.Series(["|".join(x) for x in keys])
    idx = keys.groupby(keys.values).indices if len(keys) else {}
    for key, rows in idx.items():
        s = sm.get(key)
        if s is None:
            continue
        out[rows] = np.interp(k[rows], s[0], s[1])
    return out


def prepare():
    c = load_chain()
    fw = parity_forward(c)
    c = c.merge(fw, on=["trade_date", "symbol", "expiry"], how="left")
    c["iv"] = implied_vol(c.mid.values, c.F.values, c.strike.values, c["T"].values, c.is_call.values)
    c["delta"] = b76_delta(c.F, c.strike, c["T"], c.iv.fillna(0.3), c.is_call.values)
    c.loc[c.iv.isna(), "delta"] = np.nan
    # nearest three expiries per (date, symbol) among dte>0
    ex = c[["trade_date", "symbol", "expiry"]].drop_duplicates().sort_values(["trade_date", "symbol", "expiry"])
    ex["exp_rank"] = ex.groupby(["trade_date", "symbol"]).cumcount() + 1
    c = c.merge(ex, on=["trade_date", "symbol", "expiry"])
    sm = build_smiles(c)
    return c, fw, sm


# ------------------------------------------------------------------ pairs
def make_pairs(c, sm, dates, h):
    nxt = {dates[i]: dates[i + h] for i in range(len(dates) - h)
           if np.busday_count(dates[i], dates[i + h]) <= h + 1}
    base = c[(c.exp_rank <= 3) & c.iv.notna() & c.delta.abs().between(0.10, 0.80)
             & c.trade_date.isin(list(nxt))].copy()
    base["d1"] = base.trade_date.map(nxt)
    tgt = c[c.iv.notna()][["trade_date", "symbol", "expiry", "strike", "type", "mid", "hs", "iv", "F", "T",
                           "spot", "futPrice", "fut_expiry"]]
    tgt = tgt.rename(columns={"trade_date": "d1", "mid": "mid1", "hs": "hs1", "iv": "iv1", "F": "F1_true",
                              "T": "T1", "spot": "spot1", "futPrice": "fut1", "fut_expiry": "fexp1"})
    p = base.merge(tgt, on=["d1", "symbol", "expiry", "strike", "type"], how="inner")
    same = p.fut_expiry.eq(p.fexp1)
    p["Fhat"] = np.where(same, p.F * p.fut1 / p.futPrice, p.F * p.spot1 / p.spot)
    p["x"] = np.log(p.Fhat / p.F)
    k1 = np.log(p.strike.values / p.Fhat.values)
    p["iv_a"] = p.iv
    p["iv_b"] = smile_iv(sm, zip(p.trade_date, p.symbol, p.expiry), k1)
    p["iv_b"] = p.iv_b.fillna(p.iv)                      # no smile -> sticky strike
    p["iv_d"] = p.iv1
    p["h"] = h
    return p.reset_index(drop=True)


def fit_beta(p):
    """Expanding-window beta by class: y = ln(iv1/iv_b) on x, through origin, using only
    pairs whose target date d1 <= evaluation date t. Returns beta per (class, t)."""
    y = np.log(p.iv1 / p.iv_b).clip(-0.5, 0.5)
    agg = pd.DataFrame({"cls": p.cls, "d1": p.d1, "sxy": p.x * y, "sxx": p.x * p.x})
    agg = agg.groupby(["cls", "d1"])[["sxy", "sxx"]].sum().reset_index()
    out = {}
    for cls, g in agg.groupby("cls"):
        g = g.sort_values("d1")
        for t in sorted(p.trade_date.unique()):
            gg = g[g.d1 <= t]
            if len(gg) >= MIN_BETA_DATES and gg.sxx.sum() > 0:
                out[(cls, t)] = float(gg.sxy.sum() / gg.sxx.sum())
            else:
                out[(cls, t)] = 0.0
    return out


def price_models(p, beta):
    p["beta"] = [beta[(c_, t)] for c_, t in zip(p.cls, p.trade_date)]
    p["iv_c"] = p.iv_b * np.exp(p.beta * p.x)
    for m in MODELS:
        p["P_" + m] = b76(p.Fhat, p.strike, p.T1, p["iv_" + m], p.is_call.values)
    p["ret_act"] = p.mid1 / p.mid - 1
    p["dte_b"] = bucket(p.dte.values, DTE_B)
    p["del_b"] = bucket(p.delta.abs().values, DEL_B)
    return p


def stats(g, m):
    P = g["P_" + m].values
    act = g.ret_act.values
    syn = P / g.mid.values - 1
    err = syn - act
    are = np.abs(P / g.mid1.values - 1)
    out = {"n": int(len(g)), "n_dates": int(g.trade_date.nunique()),
           "med_abs_rel_err": round(float(np.median(are)), 4),
           "p90_abs_rel_err": round(float(np.percentile(are, 90)), 4),
           "med_err_x_halfspread": round(float(np.median(np.abs(P - g.mid1.values) / g.hs1.values.clip(1e-9))), 2),
           "ret_corr": round(float(np.corrcoef(syn, act)[0, 1]), 3) if len(g) > 2 and np.std(act) > 0 else None,
           "sign_agree": round(float(np.mean(np.sign(syn) == np.sign(act))), 3),
           "mean_bias": round(float(np.mean(err)), 4),
           "med_abs_ret_err": round(float(np.median(np.abs(err))), 4),
           "frac_err_gt_absret": round(float(np.mean(np.abs(err) > np.abs(act))), 3)}
    return out


def main() -> int:
    c, fw, sm = prepare()
    dates = sorted(c.trade_date.unique())
    res = {"meta": {"n_dates": len(dates), "first": dates[0], "last": dates[-1],
                    "n_quotes_two_sided": int(len(c)),
                    "parity_forward_share": round(float(fw.F_from_parity.mean()), 3),
                    "r": R, "spread_filter": "spread/mid<=0.40", "min_beta_dates": MIN_BETA_DATES},
           "overall": {}, "cells": [], "beta_latest": {}, "beta_by_date_h1": {}}
    frames = []
    for h in HORIZONS:
        p = make_pairs(c, sm, dates, h)
        beta = fit_beta(p)
        p = price_models(p, beta)
        p = p[np.isfinite(p[["P_" + m for m in MODELS]]).all(axis=1) & (p.mid1 > 0)]
        frames.append(p)
        for cls in ["index", "stock", "all"]:
            g = p if cls == "all" else p[p.cls == cls]
            res["overall"][f"h{h}_{cls}"] = {m: stats(g, m) for m in MODELS}
            # model c evaluated only once beta is live (fair comparison subset)
            live = g[g.beta != 0]
            res["overall"][f"h{h}_{cls}_beta_live"] = {m: stats(live, m) for m in MODELS} if len(live) > 50 else None
        for (cls, db, de), g in p.groupby(["cls", "dte_b", "del_b"]):
            if len(g) < 30:
                continue
            res["cells"].append({"h": h, "cls": cls, "dte": db, "delta": de,
                                 **{m: stats(g, m) for m in MODELS}})
        last = max(p.trade_date)
        res["beta_latest"][f"h{h}"] = {cls: round(beta[(cls, last)], 3) for cls in ["index", "stock"]
                                       if (cls, last) in beta}
        if h == 1:
            # betas usable by later intraday work: fitted on pairs with d1 <= date
            res["beta_by_date_h1"] = {f"{cls}|{t}": round(v, 4) for (cls, t), v in beta.items()}

    # ---------- heatmap (h=1, models b and a, median abs rel error)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    p1 = frames[0]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    dl, tl = [b[2] for b in DEL_B], [b[2] for b in DTE_B]
    for ax, cls in zip(axes, ["index", "stock"]):
        M = np.full((len(tl), len(dl)), np.nan)
        N = np.zeros_like(M, dtype=int)
        g0 = p1[p1.cls == cls]
        for i, t in enumerate(tl):
            for j, d in enumerate(dl):
                g = g0[(g0.dte_b == t) & (g0.del_b == d)]
                if len(g) >= 30:
                    M[i, j] = np.median(np.abs(g.P_b / g.mid1 - 1)) * 100
                    N[i, j] = len(g)
        im = ax.imshow(M, cmap="viridis_r", vmin=0, vmax=np.nanmax([30, np.nanmax(M) if np.isfinite(M).any() else 30]))
        for i in range(len(tl)):
            for j in range(len(dl)):
                if np.isfinite(M[i, j]):
                    ax.text(j, i, f"{M[i, j]:.1f}%\nn={N[i, j]}", ha="center", va="center", fontsize=7,
                            color="white" if M[i, j] > 15 else "black")
        ax.set_xticks(range(len(dl)), dl)
        ax.set_yticks(range(len(tl)), tl)
        ax.set_xlabel("|delta| x100 at t")
        ax.set_ylabel("DTE at t")
        ax.set_title(f"{cls}: median |synthetic/real - 1|, 1-day, model b")
        fig.colorbar(im, ax=ax, shrink=0.8)
    fig.tight_layout()
    fig.savefig(OUTP, dpi=110)
    plt.close(fig)

    old = json.loads(OUTJ.read_text(encoding="utf-8")) if OUTJ.exists() else {}
    old["test1"] = res
    OUTJ.parent.mkdir(parents=True, exist_ok=True)
    OUTJ.write_text(json.dumps(old, indent=1), encoding="utf-8")

    print(f"dates={len(dates)} quotes={len(c)} parity_fwd_share={res['meta']['parity_forward_share']}")
    print("h cls    model  n      med_err p90_err  xHS  corr  sign  bias    |err|>|ret|")
    for h in HORIZONS:
        for cls in ["index", "stock"]:
            for m in MODELS:
                s = res["overall"][f"h{h}_{cls}"][m]
                print(f"{h} {cls:6s} {m}     {s['n']:6d} {s['med_abs_rel_err']:.3f}   {s['p90_abs_rel_err']:.3f}  "
                      f"{s['med_err_x_halfspread']:5.1f} {s['ret_corr']:.3f} {s['sign_agree']:.3f} "
                      f"{s['mean_bias']:+.4f} {s['frac_err_gt_absret']:.3f}")
    print("beta_latest", res["beta_latest"])
    print(f"cells={len(res['cells'])} -> {OUTJ}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
