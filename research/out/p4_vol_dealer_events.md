# P4 - Volatility, dealer-positioning and event hypotheses (V1-V5, D1-D4, E1-E3)
Generated 2026-11-14 12:15 by `research/scripts/p4_run_vol_dealer_events.py`. Swing, §0 standard contract (|delta| 0.40, std expiry), real bhavcopy, close fills ± modelled half-spread, statutory costs, 1 lot. Strangle R = combined net ₹ ÷ combined premium paid incl. costs. CI = day-clustered bootstrap 95%; p = raw two-sided vs B1 (same class/side mix/hold; BH across all families pending). primary excludes ca_break-in-hold, past-data-end, settle-priced and unmarked exits; no winsorising (cap +5 gross as robustness); one open position per symbol x hypothesis x side (re-entry allowed at the prior exit close); D2 primary = cross of previous day's wall; E: one event per symbol-quarter, unknown time = after close; BANKNIFTY spread p90; grid cell chosen on IS (max IS net mean R at the registered hold, n>=30) and frozen.

Criteria (OOS): 1 net>0 CI excl 0 | 2 beats B1 p<.05 (raw) | 3 OOS ≥ 50% IS, same sign | 4 ±20% plateau | 5 n≥100 on ≥40 days | 6 2× spread net ≥ 0. Veto rows: 1-2 = worse than B1, 3 = IS diff same sign, 6 = still worse at 2×.

| hyp | class | params | IS net (gross) | OOS net (gross) | OOS vs B1 [CI] p | n / days OOS | 1 2 3 4 5 6 | verdict |
|---|---|---|---|---|---|---|---|---|
| V1 | NIFTY | c=0.9 | +0.000 (+0.034) | -0.020 (+0.010) | +0.016 [-0.284,+0.400] 0.947 | 20 / 20 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| V1 | BANKNIFTY | c=0.9 | +0.064 (+0.175) | +0.198 (+0.277) | +0.240 [-0.274,+0.923] 0.383 | 12 / 12 | ✗ ✗ ✓ ✗ ✗ ✓ | ANECDOTAL |
| V1 | SENSEX | c=0.9 | -0.034 (+0.062) | +0.375 (+0.494) | +0.436 [-0.444,+2.009] 0.741 | 5 / 5 | ✗ ✗ ✗ ✗ ✗ ✓ | ANECDOTAL |
| V1 | INDEX | c=0.9 | +0.051 (+0.137) | +0.090 (+0.159) | +0.146 [-0.136,+0.457] 0.353 | 55 / 46 | ✗ ✗ ✓ ✗ ✗ ✓ | ANECDOTAL |
| V1 | STOCKS | c=0.8 | -0.104 (-0.027) | -0.048 (+0.027) | +0.038 [+0.011,+0.071] 0.003 | 4734 / 495 | ✗ ✓ ✗ ✗ ✓ ✗ | LESS-BAD-NOT-EDGE |
| V2 | NIFTY | c=20 | -0.045 (-0.012) | +0.197 (+0.233) | +0.233 [-0.048,+0.568] 0.110 | 32 / 32 | ✗ ✗ ✗ ✓ ✗ ✓ | ANECDOTAL |
| V2 | BANKNIFTY | c=20 | -0.062 (+0.028) | +0.123 (+0.180) | +0.166 [-0.047,+0.424] 0.161 | 37 / 37 | ✗ ✗ ✗ ✗ ✗ ✓ | ANECDOTAL |
| V2 | SENSEX | c=20 | +0.328 (+0.461) | +0.120 (+0.223) | +0.181 [-0.099,+0.510] 0.221 | 31 / 31 | ✗ ✗ ✗ ✓ ✗ ✓ | ANECDOTAL |
| V2 | INDEX | c=20 | +0.003 (+0.076) | +0.077 (+0.151) | +0.134 [+0.007,+0.269] 0.037 | 168 / 127 | ✗ ✓ ✓ ✗ ✓ ✓ | FAIL |
| V2 | STOCKS | c=10 | -0.101 (-0.027) | -0.035 (+0.042) | +0.051 [+0.014,+0.089] 0.003 | 4816 / 363 | ✗ ✓ ✗ ✗ ✓ ✗ | LESS-BAD-NOT-EDGE |
| V3 | NIFTY | c=0 | -0.046 (-0.015) | -0.083 (-0.058) | -0.055 [-0.127,+0.022] 0.155 | 120 / 120 | ✗ ✗ ✓ ✓ ✓ ✓ | FAIL |
| V3 | BANKNIFTY | c=0 | -0.128 (-0.044) | -0.084 (-0.038) | -0.031 [-0.077,+0.016] 0.188 | 168 / 168 | ✗ ✗ ✓ ✓ ✓ ✓ | FAIL |
| V3 | SENSEX | c=0 | -0.226 (-0.143) | -0.148 (-0.062) | -0.056 [-0.125,+0.018] 0.113 | 102 / 102 | ✗ ✗ ✗ ✓ ✓ ✓ | FAIL |
| V3 | INDEX | c=0 | -0.111 (-0.049) | -0.086 (-0.030) | -0.024 [-0.059,+0.009] 0.162 | 545 / 367 | ✗ ✗ ✓ ✓ ✓ ✓ | FAIL |
| V3 | STOCKS | c=0 | -0.119 (-0.053) | -0.086 (-0.023) | -0.013 [-0.021,-0.005] 0.001 | 18323 / 544 | ✓ ✓ ✓ ✓ ✓ ✓ | VETO-PASS |
| V4 | NIFTY | t=33.3,c=0 | -0.070 (-0.038) | +0.069 (+0.098) | +0.105 [-0.175,+0.405] 0.495 | 17 / 17 | ✗ ✗ ✗ ✓ ✗ ✓ | ANECDOTAL |
| V4 | BANKNIFTY | t=33.3,c=0 | -0.126 (-0.044) | -0.055 (-0.014) | -0.012 [-0.165,+0.150] 0.848 | 17 / 17 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| V4 | SENSEX | t=33.3,c=0 | -0.100 (+0.002) | -0.092 (-0.002) | -0.031 [-0.279,+0.253] 0.795 | 17 / 17 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| V4 | INDEX | t=33.3,c=0 | -0.134 (-0.071) | -0.024 (+0.039) | +0.033 [-0.144,+0.224] 0.782 | 84 / 31 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| V4 | STOCKS | t=33.3,c=0 | -0.147 (-0.078) | -0.013 (+0.063) | +0.072 [-0.024,+0.171] 0.134 | 3203 / 33 | ✗ ✗ ✗ ✗ ✗ ✗ | N/A (index-only rule; stock run unregistered: ANECDOTAL) |
| V5 | NIFTY | - | -0.042 (-0.010) | -0.036 (-0.007) | +0.000 [+0.000,+0.000] 1.000 | 701 / 701 | - | FAIL (contrast) |
| V5 | BANKNIFTY | - | -0.085 (+0.000) | -0.043 (+0.010) | +0.000 [+0.000,+0.000] 1.000 | 700 / 700 | - | FAIL (contrast) |
| V5 | SENSEX | - | -0.044 (+0.057) | -0.061 (+0.025) | +0.000 [+0.000,+0.000] 1.000 | 449 / 449 | - | FAIL (contrast) |
| V5 | INDEX | - | -0.066 (-0.002) | -0.057 (+0.005) | +0.000 [+0.000,+0.000] 1.000 | 2949 / 702 | - | FAIL (contrast) |
| V5 | STOCKS | - | -0.142 (-0.068) | -0.086 (-0.013) | +0.000 [+0.000,+0.000] 1.000 | 102609 / 542 | - | FAIL (contrast) |
| D1 | NIFTY | c=10 | -0.036 (-0.005) | -0.013 (+0.014) | +0.015 [-0.119,+0.153] 0.868 | 56 / 56 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D1 | BANKNIFTY | c=10 | -0.037 (+0.051) | -0.061 (-0.014) | -0.007 [-0.114,+0.123] 0.841 | 43 / 43 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D1 | SENSEX | c=10 | +0.147 (+0.281) | +0.031 (+0.149) | +0.122 [-0.015,+0.274] 0.079 | 51 / 51 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D1 | INDEX | c=10 | -0.032 (+0.035) | -0.030 (+0.036) | +0.032 [-0.034,+0.102] 0.318 | 220 / 168 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |
| D1 | STOCKS | c=10 | -0.064 (+0.004) | -0.066 (-0.003) | +0.006 [-0.011,+0.023] 0.478 | 5052 / 457 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |
| D2 | NIFTY | - | -0.006 (+0.024) | -0.071 (-0.050) | -0.045 [-0.192,+0.113] 0.509 | 74 / 74 | ✗ ✗ ✗ ✓ ✗ ✗ | ANECDOTAL |
| D2 | BANKNIFTY | - | -0.026 (+0.071) | -0.026 (+0.025) | +0.031 [-0.082,+0.147] 0.618 | 99 / 99 | ✗ ✗ ✗ ✓ ✗ ✗ | ANECDOTAL |
| D2 | SENSEX | - | +0.002 (+0.132) | -0.077 (+0.033) | +0.036 [-0.134,+0.209] 0.667 | 54 / 54 | ✗ ✗ ✗ ✓ ✗ ✗ | ANECDOTAL |
| D2 | INDEX | - | -0.008 (+0.063) | -0.076 (-0.017) | -0.012 [-0.082,+0.060] 0.756 | 351 / 259 | ✗ ✗ ✗ ✓ ✓ ✗ | FAIL |
| D2 | STOCKS | - | -0.075 (-0.012) | -0.057 (+0.003) | +0.011 [-0.015,+0.037] 0.449 | 13587 / 545 | ✗ ✗ ✗ ✓ ✓ ✗ | FAIL |
| D3 | NIFTY | prox=1,c=15 | -0.144 (-0.113) | -0.532 (-0.519) | -0.491 [-0.854,+0.058] 0.066 | 6 / 6 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D3 | BANKNIFTY | prox=1,c=15 | -0.252 (-0.184) | +0.305 (+0.367) | +0.356 [-0.288,+0.773] 0.114 | 4 / 4 | ✗ ✗ ✗ ✓ ✗ ✓ | ANECDOTAL |
| D3 | SENSEX | prox=1,c=15 | n/a | n/a | n/a | 0 / 0 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D3 | INDEX | prox=1,c=15 | -0.213 (-0.164) | -0.020 (+0.043) | +0.039 [-0.235,+0.311] 0.810 | 22 / 22 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D3 | STOCKS | prox=1,c=15 | -0.069 (+0.009) | -0.024 (+0.046) | +0.051 [-0.013,+0.122] 0.119 | 752 / 387 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |
| D4 | NIFTY | q=20,k=0.5,dte=5 | -0.062 (-0.034) | -0.066 (-0.043) | -0.021 [-0.102,+0.070] 0.583 | 22 / 22 | ✗ ✗ ✓ ✗ ✗ ✓ | ANECDOTAL |
| D4 | BANKNIFTY | q=20,k=0.5,dte=5 | -0.147 (-0.060) | -0.006 (+0.079) | +0.047 [-0.077,+0.172] 0.458 | 11 / 11 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D4 | SENSEX | q=20,k=0.5,dte=5 | n/a | -0.154 (-0.049) | -0.069 [-0.152,+0.007] 0.079 | 11 / 11 | ✗ ✗ ✗ ✓ ✗ ✓ | ANECDOTAL |
| D4 | INDEX | q=20,k=0.5,dte=5 | -0.102 (-0.045) | -0.043 (+0.024) | +0.022 [-0.070,+0.129] 0.742 | 52 / 50 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| D4 | STOCKS | q=20,k=0.5,dte=5 | -0.222 (-0.140) | -0.172 (-0.098) | -0.045 [-0.095,+0.011] 0.115 | 598 / 43 | ✗ ✗ ✓ ✗ ✓ ✓ | FAIL |
| E1 | STOCKS | - | -0.084 (-0.028) | -0.070 (-0.020) | -0.007 [-0.025,+0.010] 0.392 | 1481 / 248 | ✗ ✗ ✓ ✓ ✓ ✓ | FAIL |
| E2 | STOCKS | - | -0.059 (+0.015) | +0.042 (+0.122) | +0.128 [+0.094,+0.163] 0.001 | 1461 / 259 | ✓ ✓ ✗ ✓ ✓ ✗ | FAIL |
| E3 | STOCKS | c=2 | -0.024 (+0.041) | -0.077 (-0.025) | -0.004 [-0.080,+0.081] 0.916 | 406 / 180 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |

**V5 contrast** (unconditional strangle h5, stocks − indices): IS -0.142 vs -0.066 (diff -0.075); OOS -0.086 vs -0.057, diff -0.029 [-0.064,+0.003] p=0.080 -> **FAIL**; both ≤ 0 OOS: True.

Trials: 1148 rows appended; trials_log.csv now 3222 rows.

## Reading
Index rows for E1-E3 are N/A (results dates exist for stocks only); V4 is index-only by register (stock run logged registered=no). 16 BSE-listed stock-option std legs dropped (stocks = NSE only).
- **V1** (VRP < c): indices too rare to judge (55 OOS trades); stocks c=0.8 beat B1 by +0.04 R (p=.003) but stay net negative -> filter only. The VRP ratio is biased high (HAR ignores gaps), so "cheap" days are scarce.
- **V2** (low IV pct): index OOS +0.077 R, +0.13 vs B1 (p=.037), but the CI includes 0, 2024 (22 trades, +0.50) does most of the work, and 16-pct gives only +0.02 -> FAIL; the closest thing to a candidate in this family. Stocks: less bad, not an edge.
- **V3** (inverted term structure, veto): stocks are worse than B1 by only -0.013 R, but it is significant and stable -> VETO-PASS (small penalty). Indices point the same way (-0.02 to -0.06) without significance.
- **V4** (low VIX + rising VIX): 84 OOS index trades on 31 days -> anecdotal. No sign of vol-clustering pay-off.
- **V5**: both are negative as expected, but the sign is reversed: stock strangles lose more than index strangles (IS -0.142 vs -0.066; OOS -0.086 vs -0.057). Index variance is not visibly "richer" to a buyer after spreads; stock spreads and theta dominate.
- **D1** (short-gamma GEX): no effect in either class (diff +0.03 / +0.01, p > .3). Dealer amplification does not show up in strangle P&L.
- **D2** (wall cross): nothing. Stocks +0.011 vs B1 (p=.45); the moving-wall variant is the same. Indices are negative OOS.
- **D3** (wall OI abandonment): IS picked 15%; stocks +0.05 vs B1, not significant; index n=22.
- **D4** (pin veto): stocks -0.17 R net, -0.045 vs the same-schedule B1, p=.12 -> FAIL as a veto (the direction agrees). Indices are anecdotal.
- **E1** (strangle through results, veto): -0.07 R, but no worse than B1 (-0.007) -> no IV-crush penalty beyond the usual bleed.
- **E2** (pre-results run-up): surprise. OOS net +0.042 R [CI +0.006, +0.082], +0.13 vs B1 (p=.001), known-time events only +0.087 (n=507). But IS is -0.059, and at 2x spread it is -0.027 -> FAIL (criteria 3, 6). Caveats: results dates with unknown time (most of 2025-26) are treated as after close, so a results release during market hours would already be inside the E2 exit price. The universe also has survivorship bias (today's F&O names).
- **E3** (post-results drift): nothing (-0.004 vs B1).
- Checks: the feature join is exact on (date, symbol), and features are dated at the entry close (no lookahead found). There are no duplicate std trades per (date, symbol, side). CA-break holds are excluded. Each pre-results row maps to the next reaction day (100% aligned). The capped (+5 gross) robustness line and the t+1-open line move no verdict (see JSON `sens`).
- p-values are raw. The within-family BH q is in the JSON (`p_bh_within_family`); the across-family BH is for the lead.
