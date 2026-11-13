# P3 - 1-minute underlying history QA
Run 2026-11-13 by `research/scripts/p3_intraday_qa.py`. Calendar = NIFTY regular days 2022-01-03..2026-11-12 (1203 days). Full per-symbol table: `research/out/p3_intraday_qa_symbols.csv`.
Fetch: 2027 Upstox requests in 5460.9 s over 2 run(s), all tokenless; parquet 15,337,715 rows, 322 MB on disk.

## Indices / VIX / futures
| sym | first..last | days | cov% full | bars/day med/p5/min | miss-min% | <370 | dup/oos rm | flat30 days |
|---|---|---|---|---|---|---|---|---|
| NIFTY | 2022-01-03..2026-11-12 | 1203 | 100.0 | 375/375/60 | 0.259 | 4 | 0/404 | 0 |
| BANKNIFTY | 2022-01-03..2026-11-12 | 1203 | 100.0 | 375/375/60 | 0.269 | 5 | 0/397 | 0 |
| SENSEX | 2022-01-03..2026-11-12 | 1204 | 100.0 | 375/375/4 | 0.339 | 5 | 0/279 | 0 |
| INDIAVIX | 2022-01-03..2026-11-12 | 1203 | 100.0 | 375/375/60 | 0.258 | 4 | 0/351 | 0 |
| NIFTY_FUT_NEAR | 2026-08-26..2026-11-12 | 54 | 4.49 | 375/375/70 | 1.516 | 1 | 0/540 | 2 |
| BANKNIFTY_FUT_NEAR | 2026-08-26..2026-11-12 | 54 | 4.49 | 375/374/70 | 1.531 | 1 | 0/540 | 19 |

## Stocks (30)
Coverage of full calendar: median 100.0%, min 100.0% (RELIANCE); within own listed span: min 100.0%. Bars/day median 375; missing-minute share median 0.263%, max 0.289% (KALYANKJIL); days<370 total 166.
Short history / gaps (cov_full < 98%): none
Stale feed (>= 30 flat bars in a row): BSE 115d, PAYTM 34d, MCX 269d, ADANIENT 1d, ETERNAL 2d, IDEA 2d, NIFTY_FUT_NEAR 2d, BANKNIFTY_FUT_NEAR 19d

## Overnight |ret| > 20% in stocks (prev close -> open; NIFTY same gap)
- PAYTM 2024-02-02: -20.0% (609.0 -> 487.2), NIFTY +0.56%

## Adjusted vs raw
Prices are **back-adjusted** for splits/bonuses (no overnight jump on any ex-date). Known events: TATASTEEL 1:10 split ex 2022-07-28: close 92.5 -> 96.6; BAJFINANCE 1:2 split + 4:1 bonus ex 2025-06-16: close 934.0 -> 938.5; HDFCBANK 1:1 bonus ex 2025-08-26: close 984.6 -> 972.3. Raw pre-event prices were ~10x / ~10x / ~2x those levels.
Flat (high==low) runs in BSE/MCX are single-print minutes with volume > 0 concentrated in 2022-23 (thin liquidity before their re-rating), not a stale feed; the near-month futures runs are early in each contract's life (illiquid far-month period).

## Special sessions (`data/ref/special_sessions.csv`)
- 2022-08-09 Tue 14:21-14:24 short_day: 4 in-session / 0 out bars; in-session bars only
- 2022-10-24 Mon 18:15-19:14 muhurat: 0 in-session / 60 out bars; no (dropped)
- 2023-11-12 Sun 18:15-19:14 muhurat: 0 in-session / 60 out bars; no (dropped)
- 2024-01-20 Sat 09:15-15:29 weekend_session: 375 in-session / 0 out bars; in-session bars only
- 2024-03-02 Sat 09:15-12:29 weekend_session: 105 in-session / 0 out bars; in-session bars only
- 2024-05-18 Sat 09:15-12:29 weekend_session: 105 in-session / 0 out bars; in-session bars only
- 2024-11-01 Fri 18:00-18:59 muhurat: 0 in-session / 60 out bars; no (dropped)
- 2025-02-01 Sat 09:15-15:29 weekend_session: 375 in-session / 0 out bars; in-session bars only
- 2025-10-21 Tue 13:45-14:44 muhurat: 60 in-session / 0 out bars; in-session bars only
- 2026-02-01 Sun 09:15-15:29 weekend_session: 375 in-session / 0 out bars; in-session bars only
- 2026-11-08 Sun 14:20-15:29 muhurat: 70 in-session / 0 out bars; in-session bars only

## Daily close cross-check (last 1-min close vs Upstox daily candle, 20 random days)
Official index close = weighted avg of the last 30 min, so a few bp vs the 15:29 bar close is expected; missing days below are evening Muhurat sessions (out-of-session, dropped).
- NIFTY: |diff| median 2.85 bp, max 20.93 bp, 4/20 within 1 bp; daily days missing in 1-min: 3 2022-10-24, 2023-11-12, 2024-11-01
- BANKNIFTY: |diff| median 6.02 bp, max 22.59 bp, 2/20 within 1 bp; daily days missing in 1-min: 3 2022-10-24, 2023-11-12, 2024-11-01
- SENSEX: |diff| median 4.43 bp, max 13.61 bp, 4/20 within 1 bp; daily days missing in 1-min: 3 2022-10-24, 2023-11-12, 2024-11-01
