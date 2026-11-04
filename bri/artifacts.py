"""Raw artifacts (immutable source content) and import batches (parse attempts).

An import attempt is recorded in its own committed transaction, so a failed import keeps
its 'failed' row even though the data transaction was rolled back.
"""

import hashlib
import json
import os
import datetime as dt
import stat
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
        cur.execute("""UPDATE source_artifact SET source_metadata=source_metadata || '{"raw_state":"available"}'::jsonb
                       WHERE artifact_id=%s AND source_metadata->>'raw_state' IN ('purged','purge_pending')""", (row[0],))
    conn.commit()
    return row[0]


def read_artifact(conn, artifact_id):
    with conn.cursor() as cur:
        cur.execute("SELECT raw_archive_path, source_metadata FROM source_artifact WHERE artifact_id = %s", (artifact_id,))
        rel, metadata = cur.fetchone()
    conn.commit()
    if metadata.get("raw_state") == "purged" and not (settings.RAW_ARCHIVE_DIR / rel).exists():
        raise FileNotFoundError("Original artifact {} was intentionally purged after publication; normalized inputs remain in PostgreSQL".format(artifact_id))
    return (settings.RAW_ARCHIVE_DIR / rel).read_bytes()


def purge_local_artifact(conn, artifact_id, digest):
    """Remove only raw originals explicitly owned by local-inbox retention policy.

    Keep the historical archive path and record its lifecycle in existing JSONB metadata.
    Never purge an existing OneDrive/legacy artifact or a path shared with another artifact.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT raw_archive_path,content_sha256,source_metadata FROM source_artifact WHERE artifact_id=%s",
                    (artifact_id,))
        rel, stored_hash, metadata = cur.fetchone()
        if str(stored_hash).strip() != digest:
            raise ValueError("Artifact hash does not match the processed file")
        if metadata.get("local_retention") != "until_published":
            conn.commit()
            return "retained_existing"
        cur.execute("""SELECT EXISTS(SELECT 1 FROM source_artifact WHERE raw_archive_path=%s
                       AND artifact_id<>%s AND COALESCE(source_metadata->>'raw_state','available')<>'purged')""",
                    (rel, artifact_id))
        if cur.fetchone()[0]:
            conn.commit()
            return "retained_shared"
        cur.execute("UPDATE source_artifact SET source_metadata=source_metadata || %s::jsonb WHERE artifact_id=%s",
                    (json.dumps({"raw_state": "purge_pending"}), artifact_id))
    conn.commit()
    root = settings.RAW_ARCHIVE_DIR.resolve()
    path = root / rel
    if path.parent != root or path.is_symlink():
        raise ValueError("Unsafe raw archive path")
    if path.exists():
        if not stat.S_ISREG(path.lstat().st_mode) or sha256_bytes(path.read_bytes()) != digest:
            raise ValueError("Raw archive changed; refusing cleanup")
        path.unlink()
    with conn.cursor() as cur:
        cur.execute("UPDATE source_artifact SET source_metadata=source_metadata || %s::jsonb WHERE artifact_id=%s",
                    (json.dumps({"raw_state": "purged", "raw_purged_at": dt.datetime.now(dt.timezone.utc).isoformat()}), artifact_id))
    conn.commit()
    return "purged"


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
