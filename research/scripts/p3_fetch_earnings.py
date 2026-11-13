"""P3 - multi-year quarterly-results (earnings) dates for the NSE F&O stock universe.

Source: NSE public JSON APIs via a cookie-primed requests.Session (same handshake
as _ext/xerxes/scripts/nse.mjs). Per symbol, two calls:
  1. /api/corporates-financial-results?index=equities&symbol=S&period=Quarterly
     -> every quarterly results filing with its exchange broadcast timestamp
        (broadCastDate / exchdisstime). This is the ACTUAL announcement moment.
  2. /api/event-calendar?symbol=S
     -> full board-meeting history (date + purpose) - the scheduled results date.
Raw JSON is cached under data/raw/earnings/{finres,evcal}/SYMBOL.json; re-runs
only fetch missing symbols (use --refresh to refetch).

Output data/earnings/earnings_dates.parquet, one row per (symbol, results event):
  symbol, event_date (date the results hit the exchange; else board-meeting date),
  kind ('results'), purpose (calendar purpose / fin-results relatingTo),
  source ('nse_finres+evcal' | 'nse_finres' | 'nse_evcal'),
  announce_time (IST datetime of first broadcast, NaT if unknown),
  session ('pre' <09:15, 'during' 09:15-15:30, 'post' >15:30, None if unknown),
  first_tradable_date (event_date if pre/during... see below), board_date,
  period_end (quarter end the results relate to, from fin-results).
first_tradable_date = event_date if session in (pre, during) else next weekday
(no holiday calendar applied - caveat).
"""
import argparse, json, os, re, sys, time
from datetime import timedelta

import pandas as pd
import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RAW = os.path.join(ROOT, "data", "raw", "earnings")
OUT = os.path.join(ROOT, "data", "earnings", "earnings_dates.parquet")
START, END = pd.Timestamp("2019-01-01"), pd.Timestamp("2026-11-30")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
EP = {
    "finres": ("corporates-financial-results?index=equities&symbol={s}&period=Quarterly",
               "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"),
    "evcal": ("event-calendar?symbol={s}",
              "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"),
}
RESULT_RE = re.compile(r"result", re.I)


class NSE:
    def __init__(self):
        self.s, self.n = None, 0

    def prime(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        r = self.s.get("https://www.nseindia.com/option-chain",
                       headers={"Accept": "text/html,*/*"}, timeout=30)
        self.n = 0
        time.sleep(1.0)
        return r.status_code

    def get(self, path, referer):
        for attempt in range(4):
            if self.s is None or self.n >= 80:
                self.prime()
            time.sleep(0.6)
            self.n += 1
            try:
                r = self.s.get("https://www.nseindia.com/api/" + path, timeout=40,
                               headers={"Accept": "application/json, text/plain, */*", "Referer": referer})
                if r.status_code == 200:
                    j = r.json()
                    return j if isinstance(j, list) else j.get("data", j)
                err = r.status_code
            except Exception as e:  # network / non-JSON bot page
                err = type(e).__name__
            self.s = None  # force re-prime
            time.sleep(2 + 3 * attempt)
        raise RuntimeError(f"{path}: {err}")


def universe():
    s = pd.read_parquet(os.path.join(ROOT, "data", "archive", "snap.parquet"), columns=["symbol", "cls"])
    return sorted(s.loc[s.cls == "stock", "symbol"].unique())


def fetch_all(syms, refresh):
    nse, fails = NSE(), {}
    for kind, (tmpl, ref) in EP.items():
        os.makedirs(os.path.join(RAW, kind), exist_ok=True)
        for sym in syms:
            p = os.path.join(RAW, kind, f"{sym}.json")
            if os.path.exists(p) and not refresh:
                continue
            try:
                d = nse.get(tmpl.format(s=requests.utils.quote(sym, safe="")), ref)
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(d, f)
            except Exception as e:
                fails[f"{kind}:{sym}"] = str(e)[-60:]
    return fails


def load(kind, sym):
    p = os.path.join(RAW, kind, f"{sym}.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, list) else []


def ts(x, fmt):
    return pd.to_datetime(x, format=fmt, errors="coerce") if x else pd.NaT


def session_of(t):
    if pd.isna(t):
        return None
    m = t.hour * 60 + t.minute
    return "pre" if m < 555 else ("during" if m <= 930 else "post")


def next_weekday(d):
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def build(syms):
    rows = []
    for sym in syms:
        # --- fin-results filings -> one event per quarter (earliest broadcast)
        fr = []
        for r in load("finres", sym):
            t = ts(r.get("broadCastDate") or r.get("exchdisstime"), "%d-%b-%Y %H:%M:%S")
            if pd.isna(t):
                t = ts(r.get("filingDate"), "%d-%b-%Y %H:%M")
            if pd.isna(t):
                continue
            fr.append(dict(t=t, pe=ts(r.get("toDate"), "%d-%b-%Y"), rel=r.get("relatingTo")))
        frd = pd.DataFrame(fr)
        ev = []
        if len(frd):
            # group by period end; a result is announced once, later filings are revisions/
            # the other (consol/standalone) statement - keep earliest broadcast.
            frd["pe"] = frd["pe"].fillna(frd["t"].dt.normalize())
            g = frd.sort_values("t").groupby("pe", as_index=False).first()
            # guard: a filing >150d after period end is a late revision, not the announcement
            g = g[(g.t - g.pe).dt.days.between(0, 150)]
            for r in g.itertuples():
                ev.append(dict(symbol=sym, event_date=r.t.normalize(), announce_time=r.t,
                               period_end=r.pe, purpose=r.rel, board_date=pd.NaT, src="nse_finres"))
        # --- event calendar results meetings
        cal = []
        for r in load("evcal", sym):
            pur = f"{r.get('purpose', '')} | {r.get('bm_desc', '')}"
            if not RESULT_RE.search(pur):
                continue
            d = ts(r.get("date"), "%d-%b-%Y")
            if pd.notna(d):
                cal.append((d, str(r.get("purpose", ""))[:60]))
        cal = sorted(set(cal))
        used = set()
        for e in ev:  # attach board date within [-2, +1] days of the broadcast date
            for i, (d, pur) in enumerate(cal):
                if i not in used and -2 <= (e["event_date"] - d).days <= 1:
                    e["board_date"], e["src"], e["purpose"] = d, "nse_finres+evcal", pur
                    used.add(i)
                    break
        for i, (d, pur) in enumerate(cal):
            if i in used:
                continue
            if any(abs((e["event_date"] - d).days) <= 7 for e in ev):
                continue  # meeting near a known filing (adjourned / two-day board)
            ev.append(dict(symbol=sym, event_date=d, announce_time=pd.NaT, period_end=pd.NaT,
                           purpose=pur, board_date=d, src="nse_evcal"))
        rows += ev
    df = pd.DataFrame(rows)
    df = df[(df.event_date >= START) & (df.event_date <= END)].copy()
    df["kind"] = "results"
    df["source"] = df.pop("src")
    df["session"] = df.announce_time.map(session_of)
    df["first_tradable_date"] = [d if s in ("pre", "during") else (next_weekday(d) if s == "post" else pd.NaT)
                                 for d, s in zip(df.event_date, df.session)]
    df = df.sort_values(["symbol", "event_date"]).drop_duplicates(["symbol", "event_date"])
    cols = ["symbol", "event_date", "kind", "purpose", "source", "announce_time", "session",
            "first_tradable_date", "board_date", "period_end"]
    return df[cols].reset_index(drop=True)


def validate(df):
    """Match vs the archive's in-window Result|Earnings symbol-events (p1 definition)."""
    e = pd.read_parquet(os.path.join(ROOT, "data", "archive", "events.parquet"))
    days = sorted(e.trade_date.dropna().unique())
    w = e[e.event_date.notna() & (e.event_date >= days[0]) & (e.event_date <= days[-1])]
    w = w[w.kind.str.contains("Result|Earnings", case=False, na=False) & (w.cls == "stock")]
    ref = w.drop_duplicates(["symbol", "event_date"]).copy()
    ref["d"] = pd.to_datetime(ref.event_date)
    mine = df.groupby("symbol").event_date.apply(list).to_dict()
    def best(r):
        ds = mine.get(r.symbol, [])
        return min((abs((x - r.d).days) for x in ds), default=None)
    ref["gap"] = ref.apply(best, axis=1)
    return ref


def report(df, ref, fails, nsym):
    y = df.event_date.dt.year
    sy = df.groupby([df.symbol, y]).size()
    full = [yy for yy in range(2019, 2026)]
    L = ["# P3 - Earnings (quarterly results) dates, NSE F&O stocks 2019-01 -> 2026-11", "",
         "**Source:** NSE public JSON API (cookie-primed session, as in xerxes `nse.mjs`), per symbol: "
         "`/api/corporates-financial-results?index=equities&symbol=S&period=Quarterly` (filing broadcast "
         "timestamp = actual announcement time) + `/api/event-calendar?symbol=S` (board-meeting dates, "
         "purpose matching /result/). BSE not needed. Script `research/scripts/p3_fetch_earnings.py`, raw cache "
         "`data/raw/earnings/{finres,evcal}/`, output `data/earnings/earnings_dates.parquet`.", "",
         f"- Universe {nsym} symbols; fetch failures: {len(fails)}. Events: **{len(df)}**, symbols covered "
         f"**{df.symbol.nunique()}/{nsym}**.",
         f"- Source mix: {df.source.value_counts().to_dict()}.",
         f"- announce_time known for {df.announce_time.notna().mean():.1%} of events; session split "
         f"{df.session.value_counts(dropna=False).to_dict()}.", "",
         "| year | events | symbols | median ev/sym | share of syms with 4 |", "|---|---|---|---|---|"]
    for yy, g in df.groupby(y):
        c = g.groupby("symbol").size()
        L.append(f"| {yy} | {len(g)} | {c.size} | {c.median():.0f} | {(c == 4).mean():.0%} |")
    fy = sy[sy.index.get_level_values(1).isin(full)]
    L += ["", f"Full years 2019-2025: events per symbol-year mean {fy.mean():.2f}, median {fy.median():.0f}; "
          f"distribution {fy.value_counts().sort_index().to_dict()}.", "",
          "## Validation vs archive (`data/archive/events.parquet`)",
          f"Reference = in-window stock events with kind ~ Result|Earnings, distinct (symbol, date): **{len(ref)}** "
          "(p1 definition; includes news-derived and 'Earnings call' rows)."]
    for src, g in [("all", ref), ("nse-sourced", ref[ref.source == "nse"]), ("news-sourced", ref[ref.source == "news"])]:
        L.append(f"- {src} (n={len(g)}): exact {(g.gap == 0).mean():.1%}, within +-1d **{(g.gap <= 1).mean():.1%}**, "
                 f"+-3d {(g.gap <= 3).mean():.1%}, no event within 7d {((g.gap > 7) | g.gap.isna()).mean():.1%}.")
    k = ref[ref.kind == "Results"]
    L.append(f"- kind=='Results' only (n={len(k)}): within +-1d {(k.gap <= 1).mean():.1%}.")
    miss = ref[(ref.gap > 1) | ref.gap.isna()]
    L.append(f"- Misses by archive kind/source: {miss.groupby(['kind', 'source']).size().to_dict()}.")
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    syms = universe()
    fails = {} if a.no_fetch else fetch_all(syms, a.refresh)
    df = build(syms)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"universe {len(syms)}; fetch failures {len(fails)} {list(fails.items())[:5]}")
    print(f"events {len(df)}; symbols {df.symbol.nunique()}; sources {df.source.value_counts().to_dict()}")
    print(f"session {df.session.value_counts(dropna=False).to_dict()}")
    print("per year:", df.groupby(df.event_date.dt.year).size().to_dict())
    print(f"-> {OUT}")
    ref = validate(df)
    L = report(df, ref, fails, len(syms))
    md = os.path.join(ROOT, "research", "out", "p3_earnings.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("\n".join(L + CAVEATS) + "\n")
    for l in L[-6:]:
        print(l[:200])
    print(f"-> {md} ({len(L) + len(CAVEATS)} lines)")
    return 0


CAVEATS = [
    "", "## Caveats",
    "- event_date = calendar date of the FIRST exchange broadcast of that quarter's results (earliest of "
    "standalone/consolidated filings). Board-meeting date kept in `board_date`; calendar-only events "
    "(no filing found) use the meeting date and have no time.",
    "- `session`: pre <09:15, during 09:15-15:30, post >15:30 IST. `first_tradable_date` = next weekday for "
    "post-market results (no exchange-holiday calendar applied).",
    "- Universe = today's ~210 F&O names (survivorship): names that left F&O are absent; recently listed names "
    "have short histories. Renamed symbols are queried under the current symbol only.",
    "- 2019-2020 are noisier: about 13% of symbol-years have 5 or more events, from finres-only and calendar-only rows "
    "falling in the same quarter (late or re-filed broadcasts, adjourned meetings). From 2021 on, more than 90% of "
    "symbol-years have exactly 4. Before an event study, dedupe to one event per symbol-quarter.",
    "- About 29% of events (mostly `nse_evcal`) have no filing match, so they have no announce_time and no session.",
    "- Filings >150 days after period end are dropped as revisions; adjourned meetings may leave a stray "
    "calendar-only row. Annual (Q4) results come through the Quarterly feed as 'Fourth Quarter'.",
]


if __name__ == "__main__":
    sys.exit(main())
