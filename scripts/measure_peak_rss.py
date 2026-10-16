#!/usr/bin/env python3
"""Measure peak resident memory for capacity sizing and multi-year dispatch.

The sampler includes this process and its descendants, including worker processes.
Usage: `PYTHONPATH=. python3 scripts/measure_peak_rss.py --phase both`.
Requires bundled NEM availability and price caches; `/proc` sampling is Linux-specific."""
from __future__ import annotations

import argparse
import dataclasses
import gc
import glob
import os
import threading
import time

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def _visible_pid() -> int:
    """Use the PID exposed by /proc, including inside PID-isolated runners."""
    try:
        with open("/proc/self/stat") as fh:
            return int(fh.read().split()[0])
    except (OSError, ValueError):
        return os.getpid()


def _rss_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/statm") as fh:
            return int(fh.read().split()[1]) * PAGE_SIZE / (1024 * 1024)
    except (OSError, ValueError, IndexError):
        return 0.0


def _pss_mb(pid: int) -> float:
    """Proportional memory avoids counting shared library pages twice."""
    try:
        with open(f"/proc/{pid}/smaps_rollup") as fh:
            for line in fh:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return 0.0


def _descendants(pid: int) -> list[int]:
    """Read parent PIDs even where /proc/task/children is unavailable."""
    children: dict[int, list[int]] = {}
    for name in glob.glob("/proc/[0-9]*/stat"):
        try:
            with open(name) as fh:
                raw = fh.read()
            child = int(raw.split()[0])
            parent = int(raw.rsplit(") ", 1)[1].split()[1])
            children.setdefault(parent, []).append(child)
        except (OSError, ValueError, IndexError):
            continue
    found = set()
    pending = list(children.get(pid, []))
    while pending:
        child = pending.pop()
        if child in found:
            continue
        found.add(child)
        pending.extend(children.get(child, []))
    return list(found)


class PeakSampler:
    """Sample total RSS (self + descendants) on a background thread."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self.peak_mb = 0.0
        self.peak_pss_mb = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run(self) -> None:
        me = _visible_pid()
        while not self._stop.is_set():
            pids = [me, *_descendants(me)]
            total = sum(_rss_mb(p) for p in pids)
            self.peak_mb = max(self.peak_mb, total)
            self.peak_pss_mb = max(self.peak_pss_mb, sum(_pss_mb(p) for p in pids))
            self._stop.wait(self.interval)

    def __enter__(self) -> "PeakSampler":
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", default="corporate_ppa")
    ap.add_argument("--years", type=int, default=None)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--phase", default="both", choices=["sizing", "dispatch", "both"])
    ap.add_argument("--method", default="full_hourly",
                    choices=["tsam", "full_hourly", "coarse"])
    args = ap.parse_args()

    from ppa.data import nem_data
    from ppa.multi_year import run_multi_year
    from ppa.scenario import CASE_STUDIES_BY_ID, load_case_study
    from ppa.sizing import (
        apply_sizing,
        build_sizing_timeseries,
        run_sizing_subprocess,
        weather_cycle_years,
    )

    case = CASE_STUDIES_BY_ID.get(args.case)
    if case is None:
        raise SystemExit(f"unknown case '{args.case}'; have {list(CASE_STUDIES_BY_ID)}")

    scn = load_case_study(case)
    scn = dataclasses.replace(scn, optimise_capacity=True, sizing_method=args.method)
    if args.years:
        scn = dataclasses.replace(scn, simulation_years=args.years)

    pv, wind, prices = nem_data.get_timeseries_dicts(scn)

    print(f"Case: {case.name} · {scn.simulation_years} year(s) · sizing {args.method} "
          f"· workers {args.workers}")
    print(f"baseline RSS after data load: {_rss_mb(_visible_pid()):,.0f} MB\n")

    sized = None
    if args.phase in ("sizing", "both"):
        n_sizing_years, _ = weather_cycle_years(
            scn.simulation_years, len(pv), len(prices)
        )
        sizing_ts = build_sizing_timeseries(scn, pv, wind, prices, n_sizing_years)
        t0 = time.monotonic()
        with PeakSampler() as sampler:
            sized = run_sizing_subprocess(sizing_ts, scn)
        print(f"SIZING   peak RSS {sampler.peak_mb:8,.0f} MB   "
              f"peak PSS {sampler.peak_pss_mb:,.0f} MB   "
              f"{time.monotonic() - t0:6.1f}s   status={sized.status}")
        # Release the sizing inputs before forking dispatch workers.
        del sizing_ts
        gc.collect()
        print(f"         parent RSS after release: {_rss_mb(_visible_pid()):,.0f} MB")

    if args.phase in ("dispatch", "both"):
        sim_scn = apply_sizing(scn, sized) if sized is not None else scn
        sim_scn = dataclasses.replace(sim_scn, optimise_capacity=False)
        t0 = time.monotonic()
        with PeakSampler() as sampler:
            run_multi_year(
                scenario=sim_scn,
                pv_cf_by_year=pv,
                wind_cf_by_year=wind,
                prices_by_year=prices,
                first_sim_year=sim_scn.first_sim_year,
                max_workers=args.workers,
            )
        print(f"DISPATCH peak RSS {sampler.peak_mb:8,.0f} MB   "
              f"{time.monotonic() - t0:6.1f}s")

    print(f"\nfinal parent RSS: {_rss_mb(_visible_pid()):,.0f} MB")


if __name__ == "__main__":
    main()
