"""P1: flatten the xerxes-eod-archive gz snapshots into compact Parquet caches.

Usage:
  python -I research/scripts/p1_build_archive_parquet.py --archive _ext/xerxes-eod-archive --out data/archive

Outputs (overwritten on each run -> idempotent):
  chain.parquet   one row per (trade_date, symbol, class, expiry, strike, type)
  snap.parquet    one row per (trade_date, symbol)
  events.parquet  dated/undated event items (stock `events` + market.json `events`)

The archive is READ-ONLY; file contents are treated as data only.
"""
import argparse
import gzip
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

CHAIN_KEYS = {"strike", "type", "ltp", "iv", "oi", "prevOi", "volume", "bid", "ask", "delta"}
TOP_KEYS = {"asOf", "spot", "vix", "future", "lotSize", "expiries", "ivHistory", "gap", "term",
            "events", "outlook", "verdict", "structure"}
NON_INSTRUMENT = {"index.json.gz", "candidates.json.gz", "market.json.gz"}


def num(v):
    if isinstance(v, bool) or v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    return np.nan


def s(v, n=None):
    if v is None:
        return None
    v = str(v)
    return v[:n] if n else v


def load(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def process_day(args):
    day_dir, trade_date = args
    chain_rows, snap_rows, ev_rows = [], [], []
    schema = {"top_missing": Counter(), "top_extra": Counter(), "chain_missing": Counter(),
              "chain_extra": Counter(), "files": Counter(), "bad_files": []}
    for cls, sub in (("index", "indices"), ("stock", "stocks")):
        d = os.path.join(day_dir, sub)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json.gz"):
                continue
            p = os.path.join(d, fn)
            if fn == "market.json.gz":
                try:
                    m = load(p)
                except Exception as e:  # noqa
                    schema["bad_files"].append(f"{trade_date}/{sub}/{fn}:{type(e).__name__}")
                    continue
                for e in m.get("events") or []:
                    if not isinstance(e, dict):
                        continue
                    ev_rows.append(dict(trade_date=trade_date, symbol="MARKET", cls="market",
                                        event_date=s(e.get("date")), kind=s(e.get("kind")),
                                        title=s(e.get("name") or e.get("title"), 200),
                                        approx=e.get("approx"), source=s(e.get("source")),
                                        done=e.get("done")))
                schema["files"]["market"] += 1
                continue
            if fn in NON_INSTRUMENT:
                schema["files"][fn] += 1
                continue
            try:
                j = load(p)
            except Exception as e:  # noqa
                schema["bad_files"].append(f"{trade_date}/{sub}/{fn}:{type(e).__name__}")
                continue
            schema["files"][cls] += 1
            keys = set(j)
            for k in TOP_KEYS - keys:
                schema["top_missing"][f"{cls}:{k}"] += 1
            for k in keys - TOP_KEYS:
                schema["top_extra"][f"{cls}:{k}"] += 1
            sym = j.get("index") or fn.replace(".json.gz", "")
            asof = j.get("asOf")
            spot = j.get("spot") or {}
            fut = j.get("future") or {}
            vix = j.get("vix")
            vixv = vix.get("value") if isinstance(vix, dict) else vix
            term = j.get("term") or {}
            st = j.get("structure") or {}
            ver = j.get("verdict") or {}
            gap = j.get("gap") or {}
            outl = j.get("outlook") or {}
            hist = spot.get("history") or []
            ivh = j.get("ivHistory") or []
            evs = j.get("events") or []
            exps = j.get("expiries") or {}
            spot_p, fut_p, lot = num(spot.get("price")), num(fut.get("price")), num(j.get("lotSize"))
            snap_rows.append(dict(
                trade_date=trade_date, symbol=sym, cls=cls, file=fn, asOf=asof, stale=j.get("stale"),
                source=s(j.get("source")), name=s(j.get("name")), expiryKind=s(j.get("expiryKind")),
                sector=s(j.get("sector")), defaultExpiry=s(j.get("defaultExpiry")),
                spot_price=spot_p, spot_prevClose=num(spot.get("prevClose")),
                spot_changePct=num(spot.get("changePct")), vix=num(vixv),
                vix_hist_len=len(vix.get("history") or []) if isinstance(vix, dict) else 0,
                fut_price=fut_p, fut_expiry=s(fut.get("expiry")), fut_oi=num(fut.get("oi")),
                fut_basisPts=num(fut.get("basisPts")), lotSize=lot,
                term_slopePts=num(term.get("slopePts")), term_regime=s(term.get("regime")),
                term_nearIv=num(term.get("nearIv")), term_farIv=num(term.get("farIv")),
                structure_label=s(st.get("label")), structure_bias=s(st.get("bias")),
                structure_strength=s(st.get("strength")), structure_priceChgPct=num(st.get("priceChgPct")),
                structure_oiChgPct=num(st.get("oiChgPct")),
                verdict=s(ver.get("verdict")), verdict_score=num(ver.get("score")),
                verdict_confidence=num(ver.get("confidence")), verdict_structure=s(ver.get("structure"), 120),
                gap_gapShare=num(gap.get("gapShare")), gap_maxAbsMove=num(gap.get("maxAbsMove")),
                gap_jumpCount=num(gap.get("jumpCount")),
                beta=num(outl.get("beta")), adv=num(outl.get("adv")),
                vol_today=num((outl.get("volume") or {}).get("today")),
                vol_avg=num((outl.get("volume") or {}).get("avg")),
                n_expiries=len(exps), spot_hist_len=len(hist),
                spot_hist_first=s(hist[0].get("t")) if hist and isinstance(hist[0], dict) else None,
                spot_hist_last=s(hist[-1].get("t")) if hist and isinstance(hist[-1], dict) else None,
                spot_hist_keys=",".join(sorted(hist[0].keys())) if hist and isinstance(hist[0], dict) else None,
                ivhist_len=len(ivh),
                ivhist_first=s(ivh[0].get("t")) if ivh and isinstance(ivh[0], dict) else None,
                n_events=len(evs), n_news=len(j.get("news") or []),
            ))
            for e in evs:
                if not isinstance(e, dict):
                    continue
                ev_rows.append(dict(trade_date=trade_date, symbol=sym, cls=cls,
                                    event_date=s(e.get("date")), kind=s(e.get("kind")),
                                    title=s(e.get("title") or e.get("name"), 200),
                                    approx=e.get("approx"), source=s(e.get("source")), done=e.get("done")))
            for ek, blk in exps.items():
                if not isinstance(blk, dict):
                    continue
                m = blk.get("metrics") or {}
                exp = blk.get("date") or ek
                dte, ty = num(blk.get("dte")), num(blk.get("tYears"))
                lab = s(blk.get("label"))
                atm_iv, rv20, sigf, vrp = num(m.get("atmIv")), num(m.get("rv20")), num(m.get("sigmaForecast")), num(m.get("vrp"))
                for r in blk.get("chain") or []:
                    if not isinstance(r, dict):
                        continue
                    rk = set(r)
                    for k in CHAIN_KEYS - rk:
                        schema["chain_missing"][f"{cls}:{k}"] += 1
                    for k in rk - CHAIN_KEYS:
                        schema["chain_extra"][f"{cls}:{k}"] += 1
                    chain_rows.append((trade_date, sym, cls, exp, lab, dte, ty, num(r.get("strike")), s(r.get("type")),
                                       num(r.get("ltp")), num(r.get("iv")), num(r.get("oi")), num(r.get("prevOi")),
                                       num(r.get("volume")), num(r.get("bid")), num(r.get("ask")), num(r.get("delta")),
                                       spot_p, fut_p, lot, asof, atm_iv, rv20, sigf, vrp))
    return trade_date, chain_rows, snap_rows, ev_rows, schema


CHAIN_COLS = ["trade_date", "symbol", "cls", "expiry", "exp_label", "dte", "tYears", "strike", "type",
              "ltp", "iv", "oi", "prevOi", "volume", "bid", "ask", "delta", "spot", "futPrice", "lotSize",
              "asOf", "exp_atmIv", "exp_rv20", "exp_sigmaForecast", "exp_vrp"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=max(1, min(8, (os.cpu_count() or 2) - 1)))
    a = ap.parse_args()
    root = os.path.join(a.archive, "archive")
    days = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    os.makedirs(a.out, exist_ok=True)
    tasks = [(os.path.join(root, d), d) for d in days]
    chains, snaps, evs = [], [], []
    schema = {"top_missing": Counter(), "top_extra": Counter(), "chain_missing": Counter(),
              "chain_extra": Counter(), "files": Counter(), "bad_files": []}
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for td, cr, sr, er, sc in ex.map(process_day, tasks):
            chains.append(pd.DataFrame.from_records(cr, columns=CHAIN_COLS))
            snaps.extend(sr)
            evs.extend(er)
            for k in ("top_missing", "top_extra", "chain_missing", "chain_extra", "files"):
                schema[k].update(sc[k])
            schema["bad_files"].extend(sc["bad_files"])
    chain = pd.concat(chains, ignore_index=True)
    for c in ("trade_date", "symbol", "cls", "expiry", "exp_label", "type", "asOf"):
        chain[c] = chain[c].astype("category")
    for c in ("ltp", "iv", "bid", "ask", "delta", "tYears", "exp_atmIv", "exp_rv20", "exp_sigmaForecast", "exp_vrp"):
        chain[c] = chain[c].astype("float32")
    for c in ("dte", "lotSize"):
        chain[c] = chain[c].astype("Int32")
    for c in ("oi", "prevOi", "volume"):
        chain[c] = chain[c].astype("Int64")
    dup = chain.duplicated(["trade_date", "symbol", "expiry", "strike", "type"]).sum()
    snap = pd.DataFrame(snaps).sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    # Data-quality flags (do not drop anything; downstream filters on these).
    ist = pd.to_datetime(snap["asOf"], utc=True).dt.tz_convert("Asia/Kolkata")
    snap["asof_ist"] = ist.dt.strftime("%Y-%m-%d %H:%M")
    close_ts = pd.to_datetime(snap["trade_date"] + " 15:30").dt.tz_localize("Asia/Kolkata")
    snap["asof_lag_h"] = ((ist - close_ts).dt.total_seconds() / 3600).round(2)
    # post-close of trade_date and before the next session's 09:15 open
    snap["post_close"] = snap["asof_lag_h"] >= 0
    snap["asof_date_ok"] = (snap["asof_lag_h"] > -6.25) & (snap["asof_lag_h"] < 17.75)
    prev = snap.groupby("symbol")[["spot_price", "spot_prevClose", "asOf"]].shift(1)
    # Same close as the previous archived day AND (zero change / same prevClose), or the
    # very same upstream asOf -> a holiday re-publish or a frozen upstream file.
    snap["dup_prev"] = ((snap["spot_price"] == prev["spot_price"]) &
                        ((snap["spot_changePct"] == 0) | (snap["spot_prevClose"] == prev["spot_prevClose"]))
                        ) | (snap["asOf"] == prev["asOf"])
    snap["usable"] = snap["asof_date_ok"] & snap["post_close"] & ~snap["dup_prev"]
    ev = pd.DataFrame(evs)
    chain.to_parquet(os.path.join(a.out, "chain.parquet"), index=False, compression="zstd")
    snap.to_parquet(os.path.join(a.out, "snap.parquet"), index=False, compression="zstd")
    if len(ev):
        ev["approx"] = ev["approx"].astype("boolean")
        ev["done"] = ev["done"].astype("boolean")
        ev.to_parquet(os.path.join(a.out, "events.parquet"), index=False, compression="zstd")
    print(f"days={len(days)} files={dict(schema['files'])} bad_files={len(schema['bad_files'])}")
    print(f"chain.parquet rows={len(chain):,} (dup keys={dup}) symbols={chain['symbol'].nunique()}")
    print(f"snap.parquet rows={len(snap):,}  events.parquet rows={len(ev):,}")
    print("schema top_missing:", dict(schema["top_missing"]))
    print("schema top_extra:", dict(list(schema["top_extra"].items())[:12]))
    print("schema chain_missing:", dict(schema["chain_missing"]), "chain_extra:", dict(schema["chain_extra"]))


if __name__ == "__main__":
    main()
