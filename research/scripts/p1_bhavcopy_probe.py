"""p1_bhavcopy_probe.py -- structure / coverage / data-quality probe over sampled bhavcopy files.

Reads whatever p1_fetch_bhavcopy.py put under data/raw/bhavcopy and writes
research/out/p1_bhavcopy_probe.json. Prints <= 50 lines.

  python -I research/scripts/p1_bhavcopy_probe.py [--raw data/raw/bhavcopy] [--out research/out/p1_bhavcopy_probe.json]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import io
import json
import os
import re
import sys
import zipfile

import numpy as np
import pandas as pd

INDEX_SYMS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "NIFTYIT", "SENSEX",
              "BANKEX", "SENSEX50", "FOCIT"}

# Normalised column map: legacy NSE / UDiFF / legacy BSE -> common names
LEG_NSE = dict(INSTRUMENT="inst", SYMBOL="sym", EXPIRY_DT="expiry", STRIKE_PR="strike", OPTION_TYP="otype",
               OPEN="open", HIGH="high", LOW="low", CLOSE="close", SETTLE_PR="settle", CONTRACTS="contracts",
               VAL_INLAKH="turnover_lakh", OPEN_INT="oi", CHG_IN_OI="chg_oi",
               OPTIONTYPE="otype")  # some 2000s files spell it OPTIONTYPE
UDIFF = dict(FinInstrmTp="inst", TckrSymb="sym", XpryDt="expiry", StrkPric="strike", OptnTp="otype",
             OpnPric="open", HghPric="high", LwPric="low", ClsPric="close", LastPric="last", SttlmPric="settle",
             TtlTradgVol="volume_qty", TtlTrfVal="turnover", OpnIntrst="oi", ChngInOpnIntrst="chg_oi",
             UndrlygPric="underlying", NewBrdLotQty="lot", TtlNbOfTxsExctd="n_trades", PrvsClsgPric="prev_close")
LEG_BSE = {"Contract Type": "inst", "Symbol": "series", "Expiry": "expiry", "Strike": "strike",
           "Option Type": "otype", "Open": "open", "High": "high", "Low": "low", "Close": "close",
           "Contracts Traded": "contracts", "Turnover(Rs Lakhs)": "turnover_lakh", "Open Interest": "oi",
           "Change in OI": "chg_oi", "Wt. Avg. Price": "wavg", "L T Date": "lt_date", "Is Traded": "is_traded"}

OPT_INST = {"OPTIDX", "OPTSTK", "IDO", "STO", "IO", "SO"}
IDX_OPT_INST = {"OPTIDX", "IDO", "IO"}
FUT_IDX_INST = {"FUTIDX", "IDF", "IF"}


def read_any(path: str) -> pd.DataFrame:
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            return pd.read_csv(z.open(name), low_memory=False)
    return pd.read_csv(path, low_memory=False)


def date_from_name(fn: str) -> str:
    m = re.search(r"_(\d{8})_F_", fn)
    if m:
        return dt.datetime.strptime(m.group(1), "%Y%m%d").date().isoformat()
    m = re.search(r"(\d{2})([A-Z]{3})(\d{4})bhav", fn)
    if m:
        return dt.datetime.strptime("".join(m.groups()), "%d%b%Y").date().isoformat()
    m = re.search(r"bhavcopy(\d{2}-\d{2}-\d{2})", fn)
    if m:
        return dt.datetime.strptime(m.group(1), "%d-%m-%y").date().isoformat()
    m = re.search(r"_(\d{8})\.csv", fn)
    if m:
        return dt.datetime.strptime(m.group(1), "%d%m%Y").date().isoformat()
    m = re.search(r"EQ_ISINCODE_(\d{6})", fn)
    if m:
        return dt.datetime.strptime(m.group(1), "%d%m%y").date().isoformat()
    return ""


def normalise(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    cols = set(df.columns)
    if "TckrSymb" in cols:
        fmt, m = "udiff", UDIFF
    elif "INSTRUMENT" in cols:
        fmt, m = "legacy_nse", LEG_NSE
    elif "Contract Type" in cols:
        fmt, m = "legacy_bse", LEG_BSE
    else:
        return df, "unknown"
    out = df.rename(columns=m)[[v for k, v in m.items() if k in cols]].copy()
    for c in out.columns:
        if out[c].dtype == object:
            out[c] = out[c].astype(str).str.strip()
    for c in out.columns:
        if c not in ("inst", "sym", "series", "otype", "expiry", "lt_date", "is_traded"):
            out[c] = pd.to_numeric(out[c].astype(str).str.replace(",", ""), errors="coerce")
    if fmt == "legacy_bse":
        out["sym"] = out["series"].str.extract(r"^([A-Z]+?)(?=\d{2}[0-9A-Z])")[0]
        out["otype"] = out["otype"].map({"CALL": "CE", "PUT": "PE"}).fillna(out["otype"])
    out["expiry"] = pd.to_datetime(out["expiry"], errors="coerce", dayfirst=(fmt != "udiff"))
    if "volume_qty" not in out and "contracts" in out:
        out["traded"] = out["contracts"]
    else:
        out["traded"] = out.get("volume_qty")
    return out, fmt


def opt_stats(o: pd.DataFrame, fmt: str, tdate: str) -> dict:
    """Data-quality stats on option rows."""
    res = {}
    if o.empty:
        return res
    o = o.copy()
    o["zero"] = o["traded"].fillna(0) <= 0
    res["option_rows"] = int(len(o))
    res["zero_volume_share"] = round(float(o["zero"].mean()), 4)
    idx = o[o["sym"].isin(INDEX_SYMS)]
    stk = o[~o["sym"].isin(INDEX_SYMS)]
    res["zero_volume_share_index"] = round(float(idx["zero"].mean()), 4) if len(idx) else None
    res["zero_volume_share_stock"] = round(float(stk["zero"].mean()), 4) if len(stk) else None
    # close vs settle on zero-volume rows
    if "settle" in o:
        z = o[o["zero"]]
        if len(z):
            res["zero_vol_close_ne_settle_share"] = round(float((~np.isclose(z["close"], z["settle"])).mean()), 4)
            res["zero_vol_close_eq_0_share"] = round(float((z["close"] == 0).mean()), 4)
        t = o[~o["zero"]]
        if len(t):
            res["traded_close_ne_settle_share"] = round(float((~np.isclose(t["close"], t["settle"])).mean()), 4)
    # strikes per expiry with volume > 0
    for name in ("NIFTY", "SENSEX", "BANKNIFTY"):
        s = o[o["sym"] == name]
        if s.empty:
            continue
        exps = sorted(s["expiry"].dropna().unique())
        near = [e for e in exps if pd.Timestamp(e) >= pd.Timestamp(tdate)][:3]
        rows = []
        for e in near:
            se = s[s["expiry"] == e]
            rows.append(dict(expiry=str(pd.Timestamp(e).date()), strikes=int(se["strike"].nunique()),
                             strikes_traded=int(se.loc[~se["zero"], "strike"].nunique()),
                             zero_share=round(float(se["zero"].mean()), 3)))
        res[f"{name}_near_expiries"] = rows
        res[f"{name}_n_expiries"] = len(exps)
    if len(stk):
        near_e = stk.groupby("sym")["expiry"].transform("min")
        sn = stk[stk["expiry"] == near_e]
        g = sn.groupby("sym").agg(strikes=("strike", "nunique"),
                                  traded=("strike", lambda x: x[~sn.loc[x.index, "zero"]].nunique()))
        res["stock_near_expiry_strikes_median"] = float(g["strikes"].median())
        res["stock_near_expiry_traded_strikes_median"] = float(g["traded"].median())
        res["stock_near_expiry_traded_strikes_p10_p90"] = [float(g["traded"].quantile(.1)), float(g["traded"].quantile(.9))]
        res["stock_underlyings_with_zero_traded_strikes"] = int((g["traded"] == 0).sum())
    # expiry-day anomaly: close equals underlying / intrinsic for expiring contracts
    exp_today = o[o["expiry"] == pd.Timestamp(tdate)]
    if len(exp_today) and "underlying" in o:
        res["expiring_rows"] = int(len(exp_today))
        res["expiring_close_eq_underlying_share"] = round(float(np.isclose(exp_today["close"], exp_today["underlying"]).mean()), 3)
    elif len(exp_today):
        res["expiring_rows"] = int(len(exp_today))
        big = exp_today["close"] > exp_today["strike"] * 0.5
        res["expiring_close_looks_like_spot_share"] = round(float(big.mean()), 3)
    return res


def probe_fo(path: str) -> dict:
    fn = os.path.basename(path)
    tdate = date_from_name(fn)
    raw = read_any(path)
    df, fmt = normalise(raw)
    rec = dict(file=fn, date=tdate, format=fmt, bytes=os.path.getsize(path), rows=int(len(raw)),
               columns=[c for c in raw.columns if not c.startswith("Unnamed")])
    o = df[df["inst"].isin(OPT_INST)]
    key = ["sym", "expiry", "strike", "otype"]
    rec["n_option_contracts"] = int(o[key].drop_duplicates().shape[0])
    rec["n_option_underlyings"] = int(o["sym"].nunique())
    rec["n_index_option_underlyings"] = int(o.loc[o["inst"].isin(IDX_OPT_INST), "sym"].nunique())
    rec["n_stock_option_underlyings"] = int(o.loc[~o["inst"].isin(IDX_OPT_INST), "sym"].nunique())
    rec["index_option_symbols"] = sorted(o.loc[o["inst"].isin(IDX_OPT_INST), "sym"].dropna().unique().tolist())
    rec["quality"] = opt_stats(o, fmt, tdate)
    # units check
    if fmt == "udiff":
        t = o[(o["traded"] > 0) & (o["lot"] > 0)]
        rec["udiff_volume_divisible_by_lot_share"] = round(float((t["traded"] % t["lot"] == 0).mean()), 4)
        t2 = o[(o["oi"] > 0) & (o["lot"] > 0)]
        rec["udiff_oi_divisible_by_lot_share"] = round(float((t2["oi"] % t2["lot"] == 0).mean()), 4)
        rec["has_underlying_price_share"] = round(float((o["underlying"] > 0).mean()), 4)
        nf = o[o["sym"] == "NIFTY"]
        if len(nf):
            rec["nifty_lot"] = int(nf["lot"].mode().iloc[0])
    if fmt == "legacy_nse":
        f = df[(df["inst"] == "FUTIDX") & (df["sym"] == "NIFTY") & (df["contracts"] > 0)]
        if len(f):
            # implied multiplier = turnover / (contracts * price): recovers lot size from legacy files
            imp = f["turnover_lakh"] * 1e5 / (f["contracts"] * f["settle"])
            rec["nifty_fut_implied_lot"] = round(float(imp.median()), 1)
        nf = o[(o["sym"] == "NIFTY") & (o["oi"] > 0)]
        if len(nf) and "nifty_fut_implied_lot" in rec:
            lot = round(rec["nifty_fut_implied_lot"])
            rec["legacy_oi_divisible_by_implied_lot_share"] = round(float((nf["oi"] % lot == 0).mean()), 4)
    if fmt == "legacy_bse":
        rec["has_is_traded_flag"] = True
    return rec


def probe_cm(path: str) -> dict:
    raw = read_any(path)
    rec = dict(file=os.path.basename(path), date=date_from_name(os.path.basename(path)),
               bytes=os.path.getsize(path), rows=int(len(raw)),
               columns=[c.strip() for c in raw.columns if not str(c).startswith("Unnamed")])
    for c in ("SERIES", "SctySrs"):
        if c in raw:
            rec["eq_series_rows"] = int((raw[c].astype(str).str.strip() == "EQ").sum())
    return rec


def probe_index(path: str) -> dict:
    raw = read_any(path)
    names = raw["Index Name"].astype(str).str.strip()
    return dict(file=os.path.basename(path), date=date_from_name(os.path.basename(path)),
                bytes=os.path.getsize(path), rows=int(len(raw)), columns=list(raw.columns),
                has_india_vix=bool(names.str.upper().eq("INDIA VIX").any()),
                has_nifty50=bool(names.str.upper().isin(["NIFTY 50", "CNX NIFTY", "S&P CNX NIFTY"]).any()),
                vix_row=raw[names.str.upper() == "INDIA VIX"].iloc[:, 2:6].astype(str).values.tolist()[:1])


def probe_poi(path: str) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [l.strip() for l in f if l.strip()]
    hdr = next(csv.reader([lines[1]]))
    clients = [next(csv.reader([l]))[0].strip() for l in lines[2:]]
    return dict(file=os.path.basename(path), date=date_from_name(os.path.basename(path)),
                bytes=os.path.getsize(path), title=lines[0][:90], header=[h.strip() for h in hdr if h.strip()],
                client_types=clients)


def trading_days(a: dt.date, b: dt.date, per_year: float = 248) -> int:
    wk = sum(1 for i in range((b - a).days + 1) if (a + dt.timedelta(i)).weekday() < 5)
    return int(round(wk * per_year / 261))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw/bhavcopy")
    ap.add_argument("--out", default="research/out/p1_bhavcopy_probe.json")
    a = ap.parse_args()
    R = a.raw
    res = dict(generated=dt.datetime.now().isoformat(timespec="seconds"), nse_fo=[], bse_fo=[], nse_cm=[],
               bse_cm=[], index=[], participant_oi=[], secban=[], lots={})
    sample_nse_fo = []
    for p in sorted(glob.glob(f"{R}/nse/fo/*/*")):
        sample_nse_fo.append(p)
    # full probe on one file per year/format (+ skip the 20-day rate test bulk except one)
    seen = set()
    for p in sample_nse_fo:
        d = date_from_name(os.path.basename(p))
        key = d[:7] if d >= "2026-09" else d
        if d.startswith("2026-09") and d != "2026-09-01":
            continue
        if key in seen:
            continue
        seen.add(key)
        res["nse_fo"].append(probe_fo(p))
    for p in sorted(glob.glob(f"{R}/bse/fo/*/*")):
        res["bse_fo"].append(probe_fo(p))
    for p in sorted(glob.glob(f"{R}/nse/cm/*/*")):
        res["nse_cm"].append(probe_cm(p))
    for p in sorted(glob.glob(f"{R}/bse/cm/*/*")):
        r = probe_cm(p)
        res["bse_cm"].append(r)
    for p in sorted(glob.glob(f"{R}/nse/index/*/*")):
        res["index"].append(probe_index(p))
    for p in sorted(glob.glob(f"{R}/nse/participant_oi/*/*")):
        res["participant_oi"].append(probe_poi(p))
    for p in sorted(glob.glob(f"{R}/nse/secban/*/*")):
        with open(p, encoding="utf-8", errors="replace") as f:
            res["secban"].append(dict(file=os.path.basename(p), first_lines=[l.strip()[:80] for l in f][:3]))
    for p in sorted(glob.glob(f"{R}/nse/lots/*.csv")):
        lots = pd.read_csv(p)
        lots.columns = [c.strip() for c in lots.columns]
        res["lots"] = dict(file=os.path.basename(p), rows=int(len(lots)), columns=list(lots.columns),
                           note="current snapshot only: forward months, no history")

    # rate-limit test from manifest
    man = pd.read_csv(f"{R}/manifest.csv")
    rt = man[(man["kind"] == "fo") & (man["exchange"] == "nse") & man["date"].between("2026-09-01", "2026-09-30")]
    res["rate_test"] = dict(dates=int(len(rt)), ok=int((rt["status"] == 200).sum()),
                            missing_404=int((rt["status"] == 404).sum()),
                            missing_dates=rt.loc[rt["status"] == 404, "date"].tolist(),
                            mean_bytes=int(rt.loc[rt["status"] == 200, "bytes"].mean()))
    res["downloaded_files"] = int((man["status"] == 200).sum()) + len(glob.glob(f"{R}/nse/lots/*"))

    # sizing 2019-01-01 -> 2026-11-12
    a0, sw, b0 = dt.date(2019, 1, 1), dt.date(2024, 7, 8), dt.date(2026, 11, 12)
    n_leg, n_ud = trading_days(a0, sw - dt.timedelta(1)), trading_days(sw, b0)
    def mean_bytes(rows, pred):
        v = [r["bytes"] for r in rows if pred(r)]
        return float(np.mean(v)) if v else 0.0
    nse_fo_leg = mean_bytes(res["nse_fo"], lambda r: r["format"] == "legacy_nse" and r["date"] >= "2019")
    nse_fo_ud = float(man.loc[(man["exchange"] == "nse") & (man["kind"] == "fo") & (man["status"] == 200)
                              & (man["date"] >= "2024-07-08"), "bytes"].mean())
    bse_leg = mean_bytes(res["bse_fo"], lambda r: r["format"] == "legacy_bse")
    bse_ud = mean_bytes(res["bse_fo"], lambda r: r["format"] == "udiff")
    cm_leg = mean_bytes(res["nse_cm"], lambda r: "bhav.csv" in r["file"])
    cm_ud = mean_bytes(res["nse_cm"], lambda r: "BhavCopy" in r["file"])
    idx_b = mean_bytes(res["index"], lambda r: True)
    poi_b = mean_bytes(res["participant_oi"], lambda r: True)
    est = {
        "nse_fo": n_leg * nse_fo_leg + n_ud * nse_fo_ud,
        "bse_fo": n_leg * bse_leg + n_ud * bse_ud,
        "nse_cm": n_leg * cm_leg + n_ud * cm_ud,
        "nse_index_incl_vix": (n_leg + n_ud) * idx_b,
        "nse_participant_oi": (n_leg + n_ud) * poi_b,
    }
    n_days = n_leg + n_ud
    files = n_days * len(est)
    secs_per_req = 1.55  # measured: 20 requests in ~28-31 s incl. 1 s sleep
    res["sizing"] = dict(trading_days=n_days, legacy_days=n_leg, udiff_days=n_ud,
                         mb_by_kind={k: round(v / 1e6, 1) for k, v in est.items()},
                         total_mb=round(sum(est.values()) / 1e6, 1), files=files,
                         hours_at_1rps=round(files * secs_per_req / 3600, 2),
                         note="BSE legacy sample sizes are 2022-24 (thin early SENSEX) -> lower bound")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=str)

    # ---- console summary (<= 50 lines) ----
    print("NSE F&O: date fmt KB rows contracts und(idx/stk) zero%(all/idx/stk) NIFTYnear strikes/traded")
    for r in res["nse_fo"]:
        q = r["quality"]
        ne = (q.get("NIFTY_near_expiries") or [{}])[0]
        print(f" {r['date']} {r['format'][:6]:6} {r['bytes']//1024:5} {r['rows']:6} {r['n_option_contracts']:6} "
              f"{r['n_option_underlyings']:3}({r['n_index_option_underlyings']}/{r['n_stock_option_underlyings']}) "
              f"{q.get('zero_volume_share',0):.2f}/{q.get('zero_volume_share_index') or 0:.2f}/{q.get('zero_volume_share_stock') or 0:.2f} "
              f"{ne.get('strikes','-')}/{ne.get('strikes_traded','-')} stkTradedMed={q.get('stock_near_expiry_traded_strikes_median','-')}")
    print("BSE F&O: date fmt KB rows contracts idxsyms zero% SENSEXnear strikes/traded")
    for r in res["bse_fo"]:
        q = r["quality"]
        ne = (q.get("SENSEX_near_expiries") or [{}])[0]
        print(f" {r['date']} {r['format'][:6]:6} {r['bytes']//1024:5} {r['rows']:5} {r['n_option_contracts']:5} "
              f"{','.join(r['index_option_symbols'])[:22]:22} {q.get('zero_volume_share',0):.2f} {ne.get('strikes','-')}/{ne.get('strikes_traded','-')}")
    print("index:", [(r["date"], r["rows"], r["has_india_vix"]) for r in res["index"]])
    print("participant_oi:", [(r["date"], len(r["client_types"])) for r in res["participant_oi"]])
    print("rate_test:", res["rate_test"])
    print("sizing:", json.dumps(res["sizing"]))
    print("downloaded files:", res["downloaded_files"], "->", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
