"""SEPS/Damas "Regulacna elektrina (denna)" XLSX -> afrr_energy (raw values).

Outlier cleaning is NOT applied here; it belongs to the calculation (versioned in the
model) so the database keeps exactly what SEPS published.
"""

import os
import tempfile
import io
import json
import math
import re
from collections import defaultdict

import pandas as pd

from bri import artifacts, inputs
from bri.importers import entsoe_energy
from bri.timeutil import day_bounds, quarter_hour_start
from tools import import_seps_damas_energy as seps_tool

PARSER = "seps-damas-xlsx-v3"
JOINED_PARSER = "seps-volumes-entsoe-a84-v1"


class FormatError(ValueError):
    """The file is not a usable SEPS/Damas aFRR export (goes to 3_Chyby)."""


def parse_xlsx(content, original_name="upload.xlsx"):
    """XLSX bytes -> [(start, end, up_mwh, up_price, down_mwh, down_price)]; validates coverage."""
    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(content)
        try:
            raw = seps_tool.parse_afrr_sheet(tmp, original_name)
        except Exception as exc:  # missing sheet, not an xlsx, unreadable cells ...
            raise FormatError("súbor sa nedá prečítať ako export SEPS/Damas (hárok aFRR): {}".format(exc))
    finally:
        os.remove(tmp)
    rows = seps_tool.dedupe_rows(raw)
    if not rows:
        raise FormatError("v hárku aFRR nie sú žiadne riadky s dátami")

    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    out = []
    for day, day_rows in sorted(by_day.items()):
        start, end = day_bounds(day)
        expected = int((end - start) / pd.Timedelta(minutes=15))
        qhs = sorted(int(r["quarter_hour"]) for r in day_rows)
        if qhs != list(range(1, expected + 1)):
            raise FormatError("deň {} má {} štvrťhodín namiesto {}".format(day, len(qhs), expected))
        for r in day_rows:
            s = quarter_hour_start(day, int(r["quarter_hour"]))
            out.append((s, s + pd.Timedelta(minutes=15), r["up_volume_mwh"], r["up_price_eur_mwh"],
                        r["down_volume_mwh"], r["down_price_eur_mwh"]))
    return out


def import_xlsx(conn, content, original_name, transport, metadata=None):
    """Store + import one XLSX. Raises FormatError for unusable content (nothing is written)."""
    parsed = parse_xlsx(content, original_name)  # validate before anything is stored as imported
    artifact_id = artifacts.store_artifact(
        conn, "SEPS_DAMAS", transport, content, "xlsx", original_name=original_name, metadata=metadata,
    )
    batch_id, n, skipped = artifacts.import_artifact(
        conn, artifact_id, PARSER, lambda cur, b: inputs.write_afrr_energy_rows(cur, parsed, b)
    )
    days = sorted({str(pd.Timestamp(r[0]).tz_convert("Europe/Bratislava").date()) for r in parsed})
    return {"artifact_id": artifact_id, "batch_id": batch_id, "rows": n, "skipped": skipped,
            "first_day": days[0], "last_day": days[-1], "days": days, "source_rows": len(parsed)}


def _volume_records(frame, columns):
    """Read quarter-hour data without assuming the number of header rows."""
    day, hour, records, first_data = None, None, {}, None
    for index, series in frame.iterrows():
        values = list(series.values)
        if pd.notna(values[0]):
            try:
                candidate = seps_tool.parse_date(values[0])
                if candidate is None or pd.isna(candidate):
                    continue
                day = candidate
            except (TypeError, ValueError):
                continue
        if pd.notna(values[1]):
            try:
                hour = int(values[1])
            except (TypeError, ValueError):
                continue
        if day is None or hour is None or pd.isna(values[2]):
            continue
        try:
            quarter = int(values[2])
        except (TypeError, ValueError):
            continue
        if not 1 <= hour <= 25 or quarter not in (1, 2, 3, 4):
            raise FormatError("Invalid hour/quarter in SEPS volume export")
        start = quarter_hour_start(day, (hour - 1) * 4 + quarter)
        end = start + pd.Timedelta(minutes=15)
        quantities = []
        for column in columns:
            value = values[column]
            try:
                number = 0.0 if pd.isna(value) else float(str(value).replace(',', '.'))
            except (TypeError, ValueError):
                raise FormatError("Non-numeric SEPS volume at {}".format(start))
            if not math.isfinite(number) or number < 0:
                raise FormatError("Invalid SEPS volume at {}".format(start))
            quantities.append(number)
        if start in records:
            raise FormatError("Duplicate SEPS volume interval {}".format(start))
        records[start] = (end, quantities)
        if first_data is None:
            first_data = index
    return records, first_data


def inspect_local_xlsx(content, original_name="upload.xlsx"):
    """Accept old priced Excel and the new standard Up/Down volume-only export."""
    try:
        book = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None, engine='openpyxl')
        frame = book['aFRR']
    except Exception as exc:
        raise FormatError("Cannot read SEPS aFRR worksheet: {}".format(exc))
    if frame.shape[1] >= 7:
        rows = parse_xlsx(content, original_name)
        return {"format": "priced_excel", "rows": rows,
                "days": sorted({str(row[0].tz_convert('Europe/Bratislava').date()) for row in rows}),
                "source_rows": len(rows), "volume_metadata": {"normalized_unit": "MWh"}}
    if frame.shape[1] != 5:
        raise FormatError("Expected date/hour/quarter and either two aFRR volumes or volumes with prices")
    records, first = _volume_records(frame, (3, 4))
    if not records:
        raise FormatError("No SEPS aFRR volume intervals")
    headers = ' '.join(str(v) for v in frame.iloc[:first].values.ravel() if pd.notna(v))
    if not re.search(r'\bUp\b', headers, re.I) or not re.search(r'\bDown\b', headers, re.I):
        raise FormatError("SEPS volume directions Up/Down are not identified in the header")
    units = re.findall(r'\[(MWh|MW)\]', headers, re.I)
    if not units or len({unit.upper() for unit in units}) != 1:
        raise FormatError("SEPS volume unit must be explicitly MW or MWh")
    declared = 'MWh' if units[0].upper() == 'MWH' else 'MW'
    source_unit = declared
    evidence = 'aFRR worksheet header'
    # The current Damas workbook labels aFRR MWh, while Spolu explicitly aggregates
    # exactly the same aFRR+mFRR quantities under 12.3.E [MW]. Validate that identity
    # on EVERY interval before interpreting the new export as average MW.
    if 'Spolu' in book and 'mFRR' in book and book['Spolu'].shape[1] >= 8 and book['mFRR'].shape[1] >= 7:
        # header rows of Spolu are found on Spolu itself, not taken from the aFRR sheet
        aggregate, first_aggregate = _volume_records(book['Spolu'], (6, 7))
        aggregate_headers = ' '.join(str(v) for v in book['Spolu'].iloc[:first_aggregate].values.ravel()
                                     if pd.notna(v))
        if re.search(r'12\.3\.E\s*\[MW\]', aggregate_headers, re.I):
            mfrr, _ = _volume_records(book['mFRR'], (3, 4, 5, 6))
            if set(aggregate) != set(records) or set(mfrr) != set(records):
                raise FormatError("Cannot align SEPS unit cross-check worksheets")
            for start, (end, (up, down)) in records.items():
                m = mfrr[start][1]
                totals = aggregate[start][1]
                if not (math.isclose(up + m[0] + m[1], totals[0], abs_tol=0.000001)
                        and math.isclose(down + m[2] + m[3], totals[1], abs_tol=0.000001)):
                    raise FormatError("SEPS volume unit cross-check failed at {}".format(start))
            source_unit = 'MW'
            evidence = 'all intervals match aFRR + mFRR = Spolu 12.3.E [MW]; conversion is explicit in report'
    by_day = defaultdict(list)
    for start in records:
        by_day[str(start.tz_convert('Europe/Bratislava').date())].append(start)
    for day, starts in by_day.items():
        begin, finish = day_bounds(day)
        expected = list(pd.date_range(begin, finish, freq='15min', closed='left'))
        if sorted(starts) != expected:
            raise FormatError("SEPS {} does not contain every quarter-hour of the local day".format(day))
    volumes = []
    for start, (end, (up, down)) in sorted(records.items()):
        factor = float((end - start).total_seconds()) / 3600.0 if source_unit == 'MW' else 1.0
        volumes.append((start, end, up * factor, down * factor))
    return {"format": "volume_only", "volumes": volumes, "days": sorted(by_day), "source_rows": len(volumes),
            "volume_metadata": {"declared_unit": declared, "interpreted_source_unit": source_unit,
                                "normalized_unit": "MWh", "unit_evidence": evidence}}


def import_local_xlsx(conn, content, original_name, inspected, metadata=None, allow_api=True):
    if inspected['format'] == 'priced_excel':
        result = import_xlsx(conn, content, original_name, 'local', metadata=metadata)
        result.update(parser_version=PARSER, input_format='priced_excel',
                      artifact_sha256=artifacts.sha256_bytes(content))
        return result
    prices, provenance, profiles = entsoe_energy.load_for_volumes(conn, inspected['volumes'], allow_api=allow_api)
    rows = [(start, end, up, prices[(start, 'Up')], down, prices[(start, 'Down')])
            for start, end, up, down in inspected['volumes']]
    original_hash = artifacts.sha256_bytes(content)
    original_id = artifacts.store_artifact(conn, 'SEPS_DAMAS', 'local', content, 'xlsx',
                                           original_name=original_name, metadata=metadata)
    joined = {"kind": JOINED_PARSER, "excel_sha256": original_hash,
              "volume_metadata": inspected['volume_metadata'], "price_profiles": profiles,
              "price_documents": [{"delivery_day": p['delivery_day'], "sha256": p['sha256']} for p in provenance],
              "rows": [[str(start), str(end), up, up_price, down, down_price]
                       for start, end, up, up_price, down, down_price in rows]}
    payload = json.dumps(joined, sort_keys=True, separators=(',', ':')).encode('utf-8')
    joined_metadata = dict(metadata or {})
    joined_metadata.update(source_excel_artifact_id=original_id, price_documents=provenance,
                           volume_metadata=inspected['volume_metadata'], parser=JOINED_PARSER)
    artifact_id = artifacts.store_artifact(conn, 'SEPS_DAMAS', 'local', payload, 'json',
                                          original_name=original_name + '.joined.json', metadata=joined_metadata)
    batch, count, skipped = artifacts.import_artifact(conn, artifact_id, JOINED_PARSER,
                                                     lambda cur, b: inputs.write_afrr_energy_rows(cur, rows, b))
    days = inspected['days']
    return {"artifact_id": artifact_id, "artifact_sha256": artifacts.sha256_bytes(payload),
            "batch_id": batch, "rows": count, "skipped": skipped, "days": days,
            "first_day": days[0], "last_day": days[-1], "source_rows": len(rows),
            "input_format": "volume_only", "parser_version": JOINED_PARSER,
            "volume_metadata": inspected['volume_metadata'], "price_profiles": profiles,
            "price_documents": provenance,
            "additional_raw_artifacts": [{"artifact_id": original_id, "sha256": original_hash}]}
