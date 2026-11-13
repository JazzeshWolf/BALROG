# P3 — Option bid/ask spread model for bhavcopy backtests
Code: `research/scripts/p3_fit_spread.py` → `engine/spread_model.json` + `engine/spread.py` (stdlib only; `engine/test_spread.py`, 75 tests pass). Plot: `p3_spread_model.png`. API: `half_spread(cls, symbol, abs_delta, dte_cal, premium, volume_contracts=None, expiry_rank=1, pct=75)` → ₹ half-spread; round trip = 2 × half.

**Data.** 668k two-sided quotes, 40 sessions (2026-09-15 → 2026-11-12), usable and non-duplicate snapshots only, dte ≥ 1. Target rel = (ask−bid)/mid, with the observed spread floored at one tick. |Δ| is recomputed by Black-76 from the mid using the parity forward. Untraded wings with no IV get |Δ| 0 or 1. Groups: NIFTY, BANKNIFTY, SENSEX, and stocks in 3 tiers by median daily option traded value (70/70/69 names; unknown stock → tier 2, unknown index → class-level cells). Predictors are bhavcopy fields only: premium, |Δ|, calendar DTE, expiry rank and contracts traded.

**Candidates** (fit on sessions 1–28, test on 29–40):
- **bucket**: empirical p50/p75/p90 per cell g × |Δ|{.05,.15,.25,.40,.60,.80} × DTE{1, 2–6, 7–19, 20–44, 45+} × premium{<2, 2–5, 5–20, 20–50, 50–200, >200} × rank{1, 2, 3+}. Falls back gdtpr→gdpr→gtpr→gpr→gp→cp→c when n<50. Smoothed by PAVA so it is non-increasing in premium.
- **param**: per-group location-scale model, log rel = Xβ + exp(Xγ)·z_q, with X = [log P, log²P, |Δ|, |Δ|², log DTE, log1p(contracts), rank2, rank3] and z_q the empirical residual quantiles.
- **ens**: geometric mean of the bucket and param quantiles. Selection rule: lowest OOS pinball among candidates with |cov75 − 75%| ≤ 2.5 pp. Param alone has the best pinball but under-covers (cov75 71.3%; BANKNIFTY 59%), so **ens is chosen**. It is refit on all 40 sessions.

## Out-of-sample (last 12 sessions, 191k rows; share of true spread ≤ predicted quantile)
| model | pinball (mean q50/75/90) | cov p50 | cov p75 | cov p90 |
|---|---|---|---|---|
| bucket | 0.0670 | 55.1% | 76.0% | 89.6% |
| param | 0.0551 | 47.6% | 71.3% | 87.6% |
| **ens (chosen)** | 0.0570 | 52.3% | **75.1%** | 90.1% |

ens cov p75 by group: NIFTY 76.2% · BANKNIFTY **61.9%** · SENSEX 73.5% · STK1 76.4% · STK2 74.2% · STK3 75.9%. BANKNIFTY is under-covered: it has only 4 monthly cycles, and the misses are mostly at rank 2–3 and DTE ≥ 20. **Use `pct=90` for BANKNIFTY**, which gives 79% OOS coverage. Volume matters: param, which uses traded contracts, beats the volume-blind bucket by 18% on pinball. Pass the bhavcopy volume when you have it. Otherwise the cell's median volume is used.

## Empirical rel spread p50/p75 (%), nearest expiry, all 40 sessions (— = n<50)
| class | abs(Δ) | DTE 1 | 2–6 | 7–19 | 20–44 |
|---|---|---|---|---|---|
| NIFTY | .05–.15 | — | 2.8/3.8 | 2.6/3.5 | — |
| NIFTY | .15–.25 | — | 1.3/1.8 | — | — |
| NIFTY | .25–.40 | — | 0.9/1.2 | 1.3/1.7 | — |
| NIFTY | .40–.60 | — | 0.6/0.8 | 1.0/1.4 | — |
| BANKNIFTY | .05–.15 | — | 4.7/7.7 | 5.8/12.2 | 4.4/8.1 |
| BANKNIFTY | .15–.25 | — | — | 2.0/2.9 | 1.5/1.9 |
| BANKNIFTY | .25–.40 | — | — | 1.7/2.7 | 1.2/2.1 |
| BANKNIFTY | .40–.60 | — | 1.8/2.2 | 1.7/2.3 | 1.3/1.8 |
| SENSEX | .05–.15 | 7.4/10.2 | 5.9/8.9 | 7.1/9.6 | — |
| SENSEX | .15–.25 | 1.7/2.6 | 3.1/4.7 | 4.2/5.8 | — |
| SENSEX | .25–.40 | 1.4/1.8 | 1.7/2.5 | 2.6/4.2 | — |
| SENSEX | .40–.60 | 1.1/1.5 | 1.2/1.7 | 2.1/2.9 | — |
| stocks | .05–.15 | 27.7/61.5 | 11.1/22.2 | 7.2/13.9 | 6.7/14.3 |
| stocks | .15–.25 | 22.2/46.5 | 7.3/11.8 | 4.4/6.9 | 3.9/6.5 |
| stocks | .25–.40 | 19.4/38.8 | 5.9/9.3 | 3.4/5.3 | 3.1/5.1 |
| stocks | .40–.60 | 18.9/43.0 | 5.1/8.8 | 2.7/4.4 | 2.4/4.5 |

## Round-trip spread cost, % of premium: model p50 / p75 [empirical p50/p75]
Each row is a typical nearest-expiry trade at that premium (median |Δ| and DTE in the data), with volume unknown.
| trade | NIFTY (Δ .39/.10/.03, 5d) | BANKNIFTY (Δ .10/.02/.01, 7–17d) | SENSEX (Δ .15/.03/.01, 3d) |
|---|---|---|---|
| index ₹100 | 0.6 / 0.8 [0.8/1.0] | 4.2 / 8.3 [3.6/6.7] | 2.5 / 4.1 [3.6/6.1] |
| index ₹20 | 1.8 / 2.7 [2.3/3.2] | 12.8 / 24.4 [16.5/35.2] | 7.1 / 12.1 [10.0/15.7] |
| index ₹5 | 3.6 / 5.5 [5.0/6.8] | 29.4 / 57.7 [39.8/97.6] | 12.6 / 21.5 [19.8/42.9] |

| trade | stock tier 1 | tier 2 | tier 3 |
|---|---|---|---|
| stock ₹20 (Δ≈.3, 18d) | 2.1 / 3.2 [2.2/3.6] | 3.4 / 5.3 [3.6/6.6] | 4.9 / 9.1 [4.8/13.1] |
| stock ₹5 (Δ≈.13, 16d) | 3.5 / 5.7 [3.3/5.9] | 5.9 / 10.2 [4.5/8.3] | 9.3 / 19.2 [6.1/18.0] |
| stock ₹2 (Δ≈.07, 14d) | 4.6 / 6.8 [5.6/13.9] | 7.6 / 12.5 [8.0/28.6] | 10.3 / 21.2 [9.2/63.6] |

## Limits
- For cheap index wings and ₹2 stocks, the model with default volume sits below the empirical p75 because the empirical figures include zero-volume quotes. For those, pass the actual volume or use `pct=90`.
- **Post-close quotes** (17:20–18:45 IST) are wider than intraday books, so the model is conservative for intraday fills.
- **One calm regime and 40 sessions**, with few independent cycles (BANKNIFTY 4, stock monthlies 3–4). There are no event or high-VIX days in sample, and spreads under stress will be wider.
- Premium here is the quote mid. Backtests feed the bhavcopy close/settle, which can be stale (ltp is outside bid/ask in 34% of rows).
- At fixed |Δ| the stock spread is roughly flat for ₹2–20, so "cheaper → wider" holds only weakly (5% tolerance) there. It is strict along realistic trades and below ₹2. Relative spread rises again for deep-ITM contracts.
- Same-day volume is assumed known, which holds for EOD decisions at the close.
