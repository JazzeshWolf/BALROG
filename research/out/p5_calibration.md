# P5 buyConviction calibration (v1_full)
Score = engine/model.json applied to every OOS trade row (h=3, all delta x DTE cells). Inputs are bhavcopy-derived; **the modelled p75 half-spread stands in for the real bid/ask**. Net R CI = day-clustered bootstrap. Friction > 10% rows are not scoreable (OOS 559,685 of 1,480,308).

| sample | cls | bucket | n | days | win | net R [CI] | gross R | exp Rs/lot |
|---|---|---|---|---|---|---|---|---|
| OOS | index | <40 | 4,943 | 702 | 32% | -0.086 [-0.117, -0.052] | +0.003 | -1,261 |
| OOS | index | 40-50 | 4,026 | 696 | 33% | -0.083 [-0.111, -0.052] | -0.005 | -1,123 |
| OOS | index | 50-60 | 6,882 | 703 | 35% | -0.051 [-0.079, -0.022] | +0.018 | -878 |
| OOS | index | 60-70 | 7,443 | 703 | 35% | -0.072 [-0.096, -0.045] | -0.020 | -1,242 |
| OOS | index | 70-80 | 5,154 | 698 | 39% | -0.030 [-0.056, -0.002] | +0.010 | -861 |
| OOS | index | 80+ | 11,819 | 703 | 39% | -0.039 [-0.056, -0.021] | -0.010 | -771 |
| OOS | stocks | <40 | 515,178 | 703 | 32% | -0.092 [-0.105, -0.077] | -0.019 | -1,406 |
| OOS | stocks | 40-50 | 87,567 | 701 | 36% | -0.054 [-0.062, -0.043] | +0.010 | -1,394 |
| OOS | stocks | 50-60 | 40,537 | 694 | 37% | -0.045 [-0.056, -0.035] | +0.016 | -1,174 |
| OOS | stocks | 60-70 | 57,084 | 690 | 36% | -0.056 [-0.065, -0.047] | +0.002 | -1,347 |
| OOS | stocks | 70-80 | 52,780 | 664 | 37% | -0.050 [-0.060, -0.041] | +0.007 | -1,314 |
| OOS | stocks | 80+ | 127,210 | 584 | 39% | -0.035 [-0.044, -0.025] | +0.012 | -1,025 |
| OOS | NIFTY | <40 | 850 | 479 | 29% | -0.076 [-0.148, +0.002] | +0.004 | -341 |
| OOS | NIFTY | 40-50 | 939 | 507 | 29% | -0.088 [-0.159, -0.012] | -0.017 | -369 |
| OOS | NIFTY | 50-60 | 1,908 | 660 | 31% | -0.082 [-0.125, -0.032] | -0.024 | -493 |
| OOS | NIFTY | 60-70 | 3,264 | 701 | 34% | -0.061 [-0.099, -0.021] | -0.016 | -424 |
| OOS | NIFTY | 70-80 | 4,070 | 696 | 38% | -0.035 [-0.061, -0.009] | +0.003 | -844 |
| OOS | NIFTY | 80+ | 10,587 | 703 | 39% | -0.040 [-0.058, -0.022] | -0.012 | -781 |
| IS | index | <40 | 6,849 | 1340 | 32% | -0.098 [-0.122, -0.072] | -0.006 | -1,477 |
| IS | index | 40-50 | 4,686 | 1246 | 33% | -0.076 [-0.109, -0.037] | +0.006 | -1,207 |
| IS | index | 50-60 | 6,758 | 1272 | 31% | -0.077 [-0.107, -0.048] | -0.009 | -818 |
| IS | index | 60-70 | 6,112 | 1230 | 33% | -0.063 [-0.091, -0.030] | -0.014 | -492 |
| IS | index | 70-80 | 6,202 | 1215 | 37% | -0.053 [-0.073, -0.034] | -0.015 | -821 |
| IS | index | 80+ | 11,562 | 1233 | 38% | -0.035 [-0.051, -0.017] | -0.004 | -584 |
| IS | stocks | <40 | 596,547 | 1234 | 29% | -0.128 [-0.140, -0.117] | -0.056 | -2,091 |
| IS | stocks | 40-50 | 83,078 | 1175 | 34% | -0.076 [-0.082, -0.070] | -0.012 | -1,802 |
| IS | stocks | 50-60 | 37,241 | 1116 | 35% | -0.065 [-0.073, -0.057] | -0.002 | -1,667 |
| IS | stocks | 60-70 | 51,549 | 1095 | 35% | -0.062 [-0.070, -0.055] | -0.002 | -1,586 |
| IS | stocks | 70-80 | 49,487 | 1015 | 36% | -0.052 [-0.060, -0.044] | +0.007 | -1,393 |
| IS | stocks | 80+ | 117,723 | 819 | 39% | -0.033 [-0.040, -0.026] | +0.016 | -907 |
| OOS tier (post-hoc) | cls | n | days | win | net R [CI] |
|---|---|---|---|---|---|
| avoid | index | 4,943 | 702 | 32% | -0.086 [-0.117, -0.052] |
| neutral | index | 10,908 | 703 | 35% | -0.063 [-0.088, -0.035] |
| least-bad | index | 24,416 | 703 | 38% | -0.047 [-0.066, -0.027] |
| avoid | stocks | 515,178 | 703 | 32% | -0.092 [-0.105, -0.077] |
| neutral | stocks | 128,104 | 701 | 36% | -0.051 [-0.059, -0.041] |
| least-bad | stocks | 237,074 | 690 | 38% | -0.043 [-0.052, -0.034] |
| avoid | NIFTY | 850 | 479 | 29% | -0.076 [-0.148, +0.002] |
| neutral | NIFTY | 2,847 | 693 | 31% | -0.084 [-0.127, -0.035] |
| least-bad | NIFTY | 17,921 | 703 | 38% | -0.043 [-0.064, -0.021] |
- OOS Spearman(score, net R): index +0.058, stocks +0.166, NIFTY +0.087; 6-bucket monotone: {'index': False, 'stocks': False, 'NIFTY': False}
- OOS tiers monotone: {'index': True, 'stocks': True, 'NIFTY': False}
**Verdict (blunt).** Expectancy rises with score in trend (<40 -> 80+: index -0.086 -> -0.039 R, stocks -0.092 -> -0.035 R, NIFTY -0.076 -> -0.040) but NOT monotonically: the 60-70 bucket dips below 50-60 in index and stocks in all three variants (CIs overlap). The index score is a monotone function of friction alone in v3, so no simpler index model can remove the dip: monotonicity cannot be obtained. No bucket has positive net expectancy OOS: every bucket mean is negative and no CI lower bound is above 0.
Refits on IS only (ENGINE, registered=no): v1_full: OOS Spearman idx +0.058 / stk +0.166; v2_coarse: OOS Spearman idx +0.057 / stk +0.165; v3_minimal: OOS Spearman idx +0.054 / stk +0.155. Shipped: v1_full.
