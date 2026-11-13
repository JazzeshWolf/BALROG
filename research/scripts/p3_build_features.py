"""Build the point-in-time daily feature table data/features/daily.parquet.

  python -I research/scripts/p3_build_features.py [--years 2023 2024] [--no-report]

Inputs: data/underlying/daily.parquet, data/fo/{options,futures}, data/ref/*, data/earnings.
Per-month option-chain features are cached in data/features/_cache (keyed on the source
file's size+mtime and the underlying file's mtime), so re-runs only redo changed months.
Library: research/lib/features.py. Report: research/out/p3_features.md.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.lib import features as F

DATA = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(DATA, "features")
CACHE = os.path.join(OUT_DIR, "_cache")
REPORT = os.path.join(ROOT, "research", "out", "p3_features.md")
INDEX_SYMS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX"}
OPT_COLS = ["date", "exchange", "symbol", "cls", "expiry", "strike", "opt_type", "traded", "iv", "fwd", "r",
            "spot", "oi_contracts", "d_oi_contracts", "lot_size", "dte_cal", "dte_trd", "ca_break"]


def read_retry(path, tries=6, wait=10, **kw):
    for i in range(tries):
        try:
            return pd.read_parquet(path, **kw)
        except Exception as e:  # file being rewritten by the F&O build
            if i == tries - 1:
                raise
            print(f"  retry read {os.path.relpath(path, ROOT)}: {type(e).__name__}")
            time.sleep(wait)


# ----------------------------------------------------------------------------- loaders
def load_underlying():
    u = read_retry(os.path.join(DATA, "underlying", "daily.parquet"))
    u["symbol"] = u["symbol"].astype(str)
    u["date"] = pd.to_datetime(u["date"]).astype("datetime64[ns]")
    return u


def load_futures():
    files = glob.glob(os.path.join(DATA, "fo", "futures", "year=*", "*.parquet"))
    parts = [read_retry(f) for f in files]
    f = pd.concat(parts, ignore_index=True)
    for c in ("symbol", "exchange", "cls"):
        f[c] = f[c].astype(str)
    f["date"] = pd.to_datetime(f["date"]).astype("datetime64[ns]")
    f["expiry"] = pd.to_datetime(f["expiry"]).astype("datetime64[ns]")
    keep = (f["cls"] == "index") | (f["exchange"] == "nse")     # stocks: NSE only
    return f[keep].reset_index(drop=True)


def price_panel(u: pd.DataFrame, fut: pd.DataFrame) -> pd.DataFrame:
    """Underlying OHLC; where high/low/open are missing (close-only sources such as SENSEX
    UndrlygPric), use the near future's OHLC scaled by spot/futures close (ohlc_src='fut')."""
    px = u[u["symbol"] != "INDIAVIX"].copy()
    nf = fut[fut["dte_cal"] >= 1].sort_values(["symbol", "date", "expiry"]).groupby(["symbol", "date"]).first()
    nf = nf[["open", "high", "low", "close"]].add_prefix("f_").reset_index()
    px = px.merge(nf, on=["symbol", "date"], how="left")
    miss = px["high"].isna() | px["low"].isna() | px["open"].isna()
    ratio = px["close"] / px["f_close"]
    fill = miss & ratio.notna() & px["f_high"].notna() & px["f_low"].notna()
    for c in ("open", "high", "low"):
        px.loc[fill, c] = px.loc[fill, "f_" + c] * ratio[fill]
    px["high"] = px[["high", "close"]].max(axis=1).where(px["high"].notna())
    px["low"] = px[["low", "close"]].min(axis=1).where(px["low"].notna())
    px["ohlc_src"] = np.where(~miss, "spot", np.where(fill, "fut", "close_only"))
    k = px["adj_close"] / px["close"]
    for c in ("open", "high", "low", "close"):
        px["a" + c] = px[c] * k
    return px[["date", "symbol", "cls", "open", "high", "low", "close", "aopen", "ahigh", "alow", "aclose", "ohlc_src"]]


def month_files():
    out = []
    for f in sorted(glob.glob(os.path.join(DATA, "fo", "options", "year=*", "*.parquet"))):
        out.append(f)
    return out


def chain_month(path: str, spot: pd.Series, umtime: float) -> pd.DataFrame:
    os.makedirs(CACHE, exist_ok=True)
    st = os.stat(path)
    tag = f"{st.st_size}_{int(st.st_mtime)}_{int(umtime)}_v3"
    cp = os.path.join(CACHE, os.path.basename(path))
    mp = cp + ".json"
    if os.path.exists(cp) and os.path.exists(mp):
        try:
            if json.load(open(mp)).get("tag") == tag:
                return pd.read_parquet(cp)
        except Exception:
            pass
    ch = read_retry(path, columns=OPT_COLS)
    ch = ch[(ch["cls"].astype(str) == "index") | (ch["exchange"].astype(str) == "nse")]
    ch["date"] = pd.to_datetime(ch["date"]).astype("datetime64[ns]")
    ch["expiry"] = pd.to_datetime(ch["expiry"]).astype("datetime64[ns]")
    if ch.empty:
        res = pd.DataFrame()
    else:
        res = F.chain_features(ch, spot)
        cls = ch.groupby([ch["date"], ch["symbol"].astype(str)])["cls"].first().astype(str)
        res = res.merge(cls.rename("cls").reset_index(), on=["date", "symbol"], how="left")
    res.to_parquet(cp, index=False)
    json.dump({"tag": tag}, open(mp, "w"))
    return res


# ----------------------------------------------------------------------------- build
def build(years=None, workers=1):
    t0 = time.time()
    u = load_underlying()
    fut = load_futures()
    cal = read_retry(os.path.join(DATA, "ref", "calendar.parquet"))
    tdays = pd.to_datetime(cal.loc[cal["trading_day"], "date"]).sort_values()

    # ---- price / HAR
    px = price_panel(u, fut)
    # a gap of > 30 calendar days (e.g. SENSEX 2022-06 -> 2023-05 relaunch) starts a new series:
    # trailing windows never bridge it
    px = px.sort_values(["symbol", "date"]).reset_index(drop=True)
    seg = (px.groupby("symbol")["date"].diff().dt.days > 30).groupby(px["symbol"]).cumsum()
    px["sym0"] = px["symbol"]
    px["symbol"] = px["symbol"] + "#" + seg.astype(str)
    pf = F.price_features(px.drop(columns="sym0"))
    pf = pf.merge(px[["date", "symbol", "ohlc_src"]], on=["date", "symbol"])
    hd = F.har_design(pf[["date", "symbol", "cls", "pk_var"]])
    hf = F.har_forecast(hd)
    pf = pf.merge(hf, on=["date", "symbol"], how="left")
    pf = pf.merge(hd[["date", "symbol", "y_1", "y_5", "x_m"]], on=["date", "symbol"], how="left")
    pf["sigma_daily"] = np.sqrt(pf["har_var_h1"])
    for h in (1, 3, 5):
        pf[f"har_vol_h{h}"] = np.sqrt(F.ANN * pf[f"har_var_h{h}"])
    pf = pf.sort_values(["symbol", "date"]).reset_index(drop=True)
    pf["symbol"] = pf["symbol"].str.split("#").str[0]
    px["symbol"] = px["sym0"]
    prev_sig = pf.groupby("symbol")["sigma_daily"].shift(1)
    pf["gap_sigma"] = pf["gap_ret"] / prev_sig
    pf["sigma_daily_prev"] = prev_sig
    print(f"price+HAR: {len(pf):,} rows  ({time.time()-t0:.0f}s)")

    # ---- market-wide
    nif = pf[pf["symbol"] == "NIFTY"][["date", "ret1", "ret20", "above_sma20"]].rename(
        columns={"ret1": "m_ret1", "ret20": "m_ret20", "above_sma20": "nifty_above_20dma"})
    br = F.beta_rs(pf[["date", "symbol", "ret1", "ret20"]], nif[["date", "m_ret1", "m_ret20"]])
    pf = pf.merge(br, on=["date", "symbol"], how="left")
    vix = u[u["symbol"] == "INDIAVIX"][["date", "close"]].rename(columns={"close": "india_vix"}).sort_values("date")
    vix["vix_pctile_252"] = F.rolling_pctile(vix["india_vix"].to_numpy())
    vix["vix_chg5"] = vix["india_vix"] - vix["india_vix"].shift(5)
    mkt = nif[["date", "nifty_above_20dma"]].merge(vix, on="date", how="outer")
    mkt = mkt.merge(F.participant_features(read_retry(os.path.join(DATA, "ref", "participant_oi.parquet")), tdays),
                    on="date", how="outer")

    # ---- futures
    ff = F.futures_features(fut)
    print(f"futures: {len(ff):,} rows  ({time.time()-t0:.0f}s)")

    # ---- option chains (cached per month)
    spot = u.drop_duplicates(["date", "symbol"]).set_index(["date", "symbol"])["close"]
    umt = os.stat(os.path.join(DATA, "underlying", "daily.parquet")).st_mtime
    files = month_files()
    if years:
        files = [f for f in files if any(f"year={y}" in f for y in years)]
    parts = []
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as ex:
            for i, p in enumerate(ex.map(chain_month, files, [spot] * len(files), [umt] * len(files))):
                parts.append(p)
                if (i + 1) % 25 == 0:
                    print(f"  chains {i+1}/{len(files)}  ({time.time()-t0:.0f}s)")
    else:
        for i, f in enumerate(files):
            parts.append(chain_month(f, spot, umt))
            if (i + 1) % 25 == 0:
                print(f"  chains {i+1}/{len(files)}  ({time.time()-t0:.0f}s)")
    chn = pd.concat([p for p in parts if len(p)], ignore_index=True)
    chn = chn.sort_values(["symbol", "date"]).drop_duplicates(["date", "symbol"], keep="last")
    print(f"chains: {len(chn):,} symbol-days  ({time.time()-t0:.0f}s)")

    # ---- assemble (rows = symbol-days with an option chain)
    d = chn.merge(pf.drop(columns=["cls", "close"]), on=["date", "symbol"], how="left")
    d = d.merge(ff, on=["date", "symbol"], how="left")
    d = d.merge(mkt, on="date", how="left")
    d = d.sort_values(["symbol", "date"]).reset_index(drop=True)
    d["rs20_decile"] = F.cs_decile(d["rs20"].where(d["cls"] == "stock"), d["date"])
    d["ca_break"] = d["ca_break"].eq(True) | d["ca_break_fut"].eq(True)
    d = d.drop(columns="ca_break_fut")
    # vol surface ratios / percentiles
    for h in (3, 5):
        d[f"vrp_ratio_h{h}"] = d["atm_iv"] / d[f"har_vol_h{h}"]
    d["net_gex_norm"] = d["net_gex"] / d["fut_turnover_20"]
    g = d.groupby("symbol", sort=False)
    d["iv_pctile_252"] = g["atm_iv"].transform(lambda s: pd.Series(F.rolling_pctile(s.to_numpy()), index=s.index))
    d["gex_pctile_252"] = g["net_gex_norm"].transform(lambda s: pd.Series(F.rolling_pctile(s.to_numpy()), index=s.index))
    sig_spot = d["sigma_daily"] * d["spot"]
    d["gamma_flip_dist_sigma"] = (d["spot"] - d["gamma_flip"]) / sig_spot
    d["max_oi_dist_sigma"] = (d["spot"] - d["max_oi_strike"]) / sig_spot
    # wall breaks (consecutive observed sessions of the symbol, not on a ca_break day)
    d = F.wall_breaks(d)
    # results
    ev = read_retry(os.path.join(DATA, "earnings", "earnings_dates.parquet"))
    evs = F.results_events(ev, tdays)
    rp = F.results_panel(d[["date", "symbol"]], evs, tdays)
    d = d.merge(rp, on=["date", "symbol"], how="left")
    d["results_move_sigma"] = (d["ret1"] / d["sigma_daily_prev"]).where(d["results_today_tradable"])
    d = d.drop(columns=["m_ret1", "m_ret20"], errors="ignore")
    d = d.sort_values(["date", "cls", "symbol"]).reset_index(drop=True)
    print(f"assembled: {len(d):,} rows x {d.shape[1]} cols  ({time.time()-t0:.0f}s)")
    return d, evs


def har_oos(d: pd.DataFrame) -> list[str]:
    """OOS fit of the h=5 forecast of mean daily Parkinson variance over t+1..t+5: R^2 in
    variance levels (1 - MSE/var) and squared correlation in logs, HAR vs naive trailing-22d
    Parkinson mean and vs rv20^2/252 (close-to-close). Every HAR forecast is out of sample."""
    lines = []
    for cls in ("index", "stock"):
        x = d[(d["cls"] == cls)][["y_5", "har_var_h5", "x_m", "rv20"]].dropna()
        x = x[np.isfinite(x).all(1)]
        if len(x) < 100:
            continue
        y = np.exp(x["y_5"])
        cand = {"har": x["har_var_h5"], "pk22": np.exp(x["x_m"]), "rv20": x["rv20"] ** 2 / F.ANN}
        r2 = {k: 1 - ((y - p) ** 2).mean() / y.var() for k, p in cand.items()}
        c2 = {k: np.corrcoef(np.log(y), np.log(p))[0, 1] ** 2 for k, p in cand.items()}
        lines.append(f"| {cls} | {len(x):,} | " + " | ".join(f"{r2[k]:.3f} / {c2[k]:.3f}" for k in cand) + " |")
    return lines


def report(d: pd.DataFrame, evs, path_out: str, rebuild_note: str):
    yr = d["date"].dt.year
    cols = ["ret20", "har_vol_h5", "adx14", "atm_iv", "atm_iv_next", "iv_pctile_252", "vrp_ratio_h5", "net_gex",
            "net_gex_norm", "gamma_flip", "call_wall", "fut_structure", "india_vix", "fii_idx_call_net_ratio",
            "days_to_results"]
    L = ["# P3 - point-in-time daily feature table",
         f"Generated {pd.Timestamp.now():%Y-%m-%d %H:%M} by `research/scripts/p3_build_features.py`; "
         f"columns in `data/README.md`. {rebuild_note}",
         f"\n**Output** `data/features/daily.parquet`: {len(d):,} rows x {d.shape[1]} cols, "
         f"{os.path.getsize(path_out)/1e6:.1f} MB; {d['date'].min():%Y-%m-%d} -> {d['date'].max():%Y-%m-%d}; "
         f"symbols index {d.loc[d.cls=='index','symbol'].nunique()}, stock {d.loc[d.cls=='stock','symbol'].nunique()}. "
         f"SENSEX from {d.loc[d.symbol=='SENSEX','date'].min():%Y-%m-%d} ({(d.symbol=='SENSEX').sum()} d).",
         "\n**Coverage** (non-null share; `fut_structure` = not 'none'-able, shown as non-null):",
         "| cls | year | rows | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 3)]
    for (c, y), g in d.groupby(["cls", yr]):
        L.append(f"| {c} | {y} | {len(g):,} | " + " | ".join(f"{g[k].notna().mean():.2f}" for k in cols) + " |")
    n = d[d["symbol"] == "NIFTY"]
    s = [f"{y}: IV {g['atm_iv'].median()*100:.1f} / VIX {g['india_vix'].median():.1f} / HAR5 {g['har_vol_h5'].median()*100:.1f}"
         for y, g in n.groupby(n["date"].dt.year)]
    L += ["\n**NIFTY median atm_iv vs India VIX vs HAR h5 (vol pts)**: " + "; ".join(s)]
    st = d[d["fut_dprice_z"].notna()].groupby("cls")["fut_structure"].value_counts(normalize=True).unstack().round(3)
    L += ["\n**Futures structure label share** (days with z available): " +
          "; ".join(f"{c}: " + ", ".join(f"{k} {v:.2f}" for k, v in r.items()) for c, r in st.iterrows())]
    ev = d.groupby(yr).agg(cw=("broke_call_wall", "sum"), pw=("broke_put_wall", "sum"),
                           cw2=("cross_call_wall_prev", "sum"), pw2=("cross_put_wall_prev", "sum"),
                           res=("results_today_tradable", "sum"), unk=("results_time_unknown", "sum"))
    L += ["\n**Events by year** (wall breaks all classes; results reaction days, stocks): " +
          "; ".join(f"{y}: call {r.cw} ({r.cw2} vs prev wall), put {r.pw} ({r.pw2}), results {r.res} ({r.unk} t-unk)" for y, r in ev.iterrows())]
    rm = d.loc[d["results_today_tradable"], "results_move_sigma"].abs()
    L += [f"Results-day |move|/sigma_daily: median {rm.median():.2f}, share > 2: {(rm > 2).mean():.2f} (n={rm.notna().sum()})."]
    L += ["\n**HAR out-of-sample R^2** (target: log mean Parkinson var over t+1..t+5; every forecast uses a model "
          "fitted only on targets observed before its month):",
          "| cls | n | log-HAR (R2 lvl / corr2 log) | naive 22d Parkinson | naive rv20^2/252 (close-close) |", "|---|---|---|---|---|"] + har_oos(d)
    pk_cc = d[d["cls"] == "index"]
    ratio = (pk_cc["har_vol_h5"] / pk_cc["rv20"]).median()
    L += [f"\nMedian har_vol_h5 / rv20 (index): {ratio:.2f} - Parkinson ignores overnight gaps, so HAR (and "
          "therefore vrp_ratio) is biased LOW vs close-to-close vol and vrp_ratio biased HIGH; read V1 thresholds with that in mind. Level R^2 is dominated by outliers (rv20 by single-day jumps), so corr^2 in logs is the fairer comparison.",
          "\n**Approximations**",
          "- HAR is log-HAR (pooled per class; exp(xb+s2/2) back-transform), refit monthly on an expanding window; "
          "first months of 2019 have no forecast (needs 400 training rows).",
          "- OHLC: where the underlying is close-only (SENSEX/BANKEX, BSE UDiFF) open/high/low come from the near "
          "future scaled by spot/futures close (`ohlc_src`='fut'); rows with neither have no Parkinson/ADX.",
          "- GEX/flip/walls use the nearest two (walls: nearest) expiries with dte_cal >= 1 (the contract expiring "
          "today is skipped). Untraded strikes (no IV) use that expiry's ATM IV for gamma; strikes beyond +-30% ignored. "
          "Gamma flip searched on a +-15% spot grid (0.25% step), IVs held fixed; NaN if no sign change.",
          "- net_gex_norm = net_gex / 20-day average futures turnover (all expiries, contracts x lot x price).",
          "- Futures dOI = change of combined OI across expiries (rollover-neutral); z = value / std of previous 60 days.",
          "- days_to_results uses the realised announcement date (board meetings are pre-announced, usually >= 2 working "
          "days, often 1-3 weeks); capped at 15. Events deduped to one per 20 calendar days per symbol.",
          "- Participant OI ratios lagged one session (file published after the close)."]
    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")


def rebuild_status() -> str:
    try:
        head = open(os.path.join(ROOT, "research", "out", "p3_fo_build.md"), encoding="utf-8").read(600)
    except OSError:
        return "F&O build report missing."
    partial = "Partial build" in head
    gen = head.split("Generated ")[1][:16] if "Generated " in head else "?"
    return f"Inputs: F&O build report generated {gen} ({'PARTIAL build' if partial else 'complete build'})."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", help="restrict option months (dev); output still written")
    ap.add_argument("--out", default=os.path.join(OUT_DIR, "daily.parquet"))
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args()
    d, evs = build(a.years, a.workers)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    keep = [c for c in d.columns if c not in ("y_1", "y_5", "x_m", "pk_var", "gap_ret", "har_var_h1", "har_var_h3", "har_var_h5")]
    d[keep].to_parquet(a.out, index=False)
    print(f"wrote {a.out}: {len(d):,} rows, {os.path.getsize(a.out)/1e6:.1f} MB")
    if not a.no_report:
        report(d, evs, a.out, rebuild_status())
        print(f"report -> {REPORT}")


if __name__ == "__main__":
    main()
