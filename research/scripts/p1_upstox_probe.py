"""P1 Upstox data-availability probe (BALROG).

Probes Upstox historical / intraday / expired-instrument endpoints and records,
for every call: endpoint, params, HTTP status, n_candles, first_ts, last_ts,
has_oi, error (<=200 chars). No raw responses are stored.

Auth: reads UPSTOX_ACCESS_TOKEN from the environment and sends it only if
present. The token is never printed, logged or written.

Usage:  python research/scripts/p1_upstox_probe.py
Output: research/out/p1_upstox_probe.json  (+ compact console summary)
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "upstox"
OUT = ROOT / "research" / "out" / "p1_upstox_probe.json"
API = "https://api.upstox.com"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{ex}.json.gz"
MIN_GAP = 0.35          # seconds between API calls (< 3 req/s)
MAX_CALLS = 140         # hard budget (< 150)
TODAY = dt.date.today()

TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN") or None
SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json", "User-Agent": "balrog-research-probe"})
if TOKEN:
    SESSION.headers["Authorization"] = "Bearer " + TOKEN

RECORDS: list[dict] = []
_last_call = [0.0]
_n_calls = [0]


# --------------------------------------------------------------------------- helpers
def _redact(s: str) -> str:
    if TOKEN and TOKEN in s:
        s = s.replace(TOKEN, "<redacted>")
    return s


def load_master(ex: str) -> list[dict]:
    RAW.mkdir(parents=True, exist_ok=True)
    p = RAW / f"{ex}.json.gz"
    fresh = p.exists() and (time.time() - p.stat().st_mtime) < 20 * 3600
    if not fresh:
        r = requests.get(MASTER_URL.format(ex=ex), timeout=60)
        r.raise_for_status()
        p.write_bytes(r.content)
    with gzip.open(p, "rt", encoding="utf-8") as f:
        return json.load(f)


def call(probe: str, path: str, params: dict | None = None, note: str = "") -> dict:
    """GET API+path; summarise into one record. Never stores the raw body."""
    rec = {"probe": probe, "endpoint": path, "params": params or {}, "note": note,
           "auth_sent": bool(TOKEN), "status": None, "n_candles": None,
           "first_ts": None, "last_ts": None, "has_oi": None, "oi_nonzero": None,
           "n_items": None, "error": None}
    if _n_calls[0] >= MAX_CALLS:
        rec["error"] = "skipped: request budget exhausted"
        RECORDS.append(rec)
        return rec
    wait = MIN_GAP - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _n_calls[0] += 1
    try:
        r = SESSION.get(API + path, params=params, timeout=30)
        _last_call[0] = time.time()
        rec["status"] = r.status_code
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code == 200 and isinstance(body, dict):
            data = body.get("data")
            if isinstance(data, dict) and "candles" in data:
                c = data["candles"] or []
                rec["n_candles"] = len(c)
                if c:
                    ts = sorted(x[0] for x in c)
                    rec["first_ts"], rec["last_ts"] = ts[0], ts[-1]
                    rec["has_oi"] = all(len(x) >= 7 for x in c)
                    rec["oi_nonzero"] = any(len(x) >= 7 and (x[6] or 0) > 0 for x in c)
                    rec["n_days"] = len({x[0][:10] for x in c})
            elif isinstance(data, list):
                rec["n_items"] = len(data)
                if data and isinstance(data[0], str):
                    rec["first_ts"], rec["last_ts"] = min(data), max(data)
        else:
            if isinstance(body, dict):
                errs = body.get("errors") or body
                msg = json.dumps(errs, separators=(",", ":"))
            else:
                msg = r.text
            rec["error"] = _redact(msg)[:200]
    except requests.RequestException as e:
        rec["error"] = _redact(f"{type(e).__name__}: {e}")[:200]
    RECORDS.append(rec)
    return rec


def k(key: str) -> str:
    return quote(key, safe="")


def v3(key, to, frm, unit="minutes", n=1):
    return f"/v3/historical-candle/{k(key)}/{unit}/{n}/{to}/{frm}"


def v2(key, to, frm, interval="1minute"):
    return f"/v2/historical-candle/{k(key)}/{interval}/{to}/{frm}"


def d(x: dt.date) -> str:
    return x.isoformat()


def window(end: dt.date, days=13):
    return d(end), d(end - dt.timedelta(days=days))


def got(rec) -> bool:
    return rec["status"] == 200 and (rec["n_candles"] or 0) > 0


# --------------------------------------------------------------------------- probes
def probe_index_and_equity():
    nifty, bank, sensex = "NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank", "BSE_INDEX|SENSEX"
    rel = "NSE_EQ|INE002A01018"
    m1, y1, y3 = TODAY - dt.timedelta(days=30), TODAY - dt.timedelta(days=365), TODAY - dt.timedelta(days=3 * 365)

    res = {}
    for lbl, end in [("1m_ago", m1), ("1y_ago", y1), ("3y_ago", y3)]:
        to, frm = window(end)
        res[lbl] = call(f"nifty_idx_1min_v3_{lbl}", v3(nifty, to, frm))
    for lbl, end in [("1m_ago", m1), ("1y_ago", y1)]:
        to, frm = window(end)
        call(f"nifty_idx_1min_v2_{lbl}", v2(nifty, to, frm))

    # earliest 1-min date for NIFTY via month-granular binary search (V3)
    lo, hi = dt.date(2008, 1, 1), y3            # hi is assumed served (checked above)
    if not got(res["3y_ago"]):
        hi = y1 if got(res["1y_ago"]) else m1
    lo_m, hi_m = lo.year * 12 + lo.month - 1, hi.year * 12 + hi.month - 1
    while hi_m - lo_m > 1:
        mid = (lo_m + hi_m) // 2
        start = dt.date(mid // 12, mid % 12 + 1, 1)
        end = start + dt.timedelta(days=27)
        r = call("nifty_idx_1min_v3_earliest_search", v3(nifty, d(end), d(start)), note=f"month {start:%Y-%m}")
        if got(r):
            hi_m = mid
        else:
            lo_m = mid
    earliest = dt.date(hi_m // 12, hi_m % 12 + 1, 1)
    call("nifty_idx_1min_v3_earliest_month", v3(nifty, d(earliest + dt.timedelta(days=27)), d(earliest)),
         note="first month that returned candles")
    # daily depth
    call("nifty_idx_day_v3_2000s", v3(nifty, "2009-12-31", "2000-01-01", unit="days"))
    call("nifty_idx_day_v3_1990s", v3(nifty, "1999-12-31", "1990-01-01", unit="days"))

    for name, key in [("banknifty_idx", bank), ("sensex_idx", sensex), ("reliance_eq", rel)]:
        to, frm = window(m1)
        call(f"{name}_1min_v3_1m_ago", v3(key, to, frm))
        to, frm = window(y3)
        call(f"{name}_1min_v3_3y_ago", v3(key, to, frm))
        call(f"{name}_1min_v3_2022-01", v3(key, "2022-01-28", "2022-01-01"))
        call(f"{name}_1min_v3_2021-12", v3(key, "2021-12-28", "2021-12-01"))
    to, frm = window(m1)
    call("reliance_eq_1min_v2_1m_ago", v2(rel, to, frm))
    # V2 old-window (docs say 1-min limited)
    to, frm = window(y3)
    call("reliance_eq_1min_v2_3y_ago", v2(rel, to, frm))
    # intraday (today)
    call("nifty_idx_intraday_v3_1min", f"/v3/historical-candle/intraday/{k(nifty)}/minutes/1")
    call("nifty_idx_intraday_v2_1min", f"/v2/historical-candle/intraday/{k(nifty)}/1minute")


def spot_close(key: str) -> float | None:
    """Daily candles over last 10 days; return last close (kept out of RECORDS body)."""
    if _n_calls[0] >= MAX_CALLS:
        return None
    to, frm = d(TODAY), d(TODAY - dt.timedelta(days=10))
    wait = MIN_GAP - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _n_calls[0] += 1
    status = None
    try:
        r = SESSION.get(API + v3(key, to, frm, unit="days"), timeout=30)
        status = r.status_code
        _last_call[0] = time.time()
        c = r.json()["data"]["candles"]
        c.sort(key=lambda x: x[0])
        close = float(c[-1][4])
    except Exception:  # noqa: BLE001
        close = None
    RECORDS.append({"probe": f"spot_for_atm[{key}]", "endpoint": "/v3/historical-candle/.../days/1",
                    "params": {}, "note": "used only to pick ATM strike", "auth_sent": bool(TOKEN),
                    "status": status, "n_candles": None,
                    "first_ts": None, "last_ts": None, "has_oi": None, "oi_nonzero": None,
                    "n_items": None, "error": None if close else "could not read close"})
    return close


def pick_option(master, underlying_key, spot, step, monthly_only=False, otype="CE"):
    now_ms = time.time() * 1000
    opts = [x for x in master if x.get("underlying_key") == underlying_key
            and x.get("instrument_type") == otype and (x.get("expiry") or 0) > now_ms]
    if monthly_only:
        opts = [x for x in opts if not x.get("weekly")]
    if not opts:
        return None
    exp = min(x["expiry"] for x in opts)
    same = [x for x in opts if x["expiry"] == exp]
    return min(same, key=lambda x: abs(x["strike_price"] - (spot or x["strike_price"])))


def pick_future(master, underlying_key):
    now_ms = time.time() * 1000
    fut = [x for x in master if x.get("underlying_key") == underlying_key
           and x.get("instrument_type") == "FUT" and (x.get("expiry") or 0) > now_ms]
    return min(fut, key=lambda x: x["expiry"]) if fut else None


def walk_back(label: str, inst: dict, max_windows=5):
    """1-min V3 candles in ~1-month windows from today backwards until two empties."""
    key = inst["instrument_key"]
    exp = dt.datetime.fromtimestamp(inst["expiry"] / 1000).date()
    end, empties, total_days, first = TODAY, 0, 0, None
    for i in range(max_windows):
        start = end - dt.timedelta(days=27)
        r = call(f"{label}_1min_v3_win{i}", v3(key, d(end), d(start)),
                 note=f"{inst.get('trading_symbol')} exp {exp}")
        if got(r):
            empties = 0
            total_days += r.get("n_days", 0)
            first = r["first_ts"]
        else:
            empties += 1
            if empties >= 2 or r["status"] != 200:
                break
        end = start - dt.timedelta(days=1)
    RECORDS.append({"probe": f"{label}_summary", "endpoint": "(derived)", "params": {"instrument_key": key},
                    "note": f"{inst.get('trading_symbol')} exp {exp}; total trading days with 1-min data",
                    "auth_sent": bool(TOKEN), "status": None, "n_candles": None,
                    "first_ts": first, "last_ts": None, "has_oi": None, "oi_nonzero": None,
                    "n_items": total_days, "error": None})
    # also V2 shape for the same contract, last ~2 weeks
    to, frm = window(TODAY)
    call(f"{label}_1min_v2_recent", v2(key, to, frm), note=inst.get("trading_symbol", ""))


def probe_derivatives():
    nse = load_master("NSE")
    bse = load_master("BSE")
    nifty_spot = spot_close("NSE_INDEX|Nifty 50")
    rel_spot = spot_close("NSE_EQ|INE002A01018")
    sensex_spot = spot_close("BSE_INDEX|SENSEX")

    o = pick_option(nse, "NSE_INDEX|Nifty 50", nifty_spot, 50)
    if o:
        walk_back("nifty_weekly_opt_atm", o)
    # a NIFTY monthly option (listed longer) to measure since-listing depth
    om = pick_option(nse, "NSE_INDEX|Nifty 50", nifty_spot, 50, monthly_only=True)
    if om and (not o or om["instrument_key"] != o["instrument_key"]):
        walk_back("nifty_monthly_opt_atm", om)
    rel_u = next((x["underlying_key"] for x in nse if x.get("underlying_symbol") == "RELIANCE"
                  and x.get("instrument_type") in ("CE", "FUT")), "NSE_EQ|INE002A01018")
    ro = pick_option(nse, rel_u, rel_spot, 10, monthly_only=True)
    if ro:
        walk_back("reliance_monthly_opt_atm", ro)
    so = pick_option(bse, "BSE_INDEX|SENSEX", sensex_spot, 100)
    if so:
        walk_back("sensex_weekly_opt_atm", so, max_windows=3)
    nf = pick_future(nse, "NSE_INDEX|Nifty 50")
    if nf:
        walk_back("nifty_fut_near", nf)
    rf = pick_future(nse, rel_u)
    if rf:
        walk_back("reliance_fut_near", rf)
    if o:
        call("nifty_weekly_opt_intraday_v3_1min",
             f"/v3/historical-candle/intraday/{k(o['instrument_key'])}/minutes/1")
    return o


def probe_expired():
    u = "NSE_INDEX|Nifty 50"
    call("expired_expiries_nifty", "/v2/expired-instruments/expiries", {"instrument_key": u})
    call("expired_option_contracts_nifty_2026-10-27", "/v2/expired-instruments/option/contract",
         {"instrument_key": u, "expiry_date": "2026-10-27"})
    call("expired_option_contracts_nifty_2026-10-28", "/v2/expired-instruments/option/contract",
         {"instrument_key": u, "expiry_date": "2026-10-28"})
    call("expired_future_contracts_nifty_2026-10-27", "/v2/expired-instruments/future/contract",
         {"instrument_key": u, "expiry_date": "2026-10-27"})
    # illustrative expired key shape from docs: <instrument_key>|DD-MM-YYYY
    ek = "NSE_FO|73507|24-04-2025"
    call("expired_hist_candle_1min_doc_example",
         f"/v2/expired-instruments/historical-candle/{k(ek)}/1minute/2025-04-24/2025-04-01")
    call("expired_hist_candle_day_doc_example",
         f"/v2/expired-instruments/historical-candle/{k(ek)}/day/2025-04-24/2025-01-01")


def main() -> int:
    t0 = time.time()
    probe_index_and_equity()
    probe_derivatives()
    probe_expired()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    meta = {"run_at": dt.datetime.now().isoformat(timespec="seconds"), "auth_sent": bool(TOKEN),
            "n_requests": _n_calls[0], "elapsed_s": round(time.time() - t0, 1)}
    OUT.write_text(json.dumps({"meta": meta, "records": RECORDS}, indent=1), encoding="utf-8")

    print(f"requests={meta['n_requests']} auth_sent={meta['auth_sent']} elapsed={meta['elapsed_s']}s -> {OUT}")
    for r in RECORDS:
        if r["probe"].startswith("spot_for_atm") or "earliest_search" in r["probe"]:
            continue
        ok = r["n_candles"] if r["n_candles"] is not None else (r["n_items"] if r["n_items"] is not None else "-")
        span = f"{(r['first_ts'] or '')[:10]}..{(r['last_ts'] or '')[:10]}" if r["first_ts"] else ""
        err = (r["error"] or "")[:60]
        print(f"{r['probe'][:44]:44s} {str(r['status']):4s} n={str(ok):6s} oi={str(r.get('oi_nonzero')):5s} {span} {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
