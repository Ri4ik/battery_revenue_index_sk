"""OKTE public API: DA (DAM results), IDM15 / IDM60 (IDM results), IMB (system imbalance).

Every API response is stored as an artifact and imported with its native intervals:
DA and IDM take deliveryStart / deliveryEnd from the API (hourly DAM until 30.09.2025,
15-minute from 01.10.2025); IMB periods are counted from local midnight in real time,
so DST days have 92 or 100 quarter-hours.
"""

import json
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from bri import artifacts, inputs, settings
from bri.timeutil import day_bounds, quarter_hour_start

DAM_URL = "https://isot.okte.sk/api/v1/dam/results"
IDM_URL = "https://isot.okte.sk/api/v1/idm/results"
IMB_URL = "https://iszo.okte.sk/api/v1/SystemImbalance"

PARSER = {
    "DA": "okte-dam-api-v1",
    "IDM15": "okte-idm-api-v1",
    "IDM60": "okte-idm-api-v1",
    "IMB": "okte-imb-api-v1",
}
IMB_PRICE_FIELD = "isp"


def _get(url, params):
    with urlopen("{}?{}".format(url, urlencode(params)), timeout=settings.HTTP_TIMEOUT) as resp:
        return resp.read()


def _utc(value):
    return pd.Timestamp(value).tz_convert("UTC") if pd.Timestamp(value).tzinfo else pd.Timestamp(value, tz="UTC")


def parse_delivery_rows(payload, day, price_field):
    """[(start, end, price)] from DAM/IDM results; rows without a price are skipped."""
    items = json.loads(payload.decode("utf-8"))
    if not isinstance(items, list):
        raise ValueError("unexpected OKTE payload: {}".format(str(items)[:200]))
    start_day, end_day = day_bounds(day)
    rows = []
    for item in items:
        price = item.get(price_field)
        if price is None or item.get("deliveryStart") is None:
            continue
        start, end = _utc(item["deliveryStart"]), _utc(item["deliveryEnd"])
        if not (start_day <= start < end_day):
            raise ValueError("interval {} outside delivery day {}".format(start, day))
        rows.append((start, end, float(price)))
    return rows


def parse_imb_rows(payload, day, evaluation):
    items = json.loads(payload.decode("utf-8"))
    if isinstance(items, dict):
        raise ValueError("OKTE SystemImbalance error: {}".format(str(items)[:200]))
    rows = []
    for item in items:
        if str(item.get("date")) != str(day):
            continue
        for period in item.get("periods") or []:
            price = period.get(IMB_PRICE_FIELD)
            if price is None or period.get("period") is None:
                continue
            start = quarter_hour_start(day, int(period["period"]))
            rows.append((start, start + pd.Timedelta(minutes=15), evaluation, float(price)))
    return rows


def _import(conn, market, day, url, params, parse, write):
    payload = _get(url, params)
    parsed = parse(payload)
    if not parsed:
        return None  # nothing published yet; no artifact for empty answers
    artifact_id = artifacts.store_artifact(
        conn, "OKTE", "api", payload, "json",
        metadata={"endpoint": url, "params": params, "market": market},
    )
    batch_id, rows, skipped = artifacts.import_artifact(
        conn, artifact_id, PARSER[market], lambda cur, batch: write(cur, parsed, batch)
    )
    return {"market": market, "day": str(day), "artifact_id": artifact_id,
            "batch_id": batch_id, "rows": rows, "skipped": skipped}


def import_da(conn, day):
    params = {"deliveryDayFrom": str(day), "deliveryDayTo": str(day)}
    return _import(conn, "DA", day, DAM_URL, params,
                   lambda p: parse_delivery_rows(p, day, "price"),
                   lambda cur, rows, b: inputs.write_price_rows(cur, "DA", rows, b))


def import_idm(conn, day, product_type):
    market = "IDM15" if int(product_type) == 15 else "IDM60"
    params = {"deliveryDayFrom": str(day), "deliveryDayTo": str(day), "productType": int(product_type)}
    return _import(conn, market, day, IDM_URL, params,
                   lambda p: parse_delivery_rows(p, day, "priceWeightedAverage"),
                   lambda cur, rows, b: inputs.write_price_rows(cur, market, rows, b))


def import_imb(conn, day, evaluation):
    """evaluation: final | monthly | daily (OKTE evaluationType)."""
    params = {"dateFrom": str(day), "dateTo": str(day), "evaluationType": evaluation}
    return _import(conn, "IMB", day, IMB_URL, params,
                   lambda p: parse_imb_rows(p, day, evaluation),
                   lambda cur, rows, b: inputs.write_imb_rows(cur, rows, b))


def best_imb_evaluation(conn, day):
    """Highest evaluation currently stored for any interval of the day (None if nothing)."""
    start, end = day_bounds(day)
    with conn.cursor() as cur:
        cur.execute(
            """SELECT max(e.rank) FROM price_imb_current p JOIN imb_evaluation e USING (evaluation)
               WHERE p.interval_start >= %s AND p.interval_start < %s""",
            (start.to_pydatetime(), end.to_pydatetime()),
        )
        rank = cur.fetchone()[0]
    conn.commit()
    return rank
