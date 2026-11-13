"""Intraday helpers for 1-minute OHLCV frames.

Frame convention: column `ts` = tz-aware IST bar *start* time (09:15 bar covers
09:15:00-09:15:59), plus open/high/low/close/volume.  Regular session bars are
09:15..15:29 inclusive (375 bars).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

IST = "Asia/Kolkata"
SESSION_START = dt.time(9, 15)
SESSION_LAST_BAR = dt.time(15, 29)
BARS_PER_DAY = 375
OHLCV = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def _ensure_ist(ts: pd.Series) -> pd.Series:
    ts = pd.to_datetime(ts)
    if ts.dt.tz is None:
        return ts.dt.tz_localize(IST)
    return ts.dt.tz_convert(IST)


def session_mask(ts: pd.Series, start=SESSION_START, last_bar=SESSION_LAST_BAR) -> pd.Series:
    t = _ensure_ist(ts).dt.time
    return (t >= start) & (t <= last_bar)


def clean_session(df: pd.DataFrame, keep_out_of_session: bool = False):
    """Sort, drop duplicate timestamps (keep last), drop bars outside 09:15-15:29
    (unless keep_out_of_session, used for special sessions).  Returns
    (clean_df, n_duplicates_removed, n_out_of_session_removed)."""
    df = df.copy()
    df["ts"] = _ensure_ist(df["ts"])
    df = df.sort_values("ts", kind="stable")
    n0 = len(df)
    df = df.drop_duplicates("ts", keep="last")
    n_dup = n0 - len(df)
    if keep_out_of_session:
        n_out = 0
    else:
        m = session_mask(df["ts"])
        n_out = int((~m).sum())
        df = df[m]
    return df.reset_index(drop=True), n_dup, n_out


def resample_bars(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate 1-min bars into `minutes`-bars anchored at 09:15 each day
    (label = bar start, left-closed).  Buckets never cross days."""
    if minutes == 1:
        return df.reset_index(drop=True)
    d = df.copy()
    d["ts"] = _ensure_ist(d["ts"])
    day = d["ts"].dt.normalize()
    anchor = day + pd.Timedelta(hours=9, minutes=15)
    k = ((d["ts"] - anchor) // pd.Timedelta(minutes=minutes)).astype("int64")
    d["bucket"] = anchor + pd.to_timedelta(k * minutes, unit="m")
    out = d.groupby("bucket", sort=True).agg({c: f for c, f in OHLCV.items() if c in d.columns})
    out = out.reset_index().rename(columns={"bucket": "ts"})
    return out


def opening_range(df: pd.DataFrame, minutes: int = 15) -> pd.DataFrame:
    """Per day high/low of bars whose *start* is < 09:15 + minutes, i.e. bars that
    are complete by the cutoff.  `available_at` = the cutoff time: the range may only
    be used for decisions at or after that time (no lookahead)."""
    d = df.copy()
    d["ts"] = _ensure_ist(d["ts"])
    day = d["ts"].dt.normalize()
    cutoff = day + pd.Timedelta(hours=9, minutes=15 + minutes)
    d = d[(d["ts"] < cutoff) & (d["ts"] >= day + pd.Timedelta(hours=9, minutes=15))]
    g = d.groupby(d["ts"].dt.normalize())
    out = pd.DataFrame({"or_high": g["high"].max(), "or_low": g["low"].min(), "n_bars": g.size()})
    out.index.name = "date"
    out["available_at"] = out.index + pd.Timedelta(hours=9, minutes=15 + minutes)
    return out.reset_index()


def load_symbol(symbol: str, root=None, years=None) -> pd.DataFrame:
    from pathlib import Path
    root = Path(root) if root else Path(__file__).resolve().parents[2] / "data" / "intraday" / "1min"
    files = sorted((root / f"symbol={symbol}").glob("year=*.parquet"))
    if years is not None:
        files = [f for f in files if int(f.stem.split("=")[1]) in set(years)]
    if not files:
        return pd.DataFrame(columns=["ts", *OHLCV])
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
