"""Append-only trial ledger: research/trials_log.csv (02_hypotheses.md §7).

log_trial(**row) appends one row under an exclusive lock file (safe across processes; Windows + POSIX).
Unknown keys raise; missing keys are written empty; run_ts defaults to now (IST, ISO).
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_PATH = os.path.join(ROOT, "research", "trials_log.csv")
COLUMNS = ["id", "variant", "registered", "sample", "cls", "style", "n_trades", "n_days", "gross_mean_R",
           "net_mean_R", "ci_lo", "ci_hi", "p_vs_b1", "net_mean_R_2x_spread", "verdict", "notes", "run_ts", "script"]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        return f"{v:.6g}"
    return str(v)


def _acquire(lock, timeout=60.0, stale=120.0):
    t0 = time.time()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            return fd
        except (FileExistsError, PermissionError):
            try:
                if time.time() - os.path.getmtime(lock) > stale:
                    os.remove(lock)
                    continue
            except OSError:
                pass
            if time.time() - t0 > timeout:
                raise TimeoutError(f"could not lock {lock}")
            time.sleep(0.05)


def log_trial(path: str | None = None, **row) -> dict:
    path = path or LOG_PATH
    bad = set(row) - set(COLUMNS)
    if bad:
        raise KeyError(f"unknown trial columns: {sorted(bad)}")
    row = dict(row)
    row.setdefault("run_ts", _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow([_fmt(row.get(c)) for c in COLUMNS])
    line = buf.getvalue()
    lock = path + ".lock"
    fd = _acquire(lock)
    try:
        new = (not os.path.exists(path)) or os.path.getsize(path) == 0
        with open(path, "a", encoding="utf-8", newline="") as f:
            if new:
                f.write(",".join(COLUMNS) + "\n")
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.close(fd)
        try:
            os.remove(lock)
        except OSError:
            pass
    return row


def n_trials(path: str | None = None) -> int:
    path = path or LOG_PATH
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)
