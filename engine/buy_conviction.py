"""BALROG buyConviction - a LOSS-AVOIDANCE ranking for buying NSE/BSE options. NOT an edge claim.

No entry hypothesis passed research/02_hypotheses.md §0, so per §8 this engine only ranks contracts by how little
structural bleed (friction, far-OTM lottery premium, wrong DTE, large required move, expensive vol) a buyer is
expected to suffer. Every expected_net_R the model has ever produced on real data is NEGATIVE on average.

    python -m engine.buy_conviction <snapshot.json[.gz]> [--top 10] [--cls index|stocks] [--json out.json]

Input: one Xerxes snapshot as-is. For every expiry x strike x side with a two-sided quote: mid, IV re-derived from
the mid (stored iv/ltp are never used), Black-76 delta/gamma/theta, friction from the real bid/ask, required-move
ratio with the snapshot's own sigmaForecast, IV percentile from ivHistory (>= 20 clean points, else null and the
weight redistributes pro-rata), VRP from the expiry metrics, term structure from `term`.
Model: engine/model.json (fit on IS trades only by research/scripts/p5_fit_engine.py). Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import math
import os
import sys
from pathlib import Path

try:
    from . import costs as _costs
except ImportError:  # run as a plain script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import costs as _costs  # type: ignore

MODEL_PATH = Path(__file__).with_name("model.json")
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "SENSEX", "BANKEX", "SENSEX50"}
BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}
EDGE_CLAIM = "none — loss-avoidance ranking"
CALIBRATION_STATUS = ("Not monotone at 10-point resolution out-of-sample; no bucket has positive expectancy. "
                      "Three-tier grouping is monotone for index and stocks OOS but was chosen post-hoc — "
                      "forward-test before relying on it.")


def tier_of(score):
    """avoid (< 40), neutral (40-60), least-bad (>= 60); None when not scoreable / untested."""
    if score is None:
        return None
    return "avoid" if score < 40 else ("neutral" if score < 60 else "least-bad")
HOLD_DAYS = 3
_M = None


def load_model(path=None) -> dict:
    global _M
    if path is not None:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    if _M is None:
        _M = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    return _M


# ----------------------------------------------------------------------------- Black-76 (pure)
def _ncdf(x):
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def b76_price(F, K, T, sigma, df, is_call):
    sT = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / sT
    d2 = d1 - sT
    if is_call:
        return df * (F * _ncdf(d1) - K * _ncdf(d2))
    return df * (K * _ncdf(-d2) - F * _ncdf(-d1))


def b76_iv(px, F, K, T, df, is_call, lo=1e-4, hi=5.0):
    """Implied vol by bisection; None outside (intrinsic, upper bound)."""
    if not (px and F > 0 and K > 0 and T > 0):
        return None
    intr = df * max((F - K) if is_call else (K - F), 0.0)
    ub = df * (F if is_call else K)
    if not (intr + 1e-10 < px < ub):
        return None
    a, b = lo, hi
    if b76_price(F, K, T, b, df, is_call) < px:
        return None
    for _ in range(80):
        m = 0.5 * (a + b)
        if b76_price(F, K, T, m, df, is_call) > px:
            b = m
        else:
            a = m
    s = 0.5 * (a + b)
    return None if s <= lo * 1.001 or s >= hi * 0.999 else s


def b76_greeks(F, K, T, sigma, df, is_call, r):
    sT = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / sT
    n1 = _npdf(d1)
    delta = df * _ncdf(d1) if is_call else -df * _ncdf(-d1)
    gamma = df * n1 / (F * sT)
    pv = b76_price(F, K, T, sigma, df, is_call)
    theta = (r * pv - df * F * n1 * sigma / (2.0 * math.sqrt(T))) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta}


# ----------------------------------------------------------------------------- additive model (twin of research/lib/p5_engine_vec.py)
def _interp(x, xs, ys):
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            w = (x - xs[i - 1]) / (xs[i] - xs[i - 1]) if xs[i] > xs[i - 1] else 0.0
            return (1 - w) * ys[i - 1] + w * ys[i]
    return ys[-1]


def component_effect(comp: dict, x):
    """Effect in R of one raw value; None = missing."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    t = comp["type"]
    if t == "linear":
        return _interp(float(x), comp["knots"], comp["effects"])
    if t == "cat":
        return comp["effects"][comp["levels"].index(x)] if x in comp["levels"] else 0.0
    if t == "binary":
        return comp["effects"][1] if float(x) > 0.5 else comp["effects"][0]
    raise ValueError(t)


def predict(gm: dict, values: dict) -> tuple[float, dict, list]:
    """-> (expected net R, {component: contribution or None}, [missing components]). Missing components drop out
    and the remaining contributions are scaled by W_all / W_available (pro-rata redistribution)."""
    comps = gm["components"]
    eff = {c: component_effect(comps[c], values.get(c)) for c in comps}
    w_all = sum(comps[c]["weight"] for c in comps)
    w_av = sum(comps[c]["weight"] for c in comps if eff[c] is not None)
    scale = w_all / w_av if w_av > 0 else 0.0
    contrib = {c: (None if eff[c] is None else eff[c] * scale) for c in comps}
    pred = gm["intercept"] + sum(v for v in contrib.values() if v is not None)
    return pred, contrib, [c for c in comps if eff[c] is None]


def to_score(pred: float, mapping: dict) -> float:
    return max(0.0, min(100.0, _interp(pred, mapping["R"], mapping["score"])))


def dte_level(dte_cal: int, levels):
    for lv in levels:
        if lv.endswith("+"):
            if dte_cal >= int(lv[:-1]):
                return lv
        else:
            lo, hi = (int(v) for v in lv.split("-"))
            if lo <= dte_cal <= hi:
                return lv
    return None


# ----------------------------------------------------------------------------- snapshot helpers
def read_snapshot(path) -> dict:
    p = str(path)
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def instrument_of(snap: dict) -> tuple[str, str]:
    sym = str(snap.get("index") or snap.get("symbol") or "").upper()
    return sym, ("index" if sym in INDEX_SYMBOLS else "stocks")


def _date(s) -> _dt.date:
    return _dt.date.fromisoformat(str(s)[:10])


def as_of_date(snap: dict) -> _dt.date:
    """IST date of asOf (UTC timestamps are shifted +05:30)."""
    s = str(snap.get("asOf") or "")
    try:
        t = _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if t.tzinfo is not None:
            t = t.astimezone(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
        return t.date()
    except ValueError:
        return _date(s)


def add_weekdays(d: _dt.date, k: int) -> _dt.date:
    """k-th weekday after d (exchange holidays are not known to the snapshot)."""
    while k > 0:
        d += _dt.timedelta(days=1)
        if d.weekday() < 5:
            k -= 1
    return d


def prev_weekday(d: _dt.date) -> _dt.date:
    d -= _dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= _dt.timedelta(days=1)
    return d


def clean_iv_history(hist, n_last=252):
    """Positive values within [median/3, 3 x median] of the last n_last points (ivHistory carries spikes from
    near-expiry default expiries, e.g. 5.0 / 6.9)."""
    v = [float(p["v"]) for p in (hist or []) if isinstance(p, dict) and p.get("v") is not None and float(p["v"]) > 0]
    v = v[-n_last:]
    if not v:
        return []
    med = sorted(v)[len(v) // 2]
    return [x for x in v if med / 3.0 <= x <= med * 3.0]


def iv_percentile(snap: dict, min_points: int):
    h = clean_iv_history(snap.get("ivHistory"))
    if len(h) < min_points:
        return None, len(h)
    cur = h[-1]
    return 100.0 * sum(1 for x in h if x <= cur) / len(h), len(h)


def _two_sided(row):
    b, a = row.get("bid"), row.get("ask")
    return b is not None and a is not None and b > 0 and a >= b


def forward_for(exp: dict, snap: dict, r: float):
    """same-expiry future -> put-call parity at the strike nearest spot with both sides two-sided -> spot e^{rT}."""
    T = float(exp.get("tYears") or max(exp.get("dte", 0), 0) / 365.0)
    spot = float(snap["spot"]["price"])
    fut = snap.get("future") or {}
    if fut.get("expiry") and str(fut["expiry"])[:10] == str(exp.get("date"))[:10] and fut.get("price"):
        return float(fut["price"]), "fut"
    by = {}
    for row in exp.get("chain") or []:
        if _two_sided(row):
            by.setdefault(row["strike"], {})[row["type"]] = (row["bid"] + row["ask"]) / 2
    pairs = [(abs(k - spot), k, v) for k, v in by.items() if "CE" in v and "PE" in v]
    if pairs:
        _, k, v = min(pairs)
        return k + (v["CE"] - v["PE"]) * math.exp(r * T), "parity"
    return spot * math.exp(r * T), "spot"


# ----------------------------------------------------------------------------- scoring
def _watch_gap_hold(snap, side, sigma_d):
    """M4 (index): open gap > 1 sigma_daily in the side's direction that held into the close. Needs the day open,
    which the Xerxes snapshot does not carry (spot.history is close-only) -> None unless spot.open is present."""
    sp = snap.get("spot") or {}
    o, pc, c = sp.get("open"), sp.get("prevClose"), sp.get("price")
    if o is None or not pc or not c or not sigma_d:
        return None
    gap = (o / pc - 1.0) / sigma_d
    up = gap > 1.0 and c > o
    dn = gap < -1.0 and c < o
    return bool(up if side == "CE" else dn)


def score_contract(row, exp, snap, ctx, model) -> dict:
    """One chain row -> scored record (buyConviction None when gated or untested)."""
    g = model["gates"]
    gm = model["groups"][ctx["cls"]]
    side, K = row["type"], float(row["strike"])
    is_call = side == "CE"
    dte = int(exp.get("dte") or 0)
    lot = ctx["lot"]
    rec = {"symbol": ctx["symbol"], "expiry": str(exp.get("date"))[:10], "dte": dte, "strike": K, "side": side,
           "bid": row.get("bid"), "ask": row.get("ask"), "buyConviction": None, "tier": None, "expected_net_R": None,
           "regime": "tested", "gates": [], "components": {}, "missing": [], "watch": {}}
    if not _two_sided(row):
        rec["gates"].append("no_two_sided_quote")
        rec["regime"] = "not_scoreable"
        return rec
    bid, ask = float(row["bid"]), float(row["ask"])
    mid = (bid + ask) / 2.0
    rec["mid"] = round(mid, 4)
    r = model["r"]
    T = float(exp.get("tYears") or dte / 365.0)
    F, fsrc = ctx["fwd"][rec["expiry"]]
    df = math.exp(-r * T)
    iv = b76_iv(mid, F, K, T, df, is_call)
    if iv is None:
        rec["gates"].append("no_iv_from_mid")
        rec["regime"] = "not_scoreable"
        return rec
    gk = b76_greeks(F, K, T, iv, df, is_call, r)
    ad = abs(gk["delta"])
    rec.update({"iv_mid": round(iv, 4), "delta": round(gk["delta"], 4), "fwd_src": fsrc})
    vol_c = (row.get("volume") or 0) / lot if lot else 0.0
    ex = "BSE" if ctx["symbol"] in BSE_SYMBOLS else "NSE"
    c = _costs.round_trip_cost(ctx["asof"], ex, ask, bid, int(lot))["total"]
    spread_pct = 100.0 * (ask - bid) / mid
    cost_pct = 100.0 * c / (mid * lot)
    fr = spread_pct + cost_pct
    rec["friction_pct"] = round(fr, 3)
    # untested regimes: no score (null), not a low score
    if dte < g["dte_min"]:
        rec["gates"].append("dte<%d untested" % g["dte_min"])
    if not (g["delta_range"][0] <= ad <= g["delta_range"][1]):
        rec["gates"].append("|delta| outside tested range")
    if mid < g["min_premium"]:
        rec["gates"].append("premium below min tested")
    if vol_c < g["vol_floor_contracts"][ctx["cls"]]:
        rec["gates"].append("volume below tested floor")
    if rec["gates"]:
        rec["regime"] = "untested"
    if fr > g["friction_max_pct"]:
        rec["gates"].append("friction>%g%%" % g["friction_max_pct"])
        rec["regime"] = "not_scoreable"
    m = exp.get("metrics") or {}
    sig = m.get("sigmaForecast") or m.get("rv20")
    sigma_d = float(sig) / math.sqrt(252.0) if sig else None
    cal_days = (add_weekdays(ctx["asof"], HOLD_DAYS) - ctx["asof"]).days
    need = (ask - bid) + c / lot + abs(gk["theta"]) * cal_days
    gam = gk["gamma"]
    x = (-ad + math.sqrt(ad * ad + 2 * gam * need)) / gam if gam > 0 else need / max(ad, 1e-9)
    spot = ctx["spot"]
    rmr = x / (sigma_d * math.sqrt(HOLD_DAYS) * spot) if sigma_d else None
    vrp = m.get("vrp")
    if vrp is None and m.get("atmIv") and sig:
        vrp = float(m["atmIv"]) / float(sig)
    vals = {"friction": fr, "delta": ad, "dte": dte_level(dte, model["dte_levels"]), "rmr": rmr,
            "ivpct": ctx["ivpct"], "vrp": vrp, "v3": ctx["v3"]}
    rec["inputs"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in vals.items() if k in gm["components"]}
    if ctx["cls"] == "index":
        rec["watch"] = {"watch_gap_hold": _watch_gap_hold(snap, side, sigma_d),
                        "watch_low_ivpct": None if ctx["ivpct_raw"] is None else bool(ctx["ivpct_raw"] < 20),
                        "label": "forward-test candidate, not evidence (zero weight)"}
    if rec["regime"] == "tested":
        pred, contrib, missing = predict(gm, vals)
        rec["expected_net_R"] = round(pred, 4)
        rec["buyConviction"] = round(to_score(pred, model["mapping"]), 1)
        rec["tier"] = tier_of(rec["buyConviction"])
        rec["components"] = {k: (None if v is None else round(v, 4)) for k, v in contrib.items()}
        rec["missing"] = missing
    exp_d = _date(rec["expiry"])
    exit_by = min(add_weekdays(ctx["asof"], HOLD_DAYS), prev_weekday(exp_d))
    sl_spot = None
    if sigma_d:
        sl_spot = spot * (1 - sigma_d) if is_call else spot * (1 + sigma_d)
    sl_px = None
    if sl_spot:
        T1 = max(T - 1 / 365.0, 1e-6)
        sl_px = b76_price(F * sl_spot / spot, K, T1, iv, math.exp(-r * T1), is_call)
    rec["trade"] = {
        "strike": K, "expiry": rec["expiry"], "side": side, "holding": "swing 1-3 days (intraday never recommended)",
        "entry": {"order": "limit at mid", "limit": round(mid, 2), "max_ask": round(ask, 2)},
        "exit": {"invalidation_X2": ("underlying close below the entry-day low" if is_call else
                                     "underlying close above the entry-day high"),
                 "time_stop": "%d trading days" % HOLD_DAYS, "exit_by": str(exit_by),
                 "never_into_expiry_day": True,
                 "physical_settlement_warning": ("stock options settle physically: never hold ITM into expiry"
                                                 if ctx["cls"] == "stocks" else None)},
        "tp": None, "tp_note": "none - no TP rule beat a time exit",
        "sl_info": {"premium": None if sl_px is None else round(sl_px, 2),
                    "underlying": None if sl_spot is None else round(sl_spot, 2),
                    "note": "informational only: premium if the underlying moves 1 sigma_daily against next session"},
        "max_premium_at_risk_inr": round(ask * lot + c, 2), "lot": lot}
    return rec


def score_snapshot(snap: dict, model: dict | None = None, cls: str | None = None, top: int = 10,
                   per_side: int = 2) -> dict:
    model = model or load_model()
    sym, c0 = instrument_of(snap)
    cls = cls or c0
    ivp, n_iv = iv_percentile(snap, model["gates"]["ivhist_min_points"])
    term = snap.get("term") or {}
    v3 = None
    if term.get("nearIv") is not None and term.get("farIv") is not None:
        v3 = 1.0 if float(term["nearIv"]) > float(term["farIv"]) else 0.0
    ctx = {"symbol": sym, "cls": cls, "lot": float(snap.get("lotSize") or 0), "spot": float(snap["spot"]["price"]),
           "asof": as_of_date(snap), "ivpct_raw": ivp, "ivpct": ivp if cls == "stocks" else None,
           "v3": v3 if cls == "stocks" else None, "fwd": {}}
    out = []
    for e, exp in (snap.get("expiries") or {}).items():
        ctx["fwd"][str(exp.get("date") or e)[:10]] = forward_for(exp, snap, model["r"])
        for row in exp.get("chain") or []:
            out.append(score_contract(row, exp, snap, ctx, model))
    scored = sorted((x for x in out if x["buyConviction"] is not None),
                    key=lambda x: (-x["buyConviction"], -x["expected_net_R"], x["friction_pct"], x["expiry"], x["strike"]))
    ranked, cnt = [], {}
    for x in scored:
        k = (x["expiry"], x["side"])
        if cnt.get(k, 0) >= per_side:
            continue
        cnt[k] = cnt.get(k, 0) + 1
        ranked.append(x)
        if len(ranked) >= top:
            break
    reg = {}
    for x in out:
        reg[x["regime"]] = reg.get(x["regime"], 0) + 1
    return {"symbol": sym, "cls": cls, "asOf": snap.get("asOf"), "stale": snap.get("stale"),
            "edge_claim": EDGE_CLAIM, "calibration_status": CALIBRATION_STATUS, "model_variant": model.get("variant"),
            "iv_percentile": None if ivp is None else round(ivp, 1), "iv_history_points": n_iv,
            "regime_counts": reg, "n_contracts": len(out), "top": ranked, "contracts": out,
            "ranking_rule": "by buyConviction (ties: lower friction), at most %d strikes per side per expiry" % per_side}


def _table(res) -> str:
    L = ["%s (%s) asOf %s  edge_claim: %s" % (res["symbol"], res["cls"], res["asOf"], res["edge_claim"]),
         "contracts %d regimes %s ivPct %s" % (res["n_contracts"], res["regime_counts"], res["iv_percentile"]),
         "%-10s %3s %9s side %8s %6s %6s %5s %-9s %7s %9s exit_by" % ("expiry", "dte", "strike", "mid", "delta", "fric%",
                                                               "score", "tier", "expR", "risk Rs")]
    for x in res["top"]:
        L.append("%-10s %3d %9.1f %-4s %8.2f %+6.2f %6.2f %5.1f %-9s %+7.3f %9.0f %s" % (
            x["expiry"], x["dte"], x["strike"], x["side"], x["mid"], x["delta"], x["friction_pct"],
            x["buyConviction"], x["tier"], x["expected_net_R"], x["trade"]["max_premium_at_risk_inr"], x["trade"]["exit"]["exit_by"]))
    if not res["top"]:
        L.append("(no scoreable contract: every candidate is gated or untested)")
    L.append("calibration: " + res["calibration_status"])
    L.append("expected_net_R is the model's IS-fitted expectation and is negative: this ranks the least-bad buys.")
    return "\n".join(L)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="BALROG buyConviction (loss-avoidance ranking, no edge claim)")
    ap.add_argument("snapshot")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--cls", choices=["index", "stocks"])
    ap.add_argument("--json", help="write the full result here")
    a = ap.parse_args(argv)
    res = score_snapshot(read_snapshot(a.snapshot), cls=a.cls, top=a.top)
    print(_table(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
