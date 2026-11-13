"""Tests for the P3 F&O build (research/lib + data/fo outputs).

  python -I -m pytest research/tests/test_p3_fo.py -q
Data-dependent tests skip (with a reason) when the needed raw/built files are not present yet.
"""
from __future__ import annotations

import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
import pytest

from research.lib import bhav, black76, fo_derive

RAW = os.path.join(ROOT, "data", "raw", "bhavcopy")
FO = os.path.join(ROOT, "data", "fo")


# ----------------------------------------------------------------------------- Black-76
def _grid():
    rng = np.random.default_rng(7)
    n = 4000
    F = rng.uniform(100, 60000, n)
    K = F * np.exp(rng.normal(0, 0.15, n))
    T = rng.uniform(1, 120, n) / 365
    s = rng.uniform(0.05, 1.2, n)
    r = rng.uniform(0.03, 0.07, n)
    c = rng.random(n) < 0.5
    return F, K, T, s, np.exp(-r * T), c, r


def test_black76_price_iv_round_trip():
    F, K, T, s, df, c, _ = _grid()
    p = black76.price(F, K, T, s, df, c)
    lb, _ = black76.bounds(F, K, df, c)
    ok = (p - lb) > 1e-6 * F  # skip numerically-zero time value
    iv = black76.implied_vol(p[ok], F[ok], K[ok], T[ok], df[ok], c[ok])
    assert np.isfinite(iv).mean() > 0.999
    assert np.nanmax(np.abs(iv - s[ok])) < 1e-6


def test_black76_iv_nan_outside_bounds():
    F, K, T, df = 100.0, 90.0, 0.1, 0.995
    lb, ub = black76.bounds(F, K, df, True)
    assert np.isnan(black76.implied_vol(lb * 0.99, F, K, T, df, True))
    assert np.isnan(black76.implied_vol(ub * 1.01, F, K, T, df, True))
    assert np.isnan(black76.implied_vol(5.0, F, K, 0.0, df, True))


def test_greeks_vs_finite_differences():
    F, K, T, s, df, c, r = _grid()
    F, K, T, s, c, r = F[:500], K[:500], T[:500], s[:500], c[:500], r[:500]
    pr = lambda F_=F, T_=T, s_=s: black76.price(F_, K, T_, s_, np.exp(-r * T_), c)
    g = black76.greeks(F, K, T, s, np.exp(-r * T), c, r)
    h = F * 1e-4
    delta = (pr(F + h) - pr(F - h)) / (2 * h)
    gamma = (pr(F + h) - 2 * pr() + pr(F - h)) / h ** 2
    vega = (pr(s_=s + 1e-4) - pr(s_=s - 1e-4)) / 2e-4 / 100
    dt_ = 1 / 365
    eps = dt_ / 100
    theta = (pr(T_=T - eps) - pr(T_=T + eps)) / (2 * eps) / 365  # value change per calendar day as time passes
    scale = np.maximum(F, 1)
    assert np.allclose(g["delta"], delta, atol=1e-5)
    assert np.allclose(g["gamma"] * scale ** 2, gamma * scale ** 2, rtol=1e-3, atol=1e-2 * scale.mean())
    assert np.allclose(g["vega"], vega, rtol=1e-4, atol=1e-6 * scale.mean())
    assert np.allclose(g["theta"], theta, rtol=2e-3, atol=1e-6 * scale.mean())


# ----------------------------------------------------------------------------- calendar / ranks
def test_dte_trading_and_rank_hand_checked():
    # April 2019: holidays 17 (Mahavir Jayanti), 19 (Good Friday), 29 (Mumbai election).
    days = pd.bdate_range("2019-04-01", "2019-05-10")
    cal = days[~days.isin(pd.to_datetime(["2019-04-17", "2019-04-19", "2019-04-29", "2019-05-01"]))]
    d = pd.Timestamp("2019-04-01")
    exps = pd.to_datetime(["2019-04-04", "2019-04-11", "2019-04-25", "2019-05-30"])
    n = fo_derive.dte_trading([d] * 4, exps, cal)
    # 04-04: Apr 2,3,4 -> 3; 04-11: 8; 04-25: 18 weekdays - 2 holidays = 16; 05-30 beyond calendar end
    assert list(n[:3]) == [3, 8, 16]
    # beyond observed calendar (ends 05-10): 16 + Apr26..May10 observed (= 9, excl. Apr29 & May1) + May13..30 weekdays (14)
    assert n[3] == 16 + 9 + 14
    assert fo_derive.dte_trading([pd.Timestamp("2019-04-25")], [pd.Timestamp("2019-04-25")], cal)[0] == 0
    df = pd.DataFrame({"date": d, "exchange": "nse", "symbol": "NIFTY", "expiry": np.repeat(exps, 2)})
    assert fo_derive.expiry_rank(df).tolist() == [1, 1, 2, 2, 3, 3, 4, 4]
    assert fo_derive.monthly_flag(df).tolist() == [False] * 4 + [True] * 4


def test_dte_rank_on_built_data():
    fs = glob.glob(os.path.join(FO, "options", "year=2019", "nse_2019-04.parquet"))
    if not fs:
        pytest.skip("2019-04 NSE options partition not built yet")
    o = pd.read_parquet(fs[0], filters=[("symbol", "==", "NIFTY")])
    o = o[o["date"] == pd.Timestamp("2019-04-01")]
    near = o[o["expiry_rank"] == 1]
    assert set(near["expiry"]) == {pd.Timestamp("2019-04-04")}
    assert (near["dte_cal"] == 3).all() and (near["dte_trd"] == 3).all()
    m = o[o["expiry"] == pd.Timestamp("2019-04-25")]
    assert m["is_monthly"].all() and (m["dte_trd"] == 16).all() and (m["expiry_rank"] == 4).all()


# ----------------------------------------------------------------------------- parsers
def _files(pattern):
    return sorted(glob.glob(os.path.join(RAW, pattern)))


def test_legacy_and_udiff_same_schema():
    leg = _files("nse/fo/*/fo*bhav.csv.zip")
    ud = _files("nse/fo/*/BhavCopy_NSE_FO_*.csv.zip")
    if not leg or not ud:
        pytest.skip("need one legacy and one UDiFF NSE F&O file")
    a, _ = bhav.parse_fo(bhav.read_raw(leg[-1]), "nse", bhav.date_from_name(leg[-1]))
    b, _ = bhav.parse_fo(bhav.read_raw(ud[-1]), "nse", bhav.date_from_name(ud[-1]))
    assert list(a.columns) == list(b.columns) == bhav.FO_COLS
    for c in ("strike", "close", "volume_contracts", "oi_contracts", "lot_size"):
        assert a[c].dtype.kind == "f" and b[c].dtype.kind == "f"
    for f in (a, b):
        assert set(f["inst"]) <= {"OPT", "FUT"} and set(f["opt_type"].dropna()) <= {"CE", "PE"}
    bl = _files("bse/fo/*/bhavcopy*.zip")
    bu = _files("bse/fo/*/BhavCopy_BSE_FO_*.CSV")
    if bl and bu:
        c, _ = bhav.parse_fo(bhav.read_raw(bl[-1]), "bse", bhav.date_from_name(bl[-1]))
        d, st = bhav.parse_fo(bhav.read_raw(bu[-1]), "bse", bhav.date_from_name(bu[-1]))
        assert list(c.columns) == list(d.columns) == bhav.FO_COLS
        assert st["udiff_volume_unit"] == "quantity"


def test_legacy_udiff_agree_on_overlap():
    leg = {bhav.date_from_name(p): p for p in _files("nse/fo/2024/fo*bhav.csv.zip")}
    ud = {bhav.date_from_name(p): p for p in _files("nse/fo/2024/BhavCopy_NSE_FO_*.csv.zip")}
    both = sorted(set(leg) & set(ud))[:3]
    if not both:
        pytest.skip("no date with both legacy and UDiFF NSE F&O files (bulk fetch takes one format per date)")
    for d in both:
        a, _ = bhav.parse_fo(bhav.read_raw(leg[d]), "nse", d)
        b, _ = bhav.parse_fo(bhav.read_raw(ud[d]), "nse", d)
        k = ["symbol", "inst", "expiry", "strike", "opt_type"]
        m = a.merge(b, on=k, suffixes=("_l", "_u"))
        assert len(m) >= 0.95 * min(len(a), len(b))
        assert np.allclose(m["close_l"], m["close_u"], atol=0.051, equal_nan=True)
        assert np.allclose(m["volume_contracts_l"], m["volume_contracts_u"], atol=0.5)
        assert (np.isclose(m["oi_contracts_l"], m["oi_contracts_u"], atol=0.5) | m["oi_contracts_l"].isna()).mean() > 0.98


def _udiff_as_legacy_lots(path):
    """Apply the legacy lot reconstruction to a UDiFF file and compare with NewBrdLotQty."""
    raw = bhav.read_raw(path)
    df, _ = bhav.parse_fo(raw, "nse", bhav.date_from_name(path))
    oi_sh = df["oi_contracts"] * df["lot_size"]
    chg_sh = df["chg_oi_contracts"] * df["lot_size"]
    imp = bhav.implied_multiplier(df[df["inst"] == "FUT"])
    tmp = df.assign(oi=oi_sh.round(), chg=chg_sh.round())
    res = []
    for (s, e), g in tmp.groupby(["symbol", "expiry"]):
        est, how = bhav.estimate_lot(np.r_[g["oi"].values, g["chg"].values], imp.get(s, np.nan))
        res.append((s, e, est, g["lot_size"].iloc[0], how))
    return pd.DataFrame(res, columns=["symbol", "expiry", "est", "true", "how"])


def test_lot_reconstruction_matches_udiff_lots():
    ud = _files("nse/fo/*/BhavCopy_NSE_FO_*.csv.zip")
    if not ud:
        pytest.skip("no UDiFF NSE F&O file")
    r = _udiff_as_legacy_lots(ud[0])
    sure = r[r["how"].isin(["gcd+turnover", "gcd"])]
    assert len(sure) > 100
    assert (sure["est"] == sure["true"]).mean() > 0.98
    # legacy file in 2024 vs a UDiFF file in 2024: per-symbol near-month lot should mostly agree
    leg = _files("nse/fo/2024/fo*bhav.csv.zip")
    ud24 = _files("nse/fo/2024/BhavCopy_NSE_FO_*.csv.zip")
    if leg and ud24:
        a, _ = bhav.parse_fo(bhav.read_raw(leg[-1]), "nse", bhav.date_from_name(leg[-1]))
        b, _ = bhav.parse_fo(bhav.read_raw(ud24[0]), "nse", bhav.date_from_name(ud24[0]))
        la = a[a["lot_src"] != "none"].sort_values("expiry").drop_duplicates("symbol").set_index("symbol")["lot_size"]
        lb = b.sort_values("expiry").drop_duplicates("symbol").set_index("symbol")["lot_size"]
        j = pd.concat([la, lb], axis=1, keys=["l", "u"]).dropna()
        assert (j["l"] == j["u"]).mean() > 0.9  # residual = genuine lot revisions between the two dates


def test_estimate_lot_unit():
    assert bhav.estimate_lot([75 * 3, 75 * 10, 75 * 7, -75 * 2], 74.6) == (75.0, "gcd+turnover")
    # gcd is a multiple of the lot -> the turnover multiplier picks the right divisor
    assert bhav.estimate_lot([150, 300, 450], 75.2)[0] == 75.0
    assert np.isnan(bhav.estimate_lot([], np.nan)[0])


# ----------------------------------------------------------------------------- built data invariants
def _built_options():
    fs = sorted(glob.glob(os.path.join(FO, "options", "year=*", "*.parquet")))
    if not fs:
        pytest.skip("no built options partitions")
    return fs


def test_no_iv_when_not_traded():
    for f in _built_options()[:: max(1, len(_built_options()) // 8)]:
        o = pd.read_parquet(f, columns=["traded", "iv", "delta", "close", "close_masked", "dte_cal"])
        assert not (o["iv"].notna() & ~o["traded"]).any(), f
        assert not (o["delta"].notna() & o["iv"].isna()).any(), f
        assert not (o["iv"].notna() & o["close_masked"]).any(), f
        assert not (o["iv"].notna() & (o["dte_cal"] <= 0)).any(), f


def test_built_schema_and_keys_unique():
    f = _built_options()[0]
    o = pd.read_parquet(f)
    need = ["date", "exchange", "symbol", "cls", "expiry", "strike", "opt_type", "open", "high", "low", "close",
            "settle", "volume_contracts", "oi_contracts", "d_oi_contracts", "lot_size", "traded", "dte_cal",
            "dte_trd", "expiry_rank", "is_weekly", "is_monthly", "fwd", "spot", "iv", "delta", "gamma", "theta",
            "vega", "ca_break"]
    assert all(c in o.columns for c in need)
    assert not o.duplicated(["date", "symbol", "expiry", "strike", "opt_type"]).any()
    assert (o["expiry"] >= o["date"]).all() and (o["expiry_rank"] >= 1).all()
    assert (o["is_weekly"] != o["is_monthly"]).all()
