"""
Import Slovak aFRR market data from ENTSO-E Transparency Platform.

The script downloads the data blocks needed for a Slovak aFRR benchmark:

* balancing energy bids / merit order (GL EB 12.3.B&C),
* aggregated balancing energy bids and activated volumes (GL EB 12.3.E),
* activated balancing quantities (TR 17.1.E legacy / balancing quantities, best effort),
* prices of activated balancing energy (TR 17.1.F),
* contracted/procured balancing reserve volumes and prices (TR 17.1.B&C / GL EB 12.3.F).

It always stores raw XML responses and also writes normalized CSV files from
common ENTSO-E TimeSeries/Period/Point XML structures.

Usage:
    python tools/importaFRR.py --workspace . --date-from 2026-05-25 --date-to 2026-05-25

Requires:
    ENTSOE_API_KEY in the environment, or api_checks/entsoe/.env.
"""

import argparse
import csv
import hashlib
import io
import os
import sys
import time
import traceback
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


API_URL = "https://web-api.tp.entsoe.eu/api"
DEFAULT_SK_DOMAIN = "10YSK-SEPS-----K"
MAX_OFFSET = 12000
PAGE_SIZE = 100


DATASETS = [
    {
        "name": "balancing_energy_bids",
        "description": "Balancing Energy Bids [GL EB 12.3.B&C]",
        "params": {
            "documentType": "A37",
            "processType": "A51",  # aFRR
            "businessType": "B74",  # Offer
            "connecting_Domain": "{domain}",
        },
        "normalized_file": "afrr_balancing_energy_bids_{start}_{end}.csv",
        "paginated": True,
    },
    {
        "name": "aggregated_balancing_energy_bids",
        "description": "Aggregated Balancing Energy Bids [GL EB 12.3.E]",
        "params": {
            "documentType": "A24",
            "processType": "A51",  # aFRR
            "businessType": "A14",  # Aggregated energy data
            "Area_Domain": "{domain}",
        },
        "normalized_file": "afrr_aggregated_balancing_energy_bids_{start}_{end}.csv",
        "paginated": False,
    },
    {
        "name": "activated_balancing_quantities",
        "description": "Activated balancing quantities [TR 17.1.E / A83]",
        "params": {
            "documentType": "A83",
            "businessType": "A96",  # aFRR
            "controlArea_Domain": "{domain}",
        },
        "normalized_file": "afrr_activated_quantities_{start}_{end}.csv",
        "paginated": False,
    },
    {
        "name": "activated_balancing_prices",
        "description": "Prices of Activated Balancing Energy [TR 17.1.F / A84]",
        "params": {
            "documentType": "A84",
            "processType": "A16",  # Realised
            "businessType": "A96",  # aFRR
            "controlArea_Domain": "{domain}",
        },
        "normalized_file": "afrr_activated_prices_{start}_{end}.csv",
        "paginated": False,
    },
    {
        "name": "contracted_reserve_quantities",
        "description": "Amount of Balancing Reserves Under Contract [TR 17.1.B / A81]",
        "params": {
            "documentType": "A81",
            "processType": "A52",  # Automatic frequency restoration reserve
            "businessType": "B95",  # Frequency containment reserve/aFRR reserve capacity
            "psrType": "A04",
            "type_MarketAgreement.Type": "A13",
            "controlArea_Domain": "{domain}",
        },
        "normalized_file": "afrr_contracted_reserve_quantities_{start}_{end}.csv",
        "paginated": False,
    },
    {
        "name": "procured_balancing_capacity",
        "description": "Procured Balancing Capacity [GL EB 12.3.F / A15]",
        "params": {
            "documentType": "A15",
            "processType": "A51",  # aFRR
            "area_Domain": "{domain}",
        },
        "normalized_file": "afrr_procured_balancing_capacity_{start}_{end}.csv",
        "paginated": True,
    },
    {
        "name": "contracted_reserve_prices",
        "description": "Prices of Balancing Reserves Under Contract [TR 17.1.C / A89]",
        "params": {
            "documentType": "A89",
            "businessType": "A96",  # aFRR
            "type_MarketAgreement.Type": "A01",
            "controlArea_Domain": "{domain}",
        },
        "normalized_file": "afrr_contracted_reserve_prices_{start}_{end}.csv",
        "paginated": False,
    },
]


def load_dotenv(path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fp:
        for raw_line in fp:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def fmt_period(dt):
    return dt.strftime("%Y%m%d%H%M")


def parse_date(day, end=False):
    dt = datetime.strptime(day, "%Y-%m-%d")
    if end:
        dt = dt + timedelta(days=1)
    return dt


def local_name(tag):
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def text_or_empty(node):
    if node is None or node.text is None:
        return ""
    return node.text.strip()


def flatten_children(node, prefix="", skip_names=None):
    skip_names = skip_names or set()
    out = {}
    for child in list(node):
        name = local_name(child.tag)
        if name in skip_names:
            continue
        key = "{}.{}".format(prefix, name) if prefix else name
        children = list(child)
        if children:
            out.update(flatten_children(child, key, skip_names=skip_names))
        else:
            value = text_or_empty(child)
            if value != "":
                out[key] = value
    return out


def find_all(root, name):
    return [x for x in root.iter() if local_name(x.tag) == name]


def first_matching(row, fragments):
    for fragment in fragments:
        fragment_l = fragment.lower()
        for key in sorted(row.keys()):
            if fragment_l in key.lower() and row.get(key) not in ("", None):
                return row[key]
    return ""


def parse_iso_duration(value):
    if value == "PT1S":
        return timedelta(seconds=1)
    if value == "PT4S":
        return timedelta(seconds=4)
    if value == "PT15M":
        return timedelta(minutes=15)
    if value == "PT30M":
        return timedelta(minutes=30)
    if value == "PT60M":
        return timedelta(hours=1)
    return None


def parse_xml_datetime(value):
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    value_no_tz = value
    if value.endswith("+00:00"):
        value_no_tz = value[:-6]
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ):
        try:
            return datetime.strptime(value_no_tz, fmt)
        except ValueError:
            continue
    return None


def acknowledgement_reasons(xml_bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return []
    if local_name(root.tag) != "Acknowledgement_MarketDocument":
        return []
    reasons = []
    for reason in find_all(root, "Reason"):
        code = ""
        text = ""
        for child in list(reason):
            name = local_name(child.tag)
            if name == "code":
                code = text_or_empty(child)
            elif name == "text":
                text = text_or_empty(child)
        reasons.append("code={}; text={}".format(code, text))
    return reasons


def point_timestamp(period_fields, position):
    start = first_matching(period_fields, ["timeInterval.start", "start"])
    resolution = first_matching(period_fields, ["resolution"])
    start_dt = parse_xml_datetime(start)
    step = parse_iso_duration(resolution)
    try:
        pos = int(float(position))
    except (TypeError, ValueError):
        pos = 1
    if start_dt is None or step is None:
        return ""
    return (start_dt + step * (pos - 1)).isoformat()


def parse_timeseries_xml(xml_bytes, dataset_name):
    root = ET.fromstring(xml_bytes)
    rows = []
    series = find_all(root, "TimeSeries") + find_all(root, "Bid_TimeSeries")
    for ts_index, ts in enumerate(series, start=1):
        ts_fields = flatten_children(ts, skip_names={"Period"})
        periods = [x for x in list(ts) if local_name(x.tag) == "Period"]
        if not periods:
            continue
        for period in periods:
            period_fields = flatten_children(period, skip_names={"Point"})
            points = [x for x in list(period) if local_name(x.tag) == "Point"]
            for point in points:
                point_fields = flatten_children(point)
                position = first_matching(point_fields, ["position"])
                row = {
                    "dataset": dataset_name,
                    "timeseries_index": ts_index,
                    "timestamp_utc": point_timestamp(period_fields, position),
                }
                row.update({"ts." + k: v for k, v in ts_fields.items()})
                row.update({"period." + k: v for k, v in period_fields.items()})
                row.update({"point." + k: v for k, v in point_fields.items()})
                rows.append(row)
    return rows


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    seen = set()
    preferred = ["dataset", "timeseries_index", "timestamp_utc"]
    for name in preferred:
        if any(name in row for row in rows):
            fields.append(name)
            seen.add(name)
    for row in rows:
        for key in sorted(row.keys()):
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def direction_label(value):
    if not value:
        return ""
    value = value.strip()
    mapping = {
        "A01": "Up",
        "A02": "Down",
        "up": "Up",
        "down": "Down",
        "positive": "Up",
        "negative": "Down",
    }
    return mapping.get(value, mapping.get(value.lower(), value))


def to_float(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def market_datetime_from_utc(value):
    dt = parse_xml_datetime(value)
    if dt is None:
        return None
    return (
        pd.Timestamp(dt)
        .tz_localize("UTC")
        .tz_convert("Europe/Bratislava")
        .to_pydatetime()
        .replace(tzinfo=None)
    )


def normalize_bids_to_merit_order(rows):
    out = {}
    for row in rows:
        ts = row.get("timestamp_utc", "")
        if not ts:
            continue
        dt = market_datetime_from_utc(ts)
        if dt is None:
            continue
        day = dt.date().isoformat()
        qh = int((dt.hour * 60 + dt.minute) / 15) + 1
        direction = direction_label(
            first_matching(row, ["flowDirection.direction", "Direction", "direction"])
        )
        if direction not in ("Up", "Down"):
            continue
        price = to_float(first_matching(row, ["price.amount", "Price"]))
        volume = to_float(
            first_matching(row, ["quantity", "Volume", "Quantity", "maximum_Quantity"])
        )
        if price is None or volume is None:
            continue
        product_prefix = "POS" if direction == "Up" else "NEG"
        bid_id = row.get("ts.mRID") or first_matching(row, ["marketAgreement.mRID", "BidID", "mRID"])
        out.setdefault(day, []).append(
            {
                "id": bid_id,
                "DELIVERY_DATE": day,
                "TYPE_OF_RESERVES": "aFRR",
                "PRODUCT": "{}_{:03d}".format(product_prefix, qh),
                "ENERGY_PRICE_[EUR/MWh]": price,
                "ENERGY_PRICE_PAYMENT_DIRECTION": (
                    "PROVIDER_TO_GRID" if direction == "Up" else "GRID_TO_PROVIDER"
                ),
                "OFFERED_CAPACITY_[MW]": volume,
                "ALLOCATED_CAPACITY_[MW]": volume,
                "COUNTRY": "SK",
                "NOTE": "ENTSO-E BalancingEnergyBids",
            }
        )
    return out


def block_product(dt, direction):
    block_start = (dt.hour // 4) * 4
    block_end = block_start + 4
    prefix = "POS" if direction == "Up" else "NEG"
    return "{}_{:02d}_{:02d}".format(prefix, block_start, block_end)


def normalize_procured_capacity_to_legacy(rows):
    grouped = {}
    for row in rows:
        ts = row.get("timestamp_utc", "")
        dt = market_datetime_from_utc(ts)
        if dt is None:
            continue
        direction = direction_label(
            first_matching(row, ["flowDirection.direction", "Direction", "direction"])
        )
        if direction not in ("Up", "Down"):
            continue
        price = to_float(
            first_matching(row, ["procurement_Price.amount", "price.amount", "Price"])
        )
        quantity = to_float(first_matching(row, ["quantity", "Volume", "Quantity"]))
        if price is None or quantity is None:
            continue
        local_day = dt.date().isoformat()
        product = block_product(dt, direction)
        key = (local_day, product)
        item = grouped.setdefault(
            key,
            {
                "DATE_FROM": local_day,
                "DATE_TO": local_day,
                "TYPE_OF_RESERVES": "aFRR",
                "PRODUCT": product,
                "weighted_price_sum": 0.0,
                "quantity_sum": 0.0,
                "price_values": [],
            },
        )
        item["weighted_price_sum"] += price * quantity
        item["quantity_sum"] += quantity
        item["price_values"].append(price)

    by_day = {}
    for (day, product), item in grouped.items():
        if item["quantity_sum"]:
            avg_price = item["weighted_price_sum"] / item["quantity_sum"]
        elif item["price_values"]:
            avg_price = sum(item["price_values"]) / len(item["price_values"])
        else:
            avg_price = 0.0
        by_day.setdefault(day, []).append(
            {
                "DATE_FROM": item["DATE_FROM"],
                "DATE_TO": item["DATE_TO"],
                "TYPE_OF_RESERVES": item["TYPE_OF_RESERVES"],
                "PRODUCT": item["PRODUCT"],
                "AFRR_AVERAGE_CAPACITY_PRICE_EUR_PER_MW_PER_H": avg_price,
                "AFRR_PROCURED_CAPACITY_MW": item["quantity_sum"],
                "COUNTRY": "SK",
                "NOTE": "ENTSO-E Procured Balancing Capacity GL EB 12.3.F",
            }
        )
    return by_day


def write_capacity_files(workspace, rows, allowed_days=None):
    by_day = normalize_procured_capacity_to_legacy(rows)
    out_dir = Path(workspace) / "marketdata" / "aFRR_capacity"
    written = []
    product_order = {
        "NEG_00_04": 0,
        "NEG_04_08": 1,
        "NEG_08_12": 2,
        "NEG_12_16": 3,
        "NEG_16_20": 4,
        "NEG_20_24": 5,
        "POS_00_04": 6,
        "POS_04_08": 7,
        "POS_08_12": 8,
        "POS_12_16": 9,
        "POS_16_20": 10,
        "POS_20_24": 11,
    }
    fields = [
        "DATE_FROM",
        "DATE_TO",
        "TYPE_OF_RESERVES",
        "PRODUCT",
        "AFRR_AVERAGE_CAPACITY_PRICE_EUR_PER_MW_PER_H",
        "AFRR_PROCURED_CAPACITY_MW",
        "COUNTRY",
        "NOTE",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    for day, day_rows in sorted(by_day.items()):
        if allowed_days is not None and day not in allowed_days:
            continue
        complete_rows = {row["PRODUCT"]: row for row in day_rows}
        for product in product_order:
            complete_rows.setdefault(
                product,
                {
                    "DATE_FROM": day,
                    "DATE_TO": day,
                    "TYPE_OF_RESERVES": "aFRR",
                    "PRODUCT": product,
                    "AFRR_AVERAGE_CAPACITY_PRICE_EUR_PER_MW_PER_H": 0.0,
                    "AFRR_PROCURED_CAPACITY_MW": 0.0,
                    "COUNTRY": "SK",
                    "NOTE": "ENTSO-E Procured Balancing Capacity GL EB 12.3.F",
                },
            )
        rows_out = sorted(complete_rows.values(), key=lambda r: product_order[r["PRODUCT"]])
        path = out_dir / "afrr_capacity_{}.csv".format(day)
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows_out)
        written.append(path)
    return written


def write_merit_order_files(workspace, rows, allowed_days=None):
    by_day = normalize_bids_to_merit_order(rows)
    out_dir = Path(workspace) / "marketdata" / "aFRR_energy"
    written = []
    for day, day_rows in sorted(by_day.items()):
        if allowed_days is not None and day not in allowed_days:
            continue
        path = out_dir / "aFRR_merit_order_{}.csv".format(day)
        fields = [
            "id",
            "DELIVERY_DATE",
            "TYPE_OF_RESERVES",
            "PRODUCT",
            "ENERGY_PRICE_[EUR/MWh]",
            "ENERGY_PRICE_PAYMENT_DIRECTION",
            "OFFERED_CAPACITY_[MW]",
            "ALLOCATED_CAPACITY_[MW]",
            "COUNTRY",
            "NOTE",
        ]
        out_dir.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=fields)
            writer.writeheader()
            writer.writerows(day_rows)
        written.append(path)
    return written


def build_params(base_params, token, domain, start_dt, end_dt, offset=None):
    params = {
        "securityToken": token,
        "periodStart": fmt_period(start_dt),
        "periodEnd": fmt_period(end_dt),
    }
    for key, value in base_params.items():
        params[key] = value.format(domain=domain) if isinstance(value, str) else value
    if offset is not None:
        params["offset"] = str(offset)
    return params


def fetch_url(api_url, params, timeout):
    url = api_url + "?" + urlencode(params)
    with urlopen(url, timeout=timeout) as response:
        return response.read(), getattr(response, "status", response.getcode())


def response_documents(body):
    if body.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            docs = []
            for name in zf.namelist():
                if name.lower().endswith(".xml"):
                    docs.append((name, zf.read(name)))
            return docs
    return [(None, body)]


def fetch_dataset(api_url, token, dataset, domain, start_dt, end_dt, raw_dir, timeout):
    all_rows = []
    raw_files = []
    errors = []
    seen_bodies = set()
    offsets = range(0, MAX_OFFSET + PAGE_SIZE, PAGE_SIZE) if dataset.get("paginated") else [0]
    for offset in offsets:
        page_offset = offset if dataset.get("paginated") else None
        params = build_params(dataset["params"], token, domain, start_dt, end_dt, page_offset)
        raw_path = raw_dir / "{}_{}_{}_offset{:04d}.xml".format(
            dataset["name"], start_dt.date().isoformat(), (end_dt - timedelta(days=1)).date().isoformat(), offset
        )
        try:
            body, status = fetch_url(api_url, params, timeout)
        except HTTPError as exc:
            body = exc.read()
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(body)
            raw_files.append(raw_path)
            payload = body.decode("utf-8", errors="replace")
            ack = acknowledgement_reasons(body)
            for item in ack:
                errors.append("Acknowledgement: " + item)
            if offset > 0 and exc.code in (400, 404):
                break
            errors.append("HTTP {} at offset {}: {}".format(exc.code, offset, payload[:500]))
            break
        except URLError as exc:
            errors.append("Network error at offset {}: {}".format(offset, exc))
            break

        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(body)
        raw_files.append(raw_path)

        body_hash = hashlib.sha256(body).hexdigest()
        if body_hash in seen_bodies:
            errors.append("Repeated response body at offset {}; paging stopped.".format(offset))
            break
        seen_bodies.add(body_hash)

        rows = []
        try:
            documents = response_documents(body)
        except Exception:
            errors.append("Could not unpack response at offset {}:\n{}".format(offset, traceback.format_exc()))
            documents = []

        terminal_page = False
        for doc_name, doc_body in documents:
            ack = acknowledgement_reasons(doc_body)
            if ack:
                is_terminal_page = (
                    dataset.get("paginated")
                    and offset > 0
                    and all_rows
                    and any("No matching data found" in item for item in ack)
                )
                if is_terminal_page:
                    terminal_page = True
                else:
                    errors.extend(["Acknowledgement: " + item for item in ack])
                continue
            try:
                rows.extend(parse_timeseries_xml(doc_body, dataset["name"]))
            except Exception:
                label = doc_name or "response"
                errors.append(
                    "Could not parse XML at offset {} in {}:\n{}".format(
                        offset, label, traceback.format_exc()
                    )
                )
        all_rows.extend(rows)
        if terminal_page:
            break

        # ENTSO-E pages API responses by documents. If the parsed response has no
        # rows, there is no reliable signal to continue paging.
        if not rows or status != 200:
            break
        time.sleep(0.2)

    return all_rows, raw_files, errors


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for line in report:
            fp.write(line.rstrip() + "\n")


def main():
    repo = Path(__file__).resolve().parent.parent
    load_dotenv(repo / "api_checks" / "entsoe" / ".env")

    parser = argparse.ArgumentParser(description="Import Slovak aFRR data from ENTSO-E.")
    parser.add_argument("--workspace", default=".", help="Repository root.")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--domain", default=DEFAULT_SK_DOMAIN, help="Slovakia SEPS EIC by default.")
    parser.add_argument("--date-from", required=True, help="YYYY-MM-DD inclusive.")
    parser.add_argument("--date-to", required=True, help="YYYY-MM-DD inclusive.")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument(
        "--datasets",
        default="all",
        help="Comma-separated dataset names or 'all'.",
    )
    parser.add_argument(
        "--skip-merit-order",
        action="store_true",
        help="Do not write marketdata/aFRR_energy/aFRR_merit_order_YYYY-MM-DD.csv.",
    )
    args = parser.parse_args()

    token = os.environ.get("ENTSOE_API_KEY")
    if not token:
        print(
            "ENTSOE_API_KEY is not set. Create api_checks/entsoe/.env "
            "or set the environment variable.",
            file=sys.stderr,
        )
        return 2

    workspace = Path(args.workspace).resolve()
    start_dt = parse_date(args.date_from)
    end_dt = parse_date(args.date_to, end=True)
    if start_dt >= end_dt:
        print("--date-from must be before or equal to --date-to", file=sys.stderr)
        return 2

    selected_names = None
    if args.datasets != "all":
        selected_names = set(x.strip() for x in args.datasets.split(",") if x.strip())
    datasets = [d for d in DATASETS if selected_names is None or d["name"] in selected_names]
    known = set(d["name"] for d in DATASETS)
    if selected_names and not selected_names.issubset(known):
        print("Unknown dataset(s): {}".format(", ".join(sorted(selected_names - known))), file=sys.stderr)
        return 2

    raw_dir = workspace / "marketdata" / "ENTSOE" / "aFRR" / "raw"
    norm_dir = workspace / "marketdata" / "ENTSOE" / "aFRR" / "normalized"
    report = [
        "ENTSO-E aFRR import",
        "domain={}".format(args.domain),
        "date_from={}".format(args.date_from),
        "date_to={}".format(args.date_to),
        "",
    ]
    allowed_days = set(
        pd.date_range(start=args.date_from, end=args.date_to, freq="D")
        .strftime("%Y-%m-%d")
        .tolist()
    )

    for dataset in datasets:
        print("Fetching {}...".format(dataset["name"]))
        rows, raw_files, errors = fetch_dataset(
            args.api_url,
            token,
            dataset,
            args.domain,
            start_dt,
            end_dt,
            raw_dir,
            args.timeout,
        )
        normalized_path = norm_dir / dataset["normalized_file"].format(
            start=args.date_from, end=args.date_to
        )
        write_csv(normalized_path, rows)
        report.append("[{}] {}".format(dataset["name"], dataset["description"]))
        report.append("raw_files={}".format(len(raw_files)))
        report.append("normalized_rows={}".format(len(rows)))
        report.append("normalized_csv={}".format(normalized_path))
        for error in errors:
            report.append("error={}".format(error.replace("\n", " | ")))
        report.append("")

        if dataset["name"] == "balancing_energy_bids" and not args.skip_merit_order:
            written = write_merit_order_files(workspace, rows, allowed_days=allowed_days)
            report.append("merit_order_files={}".format(len(written)))
            for path in written:
                report.append("merit_order={}".format(path))
            report.append("")

        if dataset["name"] == "procured_balancing_capacity":
            written = write_capacity_files(workspace, rows, allowed_days=allowed_days)
            report.append("capacity_files={}".format(len(written)))
            for path in written:
                report.append("capacity={}".format(path))
            report.append("")

    report_path = workspace / "marketdata" / "ENTSOE" / "aFRR" / "import_report_{}_{}.txt".format(
        args.date_from, args.date_to
    )
    write_report(report_path, report)
    print("Report:", report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
