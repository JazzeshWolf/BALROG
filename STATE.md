# BALROG — state

_Last updated: 2026-11-14_

## Current phase

**Phases 1–5 complete (research v1).** Branch `research/v1`, PR to `main`.
Next session (out of scope here): live alerts, dashboard, forward-testing.

## Headline result

**No entry hypothesis cleared the pre-registered bar.** Unconditional option
buying is ≈ 0 gross and −3% (NIFTY) to −11% (stocks) net per 3-day trade;
friction is the loss. The engine is a **loss-avoidance ranker**: no score
bucket is profitable out-of-sample and the score is **not monotone at 10-point
resolution**, so by the project's own rule it is *not finished* — it reliably
flags the worst contracts only. Read `report/index.html` §1 first.

## Decisions made

| Date | Decision | Why |
|---|---|---|
| 2026-11-13 | Research code and engine in Python | pandas stack present; engine reuses `costs.py`/`spread.py`; accepts a Xerxes snapshot JSON as-is |
| 2026-11-13 | External repos cloned read-only under `_ext/` (gitignored) | keeps private archive data out of BALROG history |
| 2026-11-13 | Scope: swing on bhavcopy (main), intraday synthetic (secondary), archive for calibration only | Phase 1 findings; approved by owner |
| 2026-11-13 | IS 2019–2023 / OOS 2024–2026 (SENSEX IS 2024); hypotheses pre-registered in commit `77a4539` | no tuning on test |
| 2026-11-13 | BANKNIFTY uses the p90 modelled spread; others p75 | p75 under-covered BANKNIFTY out-of-sample (62%) |
| 2026-11-14 | Stock options: NSE only (BSE-listed stock options dropped) | they duplicate NSE symbols |
| 2026-11-14 | D2 primary = cross of the *previous day's* wall | the wall strike itself moves day to day |
| 2026-11-14 | Deflated-Sharpe N = distinct strategy variants (2,221), BH over 51 primary tests | honest count of what was tried |
| 2026-11-14 | Engine = loss-avoidance scorer; GEX/walls/structure/ADX/breakouts/RS/earnings/intraday excluded | they failed; §8 of the register |

## What survived (as filters only — none is an edge)

- Friction: round-trip spread ceilings by class (≈2% stocks / pooled index, 4% NIFTY, 8% BANKNIFTY, 10% SENSEX).
- Delta: avoid |delta| < 0.25; 0.40–0.70 (0.55 best OOS).
- DTE: stocks 21–35; index 7–10 among tested. **DTE < 7 never tested.**
- Low required-move ratio and cheap vol (V1/V2) — stocks only.
- Veto: inverted term structure on stocks (the only full §0 pass).
- Exit: underlying invalidation (X2) + time stop; no TP/SL grid beat a time exit.
- Forward-test candidates, zero weight: index gap-and-hold (M4), index low IV percentile (V2).

## Known limits

- Spread is modelled (40 archive sessions, one calm regime). Cross-check vs real
  bid/ask: model is **conservative** (+4.2 pts index, +2.0 pts stocks per trade).
- IV is computed from bhavcopy closes; intraday option prices are synthetic.
- Nothing known about 0–2 DTE / expiry day (needs bought data: Fyers Prime, Upstox Plus).
- Earnings announcement time unknown for most 2025–26 events.
- Earnings and intraday stock universes are today's F&O list (survivorship).
- Archive: 13 trading days missing (2026-08-26 → 09-11), bid/ask only from mid-Sep,
  4 frozen stock files — worth fixing upstream in xerxes-eod-archive.

## Open questions for the owner

- Buy real intraday option data to test expiry-day buying?
- Forward-test the two index candidates and the 3-tier score separation live?
- Port the engine to Node for the Xerxes pipeline, or call it as Python?

## File map

| Path | What |
|---|---|
| `CLAUDE.md` | mission, roles, conventions, market facts, evidence bars |
| `research/01_data_reality.md` | Phase 1: sources, feasibility matrix, scope |
| `research/02_hypotheses.md` | Phase 2: 24 pre-registered hypotheses + protocol |
| `research/trials_log.csv` | every variant run (5,380+ rows) |
| `research/out/p4_synthesis.md` | all verdicts, BH q-values, deflated Sharpe |
| `research/out/p4_{baseline,vol_dealer_events,momentum,contract_exits,intraday}.md` | per-family results (+ `.json`, chart-ready) |
| `research/out/p4_archive_crosscheck.md` | spread model vs real bid/ask |
| `research/out/p5_calibration.md` | engine score vs realized OOS expectancy |
| `research/out/p1_*`, `p3_*` | data-source probes, dataset build reports, cost and spread models |
| `research/scripts/` | all computation (`p1_*` probes, `p3_*` builds, `p4_*` backtests, `p5_*` engine fit/calibration) |
| `research/lib/`, `research/tests/` | shared pure functions (Black-76, features, backtest, stats) + tests |
| `engine/` | `buy_conviction.py`, `costs.py`, `spread.py`, `model.json`, tests, fixture, `README.md` |
| `report/index.html` | chart-led report (self-contained); `report/build_report.py` rebuilds it |
| `data/README.md` | dataset schemas; data files are gitignored |

## Rebuild from scratch

1. `p1_fetch_bhavcopy.py` (nse fo/cm/index/participant_oi 2019→, bse fo 2023-05→) — ~1.9 GB, ~2 h.
2. `p3_build_fo.py --full` → `p3_build_features.py` → `p3_fetch_earnings.py`, `p3_fetch_intraday.py`, `p3_fit_spread.py`.
3. `p4_build_trades.py` → `p4_baseline.py`, `p4_run_*.py`, `p4_intraday_stage{1,2}.py`, `p4_synthesis.py`, `p4_archive_crosscheck.py`.
4. `p5_fit_engine.py` → `p5_calibrate.py` → `python report/build_report.py`.
