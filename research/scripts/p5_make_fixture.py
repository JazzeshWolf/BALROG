"""Build the buyConviction regression fixture from the real archive snapshot.

    python -I research/scripts/p5_make_fixture.py [--src _ext/xerxes-eod-archive/archive/2026-11-12/indices/nifty.json.gz]
        [--out engine/fixtures/nifty_2026-11-12.json] [--pin]

Keeps only what the engine reads: nearest three expiries, strikes within +-6% of spot, metrics subset, ivHistory.
--pin also writes <out>.expected.json with the engine's current top-5 (re-pin deliberately after a model refit).
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

KEEP_TOP = ["asOf", "stale", "source", "index", "name", "expiryKind", "lotSize", "future", "defaultExpiry",
            "ivHistory", "term"]
KEEP_METRICS = ["atmStrike", "atmIv", "ivPercentile", "rv20", "sigmaForecast", "vrp"]
PIN_KEYS = ["expiry", "dte", "strike", "side", "bid", "ask", "mid", "iv_mid", "delta", "friction_pct",
            "buyConviction", "tier", "expected_net_R", "regime"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "_ext", "xerxes-eod-archive", "archive", "2026-11-12", "indices",
                                                  "nifty.json.gz"))
    ap.add_argument("--out", default=os.path.join(ROOT, "engine", "fixtures", "nifty_2026-11-12.json"))
    ap.add_argument("--pin", action="store_true")
    a = ap.parse_args()
    with gzip.open(a.src, "rt", encoding="utf-8") as f:
        s = json.load(f)
    spot = s["spot"]["price"]
    out = {k: s[k] for k in KEEP_TOP if k in s}
    out["spot"] = {k: s["spot"][k] for k in ("price", "prevClose", "changePct")}
    out["vix"] = {"value": (s.get("vix") or {}).get("value")}
    exps = sorted(s["expiries"].items(), key=lambda kv: kv[1]["dte"])[:3]
    out["expiries"] = {}
    for k, e in exps:
        out["expiries"][k] = {"label": e.get("label"), "date": e["date"], "dte": e["dte"], "tYears": e["tYears"],
                              "metrics": {m: e["metrics"].get(m) for m in KEEP_METRICS},
                              "chain": [r for r in e["chain"] if abs(r["strike"] / spot - 1) <= 0.06]}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=0)
    n = sum(len(e["chain"]) for e in out["expiries"].values())
    print(f"fixture {a.out}: {len(out['expiries'])} expiries, {n} chain rows, ivHistory {len(out.get('ivHistory', []))}")
    if a.pin:
        from engine import buy_conviction as bc
        res = bc.score_snapshot(out, top=5)
        exp = {"source": os.path.relpath(a.src, ROOT).replace("\\", "/"), "model_variant": res["model_variant"],
               "regime_counts": res["regime_counts"], "top5": [{k: x.get(k) for k in PIN_KEYS} for x in res["top"]]}
        p = a.out.replace(".json", ".expected.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(exp, f, indent=1)
        for x in exp["top5"]:
            print(x)


if __name__ == "__main__":
    main()
