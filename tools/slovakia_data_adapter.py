import argparse
import os
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from markets.price_column_names import AFRR_CAPACITY, FCR_SETTLEMENT


EXPECTED = {
    "DA": ("timestamp", "price"),
    "ID1": ("timestamp", "price"),
    "IDA1": ("timestamp", "price"),
    "IMB": ("timestamp", "price"),
}


def _ensure_output_dir(base: Path, market: str) -> Path:
    out = base / "marketdata" / market
    out.mkdir(parents=True, exist_ok=True)
    return out


def _normalize_time_price(
    df: pd.DataFrame,
    timestamp_col: str,
    price_col: str,
    day: str,
    freq: str,
) -> pd.DataFrame:
    tmp = df[[timestamp_col, price_col]].copy()
    tmp.columns = ["timestamp", "price"]
    tmp["timestamp"] = pd.to_datetime(tmp["timestamp"], errors="coerce")
    tmp["price"] = pd.to_numeric(tmp["price"], errors="coerce")
    tmp = tmp.dropna(subset=["timestamp"])
    tmp = tmp.sort_values("timestamp")

    if freq == "1H":
        expected_index = pd.date_range(start=f"{day} 00:00:00", periods=24, freq="1H")
    else:
        expected_index = pd.date_range(start=f"{day} 00:00:00", periods=96, freq="15min")

    tmp = tmp.set_index("timestamp").reindex(expected_index)
    tmp.index.name = "timestamp"
    tmp = tmp[["price"]]
    return tmp


def convert_da(raw_file: Path, output_base: Path, day: str, timestamp_col: str, price_col: str):
    df = pd.read_csv(raw_file) if raw_file.suffix.lower() == ".csv" else pd.read_excel(raw_file)
    out_df = _normalize_time_price(df, timestamp_col, price_col, day, "1H")
    out_file = _ensure_output_dir(output_base, "DA") / f"DA_{day}.csv"
    out_df.to_csv(out_file)
    print(f"Saved {out_file}")


def convert_qh_market(
    market: str,
    raw_file: Path,
    output_base: Path,
    day: str,
    timestamp_col: str,
    price_col: str,
):
    df = pd.read_csv(raw_file) if raw_file.suffix.lower() == ".csv" else pd.read_excel(raw_file)
    out_df = _normalize_time_price(df, timestamp_col, price_col, day, "15min")
    out_file = _ensure_output_dir(output_base, market) / f"{market}_{day}.csv"
    if market == "IDA1":
        # Keep compatibility with the original file naming in the repo.
        out_file = _ensure_output_dir(output_base, "IDA1") / f"IDA1 {day}.csv"
        out_df = out_df.rename(columns={"price": "0"})
    out_df.to_csv(out_file)
    print(f"Saved {out_file}")


def convert_fcr(raw_file: Path, output_base: Path, day: str, price_col: str):
    df = pd.read_csv(raw_file) if raw_file.suffix.lower() == ".csv" else pd.read_excel(raw_file)
    fcr_prices = pd.to_numeric(df[price_col], errors="coerce").dropna().reset_index(drop=True)
    fcr_prices = fcr_prices.iloc[:6]
    out = pd.DataFrame(
        {
            "DATE_FROM": [day] * len(fcr_prices),
            FCR_SETTLEMENT: fcr_prices.values,
        }
    )
    out_file = _ensure_output_dir(output_base, "FCR") / f"FCR_{day}.csv"
    out.to_csv(out_file, index=False)
    print(f"Saved {out_file}")


def convert_afrr_capacity(
    raw_file: Path,
    output_base: Path,
    day: str,
    product_col: str,
    price_col: str,
):
    df = pd.read_csv(raw_file) if raw_file.suffix.lower() == ".csv" else pd.read_excel(raw_file)
    tmp = df[[product_col, price_col]].copy()
    tmp.columns = ["PRODUCT", AFRR_CAPACITY]
    tmp["PRODUCT"] = tmp["PRODUCT"].astype(str)
    tmp[AFRR_CAPACITY] = pd.to_numeric(tmp[AFRR_CAPACITY], errors="coerce")
    tmp = tmp.dropna()
    tmp = tmp[tmp["PRODUCT"].str.contains("POS|NEG", case=False, regex=True)]
    tmp = tmp.head(12).reset_index(drop=True)
    tmp["DATE_FROM"] = day
    tmp["DATE_TO"] = day
    tmp["TYPE_OF_RESERVES"] = "aFRR"

    out = tmp[
        [
            "DATE_FROM",
            "DATE_TO",
            "TYPE_OF_RESERVES",
            "PRODUCT",
            AFRR_CAPACITY,
        ]
    ]
    out_file = _ensure_output_dir(output_base, "aFRR_capacity") / f"afrr_capacity_{day}.csv"
    out.to_csv(out_file)
    print(f"Saved {out_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert Slovak OKTE/SEPS exports to battery index input formats."
    )
    parser.add_argument("--workspace", default=".", help="Repo root path")
    parser.add_argument("--day", required=True, help="Delivery day, YYYY-MM-DD")

    parser.add_argument("--da-file", help="Raw DA csv/xlsx")
    parser.add_argument("--da-timestamp-col", default="timestamp")
    parser.add_argument("--da-price-col", default="price")

    parser.add_argument("--id1-file", help="Raw ID1 csv/xlsx")
    parser.add_argument("--id1-timestamp-col", default="timestamp")
    parser.add_argument("--id1-price-col", default="price")

    parser.add_argument("--ida1-file", help="Raw IDA1 csv/xlsx")
    parser.add_argument("--ida1-timestamp-col", default="timestamp")
    parser.add_argument("--ida1-price-col", default="price")

    parser.add_argument("--imb-file", help="Raw imbalance csv/xlsx")
    parser.add_argument("--imb-timestamp-col", default="timestamp")
    parser.add_argument("--imb-price-col", default="price")

    parser.add_argument("--fcr-file", help="Raw FCR capacity csv/xlsx")
    parser.add_argument("--fcr-price-col", default="price")

    parser.add_argument("--afrr-cap-file", help="Raw aFRR capacity csv/xlsx")
    parser.add_argument("--afrr-product-col", default="PRODUCT")
    parser.add_argument("--afrr-price-col", default="price")

    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    day = args.day

    if args.da_file:
        convert_da(
            Path(args.da_file),
            workspace,
            day,
            args.da_timestamp_col,
            args.da_price_col,
        )
    if args.id1_file:
        convert_qh_market(
            "ID1",
            Path(args.id1_file),
            workspace,
            day,
            args.id1_timestamp_col,
            args.id1_price_col,
        )
    if args.ida1_file:
        convert_qh_market(
            "IDA1",
            Path(args.ida1_file),
            workspace,
            day,
            args.ida1_timestamp_col,
            args.ida1_price_col,
        )
    if args.imb_file:
        convert_qh_market(
            "IMB",
            Path(args.imb_file),
            workspace,
            day,
            args.imb_timestamp_col,
            args.imb_price_col,
        )
    if args.fcr_file:
        convert_fcr(Path(args.fcr_file), workspace, day, args.fcr_price_col)
    if args.afrr_cap_file:
        convert_afrr_capacity(
            Path(args.afrr_cap_file),
            workspace,
            day,
            args.afrr_product_col,
            args.afrr_price_col,
        )


if __name__ == "__main__":
    main()
