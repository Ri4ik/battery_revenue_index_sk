"""
Watch Slovakia battery-config calculations and restart if they stop.

Logs folder completion to results/calc_watch.log and stdout.

Usage (from repo root):
    .venv\\Scripts\\python.exe tools/watch_battery_calc.py
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import pandas as pd

from tools.slovakia_run_common import day_outputs_complete

RESULTS_BASE = "Slovakia_2025-2026 (01.03)"
START_DAY = "2025-03-01"
END_DAY = "2026-03-01"
CONFIGS = [(1, 2), (2, 1), (2, 2)]
MARKETS = ["DA", "IDM15", "IDM60", "IMB", "FCR", "aFRR"]
WORKERS = max(1, (os.cpu_count() or 2) - 1)
POLL_SECONDS = 90
STALE_SECONDS = 300

LOG_PATH = _REPO / "results" / "calc_watch.log"
PYTHON = _REPO / ".venv" / "Scripts" / "python.exe"
CALC_SCRIPT = _REPO / "calculation_config_slovakia.py"


def _log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _folder_path(energy: int, cycles: int) -> Path:
    return _REPO / "results" / f"{RESULTS_BASE}_{energy}_{cycles}"


def _day_list() -> List[str]:
    return (
        pd.date_range(start=START_DAY, end=END_DAY, freq="D")
        .strftime("%Y-%m-%d")
        .tolist()
    )


def folder_complete(folder: Path, days: List[str]) -> bool:
    if not folder.is_dir():
        return False
    for day in days:
        if not day_outputs_complete(str(folder), day, MARKETS):
            return False
    return True


def folder_progress(folder: Path) -> Tuple[int, int, Optional[str]]:
    if not folder.is_dir():
        return 0, len(_day_list()), None
    days = _day_list()
    done = sum(
        1 for day in days if day_outputs_complete(str(folder), day, MARKETS)
    )
    latest = None
    latest_mtime = 0.0
    for f in folder.glob("*_results_*.json"):
        mtime = f.stat().st_mtime
        if mtime > latest_mtime:
            latest_mtime = mtime
            latest = f.name
    return done, len(days), latest


def latest_mtime(folder: Path) -> float:
    if not folder.is_dir():
        return 0.0
    latest = 0.0
    for f in folder.glob("*_results_*.json"):
        latest = max(latest, f.stat().st_mtime)
    return latest


def calc_already_running() -> bool:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | "
                "Where-Object { $_.CommandLine -like '*calculation_config_slovakia.py*' })"
                ".Count",
            ],
            text=True,
            timeout=20,
        ).strip()
        return int(out or "0") > 0
    except Exception:
        return False


def start_calculation() -> subprocess.Popen:
    only = ",".join(f"{e}_{c}" for e, c in CONFIGS)
    cmd = [
        str(PYTHON),
        str(CALC_SCRIPT),
        "--start-day",
        START_DAY,
        "--end-day",
        END_DAY,
        "--resume",
        "--resume-parent",
        f"results/{RESULTS_BASE}",
        "--only-configs",
        only,
        "--workers",
        str(WORKERS),
    ]
    _log(f"Starting calculation: workers={WORKERS}, configs={only}")
    return subprocess.Popen(
        cmd,
        cwd=str(_REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> None:
    days = _day_list()
    completed: Set[Tuple[int, int]] = set()
    proc = None  # type: Optional[subprocess.Popen]
    last_activity = time.time()
    last_seen_mtime = 0.0

    _log("Watch started.")

    while True:
        all_done = True
        for energy, cycles in CONFIGS:
            folder = _folder_path(energy, cycles)
            if folder_complete(folder, days):
                key = (energy, cycles)
                if key not in completed:
                    completed.add(key)
                    _log(
                        f"DONE folder results/{folder.name} "
                        f"({energy} h / {cycles} cycles/day)"
                    )
            else:
                all_done = False
                done, total, latest = folder_progress(folder)
                _log(
                    f"Progress {folder.name}: {done}/{total} days complete"
                    + (f", latest file {latest}" if latest else "")
                )

        if all_done:
            _log("All target folders complete. Watch exiting.")
            if proc and proc.poll() is None:
                proc.terminate()
            return

        mtimes = [latest_mtime(_folder_path(e, c)) for e, c in CONFIGS]
        newest = max(mtimes) if mtimes else 0.0
        if newest > last_seen_mtime:
            last_seen_mtime = newest
            last_activity = time.time()

        running_externally = calc_already_running()
        owns_proc = proc is not None and proc.poll() is None

        if not owns_proc and not running_externally:
            if proc is not None:
                _log(f"Calculation process exited (code={proc.returncode}). Restarting...")
            proc = start_calculation()
            last_activity = time.time()
        elif not owns_proc and running_externally:
            _log("Calculation running externally. Watching file progress...")
        elif owns_proc and time.time() - last_activity > STALE_SECONDS:
            _log(
                f"No new files for {STALE_SECONDS}s. Restarting owned calculation..."
            )
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            proc = None
        elif not owns_proc and not running_externally and time.time() - last_activity > STALE_SECONDS:
            _log(f"No activity for {STALE_SECONDS}s and no process. Restarting...")
            proc = start_calculation()
            last_activity = time.time()

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
