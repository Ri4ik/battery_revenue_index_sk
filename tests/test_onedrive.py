"""OneDrive inbox handling with a fake rclone working on a local folder."""

import os
import stat
import sys
from pathlib import Path

import pytest

from bri import onedrive, settings
from tests.conftest import D, seps_xlsx

FAKE = Path(__file__).resolve().parent / "fake_rclone.py"


@pytest.fixture
def drive(tmp_path, monkeypatch):
    root = tmp_path / "drive"
    (root / "BatteryIndex" / settings.INBOX_DIR).mkdir(parents=True)
    wrapper = tmp_path / "rclone"
    wrapper.write_text('#!/bin/sh\nexec {} {} "$@"\n'.format(sys.executable, FAKE))
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(root))
    monkeypatch.delenv("FAKE_RCLONE_FAIL", raising=False)
    monkeypatch.setattr(settings, "RCLONE_BIN", str(wrapper))
    monkeypatch.setattr(settings, "ONEDRIVE_REMOTE", "remote:BatteryIndex")
    monkeypatch.setattr(settings, "INBOX_MIN_AGE_MINUTES", 0)
    return root / "BatteryIndex"


def _put(drive, name, content):
    (drive / settings.INBOX_DIR / name).write_bytes(content)


def _states(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT remote_path, state FROM inbox_file ORDER BY inbox_file_id")
        rows = cur.fetchall()
    conn.commit()
    return rows


def _rows(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM {}".format(table))
        n = cur.fetchone()[0]
    conn.commit()
    return n


def test_file_is_imported_then_moved(conn, drive):
    _put(drive, "oktober.xlsx", seps_xlsx(D(2025, 6, 10)))
    messages = []
    days = onedrive.intake(conn, messages)
    assert D(2025, 6, 10) in days
    assert not list((drive / settings.INBOX_DIR).iterdir())
    assert len(list((drive / settings.PROCESSED_DIR).iterdir())) == 1
    assert _states(conn) == [("oktober.xlsx", "archived")]
    assert _rows(conn, "afrr_energy") == 96


def test_same_content_again_is_a_duplicate(conn, drive):
    content = seps_xlsx(D(2025, 6, 10))
    _put(drive, "a.xlsx", content)
    onedrive.intake(conn, [])
    _put(drive, "a_copy.xlsx", content)
    messages = []
    onedrive.intake(conn, messages)
    assert _rows(conn, "afrr_energy") == 96
    assert any("už bol importovaný" in m for m in messages)


def test_corrected_file_with_same_name_is_new_content(conn, drive):
    _put(drive, "jun.xlsx", seps_xlsx(D(2025, 6, 10), up_price=100.0))
    onedrive.intake(conn, [])
    _put(drive, "jun.xlsx", seps_xlsx(D(2025, 6, 10), up_price=120.0))
    onedrive.intake(conn, [])
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM source_artifact")
        assert cur.fetchone()[0] == 2
        cur.execute("SELECT DISTINCT up_price_eur_mwh FROM afrr_energy_current")
        assert [float(r[0]) for r in cur.fetchall()] == [120.0]
    conn.commit()


def test_failed_move_is_retried_without_reimporting(conn, drive, monkeypatch):
    monkeypatch.setenv("FAKE_RCLONE_FAIL", "moveto")
    _put(drive, "jul.xlsx", seps_xlsx(D(2025, 7, 1)))
    messages = []
    onedrive.intake(conn, messages)
    assert _states(conn) == [("jul.xlsx", "archive_pending")]
    batches_before = _rows(conn, "import_batch")
    onedrive.intake(conn, [])  # the fake fails only once
    assert _states(conn) == [("jul.xlsx", "archived")]
    assert _rows(conn, "import_batch") == batches_before
    assert _rows(conn, "afrr_energy") == 96


def test_bad_file_goes_to_errors_with_reason(conn, drive):
    _put(drive, "zly.xlsx", b"this is not an excel file")
    onedrive.intake(conn, [])
    errors = sorted(p.name for p in (drive / settings.ERROR_DIR).iterdir())
    assert len(errors) == 2 and errors[1].endswith(".chyba.txt")
    assert _rows(conn, "afrr_energy") == 0
    assert _states(conn)[0][1] == "rejected"


def test_network_error_leaves_the_file_in_the_inbox(conn, drive, monkeypatch):
    monkeypatch.setenv("FAKE_RCLONE_FAIL", "copyto")
    _put(drive, "aug.xlsx", seps_xlsx(D(2025, 8, 1)))
    with pytest.raises(onedrive.RcloneError) as exc:
        onedrive.intake(conn, [])
    assert not exc.value.auth
    assert (drive / settings.INBOX_DIR / "aug.xlsx").exists()
    assert not (drive / settings.ERROR_DIR).exists()
    onedrive.intake(conn, [])  # next run succeeds
    assert _states(conn) == [("aug.xlsx", "archived")]


def test_auth_error_is_recognised(conn, drive, monkeypatch):
    monkeypatch.setenv("FAKE_RCLONE_FAIL", "lsjson")
    monkeypatch.setenv("FAKE_RCLONE_FAIL_MSG", "couldn't fetch token: invalid_grant")
    with pytest.raises(onedrive.RcloneError) as exc:
        onedrive.intake(conn, [])
    assert exc.value.auth


def test_status_file_is_written(drive):
    onedrive.write_status("Stav\n")
    assert (drive / settings.STATUS_FILE).read_text() == "Stav\n"
