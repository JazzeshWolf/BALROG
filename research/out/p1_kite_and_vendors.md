# P1 — Kite Connect historical data & alternative option-data vendors

Compiled 2026-11-13. Tags: **[tested]** = verified this session; **[docs]** = official doc/support/staff forum post; **[community]** = user posts or third-party pages.
Vendor rows are documentation-only (nothing was tested against vendor APIs).

## A. Zerodha Kite Connect

| # | Claim | Tag / source |
|---|---|---|
| 1 | Intervals: minute, 3/5/10/15/30/60minute, day. | [docs] https://kite.trade/docs/connect/v3/historical/ |
| 2 | Max span per request: minute 60d; 3/5/10min 100d; 15/30min 200d; 60min 400d; day 2000d (exceeding it gives an "interval exceeds limit" error). This is not on the docs page. | [docs] staff forum https://kite.trade/forum/discussion/9129 ; https://kite.trade/forum/discussion/16124 |
| 3 | Minute history starts around early 2015 (some instruments start in late 2015; NIFTY 50 was seen from 2015-01-09). | [docs] staff forum https://kite.trade/forum/discussion/8722 ; https://kite.trade/forum/discussion/14149 |
| 4 | `oi=1` adds an OI field to each candle (works for minute candles too). | [docs] historical page |
| 5 | `continuous=1` works only for NFO/MCX **futures**, and only with **day** candles: you pass a live token and get stitched expired contracts. It is no use for options. | [docs] historical page; staff: https://kite.trade/forum/discussion/3666 |
| 6 | Rate limit: historical 3 req/s, quote 1 req/s, other endpoints 10 req/s. Staff say there is no daily cap. | [docs] https://kite.trade/docs/connect/v3/exceptions/ |
| 7 | Pricing: Connect costs ₹500/month per app and **includes** historical + live data. The ₹2,000 historical add-on was dropped on 2025-02-08. The free **Personal** tier has orders/portfolio only, **no live or historical data**. GST treatment is not stated. | [docs] https://support.zerodha.com/category/trading-and-markets/kite-api/articles/what-are-the-charges-for-kite-apis ; https://kite.trade/forum/discussion/14806 ; https://kite.trade/forum/discussion/15015 |
| 8 | **Expired options: NOT available.** Exchanges flush F&O tokens at every expiry, and the instruments API lists only live contracts. The docs say "It is not possible to retrieve instrument_tokens for expired contracts from the API." Expired futures are available as **day** candles only. Zerodha support also says expired option data cannot be accessed on Kite. | [docs] historical page; https://support.zerodha.com/category/trading-and-markets/charts-and-orders/charts/articles/historical-data-for-expired-f-o-contract ; https://kite.trade/forum/discussion/12880 |
| 9 | **Pending change:** Zerodha staff (siva) replied "Yes, we will offer this in coming months" on 2026-06-05. On 2026-10-14 they said they "want to make it available by this year end". No ETA or official announcement yet. | [docs] https://tradingqna.com/t/kite-api-expired-options-instrument-token-not-accessible-for-historical-data-download/194727 |
| 10 | Whether a **self-cached** expired option token still returns minute candles is unconfirmed (one forum report says this works for futures). Workaround: snapshot the instruments dump every day starting now, then test once Connect data access works. | [community] https://kite.trade/forum/discussion/2277 |
| 11 | Kite MCP connector: the session is authenticated and `ltp` works (NIFTY 50 = 25139.25). But **every `historical` call returns "Insufficient permission for that call"**, both minute 2015 and day 2026-11. Most likely the linked app is on the Personal tier or has no data entitlement. I could not test minute depth, option OI or stock options. | [tested] |
| 12 | The instruments dump (2026-11-13, 124,568 rows) has derivative expiries 2026-11-17 to 2031-06-26, and **no expiry earlier than today**, so only live contracts are listed. NFO: 43,390 options + 667 futures; BFO: 6,280 options + 12 futures. | [tested] `research/out/p1_kite_instruments.json` |

Live index expiries ([tested], instruments dump 2026-11-13):

| Index (exch) | Lot | Near expiries (weekday) | # live expiries |
|---|---|---|---|
| NIFTY (NFO) | 65 | 11-17 Tue, 11-23 **Mon**, 12-01 Tue … (weekly Tue) | 18 |
| BANKNIFTY (NFO) | 30 | 11-23 Mon, 12-29 Tue, 2027-01-25 Mon (monthly only) | 6 |
| FINNIFTY (NFO) | 60 | 11-23 Mon, 12-29 Tue, 01-25 Mon (monthly only) | 3 |
| MIDCPNIFTY (NFO) | 120 | 11-23 Mon, 12-29 Tue, 01-25 Mon (monthly only) | 3 |
| SENSEX (BFO) | 20 | 11-19 Thu, 11-26 Thu, 12-03 Thu (weekly Thu) | 19 |

Stock options (NFO) cover 219 underlyings with 3 monthly expiries (2026-11-23 Mon, 2026-12-29 Tue, 2027-01-25 Mon). Other BFO option underlyings: BANKEX, SENSEX50, FOCIT.
*Anomaly:* the Monday monthlies look like last-Tuesday expiries moved forward a day for holidays (24-Nov-2026 and 26-Jan-2027 are presumably holidays). Not checked against the NSE holiday list. "FOCIT" is an unfamiliar BFO underlying.

## B. Alternatives for historical intraday option data (expired contracts)

| Source | Coverage | Granularity / per-call | Depth | Expired? | OI | Bid/ask | Cost | Access | Conf. | URL |
|---|---|---|---|---|---|---|---|---|---|---|
| **Dhan Data API** (`/charts/rollingoption`) | Index + stock options, NSE + BSE (no MCX) | 1/5/15/25/60 min; 30 days per call | 5 yrs | Yes, but **rolling by moneyness** (ATM±10 for near-expiry index, ATM±3 for others). The strike moves with spot, so you cannot request an arbitrary fixed strike. | Yes (+IV, spot) | No | ₹499/mo + GST (yearly ≈ ₹399/mo) | Dhan account + KYC, API token | high | https://dhanhq.co/docs/v2/expired-options-data/ ; https://dhan.co/support/platforms/dhanhq-api/which-exchanges-support-expired-options-data/ |
| **Fyers API v3** Expired F&O Data | NSE/BSE/MCX F&O, per contract (index + stock) | 5s, 1–240 min; 100 days per call (30 for seconds) | Intraday: NSE from 2018-10-10, BSE from 2023-08-07 | Yes (Get Expired Contracts → symbol) | Yes (+greeks) | No | Standard free but **10 intraday req/day**. Prime ₹499/mo + GST (₹4,990/yr) | Fyers account + KYC, API app | medium-high | https://fyers.in/api-docs/v3/api/market-data/expired-fo-data/get-expired-fo-data/ ; https://support.fyers.in/portal/en/kb/articles/how-much-does-fyers-prime-cost |
| **ICICI Direct Breeze** `get_historical_data_v2` | NSE F&O per contract (BSE unclear) | 1s, 1/5/30 min, day | ~3 yrs F&O (one claim says 10 yrs, which conflicts) | Yes (by expiry/strike/right) | Yes | No | Free | ICICI Securities demat + trading account | medium | https://api.icicidirect.com/apiuser/home ; https://pypi.org/project/breeze-connect |
| **Groww Trade API** backtesting | NSE (+BSE listed) CASH & FNO | 1–5 min: 30 days/call; 10–30 min: 90 days | FNO "from 2020" | Yes (expiries + contracts endpoints) | Yes | No | Not stated (Trade API subscription) | Groww account | medium | https://groww.in/trade-api/docs/python-sdk/backtesting |
| **Upstox** Expired Instruments API | NSE_FO (BSE_FO reports exist) | 1/3/5/15/30 min, day | Not stated (sample code from 2020) | Yes | Yes | No | Needs Upstox **Plus** (price not found) | Upstox account | medium-low (users report missing candles and contracts) | https://upstox.com/developer/api-documentation/announcements/expired-instruments-api ; https://community.upstox.com/t/missing-historical-data-for-multiple-option-instruments/9232 |
| Angel One SmartAPI | Live NFO only | 1 min+ | ~6 months for live options | **No** | Yes (live) | No | Free | Angel account | medium | https://smartapi.angelone.in/topic/4133/get-fno-data-for-expired-instrument/2 |
| 5paisa API | Live contracts only | 1 min+ | — | **No** | — | No | Free | 5paisa account | low (undated forum post) | https://tradebetter.5paisa.com/t/how-can-i-get-historic-candles-data-of-options/773 |
| Shoonya / Finvasia | No official API for expired data. Unverified "free CSV" page (NIFTY from 2024-01, BANKNIFTY from 2026-02) | 1 min? | ~2 yrs | CSV only | ? | ? | Free | — | low | https://shoonya-trader-web.pages.dev/free-historical-expired-options-contract-data |
| **TrueData** (Market Data API / IEOD) | NSE + BSE F&O | Tick / 1 min / 5 min / EOD | Not published | Not stated | ? | Tick data has it | API: "contact sales". Velocity plugin ₹1,699–3,299/mo | Vendor contract (exchange-licensed) | low | https://www.truedata.in/products/marketdataapi |
| Global Datafeeds (GDFL) | NSE/BSE F&O (authorised vendor) | Tick / 1 min | Not published | Recommended on forums; not verified | ? | ? | Quote on request | Vendor contract | low | https://kite.trade/forum/discussion/11571 (no official page fetched; globaldatafeeds.in/historical-data returned 404) |
| NSE Data & Analytics (NDAL) | All NSE F&O: historical trade (tick) data, 1/2/5-min snapshots | Tick / snapshot | Many years | Yes (exchange source) | Yes | Snapshots: yes | Tariff PDF (not read). Historically expensive, aimed at institutions | Order form with NDAL | medium (that it exists) / low (price) | https://www.nseindia.com/nsedataandanalytics |
| AlgoTest / Stockmock / Opstra | Index options replay (AlgoTest ~7.5 yrs; Stockmock from 2021-01) | 1 min (in-app) | 5–7 yrs | In-app only | in-app | ? | ₹ subscriptions | Web account | low: **no data export found**, so unusable as a raw dataset | https://algotest.in/blog/algotest-vs-stockmock/ |
| HuggingFace `thetrademarkk/india-index-options-1m` | NIFTY, BANKNIFTY, SENSEX spot + partial chains | 1 min | ~2021-05 to 2026 | Yes | Listed in schema | No | Free, **CC-BY-NC-4.0** | none | low (source unnamed; far strikes sparse; option files "uploaded later") | https://huggingface.co/datasets/thetrademarkk/india-index-options-1m |
| Kaggle | No credible NIFTY/BANKNIFTY options 1-min dataset found | — | — | — | — | — | — | — | low | (search only) |

## C. Top-3 for a solo retail researcher

1. **Fyers Prime (₹499 + GST/mo, ≈₹589).** You get exact-contract expired options (index **and** stock, NSE + SENSEX/BSE) at 1 min, with OI and greeks, back to Oct 2018 for NSE. That covers both needs (i) and (ii), and fixed-strike data is what buyer-side P&L reconstruction needs. Stay on Prime for the bulk pull: Standard allows only 10 intraday req/day.
2. **Dhan Data API (₹499 + GST/mo, or ≈₹399/mo yearly).** 5 years of 1-min data with OI, IV and spot, for index + stock options on NSE + BSE. A good fit for ATM-relative buying studies. The limit: strikes roll by moneyness (±10 near index, ±3 for others), so far-OTM or fixed-strike trades cannot be rebuilt. Good as a cross-check against Fyers.
3. **ICICI Breeze (free with an ICICI demat).** About 3 years of expired F&O at 1-min or 1-sec, per contract. It is the cheapest route and the depth only just meets the ≥2 yr requirement. Use it as a second source to validate prices.
Watch-list: Kite says expired options may arrive by end-2026, and Groww has FNO data from 2020 (pricing unknown). Unlocking Kite Connect (₹500/mo) also gives 2015+ minute data for the index/stock **underlyings**.

## D. Unknowns / next checks
- Why Kite MCP `historical` is denied (Personal tier vs. app entitlement). The minute depth claim (2015) and live-option `oi` are not tested here.
- Fyers and Dhan: real coverage of stock options for old expiries and of BSE SENSEX weeklies, and gaps or bad ticks. Needs a hands-on sample pull of 1 expiry × 5 strikes.
- Breeze: is the depth 3 yrs or 10 yrs, and does it cover BFO (SENSEX)?
- Upstox Plus price and depth; Groww API price; TrueData/GDFL/NDAL quotes (all need email or sales contact).
- No source gives historical **bid/ask** cheaply. Only NDAL snapshots or tick data do, so spread/slippage modelling needs another approach.
