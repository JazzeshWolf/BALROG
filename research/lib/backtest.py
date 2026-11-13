"""BALROG swing backtest harness (pure functions; no I/O at import except loading the engine models).

Protocol: research/02_hypotheses.md §0. Everything here works on in-memory frames/arrays so it can be
unit-tested on toy chains (research/tests/test_p4_backtest.py). The table builder is
research/scripts/p4_build_trades.py; the B1 report is research/scripts/p4_baseline.py.

Conventions
- Premiums are per unit; rupee P&L = premium x lot_size (1 lot per trade).
- Fills: buy = close + half_spread, sell = max(price - half_spread, 0) (half-spread from engine/spread.py,
  pct=75, pct=90 for BANKNIFTY, contracts traded passed). Statutory costs from engine/costs.py.
- R (net) = (sell_fill - buy_fill) * lot - total_costs  /  (buy_fill * lot + buy-leg costs)
  R (gross) = close_exit / close_entry - 1 (no spread, no costs).
- Path arrays have shape (n, 6): column k = trading day t+k (k=0 entry day). NaN = untraded.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import math
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load_engine(name):
    spec = importlib.util.spec_from_file_location(f"balrog_engine_{name}", os.path.join(ROOT, "engine", f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COSTS = _load_engine("costs")
SPREAD = _load_engine("spread")

# ----------------------------------------------------------------------------- protocol constants
DELTA_TARGETS = (0.15, 0.25, 0.40, 0.55, 0.70)
DTE_BUCKETS = (("7-10", 7, 10), ("11-20", 11, 20), ("21-35", 21, 35), ("36+", 36, 100000))
H_LIST = (1, 2, 3, 5)
NPATH = 5
VOL_FLOOR = {"index": 500.0, "stock": 50.0}
MIN_PREMIUM = 2.0
MAX_DELTA_GAP = 0.10
STD_DELTA = 0.40
MIN_DTE = 7
X1_TP = (0.30, 0.50, 1.00)
X1_SL = (0.25, 0.40, 0.60)
X3_ARM = 0.30
X3_GIVEBACK = 1.0 / 3.0
H_MAX = (3, 5)
BROKERAGE = 20.0


def spread_pct(symbol) -> np.ndarray:
    """75th percentile half-spread, 90th for BANKNIFTY (p3_spread_model.md: p75 under-covers BANKNIFTY)."""
    s = np.asarray(symbol).astype(str)
    return np.where(s == "BANKNIFTY", 90, 75)


def sample_label(dates, symbols) -> np.ndarray:
    """IS / OOS per §0: 2019-2023 IS, 2024+ OOS; SENSEX (and other BSE indices): IS 2024, OOS 2025+."""
    d = pd.to_datetime(pd.Series(dates)).dt.year.to_numpy()
    s = np.asarray(symbols).astype(str)
    bse = np.isin(s, ["SENSEX", "BANKEX", "SENSEX50"])
    is_ = np.where(bse, d <= 2024, d <= 2023)
    return np.where(is_, "IS", "OOS")


def grp_label(cls, symbol) -> np.ndarray:
    s = np.asarray(symbol).astype(str)
    c = np.asarray(cls).astype(str)
    return np.where(c == "stock", "stocks", np.where(np.isin(s, ["NIFTY", "BANKNIFTY", "SENSEX"]), s, "other_index"))


# ----------------------------------------------------------------------------- vectorised spread model
def half_spread_vec(cls, symbol, abs_delta, dte_cal, premium, volume=None, expiry_rank=1, pct=75) -> np.ndarray:
    """Vectorised engine.spread.half_spread (identical numbers; tested against the scalar version).
    volume NaN/None -> the model's cell median (as the scalar does for volume_contracts=None).
    NaN premium -> NaN."""
    m = SPREAD.model()
    prem_raw = np.asarray(premium, dtype=float)
    n = prem_raw.shape[0]
    cls = np.broadcast_to(np.asarray(cls).astype(str), (n,))
    symbol = np.broadcast_to(np.asarray(symbol).astype(str), (n,))
    ad = np.clip(np.abs(np.nan_to_num(np.broadcast_to(np.asarray(abs_delta, dtype=float), (n,)), nan=0.0)), 0.0, 1.0)
    dte = np.maximum(np.broadcast_to(np.asarray(dte_cal, dtype=float), (n,)), 1.0)
    prem = np.maximum(np.nan_to_num(prem_raw, nan=m["tick"]), m["tick"])
    rank = np.maximum(np.broadcast_to(np.asarray(expiry_rank, dtype=float), (n,)).astype(int), 1)
    vol = np.full(n, np.nan) if volume is None else np.broadcast_to(np.asarray(volume, dtype=float), (n,)).copy()
    pct = np.broadcast_to(np.asarray(pct, dtype=float), (n,))

    # group / class on unique (cls, symbol) pairs
    pair_codes, pairs = pd.factorize(pd.Series(cls).str.cat(pd.Series(symbol), sep=""))
    pg, pc = [], []
    for ps in pairs:
        ci, sy = ps.split("", 1)
        gg, cc = SPREAD.group_of(ci, sy, m)
        pg.append(gg)
        pc.append(cc)
    g = np.asarray(pg, dtype=object)[pair_codes] if n else np.array([], object)
    c = np.asarray(pc, dtype=object)[pair_codes] if n else np.array([], object)
    e = m["edges"]
    dd = np.searchsorted(e["delta"], ad, side="right")
    tt = np.searchsorted(e["dte"], dte, side="right")
    pp = np.searchsorted(e["premium"], prem, side="right")
    rr = np.minimum(rank, 3)

    # bucket lookup on unique cells
    combo_code = pair_codes.astype(np.int64) * 100000 + dd * 10000 + tt * 1000 + pp * 10 + rr
    codes, uniq_code = pd.factorize(combo_code)
    first = pd.Series(np.arange(n)).groupby(codes).first().to_numpy() if n else np.array([], int)
    uniq = [(g[j], c[j], int(dd[j]), int(tt[j]), int(pp[j]), int(rr[j])) for j in first]
    cell_q = np.full((len(uniq), 5), np.nan)
    for i, (gg, cc, d_, t_, p_, r_) in enumerate(uniq):
        v = {"g": gg, "c": cc, "d": d_, "t": t_, "p": p_, "r": r_}
        for lvl in m["levels"]:
            if "g" in lvl and gg == "index":
                continue
            cell = m["cells"].get(lvl + ":" + "|".join(str(v[ch]) for ch in lvl))
            if cell is not None:
                cell_q[i] = cell[:5]
                break
    cq = cell_q[codes]
    kind = m["model"]
    if kind == "bucket":
        q = cq[:, :3]
    else:
        vol = np.where(np.isnan(vol), cq[:, 4], vol)
        lp = np.log(prem)
        X = np.column_stack([np.ones(n), lp, lp * lp, ad, ad * ad, np.log(dte), np.log1p(np.maximum(vol, 0.0)),
                             (rank == 2).astype(float), (rank >= 3).astype(float)])
        qp = np.full((n, 3), np.nan)
        for gg in np.unique(g):
            sel = g == gg
            pm = m["param"].get(gg) or m["param"][c[sel][0]]
            loc = X[sel] @ np.asarray(pm["b"])
            sc = np.exp(X[sel] @ np.asarray(pm["g"]))
            qp[sel] = np.minimum(np.exp(loc[:, None] + sc[:, None] * np.asarray(pm["zq"])[None, :]), 2.0)
        q = qp if kind == "param" else np.sqrt(cq[:, :3] * qp)
    pcts = m["pcts"]
    rel = np.empty(n)
    lo = pct <= pcts[0]
    hi = pct >= pcts[-1]
    rel[:] = np.nan
    rel[lo] = q[lo, 0]
    rel[hi & ~lo] = q[hi & ~lo, -1]
    mid = ~lo & ~hi
    if mid.any():
        i = np.searchsorted(pcts, pct[mid], side="right") - 1
        w = (pct[mid] - np.asarray(pcts)[i]) / (np.asarray(pcts)[i + 1] - np.asarray(pcts)[i])
        qm = q[mid]
        rel[mid] = np.exp((1 - w) * np.log(qm[np.arange(len(i)), i]) + w * np.log(qm[np.arange(len(i)), i + 1]))
    full = np.minimum(np.maximum(rel * prem, m["tick"]), 2.0 * prem)
    out = full / 2.0
    out[np.isnan(prem_raw)] = np.nan
    return out


# ----------------------------------------------------------------------------- vectorised costs
def _as_day(dates) -> np.ndarray:
    a = np.asarray(dates)
    if not np.issubdtype(a.dtype, np.datetime64):
        a = pd.to_datetime(pd.Series(a)).to_numpy()
    return a.astype("datetime64[D]")


def _rate_vec(component, dates) -> np.ndarray:
    rows = COSTS.SCHEDULE[component]
    eff = np.array([np.datetime64(r[0]) for r in rows], dtype="datetime64[D]")
    val = np.array([r[1] for r in rows])
    d = _as_day(dates)
    i = np.searchsorted(eff, d, side="right") - 1
    if (i < 0).any():
        raise ValueError(f"{component}: date before schedule start")
    return val[i]


def costs_vec(entry_date, exit_date, exchange, buy, sell, lot, brokerage_per_order=BROKERAGE) -> dict:
    """Vectorised engine.costs.round_trip_cost for 1 lot, sell before expiry (sell=0 -> no sell order).
    Returns arrays: brokerage, exchange_txn, ipft, sebi_fee, stamp_duty, stt_sell, gst, total, buy_leg
    (buy_leg = the buy order's own charges incl. its GST; used in the R denominator)."""
    buy = np.asarray(buy, float)
    sell = np.nan_to_num(np.asarray(sell, float), nan=0.0)
    lot = np.asarray(lot, float)
    n = buy.shape[0]
    ex = np.broadcast_to(np.char.upper(np.asarray(exchange).astype(str)), (n,))
    nse = ex == "NSE"
    ed = np.broadcast_to(_as_day(entry_date), (n,))
    xd = np.broadcast_to(_as_day(exit_date), (n,))
    buy_t = buy * lot
    sold = sell > 0
    sell_t = np.where(sold, sell * lot, 0.0)
    txn_b = np.where(nse, _rate_vec("nse_txn", ed), _rate_vec("bse_txn", ed)) * buy_t
    txn_s = np.where(nse, _rate_vec("nse_txn", xd), _rate_vec("bse_txn", xd)) * sell_t
    ipft_b = np.where(nse, _rate_vec("nse_ipft", ed), 0.0) * buy_t
    ipft_s = np.where(nse, _rate_vec("nse_ipft", xd), 0.0) * sell_t
    sebi_b = _rate_vec("sebi_fee", ed) * buy_t
    sebi_s = _rate_vec("sebi_fee", xd) * sell_t
    stamp = _rate_vec("stamp_buy", ed) * buy_t
    stt = _rate_vec("stt_option_sale", xd) * sell_t
    gst_r = _rate_vec("gst", ed)
    brk = brokerage_per_order * (1 + sold.astype(float))
    exch = txn_b + txn_s
    ipft = ipft_b + ipft_s
    sebi = sebi_b + sebi_s
    gst = gst_r * (brk + exch + ipft + sebi)
    total = brk + exch + ipft + sebi + stamp + stt + gst
    buy_leg = brokerage_per_order + txn_b + ipft_b + sebi_b + stamp + gst_r * (brokerage_per_order + txn_b + ipft_b + sebi_b)
    return {"brokerage": brk, "exchange_txn": exch, "ipft": ipft, "sebi_fee": sebi, "stamp_duty": stamp,
            "stt_sell": stt, "gst": gst, "total": total, "buy_leg": buy_leg}


def net_r(buy_fill, sell_fill, lot, costs) -> tuple[np.ndarray, np.ndarray]:
    """-> (net rupees, net R). R denominator = premium paid incl. buy-leg costs."""
    lot = np.asarray(lot, float)
    pnl = (np.asarray(sell_fill, float) - np.asarray(buy_fill, float)) * lot - costs["total"]
    den = np.asarray(buy_fill, float) * lot + costs["buy_leg"]
    return pnl, pnl / den


# ----------------------------------------------------------------------------- contract selection
SEL_KEYS = ["date", "exchange", "symbol"]


def select_contracts(pool: pd.DataFrame, delta_targets=DELTA_TARGETS, buckets=DTE_BUCKETS) -> pd.DataFrame:
    """Pick one contract per (date, exchange, symbol, opt_type, dte_bucket, delta_target).

    pool: option rows (any traded state) with date, exchange, symbol, cls, expiry, strike, opt_type, close,
    volume_contracts, delta, dte_cal, traded, is_monthly.
    Expiry: within each DTE bucket the nearest expiry (by dte_cal) that has traded rows on that day; stocks use
    monthly expiries only. Strike: among rows that traded, pass the floors (volume >= 500 index / 50 stock,
    close >= 2) and have a delta, the one with |delta| closest to the target; rejected if the gap > 0.10.
    Ties: larger volume, then lower strike.
    is_std_exp: expiry = §0 standard expiry (index: nearest with dte_cal >= 7; stock: nearest monthly, none if
    it has < 7 days). is_std = is_std_exp & delta_target == 0.40.
    """
    p = pool[pool["traded"].astype(bool)].copy()
    cls = p["cls"].astype(str)
    p = p[(cls == "index") | p["is_monthly"].astype(bool)]
    if p.empty:
        return pd.DataFrame()
    cls = p["cls"].astype(str)
    # standard expiry per symbol-day
    exp = p.drop_duplicates(SEL_KEYS + ["expiry"])[SEL_KEYS + ["expiry", "dte_cal", "cls"]].copy()
    exp["cls"] = exp["cls"].astype(str)
    stock_near = exp[exp["cls"] == "stock"].sort_values("dte_cal").drop_duplicates(SEL_KEYS)
    stock_std = stock_near[stock_near["dte_cal"] >= MIN_DTE]
    idx_std = exp[(exp["cls"] == "index") & (exp["dte_cal"] >= MIN_DTE)].sort_values("dte_cal").drop_duplicates(SEL_KEYS)
    std = pd.concat([stock_std, idx_std])[SEL_KEYS + ["expiry"]].assign(is_std_exp=True)
    # bucket expiries
    parts = []
    for name, lo, hi in buckets:
        b = exp[(exp["dte_cal"] >= lo) & (exp["dte_cal"] <= hi)].sort_values("dte_cal").drop_duplicates(SEL_KEYS)
        parts.append(b[SEL_KEYS + ["expiry"]].assign(dte_bucket=name))
    chosen = pd.concat(parts, ignore_index=True)
    floor = np.where(cls == "index", VOL_FLOOR["index"], VOL_FLOOR["stock"])
    el = p[(p["volume_contracts"].to_numpy() >= floor) & (p["close"] >= MIN_PREMIUM) & p["delta"].notna()]
    el = el.merge(chosen, on=SEL_KEYS + ["expiry"], how="inner")
    if el.empty:
        return pd.DataFrame()
    ad = el["delta"].abs().to_numpy(float)
    reps = []
    for tgt in delta_targets:
        r = el.assign(delta_target=tgt, delta_gap=np.abs(ad - tgt))
        r = r[r["delta_gap"] <= MAX_DELTA_GAP + 1e-12]
        reps.append(r)
    x = pd.concat(reps, ignore_index=True)
    x = x.sort_values(SEL_KEYS + ["opt_type", "dte_bucket", "delta_target", "delta_gap", "volume_contracts", "strike"],
                      ascending=[True] * 6 + [True, False, True], kind="mergesort")
    x = x.drop_duplicates(SEL_KEYS + ["opt_type", "dte_bucket", "delta_target"], keep="first")
    x = x.merge(std, on=SEL_KEYS + ["expiry"], how="left")
    x["is_std_exp"] = x["is_std_exp"].eq(True)
    x["is_std"] = x["is_std_exp"] & np.isclose(x["delta_target"], STD_DELTA)
    return x.reset_index(drop=True)


# ----------------------------------------------------------------------------- exit marks
def cap_horizon(h, dte_trd):
    """Never hold into expiry day: exit no later than the trading day before expiry (dte_trd - 1).
    -> (effective h, capped flag). Effective h < 1 means no trade."""
    cap = np.asarray(dte_trd, int) - 1
    H = np.minimum(np.asarray(h, int), cap)
    return H, cap < np.asarray(h, int)


def mark_at(k, close, settle, hcap=None):
    """Exit mark at path day k (int array, 1..NPATH) given path arrays (n, NPATH+1).
    traded on day k -> close_k; else the last traded close on days 1..k-1 (exit_stale); else settle_k
    (exit_settle). Returns (price, day_of_price, stale, settled). day_of_price is the path column whose
    characteristics price the half-spread (k for settle)."""
    n = close.shape[0]
    k = np.asarray(k, int)
    rows = np.arange(n)
    kk = np.clip(k, 0, close.shape[1] - 1)
    px = close[rows, kk].copy()
    day = kk.copy()
    stale = np.zeros(n, bool)
    settled = np.zeros(n, bool)
    miss = np.isnan(px) & (k >= 1)
    for j in range(close.shape[1] - 1, 0, -1):  # last traded day before k
        cand = miss & np.isnan(px) & (j < kk) & ~np.isnan(close[:, j])
        # only accept the latest j: iterate downwards, fill once
        px[cand] = close[cand, j]
        day[cand] = j
        stale[cand] = True
    still = miss & np.isnan(px)
    if settle is not None:
        sp = settle[rows, kk]
        ok = still & ~np.isnan(sp)
        px[ok] = sp[ok]
        settled[ok] = True
    px[k < 1] = np.nan
    return px, day, stale, settled


def path_hs(day, price, P, scale=1.0):
    """Half-spread for a sell at `price` on path day `day` using that day's characteristics.
    P: dict of path arrays + per-trade scalars (cls, symbol, delta0, rank0, pct)."""
    n = len(price)
    rows = np.arange(n)
    d = np.clip(np.asarray(day, int), 0, NPATH)
    ad = np.abs(P["delta"][rows, d])
    ad = np.where(np.isnan(ad), np.abs(P["delta0"]), ad)
    dte = P["dte"][rows, d]
    vol = P["vol"][rows, d]
    rank = P["rank"][rows, d]
    rank = np.where(np.isnan(rank), P["rank0"], rank)
    ok = ~np.isnan(price) & ~np.isnan(dte)
    out = np.full(n, np.nan)
    if ok.any():
        out[ok] = half_spread_vec(P["cls"][ok], P["symbol"][ok], ad[ok], dte[ok], price[ok], vol[ok],
                                  rank[ok], P["pct"][ok])
    return out * scale


def settle_trade(P, exit_day, raw_price, price_day, spread_mult=1.0):
    """Sell `raw_price` (pre-spread) on path day `exit_day`; returns dict of fill, costs, pnl, R."""
    hs_in = P["hs0"] * spread_mult
    buy = P["close0"] + hs_in
    hs_out = path_hs(price_day, raw_price, P, spread_mult)
    sell = np.maximum(raw_price - hs_out, 0.0)
    rows = np.arange(len(buy))
    xdate = P["dates"][rows, np.clip(np.asarray(exit_day, int), 0, NPATH)]
    bad = np.isnan(sell) | pd.isna(xdate)
    xdate = np.where(bad, P["dates"][:, 0], xdate)
    c = costs_vec(P["dates"][:, 0], xdate, P["exchange"], buy, np.where(bad, 0.0, sell), P["lot"])
    pnl, r = net_r(buy, sell, P["lot"], c)
    pnl[bad] = np.nan
    r[bad] = np.nan
    for v in c.values():
        v[bad] = np.nan
    return {"buy": buy, "sell": sell, "hs_out": hs_out, "costs": c, "pnl": pnl, "R": r}


# ----------------------------------------------------------------------------- exit rules (X1-X4)
def _time_stop(P, H):
    px, day, stale, settled = mark_at(H, P["close"], P.get("settle"))
    return px, day


def run_exit_rule(P, rule, H, tp=None, sl=None, variant=None, spread_mult=1.0):
    """Simulate one exit rule with time stop at H (array, already capped; NaN/0 -> no trade).
    Returns (exit_day, raw_price, price_day, reason) where reason: 0 time, 1 SL, 2 TP, 3 invalidation, 4 trail,
    5 fast-stop. Fills: SL/TP at the trigger level, or at the day's open if the contract opened through it;
    SL is assumed first when both are touched on one day. Untraded days never trigger premium rules."""
    n = len(P["close0"])
    H = np.asarray(H, float)
    Hi = np.nan_to_num(H, nan=0).astype(int)
    entry = P["close0"] + P["hs0"] * spread_mult  # levels are relative to the premium paid (buy fill)
    done = np.zeros(n, bool) | (Hi < 1)
    xday = np.zeros(n, int)
    xpx = np.full(n, np.nan)
    pday = np.zeros(n, int)
    reason = np.zeros(n, np.int8)
    O, Hh, L, C = P["open"], P["high"], P["low"], P["close"]
    peak = entry.copy()

    def fire(mask, d, price, why):
        nonlocal done
        mask = mask & ~done
        xday[mask] = d
        xpx[mask] = price[mask] if np.ndim(price) else price
        pday[mask] = d
        reason[mask] = why
        done = done | mask

    for d in range(1, NPATH + 1):
        live = ~done & (d <= Hi)
        if not live.any():
            continue
        o, h, lo_, c = O[:, d], Hh[:, d], L[:, d], C[:, d]
        o = np.where(np.isnan(o), c, o)
        tr = live & ~np.isnan(c)
        if rule == "X1":
            sl_lv = entry * (1 - sl)
            tp_lv = entry * (1 + tp)
            gap_sl = tr & (o <= sl_lv)
            fire(gap_sl, d, o, 1)
            hit_sl = tr & (lo_ <= sl_lv)
            fire(hit_sl, d, sl_lv, 1)
            gap_tp = tr & (o >= tp_lv)
            fire(gap_tp, d, o, 2)
            hit_tp = tr & (h >= tp_lv)
            fire(hit_tp, d, tp_lv, 2)
        elif rule == "X2":
            uc = P["u_close"][:, d]
            if variant == "extreme":
                call_bad = uc < P["u_low0"]
                put_bad = uc > P["u_high0"]
            else:  # 1 sigma against from the entry-day underlying close
                call_bad = uc < P["u_close0"] * (1 - P["sigma"])
                put_bad = uc > P["u_close0"] * (1 + P["sigma"])
            bad = live & np.where(P["is_call"], call_bad, put_bad)
            if bad.any():
                px, day, _, _ = mark_at(np.full(n, d), C, P.get("settle"))
                m = bad & ~np.isnan(px) & ~done
                xday[m] = d
                xpx[m] = px[m]
                pday[m] = day[m]
                reason[m] = 3
                done = done | m
        elif rule == "X3":
            armed = peak >= entry * (1 + X3_ARM)
            trail = peak - X3_GIVEBACK * (peak - entry)
            gap = tr & armed & (o <= trail)
            fire(gap, d, o, 4)
            hit = tr & armed & (lo_ <= trail)
            fire(hit, d, trail, 4)
            peak = np.where(tr & ~done, np.maximum(peak, np.where(np.isnan(h), c, h)), peak)
            armed = peak >= entry * (1 + X3_ARM)
            trail = peak - X3_GIVEBACK * (peak - entry)
            close_hit = tr & armed & (c <= trail)
            fire(close_hit, d, c, 4)
        elif rule == "X4":
            if d == 1:
                hs1 = path_hs(np.ones(n, int), c, P, spread_mult)
                notprofit = tr & ((c - hs1) <= entry)
                fire(notprofit & (Hi >= 1), 1, c, 5)
        else:
            raise ValueError(rule)
    rest = ~done & (Hi >= 1)
    if rest.any():
        px, day, _, _ = mark_at(np.where(rest, Hi, 1), C, P.get("settle"))
        xday[rest] = Hi[rest]
        xpx[rest] = px[rest]
        pday[rest] = day[rest]
    xpx[Hi < 1] = np.nan
    return xday, xpx, pday, reason


def exit_rule_grid():
    """[(column prefix, rule, kwargs)] - the registered X1-X4 cells."""
    g = []
    for tp in X1_TP:
        for sl in X1_SL:
            g.append((f"x1_tp{int(tp*100)}_sl{int(sl*100)}", "X1", {"tp": tp, "sl": sl}))
    g.append(("x2_ext", "X2", {"variant": "extreme"}))
    g.append(("x2_sig", "X2", {"variant": "sigma"}))
    g.append(("x3_trail", "X3", {}))
    g.append(("x4_fast", "X4", {}))
    return g


# ----------------------------------------------------------------------------- statistics
def _cluster_sums(x, clusters):
    x = np.asarray(x, float)
    ok = ~np.isnan(x)
    codes, uniq = pd.factorize(pd.Series(np.asarray(clusters)[ok]))
    S = np.bincount(codes, weights=x[ok], minlength=len(uniq))
    N = np.bincount(codes, minlength=len(uniq)).astype(float)
    return S, N


def cluster_bootstrap_ci(x, clusters, n_boot=2000, alpha=0.05, seed=0, chunk=250) -> dict:
    """Day-clustered bootstrap (resample entry dates with replacement, keep all trades of a date)."""
    S, N = _cluster_sums(x, clusters)
    C = len(S)
    if C == 0:
        return {"mean": np.nan, "lo": np.nan, "hi": np.nan, "se": np.nan, "n": 0, "n_clusters": 0}
    rng = np.random.default_rng(seed)
    means = []
    for b0 in range(0, n_boot, chunk):
        idx = rng.integers(0, C, size=(min(chunk, n_boot - b0), C))
        means.append(S[idx].sum(1) / N[idx].sum(1))
    means = np.concatenate(means)
    return {"mean": S.sum() / N.sum(), "lo": float(np.quantile(means, alpha / 2)),
            "hi": float(np.quantile(means, 1 - alpha / 2)), "se": float(means.std(ddof=1)),
            "n": int(N.sum()), "n_clusters": C}


def diff_vs_baseline(sig_x, sig_cl, base_x, base_cl, n_boot=2000, seed=0, chunk=250) -> dict:
    """Signal-day trades minus all-eligible-day trades (B1); joint day-clustered bootstrap (same resampled
    dates for both means, so overlap between the samples is respected). p = two-sided percentile p-value."""
    sx, bx = np.asarray(sig_x, float), np.asarray(base_x, float)
    sk, bk = ~np.isnan(sx), ~np.isnan(bx)
    allc = pd.Index(pd.unique(np.concatenate([np.asarray(sig_cl)[sk], np.asarray(base_cl)[bk]])))
    cs = allc.get_indexer(np.asarray(sig_cl)[sk])
    cb = allc.get_indexer(np.asarray(base_cl)[bk])
    C = len(allc)
    Ss = np.bincount(cs, sx[sk], C); Ns = np.bincount(cs, minlength=C).astype(float)
    Sb = np.bincount(cb, bx[bk], C); Nb = np.bincount(cb, minlength=C).astype(float)
    diff = Ss.sum() / Ns.sum() - Sb.sum() / Nb.sum()
    rng = np.random.default_rng(seed)
    out = []
    for b0 in range(0, n_boot, chunk):
        idx = rng.integers(0, C, size=(min(chunk, n_boot - b0), C))
        with np.errstate(invalid="ignore", divide="ignore"):
            out.append(Ss[idx].sum(1) / Ns[idx].sum(1) - Sb[idx].sum(1) / Nb[idx].sum(1))
    d = np.concatenate(out)
    d = d[~np.isnan(d)]
    p = 2 * min((d <= 0).mean(), (d >= 0).mean())
    return {"diff": diff, "lo": float(np.quantile(d, 0.025)), "hi": float(np.quantile(d, 0.975)),
            "p": float(min(1.0, max(p, 1.0 / len(d))))}


def bh_adjust(p) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (q-values), NaNs kept."""
    p = np.asarray(p, float)
    out = np.full_like(p, np.nan)
    ok = ~np.isnan(p)
    pv = p[ok]
    m = len(pv)
    if m == 0:
        return out
    order = np.argsort(pv)
    ranked = pv[order] * m / np.arange(1, m + 1)
    q = np.minimum.accumulate(ranked[::-1])[::-1]
    res = np.empty(m)
    res[order] = np.minimum(q, 1.0)
    out[ok] = res
    return out


EULER_GAMMA = 0.5772156649015329


def _norm_ppf(p):
    from scipy.stats import norm
    return norm.ppf(p)


def expected_max_sr(n_trials, sr_var) -> float:
    """SR0 = sqrt(V[SR]) * ((1-g) Z^-1(1-1/N) + g Z^-1(1-1/(N e))) (Bailey & Lopez de Prado 2014)."""
    if n_trials <= 1:
        return 0.0
    g = EULER_GAMMA
    return math.sqrt(sr_var) * ((1 - g) * _norm_ppf(1 - 1.0 / n_trials) + g * _norm_ppf(1 - 1.0 / (n_trials * math.e)))


def deflated_sharpe(returns=None, n_trials=1, sr_var=None, sr=None, n_obs=None, skew=None, kurt=None) -> dict:
    """Deflated Sharpe ratio (per-observation, non-annualised).
    DSR = Phi( (SR - SR0) sqrt(T-1) / sqrt(1 - skew SR + (kurt-1)/4 SR^2) ), kurt = non-excess kurtosis.
    sr_var: cross-trial variance of SR estimates; default = 1/(T-1) (variance of SR under the null)."""
    from scipy.stats import norm
    if returns is not None:
        r = np.asarray(returns, float)
        r = r[~np.isnan(r)]
        n_obs = len(r)
        sd = r.std(ddof=1)
        sr = r.mean() / sd
        z = (r - r.mean()) / r.std(ddof=0)
        skew = float((z ** 3).mean())
        kurt = float((z ** 4).mean())
    if sr_var is None:
        sr_var = 1.0 / (n_obs - 1)
    sr0 = expected_max_sr(n_trials, sr_var)
    den = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4.0 * sr * sr, 1e-12))
    dsr = float(norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / den))
    return {"sr": float(sr), "sr0": float(sr0), "dsr": dsr, "n_obs": int(n_obs), "skew": skew, "kurt": kurt}


def max_drawdown(pnl_sorted) -> float:
    eq = np.cumsum(np.nan_to_num(np.asarray(pnl_sorted, float)))
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float((peak - eq).max()) if len(eq) else 0.0


def longest_losing_streak(pnl_sorted) -> int:
    best = cur = 0
    for v in np.asarray(pnl_sorted, float):
        if np.isnan(v):
            continue
        cur = cur + 1 if v < 0 else 0
        best = max(best, cur)
    return int(best)


def theta_share(theta, lot, cal_days, pnl) -> float:
    """Losers only: sum_i min(|theta_i| * lot_i * cal_days_i, loss_i) / sum_i loss_i (each trade capped at 100%)."""
    pnl = np.asarray(pnl, float)
    los = pnl < 0
    if not los.any():
        return np.nan
    loss = -pnl[los]
    th = np.abs(np.asarray(theta, float)[los]) * np.asarray(lot, float)[los] * np.asarray(cal_days, float)[los]
    th = np.minimum(np.nan_to_num(th), loss)
    return float(th.sum() / loss.sum())


def time_to_target(high_path, entry, target=0.5, H=NPATH) -> dict:
    """Distribution of the first path day whose contract high >= entry * (1 + target): {1..H, 'never'} shares."""
    hp = np.asarray(high_path, float)[:, 1:H + 1]
    hit = hp >= (np.asarray(entry, float) * (1 + target))[:, None]
    first = np.where(hit.any(1), hit.argmax(1) + 1, 0)
    n = len(first)
    out = {str(d): float((first == d).mean()) if n else np.nan for d in range(1, H + 1)}
    out["never"] = float((first == 0).mean()) if n else np.nan
    return out


def buyer_metrics(pnl, R, dates, theta=None, lot=None, cal_days=None) -> dict:
    """Win rate, avg win/loss (R and Rs), expectancy, profit factor, max DD of a 1-lot sequential equity curve
    (trades ordered by entry date), longest losing streak, theta share of loss on losers."""
    pnl = np.asarray(pnl, float)
    R = np.asarray(R, float)
    ok = ~np.isnan(pnl) & ~np.isnan(R)
    pnl, R = pnl[ok], R[ok]
    d = np.asarray(dates)[ok]
    order = np.argsort(d, kind="mergesort")
    win = pnl > 0
    gw, gl = pnl[win].sum(), -pnl[pnl < 0].sum()
    out = {"n": int(ok.sum()), "win_rate": float(win.mean()) if len(pnl) else np.nan,
           "avg_win_R": float(R[win].mean()) if win.any() else np.nan,
           "avg_loss_R": float(R[~win].mean()) if (~win).any() else np.nan,
           "avg_win_rs": float(pnl[win].mean()) if win.any() else np.nan,
           "avg_loss_rs": float(pnl[~win].mean()) if (~win).any() else np.nan,
           "expectancy_R": float(R.mean()) if len(R) else np.nan,
           "expectancy_rs": float(pnl.mean()) if len(pnl) else np.nan,
           "profit_factor": float(gw / gl) if gl > 0 else np.nan,
           "max_dd_rs": max_drawdown(pnl[order]),
           "longest_losing_streak": longest_losing_streak(pnl[order])}
    if theta is not None:
        out["theta_share_losers"] = theta_share(np.asarray(theta)[ok], np.asarray(lot)[ok],
                                                np.asarray(cal_days)[ok], pnl)
    return out
