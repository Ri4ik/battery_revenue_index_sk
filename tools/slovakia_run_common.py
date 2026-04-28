import argparse
import os
from typing import List, Optional, Tuple


def result_json_name(day: str, market: str) -> str:
    """Filename used by analysismodes.single_market_analysis.process_day."""
    return f"{day}_results_{market}.json"


def day_outputs_complete(result_folder: str, day: str, market_list: List[str]) -> bool:
    for market in market_list:
        path = os.path.join(result_folder, result_json_name(day, market))
        if not os.path.isfile(path):
            return False
    return True


def parse_slovakia_run_args(description: str) -> Tuple[argparse.Namespace, List[str]]:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--start-day", default=None, help="YYYY-MM-DD (default: built-in range)")
    parser.add_argument("--end-day", default=None, help="YYYY-MM-DD (default: built-in range)")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip days that already have all result JSON files in the target folder.",
    )
    parser.add_argument(
        "--result-folder",
        default=None,
        help="With --resume: full path to an existing results directory (single-folder runs).",
    )
    parser.add_argument(
        "--resume-parent",
        default=None,
        help="With --resume: base path without _{energy}_{cycles} suffix (multi-config runs).",
    )
    args, unknown = parser.parse_known_args()
    return args, unknown


def resolve_result_folder_single(
    default_timestamped_name: str,
    resume: bool,
    result_folder: Optional[str],
) -> str:
    if resume:
        if not result_folder:
            raise SystemExit("--resume requires --result-folder <path>")
        result_folder = os.path.abspath(result_folder)
        if not os.path.isdir(result_folder):
            raise SystemExit(f"Result folder not found: {result_folder}")
        return result_folder
    return os.path.join("results", default_timestamped_name)


def resolve_result_folder_multi(
    energy: int,
    cycles: int,
    current_date: str,
    resume: bool,
    resume_parent: Optional[str],
) -> str:
    if resume:
        if not resume_parent:
            raise SystemExit("--resume requires --resume-parent <base_path> (no _{e}_{c} suffix)")
        base = os.path.abspath(resume_parent)
        return f"{base}_{energy}_{cycles}"
    return os.path.join(
        "results", f"Slovakia_SingleMarket_results_{current_date}_{energy}_{cycles}"
    )
