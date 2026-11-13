# P1 — Upstox historical data: what is actually available

Run: 2026-11-13, `python research/scripts/p1_upstox_probe.py` (73 requests, **no token sent**), raw per-call log in `research/out/p1_upstox_probe.json`.
Doc URLs: V3 hist = https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/ ; V2 hist = https://upstox.com/developer/api-documentation/get-historical-candle-data/ ; intraday = https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/ ; expired candles = https://upstox.com/developer/api-documentation/get-expired-historical-candle-data/ ; expiries = https://upstox.com/developer/api-documentation/get-expiries/ ; expired option contracts = https://upstox.com/developer/api-documentation/get-expired-option-contracts/ ; rate limits = https://upstox.com/developer/api-documentation/rate-limiting/ ; Plus T&C = https://upstox.com/files/terms-and-condition/plus-pack.pdf

## Findings table

| Capability × instrument | Available? | Depth | Granularity / fields | Auth / plan | Tag |
|---|---|---|---|---|---|
| Index 1-min (NIFTY 50, NIFTY BANK, SENSEX) via V3 | Yes | **2022-01-03 onward**; Dec-2021 window returns 200 with 0 candles (binary search on NIFTY, spot-checked BANK/SENSEX) | 375 bars/day, full session; OI field present but 0 | **No token needed** (HTTP 200 unauthenticated) | [tested] |
| Index daily (NIFTY 50) via V3 | Yes | back to **1990-07-03** (docs say from 2000) | OHLC | no token | [tested] |
| Stock equity 1-min (RELIANCE) V3 | Yes | 2022-01-03 onward; Dec-2021 empty | 375 bars/day | no token | [tested] |
| V3 range limits | — | minutes 1–15: max **1 month** per request; >15–300 min / hours: 1 quarter; days: 1 decade; weeks/months: none. Minutes/hours from Jan 2022, days/weeks/months from Jan 2000 | units minutes(1–300)/hours(1–5)/days/weeks/months; candle = [ts,o,h,l,c,vol,oi] | samples use Bearer token; not stated as mandatory | [docs] V3 hist |
| V3 1-month window | Works | 27-day window returned 7,125–7,500 1-min bars | — | — | [tested] |
| V2 historical 1-min (`/v2/historical-candle/{key}/1minute/{to}/{from}`) | Yes, despite "Deprecated" label | served 3-year-old windows (Nov-2023) for NIFTY & RELIANCE — contradicts docs ("1 month"/"6 months") | intervals 1minute/30minute/day/week/month | no token | [tested] + [docs] V2 hist |
| Intraday (today) V3 `/v3/historical-candle/intraday/{key}/minutes/1` and V2 `/v2/historical-candle/intraday/{key}/1minute` | Yes | current session only | 1-min, OI populated for F&O | no token | [tested] + [docs] intraday |
| Live NIFTY weekly option (25300 CE, exp 17-Nov-26), 1-min | Yes | from first trade 2026-11-03 → 8 trading days | **OI non-zero** | no token | [tested] |
| Live NIFTY monthly option (25300 CE, exp 23-Nov-26) | Yes | from 2026-10-01 → 27 days | OI non-zero | no token | [tested] |
| Live SENSEX weekly option (81900 CE, exp 19-Nov-26, BSE_FO) | Yes | from 2026-11-06 → 5 days | OI non-zero | no token | [tested] |
| Live stock option (RELIANCE 1340 CE, exp 23-Nov-26) | Yes | from 2026-09-01 → 48 days | OI non-zero | no token | [tested] |
| Live futures (NIFTY FUT, RELIANCE FUT, exp 23-Nov-26) | Yes | from 2026-08-26 → 54 days (whole contract life) | OI non-zero | no token | [tested] |
| Live-contract history = since first trade only | — | once a contract expires it drops out of the master and these endpoints; history beyond current series needs the Expired API | — | — | [tested] (by construction) + [docs] |
| Expired: Get Expiries `GET /v2/expired-instruments/expiries?instrument_key=` | Gated | "up to six months of historical expiries", weekly+monthly, not MCX | list of YYYY-MM-DD | **Upstox Plus** (UDAPI1149 otherwise) | [docs] expiries |
| Expired option contracts `GET /v2/expired-instruments/option/contract?instrument_key=&expiry_date=` | Gated | per expiry | strike, CE/PE, lot, expired instrument_key | Upstox Plus | [docs] expired option contracts |
| Expired future contracts `GET /v2/expired-instruments/future/contract?instrument_key=&expiry_date=` | Gated | per expiry | — | Upstox Plus | [docs] (family page) |
| Expired candles `GET /v2/expired-instruments/historical-candle/{key|DD-MM-YYYY}/{interval}/{to}/{from}` | Gated | from contract's first trade to expiry; expiries available ≈ 6 months back (staff, May-2025: back to Oct-2024, "plan to extend to 2 years") | 1minute, 3minute, 5minute, 15minute, 30minute, day; **candle includes OI** (7th field) | Upstox Plus | [docs] expired candles; community thread https://community.upstox.com/t/historical-availability-retrieval-limit-per-query-for-expired-options-contract/9245 |
| All four expired endpoints without token | **401** `UDAPI100050 "Invalid token used to access API"` (auth checked before plan) | — | — | — | [tested] |
| Rate limits (standard APIs incl. historical) | — | 50/s, 500/min, 2,000/30 min; nothing separate listed for expired APIs | — | — | [docs] rate limits |

## Headlines
- 1-min index/equity/futures/live-option candles are served **without any token** back to **Jan 2022** (≈4y10m today). Nothing before 2022-01 at minute granularity.
- Live option/future 1-min history = the contract's own trading life only (weeklies ≈ 1–2 weeks, monthlies ≈ 1–3 months), OI included.
- Expired-contract intraday option data (the thing a backtest of option buying needs) is **only** via the Expired Instruments API → needs a token **and** Upstox Plus. Docs promise only ~6 months of expiries.

## What the owner must do to unlock expired intraday option candles
1. Have an Upstox trading account; switch it to the **Upstox Plus** plan (app/web → plan settings). Price: Plus T&C says it "can be activated for free initially"; Upstox may make it chargeable later with advance notice. The real cost is the tariff: brokerage **₹30 per executed order** for options (Plus T&C), and a 24-hour cooling-off before switching back. [docs] Plus T&C, https://upstox.com/plus/
2. In the Upstox Developer Console create an app (API key + secret + redirect URL); community reports note a separate "Expired Instruments" scope/toggle may need enabling (https://community.upstox.com/t/expired-instruments-api-toggle-missing-in-developer-console-plus-plan-active/11428).
3. Daily OAuth login (authorization-code flow → `/v2/login/authorization/token`) gives an access token valid until ~03:30 IST next day. Owner exports it as `UPSTOX_ACCESS_TOKEN` in his own shell (never commit it), then re-runs this probe: the expired-endpoint rows should flip from 401 to 200 and show real depth.

## Could not determine
- Actual depth of expired-contract history (6 months per docs vs a possible extension to 2 years) — needs a Plus token.
- Whether Upstox Plus has any recurring fee after the "free initially" period; whether brokerage ₹30 vs Basic's rate matters for the owner.
- Whether unauthenticated historical access is a stable feature or an undocumented leak that may be closed (docs show Bearer header on every sample).
- Rate limits applied to unauthenticated calls (none hit at ≤3 req/s).
- Docs inconsistency on expired-candle intervals (table lists 1/3/5/15/30-min/day; error text lists 1minute/30minute/day/week/month) — untested.
- Whether V3 rejects a >1-month 1-min window (not probed; docs say it errors).
