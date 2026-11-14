"""Vectorised twin of engine/buy_conviction.py's additive model (fit + calibration use this; the engine is stdlib).

Component model (per class group, see engine/model.json):
  linear  - effect(x) = piecewise-linear interpolation between knot effects, flat beyond the end knots;
  cat     - effect = level effect (unknown level -> 0);
  binary  - effect = effects[1] if x else effects[0].
pred = intercept + scale * sum(available contributions), scale = W_all / W_available (pro-rata redistribution of
missing components; W_j = the component's IS importance weight). Mapping to 0-100: np.interp on the fixed knots.
tests: research/tests/test_p5_engine.py checks this against the scalar engine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def interp_weights(x, knots):
    """-> (lo index, hi index, weight on hi) for linear interpolation; NaN x -> lo = -1."""
    x = np.asarray(x, float)
    k = np.asarray(knots, float)
    xc = np.clip(x, k[0], k[-1])
    hi = np.clip(np.searchsorted(k, xc, side="right"), 1, len(k) - 1)
    lo = hi - 1
    span = k[hi] - k[lo]
    w = np.where(span > 0, (xc - k[lo]) / np.where(span > 0, span, 1.0), 0.0)
    nan = np.isnan(x)
    lo = np.where(nan, -1, lo)
    return lo, hi, w


def effect(comp: dict, x) -> np.ndarray:
    """Effect of one component for an array of raw values (NaN / None -> NaN = missing)."""
    t = comp["type"]
    if t == "linear":
        lo, hi, w = interp_weights(x, comp["knots"])
        e = np.asarray(comp["effects"], float)
        out = (1 - w) * e[np.maximum(lo, 0)] + w * e[hi]
        out[lo < 0] = np.nan
        return out
    if t == "cat":
        lv = {k: i for i, k in enumerate(comp["levels"])}
        e = np.asarray(comp["effects"], float)
        s = pd.Series(x).astype(object)
        idx = s.map(lambda v: lv.get(v, -1) if v is not None and v == v else -2).to_numpy(int)
        out = np.where(idx >= 0, e[np.maximum(idx, 0)], 0.0)
        out[idx == -2] = np.nan
        return out
    if t == "binary":
        v = np.asarray(pd.Series(x).astype(float), float)
        e = comp["effects"]
        return np.where(np.isnan(v), np.nan, np.where(v > 0.5, e[1], e[0]))
    raise ValueError(t)


def predict(gm: dict, X: dict) -> tuple[np.ndarray, dict]:
    """gm: model['groups'][g]; X: {component: array of raw values}. -> (pred net R, {component: contribution})."""
    comps = gm["components"]
    n = len(next(iter(X.values())))
    eff = {c: effect(comps[c], X[c]) if c in X else np.full(n, np.nan) for c in comps}
    W = np.array([comps[c]["weight"] for c in comps])
    avail = np.column_stack([~np.isnan(eff[c]) for c in comps])
    wa = (avail * W).sum(1)
    scale = np.where(wa > 0, W.sum() / np.where(wa > 0, wa, 1.0), np.nan)
    tot = np.zeros(n)
    contrib = {}
    for c in comps:
        e = np.nan_to_num(eff[c]) * scale
        contrib[c] = np.where(np.isnan(eff[c]), np.nan, e)
        tot += e
    return gm["intercept"] + tot, contrib


def to_score(pred, mapping: dict) -> np.ndarray:
    return np.clip(np.interp(np.asarray(pred, float), mapping["R"], mapping["score"]), 0.0, 100.0)


# ----------------------------------------------------------------------------- bhavcopy rows -> components
LOAD_COLS = ["trade_id", "date", "exchange", "symbol", "cls", "grp", "opt_type", "dte_bucket", "delta_target", "delta",
             "gamma", "theta", "premium", "half_spread", "lot", "spot", "req_move_pts", "dte_cal", "volume",
             "net_R_h3", "gross_R_h3", "net_rs_h3", "ca_break_h3", "incomplete_h3", "settled_h3", "nomark_h3"]
FEAT_COLS = ["date", "symbol", "har_vol_h3", "iv_pctile_252", "vrp_ratio_h3", "term_spread", "gap_sigma",
             "close_vs_open"]


def load_rows(bt_dir: str, feat_path: str, sample: str) -> pd.DataFrame:
    """All delta x DTE rows of one sample, h=3 primary exclusions, NSE-only stocks, joined to the features."""
    import glob
    import os
    parts = []
    for f in sorted(glob.glob(os.path.join(bt_dir, "trades", "year=*.parquet"))):
        x = pd.read_parquet(f, columns=LOAD_COLS, filters=[("sample", "==", sample)])
        if x.empty:
            continue
        ok = (x["net_R_h3"].notna() & ~x["ca_break_h3"].astype(bool) & ~x["incomplete_h3"].astype(bool)
              & ~x["settled_h3"].astype(bool) & ~x["nomark_h3"].astype(bool))
        ok &= ~((x["cls"].astype(str) == "stock") & (x["exchange"].astype(str) != "nse"))
        x = x[ok].drop(columns=["ca_break_h3", "incomplete_h3", "settled_h3", "nomark_h3"])
        for c in ("symbol", "cls", "grp", "opt_type", "dte_bucket", "exchange"):
            x[c] = x[c].astype(str)
        parts.append(x)
    df = pd.concat(parts, ignore_index=True)
    fe = pd.read_parquet(feat_path, columns=FEAT_COLS)
    fe["symbol"] = fe["symbol"].astype(str)
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    fe["date"] = pd.to_datetime(fe["date"]).dt.normalize()
    return df.merge(fe, on=["date", "symbol"], how="left")


def components_from_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Raw component values for bhavcopy rows (modelled half-spread stands in for the real bid/ask).
    friction_pct = 100 * (2 hs / premium) + 100 * round-trip statutory+brokerage cost (buy prem+hs, sell prem-hs,
    same day) / (premium * lot); rmr = req_move_pts / (HAR sigma_h3 daily * sqrt(3) * spot)."""
    from research.lib import backtest as bt
    prem = df["premium"].to_numpy(float)
    hs = df["half_spread"].to_numpy(float)
    lot = df["lot"].to_numpy(float)
    c = bt.costs_vec(df["date"].to_numpy(), df["date"].to_numpy(), df["exchange"].to_numpy(), prem + hs,
                     np.maximum(prem - hs, 0.0), lot)["total"]
    out = pd.DataFrame(index=df.index)
    out["friction"] = 100 * 2 * hs / prem + 100 * c / (prem * lot)
    out["delta"] = np.abs(df["delta"].to_numpy(float))
    out["dte"] = df["dte_bucket"].astype(str).to_numpy()
    with np.errstate(invalid="ignore", divide="ignore"):
        out["rmr"] = df["req_move_pts"].to_numpy(float) / (
            df["har_vol_h3"].to_numpy(float) / np.sqrt(252.0) * np.sqrt(3.0) * df["spot"].to_numpy(float))
    out["ivpct"] = df["iv_pctile_252"].to_numpy(float)
    out["vrp"] = df["vrp_ratio_h3"].to_numpy(float)
    ts = df["term_spread"].to_numpy(float)
    out["v3"] = np.where(np.isnan(ts), np.nan, (ts > 0).astype(float))
    return out
