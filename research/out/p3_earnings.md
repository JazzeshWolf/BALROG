# P3 - Earnings (quarterly results) dates, NSE F&O stocks 2019-01 -> 2026-11

**Source:** NSE public JSON API (cookie-primed session, as in xerxes `nse.mjs`), per symbol: `/api/corporates-financial-results?index=equities&symbol=S&period=Quarterly` (filing broadcast timestamp = actual announcement time) + `/api/event-calendar?symbol=S` (board-meeting dates, purpose matching /result/). BSE not needed. Script `research/scripts/p3_fetch_earnings.py`, raw cache `data/raw/earnings/{finres,evcal}/`, output `data/earnings/earnings_dates.parquet`.

- Universe 210 symbols; fetch failures: 0. Events: **6514**, symbols covered **208/210**.
- Source mix: {'nse_finres+evcal': 3822, 'nse_evcal': 1888, 'nse_finres': 804}.
- announce_time known for 71.0% of events; session split {'post': 3089, None: 1888, 'during': 1486, 'pre': 51}.

| year | events | symbols | median ev/sym | share of syms with 4 |
|---|---|---|---|---|
| 2019 | 855 | 179 | 5 | 40% |
| 2020 | 835 | 185 | 4 | 70% |
| 2021 | 766 | 193 | 4 | 92% |
| 2022 | 778 | 196 | 4 | 91% |
| 2023 | 800 | 201 | 4 | 98% |
| 2024 | 817 | 207 | 4 | 97% |
| 2025 | 832 | 208 | 4 | 100% |
| 2026 | 831 | 208 | 4 | 98% |

Full years 2019-2025: events per symbol-year mean 4.15, median 4; distribution {1: 10, 2: 7, 3: 18, 4: 1161, 5: 111, 6: 39, 7: 15, 8: 5, 9: 3}.

## Validation vs archive (`data/archive/events.parquet`)
Reference = in-window stock events with kind ~ Result|Earnings, distinct (symbol, date): **287** (p1 definition; includes news-derived and 'Earnings call' rows).
- all (n=287): exact 78.7%, within +-1d **87.1%**, +-3d 92.7%, no event within 7d 4.2%.
- nse-sourced (n=239): exact 92.9%, within +-1d **96.7%**, +-3d 97.9%, no event within 7d 0.8%.
- news-sourced (n=48): exact 8.3%, within +-1d **39.6%**, +-3d 66.7%, no event within 7d 20.8%.
- kind=='Results' only (n=261): within +-1d 90.8%.
- Misses by archive kind/source: {('Earnings call', 'news'): 7, ('Earnings call', 'nse'): 6, ('Results', 'news'): 22, ('Results', 'nse'): 2}.

## Caveats
- event_date = calendar date of the FIRST exchange broadcast of that quarter's results (earliest of standalone/consolidated filings). Board-meeting date kept in `board_date`; calendar-only events (no filing found) use the meeting date and have no time.
- `session`: pre <09:15, during 09:15-15:30, post >15:30 IST. `first_tradable_date` = next weekday for post-market results (no exchange-holiday calendar applied).
- Universe = today's ~210 F&O names (survivorship): names that left F&O are absent; recently listed names have short histories. Renamed symbols are queried under the current symbol only.
- 2019-2020 are noisier: about 13% of symbol-years have 5 or more events, from finres-only and calendar-only rows falling in the same quarter (late or re-filed broadcasts, adjourned meetings). From 2021 on, more than 90% of symbol-years have exactly 4. Before an event study, dedupe to one event per symbol-quarter.
- About 29% of events (mostly `nse_evcal`) have no filing match, so they have no announce_time and no session.
- Filings >150 days after period end are dropped as revisions; adjourned meetings may leave a stray calendar-only row. Annual (Q4) results come through the Quarterly feed as 'Fourth Quarter'.
