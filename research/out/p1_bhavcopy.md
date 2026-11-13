# P1 - NSE/BSE bhavcopy as a source for swing option-buying backtests
Probed 2026-11-13 from this machine; 48 data files downloaded (samples only). Scripts: `research/scripts/p1_fetch_bhavcopy.py` (cached downloader + `data/raw/bhavcopy/manifest.csv`), `research/scripts/p1_bhavcopy_probe.py` -> `research/out/p1_bhavcopy_probe.json`.

## Bottom line - earliest usable dates
| Product | File exists from | Usable for a buyer's backtest from | Why |
|---|---|---|---|
| NSE index options (NIFTY/BANKNIFTY...) | 2001-06/07 [tested 2001-07-16] | **2010** for monthly NIFTY; **2019-02** for NIFTY weeklies (BANKNIFTY weeklies 2016) [docs/inferred for weekly dates] | 2005: 22 traded NIFTY strikes near expiry; 2010: 25; 2016: 61; 2022+: every listed near strike traded [tested] |
| NSE stock options | 2001-07 [tested, 31 underlyings] | **2018** (prefer 2020+) | median traded strikes per stock (near expiry): 0 (2001), 3 (2005), 2 (2010), 9 (2016), 11 (2018), 21 (2020), 22-31 (2022-26) [tested] |
| BSE SENSEX options | legacy deriv. bhavcopy present 2016+ [tested HEAD], but 37 rows in 2022-06 | **2023-05-15** relaunch; realistically **2024-01+** | 2023-06-15: 45 option rows, 20 traded strikes near expiry; 2025-06: 178; 2026-11: 236 [tested] |
| Underlying OHLC (NSE CM) | 2016 tested; legacy archive goes back further [docs/inferred] | 2019 target range is fully covered | |
| Index OHLC incl. India VIX (`ind_close_all`) | between 2012-01-03 and 2012-06-15 [tested: 2010/2011/Jan-2012 404, Jun-2012 200] | 2012 | India VIX row present in 2016 and 2026 files [tested] |
| Participant-wise OI | 2012-06 or earlier [tested 2012, 2016, 2026] | 2012 | 5 rows: Client/DII/FII/Pro/TOTAL x 14 long/short buckets |

## URL patterns
| Kind | URL (host prefix) | Valid dates |
|---|---|---|
| NSE F&O legacy | `nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip` (also `archives.nseindia.com`, same path) | 2000/2001 -> **2024-07-05** (404 from 2024-07-08) [tested] |
| NSE F&O UDiFF | `nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | **2024-01-01** -> today (404 on 2023-12-29) [tested]; overlap with legacy Jan-Jul 2024 |
| NSE CM legacy / UDiFF | `.../content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip` / `.../content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | legacy to 2024-07-05; UDiFF from 2024-01-02 or earlier [tested] |
| NSE index + VIX | `.../content/indices/ind_close_all_{DDMMYYYY}.csv` | 2012 -> today [tested] |
| NSE participant OI | `.../content/nsccl/fao_participant_oi_{DDMMYYYY}.csv` | 2012 -> today [tested] |
| NSE F&O ban list | `.../archives/fo/sec_ban/fo_secban_{DDMMYYYY}.csv` (`/content/fo/` path 404s for 2016) | 2016 -> today [tested]; earlier unknown |
| NSE lot sizes | `.../content/fo/fo_mktlots.csv` | **current snapshot only** (forward months) [tested] |
| BSE F&O legacy | `www.bseindia.com/download/Bhavcopy/Derivative/bhavcopy{DD-MM-YY}.zip` | 2016 -> still served after the switch (HEAD 2024-08-14 = zip) [tested] |
| BSE F&O UDiFF | `www.bseindia.com/download/BhavCopy/Derivative/BhavCopy_BSE_FO_0_0_0_{YYYYMMDD}_F_0000.CSV` (plain CSV, no zip) | **2024-01-02** -> today; for 2023 returns HTTP 200 + HTML page [tested] |
| BSE CM | `.../BhavCopy/Equity/EQ_ISINCODE_{DDMMYY}.zip` (legacy) / `.../BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{YYYYMMDD}_F_0000.CSV` | legacy 2023 tested; UDiFF 2026 tested |
| India VIX API | `www.nseindia.com/api/historical/vixhistory?...` | **503 without session cookies** [tested]; not needed - VIX is in `ind_close_all` |

## Field coverage (option rows)
| Field | NSE legacy | NSE UDiFF | BSE legacy | BSE UDiFF |
|---|---|---|---|---|
| Open/High/Low/Close | Y | Y | Y | Y |
| Last traded price | - | `LastPric` | - (`Wt. Avg. Price`) | `LastPric` |
| Settle price | `SETTLE_PR` | `SttlmPric` | - | `SttlmPric` |
| OI / chg OI | Y (**shares**) | Y (**shares**, 99.5% divisible by lot) | Y (contracts) | Y (qty, 100% div. by lot) |
| Volume | `CONTRACTS` (contracts) | `TtlTradgVol` = **contracts** (fut turnover/(vol*close) = 1.00 x lot) | `Contracts Traded` | `TtlTradgVol` = **quantity** (100% div. by lot) |
| Turnover | `VAL_INLAKH` (Rs lakh) | `TtlTrfVal` (Rs; for options ~notional, not premium - ratio >> lot) | Rs lakh | Rs |
| Underlying price | - (join CM/index) | `UndrlygPric` (100% rows) | - | `UndrlygPric` |
| Lot size | - (recoverable: NIFTY fut turnover/contracts/price gives 200 (2001), 100 (2005), 50 (2010), 75 (2016-20), 50 (2022-23), 25 (2024-06) [tested]) | `NewBrdLotQty` | - | `NewBrdLotQty` |
| # trades | - | `TtlNbOfTxsExctd` | - | Y |
| Expiry, strike, CE/PE | Y (`OPTION_TYP`; 2005 file spells `OPTIONTYPE`) | Y | in `Symbol` code + `Expiry`, CALL/PUT | Y |
All [tested] on the samples. BSE files list only contracts with activity/OI (2026-11-12: 961 SENSEX option rows vs ~42k NSE rows) - not the full listed chain.

## Data quality (option rows, from samples) [tested]
- Zero-volume share, all NSE options: 0.90 (2016), 0.88 (2018), 0.83 (2020), 0.85 (2022), 0.78 (2023), 0.71 (2024-06), 0.66 (2024-08), 0.61 (2025-06), 0.61 (2026-11). Index ~0.65-0.89, stock ~0.60-0.90.
- NIFTY nearest expiry, strikes listed/traded: 34/22 (2005), 41/25 (2010), 126/61 (2016), 139/63 (2018), 88/73 (2020), 96/96 (2022), 101/101 (2024-06), 83/83 (2025), 142/142 (2026). Third expiry still 10-40% zero-volume rows.
- Stock options nearest expiry, traded strikes per underlying: median 9 (2016) -> 21 (2020) -> 30 (2026); p10 3 (2016), 13 (2020), 21 (2026).
- Close vs settle: on zero-volume rows close != settle in **99.6-99.9%** of rows (close = stale last trade/prev close; settle = exchange theoretical price). On traded rows they differ in 0% (<=2018) and 24-33% (2020+) - NSE settle for options is no longer the last price.
- **BSE expiry-day anomaly**: on expiry day every expiring SENSEX option row has close = SENSEX spot (100% in 2024-06-14 legacy and 2026-11-12 UDiFF). Must be masked. NSE expiring rows do not show this.
- SENSEX real volume: yes from 2024 (2026-11-12: 735/961 option rows traded, 11 expiries).

## Rate limit / headers [tested]
- 20 consecutive NSE UDiFF requests (2026-09-01..24) at 1 s sleep: 17 x 200 (~1.05 MB each), 1 holiday (2026-09-14) 404, **0 blocks, 0 retries**, ~28 s wall (~1.5 s/request).
- `nsearchives` needs only a browser User-Agent: no cookies, no Referer. Without UA the request **hangs** (tarpit, >120 s) rather than failing fast - the fetcher sets a 30 s timeout. `www.nseindia.com` (site + API) is 403/503 without a cookie session - avoid it.
- BSE answered even without a UA (206), but one later request timed out after 20 s, so expect some flakiness and rely on retries. Missing files can return **HTTP 200 + HTML** - the fetcher rejects HTML/non-zip bodies and records them as 404.

## Sizing: 2019-01-01 -> 2026-11-12 (~1,950 trading days) [inferred from sample means]
NSE F&O ~1.52 GB, BSE F&O >=0.09 GB, NSE CM ~0.22 GB, index+VIX ~0.02 GB, participant OI ~2 MB => **~1.9 GB, ~9,750 files, ~4.2 h at 1 req/s** (one process; can be split by kind). Holidays add ~5% 404 requests.

## What bhavcopy cannot give, and mitigations
1. **No bid/ask or depth** - fills at close are fiction for illiquid strikes. *Mitigation*: trade only rows with volume > 0 on entry and exit days; charge a modelled half-spread (e.g. max(tick, k% of premium) calibrated on live quotes later); restrict to near-ATM, near-expiry NIFTY/BANKNIFTY/SENSEX and liquid stocks. [docs/inferred]
2. **No IV / Greeks** - *Mitigation*: back out IV from close (or settle) with Black-76 using the futures price (UDiFF has `UndrlygPric`; legacy needs a join to futures/CM) and a T-bill rate; flag rows where close < intrinsic. [inferred]
3. **One print per day, close is "last trade" time-unknown** - a 15:29 vs 11:00 last trade looks identical. *Mitigation*: require volume > threshold; prefer settle for MTM on illiquid rows; enter/exit at next-day open only with a slippage haircut (open is the first trade, even noisier). [inferred]
4. **Stale closes on ~60-90% of option rows** [tested] - *Mitigation*: never mark-to-close on zero-volume rows; use `SettlePrice` (theoretical) for MTM only, never for fills.
5. **Corporate actions** (splits/bonus/special dividends) change strikes, lots and symbol - *Mitigation*: detect strike-grid/lot jumps per symbol; join NSE CA list; drop contracts spanning an ex-date. [docs/inferred]
6. **Lot sizes over time** - legacy files have none and `fo_mktlots.csv` is current only. *Mitigation*: UDiFF `NewBrdLotQty` (2024+); pre-2024 derive from futures turnover/(contracts x price) per symbol per day [tested for NIFTY]; cross-check OI divisibility.
7. **Units change** legacy vs UDiFF (OI shares both; NSE volume contracts; BSE UDiFF volume quantity) - normalise in the loader. [tested]
8. **BSE files omit untraded/zero-OI strikes and overwrite expiry-day close with spot** - *Mitigation*: treat missing strike as untradeable; mask expiry-day rows (use settlement = intrinsic vs spot). [tested]
9. **Expiry-day schedule changes** (NIFTY weekly Thu->Tue in 2025, BANKNIFTY weekly discontinued Nov-2024, holiday-shifted expiries e.g. NIFTY 2026-11-23 Mon) - *Mitigation*: always take expiry from the file, never from a calendar rule. [tested + docs]
10. **Survivorship / universe**: F&O stock list changes; ban-list days block new positions. *Mitigation*: universe = symbols present in that day's file; apply `fo_secban` (2016+). [tested]
11. **No intraday path** - stops/targets inside the day are unknowable. *Mitigation*: model exits only on daily close/open, or use high/low with a pessimistic ordering assumption. [inferred]
12. **VIX pre-2012 and index OHLC pre-2012** not in `ind_close_all`. *Mitigation*: start backtests in 2012+ or source VIX history separately. [tested]

## Unknowns
Exact first date of `ind_close_all` (Feb-May 2012), of the ban-list archive, and of NSE UDiFF CM; legacy option `VAL_INLAKH` premium vs notional basis; BSE legacy file sizes 2019-2022 (only 2022/2023 sampled, both tiny).
