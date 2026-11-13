# P3: Option-buyer transaction costs (NSE/BSE), Jan 2019 to Nov 2026

Code: `engine/costs.py` (`round_trip_cost`, dated `SCHEDULE`). Tests: `engine/test_costs.py` (11 pass).
Scope: explicit charges only. **Bid-ask spread and slippage are excluded.** For cheap options they are usually larger than all of the charges below.

## Dated schedule (the rate applies from the date until the next row; % of premium turnover)
| Component | From | Rate | Source | Conf |
|---|---|---|---|---|
| STT, option sale (seller) | 2019-01-01 | 0.05% | NSE/FATAX/56235 (prior-rate column) | high |
| | 2023-04-01 | 0.0625% | NSE/FATAX/56235 (Finance Act 2023) | high |
| | 2024-10-01 | 0.10% | NSE/FATAX/63809 (Finance (No.2) Act 2024) | high |
| | **2026-04-01** | **0.15%** | NSE/FATAX/73524 (Finance Act 2026, Budget 2026) | high |
| STT, exercised (ITM at expiry, paid by holder) | 2019-01-01 | 0.125% of **settlement price** x qty | NSE/FATAX/41919 | high |
| | 2019-09-01 | 0.125% of **intrinsic** x qty | NSE/FATAX/41919 | high |
| | **2026-04-01** | **0.15%** of intrinsic | NSE/FATAX/73524 | high |
| NSE options transaction charge | 2019-01-01 | 0.050% (Rs50/lakh, Rs3-100cr slab) | NSE/FA/46730 ("existing" column) | medium |
| | 2021-01-01 | 0.053% | NSE/FA/46730 | high |
| | 2023-04-01 | 0.050% | NSE/FA/56129 | high |
| | 2024-10-01 | 0.03503% flat ("true to label") | NSE/FA/64323, recapped in NSE/FA/73061 | high |
| | **2026-03-01** | **0.0355299%** (Rs3,552.99/cr) | NSE/FA/73061 | high |
| NSE IPFT | 2019-01-01 | Rs0.01/cr | NSE/FA/56129 | high |
| | 2023-04-01 | Rs50/cr (0.0005%) | NSE/FA/56129 | high |
| | **2026-03-01** | **Rs0.01/cr** (txn+IPFT total unchanged at Rs3,553/cr) | NSE/FA/73061 | high |
| BSE SENSEX/BANKEX options transaction charge | 2019-01-01 | ~0.0225% (inferred from the +24-32% May-2024 hike) | Business Standard 2-May-2024 | **low** |
| | 2024-05-13 | 0.0295% (top slab of Rs500-2,950/cr) | Business Standard / BQ Prime | medium |
| | **2024-10-01** | **0.0325%** (Rs3,250/cr flat) | AngelOne/BS Sep-2024; zerodha.com/charges & Upstox (Nov-2026) | high |
| SEBI turnover fee | 2019-01-01 | Rs15/cr | SEBI board 1-Mar-2019 (cut to Rs10) | medium |
| | 2019-06-01 | Rs10/cr (**effective date assumed**) | same | low |
| | 2020-06-01 | Rs5/cr (COVID 50% relief) | SEBI PR 27-Apr-2020 | medium |
| | **2021-04-01** | **Rs10/cr** | zerodha.com/charges | high |
| Stamp duty (buy only) | 2019-01-01 | ~0.002% (state-wise; approximation) | pre-reform | **low** |
| | **2020-07-01** | **0.003%** | Stamp Act amendment (Finance Act 2019); zerodha.com/charges | high |
| GST | **2017-07-01** | **18%** of (brokerage + txn + IPFT + SEBI fee) | zerodha.com/charges | high |
| Brokerage | n/a | Rs20/executed order (Zerodha, Upstox); Upstox Plus up to Rs30 | zerodha.com/charges, upstox.com/brokerage-charges | high |

Full URLs are in the `SCHEDULE` rows: `https://nsearchives.nseindia.com/content/circulars/<REF>.pdf`.
Current state, checked against zerodha.com/charges and upstox.com/brokerage-charges on 13-Nov-2026: NSE 0.03553% (including IPFT), BSE 0.0325%, STT 0.15%, SEBI Rs10/cr, stamp 0.003%. Both broker pages match the circulars.

## How the exit is charged
- **Sell before expiry:** two orders; STT on the sell premium.
- **Expires OTM (sell_premium=0):** one order only, with no sell-side charges.
- **Index option ITM at expiry (cash settled):** auto-exercised; STT 0.15% x intrinsic x qty. Zerodha also charges brokerage on exercised contracts. Before Sep-2019, STT was charged on the full settlement price, which is a large trap and is modelled. Squaring off before expiry costs 0.15% x premium instead, which is usually cheaper for deep ITM.
- **Stock option ITM at expiry is PHYSICALLY settled** (all stock F&O since Oct-2019). A long call means taking delivery of a full lot at the strike, and a long put means delivering shares. Settling needs the full notional in cash or shares. Delivery STT is about 0.1% of contract value on each side, plus delivery-type brokerage (forum reports say Zerodha charges 0.25% of value; **not confirmed officially**) and delivery stamp duty. Brokers also raise margins and may force square-off in expiry week. `round_trip_cost` raises an error for this case. **BALROG must exit stock options before expiry.**

## Worked examples (13-Nov-2026 rates, Rs20/order, NSE; NIFTY lot 65 assumed)
| Trade | Brokerage | Txn | STT | GST | Stamp+SEBI | **Total** | % of buy premium | % of gross profit |
|---|---|---|---|---|---|---|---|---|
| NIFTY 1 lot, 100 -> 110 | 40.00 | 4.85 | 10.72 | 8.08 | 0.21 | **63.86** | **0.98%** | 9.8% of Rs650 |
| NIFTY 1 lot, 10 -> 11 | 40.00 | 0.48 | 1.07 | 7.29 | 0.02 | **48.87** | **7.52%** | **75%** of Rs65 |
| Stock option lot 1500, 5 -> 5.5 | 40.00 | 5.60 | 12.38 | 8.21 | 0.25 | **66.42** | **0.89%** | 8.9% of Rs750 |
| NIFTY 10 -> expires worthless | 20.00 | 0.23 | 0 | 3.64 | 0.02 | 23.89 | 3.68% | n/a |
| SENSEX lot 20, 10 -> 11 | 40.00 | 0.14 | 0.33 | 7.22 | 0.01 | 47.70 | **23.9%** | 238% |

Takeaways
- **Brokerage plus its GST (Rs47.20 per round trip) is the main cost.** Ad-valorem charges total only about 0.2-0.3% of turnover in 2026; STT on the sell side is the largest of them.
- Cost as % of premium scales roughly as 47.2 / (premium x qty). Cheap, low-notional trades (Rs10 NIFTY, small SENSEX lots) need about a 7.5% (NIFTY) or 24% (SENSEX) move in premium just to break even. Size so that each order's premium notional is at least Rs5-10k, or use more lots per order.
- Regime changes matter little for cheap options. For example, NIFTY 10 -> 11 cost 48.43 in 2022 vs 48.87 now. For expensive options, STT tripled from 0.05% to 0.15% (2019 to 2026), which adds about 0.1% of sell premium.

## Unconfirmed / caveats
- BSE options charges before 13-May-2024 (low confidence, inferred), and any BSE revision after Oct-2024. No 2025-26 BSE circular was found, but current broker pages still show 0.0325%.
- SEBI Rs15 -> Rs10 effective date (assumed 1-Jun-2019). Pre-Jul-2020 stamp duty was state-wise (approximated at 0.002%).
- Before Oct-2024, NSE options charges were volume slabs. The schedule uses the first slab above Rs3cr (Rs50/53 per lakh), which is the rate retail brokers passed on.
- No broker-calculator example with stated numbers was found for a cross-check. The tests are hand-computed only.
- NIFTY lot 65 is the current lot size assumed here; `lot_size` is an input to the function.
