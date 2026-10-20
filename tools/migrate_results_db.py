"""Schema v1 migration. Modified 2026-10-20; legacy PostgreSQL requires pg_dump.

Run: python -m tools.migrate_results_db --backup-dir /backups
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess

from sqlalchemy import Column, DateTime, Integer, MetaData, Table, inspect, select, text
from tools import results_db as db

VERSION = 1
versions = Table("schema_migrations", MetaData(),
                 Column("version", Integer, primary_key=True),
                 Column("applied_at", DateTime(timezone=True), nullable=False))


def is_legacy(connection):
    inspector = inspect(connection)
    return inspector.has_table("daily_revenue") and "dataset" in {
        c["name"] for c in inspector.get_columns("daily_revenue")}


def backup_database(engine, directory):
    """Passwords go through environment variables, never process arguments."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("before-v1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".dump")
    url = engine.url
    env = dict(os.environ, PGHOST=url.host or "localhost", PGPORT=str(url.port or 5432),
               PGUSER=url.username or "", PGPASSWORD=url.password or "", PGDATABASE=url.database or "")
    path.touch(mode=0o600, exist_ok=False)
    try:
        subprocess.run(["pg_dump", "--format=custom", "--file", str(path)], env=env, check=True)
        if path.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty backup.")
        path.chmod(0o600)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    print(f"Backup saved: {path}")
    return path


def migrate(engine, *, allow_legacy=False):
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(714820261020)"))
        inspector = inspect(conn)
        if inspector.has_table("schema_migrations"):
            applied = list(conn.execute(select(versions.c.version)).scalars())
            if applied:
                if applied != [VERSION]:
                    raise RuntimeError(f"Unsupported schema versions: {applied}")
                return
        legacy = is_legacy(conn)
        if legacy and not allow_legacy:
            raise RuntimeError("Legacy database: run migration CLI with --backup-dir first.")
        if inspector.has_table("daily_revenue") and not legacy:
            raise RuntimeError("Unversioned normalized schema; inspect manually before migrating.")
        if legacy:
            if conn.dialect.name == "postgresql":
                conn.execute(text("LOCK TABLE daily_revenue IN ACCESS EXCLUSIVE MODE"))
            conn.execute(text("ALTER TABLE daily_revenue RENAME TO daily_revenue_legacy_v0"))
        db.metadata.create_all(conn)
        versions.create(conn, checkfirst=True)
        if legacy:
            conn.execute(text("INSERT INTO datasets (name, is_published) "
                              "SELECT DISTINCT dataset, dataset = :published FROM daily_revenue_legacy_v0"),
                         {"published": db.DEFAULT_DATASET})
            conn.execute(text("INSERT INTO markets (code) SELECT DISTINCT market FROM daily_revenue_legacy_v0"))
            conn.execute(text("""
                INSERT INTO daily_revenue
                    (dataset_id, system_type, cycle_limit, day, market_id, daily_revenue_eur)
                SELECT d.id, r.system_type, r.cycle_limit, r.day, m.id, r.daily_revenue_eur
                FROM daily_revenue_legacy_v0 r
                JOIN datasets d ON d.name = r.dataset JOIN markets m ON m.code = r.market
            """))
            old = "SELECT dataset, system_type, cycle_limit, day, market, daily_revenue_eur FROM daily_revenue_legacy_v0"
            new = ("SELECT d.name, r.system_type, r.cycle_limit, r.day, m.code, r.daily_revenue_eur "
                   "FROM daily_revenue r JOIN datasets d ON d.id=r.dataset_id JOIN markets m ON m.id=r.market_id")
            old_count = conn.execute(text("SELECT count(*) FROM daily_revenue_legacy_v0")).scalar_one()
            new_count = conn.execute(select(db.func.count()).select_from(db.daily_revenue)).scalar_one()
            if old_count != new_count:
                raise RuntimeError("Migration row count mismatch; rolling back.")
            for left, right in ((old, new), (new, old)):
                if conn.execute(text(f"SELECT count(*) FROM ({left} EXCEPT {right}) AS difference")).scalar_one():
                    raise RuntimeError("Migration value mismatch; rolling back.")
            print(f"Verified {new_count} rows. Legacy table retained for comparison.")
        conn.execute(versions.insert().values(version=VERSION, applied_at=datetime.now(timezone.utc)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", help="Required for a legacy PostgreSQL database.")
    args = parser.parse_args()
    engine = db.get_engine()
    with engine.connect() as conn:
        legacy = is_legacy(conn)
    if legacy:
        if engine.dialect.name != "postgresql" or not args.backup_dir:
            raise SystemExit("Legacy migration requires PostgreSQL and --backup-dir (pg_dump backup).")
        backup_database(engine, args.backup_dir)
    migrate(engine, allow_legacy=legacy)
    print(f"Schema version {VERSION} ready.")


if __name__ == "__main__":
    main()
