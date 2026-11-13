"""P4 intraday helpers (BALROG): day matrices, M6/M7/B2 signals, standard contract, SYNTHETIC pricing.

Time convention.  A day is a row of 375 one-minute bars, bar i starts at 09:15 + i min.
"Price at minute index m" = close of bar m-1 (known at 09:15 + m min).  So 09:20 -> m=5,
15:15 -> m=360, 15:00 -> m=345.  A breakout on bar j is acted on at its close: entry m = j+1.

Synthetic option model = P1 model b (sticky-moneyness): the previous trading day p's OTM
smile iv(log K/F) for the chosen expiry; forward at minute m Fhat = F_p * U_m / U_p (U = 1-min
underlying, U_p = its last close on day p).  Black-76, r from the chain.
Theta convention = TRADING TIME: T(d, m) = T_d + (T_p - T_d) * (375 - m) / 375 with
T_x = calendar days from day x's close to expiry / 365.  The whole close(p)->close(d) decay
(incl. weekends/holidays) is spent over the 375 session minutes; nothing decays overnight.
(P1 found calendar-time theta biases intraday synthetic returns up by 1-3%.)
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from engine import costs as _costs  # noqa: E402
from engine import spread as _spread  # noqa: E402

NB = 375
INDICES = ("NIFTY", "BANKNIFTY", "SENSEX")
FO_ALIAS = {"ETERNAL": ("ZOMATO", "ETERNAL")}     # 1-min symbol -> F&O symbols over time
MIN_DTE = 8
TARGET_DELTA = 0.40
VOL_MIN = {"index": 500, "stock": 50}
PREM_MIN = 2.0


def m_of(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return (h * 60 + m) - (9 * 60 + 15)


def cls_of(sym: str) -> str:
    return "index" if sym in INDICES else "stock"


def exchange_of(sym: str) -> str:
    return "BSE" if sym == "SENSEX" else "NSE"


def spread_pct(sym: str) -> int:
    return 90 if sym == "BANKNIFTY" else 75


# ----------------------------------------------------------------------------- day matrices
def day_matrices(df: pd.DataFrame, drop_dates=(), min_bars=300):
    """1-min frame -> (dates array[datetime64[D]], dict of [n_days, 375] float arrays o/h/l/c).
    Missing minutes: close forward-filled, o/h/l set to that close.  Days with < min_bars dropped."""
    ts = pd.to_datetime(df["ts"])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("Asia/Kolkata")
    ts = ts.dt.tz_convert("Asia/Kolkata")
    i = (ts.dt.hour * 60 + ts.dt.minute - (9 * 60 + 15)).values
    day = ts.dt.tz_localize(None).dt.normalize().values.astype("datetime64[D]")
    ok = (i >= 0) & (i < NB)
    drop = np.array(sorted(pd.to_datetime(list(drop_dates)).values.astype("datetime64[D]"))) if len(drop_dates) else None
    if drop is not None:
        ok &= ~np.isin(day, drop)
    d = pd.DataFrame({"day": day[ok], "i": i[ok], "o": df["open"].values[ok], "h": df["high"].values[ok],
                      "l": df["low"].values[ok], "c": df["close"].values[ok]}).drop_duplicates(["day", "i"], keep="last")
    days, di = np.unique(d["day"].values.astype("datetime64[D]"), return_inverse=True)
    out = {}
    for k in "ohlc":
        a = np.full((len(days), NB), np.nan)
        a[di, d["i"].values] = d[k].values
        out[k] = a
    cnt = np.isfinite(out["c"]).sum(1)
    c = pd.DataFrame(out["c"].T).ffill().bfill().values.T      # within-day fill (bfill only for a missing 09:15)
    for k in "ohl":
        out[k] = np.where(np.isfinite(out[k]), out[k], c)
    out["c"] = c
    keep = cnt >= min_bars
    return days[keep], {k: v[keep] for k, v in out.items()}


def sigma_daily(days, close_mat, win=20, min_obs=15):
    """Trailing close-to-close log-return std over the `win` returns ending at day t-1 (past only)."""
    dc = close_mat[:, -1]
    r = np.r_[np.nan, np.diff(np.log(dc))]
    s = pd.Series(r).rolling(win, min_periods=min_obs).std().shift(1).values
    return s


# ----------------------------------------------------------------------------- signals
def orb_signals(c, h, l, L=15, exit_m=360, last_entry_m=345):
    """Opening-range breakout on day matrices.  Range = bars 0..L-1.  Entry on the first bar j >= L
    whose close is beyond the range (entry m = j+1, price c[j]); only j+1 <= last_entry_m and
    j+1 < exit_m.  Exit: first bar k > j whose close re-crosses the range midpoint (long: c < mid,
    short: c > mid) -> exit m = k+1, price c[k]; else time exit at exit_m (price c[exit_m-1]).
    Returns dict of arrays (side 0 = no trade)."""
    n = c.shape[0]
    hi = h[:, :L].max(1)
    lo = l[:, :L].min(1)
    mid = 0.5 * (hi + lo)
    jmax = min(last_entry_m, exit_m - 1) - 1          # last bar index allowed to trigger
    seg = c[:, L:jmax + 1]
    up = seg > hi[:, None]
    dn = seg < lo[:, None]
    brk = up | dn
    has = brk.any(1)
    j = np.where(has, brk.argmax(1) + L, -1)
    side = np.zeros(n, int)
    rows = np.where(has)[0]
    side[rows] = np.where(up[rows, j[rows] - L], 1, -1)
    entry_m = np.where(has, j + 1, -1)
    exit_m_arr = np.full(n, -1)
    reason = np.array([""] * n, dtype=object)
    cols = np.arange(NB)
    for r in rows:
        jj = j[r]
        k_rng = cols[(cols > jj) & (cols <= exit_m - 1)]
        x = c[r, k_rng]
        cross = (x < mid[r]) if side[r] > 0 else (x > mid[r])
        # the time-exit bar itself (exit_m-1) is a time exit, not a stop, if both coincide
        if cross[:-1].any():
            k = k_rng[:-1][cross[:-1].argmax()]
            exit_m_arr[r] = k + 1
            reason[r] = "mid"
        else:
            exit_m_arr[r] = exit_m
            reason[r] = "time"
    return {"side": side, "entry_m": entry_m, "exit_m": exit_m_arr, "reason": reason,
            "or_hi": hi, "or_lo": lo, "or_mid": mid}


def m7_signals(o, c, sig_end_m=30, entry_m=345, exit_m=370):
    """M7: sign of (price at 09:45 / 09:15 open - 1) -> side; entry 15:00, exit 15:25."""
    r1 = c[:, sig_end_m - 1] / o[:, 0] - 1
    side = np.sign(r1).astype(int)
    r2 = c[:, exit_m - 1] / c[:, entry_m - 1] - 1
    return {"side": side, "r1": r1, "r2": r2, "entry_m": np.full(len(side), entry_m),
            "exit_m": np.full(len(side), exit_m)}


# ----------------------------------------------------------------------------- Black-76
def b76(F, K, T, v, df, is_call):
    sT = v * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * v * v * T) / sT
    d2 = d1 - sT
    c = df * (F * ndtr(d1) - K * ndtr(d2))
    p = df * (K * ndtr(-d2) - F * ndtr(-d1))
    return np.where(is_call, c, p)


def b76_delta(F, K, T, v, df, is_call):
    d1 = (np.log(F / K) + 0.5 * v * v * T) / (v * np.sqrt(T))
    return np.where(is_call, df * ndtr(d1), -df * ndtr(-d1))


def t_trading(T_p, T_d, m):
    """Trading-time year fraction at minute index m of day d (see module doc)."""
    return T_d + (T_p - T_d) * (NB - np.asarray(m, float)) / NB


def t_calendar(T_d, m):
    """Calendar-time year fraction: expiry 15:30 minus wall-clock time at minute m."""
    mins_to_close = (NB - np.asarray(m, float))       # 09:15+m .. 15:30
    return T_d + mins_to_close / (365.0 * 1440.0)


# ----------------------------------------------------------------------------- chains / smiles
FO_COLS = ["date", "exchange", "symbol", "expiry", "strike", "opt_type", "close", "volume_contracts",
           "lot_size", "traded", "dte_cal", "expiry_rank", "is_monthly", "fwd", "r", "iv", "ca_break"]


def load_chains(fo_root: Path, symbols, years) -> pd.DataFrame:
    """Previous-day chain rows that can matter: traded, IV solved, 9 <= dte_cal <= 75."""
    want = set()
    for s in symbols:
        want.update(FO_ALIAS.get(s, (s,)))
    back = {a: s for s, al in FO_ALIAS.items() for a in al}
    parts = []
    for y in years:
        for f in sorted((fo_root / f"year={y}").glob("*.parquet")):
            t = pd.read_parquet(f, columns=FO_COLS)
            t = t[t["symbol"].astype(str).isin(want)]
            t = t[t["traded"] & t["iv"].notna() & t["dte_cal"].between(9, 75)]
            if len(t):
                t = t.copy()
                t["symbol"] = t["symbol"].astype(str).map(lambda x: back.get(x, x))
                for k in ("exchange", "opt_type"):
                    t[k] = t[k].astype(str)
                parts.append(t)
    ch = pd.concat(parts, ignore_index=True)
    ch["date"] = pd.to_datetime(ch["date"]).values.astype("datetime64[D]")
    ch["expiry"] = pd.to_datetime(ch["expiry"]).values.astype("datetime64[D]")
    # one exchange per symbol (SENSEX on BSE, everything else NSE)
    ch = ch[ch["exchange"].str.upper() == ch["symbol"].map(exchange_of)]
    return ch


def load_ca_break(fo_root: Path, symbols, years) -> set:
    want = set()
    for s in symbols:
        want.update(FO_ALIAS.get(s, (s,)))
    back = {a: s for s, al in FO_ALIAS.items() for a in al}
    out = set()
    for y in years:
        for f in sorted((fo_root / f"year={y}").glob("*.parquet")):
            t = pd.read_parquet(f, columns=["date", "symbol", "ca_break"])
            t = t[t["ca_break"] & t["symbol"].astype(str).isin(want)].drop_duplicates(["date", "symbol"])
            for d, s in zip(pd.to_datetime(t["date"]).values.astype("datetime64[D]"), t["symbol"].astype(str)):
                out.add((back.get(s, s), d))
    return out


class Contract:
    """Previous-day snapshot of the standard expiry for (symbol, day d)."""
    __slots__ = ("p", "expiry", "F_p", "r", "ks", "ivs", "cand", "rank", "T_p", "T_d")

    def iv(self, k):
        return np.interp(k, self.ks, self.ivs)


def build_contracts(ch: pd.DataFrame, sym: str, days: np.ndarray, ca: set) -> dict:
    """day d -> Contract built ONLY from chain rows dated p = last chain date < d (<= 4 business
    days back).  Expiry = nearest with (expiry - d) >= MIN_DTE (stocks: monthly only)."""
    cl = cls_of(sym)
    g = ch[ch["symbol"] == sym]
    if g.empty:
        return {}
    if cl == "stock":
        g = g[g["is_monthly"]]
    by_p = {np.datetime64(pd.Timestamp(p), "D"): x for p, x in g.groupby("date")}
    pdates = np.array(sorted(by_p), dtype="datetime64[D]")
    out = {}
    for d in days:
        i = np.searchsorted(pdates, d) - 1          # strictly before d
        if i < 0:
            continue
        p = pdates[i]
        if np.busday_count(p, d) > 4 or (sym, d) in ca or (sym, p) in ca:
            continue
        x = by_p[p]
        exps = np.unique(x["expiry"].values.astype("datetime64[D]"))
        exps = exps[(exps - d).astype(int) >= MIN_DTE]
        if not len(exps):
            continue
        e = exps[0]
        y = x[x["expiry"].values.astype("datetime64[D]") == e]
        F = float(np.nanmedian(y["fwd"].values))
        if not np.isfinite(F) or F <= 0:
            continue
        callm = y["opt_type"].values == "CE"
        otm = (callm & (y["strike"].values >= F)) | (~callm & (y["strike"].values < F))
        z = y[otm]
        if len(z) < 3:
            continue
        k = np.log(z["strike"].values / F)
        o = np.argsort(k)
        ks, ivs = k[o], z["iv"].values[o].astype(float)
        ks, ui = np.unique(ks, return_index=True)
        ivs = ivs[ui]
        if len(ks) < 3:
            continue
        vmin = VOL_MIN[cl]
        cand = y[(y["volume_contracts"] >= vmin) & (y["close"] >= PREM_MIN)]
        if cand.empty:
            continue
        c = Contract()
        c.p, c.expiry, c.F_p, c.r = p, e, F, float(y["r"].iloc[0])
        c.ks, c.ivs = ks, ivs
        c.cand = {t: (cand.loc[cand["opt_type"] == t, "strike"].values.astype(float),
                      cand.loc[cand["opt_type"] == t, "volume_contracts"].values.astype(float))
                  for t in ("CE", "PE")}
        c.rank = int(y["expiry_rank"].iloc[0])
        c.T_p = (e - p).astype(int) / 365.0
        c.T_d = (e - d).astype(int) / 365.0
        out[d] = c
    return out


_LOT_CACHE: dict = {}


def lot_size(lots: pd.DataFrame, sym: str, d) -> float:
    """Nearest-expiry lot in force on day d (data/ref/lots.parquet; first known lot before its first row)."""
    key = (id(lots), sym)
    if key not in _LOT_CACHE:
        ex = exchange_of(sym).lower()
        t = lots[(lots["exchange"] == ex) & (lots["symbol"].isin(FO_ALIAS.get(sym, (sym,))))].sort_values("date_from")
        _LOT_CACHE[key] = (t["date_from"].values.astype("datetime64[D]"), t["lot_size"].values.astype(float))
    dd, ls = _LOT_CACHE[key]
    if not len(dd):
        return float("nan")
    i = np.searchsorted(dd, np.datetime64(d, "D"), side="right") - 1
    return float(ls[max(i, 0)])


def net_from(ev: pd.DataFrame, spread_mult: float = 1.0) -> np.ndarray:
    """Net R recomputed from stored mid prices with the half-spreads scaled by spread_mult."""
    out = np.full(len(ev), np.nan)
    for i, (d, s, Pe, Px, he, hx, q) in enumerate(zip(ev["date"].values, ev["symbol"].values, ev["P_e"].values,
                                                     ev["P_x"].values, ev["hs_e"].values, ev["hs_x"].values,
                                                     ev["lot"].values)):
        if not np.isfinite(Pe):
            continue
        fx, kb, ks = cost_coeffs(np.datetime64(d, "D"), exchange_of(s))
        buy = (Pe + spread_mult * he) * q
        sell = max(Px - spread_mult * hx, 0.0) * q
        cost = fx + kb * buy + ks * sell if sell > 0 else fx - 20.0 * 1.18 + kb * buy
        out[i] = (sell - buy - cost) / (buy + cost)
    return out


_COST_CACHE: dict = {}


def cost_coeffs(d, exchange: str):
    """Round-trip cost = fixed + kb*buy_turnover + ks*sell_turnover (exact: engine/costs.py is linear)."""
    key = (str(d)[:10], exchange)
    if key not in _COST_CACHE:
        f = lambda b, s: _costs.round_trip_cost(key[0], exchange, b, s, 1)["total"]
        base = f(1.0, 1.0)
        kb = f(2.0, 1.0) - base
        ks = f(1.0, 2.0) - base
        _COST_CACHE[key] = (base - kb - ks, kb, ks)
    return _COST_CACHE[key]


def half_spread(sym, abs_delta, dte, prem, vol, rank, mult=1.0):
    if not (np.isfinite(prem) and prem > 0 and np.isfinite(abs_delta)):
        return float("nan")
    return mult * _spread.half_spread(cls_of(sym), sym, float(abs_delta), float(max(dte, 1)), float(prem),
                                      volume_contracts=float(vol), expiry_rank=int(rank), pct=spread_pct(sym))


def pick_strike(c: Contract, F_e: float, T_e: float, is_call: bool):
    """Strike among liquid previous-day strikes with |delta| (model b IV, entry forward) closest to 0.40."""
    K, V = c.cand["CE" if is_call else "PE"]
    if not len(K):
        return None
    df = math.exp(-c.r * T_e)
    iv = c.iv(np.log(K / F_e))
    dl = np.abs(b76_delta(F_e, K, T_e, iv, df, is_call))
    i = int(np.argmin(np.abs(dl - TARGET_DELTA)))
    return float(K[i]), float(V[i]), float(dl[i])


def price_b(c: Contract, K, F, T, is_call):
    """Model-b synthetic price (vectorised over F, T)."""
    F = np.asarray(F, float)
    T = np.asarray(T, float)
    iv = c.iv(np.log(K / F))
    return b76(F, K, T, iv, np.exp(-c.r * T), is_call), b76_delta(F, K, T, iv, np.exp(-c.r * T), is_call)


# ----------------------------------------------------------------------------- trade evaluation
_MGRID = np.r_[np.linspace(-0.03, 0.0, 31)[:-1], np.linspace(0.0, 0.03, 301), np.linspace(0.03, 0.25, 221)[1:]]


def evaluate(tr: pd.DataFrame, mats: dict, contracts: dict, last_close: dict, lots: pd.DataFrame,
             theta: str = "trading", spread_mult: float = 1.0, need_req: bool = False,
             path_stats: bool = False) -> pd.DataFrame:
    """Price trades with the SYNTHETIC model b.  tr columns: symbol, date (datetime64[D]), side (+1 call
    / -1 put), entry_m, exit_m.  mats[sym] = (days, {o,h,l,c}); contracts[sym][d] = Contract;
    last_close[sym][date] = last 1-min close (U_p).  Returns tr + pricing columns (NaN = no contract)."""
    n = len(tr)
    cols = {k: np.full(n, np.nan) for k in (
        "K", "absdelta_e", "absdelta_x", "dte", "P_e", "P_x", "hs_e", "hs_x", "lot", "cost", "U_e", "U_x",
        "move", "req", "gross_R", "net_R", "pnl_rs", "min_P", "iv_e", "T_e", "T_x")}
    day_idx = {s: {d: i for i, d in enumerate(m[0])} for s, m in mats.items()}
    syms = tr["symbol"].values
    dates = tr["date"].values.astype("datetime64[D]")
    sides = tr["side"].values.astype(int)
    em = tr["entry_m"].values.astype(int)
    xm = tr["exit_m"].values.astype(int)
    lot_cache = {}
    for t in range(n):
        s, d, sd = syms[t], dates[t], sides[t]
        c = contracts.get(s, {}).get(d)
        i = day_idx[s].get(d)
        Up = last_close.get(s, {}).get(c.p) if c is not None else None
        if c is None or i is None or Up is None or sd == 0:
            continue
        close = mats[s][1]["c"][i]
        Ue, Ux = close[em[t] - 1], close[xm[t] - 1]
        is_call = sd > 0
        if theta == "trading":
            Te, Tx = t_trading(c.T_p, c.T_d, em[t]), t_trading(c.T_p, c.T_d, xm[t])
        else:
            Te, Tx = t_calendar(c.T_d, em[t]), t_calendar(c.T_d, xm[t])
        Fe, Fx = c.F_p * Ue / Up, c.F_p * Ux / Up
        pk = pick_strike(c, Fe, Te, is_call)
        if pk is None:
            continue
        K, vol, _ = pk
        (Pe, Px), (de, dx) = price_b(c, K, [Fe, Fx], [Te, Tx], is_call)
        if not (Pe > 0 and np.isfinite(Px)):
            continue
        dte = float((c.expiry - d).astype(int))
        hse = half_spread(s, abs(de), dte, Pe, vol, c.rank, spread_mult)
        hsx = half_spread(s, abs(dx), dte, max(Px, 0.05), vol, c.rank, spread_mult)
        key = (s, d)
        if key not in lot_cache:
            lot_cache[key] = lot_size(lots, s, d)
        q = lot_cache[key]
        fx, kb, ks = cost_coeffs(d, exchange_of(s))
        buy = (Pe + hse) * q
        sell = max(Px - hsx, 0.0) * q
        cost = fx + kb * buy + ks * sell if sell > 0 else fx - 20.0 * 1.18 + kb * buy
        pnl = sell - buy - cost
        for k, v in (("K", K), ("absdelta_e", abs(de)), ("absdelta_x", abs(dx)), ("dte", dte), ("P_e", Pe),
                     ("P_x", Px), ("hs_e", hse), ("hs_x", hsx), ("lot", q), ("cost", cost), ("U_e", Ue),
                     ("U_x", Ux), ("move", sd * (Ux / Ue - 1)), ("gross_R", Px / Pe - 1),
                     ("net_R", pnl / (buy + cost)), ("pnl_rs", pnl), ("iv_e", float(c.iv(np.log(K / Fe)))),
                     ("T_e", Te), ("T_x", Tx)):
            cols[k][t] = v
        if path_stats and xm[t] > em[t] + 1:
            mm = np.arange(em[t] + 1, xm[t])
            Fp_ = c.F_p * close[mm - 1] / Up
            Tp_ = t_trading(c.T_p, c.T_d, mm) if theta == "trading" else t_calendar(c.T_d, mm)
            pp, _ = price_b(c, K, Fp_, Tp_, is_call)
            cols["min_P"][t] = min(float(np.min(pp)), Pe, Px)
        if need_req:
            Fg = Fe * (1 + sd * _MGRID)
            Pg, _ = price_b(c, K, Fg, np.full(len(Fg), Tx), is_call)
            net = (np.maximum(Pg - hse, 0) * q) * (1 - ks) - buy * (1 + kb) - fx
            pos = np.where(net >= 0)[0]
            if len(pos):
                j = pos[0]
                if j == 0:
                    cols["req"][t] = _MGRID[0]
                else:
                    a, b = net[j - 1], net[j]
                    cols["req"][t] = _MGRID[j - 1] + (_MGRID[j] - _MGRID[j - 1]) * (-a) / (b - a)
            else:
                cols["req"][t] = np.inf
    out = tr.copy()
    for k, v in cols.items():
        out[k] = v
    return out


# ----------------------------------------------------------------------------- statistics
def cluster_boot(x, day, B=2000, seed=0):
    """Day-clustered bootstrap of the mean. Returns (mean, lo, hi, boot_means)."""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    x, day = x[ok], np.asarray(day)[ok]
    if len(x) == 0:
        return np.nan, np.nan, np.nan, np.array([])
    u, inv = np.unique(day, return_inverse=True)
    s = np.bincount(inv, weights=x)
    c = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(len(u), np.full(len(u), 1.0 / len(u)), size=B).astype(float)
    bm = (w @ s) / (w @ c)
    return float(x.mean()), float(np.quantile(bm, 0.025)), float(np.quantile(bm, 0.975)), bm


def cluster_boot_diff(x, dx, y, dy, B=2000, seed=0):
    """Mean(x) - mean(y) with days resampled jointly (union of days). Returns (diff, lo, hi, p_one_sided)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    mx, my = np.isfinite(x), np.isfinite(y)
    x, dx, y, dy = x[mx], np.asarray(dx)[mx], y[my], np.asarray(dy)[my]
    if len(x) == 0 or len(y) == 0:
        return (np.nan,) * 4
    u = np.unique(np.r_[dx, dy])
    ix, iy = np.searchsorted(u, dx), np.searchsorted(u, dy)
    sx, cx = np.bincount(ix, weights=x, minlength=len(u)), np.bincount(ix, minlength=len(u)).astype(float)
    sy, cy = np.bincount(iy, weights=y, minlength=len(u)), np.bincount(iy, minlength=len(u)).astype(float)
    rng = np.random.default_rng(seed)
    w = rng.multinomial(len(u), np.full(len(u), 1.0 / len(u)), size=B).astype(float)
    bd = (w @ sx) / (w @ cx) - (w @ sy) / (w @ cy)
    d0 = x.mean() - y.mean()
    return float(d0), float(np.quantile(bd, 0.025)), float(np.quantile(bd, 0.975)), float((bd <= 0).mean())


# ----------------------------------------------------------------------------- loaders
INTRA = ROOT / "data" / "intraday" / "1min"
FO = ROOT / "data" / "fo" / "options"
BT = ROOT / "data" / "bt" / "intraday"
YEARS = range(2022, 2027)


def universe():
    st = pd.read_csv(ROOT / "data" / "ref" / "intraday_universe.csv")["symbol"].tolist()
    return list(INDICES) + st


def special_dates():
    s = pd.read_csv(ROOT / "data" / "ref" / "special_sessions.csv")
    return pd.to_datetime(s["date"]).values.astype("datetime64[D]")


def sample_of(sym, dates):
    y = pd.DatetimeIndex(dates).year.values
    if sym == "SENSEX":
        return np.where(y == 2024, "IS", np.where(y >= 2025, "OOS", "pre"))
    return np.where(y <= 2023, "IS", "OOS")


def _load_symbol(sym):
    files = sorted((INTRA / f"symbol={sym}").glob("year=*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def load_all(symbols=None):
    """-> mats{sym: (days, mats)}, info(symbol, date, sigma, prev_close, gap), last_close{sym: {date: U}}"""
    symbols = symbols or universe()
    sp = special_dates()
    mats, last_close, info = {}, {}, []
    for s in symbols:
        df = _load_symbol(s)
        ts = pd.to_datetime(df["ts"]).dt.tz_convert("Asia/Kolkata")
        d = ts.dt.tz_localize(None).dt.normalize().values.astype("datetime64[D]")
        lc = pd.Series(df["close"].values, index=ts.values).groupby(d).last()
        last_close[s] = dict(zip(lc.index.values.astype("datetime64[D]"), lc.values))
        days, m = day_matrices(df, drop_dates=sp)
        mats[s] = (days, m)
        sig = sigma_daily(days, m["c"])
        prev = np.r_[np.nan, m["c"][:-1, -1]]
        info.append(pd.DataFrame({"symbol": s, "date": days, "sigma": sig, "prev_close": prev,
                                  "gap": m["o"][:, 0] / prev - 1}))
    return mats, pd.concat(info, ignore_index=True), last_close


def load_contracts(symbols, mats, refresh=False):
    BT.mkdir(parents=True, exist_ok=True)
    cache = BT / "_chains.parquet"
    if cache.exists() and not refresh:
        ch = pd.read_parquet(cache)
        ch["date"] = ch["date"].values.astype("datetime64[D]")
        ch["expiry"] = ch["expiry"].values.astype("datetime64[D]")
        ca = set(map(tuple, pd.read_parquet(BT / "_ca.parquet").assign(
            date=lambda x: x["date"].values.astype("datetime64[D]")).itertuples(index=False, name=None)))
    else:
        yrs = range(2021, 2027)
        ch = load_chains(FO, symbols, yrs)
        ca = load_ca_break(FO, symbols, yrs)
        ch.to_parquet(cache)
        pd.DataFrame(sorted(ca), columns=["symbol", "date"]).to_parquet(BT / "_ca.parquet")
    return {s: build_contracts(ch, s, mats[s][0], ca) for s in symbols}, ch
