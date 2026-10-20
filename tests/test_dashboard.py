from pathlib import Path

import pandas as pd
import pytest

from tools import results_db as db
from tools.slovakia_revenue_dashboard import build_figure, create_app

ROOT = Path(__file__).resolve().parents[1]


def sample():
    df = pd.DataFrame([
        ("2025-03-01", "DA", 10.0), ("2025-03-03", "DA", 30.0),
        ("2025-03-01", "FCR", 7.0),
    ], columns=db.FRAME_COLUMNS)
    df.day = pd.to_datetime(df.day)
    return df


def test_gaps_means_and_independent_cumulative_values():
    fig = build_figure(sample(), ["DA", "FCR"], "lines", 3, True, "annualized")
    assert len(fig.data) == 6
    assert pd.isna(fig.data[0].y[1])
    assert pd.isna(fig.data[3].y[2])
    assert fig.data[2].y[2] == 40.0  # Cumulative raw EUR, not annualized units.
    assert fig.data[5].y[0] == 7.0
    assert all("Súčet trhov" not in trace.name for trace in fig.data)
    assert fig.layout.yaxis2.title.text.endswith("(EUR)")
    bars = build_figure(sample(), ["DA", "FCR"], "stack", None, False, "absolute")
    assert bars.layout.barmode == "group"


def test_postgres_and_file_publication_boundaries(initialized, monkeypatch):
    for hours in (1, 2):
        for cycles in (1, 2):
            db.write_combo(initialized, db.DEFAULT_DATASET, hours, cycles, sample())
    db.write_combo(initialized, "private", 1, 1, sample())
    monkeypatch.setattr(db, "database_url", lambda: "configured")
    monkeypatch.setattr(db, "get_engine", lambda: initialized)
    client = create_app(ROOT).test_client()
    for hours in (1, 2):
        for cycles in (1, 2):
            response = client.get(f"/?system_type={hours}&cycle_limit={cycles}&ma=bad")
            assert response.status_code == 200
            assert "Slovakia Battery Revenue Index" in response.text
            assert "Pokrytie vo vybranom období" not in response.text
            assert 'value="FCR"' not in response.text
            assert '"name":"FCR"' not in response.text
            assert "Skladané stĺpce" not in response.text
    assert '>FCR<' not in client.get("/?markets=FCR&system_type=2&cycle_limit=2").text
    for base in ("private", "../private", "/etc", "missing"):
        assert client.get("/", query_string={"base": base}).status_code == 404
    assert client.get("/?start=2025-03-03&end=2025-03-01").status_code == 400
    assert client.get("/?start=0001-01-01&end=9999-12-31").status_code == 400
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200
    assert "GNU GENERAL PUBLIC LICENSE" in client.get("/license").text
    assert client.get("/license").data == (ROOT / "LICENSE.txt").read_bytes()
    assert "Jonas Brucksch" in client.get("/about").text
    with initialized.begin() as conn:
        conn.execute(db.datasets.update().values(is_published=False))
    assert client.get("/readyz").status_code == 503
    assert client.get("/").status_code == 404


def test_file_mode_denies_traversal_and_experimental_sets(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(db, "database_url", lambda: None)
    for name in (db.DEFAULT_DATASET, "private"):
        folder = tmp_path / "results" / name
        folder.mkdir(parents=True)
        (folder / "2025-03-01_results_DA.json").write_text(
            json.dumps({"results": {"daily_revenue": {"Total": 1}}}), encoding="utf-8")
    client = create_app(tmp_path).test_client()
    assert client.get("/").status_code == 200
    assert client.get("/?base=private").status_code == 404
    assert client.get("/?base=../private").status_code == 404


def test_actual_annual_inventory():
    from tools.result_files import parse_result_files
    root = ROOT / "results"
    if not root.exists():
        pytest.skip("Historical JSON intentionally excluded from the image; mount /app/results to audit.")
    count = 0
    for hours in (1, 2):
        for cycles in (1, 2):
            suffix = "" if (hours, cycles) == (1, 1) else f"_{hours}_{cycles}"
            df = parse_result_files(root / (db.DEFAULT_DATASET + suffix), hours, cycles)
            assert len(df) == (2562 if not suffix else 1831)
            assert df.day.min() == pd.Timestamp("2025-03-01")
            assert df.day.max() == pd.Timestamp("2026-03-01")
            assert not df.duplicated(["day", "market"]).any()
            assert (df.market == "FCR").sum() == (0 if not suffix else 1)
            count += len(df)
    assert count == 8055
