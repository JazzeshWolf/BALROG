"""Derived F&O fields (pure functions on normalised frames from research.lib.bhav)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.lib import black76
from research.lib.rates import repo_rate


# ----------------------------------------------------------------------------- calendar / expiry
def dte_trading(dates, expiries, calendar) -> np.ndarray:
    """Trading days in (date, expiry] on the observed calendar (expiry day itself counts, the
    current day does not: 0 on expiry day, 1 the day before). Beyond the last observed calendar
    day, weekdays are counted (holidays unknown -> approximate)."""
    cal = np.asarray(pd.to_datetime(pd.Series(calendar)).values.astype("datetime64[D]"))
    cal = np.unique(cal)
    d = pd.to_datetime(pd.Series(dates)).values.astype("datetime64[D]")
    e = pd.to_datetime(pd.Series(expiries)).values.astype("datetime64[D]")
    last = cal[-1]
    e_in = np.minimum(e, last)
    n = np.searchsorted(cal, e_in, side="right") - np.searchsorted(cal, d, side="right")
    beyond = e > last
    if beyond.any():
        start = np.maximum(d[beyond], last) + np.timedelta64(1, "D")
        n = n.astype(np.int64)
        n[beyond] += np.busday_count(start, e[beyond] + np.timedelta64(1, "D"))
    return n


def expiry_rank(df: pd.DataFrame, by=("date", "exchange", "symbol")) -> pd.Series:
    """Dense rank of expiry among expiries >= date (1 = nearest, expiring-today counts)."""
    return df.groupby(list(by))["expiry"].rank(method="dense").astype("int16")


def monthly_flag(df: pd.DataFrame) -> pd.Series:
    """True if the expiry is the last listed expiry of its calendar month for that
    (date, exchange, symbol). Exchanges list monthlies months ahead of weeklies, so the
    month's last expiry is always visible when any of its weeklies is."""
    ym = df["expiry"].dt.year * 100 + df["expiry"].dt.month
    mx = df.groupby([df["date"], df["exchange"], df["symbol"], ym])["expiry"].transform("max")
    return df["expiry"].eq(mx)


# ----------------------------------------------------------------------------- forward
def forwards(opt: pd.DataFrame, fut: pd.DataFrame, spot: pd.Series) -> pd.DataFrame:
    """Forward per (date, exchange, symbol, expiry) present in `opt`.
    Order: same-expiry futures close (settle if close missing) -> put-call parity at the strike
    pair with the largest min(call vol, put vol), both traded -> spot * exp(rT).
    `spot` is indexed by (date, symbol). Returns columns key + fwd, fwd_src, r."""
    key = ["date", "exchange", "symbol", "expiry"]
    k = opt[key].drop_duplicates().reset_index(drop=True)
    T = (k["expiry"] - k["date"]).dt.days.clip(lower=0) / 365.0
    k["r"] = repo_rate(k["date"])
    df = np.exp(-k["r"] * T)
    # 1) futures
    f = fut.copy()
    f["fpx"] = f["close"].where(f["close"] > 0, f["settle"])
    f = f[f["fpx"] > 0].drop_duplicates(key)[key + ["fpx"]]
    k = k.merge(f, on=key, how="left")
    # 2) parity
    o = opt[(opt["volume_contracts"] > 0) & (opt["close"] > 0)]
    c = o[o["opt_type"] == "CE"][key + ["strike", "close", "volume_contracts"]]
    p = o[o["opt_type"] == "PE"][key + ["strike", "close", "volume_contracts"]]
    cp = c.merge(p, on=key + ["strike"], suffixes=("_c", "_p"))
    if len(cp):
        cp["mv"] = np.minimum(cp["volume_contracts_c"], cp["volume_contracts_p"])
        cp = cp.sort_values("mv", ascending=False).drop_duplicates(key)
        k = k.merge(cp[key + ["strike", "close_c", "close_p"]], on=key, how="left")
    else:
        k[["strike", "close_c", "close_p"]] = np.nan
    par = k["strike"] + (k["close_c"] - k["close_p"]) / df.values
    # 3) spot carry
    sp = pd.Series(spot.reindex(pd.MultiIndex.from_frame(k[["date", "symbol"]])).values, index=k.index)
    carry = sp * np.exp(k["r"] * T)
    k["fwd"] = k["fpx"]
    k["fwd_src"] = np.where(k["fpx"].notna(), "fut", None)
    m = k["fwd"].isna() & (par > 0)
    k.loc[m, "fwd"] = par[m]; k.loc[m, "fwd_src"] = "parity"
    m = k["fwd"].isna() & (carry > 0)
    k.loc[m, "fwd"] = carry[m]; k.loc[m, "fwd_src"] = "spot"
    # 4) no spot either (e.g. CM/index file not yet available): carry the nearest-dated forward
    #    of the same symbol-day to this expiry at r
    if k["fwd"].isna().any() and k["fwd"].notna().any():
        k["T"] = T.values
        ref = k[k["fwd"].notna()].copy()
        for (d, x, s), g in k[k["fwd"].isna()].groupby(["date", "exchange", "symbol"]):
            rr = ref[(ref["date"] == d) & (ref["exchange"] == x) & (ref["symbol"] == s)]
            if rr.empty:
                continue
            for i, row in g.iterrows():
                j = (rr["T"] - row["T"]).abs().idxmin()
                k.loc[i, "fwd"] = rr.at[j, "fwd"] * np.exp(row["r"] * (row["T"] - rr.at[j, "T"]))
                k.loc[i, "fwd_src"] = "interp"
    return k[key + ["fwd", "fwd_src", "r"]]


# ----------------------------------------------------------------------------- IV / greeks
def add_iv_greeks(opt: pd.DataFrame) -> pd.DataFrame:
    """Adds iv, delta, gamma, theta, vega from `close` (Black-76 on fwd). Only for traded rows
    with dte_cal > 0 and close strictly above discounted intrinsic; NaN otherwise."""
    for c in ("iv", "delta", "gamma", "theta", "vega"):
        opt[c] = np.nan
    T = opt["dte_cal"].values / 365.0
    df = np.exp(-opt["r"].values * T)
    is_call = (opt["opt_type"] == "CE").values
    elig = (opt["traded"].values & (T > 0) & np.isfinite(opt["fwd"].values) & (opt["close"].values > 0)
            & ~opt["close_masked"].values)
    if not elig.any():
        return opt
    i = np.flatnonzero(elig)
    iv = black76.implied_vol(opt["close"].values[i], opt["fwd"].values[i], opt["strike"].values[i], T[i], df[i],
                             is_call[i])
    good = np.isfinite(iv)
    j = i[good]
    g = black76.greeks(opt["fwd"].values[j], opt["strike"].values[j], T[j], iv[good], df[j], is_call[j],
                       opt["r"].values[j])
    cols = {c: opt.columns.get_loc(c) for c in ("iv", "delta", "gamma", "theta", "vega")}
    opt.iloc[j, cols["iv"]] = iv[good]
    for c in ("delta", "gamma", "theta", "vega"):
        opt.iloc[j, cols[c]] = g[c]
    return opt


def _atm_one(g: pd.DataFrame, max_gap: float = 0.05):
    F = g["fwd"].iloc[0]
    vals = []
    for t in ("CE", "PE"):
        h = g[g["opt_type"] == t].sort_values("strike")
        k, v = h["strike"].values, h["iv"].values
        hi = np.searchsorted(k, F)
        lo = hi - 1
        if lo >= 0 and hi < len(k) and k[hi] > k[lo] and (k[hi] - k[lo]) <= max_gap * F:
            w = (F - k[lo]) / (k[hi] - k[lo])
            vals.append(v[lo] * (1 - w) + v[hi] * w)
    return float(np.mean(vals)) if vals else np.nan


def atm_iv(opt: pd.DataFrame, target_dte: int = 30, min_dte: int = 7) -> pd.DataFrame:
    """Per (date, exchange, symbol): ATM IV of the expiry with dte_cal >= min_dte closest to
    target_dte among expiries where an ATM IV exists (linear interpolation in strike at the
    forward between the nearest strikes with an IV on each side, gap <= 5% of F; mean of CE/PE)."""
    cols = ["date", "exchange", "symbol", "expiry", "dte_cal", "atm_iv"]
    o = opt[(opt["dte_cal"] >= min_dte) & opt["iv"].notna()]
    o = o.assign(opt_type=o["opt_type"].astype(str))
    rows = []
    for (d, x, s), g in o.groupby(["date", "exchange", "symbol"], sort=False, observed=True):
        exps = g.groupby("expiry")["dte_cal"].first()
        for e in (exps - target_dte).abs().sort_values().index:
            v = _atm_one(g[g["expiry"] == e])
            if np.isfinite(v):
                rows.append((d, x, s, e, int(exps[e]), v))
                break
    return pd.DataFrame(rows, columns=cols)


# ----------------------------------------------------------------------------- corporate actions
def detect_ca(prev: pd.DataFrame, cur: pd.DataFrame, min_removed: float = 0.5, min_strikes: int = 4) -> pd.DataFrame:
    """Corporate-action breaks between two consecutive trading days of option rows for one exchange.
    A live contract's strike never disappears except when the exchange re-strikes it on an ex-date,
    so: for expiries listed on both days (and not expiring on prev day), the share of prev-day
    strikes missing today >= min_removed. lot_changed (same expiry) is reported for information.
    Returns (date, symbol, removed_share, lot_changed, fut_ret_raw)."""
    cols = ["date", "symbol", "removed_share", "lot_changed", "fut_ret_raw"]
    if prev is None or prev.empty or cur.empty:
        return pd.DataFrame(columns=cols)
    d1 = cur["date"].iloc[0]
    p = prev[prev["expiry"] >= d1]
    ps = p[["symbol", "expiry", "strike"]].drop_duplicates()
    cs = cur[["symbol", "expiry", "strike"]].drop_duplicates()
    common = ps[["symbol", "expiry"]].drop_duplicates().merge(cs[["symbol", "expiry"]].drop_duplicates())
    ps = ps.merge(common)
    m = ps.merge(cs, how="left", indicator=True)
    m["gone"] = m["_merge"].eq("left_only")
    agg = m.groupby("symbol").agg(n=("gone", "size"), removed_share=("gone", "mean"))
    agg = agg[agg["n"] >= min_strikes]
    pl = p.groupby(["symbol", "expiry"])["lot_size"].median()
    cl = cur.groupby(["symbol", "expiry"])["lot_size"].median()
    lc = pd.concat([pl, cl], axis=1, keys=["a", "b"]).dropna()
    lot_changed = (lc["a"] != lc["b"]).groupby(level=0).any()
    agg = agg.join(lot_changed.rename("lot_changed"), how="outer")
    agg["lot_changed"] = agg["lot_changed"].fillna(False).astype(bool)
    agg["removed_share"] = agg["removed_share"].fillna(0.0)
    # lot changes alone are periodic revisions (no price discontinuity); kept as info only
    hit = agg[agg["removed_share"] >= min_removed].copy()
    hit["date"] = d1
    hit["fut_ret_raw"] = np.nan
    return hit.reset_index().rename(columns={"index": "symbol"})[cols]
