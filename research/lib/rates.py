"""Dated RBI policy repo-rate table (annual, decimal) used as the risk-free r for Black-76.

Source: RBI monetary-policy announcements (effective date = announcement date).
Precision is not critical: r only enters via discounting, and for <=90-day options a
25 bp error moves IV by well under 0.1 vol point.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

REPO_TABLE = [  # (effective_from, rate)
    ("2018-08-01", 0.0650),
    ("2019-02-07", 0.0625),
    ("2019-04-04", 0.0600),
    ("2019-06-06", 0.0575),
    ("2019-08-07", 0.0540),
    ("2019-10-04", 0.0515),
    ("2020-03-27", 0.0440),
    ("2020-05-22", 0.0400),
    ("2022-05-04", 0.0440),
    ("2022-06-08", 0.0490),
    ("2022-08-05", 0.0540),
    ("2022-09-30", 0.0590),
    ("2022-12-07", 0.0625),
    ("2023-02-08", 0.0650),
    ("2025-02-07", 0.0625),
    ("2025-04-09", 0.0600),
    ("2025-06-06", 0.0550),
    ("2025-12-05", 0.0525),  # verify against RBI before relying on 2026 values
]


def repo_rate(dates) -> np.ndarray:
    """Repo rate in force on each date (vectorised step lookup)."""
    d = pd.to_datetime(pd.Series(dates)).values.astype("datetime64[D]")
    eff = np.array([np.datetime64(x) for x, _ in REPO_TABLE])
    r = np.array([v for _, v in REPO_TABLE])
    idx = np.searchsorted(eff, d, side="right") - 1
    return r[np.clip(idx, 0, len(r) - 1)]
