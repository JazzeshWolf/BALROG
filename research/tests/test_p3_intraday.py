import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.intraday import clean_session, opening_range, resample_bars, session_mask  # noqa: E402


def make_day(date="2024-03-05", n=375, seed=0, start="09:15"):
    rng = np.random.default_rng(seed)
    ts = pd.date_range(f"{date} {start}", periods=n, freq="min", tz="Asia/Kolkata")
    c = 100 + rng.normal(0, 0.1, n).cumsum()
    o = np.r_[100, c[:-1]]
    h = np.maximum(o, c) + rng.uniform(0, 0.05, n)
    lo = np.minimum(o, c) - rng.uniform(0, 0.05, n)
    v = rng.integers(1, 1000, n)
    return pd.DataFrame({"ts": ts, "open": o, "high": h, "low": lo, "close": c, "volume": v})


def two_days():
    return pd.concat([make_day("2024-03-05", seed=1), make_day("2024-03-06", seed=2)], ignore_index=True)


def test_session_filter_drops_pre_post_and_duplicates():
    d = make_day()
    extra = make_day(n=10, start="09:00")          # pre-open 09:00-09:09
    post = make_day(n=5, start="15:30")            # 15:30-15:34
    dup = d.iloc[[10, 200]].copy()
    raw = pd.concat([d, extra, post, dup], ignore_index=True).sample(frac=1, random_state=0)
    out, n_dup, n_out = clean_session(raw)
    assert len(out) == 375 and n_dup == 2 and n_out == 15
    assert out.ts.is_monotonic_increasing
    assert out.ts.dt.strftime("%H:%M").iloc[[0, -1]].tolist() == ["09:15", "15:29"]
    assert session_mask(out.ts).all()


def test_session_filter_naive_timestamps_treated_as_ist():
    d = make_day()
    d["ts"] = d.ts.dt.tz_localize(None)
    out, _, _ = clean_session(d)
    assert str(out.ts.dt.tz) == "Asia/Kolkata" and len(out) == 375


@pytest.mark.parametrize("m", [5, 15])
def test_resample_matches_manual(m):
    d = two_days()
    r = resample_bars(d, m)
    assert len(r) == 2 * int(np.ceil(375 / m))
    for day, g in d.groupby(d.ts.dt.date):
        for i in range(0, 375, m):
            chunk = g.iloc[i:i + m]
            row = r[r.ts == chunk.ts.iloc[0]].iloc[0]
            assert row.open == chunk.open.iloc[0]
            assert row.close == chunk.close.iloc[-1]
            assert row.high == chunk.high.max()
            assert row.low == chunk.low.min()
            assert row.volume == chunk.volume.sum()
    # buckets anchored at 09:15, never crossing days
    assert set(r.ts.dt.strftime("%H:%M").head(2)) == {"09:15", f"09:{15 + m:02d}"}
    assert (r.ts.dt.date.value_counts() == int(np.ceil(375 / m))).all()


def test_resample_with_missing_minutes_keeps_anchor():
    d = make_day().drop(index=[0, 1, 7]).reset_index(drop=True)   # 09:15, 09:16, 09:22 missing
    r = resample_bars(d, 5)
    first = r.iloc[0]
    assert first.ts.strftime("%H:%M") == "09:15"
    assert first.open == d.open.iloc[0]          # 09:17 open
    assert first.volume == d.volume.iloc[:3].sum()  # 09:17-09:19


def test_opening_range_uses_only_bars_before_cutoff():
    d = two_days()
    orng = opening_range(d, 15)
    assert (orng.n_bars == 15).all()
    # spike exactly at the cutoff bar (09:30) and after must not affect the range
    spiked = d.copy()
    at_cut = spiked.ts.dt.strftime("%H:%M") >= "09:30"
    spiked.loc[at_cut, "high"] += 1000
    spiked.loc[at_cut, "low"] -= 1000
    o2 = opening_range(spiked, 15)
    pd.testing.assert_frame_equal(orng, o2)
    # and the last included bar (09:29) does affect it
    s3 = d.copy()
    s3.loc[s3.ts.dt.strftime("%H:%M") == "09:29", "high"] += 1000
    assert (opening_range(s3, 15).or_high > orng.or_high).all()
    first = d[d.ts.dt.date == d.ts.dt.date.iloc[0]].iloc[:15]
    assert orng.or_high.iloc[0] == first.high.max() and orng.or_low.iloc[0] == first.low.min()
    assert orng.available_at.dt.strftime("%H:%M").eq("09:30").all()


def test_opening_range_from_resampled_equals_1min():
    d = two_days()
    a = opening_range(d, 30)
    b = opening_range(resample_bars(d, 5), 30)
    np.testing.assert_allclose(a.or_high, b.or_high)
    np.testing.assert_allclose(a.or_low, b.or_low)
