"""p1_fetch_bhavcopy.py -- polite, cached downloader for NSE/BSE daily archive files.

Usage:
  python research/scripts/p1_fetch_bhavcopy.py --exchange nse --kind fo \
      --from 2024-08-01 --to 2024-08-30 --out data/raw/bhavcopy [--sleep 1.0]

Kinds:
  nse: fo (F&O bhavcopy), cm (cash bhavcopy), index (ind_close_all, includes India VIX),
       vix (alias of index -- India VIX is a row in ind_close_all), participant_oi,
       secban (F&O ban list)
  bse: fo (derivatives bhavcopy), cm (equity bhavcopy)

Behaviour:
  * Weekends are skipped; other non-trading days are recorded as 404 in manifest.csv
    and are NOT retried on later runs (use --retry-missing to force).
  * For each date a list of candidate URLs is tried in order (legacy vs UDiFF picked
    by date, the other format tried as fallback).
  * Retries with exponential backoff on network errors / 5xx / 403 / 429.
  * Files land in <out>/<exchange>/<kind>/<YYYY>/<original-file-name>.
  * manifest.csv columns: date,exchange,kind,url,status,bytes,path,fetched_at
Standard library + requests only.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NSE_HDRS = {"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
BSE_HDRS = {"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.bseindia.com/"}

NSEA = "https://nsearchives.nseindia.com"
NSEA_OLD = "https://archives.nseindia.com"
BSE = "https://www.bseindia.com"

# Tested 2026-11-13: NSE UDiFF F&O/CM files exist from 2024-01-01; legacy fo/cm bhav.csv.zip
# exist up to 2024-07-05 (404 from 2024-07-08). BSE UDiFF derivative CSV exists from 2024-01-02.
UDIFF_NSE = dt.date(2024, 7, 8)        # preferred format switches here
UDIFF_FIRST = dt.date(2024, 1, 1)      # UDiFF available from (both exchanges)
LEGACY_LAST = dt.date(2024, 7, 5)      # NSE legacy available until
UDIFF_BSE = dt.date(2024, 7, 8)

MANIFEST_COLS = ["date", "exchange", "kind", "url", "status", "bytes", "path", "fetched_at"]


def _mon(d: dt.date) -> str:
    return d.strftime("%b").upper()


def _order(d: dt.date, legacy: list, udiff: list) -> list:
    """Preferred format first; only include formats that exist for that date."""
    if d > LEGACY_LAST:
        return udiff
    if d < UDIFF_FIRST:
        return legacy
    return udiff + legacy if d >= UDIFF_NSE else legacy + udiff


def candidates(exchange: str, kind: str, d: dt.date) -> list[str]:
    ymd = d.strftime("%Y%m%d")
    dmy = d.strftime("%d%m%Y")
    if exchange == "nse":
        if kind == "fo":
            legacy = [f"{h}/content/historical/DERIVATIVES/{d.year}/{_mon(d)}/fo{d:%d}{_mon(d)}{d.year}bhav.csv.zip"
                      for h in (NSEA, NSEA_OLD)]
            udiff = [f"{NSEA}/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip"]
            return _order(d, legacy, udiff)
        if kind == "cm":
            legacy = [f"{h}/content/historical/EQUITIES/{d.year}/{_mon(d)}/cm{d:%d}{_mon(d)}{d.year}bhav.csv.zip"
                      for h in (NSEA, NSEA_OLD)]
            udiff = [f"{NSEA}/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"]
            return _order(d, legacy, udiff)
        if kind in ("index", "vix"):
            return [f"{NSEA}/content/indices/ind_close_all_{dmy}.csv",
                    f"{NSEA_OLD}/content/indices/ind_close_all_{dmy}.csv"]
        if kind == "participant_oi":
            return [f"{NSEA}/content/nsccl/fao_participant_oi_{dmy}.csv",
                    f"{NSEA_OLD}/content/nsccl/fao_participant_oi_{dmy}.csv"]
        if kind == "secban":
            return [f"{NSEA}/archives/fo/sec_ban/fo_secban_{dmy}.csv",
                    f"{NSEA}/content/fo/fo_secban_{dmy}.csv"]
    if exchange == "bse":
        dmy2 = d.strftime("%d%m%y")
        if kind == "fo":
            udiff = [f"{BSE}/download/BhavCopy/Derivative/BhavCopy_BSE_FO_0_0_0_{ymd}_F_0000.CSV"]
            legacy = [f"{BSE}/download/Bhavcopy/Derivative/bhavcopy{d:%d-%m-%y}.zip"]
            return udiff + legacy if d >= UDIFF_BSE else legacy + udiff
        if kind == "cm":
            udiff = [f"{BSE}/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ymd}_F_0000.CSV"]
            legacy = [f"{BSE}/download/BhavCopy/Equity/EQ_ISINCODE_{dmy2}.zip",
                      f"{BSE}/download/BhavCopy/Equity/EQ{dmy2}_CSV.ZIP"]
            return udiff + legacy if d >= UDIFF_BSE else legacy + udiff
    raise SystemExit(f"unsupported exchange/kind: {exchange}/{kind}")


def load_manifest(path: str) -> dict:
    done = {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                done[(r["date"], r["exchange"], r["kind"])] = r
    return done


def append_manifest(path: str, row: dict) -> None:
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def looks_valid(content: bytes, url: str) -> bool:
    if len(content) < 50:
        return False
    head = content[:200].lstrip().lower()
    if head.startswith(b"<!doctype") or head.startswith(b"<html"):
        return False  # error/landing page served with 200
    if url.lower().endswith(".zip") and not content.startswith(b"PK"):
        return False
    return True


def fetch(session: requests.Session, url: str, headers: dict, max_tries: int = 4):
    """Return (status, content). status 404 = definitive miss; -1 = gave up."""
    delay = 2.0
    for attempt in range(max_tries):
        try:
            r = session.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                if looks_valid(r.content, url):
                    return 200, r.content
                return 404, b""  # 200 with HTML/empty body = not available
            if r.status_code == 404:
                return 404, b""
            status = r.status_code
        except requests.RequestException:
            status = -1
        if attempt < max_tries - 1:
            time.sleep(delay)
            delay *= 2
    return status, b""


def daterange(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        if d.weekday() < 5:
            yield d
        d += dt.timedelta(days=1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exchange", required=True, choices=["nse", "bse"])
    ap.add_argument("--kind", required=True,
                    choices=["fo", "cm", "index", "vix", "participant_oi", "secban"])
    ap.add_argument("--from", dest="dfrom", required=True)
    ap.add_argument("--to", dest="dto", required=True)
    ap.add_argument("--out", default="data/raw/bhavcopy")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--max-files", type=int, default=0, help="stop after N network fetches (0 = no cap)")
    ap.add_argument("--retry-missing", action="store_true", help="re-try dates recorded as missing")
    ap.add_argument("--dates", default="", help="comma list of YYYY-MM-DD instead of a range")
    a = ap.parse_args(argv)

    kind = "index" if a.kind == "vix" else a.kind  # VIX lives inside ind_close_all
    os.makedirs(a.out, exist_ok=True)
    mpath = os.path.join(a.out, "manifest.csv")
    done = load_manifest(mpath)
    headers = NSE_HDRS if a.exchange == "nse" else BSE_HDRS
    s = requests.Session()

    if a.dates:
        dates = [dt.date.fromisoformat(x.strip()) for x in a.dates.split(",") if x.strip()]
    else:
        dates = list(daterange(dt.date.fromisoformat(a.dfrom), dt.date.fromisoformat(a.dto)))

    n_net = n_ok = n_miss = n_fail = n_skip = 0
    for d in dates:
        key = (d.isoformat(), a.exchange, kind)
        prev = done.get(key)
        if prev and (prev["status"] == "200" and os.path.exists(prev["path"])):
            n_skip += 1
            continue
        if prev and prev["status"] == "404" and not a.retry_missing:
            n_skip += 1
            continue
        if a.max_files and n_net >= a.max_files:
            print(f"max-files {a.max_files} reached; stopping")
            break
        status, content, url_used = 404, b"", ""
        for url in candidates(a.exchange, kind, d):
            if n_net:
                time.sleep(a.sleep)
            n_net += 1
            status, content = fetch(s, url, headers)
            url_used = url
            if status == 200:
                break
            if status not in (404,):
                break  # blocked / network failure: don't hammer fallbacks
        path = ""
        if status == 200:
            sub = os.path.join(a.out, a.exchange, kind, str(d.year))
            os.makedirs(sub, exist_ok=True)
            path = os.path.join(sub, url_used.rsplit("/", 1)[1])
            with open(path, "wb") as f:
                f.write(content)
            n_ok += 1
        elif status == 404:
            n_miss += 1
        else:
            n_fail += 1
        row = dict(date=d.isoformat(), exchange=a.exchange, kind=kind, url=url_used, status=status,
                   bytes=len(content), path=path.replace("\\", "/"),
                   fetched_at=dt.datetime.now().isoformat(timespec="seconds"))
        if status in (200, 404):  # transient failures are not recorded -> retried next run
            append_manifest(mpath, row)
        print(f"{d} {a.exchange}/{kind} {status} {len(content):>9} {url_used.rsplit('/', 1)[-1]}")

    print(f"done: ok={n_ok} missing={n_miss} failed={n_fail} skipped={n_skip} requests={n_net}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
