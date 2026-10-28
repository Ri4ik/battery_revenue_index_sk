"""PostgreSQL access: connections, schema migrations and the pipeline lock."""

import contextlib
import re
from pathlib import Path

import psycopg2
import psycopg2.extras

from bri import settings

SQL_DIR = Path(__file__).resolve().parent / "sql"
# Any number identifying the pipeline; the lock is shared by every worker container.
PIPELINE_LOCK_KEY = 4206917


def _dsn(url=None):
    url = url or settings.DATABASE_URL
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    # SQLAlchemy-style URLs are accepted too.
    return re.sub(r"^postgresql\+psycopg2://", "postgresql://", url)


def connect(url=None):
    conn = psycopg2.connect(_dsn(url))
    with conn.cursor() as cur:
        cur.execute("SET search_path = bri, public")
        cur.execute("SET TIME ZONE 'UTC'")
    conn.commit()
    return conn


@contextlib.contextmanager
def transaction(conn):
    """Commit on success, roll back on any exception."""
    try:
        yield conn.cursor()
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate(conn):
    """Apply bri/sql/NNN_*.sql files that are not recorded in bri.schema_migration."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('bri.schema_migration')")
        applied = set()
        if cur.fetchone()[0]:
            cur.execute("SELECT version FROM bri.schema_migration")
            applied = {r[0] for r in cur.fetchall()}
    conn.commit()
    done = []
    for path in sorted(SQL_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        if path.stem in applied:
            continue
        with transaction(conn) as cur:
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("SET search_path = bri, public")
        done.append(path.stem)
    return done


def try_pipeline_lock(conn):
    """Session-level advisory lock: only one pipeline may run, across all containers."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (PIPELINE_LOCK_KEY,))
        ok = cur.fetchone()[0]
    conn.commit()
    return ok


def release_pipeline_lock(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))
    conn.commit()


def execute_values(cur, sql, rows, page_size=2000):
    psycopg2.extras.execute_values(cur, sql, rows, page_size=page_size)
