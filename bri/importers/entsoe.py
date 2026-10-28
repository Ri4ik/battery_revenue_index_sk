"""ENTSO-E Transparency: procured aFRR balancing capacity (SEPS area).

Uses the request / XML parsing helpers of tools/importaFRR.py; each XML document returned
by the API becomes one artifact.
"""

import tempfile
from pathlib import Path

import pandas as pd

from bri import artifacts, inputs, settings
from tools import importaFRR as entsoe_tool

PARSER = "entsoe-procured-capacity-v1"
DATASET = next(d for d in entsoe_tool.DATASETS if d["name"] == "procured_balancing_capacity")


def product_to_block(product):
    """'POS_04_08' -> ('POS', 4, 8)."""
    direction, start, end = product.split("_")
    return direction, int(start), int(end)


def rows_from_legacy(records):
    """Normalized ENTSO-E rows (PRODUCT, price, MW per local day) -> afrr_capacity rows."""
    out = []
    for rec in records:
        direction, b_start, b_end = product_to_block(rec["PRODUCT"])
        out.append((
            pd.Timestamp(rec["DATE_FROM"]).date(), direction, b_start, b_end,
            float(rec["AFRR_AVERAGE_CAPACITY_PRICE_EUR_PER_MW_PER_H"]),
            float(rec["AFRR_PROCURED_CAPACITY_MW"]),
        ))
    return out


def import_range(conn, first_day, last_day):
    """Download procured capacity for local days first_day..last_day."""
    if not settings.ENTSOE_API_KEY:
        raise RuntimeError("ENTSOE_API_KEY is not set")
    start_dt = entsoe_tool.parse_date(str(first_day))
    end_dt = entsoe_tool.parse_date(str(last_day), end=True)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        rows, raw_files, errors = entsoe_tool.fetch_dataset(
            entsoe_tool.API_URL, settings.ENTSOE_API_KEY, DATASET, entsoe_tool.DEFAULT_SK_DOMAIN,
            start_dt, end_dt, Path(tmp), settings.HTTP_TIMEOUT,
        )
        if errors and not raw_files:
            raise RuntimeError("ENTSO-E: " + "; ".join(str(e) for e in errors)[:500])
        for raw_path in raw_files:
            payload = Path(raw_path).read_bytes()
            parsed = []
            # a response is an XML document or a ZIP of documents; acknowledgements carry no data
            for _, doc in entsoe_tool.response_documents(payload):
                if entsoe_tool.acknowledgement_reasons(doc):
                    continue
                parsed.extend(entsoe_tool.parse_timeseries_xml(doc, DATASET["name"]))
            by_day = entsoe_tool.normalize_procured_capacity_to_legacy(parsed)
            records = [r for day, recs in by_day.items()
                       if str(first_day) <= day <= str(last_day) for r in recs]
            if not records:
                continue
            artifact_id = artifacts.store_artifact(
                conn, "ENTSOE", "api", payload, "zip" if payload.startswith(b"PK") else "xml",
                metadata={"endpoint": entsoe_tool.API_URL, "dataset": DATASET["name"],
                          "from": str(first_day), "to": str(last_day)},
            )
            batch_id, n, skipped = artifacts.import_artifact(
                conn, artifact_id, PARSER,
                lambda cur, b: inputs.write_afrr_capacity_rows(cur, rows_from_legacy(records), b),
            )
            results.append({"artifact_id": artifact_id, "batch_id": batch_id, "rows": n, "skipped": skipped})
    return results
