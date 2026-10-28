"""Native intervals, readiness, revisions and failed imports."""

import json

import pandas as pd
import pytest

from bri import artifacts, inputs, readiness
from bri.importers import okte
from tests.conftest import D, fixture_bytes


def _status(conn, market, day, component="prices"):
    with conn.cursor() as cur:
        cur.execute("""SELECT d.status FROM market_day d JOIN market m USING (market_id)
                       WHERE m.code = %s AND d.delivery_day = %s AND d.component = %s""",
                    (market, day, component))
        row = cur.fetchone()
    conn.commit()
    return row[0] if row else None


@pytest.mark.parametrize("day,count,minutes", [
    (D(2025, 3, 30), 23, 60),    # spring DST, hourly DAM
    (D(2025, 9, 30), 24, 60),    # last hourly DAM day
    (D(2025, 10, 1), 96, 15),    # first 15-minute DAM day
    (D(2025, 10, 26), 100, 15),  # autumn DST, 15-minute DAM
])
def test_dam_native_intervals(conn, monkeypatch, day, count, minutes):
    monkeypatch.setattr(okte, "_get", lambda url, params: fixture_bytes("okte_dam_{}.json".format(day)))
    okte.import_da(conn, day)
    with conn.cursor() as cur:
        df = inputs.day_prices(cur, "DA", day)
    conn.commit()
    assert len(df) == count
    assert set(df["interval_end"] - df["interval_start"]) == {pd.Timedelta(minutes=minutes)}
    readiness.refresh(conn, [day], markets=("DA",))
    assert _status(conn, "DA", day) == "complete"


def test_dam_quarter_hours_are_not_averaged(conn, monkeypatch):
    monkeypatch.setattr(okte, "_get", lambda url, params: fixture_bytes("okte_dam_2025-10-01.json"))
    okte.import_da(conn, D(2025, 10, 1))
    with conn.cursor() as cur:
        df = inputs.day_prices(cur, "DA", D(2025, 10, 1))
    conn.commit()
    assert list(df["price"][:4]) == [99.26, 89.70, 84.43, 83.70]


def test_imb_and_idm_dst_day_have_100_quarter_hours(conn, monkeypatch):
    day = D(2025, 10, 26)
    monkeypatch.setattr(okte, "_get", lambda url, params: fixture_bytes(
        "okte_imb_final_2025-10-26.json" if "dateFrom" in params else "okte_idm15_2025-10-26.json"))
    okte.import_imb(conn, day, "final")
    okte.import_idm(conn, day, 15)
    readiness.refresh(conn, [day], markets=("IMB", "IDM15"))
    with conn.cursor() as cur:
        assert len(inputs.day_prices(cur, "IMB", day)) == 100
    conn.commit()
    assert _status(conn, "IMB", day) == "complete"
    assert _status(conn, "IDM15", day) in ("complete", "incomplete")


def test_same_payload_twice_adds_nothing_and_new_value_is_a_revision(conn, monkeypatch):
    day = D(2025, 9, 30)
    original = fixture_bytes("okte_dam_2025-09-30.json")
    monkeypatch.setattr(okte, "_get", lambda url, params: original)
    first = okte.import_da(conn, day)
    again = okte.import_da(conn, day)
    assert first["rows"] == 24 and again["skipped"]

    changed = json.loads(original)
    changed[0]["price"] = 1234.5
    monkeypatch.setattr(okte, "_get", lambda url, params: json.dumps(changed).encode())
    revision = okte.import_da(conn, day)
    assert revision["rows"] == 1 and revision["artifact_id"] != first["artifact_id"]
    with conn.cursor() as cur:
        now = inputs.day_prices(cur, "DA", day)
        before = inputs.day_prices(cur, "DA", day, as_of_batch=first["batch_id"])
        cur.execute("SELECT count(*) FROM price_da")
        total_rows = cur.fetchone()[0]
    conn.commit()
    assert now["price"].iloc[0] == 1234.5
    assert before["price"].iloc[0] == json.loads(original)[0]["price"]  # reproducible snapshot
    assert total_rows == 25  # history kept, nothing duplicated


def test_failed_import_keeps_previous_values_and_records_the_failure(conn, monkeypatch):
    day = D(2025, 9, 30)
    monkeypatch.setattr(okte, "_get", lambda url, params: fixture_bytes("okte_dam_2025-09-30.json"))
    okte.import_da(conn, day)
    artifact = artifacts.store_artifact(conn, "OKTE", "api", b"broken", "json")

    def write_then_fail(cur, batch_id):
        inputs.write_price_rows(cur, "DA", [(pd.Timestamp("2025-09-29T22:00Z"), pd.Timestamp("2025-09-29T23:00Z"),
                                             -1.0)], batch_id)
        raise RuntimeError("parser crashed")

    with pytest.raises(RuntimeError):
        artifacts.import_artifact(conn, artifact, "test", write_then_fail)
    with conn.cursor() as cur:
        cur.execute("SELECT status, error_summary FROM import_batch WHERE artifact_id = %s", (artifact,))
        status, error = cur.fetchone()
        prices = inputs.day_prices(cur, "DA", day)
    conn.commit()
    assert status == "failed" and "parser crashed" in error
    assert (prices["price"] >= 0).all() and len(prices) == 24


def test_overlapping_intervals_are_rejected(conn):
    artifact = artifacts.store_artifact(conn, "OKTE", "api", b"overlap", "json")
    rows = [(pd.Timestamp("2025-09-29T22:00Z"), pd.Timestamp("2025-09-29T23:00Z"), 1.0),
            (pd.Timestamp("2025-09-29T22:30Z"), pd.Timestamp("2025-09-29T23:30Z"), 2.0)]
    with pytest.raises(ValueError):
        artifacts.import_artifact(conn, artifact, "test", lambda cur, b: inputs.write_price_rows(cur, "DA", rows, b))
