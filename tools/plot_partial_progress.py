import argparse
import json
import os
import re
from collections import defaultdict

import matplotlib.pyplot as plt
import pandas as pd


FILE_RE = re.compile(r"(?P<day>\d{4}-\d{2}-\d{2})_results_(?P<market>.+)\.json$")


def load_daily_totals(results_dir: str) -> pd.DataFrame:
    totals = defaultdict(dict)

    for name in os.listdir(results_dir):
        match = FILE_RE.match(name)
        if not match:
            continue
        day = match.group("day")
        market = match.group("market")
        path = os.path.join(results_dir, name)
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        value = (
            payload.get("results", {})
            .get("daily_revenue", {})
            .get(market, 0.0)
        )
        try:
            value = float(value)
        except Exception:
            value = 0.0
        totals[day][market] = value

    if not totals:
        raise FileNotFoundError(f"No daily result json files in {results_dir}")

    df = pd.DataFrame.from_dict(totals, orient="index").sort_index().fillna(0.0)
    df.index = pd.to_datetime(df.index)
    return df


def plot_progress(df: pd.DataFrame, out_dir: str):
    os.makedirs(out_dir, exist_ok=True)

    total_daily = df.sum(axis=1)
    cumulative = total_daily.cumsum()

    plt.figure(figsize=(13, 5))
    colors = ["#2ca02c" if x >= 0 else "#d62728" for x in total_daily.values]
    plt.bar(total_daily.index, total_daily.values, color=colors)
    plt.title("Daily total revenue (all indices combined)")
    plt.xlabel("Day")
    plt.ylabel("EUR/day")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "01_daily_total_revenue.png"), dpi=150)
    plt.close()

    plt.figure(figsize=(13, 6))
    for market in sorted(df.columns):
        plt.plot(df.index, df[market].values, label=market)
    plt.title("Daily revenue by index")
    plt.xlabel("Day")
    plt.ylabel("EUR/day")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "02_daily_revenue_by_market.png"), dpi=150)
    plt.close()

    plt.figure(figsize=(13, 5))
    plt.plot(cumulative.index, cumulative.values, color="#1f77b4", linewidth=2)
    plt.title("Cumulative revenue (calculated period)")
    plt.xlabel("Day")
    plt.ylabel("EUR")
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "03_cumulative_revenue.png"), dpi=150)
    plt.close()

    # One clear chart for day-by-day comparison across indices + total.
    plt.figure(figsize=(14, 7))
    ordered_cols = sorted(df.columns)
    palette = {
        "DA": "#2ca02c",
        "IDA1": "#d62728",
        "ID1": "#9467bd",
        "IMB": "#ff7f0e",
    }
    for col in ordered_cols:
        plt.plot(
            df.index,
            df[col].values,
            marker="o",
            linewidth=2,
            markersize=4,
            label=col,
            color=palette.get(col),
            alpha=0.95,
        )
    plt.plot(
        total_daily.index,
        total_daily.values,
        marker="s",
        linewidth=2.5,
        markersize=5,
        label="TOTAL",
        color="#1f77b4",
    )
    plt.title("Daily index performance (EUR/day)")
    plt.xlabel("Day")
    plt.ylabel("EUR/day")
    plt.grid(alpha=0.25)
    plt.legend(ncol=5, loc="upper right")
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "00_daily_indices_one_chart.png"), dpi=160)
    plt.close()

    summary = {
        "days_count": int(len(df)),
        "from_day": df.index.min().strftime("%Y-%m-%d"),
        "to_day": df.index.max().strftime("%Y-%m-%d"),
        "total_revenue_eur": float(total_daily.sum()),
        "average_daily_revenue_eur": float(total_daily.mean()),
        "markets": sorted(df.columns.tolist()),
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Plot progress charts from partial result json files.")
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    df = load_daily_totals(results_dir)
    out_dir = os.path.join(results_dir, "progress_charts")
    plot_progress(df, out_dir)
    print(out_dir)


if __name__ == "__main__":
    main()

