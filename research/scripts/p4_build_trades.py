"""P4: precomputed swing trade-outcome table (02_hypotheses.md §0) -> data/bt/trades/year=YYYY.parquet
and the X1-X4 exit-rule outcomes -> data/bt/exits/year=YYYY.parquet (wide, keyed by trade_id).

One trade row per (entry date, symbol, side, delta_target, dte_bucket) for which a qualifying contract exists
(research/lib/backtest.select_contracts); `is_std` marks the §0 standard contract (std expiry, delta 0.40).

  python -I research/scripts/p4_build_trades.py [--years 2023 2024] [--symbols NIFTY RELIANCE] [--out data/bt]
  python -I research/scripts/p4_build_trades.py --spotcheck 5      # reconcile 5 random trades by hand
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time
import warnings

warnings.simplefilter("ignore", category=Warning)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.lib import backtest as bt

FO = os.path.join(ROOT, "data", "fo", "options")
UND = os.path.join(ROOT, "data", "underlying", "daily.parquet")
CAL = os.path.join(ROOT, "data", "ref", "calendar.parquet")
NP = bt.NPATH
POOL_COLS = ["date", "exchange", "symbol", "cls", "expiry", "strike", "opt_type", "open", "high", "low", "close",
             "volume_contracts", "oi_contracts", "lot_size", "traded", "dte_cal", "dte_trd", "expiry_rank",
             "is_monthly", "spot", "iv", "delta", "gamma", "theta", "vega", "ca_break"]
KEY = ["exchange", "symbol", "expiry", "strike", "opt_type"]


def to_str(s: pd.Series) -> np.ndarray:
    """Fast categorical -> str array."""
    if isinstance(s.dtype, pd.CategoricalDtype):
        cats = np.asarray(s.cat.categories.astype(str), dtype=object)
        codes = s.cat.codes.to_numpy()
        out = cats[np.where(codes < 0, 0, codes)]
        return out
    return s.astype(str).to_numpy()


def read_retry(path, columns=None, tries=8, wait=15):
    for i in range(tries):
        try:
            return pd.read_parquet(path, columns=columns)
        except Exception as e:  # file being rewritten by the rebuild
            if i == tries - 1:
                raise
            time.sleep(wait)


def month_files(year, months=None):
    fs = sorted(glob.glob(os.path.join(FO, f"year={year}", "*.parquet")))
    if months is not None:
        fs = [f for f in fs if int(os.path.basename(f)[4:11][-2:]) in months]
    return fs


def load_pool(year, symbols=None):
    """Traded rows of `year` plus January of year+1 (exit paths), and ca_break symbol-days (all rows)."""
    files = month_files(year) + month_files(year + 1, months={1})
    pools, cas = [], []
    for f in files:
        df = read_retry(f, columns=POOL_COLS)
        if symbols:
            df = df[df["symbol"].astype(str).isin(symbols)]
        for c in ("exchange", "symbol", "cls", "opt_type"):
            df[c] = to_str(df[c])
        cb = df.loc[df["ca_break"].astype(bool), ["date", "symbol"]].drop_duplicates()
        cas.append(cb)
        pools.append(df[df["traded"].astype(bool)].drop(columns=["ca_break"]))
    pool = pd.concat(pools, ignore_index=True)
    pool["date"] = pd.to_datetime(pool["date"]).astype("datetime64[ns]")
    pool["expiry"] = pd.to_datetime(pool["expiry"]).astype("datetime64[ns]")
    ca = pd.concat(cas, ignore_index=True)
    ca["date"] = pd.to_datetime(ca["date"]).astype("datetime64[ns]")
    return pool, ca, files


def load_settle(files, need: pd.DataFrame):
    """settle for untraded (contract, date) cells in `need` (KEY + date)."""
    out = []
    syms = set(need["symbol"].unique())
    for f in files:
        df = read_retry(f, columns=KEY + ["date", "settle", "traded"])
        df = df[~df["traded"].astype(bool)].drop(columns="traded")
        df = df[df["symbol"].isin(syms)]
        for c in ("exchange", "symbol", "opt_type"):
            df[c] = to_str(df[c])
        df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
        df["expiry"] = pd.to_datetime(df["expiry"]).astype("datetime64[ns]")
        out.append(df.merge(need, on=KEY + ["date"], how="inner"))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=KEY + ["date", "settle"])


def load_underlying():
    u = read_retry(UND, columns=["date", "symbol", "high", "low", "close", "ret"])
    u["symbol"] = u["symbol"].astype(str)
    u["date"] = pd.to_datetime(u["date"]).astype("datetime64[ns]")
    u = u.sort_values(["symbol", "date"])
    lr = np.log1p(u["ret"].astype(float))
    u["rv20"] = lr.groupby(u["symbol"]).transform(lambda s: s.rolling(20, min_periods=15).std())
    return u.set_index(["symbol", "date"])


def calendar(pool):
    days = set(pd.to_datetime(pool["date"].unique()))
    try:
        c = read_retry(CAL)
        days |= set(pd.to_datetime(c.loc[c["trading_day"].astype(bool), "date"]).astype("datetime64[ns]"))
    except Exception:
        pass
    return pd.DatetimeIndex(sorted(days))


def build_year(year, und, symbols=None, log=print):
    t0 = time.time()
    pool, ca, files = load_pool(year, symbols)
    cal = calendar(pool)
    cal_y_end = pool["date"].max()
    cal = cal[cal <= cal_y_end]
    pool["cid"] = pool.groupby(KEY, sort=False, observed=True).ngroup().astype(np.int64)
    pool["di"] = cal.get_indexer(pool["date"])
    pidx = pd.Index(pool["cid"].to_numpy() * 100000 + pool["di"].to_numpy())

    sel = []
    ent = pool[pool["date"].dt.year == year]
    for _, g in ent.groupby(ent["date"].dt.month):
        s = bt.select_contracts(g)
        if len(s):
            sel.append(s)
    if not sel:
        return None, None
    tr = pd.concat(sel, ignore_index=True)
    n = len(tr)
    tr.insert(0, "trade_id", np.int64(year) * 10_000_000 + np.arange(n))
    log(f"{year}: {n:,} trades selected from {len(ent):,} traded rows ({time.time()-t0:.0f}s)")

    # ---- path arrays (n, NP+1)
    di0 = tr["di"].to_numpy()
    cid = tr["cid"].to_numpy()
    shp = (n, NP + 1)
    A = {k: np.full(shp, np.nan) for k in ("open", "high", "low", "close", "vol", "delta", "rank", "spot")}
    dates = np.full(shp, np.datetime64("NaT"), dtype="datetime64[ns]")
    for k in range(NP + 1):
        dk = di0 + k
        inside = dk < len(cal)
        dates[inside, k] = cal.values[dk[inside]]
        pos = pidx.get_indexer(cid * 100000 + dk)
        hit = pos >= 0
        for a, c in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"),
                     ("vol", "volume_contracts"), ("delta", "delta"), ("rank", "expiry_rank"), ("spot", "spot")):
            A[a][hit, k] = pool[c].to_numpy(float)[pos[hit]]
    exp_ = tr["expiry"].to_numpy("datetime64[ns]")
    dte = ((exp_[:, None] - dates) / np.timedelta64(1, "D")).astype(float)
    # settle for untraded path cells
    miss_r, miss_k = np.where(np.isnan(A["close"][:, 1:]) & ~np.isnat(dates[:, 1:]))
    settle = np.full(shp, np.nan)
    if len(miss_r):
        need = tr.iloc[miss_r][KEY].copy()
        need["date"] = dates[miss_r, miss_k + 1]
        need["_r"], need["_k"] = miss_r, miss_k + 1
        got = load_settle(files, need)
        settle[got["_r"].to_numpy(), got["_k"].to_numpy()] = got["settle"].to_numpy(float)

    # ---- underlying path
    usym = tr["symbol"].to_numpy(str)
    U = {k: np.full(shp, np.nan) for k in ("close", "high", "low", "rv20")}
    for k in range(NP + 1):
        ix = pd.MultiIndex.from_arrays([usym, dates[:, k]])
        pos = und.index.get_indexer(ix)
        hit = pos >= 0
        for a in U:
            U[a][hit, k] = und[a].to_numpy(float)[pos[hit]]
    U["close"] = np.where(np.isnan(U["close"]), A["spot"], U["close"])  # BSE / gaps: option-file spot
    rv20 = U["rv20"][:, 0]

    # ---- per-trade scalars
    cls = tr["cls"].to_numpy(str)
    sym = usym
    pct = bt.spread_pct(sym)
    lot = tr["lot_size"].to_numpy(float)
    close0 = tr["close"].to_numpy(float)
    delta0 = tr["delta"].to_numpy(float)
    rank0 = tr["expiry_rank"].to_numpy(float)
    hs0 = bt.half_spread_vec(cls, sym, np.abs(delta0), tr["dte_cal"].to_numpy(float), close0,
                             tr["volume_contracts"].to_numpy(float), rank0, pct)
    h_cap = tr["dte_trd"].to_numpy(int) - 1
    sigma_d = np.where(np.isnan(rv20), tr["iv"].to_numpy(float) / np.sqrt(252.0), rv20)
    A["delta"][:, 0] = delta0
    P = {"open": A["open"], "high": A["high"], "low": A["low"], "close": A["close"], "settle": settle,
         "delta": A["delta"], "dte": dte, "vol": A["vol"], "rank": A["rank"], "dates": dates,
         "cls": cls, "symbol": sym, "delta0": delta0, "rank0": rank0, "pct": pct, "lot": lot,
         "exchange": tr["exchange"].to_numpy(str), "close0": close0, "hs0": hs0,
         "is_call": tr["opt_type"].to_numpy(str) == "CE", "u_close": U["close"], "u_close0": U["close"][:, 0],
         "u_high0": U["high"][:, 0], "u_low0": U["low"][:, 0], "sigma": sigma_d}
    ca_set = pd.Index(ca["symbol"].astype(str) + "|" + ca["date"].dt.strftime("%Y%m%d"))
    ca_mat = np.zeros(shp, bool)
    for k in range(1, NP + 1):
        ok = ~np.isnat(dates[:, k])
        keys = pd.Index(np.char.add(np.char.add(sym, "|"), pd.DatetimeIndex(dates[:, k]).strftime("%Y%m%d").to_numpy(str)))
        ca_mat[:, k] = ok & (ca_set.get_indexer(keys) >= 0)

    out = pd.DataFrame({
        "trade_id": tr["trade_id"], "date": tr["date"], "exchange": tr["exchange"], "symbol": tr["symbol"],
        "cls": tr["cls"], "grp": bt.grp_label(cls, sym), "sample": bt.sample_label(tr["date"], sym),
        "opt_type": tr["opt_type"], "expiry": tr["expiry"], "strike": tr["strike"],
        "dte_bucket": tr["dte_bucket"], "delta_target": tr["delta_target"].astype("float32"),
        "delta_gap": tr["delta_gap"].astype("float32"), "is_std": tr["is_std"], "is_std_exp": tr["is_std_exp"],
        "lot": lot.astype("float32"), "premium": close0, "iv": tr["iv"], "delta": tr["delta"], "gamma": tr["gamma"],
        "theta": tr["theta"], "vega": tr["vega"], "volume": tr["volume_contracts"], "oi": tr["oi_contracts"],
        "dte_cal": tr["dte_cal"].astype("int16"), "dte_trd": tr["dte_trd"].astype("int16"),
        "expiry_rank": tr["expiry_rank"].astype("int8"), "spot": tr["spot"], "rv20": rv20, "sigma_d": sigma_d,
        "half_spread": hs0, "half_spread_2x": 2 * hs0, "entry_fill": close0 + hs0, "h_cap": h_cap.astype("int16"),
    })
    th = tr["theta"].to_numpy(float)
    gm = tr["gamma"].to_numpy(float)
    spot0 = tr["spot"].to_numpy(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out["gamma_theta"] = gm / np.abs(th)
        out["gamma_theta_rv"] = 0.5 * gm * (spot0 * rv20) ** 2 / np.abs(th)
    for k in range(1, NP + 1):
        for a, nm in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c")):
            out[f"p{k}_{nm}"] = A[a][:, k].astype("float32")
    for k in range(0, NP + 1):
        for a in ("close", "high", "low"):
            out[f"u{k}_{a[0]}"] = U[a][:, k]

    rows = np.arange(n)
    for h in bt.H_LIST:
        H, _ = bt.cap_horizon(np.full(n, h), tr["dte_trd"].to_numpy(int))
        valid = (H >= 1) & ~np.isnat(dates[rows, np.clip(H, 0, NP)])
        Hk = np.where(valid, H, 1)
        mark, pday, stale, settled = bt.mark_at(Hk, A["close"], settle)
        mark = np.where(valid, mark, np.nan)
        res = bt.settle_trade(P, Hk, mark, pday)
        res2 = bt.settle_trade(P, Hk, mark, pday, spread_mult=2.0)
        xdate = dates[rows, Hk]
        caldays = ((xdate - dates[:, 0]) / np.timedelta64(1, "D")).astype(float)
        out[f"exit_date_h{h}"] = np.where(valid, xdate, np.datetime64("NaT"))
        out[f"exit_mark_h{h}"] = mark
        out[f"exit_fill_h{h}"] = res["sell"]
        out[f"hs_exit_h{h}"] = res["hs_out"]
        out[f"cost_h{h}"] = res["costs"]["total"]
        out[f"gross_rs_h{h}"] = (mark - close0) * lot
        out[f"net_rs_h{h}"] = res["pnl"]
        out[f"gross_R_h{h}"] = mark / close0 - 1
        out[f"net_R_h{h}"] = res["R"]
        out[f"net_R2x_h{h}"] = res2["R"]
        out[f"cal_days_h{h}"] = np.where(valid, caldays, np.nan).astype("float32")
        out[f"capped_h{h}"] = h_cap < h
        out[f"stale_h{h}"] = valid & stale
        out[f"settled_h{h}"] = valid & settled
        out[f"nomark_h{h}"] = valid & np.isnan(mark)
        out[f"incomplete_h{h}"] = ~valid & (H >= 1)
        out[f"ca_break_h{h}"] = (ca_mat[:, 1:] & (np.arange(1, NP + 1)[None, :] <= Hk[:, None])).any(1) & valid
        if h == 3:
            for c in ("brokerage", "exchange_txn", "ipft", "sebi_fee", "stamp_duty", "stt_sell", "gst", "buy_leg"):
                out[f"c3_{c}"] = res["costs"][c]
            # required-move ratio (h=3): premium rise needed = spreads + costs/unit + theta over the hold,
            # solved for the underlying move with delta-gamma; / (rv20 * sqrt(3) * spot)
            need = 2 * hs0 + res["costs"]["total"] / lot + np.abs(th) * np.nan_to_num(caldays, nan=3 * 7 / 5)
            ad = np.abs(delta0)
            with np.errstate(invalid="ignore", divide="ignore"):
                x = np.where(gm > 0, (-ad + np.sqrt(ad * ad + 2 * gm * need)) / gm, need / ad)
                out["req_move_pts"] = x
                out["rmr_rv20"] = x / (rv20 * np.sqrt(3.0) * spot0)
        # sensitivity: enter at t+1 open (day-1 characteristics), same exit mark
        o1 = A["open"][:, 1]
        hs1 = bt.path_hs(np.ones(n, int), o1, P)
        buy1 = o1 + hs1
        ok1 = valid & ~np.isnan(o1) & ~np.isnan(res["sell"]) & (Hk >= 1)
        c1 = bt.costs_vec(np.where(ok1, dates[:, 1], dates[:, 0]), np.where(ok1, xdate, dates[:, 0]),
                          P["exchange"], np.nan_to_num(buy1, nan=1.0), np.where(ok1, res["sell"], 0.0), lot)
        _, r1 = bt.net_r(buy1, res["sell"], lot, c1)
        out[f"net_R_open1_h{h}"] = np.where(ok1, r1, np.nan)

    # ---- exit rules X1-X4
    ex = {"trade_id": tr["trade_id"].to_numpy()}
    for hm in bt.H_MAX:
        H = np.minimum(hm, h_cap).astype(float)
        H[np.isnat(dates[rows, np.clip(H.astype(int), 0, NP)])] = np.nan
        for pre, rule, kw in bt.exit_rule_grid():
            for mult, suf in ((1.0, "R"), (2.0, "R2x")):
                xd, raw, pd_, why = bt.run_exit_rule(P, rule, H, spread_mult=mult, **kw)
                r = bt.settle_trade(P, np.maximum(xd, 1), raw, pd_, spread_mult=mult)
                ex[f"{pre}_h{hm}_{suf}"] = np.where(np.isnan(H), np.nan, r["R"]).astype("float32")
                if mult == 1.0:
                    ex[f"{pre}_h{hm}_rs"] = np.where(np.isnan(H), np.nan, r["pnl"]).astype("float32")
                    ex[f"{pre}_h{hm}_day"] = xd.astype("int8")
                    ex[f"{pre}_h{hm}_why"] = why
    ex = pd.DataFrame(ex)
    log(f"{year}: priced in {time.time()-t0:.0f}s")
    return out, ex


def spotcheck(k=5, out_dir=None, seed=None):
    """Recompute k random std-contract h=3 trades with the scalar engine functions and print the arithmetic."""
    out_dir = out_dir or os.path.join(ROOT, "data", "bt")
    fs = sorted(glob.glob(os.path.join(out_dir, "trades", "year=*.parquet")))
    rng = np.random.default_rng(seed)
    picks = []
    for f in rng.choice(fs, size=k, replace=True):
        t = pd.read_parquet(f)
        t = t[t["net_R_h3"].notna()]
        picks.append(t.iloc[rng.integers(len(t))])
    for r in picks:
        sym, ex = r["symbol"], r["exchange"].upper()
        hs_in = bt.SPREAD.half_spread(r["cls"], sym, abs(r["delta"]), r["dte_cal"], r["premium"], r["volume"],
                                      int(r["expiry_rank"]), int(bt.spread_pct([sym])[0]))
        buy = r["premium"] + hs_in
        sell = r["exit_fill_h3"]
        c = bt.COSTS.round_trip_cost(r["date"], ex, buy, sell, int(r["lot"]), exit_date=r["exit_date_h3"])
        pnl = (sell - buy) * r["lot"] - c["total"]
        buyleg = bt.costs_vec([r["date"]], [r["date"]], ex, [buy], [0.0], [r["lot"]])["total"][0]
        R = pnl / (buy * r["lot"] + buyleg)
        print(f"{r['date']:%Y-%m-%d} {sym} {r['expiry']:%Y-%m-%d} {r['strike']:g}{r['opt_type']} lot {r['lot']:g} "
              f"d={r['delta']:.2f} dte={r['dte_cal']} exit {r['exit_date_h3']:%Y-%m-%d}"
              f"{' STALE' if r['stale_h3'] else ''}{' CAPPED' if r['capped_h3'] else ''}")
        print(f"  entry close {r['premium']:.2f} + hs {hs_in:.3f} = {buy:.3f} | exit mark {r['exit_mark_h3']:.2f} - hs "
              f"{r['hs_exit_h3']:.3f} = {sell:.3f}")
        print(f"  costs: brk {c['brokerage']:.2f} txn {c['exchange_txn']:.3f} ipft {c['ipft']:.4f} sebi {c['sebi_fee']:.4f} "
              f"stamp {c['stamp_duty']:.3f} stt {c['stt_sell']:.3f} gst {c['gst']:.3f} = {c['total']:.3f} (table {r['cost_h3']:.3f})")
        print(f"  net = ({sell:.3f}-{buy:.3f})x{r['lot']:g} - {c['total']:.3f} = {pnl:.2f} (table {r['net_rs_h3']:.2f}); "
              f"R = {pnl:.2f}/({buy:.3f}x{r['lot']:g}+{buyleg:.3f}) = {R:.4f} (table {r['net_R_h3']:.4f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--spotcheck", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    a = ap.parse_args()
    if a.spotcheck:
        spotcheck(a.spotcheck, a.out)
        return
    years = a.years or sorted(int(os.path.basename(d)[5:]) for d in glob.glob(os.path.join(FO, "year=*")))
    os.makedirs(os.path.join(a.out, "trades"), exist_ok=True)
    os.makedirs(os.path.join(a.out, "exits"), exist_ok=True)
    if a.workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(a.workers) as pe:
            msgs = list(pe.map(_run_year, [(y, a.symbols, a.out) for y in years]))
    else:
        und = load_underlying()
        msgs = [_run_year((y, a.symbols, a.out), und) for y in years]
    tot = sum(m[0] for m in msgs)
    size = sum(m[1] for m in msgs)
    for m in msgs:
        print(m[2])
    print(f"total rows {tot:,}; files {size/1e6:.0f} MB under {a.out}")


def _run_year(args, und=None):
    y, symbols, out_dir = args
    if und is None:
        und = load_underlying()
    tr, ex = build_year(y, und, symbols, log=lambda *_: None)
    if tr is None:
        return 0, 0, f"{y}: no trades"
    size = 0
    for df, sub in ((tr, "trades"), (ex, "exits")):
        p = os.path.join(out_dir, sub, f"year={y}.parquet")
        tmp = p + ".tmp"
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
        size += os.path.getsize(p)
    s = tr[tr["is_std"]]
    return len(tr), size, (f"{y}: rows {len(tr):,} (std {len(s):,}); std h3 net R mean {s['net_R_h3'].mean():+.3f}, "
                           f"stale {s['stale_h3'].mean():.1%}, capped h5 {s['capped_h5'].mean():.1%}")

if __name__ == "__main__":
    main()
