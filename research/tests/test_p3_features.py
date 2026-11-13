"""Tests for research/lib/features.py (point-in-time daily features).

  python -I -m pytest research/tests/test_p3_features.py -q
All tests run on synthetic frames; the last one checks the built table if it exists.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
import pytest

from research.lib import black76
from research.lib import features as F

BDAYS = pd.bdate_range("2020-01-01", periods=420)


def _px(n_sym=3, n=420, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    for s in range(n_sym):
        c = 100 * np.exp(np.cumsum(rng.normal(0, 0.015, n)))
        o = c * np.exp(rng.normal(0, 0.005, n))
        h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.008, n)))
        l = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.008, n)))
        adj = np.where(np.arange(n) < 200, 0.5, 1.0)          # a 2:1 split at day 200 (raw halves)
        raw = 1 / adj
        rows.append(pd.DataFrame({"date": BDAYS[:n], "symbol": f"S{s}", "cls": "stock" if s else "index",
                                  "open": o * raw, "high": h * raw, "low": l * raw, "close": c * raw,
                                  "aopen": o, "ahigh": h, "alow": l, "aclose": c}))
    return pd.concat(rows, ignore_index=True)


def _eq_at(full: pd.DataFrame, trunc: pd.DataFrame, t, keys=("date", "symbol")):
    a = full[full["date"] == t].sort_values(list(keys)).reset_index(drop=True)
    b = trunc[trunc["date"] == t].sort_values(list(keys)).reset_index(drop=True)
    assert len(a) and len(a) == len(b)
    pd.testing.assert_frame_equal(a[b.columns], b, check_dtype=False, rtol=1e-9, atol=1e-12)


# ----------------------------------------------------------------------------- hand-computed ADX/ATR
def _hand_adx(h, l, c, n):
    m = len(c)
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, m)]
    atr = [np.nan] * m
    atr[n - 1] = sum(tr[:n]) / n
    for i in range(n, m):
        atr[i] = atr[i - 1] + (tr[i] - atr[i - 1]) / n
    pdm, mdm = [np.nan], [np.nan]
    for i in range(1, m):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)

    def rma(x):  # seeded at the n-th finite value (index n since x[0] is NaN)
        out = [np.nan] * m
        out[n] = sum(x[1:n + 1]) / n
        for i in range(n + 1, m):
            out[i] = out[i - 1] + (x[i] - out[i - 1]) / n
        return np.array(out)

    tr2 = [np.nan] + tr[1:]
    a2 = rma(tr2)
    pdi, mdi = 100 * rma(pdm) / a2, 100 * rma(mdm) / a2
    dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    adx = [np.nan] * m
    first = 2 * n - 1
    adx[first] = np.mean(dx[n:first + 1])
    for i in range(first + 1, m):
        adx[i] = adx[i - 1] + (dx[i] - adx[i - 1]) / n
    return np.array(atr), pdi, mdi, np.array(adx)


def test_adx_atr_vs_hand():
    h = np.array([10, 11, 12, 11.5, 12.5, 13, 12, 12.8, 13.5, 14, 13.2, 13.9, 14.6, 15, 14.2, 15.1, 15.8, 15.2], float)
    l = h - np.array([1, 1.2, .8, 1.1, .9, 1.3, 1, .7, 1.2, 1, .9, 1.1, 1, .8, 1.2, 1, .9, 1.1])
    c = (h + l) / 2 + 0.1
    n = 4
    atr, pdi, mdi, adx = _hand_adx(h, l, c, n)
    r = F.adx_atr(h, l, c, n)
    np.testing.assert_allclose(r["atr"], atr, equal_nan=True)
    np.testing.assert_allclose(r["plus_di"], pdi, equal_nan=True)
    np.testing.assert_allclose(r["minus_di"], mdi, equal_nan=True)
    np.testing.assert_allclose(r["adx"], adx, equal_nan=True)
    # first ATR value is the plain mean of the first n true ranges
    assert abs(r["atr"][n - 1] - np.mean([h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)])) < 1e-12


# ----------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("t_idx", [150, 260, 399])
def test_no_lookahead_price(t_idx):
    px = _px()
    t = BDAYS[t_idx]
    full = F.price_features(px)
    tr = px[px["date"] <= t].copy()
    # a later re-adjustment rescales the whole adjusted history; level features must not move
    tr[["aopen", "ahigh", "alow", "aclose"]] *= 3.7
    _eq_at(full, F.price_features(tr), t)


def test_donchian_uses_prior_days_only():
    px = _px(1)
    f = F.price_features(px)
    i = 300
    exp = px["high"].iloc[i - 20:i].max()     # raw = adjusted after the split (factor 1)
    assert abs(f["donch_hi20"].iloc[i] - exp) < 1e-9


def test_no_lookahead_har():
    px = _px(4)
    pk = F.price_features(px)[["date", "symbol", "cls", "pk_var"]]
    pk["cls"] = "stock"
    full = F.har_forecast(F.har_design(pk), min_train=200)
    for t in (BDAYS[300], BDAYS[350], BDAYS[419]):
        tp = pk[pk["date"] <= t]
        tr = F.har_forecast(F.har_design(tp), min_train=200)
        _eq_at(full, tr, t)
    assert full["har_var_h5"].notna().mean() > 0.5


def test_no_lookahead_pctile_beta():
    rng = np.random.default_rng(3)
    x = rng.normal(size=400)
    full = F.rolling_pctile(x)
    for t in (130, 251, 300, 399):
        assert np.allclose(F.rolling_pctile(x[:t + 1])[t], full[t])
    assert np.isnan(full[100]) and 0 <= full[300] <= 100
    df = pd.DataFrame({"date": np.tile(BDAYS[:200], 2), "symbol": np.repeat(["A", "B"], 200),
                       "ret1": rng.normal(0, .01, 400)})
    df["ret20"] = df["ret1"] * 5
    mkt = pd.DataFrame({"date": BDAYS[:200], "m_ret1": rng.normal(0, .01, 200)})
    mkt["m_ret20"] = mkt["m_ret1"] * 3
    t = BDAYS[150]
    _eq_at(F.beta_rs(df, mkt), F.beta_rs(df[df.date <= t], mkt[mkt.date <= t]), t)


def _chain(days, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for d in days:
        S = 100 * (1 + rng.normal(0, .01))
        for ex, dte in ((d + pd.Timedelta(days=3), 3), (d + pd.Timedelta(days=10), 10), (d + pd.Timedelta(days=38), 38)):
            for K in np.arange(80, 121, 2.5):
                for cp in ("CE", "PE"):
                    T = dte / 365
                    iv = 0.2 + 0.3 * (K / S - 1) ** 2
                    px = black76.price(S, K, T, iv, 1.0, cp == "CE")
                    traded = px > 0.05
                    rows.append(dict(date=d, symbol="X", expiry=ex, strike=K, opt_type=cp, traded=traded,
                                     iv=iv if traded else np.nan, fwd=S, r=0.0, spot=S,
                                     oi_contracts=float(rng.integers(0, 1000)), d_oi_contracts=float(rng.integers(-50, 50)),
                                     lot_size=50.0, dte_cal=dte, dte_trd=dte, ca_break=False))
    return pd.DataFrame(rows)


def test_no_lookahead_chain_and_atm():
    days = list(BDAYS[:3])
    ch = _chain(days)
    full = F.chain_features(ch)
    one = F.chain_features(ch[ch["date"] <= days[1]])
    _eq_at(full, one, days[1])
    r = full.iloc[0]
    assert r["std_expiry"] == days[0] + pd.Timedelta(days=10)       # nearest with >= 7 calendar days
    assert abs(r["atm_iv"] - 0.2) < 0.003                            # smile minimum at the forward
    assert r["call_wall"] >= 0.9 * r["spot"] and r["call_wall"] <= 1.1 * r["spot"]


def test_gex_sign_convention():
    ch = _chain([BDAYS[0]])
    calls = ch.assign(oi_contracts=np.where(ch["opt_type"] == "CE", 100.0, 0.0))
    puts = ch.assign(oi_contracts=np.where(ch["opt_type"] == "PE", 100.0, 0.0))
    gc = F.chain_features(calls)["net_gex"].iloc[0]
    gp = F.chain_features(puts)["net_gex"].iloc[0]
    assert gc > 0 and gp < 0 and abs(gc + gp) / gc < 1e-3   # opposite sign (tiny diff: untraded strikes use ATM IV)
    # magnitude: one ATM call, gamma*OI*lot*S^2*0.01
    one = ch[(ch["strike"] == 100) & (ch["opt_type"] == "CE") & (ch["dte_cal"] == 10)].copy()
    one["oi_contracts"] = 10.0
    S = one["spot"].iloc[0]
    gam = F.gamma_b76(S, 100.0, 10 / 365, one["iv"].iloc[0], 1.0)
    assert abs(F.chain_features(one)["net_gex"].iloc[0] - gam * 10 * 50 * S * S * 0.01) < 1e-6 * abs(gam * 500 * S * S)
    # gamma flip exists when calls sit above and puts below spot... and lies between them
    mix = ch.assign(oi_contracts=np.where((ch["opt_type"] == "CE") & (ch["strike"] >= 105), 100.0,
                                          np.where((ch["opt_type"] == "PE") & (ch["strike"] <= 90), 400.0, 0.0)))
    f = F.chain_features(mix).iloc[0]
    assert np.isfinite(f["gamma_flip"]) and 85 < f["gamma_flip"] < 110


def test_no_lookahead_futures_and_labels():
    rng = np.random.default_rng(5)
    rows = []
    for i, d in enumerate(BDAYS[:150]):
        ex = BDAYS[(i // 20 + 1) * 20 + 5] if (i // 20 + 1) * 20 + 5 < 420 else BDAYS[-1]
        rows.append(dict(date=d, symbol="X", expiry=ex, close=100 + rng.normal(), settle=np.nan,
                         volume_contracts=100.0, lot_size=10.0, oi_contracts=1000 + rng.normal(0, 50),
                         d_oi_contracts=rng.normal(0, 20), dte_cal=(ex - d).days, ca_break=False))
    fut = pd.DataFrame(rows)
    full = F.futures_features(fut)
    t = BDAYS[120]
    _eq_at(full, F.futures_features(fut[fut.date <= t]), t)
    lab = F.structure_label([1, -1, 1, -1, 0.4, 2], [1, 1, -1, -1, 2, 0.3])
    assert list(lab) == ["long_buildup", "short_buildup", "short_covering", "long_unwinding", "none", "none"]


def test_wall_break_flags():
    d = pd.DataFrame({"date": BDAYS[:5], "symbol": "X",
                      "spot": [99, 101, 102, 98, 103], "call_wall": [100, 100, 105, 100, 102],
                      "put_wall": [95, 95, 95, 99, 95], "ca_break": [False, False, False, False, True]})
    r = F.wall_breaks(d)
    assert list(r["broke_call_wall"]) == [False, True, False, False, False]   # day 4 is a ca_break
    assert list(r["broke_put_wall"]) == [False, False, False, True, False]
    assert list(r["cross_call_wall_prev"]) == [False, True, False, False, False]


def test_results_tradable_dates():
    # Fri 2021-03-26 after close, Mon 03-29 holiday (Holi) -> reacts Tue 03-30
    tdays = pd.to_datetime(["2021-03-25", "2021-03-26", "2021-03-30", "2021-03-31", "2021-04-01",
                            "2021-04-05", "2021-04-06", "2021-04-07"])
    ev = pd.DataFrame({"symbol": ["A", "B", "C", "D"],
                       "event_date": pd.to_datetime(["2021-03-26", "2021-03-26", "2021-03-29", "2021-04-01"]),
                       "session": ["post", "during", "pre", None]})
    e = F.results_events(ev, tdays).set_index("symbol")
    assert e.loc["A", "react_date"] == pd.Timestamp("2021-03-30") and e.loc["A", "pre_date"] == pd.Timestamp("2021-03-26")
    assert e.loc["B", "react_date"] == pd.Timestamp("2021-03-26") and e.loc["B", "pre_date"] == pd.Timestamp("2021-03-25")
    assert e.loc["C", "react_date"] == pd.Timestamp("2021-03-30")          # pre-market on a holiday
    assert e.loc["D", "react_date"] == pd.Timestamp("2021-04-05") and bool(e.loc["D", "time_unknown"])
    rows = pd.DataFrame({"date": np.tile(tdays, 1), "symbol": "A"})
    p = F.results_panel(rows, e.reset_index(), tdays).set_index("date")
    assert bool(p.loc["2021-03-30", "results_today_tradable"]) and not p.loc["2021-03-26", "results_today_tradable"]
    assert p.loc["2021-03-26", "days_to_results"] == 0 and p.loc["2021-03-25", "days_to_results"] == 1
    # truncating rows does not change values
    p2 = F.results_panel(rows[rows.date <= "2021-03-26"], e.reset_index(), tdays).set_index("date")
    assert p2.loc["2021-03-25", "days_to_results"] == 1
    # dedupe keeps the timed row of a cluster
    ev2 = pd.DataFrame({"symbol": ["Z", "Z"], "event_date": pd.to_datetime(["2021-03-25", "2021-03-26"]),
                        "session": [None, "post"]})
    e2 = F.results_events(ev2, tdays)
    assert len(e2) == 1 and not e2["time_unknown"].iloc[0]


def test_participant_lag_and_no_lookahead():
    days = BDAYS[:6]
    poi = pd.DataFrame([dict(date=d, client_type=ct, fut_idx_long=10 + i, fut_idx_short=10, opt_idx_call_long=5,
                             opt_idx_call_short=5 + i, opt_idx_put_long=7, opt_idx_put_short=3)
                        for i, d in enumerate(days) for ct in ("FII", "Client")])
    full = F.participant_features(poi, days).set_index("date")
    assert np.isnan(full.loc[days[0], "fii_idx_fut_long_ratio"])
    assert abs(full.loc[days[3], "fii_idx_fut_long_ratio"] - 12 / 22) < 1e-12      # file of day 2
    tr = F.participant_features(poi[poi.date <= days[3]], days[:4]).set_index("date")
    assert np.allclose(full.loc[days[3]].values, tr.loc[days[3]].values, equal_nan=True)


# ----------------------------------------------------------------------------- built table
OUT = os.path.join(ROOT, "data", "features", "daily.parquet")


@pytest.mark.skipif(not os.path.exists(OUT), reason="feature table not built yet")
def test_built_table_keys():
    d = pd.read_parquet(OUT, columns=["date", "symbol", "cls", "atm_iv", "broke_call_wall"])
    assert not d.duplicated(["date", "symbol"]).any()
    assert set(d["cls"].unique()) <= {"index", "stock"}
    n = d[d.symbol == "NIFTY"]
    assert n["date"].min() <= pd.Timestamp("2019-01-31") and n["atm_iv"].median() < 0.4
