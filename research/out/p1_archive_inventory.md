# P1 — xerxes-eod-archive inventory

Cache: `data/archive/{chain,snap,events}.parquet` · chain rows **990,079** · snap rows 11,366 · event rows 12,057

## 1. Days
- **54 day-folders**, 2026-08-11 → 2026-11-12; 64 NSE trading days in range (holidays: upstox api (live)).
- **Missing trading days: 13** — 2026-08-26, 2026-08-27, 2026-08-28, 2026-08-31, 2026-09-01, 2026-09-02, 2026-09-03, 2026-09-04, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-11. `state/missed-days.json` = [] (gap unrecorded; README attributes Aug-26→Sep-8 to the >100 MB DB push failure).
- **Folders on NSE holidays (whole-day re-publish of prior close): 2026-10-02, 2026-10-20, 2026-11-10**; whole-day duplicate days detected (>50% symbols with unchanged close): 2026-10-02, 2026-10-20, 2026-11-10. → **51 distinct trading sessions.**
- asOf IST (median, p10–p90): index 18:46 (16:25–22:28), stock 17:22 (16:15–20:43); range 16:00–22:28. All are post-close (not ~16:05: upstream builds run late); indices on 10-05/10-12 stamped 00:52/01:28 IST next day (still pre-open, accepted).
- Pre-close snapshots: 0. Frozen per-symbol files (asOf from an earlier day): BAJAJHLDNG ×12 (2026-10-28→2026-11-12, asOf 2026-10-27); DALBHARAT ×43 (2026-09-15→2026-11-12, asOf 2026-08-25); ICICIPRULI ×15 (2026-10-23→2026-11-12, asOf 2026-10-22); SAIL ×7 (2026-10-28→2026-11-05, asOf 2026-10-27).
- Other symbol-days with unchanged close vs prior day: 136. `snap.usable` (post-close, fresh, non-dup) = 10,595/11,366.

## 2. Instruments
- Indices/day: [3] (BANKNIFTY, NIFTY, SENSEX); stocks/day min 157 / median 208 / max 210 (first day 157, last 210).
- Distinct symbols: **213** (210 stocks + 3 indices).
- Entered after day 1: 2026-08-12: 51 (360ONE, ADANIPOWER, AMBER, BAJAJHLDNG, BANKINDIA, BDL…); 2026-10-28: 1 (NAVINFLUOR); 2026-10-29: 1 (COROMANDEL)
- Left before last day: none
- Symbols with internal gaps: 0 

## 3. Expiries
| class | expiries/snapshot (count of snapshots) | DTE rank1 min/med/max | rank2 | rank3+ |
|---|---|---|---|---|
| index | 2:1, 3:53, 4:22, 5:32, 6:54 | 0/4/27 | 6/12/62 | 13/27/90 |
| stock | 1:855, 2:10349 | 0/12/35 | 27/41/62 | — |

- Expiry weekdays (distinct expiries seen): BANKNIFTY {'Tue': 4, 'Mon': 2}; NIFTY {'Tue': 16, 'Mon': 4}; SENSEX {'Thu': 17}; stocks {'Tue': 4, 'Mon': 1} (2026-08-25, 2026-09-29, 2026-10-27, 2026-11-23, 2026-12-29).
- Off-weekday (holiday-shifted) expiries: BANKNIFTY 2026-11-23 (Mon), BANKNIFTY 2027-01-25 (Mon), NIFTY 2026-11-23 (Mon), NIFTY 2026-10-19 (Mon), NIFTY 2026-11-09 (Mon), NIFTY 2027-01-25 (Mon), stocks 2026-11-23 (Mon).
- BANKNIFTY expiries: 2026-08-25, 2026-09-29, 2026-10-27, 2026-11-23, 2026-12-29, 2027-01-25 → monthly-only (last Tue).
- Index lot sizes: BANKNIFTY 30 (2026-08-11→2026-11-12); NIFTY 65 (2026-08-11→2026-11-12); SENSEX 20 (2026-08-11→2026-11-12).
- Stocks whose lotSize changed in-window: **9** (change dates: {'2026-10-28': 9}).

## 4. Chain completeness (all archived days)
| class | rank | strikes/expiry med | rows | ltp>0 | iv | bid&ask>0 (all days) | bid&ask>0 (bid-days) | bid key absent (bid-days) | vol>0 | oi>0 | delta | strike lo/hi % spot (med) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| index | 1 | 146 | 37,935 | 100.0 | 78.3 | 73.0 | 90.7 | 8.8 | 90.1 | 99.7 | 100.0 | 87/114 |
| index | 2 | 107 | 29,033 | 100.0 | 86.4 | 78.6 | 95.6 | 0.6 | 84.2 | 98.6 | 100.0 | 87/112 |
| index | 3+ | 81 | 51,380 | 100.0 | 87.9 | 80.6 | 94.3 | 1.9 | 78.8 | 98.5 | 100.0 | 90/111 |
| stock | 1 | 27 | 576,485 | 100.0 | 81.6 | 74.2 | 93.7 | 5.6 | 77.3 | 99.7 | 100.0 | 82/122 |
| stock | 2 | 18 | 295,246 | 100.0 | 90.2 | 72.5 | 96.9 | 2.2 | 68.9 | 96.6 | 100.0 | 85/117 |

- **`bid`/`ask` keys do not exist at all on 11 days (2026-08-11→2026-08-25)** → two-sided quotes only from 2026-09-15: **40 distinct sessions with a book**. On bid-days the keys are absent on 4.4% of rows (median abs(delta) of those 0.001: deep ITM/OTM wings). Otherwise chain keys exactly as briefed (incl. prevOi). iv null rows: 15.5% of rows, of which 95.7% have |delta|≥0.99 (delta is then a placeholder ±1/0, not a model value).
- **Truncated around ATM, not the full exchange chain**: median strike span is the lo/hi % of spot in the table (≈ 87–114% index, 82–122% stock nearest expiry); farther wings are not archived.

## 5. Spread reality (two-sided rows; rel = (ask−bid)/mid, med / p75 / p90, n)
| class | abs(delta) | nearest | 2nd | 3rd+ |
|---|---|---|---|---|
| index | 0.05-0.15 | 0.046 / 0.084 / 0.152 (1,954) | 0.059 / 0.191 / 0.734 (2,776) | 0.095 / 0.297 / 0.757 (6,016) |
| index | 0.15-0.25 | 0.019 / 0.030 / 0.052 (972) | 0.021 / 0.063 / 0.231 (1,725) | 0.041 / 0.099 / 0.365 (3,983) |
| index | 0.25-0.40 | 0.014 / 0.022 / 0.034 (1,029) | 0.017 / 0.051 / 0.181 (1,973) | 0.022 / 0.072 / 0.339 (5,029) |
| index | 0.40-0.60 | 0.012 / 0.019 / 0.029 (1,224) | 0.019 / 0.037 / 0.083 (2,353) | 0.023 / 0.056 / 0.234 (6,349) |
| index | >0.60 | 0.020 / 0.061 / 0.181 (11,447) | 0.040 / 0.160 / 0.317 (8,882) | 0.054 / 0.206 / 0.376 (14,214) |
| stock | 0.05-0.15 | 0.062 / 0.118 / 0.322 (59,750) | 0.213 / 0.911 / 1.433 (36,507) | — |
| stock | 0.15-0.25 | 0.040 / 0.063 / 0.125 (29,317) | 0.106 / 0.549 / 1.035 (27,977) | — |
| stock | 0.25-0.40 | 0.032 / 0.053 / 0.113 (31,025) | 0.091 / 0.390 / 0.670 (33,534) | — |
| stock | 0.40-0.60 | 0.028 / 0.054 / 0.180 (37,930) | 0.084 / 0.255 / 0.423 (38,513) | — |
| stock | >0.60 | 0.075 / 0.136 / 0.261 (179,098) | 0.107 / 0.182 / 0.326 (68,937) | — |

| class | <₹5 | 5–20 | 20–50 | 50–200 | >200 |
|---|---|---|---|---|---|
| index | 0.196/0.857 | 0.153/0.843 | 0.084/0.548 | 0.042/0.425 | 0.030/0.297 |
| stock | 0.145/1.200 | 0.053/0.766 | 0.063/0.375 | 0.082/0.314 | 0.086/0.296 |
(cells: median/p90 of rel spread by mid-premium bucket)

- Near-ATM (|Δ| 0.40–0.60, nearest expiry) median rel spread: BANKNIFTY 1.51%, NIFTY 0.66%, SENSEX 1.17%; stocks: per-symbol medians q25/q50/q75 = 2.09%, 2.63%, 3.56%.
- Stale-print indicator — ltp outside [bid, ask]: index 33.9%, stock 34.9% of two-sided rows; ltp>0 with volume=0 (never traded that session): index 14.5%, stock 24.6%.

## 6. Path continuity (swing-test sample)
Entries: |Δ| 0.20–0.60, nearest two expiries, two-sided quote on t (usable snapshot). t+k = k-th NSE trading day; contracts expiring on/before t+k excluded; t+k not archived = unobservable.
| horizon | index eligible | index still two-sided | index unobservable | stock eligible | stock still two-sided | stock unobservable |
|---|---|---|---|---|---|---|
| t+1 | 6,685 | 98.1% | 0 | 147,701 | 98.9% | 557 |
| t+2 | 6,105 | 97.6% | 0 | 141,189 | 98.7% | 532 |
| t+3 | 5,525 | 97.9% | 0 | 134,539 | 98.6% | 534 |
| t+5 | 4,352 | 97.0% | 0 | 120,905 | 98.5% | 491 |

- **Entry opportunities (symbol, contract, entry-day): index 7,161, stock 154,318** (distinct contracts: index 1,868, stock 22,051).
- Distinct nearest-expiry cycles observed: BANKNIFTY 4, NIFTY 13, SENSEX 12, stocks(monthly) 4. These overlapping rows are highly correlated — the independent sample is closer to the cycle count × underlyings.

## 7. Auxiliary fields
- `spot.history`: index median 300 pts (min 289, max 300) daily **close-only** (keys ['t,v']) from 2025-06-16; stocks 0 (empty everywhere). `vix.history` on index files similar.
- `ivHistory` median length first→last day: index 18→92, stock 4→72.
- Future coverage % (price/oi/basis/expiry): index 100.0/100.0/100.0/100.0; stock 100.0/100.0/100.0/100.0. Index files have no term/gap/events/outlook; stock `outlook` absent on 55.5% of stock snaps.
- Structure labels (usable snaps, %): index: Short covering 32.0, Short buildup 20.3, Long unwinding 18.3, Indecisive 16.3, Long buildup 13.1; stock: Short buildup 23.1, Long unwinding 22.4, Short covering 22.2, Long buildup 22.1, Indecisive 10.2. Stock term regime %: {'contango': 35.1, 'flat': 33.2, 'backwardation': 31.7}.
- Event kinds (deduped symbol/kind/date/title): Event priced in 3070, Results 880, Dividend 450, Earnings call 333, AGM 84, Buyback 75, Fund raise 57, Stock split 55, Board meeting 19, Bonus issue 16, Fund Raising 13, EGM 11, Rights issue 11, in_cpi 5.
- **Results/earnings inside window: 287 symbol-events across 207 stocks** (NSE-sourced 243, of which non-approx 243; remainder news-derived). Market calendar events: 17 (fomc, in_cpi, rbi, us_cpi).

## 8. xerxes.db / state
- `xerxes.db`: **not present** (gitignored build product, published as a GitHub release asset; not needed — gz files are the source of truth).
- `state/outcomes.jsonl`: 16,419 rows — frozen settlements of upstream *seller* candidates (entry ltp/spot/dte, conviction factors, peak_ltp, MAE, settle_spot, intrinsic/P&L), 21 expiries ['2026-08-13', '2026-11-09'], 208 symbols. Candidate-only, so a biased subset; not a buyer P&L source.
- `state/missed-days.json`: [] (empty despite 13 missing days).

## 9. Regime coverage
- Archived sessions (n=51): NIFTY 22232–25299 (median 23146); India VIX 10.35–17.56 (median 14.03); NIFTY first→last +3.38%; |daily move|>1% on 13 days.
- Full window incl. missing days (NIFTY spot.history on NSE trading days, n=64; 12 weekend/holiday history points dropped): 22232–25299, VIX 10.35–17.56, return +3.38%, max drawdown -9.15%, |move|>1% on 13 days.
- Read: one ~3-month window, a single vol regime family; treat any edge found as regime-conditional.
