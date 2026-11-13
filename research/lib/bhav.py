"""Bhavcopy parsers -> one normalised schema per kind (pure functions, no writes).

F&O formats handled (see research/out/p1_bhavcopy.md):
  * NSE legacy  (fo{DD}{MON}{YYYY}bhav.csv.zip, to 2024-07-05): volume CONTRACTS, OI in shares, no lot.
  * UDiFF       (BhavCopy_{NSE|BSE}_FO_..., 2024-01+): lot NewBrdLotQty; OI in shares;
                volume = contracts (NSE) or quantity (BSE) -- detected per file from futures turnover.
  * BSE legacy  (bhavcopy{DD-MM-YY}.zip): volume and OI in contracts, no lot, no spot.
Normalised F&O columns: FO_COLS. Lots for legacy files are rebuilt per (symbol, expiry) from the
gcd of OI / change-in-OI share counts, disambiguated by the futures turnover-implied multiplier.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import math
import os
import re
import zipfile
from functools import reduce

import numpy as np
import pandas as pd

PARSER_VERSION = 2  # bump to force re-staging of every raw file

FO_COLS = ["date", "exchange", "symbol", "inst", "cls", "expiry", "strike", "opt_type", "open", "high", "low",
           "close", "settle", "volume_contracts", "oi_contracts", "chg_oi_contracts", "lot_size", "lot_src",
           "und_px", "turnover_rs", "src_fmt"]

INST_MAP = {  # raw instrument code -> (inst, cls)
    "OPTIDX": ("OPT", "index"), "OPTSTK": ("OPT", "stock"), "FUTIDX": ("FUT", "index"), "FUTSTK": ("FUT", "stock"),
    "IDO": ("OPT", "index"), "STO": ("OPT", "stock"), "IDF": ("FUT", "index"), "STF": ("FUT", "stock"),
    "IO": ("OPT", "index"), "SO": ("OPT", "stock"), "IF": ("FUT", "index"), "SF": ("FUT", "stock"),
}

INDEX_NAME_MAP = {  # ind_close_all "Index Name" (upper) -> F&O symbol
    "NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTY FINANCIAL SERVICES": "FINNIFTY",
    "NIFTY MIDCAP SELECT": "MIDCPNIFTY", "NIFTY NEXT 50": "NIFTYNXT50", "NIFTY IT": "NIFTYIT",
    "INDIA VIX": "INDIAVIX",
}


# ----------------------------------------------------------------------------- file helpers
def read_raw(path: str) -> pd.DataFrame:
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            name = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
            df = pd.read_csv(z.open(name), low_memory=False)
    else:
        df = pd.read_csv(path, low_memory=False)
    df.columns = [str(c).strip() for c in df.columns]
    df = df.loc[:, [c for c in df.columns if not c.startswith("Unnamed")]]
    return df


def date_from_name(fn: str) -> str:
    fn = os.path.basename(fn)
    pats = [(r"_(\d{8})_F_", "%Y%m%d"), (r"(\d{2}[A-Z]{3}\d{4})bhav", "%d%b%Y"),
            (r"bhavcopy(\d{2}-\d{2}-\d{2})", "%d-%m-%y"), (r"_(\d{8})\.csv", "%d%m%Y"),
            (r"EQ_ISINCODE_(\d{6})", "%d%m%y")]
    for p, f in pats:
        m = re.search(p, fn)
        if m:
            return dt.datetime.strptime(m.group(1), f).date().isoformat()
    return ""


def file_format(fn: str) -> str:
    b = os.path.basename(fn)
    return "udiff" if b.startswith("BhavCopy_") else "legacy"


def _num(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        s = s.astype(str).str.replace(",", "").str.strip().replace({"-": None, "": None, "nan": None})
    return pd.to_numeric(s, errors="coerce")


def _str(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip()


# ----------------------------------------------------------------------------- lot reconstruction
def _gcd_list(vals) -> int:
    vals = [int(v) for v in vals if v and np.isfinite(v)]
    return reduce(math.gcd, vals, 0) if vals else 0


def _divisors(n: int):
    out = set()
    i = 1
    while i * i <= n:
        if n % i == 0:
            out.add(i); out.add(n // i)
        i += 1
    return sorted(out)


def estimate_lot(share_counts, implied, min_rows: int = 3):
    """Lot size from share-denominated OI/chg-OI values (all multiples of the lot) and the
    futures-turnover implied multiplier(s). `implied` is a scalar or a list of candidates (the
    contract's own futures first; other expiries of the symbol after - they differ around lot
    revisions). Returns (lot or nan, method)."""
    vals = np.abs(np.asarray(share_counts, float))
    vals = vals[np.isfinite(vals) & (vals > 0)]
    g = _gcd_list(vals)
    cand = [x for x in np.atleast_1d(np.asarray(implied, float)) if np.isfinite(x) and x > 0]
    if g > 0 and cand:
        divs = np.array(_divisors(g), float)
        for imp in cand:
            rel = np.abs(divs / imp - 1.0)
            j = int(np.argmin(rel))
            if rel[j] <= 0.10:
                return float(divs[j]), "gcd+turnover"
        return float(round(cand[0])), "turnover"
    if g > 0 and len(vals) >= min_rows:
        return float(g), "gcd"
    if cand:
        return float(round(cand[0])), "turnover"
    return np.nan, "none"


def implied_multiplier(fut: pd.DataFrame, by_expiry: bool = False) -> pd.Series:
    """Median of turnover_rs / (contracts * price) over traded futures rows, per symbol
    (or per (symbol, expiry): around lot revisions near and far months carry different lots)."""
    f = fut[(fut["volume_contracts"] > 0) & (fut["turnover_rs"] > 0)]
    px = f["settle"].where(f["settle"] > 0, f["close"])
    m = f["turnover_rs"] / (f["volume_contracts"] * px)
    keys = [f["symbol"], f["expiry"]] if by_expiry else f["symbol"]
    return m.groupby(keys).median()


# ----------------------------------------------------------------------------- F&O parser
def _finish(out: pd.DataFrame, date: str, exchange: str, fmt: str) -> pd.DataFrame:
    out["date"] = pd.Timestamp(date)
    out["exchange"] = exchange
    out["src_fmt"] = fmt
    for c in FO_COLS:
        if c not in out:
            out[c] = np.nan
    out = out[FO_COLS]
    for c in ("strike", "open", "high", "low", "close", "settle", "volume_contracts", "oi_contracts",
              "chg_oi_contracts", "lot_size", "und_px", "turnover_rs"):
        out[c] = out[c].astype("float64")
    out.loc[out["inst"] == "FUT", ["strike", "opt_type"]] = [np.nan, None]
    return out.reset_index(drop=True)


def parse_fo(raw: pd.DataFrame, exchange: str, date: str) -> tuple[pd.DataFrame, dict]:
    """Normalise one F&O bhavcopy. Returns (frame with FO_COLS, stats)."""
    cols = set(raw.columns)
    stats = {}
    if "TckrSymb" in cols:
        fmt = "udiff"
        inst_raw = _str(raw["FinInstrmTp"])
    elif "INSTRUMENT" in cols:
        fmt = "legacy_nse"
        inst_raw = _str(raw["INSTRUMENT"])
    elif "Contract Type" in cols:
        fmt = "legacy_bse"
        inst_raw = _str(raw["Contract Type"])
    else:
        raise ValueError("unknown F&O format")
    keep = inst_raw.isin(INST_MAP)
    stats["dropped_inst"] = inst_raw[~keep].value_counts().to_dict()
    r = raw.loc[keep].copy()
    ic = inst_raw[keep].map(INST_MAP)
    out = pd.DataFrame(index=r.index)
    out["inst"] = ic.str[0]
    out["cls"] = ic.str[1]

    if fmt == "udiff":
        out["symbol"] = _str(r["TckrSymb"])
        out["expiry"] = pd.to_datetime(_str(r["XpryDt"]), format="%Y-%m-%d", errors="coerce")
        out["strike"] = _num(r["StrkPric"])
        out["opt_type"] = _str(r["OptnTp"]).where(out["inst"] == "OPT")
        for a, b in [("open", "OpnPric"), ("high", "HghPric"), ("low", "LwPric"), ("close", "ClsPric"),
                     ("settle", "SttlmPric"), ("und_px", "UndrlygPric"), ("turnover_rs", "TtlTrfVal")]:
            out[a] = _num(r[b])
        lot = _num(r["NewBrdLotQty"])
        vol = _num(r["TtlTradgVol"])
        oi, chg = _num(r["OpnIntrst"]), _num(r["ChngInOpnIntrst"])
        # volume unit: turnover/(vol*price) ~= lot -> contracts; ~= 1 -> quantity
        fm = (out["inst"] == "FUT") & (vol > 0) & (lot > 1) & (out["close"] > 0) & (out["turnover_rs"] > 0)
        if fm.sum() >= 1:
            ratio = float((out.loc[fm, "turnover_rs"] / (vol[fm] * out.loc[fm, "close"]) / lot[fm]).median())
            vol_is_contracts = ratio > 0.3
        else:
            vol_is_contracts = exchange == "nse"
        stats["udiff_volume_unit"] = "contracts" if vol_is_contracts else "quantity"
        out["volume_contracts"] = vol if vol_is_contracts else vol / lot
        out["oi_contracts"] = oi / lot
        out["chg_oi_contracts"] = chg / lot
        out["lot_size"] = lot
        out["lot_src"] = "file"
    elif fmt == "legacy_nse":
        out["symbol"] = _str(r["SYMBOL"])
        out["expiry"] = pd.to_datetime(_str(r["EXPIRY_DT"]), format="%d-%b-%Y", errors="coerce")
        out["strike"] = _num(r["STRIKE_PR"])
        ot = r["OPTION_TYP"] if "OPTION_TYP" in r else r["OPTIONTYPE"]
        out["opt_type"] = _str(ot).where(out["inst"] == "OPT")
        for a, b in [("open", "OPEN"), ("high", "HIGH"), ("low", "LOW"), ("close", "CLOSE"), ("settle", "SETTLE_PR")]:
            out[a] = _num(r[b])
        out["volume_contracts"] = _num(r["CONTRACTS"])
        out["turnover_rs"] = _num(r["VAL_INLAKH"]) * 1e5
        oi_sh, chg_sh = _num(r["OPEN_INT"]), _num(r["CHG_IN_OI"])
        imp = implied_multiplier(out[out["inst"] == "FUT"])
        imp_e = implied_multiplier(out[out["inst"] == "FUT"], by_expiry=True)
        by_sym = {}
        for (s2, e2), v in imp_e.items():
            by_sym.setdefault(s2, []).append(((s2, e2), v))
        lots = {}
        tmp = pd.DataFrame({"symbol": out["symbol"], "expiry": out["expiry"], "oi": oi_sh, "chg": chg_sh})
        for (s, e), g in tmp.groupby(["symbol", "expiry"], sort=False):
            cands = [imp_e.get((s, e), np.nan)] + [v for (s2, e2), v in by_sym.get(s, [])] + [imp.get(s, np.nan)]
            lots[(s, e)] = estimate_lot(np.r_[g["oi"].values, g["chg"].values], cands)
        key = list(zip(out["symbol"], out["expiry"]))
        out["lot_size"] = [lots[k][0] for k in key]
        out["lot_src"] = [lots[k][1] for k in key]
        out["oi_contracts"] = oi_sh / out["lot_size"]
        out["chg_oi_contracts"] = chg_sh / out["lot_size"]
    else:  # legacy_bse
        series = _str(r["Symbol"])
        out["symbol"] = series.str.extract(r"^([A-Z&\-]+?)(?=\d{2})")[0]
        out["expiry"] = pd.to_datetime(_str(r["Expiry"]), format="%d %b %Y", errors="coerce")
        out["strike"] = _num(r["Strike"])
        out["opt_type"] = _str(r["Option Type"]).map({"CALL": "CE", "PUT": "PE", "CE": "CE", "PE": "PE"})
        for a, b in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")]:
            out[a] = _num(r[b])
        out["volume_contracts"] = _num(r["Contracts Traded"])
        out["turnover_rs"] = _num(r["Turnover(Rs Lakhs)"]) * 1e5
        out["oi_contracts"] = _num(r["Open Interest"])
        out["chg_oi_contracts"] = _num(r["Change in OI"])
        imp = implied_multiplier(out[out["inst"] == "FUT"].assign(settle=np.nan))
        out["lot_size"] = out["symbol"].map(imp.round())
        out["lot_src"] = np.where(out["lot_size"].notna(), "turnover", "none")

    out = out[out["symbol"].notna() & out["expiry"].notna()]
    key = ["symbol", "inst", "expiry", "strike", "opt_type"]
    dup = out.duplicated(key, keep=False)
    stats["duplicate_rows"] = int(out.duplicated(key).sum())
    if dup.any():
        out = out.sort_values("volume_contracts", ascending=False).drop_duplicates(key).sort_index()
    out = _finish(out, date, exchange, fmt)
    return out, stats


# ----------------------------------------------------------------------------- CM / index / participant OI
CM_COLS = ["date", "symbol", "open", "high", "low", "close", "prev_close"]


def parse_cm(raw: pd.DataFrame, date: str) -> pd.DataFrame:
    if "TckrSymb" in raw:
        r = raw[_str(raw["SctySrs"]).isin(["EQ", "BE"])]
        out = pd.DataFrame({"symbol": _str(r["TckrSymb"]), "series": _str(r["SctySrs"]),
                            "open": _num(r["OpnPric"]), "high": _num(r["HghPric"]), "low": _num(r["LwPric"]),
                            "close": _num(r["ClsPric"]), "prev_close": _num(r["PrvsClsgPric"])})
    else:
        r = raw[_str(raw["SERIES"]).isin(["EQ", "BE"])]
        out = pd.DataFrame({"symbol": _str(r["SYMBOL"]), "series": _str(r["SERIES"]),
                            "open": _num(r["OPEN"]), "high": _num(r["HIGH"]), "low": _num(r["LOW"]),
                            "close": _num(r["CLOSE"]), "prev_close": _num(r["PREVCLOSE"])})
    # prefer EQ over BE when a symbol has both
    out = out.sort_values("series", key=lambda s: s.ne("EQ")).drop_duplicates("symbol")
    out["date"] = pd.Timestamp(date)
    return out[CM_COLS].reset_index(drop=True)


def parse_index(raw: pd.DataFrame, date: str) -> pd.DataFrame:
    name = _str(raw["Index Name"]).str.upper()
    sym = name.map(INDEX_NAME_MAP)
    r = raw[sym.notna()]
    close = _num(r["Closing Index Value"])
    chg = _num(r["Points Change"])
    out = pd.DataFrame({"symbol": sym[sym.notna()], "open": _num(r["Open Index Value"]),
                        "high": _num(r["High Index Value"]), "low": _num(r["Low Index Value"]),
                        "close": close, "prev_close": close - chg})
    for c in ("open", "high", "low"):  # some files give '-' / 0 for O/H/L of VIX-like series
        out.loc[out[c] <= 0, c] = np.nan
    out["date"] = pd.Timestamp(date)
    return out.drop_duplicates("symbol")[CM_COLS].reset_index(drop=True)


POI_MAP = {"Client Type": "client_type", "Future Index Long": "fut_idx_long", "Future Index Short": "fut_idx_short",
           "Future Stock Long": "fut_stk_long", "Future Stock Short": "fut_stk_short",
           "Option Index Call Long": "opt_idx_call_long", "Option Index Put Long": "opt_idx_put_long",
           "Option Index Call Short": "opt_idx_call_short", "Option Index Put Short": "opt_idx_put_short",
           "Option Stock Call Long": "opt_stk_call_long", "Option Stock Put Long": "opt_stk_put_long",
           "Option Stock Call Short": "opt_stk_call_short", "Option Stock Put Short": "opt_stk_put_short",
           "Total Long Contracts": "total_long", "Total Short Contracts": "total_short"}


def parse_participant_oi(text: str, date: str) -> pd.DataFrame:
    lines = [l for l in text.splitlines() if l.strip()]
    hi = next(i for i, l in enumerate(lines) if l.strip().strip('"').startswith("Client Type"))
    rows = list(csv.reader(lines[hi:]))
    hdr = [h.strip() for h in rows[0]]
    df = pd.DataFrame([r[:len(hdr)] for r in rows[1:] if r and r[0].strip()], columns=hdr)
    df = df.rename(columns=POI_MAP)[[v for k, v in POI_MAP.items() if k in hdr]]
    df["client_type"] = _str(df["client_type"])
    for c in df.columns[1:]:
        df[c] = _num(df[c])
    df.insert(0, "date", pd.Timestamp(date))
    return df
