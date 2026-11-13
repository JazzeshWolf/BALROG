"""Tests for engine/spread.py (run: python -m pytest engine/test_spread.py -q).

Regression values pin the model fitted by research/scripts/p3_fit_spread.py on 40 sessions
(2026-09-15..2026-11-12); refitting changes them on purpose - update REGRESSION then.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spread as S  # noqa: E402

TIER_SYM = {k: next(s for s, v in sorted(S.model()["stock_tier"].items()) if v == k) for k in (1, 2, 3)}


def rel(*a, **kw):
    return 2 * S.half_spread(*a, **kw) / a[4]


# ---------------------------------------------------------------- loading
def test_loads_without_pandas():
    code = ("import sys; sys.modules['pandas']=None; sys.modules['numpy']=None; "
            "sys.path.insert(0, r'%s'); import spread; print(spread.half_spread('index','NIFTY',0.4,5,100))"
            % Path(__file__).resolve().parent)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert float(out.stdout) > 0


def test_json_structure():
    m = json.loads((Path(__file__).with_name("spread_model.json")).read_text())
    assert m["model"] in ("bucket", "param", "ens")
    assert m["pcts"] == [50, 75, 90]
    assert {"NIFTY", "BANKNIFTY", "SENSEX", "STK1", "STK2", "STK3", "index", "stock"} <= set(m["param"])
    assert all(c[3] >= m["min_n"] for c in m["cells"].values())


# ---------------------------------------------------------------- monotonic sanity
@pytest.mark.parametrize("vol", [None, 1, 1000])
@pytest.mark.parametrize("sym", ["NIFTY", "BANKNIFTY", "SENSEX"])
def test_index_cheaper_premium_wider(sym, vol):
    prems = [50, 20, 10, 5, 2, 1]
    r = [rel("index", sym, 0.15, 5, p, vol) for p in prems]
    assert all(b >= a * 0.95 for a, b in zip(r, r[1:])), r
    assert r[-1] > r[0]


@pytest.mark.parametrize("vol", [None, 1, 1000])
@pytest.mark.parametrize("tier", [1, 2, 3])
def test_stock_cheaper_premium_wider(tier, vol):
    # Weak monotonicity (5% tolerance): at fixed |delta| the empirical stock spread is nearly flat for
    # Rs 2-20. With volume unknown the cell-median volume steps between premium buckets (up to ~10%
    # wiggle above Rs 10), so that case starts at Rs 10.
    prems = [20, 10, 5, 2, 1, 0.5] if vol is not None else [10, 5, 2, 1, 0.5]
    r = [rel("stock", TIER_SYM[tier], 0.15, 15, p, vol) for p in prems]
    assert all(b >= a * 0.95 for a, b in zip(r, r[1:])), r
    assert r[-1] > r[0]


@pytest.mark.parametrize("cls,sym,trades", [
    ("index", "NIFTY", [(100, 0.39, 5), (20, 0.10, 5), (5, 0.03, 5)]),
    ("index", "BANKNIFTY", [(100, 0.10, 17), (20, 0.02, 14), (5, 0.01, 7)]),
    ("index", "SENSEX", [(100, 0.15, 3), (20, 0.03, 3), (5, 0.01, 3)]),
    ("stock", TIER_SYM[1], [(20, 0.27, 18), (5, 0.12, 15), (2, 0.06, 14)]),
    ("stock", TIER_SYM[3], [(20, 0.33, 18), (5, 0.15, 17), (2, 0.08, 14)]),
])
def test_typical_trades_cheaper_wider(cls, sym, trades):
    r = [rel(cls, sym, d, t, p) for p, d, t in trades]
    assert r[0] < r[1] < r[2], r


@pytest.mark.parametrize("vol", [None, 1, 1000])
@pytest.mark.parametrize("tier", [1, 2, 3])
@pytest.mark.parametrize("prem", [50, 20, 5, 2])
def test_stock_nearest_expiry_tighter(tier, prem, vol):
    near = S.half_spread("stock", TIER_SYM[tier], 0.25, 12, prem, vol, expiry_rank=1)
    far = S.half_spread("stock", TIER_SYM[tier], 0.25, 40, prem, vol, expiry_rank=2)
    assert near < far


def test_percentiles_ordered():
    for args in [("index", "NIFTY", 0.3, 5, 60), ("stock", TIER_SYM[2], 0.2, 20, 4)]:
        h = [S.half_spread(*args, pct=p) for p in (50, 60, 75, 90)]
        assert h == sorted(h)


def test_less_liquid_tier_wider():
    r = [rel("stock", TIER_SYM[k], 0.3, 15, 10) for k in (1, 2, 3)]
    assert r[0] < r[1] < r[2], r


# ---------------------------------------------------------------- floor / cap
@pytest.mark.parametrize("prem", [0.05, 0.10, 0.20])
def test_floor_one_tick(prem):
    for p in (50, 75, 90):
        h = S.half_spread("index", "NIFTY", 0.5, 5, 500, 10_000, pct=p)
        assert h >= 0.025
        assert S.half_spread("stock", TIER_SYM[1], 0.01, 3, prem, 0, pct=p) >= 0.025 - 1e-12


def test_floor_binds_on_tight_quotes():
    # tiny premium, deep liquidity: model rel*prem < 0.05 -> exactly half a tick
    assert S.half_spread("index", "NIFTY", 0.5, 2, 0.05, 1e6, pct=50) == pytest.approx(0.025)


def test_cap_at_premium():
    assert S.half_spread("stock", TIER_SYM[3], 0.01, 60, 0.05, 0, expiry_rank=2, pct=90) <= 0.05 + 1e-12


# ---------------------------------------------------------------- fallback
def test_fallback_levels():
    assert S.fallback_level("index", "NIFTY", 0.4, 5, 100) == "gdtpr"
    lvl = S.fallback_level("index", "SENSEX", 0.97, 60, 0.5, expiry_rank=3)
    assert lvl != "gdtpr" and lvl in S.model()["levels"]


def test_unknown_symbols():
    assert S.group_of("stock", "NOT_A_SYMBOL") == ("STK2", "stock")
    assert S.group_of("index", "FINNIFTY") == ("index", "index")
    assert S.fallback_level("index", "FINNIFTY", 0.3, 5, 50) in ("cp", "c")
    h = S.half_spread("index", "FINNIFTY", 0.3, 5, 50)
    assert 0.025 <= h <= 50
    assert S.half_spread("stock", "NOT_A_SYMBOL", 0.3, 15, 20, 500) == pytest.approx(
        S.half_spread("stock", TIER_SYM[2], 0.3, 15, 20, 500))


def test_degenerate_inputs():
    assert S.half_spread("index", "NIFTY", -0.4, 0, 100) == S.half_spread("index", "NIFTY", 0.4, 1, 100)
    assert S.half_spread("stock", TIER_SYM[1], 1.5, 10, 0) >= 0.025


# ---------------------------------------------------------------- regression (current fit)
REGRESSION = [
    (("index", "NIFTY", 0.4, 5, 100, None, 1), [0.233179, 0.323304, 0.46378]),
    (("index", "BANKNIFTY", 0.25, 10, 150, None, 1), [1.270195, 1.848771, 2.679335]),
    (("stock", "RELIANCE", 0.3, 15, 20, 500, 1), [0.27632, 0.430653, 0.68074]),
    (("stock", "RELIANCE", 0.3, 40, 20, 500, 2), [0.325817, 0.729384, 1.876071]),
]


@pytest.mark.parametrize("args,exp", REGRESSION)
def test_known_cells(args, exp):
    got = [S.half_spread(*args[:6], expiry_rank=args[6], pct=p) for p in (50, 75, 90)]
    assert got == pytest.approx(exp, rel=1e-5)
