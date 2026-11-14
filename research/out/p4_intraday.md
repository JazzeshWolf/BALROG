# P4: intraday hypotheses M6 (opening-range breakout), M7 (first half-hour to last half-hour), and the B2 baseline
Scripts: `research/scripts/p4_intraday_stage1.py` and `p4_intraday_stage2.py`. Library: `research/lib/p4_intraday.py`. Tests: `research/tests/test_p4_intraday.py` (11 pass).
Numbers: `research/out/p4_intraday.json`, which holds `stage1` and `stage2` (equity by month, R histograms, heatmap grids).
Trades: `data/bt/intraday/{m6,m6_random,m7,m7_random,b2}.parquet`. Charts: `p4_intraday_{stage1,equity,heatmap}.png`.
Run date: 2026-11-13, on the FULL data/fo build (23:03), which includes SENSEX options from 2023-05. The trials log has 108 rows from this run, with run_ts 2026-11-13T23:17:01.

**Setup.** Real 1-minute underlying for 33 symbols (NIFTY, BANKNIFTY, SENSEX, 30 stocks), special sessions dropped. In-sample 2022–23, out-of-sample 2024–26; SENSEX per §0 (IS 2024, OOS 2025–26).
- **Standard contract.** Nearest expiry with at least 8 calendar days left; stocks use the monthly expiry. The strike is a liquid strike from the previous day (≥500 contracts for indices, ≥50 for stocks, close ≥ ₹2). It is the one whose model |delta| is closest to 0.40 at the entry minute.
- **Option prices are SYNTHETIC.** They use P1 model b: the previous day's OTM smile in log(K/F), Black-76, and the forward F_p·U_m/U_p. Theta runs in **trading time**: the decay from close(p) to close(d) is spread evenly over the 375 session minutes, with none overnight.
- **Fills and R.** Buy at mid plus half-spread and sell at mid minus half-spread (p75; p90 for BANKNIFTY). Full engine costs, 1 lot. R = net P&L ÷ (premium paid + costs).

## Stage 1: real underlying (the required move is SYNTHETIC)
M6 rules: the opening range is the 09:15–09:29 bars. Entry is the first close outside the range, from 09:30 to 15:00. Exit is 15:15 or the first close back through the range midpoint.
The random baseline uses the same joint distribution of (entry minute, holding time) and flips a coin for the side.

| cls / sample | n | hit (move>0) | mean move bp | median bp | required move bp | move ÷ required | mean move σ | % trades clearing |
|---|---|---|---|---|---|---|---|---|
| index IS | 1,020 | .322 | +0.9 | -17.7 | 21.5 | **0.04** | +0.016 | 23.6% |
| index OOS | 1,785 | .347 | +0.7 | -16.8 | 19.8 | **0.04** | +0.013 | 25.3% |
| stock IS | 11,033 | .384 | +3.6 | -31.1 | 41.9 | **0.08** | +0.023 | 26.5% |
| stock OOS | 18,620 | .403 | +5.6 | -31.2 | 42.1 | **0.13** | +0.032 | 28.4% |
| random, stock OOS | 19,594 | .497 | +0.3 | +0.0 | 41.7 | 0.01 | +0.003 | 30.3% |
| random, index OOS | 1,809 | .488 | −0.4 | -0.7 | 19.8 | −0.02 | −0.006 | 28.7% |

- The median move is −27 bp (−0.21σ) across all trades. Midpoint stops end 61% of index trades and 49% of stock trades, with an average loss of −27 bp (index) and −67 bp (stock).
- **M6 − random (σ, day-clustered):** stocks +0.030σ IS [+.009, +.052], +0.029σ OOS [+.012, +.045] (≈ +4.9 bp [+2.1, +7.7] over the requirement); indices −0.013σ IS, +0.020σ OOS, both CIs include 0. Survivors to 15:15 average +45/+75 bp, but that is selected after the fact.
- **Direction and gap.** Up and down breakouts look alike. Breakouts in the gap direction are the best cell: stocks 0.25× the required move (+0.071σ), indices 0.12×. That is still about 4–8× short of breakeven.
- **The bar is not cleared.** Mean underlying move ÷ required move is 0.04–0.13 for every class and sample. The real-underlying breakout effect is real for stocks, but it is about one-tenth of what an option buyer needs.

**M7 Stage 1.** Signal: the sign of the 09:15→09:45 return, traded 15:00→15:25.

| cls / sample | n | corr(r1, r2) | hit | mean signed bp [95% CI] | required move bp, mean / median |
|---|---|---|---|---|---|
| index IS | 1,058 | −0.043 | .486 | −0.46 [−2.29, +1.34] | 14.7 / 11.4 |
| index OOS | 1,807 | +0.038 | .509 | +0.63 [−0.55, +1.84] | 14.3 / 9.5 |
| stock IS | 11,690 | +0.033 | .474 | −0.04 [−1.00, +0.84] | 30.7 / 23.5 |
| stock OOS | 19,447 | +0.023 | .494 | +0.35 [−0.42, +1.14] | 31.3 / 21.3 |

There is no intraday momentum here. The signed return is indistinguishable from 0 and about 1/20 to 1/40 of the cost of the trade.

## Stage 2: SYNTHETIC option P&L (net mean R, day-clustered bootstrap 95% CI, 1 lot)
| SYNTHETIC | sample | index n | index net R [CI] | stock n | stock net R [CI] | 2× spread idx / stk | −10% haircut idx / stk |
|---|---|---|---|---|---|---|---|
| M6 L15 exit 15:15 | IS | 1,050 | −.079 [−.095, −.061] | 11,385 | −.055 [−.061, −.048] | −.129 / −.097 | −.179 / −.155 |
| M6 | OOS | 1,785 | **−.080 [−.095, −.065]** | 18,620 | **−.048 [−.053, −.043]** | −.133 / −.088 | −.180 / −.148 |
| M7 15:00 to 15:25 | IS | 1,058 | −.066 [−.076, −.057] | 11,690 | −.052 [−.055, −.050] | −.118 / −.095 | −.166 / −.152 |
| M7 | OOS | 1,807 | **−.067 [−.073, −.060]** | 19,447 | **−.050 [−.052, −.048]** | −.121 / −.090 | −.167 / −.150 |
| B2, 09:20 to 15:15, call and put | IS | 2,125 | −.096 [−.103, −.089] | 23,744 | −.065 [−.068, −.061] | −.149 / −.106 | — |
| B2 | OOS | 3,619 | −.095 [−.101, −.088] | 39,189 | −.059 [−.062, −.055] | −.147 / −.098 | — |

Gross mean R (mid to mid) is roughly −0.01 to −0.03 throughout. Costs and spread take another 4–7 R-points.

| SYNTHETIC: difference in net R, OOS [CI] | M6 − random | M6 − B2 | M7 − random side | M7 − B2 |
|---|---|---|---|---|
| index | +.002 [−.014, +.019], p=.41 | +.014 [+.002, +.027] | +.005 [−.003, +.013], p=.10 | +.027 [+.018, +.036] |
| stock | **+.006 [+.001, +.012], p=.009** | +.011 [+.007, +.015] | +.000 [−.002, +.002], p=.35 | +.009 [+.006, +.012] |

Read-across: M7 "beats" B2 only because it holds for 25 minutes instead of 355, so it carries less theta. It does not beat a random side.

**Plateau, SYNTHETIC net R OOS** (opening range 10/15/30 min × exit 14:45/15:15):
- index: −.073/−.074, −.077/−.080, −.076/−.078.
- stock: −.047/−.046, −.049/−.048, −.050/−.049.

The result is flat, and flat-negative. The ±20% plateau test cannot pass because no cell is positive.

**Buyer metrics, OOS, SYNTHETIC.** Values are win rate / average win / average loss / profit factor / longest losing streak.

| strategy | index | stock |
|---|---|---|
| M6 | .25 / +.24 / −.19 / 0.43 / 37 | .28 / +.22 / −.15 / 0.56 / 34 |
| M7 | .23 / +.07 / −.11 / 0.20 / 25 | .20 / +.06 / −.08 / 0.18 / 118 |
| B2 | .33 / +.27 / −.27 / 0.48 / 24 | .35 / +.24 / −.22 / 0.58 / 27 |
| M6 random | .29 / +.17 / −.18 / 0.37 / 17 | .31 / +.16 / −.15 / 0.47 / 37 |

- Median worst premium in an M6 trade: −9.9%. Max drawdown in summed R (OOS): M6 −145 (index), −893 (stock); every equity curve falls monotonically. Deflated Sharpe ≈ 0 everywhere (N = 188 log rows).

**Theta convention** (logged `registered=no`). With calendar-time theta, net R is −.052 / −.027 for M6 and −.037 / −.021 for B2 (index / stock, OOS).
- That is 2.5–6 R-points better than trading time and still negative. P1 measured the calendar bias at only +1–3%, so trading time probably over-charges somewhat; no conclusion changes.

## Verdicts (§0 bar, OOS)
- **M6: FAIL (index); "less bad, not an edge" (stock).**
  - Stage 1 does not clear its own bar: the move is 0.04–0.13× the required move.
  - Net R −.080 (index) and −.048 (stock), CIs entirely below 0; negative after the 10% haircut and at 2× spread. For stocks it beats random (+.006 R, p=.009, pre-BH) and B2 (+.011): a mild filter at most, never an entry.
- **M7: FAIL.** There is no first-half-hour → last-half-hour momentum on the real underlying (|corr| < .05) and no advantage over a random side. It loses about 5–7% per trade, as the register predicted ("fails after costs").
- **B2:** buying the standard 0.40-delta contract intraday loses −.095 (index) and −.059 (stock) net R per trade. The intraday buyer's structural bleed is about 6–10% per day held.

## Anomalies and limits
- **Everything here is SYNTHETIC.** The model's ±10% per-trade error is larger than every between-strategy difference reported (+.006 to +.027).
- **Coverage.** SENSEX only 2024 IS / 2025–26 OOS; ETERNAL only from 2025-04 (ZOMATO alias found no rows). Contract-days: 34,879 of ≈39.6k instrument-days. **Hit rate.** M6's hit rate (move > 0) of .32–.40 is below random's .49. This is mechanical: the midpoint stop truncates losers at about −0.3σ to −0.4σ, and winners need the full day.
- **Spread.** It comes from the post-close p75 book. Intraday books are tighter, which mainly affects how bad B2 looks; the 2× spread run is the stress case.
