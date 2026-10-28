"""Stand-in for rclone in tests: 'remote:<path>' maps to $FAKE_RCLONE_ROOT/<path>.

Failure injection: FAKE_RCLONE_FAIL=moveto,copyto fails each listed command once
(FAKE_RCLONE_FAIL_MSG sets the error text; contains 'token' for an auth error).
"""

import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(os.environ["FAKE_RCLONE_ROOT"])


def local(path):
    return ROOT / path.split(":", 1)[1] if ":" in path else Path(path)


def maybe_fail(cmd):
    fail = [c for c in os.environ.get("FAKE_RCLONE_FAIL", "").split(",") if c]
    marker = ROOT / ".failed_{}".format(cmd)
    if cmd in fail and not marker.exists():
        marker.write_text("x")
        sys.stderr.write(os.environ.get("FAKE_RCLONE_FAIL_MSG", "dial tcp: connection refused") + "\n")
        sys.exit(1)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--") and not a.isdigit()]
    cmd = args[0]
    maybe_fail(cmd)
    if cmd == "lsjson":
        folder = local(args[1])
        items = []
        if folder.exists():
            for f in sorted(folder.iterdir()):
                if f.is_file():
                    st = f.stat()
                    items.append({"Path": f.name, "Name": f.name, "Size": st.st_size, "IsDir": False,
                                  "ModTime": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat()})
        sys.stdout.write(json.dumps(items))
    elif cmd == "copyto":
        shutil.copyfile(str(local(args[1])), str(local(args[2])))
    elif cmd == "moveto":
        dst = local(args[2])
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(local(args[1])), str(dst))
    elif cmd == "rcat":
        dst = local(args[1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(sys.stdin.buffer.read())
    else:
        sys.stderr.write("unsupported: {}\n".format(cmd))
        sys.exit(2)


if __name__ == "__main__":
    main()
