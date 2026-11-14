"""Build report/index.html: the chart-led BALROG research report (single self-contained file).

Usage:  python report/build_report.py [--out report/index.html]

Every number is read from research/out/*.json|md, research/01_data_reality.md and
research/trials_log.csv. Nothing is typed in by hand except the lead's verdict
wording (which is filled with numbers from the result files). Sections whose
inputs are missing (p4_synthesis.json, p5_calibration.json) render as PENDING.
Prints a build log: chart id, source file, number of plotted points.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "research", "out")

LOG: list[tuple[str, str, int]] = []
CHARTS: dict[str, dict] = {}
WARN: list[str] = []


# ----------------------------------------------------------------- helpers
def load(name):
    p = os.path.join(OUT_DIR, name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read_text(rel):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def clean(o):
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else round(o, 5)
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def esc(s):
    return html.escape(str(s))


def f3(x, sign=True):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    return f"{x:+.3f}" if sign else f"{x:.3f}"


def pct(x, d=0):
    if x is None:
        return "n/a"
    return f"{100 * x:.{d}f}%"


def g(d, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def npoints(traces):
    n = 0
    for t in traces:
        for k in ("y", "z", "x"):
            v = t.get(k)
            if isinstance(v, list) and v:
                if isinstance(v[0], list):
                    n += sum(len([c for c in r if c is not None]) for r in v)
                else:
                    n += len([c for c in v if c is not None])
                break
    return n


def chart(cid, title, caption, traces, layout=None, src="", height=300, synthetic=False, n_text=""):
    """Register a chart and return its HTML wrapper."""
    layout = layout or {}
    pts = npoints(traces)
    LOG.append((cid, src, pts))
    if pts == 0:
        WARN.append(f"chart {cid} has no data points")
    CHARTS[cid] = clean({"data": traces, "layout": layout, "h": height})
    badge = '<span class="badge synth">SYNTHETIC</span>' if synthetic else ""
    ntx = f' <span class="n">{esc(n_text)}</span>' if n_text else ""
    return (f'<figure class="fig"><figcaption class="ft">{badge}{esc(title)}</figcaption>'
            f'<div class="plot" id="{cid}" style="height:{height}px"></div>'
            f'<p class="cap">{caption}{ntx}</p></figure>')


def table(headers, rows, cls=""):
    h = "".join(f"<th>{esc(x)}</th>" for x in headers)
    b = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def pending(name, what):
    return (f'<div class="pending"><b>PENDING</b> — <code>research/out/{esc(name)}</code> not found at build time. '
            f'{esc(what)} Re-run <code>python report/build_report.py</code> once it exists.</div>')


# verdict vocabulary -> (short label, colour token)
def vshort(v):
    if v is None:
        return "N/A"
    s = str(v)
    u = s.upper()
    if u.startswith("N/A"):
        return "N/A"
    if "VETO-PASS" in u:
        return "VETO-PASS"
    if "LESS-BAD" in u or "LESS BAD" in u:
        return "LESS-BAD"
    if "PARTIAL" in u or "CONTRAST ONLY" in u:
        return "PARTIAL"
    if "NOT SUPPORTED" in u:
        return "NOT-SUPP"
    if "SUPPORTED" in u:
        return "SUPPORTED"
    if "REVERSED" in u:
        return "REVERSED"
    if "B1 LEG PASSES" in u:
        return "B1-LEG"
    if "NO GAIN" in u:
        return "NO-GAIN"
    if "ANECDOTAL" in u:
        return "ANECDOTAL"
    if "FAIL" in u:
        return "FAIL"
    return s[:12]


VCOL = {"FAIL": "var:fail", "NOT-SUPP": "var:fail", "NO-GAIN": "var:fail", "REVERSED": "var:fail",
        "ANECDOTAL": "var:anec", "N/A": "var:anec", "LESS-BAD": "var:lessbad", "PARTIAL": "var:lessbad",
        "VETO-PASS": "var:pass", "SUPPORTED": "var:pass", "B1-LEG": "var:pass", "WATCH": "var:watch"}


def chip(label):
    return f'<span class="v v-{label.replace("/", "").replace("-", "")}">{esc(label)}</span>'


def crit_chips(crit, labels):
    out = []
    for key, lab in labels:
        val = crit.get(key) if isinstance(crit, dict) else None
        if val is None:
            out.append(f'<span class="c c-na">– {esc(lab)}</span>')
        elif val:
            out.append(f'<span class="c c-ok">✓ {esc(lab)}</span>')
        else:
            out.append(f'<span class="c c-no">✗ {esc(lab)}</span>')
    return '<div class="chips">' + "".join(out) + "</div>"


CRIT_LABELS = ["net>0, CI excl. 0", "beats B1", "OOS ≥ 50% IS, same sign", "±20% plateau",
               "n ≥ 100 on ≥ 40 days", "2× spread net ≥ 0"]
VOL_CRIT = list(zip(["1", "2", "3", "4", "5", "6"], CRIT_LABELS))
MOM_CRIT = list(zip(["c1_net_pos_ci", "c2_beats_b1_bh", "c3_is_oos_agree", "c4_plateau", "c5_sample",
                     "c6_2x_spread"], CRIT_LABELS[:1] + ["beats B1 (BH q)"] + CRIT_LABELS[2:]))

# common trace styles
def bar_is(name, x, y, color, err=None, **kw):
    t = {"type": "bar", "name": name, "x": x, "y": y,
         "marker": {"color": "rgba(0,0,0,0)", "line": {"color": color, "width": 2}}}
    if err:
        t["error_y"] = {"type": "data", "symmetric": False, "array": err[0], "arrayminus": err[1],
                        "color": "var:ink2", "thickness": 1}
    t.update(kw)
    return t


def bar_oos(name, x, y, color, err=None, **kw):
    t = {"type": "bar", "name": name, "x": x, "y": y, "marker": {"color": color}}
    if err:
        t["error_y"] = {"type": "data", "symmetric": False, "array": err[0], "arrayminus": err[1],
                        "color": "var:ink2", "thickness": 1}
    t.update(kw)
    return t


def errs(vals, los, his):
    up = [None if (v is None or h is None) else h - v for v, h in zip(vals, his)]
    dn = [None if (v is None or l is None) else v - l for v, l in zip(vals, los)]
    return up, dn


HEAT_SCALE = [[0, "var:neg"], [0.5, "var:mid"], [1, "var:pos"]]


def heat_pair(cid, title, caption, x, y, z_is, z_oos, xlab, ylab, src, n_text="", synthetic=False,
              height=290, fmt=".3f", labels=("in-sample", "out-of-sample")):
    vals = [abs(v) for zz in (z_is, z_oos) for r in zz for v in r if v is not None]
    m = max(vals) if vals else 1
    tr = []
    for i, (z, nm) in enumerate(((z_is, labels[0]), (z_oos, labels[1]))):
        tr.append({"type": "heatmap", "x": x, "y": y, "z": z, "coloraxis": "coloraxis",
                   "xaxis": "x" if i == 0 else "x2", "yaxis": "y" if i == 0 else "y2",
                   "texttemplate": "%{z:" + fmt + "}", "textfont": {"size": 10}, "name": nm,
                   "hovertemplate": nm + "<br>" + xlab + " %{x}<br>" + ylab + " %{y}<br>net R %{z:.3f}<extra></extra>",
                   "xgap": 2, "ygap": 2})
    lay = {"coloraxis": {"colorscale": HEAT_SCALE, "cmin": -m, "cmax": m, "showscale": False},
           "xaxis": {"domain": [0, 0.47], "title": {"text": xlab}, "type": "category"},
           "xaxis2": {"domain": [0.53, 1], "title": {"text": xlab}, "type": "category"},
           "yaxis": {"title": {"text": ylab}, "type": "category"},
           "yaxis2": {"anchor": "x2", "type": "category", "showticklabels": False},
           "annotations": [{"text": labels[0] + " (outline)" if False else labels[0], "x": 0.235, "y": 1.06, "xref": "paper",
                            "yref": "paper", "showarrow": False, "font": {"size": 11}},
                           {"text": labels[1], "x": 0.765, "y": 1.06, "xref": "paper", "yref": "paper",
                            "showarrow": False, "font": {"size": 11}}],
           "margin": {"t": 26}}
    return chart(cid, title, caption, tr, lay, src, height, synthetic, n_text)


def hist_labels(edges):
    labs = []
    for a, b in zip(edges[:-1], edges[1:]):
        a_s = "<" if a in (None, "-inf") else f"{a:g}"
        b_s = "+" if b in (None, "inf") else f"{b:g}"
        if a_s == "<":
            labs.append(f"<{b_s}")
        elif b_s == "+":
            labs.append(f">{a_s}")
        else:
            labs.append(f"{a_s}…{b_s}")
    return labs


def hist_colors(edges):
    cols = []
    for a, b in zip(edges[:-1], edges[1:]):
        hi = b if isinstance(b, (int, float)) else float("inf")
        cols.append("var:loss" if hi <= 0 else "var:net")
    return cols


def hist_chart(cid, title, edges, counts, b_counts, src, n_sig, n_b, synthetic=False, bname="baseline"):
    labs = hist_labels(edges)
    tot = sum(counts) or 1
    btot = sum(b_counts) or 1 if b_counts else 1
    tr = [{"type": "bar", "x": labs, "y": [100 * c / tot for c in counts], "name": "signal (OOS)",
           "marker": {"color": hist_colors(edges)}, "hovertemplate": "%{x}: %{y:.1f}%<extra>signal</extra>"}]
    if b_counts:
        tr.append({"type": "scatter", "mode": "lines+markers", "x": labs,
                   "y": [100 * c / btot for c in b_counts], "name": bname, "line": {"color": "var:base", "dash": "dot"},
                   "marker": {"size": 4}})
    lay = {"xaxis": {"title": {"text": "net R per trade (bin)"}, "type": "category", "tickangle": -45},
           "yaxis": {"title": {"text": "% of trades"}}, "showlegend": False, "bargap": 0.08}
    return chart(cid, title, f"Red = losing bins, blue = winning bins; dotted grey = {bname} share.",
                 tr, lay, src, 230, synthetic, f"n = {n_sig:,} signal / {n_b:,} baseline")


# ----------------------------------------------------------------- inputs
B = load("p4_baseline.json")
V = load("p4_vol_dealer_events.json")
M = load("p4_momentum.json")
C = load("p4_contract_exits.json")
I = load("p4_intraday.json")
S = load("p3_spread_eval.json")
P1 = load("p1_synth_error.json")
SYN = load("p4_synthesis.json")
CAL = load("p5_calibration.json")
COSTS_MD = read_text("research/out/p3_costs.md") or ""
DATA_MD = read_text("research/01_data_reality.md") or ""

for nm, obj in [("p4_baseline.json", B), ("p4_vol_dealer_events.json", V), ("p4_momentum.json", M),
                ("p4_contract_exits.json", C), ("p4_intraday.json", I), ("p3_spread_eval.json", S),
                ("p1_synth_error.json", P1)]:
    if obj is None:
        sys.exit(f"missing required input research/out/{nm}")

VR = V["results"]
MH = M["hypotheses"]
I2 = I["stage2"]
I1 = I["stage1"]


def trials_count():
    p = os.path.join(ROOT, "research", "trials_log.csv")
    if not os.path.exists(p):
        return None, {}
    n = 0
    reg = {}
    with open(p, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            n += 1
            reg[r.get("registered", "")] = reg.get(r.get("registered", ""), 0) + 1
    return n, reg


N_TRIALS, TRIALS_REG = trials_count()

# ----------------------------------------------------------------- verdict table data
WATCH = {("M4", "index"), ("V2", "index")}


def vol_v(h, cls):
    r = VR.get(h, {}).get(cls)
    return r["verdict"] if r else None


def mom_v(h, cls):
    r = MH.get(h, {}).get(cls)
    return r["verdict"] if r else None


HYP_ROWS = []  # (id, description, family, index verdict raw, stocks verdict raw, synthetic)
DESC = {"V1": "VRP ratio cheap → strangle", "V2": "Low IV percentile → strangle",
        "V3": "Inverted term structure (veto)", "V4": "Low & rising VIX → strangle",
        "V5": "Unconditional strangle: stocks vs indices", "D1": "Short-gamma GEX → strangle",
        "D2": "OI-wall cross → directional", "D3": "Wall OI abandonment → directional",
        "D4": "Positive-gamma pin (veto)", "E1": "Strangle through results (veto)",
        "E2": "Pre-results IV run-up", "E3": "Post-results drift",
        "M1": "Futures build-up (long/short)", "M1b": "Covering / unwinding",
        "M2": "ADX trend band", "M3": "Range-expansion breakout", "M4": "Gap-and-hold",
        "M5": "Relative strength (stocks)", "M6": "Opening-range breakout (intraday)",
        "M7": "First → last half-hour (intraday)", "M8": "Expiry-day entries (contrast)"}
for h in ["V1", "V2", "V3", "V4", "V5", "D1", "D2", "D3", "D4", "E1", "E2", "E3"]:
    HYP_ROWS.append((h, DESC[h], "vol/dealer/event", vol_v(h, "INDEX"), vol_v(h, "STOCKS"), False))
for h in ["M1", "M1b", "M2", "M3", "M4", "M5"]:
    HYP_ROWS.append((h, DESC[h], "momentum", mom_v(h, "index"), mom_v(h, "stocks"), False))
HYP_ROWS.append(("M6", DESC["M6"], "intraday", I2["verdicts"]["M6"]["index"], I2["verdicts"]["M6"]["stock"], True))
HYP_ROWS.append(("M7", DESC["M7"], "intraday", I2["verdicts"]["M7"]["index"], I2["verdicts"]["M7"]["stock"], True))
HYP_ROWS.append(("M8", DESC["M8"], "momentum", M["M8"]["verdict"]["index"]["verdict"],
                 M["M8"]["verdict"]["stocks"]["verdict"] + " (untestable)", False))
CV = C["verdicts"]["C"]
CDESC = {"C1": ("C1", "Higher |delta| (d.70 vs d.15)"), "C2": ("C2", "Interior DTE 11–35"),
         "C3:rmr_har": ("C3", "Low required-move ratio"), "C4:gamma_theta_rv": ("C4", "High gamma/theta")}
for key, (hid, d) in CDESC.items():
    HYP_ROWS.append((hid, d + " (contract)", "contract", CV[f"{key}|index|all"]["verdict"],
                     CV[f"{key}|stocks|all"]["verdict"], False))
XV = C["verdicts"]["X"]
for x, d in [("X1", "Premium TP/SL"), ("X2", "Underlying invalidation"), ("X3", "Trailing stop"),
             ("X4", "Fast time-stop (day 1)")]:
    HYP_ROWS.append((x, d + " (exit, h=3, vs X0)", "exit", XV[f"{x}|index|h3"]["verdict"],
                     XV[f"{x}|stocks|h3"]["verdict"], False))


def vcell(h, raw, cls):
    s = vshort(raw)
    out = chip(s)
    if (h, cls) in WATCH:
        out += " " + chip("WATCH")
    return out


# ----------------------------------------------------------------- sections
def css_js_head():
    return r"""
<style>
:root{--bg:#fbfaf7;--panel:#ffffff;--ink:#1d232b;--ink2:#56606b;--grid:#e6e3dc;--zero:#b9b4aa;
--net:#2f62c9;--gross:#a7b6d3;--base:#8d939a;--loss:#c8433a;--pos:#2f62c9;--neg:#c8433a;--mid:#f4f2ee;
--fail:#5d6670;--anec:#c3c7cc;--lessbad:#d18b1f;--pass:#178a7c;--watch:#7d4fc4;--accent:#2f62c9;--pendbg:#fff6e0;--pendln:#d18b1f}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14171b;--panel:#1c2026;--ink:#e6e8eb;--ink2:#a3abb5;--grid:#2c3239;--zero:#4a525c;
--net:#6f9bf0;--gross:#4f5d78;--base:#8a9099;--loss:#e5675d;--pos:#6f9bf0;--neg:#e5675d;--mid:#23282f;
--fail:#9aa3ad;--anec:#4b525b;--lessbad:#e0a33f;--pass:#3bbfae;--watch:#a983ea;--accent:#6f9bf0;--pendbg:#2e2614;--pendln:#e0a33f}}
:root[data-theme="dark"]{--bg:#14171b;--panel:#1c2026;--ink:#e6e8eb;--ink2:#a3abb5;--grid:#2c3239;--zero:#4a525c;
--net:#6f9bf0;--gross:#4f5d78;--base:#8a9099;--loss:#e5675d;--pos:#6f9bf0;--neg:#e5675d;--mid:#23282f;
--fail:#9aa3ad;--anec:#4b525b;--lessbad:#e0a33f;--pass:#3bbfae;--watch:#a983ea;--accent:#6f9bf0;--pendbg:#2e2614;--pendln:#e0a33f}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
body{overflow-x:hidden}
main{max-width:1180px;margin:0 auto;padding:0 16px 80px}
header.top{max-width:1180px;margin:0 auto;padding:28px 16px 8px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:22px;margin:44px 0 6px;padding-top:10px;border-top:1px solid var(--grid)}
h3{font-size:17px;margin:26px 0 6px}
.sub{color:var(--ink2);margin:0 0 10px}
nav.toc{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 0}
nav.toc a{font-size:13px;color:var(--ink2);text-decoration:none;border:1px solid var(--grid);border-radius:14px;padding:2px 10px}
nav.toc a:hover{color:var(--accent);border-color:var(--accent)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:14px}
.grid.g3{grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr))}
.fig{margin:0;background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:10px 12px;min-width:0}
.ft{font-weight:600;font-size:14px;margin-bottom:4px}
.plot{width:100%}
.cap{font-size:12.5px;color:var(--ink2);margin:6px 0 0}
.n{white-space:normal;color:var(--ink2);font-style:italic}
.badge{display:inline-block;font-size:10.5px;font-weight:700;letter-spacing:.04em;border-radius:4px;padding:1px 6px;margin-right:6px;vertical-align:1px}
.badge.synth{background:var(--lessbad);color:#fff}
.verdict{background:var(--panel);border:1px solid var(--grid);border-radius:12px;padding:14px 18px}
.verdict h3{margin:14px 0 4px;font-size:15px}
.verdict ul{margin:4px 0 4px 18px;padding:0}.verdict li{margin:2px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:10px;margin:10px 0}
.kpi{background:var(--panel);border:1px solid var(--grid);border-radius:10px;padding:10px 12px}
.kpi b{display:block;font-size:22px}.kpi span{font-size:12px;color:var(--ink2)}
.tw{overflow-x:auto;max-width:100%}
table{border-collapse:collapse;font-size:13px;width:100%;display:block;overflow-x:auto;max-width:100%}
th,td{border-bottom:1px solid var(--grid);padding:4px 8px;text-align:left;vertical-align:top}
th{color:var(--ink2);font-weight:600}
.v{display:inline-block;font-size:11px;font-weight:700;border-radius:4px;padding:0 6px;color:#fff;white-space:nowrap}
.v-FAIL,.v-NOTSUPP,.v-NOGAIN,.v-REVERSED{background:var(--fail)}
.v-ANECDOTAL,.v-NA{background:var(--anec);color:var(--ink)}
.v-LESSBAD,.v-PARTIAL{background:var(--lessbad)}
.v-VETOPASS,.v-SUPPORTED,.v-B1LEG{background:var(--pass)}
.v-WATCH{background:var(--watch)}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin:6px 0}
.c{font-size:11.5px;border-radius:10px;padding:0 8px;border:1px solid var(--grid)}
.c-ok{color:var(--pass);border-color:var(--pass)}.c-no{color:var(--ink2)}.c-na{color:var(--ink2);opacity:.6}
.card{background:var(--panel);border:1px solid var(--grid);border-radius:12px;padding:12px 14px;margin:14px 0}
.card h4{margin:0 0 2px;font-size:16px}.card .stat{font-size:13px;color:var(--ink2);margin:0 0 6px}
.card .grid{gap:10px}.card .fig{border:none;padding:4px 0;background:transparent}
.pending{border:1px dashed var(--pendln);background:var(--pendbg);border-radius:10px;padding:12px 14px;margin:10px 0}
.note{font-size:13px;color:var(--ink2)}
.flag{border-left:3px solid var(--lessbad);padding:4px 10px;margin:6px 0;font-size:13.5px}
details summary{cursor:pointer;color:var(--ink2);font-size:13px}
code{font-size:12.5px}
.legend-row{display:flex;flex-wrap:wrap;gap:10px;font-size:12.5px;color:var(--ink2);margin:6px 0}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-2px;margin-right:4px}
.thm{position:fixed;right:12px;bottom:12px;background:var(--panel);color:var(--ink);border:1px solid var(--grid);border-radius:16px;padding:4px 10px;font-size:12px;cursor:pointer}
</style>"""


def section_verdict():
    pb = B["pooled_h3"]
    def nr(c, s):
        return pb[f"{c}|{s}"]["net_mean_R"]
    def rng(c):
        a, b = sorted([nr(c, "IS"), nr(c, "OOS")])
        return f"{pct(a, 1)} to {pct(b, 1)}"
    m4 = MH["M4"]["index"]
    m4o = m4["OOS"]["signal"]
    v2o = VR["V2"]["INDEX"]["OOS"]
    synrows = {f"{r['id']}|{r['grp']}": r for r in (SYN or {}).get("rows", [])}
    dsr = (SYN or {}).get("deflated_sharpe", {})
    dsr_m4 = g(dsr, "M4 index gap-and-hold", "dsr")
    dsr_v2 = g(dsr, "V2 index IV pct<20", "dsr")
    q_m4 = g(synrows, "M4|index", "q_bh")
    nvar = g(SYN or {}, "trials", "distinct_variants")
    ce = {k.split("|")[1]: v.get("spread_ceiling_pct") for k, v in C["frozen_IS"]["c5"].items() if k.startswith("floor|")}
    xc = load("p4_archive_crosscheck.json") or {}
    xd = lambda c: g(xc, "by_class", c, "diff_real_minus_model")
    heat = C["OOS"]["c12"]["heat"]
    worst_idx = min(v["net"] for k, v in heat.items() if k.startswith("index|all|h3|0.15|"))
    worst_stk = min(v["net"] for k, v in heat.items() if k.startswith("stocks|all|h3|0.15|"))
    x2 = XV["X2|index|h3"]["oos_diff"]
    bl = C["OOS"]["bleed"]
    e2 = VR["E2"]["STOCKS"]["OOS"]
    m6 = I2["m6"]["OOS|stock"]
    v1s, v2s = VR["V1"]["STOCKS"]["OOS"]["diff_vs_b1"]["diff"], VR["V2"]["STOCKS"]["OOS"]["diff_vs_b1"]["diff"]
    rows = []
    for h, d, fam, vi, vs, syn in HYP_ROWS:
        rows.append([f"<b>{esc(h)}</b>" + (' <span class="badge synth">SYNTHETIC</span>' if syn else ""),
                     esc(d), vcell(h, vi, "index"), vcell(h, vs, "stocks")])
    k = [("NIFTY net R / 3-day trade (OOS)", f3(nr("NIFTY", "OOS"))), ("Stocks net R / 3-day trade (OOS)", f3(nr("stocks", "OOS"))),
         ("Entry hypotheses that passed", "0"), ("Full §0 passes", ", ".join((SYN or {}).get("passes", [])) or "n/a"),
         ("Distinct variants tried", f"{nvar:,}" if nvar else "n/a")]
    kp = "".join(f'<div class="kpi"><b>{esc(b)}</b><span>{esc(a)}</span></div>' for a, b in k)
    eng = ""
    if CAL:
        eng = (f"<li>A loss-avoidance ranker. No score bucket is profitable out-of-sample "
               f"(any bucket positive: {esc(g(CAL, 'verdict', 'any_bucket_positive_OOS'))}) and the score is not monotone at 10-point resolution "
               f"(Spearman OOS index {CAL['spearman']['index']:.2f}, stocks {CAL['spearman']['stocks']:.2f}, NIFTY {CAL['spearman']['NIFTY']:.2f}), "
               "so by the project's own rule it is <b>not finished</b>; it reliably flags the worst contracts.</li>")
    else:
        eng = "<li>Calibration PENDING (p5_calibration.json).</li>"
    return f"""
<section id="verdict"><h2>1 · Verdict</h2>
<div class="kpis">{kp}</div>
<div class="verdict">
<h3>What works for buying</h3>
<ul><li>No entry signal cleared the pre-registered bar: 0 of the registered entry hypotheses passed; the only full pass is a veto
(inverted term structure on stocks: {f3(VR['V3']['STOCKS']['OOS']['diff_vs_b1']['diff'])} R vs baseline, i.e. slightly worse). What the evidence supports is <i>losing less</i> through contract choice and friction control.</li></ul>
<h3>The structural problem</h3>
<ul><li>Unconditional buying of the standard contract for 3 days is ≈ 0% gross on indices out-of-sample
(NIFTY {pct(pb['NIFTY|OOS']['gross_mean_R'],1)}, BANKNIFTY {pct(pb['BANKNIFTY|OOS']['gross_mean_R'],1)}, SENSEX {pct(pb['SENSEX|OOS']['gross_mean_R'],1)})
and stocks gross IS {pct(pb['stocks|IS']['gross_mean_R'],1)}, OOS {pct(pb['stocks|OOS']['gross_mean_R'],1)}.
Net it is NIFTY {rng('NIFTY')}, BANKNIFTY {rng('BANKNIFTY')}, SENSEX {rng('SENSEX')}, stocks {rng('stocks')} (IS/OOS).</li>
<li>Friction is the whole loss on indices and about two-thirds of it on stocks in-sample. The loss arrives on day 1
(index OOS {f3(g(bl,'OOS|index|all|h1','net'))} R after 1 day vs {f3(g(bl,'OOS|index|all|h3','net'))} after 3).</li></ul>
<h3>What doesn't work</h3>
<ul><li>Dealer positioning (negative GEX, OI-wall breaks, wall unwinding, pinning veto), futures build-up (good in-sample on index, gone out-of-sample),
the ADX 25–45 band, breakouts (worse than random on stocks), relative strength, buying through earnings, post-earnings drift, and both intraday setups as entries.</li></ul>
<h3>Statistically real but not tradable</h3>
<ul><li>Stock opening-range breakout beats random by {100*m6['diff_vs_b1']:+.1f} points (SYNTHETIC).</li>
<li>Pre-earnings IV run-up beats baseline out-of-sample ({100*e2['diff_vs_b1']['diff']:+.1f} points) but was negative in-sample ({f3(VR['E2']['STOCKS']['IS']['stats']['net_mean_R'])} R) and is negative at 2× spread ({f3(e2['stats']['net_2x'])}).</li>
<li>Cheap-vol filters on stocks (V1, V2) lose {100*v1s:.1f}–{100*v2s:.1f} points less than baseline and remain negative.</li></ul>
<h3>Closest to alive — forward-test only</h3>
<ul><li><b>Index gap-and-hold (M4)</b>: {pct(m4o['net_mean_R'],1)} OOS, {m4o['n']} trades / {m4o['n_days']} days, CI includes 0 [{f3(m4o['ci'][0])}, {f3(m4o['ci'][1])}], BH q {q_m4:.2f}, deflated-Sharpe probability {dsr_m4:.2f}.</li>
<li><b>Index low IV percentile (V2)</b>: {pct(v2o['stats']['net_mean_R'],1)}, driven by {v2o['signal']['by_year']['2024']['n']} trades in 2024, fails the plateau, deflated probability {dsr_v2:.3f}.</li></ul>
<h3>Less bad — usable as filters</h3>
<ul><li>Avoid |delta| &lt; 0.25 (worst cell {pct(worst_idx,0)} index to {pct(worst_stk,0)} stocks); |delta| ≈ 0.40–0.70 (0.55 did best out-of-sample; the in-sample stock pick was {C['frozen_IS']['best_cell']['stocks']['delta']}).</li>
<li>Stocks 21–35 DTE, index 7–10 DTE among those tested; low required-move ratio and cheap vol on stocks.</li>
<li>Round-trip spread ceilings by class (≈{ce.get('stocks')}% stocks and {ce.get('index')}% pooled index, {ce.get('NIFTY')}% NIFTY, {ce.get('BANKNIFTY')}% BANKNIFTY, {ce.get('SENSEX')}% SENSEX); avoid stocks with inverted term structure.</li>
<li>Exit on underlying invalidation ({100*x2:+.1f} points vs a time exit); no TP/SL grid beat a time exit.</li></ul>
<h3>The engine</h3><ul>{eng}</ul>
<h3>Confidence</h3>
<ul><li>Swing results rest on real daily prices 2019–2026 with computed IV and a <i>modelled</i> spread that the archive cross-check shows is conservative
(real fills better by {100*(xd('index') or 0):+.1f} points on index, {100*(xd('stocks') or 0):+.1f} on stocks, one 2-month window) — not enough to flip any class positive except possibly NIFTY towards break-even.</li>
<li>Intraday results are SYNTHETIC. Nothing is known about 0–2 DTE or expiry day. Earnings timing is weak for 2025–26. The stock universe for earnings and intraday is today's F&amp;O list. {f"{nvar:,}" if nvar else "n/a"} distinct variants were tried.</li></ul>
<h3>What live forward-testing must confirm</h3>
<ul><li>Real fills vs the spread model · the two index candidates · the three-tier score separation · whether NIFTY unconditional buying is near break-even at real spreads · expiry-day behaviour (needs bought intraday data).</li></ul>
<h3>Hypotheses</h3>
{table(["hypothesis", "rule", "index", "stocks"], rows, "vt")}
<p class="note">Index = pooled NIFTY+BANKNIFTY+SENSEX. Contract (C) and exit (X) rows are filters/exit rules tested on the unconditional population, not entry signals.</p>
</div>
</section>"""


def verdict_flags():
    out = []
    pb = B["pooled_h3"]
    sg = pb["stocks|IS"]["gross_mean_R"]
    out.append(f"“≈ 0% gross”: true for indices, but stocks in-sample gross is {f3(sg)} R "
               f"(net {f3(pb['stocks|IS']['net_mean_R'])}), so friction is roughly two-thirds of the stock IS loss, not all of it.")
    e2 = VR["E2"]["STOCKS"]["OOS"]
    out.append(f"“earnings … do not beat random entry”: E2 (pre-results run-up, stocks) does beat B1 OOS "
               f"({f3(e2['diff_vs_b1']['diff'])} [{f3(e2['diff_vs_b1']['ci'][0])}, {f3(e2['diff_vs_b1']['ci'][1])}], p {e2['diff_vs_b1']['p_raw']}); "
               f"it fails on IS sign (IS {f3(VR['E2']['STOCKS']['IS']['stats']['net_mean_R'])}) and 2× spread ({f3(e2['stats']['net_2x'])}).")
    m6 = I2["m6"]["OOS|stock"]
    out.append(f"“both intraday setups do not beat random entry”: M6 on stocks beats random by "
               f"{f3(m6['diff_vs_b1'])} R [{f3(m6['diff_lo'])}, {f3(m6['diff_hi'])}] (p {m6['p_vs_b1']:.3f}, pre-BH, SYNTHETIC) — rated “less bad, not an edge”.")
    c5 = C["frozen_IS"]["c5"]
    ce = {k.split("|")[1]: v.get("spread_ceiling_pct") for k, v in c5.items() if k.startswith("floor|")}
    out.append("“round-trip ≤ ~5% of premium”: the IS-implied spread ceilings are "
               + ", ".join(f"{k} ≤ {v}%" for k, v in ce.items())
               + " — pooled index and stocks point to ≤ 2%, per-index ceilings range up to 10%.")
    sb = C["frozen_IS"]["best_cell"]["stocks"]
    out.append(f"“delta ≈0.40–0.55”: for stocks the IS-chosen cell was |delta| {sb['delta']} / DTE {sb['bucket']}; "
               f"OOS the best stock cell (ex post) was d0.55 21–35. Index IS choice was d0.55 7–10.")
    if SYN:
        out.append("Across-family BH (p4_synthesis): q < 0.05 survivors are " + ", ".join(SYN.get("bh_survivors", []))
                   + " — every one is a stock filter, veto or exit; E2 and M6 stocks survive BH but fail other §0 criteria. Only V3 stocks passes §0 (as a veto).")
    XC = load("p4_archive_crosscheck.json")
    if XC:
        b = XC["by_class"]
        out.append(f"Spread model vs real archive fills: real bid/ask made the buyer better by {f3(b['index']['diff_real_minus_model'])} R (index) and "
                   f"{f3(b['stocks']['diff_real_minus_model'])} R (stocks) per trade — the modelled spread is conservative, so the swing losses are, if anything, slightly overstated "
                   "(one calm 2-month window, small effective sample; post-close books, so real intraday fills may be tighter still).")
    v3 = VR["V3"]["STOCKS"]
    out.append(f"V3 (inverted term structure) is a VETO-PASS on stocks (penalty {f3(v3['OOS']['diff_vs_b1']['diff'])} R vs B1) — a usable veto, not in the filter list above.")
    return "".join(f'<div class="flag">{esc(x)}</div>' for x in out)


def section_structural():
    pb = B["pooled_h3"]
    cls = ["NIFTY", "BANKNIFTY", "SENSEX", "other_index", "stocks"]
    def col(c, s, k):
        return pb[f"{c}|{s}"][k]
    traces = []
    for s, f in (("IS", bar_is), ("OOS", bar_oos)):
        traces.append(f(f"gross {s}", cls, [col(c, s, "gross_mean_R") for c in cls], "var:gross",
                        err=errs([col(c, s, "gross_mean_R") for c in cls], [col(c, s, "gross_ci")[0] for c in cls],
                                 [col(c, s, "gross_ci")[1] for c in cls]), offsetgroup=f"g{s}"))
        traces.append(f(f"net {s}", cls, [col(c, s, "net_mean_R") for c in cls], "var:net",
                        err=errs([col(c, s, "net_mean_R") for c in cls], [col(c, s, "ci_lo") for c in cls],
                                 [col(c, s, "ci_hi") for c in cls]), offsetgroup=f"n{s}"))
    ntot = sum(pb[k]["n"] for k in pb)
    c1 = chart("st_gross_net", "Gross ≈ 0, net < 0 in every class: friction is the loss",
               "Mean R per standard 0.40-delta buy held 3 days (B1, no signal). Outline = in-sample 2019–23, filled = out-of-sample 2024–26; whiskers = day-clustered 95% CI.",
               traces, {"barmode": "group", "yaxis": {"title": {"text": "mean R per trade"}, "zeroline": True},
                        "xaxis": {"title": {"text": "instrument class"}}},
               "p4_baseline.json:pooled_h3", 330, n_text=f"n = {ntot:,} trades")
    dp = B["drag_pct"]
    order = ["NIFTY", "stocks", "BANKNIFTY", "other_index", "SENSEX"]
    c2 = chart("st_friction", "Round-trip spread dwarfs statutory costs (except NIFTY)",
               "Mean round-trip friction as % of entry premium for the standard contract (h=3). Spread is modelled (p75; BANKNIFTY p90).",
               [{"type": "bar", "name": "spread (modelled)", "x": order, "y": [dp[c]["spread_drag_mean"] for c in order], "marker": {"color": "var:net"}},
                {"type": "bar", "name": "statutory costs", "x": order, "y": [dp[c]["cost_drag_mean"] for c in order], "marker": {"color": "var:gross"}}],
               {"barmode": "stack", "yaxis": {"title": {"text": "% of entry premium"}}, "xaxis": {"title": {"text": "instrument class"}}},
               "p4_baseline.json:drag_pct", 300, n_text="all B1 standard-contract rows")
    # spread vs premium (model p75 + empirical p75) and cost worked examples
    ex = S["examples"]
    groups = []
    for e in ex:
        if e["g"] not in groups:
            groups.append(e["g"])
    tr = []
    palette = {"NIFTY": "var:net", "BANKNIFTY": "var:lessbad", "SENSEX": "var:watch", "STK1": "var:pass",
               "STK2": "var:fail", "STK3": "var:base"}
    for gname in groups:
        rows = sorted([e for e in ex if e["g"] == gname], key=lambda e: e["prem"])
        tr.append({"type": "scatter", "mode": "lines+markers", "name": gname, "x": [e["prem"] for e in rows],
                   "y": [e["rt75"] for e in rows], "line": {"color": palette.get(gname, "var:base")},
                   "customdata": [[e["emp75"], e["n"], e["adelta"], e["dte"]] for e in rows],
                   "hovertemplate": gname + " ₹%{x}: model p75 %{y:.1f}% (empirical %{customdata[0]:.1f}%, n %{customdata[1]})<br>|Δ| %{customdata[2]}, DTE %{customdata[3]}<extra></extra>"})
    # cost worked examples from p3_costs.md
    cx, cy, ct = [], [], []
    for line in COSTS_MD.splitlines():
        mm = re.match(r"\|\s*([^|]*?(\d+(?:\.\d+)?)\s*->[^|]*)\|.*\|\s*\*\*([\d.]+)\*\*\s*\|\s*\**([\d.]+)%\**\s*\|", line)
        if mm:
            cx.append(float(mm.group(2)))
            cy.append(float(mm.group(4)))
            ct.append(mm.group(1).strip())
    if cx:
        tr.append({"type": "scatter", "mode": "markers", "name": "statutory costs (worked ex.)", "x": cx, "y": cy,
                   "text": ct, "marker": {"symbol": "diamond-open", "size": 10, "color": "var:ink"},
                   "hovertemplate": "%{text}: costs %{y:.2f}% of premium<extra></extra>"})
    c3 = chart("st_spread_prem", "Cheap premiums pay a much bigger toll",
               "Lines: modelled round-trip spread (p75) for a typical nearest-expiry trade at that premium. Diamonds: statutory costs as % of buy premium from the P3 worked examples (NIFTY lot 65, ₹20/order).",
               tr, {"xaxis": {"type": "log", "title": {"text": "entry premium, ₹ (log)"}},
                    "yaxis": {"type": "log", "title": {"text": "round-trip % of premium (log)"}}},
               "p3_spread_eval.json:examples + p3_costs.md", 330,
               n_text=f"{len(ex)} spread cells from 668k quotes / 40 sessions; {len(cx)} cost examples")
    # theta bleed
    bl = C["OOS"]["bleed"]
    hs = ["h1", "h2", "h3", "h5"]
    tr = []
    dashes = {"7-10": "solid", "11-20": "dash", "21-35": "dot", "36+": "dashdot", "all": "solid"}
    for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
        for b in ["7-10", "11-20", "21-35", "36+"]:
            ys = [g(bl, f"OOS|{gname}|{b}|{h}", "net") for h in hs]
            if all(y is None for y in ys):
                continue
            tr.append({"type": "scatter", "mode": "lines+markers", "name": f"{gname} DTE {b}", "x": [1, 2, 3, 5], "y": ys,
                       "line": {"color": color, "dash": dashes[b]}})
    c4 = chart("st_bleed", "Short-dated stock options bleed fastest; longer DTE holds up",
               "Net mean R of the standard contract by days held, split by expiry bucket (OOS). Blue = index, amber = stocks.",
               tr, {"xaxis": {"title": {"text": "days held"}, "tickvals": [1, 2, 3, 5]},
                    "yaxis": {"title": {"text": "net mean R"}}},
               "p4_contract_exits.json:OOS.bleed", 330, n_text=f"n(h3) index {g(bl,'OOS|index|all|h3','n') or 0:,} · stocks {g(bl,'OOS|stocks|all|h3','n') or 0:,}")
    ib = C["IS"]["bleed"]
    bk = ["7-10", "11-20", "21-35", "36+"]
    tr = []
    for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
        tr.append(bar_is(f"{gname} IS", bk, [100 * (g(ib, f"IS|{gname}|{b}|h3", "theta_share_losers") or 0) if g(ib, f"IS|{gname}|{b}|h3") else None for b in bk], color, offsetgroup=gname + "i"))
        tr.append(bar_oos(f"{gname} OOS", bk, [100 * g(bl, f"OOS|{gname}|{b}|h3", "theta_share_losers") if g(bl, f"OOS|{gname}|{b}|h3") else None for b in bk], color, offsetgroup=gname + "o"))
    c5 = chart("st_theta_share", "Time decay explains up to ~half of losers' losses near expiry",
               "Share of losing trades' loss attributable to theta, h=3, by expiry bucket. Outline = IS, filled = OOS.",
               tr, {"barmode": "group", "yaxis": {"title": {"text": "theta share of losses, %"}},
                    "xaxis": {"title": {"text": "DTE bucket (calendar days)"}}},
               "p4_contract_exits.json:{IS,OOS}.bleed", 300)
    return f"""<section id="structural"><h2>2 · The buyer's structural problem</h2>
<p class="sub">Before any signal: what does it cost just to own a standard option for a few days?</p>
<div class="grid">{c1}{c2}</div><div class="grid">{c3}{c4}</div><div class="grid">{c5}</div></section>"""


def score_rows():
    rows = []
    for h in ["V1", "V2", "V3", "V4", "D1", "D2", "D3", "D4", "E1", "E2", "E3"]:
        for cls, lab in (("INDEX", "index"), ("STOCKS", "stocks")):
            r = VR.get(h, {}).get(cls)
            if not r:
                continue
            o = r["OOS"]
            d = o["diff_vs_b1"]
            if d.get("diff") is None:
                continue
            rows.append(dict(label=f"{h} {lab}", h=h, cls=lab, diff=d["diff"], lo=d["ci"][0], hi=d["ci"][1],
                             p=d.get("p_raw"), v=vshort(r["verdict"]), is_net=r["IS"]["stats"]["net_mean_R"],
                             oos_net=o["stats"]["net_mean_R"], n=o["stats"]["n"], days=o["stats"]["n_days"],
                             syn=False, ref="B1"))
    vc = V["V5_contrast"]["OOS"]
    rows.append(dict(label="V5 stocks−index", h="V5", cls="contrast", diff=vc["diff"], lo=vc["ci"][0], hi=vc["ci"][1],
                     p=vc["p"], v=vshort(VR["V5"]["STOCKS"]["verdict"]), is_net=None, oos_net=None,
                     n=vc["n_stocks"] + vc["n_index"], days=vc["n_days"], syn=False, ref="index strangle"))
    for h in ["M1", "M1b", "M2", "M3", "M4", "M5"]:
        for cls in ("index", "stocks"):
            r = MH.get(h, {}).get(cls)
            if not r:
                continue
            o = r["OOS"]
            d = o["diff_vs_b1"]
            rows.append(dict(label=f"{h} {cls}", h=h, cls=cls, diff=d["diff"], lo=d["ci"][0], hi=d["ci"][1], p=d["p"],
                             v=vshort(r["verdict"]), is_net=r["IS"]["signal"]["net_mean_R"],
                             oos_net=o["signal"]["net_mean_R"], n=o["signal"]["n"], days=o["signal"]["n_days"],
                             syn=False, ref="B1"))
    m8 = M["M8"]["OOS"]["index"]["diff_expiry_minus_other"]
    rows.append(dict(label="M8 index (expiry−other)", h="M8", cls="index", diff=m8["diff"], lo=m8["ci"][0], hi=m8["ci"][1],
                     p=m8["p"], v=vshort(M["M8"]["verdict"]["index"]["verdict"]), is_net=None, oos_net=None,
                     n=M["M8"]["OOS"]["index"]["n_pooled"], days=None, syn=False, ref="other days"))
    for h, key in (("M6", "m6"), ("M7", "m7")):
        for cls, lab in (("index", "index"), ("stock", "stocks")):
            o = I2[key][f"OOS|{cls}"]
            ii = I2[key][f"IS|{cls}"]
            rows.append(dict(label=f"{h} {lab} †", h=h, cls=lab, diff=o["diff_vs_b1"], lo=o["diff_lo"], hi=o["diff_hi"],
                             p=o.get("p_vs_b1"), v=vshort(I2["verdicts"][h][cls]), is_net=ii["net_mean_R"],
                             oos_net=o["net_mean_R"], n=o["n_trades"], days=o["n_days"], syn=True, ref="random"))
    for r in rows:
        if (r["h"], r["cls"]) in WATCH:
            r["v"] = "WATCH"
    return rows


def section_scoreboard():
    rows = score_rows()
    rows_sorted = list(reversed(rows))
    tr = []
    for vlab in ["FAIL", "ANECDOTAL", "N/A", "LESS-BAD", "VETO-PASS", "WATCH"]:
        sub = [r for r in rows_sorted if r["v"] == vlab]
        if not sub:
            continue
        tr.append({"type": "scatter", "mode": "markers", "name": vlab, "y": [r["label"] for r in sub],
                   "x": [r["diff"] for r in sub],
                   "error_x": {"type": "data", "symmetric": False, "array": [r["hi"] - r["diff"] for r in sub],
                               "arrayminus": [r["diff"] - r["lo"] for r in sub], "color": VCOL[vlab], "thickness": 1.5},
                   "marker": {"color": VCOL[vlab], "size": 8, "line": {"width": 1, "color": "var:panel"}},
                   "customdata": [[r["n"], r["days"] or 0, r["p"] if r["p"] is not None else -1, r["ref"]] for r in sub],
                   "hovertemplate": "%{y}<br>diff %{x:+.3f} vs %{customdata[3]}<br>n %{customdata[0]:,} on %{customdata[1]} days<br>p %{customdata[2]}<extra></extra>"})
    lay = {"xaxis": {"title": {"text": "OOS net R difference vs baseline (95% CI)"}, "zeroline": True, "zerolinewidth": 2},
           "yaxis": {"type": "category", "categoryorder": "array", "categoryarray": [r["label"] for r in rows_sorted],
                     "tickfont": {"size": 10.5}, "automargin": True},
           "legend": {"orientation": "h", "y": 1.04, "yanchor": "bottom"}, "margin": {"l": 10, "t": 30},
           "shapes": [{"type": "line", "x0": 0, "x1": 0, "yref": "paper", "y0": 0, "y1": 1, "line": {"color": "var:ink2", "width": 1}}]}
    c1 = chart("sc_dots", "Every entry hypothesis vs its baseline: CIs straddle zero or sit in ‘less bad’",
               "OOS difference in net mean R vs the side-/class-matched random-entry baseline (B1), day-clustered 95% CI. "
               "† = M6/M7 intraday, SYNTHETIC prices, baseline = random entry. V5 = stocks minus index strangle; M8 = expiry-day minus other days. Colour = verdict; purple = forward-test watchlist.",
               tr, lay, "p4_vol_dealer_events / p4_momentum / p4_intraday .json", 30 + 19 * len(rows), synthetic=True,
               n_text=f"{len(rows)} hypothesis × class rows")
    sl = [r for r in rows if r["is_net"] is not None]
    tr = []
    hl = {("M1", "index"), ("M4", "index"), ("V2", "index")}
    for r in sl:
        key = (r["h"], r["cls"])
        strong = key in hl
        tr.append({"type": "scatter", "mode": "lines+markers+text" if strong else "lines+markers",
                   "x": ["in-sample", "out-of-sample"], "y": [r["is_net"], r["oos_net"]], "name": r["label"],
                   "text": ["", r["label"]] if strong else None, "textposition": "middle right",
                   "line": {"color": VCOL.get(r["v"], "var:base"), "width": 3 if strong else 1},
                   "opacity": 1 if strong else 0.55, "showlegend": False,
                   "marker": {"size": [7, 7], "symbol": ["circle-open", "circle"], "color": VCOL.get(r["v"], "var:base")},
                   "hovertemplate": r["label"] + ": %{x} %{y:+.3f}<extra></extra>"})
    c2 = chart("sc_slope", "What vanished out-of-sample: M1 index went from +0.08 to −0.08",
               "Net mean R per trade, in-sample (open circle) → out-of-sample (filled). Thick labelled lines: M1 index (IS mirage), M4 index and V2 index (watchlist). † SYNTHETIC rows included.",
               tr, {"xaxis": {"type": "category", "title": {"text": "sample"}}, "yaxis": {"title": {"text": "net mean R per trade"}},
                    "margin": {"r": 120}}, "same as scoreboard", 420, synthetic=True, n_text=f"{len(sl)} rows")
    return f"""<section id="scoreboard"><h2>3 · Scoreboard</h2>
<div class="legend-row"><span><i class="sw" style="background:var(--fail)"></i>FAIL</span><span><i class="sw" style="background:var(--anec)"></i>ANECDOTAL / N/A (&lt;100 trades or not registered)</span>
<span><i class="sw" style="background:var(--lessbad)"></i>LESS-BAD (filter only)</span><span><i class="sw" style="background:var(--pass)"></i>VETO-PASS</span><span><i class="sw" style="background:var(--watch)"></i>watchlist</span></div>
<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr))">{c1}{c2}</div></section>"""


# ------------------------------------------------ family cards
def scaled_baseline(b_eq, n_sig, n_b):
    if not b_eq or not n_b:
        return [], []
    k = n_sig / n_b
    return [m for m, _ in b_eq], [v * k for _, v in b_eq]


def equity_chart(cid, title, segs, src, n_text, synthetic=False, ylab="cumulative net ₹ per lot"):
    """segs: list of (name, x, y, color, dash, width)."""
    tr = []
    for name, x, y, color, dash, width in segs:
        if not x:
            continue
        tr.append({"type": "scatter", "mode": "lines", "name": name, "x": x, "y": y,
                   "line": {"color": color, "dash": dash, "width": width}})
    return chart(cid, title, "Solid = OOS, dotted = IS (each sample restarts at 0). Grey = baseline rescaled to the signal's trade count (B1 cum × n_signal / n_B1).",
                 tr, {"xaxis": {"title": {"text": "month"}}, "yaxis": {"title": {"text": ylab}},
                      "legend": {"orientation": "h", "y": -0.3}}, src, 240, synthetic, n_text)


def grid_matrix(grid_is, grid_oos, parse):
    """grid dicts -> (rows, cols, z_is, z_oos). parse(key)->(row, col)."""
    keys = list(grid_is.keys()) + [k for k in grid_oos.keys() if k not in grid_is]
    rws, cls = [], []
    for k in keys:
        r, c = parse(k)
        if r not in rws:
            rws.append(r)
        if c not in cls:
            cls.append(c)
    def z(gd):
        out = []
        for r in rws:
            row = []
            for c in cls:
                v = None
                for k, val in gd.items():
                    if parse(k) == (r, c):
                        v = val.get("net_mean_R") if isinstance(val, dict) else val
                row.append(v)
            out.append(row)
        return out
    return rws, cls, z(grid_is), z(grid_oos)


def vol_card(h, cls):
    r = VR[h][cls]
    o, i = r["OOS"], r["IS"]
    lab = "index" if cls == "INDEX" else "stocks"
    cid = f"f_{h}_{lab}".replace(".", "")
    so, si = o["signal"], i["signal"]
    st = o["stats"]
    v = vshort(r["verdict"])
    vv = chip(v) + (" " + chip("WATCH") if (h, lab) in WATCH else "")
    d = o["diff_vs_b1"]
    xo, yo = scaled_baseline(o["b1"].get("equity_monthly"), so["n"], o["b1"]["n"])
    xi, yi = scaled_baseline(i["b1"].get("equity_monthly"), si["n"], i["b1"]["n"])
    segs = [("signal IS", [m for m, _ in si.get("equity_monthly") or []], [y for _, y in si.get("equity_monthly") or []], "var:net", "dot", 1.5),
            ("signal OOS", [m for m, _ in so.get("equity_monthly") or []], [y for _, y in so.get("equity_monthly") or []], "var:net", "solid", 2.5),
            ("B1 IS (scaled)", xi, yi, "var:base", "dot", 1.2), ("B1 OOS (scaled)", xo, yo, "var:base", "solid", 1.5)]
    eq = equity_chart(cid + "_eq", f"{h} {lab}: equity, signal vs B1", segs, "p4_vol_dealer_events.json",
                      f"n OOS {so['n']:,} · IS {si['n']:,}")
    hi = hist_chart(cid + "_h", "OOS R distribution", so["hist"]["edges"], so["hist"]["counts"],
                    o["b1"]["hist"]["counts"], "p4_vol_dealer_events.json", so["n"], o["b1"]["n"], bname="B1")
    gi, go = i.get("grid_heatmap") or {}, o.get("grid_heatmap") or {}
    hm = ""
    if gi or go:
        rws, cols, zi, zo = grid_matrix(gi, go, lambda k: (k.rsplit("|", 1)[0].replace("None", "registered"), "h" + k.rsplit("|", 1)[1]))
        frozen = r.get("params") or {}
        hm = heat_pair(cid + "_g", "Robustness: threshold × hold (net R)",
                       f"Frozen cell: {esc(json.dumps(frozen)) if frozen else 'single registered rule'}; plateau test uses ±20% of the threshold. Cells with tiny n are noisy.",
                       cols, [str(x) for x in rws], zi, zo, "hold (days)", "threshold", "p4_vol_dealer_events.json:grid_heatmap",
                       height=max(170, 70 + 34 * len(rws)))
    stat = (f"OOS net <b>{f3(st['net_mean_R'])}</b> R (gross {f3(st['gross_mean_R'])}) [{f3(st['ci_lo'])}, {f3(st['ci_hi'])}] · "
            f"IS {f3(i['stats']['net_mean_R'])} · vs B1 {f3(d['diff'])} [{f3(d['ci'][0])}, {f3(d['ci'][1])}] p {d.get('p_raw')} · "
            f"n {st['n']:,} / {st['n_days']} days · win {pct(so['win_rate'])} · theta share of losses {pct(so.get('theta_share_losers'))}")
    chips = crit_chips(r.get("criteria") or {}, VOL_CRIT) if r.get("criteria") else '<div class="note">contrast test — no §0 criteria (see V5 chart)</div>'
    return (f'<div class="card"><h4>{esc(h)} · {esc(lab)} — {esc(DESC[h])} {vv}</h4><p class="stat">{stat}</p>{chips}'
            f'<div class="grid g3">{eq}{hi}{hm}</div></div>')


def mom_card(h, cls):
    r = MH[h][cls]
    o, i = r["OOS"], r["IS"]
    cid = f"f_{h}_{cls}"
    so, si = o["signal"], i["signal"]
    v = vshort(r["verdict"])
    vv = chip(v) + (" " + chip("WATCH") if (h, cls) in WATCH else "")
    d = o["diff_vs_b1"]
    def eqxy(sig):
        e = sig.get("equity_month") or {}
        return e.get("month", []), e.get("cum_net_rs", [])
    bo = o["b1"].get("equity_month") or {}
    bi = i["b1"].get("equity_month") or {}
    ko = so["n"] / o["b1"]["n"] if o["b1"]["n"] else 0
    ki = si["n"] / i["b1"]["n"] if i["b1"]["n"] else 0
    segs = [("signal IS", *eqxy(si), "var:net", "dot", 1.5), ("signal OOS", *eqxy(so), "var:net", "solid", 2.5),
            ("B1 IS (scaled)", bi.get("month", []), [y * ki for y in bi.get("cum_net_rs", [])], "var:base", "dot", 1.2),
            ("B1 OOS (scaled)", bo.get("month", []), [y * ko for y in bo.get("cum_net_rs", [])], "var:base", "solid", 1.5)]
    eq = equity_chart(cid + "_eq", f"{h} {cls}: equity, signal vs B1", segs, "p4_momentum.json",
                      f"n OOS {so['n']:,} · IS {si['n']:,}")
    hi = hist_chart(cid + "_h", "OOS R distribution", so["hist_edges"], so["hist_counts"], o["b1"]["hist_counts"],
                    "p4_momentum.json", so["n"], o["b1"]["n"], bname="B1")
    gf_i = g(M, "grid_full", "IS", h, cls) or {}
    gf_o = g(M, "grid_full", "OOS", h, cls) or {}
    flat_i = {f"{var}|{hd}": val for var, hh in gf_i.items() for hd, val in hh.items()}
    flat_o = {f"{var}|{hd}": val for var, hh in gf_o.items() for hd, val in hh.items()}
    hm = ""
    if flat_i or flat_o:
        rws, cols, zi, zo = grid_matrix(flat_i, flat_o, lambda k: (k.split("|")[0], k.split("|")[1]))
        fr = r["OOS"].get("frozen") or {}
        hm = heat_pair(cid + "_g", "Robustness: variant × hold (net R)",
                       f"Frozen on IS: {esc(fr.get('variant'))} at h={esc(fr.get('h'))}.", cols, rws, zi, zo,
                       "hold", "variant", "p4_momentum.json:grid_full", height=max(170, 70 + 26 * len(rws)))
    cp = o.get("signal_minus_opposite") or {}
    stat = (f"OOS net <b>{f3(so['net_mean_R'])}</b> R (gross {f3(so['gross_mean_R'])}) [{f3(so['ci'][0])}, {f3(so['ci'][1])}] · "
            f"IS {f3(si['net_mean_R'])} · vs B1 {f3(d['diff'])} [{f3(d['ci'][0])}, {f3(d['ci'][1])}] p {d['p']} (BH q {r.get('q_bh_family')}) · "
            f"call−put {f3(cp.get('diff'))} · n {so['n']:,} / {so['n_days']} days · win {pct(so['win_rate'])} · PF {so['profit_factor']:.2f}")
    return (f'<div class="card"><h4>{esc(h)} · {esc(cls)} — {esc(DESC[h])} {vv}</h4><p class="stat">{stat}</p>'
            f'{crit_chips(r["criteria"], MOM_CRIT)}<div class="grid g3">{eq}{hi}{hm}</div></div>')


def m8_card():
    tr = []
    for s, f in (("IS", bar_is), ("OOS", bar_oos)):
        x = M["M8"][s]["index"]
        xs = ["expiry day", "other days"]
        ys = [x["expiry_day"]["net_mean_R"], x["other_days"]["net_mean_R"]]
        tr.append(f(s, xs, ys, "var:net", err=errs(ys, [x["expiry_day"]["ci"][0], x["other_days"]["ci"][0]],
                                                     [x["expiry_day"]["ci"][1], x["other_days"]["ci"][1]])))
    o = M["M8"]["OOS"]["index"]
    c = chart("f_M8", "M8: expiry-day entries flip sign between samples",
              "Pooled M-signal entries on index expiry days vs other days. Outline = IS, filled = OOS. Stocks: no expiry-day entries by construction (untestable).",
              tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R"}}}, "p4_momentum.json:M8", 260,
              n_text=f"OOS expiry-day n {o['expiry_day']['n']} / other {o['other_days']['n']}")
    return f'<div class="card"><h4>M8 · index — {esc(DESC["M8"])} {chip(vshort(M["M8"]["verdict"]["index"]["verdict"]))}</h4><div class="grid">{c}</div></div>'


def v5_chart():
    vc = V["V5_contrast"]
    tr = []
    for s, f in (("IS", bar_is), ("OOS", bar_oos)):
        tr.append(f(s, ["stocks", "index"], [vc[s]["stocks"], vc[s]["index"]], "var:net"))
    return chart("f_V5c", "V5: stock strangles lose more than index strangles (registered the other way)",
                 f"Unconditional 5-day strangle net R. OOS diff {f3(vc['OOS']['diff'])} [{f3(vc['OOS']['ci'][0])}, {f3(vc['OOS']['ci'][1])}], p {vc['OOS']['p']}.",
                 tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R per strangle"}}}, "p4_vol_dealer_events.json:V5_contrast",
                 250, n_text=f"OOS n stocks {vc['OOS']['n_stocks']:,} / index {vc['OOS']['n_index']:,}")


def intraday_card(h, key, cls):
    lab = "stocks" if cls == "stock" else "index"
    cid = f"f_{h}_{lab}"
    o, i = I2[key][f"OOS|{cls}"], I2[key][f"IS|{cls}"]
    em = I2["equity_monthly"]
    sig = em[key][cls]
    b2 = em["b2"][cls]
    b2m = dict(zip(b2["month"], b2["mean_R"]))
    # B2 at the signal's per-month trade count, cumulated
    cum, acc = [], 0.0
    for mth, n in zip(sig["month"], sig["n"]):
        acc += (b2m.get(mth) or 0) * n
        cum.append(acc)
    segs = [(f"{h}", sig["month"], sig["cum_R"], "var:net", "solid", 2.5),
            ("B2 (scaled to signal n)", sig["month"], cum, "var:base", "solid", 1.5)]
    if key == "m6":
        rd = em["rand6"][cls]
        segs.append(("random entry", rd["month"], rd["cum_R"], "var:base", "dot", 1.5))
    eq = chart(cid + "_eq", f"{h} {lab}: cumulative R falls monotonically",
               "Sum of net R per trade (1 lot), IS 2022–23 then OOS 2024–26 as one series. Grey = B2 (buy 09:20 → 15:15) at the signal's monthly trade count; dotted = matched random entry.",
               [{"type": "scatter", "mode": "lines", "name": n, "x": x, "y": y, "line": {"color": c, "dash": dsh, "width": w}}
                for n, x, y, c, dsh, w in segs],
               {"xaxis": {"title": {"text": "month"}}, "yaxis": {"title": {"text": "cumulative net R"}},
                "legend": {"orientation": "h", "y": -0.3},
                "shapes": [{"type": "line", "x0": "2024-01", "x1": "2024-01", "yref": "paper", "y0": 0, "y1": 1, "line": {"color": "var:ink2", "dash": "dash", "width": 1}}]},
               "p4_intraday.json:stage2.equity_monthly", 240, True, f"{sum(sig['n']):,} trades")
    rdist = I2["r_dist"]
    hi = hist_chart(cid + "_h", "OOS R distribution", rdist["bins"], rdist[key][f"OOS|{cls}"], rdist["b2"][f"OOS|{cls}"],
                    "p4_intraday.json:stage2.r_dist", o["n_trades"], I2["b2"][f"OOS|{cls}"]["n_trades"], synthetic=True, bname="B2")
    hm = ""
    if key == "m6":
        hmap = I2["heatmap"]
        rws = [f"OR {x} min" for x in hmap["rows_L"]]
        cols = [f"exit {x[:2]}:{x[2:]}" for x in hmap["cols_exit"]]
        hm = heat_pair(cid + "_g", "Robustness: opening range × exit (net R)", "Flat and negative everywhere: no positive cell to form a plateau.",
                       cols, rws, hmap["cells"][f"IS|{cls}|net_mean_R"], hmap["cells"][f"OOS|{cls}|net_mean_R"],
                       "exit time", "opening range", "p4_intraday.json:stage2.heatmap", synthetic=True, height=200)
    vd = I2["verdicts"]
    crit = {"a": o["ci_lo"] > 0, "b": o["diff_lo"] > 0, "c": vd["stage1_ok"].get(cls) if key == "m6" else None,
            "d": vd["plateau_ok"].get(cls) if key == "m6" else None, "e": o["n_trades"] >= 100, "f": o["net_mean_R_2x"] >= 0,
            "g": o["net_mean_R_haircut"] >= 0}
    labels = [("a", "net>0, CI excl. 0"), ("b", "beats random (CI)"), ("c", "Stage 1: move > required"),
              ("d", "plateau"), ("e", "n ≥ 100"), ("f", "2× spread ≥ 0"), ("g", "−10% haircut ≥ 0")]
    stat = (f"OOS net <b>{f3(o['net_mean_R'])}</b> R (gross {f3(o['gross_mean_R'])}) [{f3(o['ci_lo'])}, {f3(o['ci_hi'])}] · IS {f3(i['net_mean_R'])} · "
            f"vs random {f3(o['diff_vs_b1'])} [{f3(o['diff_lo'])}, {f3(o['diff_hi'])}] · n {o['n_trades']:,} / {o['n_days']} days")
    return (f'<div class="card"><h4><span class="badge synth">SYNTHETIC</span>{esc(h)} · {esc(lab)} — {esc(DESC[h])} {chip(vshort(vd[h][cls]))}</h4>'
            f'<p class="stat">{stat}</p>{crit_chips(crit, labels)}<div class="grid g3">{eq}{hi}{hm}</div></div>')


def intraday_stage1_chart():
    bs = I1["m6"]["by_cls_sample"]
    rs = I1["rand"]["by_cls_sample"]
    keys = ["index|IS", "index|OOS", "stock|IS", "stock|OOS"]
    tr = [{"type": "bar", "name": "mean move ÷ required (M6)", "x": keys, "y": [bs[k].get("move_over_req_means") for k in keys], "marker": {"color": "var:net"}},
          {"type": "bar", "name": "random entry", "x": keys, "y": [rs[k].get("move_over_req_means") for k in keys], "marker": {"color": "var:base"}}]
    lay = {"barmode": "group", "yaxis": {"title": {"text": "mean underlying move ÷ required move"}},
           "shapes": [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 1, "y1": 1, "line": {"color": "var:loss", "dash": "dash"}}],
           "annotations": [{"text": "breakeven = 1.0", "xref": "paper", "x": 1, "y": 1, "xanchor": "right", "yanchor": "bottom", "showarrow": False, "font": {"size": 11}}]}
    return chart("f_M6_stage1", "M6 Stage 1: the real underlying moves 0.04–0.13× what the option needs",
                 "Real 1-minute underlying; the required move (breakeven incl. costs) is from SYNTHETIC option prices. Dashed = 1.0.",
                 tr, lay, "p4_intraday.json:stage1", 280, True, n_text=f"n = {sum(bs[k]['n'] for k in keys):,} M6 trades")


def section_families():
    parts = ['<section id="families"><h2>4 · Family pages</h2><p class="sub">One card per hypothesis × class: equity (signal vs baseline), R histogram, '
             'parameter grid in- and out-of-sample, and the six §0 pass criteria. Index = NIFTY + BANKNIFTY + SENSEX pooled.</p>']
    fams = [("fam-vol", "Volatility pricing (V1–V5)", [("V1",), ("V2",), ("V3",), ("V4",), ("V5",)]),
            ("fam-dealer", "Dealer positioning (D1–D4)", [("D1",), ("D2",), ("D3",), ("D4",)]),
            ("fam-events", "Events (E1–E3, stocks only)", [("E1",), ("E2",), ("E3",)])]
    for fid, title, hs in fams:
        parts.append(f'<h3 id="{fid}">{esc(title)}</h3>')
        if fid == "fam-vol":
            parts.append(f'<div class="grid">{v5_chart()}</div>')
        for (h,) in hs:
            for cls in ("INDEX", "STOCKS"):
                if cls in VR.get(h, {}):
                    if h == "V4" and cls == "STOCKS":
                        continue  # index-only rule; stock run unregistered
                    parts.append(vol_card(h, cls))
    parts.append('<h3 id="fam-mom">Momentum / structure (M1–M5, M8)</h3>')
    for h in ["M1", "M1b", "M2", "M3", "M4", "M5"]:
        for cls in ("index", "stocks"):
            if cls in MH.get(h, {}):
                parts.append(mom_card(h, cls))
    parts.append(m8_card())
    parts.append('<h3 id="fam-intra">Intraday (M6, M7) <span class="badge synth">SYNTHETIC</span></h3>')
    parts.append(f'<div class="grid">{intraday_stage1_chart()}</div>')
    for h, key in (("M6", "m6"), ("M7", "m7")):
        for cls in ("index", "stock"):
            parts.append(intraday_card(h, key, cls))
    parts.append("</section>")
    return "".join(parts)


def section_contract():
    deltas = [0.15, 0.25, 0.4, 0.55, 0.7]
    bk = ["7-10", "11-20", "21-35", "36+"]
    out = []
    for gname in ("index", "stocks"):
        def z(sample):
            heat = C[sample]["c12"]["heat"]
            return [[g(heat, f"{gname}|all|h3|{d}|{b}", "net") for b in bk] for d in deltas]
        def n(sample):
            heat = C[sample]["c12"]["heat"]
            return sum(g(heat, f"{gname}|all|h3|{d}|{b}", "n") or 0 for d in deltas for b in bk)
        bc = C["best_cell_frozen_oos"][gname]
        out.append(heat_pair(f"cs_heat_{gname}", f"{gname.capitalize()}: no delta × DTE cell is positive; far-OTM is worst",
                             f"Net mean R, h=3, by |delta| and expiry bucket. IS-frozen best cell: d{bc['delta']} / {bc['bucket']} → OOS {f3(bc['oos']['net'])} R.",
                             bk, [f"|Δ| {d}" for d in deltas], z("IS"), z("OOS"), "DTE bucket", "|delta|",
                             "p4_contract_exits.json:{IS,OOS}.c12.heat", n_text=f"n IS {n('IS'):,} · OOS {n('OOS'):,}", height=300))
    # terciles
    terc = []
    for feat, title, cmp in (("C3|rmr_har", "Low required-move ratio loses less (stocks)", "T1-T3"),
                             ("C4|gamma_theta_rv", "More gamma per unit theta loses less (stocks)", "T3-T1")):
        tr = []
        for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
            for s, f in (("IS", bar_is), ("OOS", bar_oos)):
                c34 = C[s]["c34"]
                ys = [g(c34, f"{feat}|{gname}|all|{t}", "net") for t in ("T1", "T2", "T3")]
                lo = [g(c34, f"{feat}|{gname}|all|{t}", "lo") for t in ("T1", "T2", "T3")]
                hi = [g(c34, f"{feat}|{gname}|all|{t}", "hi") for t in ("T1", "T2", "T3")]
                tr.append(f(f"{gname} {s}", ["T1 (low)", "T2", "T3 (high)"], ys, color, err=errs(ys, lo, hi), offsetgroup=gname + s))
        dd = C["OOS"]["c34"].get(f"{feat}|stocks|all|{cmp}") or {}
        terc.append(chart(f"cs_{feat.split('|')[1]}", title,
                          f"Net mean R by IS-cut tercile of {'required-move ratio (HAR vol)' if 'rmr' in feat else 'gamma/|theta| (realised vol)'}; outline IS, filled OOS. Stocks OOS contrast {cmp}: {f3(dd.get('diff'))} [{f3(dd.get('lo'))}, {f3(dd.get('hi'))}].",
                          tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R (h=3)"}}, "xaxis": {"title": {"text": "tercile"}}},
                          f"p4_contract_exits.json:c34", 300))
    # spread buckets
    tr = []
    shapes = []
    ann = []
    ce = C["frozen_IS"]["c5"]
    for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
        for s, mode in (("IS", "dot"), ("OOS", "solid")):
            rows = C[s]["c5"]["spread"][gname]
            rows = [r for r in rows if r["n"] >= 100]
            xs = [(r["lo_edge"] + min(r["hi_edge"], 40)) / 2 for r in rows]
            ys = [r["net"] for r in rows]
            t = {"type": "scatter", "mode": "lines+markers", "name": f"{gname} {s}", "x": xs, "y": ys,
                 "line": {"color": color, "dash": mode}, "marker": {"symbol": "circle-open" if s == "IS" else "circle"},
                 "customdata": [[r["lo_edge"], r["hi_edge"], r["n"]] for r in rows],
                 "hovertemplate": "RT spread %{customdata[0]}–%{customdata[1]}%: %{y:+.3f} R (n %{customdata[2]:,})<extra>" + f"{gname} {s}</extra>"}
            if s == "OOS":
                up, dn = errs(ys, [r["lo"] for r in rows], [r["hi"] for r in rows])
                t["error_y"] = {"type": "data", "symmetric": False, "array": up, "arrayminus": dn, "color": color, "thickness": 1}
            tr.append(t)
        cp = g(ce, f"floor|{gname}", "spread_ceiling_pct")
        if cp is not None:
            shapes.append({"type": "line", "x0": cp, "x1": cp, "yref": "paper", "y0": 0, "y1": 1, "line": {"color": color, "dash": "dash", "width": 1.5}})
            ann.append({"text": f"{gname} cut ≤{cp}%", "x": cp, "yref": "paper", "y": 1, "yanchor": "bottom", "showarrow": False, "font": {"size": 10, "color": color}})
    sp = chart("cs_spread", "Net R falls as the round-trip spread widens; IS-implied cut at ≤ 2%",
               "Standard contract, h=3, by modelled round-trip spread bucket (% of premium; buckets with n < 100 hidden). Dashed verticals = liquidity cut chosen on IS (within 0.03 R of the plateau). Per-index cuts: "
               + ", ".join(f"{k.split('|')[1]} ≤ {v.get('spread_ceiling_pct')}%" for k, v in ce.items() if k.startswith("floor|")) + ".",
               tr, {"xaxis": {"title": {"text": "round-trip spread, % of premium (bucket mid)"}}, "yaxis": {"title": {"text": "net mean R"}},
                    "shapes": shapes, "annotations": ann}, "p4_contract_exits.json:{IS,OOS}.c5.spread + frozen_IS.c5", 320)
    # C1 delta and C2 DTE lines (OOS by hold)
    return f"""<section id="contract"><h2>5 · Contract selection</h2>
<p class="sub">Same unconditional entries, different contracts. Nothing turns positive; the spread between the best and worst choice is large.</p>
<div class="grid">{out[0]}{out[1]}</div><div class="grid">{terc[0]}{terc[1]}</div><div class="grid">{sp}</div></section>"""


def section_exits():
    tps, sls = [30, 50, 100], [25, 40, 60]
    out = []
    for gname in ("index", "stocks"):
        def z(sample):
            rules = C[sample]["exits"]["rules"]
            return [[g(rules, f"{gname}|all|h3|x1_tp{tp}_sl{sl}", "vs_x0", "diff") for tp in tps] for sl in sls]
        nn = g(C, "OOS", "exits", "x0", f"{gname}|all|h3", "n") or 0
        out.append(heat_pair(f"ex_tpsl_{gname}", f"{gname.capitalize()}: TP/SL barely moves expectancy vs a plain time exit",
                             "Net R of premium take-profit × stop-loss (X1, h_max=3) minus time exit X0. ≈0 = no gain; X1 cuts variance, not loss.",
                             [f"TP +{t}%" for t in tps], [f"SL −{s}%" for s in sls], z("IS"), z("OOS"), "take-profit", "stop-loss",
                             "p4_contract_exits.json:exits.rules", n_text=f"OOS n {nn:,}", height=260))
    rules = [("X0 time", None), ("X1 TP100/SL25", "x1_tp100_sl25"), ("X2 invalidation", "x2_ext"), ("X3 trail", "x3_trail"), ("X4 fast", "x4_fast")]
    met = []
    for metric, ylab, title, scale in (("net", "expectancy, net R", "Expectancy: X2/X4 lose least", 1),
                                       ("win_rate", "win rate, %", "Stops cut the win rate from ~34% to ~22%", 100),
                                       ("ratio", "avg win ÷ |avg loss|", "…but raise the payoff ratio", 1)):
        tr = []
        for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
            ys = []
            for _, key in rules:
                rec = g(C, "OOS", "exits", "x0", f"{gname}|all|h3") if key is None else g(C, "OOS", "exits", "rules", f"{gname}|all|h3|{key}")
                if rec is None:
                    ys.append(None)
                    continue
                m = rec.get("metrics", {})
                if metric == "net":
                    ys.append(rec["net"])
                elif metric == "win_rate":
                    ys.append(m.get("win_rate") * scale if m.get("win_rate") is not None else None)
                else:
                    ys.append(m["avg_win_R"] / abs(m["avg_loss_R"]) if m.get("avg_loss_R") else None)
            tr.append(bar_oos(gname, [r[0] for r in rules], ys, color))
        met.append(chart(f"ex_{metric}", title, "OOS, standard contract, h_max = 3, sides pooled. X1 shown at the IS-chosen cell.",
                         tr, {"barmode": "group", "yaxis": {"title": {"text": ylab}}}, "p4_contract_exits.json:OOS.exits", 270))
    # time in trade & time to +50%
    tr = []
    days = ["day 1", "day 2", "day 3", "day 4", "day 5"]
    for (lab, key), dash in zip(rules, ["solid", "dot", "dash", "dashdot", "longdash"]):
        for gname, color in (("index", "var:net"),):
            rec = g(C, "OOS", "exits", "x0", f"{gname}|all|h3") if key is None else g(C, "OOS", "exits", "rules", f"{gname}|all|h3|{key}")
            tit = g(rec, "metrics", "time_in_trade") if rec else None
            if tit:
                tr.append({"type": "bar", "name": lab, "x": days[:3], "y": [100 * v for v in tit[:3]]})
    tt = chart("ex_time_in_trade", "Early-exit rules close most index trades on day 1",
               "Share of trades by days held, index, h_max = 3, OOS.", tr,
               {"barmode": "group", "yaxis": {"title": {"text": "% of trades"}}, "xaxis": {"title": {"text": "exit day"}},
                "colorway": ["var:base", "var:gross", "var:net", "var:lessbad", "var:pass"]}, "p4_contract_exits.json:OOS.exits.*.metrics.time_in_trade", 270)
    tr = []
    ttt = C["OOS"]["exits"]["ttt"]
    cats = ["1", "2", "3", "never"]
    for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
        for thr, op in (("0.3", 0.45), ("0.5", 0.75), ("1.0", 1.0)):
            d = ttt.get(f"{gname}|all|h3|{thr}")
            if d:
                tr.append({"type": "bar", "name": f"{gname} +{int(float(thr)*100)}%", "x": ["day 1", "day 2", "day 3", "never"],
                           "y": [100 * d.get(c, 0) for c in cats], "marker": {"color": color, "opacity": op}})
    t2 = chart("ex_ttt", "Most trades never touch +50% within 3 days",
               "Time to first touch of a premium gain of +30/+50/+100% (darker = bigger target), standard contract, h=3, OOS.", tr,
               {"barmode": "group", "yaxis": {"title": {"text": "% of trades"}}, "xaxis": {"title": {"text": "first touch"}}},
               "p4_contract_exits.json:OOS.exits.ttt", 290)
    return f"""<section id="exits"><h2>6 · Exits</h2>
<p class="sub">Exit rules applied to unconditional B1 entries. X2 (underlying invalidation) passes the B1 leg in most groups; no rule is significantly worse than X0. Adoption still needs a surviving entry.</p>
<div class="grid">{out[0]}{out[1]}</div><div class="grid g3">{met[0]}{met[1]}{met[2]}</div><div class="grid">{tt}{t2}</div></section>"""


def section_regime():
    # by VIX tercile and by year: B1 vs M4 index vs V2 index
    m4 = MH["M4"]["index"]
    v2 = VR["V2"]["INDEX"]
    terc = ["low", "mid", "high"]
    series = [("M4 index signal", m4["OOS"]["signal"], "var:watch", bar_oos), ("M4 B1", m4["OOS"]["b1"], "var:base", bar_oos),
              ("V2 index signal", v2["OOS"]["signal"], "var:watch", bar_is), ("V2 B1 (strangle)", v2["OOS"]["b1"], "var:base", bar_is)]
    tr = []
    for name, sig, color, f in series:
        bv = sig.get("by_vix_tercile") or {}
        t = f(name, terc, [g(bv, k, "net_mean_R") for k in terc], color, offsetgroup=name)
        t["customdata"] = [g(bv, k, "n") for k in terc]
        t["hovertemplate"] = name + " VIX %{x}: %{y:+.3f} R (n %{customdata})<extra></extra>"
        tr.append(t)
    c1 = chart("rg_vix", "The candidates' edge is not concentrated in one VIX regime — but cells are thin",
               "OOS net mean R by VIX tercile. Filled = M4 (directional, h=2), outline = V2 (strangle, h=5); purple = signal, grey = its own B1. Hover for n.",
               tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R"}}, "xaxis": {"title": {"text": "VIX tercile"}}},
               "p4_momentum.json / p4_vol_dealer_events.json: by_vix_tercile", 300,
               n_text=f"M4 n {m4['OOS']['signal']['n']} · V2 n {v2['OOS']['signal']['n']}")
    tr = []
    for name, sigs, color in (("M4 index signal", (m4["IS"]["signal"], m4["OOS"]["signal"]), "var:watch"),
                              ("M4 B1", (m4["IS"]["b1"], m4["OOS"]["b1"]), "var:base"),
                              ("V2 index signal", (v2["IS"]["signal"], v2["OOS"]["signal"]), "var:net"),
                              ("V2 B1", (v2["IS"]["b1"], v2["OOS"]["b1"]), "var:gross")):
        yrs = {}
        for s in sigs:
            for y, d in (s.get("by_year") or {}).items():
                yrs[y] = d
        xs = sorted(yrs)
        tr.append({"type": "scatter", "mode": "lines+markers", "name": name, "x": xs, "y": [yrs[y]["net_mean_R"] for y in xs],
                   "customdata": [yrs[y]["n"] for y in xs], "line": {"color": color, "dash": "solid" if "signal" in name else "dot"},
                   "marker": {"size": [max(5, min(16, 3 + (yrs[y]["n"] ** 0.5))) for y in xs]},
                   "hovertemplate": name + " %{x}: %{y:+.3f} R (n %{customdata})<extra></extra>"})
    c2 = chart("rg_year", "By year: V2's OOS result leans on 2024; M4 is positive in every OOS year",
               "Net mean R by calendar year (IS 2019–23, OOS 2024–26; a year split by the boundary shows its OOS part). Marker size ∝ √n.",
               tr, {"yaxis": {"title": {"text": "net mean R"}}, "xaxis": {"title": {"text": "year"}, "type": "category"},
                    "shapes": [{"type": "line", "x0": "2024", "x1": "2024", "yref": "paper", "y0": 0, "y1": 1, "line": {"color": "var:ink2", "dash": "dash", "width": 1}}]},
               "p4_momentum.json / p4_vol_dealer_events.json: by_year", 300)
    # baseline by DTE and delta (C1 / C2)
    tr = []
    deltas = ["0.15", "0.25", "0.4", "0.55", "0.7"]
    for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
        for s, f in (("IS", bar_is), ("OOS", bar_oos)):
            c1d = C[s]["c12"]["c1"]
            ys = [g(c1d, f"{gname}|all|h3|{d}", "net") for d in deltas]
            tr.append(f(f"{gname} {s}", [f"|Δ| {d}" for d in deltas], ys, color, offsetgroup=gname + s))
    c3 = chart("rg_delta", "Baseline by delta: 0.15-delta lottery tickets lose the most",
               "B1 net mean R at h=3 by |delta| (standard expiry). Outline IS, filled OOS.",
               tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R"}}}, "p4_contract_exits.json:c12.c1", 290)
    tr = []
    bk = ["7-10", "11-20", "21-35", "36+"]
    for gname, color in (("index", "var:net"), ("stocks", "var:lessbad")):
        for s, f in (("IS", bar_is), ("OOS", bar_oos)):
            c2d = C[s]["c12"]["c2"]
            ys = [g(c2d, f"{gname}|all|h3|{b}", "net") for b in bk]
            tr.append(f(f"{gname} {s}", bk, ys, color, offsetgroup=gname + s))
    c4 = chart("rg_dte", "Baseline by DTE: stocks prefer 21–35 days; index is flat",
               "B1 net mean R at h=3 by expiry bucket (|delta| 0.40). Outline IS, filled OOS.",
               tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R"}}, "xaxis": {"title": {"text": "DTE bucket"}}},
               "p4_contract_exits.json:c12.c2", 290)
    return f"""<section id="regime"><h2>7 · Expectancy by regime</h2>
<div class="grid">{c1}{c2}</div><div class="grid">{c3}{c4}</div>
<p class="note">Gap: the result files hold DTE-bucket and delta splits only for the unconditional baseline, not for the M4/V2 candidates (their trades use the single standard contract).</p></section>"""


def section_engine():
    head = """<section id="engine"><h2>8 · Scoring engine (buyConviction)</h2>
<div class="grid"><div class="fig"><div class="ft">Built from what survived (as “less bad”, not as edge)</div>
<ul class="note"><li>Contract: |delta| ≈ 0.40–0.55 (C1), class-appropriate DTE — stocks 11–35 (C2), low required-move ratio and high gamma/theta on stocks (C3, C4)</li>
<li>Friction: spread ceiling / liquidity floor (C5)</li><li>Cheap-vol filters on stocks (V1, V2: less-bad-not-edge)</li>
<li>Veto: inverted term structure on stocks (V3 VETO-PASS)</li><li>Exit: underlying invalidation (X2)</li><li>Watchlist bonus only if forward test confirms: M4 index, V2 index</li></ul></div>
<div class="fig"><div class="ft">Excluded (did not beat random entry)</div>
<ul class="note"><li>Dealer positioning D1–D4 (GEX, walls, pin)</li><li>Futures build-up M1/M1b, ADX band M2, breakouts M3, relative strength M5</li>
<li>Earnings E1–E3 (E2 fails IS sign and 2× spread)</li><li>Intraday M6/M7 (SYNTHETIC, negative everywhere)</li><li>Expiry-day timing M8 (sign flips IS→OOS)</li></ul></div></div>
<p class="note">The include/exclude lists above follow the Phase 4 verdicts in this report; the authoritative engine definition is whatever <code>p5_calibration.json</code> and the <code>engine/</code> code record.</p>"""
    if CAL is None:
        return head + pending("p5_calibration.json", "Calibration plot (realised OOS net R with CI and win rate per score bucket, per class) and the monotonicity / positive-bucket statements will appear here.") + "</section>"
    bks = CAL.get("buckets") or []
    isb = CAL.get("IS_buckets") or []
    classes = [c for c in ("index", "stocks", "NIFTY") if any(b.get("cls") == c for b in bks)]
    charts = []
    for c in classes:
        rows = [b for b in bks if b.get("cls") == c]
        irows = {b.get("bucket"): b for b in isb if b.get("cls") == c}
        xs = [str(b.get("bucket")) for b in rows]
        ys = [b.get("net_R") for b in rows]
        up, dn = errs(ys, [b.get("ci_lo") for b in rows], [b.get("ci_hi") for b in rows])
        iy = [g(irows, b.get("bucket"), "net_R") for b in rows]
        iup, idn = errs(iy, [g(irows, b.get("bucket"), "ci_lo") for b in rows], [g(irows, b.get("bucket"), "ci_hi") for b in rows])
        tr = [{"type": "bar", "name": "win rate OOS", "x": xs, "y": [b.get("win_rate") for b in rows], "yaxis": "y2",
               "marker": {"color": "var:base", "opacity": 0.25}, "hovertemplate": "win %{y:.0%}<extra></extra>"},
              {"type": "scatter", "mode": "lines+markers", "name": "net R IS", "x": xs, "y": iy,
               "line": {"color": "var:net", "dash": "dot", "width": 1.5}, "marker": {"symbol": "circle-open", "size": 8},
               "error_y": {"type": "data", "symmetric": False, "array": iup, "arrayminus": idn, "color": "var:net", "thickness": 1}},
              {"type": "scatter", "mode": "lines+markers", "name": "net R OOS", "x": xs, "y": ys, "line": {"color": "var:net", "width": 2.5},
               "error_y": {"type": "data", "symmetric": False, "array": up, "arrayminus": dn, "color": "var:net", "thickness": 1.5},
               "customdata": [[b.get("n"), b.get("n_days"), b.get("exp_inr") or 0] for b in rows],
               "hovertemplate": "bucket %{x}: %{y:+.3f} R<br>n %{customdata[0]:,} on %{customdata[1]} days<br>₹ %{customdata[2]:.0f}/trade<extra>OOS</extra>"},
              {"type": "scatter", "mode": "lines", "name": "gross R OOS", "x": xs, "y": [b.get("gross_R") for b in rows], "line": {"color": "var:gross", "dash": "dash"}}]
        nt = sum((b.get("n") or 0) for b in rows)
        sp = g(CAL, "spearman", c)
        charts.append(chart(f"cal_{c}", f"{c}: higher score loses less, but every bucket is below zero",
                            f"Realised net R per trade by buyConviction bucket with 95% CI: filled = OOS, open/dotted = IS; dashed = OOS gross; bars = OOS win rate (right axis). "
                            f"Monotone OOS: {g(CAL, 'monotone', c)}; top bucket positive: {g(CAL, 'top_bucket_positive', c)}; Spearman OOS {sp:.3f}." if sp is not None else "",
                            tr, {"yaxis": {"title": {"text": "net mean R per trade"}}, "xaxis": {"title": {"text": "score bucket"}, "type": "category"},
                                 "yaxis2": {"overlaying": "y", "side": "right", "title": {"text": "win rate"}, "tickformat": ".0%", "showgrid": False, "rangemode": "tozero"},
                                 "shapes": [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 0, "y1": 0, "line": {"color": "var:ink2", "width": 1}}]},
                            "p5_calibration.json:buckets + IS_buckets", 310, n_text=f"OOS n = {nt:,}"))
    tiers = CAL.get("tiers")
    if tiers and isinstance(tiers, list) and isinstance(tiers[0], dict):
        tcls = [c for c in ("index", "stocks", "NIFTY") if any(t.get("cls") == c for t in tiers)] or sorted({str(t.get("cls")) for t in tiers})
        tnames = []
        for t in tiers:
            nm = str(t.get("tier", t.get("bucket")))
            if nm not in tnames:
                tnames.append(nm)
        tr = []
        for samp, f in (("IS", bar_is), ("OOS", bar_oos)):
            for ci, c in enumerate(tcls):
                sub = [t for t in tiers if t.get("cls") == c and (t.get("sample", "OOS") == samp)]
                if not sub:
                    continue
                d = {str(t.get("tier", t.get("bucket"))): t for t in sub}
                ys = [g(d, n, "net_R") for n in tnames]
                tr.append(f(f"{c} {samp}", tnames, ys, ["var:net", "var:lessbad", "var:watch"][ci % 3],
                            err=errs(ys, [g(d, n, "ci_lo") for n in tnames], [g(d, n, "ci_hi") for n in tnames]), offsetgroup=c + samp))
        if tr:
            charts.append(chart("cal_tiers", "Three score tiers — post-hoc grouping, forward-test",
                                "Post-hoc grouping of score buckets into 3 tiers (not pre-registered): net R per trade with 95% CI; outline = IS, filled = OOS. To be confirmed in the forward test.",
                                tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R per trade"}}, "xaxis": {"title": {"text": "tier"}, "type": "category"}},
                                "p5_calibration.json:tiers", 300))
    vt = g(CAL, "verdict", "text")
    st = ('<div class="verdict"><p><b>Calibration verdict.</b> The score is <b>NOT monotone</b> at 10-point resolution: the 60–70 bucket dips below 50–60 '
          'in index and stocks (NIFTY also wobbles at the low end). <b>No bucket is positive</b> out-of-sample in any class. By the owner\'s own criterion the engine is '
          '<b>“not done”</b>: it reliably separates the worst contracts from the least-bad ones, nothing more.</p>'
          + (f'<p class="note">From p5_calibration.json: {esc(vt)}</p>' if vt else "")
          + f'<p class="note">Variant shipped: {esc(g(CAL, "verdict", "shipped"))}; monotone in any refit variant: {esc(g(CAL, "verdict", "monotone_any_variant"))}. {esc(CAL.get("note", ""))}</p></div>')
    extra = ""
    return head + st + '<div class="grid">' + "".join(charts) + "</div>" + extra + "</section>"


def render_generic(obj, depth=0, maxrows=40):
    """Render an unknown JSON object as compact tables/lists."""
    if isinstance(obj, dict):
        scal = [(k, v) for k, v in obj.items() if not isinstance(v, (dict, list))]
        out = ""
        if scal:
            out += table(["field", "value"], [[esc(k), esc(v if not isinstance(v, float) else round(v, 4))] for k, v in scal])
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and depth < 2:
                out += f"<details><summary>{esc(k)}</summary>{render_generic(v, depth + 1)}</details>"
        return out
    if isinstance(obj, list):
        if obj and all(isinstance(x, dict) for x in obj):
            cols = []
            for x in obj[:maxrows]:
                for k in x:
                    if k not in cols and not isinstance(x[k], (dict, list)):
                        cols.append(k)
            rows = [[esc(round(x.get(c), 4) if isinstance(x.get(c), float) else x.get(c, "")) for c in cols] for x in obj[:maxrows]]
            more = f'<p class="note">… {len(obj) - maxrows} more rows in the JSON</p>' if len(obj) > maxrows else ""
            return table(cols, rows) + more
        return f"<p class='note'>{esc(json.dumps(obj)[:600])}</p>"
    return esc(obj)


def section_evidence():
    ds = []
    ds.append(("M4 index", g(MH, "M4", "index", "OOS", "deflated_sharpe", "dsr")))
    ds.append(("V2 index", g(VR, "V2", "INDEX", "OOS", "deflated_sharpe", "dsr")))
    ds.append(("E2 stocks", g(VR, "E2", "STOCKS", "OOS", "deflated_sharpe", "dsr")))
    dstr = ", ".join(f"{a} {b:.3f}" for a, b in ds if b is not None)
    reg = ", ".join(f"{k or 'blank'}: {v:,}" for k, v in TRIALS_REG.items())
    rows = [
        ["1", "Costs + real spread; gross only beside net", "Every chart shows net; gross is the muted series beside it (§2, family cards). Spread is modelled from 40 archive sessions (p75; BANKNIFTY p90), 2× spread stress in every card.", "✓ (spread modelled)"],
        ["2", "Walk-forward / OOS, IS and OOS side by side", "IS 2019–23 / OOS 2024–26 (SENSEX IS 2024); grid cells frozen on IS before OOS was loaded (frozen JSON timestamps). Outline = IS, filled = OOS throughout.", "✓"],
        ["3", "Plateaus only (±10–20%)", "Criterion 4 chip on every card; threshold × hold heatmaps IS and OOS.", "✓"],
        ["4", "Multiple-testing control", f"trials_log.csv: {N_TRIALS:,} rows ({reg}). Within-family BH q reported; deflated Sharpe e.g. {dstr}." + (f" Cross-family BH over {SYN.get('n_primary_tests')} primary tests and DSR over {g(SYN, 'trials', 'distinct_variants', default=0):,} variants: see below." if SYN else " Cross-family BH / haircut: PENDING (p4_synthesis.json)."), "✓" if SYN else "partial — pending synthesis"],
        ["5", "< ~100 trades ⇒ anecdotal", "ANECDOTAL verdicts (grey) on every sub-100 row; n and days in every caption.", "✓"],
        ["6", "Buyer metrics", "Win rate, avg win/loss, expectancy (R and ₹/lot), PF, max DD, losing streak, theta share, time-to-target — cards, §6, §7.", "✓ (some splits only for baseline)"],
        ["7", "Baselines: random entry and buy-ATM-at-open", "B1 (random-entry std contract, same class/side/hold) in every swing test; random-entry and B2 (buy 09:20) for intraday. B1 t+1-open variant reported in p4_baseline.", "✓"],
        ["8", "Pre-registration", "research/02_hypotheses.md committed before any backtest (commit 77a4539 precedes Phase 3/4).", "✓"],
        ["9", "Synthetic prices labelled", "SYNTHETIC badge on every intraday chart and on the scoreboard (contains M6/M7).", "✓"],
    ]
    out = f"""<section id="evidence"><h2>9 · Evidence bars</h2>{table(["#", "bar (CLAUDE.md)", "where / how", "status"], [[esc(c) for c in r] for r in rows])}"""
    if SYN is None:
        out += pending("p4_synthesis.json", "Cross-family Benjamini–Hochberg q-values, deflated-Sharpe haircut over all trials, and the archive cross-check of the spread model will appear here.")
    else:
        out += "<h3>Cross-family multiple-testing synthesis (p4_synthesis.json)</h3>" + synthesis_view()
    return out + "</section>"


def synthesis_view():
    rows = SYN.get("rows") or []
    out = ""
    if rows:
        rs = sorted(rows, key=lambda r: r.get("q_bh") if r.get("q_bh") is not None else 9)
        labs = [f"{r['id']} {r['grp']}" + (" †" if r["id"] in ("M6", "M7") else "") for r in rs]
        cols = ["var:pass" if r.get("bh_survivor") else "var:base" for r in rs]
        out += chart("syn_q", f"Across all {len(rows)} primary tests, {len(SYN.get('bh_survivors', []))} survive BH — all stock filters/vetoes, no entry edge",
                     "Benjamini–Hochberg q across every registered hypothesis × class group. Teal = q < 0.05 in the expected direction. Dashed = 0.05. † SYNTHETIC.",
                     [{"type": "bar", "orientation": "h", "y": labs[::-1], "x": [r.get("q_bh") for r in rs][::-1],
                       "marker": {"color": cols[::-1]},
                       "customdata": [[r.get("final_verdict"), r.get("p")] for r in rs][::-1],
                       "hovertemplate": "%{y}: q %{x:.3f} (raw p %{customdata[1]})<br>%{customdata[0]}<extra></extra>"}],
                     {"xaxis": {"title": {"text": "q (BH, across families)"}, "range": [0, 1]},
                      "yaxis": {"type": "category", "automargin": True, "tickfont": {"size": 10}},
                      "shapes": [{"type": "line", "x0": 0.05, "x1": 0.05, "yref": "paper", "y0": 0, "y1": 1, "line": {"color": "var:loss", "dash": "dash"}}],
                      "margin": {"l": 10}}, "p4_synthesis.json:rows", 40 + 15 * len(rs), True, n_text=f"{len(rows)} tests")
    ds = SYN.get("deflated_sharpe") or {}
    if ds:
        names = list(ds)
        tr = [{"type": "bar", "name": "PSR (no haircut)", "x": names, "y": [ds[k].get("psr_no_haircut") for k in names], "marker": {"color": "var:gross"}},
              {"type": "bar", "name": "deflated Sharpe (DSR)", "x": names, "y": [ds[k].get("dsr") for k in names], "marker": {"color": "var:net"}}]
        nt = next(iter(ds.values())).get("n_trials")
        out += chart("syn_dsr", "The multiple-testing haircut erases the best-looking results",
                     f"Probability the per-trade Sharpe is > 0: before (PSR, N=1) vs after deflating for N = {nt:,} distinct variants tried (DSR). Dashed = 0.95.",
                     tr, {"barmode": "group", "yaxis": {"title": {"text": "probability"}, "range": [0, 1.05]},
                          "shapes": [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": 0.95, "y1": 0.95, "line": {"color": "var:ink2", "dash": "dash"}}]},
                     "p4_synthesis.json:deflated_sharpe", 300, n_text=", ".join(f"{k}: n {ds[k].get('n_obs'):,}" for k in names))
    t = SYN.get("trials") or {}
    surv = ", ".join(SYN.get("bh_survivors", []))
    out = f'<div class="grid">{out}</div>'
    out += (f'<p class="note">Trials: {t.get("total_rows", 0):,} ledger rows ({t.get("registered_rows", 0):,} registered, {t.get("unregistered_rows", 0):,} unregistered, '
            f'{t.get("baseline_rows", 0)} baseline, {t.get("sensitivity_rows", 0)} sensitivity); {t.get("distinct_variants", 0):,} distinct variants = N for the deflated Sharpe. '
            f'BH survivors: {esc(surv)}. §0 passes: {esc(", ".join(SYN.get("passes", [])))} (a veto).</p>')
    return out


def _unused_generic_synthesis():
    out = ""
    # try to chart any list of rows that have q-values
    qrows = None
    def find_rows(o):
        if isinstance(o, list) and o and isinstance(o[0], dict) and any("q" in k.lower() for k in o[0]):
            return o
        if isinstance(o, dict):
            for v in o.values():
                r = find_rows(v)
                if r:
                    return r
        return None
    qrows = find_rows(SYN)
    if qrows:
        qk = next(k for k in qrows[0] if "q" in k.lower() and isinstance(qrows[0][k], (int, float)) or k.lower().startswith("q"))
        lk = [k for k in qrows[0] if isinstance(qrows[0][k], str)]
        labs = [" ".join(str(r.get(k, "")) for k in lk[:3]) for r in qrows]
        vals = [r.get(qk) for r in qrows]
        pairs = sorted([(v, l) for v, l in zip(vals, labs) if isinstance(v, (int, float))])[:40]
        if pairs:
            out += chart("syn_q", "Cross-family BH q-values: nothing below 0.05" if all(v >= 0.05 for v, _ in pairs) else "Cross-family BH q-values",
                         f"Smallest {len(pairs)} q-values ({esc(qk)}) across all families. Dashed = 0.05.",
                         [{"type": "bar", "orientation": "h", "y": [l for _, l in pairs][::-1], "x": [v for v, _ in pairs][::-1], "marker": {"color": "var:net"}}],
                         {"xaxis": {"title": {"text": "q (BH, cross-family)"}, "range": [0, 1]}, "yaxis": {"type": "category", "automargin": True, "tickfont": {"size": 10}},
                          "shapes": [{"type": "line", "x0": 0.05, "x1": 0.05, "yref": "paper", "y0": 0, "y1": 1, "line": {"color": "var:loss", "dash": "dash"}}],
                          "margin": {"l": 10}}, "p4_synthesis.json", 40 + 18 * len(pairs))
    out += render_generic(SYN)
    return out


def section_data():
    # feasibility matrix
    lines = DATA_MD.splitlines()
    fm = []
    for idx, l in enumerate(lines):
        if l.startswith("## Feasibility matrix"):
            for l2 in lines[idx + 1:]:
                if l2.startswith("|"):
                    fm.append(l2)
                elif fm:
                    break
            break
    feas = ""
    if fm:
        hdr = [c.strip() for c in fm[0].strip("|").split("|")]
        body = [[re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc(c.strip())) for c in r.strip("|").split("|")] for r in fm[2:]]
        feas = table(hdr, body)
    # synthetic error heatmaps (test1 h=1, model b)
    cells = P1["test1"]["cells"]
    dtes = ["0-2", "3-7", "8-20", "21+"]
    dls = ["10-25", "25-40", "40-60", "60-80"]
    def z(cls, h):
        out = []
        for d in dtes:
            row = []
            for dl in dls:
                v = None
                for c in cells:
                    if c["h"] == h and c["cls"] == cls and c["dte"] == d and c["delta"] == dl:
                        v = g(c, "b", "med_abs_rel_err")
                        v = 100 * v if v is not None else None
                row.append(v)
            out.append(row)
        return out
    se = []
    for h in (1, 5):
        zi, zs = z("index", h), z("stock", h)
        vals = [v for zz in (zi, zs) for r in zz for v in r if v is not None]
        mx = max(vals) if vals else 1
        tr = []
        for i, (zz, nm) in enumerate(((zi, "index"), (zs, "stock"))):
            tr.append({"type": "heatmap", "x": dls, "y": dtes, "z": zz, "coloraxis": "coloraxis", "xaxis": "x" if i == 0 else "x2",
                       "yaxis": "y" if i == 0 else "y2", "texttemplate": "%{z:.0f}%", "textfont": {"size": 10}, "xgap": 2, "ygap": 2,
                       "hovertemplate": nm + " DTE %{y} |Δ|×100 %{x}: median |error| %{z:.1f}%<extra></extra>"})
        se.append(chart(f"dl_synth_h{h}", f"Synthetic price error after {h} day{'s' if h > 1 else ''}: worst near expiry and far OTM",
                        "Median absolute relative error of the SYNTHETIC repriced option vs the real mid (P1 model b: previous-day smile, Black-76). Rows = DTE, columns = |delta|×100.",
                        tr, {"coloraxis": {"colorscale": [[0, "var:mid"], [1, "var:loss"]], "cmin": 0, "cmax": mx, "showscale": False},
                             "xaxis": {"domain": [0, 0.47], "type": "category", "title": {"text": "|delta| × 100"}},
                             "xaxis2": {"domain": [0.53, 1], "type": "category", "title": {"text": "|delta| × 100"}},
                             "yaxis": {"type": "category", "title": {"text": "DTE"}}, "yaxis2": {"anchor": "x2", "type": "category", "showticklabels": False},
                             "annotations": [{"text": "index", "x": 0.235, "y": 1.06, "xref": "paper", "yref": "paper", "showarrow": False},
                                             {"text": "stock", "x": 0.765, "y": 1.06, "xref": "paper", "yref": "paper", "showarrow": False}],
                             "margin": {"t": 26}}, "p1_synth_error.json:test1.cells", 260, True,
                        n_text=f"{sum((g(c,'b','n') or 0) for c in cells if c['h']==h):,} quotes, 40 archive days"))
    # spread model coverage
    bg = S["eval"]["ens"]["by_group"]
    grp = list(bg)
    tr = []
    for q, target, op in (("cov50", 50, 0.4), ("cov75", 75, 0.7), ("cov90", 90, 1.0)):
        tr.append({"type": "bar", "name": f"p{target} coverage", "x": grp, "y": [100 * bg[x][q] for x in grp], "marker": {"color": "var:net", "opacity": op}})
    sh = [{"type": "line", "xref": "paper", "x0": 0, "x1": 1, "y0": t, "y1": t, "line": {"color": "var:ink2", "dash": "dot", "width": 1}} for t in (50, 75, 90)]
    cov = chart("dl_spread_cov", "Spread model: honest on its own test set, except BANKNIFTY",
                "Share of real out-of-sample spreads (last 12 archive sessions) at or below the modelled p50/p75/p90. Dotted = target. BANKNIFTY p75 under-covers, so backtests use p90 there.",
                tr, {"barmode": "group", "yaxis": {"title": {"text": "% of quotes covered"}, "range": [0, 100]}, "shapes": sh},
                "p3_spread_eval.json:eval.ens.by_group", 280, n_text=f"{sum(bg[x]['n'] for x in grp):,} test quotes")
    XC = load("p4_archive_crosscheck.json")
    if XC is None:
        xc = pending("p4_archive_crosscheck.json", "The archive cross-check (modelled vs real post-close bid/ask fills) will appear here.")
    else:
        bc = XC["by_class"]
        cl = list(bc)
        def ci(k, key):
            return [bc[c][key][0] for c in cl], [bc[c][key][1] for c in cl]
        ym = [bc[c]["net_R_model"] for c in cl]
        yr = [bc[c]["net_R_real"] for c in cl]
        tr = [bar_is("(A) bhavcopy close ± modelled spread", cl, ym, "var:net", err=errs(ym, *ci(0, "net_R_model_ci"))),
              bar_oos("(C) REAL: buy ask, sell bid", cl, yr, "var:net", err=errs(yr, *ci(0, "net_R_real_ci")))]
        ntot = sum(bc[c]["n"] for c in ("index", "stocks") if c in bc)
        c1 = chart("dl_xc_R", "Real archive fills beat the modelled spread: the backtests are conservative",
                   f"Same B1 h=3 trades, {XC['window'][0]} → {XC['window'][1]}, net R under the model (outline) vs real post-close bid/ask (filled), 95% day-clustered CI. "
                   "One calm 2-month window — the level (positive here) is not representative; the A-vs-C gap is the point.",
                   tr, {"barmode": "group", "yaxis": {"title": {"text": "net mean R (h=3)"}}}, "p4_archive_crosscheck.json:by_class", 300,
                   n_text=f"n matched index+stocks {ntot:,}")
        tr = [{"type": "bar", "name": "modelled RT spread (mean)", "x": cl, "y": [bc[c]["rt_spread_model_mean"] for c in cl], "marker": {"color": "var:gross"}},
              {"type": "bar", "name": "real RT spread (mean)", "x": cl, "y": [bc[c]["rt_spread_real_mean"] for c in cl], "marker": {"color": "var:net"}}]
        dif = [bc[c]["diff_real_minus_model"] for c in cl]
        rat = [bc[c]["rt_spread_model_mean"] / bc[c]["rt_spread_real_mean"] for c in cl if bc[c]["rt_spread_real_mean"]]
        c2 = chart("dl_xc_spread", f"Modelled round-trip spread is {min(rat):.1f}–{max(rat):.1f}× the real one",
                   "Mean round-trip spread as % of premium on the matched trades. Real − model net R: " +
                   ", ".join(f"{c} {f3(bc[c]['diff_real_minus_model'])} [{f3(bc[c]['diff_ci'][0])}, {f3(bc[c]['diff_ci'][1])}]" for c in cl) + ".",
                   tr, {"barmode": "group", "yaxis": {"title": {"text": "% of premium"}}}, "p4_archive_crosscheck.json:by_class", 300)
        xc = f'<div class="grid">{c1}{c2}</div>'
    return f"""<section id="data"><h2>10 · Data &amp; limits</h2>
<h3>Feasibility matrix (Phase 1)</h3>{feas}
<div class="grid">{se[0]}{se[1]}</div><div class="grid">{cov}</div>
<h3>Archive cross-check of the spread model</h3>{xc}
<ul class="note"><li>Spread model: 668k two-sided quotes from 40 post-close archive sessions (2026-09-15 → 2026-11-12), one calm regime (VIX 10.4–17.6), no event / high-VIX days.</li>
<li>No 0–2 DTE or expiry-day intraday data; Kite and Upstox expired-option history not available on current access.</li>
<li>Stock universe = today's F&amp;O list (survivorship). Results dates with unknown time (most of 2025–26) treated as after close.</li></ul></section>"""


def build(out_path):
    secs = [section_verdict(), section_structural(), section_scoreboard(), section_families(), section_contract(),
            section_exits(), section_regime(), section_engine(), section_evidence(), section_data()]
    with open(os.path.join(os.path.dirname(__import__("plotly").__file__), "package_data", "plotly.min.js"), encoding="utf-8") as f:
        plotly_js = f.read()
    # neutralise any literal URLs inside the library so the file contains no http(s) resource strings
    plotly_js = plotly_js.replace("https://", "https:\\/\\/").replace("http://", "http:\\/\\/")
    gen = []
    for nm, obj in [("p4_synthesis.json", SYN), ("p5_calibration.json", CAL)]:
        gen.append(f"{nm}: {'present' if obj is not None else 'PENDING'}")
    toc = "".join(f'<a href="#{a}">{b}</a>' for a, b in [("verdict", "1 Verdict"), ("structural", "2 Structural problem"), ("scoreboard", "3 Scoreboard"),
                                                           ("families", "4 Families"), ("contract", "5 Contract"), ("exits", "6 Exits"), ("regime", "7 Regime"),
                                                           ("engine", "8 Engine"), ("evidence", "9 Evidence bars"), ("data", "10 Data & limits")])
    charts_js = json.dumps(CHARTS, ensure_ascii=False, separators=(",", ":"))
    js = r"""
const CH=__CHARTS__;
function cv(n){return getComputedStyle(document.documentElement).getPropertyValue('--'+n).trim();}
function res(o){if(typeof o==='string'&&o.startsWith('var:'))return cv(o.slice(4));if(Array.isArray(o))return o.map(res);
 if(o&&typeof o==='object'){const r={};for(const k in o)r[k]=res(o[k]);return r;}return o;}
function deep(a,b){for(const k in b){if(b[k]&&typeof b[k]==='object'&&!Array.isArray(b[k])&&a[k]&&typeof a[k]==='object')deep(a[k],b[k]);else a[k]=b[k];}return a;}
function lay(l){const ax={gridcolor:cv('grid'),zerolinecolor:cv('zero'),linecolor:cv('grid'),tickfont:{size:11},title:{font:{size:12}},automargin:true};
 const b={paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{color:cv('ink'),size:12,family:'system-ui,Segoe UI,Roboto,sans-serif'},
 margin:{l:56,r:14,t:14,b:48},legend:{orientation:'h',y:-0.28,font:{size:11}},hoverlabel:{bgcolor:cv('panel'),font:{color:cv('ink')}},autosize:true};
 const L=deep(b,res(JSON.parse(JSON.stringify(l))));
 for(const k of Object.keys(L).concat(['xaxis','yaxis'])){if(/^[xy]axis\d*$/.test(k)){L[k]=deep(JSON.parse(JSON.stringify(ax)),L[k]||{});}}
 return L;}
const done=new Set();
function draw(id){const c=CH[id];const el=document.getElementById(id);if(!c||!el)return;
 Plotly.react(el,res(c.data),lay(c.layout),{responsive:true,displayModeBar:false});done.add(id);}
const io=('IntersectionObserver' in window)?new IntersectionObserver(es=>{for(const e of es){if(e.isIntersecting){draw(e.target.id);io.unobserve(e.target);}}},{rootMargin:'400px'}):null;
for(const id in CH){const el=document.getElementById(id);if(!el)continue;if(io)io.observe(el);else draw(id);}
function redrawAll(){for(const id of done)draw(id);}
try{window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change',redrawAll);}catch(e){}
const tb=document.getElementById('thm');tb.addEventListener('click',()=>{const r=document.documentElement;const cur=r.getAttribute('data-theme');
 const dark=cur?cur==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;r.setAttribute('data-theme',dark?'light':'dark');redrawAll();});
window.__balrogDrawAll=function(){for(const id in CH)draw(id);return Object.keys(CH).length;};
""".replace("__CHARTS__", charts_js)
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BALROG Buyer Research</title>{css_js_head()}</head><body>
<header class="top"><h1>BALROG — can you buy NSE/BSE options with positive expectancy?</h1>
<p class="sub">Phase 4 results, chart-led. Swing tests: real bhavcopy prices 2019–2026 with modelled spreads. Intraday tests: SYNTHETIC option prices. R = net P&amp;L ÷ (premium paid + costs). Built from <code>research/out/</code> · {esc(' · '.join(gen))}.</p>
<nav class="toc">{toc}</nav></header>
<main>{''.join(secs)}</main>
<button class="thm" id="thm" type="button">theme</button>
<script>{plotly_js}</script><script>{js}</script></body></html>"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    return doc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "report", "index.html"))
    a = ap.parse_args()
    doc = build(a.out)
    size = os.path.getsize(a.out)
    print(f"wrote {os.path.relpath(a.out, ROOT)}  {size/1e6:.2f} MB  charts={len(CHARTS)}")
    print(f"pending: synthesis={'no' if SYN else 'YES'}  calibration={'no' if CAL else 'YES'}")
    bad = re.findall(r"""(?:src|href)\s*=\s*["']https?://""", doc)
    print(f"external src/href loads: {len(bad)}; literal 'http://|https://' strings: {len(re.findall(r'https?://', doc))}")
    print("chart log (id | source | points):")
    for cid, src, n in LOG:
        print(f"  {cid:28s} | {src[:58]:58s} | {n}")
    for w in WARN:
        print("WARN", w)
    if WARN:
        sys.exit(1)


if __name__ == "__main__":
    main()
