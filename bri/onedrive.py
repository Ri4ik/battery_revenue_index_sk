"""OneDrive Personal inbox via rclone (delegated OAuth of the folder owner).

Folder layout under ONEDRIVE_REMOTE (e.g. onedrive:BatteryIndex):
    1_Nahrať/      uploads from the team (SEPS/Damas XLSX)
    2_Spracované/  imported files (moved here only after the database commit)
    3_Chyby/       unusable files + <file>.chyba.txt with the reason
    STAV.txt       status written by every pipeline run

Network or authorization problems never move a file to 3_Chyby: the file stays in the
inbox and is retried by the next run. If the move after a successful import fails, the
file is recorded as archive_pending and only the move is retried later.
"""

import datetime as dt
import json
import os
import subprocess
import tempfile

import pandas as pd

from bri import artifacts, settings
from bri.importers import seps

AUTH_MARKERS = ("invalid_grant", "unauthenticated", "401", "token", "couldn't find section", "didn't find section")


class RcloneError(Exception):
    def __init__(self, message, auth=False):
        super().__init__(message)
        self.auth = auth


def _remote(*parts):
    return "/".join([settings.ONEDRIVE_REMOTE.rstrip("/")] + [p.strip("/") for p in parts if p])


def rclone(*args, stdin=None, timeout=300):
    cmd = [settings.RCLONE_BIN] + list(args) + ["--retries", "3", "--low-level-retries", "5"]
    try:
        res = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except FileNotFoundError:
        raise RcloneError("rclone nie je nainštalovaný ({})".format(settings.RCLONE_BIN))
    except subprocess.TimeoutExpired:
        raise RcloneError("rclone: časový limit {} s".format(timeout))
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", "replace").strip().splitlines()
        text = " | ".join(err[-3:])[:500]
        raise RcloneError(text, auth=any(m in text.lower() for m in AUTH_MARKERS))
    return res.stdout


def list_dir(folder):
    out = rclone("lsjson", _remote(folder), "--files-only", "--hash")
    return json.loads(out.decode("utf-8") or "[]")


def _version(item):
    hashes = item.get("Hashes") or {}
    return "{}|{}|{}".format(item.get("Size"), item.get("ModTime"), item.get("ID") or next(iter(hashes.values()), ""))


def _stamp():
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _record(conn, remote_path, version, state, artifact_id=None, target=None, error=None):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO inbox_file (remote_path, remote_version, artifact_id, state, target_path, last_error)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (remote_path, remote_version) DO UPDATE SET
                   artifact_id = COALESCE(EXCLUDED.artifact_id, inbox_file.artifact_id),
                   state = EXCLUDED.state, target_path = EXCLUDED.target_path,
                   last_error = EXCLUDED.last_error, updated_at = now()""",
            (remote_path, version, artifact_id, state, target, error),
        )
    conn.commit()


def _move(src_name, target_folder, target_name):
    rclone("moveto", _remote(settings.INBOX_DIR, src_name), _remote(target_folder, target_name))


def retry_pending_moves(conn, messages):
    with conn.cursor() as cur:
        cur.execute("""SELECT remote_path, remote_version, target_path FROM inbox_file
                       WHERE state = 'archive_pending'""")
        pending = cur.fetchall()
    conn.commit()
    for name, version, target in pending:
        folder, target_name = target.split("/", 1)
        try:
            _move(name, folder, target_name)
            _record(conn, name, version, "archived", target=target)
        except RcloneError as exc:
            messages.append("OneDrive: presun {} stále čaká ({})".format(name, exc))


def intake(conn, messages):
    """Import new XLSX files from 1_Nahrať. Returns the delivery days that got new data."""
    retry_pending_moves(conn, messages)
    files = list_dir(settings.INBOX_DIR)
    now = pd.Timestamp.now(tz="UTC")
    touched_days = set()
    for item in files:
        name = item["Name"]
        version = _version(item)
        with conn.cursor() as cur:
            cur.execute("SELECT state FROM inbox_file WHERE remote_path = %s AND remote_version = %s",
                        (name, version))
            row = cur.fetchone()
        conn.commit()
        if row and row[0] in ("archived", "archive_pending"):
            continue  # pending moves were retried above
        age = now - pd.Timestamp(item["ModTime"]).tz_convert("UTC")
        if age < pd.Timedelta(minutes=settings.INBOX_MIN_AGE_MINUTES):
            messages.append("OneDrive: {} je príliš nový, spracuje sa neskôr".format(name))
            continue
        if not name.lower().endswith(".xlsx"):
            _reject(conn, name, version, None, "nie je súbor .xlsx (očakáva sa export SEPS/Damas)", messages)
            continue

        fd, tmp = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        try:
            rclone("copyto", _remote(settings.INBOX_DIR, name), tmp)
            content = open(tmp, "rb").read()
        finally:
            os.remove(tmp)
        # the file must not have changed while it was downloaded
        after = {i["Name"]: _version(i) for i in list_dir(settings.INBOX_DIR)}
        if after.get(name) != version:
            messages.append("OneDrive: {} sa počas sťahovania zmenil, skúsi sa znova".format(name))
            continue

        digest = artifacts.sha256_bytes(content)
        try:
            res = seps.import_xlsx(conn, content, name, "onedrive",
                                   metadata={"remote": settings.ONEDRIVE_REMOTE, "item": item.get("ID"),
                                             "version": version})
        except seps.FormatError as exc:
            _reject(conn, name, version, None, str(exc), messages)
            continue
        target_name = "{}_{}_{}".format(_stamp(), digest[:8], name)
        target = "{}/{}".format(settings.PROCESSED_DIR, target_name)
        state = "archive_pending"
        _record(conn, name, version, state, artifact_id=res["artifact_id"], target=target)
        if res["skipped"]:
            messages.append("OneDrive: {} už bol importovaný (rovnaký obsah)".format(name))
        else:
            messages.append("OneDrive: {} importovaný ({} riadkov, {}..{})".format(
                name, res["rows"], res["first_day"], res["last_day"]))
            touched_days.update(pd.date_range(res["first_day"], res["last_day"]).date)
        try:
            _move(name, settings.PROCESSED_DIR, target_name)
            _record(conn, name, version, "archived", artifact_id=res["artifact_id"], target=target)
        except RcloneError as exc:
            messages.append("OneDrive: presun {} zlyhal, zopakuje sa ({})".format(name, exc))
    return touched_days


def _reject(conn, name, version, artifact_id, reason, messages):
    target_name = "{}_{}".format(_stamp(), name)
    try:
        rclone("rcat", _remote(settings.ERROR_DIR, target_name + ".chyba.txt"),
               stdin=("Súbor: {}\nDôvod: {}\n".format(name, reason)).encode("utf-8"))
        _move(name, settings.ERROR_DIR, target_name)
        _record(conn, name, version, "rejected", artifact_id=artifact_id,
                target="{}/{}".format(settings.ERROR_DIR, target_name), error=reason)
        messages.append("OneDrive: {} odmietnutý - {}".format(name, reason))
    except RcloneError as exc:
        messages.append("OneDrive: {} odmietnutý ({}), presun zlyhal: {}".format(name, reason, exc))


def write_status(text):
    rclone("rcat", _remote(settings.STATUS_FILE), stdin=text.encode("utf-8"))
