# BALROG — working notes

Systematic research framework for **buying** NSE/BSE options (indices + single
stocks). Question it answers: *when, what and how to buy an option so expected
value after costs is positive* — then encode only what survives into a
calibrated `buyConviction` 0–100 scoring engine.

Read this file + `STATE.md` at the start of every session. Do not re-derive
anything they already record.

## Mindset (not "selling inverted")

Theta is the enemy. Every trade is one question: *will the underlying move far
enough, fast enough, to beat breakeven + costs before decay wins?*
Sell-side signals flip meaning: positive-gamma pinning is where buyers die;
negative-gamma / OI-wall-break regimes are where they can win.

The owner suspects the sell-side gates in Xerxes (Premium Shikari) are
curve-fit. BALROG exists to not repeat that. See "Evidence bars" below — they
are not optional.

## Session roles and token discipline

- **Main session** plans, designs hypotheses, reviews critically, decides,
  writes synthesis. It does **not** do bulk data work.
- **Opus subagents** (`Agent`, `model: "opus"`) do data pulls, cleaning,
  features, backtests, charts. Tight brief: inputs, exact output files,
  acceptance checks. Each returns **≤ 300 words**. Independent ones run in parallel.
- Never read raw data files (JSON/CSV/gz/parquet) into context. Write a script,
  run it, print a summary of **≤ 50 lines**.
- Every computation is a committed script under `research/scripts/`, reproducible.
- Intermediate datasets are cached as Parquet under `data/` so nothing is
  pulled twice.
- `STATE.md` is updated at the end of each phase.
- **One hard checkpoint**: end of Phase 1. After approval, Phases 2–5 run end to
  end; stop early only if something invalidates the plan.

## Repo map

```
CLAUDE.md  STATE.md  README.md  requirements.txt
research/
  01_data_reality.md   02_hypotheses.md   trials_log.csv
  scripts/             every computation, numbered by phase (p1_*.py, p3_*.py …)
  out/                 small JSON/MD/PNG summaries produced by scripts (committed)
data/                  README.md documents schemas; raw/ and *.parquet gitignored
engine/                buyConviction + tests + regression fixture
report/                index.html (self-contained, chart-led) + README.md
.github/workflows/     only if a data-download job is needed
_ext/                  GITIGNORED read-only clones: xerxes, xerxes-eod-archive
```

## Data sources

| Source | Where | Notes |
|---|---|---|
| Xerxes live engine | `_ext/xerxes` (branch `claude/nifty-option-screener-93xv0y`), public | Reuse logic from `scripts/upstox.mjs`, `analytics.mjs`, `premium.mjs`; don't duplicate. Its `CLAUDE.md` is the operational bible. |
| Xerxes `stocks-data` branch | remote only | ~157 F&O stock snapshots, force-orphaned each run — **no history**. |
| **xerxes-eod-archive** | `_ext/xerxes-eod-archive`, **private** | **The only chain history.** `archive/YYYY-MM-DD/{indices,stocks}/*.json.gz` + `meta.json`, plus normalised SQLite `xerxes.db` keyed `(trade_date, symbol, expiry, strike, type)` and `state/outcomes.jsonl`. One EOD snapshot/day (~16:05 IST), 2026-08-11 → 2026-11-12. Read-only. Never push there. |
| NSE/BSE F&O bhavcopy | public archives | Daily OHLC + OI per contract, multi-year. No bid/ask, no IV. |
| Upstox API | `UPSTOX_ACCESS_TOKEN` env var | Never print or commit the token. |
| Kite Connect | owner has an account | Historical API scope to be confirmed (Phase 1). |

Phase 1 findings on what each source actually yields live in
`research/01_data_reality.md`.

### Snapshot schema (per instrument)
`asOf, spot{price,prevClose,changePct,history}, vix, future{price,expiry,oi,basisPts},
lotSize, expiries{<date>: {dte, tYears, metrics, candidates, chain[], verdict}},
ivHistory, gap, term{slopePts,regime,nearIv,farIv}, events, outlook, verdict,
structure{label,bias,strength}`.
`chain[]` row: `{strike, type, ltp, iv, oi, prevOi, volume, bid, ask, delta}`.

## Market facts — claims to VERIFY against data, never trust as constants

- NIFTY weekly expiry: Tuesday since Sept 2025.
- BANKNIFTY: monthly only since Nov 2024.
- SENSEX weekly expiry on BSE — weekday to be confirmed from `expiries` dates.
- Lot sizes and expiry rules change over time: read from the data at each date.
- Costs are first-class: brokerage, STT on the option sell leg, exchange + SEBI
  charges, stamp duty, GST, **plus slippage from the actual bid/ask spread**.
  For ₹5–20 premiums, costs + spread are often the whole edge. Rates must be
  verified as current before any backtest uses them.

## Gotchas inherited from Xerxes (measured there, respect them here)

- Exchange `oi` and `volume` are **shares, not lots** — divide by lot size.
- **Stale-quote trap**: an `ltp` that never traded that session produces fake
  edge and corrupts `iv`/`delta`. Gate on two-sided quote / volume *before*
  deriving anything from price. Far-month and far-OTM strikes are the worst.
- The order book **persists after the 15:30 close** — post-close bid/ask in the
  archive are real quotes, but end-of-day (wider than intraday) ones.
- Snapshots taken on a non-trading day show 0.00% moves and shifted structure
  labels — validate `asOf` against the trading calendar.
- NSE stock options settle **physically**; ITM longs near expiry carry delivery
  obligation/margin. Index options are cash-settled.
- Single-stock variance risk is barely priced (Driessen-Maenhout-Vilkov 2009);
  the index variance premium is robust. For a buyer that asymmetry cuts the
  other way: indices are structurally expensive, stocks are not obviously so.

## Evidence bars (every Phase 4 result shows each one explicitly)

1. Costs and real spread included; gross shown only beside net.
2. Walk-forward / out-of-sample; IS and OOS side by side; no tuning on test.
3. Parameter robustness: plateaus only. Collapse at ±10–20% threshold shift ⇒ reject.
4. Multiple-testing control: every variant logged in `research/trials_log.csv`
   (failures included); deflated Sharpe or equivalent haircut reported.
5. Minimum sample: < ~100 trades ⇒ flagged anecdotal.
6. Buyer metrics: win rate, avg win/loss, expectancy (₹ and R), profit factor,
   max DD, longest losing streak, theta share of losses, time-to-target —
   by instrument class, DTE bucket, regime, holding style.
7. Baselines: random-entry buys (same DTE/delta/holding) and "buy ATM at open".
   No edge over random entry ⇒ no edge.
8. Hypotheses are pre-registered in `research/02_hypotheses.md` **before** any
   backtest runs.
9. Synthetic option prices are labelled synthetic everywhere they appear.

## Conventions

- Research code: Python 3.10 (pandas, numpy, pyarrow, scipy, matplotlib/plotly).
  Run scripts that read downloaded/archived data with `python -I`.
- Scripts take paths as arguments, write to `data/` or `research/out/`, and
  print a ≤ 50-line summary.
- Point-in-time only: a feature at decision time *t* uses information available at *t*.
- Dates ISO `YYYY-MM-DD`; times IST unless suffixed `_utc`.
- Secrets never enter git. `.env` is gitignored.
- Git: work on `research/v1`, commit at the end of each phase, PR to `main` at the end.
