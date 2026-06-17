#!/usr/bin/env python3
"""Run an arbitrary command under the external memory monitor (mem_monitor.py).

Generic counterpart to `run_search.py --mem-monitor`, for binaries that are NOT
launched through run_search.py (e.g. the base-Tyr baseline, Powerlifted). It
Popens the command and runs mem_monitor.monitor() in a thread of THIS process
(separate from the planner process, ~0 ms startup so even sub-second runs are
sampled). The monitor self-exits when the command's pid disappears; we wait for
it to flush memlog.csv / mempeak.txt before returning the command's exit code.

Usage:
    run_with_mem_monitor.py [--step-mb 100] [--interval-ms 50]
                            [--match BASENAME] [--log memlog.csv]
                            [--peak mempeak.txt] -- <command> [args...]
"""
from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mem_monitor  # noqa: E402  (resolved next to this script)


def main() -> int:
    argv = sys.argv[1:]
    if "--" in argv:
        sep = argv.index("--")
        opt_args, command = argv[:sep], argv[sep + 1:]
    else:
        opt_args, command = argv, []

    ap = argparse.ArgumentParser(description="Run a command under mem_monitor.py")
    ap.add_argument("--step-mb", type=int, default=100)
    ap.add_argument("--interval-ms", type=int, default=50)
    ap.add_argument("--match", default=None,
                    help="also watch processes whose comm equals this (e.g. binary basename)")
    ap.add_argument("--log", default="memlog.csv")
    ap.add_argument("--peak", default="mempeak.txt")
    args = ap.parse_args(opt_args)

    if not command:
        print("error: no command given after '--'", file=sys.stderr)
        return 2

    proc = subprocess.Popen(command)

    stop = threading.Event()
    thread = threading.Thread(
        target=mem_monitor.monitor,
        args=(proc.pid,),
        kwargs=dict(match=args.match, step_mb=args.step_mb,
                    interval_ms=args.interval_ms, log_path=args.log,
                    peak_path=args.peak, stop_event=stop),
        daemon=True,
    )
    thread.start()

    # On a graceful job kill (e.g. lab wall-time SIGTERM), stop the monitor so it
    # flushes its summary, then re-raise default behaviour.
    def _on_term(*_):
        stop.set()
    signal.signal(signal.SIGTERM, _on_term)

    try:
        proc.wait()
    finally:
        stop.set()
        thread.join(timeout=max(2.0, args.interval_ms / 1000.0 * 5))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
