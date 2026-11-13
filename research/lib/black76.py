"""Vectorised Black-76 pricing, implied vol and greeks (options on a forward F).

Conventions: T in years (calendar days / 365), df = exp(-r T), sigma annualised.
delta/gamma are w.r.t. the forward F; vega per 1 vol point (0.01 sigma);
theta per calendar day (dV/dt, negative for long options).
"""
from __future__ import annotations

import numpy as np
from scipy.special import ndtr

SQ2PI = np.sqrt(2.0 * np.pi)


def _npdf(x):
    return np.exp(-0.5 * x * x) / SQ2PI


def _d1d2(F, K, T, sigma):
    sT = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / sT
    return d1, d1 - sT


def price(F, K, T, sigma, df, is_call):
    F, K, T, sigma, df, is_call = np.broadcast_arrays(*map(np.asarray, (F, K, T, sigma, df, is_call)))
    d1, d2 = _d1d2(F, K, T, sigma)
    c = df * (F * ndtr(d1) - K * ndtr(d2))
    p = df * (K * ndtr(-d2) - F * ndtr(-d1))
    return np.where(is_call.astype(bool), c, p)


def bounds(F, K, df, is_call):
    """(lower, upper) no-arbitrage bounds of the option price."""
    is_call = np.asarray(is_call, bool)
    lo = df * np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    hi = df * np.where(is_call, F, K)
    return lo, hi


def implied_vol(px, F, K, T, df, is_call, lo=1e-4, hi=10.0, n_bisect=60, n_newton=3):
    """Implied vol by vectorised bisection + Newton polish. NaN where px is outside
    (intrinsic, upper bound) or inputs invalid."""
    px, F, K, T, df, is_call = np.broadcast_arrays(*map(np.asarray, (px, F, K, T, df, is_call)))
    px = px.astype(float); F = F.astype(float); K = K.astype(float); T = T.astype(float); df = df.astype(float)
    is_call = is_call.astype(bool)
    out = np.full(px.shape, np.nan)
    lb, ub = bounds(F, K, df, is_call)
    ok = (np.isfinite(px) & np.isfinite(F) & np.isfinite(K) & (F > 0) & (K > 0) & (T > 0)
          & (px > lb + 1e-10) & (px < ub))
    if not ok.any():
        return out
    p, f, k, t, d, c = px[ok], F[ok], K[ok], T[ok], df[ok], is_call[ok]
    a = np.full(p.shape, lo)
    b = np.full(p.shape, hi)
    for _ in range(n_bisect):
        m = 0.5 * (a + b)
        hi_side = price(f, k, t, m, d, c) > p
        b = np.where(hi_side, m, b)
        a = np.where(hi_side, a, m)
    s = 0.5 * (a + b)
    for _ in range(n_newton):
        d1, _ = _d1d2(f, k, t, s)
        v = d * f * _npdf(d1) * np.sqrt(t)
        step = np.where(v > 1e-12, (price(f, k, t, s, d, c) - p) / np.maximum(v, 1e-12), 0.0)
        s = np.clip(s - step, lo, hi)
    s[(s <= lo * 1.0001) | (s >= hi * 0.9999)] = np.nan
    out[ok] = s
    return out


def greeks(F, K, T, sigma, df, is_call, r):
    """Return dict delta, gamma, theta (per day), vega (per vol point)."""
    F, K, T, sigma, df, is_call, r = np.broadcast_arrays(*map(np.asarray, (F, K, T, sigma, df, is_call, r)))
    is_call = is_call.astype(bool)
    d1, d2 = _d1d2(F, K, T, sigma)
    n1 = _npdf(d1)
    delta = np.where(is_call, df * ndtr(d1), -df * ndtr(-d1))
    gamma = df * n1 / (F * sigma * np.sqrt(T))
    vega = df * F * n1 * np.sqrt(T)
    pv = price(F, K, T, sigma, df, is_call)
    theta_yr = r * pv - df * F * n1 * sigma / (2.0 * np.sqrt(T))
    return dict(delta=delta, gamma=gamma, theta=theta_yr / 365.0, vega=vega / 100.0)
