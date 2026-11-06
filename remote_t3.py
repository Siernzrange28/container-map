"""Bridge-owned, bounded remote metadata refresh. The renderer only reads the cache."""
import json
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

import ships


DUMP_SCRIPT = '''import json, pathlib, sqlite3, sys
p = pathlib.Path(sys.argv[1]).expanduser().resolve()
db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=1)
try:
    rows = db.execute(%r).fetchall()
finally:
    db.close()
env = p.with_name("environment-id")
print(json.dumps({"rows": rows, "environment_id": env.read_text().strip() if env.is_file() else ""}))
''' % ships._T3_V2_SQL


def valid(remote):
    return (isinstance(remote, dict)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(remote.get("machine", "")))
            and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]{0,127}", str(remote.get("ssh", "")))
            and isinstance(remote.get("path"), str) and remote["path"]
            and "\x00" not in remote["path"])


def cache_path(root, machine):
    return Path(root, ".container-map", "t3-remote", machine + ".json")


def refresh(root, remote):
    """One host, 20 seconds including SSH. Failure replaces stale working metadata."""
    if not valid(remote):
        return
    target = cache_path(root, remote["machine"])
    result = {"machine": remote["machine"], "at": time.time(), "rows": []}
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", remote["ssh"],
             "python3 - " + shlex.quote(remote["path"])], input=DUMP_SCRIPT,
            text=True, capture_output=True, timeout=20, check=True)
        data = json.loads(proc.stdout)
        if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
            raise ValueError("remote metadata has no rows")
        # Check the row shape before accepting any of the response.
        for row in data["rows"]:
            ships._t3_v2_record(row)
        result.update(rows=data["rows"], environment_id=data.get("environment_id", ""))
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        result["error"] = ships.redact(str(exc))[:300]
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        os.chmod(temp, 0o600)
        json.dump(result, handle)
    os.replace(temp, target)


class Refresher:
    def __init__(self, cfg):
        self.root = cfg.get("work_root", "")
        self.remotes = [r for r in cfg.get("t3_remote", []) if valid(r)]
        self.stop = threading.Event()
        self.threads = []

    def start(self):
        for remote in self.remotes:
            thread = threading.Thread(target=self._loop, args=(remote,), daemon=True,
                                      name="t3-remote-" + remote["machine"])
            self.threads.append(thread)
            thread.start()

    def _loop(self, remote):
        while not self.stop.is_set():
            try:
                refresh(self.root, remote)
            except OSError:
                pass  # an unwritable cache is reported as missing by its reader
            if self.stop.wait(60):
                break

    def close(self):
        self.stop.set()
