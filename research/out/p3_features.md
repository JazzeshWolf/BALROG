# P3 - point-in-time daily feature table
Generated 2026-11-13 23:12 by `research/scripts/p3_build_features.py`; columns in `data/README.md`. Inputs: F&O build report generated 2026-11-13 23:03 (complete build).

**Output** `data/features/daily.parquet`: 365,790 rows x 102 cols, 129.4 MB; 2019-01-01 -> 2026-11-12; symbols index 11, stock 355. SENSEX from 2022-06-15 (862 d).

**Coverage** (non-null share; `fut_structure` = not 'none'-able, shown as non-null):
| cls | year | rows | ret20 | har_vol_h5 | adx14 | atm_iv | atm_iv_next | iv_pctile_252 | vrp_ratio_h5 | net_gex | net_gex_norm | gamma_flip | call_wall | fut_structure | india_vix | fii_idx_call_net_ratio | days_to_results |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| index | 2019 | 732 | 0.92 | 0.58 | 0.89 | 0.69 | 0.65 | 0.33 | 0.39 | 1.00 | 0.96 | 0.70 | 0.76 | 1.00 | 1.00 | 1.00 | 0.00 |
| index | 2020 | 619 | 1.00 | 1.00 | 1.00 | 0.80 | 0.73 | 0.80 | 0.80 | 1.00 | 1.00 | 0.80 | 0.81 | 1.00 | 1.00 | 1.00 | 0.00 |
| index | 2021 | 735 | 1.00 | 1.00 | 1.00 | 0.78 | 0.69 | 0.67 | 0.78 | 1.00 | 0.99 | 0.96 | 0.97 | 1.00 | 1.00 | 1.00 | 0.00 |
| index | 2022 | 978 | 1.00 | 0.99 | 0.98 | 0.65 | 0.55 | 0.52 | 0.65 | 1.00 | 0.99 | 0.76 | 0.77 | 1.00 | 1.00 | 1.00 | 0.00 |
| index | 2023 | 1,222 | 0.94 | 0.93 | 0.92 | 0.82 | 0.69 | 0.61 | 0.81 | 0.98 | 0.96 | 0.88 | 0.91 | 0.98 | 1.00 | 1.00 | 0.00 |
| index | 2024 | 1,656 | 0.99 | 0.98 | 0.99 | 0.95 | 0.71 | 0.81 | 0.94 | 1.00 | 0.99 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |
| index | 2025 | 1,778 | 0.99 | 0.91 | 0.98 | 0.95 | 0.62 | 0.95 | 0.88 | 1.00 | 0.98 | 0.96 | 0.98 | 0.98 | 1.00 | 1.00 | 0.00 |
| index | 2026 | 1,755 | 0.97 | 0.86 | 0.94 | 0.91 | 0.48 | 0.82 | 0.82 | 1.00 | 0.95 | 0.92 | 0.95 | 0.99 | 1.00 | 1.00 | 0.00 |
| stock | 2019 | 42,876 | 0.90 | 0.89 | 0.87 | 0.97 | 0.34 | 0.41 | 0.86 | 1.00 | 0.96 | 0.90 | 0.99 | 1.00 | 1.00 | 1.00 | 0.17 |
| stock | 2020 | 35,044 | 0.99 | 0.99 | 0.99 | 0.97 | 0.39 | 0.94 | 0.96 | 1.00 | 1.00 | 0.85 | 0.99 | 1.00 | 1.00 | 1.00 | 0.20 |
| stock | 2021 | 40,375 | 1.00 | 1.00 | 1.00 | 0.99 | 0.57 | 0.88 | 0.99 | 1.00 | 0.99 | 0.89 | 1.00 | 1.00 | 1.00 | 1.00 | 0.18 |
| stock | 2022 | 48,650 | 1.00 | 1.00 | 1.00 | 0.99 | 0.59 | 0.94 | 0.99 | 1.00 | 1.00 | 0.95 | 1.00 | 1.00 | 1.00 | 1.00 | 0.17 |
| stock | 2023 | 45,768 | 1.00 | 1.00 | 1.00 | 1.00 | 0.62 | 0.99 | 1.00 | 1.00 | 1.00 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 0.18 |
| stock | 2024 | 45,497 | 1.00 | 1.00 | 1.00 | 1.00 | 0.68 | 0.97 | 1.00 | 1.00 | 0.99 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 0.19 |
| stock | 2025 | 53,607 | 1.00 | 1.00 | 1.00 | 1.00 | 0.73 | 0.87 | 0.99 | 1.00 | 0.99 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 0.22 |
| stock | 2026 | 44,498 | 1.00 | 1.00 | 1.00 | 1.00 | 0.75 | 0.95 | 1.00 | 1.00 | 1.00 | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 | 0.28 |

**NIFTY median atm_iv vs India VIX vs HAR h5 (vol pts)**: 2019: IV 13.7 / VIX 15.9 / HAR5 13.0; 2020: IV 20.3 / VIX 22.4 / HAR5 15.4; 2021: IV 15.5 / VIX 17.3 / HAR5 13.5; 2022: IV 17.5 / VIX 19.0 / HAR5 13.6; 2023: IV 10.1 / VIX 12.0 / HAR5 9.4; 2024: IV 12.8 / VIX 14.1 / HAR5 11.8; 2025: IV 11.4 / VIX 12.6 / HAR5 10.2; 2026: IV 13.0 / VIX 13.8 / HAR5 11.5

**Futures structure label share** (days with z available): index: long_buildup 0.07, long_unwinding 0.04, none 0.75, short_buildup 0.09, short_covering 0.06; stock: long_buildup 0.06, long_unwinding 0.05, none 0.74, short_buildup 0.08, short_covering 0.07

**Events by year** (wall breaks all classes; results reaction days, stocks): 2019: call 1504 (2411 vs prev wall), put 2929 (2996), results 468 (56 t-unk); 2020: call 1521 (2976 vs prev wall), put 2142 (2344), results 437 (36 t-unk); 2021: call 1511 (2984 vs prev wall), put 2166 (2110), results 462 (13 t-unk); 2022: call 1833 (3176 vs prev wall), put 3068 (3070), results 516 (41 t-unk); 2023: call 1992 (3267 vs prev wall), put 2462 (2255), results 526 (14 t-unk); 2024: call 1883 (3093 vs prev wall), put 3363 (2887), results 533 (12 t-unk); 2025: call 2185 (3280 vs prev wall), put 4636 (3718), results 729 (561 t-unk); 2026: call 2322 (2942 vs prev wall), put 4078 (3315), results 789 (789 t-unk)
Results-day |move|/sigma_daily: median 0.99, share > 2: 0.22 (n=4413).

**HAR out-of-sample R^2** (target: log mean Parkinson var over t+1..t+5; every forecast uses a model fitted only on targets observed before its month):
| cls | n | log-HAR (R2 lvl / corr2 log) | naive 22d Parkinson | naive rv20^2/252 (close-close) |
|---|---|---|---|---|
| index | 8,414 | 0.168 / 0.444 | 0.085 / 0.413 | -0.765 / 0.334 |
| stock | 349,951 | 0.145 / 0.452 | -0.001 / 0.420 | -17.997 / 0.280 |

Median har_vol_h5 / rv20 (index): 0.92 - Parkinson ignores overnight gaps, so HAR (and therefore vrp_ratio) is biased LOW vs close-to-close vol and vrp_ratio biased HIGH; read V1 thresholds with that in mind. Level R^2 is dominated by outliers (rv20 by single-day jumps), so corr^2 in logs is the fairer comparison.

**Approximations**
- HAR is log-HAR (pooled per class; exp(xb+s2/2) back-transform), refit monthly on an expanding window; first months of 2019 have no forecast (needs 400 training rows).
- OHLC: where the underlying is close-only (SENSEX/BANKEX, BSE UDiFF) open/high/low come from the near future scaled by spot/futures close (`ohlc_src`='fut'); rows with neither have no Parkinson/ADX.
- GEX/flip/walls use the nearest two (walls: nearest) expiries with dte_cal >= 1 (the contract expiring today is skipped). Untraded strikes (no IV) use that expiry's ATM IV for gamma; strikes beyond +-30% ignored. Gamma flip searched on a +-15% spot grid (0.25% step), IVs held fixed; NaN if no sign change.
- net_gex_norm = net_gex / 20-day average futures turnover (all expiries, contracts x lot x price).
- Futures dOI = change of combined OI across expiries (rollover-neutral); z = value / std of previous 60 days.
- days_to_results uses the realised announcement date (board meetings are pre-announced, usually >= 2 working days, often 1-3 weeks); capped at 15. Events deduped to one per 20 calendar days per symbol.
- Participant OI ratios lagged one session (file published after the close).
