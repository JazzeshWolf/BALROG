"""P1 Test 2 - synthetic vs real 1-minute option prices on LIVE contracts (BALROG).

For live (unexpired) option contracts we can pull real 1-minute candles from Upstox without
a token. For each trading day d of a contract's life that has a usable archive day p before
it, the synthetic minute price is Black-76 with day-p EOD IV and the live underlying:
  a  sticky-strike   : contract's own day-p IV (fallback: day-p smile at day-p moneyness)
  b  sticky-moneyness: day-p OTM smile at the minute's log(K/Fhat)
  c  b * exp(beta * ln(Fhat/F_p)), beta by class from Test 1 (h=1, fitted on pairs that
     ended on or before p only - read from research/out/p1_synth_error.json)
  anchor (DIAGNOSTIC ONLY, not available historically): IV calibrated to the real option price
     at the entry minute; exit IV = anchor + smile_p(k_exit) - smile_p(k_entry).
Fhat at minute m = F_p * U_m / U_p, U = near future if it is the same contract the day-p
snapshot used, else spot; U_p = last 1-min close of U on day p. T = calendar time to
expiry 15:30 IST / 365.

Inputs : data/archive/*.parquet, data/raw/upstox/{NSE,BSE}.json.gz (instrument masters),
         research/out/p1_synth_error.json (Test 1 must have run first)
Cache  : data/raw/upstox/candles/*.json  (re-runs make no requests when cached)
Outputs: research/out/p1_synth_error.json (key "test2"), research/out/p1_synth_intraday_scatter.png,
         research/out/p1_synth_intraday_tod.png
Usage  : python research/scripts/p1_synth_intraday.py
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p1_synth_eod as T1  # noqa: E402

ROOT = T1.ROOT
RAW = ROOT / "data" / "raw" / "upstox"
CACHE = RAW / "candles"
OUTJ = T1.OUTJ
OUT_SC = ROOT / "research" / "out" / "p1_synth_intraday_scatter.png"
OUT_TOD = ROOT / "research" / "out" / "p1_synth_intraday_tod.png"
API = "https://api.upstox.com"
MIN_GAP = 0.4
MAX_REQ = 480
WINDOWS = [("2026-10-14", "2026-11-12"), ("2026-09-15", "2026-10-13")]   # (from, to), newest first
LAST_DAY = "2026-11-12"
INDICES = {"NIFTY": ("NSE", "NSE_INDEX|Nifty 50"), "BANKNIFTY": ("NSE", "NSE_INDEX|Nifty Bank"),
           "SENSEX": ("BSE", "BSE_INDEX|SENSEX")}
STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "TMPV", "BAJFINANCE"]
IDX_WING, STK_WING = 3, 1
TOD_B = [("09:15", "09:30"), ("09:30", "10:30"), ("10:30", "12:00"), ("12:00", "14:00"),
         ("14:00", "15:00"), ("15:00", "15:30")]
DTE_B2 = [(-1, 0, "expiry-day"), (0, 2, "1-2"), (2, 7, "3-7"), (7, 20, "8-20"), (20, 9999, "21+")]

SESSION = requests.Session()
SESSION.headers.update({"Accept": "application/json", "User-Agent": "research-client"})
_n = {"req": 0, "cached": 0, "fail": 0}
_last = [0.0]


def ist_date(ms):
    return (dt.datetime.utcfromtimestamp(ms / 1000) + dt.timedelta(hours=5, minutes=30)).date().isoformat()


def candles(key: str, frm: str, to: str):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{key.replace('|', '_').replace(' ', '-')}_{frm}_{to}.json"
    if f.exists():
        _n["cached"] += 1
        return json.loads(f.read_text(encoding="utf-8"))
    if _n["req"] >= MAX_REQ:
        return None
    w = MIN_GAP - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    _n["req"] += 1
    try:
        r = SESSION.get(f"{API}/v3/historical-candle/{quote(key, safe='')}/minutes/1/{to}/{frm}", timeout=30)
        _last[0] = time.time()
        if r.status_code != 200:
            _n["fail"] += 1
            return None
        c = r.json().get("data", {}).get("candles") or []
    except (requests.RequestException, ValueError):
        _n["fail"] += 1
        return None
    f.write_text(json.dumps(c), encoding="utf-8")
    return c


def pull(key: str, walk_back=True) -> pd.DataFrame:
    rows = []
    for frm, to in WINDOWS:
        c = candles(key, frm, to)
        if c is None:
            break
        rows += c
        if walk_back and not c:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([x[:7] for x in rows], columns=["ts", "o", "h", "l", "c", "v", "oi"])
    ts = pd.to_datetime(df.ts.str[:19])
    df["date"] = ts.dt.strftime("%Y-%m-%d")
    df["hm"] = ts.dt.strftime("%H:%M")
    df["ts"] = ts
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


# ------------------------------------------------------------------ instruments
def select_instruments(chain_last: pd.DataFrame):
    masters = {ex: json.load(gzip.open(RAW / f"{ex}.json.gz", "rt", encoding="utf-8")) for ex in ("NSE", "BSE")}
    out = []
    for sym, cls in [(s, "index") for s in INDICES] + [(s, "stock") for s in STOCKS]:
        ex = INDICES[sym][0] if cls == "index" else "NSE"
        m = [x for x in masters[ex] if x.get("underlying_symbol") == sym
             and x.get("instrument_type") in ("CE", "PE", "FUT") and ist_date(x["expiry"]) > LAST_DAY]
        if not m:
            print(f"  no instruments for {sym}")
            continue
        ukey = INDICES[sym][1] if cls == "index" else m[0]["underlying_key"]
        spot = chain_last.loc[chain_last.symbol == sym, "spot"]
        if spot.empty:
            continue
        spot = float(spot.iloc[0])
        opts = [x for x in m if x["instrument_type"] in ("CE", "PE")]
        if cls == "stock":
            opts = [x for x in opts if not x.get("weekly")]
        exps = sorted({ist_date(x["expiry"]) for x in opts})[: (2 if cls == "index" else 1)]
        wing = IDX_WING if cls == "index" else STK_WING
        for e in exps:
            ks = sorted({x["strike_price"] for x in opts if ist_date(x["expiry"]) == e})
            atm = int(np.argmin([abs(k - spot) for k in ks]))
            sel = set(ks[max(0, atm - wing): atm + wing + 1])
            for x in opts:
                if ist_date(x["expiry"]) == e and x["strike_price"] in sel:
                    out.append({"symbol": sym, "cls": cls, "key": x["instrument_key"], "expiry": e,
                                "strike": float(x["strike_price"]), "type": x["instrument_type"]})
        fut = sorted([x for x in m if x["instrument_type"] == "FUT"], key=lambda x: x["expiry"])
        out.append({"symbol": sym, "cls": cls, "key": ukey, "type": "SPOT"})
        if fut:
            out.append({"symbol": sym, "cls": cls, "key": fut[0]["instrument_key"], "type": "FUT",
                        "expiry": ist_date(fut[0]["expiry"])})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ main computation
def price_at(df, hm, look=5):
    """close of last candle with volume>0 stamped in [hm-look, hm]."""
    t = pd.Timestamp("2000-01-01 " + hm)
    lo = (t - pd.Timedelta(minutes=look)).strftime("%H:%M")
    g = df[(df.hm >= lo) & (df.hm <= hm) & (df.v > 0)]
    return (float(g.c.iloc[-1]), g.hm.iloc[-1]) if len(g) else (np.nan, None)


def main() -> int:
    if not OUTJ.exists() or "test1" not in json.loads(OUTJ.read_text(encoding="utf-8")):
        print("run p1_synth_eod.py first")
        return 1
    betas = json.loads(OUTJ.read_text(encoding="utf-8"))["test1"]["beta_by_date_h1"]
    bser = {cls: sorted((k.split("|")[1], v) for k, v in betas.items() if k.startswith(cls + "|"))
            for cls in ("index", "stock")}

    def beta_asof(cls, p):
        v = [b for t, b in bser[cls] if t <= p]
        return v[-1] if v else 0.0

    c, fw, sm = T1.prepare()
    dates = sorted(c.trade_date.unique())
    snapf = c.groupby(["trade_date", "symbol"]).fut_expiry.first().to_dict()
    inst = select_instruments(c[c.trade_date == LAST_DAY])
    print(f"instruments: {len(inst)} (options {int(inst.type.isin(['CE','PE']).sum())})")

    # underlying series first, then options
    under = {}
    for (sym, typ), g in inst[inst.type.isin(["SPOT", "FUT"])].groupby(["symbol", "type"]):
        under[(sym, typ)] = (pull(g.key.iloc[0], walk_back=(typ == "FUT")), g.get("expiry").iloc[0])
    own_iv = c.set_index(["trade_date", "symbol", "expiry", "strike", "type"]).iv
    fmap = fw.set_index(["trade_date", "symbol", "expiry"]).F

    mins, trades = [], []
    n_cd = 0
    for _, o in inst[inst.type.isin(["CE", "PE"])].iterrows():
        od = pull(o.key)
        if od.empty:
            continue
        spot, _ = under.get((o.symbol, "SPOT"), (pd.DataFrame(), None))
        fut, fexp = under.get((o.symbol, "FUT"), (pd.DataFrame(), None))
        if spot.empty:
            continue
        is_call = o.type == "CE"
        for d in sorted(od.date.unique()):
            if d <= dates[0] or d > LAST_DAY:
                continue
            p = max(x for x in dates if x < d)
            if np.busday_count(p, d) > 3 or (p, o.symbol, o.expiry) not in fmap.index:
                continue
            Fp = float(fmap[(p, o.symbol, o.expiry)])
            use_fut = (not fut.empty) and snapf.get((p, o.symbol)) == fexp and (fut.date == p).any() \
                and (fut.date == d).any()
            U = fut if use_fut else spot
            Up = U[U.date == p]
            Ud = U[U.date == d].set_index("hm").c
            if Up.empty or Ud.empty:
                continue
            Up = float(Up.c.iloc[-1])
            sk = f"{p}|{o.symbol}|{o.expiry}"
            if sk not in sm:
                continue
            ks, ivs = sm[sk]
            iv_own = own_iv.get((p, o.symbol, o.expiry, o.strike, o.type), np.nan)
            iv_a = float(iv_own) if np.isfinite(iv_own) else float(np.interp(np.log(o.strike / Fp), ks, ivs))
            beta = beta_asof(o.cls, p)
            exp_ts = pd.Timestamp(o.expiry + " 15:30")
            dte = (pd.Timestamp(o.expiry) - pd.Timestamp(d)).days

            def synth(hm_list):
                u = Ud.reindex(hm_list).values.astype(float)
                F = Fp * u / Up
                ts = pd.to_datetime(d + " " + pd.Series(hm_list)).values
                T = np.maximum((exp_ts - pd.DatetimeIndex(ts)).total_seconds().values / (365 * 86400), 1e-6)
                k = np.log(o.strike / F)
                ivb = np.interp(k, ks, ivs)
                out = {"F": F, "T": T, "k": k, "iv_b": ivb,
                       "a": T1.b76(F, o.strike, T, np.full(len(F), iv_a), is_call),
                       "b": T1.b76(F, o.strike, T, ivb, is_call),
                       "c": T1.b76(F, o.strike, T, ivb * np.exp(beta * np.log(F / Fp)), is_call)}
                out["delta"] = T1.b76_delta(F, o.strike, T, ivb, is_call)
                return out

            g = od[(od.date == d) & (od.v > 0)]
            g = g[g.hm.isin(Ud.index)]
            if len(g) < 5:
                continue
            n_cd += 1
            s = synth(list(g.hm))
            mins.append(pd.DataFrame({"symbol": o.symbol, "cls": o.cls, "date": d, "key": o.key, "hm": g.hm.values,
                                      "dte": dte, "real": g.c.values.astype(float), "a": s["a"], "b": s["b"],
                                      "c": s["c"], "absdelta": np.abs(s["delta"])}))
            # -------- stylised trades
            styles = [("0930-1515", "09:30", "15:15"), ("0945+60", "09:45", "10:45")]
            sp_d = spot[spot.date == d]
            orh = sp_d[(sp_d.hm >= "09:15") & (sp_d.hm < "09:30")]
            if len(orh) >= 10:
                hi, lo = orh.h.max(), orh.l.min()
                after = sp_d[(sp_d.hm >= "09:30") & (sp_d.hm < "15:00")]
                brk = after[(after.c > hi) | (after.c < lo)]
                if len(brk):
                    up = brk.c.iloc[0] > hi
                    if up == is_call:
                        styles.append(("ORB15-1515", brk.hm.iloc[0], "15:15"))
            for name, t_in, t_out in styles:
                dd = od[od.date == d]
                p_in, m_in = price_at(dd, t_in)
                p_out, m_out = price_at(dd, t_out)
                if not (np.isfinite(p_in) and np.isfinite(p_out)) or m_in not in Ud.index or m_out not in Ud.index:
                    continue
                s2 = synth([m_in, m_out])
                if not np.all(np.isfinite(s2["b"])) or s2["b"][0] <= 0:
                    continue
                rec = {"symbol": o.symbol, "cls": o.cls, "date": d, "key": o.key, "style": name, "dte": dte,
                       "absdelta_in": float(abs(s2["delta"][0])), "ret_real": p_out / p_in - 1}
                for m in ("a", "b", "c"):
                    rec["ret_" + m] = float(s2[m][1] / s2[m][0] - 1)
                    rec["ent_err_" + m] = float(s2[m][0] / p_in - 1)
                iv_anc = T1.implied_vol(np.array([p_in]), s2["F"][:1], np.array([o.strike]), s2["T"][:1],
                                        np.array([is_call]))[0]
                if np.isfinite(iv_anc):
                    iv_x = iv_anc + s2["iv_b"][1] - s2["iv_b"][0]
                    px = T1.b76(s2["F"][1:], o.strike, s2["T"][1:], np.array([iv_x]), is_call)[0]
                    rec["ret_anchor"] = float(px / p_in - 1)
                else:
                    rec["ret_anchor"] = np.nan
                trades.append(rec)

    M = pd.concat(mins, ignore_index=True)
    M = M[(M.real > 0) & M.absdelta.between(0.10, 0.80)]
    for m in ("a", "b", "c"):
        M["e_" + m] = (M[m] / M.real - 1).abs()
        M["s_" + m] = M[m] / M.real - 1
    M["tod"] = None
    for lo, hi in TOD_B:
        M.loc[(M.hm >= lo) & (M.hm < hi), "tod"] = f"{lo}-{hi}"
    M["del_b"] = T1.bucket(M.absdelta.values, T1.DEL_B)
    M["dte_b"] = T1.bucket(M.dte.values.astype(float), DTE_B2)
    Tr = pd.DataFrame(trades)
    Tr = Tr[Tr.absdelta_in.between(0.10, 0.80)]

    def merr(g):
        return {"n_min": int(len(g)), "n_contract_days": int(g.groupby(["key", "date"]).ngroups),
                **{f"med_{m}": round(float(g["e_" + m].median()), 4) for m in ("a", "b", "c")},
                **{f"p90_{m}": round(float(g["e_" + m].quantile(0.9)), 4) for m in ("a", "b", "c")},
                **{f"bias_{m}": round(float(g["s_" + m].median()), 4) for m in ("a", "b", "c")}}

    def tstats(g, m):
        x, y = g["ret_" + m].values, g.ret_real.values
        ok = np.isfinite(x)
        x, y = x[ok], y[ok]
        if len(x) < 5:
            return {"n": int(len(x))}
        e = x - y
        return {"n": int(len(x)), "n_days": int(g.date.nunique()),
                "corr": round(float(np.corrcoef(x, y)[0, 1]), 3),
                "sign_agree": round(float(np.mean(np.sign(x) == np.sign(y))), 3),
                "mean_bias": round(float(e.mean()), 4), "mae": round(float(np.abs(e).mean()), 4),
                "med_abs_err": round(float(np.median(np.abs(e))), 4),
                "frac_err_gt_absret": round(float(np.mean(np.abs(e) > np.abs(y))), 3),
                "mean_real_ret": round(float(y.mean()), 4)}

    res = {"meta": {"requests_made_this_run": _n["req"], "cache_hits": _n["cached"], "failed": _n["fail"],
                    "n_instruments": int(len(inst)), "n_option_contracts": int(inst.type.isin(["CE", "PE"]).sum()),
                    "n_contract_days": n_cd, "n_minutes_eval": int(len(M)),
                    "days": [M.date.min(), M.date.max()], "n_days": int(M.date.nunique()),
                    "filter": "|delta| 0.10-0.80 (model-b delta), option minute volume>0",
                    "note": "live contracts only: min DTE in sample ~4-5; no expiry-day or 0-2 DTE data"},
           "by_class": {}, "by_tod": {}, "by_delta": {}, "by_dte": {}, "trades": {}}
    for cls, g in M.groupby("cls"):
        res["by_class"][cls] = merr(g)
        res["by_tod"][cls] = {k: merr(x) for k, x in g.groupby("tod")}
        res["by_delta"][cls] = {k: merr(x) for k, x in g.groupby("del_b")}
        res["by_dte"][cls] = {k: merr(x) for k, x in g.groupby("dte_b")}
    for (cls, st), g in Tr.groupby(["cls", "style"]):
        res["trades"][f"{cls}|{st}"] = {m: tstats(g, m) for m in ("a", "b", "c", "anchor")}
        res["trades"][f"{cls}|{st}"]["entry_med_abs_err_b"] = round(float(g.ent_err_b.abs().median()), 4)

    # ---------------- charts
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    for ax, cls in zip(axes, ["index", "stock"]):
        g = Tr[(Tr.cls == cls) & (Tr["style"] == "0930-1515")]
        ax.scatter(g.ret_real * 100, g.ret_b * 100, s=10, alpha=0.6, label="model b (synthetic entry+exit)")
        ax.scatter(g.ret_real * 100, g.ret_anchor * 100, s=10, alpha=0.6, marker="x",
                   label="anchor (real entry, diagnostic)")
        lim = np.nanpercentile(np.abs(np.r_[g.ret_real, g.ret_b]) * 100, 98) if len(g) else 50
        ax.plot([-lim, lim], [-lim, lim], color="grey", lw=0.8)
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_xlabel("real return 09:30->15:15 (%)")
        ax.set_ylabel("synthetic return (%)")
        ax.set_title(f"{cls}: n={len(g)} trades, {g.date.nunique() if len(g) else 0} days")
        ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(OUT_SC, dpi=110)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 4))
    labs = [f"{lo}-{hi}" for lo, hi in TOD_B]
    for cls, ls in [("index", "-"), ("stock", "--")]:
        for m in ("a", "b"):
            y = [res["by_tod"].get(cls, {}).get(l, {}).get(f"med_{m}", np.nan) for l in labs]
            ax.plot(labs, np.array(y, float) * 100, ls, marker="o", label=f"{cls} model {m}")
    ax.set_ylabel("median |synthetic/real - 1| (%)")
    ax.set_title(f"Intraday synthetic price error by time of day (n={len(M)} minutes)")
    ax.legend(fontsize=8)
    plt.setp(ax.get_xticklabels(), rotation=20)
    fig.tight_layout()
    fig.savefig(OUT_TOD, dpi=110)
    plt.close(fig)

    allj = json.loads(OUTJ.read_text(encoding="utf-8"))
    allj["test2"] = res
    OUTJ.write_text(json.dumps(allj, indent=1, default=str), encoding="utf-8")

    print(f"requests={_n['req']} cached={_n['cached']} failed={_n['fail']} contract_days={n_cd} "
          f"minutes={len(M)} days={res['meta']['n_days']} trades={len(Tr)}")
    for cls, s in res["by_class"].items():
        print(f"{cls}: {s}")
    for k, v in res["trades"].items():
        b, an = v["b"], v["anchor"]
        print(f"{k:22s} n={b.get('n')} corr_b={b.get('corr')} sign_b={b.get('sign_agree')} bias_b={b.get('mean_bias')} "
              f"mae_b={b.get('mae')} | anchor corr={an.get('corr')} sign={an.get('sign_agree')} mae={an.get('mae')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
