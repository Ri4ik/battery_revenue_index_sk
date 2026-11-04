"""Local transport, publication-aware cleanup, crash recovery and durable reports."""
import json
import io
from pathlib import Path

import pytest
import openpyxl

from bri import artifacts, calc, db, inputs, local_inbox, pipeline, settings
from bri.importers import seps
from tests.conftest import D, TEST_URL, seps_xlsx

DAY = D(2025, 6, 10)


@pytest.fixture
def local_dirs(conn, tmp_path, monkeypatch):
    inbox, reports = tmp_path / "incoming", tmp_path / "reports"
    inbox.mkdir()
    monkeypatch.setattr(settings, "LOCAL_INBOX", inbox)
    monkeypatch.setattr(settings, "REPORT_DIR", reports)
    monkeypatch.setattr(settings, "LOCAL_MIN_AGE_SECONDS", 0)
    monkeypatch.setattr(settings, "DATABASE_URL", TEST_URL)
    monkeypatch.setattr(settings, "DELETE_AFTER_SUCCESS", False)
    monkeypatch.setattr(pipeline.onedrive, "intake", lambda *a: pytest.fail("local mode called OneDrive"))
    return inbox, reports


def _capacity(conn, day=DAY):
    rows = [(day, d, h, h + 4, 10.0, 100.0) for d in ("POS", "NEG") for h in range(0, 24, 4)]
    aid = artifacts.store_artifact(conn, "ENTSOE", "api", repr(rows).encode(), "txt")
    artifacts.import_artifact(conn, aid, "test-local-cap", lambda cur, b: inputs.write_afrr_capacity_rows(cur, rows, b))


def _run(delete=True):
    return pipeline.main(["--source", "local", "--skip-api", "--from", str(DAY), "--to", str(DAY),
                          "--delete-after-success" if delete else "--keep-files", "--trigger", "test"])


def _report(reports):
    files = list(reports.glob("local_*.json"))
    return max((json.loads(p.read_text(encoding="utf-8")) for p in files), key=lambda r: r["pipeline_run_id"])


def _count(conn, table):
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM " + table)
        value = cur.fetchone()[0]
    conn.commit()
    return value


def test_real_file_flow_publishes_and_cleans_after_report(conn, local_dirs):
    inbox, reports = local_dirs
    content = seps_xlsx(DAY)
    (inbox / "june.xlsx").write_bytes(content)
    _capacity(conn)
    assert _run() == 0
    report = _report(reports)
    entry = report["files"][0]
    assert report["status"] == "success" and entry["status"] == "completed"
    assert entry["source_rows"] == 96 and entry["rows_written"] == 96
    assert entry["published_results"] == 4 and entry["input_deleted"]
    assert not list((inbox / ".processing").glob("*.xlsx"))
    assert not (inbox / "june.xlsx").exists()
    assert _count(conn, "afrr_energy") == 96
    assert _count(conn, "result_publication") == 4
    assert list(reports.glob("local_*.txt"))
    with pytest.raises(FileNotFoundError, match="intentionally purged"):
        artifacts.read_artifact(conn, entry["artifact_id"])
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='bri' AND table_type='BASE TABLE'")
        assert cur.fetchone()[0] == 23
    conn.commit()


def test_same_content_renamed_after_purge_does_not_duplicate_or_recalculate(conn, local_dirs):
    inbox, reports = local_dirs
    content = seps_xlsx(DAY)
    _capacity(conn)
    (inbox / "first.xlsx").write_bytes(content)
    assert _run() == 0
    batches, runs = _count(conn, "import_batch"), _count(conn, "calc_run")
    (inbox / "renamed.xlsx").write_bytes(content)
    assert _run() == 0
    assert _count(conn, "import_batch") == batches
    assert _count(conn, "calc_run") == runs
    assert _count(conn, "afrr_energy") == 96
    entry = _report(reports)["files"][0]
    assert entry["duplicate"] and entry["raw_cleanup"] == "purged"


def test_keep_mode_and_corrected_same_filename_create_new_revision(conn, local_dirs):
    inbox, reports = local_dirs
    _capacity(conn)
    source = inbox / "june.xlsx"
    source.write_bytes(seps_xlsx(DAY, up_price=100.0))
    assert _run(delete=False) == 0
    assert source.exists() and not _report(reports)["files"][0]["input_deleted"]
    source.write_bytes(seps_xlsx(DAY, up_price=120.0))
    assert _run() == 0
    assert _count(conn, "afrr_energy") == 192
    assert _count(conn, "calc_run") == 2
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT up_price_eur_mwh FROM afrr_energy_current")
        assert [float(row[0]) for row in cur.fetchall()] == [120.0]
    conn.commit()


def test_missing_capacity_keeps_original_then_resumes_without_reimport(conn, local_dirs):
    inbox, reports = local_dirs
    (inbox / "june.xlsx").write_bytes(seps_xlsx(DAY))
    assert _run() == 1
    assert _report(reports)["files"][0]["status"] == "waiting_data"
    assert len(list((inbox / ".processing").glob("*.xlsx"))) == 1
    _capacity(conn)
    batches = _count(conn, "import_batch")
    assert _run() == 0
    assert _count(conn, "import_batch") == batches
    assert _count(conn, "result_publication") == 4


def test_bad_file_is_quarantined_and_existing_inputs_remain(conn, local_dirs):
    inbox, reports = local_dirs
    existing = seps_xlsx(DAY)
    seps.import_xlsx(conn, existing, "existing.xlsx", "local")
    (inbox / "broken.xlsx").write_bytes(b"not xlsx")
    assert _run() == 1
    assert _count(conn, "afrr_energy") == 96
    assert len(list((inbox / "errors").glob("*.xlsx"))) == 1
    entry = _report(reports)["files"][0]
    assert entry["status"] == "rejected" and entry["error"]


def test_current_seps_volume_only_export_waits_for_api_without_wrong_prices(conn, local_dirs):
    inbox, reports = local_dirs
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "aFRR"
    sheet.append([None, None, None, "Up", "Down"])
    sheet.append([None, None, None, "Objem [MWh]", "Objem [MWh]"])
    for qh in range(1, 97):
        sheet.append([DAY.strftime("%d.%m.%Y") if qh == 1 else None,
                      (qh - 1) // 4 + 1, (qh - 1) % 4 + 1, 45.636, 0.012])
    buf = io.BytesIO()
    workbook.save(buf)
    (inbox / "volume_only.xlsx").write_bytes(buf.getvalue())
    assert _run() == 1
    assert _count(conn, "afrr_energy") == 0
    assert _report(reports)["files"][0]["status"] == "waiting_api"
    assert "Run without --skip-api" in _report(reports)["files"][0]["error"]
    assert len(list((inbox / ".processing").glob("*.xlsx"))) == 1


def test_four_header_rows_do_not_drop_the_first_day():
    workbook = openpyxl.load_workbook(io.BytesIO(seps_xlsx(DAY)))
    workbook["aFRR"].delete_rows(1)
    buf = io.BytesIO()
    workbook.save(buf)
    rows = seps.parse_xlsx(buf.getvalue())
    assert len(rows) == 96
    assert str(rows[0][0].tz_convert(settings.TZ).date()) == str(DAY)


def test_partial_uploads_and_excel_lock_files_are_ignored(conn, local_dirs):
    inbox, reports = local_dirs
    (inbox / "upload.xlsx.part").write_bytes(b"partial")
    (inbox / "~$upload.xlsx").write_bytes(b"lock")
    assert _run() == 0
    assert _count(conn, "afrr_energy") == 0
    assert _report(reports)["files"] == []


def test_symlink_outside_inbox_is_not_processed_or_deleted(conn, local_dirs, tmp_path):
    inbox, reports = local_dirs
    outside = tmp_path / "outside.xlsx"
    content = seps_xlsx(DAY)
    outside.write_bytes(content)
    (inbox / "link.xlsx").symlink_to(outside)
    assert _run() == 1
    assert outside.read_bytes() == content
    assert _count(conn, "afrr_energy") == 0


def test_parallel_pipeline_cannot_claim_files(conn, local_dirs):
    inbox, reports = local_dirs
    (inbox / "june.xlsx").write_bytes(seps_xlsx(DAY))
    assert db.try_pipeline_lock(conn)
    try:
        assert _run() == 2
        assert (inbox / "june.xlsx").exists()
    finally:
        db.release_pipeline_lock(conn)


def test_report_failure_prevents_cleanup_then_resumes(conn, local_dirs, monkeypatch):
    inbox, reports = local_dirs
    (inbox / "june.xlsx").write_bytes(seps_xlsx(DAY))
    _capacity(conn)
    original = local_inbox.LocalInboxRun.write_report
    def fail_before_cleanup(self, status, messages):
        if status == "processing":
            raise OSError("reports disk full")
        return original(self, status, messages)
    monkeypatch.setattr(local_inbox.LocalInboxRun, "write_report", fail_before_cleanup)
    assert _run() == 1
    entry = _report(reports)["files"][0]
    assert entry["status"] == "cleanup_pending"
    assert len(list((inbox / ".processing").glob("*.xlsx"))) == 1
    assert artifacts.read_artifact(conn, entry["artifact_id"])
    batches = _count(conn, "import_batch")
    monkeypatch.setattr(local_inbox.LocalInboxRun, "write_report", original)
    assert _run() == 0
    assert _count(conn, "import_batch") == batches


def test_cleanup_failure_resumes_without_reimport_after_raw_was_purged(conn, local_dirs, monkeypatch):
    inbox, reports = local_dirs
    (inbox / "june.xlsx").write_bytes(seps_xlsx(DAY))
    _capacity(conn)
    original = Path.unlink
    def fail_unlink(path, *args, **kwargs):
        if path.parent.name == ".processing":
            raise PermissionError("input temporarily locked")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    assert _run() == 1
    batches = _count(conn, "import_batch")
    monkeypatch.setattr(Path, "unlink", original)
    assert _run() == 0
    assert _count(conn, "import_batch") == batches
    assert _report(reports)["files"][0]["input_deleted"]


def test_preexisting_nonlocal_archive_is_not_purged(conn, local_dirs):
    inbox, reports = local_dirs
    content = seps_xlsx(DAY)
    res = seps.import_xlsx(conn, content, "old.xlsx", "onedrive")
    _capacity(conn)
    (inbox / "june.xlsx").write_bytes(content)
    assert _run() == 0
    assert artifacts.read_artifact(conn, res["artifact_id"]) == content
    assert _report(reports)["files"][0]["raw_cleanup"] == "retained_existing"


def test_claim_intent_recovers_before_rename(conn, local_dirs):
    inbox, reports = local_dirs
    source = inbox / "june.xlsx"
    source.write_bytes(seps_xlsx(DAY))
    _capacity(conn)
    run = local_inbox.LocalInboxRun(conn, 0, True)
    entry = {"entry_id": "a" * 32, "original_name": source.name,
             "status": "claim_pending", "delete_requested": True, "rows_written": 0}
    run._save(entry)
    assert _run() == 0
    assert len(_report(reports)["files"]) == 1


def test_file_dates_outside_normal_api_window_are_calculated(conn, local_dirs):
    inbox, reports = local_dirs
    (inbox / "old.xlsx").write_bytes(seps_xlsx(DAY))
    _capacity(conn)
    assert pipeline.main(["--source", "local", "--skip-api", "--delete-after-success", "--trigger", "test"]) == 0
    assert _count(conn, "result_publication") == 4


def test_old_publication_does_not_authorize_cleanup_of_changed_inputs(conn, local_dirs, monkeypatch):
    inbox, reports = local_dirs
    _capacity(conn)
    source = inbox / "june.xlsx"
    source.write_bytes(seps_xlsx(DAY, up_price=100.0))
    assert _run(delete=False) == 0
    source.write_bytes(seps_xlsx(DAY, up_price=200.0))
    monkeypatch.setattr(calc, "run", lambda *a, **k: (None, 0, ["simulated calculation failure"]))
    assert _run() == 1
    entry = _report(reports)["files"][0]
    assert entry["status"] == "waiting_data" and not entry.get("input_deleted")
    assert len(list((inbox / ".processing").glob("*.xlsx"))) == 1


@pytest.mark.parametrize("delete", [False, True])
def test_crash_after_filesystem_cleanup_recovers_from_durable_intent(conn, local_dirs, monkeypatch, delete):
    inbox, reports = local_dirs
    _capacity(conn)
    (inbox / "june.xlsx").write_bytes(seps_xlsx(DAY))
    original = local_inbox.LocalInboxRun._save
    crashed = []
    def crash_once(self, entry):
        if entry["status"] == "completed" and not crashed:
            crashed.append(True)
            raise OSError("simulated process interruption after filesystem operation")
        return original(self, entry)
    monkeypatch.setattr(local_inbox.LocalInboxRun, "_save", crash_once)
    assert _run(delete=delete) == 1
    batches = _count(conn, "import_batch")
    monkeypatch.setattr(local_inbox.LocalInboxRun, "_save", original)
    assert _run(delete=delete) == 0
    assert _count(conn, "import_batch") == batches
    assert _report(reports)["files"][0]["status"] == "completed"


def test_missing_price_for_activated_energy_is_rejected():
    workbook = openpyxl.load_workbook(io.BytesIO(seps_xlsx(DAY)))
    workbook["aFRR"].cell(row=6, column=5).value = None
    buf = io.BytesIO()
    workbook.save(buf)
    with pytest.raises(seps.FormatError, match="Missing Up price"):
        seps.parse_xlsx(buf.getvalue())
