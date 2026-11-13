# Phase 2 — Hypothesis register

_Written 2026-11-13, **before any backtest was run**. Committed to git before
Phase 4 starts; the commit hash is the pre-registration timestamp. Anything
tested later that is not in this file is logged in `trials_log.csv` as
`registered=no` and can never be promoted to a "survivor" without a fresh
out-of-sample period._

## 0. Common protocol (applies to every hypothesis unless it says otherwise)

**Data.** Swing tests: NSE/BSE bhavcopy (real daily prices). Intraday tests:
1-minute underlying (real) + synthetic option prices — every such result is
labelled **SYNTHETIC**.

**Split.** In-sample (IS) 2019-01-01 → 2023-12-31. Out-of-sample (OOS)
2024-01-01 → 2026-11-12. SENSEX: IS 2024, OOS 2025–2026. Intraday: IS
2022–2023, OOS 2024–2026. Thresholds are either fixed here or expressed as
trailing percentiles (252 trading days, strictly past data). Where a small grid
is listed, the cell is chosen on IS only and frozen; OOS is run once.

**Decision time and fills.** Features use information up to the close of day
*t*. Entry at the day-*t* closing price of the contract; exit at the closing
price of day *t+h*. Sensitivity run: entry at day *t+1* open. Buy at
close + half-spread, sell at close − half-spread, where half-spread comes from
the spread model fitted on the archive (class × |delta| × DTE × premium bucket,
75th percentile). Sensitivity run at 2× spread. Full statutory costs and
brokerage from `engine/costs.py`, dated schedule. One lot per trade.

**Standard contract** (so entry signals are not confounded with contract
choice): expiry = nearest with ≥ 7 calendar days to expiry (stocks: nearest
monthly only — if it has < 7 days, no trade); strike = the traded strike whose
computed |delta| is closest to 0.40; contract must have traded that day with
volume ≥ 500 contracts (index) or ≥ 50 contracts (stock) and premium ≥ ₹2.
"Strangle" = the standard call + the standard put. Default hold *h* = 3 trading
days, time exit only. Never hold into expiry day (exit the day before).

**Position rule.** At most one open position per instrument × hypothesis × side.

**Trade return.** R = (exit − entry − spread − costs) / (entry premium paid
incl. costs). Reported gross and net, in ₹ per lot and in R.

**Baselines.** (B1) *random entry*: same instrument class, same standard
contract, same hold, entered on every eligible day (the unconditional
population); the test statistic is signal-days minus all-days. (B2) "buy ATM at
the open, sell at the close" for intraday.

**A hypothesis PASSES only if all hold out-of-sample:**
1. net mean R > 0, with a day-clustered bootstrap 95% CI excluding 0;
2. net mean R exceeds B1 with p < 0.05 after Benjamini–Hochberg across all
   registered hypotheses (deflated Sharpe also reported, N = total rows in `trials_log.csv`);
3. sign and rough size agree with IS (OOS mean ≥ 50% of IS mean, same sign);
4. plateau: moving each threshold ±20% keeps net mean R > 0 and ≥ 50% of the central value;
5. ≥ 100 trades on ≥ 40 distinct entry days (else "anecdotal", cannot pass);
6. survives 2× spread with net mean R ≥ 0.

A **veto** hypothesis (expected sign negative) passes if the signal subset is
worse than B1 with the same statistical bar; it earns a penalty in the engine.
A hypothesis that beats B1 but has net mean R ≤ 0 is recorded as
**"less bad, not an edge"** and may only be used as a filter/penalty.

**Feature definitions.**
- IV: Black-76 implied vol from the contract close and the same-expiry futures
  close (parity forward where no future). ATM IV = interpolated at forward.
- RV forecast: HAR-RV on daily Parkinson variance (1/5/22-day lags), fitted on
  an expanding window of past data only, per class.
- VRP ratio = ATM IV / HAR forecast (annualised, horizon-matched).
- IV percentile = rank of ATM IV within its trailing 252 days.
- Net GEX = Σ_strikes γ(K)·OI(K)·S²·0.01 × (+1 calls, −1 puts), nearest two
  expiries, normalised by 20-day average traded value. Gamma flip = spot level
  where net GEX changes sign.
- Call wall / put wall = strike with max call / put OI in the nearest expiry,
  within ±10% of spot.
- Futures structure: sign(Δprice) × sign(ΔOI) of the near future, each
  |Δ| ≥ 0.5σ of its own 60-day distribution.
- σ_daily = HAR forecast daily vol; required-move ratio (RMR) = (breakeven
  distance from spot, incl. costs) / (σ_daily·√h·spot).

## 1. Volatility pricing

| ID | Mechanism | Rule (entry → exit) | Expected | Pass metric |
|---|---|---|---|---|
| **V1** | Options are cheap when implied < expected realized | VRP ratio < 0.90 → buy strangle, hold 5d. Grid: {0.80, 0.90, 1.00} | + | §0 |
| **V2** | Low IV mean-reverts up; vega + cheap gamma | IV percentile < 20 → buy strangle, hold 5d. Grid: {10, 20, 30} | + (weak prior) | §0 |
| **V3** | Inverted term structure = stress already priced; vol mean-reverts down on the buyer | near IV − next IV > 0 (inverted) → strangle, 3d | − (veto) | veto bar |
| **V4** | Vol expanding from a low base persists (clustering) | Index: VIX in bottom tercile (252d) AND 5-day VIX change > 0 → strangle 5d | + | §0 |
| **V5** | Index variance is richly priced (correlation premium); single-stock variance barely is | Unconditional strangle, 5d, every eligible day: stocks vs indices | stocks > indices; both ≤ 0 | difference, day-clustered CI excludes 0 |

## 2. Dealer positioning

| ID | Mechanism | Rule | Expected | Pass |
|---|---|---|---|---|
| **D1** | Short-gamma dealers amplify moves | net GEX in bottom quintile (252d) → strangle, 3d. Grid: {10, 20, 30} pct | + | §0 |
| **D2** | A wall break forces short-call hedging / covering | Spot closes above call wall after closing below it the day before → buy call, 2d. Mirror: below put wall → buy put | + | §0, vs random same-side buys |
| **D3** | Writers abandoning a wall precede the break | Spot within 1% below call wall AND wall-strike OI down ≥ 10% d/d → buy call, 3d (mirror for put wall). Grid: {5, 10, 15}% | + | §0 |
| **D4** | Positive-gamma pinning kills movement | net GEX in top quintile AND spot within 0.5·σ_daily of the max-OI strike AND DTE ≤ 5 → strangle to the day before expiry | − (veto) | veto bar |

## 3. Momentum / structure

| ID | Mechanism | Rule | Expected | Pass |
|---|---|---|---|---|
| **M1** | Fresh positioning has follow-through | Futures long buildup → buy call 3d; short buildup → buy put 3d | + | §0 |
| **M1b** | Covering/unwinding moves are exhaustion | short covering → call; long unwinding → put; 3d | ≤ M1 | reported beside M1 |
| **M2** | Trending regimes persist; extreme ADX is late (re-test of Divya Drishti, not inherited) | ADX(14) in [25, 45] and +DI > −DI → call 3d (mirror put). Compared with ADX < 25 and ADX > 45. Grid on band edges ±5 | + inside band | §0 |
| **M3** | Range expansion breakouts | Close > 20-day high and day range > 1.5× 20-day ATR → call 3d (mirror put). Grid: lookback {10, 20, 40} | + | §0 |
| **M4** | Gaps that hold continue | Open gap > 1·σ_daily and close beyond the open in the gap direction → buy in gap direction, 2d | + | §0 |
| **M5** | Relative strength persists | Stock 20d return minus β·NIFTY in top decile and NIFTY > 20-DMA → call 5d; bottom decile and NIFTY < 20-DMA → put 5d | + | §0 |
| **M6** (SYNTHETIC) | Opening-range breakout | 15-min opening range; first 1-min close beyond it → buy standard contract (DTE ≥ 8), exit 15:15 or when the underlying re-crosses the range midpoint. Stage 1 on the real underlying: mean move from entry to exit in units of required move | + (weak) | Stage 1 must show mean underlying move > required move; then §0 with a 10% return-error haircut |
| **M7** (SYNTHETIC) | Intraday momentum (first half-hour predicts last) | At 15:00 buy in the direction of the 09:15–09:45 return, exit 15:25 | fails after costs | §0 |
| **M8** | Expiry-day swing entries are noise | Any M-signal whose entry day is an expiry day of that instrument vs not | − on expiry day | difference reported |

## 4. Contract selection (run on the unconditional population and again on survivors)

| ID | Mechanism | Rule | Expected | Pass |
|---|---|---|---|---|
| **C1** | Far-OTM options carry a lottery premium and the heaviest cost load | Same entries, strike at \|delta\| ∈ {0.15, 0.25, 0.40, 0.55, 0.70} | net R rises with \|delta\|; 0.15 worst | monotone trend, top vs bottom CI excludes 0 |
| **C2** | Theta load per day falls with DTE; gamma falls too | Same entries, expiry bucket DTE {7–10, 11–20, 21–35, 36+} | interior optimum 11–35 | bucket differences, plateau |
| **C3** | A trade needing a > 1σ move rarely pays | Terciles of required-move ratio | low RMR > high RMR | top vs bottom CI excludes 0 |
| **C4** | Gamma per unit of theta is the buyer's exchange rate | Terciles of gamma/\|theta\| (per ₹ premium) | high > low | as C3 |
| **C5** | Liquidity floor (descriptive, not a hypothesis) | Net R by volume and modelled-spread buckets | finds the floor | used to set universe; logged |

## 5. Exits (applied to B1 entries and to each surviving entry signal)

Daily contract high/low are used to detect intraday touches; when a TP and an
SL are both touched in one day the SL is assumed first.

| ID | Rule | Expected |
|---|---|---|
| **X0** | Time exit only at *h* ∈ {1, 2, 3, 5} | reference |
| **X1** | Premium TP/SL: TP ∈ {+30, +50, +100}% × SL ∈ {−25, −40, −60}% + time stop | cuts variance, not expectancy |
| **X2** | Underlying invalidation: exit when the underlying closes back through the entry-day extreme against the trade (or 1·σ_daily against), + time stop | ≥ X1 in expectancy |
| **X3** | Trailing: after +30%, exit on giving back one third of peak gain | improves avg win / avg loss |
| **X4** | Fast time-stop: exit at day 1 close if not in profit | lowers theta share of losses; higher expectancy than X0 |

Pass: an exit rule is adopted only if it raises net expectancy over X0 on OOS
for the surviving entries **and** on B1 entries (same sign), with a plateau
across its grid. Otherwise X0 with the best-supported *h* stays.

## 6. Events

| ID | Mechanism | Rule | Expected | Pass |
|---|---|---|---|---|
| **E1** | IV crush exceeds the realized earnings move | Buy strangle at the last close before results, exit first close after | − (veto) | veto bar |
| **E2** | Pre-event IV run-up | Buy strangle 5 trading days before results, exit the last close before results | + gross; net ≤ 0 | §0 |
| **E3** | Directional drift after results | After results day, buy in the direction of the results-day move if \|move\| > 2·σ_daily, hold 3d | + (weak) | §0 |

E1–E3 depend on a multi-year results-date history; if none can be sourced they
run on the archive-window events only and are reported as anecdotal.

## 7. Trial accounting

Registered: 24 hypotheses. Pre-declared grid cells ≈ 70. Every run — including
sensitivity runs, the 2× spread runs and anything exploratory — appends a row
to `research/trials_log.csv` with: id, variant, registered (yes/no), sample
(IS/OOS), class, n_trades, n_days, gross mean R, net mean R, CI, p vs B1,
verdict. The deflated-Sharpe haircut uses the full row count.

## 8. What would make me say "there is no edge"

If no entry hypothesis passes §0, the report says so, and the engine becomes a
**loss-avoidance scorer** (vetoes + contract selection that minimise the
buyer's structural bleed) rather than an edge claim. That outcome is acceptable.
