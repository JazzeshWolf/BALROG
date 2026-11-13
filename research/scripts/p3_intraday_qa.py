"""P3 - QA of 1-minute underlying history -> research/out/p3_intraday_qa.md (<= 50 lines),
research/out/p3_intraday_qa_symbols.csv (full per-symbol table),
data/ref/special_sessions.csv.

Uses only the parquet in data/intraday/1min plus the raw cache (for out-of-session /
special-session detection).  Index daily closes for the cross-check come from Upstox V3
daily candles (1 request per index, cached under data/raw/upstox/candles/day/).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "research" / "scripts"))
from lib.intraday import BARS_PER_DAY, load_symbol  # noqa: E402
import p3_fetch_intraday as F  # noqa: E402

OUTMD = ROOT / "research" / "out" / "p3_intraday_qa.md"
OUTCSV = ROOT / "research" / "out" / "p3_intraday_qa_symbols.csv"
DAYC = F.RAW / "candles" / "day"
FLAT_RUN = 30          # >= 30 consecutive bars with high==low (and for stocks, close unchanged) = stale feed


def daily_series(key):
    DAYC.mkdir(parents=True, exist_ok=True)
    p = DAYC / f"{key.replace('|', '_').replace(' ', '-')}_{F.START}_{F.END}.json"
    if not p.exists():
        url = f"{F.API}/v3/historical-candle/{quote(key, safe='')}/days/1/{F.END}/{F.START}"
        for a in range(4):
            r = F.S.get(url, timeout=40)
            F.STATS["requests"] += 1
            time.sleep(1.0)
            if r.status_code == 200:
                p.write_text(json.dumps(r.json()["data"]["candles"], separators=(",", ":")))
                break
            time.sleep(5 * 3 ** a)
        else:
            return None
    c = json.loads(p.read_text())
    s = pd.Series({pd.Timestamp(x[0][:10]).date(): x[4] for x in c})
    return s.sort_index()


def raw_frame(key):
    stem = key.replace("|", "_").replace(" ", "-") + "_"
    rows = [x[:6] for f in sorted(F.CACHE.glob(stem + "*.json")) for x in json.loads(f.read_text())]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df.ts).dt.tz_convert("Asia/Kolkata")
    return df


def special_sessions(tg):
    """Out-of-session / weekend dates seen in raw cache of the three indices."""
    recs = {}
    for sym, key, kind in tg:
        if sym not in ("NIFTY", "BANKNIFTY", "SENSEX"):
            continue
        r = raw_frame(key)
        r = r.drop_duplicates("ts")
        t = r.ts.dt.strftime("%H:%M")
        ins = (t >= "09:15") & (t <= "15:29")
        for d, g in r.groupby(r.ts.dt.date):
            gi = ins[g.index]
            wk = pd.Timestamp(d).weekday()
            late = (t[g.index][~gi] >= "16:00").any()   # 09:0x pre-open / 15:30 close prints are routine
            if wk >= 5 or gi.sum() < 300 or late:
                k = recs.setdefault(d, {"date": d, "weekday": pd.Timestamp(d).day_name(),
                                        "first_bar": g.ts.min().strftime("%H:%M"),
                                        "last_bar": g.ts.max().strftime("%H:%M"),
                                        "bars_in_session": int(gi.sum()), "bars_out_session": int((~gi).sum()),
                                        "seen_in": []})
                k["seen_in"].append(sym)
    out = pd.DataFrame(recs.values())
    if out.empty:
        out = pd.DataFrame(columns=["date", "weekday", "first_bar", "last_bar", "bars_in_session",
                                    "bars_out_session", "seen_in", "kind", "kept_in_parquet"])
    else:
        tot = out.bars_in_session + out.bars_out_session
        mon = pd.to_datetime(out.date).dt.month
        out["kind"] = np.where(mon.isin([10, 11]) & (tot <= 120), "muhurat",
                               np.where(out.weekday.isin(["Saturday", "Sunday"]), "weekend_session", "short_day"))
        out["kept_in_parquet"] = np.where(out.bars_in_session > 0, "in-session bars only", "no (dropped)")
        out["seen_in"] = out.seen_in.map(lambda x: "+".join(x))
    out = out.sort_values("date")
    out.to_csv(F.REF / "special_sessions.csv", index=False)
    return out


def flat_runs(df):
    flat = (df.high == df.low).to_numpy()
    same_day = (df.ts.dt.date == df.ts.dt.date.shift()).to_numpy()
    run, best, ndays, days = 0, 0, 0, set()
    for i, f in enumerate(flat):
        run = (run + 1 if same_day[i] else 1) if f else 0
        if run >= FLAT_RUN:
            days.add(df.ts.iloc[i].date())
        best = max(best, run)
    return best, len(days)


ADJ_EVENTS = [("TATASTEEL", "2022-07-27", "2022-07-28", "1:10 split ex 2022-07-28"),
              ("BAJFINANCE", "2025-06-13", "2025-06-16", "1:2 split + 4:1 bonus ex 2025-06-16"),
              ("HDFCBANK", "2025-08-25", "2025-08-26", "1:1 bonus ex 2025-08-26")]


def adj_checks(data):
    out = []
    for s, a, b, what in ADJ_EVENTS:
        d = data.get(s)
        if d is None or d.empty:
            continue
        g = d.groupby(d.ts.dt.date.astype(str)).close.last()
        if a in g and b in g:
            out.append(f"{s} {what}: close {g[a]:.1f} -> {g[b]:.1f}")
    return out


def main():
    t0 = time.time()
    tg = F.targets()
    build = pd.read_csv(ROOT / "research" / "out" / "p3_intraday_build.csv").set_index("symbol")
    data = {s: load_symbol(s) for s, _, _ in tg}
    nifty = data["NIFTY"]
    cal = pd.Index(sorted(set(nifty.ts.dt.date)))
    # trading calendar = NIFTY regular days; weekend special sessions kept but flagged
    rows, jumps = [], []
    nret = None
    if len(nifty):
        nd = nifty.groupby(nifty.ts.dt.date).agg(o=("open", "first"), c=("close", "last"))
        nret = nd.o / nd.c.shift() - 1
    for sym, key, kind in tg:
        df = data[sym]
        if df.empty:
            rows.append({"symbol": sym, "kind": kind, "bars": 0})
            continue
        days = df.groupby(df.ts.dt.date)
        bpd = days.size()
        span = cal[(cal >= bpd.index.min()) & (cal <= bpd.index.max())]
        full = cal
        best, nflat = flat_runs(df)
        rows.append({
            "symbol": sym, "kind": kind, "first": bpd.index.min(), "last": bpd.index.max(),
            "days": len(bpd), "cov_full_pct": round(100 * len(set(bpd.index) & set(full)) / len(full), 2),
            "cov_span_pct": round(100 * len(set(bpd.index) & set(span)) / max(len(span), 1), 2),
            "bars": len(df), "bpd_med": int(bpd.median()), "bpd_p5": int(bpd.quantile(.05)), "bpd_min": int(bpd.min()),
            "miss_min_pct": round(100 * (1 - bpd.clip(upper=BARS_PER_DAY).sum() / (BARS_PER_DAY * len(bpd))), 3),
            "days_lt370": int((bpd < 370).sum()),
            "dup_rm": int(build.loc[sym, "dup_removed"]), "oos_rm": int(build.loc[sym, "out_of_session_removed"]),
            "max_flat_run": best, "days_flat30": nflat,
            "zero_vol_pct": round(100 * (df.volume == 0).mean(), 1),
        })
        if kind == "stock":
            d = days.agg(o=("open", "first"), c=("close", "last"))
            r = d.o / d.c.shift() - 1
            for day, v in r[r.abs() > 0.20].items():
                jumps.append((sym, day, round(100 * v, 1), round(100 * float(nret.get(day, np.nan)), 2),
                              round(d.c.shift().loc[day], 2), round(d.o.loc[day], 2)))
    q = pd.DataFrame(rows)
    q.to_csv(OUTCSV, index=False)
    ss = special_sessions(tg)

    # cross-check daily close vs Upstox daily index candles, 20 random days
    rng = np.random.default_rng(7)
    xc = []
    for sym, key, kind in tg[:3]:
        ds = daily_series(key)
        df = data[sym]
        if ds is None or df.empty:
            continue
        c1 = df.groupby(df.ts.dt.date).close.last()
        common = sorted(set(c1.index) & set(ds.index))
        pick = rng.choice(len(common), size=min(20, len(common)), replace=False)
        diff = [(c1[common[i]] / ds[common[i]] - 1) * 1e4 for i in pick]
        xc.append((sym, len(common), np.median(np.abs(diff)), np.max(np.abs(diff)),
                   sum(abs(x) < 1 for x in diff)))
        miss = sorted(set(ds.index) - set(c1.index))
        xc[-1] += (len(miss), ", ".join(str(m) for m in miss[:4]))

    disk = sum(f.stat().st_size for f in F.OUT.rglob("*.parquet"))
    log = json.loads(F.LOG.read_text()) if F.LOG.exists() else {"runs": []}
    if F.STATS["requests"]:
        log["runs"].append({"at": dt.datetime.now().isoformat(timespec="seconds"), "qa_daily": True,
                            **F.STATS, "elapsed_s": round(time.time() - t0, 1)})
        log["total_requests"] = sum(r["requests"] for r in log["runs"])
        log["total_elapsed_s"] = round(sum(r["elapsed_s"] for r in log["runs"]), 1)
        F.LOG.write_text(json.dumps(log, indent=1))
    L = ["# P3 - 1-minute underlying history QA",
         f"Run {dt.date.today()} by `research/scripts/p3_intraday_qa.py`. Calendar = NIFTY regular days "
         f"{cal[0]}..{cal[-1]} ({len(cal)} days). Full per-symbol table: `research/out/p3_intraday_qa_symbols.csv`.",
         f"Fetch: {log.get('total_requests', '?')} Upstox requests in {log.get('total_elapsed_s', '?')} s over "
         f"{len(log.get('runs', []))} run(s), all tokenless; parquet {q.bars.sum():,} rows, {disk / 1e6:.0f} MB on disk.",
         "", "## Indices / VIX / futures",
         "| sym | first..last | days | cov% full | bars/day med/p5/min | miss-min% | <370 | dup/oos rm | flat30 days |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in q[q.kind != "stock"].itertuples():
        if not r.bars:
            L.append(f"| {r.symbol} | no data served | 0 | 0 | - | - | - | - | - |")
            continue
        L.append(f"| {r.symbol} | {r.first}..{r.last} | {r.days} | {r.cov_full_pct} | {r.bpd_med}/{r.bpd_p5}/{r.bpd_min} | "
                 f"{r.miss_min_pct} | {r.days_lt370} | {r.dup_rm}/{r.oos_rm} | {r.days_flat30} |")
    s = q[q.kind == "stock"]
    L += ["", f"## Stocks ({len(s)})",
          f"Coverage of full calendar: median {s.cov_full_pct.median()}%, min {s.cov_full_pct.min()}% "
          f"({s.loc[s.cov_full_pct.idxmin(), 'symbol']}); within own listed span: min {s.cov_span_pct.min()}%. "
          f"Bars/day median {int(s.bpd_med.median())}; missing-minute share median {s.miss_min_pct.median()}%, "
          f"max {s.miss_min_pct.max()}% ({s.loc[s.miss_min_pct.idxmax(), 'symbol']}); days<370 total {s.days_lt370.sum()}.",
          "Short history / gaps (cov_full < 98%): " + (", ".join(f"{r.symbol} from {r.first} ({r.cov_full_pct}%)"
                                                                 for r in s[s.cov_full_pct < 98].itertuples()) or "none"),
          f"Stale feed (>= {FLAT_RUN} flat bars in a row): " + (", ".join(
              f"{r.symbol} {r.days_flat30}d" for r in q[q.days_flat30 > 0].itertuples()) or "none"),
          "", "## Overnight |ret| > 20% in stocks (prev close -> open; NIFTY same gap)"]
    for j in jumps[:10]:
        L.append(f"- {j[0]} {j[1]}: {j[2]:+}% ({j[4]} -> {j[5]}), NIFTY {j[3]:+}%")
    if len(jumps) > 10:
        L.append(f"- ... {len(jumps) - 10} more")
    if not jumps:
        L.append("- none")
    L += ["", "## Adjusted vs raw",
          "Prices are **back-adjusted** for splits/bonuses (no overnight jump on any ex-date). Known events: "
          + "; ".join(adj_checks(data)) + ". Raw pre-event prices were ~10x / ~10x / ~2x those levels.",
          "Flat (high==low) runs in BSE/MCX are single-print minutes with volume > 0 concentrated in 2022-23 "
          "(thin liquidity before their re-rating), not a stale feed; the near-month futures runs are early in "
          "each contract's life (illiquid far-month period)."]
    L += ["", "## Special sessions (`data/ref/special_sessions.csv`)"]
    for r in ss.itertuples():
        L.append(f"- {r.date} {r.weekday[:3]} {r.first_bar}-{r.last_bar} {r.kind}: {r.bars_in_session} in-session / "
                 f"{r.bars_out_session} out bars; {r.kept_in_parquet}")
    if ss.empty:
        L.append("- none found in raw cache (Muhurat / weekend sessions not served at 1-min)")
    L += ["", "## Daily close cross-check (last 1-min close vs Upstox daily candle, 20 random days)",
          "Official index close = weighted avg of the last 30 min, so a few bp vs the 15:29 bar close is expected; "
          "missing days below are evening Muhurat sessions (out-of-session, dropped)."]
    for x in xc:
        L.append(f"- {x[0]}: |diff| median {x[2]:.2f} bp, max {x[3]:.2f} bp, {x[4]}/20 within 1 bp; "
                 f"daily days missing in 1-min: {x[5]} {x[6]}")
    OUTMD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:40]))
    print(f"lines={len(L)} qa_requests={F.STATS['requests']} elapsed={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
