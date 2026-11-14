"""The vectorised calibration twin (research/lib/p5_engine_vec.py) must equal the stdlib engine."""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from engine import buy_conviction as bc  # noqa: E402
from research.lib import p5_engine_vec as ev  # noqa: E402


def test_vec_matches_scalar():
    m = bc.load_model()
    rng = np.random.default_rng(0)
    for grp, gm in m["groups"].items():
        n = 400
        X = {"friction": rng.uniform(0, 10, n), "delta": rng.uniform(0.05, 0.8, n),
             "dte": rng.choice(m["dte_levels"], n).astype(object), "rmr": rng.uniform(0.05, 1.2, n),
             "ivpct": np.where(rng.random(n) < 0.3, np.nan, rng.uniform(0, 100, n)),
             "vrp": rng.uniform(0.5, 1.8, n), "v3": np.where(rng.random(n) < 0.2, np.nan, rng.integers(0, 2, n))}
        X = {k: v for k, v in X.items() if k in gm["components"]}
        pv, _ = ev.predict(gm, X)
        sv = ev.to_score(pv, m["mapping"])
        for i in range(n):
            vals = {k: (None if isinstance(X[k][i], float) and np.isnan(X[k][i]) else X[k][i]) for k in X}
            p, _, _ = bc.predict(gm, vals)
            assert abs(p - pv[i]) < 1e-9
            assert abs(bc.to_score(p, m["mapping"]) - sv[i]) < 1e-6
