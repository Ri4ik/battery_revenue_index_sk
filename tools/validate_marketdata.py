from pathlib import Path
from typing import List, Optional

import pandas as pd


def _check_file_points(file_path: Path, expected_points: int, market: str) -> Optional[str]:
    if not file_path.exists():
        return f"[{market}] missing file: {file_path.name}"
    try:
        df = pd.read_csv(file_path)
    except Exception as exc:
        return f"[{market}] cannot read {file_path.name}: {exc}"

    if "timestamp" not in df.columns:
        return f"[{market}] no 'timestamp' column in {file_path.name}"

    actual = df["timestamp"].dropna().shape[0]
    if actual != expected_points:
        return f"[{market}] {file_path.name}: expected {expected_points}, got {actual}"
    return None


def validate_required_marketdata(workspace: str, day_list: List[str], markets: List[str]):
    expected = {"DA": 24, "ID1": 96, "IDA1": 96, "IMB": 96}
    patterns = {
        "DA": "DA_{day}.csv",
        "ID1": "ID1_{day}.csv",
        "IDA1": "IDA1 {day}.csv",
        "IMB": "IMB_{day}.csv",
    }

    issues: List[str] = []
    marketdata_dir = Path(workspace) / "marketdata"

    for market in markets:
        if market not in expected:
            continue
        market_dir = marketdata_dir / market
        if not market_dir.exists():
            issues.append(f"[{market}] missing directory: {market_dir}")
            continue
        for day in day_list:
            file_path = market_dir / patterns[market].format(day=day)
            issue = _check_file_points(file_path, expected[market], market)
            if issue:
                issues.append(issue)

    if issues:
        preview = "\n".join(issues[:20])
        extra = "" if len(issues) <= 20 else f"\n... and {len(issues) - 20} more issue(s)"
        raise ValueError(
            "Market data validation failed before calculation.\n"
            f"{preview}{extra}"
        )

    print(
        "Market data validation passed for markets: "
        + ", ".join([m for m in markets if m in expected])
    )

