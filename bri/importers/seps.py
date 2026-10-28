"""SEPS/Damas "Regulacna elektrina (denna)" XLSX -> afrr_energy (raw values).

Outlier cleaning is NOT applied here; it belongs to the calculation (versioned in the
model) so the database keeps exactly what SEPS published.
"""

import os
import tempfile
from collections import defaultdict

import pandas as pd

from bri import artifacts, inputs
from bri.timeutil import day_bounds, quarter_hour_start
from tools import import_seps_damas_energy as seps_tool

PARSER = "seps-damas-xlsx-v1"


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
            "first_day": days[0], "last_day": days[-1]}
