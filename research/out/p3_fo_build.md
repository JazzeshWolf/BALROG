# P3 - point-in-time daily F&O dataset (bhavcopy build)
Generated 2026-11-13 23:03 by `research/scripts/p3_build_fo.py`; schemas in `data/README.md`. FULL build: all downloads complete (2019-01-01..2026-11-12).

**Staged days**: F&O bse 2022-06-15..2026-11-12 (864 d), nse 2019-01-01..2026-11-12 (1940 d); other kinds: cm 1938 d, index 1941 d, participant_oi 1941 d.

| year | exch | class | days | underlyings | option rows | traded share | IV solve rate (traded, dte>0) |
|---|---|---|---|---|---|---|---|
| 2019 | nse | index | 244 | 3 | 1,163,000 | 0.12 | 0.893 |
| 2019 | nse | stock | 244 | 202 | 8,125,222 | 0.16 | 0.947 |
| 2020 | nse | index | 250 | 3 | 1,304,697 | 0.16 | 0.932 |
| 2020 | nse | stock | 250 | 153 | 7,675,892 | 0.17 | 0.964 |
| 2021 | nse | index | 247 | 3 | 1,409,921 | 0.21 | 0.934 |
| 2021 | nse | stock | 247 | 197 | 12,078,428 | 0.15 | 0.971 |
| 2022 | bse | index | 1 | 1 | 37 | 0.95 | 1.000 |
| 2022 | nse | index | 248 | 4 | 1,759,987 | 0.23 | 0.938 |
| 2022 | nse | stock | 248 | 203 | 14,100,002 | 0.16 | 0.964 |
| 2023 | bse | index | 155 | 2 | 19,733 | 0.93 | 0.958 |
| 2023 | nse | index | 245 | 4 | 1,148,505 | 0.34 | 0.936 |
| 2023 | nse | stock | 245 | 192 | 11,465,046 | 0.22 | 0.962 |
| 2024 | bse | index | 246 | 3 | 80,428 | 0.90 | 0.969 |
| 2024 | bse | stock | 121 | 36 | 8,741 | 0.24 | 0.957 |
| 2024 | nse | index | 246 | 5 | 1,504,966 | 0.33 | 0.942 |
| 2024 | nse | stock | 246 | 231 | 8,195,076 | 0.34 | 0.960 |
| 2025 | bse | index | 248 | 3 | 168,183 | 0.81 | 0.961 |
| 2025 | bse | stock | 241 | 149 | 12,140 | 0.30 | 0.949 |
| 2025 | nse | index | 248 | 5 | 1,140,966 | 0.37 | 0.944 |
| 2025 | nse | stock | 248 | 258 | 7,437,401 | 0.42 | 0.958 |
| 2026 | bse | index | 212 | 4 | 173,847 | 0.77 | 0.963 |
| 2026 | bse | stock | 212 | 166 | 6,393 | 0.16 | 0.929 |
| 2026 | nse | index | 212 | 6 | 1,147,982 | 0.34 | 0.942 |
| 2026 | nse | stock | 212 | 232 | 7,304,202 | 0.40 | 0.954 |

**NIFTY ATM IV** (expiry closest to 30 cal d, >=7 d; median, vol pts): 2019: 14.2 (244 d; VIX median 15.9 on 244 d); 2020: 20.4 (250 d; VIX median 22.4 on 250 d); 2021: 16.0 (247 d; VIX median 17.4 on 248 d); 2022: 18.1 (248 d; VIX median 19.0 on 248 d); 2023: 10.7 (245 d; VIX median 12.0 on 245 d); 2024: 13.3 (246 d; VIX median 14.1 on 246 d); 2025: 11.6 (248 d; VIX median 12.6 on 248 d); 2026: 12.8 (212 d; VIX median 13.8 on 212 d)

**Expiry weekday by year** (weekday: count of expiries; observed in files, holiday shifts included):
- NIFTY: 2019 m: Thu14; 2019 w: Thu31/Wed2; 2020 m: Thu15/Wed1; 2020 w: Thu37; 2021 m: Thu14; 2021 w: Thu34/Wed4; 2022 m: Thu12; 2022 w: Thu39/Wed1; 2023 m: Thu11/Wed3; 2023 w: Thu40; 2024 m: Thu12; 2024 w: Thu38/Wed2; 2025 m: Thu9/Tue4/Wed2; 2025 w: Thu25/Tue13/Wed1/Mon1; 2026 m: Tue10/Thu2/Mon1; 2026 w: Tue31/Mon4
- BANKNIFTY: 2019 m: Thu15; 2019 w: Thu35/Wed2; 2020 m: Thu15/Wed1; 2020 w: Thu37; 2021 m: Thu14; 2021 w: Thu34/Wed4; 2022 m: Thu12; 2022 w: Thu39/Wed1; 2023 m: Thu11/Wed3; 2023 w: Thu31/Wed13; 2024 m: Wed10/Thu7/Tue2; 2024 w: Wed31/Tue3; 2025 m: Thu9/Wed7/Tue4; 2026 m: Tue10/Thu1/Mon1
- SENSEX: 2022 w: Thu1; 2023 m: Fri24/Thu1; 2023 w: Fri9; 2024 m: Fri15/Thu4; 2024 w: Fri33/Thu1; 2025 m: Tue8/Thu3/Fri2/Wed2; 2025 w: Tue26/Thu12/Fri4; 2026 m: Thu8/Wed2; 2026 w: Thu36/Wed1

**ca_break events** (symbol-days, NSE): 230; top: NATIONALUM 2019-03-11 (gone 100%, fut ret -3%), OIL 2019-02-21 (gone 100%, fut ret -4%), RECLTD 2019-03-11 (gone 100%, fut ret -5%), IDEA 2019-03-29 (gone 100%, fut ret -44%), BEL 2019-03-27 (gone 100%, fut ret -0%), ONGC 2019-03-26 (gone 100%, fut ret +0%)

**Dropped**: non-equity-derivative instrument rows {'EF': 12, 'EO': 40}; duplicate contract rows 0. UDiFF volume unit detected per file: {'quantity': 581, 'contracts': 581}. Option rows by forward source: fut 93.6%, interp 0.0%, parity 3.2%, spot 3.2%.

**CM prev_close check**: 589263 consecutive symbol-day pairs, 2100 where prev_close != prior close (>0.5%): [('2023-11-13', '360ONE', 0.98), ('2024-01-23', '360ONE', 1.0142), ('2024-03-04', '360ONE', 0.9934), ('2025-02-03', '360ONE', 1.0158), ('2026-02-02', '360ONE', 0.9713), ('2026-11-09', '360ONE', 0.9908), ('2019-10-29', 'AARTIIND', 1.0087), ('2020-02-03', 'AARTIIND', 0.9802)]. Mismatches are exchange adjustments on ex-dates; ret = close/prev_close - 1.

**Notes**
- Sources: NSE F&O legacy to 2024-07-05, UDiFF after (UDiFF preferred when both exist). Spot: NSE CM (EQ/BE) and `ind_close_all` (indices, INDIAVIX); fallback F&O `UndrlygPric` (close only). SENSEX/BANKEX: BSE UDiFF `UndrlygPric` (close only, no O/H/L - no BSE index file is downloaded); BSE legacy days have no spot field (`Prem/Disc` blank) -> `bse_parity_proxy` = nearest-expiry forward x e^{-rT}.
- BSE files list only active contracts -> no ca_break detection on BSE; is_monthly can misfire when a month's last expiry is absent from a file.
- Overlap Jan-Jul 2024: the bulk fetch takes one format per date (legacy first), so the legacy-vs-UDiFF agreement test skips until a same-date pair exists; lot reconstruction is instead validated on UDiFF files (legacy method vs NewBrdLotQty).
- dte_trd beyond the last observed trading day counts weekdays (holidays unknown); a `--full` run after the download fixes all partitions.
- Timing this run: stage 2s (0 files), assemble 1590s (139 months), refs 46s. Full-history estimate (~1,950 days x 5 kinds): stage ~10 min, assemble ~25 min (~11 s/NSE month), refs <1 min.
