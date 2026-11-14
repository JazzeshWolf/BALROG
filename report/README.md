# BALROG report

`index.html` is a single self-contained file (Plotly inlined, no network loads, light/dark, phone-width).
Open it in any browser. Every number in it is read from `research/out/`.

## Verdict (plain language)

- **What works for buying:** no entry signal cleared the pre-registered bar. 0 of the registered entry
  hypotheses passed. The only full pass is a veto: inverted term structure on stocks, which does slightly
  worse than baseline. The evidence supports *losing less* through contract choice and friction control.
- **Structural problem:** on indices, buying the standard contract for 3 days is about 0% gross OOS.
  On stocks it is −3.8% gross IS and −0.7% OOS. Net, per 3-day trade: NIFTY about −3%,
  BANKNIFTY −5% to −9%, SENSEX −9% to −10%, stocks −7% to −11%. Friction is the whole loss on
  indices and about two-thirds of it on stocks IS. The loss arrives on day 1.
- **Doesn't work:** dealer positioning, futures build-up (gone OOS), the ADX 25–45 band, breakouts,
  relative strength, buying through earnings, post-earnings drift, and both intraday setups as entries.
- **Real but not tradable:** stock opening-range breakout beats random by +0.6 points (synthetic).
  Pre-earnings IV run-up beats baseline by +12.8 points OOS, but it is negative IS and at 2× spread.
  The V1/V2 cheap-vol filters on stocks lose 4–5 points less and are still negative.
- **Forward-test only:** M4 index gap-and-hold: +13.5% OOS on 141 trades / 75 days, CI includes 0,
  BH q 0.12, deflated-Sharpe probability 0.05. V2 index low IV percentile: +7.7%, driven by 22 trades in 2024,
  no plateau, deflated probability 0.007.
- **Filters:** avoid |delta| < 0.25 (worst cell −17% to −24%). Use |delta| 0.40–0.70. Stocks 21–35 DTE,
  index 7–10. Prefer low required-move ratio and cheap vol on stocks. Round-trip spread ceilings: about 2%
  for stocks and pooled index, 4% NIFTY, 8% BANKNIFTY, 10% SENSEX. Avoid inverted term structure on stocks.
  Exit on underlying invalidation (+1.2 points). No TP/SL grid beat a time exit.
- **Engine:** a loss-avoidance ranker. No score bucket is positive OOS, and the score is not monotone at
  10-point resolution (60–70 dips), so it is *not done*. It reliably flags the worst contracts.
  The 3-tier view is a post-hoc grouping and still needs a forward test.
- **Confidence:** swing tests use real daily prices from 2019 to 2026, but the spread is modelled. The model is
  conservative: real fills were better by +4.2 points on index and +2.0 on stocks, in one 2-month window.
  Intraday results are synthetic. There is nothing on 0–2 DTE or expiry day. 2,221 variants were tried.
- **Forward test must confirm:** real fills, the two index candidates, the 3-tier separation,
  whether NIFTY is near break-even at real spreads, and expiry-day behaviour.

## Hypothesis verdicts (OOS, index pooled / stocks)

| hyp | rule | index | stocks |
|---|---|---|---|
| V1 | VRP cheap → strangle | ANECDOTAL | LESS-BAD |
| V2 | low IV pct → strangle | FAIL (watch) | LESS-BAD |
| V3 | inverted term (veto) | FAIL | VETO-PASS |
| V4 | low & rising VIX | ANECDOTAL | n/a |
| V5 | stocks vs index strangle | FAIL (contrast) | FAIL (contrast) |
| D1–D4 | GEX, wall cross, wall abandonment, pin | FAIL / ANECDOTAL | FAIL |
| E1–E3 | earnings | n/a | FAIL |
| M1, M1b, M2 | futures build-up, covering, ADX | FAIL | FAIL |
| M3 | breakout | ANECDOTAL | FAIL |
| M4 | gap-and-hold | FAIL (watch) | FAIL |
| M5 | relative strength | n/a | FAIL |
| M6 (SYNTH) | opening-range breakout | FAIL | LESS-BAD |
| M7 (SYNTH) | first → last half-hour | FAIL | FAIL |
| M8 | expiry-day entries | FAIL | untestable |
| C1–C4 | delta / DTE / RMR / gamma-theta | C1 supported, rest not | C2–C4 supported, C1 partial |
| X1–X4 | exits vs time exit | X2 B1-leg pass | X2, X4 B1-leg pass |

## Rebuild

```
python report/build_report.py            # writes report/index.html and prints a chart log
```

Missing inputs render as a dashed **PENDING** box. All inputs are currently present. The script exits non-zero if any
chart has no data.

## Sources

- `research/out/p4_baseline.json`: B1 gross/net by class, friction %
- `research/out/p4_vol_dealer_events.json`: V1–V5, D1–D4, E1–E3
- `research/out/p4_momentum.json`: M1–M5, M8, parameter grids
- `research/out/p4_intraday.json`: M6, M7, B2 (SYNTHETIC)
- `research/out/p4_contract_exits.json` (+ `_frozen.json`): C1–C5, X0–X4, theta bleed
- `research/out/p4_synthesis.json`: cross-family BH, deflated Sharpe, trial counts
- `research/out/p4_archive_crosscheck.json`: modelled spread vs real archive bid/ask
- `research/out/p5_calibration.json`: engine calibration and the 3 tiers
- `research/out/p3_spread_eval.json`, `p3_costs.md`: spread model, statutory costs
- `research/out/p1_synth_error.json`: synthetic-pricing error
- `research/01_data_reality.md`: feasibility matrix
- `research/trials_log.csv`: trial ledger
