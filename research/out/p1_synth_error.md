# P1: how wrong are synthetic option prices? (BS/Black-76 from EOD IV + real underlying)
Scripts: `research/scripts/p1_synth_eod.py` (Test 1, archive), `research/scripts/p1_synth_intraday.py` (Test 2, live 1-min).
Numbers: `research/out/p1_synth_error.json`. Charts: `p1_synth_eod_heatmap.png`, `p1_synth_intraday_scatter.png`, `p1_synth_intraday_tod.png`.
Run 2026-11-13. Test 2 made 307 Upstox requests with no token; re-runs read the cache and make 0.
**Method.** The "real" price is the mid of a two-sided bid/ask (bid>0, spread/mid <= 40%). We re-derived the forward from put-call parity (94% of expiries) and our own IV and delta.
We did not use the vendor `iv`: it is computed from LTP, and its median repricing error is 4% against LTP and 6.5% against mid. Black-76 uses r = 6.5% and T = calendar days/365.
Models:
- a: sticky-strike.
- b: sticky-moneyness. The day-t OTM smile is read in log(K/F).
- c: model b times exp(beta * forward log-return). Beta is fitted by class on an expanding window that only uses pairs ending on or before t, so there is no lookahead. Latest h=1 fit: index -3.2, stock -0.84.
- d: oracle, the contract's own true t+h IV.
**Sample limits (read first).** Only **40 archive days** (2026-09-15..11-12) have bid/ask; the August snapshots are LTP-only. That gives 39 one-day date pairs.
The errors are highly correlated within a date, so the effective sample is about 40 market days, not n rows.
0-2 DTE has only 7 dates (index) and 0 usable rows (stock). The 3-7 DTE stock bucket has 7 dates.
Test 2 covers **29 days (2026-10-01..11-12)**: 132 live contracts, 1,306 contract-days, and 185,910 traded minutes inside |delta| 0.10-0.80.
Test 2 has **no expiry-day and no 0-2 DTE data**, because live contracts had not expired yet. The minimum DTE is 5. Its index side is 3 underlyings, and all of it falls in a single 6-week regime.
## Test 1: EOD to EOD repricing (entry = real mid at t, exit = synthetic at t+h)
Overall, by model. Columns: median |P_syn/P_real-1| / median |ret error| / return corr / sign agreement / mean bias (syn-real return).

| h | class | n (dates) | a | b | c | d oracle |
|---|---|---|---|---|---|---|
| 1d | index | 19,057 (39) | 5.9% / 5.5% / .972 / .926 / -1.8% | 6.6% / 6.1% / .966 / .928 / -1.4% | 6.2% / 5.9% / .966 / .925 / -2.4% | 2.0% / 1.8% / .996 / .955 / 0.0% |
| 1d | stock | 228,224 (39) | 5.4% / 5.2% / .962 / .915 / -0.4% | 5.3% / 5.0% / .964 / .915 / -0.5% | **5.0% / 4.7% / .965 / .916 / -0.5%** | 2.2% / 2.0% / .991 / .954 / +0.2% |
| 5d | index | 13,466 (35) | **10.1% / 6.4% / .997 / .949 / -2.4%** | 12.0% / 7.0% / .995 / .946 / -1.9% | 12.2% / 8.1% / .996 / .952 / -5.2% | 3.1% / 2.0% / .998 / .977 / +1.3% |
| 5d | stock | 180,707 (35) | 10.4% / 7.7% / .987 / .946 / -2.3% | **9.9% / 7.1% / .987 / .947 / -2.5%** | 10.0% / 7.2% / .987 / .947 / -3.6% | 3.2% / 2.3% / .997 / .976 / +0.2% |
- Median synthetic error is 2-5x the half-spread for 1d and 5-10x for 5d. The oracle stays at 1-2.5x, which is the quote and forward-proxy noise floor.
- The synthetic error is larger than the actual |return| in 14% (index, 1d), 16-17% (stock, 1d) and about 10% (5d) of rows.
- On the subset where beta is live, c does not beat b for index (1d median 6.3% vs 6.7%; it is worse at 5d). For stocks it beats b by about 0.3pp.
**Systematic bias.** For short-dated OTM options the synthetic return is too low. At 1d it is -16% (index 0-2 DTE, |delta| .10-.25, n=87 over 7 dates), -12% (index 3-7 DTE, n=777) and -8% (stock 3-7 DTE, n=4,200).
The cause is that real IV rises into expiry and wings get bid, while flat-smile carry assumes they won't.
So a synthetic backtest **understates** returns from buying cheap near-expiry wings. This is not a safe-side error for strategies that sell them.

1-day cells, model b. Values: median price error / median return error / bias / sign agreement, followed by n.

| class | DTE | 10-25 | 25-40 | 40-60 | 60-80 |
|---|---|---|---|---|---|
| index | 0-2 (7 dates) | 31% / 16% / -16% / .91, n=87 | 19% / 13% / -8% / .92, n=51 | 10% / 7% / -4.5% / .96, n=69 | 5% / 4% / -2.6% / .99, n=72 |
| index | 3-7 | 30% / 18% / -12% / .90, n=777 | 18% / 12% / -5.4% / .93, n=488 | 9.6% / 8.7% / -2.9% / .95, n=567 | 4.0% / 3.9% / -1.4% / .96, n=670 |
| index | 8-20 | 18% / 15% / -2.3% / .93, n=2409 | 10% / 9% / -1.0% / .94, n=1679 | 6.2% / 6.0% / -0.3% / .96, n=2108 | 3.3% / 3.2% / -0.2% / .98, n=2041 |
| index | 21+ | 9.0% / 7.9% / -0.9% / .90, n=2042 | 7.1% / 6.4% / +0.2% / .90, n=1715 | 4.9% / 4.7% / 0.0% / .90, n=2236 | 2.9% / 2.8% / +0.4% / .93, n=2046 |
| stock | 3-7 (7 dates) | 24% / 17% / -8.1% / .89, n=4200 | 17% / 12% / -1.8% / .91, n=2463 | 10% / 8.6% / +0.1% / .93, n=2865 | 5.1% / 4.9% / -0.8% / .96, n=3494 |
| stock | 8-20 (20 dates) | 12.5% / 10.9% / -1.8% / .89, n=24995 | 8.0% / 7.3% / +0.5% / .93, n=15311 | 4.9% / 4.7% / +0.5% / .94, n=17705 | 2.6% / 2.6% / +0.2% / .96, n=19304 |
| stock | 21+ | 8.9% / 8.3% / -1.2% / .88, n=35848 | 5.9% / 5.7% / -0.2% / .91, n=28556 | 3.9% / 3.9% / 0.0% / .92, n=38433 | 2.4% / 2.4% / +0.1% / .93, n=35050 |

At 2-5d, price error roughly scales with sqrt(h). Returns grow faster, so the correlation rises to about .99 and sign agreement to .94-.98. For delta >= .40 the bias stays within ±1.3%.
For delta < .25 and DTE <= 20 the bias is -5% to -18% (see JSON `cells`).
## Test 2: intraday, previous-day EOD IV + live 1-min underlying vs real 1-min option close
Median |syn/real-1|, all minutes. Index: a 6.1%, **b 5.8%**, c 7.7% (n=74,289 min, 336 contract-days). Stock: **a 4.0%**, b 4.2%, c 4.5% (n=111,621, 561 contract-days).
Median signed error is -2% to -3% for a and b: the synthetic is too cheap by a level that persists all day.
- **By time of day (b).** Index error grows through the session: 3.9% at 09:15-09:30 (n=2,716), 4.3% at 09:30-10:30, 6.4% at 12-14, and 6.9% after 15:00 (n=7,195), with a -4% bias late in the day.
  Stock error is flat at 3.8-4.5% through the day.
- **By |delta| (b).** Index: 15.9% (.10-.25, n=12,030, bias -15%), 9.2%, 4.6%, 2.3%. Stock: 10.3%, 5.6%, 3.8%, 2.1%.
- **By DTE (b).** Index: 7.7% (3-7, 42 contract-days), 5.5% (8-20), 3.9% (21+). Stock: 3.5% (8-20), 7.6% (21+, mostly older days where today's ATM strike was far OTM).

Trades. Synthetic entry and exit vs real entry and exit; values are return corr / sign agreement / mean bias / MAE.

| class, style | n (days) | a | b | c | anchor (DIAGNOSTIC: real entry IV) |
|---|---|---|---|---|---|
| index 09:30->15:15 | 193 (29) | .978 / .90 / +3.6% / 9.0% | .978 / .90 / +2.7% / 8.7% | .970 / .91 / -2.2% / 12.3% | .982 / .91 / 0.0% / 10.1% |
| index 09:45->+60m | 206 | .949 / .85 / +0.7% / 3.9% | .935 / .85 / +0.8% / 4.3% | .901 / .84 / +1.2% / 5.2% | .927 / .85 / +0.8% / 4.5% |
| index ORB->15:15 | 105 | .987 / .90 / -0.8% / 7.6% | .987 / .91 / -1.9% / 8.5% | .986 / .91 / -8.9% / 14.7% | .991 / .91 / -5.1% / 11.5% |
| stock 09:30->15:15 | 391 | .971 / .91 / +2.3% / 5.6% | .967 / .91 / +1.4% / 5.3% | .962 / .91 / +1.3% / 5.8% | .962 / .91 / +1.6% / 5.5% |
| stock 09:45->+60m | 373 | .959 / .92 / +1.0% / 2.9% | .961 / .92 / +0.8% / 2.7% | .958 / .92 / +1.2% / 2.8% | .953 / .92 / +0.8% / 2.8% |
| stock ORB->15:15 | 185 | .988 / .92 / +2.2% / 4.5% | .987 / .92 / +1.8% / 4.2% | .987 / .92 / +1.8% / 4.1% | .971 / .92 / +1.6% / 4.7% |
- The synthetic error exceeds the real |return| in 15-26% of trades. Sign disagreement is 8-15%, concentrated in small moves.
- Intraday synthetic returns are biased **up** by +1% to +3% for full-day holds. The likely cause is calendar-time theta: only 6/24 of a day decays during the session, while the market prices more decay into the session.
  Long-option backtests on synthetic data will look slightly too good intraday, and too bad over multiple days for near-expiry wings.
- Anchoring entry to the real price does **not** improve the return. Most of the level error cancels between entry and exit, and what remains is intraday IV drift and theta convention, which no EOD-based model knows.
## Verdict grid (model b; "haircut" = extra per-trade return error to assume)
Rule. **Trustworthy:** median return error ≤5%, |bias| ≤1%, sign ≥.93. **Approximate:** median return error ≤10-15% and |bias| ≤5%. **Unusable:** otherwise, or when there are too few dates to judge.

| class | DTE | |delta| | intraday | 1-day | 2-5-day |
|---|---|---|---|---|---|
| index | 0-2 / expiry day | any | **unusable (untested; no data)** | unusable <.40 (bias -8..-16%, 7 dates); approx ≥.40 (haircut 4-7%, bias -3..-4%) | unusable (no sample) |
| index | 3-7 | .10-.40 | unusable (15% price err, -15% bias) | unusable (bias -5..-12%) | unusable (bias -5..-18%) |
| index | 3-7 | .40-.80 | approximate, haircut ~8% (only 42 contract-days) | approximate, haircut 4-9%, bias -1.4..-3% | approximate, haircut 4-8%, bias -1.6..-4.9% |
| index | 8-20 | .10-.25 | unusable | unusable (15% return err) | unusable (bias -5..-8%) |
| index | 8-20 | .25-.80 | approximate, haircut 4-9% (full-day hold), 4% (1h) | approximate, haircut 3-9%; trustworthy at .60-.80 | approximate .25-.60 (haircut 6-10%); trustworthy-ish .60-.80 (3-4%, bias -1%) |
| index | 21+ | .25-.80 | approximate, haircut ~4% | approximate, haircut 3-6% (sign only .90-.93) | approximate .25-.60 (5-10%); trustworthy .60-.80 |
| stock | 0-2 / expiry day | any | unusable (untested) | unusable (no sample) | unusable (no sample) |
| stock | 3-7 | .10-.40 | untested | unusable <.25 (bias -8%); approx .25-.40 (12%) | unusable (bias -6..-14%) |
| stock | 3-7 | .40-.80 | untested | approximate, haircut 5-9% (7 dates) | approximate, haircut 5-9% (3-7 dates) |
| stock | 8-20 | .10-.25 | approximate, haircut ~10% | approximate, haircut 11% | unusable (bias -3..-7%) |
| stock | 8-20 | .25-.80 | approximate, haircut 3-5% (1h ~3%) | **trustworthy .40-.80** (2.6-4.7%); approx .25-.40 (7%) | trustworthy .60-.80; approx .25-.60 (5-9%) |
| stock | 21+ | .25-.80 | approximate, haircut 4-8% | trustworthy .60-.80; approx .25-.60 (4-6%) | trustworthy .60-.80; approx .25-.60 (5-9%) |
**Recommendation.** Use **model b, sticky-moneyness** as the default.
- Stocks: b is best or tied at 1d and 5d. Intraday it differs from a by only 0.2pp.
- Index: a (sticky-strike) is marginally better at EOD (1d 5.5% vs 6.1% return error), and b is better intraday (5.8% vs 6.1%). The gap is within noise given 39 dates.
- Do **not** use c. Its beta is unstable on 40 days (index -1.5 to -4.3 depending on horizon) and it worsens index errors.

Apply these conditions to any synthetic backtest:
1. Restrict to |delta| ≥ .25, and to ≥ .40 when DTE ≤ 7.
2. Add the cell's haircut as return noise or slippage.
3. Do not trust any 0-2 DTE or expiry-day result.
4. Expect the edge sign to be right about 90% of the time per trade, with no reliability for edges smaller than about 5% per trade.
**Unknowns.** The intraday behaviour at expiry and at 0-2 DTE is untested. All results come from one 2-month regime with no vol spike.
Post-close mids (16:05+ IST) may themselves be stale or wide, which inflates the Test 1 errors; the oracle floor of about 2% bounds this.
The calendar-time theta convention could be tuned, for example to trading-time decay, to remove the +1..3% intraday bias. This has not been tested.
