# BALROG — state

_Last updated: 2026-11-13_

## Current phase

**Phase 1 — Data reality: DONE, awaiting owner go-ahead (hard checkpoint).**
Report: `research/01_data_reality.md`. Do not start Phase 2 until approved.

## Decisions made

| Date | Decision | Why |
|---|---|---|
| 2026-11-13 | Research code in Python; engine language deferred to Phase 5 | pandas/pyarrow stack already present; engine must accept a Xerxes snapshot JSON either way |
| 2026-11-13 | External repos cloned read-only under `_ext/` (gitignored) | keeps private archive data out of BALROG history |
| 2026-11-13 | Working branch `research/v1`, PR to `main` at the end | owner's instruction |

## Environment facts (measured 2026-11-13, this machine)

- Reachable: `api.upstox.com` (200), `nsearchives.nseindia.com` F&O bhavcopy
  (200, 1.4 MB for 2026-11-12), `www.bseindia.com` (200), `api.kite.trade/instruments` (200).
- `www.nseindia.com` homepage returns 403 to plain curl (bot wall) — archives host is fine.
- `UPSTOX_ACCESS_TOKEN` is **not set** in this environment.
- `gh` CLI is not installed; GitHub MCP connector failed to connect. Git over
  HTTPS works via Git Credential Manager.

## Phase 1 findings (short form — details in research/01_data_reality.md)

- Archive: 54 folders = 51 real sessions = **40 with bid/ask** (from mid-Sep). 13 trading days missing (08-26→09-11). Use mid, never `ltp`/stored `iv`.
- Bhavcopy reachable from this machine: swing backtests feasible on real daily prices (NIFTY 2010+, stocks ~2018/2020+, SENSEX 2023-05+). No bid/ask, no IV.
- Upstox: 1-min underlying from 2022-01 without a token; live option candles only; expired needs token + Plus plan.
- Kite: no expired-option history; connected app lacks historical permission.
- Synthetic intraday: approximate for |delta| >= .25 and DTE >= 8; unusable for 0-2 DTE / expiry day. Best model: sticky-moneyness.
- Recommended scope: A swing on bhavcopy (main), B intraday synthetic (secondary, labelled), C archive for calibration/validation, D optional paid data (Fyers Prime / Upstox Plus).

## Open questions

- Owner: approve scope A+B+C? Buy real intraday data (D)?
- Source for multi-year earnings/results dates.
- Bhavcopy bulk download (~1.9 GB, ~4.2 h) not yet run.
- Cost schedule (brokerage/STT/exchange/SEBI/stamp/GST) must be verified as
  current before Phase 4.

## File map

| Path | What |
|---|---|
| `CLAUDE.md` | mission, roles, conventions, market facts, evidence bars |
| `STATE.md` | this file |
| `research/01_data_reality.md` | Phase 1 report |
| `research/out/p1_*` | per-source detail: archive inventory, upstox, kite+vendors, bhavcopy, synthetic error |
| `research/scripts/p1_*` | probes, `p1_fetch_bhavcopy.py` downloader, `p1_build_archive_parquet.py` cache builder |
| `data/archive/{chain,snap,events}.parquet` | archive cache (gitignored; rebuild with the builder) |
| `research/scripts/` | all computation |
| `research/out/` | small committed summaries from scripts |
| `data/README.md` | dataset schemas |
