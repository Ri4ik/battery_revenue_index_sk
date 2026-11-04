"""Recoverable local SEPS inbox. Journals and reports live on the reports bind mount.

No schema change is required. Files are claimed by atomic rename, imported with the
existing parser, and removed only after matching current aFRR results are published.
"""
import datetime as dt
import json
import os
import re
import stat
import time
import uuid
from pathlib import Path

import pandas as pd

from bri import MODEL_VERSION, artifacts, settings
from bri.importers import seps, entsoe_energy

FINAL = ("completed", "rejected", "abandoned")
# Statuses that must survive an error so the interrupted step is resumed next time.
RESUMABLE = ("claim_pending", "keep_pending")
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _atomic_json(path, value):
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        json.dump(value, fp, ensure_ascii=False, indent=2, default=str)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(str(tmp), str(path))


def _regular(path):
    return not path.is_symlink() and stat.S_ISREG(path.lstat().st_mode)


class LocalInboxRun:
    def __init__(self, conn, pipeline_run_id, delete_after_success=False, allow_api=True):
        self.conn = conn
        self.run_id = pipeline_run_id
        self.delete = delete_after_success
        self.allow_api = allow_api
        self.inbox = settings.LOCAL_INBOX.resolve()
        self.reports = settings.REPORT_DIR.resolve()
        if self.inbox == self.reports or self.inbox in self.reports.parents or self.reports in self.inbox.parents:
            raise ValueError("Inbox and reports must be separate directories")
        self.processing = self.inbox / ".processing"
        self.state_dir = self.reports / ".state"
        self.errors = self.inbox / "errors"
        # kept inputs leave the scanned top level, otherwise every run would claim them again
        self.kept = self.inbox / "processed"
        for p in (self.inbox, self.reports, self.processing, self.state_dir, self.errors, self.kept):
            if p.is_symlink():
                raise ValueError("Inbox runtime directories must not be symlinks")
            p.mkdir(parents=True, exist_ok=True)
        self.entries = []
        self.deferred = []
        self.started = dt.datetime.now(dt.timezone.utc).isoformat()
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.stem = "local_{}_{}".format(pipeline_run_id, stamp)
        self.json_path = self.reports / (self.stem + ".json")
        self.txt_path = self.reports / (self.stem + ".txt")
        # Fail before claiming/importing files if reports cannot be persisted.
        self.write_report("running", [])

    def _work_path(self, entry):
        eid = entry["entry_id"]
        if not ID_PATTERN.fullmatch(eid):
            raise ValueError("Invalid local journal identifier")
        return self.processing / (eid + ".xlsx")

    def _save(self, entry):
        eid = entry["entry_id"]
        if not ID_PATTERN.fullmatch(eid):
            raise ValueError("Invalid local journal identifier")
        entry["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _atomic_json(self.state_dir / (eid + ".json"), entry)

    def _recover(self):
        for path in sorted(self.state_dir.glob("*.json")):
            if path.is_symlink() or not ID_PATTERN.fullmatch(path.stem):
                raise ValueError("Unsafe local journal path")
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry.get("entry_id") != path.stem:
                raise ValueError("Local journal identity mismatch")
            if entry.get("status") not in FINAL:
                self.entries.append(entry)

    def _claim(self, path):
        entry = {"entry_id": uuid.uuid4().hex, "original_name": path.name,
                 "status": "claim_pending", "pipeline_run_id": self.run_id,
                 "delete_requested": self.delete, "rows_written": 0}
        self._save(entry)  # durable intent before moving a source file
        os.replace(str(path), str(self._work_path(entry)))
        entry["status"] = "claimed"
        self._save(entry)
        self.entries.append(entry)

    def intake(self):
        self._recover()
        reserved = {e["original_name"] for e in self.entries
                    if e["status"] == "claim_pending" and not self._work_path(e).exists()}
        reserved |= {Path(e["kept_file"]).name for e in self.entries
                     if e["status"] == "keep_pending" and e.get("kept_file") and not self._work_path(e).exists()}
        for path in sorted(self.inbox.iterdir()):
            if path.name in reserved:
                continue
            if path.name.startswith((".", "~$")) or path.suffix.lower() != ".xlsx":
                continue
            if not _regular(path):
                self.deferred.append({"name": path.name, "reason": "not a regular file (symlinks are ignored)"})
                continue
            if time.time() - path.stat().st_mtime < settings.LOCAL_MIN_AGE_SECONDS:
                self.deferred.append({"name": path.name, "reason": "upload is too recent; retry after {} seconds".format(settings.LOCAL_MIN_AGE_SECONDS)})
                continue
            self._claim(path)

        touched = set()
        for entry in self.entries:
            entry["pipeline_run_id"] = self.run_id
            entry["delete_requested"] = entry.get("delete_requested", False) or self.delete
            entry.pop("error", None)
            work = self._work_path(entry)
            try:
                if entry["status"] == "keep_pending" and not work.exists():
                    target = Path(entry["kept_file"])
                    if target.parent != self.kept or not _regular(target) or artifacts.sha256_bytes(target.read_bytes()) != entry["sha256"]:
                        raise ValueError("Kept file could not be verified after interruption")
                    entry["status"] = "completed"
                    self._save(entry)
                    continue
                if entry["status"] == "claim_pending" and not work.exists():
                    name = entry["original_name"]
                    if Path(name).name != name:
                        raise ValueError("Invalid original filename")
                    source = self.inbox / name
                    if not _regular(source):
                        raise ValueError("Unclaimed source is not a regular file")
                    os.replace(str(source), str(work))
                if not work.exists() and entry["status"] == "cleanup_pending":
                    # A crash after unlink but before the final report/journal commit.
                    touched.update(pd.Timestamp(d).date() for d in entry["days"])
                    continue
                if not work.exists() and not work.is_symlink():
                    # The claimed input is gone (e.g. removed by hand): close the entry once
                    # instead of failing on every following run.
                    entry["status"], entry["error"] = "abandoned", "claimed input file no longer exists"
                    self._save(entry)
                    continue
                if not _regular(work):
                    raise ValueError("Claimed input must be a regular file")
                before = work.stat()
                content = work.read_bytes()
                after = work.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError("Input changed while being read; retry after upload completes")
                digest = artifacts.sha256_bytes(content)
                if entry.get("sha256") and entry["sha256"] != digest:
                    raise ValueError("Claimed input changed; refusing to import/delete it")
                entry["sha256"] = digest
                # Reuse a successful import on resume even when the preceding calculation failed.
                imported = False
                if entry.get("batch_id"):
                    with self.conn.cursor() as cur:
                        cur.execute("""SELECT EXISTS(SELECT 1 FROM import_batch b JOIN source_artifact a USING(artifact_id)
                                       WHERE b.batch_id=%s AND b.status='success' AND a.artifact_id=%s
                                         AND a.content_sha256=%s AND b.parser_version=%s)""",
                                    (entry["batch_id"], entry["artifact_id"], entry.get("artifact_sha256", digest),
                                     entry.get("parser_version", seps.PARSER)))
                        imported = cur.fetchone()[0]
                    self.conn.commit()
                if not imported:
                    inspected = seps.inspect_local_xlsx(content, entry["original_name"])
                    days = inspected["days"]
                    yesterday = pd.Timestamp.now(tz=settings.TZ).date() - dt.timedelta(days=1)
                    if any(d < settings.FIRST_DAY or pd.Timestamp(d).date() > yesterday for d in days):
                        raise seps.FormatError("File contains days outside the supported historical period {} .. {}".format(settings.FIRST_DAY, yesterday))
                    entry.update(days=days, source_rows=inspected["source_rows"], input_format=inspected["format"],
                                 volume_metadata=inspected["volume_metadata"])
                    self._save(entry)  # retain inspected period even when the remote API is unavailable
                    res = seps.import_local_xlsx(self.conn, content, entry["original_name"], inspected,
                                                metadata={"local_retention": "until_published", "raw_state": "available",
                                                          "local_entry_id": entry["entry_id"]}, allow_api=self.allow_api)
                    entry.update(artifact_id=res["artifact_id"], batch_id=res["batch_id"],
                                 days=res["days"], source_rows=res["source_rows"],
                                 rows_written=res["rows"], duplicate=res["skipped"], parser_version=res["parser_version"],
                                 artifact_sha256=res["artifact_sha256"])
                    for key in ("additional_raw_artifacts", "price_documents", "price_profiles", "volume_metadata"):
                        if key in res:
                            entry[key] = res[key]
                entry["status"] = "imported"
                self._save(entry)
                touched.update(pd.Timestamp(d).date() for d in entry["days"])
            except entsoe_energy.PricesUnavailable as exc:
                self.conn.rollback()
                entry["status"], entry["error"] = "waiting_api", str(exc)
                touched.update(pd.Timestamp(d).date() for d in entry.get("days", []))
                self._save(entry)
            except seps.FormatError as exc:
                self.conn.rollback()
                entry["status"], entry["error"] = "rejected", str(exc)
                if work.exists() and not work.is_symlink():
                    target = self.errors / (entry["entry_id"] + "_" + Path(entry["original_name"]).name)
                    os.replace(str(work), str(target))
                    entry["error_file"] = str(target)
                self._save(entry)
            except Exception as exc:
                self.conn.rollback()
                if entry["status"] not in RESUMABLE:
                    entry["status"] = "failed"
                entry["error"] = str(exc)
                self._save(entry)
        self.write_report("imported", [])
        return touched

    def _publication_check(self, entry):
        days = [pd.Timestamp(d).date() for d in entry["days"]]
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM battery_config")
            configs = cur.fetchone()[0]
            cur.execute("""SELECT p.delivery_day,count(DISTINCT p.config_id)
                FROM result_publication p JOIN market m USING(market_id) JOIN calc_run r USING(run_id)
                JOIN result_daily d ON d.run_id=p.run_id AND d.config_id=p.config_id
                  AND d.market_id=p.market_id AND d.delivery_day=p.delivery_day AND d.component='total'
                JOIN market_day en ON en.market_id=p.market_id AND en.delivery_day=p.delivery_day
                  AND en.component='energy' AND en.status='complete'
                JOIN market_day cap ON cap.market_id=p.market_id AND cap.delivery_day=p.delivery_day
                  AND cap.component='capacity' AND cap.status='complete'
                JOIN calc_input ien ON ien.run_id=p.run_id AND ien.market_id=p.market_id
                  AND ien.delivery_day=p.delivery_day AND ien.component='energy' AND ien.input_sha256=en.input_sha256
                JOIN calc_input icap ON icap.run_id=p.run_id AND icap.market_id=p.market_id
                  AND icap.delivery_day=p.delivery_day AND icap.component='capacity' AND icap.input_sha256=cap.input_sha256
                WHERE m.code='AFRR' AND r.status='success' AND r.model_version=%s
                  AND p.delivery_day=ANY(%s) GROUP BY p.delivery_day""", (MODEL_VERSION, days))
            published = dict(cur.fetchall())
        self.conn.commit()
        missing = [str(day) for day in days if not configs or published.get(day) != configs]
        return missing, sum(published.values()), configs

    def _restore_kept(self, entry):
        work = self._work_path(entry)
        # Never overwrite an earlier kept file with the same name.
        name = Path(entry["original_name"]).name
        target = self.kept / name
        if target.exists() or target.is_symlink():
            target = self.kept / (entry["entry_id"] + "_" + name)
        entry["kept_file"] = str(target)
        self._save(entry)
        os.replace(str(work), str(target))

    def finish(self, messages):
        for entry in self.entries:
            if entry["status"] not in ("imported", "waiting_data", "cleanup_pending"):
                continue
            try:
                missing, published, configs = self._publication_check(entry)
                entry.update(waiting_days=missing, published_results=published, configurations=configs)
                if missing:
                    entry["status"] = "waiting_data"
                    self._save(entry)
                    continue
                entry["status"] = "cleanup_pending" if entry["delete_requested"] else "keep_pending"
                self._save(entry)
                self.write_report("processing", messages)  # persistent report BEFORE destructive cleanup
                work = self._work_path(entry)
                if entry["delete_requested"]:
                    if work.exists():
                        if not _regular(work) or artifacts.sha256_bytes(work.read_bytes()) != entry["sha256"]:
                            raise ValueError("Claimed input changed before cleanup")
                    entry["raw_cleanup"] = artifacts.purge_local_artifact(
                        self.conn, entry["artifact_id"], entry.get("artifact_sha256", entry["sha256"]))
                    for raw in entry.get("additional_raw_artifacts", []):
                        raw["cleanup"] = artifacts.purge_local_artifact(self.conn, raw["artifact_id"], raw["sha256"])
                    if work.exists():
                        work.unlink()
                    entry["input_deleted"] = True
                else:
                    self._restore_kept(entry)
                    entry["input_deleted"] = False
                entry["status"] = "completed"
                entry.pop("error", None)
                self._save(entry)
            except Exception as exc:
                self.conn.rollback()
                # Keep cleanup_pending to retry only missing cleanup stages on the next invocation.
                if entry["status"] == "completed":
                    entry["status"] = "cleanup_pending" if entry["delete_requested"] else "keep_pending"
                entry["error"] = str(exc)
                self._save(entry)
        ok = not self.deferred and all(e["status"] == "completed" and not e.get("error") for e in self.entries)
        self.write_report("success" if ok else "partial", messages)
        return ok

    def write_report(self, status, messages):
        body = {"pipeline_run_id": self.run_id, "source": "local", "started_at": self.started,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "status": status,
                "inbox": str(self.inbox), "files": self.entries, "deferred": self.deferred,
                "messages": list(messages)}
        _atomic_json(self.json_path, body)
        lines = ["Battery Revenue Index - LOCAL INBOX", "Run: {} | Status: {}".format(self.run_id, status),
                 "JSON: " + str(self.json_path), ""]
        for entry in self.entries:
            lines += ["{}: {}".format(entry["original_name"], entry["status"]),
                      "  SHA-256: {}".format(entry.get("sha256", "-")),
                      "  Days: {} | Source rows: {} | New rows: {}".format(
                          ", ".join(entry.get("days", [])), entry.get("source_rows", 0), entry.get("rows_written", 0)),
                      "  artifact_id={} batch_id={} published={} deleted={}".format(
                          entry.get("artifact_id"), entry.get("batch_id"), entry.get("published_results", 0), entry.get("input_deleted", False))]
            if entry.get("waiting_days"):
                lines.append("  WAITING: " + ", ".join(entry["waiting_days"]))
            if entry.get("volume_metadata"):
                lines.append("  VOLUME UNITS: " + json.dumps(entry["volume_metadata"], ensure_ascii=False))
            if entry.get("price_documents"):
                lines.append("  ENTSO-E A84 documents: {} (references in JSON report)".format(len(entry["price_documents"])))
            if entry["status"] == "waiting_api":
                lines.append("  File retained for API retry: " + str(self._work_path(entry)))
            if entry.get("error"):
                lines.append("  ERROR: " + entry["error"])
            if entry.get("error_file"):
                lines.append("  File retained at: " + entry["error_file"])
        lines += ["DEFERRED {}: {}".format(e["name"], e["reason"]) for e in self.deferred]
        lines += ["", "Messages:"] + list(messages)
        tmp = self.txt_path.with_suffix(".txt.tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            fp.write("\n".join(lines) + "\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(str(tmp), str(self.txt_path))

    def fail(self, error):
        self.write_report("failed", [str(error)])
