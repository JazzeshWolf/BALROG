"""Tests for engine/buy_conviction.py.  Run: python -m pytest engine/test_buy_conviction.py -q"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest

from engine import buy_conviction as bc

FIX = Path(__file__).with_name("fixtures")
SNAP = json.loads((FIX / "nifty_2026-11-12.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((FIX / "nifty_2026-11-12.expected.json").read_text(encoding="utf-8"))
M = bc.load_model()


def _toy(cls="stocks", dte=25, bid=19.5, ask=20.5, strike=1000.0, side="CE", spot=1000.0, vol=5e5, ivh=None,
         term=(0.30, 0.32)):
    T = dte / 365.0
    snap = {"asOf": "2026-11-12T11:00:00Z", "index": "TOYSTOCK" if cls == "stocks" else "NIFTY", "lotSize": 500,
            "spot": {"price": spot, "prevClose": spot}, "future": {"price": spot * math.exp(0.0525 * T),
                                                                     "expiry": "2026-12-07"},
            "ivHistory": ivh if ivh is not None else [{"t": f"d{i}", "v": 0.25 + 0.001 * i} for i in range(60)],
            "expiries": {"2026-12-07": {"date": "2026-12-07", "dte": dte, "tYears": T,
                                        "metrics": {"atmIv": 0.3, "sigmaForecast": 0.3, "rv20": 0.3, "vrp": 1.0},
                                        "chain": [{"strike": strike, "type": side, "bid": bid, "ask": ask,
                                                   "volume": vol, "oi": 1e6, "ltp": 999.0, "iv": 9.9}]}}}
    if term is not None:
        snap["term"] = {"nearIv": term[0], "farIv": term[1]}
    return snap


def _one(snap, cls=None):
    return bc.score_snapshot(snap, cls=cls)["contracts"][0]


# ----------------------------------------------------------------------------- gates
def test_no_quote_gated():
    r = _one(_toy(bid=None))
    assert r["buyConviction"] is None and "no_two_sided_quote" in r["gates"] and r["regime"] == "not_scoreable"


def test_wide_spread_gated():
    r = _one(_toy(bid=15.0, ask=25.0))
    assert r["buyConviction"] is None and any(g.startswith("friction>") for g in r["gates"])


def test_untested_dte_is_null_not_low():
    r = _one(_toy(dte=5))
    assert r["regime"] == "untested" and r["buyConviction"] is None and r["expected_net_R"] is None


def test_scored_contract_has_structure():
    r = _one(_toy())
    assert r["regime"] == "tested" and 0 <= r["buyConviction"] <= 100 and r["expected_net_R"] < 0.05
    t = r["trade"]
    assert t["tp"] is None and t["exit"]["time_stop"] == "3 trading days" and t["exit"]["physical_settlement_warning"]
    assert t["max_premium_at_risk_inr"] > 20.5 * 500
    assert "intraday never" in t["holding"]


def test_stored_iv_and_ltp_ignored():
    a, b = _toy(), _toy()
    b["expiries"]["2026-12-07"]["chain"][0].update({"iv": 0.01, "ltp": 1.0})
    assert _one(a)["iv_mid"] == _one(b)["iv_mid"]


def test_edge_claim():
    assert bc.score_snapshot(_toy())["edge_claim"] == "none — loss-avoidance ranking"


# ----------------------------------------------------------------------------- monotonicity of components
@pytest.mark.parametrize("grp", ["index", "stocks"])
def test_monotone_components(grp):
    comps = M["groups"][grp]["components"]
    for name, c in comps.items():
        e = c["effects"]
        if c["monotone"] == "dec":
            assert all(b <= a + 1e-12 for a, b in zip(e, e[1:])), name
        if c["monotone"] == "veto":
            assert e[1] <= e[0] + 1e-12


def test_friction_monotone_end_to_end():
    s = [_one(_toy(bid=20 - w, ask=20 + w))["expected_net_R"] for w in (0.1, 0.3, 0.5, 0.7)]
    assert all(b <= a + 1e-12 for a, b in zip(s, s[1:]))


def test_ivpct_monotone_component():
    comp = M["groups"]["stocks"]["components"].get("ivpct")
    if comp is None:
        pytest.skip("ivpct not in model")
    vals = [bc.component_effect(comp, x) for x in (1, 10, 25, 50, 75, 99)]
    assert all(b <= a + 1e-12 for a, b in zip(vals, vals[1:]))


# ----------------------------------------------------------------------------- stability and missing fields
def test_score_stability_one_tick():
    a = _one(_toy(bid=19.5, ask=20.5))["buyConviction"]
    b = _one(_toy(bid=19.45, ask=20.5))["buyConviction"]
    assert abs(a - b) <= 3.0


def test_missing_ivhistory_redistributes():
    r = _one(_toy(ivh=[{"t": "a", "v": 0.3}] * 5))
    gm = M["groups"]["stocks"]
    if "ivpct" not in gm["components"]:
        pytest.skip("ivpct not in model")
    assert r["buyConviction"] is not None and "ivpct" in r["missing"] and r["components"]["ivpct"] is None
    vals = {k: v for k, v in r["inputs"].items()}
    p_full, c_full, _ = bc.predict(gm, {**vals, "ivpct": 50.0})
    avail = [k for k in gm["components"] if k != "ivpct" and vals.get(k) is not None]
    w_all = sum(gm["components"][k]["weight"] for k in gm["components"] if vals.get(k) is not None or k == "ivpct")
    w_av = sum(gm["components"][k]["weight"] for k in avail)
    for k in avail:  # every remaining contribution scaled by W_all / W_available
        raw = bc.component_effect(gm["components"][k], vals[k])
        assert r["components"][k] == pytest.approx(raw * w_all / w_av, abs=1e-3)


def test_missing_term_ok():
    r = _one(_toy(term=None))
    assert r["buyConviction"] is not None


def test_ivhistory_spikes_cleaned():
    h = [{"t": str(i), "v": 0.2} for i in range(30)] + [{"t": "x", "v": 6.9}]
    assert max(bc.clean_iv_history(h)) == 0.2


# ----------------------------------------------------------------------------- regression fixture
def test_regression_fixture_top5():
    res = bc.score_snapshot(copy.deepcopy(SNAP), top=5)
    assert res["model_variant"] == EXPECTED["model_variant"]
    assert res["regime_counts"] == EXPECTED["regime_counts"]
    got = res["top"]
    assert len(got) == len(EXPECTED["top5"])
    for g, e in zip(got, EXPECTED["top5"]):
        for k in ("expiry", "strike", "side", "regime"):
            assert g[k] == e[k]
        for k in ("buyConviction", "expected_net_R", "delta", "friction_pct", "iv_mid"):
            assert g[k] == pytest.approx(e[k], abs=1e-3)


def test_fixture_nearest_expiry_untested():
    res = bc.score_snapshot(copy.deepcopy(SNAP))
    near = [x for x in res["contracts"] if x["dte"] < 7 and x["regime"] != "not_scoreable"]
    assert near and all(x["buyConviction"] is None and x["regime"] == "untested" for x in near)


def test_at_most_two_per_side_per_expiry():
    res = bc.score_snapshot(copy.deepcopy(SNAP), top=50)
    cnt = {}
    for x in res["top"]:
        cnt[(x["expiry"], x["side"])] = cnt.get((x["expiry"], x["side"]), 0) + 1
    assert max(cnt.values()) <= 2


# ----------------------------------------------------------------------------- tiers
def test_tier_boundaries():
    assert bc.tier_of(None) is None
    assert bc.tier_of(0) == "avoid" and bc.tier_of(39.9) == "avoid"
    assert bc.tier_of(40) == "neutral" and bc.tier_of(59.9) == "neutral"
    assert bc.tier_of(60) == "least-bad" and bc.tier_of(100) == "least-bad"


def test_tier_on_contracts_and_status():
    res = bc.score_snapshot(copy.deepcopy(SNAP), top=50)
    assert "post-hoc" in res["calibration_status"]
    for x in res["contracts"]:
        assert x["tier"] == bc.tier_of(x["buyConviction"])
        if x["buyConviction"] is None:
            assert x["tier"] is None
    for g, e in zip(res["top"], EXPECTED["top5"]):
        assert g["tier"] == e["tier"]
    assert _one(_toy(dte=5))["tier"] is None
