"""Point-in-time daily features (pure functions) for the BALROG swing hypotheses.

Every function only uses rows dated <= t to produce the value at t (rolling windows are
trailing; HAR is fitted on targets fully observed before the month being forecast).
Frames are long panels with columns `date`, `symbol` (+ whatever the function needs),
sorted by (symbol, date) inside each function. Definitions: research/02_hypotheses.md §0;
column glossary: data/README.md (features/daily.parquet).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research.lib import black76

LN2x4 = 4.0 * np.log(2.0)
PK_FLOOR = 1e-7          # floor on daily variance before logs (≈0.03% daily vol)
ANN = 252.0


# ============================================================================ generic helpers
def _sorted(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder smoothing (RMA): seed = mean of the first n finite values, then
    s_t = s_{t-1} + (x_t - s_{t-1}) / n. NaN before the seed; NaN inputs carry the state."""
    x = np.asarray(x, float)
    out = np.full(x.shape, np.nan)
    idx = np.flatnonzero(np.isfinite(x))
    if len(idx) < n:
        return out
    seed_at = idx[n - 1]
    s = x[idx[:n]].mean()
    out[seed_at] = s
    for i in range(seed_at + 1, len(x)):
        if np.isfinite(x[i]):
            s = s + (x[i] - s) / n
        out[i] = s
    return out


def rolling_pctile(x: np.ndarray, window: int = 252, min_obs: int = 126) -> np.ndarray:
    """Percentile (0-100) of x_t within the trailing `window` observations ending at t
    (t included; ties count half). NaN when fewer than `min_obs` finite values."""
    x = np.asarray(x, float)
    n = len(x)
    out = np.full(n, np.nan)
    if n == 0:
        return out
    pad = np.concatenate([np.full(window - 1, np.nan), x])
    W = np.lib.stride_tricks.sliding_window_view(pad, window)       # row t = x[t-window+1..t]
    cur = x[:, None]
    fin = np.isfinite(W)
    cnt = fin.sum(1)
    less = (W < cur).sum(1)
    eq = (W == cur).sum(1)
    ok = np.isfinite(x) & (cnt >= min_obs)
    out[ok] = 100.0 * (less[ok] + 0.5 * eq[ok]) / cnt[ok]
    return out


def group_apply(df: pd.DataFrame, by: str, fn) -> pd.DataFrame:
    """Concatenate fn(sub) over groups of a frame already sorted by `by`."""
    parts = [fn(g) for _, g in df.groupby(by, sort=False, observed=True)]
    return pd.concat(parts) if parts else df.iloc[0:0]


# ============================================================================ price / trend
def adx_atr(h, l, c, n: int = 14):
    """Wilder ATR, +DI, -DI and ADX. Returns dict of arrays."""
    h, l, c = (np.asarray(v, float) for v in (h, l, c))
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    tr[~np.isfinite(h - l)] = np.nan
    up = h - np.r_[np.nan, h[:-1]]
    dn = np.r_[np.nan, l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    bad = ~np.isfinite(up) | ~np.isfinite(dn)
    pdm[bad] = np.nan
    mdm[bad] = np.nan
    trs = tr.copy()
    trs[bad] = np.nan                      # DI uses bars with a previous bar
    atr_di = wilder(trs, n)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi = 100.0 * wilder(pdm, n) / atr_di
        mdi = 100.0 * wilder(mdm, n) / atr_di
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    return dict(atr=wilder(tr, n), plus_di=pdi, minus_di=mdi, adx=wilder(dx, n))


def parkinson(h, l) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        v = np.log(np.asarray(h, float) / np.asarray(l, float)) ** 2 / LN2x4
    v[~np.isfinite(v)] = np.nan
    return v


def price_features(px: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol trailing price features.

    px columns: date, symbol, cls, open, high, low, close (raw, as traded that day) and
    aopen..aclose (split/dividend-adjusted; only ratios between days <= t are used, so any
    back-adjustment base is fine). Level outputs (atr20, donchian, sma20) are expressed in
    the raw price units of day t (adjusted series x close_t / aclose_t)."""
    px = _sorted(px)

    def one(g: pd.DataFrame) -> pd.DataFrame:
        o, h, l, c = (g[k].to_numpy(float) for k in ("aopen", "ahigh", "alow", "aclose"))
        k = g["close"].to_numpy(float) / c          # adjusted -> raw units of day t
        out = pd.DataFrame(index=g.index)
        lc = np.log(c)
        out["ret1"] = np.r_[np.nan, np.exp(np.diff(lc)) - 1.0]
        for n in (5, 20):
            out[f"ret{n}"] = np.exp(lc - pd.Series(lc).shift(n).to_numpy()) - 1.0
        r = pd.Series(np.r_[np.nan, np.diff(lc)])
        out["rv20"] = (r.rolling(20, min_periods=15).std() * np.sqrt(ANN)).to_numpy()
        a14 = adx_atr(h, l, c, 14)
        out["adx14"], out["plus_di"], out["minus_di"] = a14["adx"], a14["plus_di"], a14["minus_di"]
        atr20 = adx_atr(h, l, c, 20)["atr"]
        out["atr20"] = atr20 * k
        out["atr20_pct"] = atr20 / c
        prev_atr = np.r_[np.nan, atr20[:-1]]
        out["day_range_atr20"] = (h - l) / prev_atr          # today's range / ATR20 as of t-1
        hs, ls = pd.Series(h), pd.Series(l)
        for n in (10, 20, 40):                                # channel of days t-n..t-1
            out[f"donch_hi{n}"] = hs.shift(1).rolling(n, min_periods=n).max().to_numpy() * k
            out[f"donch_lo{n}"] = ls.shift(1).rolling(n, min_periods=n).min().to_numpy() * k
            out[f"brk_hi{n}"] = c * k > out[f"donch_hi{n}"].to_numpy()
            out[f"brk_lo{n}"] = c * k < out[f"donch_lo{n}"].to_numpy()
        sma = pd.Series(c).rolling(20, min_periods=20).mean().to_numpy()
        out["sma20"] = sma * k
        out["above_sma20"] = np.where(np.isfinite(sma), c > sma, np.nan)
        out["close_vs_open"] = np.sign(c - o)
        out["gap_ret"] = o / np.r_[np.nan, c[:-1]] - 1.0
        pk = parkinson(h, l)
        out["pk_var"] = pk
        return out

    feats = group_apply(px, "symbol", one)
    return pd.concat([px[["date", "symbol", "cls", "close"]], feats.loc[px.index]], axis=1)


# ============================================================================ HAR-RV
def har_design(pk: pd.DataFrame, horizons=(1, 3, 5)) -> pd.DataFrame:
    """pk: date, symbol, cls, pk_var. Adds log regressors x_d, x_w, x_m (trailing 1/5/22 day
    mean Parkinson variance) and, per horizon h, the log mean variance over t+1..t+h (`y_h`)
    with the date it becomes known (`ydate_h`)."""
    d = _sorted(pk)
    v = d["pk_var"].clip(lower=PK_FLOOR)
    g = v.groupby(d["symbol"], observed=True)
    d["x_d"] = np.log(v)
    d["x_w"] = np.log(g.transform(lambda s: s.rolling(5, min_periods=4).mean()))
    d["x_m"] = np.log(g.transform(lambda s: s.rolling(22, min_periods=16).mean()))
    dg = d.groupby("symbol", observed=True)["date"]
    for h in horizons:
        fwd = g.transform(lambda s: s[::-1].rolling(h, min_periods=h).mean()[::-1].shift(-1))
        d[f"y_{h}"] = np.log(fwd)
        d[f"ydate_{h}"] = dg.shift(-h)
    return d


def har_forecast(d: pd.DataFrame, horizons=(1, 3, 5), min_train: int = 400) -> pd.DataFrame:
    """Log-HAR per class, expanding window, refit at each calendar month: the model used on
    days of month M is fitted on rows whose target was fully observed before the first day of
    M. Forecast mean daily variance = exp(xb + s2/2). Returns date, symbol, har_var_h{h}."""
    d = d.copy()
    d["month"] = d["date"].dt.to_period("M")
    out = d[["date", "symbol"]].copy()
    X_all = np.column_stack([np.ones(len(d)), d["x_d"], d["x_w"], d["x_m"]])
    xok = np.isfinite(X_all).all(1)
    for h in horizons:
        pred = np.full(len(d), np.nan)
        y = d[f"y_{h}"].to_numpy()
        yd = d[f"ydate_{h}"].to_numpy()
        for cls in d["cls"].unique():
            mc = (d["cls"] == cls).to_numpy()
            for m in sorted(d.loc[mc, "month"].unique()):
                start = np.datetime64(m.start_time)
                tr = mc & xok & np.isfinite(y) & (yd < start)
                if tr.sum() < min_train:
                    continue
                beta, *_ = np.linalg.lstsq(X_all[tr], y[tr], rcond=None)
                res = y[tr] - X_all[tr] @ beta
                s2 = res.var()
                te = mc & xok & (d["month"] == m).to_numpy()
                pred[te] = np.exp(X_all[te] @ beta + 0.5 * s2)
        out[f"har_var_h{h}"] = pred
    return out


# ============================================================================ option chain
def gamma_b76(F, K, T, sigma, df):
    """Black-76 gamma w.r.t. the forward (same convention as research.lib.black76.greeks)."""
    sT = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / sT
    return df * np.exp(-0.5 * d1 * d1) / np.sqrt(2 * np.pi) / (F * sT)


def _key(df):
    return [df["date"], df["symbol"]]


def chain_features(ch: pd.DataFrame, spot: pd.Series | None = None, wall_band: float = 0.10,
                   flip_band: float = 0.15, flip_step: float = 0.0025) -> pd.DataFrame:
    """Vol-surface and dealer-positioning features from one or more days of option rows.

    ch columns: date, symbol, expiry, strike, opt_type (CE/PE), traded, iv, gamma, fwd, r, spot,
    oi_contracts, d_oi_contracts, lot_size, dte_cal, dte_trd, ca_break. `spot` (optional) is a
    Series indexed by (date, symbol) overriding ch.spot. Uses only same-day rows -> exact on
    any partition of days. Returns one row per (date, symbol)."""
    ch = ch[ch["dte_cal"] >= 0].copy()
    ch["is_call"] = ch["opt_type"].astype(str).eq("CE")
    if spot is not None:
        s = spot.reindex(pd.MultiIndex.from_arrays([ch["date"], ch["symbol"].astype(str)])).to_numpy()
        ch["S"] = np.where(np.isfinite(s), s, ch["spot"].to_numpy())
    else:
        ch["S"] = ch["spot"]
    T = ch["dte_cal"].to_numpy() / 365.0
    ch["S"] = ch["S"].fillna(ch["fwd"] * np.exp(-ch["r"] * T))
    ch["symbol"] = ch["symbol"].astype(str)
    g0 = ch.groupby(["date", "symbol"], sort=True)

    out = g0.agg(spot=("S", "first"), ca_break=("ca_break", "max")).copy()
    # ---- expiry bookkeeping
    exps = ch[["date", "symbol", "expiry", "dte_cal", "dte_trd"]].drop_duplicates(["date", "symbol", "expiry"])
    exps = exps.sort_values(["date", "symbol", "expiry"])
    first = exps.groupby(["date", "symbol"]).first()
    out["is_expiry_day"] = first["dte_cal"].eq(0)
    nz = exps[exps["dte_cal"] >= 1].groupby(["date", "symbol"])
    near = nz.first()
    out["near_expiry"] = near["expiry"]
    out["near_dte_trd"] = near["dte_trd"]
    out["days_to_next_expiry"] = first["dte_trd"].where(first["dte_cal"] > 0, 0)   # 0 on expiry day
    std = exps[exps["dte_cal"] >= 7].groupby(["date", "symbol"]).first()
    out["std_expiry"] = std["expiry"]
    out["std_dte_cal"] = std["dte_cal"]
    out["std_dte_trd"] = std["dte_trd"]
    nxt = exps.merge(std[["expiry"]].rename(columns={"expiry": "sx"}).reset_index(), on=["date", "symbol"])
    nxt = nxt[nxt["expiry"] > nxt["sx"]].groupby(["date", "symbol"]).first()
    out["next_expiry"] = nxt["expiry"]

    # ---- ATM IV at the forward, per expiry
    tr = ch[ch["traded"] & ch["iv"].notna() & (ch["dte_cal"] >= 1)]
    ks = tr.groupby(["date", "symbol", "expiry", "strike"], sort=True).agg(iv=("iv", "mean"), fwd=("fwd", "first")).reset_index()
    lo = ks[ks["strike"] <= ks["fwd"]].groupby(["date", "symbol", "expiry"]).last()
    hi = ks[ks["strike"] > ks["fwd"]].groupby(["date", "symbol", "expiry"]).first()
    atm = lo[["strike", "iv", "fwd"]].join(hi[["strike", "iv", "fwd"]], how="outer", lsuffix="_lo", rsuffix="_hi")
    F = atm["fwd_lo"].fillna(atm["fwd_hi"])
    w = ((F - atm["strike_lo"]) / (atm["strike_hi"] - atm["strike_lo"])).clip(0, 1)
    both = atm["iv_lo"].notna() & atm["iv_hi"].notna()
    iv = np.where(both, atm["iv_lo"] * (1 - w) + atm["iv_hi"] * w, np.nan)
    one_lo = atm["iv_lo"].notna() & ~both & ((F / atm["strike_lo"] - 1).abs() <= 0.03)
    one_hi = atm["iv_hi"].notna() & ~both & ((atm["strike_hi"] / F - 1).abs() <= 0.03)
    iv = np.where(one_lo, atm["iv_lo"], iv)
    iv = np.where(one_hi, atm["iv_hi"], iv)
    atm_iv = pd.Series(iv, index=atm.index, name="atm").reset_index()
    for name, col in (("atm_iv", "std_expiry"), ("atm_iv_next", "next_expiry")):
        k = out[[col]].reset_index().rename(columns={col: "expiry"})
        out[name] = k.merge(atm_iv, on=["date", "symbol", "expiry"], how="left")["atm"].to_numpy()
    out["term_spread"] = out["atm_iv"] - out["atm_iv_next"]

    # ---- dealer gamma (nearest two expiries with dte >= 1)
    ex2 = exps[exps["dte_cal"] >= 1].copy()
    ex2["rk"] = ex2.groupby(["date", "symbol"]).cumcount() + 1
    g = ch.merge(ex2[ex2["rk"] <= 2][["date", "symbol", "expiry", "rk"]], on=["date", "symbol", "expiry"])
    g = g[(g["oi_contracts"] > 0) & (np.abs(g["strike"] / g["S"] - 1) <= 0.30)].copy()
    g = g.merge(atm_iv.rename(columns={"atm": "atm_e"}), on=["date", "symbol", "expiry"], how="left")
    g["sig"] = g["iv"].where(g["iv"].notna(), g["atm_e"]).astype(float)
    Tg = g["dte_cal"].to_numpy() / 365.0
    dfac = np.exp(-g["r"].to_numpy() * Tg)
    sign = np.where(g["is_call"], 1.0, -1.0)
    q = (sign * g["oi_contracts"] * g["lot_size"]).to_numpy()
    Fg, Kg, Sg, sg = (g[c].to_numpy(float) for c in ("fwd", "strike", "S", "sig"))
    okg = np.isfinite(sg) & (sg > 0) & np.isfinite(Fg) & np.isfinite(q)
    gk = ["date", "symbol"]
    gid = g.groupby(gk, sort=True).ngroup().to_numpy()
    gidx = g.groupby(gk, sort=True).size().index
    grid = np.arange(-flip_band, flip_band + 1e-12, flip_step)
    G = len(grid)
    ng = len(gidx)
    tot = np.zeros((ng, G))
    sel = np.flatnonzero(okg)
    mult = 1.0 + grid[None, :]
    for c0 in range(0, len(sel), 40_000):
        ii = sel[c0:c0 + 40_000]
        val = q[ii, None] * gamma_b76(Fg[ii, None] * mult, Kg[ii, None], Tg[ii, None], sg[ii, None],
                                      dfac[ii, None]) * (Sg[ii, None] * mult) ** 2 * 0.01
        val = np.nan_to_num(val)
        for j in range(G):
            tot[:, j] += np.bincount(gid[ii], weights=val[:, j], minlength=ng)
    j0 = int(np.argmin(np.abs(grid)))
    gex = pd.DataFrame({"net_gex": tot[:, j0]}, index=gidx)
    # gamma flip: zero crossing of GEX(S') nearest to the current spot (linear interp)
    sgn = np.sign(tot)
    cross = (sgn[:, :-1] * sgn[:, 1:]) < 0
    flip = np.full(ng, np.nan)
    if cross.any():
        dist = np.abs(np.arange(G - 1) + 0.5 - j0).astype(float)
        dd = np.where(cross, dist[None, :], np.inf)
        jj = dd.argmin(1)
        has = np.isfinite(dd.min(1))
        a, b = tot[np.arange(ng), jj], tot[np.arange(ng), jj + 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            x = grid[jj] + (grid[jj + 1] - grid[jj]) * a / (a - b)
        flip = np.where(has, x, np.nan)
    gex["flip_x"] = flip
    out = out.join(gex)
    out["net_gex"] = out["net_gex"].fillna(0.0).where(out.index.isin(gidx) | out["near_expiry"].notna())
    out["gamma_flip"] = out["spot"] * (1 + out["flip_x"])
    out = out.drop(columns="flip_x")

    # ---- walls / max OI (nearest expiry with dte >= 1, strikes within +-band of spot)
    n1 = ch.merge(ex2[ex2["rk"] == 1][["date", "symbol", "expiry"]], on=["date", "symbol", "expiry"])
    n1 = n1[np.abs(n1["strike"] / n1["S"] - 1) <= wall_band]
    for side, flag in (("call", True), ("put", False)):
        s_ = n1[n1["is_call"] == flag].sort_values(["date", "symbol", "oi_contracts", "strike"])
        s_ = s_[s_["oi_contracts"] > 0].groupby(["date", "symbol"]).last()
        out[f"{side}_wall"] = s_["strike"]
        out[f"{side}_wall_oi"] = s_["oi_contracts"] * s_["lot_size"]
        prev = s_["oi_contracts"] - s_["d_oi_contracts"]
        out[f"{side}_wall_oi_chg"] = (s_["d_oi_contracts"] / prev).where(prev > 0)
        out[f"{side}_wall_dist_pct"] = 100.0 * (out[f"{side}_wall"] / out["spot"] - 1)
    tk = n1.groupby(["date", "symbol", "strike"])["oi_contracts"].sum().reset_index()
    tk = tk[tk["oi_contracts"] > 0].sort_values(["date", "symbol", "oi_contracts", "strike"]).groupby(["date", "symbol"]).last()
    out["max_oi_strike"] = tk["strike"]
    return out.reset_index()


def wall_breaks(d: pd.DataFrame) -> pd.DataFrame:
    """d: date, symbol, spot (close), call_wall, put_wall, ca_break; sorted or not.
    broke_call_wall_t = close_t > call_wall_t and close_{t-1} <= call_wall_{t-1} (mirror for puts:
    close_t < put_wall_t and close_{t-1} >= put_wall_{t-1}); t-1 = previous observed session of
    the symbol. cross_*_wall_prev uses the t-1 wall level on both days (pure price crossing).
    Both False on a ca_break day."""
    d = d.sort_values(["symbol", "date"]).copy()
    g = d.groupby("symbol", sort=False, observed=True)
    pc = g["spot"].shift(1)
    cab = d["ca_break"].eq(True)
    for side, op in (("call", np.greater), ("put", np.less)):
        pw = g[f"{side}_wall"].shift(1)
        before = pc.notna() & pw.notna() & ~op(pc, pw)
        d[f"broke_{side}_wall"] = op(d["spot"], d[f"{side}_wall"]) & before & ~cab
        d[f"cross_{side}_wall_prev"] = op(d["spot"], pw) & before & ~cab
    return d


# ============================================================================ futures
def futures_features(fut: pd.DataFrame, z_window: int = 60, z_min: int = 40, thr: float = 0.5) -> pd.DataFrame:
    """Near-future structure. Near = nearest expiry with dte_cal >= 1 (skips the contract
    expiring today). dprice = near close / same contract's previous close - 1. dOI = change of
    combined OI over all expiries (rollover-neutral) in % of yesterday's combined OI.
    z = value / std over the previous `z_window` days (t excluded). Label per §0."""
    f = fut.copy()
    f["symbol"] = f["symbol"].astype(str)
    f["px"] = f["close"].where(f["close"] > 0, f["settle"])
    f = f.sort_values(["symbol", "expiry", "date"])
    f["prev_px"] = f.groupby(["symbol", "expiry"])["px"].shift(1)
    f["prev_date"] = f.groupby(["symbol", "expiry"])["date"].shift(1)
    f["turnover"] = f["volume_contracts"] * f["lot_size"] * f["px"]
    tot = f.groupby(["symbol", "date"]).agg(oi_all=("oi_contracts", "sum"), turnover=("turnover", "sum"))
    nz = f[f["dte_cal"] >= 1].sort_values(["symbol", "date", "expiry"]).groupby(["symbol", "date"]).first()
    d = tot.join(nz[["px", "prev_px", "prev_date", "oi_contracts", "d_oi_contracts", "ca_break"]]).reset_index()
    d = d.sort_values(["symbol", "date"]).reset_index(drop=True)
    gs = d.groupby("symbol")
    d["prev_obs"] = gs["date"].shift(1)
    ok_prev = d["prev_date"].eq(d["prev_obs"])                # same contract traded the previous session
    d["fut_dprice"] = (d["px"] / d["prev_px"] - 1).where(ok_prev)
    d["fut_doi"] = (d["oi_all"] / gs["oi_all"].shift(1) - 1)
    d["fut_doi_near"] = d["d_oi_contracts"] / (d["oi_contracts"] - d["d_oi_contracts"]).where(lambda s: s > 0)
    for c in ("fut_dprice", "fut_doi"):
        sd = gs[c].transform(lambda s: s.shift(1).rolling(z_window, min_periods=z_min).std())
        d[c + "_z"] = d[c] / sd
    d["fut_turnover_20"] = gs["turnover"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    d["fut_structure"] = structure_label(d["fut_dprice_z"], d["fut_doi_z"], thr)
    return d[["date", "symbol", "fut_dprice", "fut_doi", "fut_doi_near", "fut_dprice_z", "fut_doi_z",
              "fut_structure", "fut_turnover_20", "ca_break"]].rename(columns={"ca_break": "ca_break_fut"})


def structure_label(zp, zoi, thr: float = 0.5) -> np.ndarray:
    zp, zoi = np.asarray(zp, float), np.asarray(zoi, float)
    big = (np.abs(zp) >= thr) & (np.abs(zoi) >= thr)
    lab = np.full(len(zp), "none", dtype=object)
    lab[big & (zp > 0) & (zoi > 0)] = "long_buildup"
    lab[big & (zp < 0) & (zoi > 0)] = "short_buildup"
    lab[big & (zp > 0) & (zoi < 0)] = "short_covering"
    lab[big & (zp < 0) & (zoi < 0)] = "long_unwinding"
    return lab


# ============================================================================ events
def results_events(ev: pd.DataFrame, trading_days, dedupe_days: int = 20) -> pd.DataFrame:
    """Map announcements to the first session whose close reflects them.
    session pre/during -> event_date itself if it is a trading day, else the next one;
    post or unknown -> first trading day strictly after event_date. Unknown time is flagged.
    Events within `dedupe_days` calendar days of the previous kept event of the symbol are
    dropped (one event per quarter). Returns symbol, react_date, pre_date (last close before
    the results), time_unknown."""
    cal = np.unique(pd.to_datetime(pd.Series(trading_days)).values.astype("datetime64[D]"))
    e = ev[["symbol", "event_date", "session"]].dropna(subset=["event_date"]).copy()
    ed = e["event_date"].values.astype("datetime64[D]")
    same_ok = e["session"].isin(["pre", "during"]).to_numpy()
    i_same = np.searchsorted(cal, ed, side="left")             # first trading day >= event_date
    i_after = np.searchsorted(cal, ed, side="right")           # first trading day > event_date
    i = np.where(same_ok, i_same, i_after)
    e = e[(i < len(cal)) & (i >= 1)].copy()
    i = i[(i < len(cal)) & (i >= 1)]
    e["react_date"] = pd.to_datetime(cal[i]).astype("datetime64[ns]")
    e["pre_date"] = pd.to_datetime(cal[i - 1]).astype("datetime64[ns]")
    e["time_unknown"] = e["session"].isna()
    # cluster events of a symbol within `dedupe_days` of the cluster's first event; keep the
    # earliest row with a known time if any, else the earliest row
    e = e.sort_values(["symbol", "react_date", "time_unknown"]).reset_index(drop=True)
    cid = np.zeros(len(e), int)
    c, prev_sym, start = -1, None, None
    for j, (sym, rd) in enumerate(zip(e["symbol"], e["react_date"])):
        if sym != prev_sym or (rd - start).days > dedupe_days:
            c, prev_sym, start = c + 1, sym, rd
        cid[j] = c
    e["cid"] = cid
    pick = e.sort_values(["cid", "time_unknown", "react_date"]).groupby("cid").head(1).index
    keep = e.index.isin(pick)
    return e[keep][["symbol", "react_date", "pre_date", "time_unknown"]].sort_values(["symbol", "react_date"]).reset_index(drop=True)


def results_panel(rows: pd.DataFrame, evs: pd.DataFrame, trading_days, max_days: int = 15) -> pd.DataFrame:
    """rows: date, symbol. Adds results_today_tradable, results_time_unknown and
    days_to_results (trading days from t to the next pre_date, 0 on that day; NaN if > max_days)."""
    cal = np.unique(pd.to_datetime(pd.Series(trading_days)).values.astype("datetime64[D]"))
    r = rows[["date", "symbol"]].copy()
    r["symbol"] = r["symbol"].astype(str)
    r["date"] = r["date"].astype("datetime64[ns]")
    rt = evs.rename(columns={"react_date": "date"})[["symbol", "date", "time_unknown"]]
    r = r.merge(rt, on=["symbol", "date"], how="left")
    r["results_today_tradable"] = r["time_unknown"].notna()
    r["results_time_unknown"] = r["time_unknown"].eq(True)
    r = r.drop(columns="time_unknown")
    pre = evs[["symbol", "pre_date"]].sort_values("pre_date")
    r = r.sort_values("date")
    r = pd.merge_asof(r, pre.rename(columns={"pre_date": "next_pre"}), left_on="date", right_on="next_pre",
                      by="symbol", direction="forward")
    ci = np.searchsorted(cal, r["date"].values.astype("datetime64[D]"))
    cj = np.searchsorted(cal, r["next_pre"].values.astype("datetime64[D]"))
    dd = (cj - ci).astype(float)
    dd[r["next_pre"].isna().to_numpy()] = np.nan
    dd[dd > max_days] = np.nan
    r["days_to_results"] = dd
    return r.drop(columns="next_pre")


def participant_features(poi: pd.DataFrame, trading_days) -> pd.DataFrame:
    """Market-wide FII / Client index positioning, lagged one session (the file for day d is
    published after the close, so it is first usable at the close of the next trading day)."""
    p = poi[poi["client_type"].isin(["FII", "Client"])].copy()
    rows = []
    for ct, g in p.groupby("client_type"):
        pre = ct.lower()
        d = pd.DataFrame({"date": g["date"].values})
        with np.errstate(invalid="ignore", divide="ignore"):
            d[f"{pre}_idx_fut_long_ratio"] = (g["fut_idx_long"] / (g["fut_idx_long"] + g["fut_idx_short"])).values
            d[f"{pre}_idx_call_net_ratio"] = ((g["opt_idx_call_long"] - g["opt_idx_call_short"]) /
                                              (g["opt_idx_call_long"] + g["opt_idx_call_short"])).values
            d[f"{pre}_idx_put_net_ratio"] = ((g["opt_idx_put_long"] - g["opt_idx_put_short"]) /
                                             (g["opt_idx_put_long"] + g["opt_idx_put_short"])).values
        rows.append(d.set_index("date"))
    w = pd.concat(rows, axis=1).sort_index()
    cal = pd.DatetimeIndex(np.unique(pd.to_datetime(pd.Series(trading_days))))
    # value usable at trading day t = latest file dated strictly before t
    w = w[~w.index.duplicated()]
    pos = np.searchsorted(w.index.values, cal.values, side="left") - 1
    vals = w.to_numpy()
    out = np.full((len(cal), vals.shape[1]), np.nan)
    ok = pos >= 0
    out[ok] = vals[pos[ok]]
    res = pd.DataFrame(out, columns=w.columns)
    res.insert(0, "date", cal)
    return res


# ============================================================================ cross-section / market
def beta_rs(df: pd.DataFrame, mkt: pd.DataFrame, window: int = 60) -> pd.DataFrame:
    """df: date, symbol, ret1, ret20 ; mkt: date, m_ret1, m_ret20 (NIFTY). Rolling beta over
    the trailing `window` sessions of the symbol (t included), rs20 = ret20 - beta*NIFTY ret20,
    rs20_decile = cross-sectional decile (1..10) among stocks that day."""
    d = _sorted(df.merge(mkt, on="date", how="left"))
    gs = d.groupby("symbol", observed=True)
    xy = d["ret1"] * d["m_ret1"]
    mx = gs["ret1"].transform(lambda s: s.rolling(window, min_periods=40).mean())
    my = gs["m_ret1"].transform(lambda s: s.rolling(window, min_periods=40).mean())
    mxy = xy.groupby(d["symbol"], observed=True).transform(lambda s: s.rolling(window, min_periods=40).mean())
    myy = (d["m_ret1"] ** 2).groupby(d["symbol"], observed=True).transform(lambda s: s.rolling(window, min_periods=40).mean())
    d["beta60"] = (mxy - mx * my) / (myy - my * my)
    d["rs20"] = d["ret20"] - d["beta60"] * d["m_ret20"]
    return d[["date", "symbol", "beta60", "rs20"]]


def cs_decile(values: pd.Series, dates: pd.Series) -> pd.Series:
    r = values.groupby(dates).rank(pct=True)
    return np.ceil(r * 10).clip(1, 10)
