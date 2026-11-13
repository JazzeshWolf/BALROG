"""P3 - fetch 1-minute underlying history from Upstox (V3 historical candles) and
write clean per-symbol/year parquet.

  python research/scripts/p3_fetch_intraday.py            # universe + fetch + build
  python research/scripts/p3_fetch_intraday.py --build    # build parquet from cache only

Resumable: every response (incl. empty) is cached as
data/raw/upstox/candles/1min/<key>_<from>_<to>.json and a cached window is never
re-requested.  <= 1 req/s (Upstox limit 2,000 / 30 min; brief asks <= 3 req/s),
exponential back-off on 429/5xx, exits with code 2 if blocked.
UPSTOX_ACCESS_TOKEN is sent if set and never printed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
from lib.intraday import clean_session  # noqa: E402

RAW = ROOT / "data" / "raw" / "upstox"
CACHE = RAW / "candles" / "1min"
OUT = ROOT / "data" / "intraday" / "1min"
REF = ROOT / "data" / "ref"
LOG = ROOT / "research" / "out" / "p3_fetch_intraday_log.json"
API = "https://api.upstox.com"
START, END = dt.date(2022, 1, 3), dt.date(2026, 11, 12)
MIN_GAP = 1.0
N_STOCKS = 30

TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN") or None
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "balrog-research"})
if TOKEN:
    S.headers["Authorization"] = "Bearer " + TOKEN

STATS = {"requests": 0, "cache_hits": 0, "empty": 0, "retries": 0, "elapsed_s": 0.0}
_last = [0.0]


class Blocked(Exception):
    pass


def master(ex: str) -> list[dict]:
    with gzip.open(RAW / f"{ex}.json.gz", "rt", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------- universe
def build_universe() -> pd.DataFrame:
    c = pd.read_parquet(ROOT / "data" / "archive" / "chain.parquet",
                        columns=["trade_date", "symbol", "cls", "volume", "ltp"])
    s = c[c.cls == "stock"].copy()
    s["val"] = s.volume.astype(float) * s.ltp.astype(float)  # volume is in shares
    d = s.groupby(["symbol", "trade_date"], observed=True).agg(val=("val", "sum"), vol=("volume", "sum")).reset_index()
    u = d.groupby("symbol", observed=True).agg(med_opt_traded_value_inr=("val", "median"),
                                               med_opt_volume_shares=("vol", "median"),
                                               n_days=("trade_date", "nunique")).reset_index()
    u = u.sort_values("med_opt_traded_value_inr", ascending=False).head(N_STOCKS).reset_index(drop=True)
    u.insert(0, "rank", range(1, len(u) + 1))
    eq = {x["trading_symbol"]: x for x in master("NSE") if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"}
    u["instrument_key"] = [eq[s]["instrument_key"] if s in eq else "" for s in u.symbol.astype(str)]
    u["isin"] = [eq[s].get("isin", "") if s in eq else "" for s in u.symbol.astype(str)]
    u["chain_period"] = f"{c.trade_date.astype(str).min()}..{c.trade_date.astype(str).max()}"
    REF.mkdir(parents=True, exist_ok=True)
    u.to_csv(REF / "intraday_universe.csv", index=False)
    return u


def targets() -> list[tuple[str, str, str]]:
    """(symbol, instrument_key, kind)."""
    t = [("NIFTY", "NSE_INDEX|Nifty 50", "index"), ("BANKNIFTY", "NSE_INDEX|Nifty Bank", "index"),
         ("SENSEX", "BSE_INDEX|SENSEX", "index"), ("INDIAVIX", "NSE_INDEX|India VIX", "index")]
    p = REF / "intraday_universe.csv"
    u = pd.read_csv(p) if p.exists() else build_universe()
    t += [(r.symbol, r.instrument_key, "stock") for r in u.itertuples() if isinstance(r.instrument_key, str) and r.instrument_key]
    # live near-month futures only (expired contracts need Upstox Plus + token)
    now = time.time() * 1000
    futs = [x for x in master("NSE") if x.get("instrument_type") == "FUT"
            and x.get("underlying_symbol") in ("NIFTY", "BANKNIFTY") and x["expiry"] > now]
    for und in ("NIFTY", "BANKNIFTY"):
        f = min((x for x in futs if x["underlying_symbol"] == und), key=lambda x: x["expiry"])
        t.append((f"{und}_FUT_NEAR", f["instrument_key"], "future"))
    return t


# ----------------------------------------------------------------- fetch
def windows():
    d = dt.date(START.year, START.month, 1)
    while d <= END:
        nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        yield max(d, START), min(nxt - dt.timedelta(days=1), END)
        d = nxt


def cpath(key, a, b) -> Path:
    return CACHE / f"{key.replace('|', '_').replace(' ', '-')}_{a}_{b}.json"


def get(key, a, b):
    p = cpath(key, a, b)
    if p.exists():
        STATS["cache_hits"] += 1
        return json.loads(p.read_text())
    url = f"{API}/v3/historical-candle/{quote(key, safe='')}/minutes/1/{b}/{a}"
    for attempt in range(6):
        w = MIN_GAP - (time.time() - _last[0])
        if w > 0:
            time.sleep(w)
        STATS["requests"] += 1
        try:
            r = S.get(url, timeout=40)
            code = r.status_code
        except requests.RequestException:
            code = -1
        _last[0] = time.time()
        if code == 200:
            c = (r.json().get("data") or {}).get("candles") or []
            p.write_text(json.dumps(c, separators=(",", ":")))
            if not c:
                STATS["empty"] += 1
            return c
        if code in (429, -1) or code >= 500:
            STATS["retries"] += 1
            time.sleep(min(5 * 3 ** attempt, 300))
            continue
        # 4xx other than 429: cache nothing, report
        raise Blocked(f"HTTP {code} for {key} {a}..{b}: {r.text[:150]}")
    raise Blocked(f"giving up after retries on {key} {a}..{b} (last code {code})")


def fetch_all(tg):
    CACHE.mkdir(parents=True, exist_ok=True)
    wins = list(windows())
    for sym, key, kind in tg:
        empty_run = 0
        got = 0
        for a, b in reversed(wins):  # newest first: lets us stop early on unlisted history
            c = get(key, a, b)
            got += bool(c)
            empty_run = 0 if c else empty_run + 1
            # stop if the series has clearly not started (3 consecutive empty months after data)
            # or never served anything in the 3 most recent months
            if empty_run >= 3 and (got > 0 or kind == "future" or sym == "INDIAVIX"):
                break
        print(f"{sym:<18} months_with_data={got:>3}  req={STATS['requests']}", flush=True)


# ----------------------------------------------------------------- build
def build(tg):
    rows = []
    for sym, key, kind in tg:
        stem = key.replace("|", "_").replace(" ", "-") + "_"
        files = sorted(CACHE.glob(stem + "*.json"))
        cand = [x for f in files for x in json.loads(f.read_text())]
        if not cand:
            rows.append((sym, 0, 0, 0))
            continue
        df = pd.DataFrame([x[:6] for x in cand], columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df.ts).dt.tz_convert("Asia/Kolkata")
        n_raw = len(df)
        df, n_dup, n_out = clean_session(df)
        df["volume"] = df.volume.astype("int64")
        d = OUT / f"symbol={sym}"
        d.mkdir(parents=True, exist_ok=True)
        for y, g in df.groupby(df.ts.dt.year):
            g.reset_index(drop=True).to_parquet(d / f"year={y}.parquet", index=False)
        rows.append((sym, n_raw, n_dup, n_out))
    pd.DataFrame(rows, columns=["symbol", "raw_bars", "dup_removed", "out_of_session_removed"]).to_csv(
        ROOT / "research" / "out" / "p3_intraday_build.csv", index=False)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="build parquet from cache only")
    args = ap.parse_args()
    t0 = time.time()
    tg = targets()
    print(f"targets: {len(tg)}  token_sent={bool(TOKEN)}")
    code = 0
    if not args.build:
        try:
            fetch_all(tg)
        except Blocked as e:
            print("BLOCKED:", e)
            code = 2
    rows = build(tg)
    STATS["elapsed_s"] = round(time.time() - t0, 1)
    prev = json.loads(LOG.read_text()) if LOG.exists() else {"runs": []}
    prev["runs"].append({"at": dt.datetime.now().isoformat(timespec="seconds"), **STATS, "exit": code})
    prev["total_requests"] = sum(r["requests"] for r in prev["runs"])
    prev["total_elapsed_s"] = round(sum(r["elapsed_s"] for r in prev["runs"]), 1)
    LOG.write_text(json.dumps(prev, indent=1))
    print(f"built {sum(1 for r in rows if r[1])} symbols, raw bars {sum(r[1] for r in rows):,}")
    print(json.dumps(STATS), "total_requests_all_runs=", prev["total_requests"])
    sys.exit(code)


if __name__ == "__main__":
    main()
