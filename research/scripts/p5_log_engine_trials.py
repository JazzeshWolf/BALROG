"""Append ENGINE rows (registered=no) to research/trials_log.csv from a p5_calibration JSON (top bucket per class
and sample; one row per class x sample).  python -I research/scripts/p5_log_engine_trials.py research/out/p5_calibration.json"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
from research.lib import trials  # noqa: E402

d = json.load(open(sys.argv[1], encoding="utf-8"))
n = 0
for smp, key in (("OOS", "buckets"), ("IS", "IS_buckets")):
    for cls, v in d[smp]["summary"].items():
        top = next((r for r in d[key] if r["cls"] == cls and r["bucket"] == v["top_bucket"]), None)
        if not top:
            continue
        trials.log_trial(id="ENGINE", variant=f"{d['variant']}_top{top['bucket']}", registered="no", sample=smp, cls=cls,
                         style="swing", n_trades=top["n"], n_days=top["n_days"], gross_mean_R=top["gross_R"],
                         net_mean_R=top["net_R"], ci_lo=top["ci_lo"], ci_hi=top["ci_hi"],
                         verdict="monotone" if v["monotone"] else "not monotone",
                         notes=f"buyConviction calibration; spearman {v['spearman']:+.3f}; score bucket {top['bucket']}",
                         script="research/scripts/p5_calibrate.py")
        n += 1
print(f"logged {n} ENGINE rows")
