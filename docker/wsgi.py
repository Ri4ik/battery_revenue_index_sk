"""WSGI entry point for running the Slovakia dashboard under gunicorn."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools.slovakia_revenue_dashboard import create_app  # noqa: E402

app = create_app(_REPO)
