import argparse
import json
import os
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import pandas as pd


def _load_market_file(path: str) -> Tuple[str, pd.Series, pd.Series]:
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    market_name = os.path.basename(path).split("_results_")[-1].replace(".json", "")
    market_results = payload.get("market_results", {})

    revenue_series = pd.Series(dtype=float)
    soc_series = pd.Series(dtype=float)

    for key, values in market_results.items():
        s = pd.Series(values)
        s.index = pd.to_datetime(s.index)
        s = pd.to_numeric(s, errors="coerce").fillna(0.0)
        if key.upper() == "SOC":
            soc_series = s
        else:
            revenue_series = s

    return market_name, revenue_series.sort_index(), soc_series.sort_index()


def _collect_data(results_dir: str) -> Tuple[Dict[str, pd.Series], pd.Series]:
    files = [
        os.path.join(results_dir, f)
        for f in os.listdir(results_dir)
        if f.endswith(".json") and "_results_" in f
    ]
    if not files:
        raise FileNotFoundError(f"V priecinku nie su JSON vysledky: {results_dir}")

    revenue_by_market: Dict[str, pd.Series] = {}
    soc_ref = pd.Series(dtype=float)

    for fp in sorted(files):
        market, revenue_s, soc_s = _load_market_file(fp)
        if not revenue_s.empty:
            revenue_by_market[market] = revenue_s
        if soc_ref.empty and not soc_s.empty:
            soc_ref = soc_s

    if not revenue_by_market:
        raise ValueError("Nenasli sa ziadne revenue casove rady.")

    return revenue_by_market, soc_ref


def _plot_daily_revenue_bar(out_dir: str, revenue_by_market: Dict[str, pd.Series]):
    totals = {m: float(s.sum()) for m, s in revenue_by_market.items()}
    df = pd.Series(totals).sort_values(ascending=False)

    plt.figure(figsize=(10, 5))
    bars = plt.bar(df.index, df.values)
    plt.title("Denný výnos podľa trhu")
    plt.xlabel("Trh")
    plt.ylabel("Výnos [EUR/deň]")
    plt.grid(axis="y", alpha=0.3)
    for bar in bars:
        h = bar.get_height()
        plt.text(bar.get_x() + bar.get_width() / 2, h, f"{h:.2f}", ha="center", va="bottom")
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "01_denny_vynos_podla_trhu.png"), dpi=150)
    plt.close()


def _plot_cumulative_revenue(out_dir: str, revenue_by_market: Dict[str, pd.Series]):
    plt.figure(figsize=(12, 6))
    for market, series in revenue_by_market.items():
        cum = series.cumsum()
        plt.plot(cum.index, cum.values, label=market)

    plt.title("Kumulatívny výnos počas dňa")
    plt.xlabel("Čas")
    plt.ylabel("Kumulatívny výnos [EUR]")
    plt.grid(alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "02_kumulativny_vynos.png"), dpi=150)
    plt.close()


def _plot_soc_and_revenue(out_dir: str, revenue_by_market: Dict[str, pd.Series], soc: pd.Series):
    # Choose the strongest market by total revenue for overlay.
    best_market = max(revenue_by_market.items(), key=lambda x: x[1].sum())[0]
    rev = revenue_by_market[best_market]

    fig, ax1 = plt.subplots(figsize=(12, 6))
    ax1.plot(rev.index, rev.values, color="tab:blue", label=f"Výnos ({best_market})")
    ax1.set_xlabel("Čas")
    ax1.set_ylabel("Výnos [EUR/interval]", color="tab:blue")
    ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax1.grid(alpha=0.25)

    if not soc.empty:
        ax2 = ax1.twinx()
        ax2.plot(soc.index, soc.values, color="tab:orange", label="SOC")
        ax2.set_ylabel("SOC [-]", color="tab:orange")
        ax2.tick_params(axis="y", labelcolor="tab:orange")
        ax2.set_ylim(0, 1)

    plt.title(f"Výnos a SOC (referenčný trh: {best_market})")
    fig.tight_layout()
    plt.savefig(os.path.join(out_dir, "03_vynos_a_soc.png"), dpi=150)
    plt.close(fig)


def _write_summary_sk(out_dir: str, revenue_by_market: Dict[str, pd.Series], soc: pd.Series):
    totals = {m: float(s.sum()) for m, s in revenue_by_market.items()}
    best_market = max(totals, key=totals.get)
    total_all = sum(totals.values())

    lines: List[str] = []
    lines.append("Stručné vysvetlenie výsledkov")
    lines.append("")
    lines.append(f"- Celkový denný výnos (súčet všetkých trhov): {total_all:.2f} EUR")
    lines.append(f"- Najsilnejší trh podľa denného výnosu: {best_market} ({totals[best_market]:.2f} EUR)")
    lines.append("- Graf 01: porovnanie denného výnosu medzi trhmi.")
    lines.append("- Graf 02: priebeh kumulatívneho výnosu počas dňa.")
    lines.append("- Graf 03: detail výkonu na najsilnejšom trhu spolu so SOC batérie.")
    if not soc.empty:
        lines.append(f"- Rozsah SOC počas dňa: min {soc.min():.3f}, max {soc.max():.3f}.")
    lines.append("")
    lines.append("Poznámka: ide o benchmarkový model (historické dáta + technické obmedzenia),")
    lines.append("nie o garantovanú predikciu zisku konkrétneho zariadenia.")

    with open(os.path.join(out_dir, "README_SK.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Vizualizacia vysledkov indexu v slovencine.")
    parser.add_argument("--results-dir", required=True, help="Priecinok s JSON vysledkami.")
    args = parser.parse_args()

    results_dir = os.path.abspath(args.results_dir)
    revenue_by_market, soc = _collect_data(results_dir)

    out_dir = os.path.join(results_dir, "charts")
    os.makedirs(out_dir, exist_ok=True)

    _plot_daily_revenue_bar(out_dir, revenue_by_market)
    _plot_cumulative_revenue(out_dir, revenue_by_market)
    _plot_soc_and_revenue(out_dir, revenue_by_market, soc)
    _write_summary_sk(out_dir, revenue_by_market, soc)

    print(out_dir)


if __name__ == "__main__":
    main()
