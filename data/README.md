# data/

Dataset schemas are documented here as each dataset is built (Phase 3).
The files themselves are gitignored; every one is reproducible from a script
under `research/scripts/`.

| Dataset | Built by | Schema | Status |
|---|---|---|---|
| `fo/options/`, `fo/futures/` | `research/scripts/p3_build_fo.py` | below | incremental; partial until the bulk download finishes |
| `underlying/daily.parquet` | same | below | rebuilt every run |
| `ref/{calendar,expiries,lots,universe,participant_oi}.parquet` | same | below | rebuilt every run |
| `features/daily.parquet` | `research/scripts/p3_build_features.py` | below | rebuilt every run (option-chain months cached in `features/_cache/`) |
| `fo/_stage/` | same | internal: one normalised parquet per raw file + `_index.csv`, `_assembled.json`, per-month summaries | do not use directly |

Run: `python -I research/scripts/p3_build_fo.py [--workers 4] [--full]` (only new raw files are parsed;
only months whose calendar window changed are re-assembled; `--full` re-assembles all months).
Library: `research/lib/{bhav,black76,fo_derive,rates}.py`. Tests: `research/tests/test_p3_fo.py`.
Source window: 2019-01-01 onward. Report: `research/out/p3_fo_build.md`.

## fo/options/year=YYYY/{nse|bse}_{YYYY-MM}.parquet - one row per option contract per trading day
| column | type | meaning |
|---|---|---|
| date | timestamp | trading day (bhavcopy date) |
| exchange | cat | `nse` / `bse` |
| symbol | cat | F&O symbol as in the file (NIFTY, BANKNIFTY, SENSEX, RELIANCE ...) |
| cls | cat | `index` / `stock` |
| expiry, strike, opt_type | ts, f64, cat | contract key; opt_type CE/PE |
| open, high, low, close | f64 | day OHLC of the premium. **close is stale (last trade or prev close) when traded=False**; BSE expiring options' close is NaN on expiry day (exchange writes spot there) |
| settle | f64 | exchange settlement/theoretical price (NSE; BSE UDiFF only) - for MTM, never for fills |
| volume_contracts | f64 | contracts traded (NSE legacy CONTRACTS; NSE UDiFF TtlTradgVol; BSE UDiFF quantity / lot; BSE legacy contracts) |
| oi_contracts, d_oi_contracts | f64 | open interest and its change vs previous day, in contracts (file share counts / lot_size) |
| lot_size | f64 | contract multiplier. UDiFF: `NewBrdLotQty`; legacy: rebuilt per (symbol, expiry, day) = divisor of gcd(OI, chg-OI shares) closest to futures turnover/(contracts x price), weak days filled from the same contract / same symbol-day |
| traded | bool | volume_contracts > 0 |
| dte_cal | int | calendar days to expiry |
| dte_trd | int | trading days in (date, expiry] on the observed calendar (0 on expiry day). Beyond the last observed day weekdays are counted |
| expiry_rank | int | dense rank of expiry per (date, exchange, symbol); 1 = nearest, an expiry trading its last day counts as 1 |
| is_monthly / is_weekly | bool | monthly = last listed expiry of its calendar month for that symbol-day (options or futures); weekly = not monthly |
| fwd, fwd_src | f64, cat | forward: `fut` same-expiry futures close (settle if no close) -> `parity` K+(C-P)e^{rT} at the CE/PE pair with largest min volume (both traded) -> `spot` spot*e^{rT} -> `interp` nearest-dated forward of the same symbol-day carried at r |
| r | f64 | RBI repo rate in force (research/lib/rates.py) |
| spot | f64 | underlying close (CM/index file; else F&O-file UndrlygPric; NaN if neither present yet) |
| iv | f32 | Black-76 IV from `close` and `fwd`, T = dte_cal/365; only when traded, dte_cal > 0, close not masked and close strictly inside (discounted intrinsic, upper bound); else NaN |
| delta, gamma | f32 | Black-76 w.r.t. the forward (delta includes discounting) |
| theta | f32 | premium change per calendar day (negative for longs) |
| vega | f32 | premium change per 1 vol point |
| close_masked | bool | BSE expiry-day row with close blanked |
| ca_break | bool | corporate-action break on this symbol-day (NSE): >= 50% of the previous day's strikes of still-live expiries vanished (exchange re-struck the chain on ex-date). Skip holds that span a ca_break day |

## fo/futures/year=YYYY/{ex}_{YYYY-MM}.parquet
date, exchange, symbol, cls, expiry, open, high, low, close, settle, volume_contracts, oi_contracts,
d_oi_contracts, lot_size, expiry_rank, dte_cal, ca_break - same units/definitions as options.

## underlying/daily.parquet - one row per underlying per day
| column | meaning |
|---|---|
| date, symbol, cls | symbol uses F&O names (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, SENSEX, BANKEX ...) plus `INDIAVIX` |
| open, high, low, close | raw (unadjusted) prices |
| prev_close | file's previous close (NSE CM `PREVCLOSE`/`PrvsClsgPric`; index close - points change); else previous observed close |
| ret | close/prev_close - 1 (CM prev_close is the exchange-adjusted base price on ex-dates - see report) |
| adj_factor, adj_open..adj_close | back-adjusted from ret so that adj_close_{t-1} = adj_close_t / (1+ret_t); latest day factor = 1 (factors change as data is appended) |
| src | `nse_cm`, `nse_index`, `nse_fo_undrlygpric` (close only), `bse_fo_undrlygpric` (SENSEX/BANKEX close only, BSE UDiFF), `bse_parity_proxy` (BSE legacy days: nearest-expiry forward x e^{-rT}, close only) |

## ref/
| file | columns |
|---|---|
| calendar.parquet | date, nse_fo, bse_fo, nse_cm, nse_index (file present), trading_day (= any F&O file) |
| expiries.parquet | exchange, symbol, expiry, cls, first_listed, last_seen, weekday (Mon..Sun), kind (weekly/monthly) - expiries as observed in files (holiday shifts included) |
| lots.parquet | exchange, symbol, date_from, lot_size - nearest-expiry lot, one row per change |
| universe.parquet | exchange, symbol, cls, first_date, last_date, n_days (days with options listed), renamed_to (heuristic: symbol vanishes and another appears next day with OI within 25%) |
| participant_oi.parquet | date, client_type (Client/DII/FII/Pro/TOTAL), fut_idx_long/short, fut_stk_long/short, opt_idx_call_long/put_long/call_short/put_short, opt_stk_*, total_long, total_short (contracts) |

## features/daily.parquet - point-in-time features, one row per (date, symbol) with an option chain
Built by `python -I research/scripts/p3_build_features.py [--workers 3]`; library `research/lib/features.py`;
tests `research/tests/test_p3_features.py`; report `research/out/p3_features.md`. Every value uses data up to
the close of `date` only (trailing windows; HAR fitted on targets observed before the month; participant OI lagged
one session). Stocks: NSE chains only; indices: NSE + SENSEX/BANKEX (BSE). Vols are annualised decimals.

| group | columns | definition |
|---|---|---|
| keys | date, symbol, cls, spot | spot = underlying close (raw), else option-file spot, else fwd x e^{-rT} |
| price | ret1, ret5, ret20 | adjusted close returns |
| | rv20 | close-to-close log-return std over 20 sessions x sqrt(252) |
| | atr20, atr20_pct, day_range_atr20 | Wilder ATR(20) in day-t raw units / as fraction of close; (high-low)_t / ATR20_{t-1} |
| | adx14, plus_di, minus_di | Wilder ADX/DMI(14) |
| | donch_{hi,lo}{10,20,40}, brk_{hi,lo}{10,20,40} | max high / min low of sessions t-n..t-1 (raw units of day t); brk = close beyond it |
| | sma20, above_sma20, close_vs_open | 20-session SMA; close > SMA20; sign(close-open) |
| | ohlc_src | `spot`; `fut` (O/H/L from near future x spot/fut close, e.g. SENSEX); `close_only` |
| HAR | sigma_daily, har_vol_h{1,3,5} | log-HAR on Parkinson variance (1/5/22-day regressors), pooled per class, expanding window refit monthly; sigma_daily = sqrt(forecast var, h=1) (daily fraction); har_vol_h = sqrt(252 x forecast mean var over t+1..t+h) |
| | sigma_daily_prev, gap_sigma | sigma_daily of t-1; (open_t/close_{t-1}-1)/sigma_daily_prev |
| market | beta60, rs20, rs20_decile | 60-session beta to NIFTY; ret20 - beta60 x NIFTY ret20; cross-sectional decile among stocks (1..10) |
| | nifty_above_20dma, india_vix, vix_pctile_252, vix_chg5 | NIFTY close > SMA20; VIX close, trailing-252 percentile, 5-session change (points) |
| | {fii,client}_idx_fut_long_ratio, _idx_call_net_ratio, _idx_put_net_ratio | participant OI, file of the previous session: long/(long+short); (long-short)/(long+short) |
| vol surface | std_expiry, std_dte_cal, std_dte_trd, next_expiry | standard expiry = nearest with dte_cal >= 7; next = the one after |
| | atm_iv, atm_iv_next, term_spread | IV linearly interpolated in strike at the forward between the bracketing traded strikes (CE/PE IV averaged per strike; one side only if within 3% of F); term_spread = atm_iv - atm_iv_next |
| | iv_pctile_252, vrp_ratio_h{3,5} | trailing-252 percentile of atm_iv (t included, >= 126 obs); atm_iv / har_vol_h |
| dealer | net_gex | sum over nearest two expiries with dte_cal >= 1 of gamma x OI x lot x S^2 x 0.01, calls +, puts - (Rs per 1% move); untraded strikes use the expiry's ATM IV; strikes within +-30% |
| | net_gex_norm, gex_pctile_252 | net_gex / 20-session mean futures turnover (all expiries, contracts x lot x price); its trailing-252 percentile |
| | gamma_flip, gamma_flip_dist_sigma | spot level (+-15% grid, IVs fixed) where net GEX changes sign nearest to spot; (spot - flip)/(spot x sigma_daily) |
| | call_wall, put_wall, *_wall_oi, *_wall_oi_chg, *_wall_dist_pct | strike of max call/put OI in the nearest expiry with dte_cal >= 1 within +-10% of spot; OI in shares; d/d OI change at that strike (d_oi / prev OI); 100 x (wall/spot - 1) |
| | broke_call_wall, broke_put_wall | close_t > call_wall_t and close_{t-1} <= call_wall_{t-1} (puts mirrored); False on ca_break days |
| | cross_call_wall_prev, cross_put_wall_prev | stricter: close crosses the t-1 wall level (wall fixed) |
| | max_oi_strike, max_oi_dist_sigma | strike with max call+put OI (same expiry/band); (spot - K)/(spot x sigma_daily) |
| | near_expiry, near_dte_trd | nearest expiry with dte_cal >= 1 (used for walls/GEX), trading days to it |
| futures | fut_dprice, fut_doi, fut_doi_near | near future (dte_cal >= 1) close vs same contract's previous close; combined-OI change over all expiries; near-contract OI change |
| | fut_dprice_z, fut_doi_z, fut_structure | value / std of previous 60 sessions; label long_buildup / short_buildup / short_covering / long_unwinding when both abs(z) >= 0.5, else none |
| | fut_turnover_20 | 20-session mean futures turnover (Rs) |
| calendar | is_expiry_day, days_to_next_expiry, ca_break | an option expiry of this symbol today; trading days to nearest expiry (0 on expiry day); corporate-action break (options or futures) |
| results | results_today_tradable, results_time_unknown | first session whose close reflects the results (same day if announced pre/during market, next trading day if post-close or time unknown; unknown flagged). One event per 20-day cluster |
| | days_to_results | trading days until the last close before results (0 that day; NaN if > 15). Uses the realised date (board meetings are pre-announced) |
| | results_move_sigma | on the reaction day: ret1 / sigma_daily_prev |

## bt/trades/year=YYYY.parquet - P4 priced swing trades (`research/scripts/p4_build_trades.py`, lib `research/lib/backtest.py`)
One row per (entry date, symbol, opt_type, dte_bucket {7-10, 11-20, 21-35, 36+}, delta_target {.15,.25,.40,.55,.70}) with a
qualifying contract (traded, volume >= 500 index / 50 stock, close >= 2, |delta| gap <= .10; stocks monthly only).
`is_std` = 02_hypotheses §0 standard contract (std expiry & delta .40); `is_std_exp` = std expiry, any delta.
Key: trade_id, date, exchange, symbol, cls, grp (NIFTY/BANKNIFTY/SENSEX/other_index/stocks), sample (IS/OOS), expiry, strike.
Entry: premium (close), iv, delta, gamma, theta, vega, volume, oi, dte_cal, dte_trd, lot, spot, rv20 (underlying, daily),
sigma_d (rv20, else iv/sqrt252), half_spread (+ _2x), entry_fill, h_cap (= dte_trd - 1), gamma_theta (gamma/|theta|),
gamma_theta_rv (0.5 gamma (S rv20)^2 / |theta|), req_move_pts, rmr_rv20 (h=3), c3_* cost components (h=3 exit).
Path: p{1..5}_{o,h,l,c} contract OHLC (NaN untraded), u{0..5}_{c,h,l} underlying.
Per h in {1,2,3,5}: exit_date, exit_mark, hs_exit, exit_fill, cost, gross_rs, net_rs, gross_R, net_R, net_R2x, cal_days,
net_R_open1 (enter t+1 open), flags capped/stale/settled/nomark/incomplete/ca_break.
## bt/exits/year=YYYY.parquet - X1-X4 outcomes, keyed by trade_id
`{rule}_h{3|5}_{R,R2x,rs,day,why}`; rules x1_tp{30,50,100}_sl{25,40,60}, x2_ext, x2_sig, x3_trail, x4_fast;
why: 0 time stop, 1 SL, 2 TP, 3 underlying invalidation, 4 trail, 5 fast stop.
