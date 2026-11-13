"""Tests for the P4 swing backtest harness (research/lib/backtest.py, research/lib/trials.py).

  python -I -m pytest research/tests/test_p4_backtest.py -q
"""
from __future__ import annotations

import csv
import math
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
import pytest

from research.lib import backtest as bt
from research.lib import trials

D0 = pd.Timestamp("2023-03-01")


# ----------------------------------------------------------------------------- toy chain
def toy_chain(vol=1000.0, cls="index", symbol="NIFTY", date=D0):
    """Two expiries (8 and 22 cal days); CE strikes with known deltas; one PE."""
    rows = []
    for exp_days, monthly in ((8, False), (22, True)):
        for strike, d, px in ((17000, 0.72, 420.0), (17200, 0.56, 260.0), (17400, 0.41, 150.0),
                              (17500, 0.33, 110.0), (17700, 0.24, 60.0), (17900, 0.14, 25.0), (18500, 0.02, 1.5)):
            rows.append(dict(date=date, exchange="nse", symbol=symbol, cls=cls, expiry=date + pd.Timedelta(days=exp_days),
                             strike=float(strike), opt_type="CE", close=px, volume_contracts=vol, delta=d,
                             dte_cal=exp_days, traded=True, is_monthly=monthly))
        rows.append(dict(date=date, exchange="nse", symbol=symbol, cls=cls, expiry=date + pd.Timedelta(days=exp_days),
                         strike=17400.0, opt_type="PE", close=140.0, volume_contracts=vol, delta=-0.58,
                         dte_cal=exp_days, traded=True, is_monthly=monthly))
    # a 3-day expiry that must never be chosen (dte < 7)
    rows.append(dict(date=date, exchange="nse", symbol=symbol, cls=cls, expiry=date + pd.Timedelta(days=3),
                     strike=17400.0, opt_type="CE", close=80.0, volume_contracts=vol, delta=0.40, dte_cal=3,
                     traded=True, is_monthly=False))
    return pd.DataFrame(rows)


def test_selection_picks_closest_delta_and_bucket():
    s = bt.select_contracts(toy_chain())
    ce = s[s.opt_type == "CE"]
    pick = ce.set_index(["dte_bucket", "delta_target"])["strike"]
    assert pick[("7-10", 0.40)] == 17400 and pick[("7-10", 0.15)] == 17900 and pick[("7-10", 0.25)] == 17700
    assert pick[("7-10", 0.55)] == 17200 and pick[("7-10", 0.70)] == 17000
    assert pick[("21-35", 0.40)] == 17400
    assert (ce.dte_cal >= 7).all()                       # 3-day expiry never chosen
    std = s[s.is_std]
    assert len(std) == 1 and set(std.opt_type) == {"CE"}           # PE |delta| .58 is > .10 from .40
    assert (std.dte_cal == 8).all() and (std.delta_target == 0.40).all()
    # PE: |delta| .58 -> nearest targets .55 (gap .03) and .70 (gap .12 > .10 -> rejected) and .40 (gap .18 rejected)
    pe = s[s.opt_type == "PE"]
    assert set(pe.delta_target[pe.dte_bucket == "7-10"]) == {0.55}


def test_selection_floors_reject():
    assert bt.select_contracts(toy_chain(vol=499.0)).empty            # index volume floor 500
    s = bt.select_contracts(toy_chain(vol=60.0, cls="stock", symbol="RELIANCE"))
    assert not s.empty and set(s.expiry.dt.day - D0.day) == {22}       # stocks: monthly only
    assert (s.close >= 2).all() and 18500 not in set(s.strike)         # premium floor Rs2
    stk = toy_chain(vol=49.0, cls="stock", symbol="RELIANCE")
    assert bt.select_contracts(stk).empty
    # untraded rows are ignored
    ch = toy_chain()
    ch["traded"] = False
    assert bt.select_contracts(ch).empty


def test_stock_std_requires_nearest_monthly_ge7():
    ch = toy_chain(vol=100.0, cls="stock", symbol="RELIANCE")
    ch.loc[ch.dte_cal == 3, "is_monthly"] = True                      # nearest monthly has 3 days -> no std
    s = bt.select_contracts(ch)
    assert not s.is_std.any() and len(s) > 0


# ----------------------------------------------------------------------------- spread / costs
def test_half_spread_vec_matches_scalar():
    rng = np.random.default_rng(3)
    n = 500
    syms = np.array(["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "RELIANCE", "NOTASTOCK"])
    s = rng.choice(syms, n)
    cls = np.where(np.isin(s, ["NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"]), "index", "stock")
    ad, dte = rng.uniform(0, 1, n), rng.integers(0, 80, n)
    p = np.exp(rng.uniform(-3, 8, n))
    v = np.where(rng.random(n) < 0.3, np.nan, rng.integers(0, 10 ** 6, n).astype(float))
    rk, pc = rng.integers(1, 5, n), bt.spread_pct(s)
    a = bt.half_spread_vec(cls, s, ad, dte, p, v, rk, pc)
    b = [bt.SPREAD.half_spread(cls[i], s[i], ad[i], dte[i], p[i], None if np.isnan(v[i]) else v[i], rk[i], pc[i])
         for i in range(n)]
    assert np.allclose(a, b, rtol=1e-12)
    assert bt.spread_pct(["BANKNIFTY", "NIFTY"]).tolist() == [90, 75]


@pytest.mark.parametrize("d0,d1,ex,buy,sell,lot", [("2019-03-01", "2019-03-05", "NSE", 100.0, 120.0, 75),
                                                     ("2023-03-30", "2023-04-03", "NSE", 10.0, 11.0, 50),
                                                     ("2024-09-27", "2024-10-03", "BSE", 50.0, 40.0, 10),
                                                     ("2026-03-30", "2026-04-02", "NSE", 5.0, 0.0, 65)])
def test_costs_vec_matches_scalar(d0, d1, ex, buy, sell, lot):
    c = bt.costs_vec([d0], [d1], ex, [buy], [sell], [lot])
    s = bt.COSTS.round_trip_cost(d0, ex, buy, sell, lot, exit_date=d1)
    for k in ("brokerage", "exchange_txn", "ipft", "sebi_fee", "stamp_duty", "stt_sell", "gst", "total"):
        assert c[k][0] == pytest.approx(s[k], rel=1e-12, abs=1e-12)
    b = bt.COSTS.round_trip_cost(d0, ex, buy, 0.0, lot)
    assert c["buy_leg"][0] == pytest.approx(b["total"], rel=1e-12)


def _P(close_path, n=1, open_path=None, high=None, low=None, cls="index", sym="NIFTY", lot=50.0,
       d0="2023-03-01", hs_scale=None):
    close = np.array([close_path], float)
    dates = pd.bdate_range(d0, periods=bt.NPATH + 1).values[None, :]
    P = {"close": close, "open": np.array([open_path or close_path], float),
         "high": np.array([high or close_path], float), "low": np.array([low or close_path], float),
         "settle": np.full_like(close, np.nan), "delta": np.full_like(close, 0.4), "dte": np.full_like(close, 20.0),
         "vol": np.full_like(close, 5000.0), "rank": np.ones_like(close), "dates": dates,
         "cls": np.array([cls]), "symbol": np.array([sym]), "delta0": np.array([0.4]), "rank0": np.array([1.0]),
         "pct": bt.spread_pct([sym]), "lot": np.array([lot]), "exchange": np.array(["nse"]),
         "close0": close[:, 0], "is_call": np.array([True])}
    P["hs0"] = bt.half_spread_vec(P["cls"], P["symbol"], [0.4], [20.0], P["close0"], [5000.0], [1], P["pct"])
    return P


def test_costs_and_spread_both_legs():
    P = _P([100, 101, 102, 110, 104, 103])
    r = bt.settle_trade(P, np.array([3]), np.array([110.0]), np.array([3]))
    hs_in, hs_out = P["hs0"][0], r["hs_out"][0]
    assert hs_in > 0 and hs_out > 0
    buy, sell = 100 + hs_in, 110 - hs_out
    c = bt.COSTS.round_trip_cost(P["dates"][0, 0], "NSE", buy, sell, 50, exit_date=P["dates"][0, 3])
    pnl = (sell - buy) * 50 - c["total"]
    den = buy * 50 + bt.COSTS.round_trip_cost(P["dates"][0, 0], "NSE", buy, 0.0, 50)["total"]
    assert r["pnl"][0] == pytest.approx(pnl) and r["R"][0] == pytest.approx(pnl / den)
    assert r["R"][0] < (110 / 100 - 1)                                  # net < gross
    r2 = bt.settle_trade(P, np.array([3]), np.array([110.0]), np.array([3]), spread_mult=2.0)
    assert r2["R"][0] < r["R"][0]
    zero = bt.settle_trade(P, np.array([3]), np.array([0.0]), np.array([3]))
    assert zero["R"][0] == pytest.approx(-1.0)                          # total loss incl. buy costs = -1R


def test_expiry_cap():
    H, capped = bt.cap_horizon(np.array([1, 3, 5, 5]), np.array([10, 4, 4, 6]))
    assert H.tolist() == [1, 3, 3, 5] and capped.tolist() == [False, False, True, False]
    # the effective exit day is always strictly before expiry day (dte_trd)
    assert (H < np.array([10, 4, 4, 6])).all()


def test_stale_exit_and_settle_flags():
    close = np.array([[100, 101, np.nan, np.nan, 99, 98],
                      [100, np.nan, np.nan, np.nan, 99, 98],
                      [100, 101, 102, 103, 99, 98]], float)
    settle = np.full_like(close, np.nan)
    settle[1, 3] = 97.5
    px, day, stale, settled = bt.mark_at(np.array([3, 3, 3]), close, settle)
    assert px.tolist() == [101, 97.5, 103] and day.tolist() == [1, 3, 3]
    assert stale.tolist() == [True, False, False] and settled.tolist() == [False, True, False]


def test_x1_sl_first_and_gap_fill():
    # day 1 touches both TP (+30%) and SL (-25%) -> SL assumed first, filled at the SL level
    P = _P([100, 100, 100, 100, 100, 100], high=[100, 140, 100, 100, 100, 100], low=[100, 70, 100, 100, 100, 100])
    entry = P["close0"][0] + P["hs0"][0]
    xd, raw, pday, why = bt.run_exit_rule(P, "X1", np.array([3.0]), tp=0.30, sl=0.25)
    assert xd[0] == 1 and why[0] == 1 and raw[0] == pytest.approx(entry * 0.75)
    # gap through SL at the open -> filled at the open
    P = _P([100, 60, 60, 60, 60, 60], open_path=[100, 50, 60, 60, 60, 60], high=[100, 65, 60, 60, 60, 60],
           low=[100, 45, 60, 60, 60, 60])
    xd, raw, _, why = bt.run_exit_rule(P, "X1", np.array([3.0]), tp=0.30, sl=0.25)
    assert why[0] == 1 and raw[0] == 50
    # TP only on day 2
    P = _P([100, 105, 120, 110, 100, 100], high=[100, 110, 135, 112, 100, 100], low=[100, 95, 100, 100, 100, 100])
    xd, raw, _, why = bt.run_exit_rule(P, "X1", np.array([3.0]), tp=0.30, sl=0.25)
    assert xd[0] == 2 and why[0] == 2 and raw[0] == pytest.approx((100 + P["hs0"][0]) * 1.3)
    # nothing touched -> time stop at H with the day-H close
    P = _P([100, 101, 102, 103, 104, 105])
    xd, raw, _, why = bt.run_exit_rule(P, "X1", np.array([3.0]), tp=0.30, sl=0.25)
    assert xd[0] == 3 and why[0] == 0 and raw[0] == 103


def test_x3_x4_rules():
    P = _P([100, 135, 150, 120, 120, 120], open_path=[100, 100, 140, 150, 120, 120],
           high=[100, 140, 160, 150, 120, 120], low=[100, 100, 140, 115, 120, 120])
    entry = 100 + P["hs0"][0]
    xd, raw, _, why = bt.run_exit_rule(P, "X3", np.array([5.0]))
    trail = 160 - (160 - entry) / 3                                    # armed (+30%), peak 160, give back 1/3
    assert xd[0] == 3 and why[0] == 4 and raw[0] == pytest.approx(trail)
    P["open"][0, 3] = 120.0                                            # gapped below the trail -> open fill
    xd, raw, _, why = bt.run_exit_rule(P, "X3", np.array([5.0]))
    assert xd[0] == 3 and raw[0] == 120.0
    P = _P([100, 95, 130, 130, 130, 130])
    xd, raw, _, why = bt.run_exit_rule(P, "X4", np.array([3.0]))
    assert xd[0] == 1 and why[0] == 5 and raw[0] == 95
    P = _P([100, 120, 130, 130, 130, 130])
    xd, raw, _, why = bt.run_exit_rule(P, "X4", np.array([3.0]))
    assert xd[0] == 3 and why[0] == 0


# ----------------------------------------------------------------------------- statistics
def test_cluster_bootstrap_covers_known_mean():
    rng = np.random.default_rng(11)
    covered = 0
    for rep in range(40):
        days = np.repeat(np.arange(150), 6)
        day_eff = rng.normal(0, 0.5, 150)[days]                       # strong within-day correlation
        x = 0.2 + day_eff + rng.normal(0, 0.5, len(days))
        ci = bt.cluster_bootstrap_ci(x, days, n_boot=500, seed=rep)
        covered += ci["lo"] <= 0.2 <= ci["hi"]
        assert ci["n_clusters"] == 150 and ci["n"] == 900
    assert covered >= 34                                               # ~95% nominal


def test_diff_vs_baseline():
    rng = np.random.default_rng(5)
    days = np.repeat(np.arange(200), 5)
    base = rng.normal(0, 1, len(days))
    sig_mask = np.arange(len(days)) % 5 == 0
    d = bt.diff_vs_baseline(base[sig_mask] + 1.0, days[sig_mask], base, days, n_boot=1000)
    assert d["p"] < 0.01 and d["lo"] > 0
    d0 = bt.diff_vs_baseline(base[sig_mask], days[sig_mask], base, days, n_boot=1000)
    assert d0["p"] > 0.01


def test_bh_hand_computed():
    q = bt.bh_adjust([0.01, 0.04, 0.03, 0.20])
    assert np.allclose(q, [0.04, 0.16 / 3, 0.16 / 3, 0.20])
    assert np.isnan(bt.bh_adjust([np.nan, 0.5])[0])


def test_deflated_sharpe_hand_computed():
    # N=1: SR0=0, DSR = Phi(0.1*sqrt(100)/sqrt(1 + 2/4*0.01)) = Phi(0.997509) = 0.840741
    r = bt.deflated_sharpe(sr=0.1, n_obs=101, n_trials=1, skew=0.0, kurt=3.0)
    assert r["sr0"] == 0 and r["dsr"] == pytest.approx(0.8407413, abs=1e-6)
    # N=10, V[SR]=0.01: SR0 = 0.1*((1-g)*1.2815516 + g*1.7892418) = 0.1574598
    r = bt.deflated_sharpe(sr=0.1, n_obs=101, n_trials=10, sr_var=0.01, skew=0.0, kurt=3.0)
    assert r["sr0"] == pytest.approx(0.1574598, abs=1e-6) and r["dsr"] == pytest.approx(0.2832658, abs=1e-6)
    r = bt.deflated_sharpe(sr=0.3, n_obs=101, n_trials=10, sr_var=0.01, skew=-1.0, kurt=6.0)
    assert r["dsr"] == pytest.approx(0.8848025, abs=1e-6)


def test_buyer_metrics():
    pnl = np.array([100, -50, -50, -50, 200, -10])
    R = pnl / 1000
    dates = pd.date_range("2023-01-02", periods=6)
    m = bt.buyer_metrics(pnl, R, dates, theta=np.full(6, -2.0), lot=np.full(6, 10.0), cal_days=np.full(6, 2.0))
    assert m["win_rate"] == pytest.approx(2 / 6) and m["profit_factor"] == pytest.approx(300 / 160)
    assert m["max_dd_rs"] == 150 and m["longest_losing_streak"] == 3
    # theta per loser = 2*10*2 = 40, capped at the loss: min(40,50)*3 + min(40,10) = 130 / 160
    assert m["theta_share_losers"] == pytest.approx(130 / 160)
    t = bt.time_to_target(np.array([[1, 2, 3, 4, 5, 6], [1, 1, 1, 1, 1, 1]], float), np.array([2.0, 2.0]), 0.5)
    assert t["2"] == 0.5 and t["never"] == 0.5


# ----------------------------------------------------------------------------- trial log
def test_log_trial_appends(tmp_path):
    p = str(tmp_path / "trials_log.csv")
    trials.log_trial(path=p, id="B1", variant="a", net_mean_R=-0.1, notes="x, with comma")
    trials.log_trial(path=p, id="B1", variant="b")
    ths = [threading.Thread(target=trials.log_trial, kwargs=dict(path=p, id="T", variant=str(i))) for i in range(20)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    rows = list(csv.reader(open(p, encoding="utf-8")))
    assert rows[0] == trials.COLUMNS
    assert len(rows) == 1 + 22 and rows[1][0] == "B1" and rows[1][15] == "x, with comma"
    assert all(len(r) == len(trials.COLUMNS) for r in rows)
    assert trials.n_trials(p) == 22
    with pytest.raises(KeyError):
        trials.log_trial(path=p, bogus=1)
