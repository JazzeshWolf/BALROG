"""Option bid/ask half-spread model for bhavcopy backtests (stdlib only).

Fitted by research/scripts/p3_fit_spread.py on 40 sessions of post-close NSE/BSE quotes
(2026-09-15 .. 2026-11-12). Post-close books are wider than intraday books, so costs are conservative.

    half_spread(cls, symbol, abs_delta, dte_cal, premium, volume_contracts=None, expiry_rank=1, pct=75)
        -> rupees per unit of underlying (same units as premium). Round trip cost = 2 * half_spread.

Model (see json "model"):
  bucket - empirical quantile of (ask-bid)/mid for the finest cell with n >= min_n among
           group x |delta| x DTE x premium x expiry-rank, falling back to coarser cells.
  param  - per-group location-scale fit: log rel = X.b + exp(X.g) * z_q.
  ens    - geometric mean of bucket and param quantiles.
Full spread is floored at one tick (Rs 0.05) and capped at 2 x premium.
"""
from __future__ import annotations

import bisect
import json
import math
from pathlib import Path

_PATH = Path(__file__).with_name("spread_model.json")
_M = None


def model(path=None) -> dict:
    global _M
    if path is not None:
        return json.loads(Path(path).read_text())
    if _M is None:
        _M = json.loads(_PATH.read_text())
    return _M


def group_of(cls: str, symbol: str, m: dict | None = None) -> tuple[str, str]:
    """-> (group, class) where class is 'index' | 'stock'."""
    m = m or model()
    c = "index" if str(cls).lower().startswith("ind") else "stock"
    s = str(symbol).upper()
    if c == "index":
        return (s if s in m["index_groups"] else "index"), c
    return "STK%d" % m["stock_tier"].get(s, m["default_stock_tier"]), c


def _bucket_q(m, g, c, ad, dte, prem, rank):
    e = m["edges"]
    v = {"g": g, "c": c, "d": bisect.bisect_right(e["delta"], ad), "t": bisect.bisect_right(e["dte"], dte),
         "p": bisect.bisect_right(e["premium"], prem), "r": min(int(rank), 3)}
    for lvl in m["levels"]:
        if "g" in lvl and g == "index":           # unknown index symbol: class-level cells only
            continue
        cell = m["cells"].get(lvl + ":" + "|".join(str(v[ch]) for ch in lvl))
        if cell is not None:
            return cell, lvl
    return None, None


def _param_q(m, g, c, ad, dte, prem, rank, vol, cell=None):
    pm = m["param"].get(g) or m["param"][c]
    if vol is None:  # unknown volume: median contracts traded in the matching bucket cell
        vol = cell[4] if cell is not None else m["median_vol_c"].get(g, m["median_vol_c"].get(c, 0.0))
    lp = math.log(prem)
    x = [1.0, lp, lp * lp, ad, ad * ad, math.log(dte), math.log1p(max(vol, 0.0)),
         1.0 if rank == 2 else 0.0, 1.0 if rank >= 3 else 0.0]
    loc = sum(a * b for a, b in zip(x, pm["b"]))
    sc = math.exp(sum(a * b for a, b in zip(x, pm["g"])))
    return [min(math.exp(loc + sc * z), 2.0) for z in pm["zq"]]


def rel_quantiles(cls, symbol, abs_delta, dte_cal, premium, volume_contracts=None, expiry_rank=1, m=None):
    """Relative spread (ask-bid)/mid at the model percentiles (p50, p75, p90), before the tick floor."""
    m = m or model()
    g, c = group_of(cls, symbol, m)
    ad = min(max(abs(float(abs_delta)), 0.0), 1.0)
    dte = max(float(dte_cal), 1.0)
    prem = max(float(premium), m["tick"])
    rank = max(int(expiry_rank), 1)
    kind = m["model"]
    cell, _ = _bucket_q(m, g, c, ad, dte, prem, rank)
    if kind == "bucket":
        return cell[:3]
    qp = _param_q(m, g, c, ad, dte, prem, rank, volume_contracts, cell)
    if kind == "param":
        return qp
    return [math.sqrt(a * b) for a, b in zip(cell[:3], qp)]


def fallback_level(cls, symbol, abs_delta, dte_cal, premium, expiry_rank=1) -> str:
    """Name of the bucket level used (e.g. 'gdtpr' = finest cell, 'c' = class-wide)."""
    m = model()
    g, c = group_of(cls, symbol, m)
    return _bucket_q(m, g, c, min(abs(float(abs_delta)), 1.0), max(float(dte_cal), 1.0),
                     max(float(premium), m["tick"]), max(int(expiry_rank), 1))[1]


def _interp_pct(q, pcts, pct):
    if pct <= pcts[0]:
        return q[0]
    if pct >= pcts[-1]:
        return q[-1]
    i = bisect.bisect_right(pcts, pct) - 1
    w = (pct - pcts[i]) / (pcts[i + 1] - pcts[i])
    return math.exp((1 - w) * math.log(q[i]) + w * math.log(q[i + 1]))


def half_spread(cls, symbol, abs_delta, dte_cal, premium, volume_contracts=None, expiry_rank=1, pct=75) -> float:
    """Half of the modelled bid/ask spread in rupees (>= one tick / 2, <= premium)."""
    m = model()
    q = rel_quantiles(cls, symbol, abs_delta, dte_cal, premium, volume_contracts, expiry_rank, m)
    rel = _interp_pct(q, m["pcts"], pct)
    prem = max(float(premium), m["tick"])
    full = min(max(rel * prem, m["tick"]), 2.0 * prem)
    return full / 2.0


def round_trip_pct(cls, symbol, abs_delta, dte_cal, premium, **kw) -> float:
    """Buy at ask + sell at bid, same spread both legs: 2 * half / premium * 100."""
    return 200.0 * half_spread(cls, symbol, abs_delta, dte_cal, premium, **kw) / premium
