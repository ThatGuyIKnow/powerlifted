#!/usr/bin/env python3
"""External memory monitor for the planner.

Samples the resident-memory high-water mark of a process subtree from /proc and,
every time a watched process crosses a new `step_mb` boundary, appends a row to
the log file; on exit it writes the busiest process's peak. It watches `VmHWM`
(the kernel's monotonic peak RSS), so each boundary is reported once and the peak
survives an OOM kill of the planner -- unlike the binary's end-of-run report,
which is lost when the process is killed.

It runs *outside* the planner process so it cannot perturb the planner's timing.
The wrappers (run_search.py, run_with_mem_monitor.py) call `monitor()` in a
thread of the launcher process -- a different process from the planner, with
~0 ms startup so even sub-second runs are sampled. This file is also runnable
standalone (`mem_monitor.py --root-pid ...`) for manual use.

Because it samples, it records peak RSS *as observed at the poll interval*: a
spike that rises and falls within one interval can be missed. For 100 MB
granularity a ~50 ms interval makes this effectively exact. Capturing individual
allocation events instead would require in-process malloc hooks, which an
external monitor deliberately avoids.

MPI note: pass the mpiexec pid as `root_pid` and (optionally) the binary
basename as `match`; the monitor watches every descendant of the root plus any
process whose `comm` equals `match`, i.e. all ranks, and the peak is the max
over them (the "busiest rank").
"""
from __future__ import annotations

import argparse
import os
import signal
import threading
import time


def _all_pids():
    return [int(p) for p in os.listdir("/proc") if p.isdigit()]


def _ppid(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
        # comm is parenthesised and may contain spaces/parens; ppid is the
        # second field after the closing paren (state is the first).
        after = data[data.rfind(")") + 2:].split()
        return int(after[1])
    except (OSError, ValueError, IndexError):
        return None


def _comm(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip()
    except OSError:
        return ""


def _vmhwm_kb(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except (OSError, ValueError):
        return None
    return None


def _watched(root, match):
    """Descendants of root (inclusive) plus any process whose comm == match."""
    children = {}
    for pid in _all_pids():
        pp = _ppid(pid)
        if pp is not None:
            children.setdefault(pp, []).append(pid)
    seen, stack = set(), [root]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stack.extend(children.get(p, ()))
    if match:
        # Linux comm is truncated to 15 chars (TASK_COMM_LEN).
        m = match[:15]
        seen.update(pid for pid in _all_pids() if _comm(pid) == m)
    return seen


def monitor(root_pid, *, match=None, step_mb=100, interval_ms=50,
            rediscover_ms=1000, log_path="memlog.csv", peak_path="mempeak.txt",
            stop_event=None, renice=True):
    """Poll until the root pid disappears (or stop_event is set), then write the
    peak summary. Safe to run in a thread of the launcher process."""
    if renice:
        try:
            os.nice(19)  # deprioritise this thread; it mostly sleeps anyway
        except OSError:
            pass

    step_kb = step_mb * 1024
    interval = interval_ms / 1000.0
    rediscover = rediscover_ms / 1000.0
    t0 = time.monotonic()

    next_thr: dict[int, int] = {}   # pid -> next unreported boundary (kB)
    peak_kb: dict[int, int] = {}    # pid -> peak VmHWM (kB)
    comm: dict[int, str] = {}       # pid -> label

    pids: set[int] = set()
    last_discover = -1e9
    with open(log_path, "w", buffering=1) as log:
        log.write("t_ms,pid,comm,vmhwm_mb\n")
        while True:
            now = time.monotonic()
            root_alive = os.path.exists(f"/proc/{root_pid}")
            if now - last_discover >= rediscover or not pids:
                pids = _watched(root_pid, match)
                last_discover = now
            for pid in pids:
                hwm = _vmhwm_kb(pid)
                if hwm is None:
                    continue
                comm.setdefault(pid, _comm(pid))
                next_thr.setdefault(pid, step_kb)
                if hwm > peak_kb.get(pid, 0):
                    peak_kb[pid] = hwm
                while hwm >= next_thr[pid]:
                    log.write(f"{(now - t0) * 1000:.0f},{pid},{comm[pid]},{next_thr[pid] // 1024}\n")
                    next_thr[pid] += step_kb
            if not root_alive:
                break  # planner finished (or was killed); peaks already captured
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(interval)

    busiest_pid = max(peak_kb, key=peak_kb.get, default=0)
    with open(peak_path, "w") as f:
        f.write(f"busiest_rank_peak_mb={peak_kb.get(busiest_pid, 0) / 1024:.1f}\n")
        f.write(f"busiest_pid={busiest_pid}\n")
        f.write(f"num_procs={len(peak_kb)}\n")
        f.write(f"sum_peak_mb={sum(peak_kb.values()) / 1024:.1f}\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root-pid", type=int, required=True)
    ap.add_argument("--match", default=None)
    ap.add_argument("--step-mb", type=int, default=100)
    ap.add_argument("--interval-ms", type=int, default=50)
    ap.add_argument("--rediscover-ms", type=int, default=1000)
    ap.add_argument("--log", default="memlog.csv")
    ap.add_argument("--peak", default="mempeak.txt")
    args = ap.parse_args()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    monitor(args.root_pid, match=args.match, step_mb=args.step_mb,
            interval_ms=args.interval_ms, rediscover_ms=args.rediscover_ms,
            log_path=args.log, peak_path=args.peak, stop_event=stop)


if __name__ == "__main__":
    main()
