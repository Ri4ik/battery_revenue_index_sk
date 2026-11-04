"""aFRR activated ENERGY prices (A84), deliberately separate from capacity A15.

Raw API documents are archived; only unambiguous EUR/MWh price profiles are used.
Unavailable, conflicting or incomplete prices leave the Excel pending for retry.
"""
import datetime as dt
import json
import math
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET

import pandas as pd

from bri import artifacts, settings
from bri.timeutil import day_bounds
from tools import importaFRR as helper

POLICY = "entsoe-a84-a16-standard-v2"
PROCESS = "A16"


class PricesUnavailable(RuntimeError):
    """Retryable source problem, not a malformed SEPS workbook."""


def _field(fields, *names):
    for name in names:
        for key, value in fields.items():
            if key.lower() == name.lower():
                return value
    return ""


def price_resolution(value):
    # The packaged Pandas predates general ISO duration support; the legacy helper
    # only recognizes 15/30/60 minutes. A84 may also publish minute/second intervals.
    match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', value)
    if not match:
        raise PricesUnavailable("Unsupported A84 resolution: {}".format(value))
    hours, minutes, seconds = (float(v or 0) for v in match.groups())
    result = pd.Timedelta(seconds=hours * 3600 + minutes * 60 + seconds)
    if result <= pd.Timedelta(0):
        raise PricesUnavailable("A84 resolution must be positive")
    return result


def _parse_prices(payload):
    """Return native intervals, metadata and revisions without averaging unlike series."""
    records = []
    for filename, doc in helper.response_documents(payload):
        reasons = helper.acknowledgement_reasons(doc)
        if reasons:
            raise PricesUnavailable("ENTSO-E A84: " + "; ".join(reasons)[:400])
        root = ET.fromstring(doc)
        top = helper.flatten_children(root, skip_names={"TimeSeries"})
        if _field(top, "type") not in ("", "A84"):
            continue
        domain = _field(top, "area_Domain.mRID")
        if domain and domain != helper.DEFAULT_SK_DOMAIN:
            continue
        created = _field(top, "createdDateTime")
        document = _field(top, "mRID")
        revision = int(_field(top, "revisionNumber") or 0)
        process = _field(top, "process.processType") or PROCESS
        if process != PROCESS:
            continue
        for ts in helper.find_all(root, "TimeSeries"):
            fields = helper.flatten_children(ts, skip_names={"Period"})
            business = _field(fields, "businessType")
            if business and business != "A96":
                continue
            direction = helper.direction_label(_field(fields, "flowDirection.direction"))
            if direction not in ("Up", "Down"):
                continue
            product = _field(fields, "standard_MarketProduct.marketProductType", "mkt_MarketProduct.marketProductType",
                             "type_MarketProduct.type", "marketProductType")
            if product and product != "A01":
                continue  # SEPS workbook is the standard aFRR product
            currency = _field(fields, "currency_Unit.name") or _field(top, "currency_Unit.name")
            unit = _field(fields, "price_Measure_Unit.name") or _field(top, "price_Measure_Unit.name")
            if currency != "EUR" or unit.upper() != "MWH":
                raise PricesUnavailable("A84 price units must be EUR/MWh (received {}/{})".format(currency, unit))
            category = _field(fields, "imbalance_Price.category")
            source = _field(fields, "mktPSRType.psrType")
            curve = _field(fields, "curveType") or "A01"
            if curve not in ("A01", "A03"):
                raise PricesUnavailable("Unsupported A84 curve type: {}".format(curve))
            for period in (p for p in list(ts) if helper.local_name(p.tag) == "Period"):
                pf = helper.flatten_children(period, skip_names={"Point"})
                start = pd.Timestamp(_field(pf, "timeInterval.start"))
                end = pd.Timestamp(_field(pf, "timeInterval.end"))
                if start.tzinfo is None or end.tzinfo is None:
                    raise PricesUnavailable("A84 interval has no timezone")
                start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
                step = price_resolution(_field(pf, "resolution"))
                if step <= pd.Timedelta(0) or end <= start:
                    raise PricesUnavailable("Invalid A84 period")
                points = []
                for point in (p for p in list(period) if helper.local_name(p.tag) == "Point"):
                    values = helper.flatten_children(point)
                    pos = int(_field(values, "position"))
                    raw = _field(values, "activation_Price.amount", "price.amount", "imbalance_Price.amount")
                    if not raw:
                        continue
                    price = float(raw)
                    if not math.isfinite(price) or pos < 1:
                        raise PricesUnavailable("Invalid A84 price or position")
                    point_category = _field(values, "imbalance_Price.category")
                    if category and point_category and category != point_category:
                        raise PricesUnavailable("Conflicting series/point A84 price categories")
                    points.append((pos, price, point_category or category))
                points.sort()
                if len({p[0] for p in points}) != len(points):
                    raise PricesUnavailable("Duplicate A84 positions")
                for i, (pos, price, point_category) in enumerate(points):
                    s = start + (pos - 1) * step
                    e = (start + (points[i + 1][0] - 1) * step if i + 1 < len(points) else end) if curve == "A03" else s + step
                    if not start <= s < e <= end:
                        raise PricesUnavailable("A84 point is outside its period")
                    if point_category == "A08":
                        continue  # CBMP must not be mistaken for the area's energy price
                    profile = (process, point_category, product, source, currency, unit.upper())
                    records.append({"start": s, "end": e, "direction": direction, "price": price,
                                    "profile": profile, "created": created, "document": document,
                                    "revision": revision})
    if not records:
        raise PricesUnavailable("ENTSO-E A84 contains no usable standard aFRR settlement prices")
    return records


def parse_prices(payload):
    try:
        return _parse_prices(payload)
    except PricesUnavailable:
        raise
    except (ET.ParseError, ValueError, TypeError, KeyError) as exc:
        raise PricesUnavailable("Malformed ENTSO-E A84 document ({})".format(type(exc).__name__)) from exc


def select_prices(records, intervals):
    """One complete profile; latest revisions; exact coverage (no interpolation)."""
    profiles = {}
    for record in records:
        profiles.setdefault(tuple(record["profile"]), []).append(record)
    usable = []
    for profile, rows in profiles.items():
        revisions = {}
        for row in rows:
            key = (row["direction"], row["start"], row["end"])
            order = (row["created"], row["revision"])
            old = revisions.get(key)
            if old is None or order > (old["created"], old["revision"]):
                revisions[key] = row
            elif order == (old["created"], old["revision"]) and row["price"] != old["price"]:
                raise PricesUnavailable("Conflicting A84 prices for the same interval/revision")
        values = {}
        complete = True
        for start, end, up_volume, down_volume in intervals:
            for direction, volume in (("Up", up_volume), ("Down", down_volume)):
                pieces = sorted((r for r in revisions.values() if r["direction"] == direction
                                 and r["start"] < end and r["end"] > start), key=lambda r: r["start"])
                cursor, weighted, distinct_prices = start, 0.0, set()
                for row in pieces:
                    s, e = max(start, row["start"]), min(end, row["end"])
                    if s != cursor:
                        break
                    weighted += row["price"] * float((e - s).total_seconds())
                    distinct_prices.add(row["price"])
                    cursor = e
                if cursor != end:
                    if volume == 0:
                        values[(start, direction)] = 0.0  # no energy payment, not an invented active price
                    else:
                        complete = False
                        break
                else:
                    # A quarter-hour aggregate volume cannot settle several different
                    # sub-interval prices exactly without sub-interval activation volumes.
                    if volume != 0 and len(distinct_prices) > 1:
                        complete = False
                        break
                    values[(start, direction)] = weighted / float((end - start).total_seconds())
            if not complete:
                break
        if complete:
            usable.append((profile, values))
    if not usable:
        raise PricesUnavailable("A84 prices do not unambiguously cover every SEPS interval with nonzero activation; finer varying prices require matching sub-interval volumes")
    # Different published profiles are acceptable only when their joined prices are identical.
    first = usable[0]
    if any(other[1] != first[1] for other in usable[1:]):
        raise PricesUnavailable("Several different complete A84 price profiles exist; refusing to mix them")
    return first[1], {"policy": POLICY, "process": first[0][0], "price_category": first[0][1],
                      "product": first[0][2], "source_type": first[0][3], "currency": "EUR", "unit": "EUR/MWh",
                      "alignment": "exact coverage; no varying sub-interval prices for nonzero volumes"}


def _download(day):
    if not settings.ENTSOE_API_KEY:
        raise PricesUnavailable("ENTSOE_API_KEY is not configured; file is retained for retry")
    start, end = day_bounds(day)
    params = {"securityToken": settings.ENTSOE_API_KEY, "documentType": "A84", "processType": PROCESS,
              "businessType": "A96", "controlArea_Domain": helper.DEFAULT_SK_DOMAIN,
              "periodStart": start.strftime("%Y%m%d%H%M"), "periodEnd": end.strftime("%Y%m%d%H%M")}
    safe = {k: v for k, v in params.items() if k != "securityToken"}
    problem = "unavailable"
    for attempt in range(3):
        try:
            with urlopen(helper.API_URL + "?" + urlencode(params), timeout=25) as response:
                body = response.read()
            parse_prices(body)  # reject acknowledgements even when HTTP status is 200
            return body, safe
        except HTTPError as exc:
            problem = "HTTP {}".format(exc.code)
            try:
                body = exc.read()
                reasons = []
                for _, document in helper.response_documents(body):
                    reasons.extend(helper.acknowledgement_reasons(document))
                if any("no matching data" in reason.lower() for reason in reasons):
                    raise PricesUnavailable("ENTSO-E A84 has no published prices for {}".format(day))
            except PricesUnavailable:
                raise
            except Exception:
                pass
            if exc.code in (401, 403):
                raise PricesUnavailable("ENTSO-E A84 authorization failed (HTTP {}); check the private API token".format(exc.code))
        except PricesUnavailable as exc:
            problem = str(exc).replace(settings.ENTSOE_API_KEY, "<redacted>")
            if "no matching data" in problem.lower():
                raise
        except (URLError, TimeoutError, OSError, ValueError, ET.ParseError) as exc:
            problem = type(exc).__name__  # never expose a URL carrying securityToken
        if attempt < 2:
            time.sleep(attempt + 1)
    raise PricesUnavailable("ENTSO-E A84 {} unavailable after 3 attempts: {}. File retained for retry.".format(day, problem))


def load_for_volumes(conn, volumes, allow_api=True):
    """Fetch/cache only delivery days present in this file and return joined prices."""
    by_day = {}
    for start, end, up, down in volumes:
        day = str(start.tz_convert(settings.TZ).date())
        by_day.setdefault(day, []).append((start, end, up, down))
    prices, provenance, profiles = {}, [], []
    for day, intervals in sorted(by_day.items()):
        with conn.cursor() as cur:
            cur.execute("""SELECT a.artifact_id,a.content_sha256 FROM source_artifact a
                           JOIN data_source s USING(source_id)
                           WHERE s.code='ENTSOE' AND a.source_metadata->>'dataset'='activated_balancing_prices'
                             AND a.source_metadata->>'delivery_day'=%s AND a.source_metadata->>'policy'=%s
                           ORDER BY a.artifact_id DESC LIMIT 1""", (day, POLICY))
            cached = cur.fetchone()
        conn.commit()
        if cached:
            try:
                body = artifacts.read_artifact(conn, cached[0])
                joined, profile = select_prices(parse_prices(body), intervals)
            except (FileNotFoundError, PricesUnavailable):
                cached = None
        if not cached:
            if not allow_api:
                raise PricesUnavailable("No cached aFRR ENERGY prices for {}. Run without --skip-api to download ENTSO-E A84.".format(day))
            body, params = _download(day)
            joined, profile = select_prices(parse_prices(body), intervals)
            aid = artifacts.store_artifact(conn, "ENTSOE", "api", body, "zip" if body.startswith(b"PK") else "xml",
                                           metadata={"endpoint": helper.API_URL, "params": params,
                                                     "dataset": "activated_balancing_prices", "delivery_day": day,
                                                     "policy": POLICY, "selected_profile": profile})
            cached = (aid, artifacts.sha256_bytes(body))
        prices.update(joined)
        provenance.append({"delivery_day": day, "artifact_id": cached[0], "sha256": str(cached[1]).strip()})
        profiles.append(profile)
    return prices, provenance, profiles
