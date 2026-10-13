"""Quick ENTSO-E Transparency Platform API smoke test.

The script intentionally uses only the Python standard library so it can run
inside this project's existing Python 3.6 virtual environment.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


API_URL = "https://web-api.tp.entsoe.eu/api"
DEFAULT_SK_DOMAIN = "10YSK-SEPS-----K"
DEFAULT_DOCUMENT_TYPE = "A44"


def load_dotenv(path):
    """Load simple KEY=VALUE lines without requiring python-dotenv."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fp:
        for raw_line in fp:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def default_period():
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=1)
    return start.strftime("%Y%m%d%H%M"), today.strftime("%Y%m%d%H%M")


def build_query(args, token):
    params = {
        "securityToken": token,
        "documentType": args.document_type,
        "periodStart": args.period_start,
        "periodEnd": args.period_end,
    }
    if args.in_domain:
        params["in_Domain"] = args.in_domain
    if args.out_domain:
        params["out_Domain"] = args.out_domain
    if args.domain:
        params.setdefault("in_Domain", args.domain)
        params.setdefault("out_Domain", args.domain)
    return urlencode(params)


def main():
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir / ".env")

    period_start, period_end = default_period()

    parser = argparse.ArgumentParser(description="Check ENTSO-E API access.")
    parser.add_argument("--api-url", default=API_URL)
    parser.add_argument("--document-type", default=DEFAULT_DOCUMENT_TYPE)
    parser.add_argument("--domain", default=DEFAULT_SK_DOMAIN)
    parser.add_argument("--in-domain")
    parser.add_argument("--out-domain")
    parser.add_argument("--period-start", default=period_start)
    parser.add_argument("--period-end", default=period_end)
    parser.add_argument("--output", default="")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    token = os.environ.get("ENTSOE_API_KEY")
    if not token:
        print(
            "ENTSOE_API_KEY is not set. Create api_checks/entsoe/.env "
            "from .env.example or set the environment variable.",
            file=sys.stderr,
        )
        return 2

    url = args.api_url + "?" + build_query(args, token)
    safe_url = url.replace(token, "***")
    print("Request:", safe_url)

    try:
        with urlopen(url, timeout=args.timeout) as response:
            body = response.read()
            status = getattr(response, "status", response.getcode())
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        print("HTTP error: {0}".format(exc.code), file=sys.stderr)
        print(exc.read().decode("utf-8", errors="replace")[:2000], file=sys.stderr)
        return 1
    except URLError as exc:
        print("Network error: {0}".format(exc), file=sys.stderr)
        return 1

    print("Status:", status)
    print("Content-Type:", content_type)
    print("Bytes:", len(body))

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(body)
        print("Saved:", output_path)
    else:
        preview = body[:1200].decode("utf-8", errors="replace")
        print("Preview:")
        print(preview)

    return 0


if __name__ == "__main__":
    sys.exit(main())
