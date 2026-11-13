"""p3_build_fo.py -- point-in-time daily derivatives dataset from raw bhavcopy files.

Incremental, re-runnable. Three phases:
  1. stage   : parse every raw file not yet staged (or whose preferred source changed, e.g. a UDiFF
               file appeared for a date staged from legacy) -> data/fo/_stage/<ex>_<kind>/<YYYY>/<date>.parquet
  2. assemble: rebuild only the (exchange, month) partitions whose calendar window
               [month start, month end + 95 d] gained/lost dates since the last run (or all with --full)
               -> data/fo/options/year=YYYY/<ex>_<YYYY-MM>.parquet, data/fo/futures/year=YYYY/...
  3. refs    : data/underlying/daily.parquet, data/ref/{calendar,expiries,lots,universe,participant_oi}.parquet
               and research/out/p3_fo_build.md (always rebuilt; cheap)

  python -I research/scripts/p3_build_fo.py [--workers 4] [--full] [--no-report]
Prints <= 40 lines.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import glob
import json
import os
import sys
import time
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.lib import bhav, fo_derive
from research.lib.rates import repo_rate

warnings.filterwarnings("ignore", category=RuntimeWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
pd.options.mode.chained_assignment = None

RAW = os.path.join(ROOT, "data", "raw", "bhavcopy")
FO = os.path.join(ROOT, "data", "fo")
STAGE = os.path.join(FO, "_stage")
REF = os.path.join(ROOT, "data", "ref")
UND = os.path.join(ROOT, "data", "underlying")
REPORT = os.path.join(ROOT, "research", "out", "p3_fo_build.md")
START = "2019-01-01"
WINDOW_DAYS = 95
KINDS = [("nse", "fo"), ("bse", "fo"), ("nse", "cm"), ("nse", "index"), ("nse", "participant_oi")]


# =============================================================================== phase 1: stage
def discover() -> pd.DataFrame:
    """Preferred raw file per (exchange, kind, date) from the manifest (status 200, file present)."""
    man = pd.read_csv(os.path.join(RAW, "manifest.csv"), on_bad_lines="skip", dtype=str)
    man = man[(man["status"] == "200") & (man["date"] >= START)]
    man = man[[(e, k) in KINDS for e, k in zip(man["exchange"], man["kind"])]]
    man["abspath"] = [os.path.join(ROOT, p) for p in man["path"]]
    man = man[[os.path.exists(p) for p in man["abspath"]]]
    man["fmt"] = [bhav.file_format(p) for p in man["abspath"]]
    man["size"] = [os.path.getsize(p) for p in man["abspath"]]
    man["pref"] = (man["fmt"] == "udiff").astype(int)  # prefer UDiFF where both exist
    man = man.sort_values(["exchange", "kind", "date", "pref"]).drop_duplicates(["exchange", "kind", "date"], keep="last")
    return man[["exchange", "kind", "date", "abspath", "fmt", "size"]].reset_index(drop=True)


def stage_path(ex, kind, date):
    return os.path.join(STAGE, f"{ex}_{kind}", date[:4], f"{date}.parquet")


def stage_one(args):
    ex, kind, date, path = args
    try:
        if kind == "fo":
            df, st = bhav.parse_fo(bhav.read_raw(path), ex, date)
            und = (df[df["und_px"] > 0].groupby(["symbol", "cls"])["und_px"].median().reset_index())
            und["date"] = pd.Timestamp(date)
            os.makedirs(os.path.dirname(stage_path(ex, "und", date)), exist_ok=True)
            und.to_parquet(stage_path(ex, "und", date), index=False)
        elif kind == "cm":
            df, st = bhav.parse_cm(bhav.read_raw(path), date), {}
        elif kind == "index":
            df, st = bhav.parse_index(bhav.read_raw(path), date), {}
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                df, st = bhav.parse_participant_oi(f.read(), date), {}
        out = stage_path(ex, kind, date)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        df.to_parquet(out, index=False)
        return ex, kind, date, len(df), json.dumps(st, default=str), ""
    except Exception as e:  # recorded, retried next run
        return ex, kind, date, -1, "{}", f"{type(e).__name__}: {e}"[:200]


def run_stage(workers: int):
    idx_path = os.path.join(STAGE, "_index.csv")
    os.makedirs(STAGE, exist_ok=True)
    idx = pd.read_csv(idx_path, dtype={"date": str}) if os.path.exists(idx_path) else pd.DataFrame(
        columns=["exchange", "kind", "date", "src", "size", "rows", "stats", "staged_at"])
    man = discover()
    if "version" not in idx:
        idx["version"] = 1
    done = {(r.exchange, r.kind, r.date): (r.src, int(r.size), int(r.version)) for r in idx.itertuples()}
    todo = [r for r in man.itertuples()
            if done.get((r.exchange, r.kind, r.date)) != (os.path.relpath(r.abspath, ROOT).replace("\\", "/"), r.size,
                                                          bhav.PARSER_VERSION)]
    errors, new = [], []
    if todo:
        jobs = [(r.exchange, r.kind, r.date, r.abspath) for r in todo]
        meta = {(r.exchange, r.kind, r.date): r for r in todo}
        with cf.ProcessPoolExecutor(max_workers=workers) as pool:
            for ex, kind, date, n, st, err in pool.map(stage_one, jobs, chunksize=4):
                r = meta[(ex, kind, date)]
                if err:
                    errors.append((ex, kind, date, err))
                    continue
                new.append(dict(exchange=ex, kind=kind, date=date,
                                src=os.path.relpath(r.abspath, ROOT).replace("\\", "/"), size=r.size, rows=n,
                                stats=st, staged_at=dt.datetime.now().isoformat(timespec="seconds"),
                                version=bhav.PARSER_VERSION))
    if new:
        nd = pd.DataFrame(new)
        idx = pd.concat([idx[~idx.set_index(["exchange", "kind", "date"]).index.isin(
            nd.set_index(["exchange", "kind", "date"]).index)], nd], ignore_index=True)
        idx.sort_values(["exchange", "kind", "date"]).to_csv(idx_path, index=False)
    return idx, len(new), errors


# =============================================================================== phase 2: assemble
def load_stage(ex, kind, dates):
    fr = [pd.read_parquet(stage_path(ex, kind, d)) for d in dates if os.path.exists(stage_path(ex, kind, d))]
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def spot_table(idx) -> pd.Series:
    """(date, symbol) -> underlying close: CM/index close first, F&O-file UndrlygPric second."""
    parts = []
    for ex, kind in (("nse", "cm"), ("nse", "index")):
        d = idx.loc[(idx["exchange"] == ex) & (idx["kind"] == kind), "date"].tolist()
        s = load_stage(ex, kind, d)
        if len(s):
            parts.append(s[["date", "symbol", "close"]].assign(pri=0))
    for ex in ("nse", "bse"):
        d = idx.loc[(idx["exchange"] == ex) & (idx["kind"] == "fo"), "date"].tolist()
        s = load_stage(ex, "und", d)
        if len(s):
            parts.append(s.rename(columns={"und_px": "close"})[["date", "symbol", "close"]].assign(pri=1 if ex == "nse" else 2))
    if not parts:
        return pd.Series(dtype=float, index=pd.MultiIndex.from_arrays([[], []], names=["date", "symbol"]))
    s = pd.concat(parts).sort_values("pri").drop_duplicates(["date", "symbol"])
    return s.set_index(["date", "symbol"])["close"]


def assemble_month(ex, month, dates, prev_date, calendar, spot):
    allx = load_stage(ex, "fo", ([prev_date] if prev_date else []) + dates)
    if allx.empty:
        return None
    m0 = pd.Timestamp(dates[0])
    prev = allx[allx["date"] < m0]
    x = allx[allx["date"] >= m0].copy()
    # lot: replace weak estimates by the nearest reliable estimate of the same (symbol, expiry)
    rel = x["lot_src"].isin(["file", "gcd+turnover", "gcd"])
    x = x.sort_values(["symbol", "expiry", "date"])
    good = x["lot_size"].where(rel)
    filled = good.groupby([x["symbol"], x["expiry"]]).transform(lambda s: s.ffill().bfill())
    # then: same symbol-date, reliable lot of the nearest other expiry (lots are per underlying
    # except during a revision); last resort: the turnover-rounded estimate
    near = (x.assign(g=good).dropna(subset=["g"]).sort_values("expiry")
            .drop_duplicates(["symbol", "date"]).set_index(["symbol", "date"])["g"])
    sd = pd.Series(near.reindex(pd.MultiIndex.from_frame(x[["symbol", "date"]])).values, index=x.index)
    new_lot = filled.fillna(sd).fillna(x["lot_size"])
    scale = (x["lot_size"] / new_lot).where(new_lot.ne(x["lot_size"]), 1.0)
    for c in ("oi_contracts", "chg_oi_contracts"):
        x[c] = x[c] * scale.fillna(1.0)
    if ex == "bse":  # bse legacy volume/oi are already contracts; scale does not apply
        for c in ("oi_contracts", "chg_oi_contracts"):
            x.loc[x["src_fmt"] == "legacy_bse", c] = x.loc[x["src_fmt"] == "legacy_bse", c] / scale.fillna(1.0)
    x["lot_size"] = new_lot
    opt = x[x["inst"] == "OPT"].copy()
    fut = x[x["inst"] == "FUT"].copy()
    # ---- corporate-action breaks (NSE: full chains listed; BSE lists active contracts only)
    ca = []
    if ex == "nse":
        po = prev[prev["inst"] == "OPT"] if len(prev) else None
        for d, g in opt.groupby("date", sort=True):
            # only across adjacent trading days (a gap in the staged files would fake breaks)
            if po is not None and len(po) and (d - po["date"].iloc[0]).days <= 6:
                ca.append(fo_derive.detect_ca(po, g))
            po = g
    ca = [c for c in ca if len(c)]
    ca = pd.concat(ca, ignore_index=True) if ca else pd.DataFrame(columns=["date", "symbol", "removed_share", "lot_changed", "fut_ret_raw"])
    if len(ca):  # raw (unadjusted) near-futures return on the break day, for information
        fa = pd.concat([prev[prev["inst"] == "FUT"], fut]) if len(prev) else fut
        fa = fa.sort_values(["symbol", "date", "expiry"]).drop_duplicates(["symbol", "date"])
        fa["fut_ret_raw"] = fa.groupby("symbol")["close"].pct_change()
        ca = ca.drop(columns="fut_ret_raw").merge(fa[["date", "symbol", "fut_ret_raw"]], on=["date", "symbol"], how="left")
    cab = set(zip(ca["date"], ca["symbol"]))
    # ---- common fields
    for f in (opt, fut):
        f["traded"] = f["volume_contracts"].fillna(0) > 0
        f["dte_cal"] = (f["expiry"] - f["date"]).dt.days.astype("int32")
        f["expiry_rank"] = fo_derive.expiry_rank(f)
        f["d_oi_contracts"] = f["chg_oi_contracts"]
        f["ca_break"] = [k in cab for k in zip(f["date"], f["symbol"])]
    opt["dte_trd"] = fo_derive.dte_trading(opt["date"], opt["expiry"], calendar).astype("int32")
    allexp = pd.concat([opt[["date", "exchange", "symbol", "expiry"]], fut[["date", "exchange", "symbol", "expiry"]]]).drop_duplicates()
    allexp["is_monthly"] = fo_derive.monthly_flag(allexp).values
    opt = opt.merge(allexp, on=["date", "exchange", "symbol", "expiry"], how="left")
    opt["is_weekly"] = ~opt["is_monthly"]
    opt["close_masked"] = (opt["exchange"] == "bse") & (opt["expiry"] == opt["date"])
    opt.loc[opt["close_masked"], "close"] = np.nan  # BSE writes spot into expiring options' close
    fw = fo_derive.forwards(opt, fut, spot)
    opt = opt.merge(fw, on=["date", "exchange", "symbol", "expiry"], how="left")
    sp = spot.reindex(pd.MultiIndex.from_frame(opt[["date", "symbol"]])).values
    opt["spot"] = np.where(np.isfinite(sp), sp, opt["und_px"])
    opt = fo_derive.add_iv_greeks(opt)
    ocols = ["date", "exchange", "symbol", "cls", "expiry", "strike", "opt_type", "open", "high", "low", "close",
             "settle", "volume_contracts", "oi_contracts", "d_oi_contracts", "lot_size", "traded", "dte_cal",
             "dte_trd", "expiry_rank", "is_weekly", "is_monthly", "fwd", "fwd_src", "r", "spot", "iv", "delta",
             "gamma", "theta", "vega", "close_masked", "ca_break"]
    fcols = ["date", "exchange", "symbol", "cls", "expiry", "open", "high", "low", "close", "settle",
             "volume_contracts", "oi_contracts", "d_oi_contracts", "lot_size", "expiry_rank", "dte_cal", "ca_break"]
    opt = opt[ocols].sort_values(["date", "symbol", "expiry", "opt_type", "strike"]).reset_index(drop=True)
    fut = fut[fcols].sort_values(["date", "symbol", "expiry"]).reset_index(drop=True)
    for f in (opt, fut):
        for c in ("exchange", "symbol", "cls"):
            f[c] = f[c].astype("category")
    for c in ("iv", "delta", "gamma", "theta", "vega"):
        opt[c] = opt[c].astype("float32")
    opt["opt_type"] = opt["opt_type"].astype("category")
    opt["fwd_src"] = opt["fwd_src"].astype("category")
    # ---- summaries for refs / report
    elig = opt["traded"] & (opt["dte_cal"] > 0) & ~opt["close_masked"]
    summ = (opt.assign(elig=elig, has_iv=opt["iv"].notna(), oi_sh=opt["oi_contracts"] * opt["lot_size"])
            .groupby(["date", "exchange", "symbol", "cls", "expiry"], observed=True)
            .agg(rows=("strike", "size"), traded=("traded", "sum"), elig=("elig", "sum"), iv=("has_iv", "sum"),
                 strikes=("strike", "nunique"), lot_size=("lot_size", "median"), oi=("oi_contracts", "sum"),
                 is_monthly=("is_monthly", "first"), expiry_rank=("expiry_rank", "first"),
                 fwd=("fwd", "first"), fwd_src=("fwd_src", "first"), r=("r", "first"))
            .reset_index())
    fsum = fut.groupby(["date", "exchange", "symbol", "expiry"], observed=True).agg(
        fut_lot=("lot_size", "median"), fut_close=("close", "first")).reset_index()
    atm = fo_derive.atm_iv(opt[(opt["cls"] == "index")].astype({"symbol": str, "exchange": str}))
    return opt, fut, summ, fsum, atm, ca


def write_part(df, kind, ex, month):
    d = os.path.join(FO, kind, f"year={month[:4]}")
    os.makedirs(d, exist_ok=True)
    df.to_parquet(os.path.join(d, f"{ex}_{month}.parquet"), index=False)


def run_assemble(idx, full: bool):
    st_path = os.path.join(STAGE, "_assembled.json")
    state = json.load(open(st_path)) if (os.path.exists(st_path) and not full) else {}
    fo = idx[idx["kind"] == "fo"]
    calendar = np.array(sorted(pd.to_datetime(fo["date"].unique())), dtype="datetime64[D]")
    if len(calendar) == 0:
        return 0
    cal_s = pd.Series(pd.to_datetime(calendar))
    spot = spot_table(idx)
    n = 0
    for ex in ("nse", "bse"):
        dates = sorted(fo.loc[fo["exchange"] == ex, "date"])
        if not dates:
            continue
        months = sorted({d[:7] for d in dates})
        for m in months:
            md = [d for d in dates if d[:7] == m]
            m0 = pd.Timestamp(m + "-01")
            m1 = m0 + pd.offsets.MonthEnd(0) + pd.Timedelta(days=WINDOW_DAYS)
            win = cal_s[(cal_s >= m0) & (cal_s <= m1)].dt.strftime("%Y-%m-%d").tolist()
            # spot inputs: re-assemble when CM / index files for the month arrive later
            n_sp = int(((idx["kind"].isin(["cm", "index"])) & (idx["date"].str[:7] == m)).sum())
            sig = f"{len(win)}|{win[-1] if win else ''}|{len(md)}|{n_sp}"
            key = f"{ex}|{m}"
            if state.get(key) == sig and os.path.exists(os.path.join(STAGE, "_summ", f"{ex}_{m}.parquet")):
                continue
            prev = [d for d in dates if d < md[0]]
            res = assemble_month(ex, m, md, prev[-1] if prev else None, calendar, spot)
            if res is None:
                continue
            opt, fut, summ, fsum, atm, ca = res
            write_part(opt, "options", ex, m)
            write_part(fut, "futures", ex, m)
            for name, df in (("_summ", summ), ("_fsum", fsum), ("_atm", atm), ("_ca", ca)):
                os.makedirs(os.path.join(STAGE, name), exist_ok=True)
                df.to_parquet(os.path.join(STAGE, name, f"{ex}_{m}.parquet"), index=False)
            state[key] = sig
            n += 1
            json.dump(state, open(st_path, "w"), indent=0)
    return n


# =============================================================================== phase 3: refs + report
def read_dir(name):
    fs = sorted(glob.glob(os.path.join(STAGE, name, "*.parquet")))
    fr = [pd.read_parquet(f) for f in fs]
    fr = [f for f in fr if len(f)]
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def build_underlying(idx, summ, fsum, universe_syms):
    parts = []
    for ex, kind, src in (("nse", "cm", "nse_cm"), ("nse", "index", "nse_index")):
        d = idx.loc[(idx["exchange"] == ex) & (idx["kind"] == kind), "date"].tolist()
        s = load_stage(ex, kind, d)
        if len(s):
            s["src"] = src
            parts.append(s)
    for ex in ("nse", "bse"):
        d = idx.loc[(idx["exchange"] == ex) & (idx["kind"] == "fo"), "date"].tolist()
        s = load_stage(ex, "und", d)
        if len(s):
            parts.append(s.rename(columns={"und_px": "close"}).assign(src=f"{ex}_fo_undrlygpric", prev_close=np.nan))
    if len(summ):  # BSE legacy days have no spot field: nearest-expiry forward discounted (proxy)
        b = summ[(summ["exchange"] == "bse") & (summ["cls"] == "index") & summ["fwd"].notna()]
        b = b.sort_values("expiry").drop_duplicates(["date", "symbol"])
        T = (b["expiry"] - b["date"]).dt.days / 365.0
        parts.append(pd.DataFrame({"date": b["date"], "symbol": b["symbol"].astype(str),
                                   "close": b["fwd"] * np.exp(-b["r"] * T), "prev_close": np.nan,
                                   "src": "bse_parity_proxy"}))
    if not parts:
        return pd.DataFrame(), {}
    pri = {"nse_cm": 0, "nse_index": 0, "nse_fo_undrlygpric": 1, "bse_fo_undrlygpric": 2, "bse_parity_proxy": 3}
    u = pd.concat(parts, ignore_index=True)
    u["symbol"] = u["symbol"].astype(str)
    u = u[u["symbol"].isin(universe_syms | {"INDIAVIX"})]
    u["p"] = u["src"].map(pri)
    u = u.sort_values("p").drop_duplicates(["date", "symbol"]).drop(columns="p")
    u = u.sort_values(["symbol", "date"]).reset_index(drop=True)
    u["cls"] = np.where(u["src"].isin(["nse_index", "bse_fo_undrlygpric", "bse_parity_proxy"]) | (u["symbol"] == "INDIAVIX"), "index", "stock")
    idx_syms = set(summ.loc[summ["cls"] == "index", "symbol"].astype(str)) if len(summ) else set()
    u.loc[u["symbol"].isin(idx_syms), "cls"] = "index"
    # prev_close verification on consecutive CM days: is the file's prev_close adjusted on ex-dates?
    cm = u[u["src"] == "nse_cm"].copy()
    cm["close_lag"] = cm.groupby("symbol")["close"].shift()
    cal = sorted(idx.loc[idx["kind"] == "fo", "date"].unique())  # trading calendar
    nxt = {pd.Timestamp(a): pd.Timestamp(b) for a, b in zip(cal[:-1], cal[1:])}
    cm["date_lag"] = cm.groupby("symbol")["date"].shift()
    consec = cm["date_lag"].map(nxt).eq(cm["date"])
    cmc = cm[consec & cm["prev_close"].gt(0) & cm["close_lag"].gt(0)]
    ratio = cmc["prev_close"] / cmc["close_lag"]
    mism = cmc[(ratio - 1).abs() > 0.005]
    verify = dict(consecutive_pairs=int(len(cmc)), mismatches=int(len(mism)),
                  mismatch_examples=[(str(r.date.date()), r.symbol, round(float(r.prev_close / r.close_lag), 4)) for r in mism.head(8).itertuples()])
    # ret: prev_close based where the file gives it, else close-to-close on consecutive observations
    u["close_lag"] = u.groupby("symbol")["close"].shift()
    pc = u["prev_close"].where(u["prev_close"] > 0, u["close_lag"])
    u["prev_close"] = pc
    u["ret"] = u["close"] / pc - 1
    g = (u["close"] / ((1 + u["ret"]) * u["close_lag"])).where(u["close_lag"] > 0, 1.0).fillna(1.0)
    # adj factor: f_t = prod_{u>t} g_u (latest = 1); adjusted prices back-adjusted from ret
    g_next = g.groupby(u["symbol"]).shift(-1).fillna(1.0)
    logf = np.log(g_next.clip(lower=1e-6))[::-1].groupby(u["symbol"][::-1]).cumsum()[::-1]
    u["adj_factor"] = np.exp(logf)
    for c in ("open", "high", "low", "close"):
        u[f"adj_{c}"] = u[c] * u["adj_factor"]
    cols = ["date", "symbol", "cls", "open", "high", "low", "close", "prev_close", "ret", "adj_factor",
            "adj_open", "adj_high", "adj_low", "adj_close", "src"]
    return u[cols], verify


def build_refs(idx):
    os.makedirs(REF, exist_ok=True); os.makedirs(UND, exist_ok=True)
    summ, fsum, atm, ca = read_dir("_summ"), read_dir("_fsum"), read_dir("_atm"), read_dir("_ca")
    out = {}
    # calendar
    cal = idx[idx["kind"].isin(["fo", "cm", "index"])].assign(flag=True)
    cal["col"] = cal["exchange"] + "_" + cal["kind"]
    cal = cal.pivot_table(index="date", columns="col", values="flag", aggfunc="any", fill_value=False).reset_index()
    cal["date"] = pd.to_datetime(cal["date"])
    for c in ("nse_fo", "bse_fo"):
        if c not in cal:
            cal[c] = False
    cal["trading_day"] = cal["nse_fo"] | cal["bse_fo"]
    cal.to_parquet(os.path.join(REF, "calendar.parquet"), index=False)
    out["calendar"] = cal
    if summ.empty:
        return out
    for c in ("exchange", "symbol", "cls"):
        summ[c] = summ[c].astype(str)
    # expiries
    e = summ.groupby(["exchange", "symbol", "expiry"]).agg(
        cls=("cls", "first"), is_monthly=("is_monthly", "max"), first_listed=("date", "min"),
        last_seen=("date", "max")).reset_index()
    e["weekday"] = e["expiry"].dt.day_name().str[:3]
    e["kind"] = np.where(e["is_monthly"], "monthly", "weekly")
    e = e.drop(columns="is_monthly")
    e.to_parquet(os.path.join(REF, "expiries.parquet"), index=False)
    out["expiries"] = e
    # lots: nearest-expiry lot per day, collapsed to change points
    l = summ[summ["lot_size"].notna()].sort_values(["exchange", "symbol", "date", "expiry"]).drop_duplicates(["exchange", "symbol", "date"])
    l = l[["exchange", "symbol", "date", "lot_size"]]
    chg = l["lot_size"].ne(l.groupby(["exchange", "symbol"])["lot_size"].shift())
    lots = l[chg].rename(columns={"date": "date_from"}).reset_index(drop=True)
    lots.to_parquet(os.path.join(REF, "lots.parquet"), index=False)
    out["lots"] = lots
    # universe + rename detection
    sd = summ.groupby(["exchange", "symbol", "date"]).agg(cls=("cls", "first"), oi=("oi", "sum")).reset_index()
    uni = sd.groupby(["exchange", "symbol"]).agg(cls=("cls", "first"), first_date=("date", "min"),
                                                 last_date=("date", "max"), n_days=("date", "size")).reset_index()
    caln = np.array(sorted(cal.loc[cal["trading_day"], "date"]))
    pos = {d: i for i, d in enumerate(caln)}
    oi_at = sd.set_index(["exchange", "symbol", "date"])["oi"]
    edge = set(zip(uni["exchange"], uni["symbol"], uni["last_date"])) | set(zip(uni["exchange"], uni["symbol"], uni["first_date"]))
    se = summ[[k in edge for k in zip(summ["exchange"], summ["symbol"], summ["date"])]]
    chain = {k: g.set_index("expiry")["strikes"] for k, g in se.groupby(["exchange", "symbol", "date"])}
    uni["renamed_to"] = None
    last_cal = caln[-1]
    starts = uni[uni["first_date"] > caln[0]]
    for r in uni[uni["last_date"] < last_cal].itertuples():
        i = pos.get(r.last_date)
        if i is None or i + 1 >= len(caln):
            continue
        if (caln[i + 1] - r.last_date).days > 6:  # gap in staged files: cannot judge
            continue
        cand = starts[(starts["exchange"] == r.exchange) & (starts["cls"] == r.cls) & (starts["first_date"] == caln[i + 1])]
        a = oi_at.get((r.exchange, r.symbol, r.last_date), np.nan)
        ea = chain.get((r.exchange, r.symbol, r.last_date))
        for c in cand.itertuples():
            b = oi_at.get((c.exchange, c.symbol, c.first_date), np.nan)
            eb = chain.get((c.exchange, c.symbol, c.first_date))
            # same live expiries with ~the same number of strikes, and OI carried over
            ea_live = None if ea is None else ea[ea.index > r.last_date]
            same_chain = (ea_live is not None and eb is not None and len(ea_live) > 0
                          and set(ea_live.index) == set(eb.index)
                          and (abs(eb.reindex(ea_live.index) / ea_live - 1) <= 0.1).all())
            if same_chain and a > 0 and b > 0 and abs(b / a - 1) < 0.25:
                uni.loc[r.Index, "renamed_to"] = c.symbol
                break
    uni.to_parquet(os.path.join(REF, "universe.parquet"), index=False)
    out["universe"] = uni
    # participant OI
    d = idx.loc[idx["kind"] == "participant_oi", "date"].tolist()
    poi = load_stage("nse", "participant_oi", d)
    if len(poi):
        poi.to_parquet(os.path.join(REF, "participant_oi.parquet"), index=False)
    out["poi"] = poi
    # underlying
    und, verify = build_underlying(idx, summ, fsum, set(uni["symbol"]))
    if len(und):
        und.to_parquet(os.path.join(UND, "daily.parquet"), index=False)
    out.update(summ=summ, fsum=fsum, atm=atm, ca=ca, und=und, verify=verify)
    return out


def write_report(idx, out, timing):
    summ, atm, ca, und, e = out.get("summ"), out.get("atm"), out.get("ca"), out.get("und"), out.get("expiries")
    if summ is None or summ.empty:
        return
    L = ["# P3 - point-in-time daily F&O dataset (bhavcopy build)",
         f"Generated {dt.datetime.now():%Y-%m-%d %H:%M} by `research/scripts/p3_build_fo.py`; schemas in `data/README.md`. "
         f"Partial build: covers raw files present at run time (download still running).", ""]
    fo = idx[idx["kind"] == "fo"]
    cov = ", ".join(f"{ex} {g['date'].min()}..{g['date'].max()} ({len(g)} d)" for ex, g in fo.groupby("exchange"))
    oth = ", ".join(f"{k} {len(g)} d" for k, g in idx[idx["kind"] != "fo"].groupby("kind"))
    L.append(f"**Staged days**: F&O {cov}; other kinds: {oth}.")
    summ["year"] = summ["date"].dt.year
    y = summ.groupby(["year", "exchange", "cls"]).agg(rows=("rows", "sum"), traded=("traded", "sum"), elig=("elig", "sum"),
                                                      iv=("iv", "sum"), syms=("symbol", "nunique"), days=("date", "nunique")).reset_index()
    L += ["", "| year | exch | class | days | underlyings | option rows | traded share | IV solve rate (traded, dte>0) |", "|---|---|---|---|---|---|---|---|"]
    for r in y.itertuples():
        L.append(f"| {r.year} | {r.exchange} | {r.cls} | {r.days} | {r.syms} | {r.rows:,} | {r.traded / r.rows:.2f} | {r.iv / max(r.elig, 1):.3f} |")
    # NIFTY ATM IV vs VIX
    if atm is not None and len(atm):
        a = atm[atm["symbol"] == "NIFTY"].assign(year=lambda d: pd.to_datetime(d["date"]).dt.year)
        vix = und[und["symbol"] == "INDIAVIX"].assign(year=lambda d: d["date"].dt.year) if und is not None and len(und) else pd.DataFrame(columns=["year", "close"])
        parts = []
        for yr, g in a.groupby("year"):
            v = vix.loc[vix["year"] == yr, "close"]
            parts.append(f"{yr}: {100 * g['atm_iv'].median():.1f} ({len(g)} d; VIX median {v.median():.1f} on {len(v)} d)" if len(v) else
                         f"{yr}: {100 * g['atm_iv'].median():.1f} ({len(g)} d; VIX n/a yet)")
        L += ["", "**NIFTY ATM IV** (expiry closest to 30 cal d, >=7 d; median, vol pts): " + "; ".join(parts)]
    # expiry weekday by year
    if e is not None and len(e):
        L += ["", "**Expiry weekday by year** (weekday: count of expiries; observed in files, holiday shifts included):"]
        e2 = e.assign(year=e["expiry"].dt.year)
        e2 = e2[e2["expiry"] <= summ["date"].max()]  # expired contracts only (long-dated listings excluded)
        for s in ("NIFTY", "BANKNIFTY", "SENSEX"):
            g = e2[e2["symbol"] == s]
            if g.empty:
                L.append(f"- {s}: no data yet"); continue
            txt = []
            for (yr, k), h in g.groupby(["year", "kind"]):
                vc = h["weekday"].value_counts()
                txt.append(f"{yr} {k[0]}: " + "/".join(f"{d}{n}" for d, n in vc.items()))
            L.append(f"- {s}: " + "; ".join(txt))
    nca = 0 if ca is None else len(ca)
    L += ["", f"**ca_break events** (symbol-days, NSE): {nca}" + (
        "; top: " + ", ".join(f"{r.symbol} {pd.Timestamp(r.date).date()} (gone {r.removed_share:.0%}, fut ret {r.fut_ret_raw:+.0%})"
                              for r in ca.sort_values("removed_share", ascending=False).head(6).itertuples()) if nca else "")]
    # drops
    st = idx[(idx["kind"] == "fo") & idx["stats"].notna()]
    drop, dup, units = {}, 0, {}
    for s in st["stats"]:
        j = json.loads(s)
        for k, v in j.get("dropped_inst", {}).items():
            drop[k] = drop.get(k, 0) + v
        dup += j.get("duplicate_rows", 0)
        if "udiff_volume_unit" in j:
            units[j["udiff_volume_unit"]] = units.get(j["udiff_volume_unit"], 0) + 1
    fs = summ.groupby("fwd_src")["rows"].sum()
    L += ["", f"**Dropped**: non-equity-derivative instrument rows {drop or 'none'}; duplicate contract rows {dup}. "
          f"UDiFF volume unit detected per file: {units or 'n/a'}. Option rows by forward source: "
          + ", ".join(f"{k} {v / fs.sum():.1%}" for k, v in fs.items()) + "."]
    v = out.get("verify", {})
    L += ["", f"**CM prev_close check**: {v.get('consecutive_pairs', 0)} consecutive symbol-day pairs, "
          f"{v.get('mismatches', 0)} where prev_close != prior close (>0.5%): {v.get('mismatch_examples', [])}. "
          "Mismatches are exchange adjustments on ex-dates; ret = close/prev_close - 1."]
    L += ["", "**Notes**",
          "- Sources: NSE F&O legacy to 2024-07-05, UDiFF after (UDiFF preferred when both exist). Spot: NSE CM (EQ/BE) and "
          "`ind_close_all` (indices, INDIAVIX); fallback F&O `UndrlygPric` (close only). SENSEX/BANKEX: BSE UDiFF `UndrlygPric` "
          "(close only, no O/H/L - no BSE index file is downloaded); BSE legacy days have no spot field (`Prem/Disc` blank) -> "
          "`bse_parity_proxy` = nearest-expiry forward x e^{-rT}.",
          "- BSE files list only active contracts -> no ca_break detection on BSE; is_monthly can misfire when a month's last expiry is absent from a file.",
          "- Overlap Jan-Jul 2024: the bulk fetch takes one format per date (legacy first), so the legacy-vs-UDiFF agreement test "
          "skips until a same-date pair exists; lot reconstruction is instead validated on UDiFF files (legacy method vs NewBrdLotQty).",
          "- dte_trd beyond the last observed trading day counts weekdays (holidays unknown); a `--full` run after the download fixes all partitions.",
          f"- Timing this run: {timing}. Full-history estimate (~1,950 days x 5 kinds): stage ~10 min, assemble ~25 min (~11 s/NSE month), refs <1 min."]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(L[:60]) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--full", action="store_true", help="re-assemble every month (staging stays incremental)")
    ap.add_argument("--no-report", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    idx, n_new, errors = run_stage(a.workers)
    t1 = time.time()
    n_m = run_assemble(idx, a.full)
    t2 = time.time()
    out = build_refs(idx)
    t3 = time.time()
    timing = f"stage {t1 - t0:.0f}s ({n_new} files), assemble {t2 - t1:.0f}s ({n_m} months), refs {t3 - t2:.0f}s"
    if not a.no_report:
        write_report(idx, out, timing)
    print(f"staged {n_new} new files; errors {len(errors)}; assembled {n_m} months")
    for e in errors[:5]:
        print("  stage error:", e)
    for k, g in idx.groupby(["exchange", "kind"]):
        print(f"  stage {k[0]}/{k[1]}: {len(g)} days {g['date'].min()}..{g['date'].max()}")
    s = out.get("summ")
    if s is not None and len(s):
        print(f"  option rows {int(s['rows'].sum()):,}; traded {s['traded'].sum() / s['rows'].sum():.3f}; "
              f"iv solve {s['iv'].sum() / max(s['elig'].sum(), 1):.3f}; ca_break {len(out.get('ca', []))}")
    print("  timing:", timing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
