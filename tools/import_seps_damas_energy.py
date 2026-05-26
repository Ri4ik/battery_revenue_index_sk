"""
Import SEPS/Damas "Regulacna elektrina (denna)" Excel exports.

The Damas export does not expose a public API in this project flow, so this
parser ingests downloaded XLSX files and stores:

* raw copies under marketdata/SepsDamasEnergy/raw,
* one normalized combined aFRR CSV,
* one daily aFRR CSV per local market day.

The aFRR sheet contains the data needed for aFRR energy settlement:
activated Up/Down volume [MWh] and standard Up/Down price.
"""

import argparse
import csv
import os
import re
import shutil
import sys
from pathlib import Path

import pandas as pd


SERVICE_SHEET = "aFRR"


def slugify(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = value.strip("._-")
    return value or "file"


def parse_date(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date()
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return pd.to_datetime(text, format=fmt).date()
        except Exception:
            pass
    return pd.to_datetime(text, dayfirst=True).date()


def parse_float(value):
    if pd.isna(value):
        return 0.0
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def local_timestamp(day, hour, quarter_in_hour):
    minutes = (int(hour) - 1) * 60 + (int(quarter_in_hour) - 1) * 15
    return pd.Timestamp(day) + pd.Timedelta(minutes=minutes)


def parse_afrr_sheet(path, source_name):
    raw = pd.read_excel(str(path), sheet_name=SERVICE_SHEET, header=None, engine="openpyxl")
    rows = []
    current_day = None
    current_hour = None

    # First 5 rows are a multi-line report header:
    # row 0: article reference, row 1: source type, row 2: direction,
    # row 3/4: volume and price labels.
    for _, row in raw.iloc[5:].iterrows():
        values = list(row.values) + [None] * 8
        if pd.notna(values[0]):
            current_day = parse_date(values[0])
        if pd.notna(values[1]):
            current_hour = int(values[1])
        if pd.isna(values[2]) or current_day is None or current_hour is None:
            continue

        try:
            qh_in_hour = int(values[2])
        except (TypeError, ValueError):
            continue

        timestamp = local_timestamp(current_day, current_hour, qh_in_hour)
        up_mwh = parse_float(values[3])
        up_price = parse_float(values[4])
        down_mwh = parse_float(values[5])
        down_price = parse_float(values[6])
        quarter_hour = (current_hour - 1) * 4 + qh_in_hour

        rows.append(
            {
                "source_file": source_name,
                "service": "aFRR",
                "source_type": "Generation",
                "date": current_day.isoformat(),
                "timestamp_local": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                "hour": current_hour,
                "quarter_in_hour": qh_in_hour,
                "quarter_hour": quarter_hour,
                "up_volume_mwh": up_mwh,
                "up_price_eur_mwh": up_price,
                "down_volume_mwh": down_mwh,
                "down_price_eur_mwh": down_price,
                "up_revenue_eur": up_mwh * up_price,
                "down_revenue_eur": down_mwh * down_price,
                "total_energy_revenue_eur": up_mwh * up_price + down_mwh * down_price,
                "net_volume_mwh": up_mwh - down_mwh,
                "gross_volume_mwh": up_mwh + down_mwh,
            }
        )
    return rows


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def dedupe_rows(rows):
    deduped = {}
    for row in rows:
        key = (row["date"], int(row["hour"]), int(row["quarter_in_hour"]))
        if key not in deduped:
            deduped[key] = row
    return list(deduped.values())


def copy_raw(input_path, raw_dir, index):
    raw_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_path).resolve()
    if input_path.parent == raw_dir.resolve():
        return input_path
    name = "{:02d}_{}".format(index, slugify(Path(input_path).name))
    dest = raw_dir / name
    shutil.copyfile(str(input_path), str(dest))
    return dest


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Parse downloaded SEPS/Damas daily regulation-energy XLSX exports."
    )
    parser.add_argument("--workspace", default=".")
    parser.add_argument(
        "inputs",
        nargs="+",
        help="XLSX files downloaded from Damas: 'Regulacna elektrina (denna)'.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    workspace = Path(args.workspace).resolve()
    base_dir = workspace / "marketdata" / "SepsDamasEnergy"
    raw_dir = base_dir / "raw"
    norm_dir = base_dir / "normalized"
    daily_dir = base_dir / "daily"

    all_rows = []
    copied = []
    for index, input_name in enumerate(args.inputs, start=1):
        input_path = Path(input_name)
        if not input_path.is_file():
            print("Missing input file: {}".format(input_name), file=sys.stderr)
            return 2
        raw_copy = copy_raw(input_path, raw_dir, index)
        copied.append(raw_copy)
        all_rows.extend(parse_afrr_sheet(raw_copy, raw_copy.name))

    if not all_rows:
        print("No aFRR rows parsed.", file=sys.stderr)
        return 1

    all_rows = dedupe_rows(all_rows)
    all_rows.sort(key=lambda row: (row["timestamp_local"], row["source_file"]))
    fields = [
        "source_file",
        "service",
        "source_type",
        "date",
        "timestamp_local",
        "hour",
        "quarter_in_hour",
        "quarter_hour",
        "up_volume_mwh",
        "up_price_eur_mwh",
        "down_volume_mwh",
        "down_price_eur_mwh",
        "up_revenue_eur",
        "down_revenue_eur",
        "total_energy_revenue_eur",
        "net_volume_mwh",
        "gross_volume_mwh",
    ]

    dates = sorted(set(row["date"] for row in all_rows))
    combined_path = norm_dir / "seps_damas_afrr_energy_{}_{}.csv".format(
        dates[0], dates[-1]
    )
    write_csv(combined_path, all_rows, fields)

    daily_written = []
    by_day = {}
    for row in all_rows:
        by_day.setdefault(row["date"], []).append(row)
    for day, rows in sorted(by_day.items()):
        path = daily_dir / "seps_damas_afrr_energy_{}.csv".format(day)
        write_csv(path, rows, fields)
        daily_written.append(path)

    report_lines = [
        "SEPS/Damas aFRR energy import",
        "inputs={}".format(len(args.inputs)),
        "raw_files={}".format(len(copied)),
        "rows={}".format(len(all_rows)),
        "days={}".format(len(dates)),
        "date_from={}".format(dates[0]),
        "date_to={}".format(dates[-1]),
        "combined_csv={}".format(combined_path),
        "daily_files={}".format(len(daily_written)),
    ]
    for path in copied:
        report_lines.append("raw={}".format(path))
    report_path = base_dir / "import_report_{}_{}.txt".format(dates[0], dates[-1])
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    print("Report:", report_path)
    print("Combined:", combined_path)
    print("Daily files:", len(daily_written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
