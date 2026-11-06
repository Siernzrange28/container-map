#!/usr/bin/env python3
"""decisions.py — parked decisions from ANY text file under the roots (slice 3).

A stranger has no digest. They do have TODO:, FIXME:, DECIDE: and QUESTION: lines in their notes
and code. Those are the parked decisions the board shows, aged by a date in the line or, failing
that, by the file's mtime (labelled as mtime). Bounded walk: text files only, skips vendored and
hidden trees, caps files per root and bytes per file. Nothing is inferred; every row carries
file:line and the verbatim snippet.
"""
import os
import re
import time
from datetime import datetime

TEXT_EXT = {".md", ".txt", ".rst", ".org", ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".go", ".rs",
            ".java", ".rb", ".toml", ".yaml", ".yml", ".json", ".c", ".h", ".cpp", ".cs", ".swift", ".kt"}
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build", "target", ".container-map",
             ".cache", "_attic", ".attic", "vendor", "site-packages"}
DEFAULT_WORDS = ["DECIDE:", "TODO:", "FIXME:", "QUESTION:"]
MAX_FILES_PER_ROOT = 400
MAX_BYTES_PER_FILE = 512 * 1024
MAX_DEPTH = 5
MAX_ROWS = 60
DATE = re.compile(r"2[0-9]{3}-[0-9]{2}-[0-9]{2}")


def _rx(words):
    words = [w for w in words if isinstance(w, str) and w.strip()]
    return re.compile("|".join(re.escape(w) for w in words), re.I) if words else None


def scan(roots, now, words=None, seen=None):
    """rows: {days, src, text, mtime_dated, path, line}. `seen` dedupes on the first 70 chars."""
    rx = _rx(words or DEFAULT_WORDS)
    if not rx:
        return []
    seen = seen if seen is not None else set()
    rows = []
    for root in roots:
        root = os.path.realpath(root)
        if not os.path.isdir(root):
            continue
        files = 0
        base_depth = root.rstrip("/").count("/")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            if dirpath.count("/") - base_depth >= MAX_DEPTH:
                dirnames[:] = []
            for fn in sorted(filenames):
                if files >= MAX_FILES_PER_ROOT or len(rows) >= MAX_ROWS:
                    break
                ext = os.path.splitext(fn)[1].lower()
                if ext not in TEXT_EXT and not fn.upper().startswith(("TODO", "DECISIONS", "NOTES")):
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    st = os.stat(path)
                    if st.st_size > MAX_BYTES_PER_FILE or st.st_size == 0:
                        continue
                    files += 1
                    with open(path, encoding="utf-8", errors="replace") as handle:
                        for n, line in enumerate(handle, 1):
                            m = rx.search(line)
                            if not m:
                                continue
                            s0, e0 = m.span()
                            snip = re.sub(r"\s+", " ", line[max(0, s0 - 40):min(len(line), e0 + 140)].strip())
                            key = snip[:70]
                            if key in seen or len(snip) < 8:
                                continue
                            seen.add(key)
                            dm = DATE.search(line)
                            if dm:
                                try:
                                    days = int((now - datetime.strptime(dm.group(), "%Y-%m-%d").timestamp()) // 86400)
                                    dated = "line"
                                except ValueError:
                                    days, dated = int((now - st.st_mtime) // 86400), "mtime"
                            else:
                                days, dated = int((now - st.st_mtime) // 86400), "mtime"
                            rel = os.path.relpath(path, root)
                            rows.append({"days": max(days, 0), "src": f"{os.path.basename(root)}/{rel}:{n}",
                                         "text": snip + ("" if dated == "line" else "  (age = file mtime)"),
                                         "path": path, "line": n, "dated": dated})
                            if len(rows) >= MAX_ROWS:
                                break
                except OSError:
                    continue
            if files >= MAX_FILES_PER_ROOT or len(rows) >= MAX_ROWS:
                break
    return rows


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(scan(sys.argv[1:] or ["."], time.time()), indent=1))
