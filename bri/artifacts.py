"""Raw artifacts (immutable source content) and import batches (parse attempts).

An import attempt is recorded in its own committed transaction, so a failed import keeps
its 'failed' row even though the data transaction was rolled back.
"""

import hashlib
import json
import os
import tempfile

from bri import settings


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def store_artifact(conn, source_code, transport, content, extension, original_name=None, metadata=None):
    """Archive raw content as raw/<sha256>.<ext> and return its artifact_id (existing or new)."""
    digest = sha256_bytes(content)
    settings.RAW_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    rel = "{}.{}".format(digest, extension.lstrip("."))
    path = settings.RAW_ARCHIVE_DIR / rel
    if not path.exists():
        # write-then-rename: a crash never leaves a partial file under the final name
        fd, tmp = tempfile.mkstemp(dir=str(settings.RAW_ARCHIVE_DIR), suffix=".part")
        with os.fdopen(fd, "wb") as fp:
            fp.write(content)
        os.replace(tmp, str(path))
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO source_artifact (source_id, transport, content_sha256, original_name,
                                         raw_archive_path, source_metadata)
            SELECT source_id, %s, %s, %s, %s, %s FROM data_source WHERE code = %s
            ON CONFLICT (source_id, content_sha256) DO NOTHING
            RETURNING artifact_id
            """,
            (transport, digest, original_name, rel, json.dumps(metadata or {}), source_code),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """SELECT a.artifact_id FROM source_artifact a JOIN data_source s USING (source_id)
                   WHERE s.code = %s AND a.content_sha256 = %s""",
                (source_code, digest),
            )
            row = cur.fetchone()
    conn.commit()
    return row[0]


def read_artifact(conn, artifact_id):
    with conn.cursor() as cur:
        cur.execute("SELECT raw_archive_path FROM source_artifact WHERE artifact_id = %s", (artifact_id,))
        rel = cur.fetchone()[0]
    conn.commit()
    return (settings.RAW_ARCHIVE_DIR / rel).read_bytes()


def successful_batch(conn, artifact_id, parser_version):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT batch_id FROM import_batch
               WHERE artifact_id = %s AND parser_version = %s AND status = 'success'""",
            (artifact_id, parser_version),
        )
        row = cur.fetchone()
    conn.commit()
    return row[0] if row else None


def start_batch(conn, artifact_id, parser_version):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO import_batch (artifact_id, parser_version, status)
               VALUES (%s, %s, 'running') RETURNING batch_id""",
            (artifact_id, parser_version),
        )
        batch_id = cur.fetchone()[0]
    conn.commit()
    return batch_id


def fail_batch(conn, batch_id, error):
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE import_batch SET status = 'failed', finished_at = now(), error_summary = %s
               WHERE batch_id = %s""",
            (str(error)[:2000], batch_id),
        )
    conn.commit()


def mark_stale_batches(conn, older_than_minutes=180):
    """Batches left 'running' by a killed process are marked failed."""
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE import_batch SET status = 'failed', finished_at = now(),
                      error_summary = 'interrupted (process stopped while running)'
               WHERE status = 'running' AND started_at < now() - make_interval(mins => %s)""",
            (older_than_minutes,),
        )
        n = cur.rowcount
    conn.commit()
    return n


def import_artifact(conn, artifact_id, parser_version, write_rows):
    """Run write_rows(cur, batch_id) -> rows_written inside one transaction.

    Returns (batch_id, rows_written, skipped). A successful earlier import of the same
    artifact with the same parser version is not repeated.
    """
    existing = successful_batch(conn, artifact_id, parser_version)
    if existing:
        return existing, 0, True
    batch_id = start_batch(conn, artifact_id, parser_version)
    try:
        with conn.cursor() as cur:
            rows = write_rows(cur, batch_id)
            cur.execute(
                """UPDATE import_batch SET status = 'success', finished_at = now(), rows_written = %s
                   WHERE batch_id = %s""",
                (rows, batch_id),
            )
        conn.commit()
    except Exception as exc:
        fail_batch(conn, batch_id, exc)
        raise
    return batch_id, rows, False
