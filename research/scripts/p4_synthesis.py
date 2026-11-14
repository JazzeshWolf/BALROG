"""P4 synthesis: one row per registered hypothesis x class group, across-family Benjamini-Hochberg, trial accounting
and deflated Sharpe of the best-looking OOS results.

    python -I research/scripts/p4_synthesis.py [--out research/out] [--bt data/bt]

Numbers are pulled from the family JSONs (p4_vol_dealer_events, p4_momentum, p4_contract_exits, p4_intraday);
no backtest is re-run. The only computation on trade rows is the per-trade Sharpe / skew / kurtosis of the best
contract cell (the contract JSON does not store them), read from data/bt/trades.

Primary test set for BH: one p per registered hypothesis x class group (index, stocks):
  entry hypotheses  p = OOS signal minus B1 (day-clustered bootstrap, two-sided) as reported by each family;
  V5                p = the registered stocks-minus-index contrast (one test);
  M8                p = expiry-day minus other-day entries (index; stocks untestable);
  M6/M7 (SYNTHETIC) p = signal minus random-entry baseline;
  C1-C4             p = the registered OOS contrast (C3 primary = RMR(HAR), C4 primary = gamma/theta(rv20));
  X1-X4             p = IS-chosen cell minus X0 at h_max = 3.
Sub-class rows (NIFTY/BANKNIFTY/SENSEX), sensitivity lines, C5 (descriptive) and unregistered runs are excluded.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from research.lib import backtest as bt

OUT = os.path.join(ROOT, "research", "out")
SCRIPT = "research/scripts/p4_synthesis.py"
SENS_PAT = re.compile(r"(?:2x|open1|cap5|haircut|calendar_theta|nonoverlap|known_time|unmatched|_frozen|frozen_|chosen )",
                      re.I)
BASELINE_IDS = {"B1", "B1_M6", "B1_M7", "B2", "X0"}


def J(name):
    with open(os.path.join(OUT, name), encoding="utf-8") as f:
        return json.load(f)


def f3(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else float(x)


def rows_vol(v):
    out = []
    for hid, per in v["results"].items():
        for key, grp in (("INDEX", "index"), ("STOCKS", "stocks")):
            r = per.get(key)
            if r is None or hid == "V5":
                continue
            if hid == "V4" and grp == "stocks":
                continue  # index-only by register; stock run logged registered=no
            if hid.startswith("E") and grp == "index":
                continue
            IS, OOS = r["IS"]["stats"], r["OOS"]["stats"]
            crit = r.get("criteria", {})
            out.append(dict(id=hid, grp=grp, family="vol_dealer_events", kind="veto" if hid in ("V3", "D4", "E1") else "entry",
                            is_net=IS.get("net_mean_R"), is_gross=IS.get("gross_mean_R"),
                            oos_net=OOS.get("net_mean_R"), oos_gross=OOS.get("gross_mean_R"),
                            oos_ci=[OOS.get("ci_lo"), OOS.get("ci_hi")], n=OOS.get("n"), n_days=OOS.get("n_days"),
                            diff=OOS.get("diff"), p=OOS.get("p"), net_2x=OOS.get("net_2x"),
                            crit={k: bool(crit.get(k)) for k in "123456"}, family_verdict=r.get("verdict"),
                            params=r.get("params")))
    c = v["V5_contrast"]
    out.append(dict(id="V5", grp="stocks-index", family="vol_dealer_events", kind="contrast",
                    is_net=c["IS"]["diff"], is_gross=None, oos_net=c["OOS"]["diff"], oos_gross=None,
                    oos_ci=c["OOS"]["ci"], n=c["OOS"]["n_stocks"] + c["OOS"]["n_index"], n_days=c["OOS"]["n_days"],
                    diff=c["OOS"]["diff"], p=c["OOS"]["p"], net_2x=None, crit={}, family_verdict=c["verdict"],
                    note="registered contrast: stocks > indices expected; OOS diff is negative"))
    return out


def rows_mom(m):
    out = []
    key = {"c1_net_pos_ci": "1", "c2_beats_b1_bh": "2", "c3_is_oos_agree": "3", "c4_plateau": "4",
           "c5_sample": "5", "c6_2x_spread": "6"}
    for hid, per in m["hypotheses"].items():
        for grp in ("index", "stocks"):
            r = per.get(grp)
            if r is None:
                continue
            IS, OOS = r["IS"]["signal"], r["OOS"]["signal"]
            d = r["OOS"]["diff_vs_b1"]
            out.append(dict(id=hid, grp=grp, family="momentum", kind="entry",
                            is_net=IS.get("net_mean_R"), is_gross=IS.get("gross_mean_R"),
                            oos_net=OOS.get("net_mean_R"), oos_gross=OOS.get("gross_mean_R"),
                            oos_ci=OOS.get("ci") or OOS.get("net_ci"), n=OOS.get("n"), n_days=OOS.get("n_days"),
                            diff=d.get("diff"), p=d.get("p"), net_2x=OOS.get("net_mean_R_2x"),
                            crit={key[k]: bool(v) for k, v in r["criteria"].items() if k in key},
                            family_verdict=r["verdict"], params=r["OOS"].get("frozen")))
    e = m["M8"]
    o, i = e["OOS"]["index"], e["IS"]["index"]
    out.append(dict(id="M8", grp="index", family="momentum", kind="veto", is_net=i["diff_expiry_minus_other"]["diff"],
                    is_gross=None, oos_net=o["diff_expiry_minus_other"]["diff"], oos_gross=None,
                    oos_ci=o["diff_expiry_minus_other"]["ci"], n=o["expiry_day"]["n"], n_days=o["expiry_day"]["n_days"],
                    diff=o["diff_expiry_minus_other"]["diff"], p=o["diff_expiry_minus_other"]["p"], net_2x=None,
                    crit={}, family_verdict=e["verdict"]["index"], note="expiry-day minus other-day entries"))
    return out


def rows_intraday(it):
    s2 = it["stage2"]
    out = []
    for hid, mk in (("M6", "m6"), ("M7", "m7")):
        for grp, g in (("index", "index"), ("stocks", "stock")):
            o, i = s2[mk][f"OOS|{g}"], s2[mk][f"IS|{g}"]
            plateau = s2["verdicts"].get("plateau_ok", {}).get(g, False) if hid == "M6" else False
            crit = {"1": bool(o["ci_lo"] > 0), "3": bool(np.sign(o["net_mean_R"]) == np.sign(i["net_mean_R"])
                                                          and abs(o["net_mean_R"]) >= 0.5 * abs(i["net_mean_R"])),
                    "4": bool(plateau), "5": bool(o["n_trades"] >= 100 and o["n_days"] >= 40),
                    "6": bool(o["net_mean_R_2x"] >= 0)}
            out.append(dict(id=hid, grp=grp, family="intraday (SYNTHETIC)", kind="entry", is_net=i["net_mean_R"],
                            is_gross=i["gross_mean_R"], oos_net=o["net_mean_R"], oos_gross=o["gross_mean_R"],
                            oos_ci=[o["ci_lo"], o["ci_hi"]], n=o["n_trades"], n_days=o["n_days"],
                            diff=o["diff_vs_b1"], p=o["p_vs_b1"], net_2x=o["net_mean_R_2x"], crit=crit,
                            family_verdict=s2["verdicts"][hid][g], note="SYNTHETIC option prices; p vs random entry"))
    return out


def rows_contract(c):
    out = []
    V = c["verdicts"]
    for hid, key in (("C1", "C1"), ("C2", "C2"), ("C3", "C3:rmr_har"), ("C4", "C4:gamma_theta_rv")):
        for grp in ("index", "stocks"):
            r = V["C"][f"{key}|{grp}|all"]
            if hid == "C2":
                det = r["detail"]
                oos = min(det["7-10"]["oos"], det["36+"]["oos"])
                is_ = min(det["7-10"]["is"], det["36+"]["is"])
                ci = None
            else:
                oos, is_, ci = r.get("oos_diff"), r.get("is_diff"), r.get("oos_ci")
            out.append(dict(id=hid, grp=grp, family="contract", kind="contrast", is_net=is_, is_gross=None,
                            oos_net=oos, oos_gross=None, oos_ci=ci, n=None, n_days=None, diff=oos, p=r.get("p_raw"),
                            net_2x=None, crit={}, family_verdict=r["verdict"],
                            note={"C1": "d.70 - d.15 (+monotone)", "C2": "interior 11-35 minus min(7-10, 36+)",
                                  "C3": "low - high RMR(HAR) tercile", "C4": "high - low gamma/theta(rv) tercile"}[hid]))
    for hid in ("X1", "X2", "X3", "X4"):
        for grp in ("index", "stocks"):
            r = V["X"][f"{hid}|{grp}|h3"]
            out.append(dict(id=hid, grp=grp, family="exits", kind="contrast", is_net=r.get("is_diff"), is_gross=None,
                            oos_net=r.get("oos_diff"), oos_gross=None, oos_ci=r.get("oos_ci"), n=None, n_days=None,
                            diff=r.get("oos_diff"), p=r.get("p_raw"), net_2x=None, crit={},
                            family_verdict=r["verdict"], note=f"{r.get('chosen_on_IS')} minus X0, h_max 3"))
    return out


def trial_accounting(path):
    t = pd.read_csv(path)
    base = t[~t["id"].isin(BASELINE_IDS)]
    sens = base["variant"].astype(str).str.contains(SENS_PAT)
    core = base[~sens]
    var = core["variant"].astype(str).str.replace(r"_?(IS|OOS)$", "", regex=True)
    distinct = pd.DataFrame({"id": core["id"], "v": var, "cls": core["cls"]}).drop_duplicates()
    return {"total_rows": int(len(t)), "baseline_rows": int(t["id"].isin(BASELINE_IDS).sum()),
            "sensitivity_rows": int(sens.sum()), "registered_rows": int((t["registered"] == "yes").sum()),
            "unregistered_rows": int((t["registered"] == "no").sum()),
            "distinct_variants": int(len(distinct)),
            "distinct_incl_baselines_and_sens": int(t.drop_duplicates(["id", "variant", "cls"]).shape[0]),
            "by_id": distinct.groupby("id").size().sort_values(ascending=False).head(12).to_dict()}


def cell_returns(bt_dir, cls, delta, bucket):
    parts = []
    for f in sorted(glob.glob(os.path.join(bt_dir, "trades", "year=*.parquet"))):
        x = pd.read_parquet(f, columns=["date", "exchange", "cls", "dte_bucket", "delta_target", "net_R_h3",
                                         "ca_break_h3", "incomplete_h3", "settled_h3", "nomark_h3"],
                            filters=[("sample", "==", "OOS"), ("cls", "==", cls), ("dte_bucket", "==", bucket)])
        if x.empty:
            continue
        x = x[np.isclose(x["delta_target"], delta)]
        if cls == "stock":
            x = x[x["exchange"].astype(str) == "nse"]
        ok = (x["net_R_h3"].notna() & ~x["ca_break_h3"].astype(bool) & ~x["incomplete_h3"].astype(bool)
              & ~x["settled_h3"].astype(bool) & ~x["nomark_h3"].astype(bool))
        parts.append(x.loc[ok, "net_R_h3"].to_numpy(float))
    return np.concatenate(parts) if parts else np.array([])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--bt", default=os.path.join(ROOT, "data", "bt"))
    ap.add_argument("--trials", default=os.path.join(ROOT, "research", "trials_log.csv"))
    a = ap.parse_args()

    rows = rows_vol(J("p4_vol_dealer_events.json")) + rows_mom(J("p4_momentum.json")) + \
        rows_intraday(J("p4_intraday.json")) + rows_contract(J("p4_contract_exits.json"))
    p = np.array([np.nan if r["p"] is None else float(r["p"]) for r in rows])
    q = bt.bh_adjust(p)
    for r, qq in zip(rows, q):
        r["q_bh"] = f3(qq)
        exp_neg = r["kind"] == "veto"
        good_dir = (r["diff"] is not None) and ((r["diff"] < 0) if exp_neg else (r["diff"] > 0))
        r["bh_survivor"] = bool(np.isfinite(qq) and qq < 0.05 and good_dir)
        if r["crit"]:
            r["crit"]["2"] = r["bh_survivor"]
            all_ok = all(r["crit"].get(k, False) for k in "123456")
        else:
            all_ok = None
        fv = str(r["family_verdict"])
        if all_ok:
            r["final_verdict"] = "VETO-PASS" if exp_neg else "PASS"
        elif r["kind"] == "contrast":
            r["final_verdict"] = (fv + (" | BH q<.05" if r["bh_survivor"] else " | BH n.s.")).strip()
        elif "LESS-BAD" in fv.upper() or "less bad" in fv:
            r["final_verdict"] = "LESS-BAD-NOT-EDGE" + (" (BH q<.05)" if r["bh_survivor"] else " (BH n.s.)")
        elif "ANECDOTAL" in fv.upper():
            r["final_verdict"] = "ANECDOTAL"
        else:
            r["final_verdict"] = "FAIL"
        for k in ("is_net", "is_gross", "oos_net", "oos_gross", "diff", "p", "net_2x"):
            r[k] = f3(r[k])

    ta = trial_accounting(a.trials)
    N = ta["distinct_variants"]
    m = J("p4_momentum.json")["hypotheses"]["M4"]["index"]["OOS"]["deflated_sharpe"]
    v2 = J("p4_vol_dealer_events.json")["results"]["V2"]["INDEX"]["OOS"]["deflated_sharpe"]
    e2 = J("p4_vol_dealer_events.json")["results"]["E2"]["STOCKS"]["OOS"]["deflated_sharpe"]
    best = J("p4_contract_exits.json")["best_cell_frozen_oos"]
    dsr = {}
    for name, d in (("M4 index gap-and-hold", m), ("V2 index IV pct<20", v2), ("E2 pre-earnings strangle", e2)):
        r = bt.deflated_sharpe(n_trials=N, sr=d["sr"], n_obs=d["n_obs"], skew=d["skew"], kurt=d["kurt"])
        r1 = bt.deflated_sharpe(n_trials=1, sr=d["sr"], n_obs=d["n_obs"], skew=d["skew"], kurt=d["kurt"])
        dsr[name] = {**r, "psr_no_haircut": r1["dsr"], "n_trials": N}
    bc = best["index"]
    x = cell_returns(a.bt, "index", bc["delta"], bc["bucket"])
    r = bt.deflated_sharpe(returns=x, n_trials=N)
    dsr[f"best contract cell index d{bc['delta']} {bc['bucket']}"] = {**r, "psr_no_haircut": bt.deflated_sharpe(
        returns=x, n_trials=1)["dsr"], "n_trials": N, "note": "overlapping trades; SR per trade"}

    res = {"script": SCRIPT, "n_primary_tests": int(np.isfinite(p).sum()), "rows": rows,
           "bh_survivors": [f"{r['id']}|{r['grp']}" for r in rows if r["bh_survivor"]],
           "passes": [f"{r['id']}|{r['grp']}" for r in rows if r["final_verdict"] in ("PASS", "VETO-PASS")],
           "trials": ta, "deflated_sharpe": dsr}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "p4_synthesis.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))

    def fm(x, s="+.3f"):
        return "n/a" if x is None else format(x, s)

    L = ["# P4 synthesis - all families, across-family BH, deflated Sharpe",
         f"Generated by `{SCRIPT}` from the family JSONs (no backtest re-run). {res['n_primary_tests']} primary tests "
         "(one per registered hypothesis x class group); q = Benjamini-Hochberg across all of them. Entry rows: "
         "net (gross) mean R; contrast rows (V5, M8, C*, X*): the registered contrast. Crit = §0 1-6 with 2 = BH q<.05 "
         "in the expected direction. M6/M7 SYNTHETIC.", "",
         "| hyp | grp | IS net (gross) | OOS net (gross) | n / days | diff vs B1 | p | q(BH) | crit 1-6 | final |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cr = "".join(("✓" if r["crit"].get(k) else "✗") for k in "123456") if r["crit"] else "-"
        g = lambda a_, b_: f"{fm(r[a_])} ({fm(r[b_])})" if r[b_] is not None else fm(r[a_])
        nd = f"{r['n']} / {r['n_days']}" if r["n"] is not None else "-"
        L.append(f"| {r['id']} | {r['grp']} | {g('is_net', 'is_gross')} | {g('oos_net', 'oos_gross')} | {nd} | "
                 f"{fm(r['diff'])} | {fm(r['p'], '.4f')} | {fm(r['q_bh'], '.3f')} | {cr} | {r['final_verdict']} |")
    L += ["", f"**BH survivors (q<.05, expected direction):** {', '.join(res['bh_survivors']) or 'none'}. "
          f"**§0 passes:** {', '.join(res['passes']) or 'none'}.",
          f"Trials: {ta['total_rows']} ledger rows; {ta['distinct_variants']} distinct strategy variants "
          f"(id x variant x cls, IS/OOS merged, {ta['sensitivity_rows']} sensitivity and {ta['baseline_rows']} baseline "
          f"rows excluded) = N for the deflated Sharpe.", "",
          "| best-looking OOS result | n | SR/trade | E[max SR] N | DSR | PSR (N=1) |", "|---|---|---|---|---|---|"]
    for k, d in dsr.items():
        L.append(f"| {k} | {d['n_obs']} | {d['sr']:+.3f} | {d['sr0']:.3f} | {d['dsr']:.3f} | {d['psr_no_haircut']:.3f} |")
    with open(os.path.join(a.out, "p4_synthesis.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"primary tests {res['n_primary_tests']}; BH survivors: {res['bh_survivors']}")
    print(f"passes: {res['passes']}")
    print(f"trials: {ta}")
    for k, d in dsr.items():
        print(f"DSR {k}: n={d['n_obs']} sr={d['sr']:+.3f} sr0={d['sr0']:.3f} dsr={d['dsr']:.4f} psr1={d['psr_no_haircut']:.3f}")
    print(f"md lines: {len(L)}")


if __name__ == "__main__":
    main()
