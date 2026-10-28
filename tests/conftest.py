"""Test database fixtures. Tests need TEST_DATABASE_URL (a database that may be wiped)."""

import datetime as dt
import os
from pathlib import Path

import openpyxl
import pytest

from bri import calc, db, settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEST_URL = os.environ.get("TEST_DATABASE_URL")


def fixture_bytes(name):
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def conn(tmp_path, monkeypatch):
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    monkeypatch.setattr(settings, "RAW_ARCHIVE_DIR", tmp_path / "raw")
    c = db.connect(TEST_URL)
    with c.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS bri CASCADE")
    c.commit()
    db.migrate(c)
    calc.seed_configs(c)
    yield c
    c.close()


def seps_xlsx(day, up_price=100.0, down_price=50.0, quarters=96, path=None):
    """Minimal SEPS/Damas-like export: sheet aFRR, 5 header rows, one row per quarter-hour."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "aFRR"
    for _ in range(5):
        ws.append(["header"])
    for qh in range(1, quarters + 1):
        hour, q = (qh - 1) // 4 + 1, (qh - 1) % 4 + 1
        ws.append([
            day.strftime("%d.%m.%Y") if qh == 1 else None,
            hour if q == 1 else None,
            q,
            1.0 if qh % 2 else 0.0, up_price,
            0.0 if qh % 2 else 1.0, down_price,
        ])
    target = path or Path(os.environ.get("TMPDIR", "/tmp")) / "seps_{}.xlsx".format(day)
    wb.save(str(target))
    return Path(target).read_bytes()


D = dt.date
