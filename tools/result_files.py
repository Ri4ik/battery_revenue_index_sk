"""Strict reader for daily result JSON. Modified 2026-10-20 for safe imports."""
from datetime import date
import json
import math
from pathlib import Path
import re

import pandas as pd

RESULT_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_results_([A-Za-z0-9+]+)\.json$")
BATTERY_SUFFIX_RE = re.compile(r"^(.+)_(\d+)_(\d+)$")
FRAME_COLUMNS = ["day", "market", "daily_revenue_eur"]


def folder_has_results(folder: Path):
    return folder.is_dir() and any(RESULT_NAME_RE.fullmatch(f.name) for f in folder.glob("*.json"))


def parse_result_files(folder: Path, system_type=None, cycle_limit=None):
    rows = []
    for file in sorted(folder.glob("*_results_*.json")):
        try:
            match = RESULT_NAME_RE.fullmatch(file.name)
            if not match:
                raise ValueError("Invalid result filename.")
            day, market = match.groups()
            date.fromisoformat(day)
            with file.open(encoding="utf-8") as source:
                payload = json.load(source)
            revenue = payload["results"]["daily_revenue"]
            value = revenue.get("Total")
            if value is None:
                value = revenue.get(market)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Missing or non-finite numeric daily revenue.")
            config = payload.get("battery_config")
            if config is not None and system_type is not None:
                duration = float(config["energy"]) / float(config["power"])
                if duration != system_type or float(config["cycle_limit"]) != cycle_limit:
                    raise ValueError("Battery configuration does not match result folder.")
                if float(config["power"]) != 1:
                    raise ValueError("This dashboard expects the 1 MW benchmark.")
            rows.append({"day": day, "market": market, "daily_revenue_eur": float(value)})
        except (ValueError, TypeError, KeyError, ZeroDivisionError) as error:
            raise ValueError(f"Invalid result {file}: {error}") from error
    df = pd.DataFrame(rows, columns=FRAME_COLUMNS)
    df["day"] = pd.to_datetime(df["day"])
    return df
