# Phase 1 — Data reality

_2026-11-13. Every number below comes from a script in `research/scripts/p1_*`
with its detailed output in `research/out/p1_*`. Tags: **[T]** tested from this
machine, **[D]** from vendor docs only._

## Bottom line

1. **Swing (1–5 day) backtests are feasible on real prices over many years** —
   public NSE/BSE bhavcopy is reachable from here, no GitHub Action needed.
2. **Intraday backtests on real option prices are not possible with what we
   have.** Free sources stop at live contracts (days to weeks of history).
   Synthetic prices are usable only for |delta| ≥ 0.25 and DTE ≥ 8, and are
   **unusable for 0–2 DTE and expiry day** — exactly where most intraday index
   option buying happens.
3. **The 54-day archive is really 40 usable sessions** and cannot carry a
   backtest alone. Its job becomes calibration (spreads), validation and the
   engine's regression fixture.

## Source by source

| # | Source | What it actually gives | Cost | Pulled from here? |
|---|---|---|---|---|
| 1 | **xerxes-eod-archive** | 54 folders → **51 real sessions** (3 are holiday re-publishes) → **40 with bid/ask** (fields absent before mid-Sep). **13 trading days missing** (26 Aug → 11 Sep, not recorded in `missed-days.json`). 213 instruments (157 stocks at start, 210 at end). 990k chain rows, truncated around the money (indices ~87–114% of spot, stocks ~82–122%). 4 monthly cycles, 13 NIFTY / 12 SENSEX weekly cycles. One calm regime: VIX 10.4–17.6, NIFTY +3.4%. | free | **[T]** yes, cached to `data/archive/*.parquet` |
| 2 | **Upstox** | 1-min **underlying** (index, stock, futures) back to **2022-01-03, no token** [T]. 1-min **live** option contracts with OI over their own life only (5–48 days) [T]. **Expired** option candles: separate API, needs token + **Upstox Plus** plan, ~6 months of expiries [D]; returns 401 here [T]. | Plus: free to activate "initially", ₹30/executed options order [D] | underlying + live: yes. Expired: no (no token, plan unknown) |
| 3 | **Kite Connect** | Minute data from ~2015 for live instruments; **expired option contracts are not retrievable** [D]. The connected Kite app returns "Insufficient permission" on every historical call [T]. | ₹500/mo | no |
| 4 | **NSE/BSE bhavcopy** | Daily OHLC, settle, OI, volume for every contract. NIFTY options usable from 2010, **stock options from ~2018** (better 2020+), **SENSEX from 2023-05** (realistically 2024+). Index OHLC + India VIX and participant-wise OI from 2012. **No bid/ask, no IV.** 61% of option rows today have zero volume and a stale close (90% in 2016). No lot-size history before 2024 (rebuildable from futures turnover). | free | **[T]** yes. 2019→2026 ≈ 1.9 GB, 9,750 files, ~4.2 h at 1 req/s; no blocking seen |
| 5 | **Synthetic intraday** (1-min underlying + EOD IV → Black-76, sticky-moneyness) | Measured against real quotes: median price error 5–7% (index) / 4–5% (stock); synthetic-vs-real trade return correlation ≈ 0.97, sign agreement ≈ 0.90–0.93; **error exceeds the real move in 14–26% of cases**. Understates near-expiry OTM returns by 8–18%. No 0–2 DTE data could be tested at all. | free | **[T]** yes (40 archive days; 29 live days, 132 contracts) |
| + | **Paid alternatives** (not tested) | **Fyers Prime**: fixed-strike 1-min with OI for expired contracts, NSE from Oct 2018, BSE from Aug 2023. **Dhan Data API**: 5 years 1-min with OI+IV, but strikes only relative to ATM. **ICICI Breeze**: ~3 years 1-min, free with an ICICI demat account. No cheap source of historical bid/ask exists. | ₹499+GST/mo; ₹499/mo; free | [D] only |

## Archive facts that change how it may be used

- Near-the-money spread, nearest expiry, median (ask−bid)/mid: **NIFTY 0.66%,
  SENSEX 1.17%, BANKNIFTY 1.51%, stocks 2.6%** (IQR 2.1–3.6%). The **second
  stock expiry is 8–21%** — effectively untradable for a buyer.
- `ltp` lies outside [bid, ask] on ~34% of quoted rows, and 15–25% of rows show
  an `ltp` with zero volume. The stored `iv` is LTP-based and misses the
  mid-implied IV by a median 6.5%. **BALROG re-derives IV from the mid; it
  never uses `ltp` or the stored `iv`/`delta` as-is.**
- Snapshots are stamped 17:22 IST (stocks) / 18:46 IST (indices), i.e.
  post-close book quotes, wider than intraday ones.
- Four stock files froze mid-window (DALBHARAT, ICICIPRULI, BAJAJHLDNG, SAIL).
- Verified from data: NIFTY weekly = **Tuesday**; SENSEX weekly = **Thursday**;
  BANKNIFTY and stocks = monthly, last Tuesday; Monday expiries are holiday
  shifts. Lots: NIFTY 65, BANKNIFTY 30, SENSEX 20; 9 stocks changed lot on 28 Oct.
- 287 earnings/results events across 207 stocks fall inside the window.
- `xerxes.db` is not in the repo; `outcomes.jsonl` is sell-side only. Neither is needed.

## Feasibility matrix (style × instrument → best source)

| | NIFTY | BANKNIFTY | SENSEX | F&O stocks |
|---|---|---|---|---|
| **Swing 1–5 d**, close-to-close | **Feasible** — bhavcopy 2019+ (weeklies), traded strikes only | **Feasible** — bhavcopy 2019+; monthly-only since Nov 2024 | **Feasible, short** — BSE bhavcopy 2024+ | **Feasible** — bhavcopy 2020+, nearest expiry only |
| **Intraday, DTE ≥ 8, \|delta\| ≥ 0.25** | Approximate (synthetic, 2022+) | Approximate (synthetic, 2022+) | Approximate (synthetic, 2024+) | Approximate (synthetic, 2022+); best cell is \|delta\| ≥ 0.40 |
| **Intraday, DTE 3–7** | Approximate only for \|delta\| ≥ 0.40 | same | same | same |
| **Intraday, 0–2 DTE / expiry day** | **Impossible** without bought data | **Impossible** | **Impossible** | **Impossible** |
| **Event (earnings) swing** | n/a | n/a | n/a | Feasible if a multi-year results-date history can be sourced; otherwise 287 events / ~2 seasons (anecdotal) |

"Feasible" carries two standing approximations: **spread is modelled, not
observed** (fitted on the 40 archive sessions by class × delta × DTE × premium,
applied at a conservative percentile), and **IV is computed** from close +
underlying. Both will be labelled in every result.

## Recommended scope

**A. Swing on real prices — the main line.** Download bhavcopy 2019-01 →
2026-11 (NSE F&O + cash + index/VIX + participant OI; BSE F&O 2024+). Trade only
rows that actually traded, with a liquidity floor. Walk-forward with the last
~2 years held out. Dealer-positioning, vol-pricing, structure, contract-selection
and exit hypotheses are all testable here: OI by strike, futures OI and
computed IV exist daily.

**B. Intraday on synthetic prices — secondary, labelled synthetic.** 2022+,
1-minute underlying. Restricted to DTE ≥ 8 with |delta| ≥ 0.25 (DTE 3–7 only at
|delta| ≥ 0.40). Momentum signals are first tested on the *underlying* (real
data: does the move exceed the required move?), then translated to option P&L
with a 3–10% per-trade error haircut. **No claim will be made about 0–2 DTE.**

**C. Archive — calibration and validation only.** Spread model, synthetic-error
bounds, cross-check of bhavcopy-based P&L against bid/ask P&L for Sep–Nov 2026,
and the engine's regression fixture.

**D. Optional, your call.** One month of **Fyers Prime** (or an Upstox Plus
token) would replace synthetic intraday with real 1-minute option candles and
is the only way to say anything about expiry-day buying.

## Risks to the conclusions

- Spread model rests on 40 sessions in one calm regime; real spreads in
  stressed markets are wider. Mitigation: conservative percentile + a
  sensitivity run at 2× spread.
- Bhavcopy closes of thinly traded strikes are noisy even when volume > 0.
- Upstox serving history without a token is undocumented and could stop;
  pull once and cache.
- Synthetic validation used archive mid-IVs; historically the IV input will be
  bhavcopy-close IV, which is noisier. Untested — true error is likely higher.
- Earnings-date history beyond the archive window has no confirmed source yet.
