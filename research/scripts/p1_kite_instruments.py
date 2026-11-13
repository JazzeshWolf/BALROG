"""P1: summarise the public Kite Connect instruments dump (live NFO/BFO derivatives).

Downloads https://api.kite.trade/instruments (public, no auth) to data/raw/kite/
and writes research/out/p1_kite_instruments.json.
Usage: python research/scripts/p1_kite_instruments.py [--no-download]
"""
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "kite"
OUT = ROOT / "research" / "out" / "p1_kite_instruments.json"
URL = "https://api.kite.trade/instruments"
INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]
INDEX_NAMES = set(INDICES) | {"NIFTYNXT50", "BANKEX", "SENSEX50"}


def download() -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    dest = RAW / f"instruments_{date.today():%Y%m%d}.csv"
    if "--no-download" in sys.argv and dest.exists():
        return dest
    r = requests.get(URL, timeout=120)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def expiries(df: pd.DataFrame) -> list:
    out = []
    for exp, g in df.groupby("expiry"):
        out.append({
            "expiry": exp.strftime("%Y-%m-%d"),
            "weekday": exp.strftime("%a"),
            "lot_size": sorted(int(x) for x in g["lot_size"].unique()),
            "n_contracts": int(len(g)),
        })
    return out


def main() -> int:
    path = download()
    df = pd.read_csv(path, low_memory=False)
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce")
    deriv = df[df["exchange"].isin(["NFO", "BFO"])].copy()
    deriv["kind"] = deriv["instrument_type"].map({"CE": "OPT", "PE": "OPT", "FUT": "FUT"})

    counts = (deriv.groupby(["exchange", "kind"]).size().unstack(fill_value=0).to_dict("index"))
    opts = deriv[deriv["kind"] == "OPT"]

    idx = {}
    for name in INDICES:
        exch = "BFO" if name == "SENSEX" else "NFO"
        g = opts[(opts["name"] == name) & (opts["exchange"] == exch)]
        idx[name] = {"exchange": exch, "n_option_contracts": int(len(g)), "expiries": expiries(g)}

    stk = opts[(opts["exchange"] == "NFO") & (~opts["name"].isin(INDEX_NAMES))]
    stk_exp = expiries(stk)
    for e in stk_exp:  # lot sizes vary per stock; keep it compact
        e["lot_size"] = f"{len(e['lot_size'])} distinct"
    bfo_other = sorted(set(opts[opts["exchange"] == "BFO"]["name"]) - {"SENSEX"})

    res = {
        "source": URL,
        "file": str(path.relative_to(ROOT)).replace("\\", "/"),
        "downloaded": date.today().isoformat(),
        "n_rows_total": int(len(df)),
        "counts_by_exchange_kind": {k: {kk: int(vv) for kk, vv in v.items()} for k, v in counts.items()},
        "indices": idx,
        "nfo_stock_options": {
            "n_underlyings": int(stk["name"].nunique()),
            "expiries": stk_exp,
        },
        "bfo_other_option_underlyings": bfo_other,
        "deriv_expiry_range": {
            "earliest": deriv["expiry"].min().strftime("%Y-%m-%d"),
            "latest": deriv["expiry"].max().strftime("%Y-%m-%d"),
            "any_expiry_before_today": bool((deriv["expiry"].dt.date < date.today()).any()),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))

    print(f"rows={len(df)}  file={res['file']}")
    print("counts:", res["counts_by_exchange_kind"])
    for name, v in idx.items():
        ex = v["expiries"]
        near = ", ".join(f"{e['expiry']}({e['weekday']},lot {e['lot_size']})" for e in ex[:3])
        print(f"{name:10s} {v['exchange']} n_exp={len(ex)} near: {near}")
    print(f"NFO stock-option underlyings: {res['nfo_stock_options']['n_underlyings']}; expiries: "
          + ", ".join(f"{e['expiry']}({e['weekday']})" for e in stk_exp))
    print("BFO other option underlyings:", bfo_other)
    print("deriv expiry range:", res["deriv_expiry_range"])
    print("wrote", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
