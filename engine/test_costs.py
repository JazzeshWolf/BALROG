"""Hand-computed checks for engine/costs.py. Run: python -m pytest engine/test_costs.py -q"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from costs import SCHEDULE, rate, round_trip_cost  # noqa: E402

A = 1e-4  # absolute tolerance in rupees


def test_every_row_has_date_source_confidence():
    for comp, rows in SCHEDULE.items():
        dates = [r[0] for r in rows]
        assert dates == sorted(dates), comp
        for eff, val, src, conf in rows:
            assert len(eff) == 10 and src and conf in ("high", "medium", "low"), (comp, eff)


def test_rate_lookup_boundaries():
    assert rate("stt_option_sale", "2023-03-31") == 0.0005
    assert rate("stt_option_sale", "2023-04-01") == 0.000625
    assert rate("stt_option_sale", "2024-09-30") == 0.000625
    assert rate("stt_option_sale", "2024-10-01") == 0.001
    assert rate("stt_option_sale", "2026-04-01") == 0.0015
    assert rate("nse_txn", "2024-10-01") == 0.0003503
    assert rate("stamp_buy", "2020-07-01") == 0.00003


def test_nse_current_regime_nov_2026():
    # NIFTY lot 65, buy 100 sell 110. buy turn 6500, sell 7150, both 13650.
    # brokerage 40; txn 13650*0.000355299=4.84983; ipft 13650*1e-9; sebi 13650*1e-6=0.01365
    # stamp 6500*0.00003=0.195; stt 7150*0.0015=10.725; gst 0.18*(40+4.84983+0.00001365+0.01365)
    r = round_trip_cost("2026-11-13", "NSE", 100, 110, 65)
    assert r["brokerage"] == 40
    assert r["exchange_txn"] == pytest.approx(4.8498314, abs=A)
    assert r["stamp_duty"] == pytest.approx(0.195, abs=A)
    assert r["stt_sell"] == pytest.approx(10.725, abs=A)
    assert r["gst"] == pytest.approx(0.18 * 44.86349505, abs=A)
    assert r["total"] == pytest.approx(63.85890, abs=1e-3)


def test_nse_2024_regime_ipft_50():
    # 2024-01-15, lot 50, 100 -> 110. txn 0.05%, IPFT Rs50/cr, STT 0.0625%.
    r = round_trip_cost("2024-01-15", "NSE", 100, 110, 50)
    assert r["exchange_txn"] == pytest.approx(5.25, abs=A)
    assert r["ipft"] == pytest.approx(0.0525, abs=A)
    assert r["stt_sell"] == pytest.approx(3.4375, abs=A)
    assert r["total"] == pytest.approx(57.05684, abs=1e-3)


def test_nse_2022_regime_txn_053():
    # 2022-06-15, lot 50, 200 -> 150. txn 0.053%, STT 0.05%.
    r = round_trip_cost("2022-06-15", "NSE", 200, 150, 50)
    assert r["exchange_txn"] == pytest.approx(9.275, abs=A)
    assert r["stt_sell"] == pytest.approx(3.75, abs=A)
    assert r["stamp_duty"] == pytest.approx(0.30, abs=A)
    assert r["total"] == pytest.approx(62.21517, abs=1e-3)


def test_bse_sensex_expired_worthless():
    # 2025-03-10 SENSEX lot 20, buy 300, expires worthless: one order only.
    r = round_trip_cost("2025-03-10", "BSE", 300, 0, 20)
    assert r["brokerage"] == 20
    assert r["exchange_txn"] == pytest.approx(1.95, abs=A)
    assert r["ipft"] == 0 and r["stt_sell"] == 0
    assert r["total"] == pytest.approx(26.08808, abs=1e-3)


def test_expired_itm_cash_settled_2026():
    # buy 50, ITM at expiry by 80 points, lot 65; exercise STT 0.15% of intrinsic.
    r = round_trip_cost("2026-05-01", "NSE", 50, 0, 65, expired_itm=True, intrinsic=80)
    assert r["stt_exercise"] == pytest.approx(7.8, abs=A)
    assert r["brokerage"] == 40  # buy + exercise (Zerodha charges brokerage on exercise)
    assert r["total"] == pytest.approx(56.46391, abs=1e-3)


def test_pre_sep_2019_exercise_stt_trap():
    # Before 1-Sep-2019 exercise STT was 0.125% of settlement price * qty.
    r = round_trip_cost("2019-07-25", "NSE", 20, 0, 75, expired_itm=True, intrinsic=5,
                        settlement_price=11000)
    assert r["stt_exercise"] == pytest.approx(1031.25, abs=A)
    with pytest.raises(ValueError):
        round_trip_cost("2019-07-25", "NSE", 20, 0, 75, expired_itm=True, intrinsic=5)


def test_physical_settlement_rejected():
    with pytest.raises(ValueError):
        round_trip_cost("2026-11-13", "NSE", 5, 0, 1500, expired_itm=True, intrinsic=3,
                        physical_settlement=True)


def test_upstox_plus_brokerage_param():
    r20 = round_trip_cost("2026-11-13", "NSE", 10, 11, 65)
    r30 = round_trip_cost("2026-11-13", "NSE", 10, 11, 65, brokerage_per_order=30)
    assert r30["total"] - r20["total"] == pytest.approx(20 * 1.18, abs=A)


def test_bad_exchange():
    with pytest.raises(ValueError):
        round_trip_cost("2026-11-13", "MCX", 10, 11, 65)
    assert math.isnan(round_trip_cost("2026-11-13", "NSE", 0, 0, 65)["total_pct_of_buy_premium"])
