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
import math
import os
import re
import shutil
import sys
from pathlib import Path

import pandas as pd


SERVICE_SHEET = "aFRR"
MAX_ABS_PRICE_EUR_MWH = 10000.0


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


def _priced_columns(raw):
    """Locate volume/price columns, including the archived nine-column export.

    Each direction can have special and standard price columns. The row reader
    accepts one populated price (or equal prices), never an arbitrary mixture.
    """
    first = None
    for index, value in raw.iloc[:, 0].items():
        if pd.isna(value):
            continue
        try:
            if parse_date(value) is not None:
                first = index
                break
        except (TypeError, ValueError):
            continue
    headers = [' '.join(str(v).lower() for v in raw.iloc[:first, col] if pd.notna(v))
               for col in range(raw.shape[1])]
    volumes = [col for col in range(3, raw.shape[1]) if 'objem' in headers[col] and 'mwh' in headers[col]]
    if len(volumes) == 2:
        result = []
        for i, volume in enumerate(volumes):
            stop = volumes[i + 1] if i + 1 < len(volumes) else raw.shape[1]
            prices = [col for col in range(volume + 1, stop) if 'cena' in headers[col]]
            if not prices:
                raise ValueError('Missing price header after SEPS volume column')
            result.append((volume, prices))
        return result
    if raw.shape[1] == 7:
        return [(3, [4]), (5, [6])]  # historical seven-column positional contract
    raise ValueError('Unrecognized SEPS price/volume headers; refusing positional import')


def parse_afrr_sheet(path, source_name):
    raw = pd.read_excel(str(path), sheet_name=SERVICE_SHEET, header=None, engine="openpyxl")
    if raw.shape[1] < 7:
        raise ValueError("aFRR export must contain date/hour/quarter and Up volume, Up price, Down volume, Down price; this file has only {} columns (volume-only exports cannot be used to calculate energy revenue)".format(raw.shape[1]))
    (up_volume_col, up_price_cols), (down_volume_col, down_price_cols) = _priced_columns(raw)
    rows = []
    current_day = None
    current_hour = None

    # Detect data rows instead of assuming a fixed number of header rows.
    for _, row in raw.iterrows():
        values = list(row.values) + [None] * 8
        if pd.notna(values[0]):
            try:
                candidate = parse_date(values[0])
                if candidate is None or pd.isna(candidate):
                    continue
                current_day = candidate
            except (TypeError, ValueError):
                continue
        if pd.notna(values[1]):
            try:
                current_hour = int(values[1])
            except (TypeError, ValueError):
                continue
        if pd.isna(values[2]) or current_day is None or current_hour is None:
            continue

        try:
            qh_in_hour = int(values[2])
        except (TypeError, ValueError):
            continue
        if qh_in_hour not in (1, 2, 3, 4) or current_hour < 1 or current_hour > 25:
            raise ValueError("Invalid hour/quarter in SEPS data")

        timestamp = local_timestamp(current_day, current_hour, qh_in_hour)
        def number(value, label, blank_zero=False):
            if pd.isna(value):
                if blank_zero:
                    return 0.0
                raise ValueError("Missing {} for {} quarter {}".format(label, current_day, quarter_hour))
            try:
                result = float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                raise ValueError("Non-numeric {} for {}".format(label, current_day))
            if not math.isfinite(result):
                raise ValueError("Non-finite {} for {}".format(label, current_day))
            return result
        quarter_hour = (current_hour - 1) * 4 + qh_in_hour
        up_mwh = number(values[up_volume_col], "Up volume", blank_zero=True)
        down_mwh = number(values[down_volume_col], "Down volume", blank_zero=True)
        def one_price(columns, volume, label):
            prices = [number(values[col], label) for col in columns if pd.notna(values[col])]
            if not prices:
                return number(None, label, blank_zero=(volume == 0))
            if volume and any(p != prices[0] for p in prices[1:]):
                raise ValueError('Several different SEPS prices for {} at {}; cannot choose without product volumes'.format(label, timestamp))
            return prices[0]
        up_price = one_price(up_price_cols, up_mwh, "Up price")
        down_price = one_price(down_price_cols, down_mwh, "Down price")

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


def sanitize_price_outliers(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return rows, 0

    outlier_count = 0
    for col in ["up_price_eur_mwh", "down_price_eur_mwh"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        mask = df[col].abs() > MAX_ABS_PRICE_EUR_MWH
        outlier_count += int(mask.sum())
        df.loc[mask, col] = pd.NA
        df[col] = (
            df.sort_values(["date", "quarter_hour"])
            .groupby("date")[col]
            .transform(lambda s: s.interpolate(method="linear").ffill().bfill())
        )
        df[col] = df[col].fillna(0.0)

    df["up_revenue_eur"] = df["up_volume_mwh"] * df["up_price_eur_mwh"]
    df["down_revenue_eur"] = df["down_volume_mwh"] * df["down_price_eur_mwh"]
    df["total_energy_revenue_eur"] = df["up_revenue_eur"] + df["down_revenue_eur"]
    return df.to_dict("records"), outlier_count


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
    all_rows, outlier_count = sanitize_price_outliers(all_rows)
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
        "price_outliers_replaced={}".format(outlier_count),
        "price_outlier_threshold_abs_eur_mwh={}".format(MAX_ABS_PRICE_EUR_MWH),
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
