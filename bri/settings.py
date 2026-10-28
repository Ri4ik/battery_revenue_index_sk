"""Runtime settings, all taken from environment variables."""

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

TZ = "Europe/Bratislava"
FIRST_DAY = os.environ.get("BRI_FIRST_DAY", "2025-03-01")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
RAW_ARCHIVE_DIR = Path(os.environ.get("BRI_RAW_ARCHIVE", str(REPO / "raw")))

# Days refreshed from the APIs on every run (late corrections), and how far back IMB is
# re-checked until the final evaluation is published.
RECENT_DAYS = int(os.environ.get("BRI_RECENT_DAYS", "3"))
IMB_FINAL_LOOKBACK_DAYS = int(os.environ.get("BRI_IMB_LOOKBACK_DAYS", "100"))

ENTSOE_API_KEY = os.environ.get("ENTSOE_API_KEY", "")

# OneDrive (rclone). Empty ONEDRIVE_REMOTE disables the OneDrive step.
ONEDRIVE_REMOTE = os.environ.get("ONEDRIVE_REMOTE", "")
RCLONE_BIN = os.environ.get("RCLONE_BIN", "rclone")
INBOX_DIR = "1_Nahrať"
PROCESSED_DIR = "2_Spracované"
ERROR_DIR = "3_Chyby"
STATUS_FILE = "STAV.txt"
# A file must be unchanged for this long before it is picked up (it may still be uploading).
INBOX_MIN_AGE_MINUTES = int(os.environ.get("BRI_INBOX_MIN_AGE_MINUTES", "10"))

HTTP_TIMEOUT = 60


def code_version():
    """Git commit of the running code (set at image build time, or read from git locally)."""
    env = os.environ.get("BRI_CODE_VERSION")
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
        )
        return out.stdout.decode().strip() or "unknown"
    except Exception:
        return "unknown"
