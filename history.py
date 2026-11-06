#!/usr/bin/env python3
"""history.py — what is getting worse. Once an hour the renderer appends one line per build:
the age, file count, stale cron lines and open board items of every root. The map compares
today with a week ago and shows the direction, so neglect is visible as motion, not a number
you have to remember. 90 days are kept; the file is the only state.

Row: {"t": epoch seconds, "a": {"<root>": [age_days, files, stale_crons, trouble, failed_tasks]}}
"""
from __future__ import annotations

import json
import os

HOUR = 3600
# Planet kinds that sit still on purpose (the page's STILL_KINDS): nothing decays there, so
# being untouched is not a reason to call them worse. The row on disk does not carry the
# kind; trend() is told it.
STILL_KINDS = ("reference", "archive", "knowledge", "bus")
KEEP_DAYS = 90
MAX_ROWS = KEEP_DAYS * 24 + 48
FIELDS = ("age", "files", "stale", "trouble", "failed")


def load(path: str) -> list[dict]:
    rows = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("t"), (int, float)) and isinstance(row.get("a"), dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def _clean(areas: dict) -> dict:
    out = {}
    for name, vals in (areas or {}).items():
        if not isinstance(name, str) or not isinstance(vals, (list, tuple)) or len(vals) != len(FIELDS):
            continue
        row = []
        for v in vals:
            row.append(int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None)
        out[name] = row
    return out


def record(path: str, now: float, areas: dict, min_gap: float = HOUR) -> bool:
    """Append one row unless the last one is younger than min_gap. Trims to MAX_ROWS. Returns
    True when a row was written. Any OS trouble is swallowed: history is a nice-to-have."""
    rows = load(path)
    if rows and now - rows[-1]["t"] < min_gap:
        return False
    rows.append({"t": int(now), "a": _clean(areas)})
    rows = rows[-MAX_ROWS:]
    try:
        import tempfile
        folder = os.path.dirname(path) or "."
        if ".container-map" in folder:       # the map's own folder: kept out of the repo around it
            import config
            config.make_state_dir(folder)
        else:
            os.makedirs(folder, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".history-", suffix=".tmp", dir=os.path.dirname(path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:     # two renderers may write at once: each its own temp
            for row in rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        os.replace(temp, path)
    except OSError:
        return False
    return True


def trend(path: str, now: float, days: int = 7, kinds: dict | None = None) -> dict:
    """Per root: the change since `days` ago (or since the oldest row, if the record is shorter):
    {dstale, dtrouble, dage, dfailed, span_days, worse, better, why}. Nothing for a root that has
    fewer than two rows, or whose record is under a day old. `kinds` maps a root to its planet
    kind: a root of a STILL_KINDS kind is never 'untouched', only its real signals (stale
    cron lines, open items, failed tasks) make it worse."""
    rows = load(path)
    if len(rows) < 2:
        return {}
    latest = rows[-1]
    cutoff = now - days * 86400
    then = next((r for r in rows if r["t"] >= cutoff), None)
    if then is None or then is latest:
        then = rows[0]
    older = [r for r in rows if r["t"] <= cutoff]
    if older:
        then = older[-1]
    span = (latest["t"] - then["t"]) / 86400
    if span < 1:
        return {}
    out = {}
    for name, cur in latest["a"].items():
        old = then["a"].get(name)
        if not old or not cur:
            continue
        age0, _, stale0, trouble0, failed0 = (old + [None] * 5)[:5]
        age1, _, stale1, trouble1, failed1 = (cur + [None] * 5)[:5]
        d = lambda a, b: (b - a) if isinstance(a, int) and isinstance(b, int) else 0
        dstale, dtrouble, dage, dfailed = d(stale0, stale1), d(trouble0, trouble1), d(age0, age1), d(failed0, failed1)
        why = []
        if dstale > 0:
            why.append(f"+{dstale} stale cron line{'s' if dstale > 1 else ''}")
        if dtrouble > 0:
            why.append(f"+{dtrouble} open item{'s' if dtrouble > 1 else ''}")
        if dfailed > 0:
            why.append(f"+{dfailed} failed task{'s' if dfailed > 1 else ''}")
        still = (kinds or {}).get(name) in STILL_KINDS
        cooling = not still and isinstance(age1, int) and age1 >= 14 and dage >= max(3, span * 0.8)
        if cooling:
            why.append(f"untouched {age1}d")
        worse = bool(why)
        better = not worse and (dstale < 0 or dtrouble < 0 or dfailed < 0 or (isinstance(age1, int) and isinstance(age0, int) and age0 >= 14 and age1 <= 2))
        out[name] = {"dstale": dstale, "dtrouble": dtrouble, "dage": dage, "dfailed": dfailed,
                     "span_days": round(span, 1), "worse": worse, "better": better,
                     "why": ", ".join(why) if why else ("recovering" if better else "")}
    return out
