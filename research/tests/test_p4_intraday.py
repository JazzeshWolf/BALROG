import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib.p4_intraday as P  # noqa: E402


def day(seed=0, n_days=1, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(drift, 0.05, (n_days, P.NB)), axis=1)
    o = np.c_[np.full(n_days, 100.0), c[:, :-1]]
    h = np.maximum(o, c) + 0.01
    l = np.minimum(o, c) - 0.01
    return o, h, l, c


def flat_then_break(up=True):
    """Range 99.9..100.1 for 15 bars, flat inside until bar 40, then a breakout bar."""
    c = np.full((1, P.NB), 100.0)
    h, l = c + 0.1, c - 0.1
    c[0, 40:] = 100.5 if up else 99.5
    h[0, 40:], l[0, 40:] = c[0, 40:] + 0.05, c[0, 40:] - 0.05
    return c.copy(), h.copy(), l.copy()


# ------------------------------------------------------------------ entry: no lookahead
def test_entry_uses_only_bars_up_to_entry_minute():
    o, h, l, c = day(seed=3, n_days=50)
    s0 = P.orb_signals(c, h, l)
    rng = np.random.default_rng(9)
    for r in np.where(s0["side"] != 0)[0]:
        j = s0["entry_m"][r] - 1                     # breakout bar
        c2, h2, l2 = c.copy(), h.copy(), l.copy()
        c2[r, j + 1:] = rng.normal(100, 5, P.NB - j - 1)   # scramble everything after the entry minute
        h2[r, j + 1:] = c2[r, j + 1:] + 1
        l2[r, j + 1:] = c2[r, j + 1:] - 1
        s1 = P.orb_signals(c2, h2, l2)
        assert s1["side"][r] == s0["side"][r]
        assert s1["entry_m"][r] == s0["entry_m"][r]
        assert s1["or_hi"][r] == s0["or_hi"][r] and s1["or_lo"][r] == s0["or_lo"][r]


def test_range_bars_after_window_do_not_change_range_and_entry_not_before_0930():
    c, h, l = flat_then_break()
    s = P.orb_signals(c, h, l, L=15)
    assert s["or_hi"][0] == pytest.approx(100.1) and s["or_lo"][0] == pytest.approx(99.9)
    assert s["side"][0] == 1 and s["entry_m"][0] == 41      # bar 40 closes beyond -> act at minute 41
    # a spike inside the range window but before the cutoff counts; one after does not
    h2 = h.copy()
    h2[0, 15] = 200
    assert P.orb_signals(c, h2, l, L=15)["or_hi"][0] == pytest.approx(100.1)
    # a close beyond the range during the range window itself is not an entry
    c3, h3, l3 = flat_then_break()
    c3[0, 10] = 100.05
    assert P.orb_signals(c3, h3, l3, L=15)["entry_m"][0] == 41


def test_m7_signal_uses_0945_and_trades_1500_1525():
    o, h, l, c = day(seed=5, n_days=3)
    s = P.m7_signals(o, c)
    assert np.all(s["side"] == np.sign(c[:, 29] / o[:, 0] - 1))
    c2 = c.copy()
    c2[:, 30:] *= 1.5                               # anything after 09:45 cannot change the side
    assert np.all(P.m7_signals(o, c2)["side"] == s["side"])
    assert P.m_of("15:00") == 345 and P.m_of("15:25") == 370 and P.m_of("09:20") == 5


# ------------------------------------------------------------------ exit logic
def test_exit_on_midpoint_recross_long_and_short():
    for up in (True, False):
        c, h, l = flat_then_break(up)
        c[0, 100] = 99.95 if up else 100.05           # back through the midpoint (100.0)
        s = P.orb_signals(c, h, l)
        assert s["reason"][0] == "mid" and s["exit_m"][0] == 101


def test_touching_midpoint_is_not_a_cross_and_time_exit_is_1515():
    c, h, l = flat_then_break(True)
    c[0, 100] = 100.0                                # equal to midpoint -> not a re-cross
    s = P.orb_signals(c, h, l)
    assert s["reason"][0] == "time" and s["exit_m"][0] == 360
    s2 = P.orb_signals(c, h, l, exit_m=330)
    assert s2["exit_m"][0] == 330
    c[0, 365] = 90.0                                 # a cross after 15:15 is ignored
    assert P.orb_signals(c, h, l)["exit_m"][0] == 360


def test_no_entry_after_1500():
    c, h, l = flat_then_break(True)
    c[0, 40:] = 100.0
    c[0, 350:] = 100.5
    assert P.orb_signals(c, h, l)["side"][0] == 0


# ------------------------------------------------------------------ IV: previous day only
def _chain(dates, iv_by_date):
    rows = []
    for d in dates:
        for K in (90, 95, 100, 105, 110):
            for t in ("CE", "PE"):
                rows.append({"date": np.datetime64(d, "D"), "exchange": "nse", "symbol": "NIFTY",
                             "expiry": np.datetime64("2024-03-28", "D"), "strike": float(K), "opt_type": t,
                             "close": 5.0, "volume_contracts": 1000.0, "lot_size": 50.0, "traded": True,
                             "dte_cal": 20, "expiry_rank": 1, "is_monthly": True, "fwd": 100.0, "r": 0.065,
                             "iv": iv_by_date[d], "ca_break": False})
    return pd.DataFrame(rows)


def test_contract_uses_previous_day_chain_only():
    dates = ["2024-03-04", "2024-03-05", "2024-03-06"]
    ch = _chain(dates, {"2024-03-04": 0.10, "2024-03-05": 0.20, "2024-03-06": 0.99})
    days = np.array(["2024-03-05", "2024-03-06"], dtype="datetime64[D]")
    con = P.build_contracts(ch, "NIFTY", days, set())
    assert con[days[0]].p == np.datetime64("2024-03-04")
    assert np.allclose(con[days[0]].ivs, 0.10)
    assert con[days[1]].p == np.datetime64("2024-03-05")
    assert np.allclose(con[days[1]].ivs, 0.20)          # the same-day 0.99 IV never leaks in
    # changing day-d rows leaves the day-d contract unchanged
    ch2 = ch.copy()
    ch2.loc[ch2["date"] == np.datetime64("2024-03-06"), "iv"] = 0.01
    assert np.allclose(P.build_contracts(ch2, "NIFTY", days, set())[days[1]].ivs, 0.20)


def test_contract_respects_min_dte_and_ca_break():
    ch = _chain(["2024-03-19"], {"2024-03-19": 0.15})
    d = np.array(["2024-03-20", "2024-03-21"], dtype="datetime64[D]")
    con = P.build_contracts(ch, "NIFTY", d, set())
    assert d[0] in con and d[1] not in con           # 2024-03-28 is 8 days after 03-20, 7 after 03-21
    ch["dte_cal"] = 9
    assert P.build_contracts(ch, "NIFTY", np.array(["2024-03-21"], dtype="datetime64[D]"),
                             {("NIFTY", np.datetime64("2024-03-21", "D"))}) == {}


def test_trading_time_theta():
    assert P.t_trading(10 / 365, 9 / 365, 0) == pytest.approx(10 / 365)
    assert P.t_trading(10 / 365, 9 / 365, P.NB) == pytest.approx(9 / 365)
    assert P.t_calendar(9 / 365, P.NB) == pytest.approx(9 / 365)


def test_cost_coeffs_match_engine():
    from engine import costs
    fx, kb, ks = P.cost_coeffs(np.datetime64("2025-03-03"), "NSE")
    ref = costs.round_trip_cost("2025-03-03", "NSE", 100.0, 120.0, 75)["total"]
    assert fx + kb * 7500 + ks * 9000 == pytest.approx(ref, rel=1e-9)


def test_cluster_boot_mean():
    x = np.r_[np.ones(50), -np.ones(50)]
    d = np.repeat(np.arange(10), 10)
    m, lo, hi, _ = P.cluster_boot(x, d, B=500)
    assert m == pytest.approx(0.0) and lo < 0 < hi
