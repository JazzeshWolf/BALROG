# P4 - Momentum / structure hypotheses M1, M1b, M2, M3, M4, M5, M8 (swing)
Generated 2026-11-14T12:07 by `research/scripts/p4_run_momentum.py`. Standard contract, close fills + p75 half-spread, statutory costs, time exit; excluded: ca_break / past-end / settle / no-mark exits; 1 position per symbol x hypothesis x side. diff vs B1 = side- and group-matched, day-clustered bootstrap (2,000). Grid cells frozen on IS (written to JSON before OOS was loaded). c2 = BH within this family (provisional). Trials logged this run: 1886.

Frozen: M1 index: z0.5 (h=3); M1 stocks: z0.5 (h=3); M1b index: z0.5 (h=3); M1b stocks: z0.5 (h=3); M2 index: adx20-45 (h=3); M2 stocks: adx20-45 (h=3); M3 index: lb20_atr1.5 (h=3); M3 stocks: lb40_atr1.5 (h=3); M4 index: gap1.0 (h=2); M4 stocks: gap1.0 (h=2); M5 stocks: q0.10 (h=5)

| hyp | class | IS net (gross) | OOS net (gross) [CI] | OOS vs B1 [CI] p | C-P* | n / days OOS | c1 c2 c3 c4 c5 c6 | verdict |
|---|---|---|---|---|---|---|---|---|
| M1 | NIFTY | +0.174 (+0.213) | -0.053 (-0.027) [-0.226, +0.149] | -0.033 [-0.222, +0.167] 0.712 | +0.076 | 83 / 83 | ✗ ✗ ✗ ✗ ✗ ✗ | (ANECDOTAL) |
| M1 | BANKNIFTY | -0.021 (+0.074) | -0.069 (-0.020) [-0.212, +0.083] | -0.018 [-0.161, +0.129] 0.797 | +0.048 | 94 / 94 | ✗ ✗ ✓ ✗ ✗ ✗ | (ANECDOTAL) |
| M1 | SENSEX | +0.104 (+0.245) | -0.100 (-0.010) [-0.301, +0.130] | -0.033 [-0.243, +0.195] 0.746 | +0.132 | 63 / 63 | ✗ ✗ ✗ ✗ ✗ ✗ | (ANECDOTAL) |
| M1 | **index** | +0.084 (+0.161) | -0.079 (-0.021) [-0.183, +0.028] | -0.024 [-0.125, +0.081] 0.634 | +0.058 | 370 / 258 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |
| M1 | **stocks** | -0.096 (-0.028) | -0.052 (+0.015) [-0.082, -0.019] | +0.019 [-0.009, +0.048] 0.190 | +0.067 | 11,115 / 543 | ✗ ✗ ✓ ✗ ✓ ✗ | FAIL |
| M1b | NIFTY | -0.003 (+0.030) | +0.036 (+0.065) [-0.241, +0.379] | +0.078 [-0.201, +0.425] 0.665 | +0.259 | 47 / 47 | ✗ ✗ ✗ ✓ ✗ ✓ | (ANECDOTAL) |
| M1b | BANKNIFTY | -0.181 (-0.099) | -0.062 (-0.014) [-0.211, +0.106] | -0.011 [-0.165, +0.148] 0.899 | +0.005 | 66 / 66 | ✗ ✗ ✗ ✗ ✗ ✗ | (ANECDOTAL) |
| M1b | SENSEX | +0.173 (+0.293) | +0.061 (+0.171) [-0.284, +0.451] | +0.191 [-0.156, +0.587] 0.325 | +0.143 | 41 / 41 | ✗ ✗ ✗ ✓ ✗ ✗ | (ANECDOTAL) |
| M1b | **index** | -0.072 (-0.004) | -0.051 (+0.006) [-0.168, +0.071] | +0.021 [-0.095, +0.147] 0.710 | +0.050 | 226 / 166 | ✗ ✗ ✓ ✗ ✓ ✗ | FAIL |
| M1b | **stocks** | -0.082 (-0.017) | -0.071 (-0.011) [-0.106, -0.033] | +0.004 [-0.031, +0.042] 0.790 | -0.024 | 9,404 / 543 | ✗ ✗ ✓ ✗ ✓ ✗ | FAIL |
| M2 | NIFTY | -0.051 (-0.021) | +0.021 (+0.049) [-0.155, +0.222] | +0.043 [-0.136, +0.234] 0.650 | -0.012 | 135 / 135 | ✗ ✗ ✗ ✗ ✓ ✓ | (FAIL) |
| M2 | BANKNIFTY | -0.057 (+0.033) | -0.023 (+0.022) [-0.161, +0.119] | +0.028 [-0.098, +0.170] 0.701 | -0.003 | 142 / 142 | ✗ ✗ ✗ ✗ ✓ ✗ | (FAIL) |
| M2 | SENSEX | -0.129 (-0.021) | -0.051 (+0.047) [-0.252, +0.173] | +0.033 [-0.160, +0.241] 0.782 | +0.038 | 95 / 95 | ✗ ✗ ✗ ✗ ✗ ✗ | (ANECDOTAL) |
| M2 | **index** | -0.084 (-0.020) | -0.014 (+0.048) [-0.116, +0.093] | +0.043 [-0.052, +0.150] 0.403 | +0.019 | 584 / 348 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |
| M2 | **stocks** | -0.099 (-0.032) | -0.085 (-0.022) [-0.111, -0.058] | -0.013 [-0.038, +0.013] 0.307 | -0.031 | 23,579 / 544 | ✗ ✗ ✓ ✗ ✓ ✗ | FAIL |
| M3 | NIFTY | +0.008 (+0.036) | -0.229 (-0.209) [-0.495, +0.085] | -0.206 [-0.493, +0.115] 0.179 | +0.137 | 17 / 17 | ✗ ✗ ✗ ✗ ✗ ✗ | (ANECDOTAL) |
| M3 | BANKNIFTY | +0.336 (+0.475) | +0.139 (+0.202) [-0.296, +0.676] | +0.190 [-0.236, +0.776] 0.505 | +0.400 | 18 / 18 | ✗ ✗ ✗ ✗ ✗ ✓ | (ANECDOTAL) |
| M3 | SENSEX | +0.046 (+0.170) | -0.185 (-0.115) [-0.534, +0.141] | -0.108 [-0.452, +0.256] 0.546 | +0.197 | 12 / 12 | ✗ ✗ ✗ ✗ ✗ ✗ | (ANECDOTAL) |
| M3 | **index** | +0.094 (+0.181) | -0.100 (-0.045) [-0.316, +0.154] | -0.045 [-0.282, +0.206] 0.688 | +0.181 | 80 / 48 | ✗ ✗ ✗ ✗ ✗ ✗ | ANECDOTAL |
| M3 | **stocks** | -0.086 (-0.022) | -0.128 (-0.075) [-0.190, -0.065] | -0.053 [-0.113, +0.012] 0.103 | -0.075 | 2,451 / 496 | ✗ ✗ ✓ ✗ ✓ ✗ | FAIL |
| M4 | NIFTY | +0.121 (+0.154) | +0.126 (+0.157) [-0.129, +0.402] | +0.162 [-0.090, +0.446] 0.237 | +0.380 | 33 / 33 | ✗ ✗ ✓ ✓ ✗ ✓ | (ANECDOTAL) |
| M4 | BANKNIFTY | -0.119 (-0.042) | +0.128 (+0.184) [-0.140, +0.472] | +0.184 [-0.099, +0.504] 0.242 | +0.232 | 32 / 32 | ✗ ✗ ✗ ✓ ✗ ✓ | (ANECDOTAL) |
| M4 | SENSEX | +0.373 (+0.526) | +0.170 (+0.275) [-0.149, +0.530] | +0.284 [-0.057, +0.664] 0.107 | +0.623 | 18 / 18 | ✗ ✗ ✗ ✗ ✗ ✓ | (ANECDOTAL) |
| M4 | **index** | +0.058 (+0.123) | +0.135 (+0.202) [-0.054, +0.332] | +0.201 [+0.016, +0.411] 0.033 | +0.386 | 141 / 75 | ✗ ✗ ✓ ✓ ✓ ✓ | FAIL |
| M4 | **stocks** | -0.035 (+0.027) | +0.008 (+0.067) [-0.078, +0.087] | +0.076 [-0.010, +0.156] 0.097 | +0.130 | 1,716 / 395 | ✗ ✗ ✗ ✗ ✓ ✗ | FAIL |
| M5 | **stocks** | -0.135 (-0.064) | -0.107 (-0.040) [-0.157, -0.053] | -0.017 [-0.071, +0.037] 0.485 | +0.001 | 3,724 / 534 | ✗ ✗ ✓ ✗ ✓ ✗ | FAIL |

*C-P = signal side minus opposite side on the same signal days (OOS). Sub-class verdicts in brackets are descriptive (raw p). c4 plateau and c6 2x spread evaluated OOS.

## M2 side groups and expiry-spanning holds (net mean R, h=3; IS | OOS; n OOS)
| class | ADX<25 | band 25-45 | ADX>45 | frozen band, hold spans expiry | frozen band, no expiry in hold |
|---|---|---|---|---|---|
| NIFTY | +0.104 \| -0.082 (187) | -0.009 \| -0.040 (77) | -0.124 \| -0.737 (1) | -0.110 \| +0.110 (88) | +0.060 \| -0.144 (47) |
| BANKNIFTY | -0.026 \| -0.058 (200) | -0.068 \| -0.167 (71) | -0.185 \| n/a (0) | -0.132 \| -0.169 (29) | +0.088 \| +0.014 (113) |
| SENSEX | -0.092 \| -0.089 (121) | -0.299 \| -0.042 (51) | -0.194 \| n/a (0) | -0.183 \| -0.106 (59) | -0.002 \| +0.039 (36) |
| index | +0.030 \| -0.087 (865) | -0.090 \| -0.089 (318) | -0.065 \| -0.198 (25) | -0.135 \| -0.036 (227) | +0.013 \| +0.000 (357) |
| stocks | -0.084 \| -0.070 (25966) | -0.104 \| -0.089 (14705) | -0.095 \| -0.105 (1815) | n/a \| n/a (0) | -0.099 \| -0.085 (23579) |

## M8 - pooled M-signal entries: expiry-day vs other days (net mean R; OOS)
| group | expiry day n / days | expiry-day net | other days net | diff [CI] p | IS diff | verdict |
|---|---|---|---|---|---|---|
| index | 179 / 155 | -0.163 | -0.015 | -0.148 [-0.266, -0.019] 0.026 | +0.188 | FAIL |
| stocks | 0 / 0 | n/a | -0.077 | n/a [n/a, n/a] nan | n/a | ANECDOTAL |

## Reading (blunt)
- **M1** (expected +): index OOS -0.079 R (IS +0.084), vs B1 -0.024 p=0.63, C-P +0.058 -> FAIL; stocks OOS -0.052 R (IS -0.096), vs B1 +0.019 p=0.19, C-P +0.067 -> FAIL. Index IS looked good (+0.08, NIFTY +0.17) and vanished OOS - classic IS mirage. Stocks: calls do beat puts on the signal day (directional content exists) but not by enough to clear theta + spread.
- **M1b** (expected <= M1): index OOS -0.051 R (IS -0.072), vs B1 +0.021 p=0.71, C-P +0.050 -> FAIL; stocks OOS -0.071 R (IS -0.082), vs B1 +0.004 p=0.79, C-P -0.024 -> FAIL. Not worse than M1 as registered: both are indistinguishable from B1. 'Exhaustion' label carries no information.
- **M2** (expected + inside band): index OOS -0.014 R (IS -0.084), vs B1 +0.043 p=0.40, C-P +0.019 -> FAIL; stocks OOS -0.085 R (IS -0.099), vs B1 -0.013 p=0.31, C-P -0.031 -> FAIL. Trend-band entries are just B1 with a side choice; band 25-45 is no better than ADX<25 and the 'late' ADX>45 group is too thin on indices to say anything. Index holds spanning an expiry are worse than those that do not (IS -0.135 vs +0.013, OOS -0.036 vs +0.000), but per-index signs disagree. Stocks never span an expiry with the monthly std contract.
- **M3** (expected +): index OOS -0.100 R (IS +0.094), vs B1 -0.045 p=0.69, C-P +0.181 -> ANECDOTAL; stocks OOS -0.128 R (IS -0.086), vs B1 -0.053 p=0.10, C-P -0.075 -> FAIL. Index breakouts are anecdotal (80 OOS trades); on stocks breakouts are, if anything, worse than B1 (-0.05, p=0.10) and C-P is negative: no follow-through for a buyer - the registered '+' is contradicted.
- **M4** (expected +): index OOS +0.135 R (IS +0.058), vs B1 +0.201 p=0.03, C-P +0.386 -> FAIL; stocks OOS +0.008 R (IS -0.035), vs B1 +0.076 p=0.10, C-P +0.130 -> FAIL. The only live one: index gap-and-hold OOS +0.135 R, +0.20 vs B1, C-P +0.39 (p=0.013), 2x spread still positive, plateau holds, IS same sign - but CI includes 0 and q(BH) is far above 0.05 with n=141/75 days. Candidate for a fresh OOS / engine bonus, not an edge. Stocks: gross +0.07 eaten by costs.
- **M5** (expected +): stocks OOS -0.107 R (IS -0.135), vs B1 -0.017 p=0.48, C-P +0.001 -> FAIL. Relative-strength stocks bleed like B1 (-0.11 R); no persistence an option buyer can monetise in 5 days.
- **M8** (expected - on expiry day): index expiry-day entries minus other days OOS -0.148 (raw p=0.026) but IS +0.188 - sign flips, so FAIL. Stocks: zero expiry-day entries by construction (std stock contract = nearest monthly with >= 7 DTE, none exists on the monthly expiry day) - untestable, not anecdotal by sample.
