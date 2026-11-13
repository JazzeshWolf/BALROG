"""BALROG transaction-cost model for a long option round trip (buy, then sell or expire).

Pure stdlib. Rates are picked by trade_date from the dated SCHEDULE below.
Each row is (effective_from, value, source, confidence). A row applies from
its effective_from date until the next row's date.
Research notes and worked examples: research/out/p3_costs.md

Conventions
- Premiums are per unit (per share or index unit); turnover = premium * lot_size * lots.
- All ad-valorem charges are applied to PREMIUM turnover (options charges are on premium).
- Returned values are unrounded rupees. Brokers round each contract-note line to the paisa,
  so real bills can differ by a few paise.
"""
from __future__ import annotations

import datetime as _dt
from typing import Dict, List, Tuple, Union

CR = 1e7  # one crore in rupees
Row = Tuple[str, float, str, str]
DateLike = Union[str, _dt.date, _dt.datetime]

# --------------------------------------------------------------------------------------
# Dated rate schedule (fractions of turnover unless noted)
# --------------------------------------------------------------------------------------
SCHEDULE: Dict[str, List[Row]] = {
    # STT on sale of an option, % of sell premium, payable by seller.
    "stt_option_sale": [
        ("2019-01-01", 0.0005, "https://nsearchives.nseindia.com/content/circulars/FATAX56235.pdf (rate upto 31-Mar-2023: 0.05%)", "high"),
        ("2023-04-01", 0.000625, "https://nsearchives.nseindia.com/content/circulars/FATAX56235.pdf (Finance Act 2023)", "high"),
        ("2024-10-01", 0.001, "https://nsearchives.nseindia.com/content/circulars/FATAX63809.pdf (Finance (No.2) Act 2024)", "high"),
        ("2026-04-01", 0.0015, "https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf (Finance Act 2026)", "high"),
    ],
    # STT on an option that is exercised (ITM at expiry), payable by purchaser (holder).
    "stt_option_exercise": [
        ("2019-01-01", 0.00125, "https://nsearchives.nseindia.com/content/circulars/FATAX41919.pdf (on SETTLEMENT PRICE x qty until 31-Aug-2019)", "high"),
        ("2019-09-01", 0.00125, "https://nsearchives.nseindia.com/content/circulars/FATAX41919.pdf (on INTRINSIC value from 1-Sep-2019)", "high"),
        ("2026-04-01", 0.0015, "https://nsearchives.nseindia.com/content/circulars/FATAX73524.pdf (0.125% -> 0.15%)", "high"),
    ],
    # NSE equity-options transaction charge on premium (retail = first incremental slab
    # until the Oct-2024 flat 'true to label' structure).
    "nse_txn": [
        ("2019-01-01", 0.00050, "https://nsearchives.nseindia.com/content/circulars/FA46730.pdf ('existing' Rs50/lakh, Rs3-100cr slab)", "medium"),
        ("2021-01-01", 0.00053, "https://nsearchives.nseindia.com/content/circulars/FA46730.pdf (Rs53/lakh w.e.f. 1-Jan-2021)", "high"),
        ("2023-04-01", 0.00050, "https://nsearchives.nseindia.com/content/circulars/FA56129.pdf (Rs50/lakh w.e.f. 1-Apr-2023)", "high"),
        ("2024-10-01", 0.0003503, "https://nsearchives.nseindia.com/content/circulars/FA73061.pdf (recaps NSE/FA/64323: Rs3,503/cr flat)", "high"),
        ("2026-03-01", 0.000355299, "https://nsearchives.nseindia.com/content/circulars/FA73061.pdf (Rs3,552.99/cr w.e.f. 1-Mar-2026)", "high"),
    ],
    # NSE IPFT contribution on premium (charged with GST by Zerodha/Upstox).
    "nse_ipft": [
        ("2019-01-01", 0.01 / CR, "https://nsearchives.nseindia.com/content/circulars/FA56129.pdf ('existing Rs0.01/cr')", "high"),
        ("2023-04-01", 50 / CR, "https://nsearchives.nseindia.com/content/circulars/FA56129.pdf (Rs50/cr w.e.f. 1-Apr-2023)", "high"),
        ("2026-03-01", 0.01 / CR, "https://nsearchives.nseindia.com/content/circulars/FA73061.pdf (rolled back to Rs0.01/cr)", "high"),
    ],
    # BSE SENSEX/BANKEX options transaction charge on premium.
    "bse_txn": [
        ("2019-01-01", 0.000225, "inferred: May-2024 hike was +24-32% to <=Rs2,950/cr; https://www.business-standard.com/amp/markets/news/bse-up-3-on-hiking-transaction-charges-for-sensex-bankex-contract-options-124050200220_1.html", "low"),
        ("2024-05-13", 0.000295, "top slab Rs2,950/cr from 13-May-2024; same Business Standard / BQ Prime reports", "medium"),
        ("2024-10-01", 0.000325, "Rs3,250/cr flat; https://www.angelone.in/news/market-updates/bse-updates-transaction-fees-for-sensex-bankex-options ; https://zerodha.com/charges/ (0.0325%, Nov-2026)", "high"),
    ],
    # SEBI turnover fee on premium.
    "sebi_fee": [
        ("2019-01-01", 15 / CR, "SEBI board 1-Mar-2019 cut Rs15->Rs10/cr; https://zeebiz.com/india/news-sebi-board-approves-lowering-of-fees-for-brokers-exchanges-88435", "medium"),
        ("2019-06-01", 10 / CR, "same; exact notified effective date NOT confirmed (assumed 1-Jun-2019)", "low"),
        ("2020-06-01", 5 / CR, "50% COVID relief Jun-2020..Mar-2021; https://www.business-standard.com/amp/article/pti-stories/sebi-reduces-broker-turnover-fees-filing-charges-for-draft-papers-120042700905_1.html", "medium"),
        ("2021-04-01", 10 / CR, "relief lapsed; https://zerodha.com/charges/ (Rs10/crore, Nov-2026)", "high"),
    ],
    # Stamp duty, buy side only, on premium.
    "stamp_buy": [
        ("2019-01-01", 0.00002, "pre-Jul-2020 duty was state-wise (approx; Maharashtra-like). Not a uniform rate", "low"),
        ("2020-07-01", 0.00003, "Indian Stamp Act amendment (Finance Act 2019), uniform 0.003% buy side; https://zerodha.com/charges/", "high"),
    ],
    # GST on brokerage + exchange txn + IPFT + SEBI fee.
    "gst": [
        ("2017-07-01", 0.18, "GST regime from 1-Jul-2017; https://zerodha.com/charges/", "high"),
    ],
}


def _to_date(d: DateLike) -> _dt.date:
    if isinstance(d, _dt.datetime):
        return d.date()
    if isinstance(d, _dt.date):
        return d
    return _dt.date.fromisoformat(str(d)[:10])


def rate(component: str, trade_date: DateLike) -> float:
    """Rate of `component` in force on trade_date."""
    return rate_row(component, trade_date)[1]


def rate_row(component: str, trade_date: DateLike) -> Row:
    d = _to_date(trade_date)
    rows = SCHEDULE[component]
    chosen = None
    for row in rows:
        if _to_date(row[0]) <= d:
            chosen = row
    if chosen is None:
        raise ValueError(f"{component}: no rate before {rows[0][0]} (requested {d})")
    return chosen


def round_trip_cost(
    trade_date: DateLike,
    exchange: str,
    buy_premium: float,
    sell_premium: float,
    lot_size: int,
    lots: int = 1,
    brokerage_per_order: float = 20.0,
    expired_itm: bool = False,
    intrinsic: float = 0.0,
    settlement_price: float | None = None,
    exit_date: DateLike | None = None,
    exercise_brokerage: bool = True,
    physical_settlement: bool = False,
) -> dict:
    """Costs (Rs) of buying `lots` option lots and exiting.

    Exit modes
    - sell_premium > 0, expired_itm=False: normal sell order.
    - sell_premium == 0, expired_itm=False: expired worthless; no sell order, no sell charges.
    - expired_itm=True: cash-settled exercise at expiry. No sell order on exchange; STT on
      exercise = rate * intrinsic * qty (on settlement_price * qty before 1-Sep-2019).
      Zerodha also charges brokerage on exercised contracts (exercise_brokerage=True).
      sell_premium is ignored.
    - physical_settlement=True with expired_itm (stock options): not modelled -> ValueError.

    trade_date sets buy-side rates; exit_date (default trade_date) sets exit-side rates.
    """
    ex = exchange.upper()
    if ex not in ("NSE", "BSE"):
        raise ValueError("exchange must be 'NSE' or 'BSE'")
    if buy_premium < 0 or sell_premium < 0 or intrinsic < 0:
        raise ValueError("premiums/intrinsic must be >= 0")
    if expired_itm and physical_settlement:
        raise ValueError(
            "Physically settled stock option held ITM to expiry is not modelled: it becomes a "
            "share delivery (full strike notional, delivery STT ~0.1%, delivery brokerage). "
            "BALROG should square off stock options before expiry. See research/out/p3_costs.md"
        )

    d_buy = _to_date(trade_date)
    d_exit = _to_date(exit_date) if exit_date is not None else d_buy
    qty = lot_size * lots
    buy_turn = buy_premium * qty

    sold = (not expired_itm) and sell_premium > 0
    sell_turn = sell_premium * qty if sold else 0.0

    txn_key = "nse_txn" if ex == "NSE" else "bse_txn"

    def txn(turn: float, d: _dt.date) -> float:
        return turn * rate(txn_key, d)

    def ipft(turn: float, d: _dt.date) -> float:
        return turn * rate("nse_ipft", d) if ex == "NSE" else 0.0

    n_orders = 1 + (1 if sold else 0) + (1 if (expired_itm and exercise_brokerage) else 0)
    brokerage = brokerage_per_order * n_orders

    exchange_txn = txn(buy_turn, d_buy) + txn(sell_turn, d_exit)
    ipft_amt = ipft(buy_turn, d_buy) + ipft(sell_turn, d_exit)
    sebi = buy_turn * rate("sebi_fee", d_buy) + sell_turn * rate("sebi_fee", d_exit)
    stamp = buy_turn * rate("stamp_buy", d_buy)

    stt_sell = sell_turn * rate("stt_option_sale", d_exit)
    stt_exercise = 0.0
    if expired_itm:
        r = rate("stt_option_exercise", d_exit)
        if d_exit < _dt.date(2019, 9, 1):
            if settlement_price is None:
                raise ValueError("before 2019-09-01 exercise STT is on settlement price; pass settlement_price")
            stt_exercise = r * settlement_price * qty
        else:
            stt_exercise = r * intrinsic * qty

    gst = rate("gst", d_buy) * (brokerage + exchange_txn + ipft_amt + sebi)
    total = brokerage + exchange_txn + ipft_amt + sebi + stamp + stt_sell + stt_exercise + gst
    return {
        "brokerage": brokerage,
        "exchange_txn": exchange_txn,
        "ipft": ipft_amt,
        "sebi_fee": sebi,
        "stamp_duty": stamp,
        "stt_sell": stt_sell,
        "stt_exercise": stt_exercise,
        "gst": gst,
        "total": total,
        "buy_turnover": buy_turn,
        "sell_turnover": sell_turn,
        "total_pct_of_buy_premium": (100.0 * total / buy_turn) if buy_turn else float("nan"),
    }


if __name__ == "__main__":  # quick worked examples
    for args in [("2026-11-13", "NSE", 100, 110, 65), ("2026-11-13", "NSE", 10, 11, 65),
                 ("2026-11-13", "NSE", 5, 5.5, 1500)]:
        r = round_trip_cost(*args)
        print(args, round(r["total"], 2), f"{r['total_pct_of_buy_premium']:.2f}%")
