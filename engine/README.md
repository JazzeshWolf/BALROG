# BALROG buyConviction engine

**What it is:** a ranking that helps you lose less when buying options. It says which contracts are less bad to buy. It does not claim an edge (`edge_claim: "none — loss-avoidance ranking"`). No entry hypothesis passed `research/02_hypotheses.md` §0, so per §8 the engine only steers a buyer away from structural losses. Every expected value it produces is **negative**.

**Input:** one Xerxes snapshot (`archive/<date>/{indices,stocks}/<SYM>.json.gz` or a live file), used as-is. The model is in `engine/model.json`. It was fitted on in-sample data only (2019–2024) by `research/scripts/p5_fit_engine.py`. The code is stdlib only, apart from `engine/costs.py`.

```
python -m engine.buy_conviction <snapshot.json[.gz]> [--top 10] [--cls index|stocks] [--json out.json]
# Python:
from engine import buy_conviction as bc
res = bc.score_snapshot(bc.read_snapshot("nifty.json.gz"), top=10)   # res["top"], res["contracts"]
```

**Output, per contract:**
- `buyConviction` (0–100 or null) and `tier`: avoid < 40, neutral 40–60, least-bad ≥ 60; null if not scored.
- `expected_net_R` (the model's number) and `components` (each component's contribution in R).
- `missing` (components with no input; their weight is spread pro-rata over the rest).
- `gates`, `regime` (tested / untested / not_scoreable), `inputs`, `iv_mid`, `delta`, `friction_pct`.
- `watch` (index only, zero weight).
- `trade`: limit order at mid with a max ask; hold 1–3 days as a swing (intraday is never recommended); X2 underlying-invalidation exit; 3-day time stop, never held into expiry day; no TP; SL is informational only; max premium at risk in ₹.

**Top level:** `edge_claim`, `calibration_status`, `iv_percentile`, `regime_counts`, `top` (ranked by score, at most 2 strikes per side per expiry).

**Pricing:** mid = (bid + ask) / 2. IV and the Greeks are re-derived from the mid with Black-76. The forward comes from the same-expiry future, else put-call parity, else spot·e^{rT}. The stored `iv` and `ltp` are never used.
## Gates (null score, never a low score)
- No two-sided quote, or no IV from the mid → `not_scoreable`.
- Friction (round-trip spread + all costs as % of mid) > 10% → `not_scoreable`.
- `untested`: DTE < 7, |delta| outside 0.05–0.80, mid < ₹2, or volume below 500 (index) / 50 (stock) contracts.

## Components (additive expected net R, shrunk toward 0, monotone where the hypothesis says so)
| component | source | OOS evidence (P4) | fitted effect range, IS |
|---|---|---|---|
| friction (real bid/ask + costs) | C5 | spread ceiling drives net R in every group | index +.021 → −.029; stocks +.033 → −.024 |
| abs(delta) | C1 | index SUPP (d.70 − d.15 +.073); stocks contrast only | index ≈0; stocks .15 −.010 → .70 +.015 |
| DTE bucket | C2 | stocks SUPP (21–35 best) | index ≈0; stocks 7–10 −.065, 21–35 +.033 |
| required-move ratio (`sigmaForecast`) | C3 | stocks SUPP (+.054), index n.s. | index 0; stocks +.011 → −.017 |
| IV percentile (`ivHistory`, ≥ 20 clean points) | V2 | stocks less-bad (+.051 vs B1) | stocks +.008 → −.006 |
| VRP (`metrics.vrp`) | V1 | stocks less-bad (+.038 vs B1) | shrunk to ≈0 |
| inverted term (`term`) | V3 veto | stocks VETO-PASS (−.013) | shrunk to ≈0 |

**Excluded (failed or anecdotal in P4):** GEX (D1/D4), OI walls (D2/D3), futures structure (M1/M1b), ADX (M2), breakouts (M3), relative strength (M5), earnings (E1–E3), intraday ORB/momentum (M6/M7, synthetic), C4 gamma/theta. Two watch flags are emitted with zero weight, labelled "forward-test candidate, not evidence": `watch_gap_hold` (M4 index) and `watch_low_ivpct` (V2 index).

## Out-of-sample calibration (2024-01 → 2026-11; `research/out/p5_calibration.json`)
Net mean R per trade, h = 3, with day-clustered 95% CI. The calibration inputs come from bhavcopy, with the modelled spread in place of real quotes.

| bucket | index | stocks | NIFTY |
|---|---|---|---|
| <40 | −.086 [−.117, −.052] | −.092 [−.105, −.077] | −.076 [−.148, +.002] |
| 40–50 | −.083 [−.111, −.052] | −.054 [−.062, −.043] | −.088 [−.159, −.012] |
| 50–60 | −.051 [−.079, −.022] | −.045 [−.056, −.035] | −.082 [−.125, −.032] |
| 60–70 | −.072 [−.096, −.045] | −.056 [−.065, −.047] | −.061 [−.099, −.021] |
| 70–80 | −.030 [−.056, −.002] | −.050 [−.060, −.041] | −.035 [−.061, −.009] |
| 80+ | −.039 [−.056, −.021] | −.035 [−.044, −.025] | −.040 [−.058, −.022] |
| **tier** avoid | −.086 [−.117, −.052] n 4,943 | −.092 [−.105, −.077] n 515,178 | −.076 [−.148, +.002] n 850 |
| neutral | −.063 [−.088, −.035] n 10,908 | −.051 [−.059, −.041] n 128,104 | −.084 [−.127, −.035] n 2,847 |
| least-bad | −.047 [−.066, −.027] n 24,416 | −.043 [−.052, −.034] n 237,074 | −.043 [−.064, −.021] n 17,921 |

- Spearman correlation between score and net R: index +0.058, stocks +0.166, NIFTY +0.087.
- None of the three is monotone across the six buckets. Two simpler refits fail the same way.
- The three tiers are monotone for index and stocks, but not for NIFTY. The tier cut-points were chosen after looking at the results.
- **No bucket or tier has positive expectancy.**

## Known limits
- **Calibration spread:** calibration uses the modelled p75 spread; live scoring uses the real bid/ask. The archive cross-check found the model overstates spread, so P4 results understate buyer net R by about +0.04 R (index) and +0.02 R (stocks).
- **DTE < 7 untested:** 0–6 DTE, including 0–2 DTE expiry trades, was never tested. NIFTY's nearest weekly is usually `untested`.
- **`watch_gap_hold` is always null:** the snapshot has no day open.
- **Holidays:** `exit_by` counts weekdays and ignores exchange holidays.
- **Deep-ITM stock preference:** for stocks the model favours deep ITM (|delta| ~0.7–0.8) and next-month expiries. Out of sample, 0.55 did better than 0.70 (−.069 vs −.081 R), so treat this preference with suspicion.
- **Physical settlement:** stock options settle physically. Never hold ITM into expiry; the output carries a warning.
- **Narrow evidence base:** a single post-close snapshot per day; only one archive window (2026-09 → 11) had real quotes.
