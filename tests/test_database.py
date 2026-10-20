import json
import os
import subprocess
import sys

import pandas as pd
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from tools import results_db as db
from tools.import_results_to_db import import_results
from tools.migrate_results_db import backup_database, migrate
from tools import migrate_results_db


def frame(rows=None):
    return pd.DataFrame(rows or [("2025-03-01", "DA", -1.234567890123)], columns=db.FRAME_COLUMNS)


def test_upsert_updates_appends_and_preserves_unmentioned_rows(initialized):
    e = initialized
    db.write_combo(e, db.DEFAULT_DATASET, 1, 1, frame())
    new = frame([("2025-03-01", "DA", 2.0), ("2025-03-02", "DA", 3.0)])
    db.write_combo(e, db.DEFAULT_DATASET, 1, 1, new)
    db.write_combo(e, db.DEFAULT_DATASET, 1, 1, new)
    db.write_combo(e, db.DEFAULT_DATASET, 1, 1, frame())
    actual = db.load_daily_revenue(e, db.DEFAULT_DATASET, 1, 1)
    assert len(actual) == 2
    assert actual.daily_revenue_eur.tolist() == [-1.234567890123, 3.0]
    db.replace_combo(e, db.DEFAULT_DATASET, 1, 1, frame())
    assert len(db.load_daily_revenue(e, db.DEFAULT_DATASET, 1, 1)) == 1


def test_foreign_keys_unique_keys_and_restrict_deletion(initialized):
    e = initialized
    db.write_combo(e, db.DEFAULT_DATASET, 1, 1, frame())
    with e.connect() as conn:
        row = dict(conn.execute(select(db.daily_revenue)).mappings().one())
    for change in ({"dataset_id": 999999}, {"market_id": 999999}, {}):
        with pytest.raises(IntegrityError), e.begin() as conn:
            conn.execute(db.daily_revenue.insert().values(**(row | change)))
    for table in (db.datasets, db.markets):
        with pytest.raises(IntegrityError), e.begin() as conn:
            conn.execute(table.delete())


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True, "1.2", None])
def test_invalid_revenue_cannot_destroy_existing_combo(initialized, invalid):
    db.write_combo(initialized, db.DEFAULT_DATASET, 1, 1, frame())
    with pytest.raises((ValueError, TypeError)):
        db.replace_combo(initialized, db.DEFAULT_DATASET, 1, 1,
                         frame([("2025-03-01", "DA", invalid)]))
    assert db.load_daily_revenue(initialized, db.DEFAULT_DATASET, 1, 1).daily_revenue_eur[0] == -1.234567890123


def test_force_rolls_back_delete_on_database_failure(initialized):
    db.write_combo(initialized, db.DEFAULT_DATASET, 1, 1, frame())
    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO daily_revenue"):
            raise RuntimeError("Injected storage failure after DELETE")
    event.listen(initialized, "before_cursor_execute", fail_insert)
    try:
        with pytest.raises(RuntimeError):
            db.replace_combo(initialized, db.DEFAULT_DATASET, 1, 1,
                             frame([("2025-03-02", "NEW", 99.0)]))
    finally:
        event.remove(initialized, "before_cursor_execute", fail_insert)
    assert len(db.load_daily_revenue(initialized, db.DEFAULT_DATASET, 1, 1)) == 1
    with initialized.connect() as conn:
        assert conn.execute(select(db.markets.c.code)).scalars().all() == ["DA"]


def create_legacy(engine):
    with engine.begin() as conn:
        conn.execute(text("""CREATE TABLE daily_revenue (
            dataset VARCHAR(255) NOT NULL, system_type INTEGER NOT NULL,
            cycle_limit INTEGER NOT NULL, day DATE NOT NULL, market VARCHAR(64) NOT NULL,
            daily_revenue_eur FLOAT NOT NULL,
            PRIMARY KEY(dataset, system_type, cycle_limit, day, market))"""))
        conn.execute(text("INSERT INTO daily_revenue VALUES (:dataset, 1, 1, :day, :market, :value)"), [
            dict(dataset=db.DEFAULT_DATASET, day="2025-03-01", market="ID1", value=-1.234567890123),
            dict(dataset="private", day="2025-03-02", market="IDA1", value=9.25),
        ])


def test_migration_preserves_all_values_and_publication_boundary(engine):
    create_legacy(engine)
    with pytest.raises(RuntimeError, match="backup"):
        migrate(engine)
    migrate(engine, allow_legacy=True)  # Production CLI obtains a dump before enabling this.
    migrate(engine)
    assert db.list_datasets(engine) == [db.DEFAULT_DATASET]
    assert set(db.list_datasets(engine, published_only=False)) == {db.DEFAULT_DATASET, "private"}
    assert db.load_daily_revenue(engine, db.DEFAULT_DATASET, 1, 1).daily_revenue_eur[0] == -1.234567890123
    assert db.load_daily_revenue(engine, "private", 1, 1).market[0] == "IDA1"
    assert inspect(engine).has_table("daily_revenue_legacy_v0")


def test_postgres_migration_rollback(engine):
    if engine.dialect.name != "postgresql":
        pytest.skip("Transactional PostgreSQL DDL")
    create_legacy(engine)
    def fail_copy(conn, cursor, statement, parameters, context, executemany):
        if "INSERT INTO daily_revenue" in statement:
            raise RuntimeError("Injected migration failure")
    event.listen(engine, "before_cursor_execute", fail_copy)
    try:
        with pytest.raises(RuntimeError):
            migrate(engine, allow_legacy=True)
    finally:
        event.remove(engine, "before_cursor_execute", fail_copy)
    assert "dataset" in {c["name"] for c in inspect(engine).get_columns("daily_revenue")}
    assert not inspect(engine).has_table("datasets")
    assert not inspect(engine).has_table("daily_revenue_legacy_v0")


def test_postgres_dump_and_restore_round_trip(engine, tmp_path):
    if engine.dialect.name != "postgresql":
        pytest.skip("pg_dump integration test")
    create_legacy(engine)
    path = backup_database(engine, tmp_path)
    migrate(engine, allow_legacy=True)
    # Restore into the same isolated test DB after removing migrated objects.
    with engine.begin() as conn:
        for table in ("daily_revenue", "daily_revenue_legacy_v0", "datasets", "markets", "schema_migrations"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    url = engine.url
    env = dict(os.environ, PGHOST=url.host, PGPORT=str(url.port or 5432), PGUSER=url.username,
               PGPASSWORD=url.password, PGDATABASE=url.database)
    subprocess.run(["pg_restore", "--exit-on-error", "--single-transaction", "--no-owner",
                    "--no-acl", "--dbname", url.database, str(path)], check=True, env=env)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM daily_revenue")).scalar_one() == 2
        assert conn.execute(text("SELECT daily_revenue_eur FROM daily_revenue WHERE market='ID1'")).scalar_one() == -1.234567890123


def test_legacy_cli_requires_successful_backup(engine, tmp_path, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("Production migration CLI")
    create_legacy(engine)
    monkeypatch.setattr(db, "get_engine", lambda: engine)
    monkeypatch.setattr(sys, "argv", ["migrate", "--backup-dir", str(tmp_path)])
    backup = migrate_results_db.backup_database
    def fail_backup(*args):
        raise RuntimeError("Backup destination unavailable")
    monkeypatch.setattr(migrate_results_db, "backup_database", fail_backup)
    with pytest.raises(RuntimeError, match="Backup destination"):
        migrate_results_db.main()
    assert "dataset" in {c["name"] for c in inspect(engine).get_columns("daily_revenue")}
    monkeypatch.setattr(migrate_results_db, "backup_database", backup)
    migrate_results_db.main()
    assert len(list(tmp_path.glob("*.dump"))) == 1
    assert db.list_datasets(engine) == [db.DEFAULT_DATASET]


def test_corrupt_json_does_not_replace_existing_data(initialized, tmp_path):
    folder = tmp_path / db.DEFAULT_DATASET
    folder.mkdir()
    good = folder / "2025-03-01_results_DA.json"
    good.write_text(json.dumps({"results": {"daily_revenue": {"Total": 1}}}), encoding="utf-8")
    import_results(initialized, tmp_path, [db.DEFAULT_DATASET])
    good.write_text('{"results":', encoding="utf-8")
    with pytest.raises(ValueError):
        import_results(initialized, tmp_path, [db.DEFAULT_DATASET], force=True)
    assert db.load_daily_revenue(initialized, db.DEFAULT_DATASET, 1, 1).daily_revenue_eur[0] == 1


def test_legacy_precedence_and_unpublished_import(initialized, tmp_path):
    for name, value in ((db.DEFAULT_DATASET, 1), (db.DEFAULT_DATASET + "_1_1", 2), ("experiment", 3)):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "2025-03-01_results_DA.json").write_text(
            json.dumps({"results": {"daily_revenue": {"Total": value}}}), encoding="utf-8")
    assert import_results(initialized, tmp_path, [db.DEFAULT_DATASET]) == 1
    assert db.load_daily_revenue(initialized, db.DEFAULT_DATASET, 1, 1).daily_revenue_eur[0] == 2
    import_results(initialized, tmp_path, ["experiment"])
    assert db.list_datasets(initialized) == [db.DEFAULT_DATASET]


def test_password_characters_are_not_interpreted_as_url_separators(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for name, value in {"PGHOST": "db", "PGPORT": "5432", "PGUSER": "bri",
                        "PGPASSWORD": "test@:/#%$password", "PGDATABASE": "bri_test"}.items():
        monkeypatch.setenv(name, value)
    assert db.database_url().password == "test@:/#%$password"
