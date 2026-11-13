"""P1: inventory of the xerxes-eod-archive (reads the Parquet cache + archive state/).

Usage:
  python -I research/scripts/p1_archive_inventory.py --archive _ext/xerxes-eod-archive \
      --cache data/archive --out research/out
Writes research/out/p1_archive_inventory.json and research/out/p1_archive_inventory.md.
"""
import argparse
import datetime as dt
import json
import os

import numpy as np
import pandas as pd

# Fallback NSE trading holidays (from api.upstox.com/v2/market/holidays, fetched 2026-11-13),
# restricted to Aug-Dec 2026. Used only if the live fetch fails.
FALLBACK_HOLIDAYS = ["2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24", "2026-12-25"]
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def nse_holidays():
    try:
        import requests
        r = requests.get("https://api.upstox.com/v2/market/holidays", timeout=20)
        data = r.json().get("data", [])
        hol = sorted({x["date"] for x in data if x.get("holiday_type") == "TRADING_HOLIDAY"
                      and "NSE" in (x.get("closed_exchanges") or [])})
        if hol:
            return hol, "upstox api (live)"
    except Exception:
        pass
    return FALLBACK_HOLIDAYS, "embedded fallback list"


def q(x, p):
    x = pd.Series(x).dropna()
    return float(np.quantile(x, p)) if len(x) else None


def r3(v):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), 3)


def pct(mask):
    return round(100.0 * float(np.mean(mask)), 1) if len(mask) else None


def hhmm(mins):
    return f"{int(mins // 60):02d}:{int(mins % 60):02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    c = pd.read_parquet(os.path.join(a.cache, "chain.parquet"))
    s = pd.read_parquet(os.path.join(a.cache, "snap.parquet"))
    ev = pd.read_parquet(os.path.join(a.cache, "events.parquet"))
    for col in ("trade_date", "symbol", "cls", "expiry", "type"):
        c[col] = c[col].astype(str)
    R = {}
    md = []

    # ---------------- 1. Days ----------------
    days = sorted(s.trade_date.unique())
    hol, hol_src = nse_holidays()
    rng = pd.bdate_range(days[0], days[-1])
    tdays = [d.strftime("%Y-%m-%d") for d in rng if d.strftime("%Y-%m-%d") not in hol]
    missing = [d for d in tdays if d not in days]
    on_holiday = [d for d in days if d in hol or pd.Timestamp(d).weekday() >= 5]
    md_path = os.path.join(a.archive, "state", "missed-days.json")
    missed_state = json.load(open(md_path)) if os.path.exists(md_path) else None
    s["mins"] = pd.to_datetime(s.asof_ist).dt.hour * 60 + pd.to_datetime(s.asof_ist).dt.minute
    tod = {}
    for cls, g in s[s.asof_date_ok].groupby("cls"):
        tod[cls] = {k: hhmm(q(g.mins, p)) for k, p in (("min", 0), ("p10", .1), ("median", .5), ("p90", .9), ("max", 1))}
    nonpost = s[~s.post_close]
    stale_files = s[~s.asof_date_ok].groupby("symbol").agg(n=("trade_date", "size"), first=("trade_date", "min"),
                                                           last=("trade_date", "max"), asof=("asof_ist", "min"))
    dup_by_day = s.groupby("trade_date").dup_prev.mean()
    dup_days = sorted(dup_by_day[dup_by_day > 0.5].index)
    n_dup_other = int(s[~s.trade_date.isin(dup_days)].dup_prev.sum())
    R["days"] = dict(n=len(days), first=days[0], last=days[-1], holidays_source=hol_src,
                     holidays_in_range=[h for h in hol if days[0] <= h <= days[-1]],
                     n_trading_days_in_range=len(tdays), missing=missing, n_missing=len(missing),
                     archived_on_holiday=on_holiday, whole_day_duplicates=dup_days,
                     missed_days_state=missed_state, asof_ist_tod=tod,
                     n_snapshots_not_post_close=int(len(nonpost)),
                     stale_symbol_files=stale_files.reset_index().to_dict("records"),
                     n_symbol_dup_prev_other_days=n_dup_other,
                     n_usable_snaps=int(s.usable.sum()), n_snaps=len(s))
    R["days"]["n_clean_days"] = len([d for d in days if d not in dup_days])
    md += ["# P1 — xerxes-eod-archive inventory", "",
           f"Cache: `data/archive/{{chain,snap,events}}.parquet` · chain rows **{len(c):,}** · snap rows {len(s):,} · event rows {len(ev):,}", "",
           "## 1. Days",
           f"- **{len(days)} day-folders**, {days[0]} → {days[-1]}; {len(tdays)} NSE trading days in range (holidays: {hol_src}).",
           f"- **Missing trading days: {len(missing)}** — {', '.join(missing)}. `state/missed-days.json` = {json.dumps(missed_state)[:60]} (gap unrecorded; README attributes Aug-26→Sep-8 to the >100 MB DB push failure).",
           f"- **Folders on NSE holidays (whole-day re-publish of prior close): {', '.join(on_holiday) or 'none'}**; whole-day duplicate days detected (>50% symbols with unchanged close): {', '.join(dup_days)}. → **{R['days']['n_clean_days']} distinct trading sessions.**",
           f"- asOf IST (median, p10–p90): index {tod['index']['median']} ({tod['index']['p10']}–{tod['index']['p90']}), stock {tod['stock']['median']} ({tod['stock']['p10']}–{tod['stock']['p90']}); range {tod['stock']['min']}–{tod['stock']['max']}. All are post-close (not ~16:05: upstream builds run late); indices on 10-05/10-12 stamped 00:52/01:28 IST next day (still pre-open, accepted).",
           f"- Pre-close snapshots: {len(nonpost[nonpost.asof_date_ok]) if len(nonpost) else 0}. Frozen per-symbol files (asOf from an earlier day): " +
           "; ".join(f"{r.symbol} ×{r.n} ({r.first}→{r.last}, asOf {r.asof[:10]})" for r in stale_files.reset_index().itertuples()) + ".",
           f"- Other symbol-days with unchanged close vs prior day: {n_dup_other}. `snap.usable` (post-close, fresh, non-dup) = {int(s.usable.sum()):,}/{len(s):,}.", ""]

    # ---------------- 2. Instruments ----------------
    per_day = s.groupby(["trade_date", "cls"]).size().unstack(fill_value=0)
    life = s.groupby(["symbol", "cls"]).trade_date.agg(["min", "max", "size"]).reset_index()
    enter = life[life["min"] > days[0]]
    leave = life[life["max"] < days[-1]]
    gappy = life[life["size"] < life.apply(lambda r: sum(1 for d in days if r["min"] <= d <= r["max"]), axis=1)]
    R["instruments"] = dict(index_symbols=sorted(s[s.cls == "index"].symbol.unique()),
                            stocks_per_day=dict(min=int(per_day.stock.min()), median=float(per_day.stock.median()), max=int(per_day.stock.max()),
                                                first_day=int(per_day.stock.iloc[0]), last_day=int(per_day.stock.iloc[-1])),
                            indices_per_day=sorted(per_day["index"].unique().tolist()),
                            distinct_total=int(s.symbol.nunique()), distinct_stocks=int((life.cls == "stock").sum()),
                            entered=enter.groupby("min").symbol.apply(list).to_dict(),
                            left=leave.groupby("max").symbol.apply(list).to_dict(),
                            with_internal_gaps=gappy.symbol.tolist())
    md += ["## 2. Instruments",
           f"- Indices/day: {R['instruments']['indices_per_day']} ({', '.join(R['instruments']['index_symbols'])}); stocks/day min {per_day.stock.min()} / median {per_day.stock.median():.0f} / max {per_day.stock.max()} (first day {per_day.stock.iloc[0]}, last {per_day.stock.iloc[-1]}).",
           f"- Distinct symbols: **{s.symbol.nunique()}** ({(life.cls=='stock').sum()} stocks + 3 indices).",
           "- Entered after day 1: " + "; ".join(f"{k}: {len(v)} ({', '.join(v[:6])}{'…' if len(v) > 6 else ''})" for k, v in R["instruments"]["entered"].items()),
           "- Left before last day: " + ("; ".join(f"{k}: {', '.join(v)}" for k, v in R["instruments"]["left"].items()) or "none"),
           f"- Symbols with internal gaps: {len(gappy)} {', '.join(gappy.symbol.tolist()[:10])}", ""]

    # ---------------- 3. Expiries ----------------
    ex = c[["trade_date", "symbol", "cls", "expiry", "dte"]].drop_duplicates()
    ex["rank"] = ex.groupby(["trade_date", "symbol"]).expiry.rank(method="dense").astype(int)
    c = c.merge(ex[["trade_date", "symbol", "expiry", "rank"]], on=["trade_date", "symbol", "expiry"], how="left")
    c["rk"] = np.where(c["rank"] >= 3, "3+", c["rank"].astype(str))
    nexp = s.groupby("cls").n_expiries.value_counts().unstack(fill_value=0)
    dte_tab = ex.assign(rk=np.where(ex["rank"] >= 3, "3+", ex["rank"].astype(str))).groupby(["cls", "rk"]).dte.describe()[["min", "50%", "max"]]
    ue = ex[["symbol", "cls", "expiry"]].drop_duplicates()
    ue["wd"] = pd.to_datetime(ue.expiry).dt.weekday.map(lambda i: WD[i])
    wd_idx = {sym: g.wd.value_counts().to_dict() for sym, g in ue[ue.cls == "index"].groupby("symbol")}
    stock_exp = ue[ue.cls == "stock"].drop_duplicates("expiry")
    off = []
    for sym, g in ue[ue.cls == "index"].groupby("symbol"):
        mode = g.wd.mode()[0]
        off += [f"{sym} {e} ({w})" for e, w in zip(g.expiry, g.wd) if w != mode]
    smode = stock_exp.wd.mode()[0]
    off += [f"stocks {e} ({w})" for e, w in zip(stock_exp.expiry, stock_exp.wd) if w != smode]
    idx_exp_lists = {sym: sorted(g.expiry) for sym, g in ue[ue.cls == "index"].groupby("symbol")}
    lots = s[s.cls == "index"].groupby(["symbol", "lotSize"]).trade_date.agg(["min", "max"]).reset_index()
    lot_chg = s[s.cls == "stock"].groupby("symbol").lotSize.nunique()
    lot_chg_syms = lot_chg[lot_chg > 1]
    lot_chg_dates = s[s.symbol.isin(lot_chg_syms.index)].sort_values("trade_date")
    lot_chg_dates = lot_chg_dates[lot_chg_dates.groupby("symbol").lotSize.diff().fillna(0) != 0].trade_date.value_counts().to_dict()
    R["expiries"] = dict(n_expiries_per_snapshot={k: v for k, v in nexp.to_dict("index").items()},
                         dte_by_rank={f"{k[0]}|{k[1]}": v for k, v in dte_tab.to_dict("index").items()},
                         index_expiry_weekdays=wd_idx, stock_expiry_weekdays=stock_exp.wd.value_counts().to_dict(),
                         stock_expiries=sorted(stock_exp.expiry), index_expiries=idx_exp_lists, off_weekday_expiries=off,
                         index_lots=lots.to_dict("records"), n_stocks_lot_changed=int(len(lot_chg_syms)),
                         stock_lot_change_dates=lot_chg_dates)
    md += ["## 3. Expiries",
           "| class | expiries/snapshot (count of snapshots) | DTE rank1 min/med/max | rank2 | rank3+ |", "|---|---|---|---|---|"]
    for cls in ("index", "stock"):
        cells = []
        for rk in ("1", "2", "3+"):
            if (cls, rk) in dte_tab.index:
                r = dte_tab.loc[(cls, rk)]
                cells.append(f"{r['min']:.0f}/{r['50%']:.0f}/{r['max']:.0f}")
            else:
                cells.append("—")
        md.append(f"| {cls} | {', '.join(f'{k}:{v}' for k, v in nexp.loc[cls].items() if v)} | " + " | ".join(cells) + " |")
    md += ["",
           "- Expiry weekdays (distinct expiries seen): " + "; ".join(f"{k} {v}" for k, v in wd_idx.items()) + f"; stocks {stock_exp.wd.value_counts().to_dict()} ({', '.join(sorted(stock_exp.expiry))}).",
           f"- Off-weekday (holiday-shifted) expiries: {', '.join(off) or 'none'}.",
           "- BANKNIFTY expiries: " + ", ".join(idx_exp_lists.get("BANKNIFTY", [])) + " → monthly-only (last Tue).",
           "- Index lot sizes: " + "; ".join(f"{r['symbol']} {r['lotSize']:.0f} ({r['min']}→{r['max']})" for r in lots.to_dict("records")) + ".",
           f"- Stocks whose lotSize changed in-window: **{len(lot_chg_syms)}** (change dates: {lot_chg_dates}).", ""]

    # ---------------- 4. Chain completeness ----------------
    c["two_sided"] = (c.bid > 0) & (c.ask > 0) & (c.ask >= c.bid)
    bid_abs_day = c.groupby("trade_date").bid.apply(lambda x: x.isna().mean())
    nobid_days = sorted(bid_abs_day[bid_abs_day > 0.999].index)
    c["bidday"] = ~c.trade_date.isin(nobid_days)
    rows = []
    md += ["## 4. Chain completeness (all archived days)",
           "| class | rank | strikes/expiry med | rows | ltp>0 | iv | bid&ask>0 (all days) | bid&ask>0 (bid-days) | bid key absent (bid-days) | vol>0 | oi>0 | delta | strike lo/hi % spot (med) |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    per_exp = c.groupby(["cls", "rk", "trade_date", "symbol", "expiry"]).agg(
        n_str=("strike", "nunique"), lo=("strike", "min"), hi=("strike", "max"), spot=("spot", "first")).reset_index()
    for (cls, rk), g in c.groupby(["cls", "rk"]):
        pe = per_exp[(per_exp.cls == cls) & (per_exp.rk == rk)]
        r = dict(cls=cls, rank=rk, strikes_med=float(pe.n_str.median()), rows=int(len(g)),
                 ltp_pos=pct(g.ltp > 0), iv=pct(g.iv.notna()), two_sided=pct(g.two_sided), vol_pos=pct(g.volume > 0),
                 oi_pos=pct(g.oi > 0), delta=pct(g.delta.notna()), bid_absent=pct(g.bid[g.bidday].isna()),
                 two_sided_biddays=pct(g.two_sided[g.bidday]),
                 lo_pct_spot=r3(100 * (pe.lo / pe.spot).median()), hi_pct_spot=r3(100 * (pe.hi / pe.spot).median()))
        rows.append(r)
        md.append(f"| {cls} | {rk} | {r['strikes_med']:.0f} | {r['rows']:,} | {r['ltp_pos']} | {r['iv']} | {r['two_sided']} | {r['two_sided_biddays']} | {r['bid_absent']} | {r['vol_pos']} | {r['oi_pos']} | {r['delta']} | {r['lo_pct_spot']:.0f}/{r['hi_pct_spot']:.0f} |")
    iv_na_itm = pct(c.loc[c.iv.isna(), "delta"].abs() >= 0.99) if c.iv.isna().any() else None
    R["chain_completeness"] = rows
    R["chain_no_bidask_days"] = nobid_days
    bd_sessions = [d for d in days if d not in nobid_days and d not in dup_days]
    R["sessions_with_bidask"] = len(bd_sessions)
    far = c[c.bidday & c.bid.isna()]
    far_ad = r3(far.delta.abs().median()) if len(far) else None
    md += ["", f"- **`bid`/`ask` keys do not exist at all on {len(nobid_days)} days ({nobid_days[0]}→{nobid_days[-1]})** → two-sided quotes only from {bd_sessions[0]}: **{len(bd_sessions)} distinct sessions with a book**. On bid-days the keys are absent on {pct(far.index.size and c[c.bidday].bid.isna())}% of rows (median abs(delta) of those {far_ad}: deep ITM/OTM wings). Otherwise chain keys exactly as briefed (incl. prevOi). iv null rows: {pct(c.iv.isna())}% of rows, of which {iv_na_itm}% have |delta|≥0.99 (delta is then a placeholder ±1/0, not a model value).",
           "- **Truncated around ATM, not the full exchange chain**: median strike span is the lo/hi % of spot in the table (≈ 87–114% index, 82–122% stock nearest expiry); farther wings are not archived.", ""]

    # ---------------- 5. Spreads ----------------
    t = c[c.two_sided].copy()
    t["mid"] = (t.bid + t.ask) / 2
    t["rel"] = (t.ask - t.bid) / t.mid
    t["ad"] = t.delta.abs()
    t["dbk"] = pd.cut(t.ad, [0.05, 0.15, 0.25, 0.40, 0.60, 1.0001], right=False,
                      labels=["0.05-0.15", "0.15-0.25", "0.25-0.40", "0.40-0.60", ">0.60"])
    t["pbk"] = pd.cut(t.mid, [0, 5, 20, 50, 200, np.inf], right=False, labels=["<5", "5-20", "20-50", "50-200", ">200"])
    t["rk2"] = np.where(t.rk == "1", "1", np.where(t.rk == "2", "2", "3+"))
    sp = t.groupby(["cls", "dbk", "rk2"], observed=True).rel.agg(
        med="median", p75=lambda x: x.quantile(.75), p90=lambda x: x.quantile(.9), n="size")
    R["spread_by_delta"] = {f"{k[0]}|{k[1]}|{k[2]}": {kk: r3(vv) for kk, vv in v.items()} for k, v in sp.to_dict("index").items()}
    md += ["## 5. Spread reality (two-sided rows; rel = (ask−bid)/mid, med / p75 / p90, n)",
           "| class | abs(delta) | nearest | 2nd | 3rd+ |", "|---|---|---|---|---|"]
    for cls in ("index", "stock"):
        for db in ["0.05-0.15", "0.15-0.25", "0.25-0.40", "0.40-0.60", ">0.60"]:
            cells = []
            for rk in ("1", "2", "3+"):
                k = (cls, db, rk)
                cells.append(f"{sp.loc[k,'med']:.3f} / {sp.loc[k,'p75']:.3f} / {sp.loc[k,'p90']:.3f} ({int(sp.loc[k,'n']):,})" if k in sp.index else "—")
            md.append(f"| {cls} | {db} | " + " | ".join(cells) + " |")
    pp = t.groupby(["cls", "pbk"], observed=True).rel.agg(med="median", p75=lambda x: x.quantile(.75), p90=lambda x: x.quantile(.9), n="size")
    R["spread_by_premium"] = {f"{k[0]}|{k[1]}": {kk: r3(vv) for kk, vv in v.items()} for k, v in pp.to_dict("index").items()}
    md += ["", "| class | <₹5 | 5–20 | 20–50 | 50–200 | >200 |", "|---|---|---|---|---|---|"]
    for cls in ("index", "stock"):
        md.append(f"| {cls} | " + " | ".join(
            f"{pp.loc[(cls,b),'med']:.3f}/{pp.loc[(cls,b),'p90']:.3f}" if (cls, b) in pp.index else "—"
            for b in ["<5", "5-20", "20-50", "50-200", ">200"]) + " |")
    atm = t[(t.ad >= 0.4) & (t.ad < 0.6) & (t.rk == "1")]
    by_idx = atm[atm.cls == "index"].groupby("symbol").rel.median().round(4).to_dict()
    st_sym = atm[atm.cls == "stock"].groupby("symbol").rel.median()
    out_band = t.groupby("cls")[["ltp", "bid", "ask", "volume"]].apply(lambda g: pct((g.ltp > 0) & ((g.ltp < g.bid) | (g.ltp > g.ask))))
    out_band_v0 = t.groupby("cls")[["ltp", "volume"]].apply(lambda g: pct((g.ltp > 0) & (g.volume == 0)))
    R["spread_atm_nearest"] = dict(index_by_symbol=by_idx, stock_symbol_median_quartiles=[r3(st_sym.quantile(x)) for x in (.25, .5, .75)],
                                   ltp_outside_bidask_pct=out_band.to_dict(), ltp_pos_volume0_pct=out_band_v0.to_dict())
    md += ["(cells: median/p90 of rel spread by mid-premium bucket)", "",
           f"- Near-ATM (|Δ| 0.40–0.60, nearest expiry) median rel spread: {', '.join(f'{k} {v:.2%}' for k, v in by_idx.items())}; stocks: per-symbol medians q25/q50/q75 = {', '.join(f'{x:.2%}' for x in st_sym.quantile([.25,.5,.75]))}.",
           f"- Stale-print indicator — ltp outside [bid, ask]: index {out_band.get('index')}%, stock {out_band.get('stock')}% of two-sided rows; ltp>0 with volume=0 (never traded that session): index {out_band_v0.get('index')}%, stock {out_band_v0.get('stock')}%.", ""]

    # ---------------- 6. Path continuity ----------------
    clean_days = [d for d in days if d not in dup_days]
    tset = [d for d in tdays]
    tpos = {d: i for i, d in enumerate(tset)}
    usable_keys = s[s.usable][["trade_date", "symbol"]]
    q2 = t.merge(usable_keys, on=["trade_date", "symbol"])
    q2 = q2[q2.trade_date.isin(clean_days)]
    key = ["symbol", "expiry", "strike", "type"]
    ent = q2[(q2.ad >= 0.2) & (q2.ad <= 0.6) & (q2["rank"] <= 2)][["trade_date", "cls"] + key]
    quoted = set(map(tuple, q2[["trade_date"] + key].astype(str).values))
    archived = set(map(tuple, usable_keys.astype(str).values))
    cont = {}
    ent_s = ent.astype({"strike": str})
    for k in (1, 2, 3, 5):
        res = {"index": [0, 0, 0], "stock": [0, 0, 0]}  # [eligible, still quoted, unobservable]
        for row in ent_s.itertuples(index=False):
            i = tpos.get(row.trade_date)
            if i is None or i + k >= len(tset):
                continue
            dk = tset[i + k]
            if dk >= row.expiry:  # expired (or expiry day itself) by t+k
                continue
            if (dk, row.symbol) not in archived:
                res[row.cls][2] += 1
                continue
            res[row.cls][0] += 1
            res[row.cls][1] += (dk, row.symbol, row.expiry, row.strike, row.type) in quoted
        cont[f"t+{k}"] = {cls: dict(eligible=v[0], quoted_pct=round(100 * v[1] / v[0], 1) if v[0] else None, unobservable=v[2])
                          for cls, v in res.items()}
    n_opp = ent.groupby("cls").size().to_dict()
    n_opp_contracts = ent.drop_duplicates(key).groupby("cls").size().to_dict()
    near = ex[ex["rank"] == 1].merge(usable_keys, on=["trade_date", "symbol"])
    cycles = {sym: int(g.expiry.nunique()) for sym, g in near[near.cls == "index"].groupby("symbol")}
    cycles["stocks(monthly)"] = int(near[near.cls == "stock"].expiry.nunique())
    R["path_continuity"] = dict(definition="|delta| 0.20-0.60, expiry rank<=2, two-sided on t (usable snap, clean day); t+k = k-th NSE trading day; excluded if t+k >= expiry; unobservable = t+k not archived",
                                continuity=cont, entry_opportunities=n_opp, distinct_contracts=n_opp_contracts,
                                nearest_expiry_cycles=cycles)
    md += ["## 6. Path continuity (swing-test sample)",
           "Entries: |Δ| 0.20–0.60, nearest two expiries, two-sided quote on t (usable snapshot). t+k = k-th NSE trading day; contracts expiring on/before t+k excluded; t+k not archived = unobservable.",
           "| horizon | index eligible | index still two-sided | index unobservable | stock eligible | stock still two-sided | stock unobservable |",
           "|---|---|---|---|---|---|---|"]
    for k, v in cont.items():
        md.append(f"| {k} | {v['index']['eligible']:,} | {v['index']['quoted_pct']}% | {v['index']['unobservable']:,} | {v['stock']['eligible']:,} | {v['stock']['quoted_pct']}% | {v['stock']['unobservable']:,} |")
    md += ["", f"- **Entry opportunities (symbol, contract, entry-day): index {n_opp.get('index',0):,}, stock {n_opp.get('stock',0):,}** (distinct contracts: index {n_opp_contracts.get('index',0):,}, stock {n_opp_contracts.get('stock',0):,}).",
           f"- Distinct nearest-expiry cycles observed: {', '.join(f'{k} {v}' for k, v in cycles.items())}. These overlapping rows are highly correlated — the independent sample is closer to the cycle count × underlyings.", ""]

    # ---------------- 7. Auxiliary ----------------
    sh = s.groupby("cls").spot_hist_len.describe()[["min", "50%", "max"]]
    hist_keys = s.spot_hist_keys.dropna().unique().tolist()
    ivh = s.groupby(["cls", "trade_date"]).ivhist_len.median().unstack(0)
    futcov = s.groupby("cls").agg(price=("fut_price", lambda x: pct(x.notna())), oi=("fut_oi", lambda x: pct(x.notna())),
                                  basis=("fut_basisPts", lambda x: pct(x.notna())), expiry=("fut_expiry", lambda x: pct(x.notna())))
    lab = s[s.usable].groupby("cls").structure_label.value_counts(normalize=True).mul(100).round(1)
    term_reg = s[s.cls == "stock"].term_regime.value_counts(normalize=True).mul(100).round(1).to_dict()
    evu = ev.drop_duplicates(["symbol", "kind", "event_date", "title"])
    kinds = evu.groupby(["cls", "kind"]).size().sort_values(ascending=False)
    in_win = evu[evu.event_date.notna() & (evu.event_date >= days[0]) & (evu.event_date <= days[-1])]
    res = in_win[in_win.kind.str.contains("Result|Earnings", case=False, na=False) & (in_win.cls == "stock")]
    res_pairs = res.drop_duplicates(["symbol", "event_date"])
    res_nse = res[res.source == "nse"].drop_duplicates(["symbol", "event_date"])
    res_nse_approx = res[(res.source == "nse") & (res.approx == False)].drop_duplicates(["symbol", "event_date"])  # noqa: E712
    mk = evu[evu.cls == "market"].drop_duplicates(["kind", "event_date"])
    R["aux"] = dict(spot_history_len=sh.to_dict("index"), spot_history_item_keys=hist_keys,
                    index_spot_history_first=s[s.cls == "index"].spot_hist_first.min(),
                    ivhist_len_first_last={cls: [r3(ivh[cls].dropna().iloc[0]), r3(ivh[cls].dropna().iloc[-1])] for cls in ivh.columns},
                    future_coverage_pct=futcov.to_dict("index"),
                    structure_labels_pct={f"{k[0]}|{k[1]}": v for k, v in lab.to_dict().items()},
                    stock_term_regime_pct=term_reg,
                    event_kinds={f"{k[0]}|{k[1]}": int(v) for k, v in kinds.items()},
                    results_symbol_events_in_window=int(len(res_pairs)), results_nse_symbol_events=int(len(res_nse)),
                    results_nse_exact_symbol_events=int(len(res_nse_approx)),
                    results_symbols=int(res_pairs.symbol.nunique()),
                    market_events=mk[["kind", "event_date"]].astype(str).values.tolist())
    md += ["## 7. Auxiliary fields",
           f"- `spot.history`: index median {sh.loc['index','50%']:.0f} pts (min {sh.loc['index','min']:.0f}, max {sh.loc['index','max']:.0f}) daily **close-only** (keys {hist_keys}) from {R['aux']['index_spot_history_first']}; stocks {sh.loc['stock','max']:.0f} (empty everywhere). `vix.history` on index files similar.",
           f"- `ivHistory` median length first→last day: " + ", ".join(f"{k} {v[0]:.0f}→{v[1]:.0f}" for k, v in R["aux"]["ivhist_len_first_last"].items()) + ".",
           "- Future coverage % (price/oi/basis/expiry): " + "; ".join(f"{k} {v['price']}/{v['oi']}/{v['basis']}/{v['expiry']}" for k, v in futcov.to_dict("index").items()) + ". Index files have no term/gap/events/outlook; stock `outlook` absent on " + f"{pct(s[s.cls=='stock'].beta.isna())}% of stock snaps.",
           "- Structure labels (usable snaps, %): " + "; ".join(f"{cls}: " + ", ".join(f"{k} {v}" for k, v in lab[cls].items()) for cls in lab.index.get_level_values(0).unique()) + f". Stock term regime %: {term_reg}.",
           "- Event kinds (deduped symbol/kind/date/title): " + ", ".join(f"{k[1]} {v}" for k, v in kinds.head(14).items()) + ".",
           f"- **Results/earnings inside window: {len(res_pairs)} symbol-events across {res_pairs.symbol.nunique()} stocks** (NSE-sourced {len(res_nse)}, of which non-approx {len(res_nse_approx)}; remainder news-derived). Market calendar events: {len(mk)} ({', '.join(sorted(mk.kind.astype(str).unique()))}).", ""]

    # ---------------- 8. DB / state ----------------
    dbp = os.path.join(a.archive, "xerxes.db")
    outp = os.path.join(a.archive, "state", "outcomes.jsonl")
    orows = [json.loads(l) for l in open(outp, encoding="utf-8") if l.strip()] if os.path.exists(outp) else []
    od = pd.DataFrame(orows)
    R["db_state"] = dict(xerxes_db_present=os.path.exists(dbp),
                         outcomes_rows=len(od), outcomes_columns=list(od.columns),
                         outcomes_expiry_range=[od.expiry.min(), od.expiry.max()] if len(od) else None,
                         outcomes_symbols=int(od.symbol.nunique()) if len(od) else 0,
                         outcomes_expiries=int(od.expiry.nunique()) if len(od) else 0)
    md += ["## 8. xerxes.db / state",
           f"- `xerxes.db`: **{'present' if os.path.exists(dbp) else 'not present'}** (gitignored build product, published as a GitHub release asset; not needed — gz files are the source of truth).",
           f"- `state/outcomes.jsonl`: {len(od):,} rows — frozen settlements of upstream *seller* candidates (entry ltp/spot/dte, conviction factors, peak_ltp, MAE, settle_spot, intrinsic/P&L), {R['db_state']['outcomes_expiries']} expiries {R['db_state']['outcomes_expiry_range']}, {R['db_state']['outcomes_symbols']} symbols. Candidate-only, so a biased subset; not a buyer P&L source.",
           f"- `state/missed-days.json`: {json.dumps(missed_state)} (empty despite {len(missing)} missing days).", ""]

    # ---------------- 9. Regime ----------------
    n = s[(s.symbol == "NIFTY") & s.usable].sort_values("trade_date")
    vx = s[s.usable].groupby("trade_date").vix.median()
    # full daily NIFTY closes from the last snapshot's spot.history (covers the missing days)
    import gzip
    last_nifty = os.path.join(a.archive, "archive", days[-1], "indices", "nifty.json.gz")
    with gzip.open(last_nifty, "rt", encoding="utf-8") as f:
        jn = json.load(f)
    h = pd.DataFrame(jn["spot"]["history"]).rename(columns={"t": "d", "v": "close"})
    vh = pd.DataFrame(jn["vix"]["history"]).rename(columns={"t": "d", "v": "vix"})
    h = h.merge(vh, on="d", how="left")
    n_hist_nontrading = int(((h.d >= days[0]) & (h.d <= days[-1]) & ~h.d.isin(tdays)).sum())
    h = h[h.d.isin(tdays)].reset_index(drop=True)
    h["ret"] = h.close.pct_change()
    prev_close = jn["spot"]["history"][[x["t"] for x in jn["spot"]["history"]].index(days[0]) - 1]["v"] if days[0] in [x["t"] for x in jn["spot"]["history"]] else None
    R["regime"] = dict(archived=dict(nifty_min=r3(n.spot_price.min()), nifty_max=r3(n.spot_price.max()), nifty_median=r3(n.spot_price.median()),
                                     vix_min=r3(vx.min()), vix_max=r3(vx.max()), vix_median=r3(vx.median()),
                                     nifty_return_pct=r3(100 * (n.spot_price.iloc[-1] / n.spot_price.iloc[0] - 1)),
                                     days_abs_move_gt1pct=int((n.spot_changePct.abs() > 1).sum()), n=len(n)),
                       full_window_from_history=dict(n_days=len(h), nifty_min=r3(h.close.min()), nifty_max=r3(h.close.max()),
                                                     vix_min=r3(h.vix.min()), vix_max=r3(h.vix.max()),
                                                     ret_pct=r3(100 * (h.close.iloc[-1] / h.close.iloc[0] - 1)),
                                                     days_abs_move_gt1pct=int((h.ret.abs() > 0.01).sum()),
                                                     max_drawdown_pct=r3(100 * (h.close / h.close.cummax() - 1).min()),
                                                     history_points_on_non_trading_days_dropped=n_hist_nontrading))
    ra, rf = R["regime"]["archived"], R["regime"]["full_window_from_history"]
    md += ["## 9. Regime coverage",
           f"- Archived sessions (n={ra['n']}): NIFTY {ra['nifty_min']:.0f}–{ra['nifty_max']:.0f} (median {ra['nifty_median']:.0f}); India VIX {ra['vix_min']}–{ra['vix_max']} (median {ra['vix_median']}); NIFTY first→last {ra['nifty_return_pct']:+.2f}%; |daily move|>1% on {ra['days_abs_move_gt1pct']} days.",
           f"- Full window incl. missing days (NIFTY spot.history on NSE trading days, n={rf['n_days']}; {n_hist_nontrading} weekend/holiday history points dropped): {rf['nifty_min']:.0f}–{rf['nifty_max']:.0f}, VIX {rf['vix_min']}–{rf['vix_max']}, return {rf['ret_pct']:+.2f}%, max drawdown {rf['max_drawdown_pct']:.2f}%, |move|>1% on {rf['days_abs_move_gt1pct']} days.",
           "- Read: one ~3-month window, a single vol regime family; treat any edge found as regime-conditional."]
    with open(os.path.join(a.out, "p1_archive_inventory.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=str)
    with open(os.path.join(a.out, "p1_archive_inventory.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"wrote inventory: md {len(md)} lines; days={len(days)} missing={len(missing)} chain_rows={len(c):,}")
    print(f"two-sided % all rows: {100*c.two_sided.mean():.1f}; entries index/stock: {n_opp}")


if __name__ == "__main__":
    main()
