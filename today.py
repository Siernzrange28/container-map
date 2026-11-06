#!/usr/bin/env python3
"""today.py — the first screen. What the user has on their list (a plain markdown to-do file, or
commitments each judged from a proof file), what waits on them (a task list, and the open rows of a
decision ledger with their real age), the current week of a plan, what agents are doing, what got
done today (agent tasks, and the commits made today in the folders they name), what an overnight
run left, daily lanes, and what each folder is for.

Which of these appear depends on the `today` object of container.json: a source that is not named
is not drawn. The generic sources are `todo_md` (any markdown checklist) and `git_dirs` (git
folders whose commits of today count as done); agents the user starts on the map need no setting.
The other readers (task-id logs, a "## Week N" plan, a CSV of dated rows, a digest with a night-run
line, a JSONL decision ledger, a commitments file) each parse one fixed format, described where
they are defined below.

Every line comes from a file on disk at render time; a missing or unreadable source says so in place
(a `missing` entry), never a blank or an invented value. Read-only: this module writes nothing.
"""
import csv, hashlib, io, json, os, re, stat, subprocess
from datetime import date, datetime, timedelta

import words

TAIL = 1024 * 1024          # a digest is append-only and large: today's lines are at its end
# `fleet` (the task log): a task is a header `## T-YYYYMMDD-HHMM-name  title`; its status is an appended line
# `- T-... claim|done|blocked <who> HH:MM — note`; a `- T-... done-check PASSED` line marks a checked one.
HEAD = re.compile(r"^## (T-[\w.-]+)\s+(.*)$")
STATUS = re.compile(r"^- (T-[\w.-]+) (claim|done|blocked) (\S+) (\d{1,2}:\d{2})\s*(?:—|-{1,2})?\s*(.*)$")
CHECKED = re.compile(r"^- (T-[\w.-]+) done-check PASSED\b")
ID_DAY = re.compile(r"^T-(\d{4})(\d{2})(\d{2})-")
# `plan_md`: `# Title (YYYY-MM-DD → YYYY-MM-DD)` then `## Week N (MM-DD → MM-DD): title` sections of `- step` lines.
WEEK = re.compile(r"^## Week (\d+) \((\d{2})-(\d{2})\s*(?:→|->)\s*(\d{2})-(\d{2})\):\s*(.*)$")
RANGE = re.compile(r"\((\d{4}-\d{2}-\d{2})\s*(?:→|->|to)\s*(\d{4}-\d{2}-\d{2})\)")
DATED = re.compile(r"^\s*-?\s*\**(\d{4}-\d{2}-\d{2})\**\s*(?:[—|:-]+)?\s*(.*)$")
DIGEST_STAMP = re.compile(r"^-\s*(?:\*\*[^*]*\*\*:?|\d{4}-\d{2}-\d{2}(?: \d{1,2}:\d{2})?)\s*")


def _read(path, tail=0):
    with open(path, "rb") as handle:
        if tail:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - tail))
        return handle.read().decode("utf-8", errors="replace")


def _day(stamp):
    m = ID_DAY.match(stamp)
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
    except ValueError:
        return None


def _title(text):
    """A task header minus its trailing parenthetical (who asked, when): the short name of the task, as a person
    says it (no "Goal:", no task ids, no paths: words.short_title), at most 60 characters."""
    short = re.sub(r"\s*\([^()]*\)\s*$", "", text).strip(" —-")
    short = (short or text.strip(" —-()")).strip()
    return words.short_title(short, 60) or short[:60]


def _clip(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


ASK_WORDS = r"waiting on|blocked on|needs (?:his|the owner)|his call|his go|his ok|for the owner"


def ask_rx(owner=""):
    """The phrases that say a note waits on the owner; the owner's first name (config "owner") counts too."""
    name = re.escape(str(owner or "").strip())
    return re.compile(r"\b(%s%s)\b" % (ASK_WORDS, "|needs %s|for %s" % (name, name) if name else ""), re.I)


def _ask(note, n=260, owner=""):
    """A long blocked note keeps its ask: the last sentence that says what it waits on."""
    note = " ".join(str(note).split())
    if len(note) > 140:
        parts = re.split(r"(?<=[.;])\s+|\s+·\s+|\s+—\s+", note)
        ask_re = ask_rx(owner)
        ask = next((p for p in reversed(parts) if ask_re.search(p)), None)
        if ask:
            note = "… " + ask
    return words.plain_text(note, n)          # task ids, links and paths stay out of what the person reads


def fleet(path, today, week_days=7, owner="", blocked=None):
    """needs: tasks whose newest status line is `blocked`, created in the last week.
    done: `done` lines written today. FLEET lines carry a time but no date, and the file is
    append-only, so a line counts as today's when it sits at or after the first line that names a
    task id created today (or names such a task itself). `blocked`, when given, collects every
    task whose newest status line is `blocked`, whatever its age or id (the open loops the sky shows
    aging; needs keeps the week), newest id first."""
    lines = _read(path).splitlines()
    titles, last, checked, first_today = {}, {}, set(), None
    stamp = today.strftime("%Y%m%d")
    for i, line in enumerate(lines):
        m = HEAD.match(line)
        if m:
            titles[m.group(1)] = _title(m.group(2))
        m = HEAD.match(line) or STATUS.match(line)
        if m and first_today is None and m.group(1).startswith("T-" + stamp):
            first_today = i
        m = CHECKED.match(line)
        if m:
            checked.add(m.group(1))
            continue
        m = STATUS.match(line)
        if m:
            last[m.group(1)] = (i, m.group(2), m.group(4), m.group(5))
    needs, done, every = [], [], []
    for tid, (i, kind, at, note) in last.items():
        if kind != "blocked":
            continue
        row = {"id": tid, "title": titles.get(tid, tid), "note": _ask(note, owner=owner), "at": at}
        every.append(row)
        day = _day(tid)
        if day and (today - day).days <= week_days:
            needs.append(dict(row, i=i))
    for i, line in enumerate(lines):
        m = STATUS.match(line)
        if not m or m.group(2) != "done":
            continue
        if (first_today is not None and i >= first_today) or _day(m.group(1)) == today:
            done.append({"id": m.group(1), "title": titles.get(m.group(1), m.group(1)),
                         "at": m.group(4), "checked": m.group(1) in checked, "i": i})
    seen, uniq = set(), []
    for d in sorted(done, key=lambda d: (d["at"].zfill(5), d["i"]), reverse=True):
        if d["id"] not in seen:
            seen.add(d["id"]); uniq.append(d)
    for row in needs + uniq:
        row.pop("i", None)
    if blocked is not None:
        blocked.extend(sorted(every, key=lambda n: n["id"], reverse=True))
    return sorted(needs, key=lambda n: n["id"], reverse=True), uniq


def _ledger_latest(text):
    """{id: (first line it appeared on, its newest row)} of an append-only JSONL ledger: the newest
    row per id wins. A line that is not a JSON object (or is nested too deep to read), or has no text
    or number id, is skipped."""
    rows = {}
    for i, line in enumerate(text.splitlines()):
        try:
            row = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(row, dict):
            continue
        ident = row.get("id")
        if isinstance(ident, bool) or not isinstance(ident, (str, int)) or not str(ident).strip():
            continue
        ident = str(ident).strip()
        rows[ident] = (rows[ident][0] if ident in rows else i, row)
    return rows


def _iso_day(value):
    """The day in 'YYYY-MM-DD' (or an ISO time that starts with one), else None."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def ledger_rows(latest, today):
    """The open rows, oldest first, each aged from the day it was opened (a stored age goes stale the
    day after it is written). `estimate` = the row says its opened day is a guess. A row whose opened
    day cannot be read keeps its place at the end with no age, never a made-up one."""
    out = []
    for ident, (first, row) in latest.items():
        if row.get("status") != "open":
            continue
        day = _iso_day(row.get("opened"))
        title, lane, blocks = row.get("title"), row.get("lane"), row.get("blocks")
        out.append((day is None, day or date.min, first, {
            "id": ident,
            "lane": _clip(lane, 40) if isinstance(lane, str) else "",
            "title": _clip(title if isinstance(title, str) and title.strip() else ident, 120),
            "opened": day.isoformat() if day else None,
            "age_days": max(0, (today - day).days) if day else None,
            "blocks": _clip(blocks, 160) if isinstance(blocks, str) and blocks.strip() else None,
            "estimate": row.get("opened_is_estimate") is True}))
    return [row for *_, row in sorted(out, key=lambda r: r[:3])]


def ledger_open(path):
    """How many decision rows are open (append-only JSONL: the newest row per id wins)."""
    return sum(1 for _, r in _ledger_latest(_read(path)).values() if r.get("status") == "open")


def plan(path, today, log_path=""):
    lines = _read(path).splitlines()
    out = {"path": path, "title": "", "goal": "", "start": "", "day": None, "days": None, "phase": "",
           "week": None, "last_session": None}
    first = next((l for l in lines if l.startswith("# ")), "")
    out["title"] = RANGE.sub("", first[2:]).strip(" —-")
    m = RANGE.search(first)
    start = end = None
    if m:
        try:
            start, end = date.fromisoformat(m.group(1)), date.fromisoformat(m.group(2))
        except ValueError:
            start = end = None
    if start and end:
        out["begins"] = start.isoformat()
        out["days"] = max(1, (end - start).days)
        n = (today - start).days + 1
        out["day"] = n
        out["phase"] = ("starts " + start.isoformat() if n < 1 else "ended " + end.isoformat() if today > end
                        else "day %d of %d" % (min(n, out["days"]), out["days"]))
    for i, line in enumerate(lines):
        if line.startswith("**Goal:**"):
            goal = [line[len("**Goal:**"):]]
            for more in lines[i + 1:]:
                if not more.strip() or more.startswith(("#", "- ")):
                    break
                goal.append(more)
            out["goal"] = _clip(" ".join(goal).replace("**", ""), 300)
        if "Start a session:" in line:
            out["start"] = _clip(line.split("Start a session:", 1)[1].strip(), 200)
    weeks = []
    for i, line in enumerate(lines):
        m = WEEK.match(line)
        if not m or not start:
            continue
        try:
            a = date(start.year + (int(m.group(2)) < start.month), int(m.group(2)), int(m.group(3)))
            b = date(start.year + (int(m.group(4)) < start.month), int(m.group(4)), int(m.group(5)))
        except ValueError:
            continue
        steps, done_when = [], ""
        for more in lines[i + 1:]:
            if more.startswith("#"):
                break
            if more.startswith("- "):
                text = more[2:].replace("**", "").strip()
                if text.lower().startswith("done when:"):
                    done_when = _clip(text.split(":", 1)[1].strip(), 240)
                else:
                    steps.append(_clip(text, 240))
        weeks.append({"n": int(m.group(1)), "from": a.isoformat(), "to": b.isoformat(), "title": m.group(6).strip(),
                      "steps": steps[:6], "done_when": done_when})
    if weeks:
        cur = next((w for w in weeks if w["from"] <= today.isoformat() <= w["to"]), None)
        out["week"] = cur or (weeks[0] if today.isoformat() < weeks[0]["from"] else weeks[-1])
        out["weeks"] = len(weeks)
    if log_path:
        try:
            dated = [m for m in (DATED.match(l) for l in _read(log_path).splitlines()) if m]
            out["last_session"] = ({"date": dated[-1].group(1), "text": _clip(dated[-1].group(2), 200)}
                                   if dated else None)
        except OSError:
            out["last_session"] = {"date": "", "text": "learning log unreadable: " + log_path}
    return out


def _today_lines(text, today):
    iso = today.isoformat()
    lines = [l for l in text.splitlines() if l.startswith("-") and iso in l[:48]]
    return [DIGEST_STAMP.sub("", l).strip() for l in reversed(lines)]


def digest_today(path, today):
    """Today's DIGEST lines, newest first, without their leading date stamp."""
    return _today_lines(_read(path, TAIL), today)


# ---- what the night run left: its report file, else its line in the digest, else a note saying why.
# "While you slept" means last night: a report for today or yesterday. An older one is named in the
# note, never shown as if it were last night's. Nothing here is acted on: keep and drop stay with the
# owner's own tool.
NIGHT_DAYS = 1
NIGHT_ITEMS = 12
NIGHT_KEYS = ("ran", "ready", "failed", "refused")
NIGHT_COUNT = {k: re.compile(r"(?<![\w.-])(\d{1,4})\s+%s\b" % k, re.I) for k in NIGHT_KEYS}
NIGHT_ITEM = re.compile(r"^-\s+(ready|failed|refused)\s*:\s*(\S.*)$", re.I)
NIGHT_DAY = re.compile(r"(?<![\d-])(\d{4}-\d{2}-\d{2}|\d{1,2}-\d{2})(?![\d-])")
STAMP_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
NIGHT_MAX_BYTES = 1024 * 1024
NIGHT_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})\.json\Z")


def _night_day(text, stamped):
    """The night a report line names ('10-10' or '2026-10-10' after the marker), else the line's own day.
    A month-day takes the stamp's year, or the year before when that would put it past the stamp."""
    m = NIGHT_DAY.search(text)
    if m:
        s = m.group(1)
        try:
            if len(s) == 10:
                return date.fromisoformat(s)
            month, day = (int(x) for x in s.split("-"))
            got = date(stamped.year, month, day)
            return got if got <= stamped + timedelta(days=1) else date(stamped.year - 1, month, day)
        except (ValueError, OverflowError):
            pass
    return stamped


def night_from_digest(text, marker, today):
    """(night or None, note). The report is the newest digest line that carries `marker` (plain words,
    any case), a date stamp and at least one count (`N ran`, `N ready`, `N failed`, `N refused`); the
    `- ready: …`, `- failed: …`, `- refused: …` lines right under it are its items."""
    lines = text.splitlines()
    words = re.compile(re.escape(" ".join(str(marker).split())), re.I)
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i]
        m = words.search(line) if line.startswith("-") else None
        stamp = m and STAMP_DAY.search(line[:48])
        if not stamp or stamp.start() > m.start():
            continue
        stamped = _iso_day(stamp.group(0))
        rest = line[m.end():]
        counts = {k: (int(c.group(1)) if c else None) for k, c in ((k, rx.search(rest)) for k, rx in NIGHT_COUNT.items())}
        if not stamped or all(v is None for v in counts.values()):
            continue
        day = _night_day(rest, stamped)
        if day > today:                                  # a night that has not happened: a bad line, look further back
            continue
        if day < today - timedelta(days=NIGHT_DAYS):
            return None, "no night-run report for last night: the newest in the digest is for %s" % day.isoformat()
        items = []
        for more in lines[i + 1:i + 1 + NIGHT_ITEMS]:
            it = NIGHT_ITEM.match(more.strip())
            if not it:
                break
            items.append({"id": None, "title": _clip(it.group(2), 120), "repo": None,
                          "verdict": it.group(1).lower(), "minutes": None})
        night = {"date": day.isoformat()}
        night.update(counts)
        night.update({"items": items, "source": "digest", "line": _clip(DIGEST_STAMP.sub("", line).strip(), 200)})
        return night, None
    return None, "no night-run line in the digest's recent lines (one with '%s', a date and a count)" % _clip(marker, 40)


def _text(value, n):
    return _clip(value, n) if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip() else None


def _night_item(item, verdict):
    if not isinstance(item, dict):
        return None
    title = next((item[k] for k in ("title", "goal", "task") if isinstance(item.get(k), str) and item[k].strip()), "")
    ident = item.get("id")
    ident = str(ident).strip() if isinstance(ident, (str, int)) and not isinstance(ident, bool) and str(ident).strip() else None
    if not (title or ident):
        return None
    minutes = item.get("minutes")
    ok = isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and 0 <= minutes < 1e6
    said = _text(item.get("verdict"), 24)
    return {"id": ident, "title": _clip(title or ident, 120), "repo": _text(item.get("repo"), 120),
            "verdict": (said or verdict or "").lower() or None, "minutes": round(minutes, 1) if ok else None}


def night_from_json(raw, fallback_day=None):
    """The night block from a report object. Counts are read only where the file states them (a number,
    or a list of items whose length is the count); items come from `items` and from those lists, once each
    (by id, else title). Raises ValueError when the file holds no JSON object."""
    if not isinstance(raw, dict):
        raise ValueError("it holds no JSON object")
    items, seen = [], set()
    for pool, verdict in [(raw.get("items"), "")] + [(raw.get(k), k) for k in NIGHT_KEYS]:
        for one in (pool if isinstance(pool, list) else [])[:200]:
            item = _night_item(one, verdict)
            key = item and (item["id"] or item["title"])
            if item and key not in seen:
                seen.add(key)
                items.append(item)
    out = {"date": None}
    for k in NIGHT_KEYS:
        v = raw.get(k)
        out[k] = (v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else
                  len(v) if isinstance(v, list) else None)
    day = _iso_day(raw.get("date")) or fallback_day
    out["date"] = day.isoformat() if day else None
    line = next((raw[k] for k in ("line", "summary") if isinstance(raw.get(k), str) and raw[k].strip()), None)
    out.update({"items": items[:NIGHT_ITEMS], "source": "json", "line": _clip(line, 200) if line else None})
    return out


def _night_file(path):
    """(the report file, the night its name says or None): `path`, or the newest <YYYY-MM-DD>.json in it
    when it is a folder (one file per night; any other name is not a report). ("", None) for a folder with none."""
    if os.path.isdir(path):
        names = sorted(n for n in os.listdir(path) if NIGHT_NAME.match(n) and _iso_day(n[:10]))
        if not names:
            return "", None
        path = os.path.join(path, names[-1])
    m = NIGHT_NAME.match(os.path.basename(path))
    return path, _iso_day(m.group(1)) if m else None


def night(today_cfg, today, digest_text, missing, has_digest=True):
    """(night or None, note or None) for "While you slept". The report file (today.night_json) when it
    holds last night's report; else the digest line (today.night_marker) from the digest text already
    read for the daily lanes; else None and a note that says why. With neither key set: (None, None)."""
    path = today_cfg.get("night_json") or ""
    marker = " ".join(str(today_cfg.get("night_marker") or "").split())
    if not path and not marker:
        return None, None
    notes = []
    if path:
        try:
            found, named = _night_file(path)
            st = os.stat(found) if found else None
            if not found:
                notes.append("no night report in " + path)
            elif not stat.S_ISREG(st.st_mode):                  # a pipe or a device would hang the read
                raise ValueError("not a regular file")
            elif st.st_size > NIGHT_MAX_BYTES:
                raise ValueError("larger than %d bytes" % NIGHT_MAX_BYTES)
            else:
                got = night_from_json(json.loads(_read(found)), named)
                day, name = _iso_day(got["date"]), os.path.basename(found)
                if not got["items"] and all(got[k] is None for k in NIGHT_KEYS):
                    notes.append("the night report %s states no count and no item" % name)
                elif not day:
                    notes.append("the night report %s states no date (a date key, or a YYYY-MM-DD.json name)" % name)
                elif day > today:
                    notes.append("the night report %s is dated %s, after today" % (name, day.isoformat()))
                elif day < today - timedelta(days=NIGHT_DAYS):
                    notes.append("the night report %s is from %s, not last night" % (name, day.isoformat()))
                else:
                    return got, None
        except (OSError, ValueError, RecursionError) as exc:    # RecursionError: JSON nested too deep
            missing.append("night report unreadable: %s (%s)" % (path, getattr(exc, "strerror", None) or exc))
            notes.append("the night report could not be read")
    if marker:
        if digest_text is None:
            notes.append("the digest could not be read" if has_digest else "no digest is set to find the night-run line in")
        else:
            got, why = night_from_digest(digest_text, marker, today)
            if got:
                return got, None
            notes.append(why)
    return None, "; ".join(notes)


def jobs(cfg, today, digest_lines, missing):
    rows = []
    applied = cfg.get("applied_csv")
    if applied:
        try:
            with open(applied, encoding="utf-8", errors="replace", newline="") as handle:
                rows = [r for r in csv.reader(handle) if r and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0].strip())]
        except OSError:
            missing.append("applications list unreadable: " + applied)
    lines = []
    if rows:
        last = max(enumerate(rows), key=lambda p: (p[1][0], p[0]))[1]
        week = sum(1 for r in rows if r[0] >= (today - timedelta(days=7)).isoformat())
        lines.append("last application sent: %s · %s (%s) · %d in the last 7 days"
                     % (last[1] if len(last) > 1 else "?", last[2] if len(last) > 2 else "?", last[0][5:], week))
    elif applied:
        lines.append("no applications on record")
    drafts = cfg.get("drafts_dir")
    if drafts:
        if os.path.isdir(drafts):
            fresh = [f for f in os.listdir(drafts) if not f.startswith(".") and
                     os.path.getmtime(os.path.join(drafts, f)) >= datetime.now().timestamp() - 2 * 86400]
            lines.append("%d application drafts ready to send (made in the last 2 days)" % len(fresh)
                         if fresh else "no application drafts waiting")
        else:
            lines.append("no application drafts: nothing is making them yet")
    rx = cfg.get("jobs_rx")
    if rx:
        hit = next((l for l in digest_lines if re.search(rx, l)), None)
        if hit:
            lines.append(_clip(hit, 160))
    return lines


# ---- the generic sources: a markdown checklist, and the commits of today in some git folders.
CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s+(\S.*)$")
TODO_OPEN, TODO_DONE = 12, 3


def todo(path, missing):
    """The `Do today` section from any markdown checklist: `- [ ] item` is open, `- [x] item` is done. The first
    TODO_OPEN open items and the last TODO_DONE done ones are listed; a file with no checkbox says so."""
    out = {"path": path, "items": [], "summary": "", "kind": "todo"}
    try:
        text = _read(path)
    except OSError:
        missing.append("to-do file unreadable: " + path)
        return out
    open_, done = [], []
    for line in text.splitlines():
        m = CHECKBOX.match(line)
        if m:
            (done if m.group(1) in "xX" else open_).append(_clip(m.group(2).replace("**", ""), 160))
    items = [("due", t) for t in open_[:TODO_OPEN]] + [("ok", t) for t in done[-TODO_DONE:]]
    for state, what in items:
        out["items"].append({"id": "todo-" + hashlib.sha1(what.encode()).hexdigest()[:8], "lane": "to-do", "what": what,
                             "how": "", "proof": "", "source": "", "proof_label": "", "checkin": False, "today": False,
                             "last": "", "state": state, "status": "done" if state == "ok" else "not done yet",
                             "history": []})
    out["summary"] = "%d open · %d done" % (len(open_), len(done)) if (open_ or done) else ""
    if not (open_ or done):
        missing.append("no checkboxes (- [ ] or - [x]) in " + path)
    return out


def git_done(dirs, today, missing, limit=20):
    """Commits made today (local time) in each folder of `git_dirs`, newest first, as `done` rows: the commit's
    subject, its time, and the folder's name. A folder that is not a git repository is named in `missing`."""
    since = datetime.combine(today, datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for d in dirs or ():
        try:
            run = subprocess.run(["git", "-C", d, "log", "--since", since, "--no-merges", "-n", str(limit),
                                  "--pretty=format:%h%x1f%ct%x1f%s"], capture_output=True, text=True, timeout=8)
        except (OSError, subprocess.SubprocessError):
            missing.append("git activity unreadable: " + d)
            continue
        if run.returncode != 0:
            missing.append("git activity unreadable: " + d + " (not a git folder?)")
            continue
        for line in run.stdout.splitlines():
            sha, _, rest = line.partition("\x1f")
            stamp, _, subject = rest.partition("\x1f")
            if stamp.isdigit():
                when = datetime.fromtimestamp(int(stamp))
                rows.append({"id": sha, "title": _clip(subject, 120), "at": when.strftime("%H:%M"), "checked": False,
                             "repo": os.path.basename(d.rstrip("/")), "_t": int(stamp)})
    rows.sort(key=lambda r: r["_t"], reverse=True)
    for r in rows:
        r.pop("_t")
    return rows[:limit]


def _failed_jobs(chars):
    """The cron lines that never ran (verdict NEVER), as "true ×5" groups: BREAKS counts them with the digest's
    failure lines, so a job that stopped working is a break even when nothing logs it."""
    names = [words.job_name(c) for c in chars or () if isinstance(c, dict) and c.get("kind") == "cron"
             and c.get("verdict") == "NEVER"]
    return len(names), [words.grouped(n, k) for n, k in words.group_causes(names)]


def daily(cfg, today, digest_lines, missing, has_digest=True, chars=None):
    out = []
    j = jobs(cfg, today, digest_lines, missing)
    if j:
        out.append({"name": "jobs", "lines": j})
    if cfg.get("alert_rx"):
        n = sum(1 for l in digest_lines if re.search(cfg["alert_rx"], l))
        out.append({"name": "trade alerts", "lines": ["%d trade alerts logged today" % n]})
    jobs_failed, job_groups = _failed_jobs(chars)
    head = (["%d job%s never ran: %s" % (jobs_failed, "" if jobs_failed == 1 else "s", ", ".join(job_groups[:4]))]
            if jobs_failed else [])
    if not has_digest:                      # no digest configured: only the jobs can say a break
        if head:
            out.append({"name": "breaks", "lines": head})
        return out
    rx = cfg.get("fail_rx") or r"\bFAILED\b"
    fails = [l for l in digest_lines if re.search(rx, l)]
    out.append({"name": "breaks", "lines": head + ((["%d failure line%s logged today; newest:"
                                                      % (len(fails), "" if len(fails) == 1 else "s")] +
                                                     [_clip(words.plain_text(f), 170) for f in fails[:2]]) if fails else
                                                    ["nothing logged as failed today"])})
    return out


# ---- what the user said they'd do: one line per commitment in a file they own, each judged from its proof.
# Nothing is ever marked kept by the page itself: a dated log line, a dated csv row, or the user's own
# check-in click (bridge POST /checkin, appended to checkins_jsonl) is the only evidence.
PER_WEEK = re.compile(r"^(\d{1,4})\s*(?:/|x|×)\s*week$", re.I)
EVERY = re.compile(r"^every (\d{1,4}) weeks?$", re.I)
LOG_DATE = re.compile(r"^\s*-?\s*\**(\d{4}-\d{2}-\d{2})(?![\w-])")
FROM = re.compile(r"\s+(?:from|since)\s+(\d{4}-\d{2}-\d{2})$", re.I)
HOW_HELP = "use daily, weekdays, N/week, every N weeks from YYYY-MM-DD, or once since YYYY-MM-DD"


def commitment_id(lane, what):
    """Readable start + a hash of the whole wording: any edit to lane or what starts a new record."""
    tag = hashlib.sha1("|".join(" ".join(part.casefold().split()) for part in (lane, what)).encode()).hexdigest()[:6]
    words = re.findall(r"[a-z0-9]+", (lane + " " + what).lower())
    return ("-".join(words[:6])[:50] or "commitment") + "-" + tag


def parse_commitments(text):
    """`- lane | what | how often | proof | source` list lines; a struck-through (~~) line is retired.
    The id is lane + the first words of `what`, so check-ins follow the line, not its position. A
    later line with the same id gets no id and an `error` (it would share the first one's check-ins)."""
    items, seen = [], set()
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("- ") or "|" not in s or s[2:].lstrip().startswith("~~"):
            continue
        cells = [c.strip() for c in s[2:].split("|")]
        if len(cells) < 4 or not all(cells[:4]):
            continue
        ident = commitment_id(cells[0], cells[1])
        item = {"id": ident, "lane": cells[0], "what": cells[1], "how": cells[2], "proof": cells[3],
                "source": cells[4] if len(cells) > 4 else ""}
        if ident in seen:
            item["id"], item["error"] = "", "same lane and first words as an earlier line: reword one of them"
        seen.add(ident)
        items.append(item)
    return items


def is_checkin(proof):
    return proof.strip().lower() in ("check-in", "checkin")


def read_checkins(path):
    """{id: set of days} from the append-only check-in file; an undo row takes that day back."""
    days = {}
    for line in _read(path).splitlines():
        try:
            row = json.loads(line)
            day = date.fromisoformat(str(row.get("day")))
        except (ValueError, AttributeError):
            continue
        if isinstance(row.get("id"), str):
            got = days.setdefault(row["id"], set())
            (got.discard if row.get("undo") else got.add)(day)
    return days


def proof_days(proof, base, checkins, ident):
    """(days, label, error): one date per proof row — two applications on one day count as two."""
    if is_checkin(proof):
        return sorted(checkins.get(ident, ())), "your check-in on this page", ""
    kind, _, arg = proof.strip().partition(" ")
    kind, arg = kind.lower(), os.path.expanduser(arg.strip())
    if kind not in ("log", "csv"):
        return [], proof, "proof '%s' not understood: use check-in, log <file> or csv <file>" % proof
    if not arg:
        return [], proof, "proof '%s' names no file" % proof
    path = arg if os.path.isabs(arg) else os.path.join(base, arg)
    try:
        text = _read(path)
    except OSError:
        return [], path, "proof file unreadable: " + path
    if kind == "log":
        found = [m.group(1) for m in (LOG_DATE.match(l) for l in text.splitlines()) if m]
    else:
        try:
            found = [r[0].strip() for r in csv.reader(io.StringIO(text, newline=""), strict=True)
                     if r and re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[0].strip())]
        except csv.Error as exc:
            return [], path, "proof file is not readable as csv: %s (%s)" % (path, exc)
    days = []
    for d in found:
        try:
            days.append(date.fromisoformat(d))
        except ValueError:
            pass
    return days, ("a dated line in " if kind == "log" else "a dated row in ") + os.path.basename(path), ""


def _md(d):
    return d.strftime("%a %m-%d")


def judge(how, days, today):
    """state ok (kept) / due / bad (slipping) / idle (not started) / off (not due today), a status, and a short
    history (kept True/False, None = not counted).
    Days before the rule's `from`/`since` date are never counted as misses."""
    how = how.strip()
    m = FROM.search(how)
    start = None
    if m:
        try:
            start = date.fromisoformat(m.group(1))
        except ValueError:
            return {"state": "bad", "status": "date not understood in '%s'" % how, "history": [], "invalid": True}
        how = how[:m.start()].strip()
    rule = how.lower()
    days = [d for d in days if d <= today and (not start or d >= start)]   # no future or pre-start proof
    wk = PER_WEEK.match(rule)
    if start and today < start and ((wk and 1 <= int(wk.group(1)) <= 99) or rule in ("daily", "every day", "weekdays", "once")):
        n = (start - today).days
        return {"state": "idle", "status": "starts %s (in %d day%s)" % (_md(start), n, "" if n == 1 else "s"), "history": []}
    out = {"state": "due", "status": "", "history": []}
    m = PER_WEEK.match(rule)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= 99:
            return {"state": "bad", "status": "'%s': the number must be 1 to 99" % how, "history": [], "invalid": True}
        mon = today - timedelta(days=today.weekday())
        for k in range(4, 0, -1):
            a = mon - timedelta(weeks=k)
            if start and a < start:                     # a week that began before counting did is not judged
                continue
            got = sum(1 for d in days if a <= d < a + timedelta(days=7))
            out["history"].append({"label": "week of %s: %d of %d" % (a.strftime("%m-%d"), got, n), "kept": got >= n})
        got = sum(1 for d in days if mon <= d <= today)
        out["history"].append({"label": "this week so far: %d of %d" % (got, n), "kept": True if got >= n else None})
        if got >= n:
            out["state"], out["status"] = "ok", "kept this week: %d of %d" % (got, n)
            return out
        out["status"] = "%d of %d this week so far · %d more by Sunday" % (got, n, n - got)
        last = out["history"][-2] if len(out["history"]) > 1 else None
        if last and last["kept"] is False:              # several proofs can land on one day: mid-week is
            out["state"] = "bad"                        # never "behind"; a missed last week is slipping
            out["status"] += " · missed last week (%s)" % last["label"].split(": ", 1)[1]
        return out
    if rule in ("daily", "every day", "weekdays"):
        for k in range(6, -1, -1):
            d = today - timedelta(days=k)
            due = not (rule == "weekdays" and d.weekday() >= 5) and not (start and d < start)
            kept = True if d in days else (False if due and d < today else None)
            out["history"].append({"label": _md(d) + (": done" if d in days else ": missed" if kept is False
                                                      else ": not due" if not due else ": not yet"), "kept": kept})
        missed = sum(1 for h in out["history"] if h["kept"] is False)
        if today in days:
            out["state"], out["status"] = "ok", "done today"
        elif rule == "weekdays" and today.weekday() >= 5:
            out["state"], out["status"] = "off", "not due today (weekend)"
        else:
            out["status"] = "not done yet today"
        if missed:
            out["status"] += " · missed %d of the last 6 days" % missed
            if missed >= 3 and out["state"] == "due":
                out["state"] = "bad"
        return out
    m = EVERY.match(rule)
    if m:
        if not start:
            return {"state": "bad", "status": "'%s' needs a start date: every N weeks from YYYY-MM-DD" % how, "history": [], "invalid": True}
        if not 1 <= int(m.group(1)) <= 52:
            return {"state": "bad", "status": "'%s': every 1 to 52 weeks" % how, "history": [], "invalid": True}
        step = timedelta(weeks=int(m.group(1)))
        if today < start:
            n = (start - today).days
            return {"state": "idle", "status": "first one %s (in %d day%s)" % (_md(start), n, "" if n == 1 else "s"), "history": []}
        cur = start + step * ((today - start) // step)
        nxt = cur + step
        for i in range(3, -1, -1):
            d = cur - step * i
            if d >= start:
                kept = any(d <= x < d + step for x in days)
                out["history"].append({"label": _md(d) + (": done" if kept else ": not yet" if d == cur else ": missed"),
                                       "kept": True if kept else (None if d == cur else False)})
        wait = (nxt - today).days
        if any(cur <= x < nxt for x in days):
            out["state"], out["status"] = "ok", "done for %s · next %s (in %d days)" % (_md(cur), _md(nxt), wait)
        elif today == cur:
            out["status"] = "today: not checked in yet"
        else:
            out["state"] = "bad"
            out["status"] = "not done for %s (%d days ago) · next %s" % (_md(cur), (today - cur).days, _md(nxt))
        return out
    if rule == "once":
        done = sorted(d for d in days if not start or d >= start)
        if done:
            return {"state": "ok", "status": "done %s" % _md(done[-1]), "history": []}
        if start:
            return {"state": "bad", "status": "open %d days (since %s)" % ((today - start).days, start.isoformat()), "history": []}
        return {"state": "due", "status": "not done yet", "history": []}
    return {"state": "bad", "status": "how often '%s' not understood: %s" % (how, HOW_HELP), "history": [], "invalid": True}


def word(cfg, today, missing):
    """The commitments section, or None when no commitments file is configured."""
    path = cfg.get("commitments_md")
    if not path:
        return None
    out = {"path": path, "items": [], "summary": ""}
    try:
        text = _read(path)
    except OSError:
        missing.append("commitments file unreadable: " + path)
        return out
    checkins, no_checkins = {}, ""
    if not cfg.get("checkins_jsonl"):
        no_checkins = "check-ins are not set up: add today.checkins_jsonl to container.json"
    else:
        try:
            checkins = read_checkins(cfg["checkins_jsonl"])
        except FileNotFoundError:
            pass                                        # no click yet: an empty record, not an error
        except OSError as exc:
            no_checkins = "check-ins unreadable (%s): nothing judged until it can be read" % (exc.strerror or exc)
            missing.append("check-ins unreadable: " + cfg["checkins_jsonl"])
    for c in parse_commitments(text):
        days, label, err = proof_days(c["proof"], os.path.dirname(path), checkins, c["id"])
        err = c.get("error") or err or (no_checkins if is_checkin(c["proof"]) else "")
        m = PER_WEEK.match(FROM.sub("", c["how"].strip()).lower())
        if not err and m and is_checkin(c["proof"]) and int(m.group(1)) > 7:
            err = "a check-in counts once a day, so at most 7/week: lower the number or prove it with a file"
        last = max((d for d in days if d <= today), default=None)
        row = dict(c, proof_label=label, checkin=is_checkin(c["proof"]) and not no_checkins, today=today in days,
                   last=last.isoformat() if last else "")
        row.update({"state": "bad", "status": err, "history": [], "invalid": True} if err else judge(c["how"], days, today))
        row["checkin"] = row["checkin"] and not row.get("invalid")   # a click can never count on a broken line
        out["items"].append(row)
    n = {s: sum(1 for r in out["items"] if r["state"] == s) for s in ("ok", "due", "bad")}
    out["summary"] = "%d kept · %d to do · %d slipping" % (n["ok"], n["due"], n["bad"]) if out["items"] else ""
    for state, words in (("idle", "not started"), ("off", "off today")):
        if any(r["state"] == state for r in out["items"]):
            out["summary"] += " · %d %s" % (sum(1 for r in out["items"] if r["state"] == state), words)
    return out


def agents(chars, ship_sources):
    """working: grouped by name (a node wrapper and its binary are one agent). waiting: a terminal
    showing a permission prompt. idle: names only. problems: stale decks and failed sources."""
    working, waiting, idle, problems = {}, [], [], []
    for c in chars:
        if c.get("kind") not in ("seat", "ship"):
            continue
        status, name = str(c.get("status") or ""), str(c.get("name") or "?")
        where = str(c.get("cwd") or "")
        if status == "WORKING":
            w = working.setdefault(name, {"uid": c.get("uid"), "name": name, "where": where, "app": c.get("app") or "",
                                          "source": c.get("source") or "tmux", "voice": "", "n": 0})
            w["n"] += 1
            if c.get("source") == "t3":
                w["voice"] = _clip(c.get("pane") or "", 120)
        elif status == "WAITING":
            waiting.append({"uid": c.get("uid"), "name": name, "where": where, "voice": _clip(c.get("pane") or "", 160)})
        elif status in ("STALE", "ABSENT", "ERROR", "LIMITED"):
            problems.append(name + ": " + _clip(c.get("cause") or c.get("pane") or status.lower(), 120))
        else:
            idle.append({"uid": c.get("uid"), "name": name, "source": c.get("source") or "tmux", "app": c.get("app") or ""})
    for s in ship_sources or []:
        if s.get("status") in ("ERROR", "ABSENT", "PARTIAL") and not str(s.get("source", "")).startswith("deck:"):
            problems.append(_clip(s.get("message") or s.get("source"), 140))
    return {"working": list(working.values()), "waiting": waiting, "idle": idle, "problems": problems}


def folders(tags, work_root, planets=None):
    """Each planet with its one-line purpose: the owner's planets when listed (their own desc, else
    the roots-map row), else each top-level folder of the work root from the roots map. The second
    list is what sits at the top level and is on no planet."""
    try:
        real = sorted(d for d in os.listdir(work_root) if os.path.isdir(os.path.join(work_root, d)))
    except OSError:
        return [], []
    if planets:
        known, covered = [], set()
        for p in planets:
            if not os.path.isdir(p.get("path") or ""):
                continue
            row = tags.get(p["name"]) or {}
            try:
                top = os.path.relpath(p["path"], work_root).split(os.sep)[0]
            except ValueError:
                top = ""
            if not row and top and not top.startswith(".."):
                row = tags.get(top) or {}
            if top and not top.startswith(".."):
                covered.add(top)
            known.append({"name": p["name"], "tag": p.get("tag") or row.get("tag") or "", "desc": p.get("desc") or row.get("desc") or ""})
        return known, [d for d in real if d not in covered and not d.startswith(".")]
    known = [{"name": k, "tag": v.get("tag") or "", "desc": v.get("desc") or ""}
             for k, v in tags.items() if k in real]
    return known, [d for d in real if d not in tags and not d.startswith(".")]


def build(cfg, chars, ship_sources, tags, now=None):
    today_cfg = cfg.get("today") or {}
    now = now or datetime.now()
    today = now.date()
    missing = []
    out = {"date": now.strftime("%a %b %-d"), "root": cfg.get("work_root") or "", "word": None, "needs": [], "done": [], "parked": None, "plan": None,
           "agents": agents(chars, ship_sources), "daily": [], "folders": [], "unmapped": [], "missing": missing,
           "ledger": [], "night": None, "night_note": None, "blocked": []}
    # Today has something to say only where a source is named: a task list, a digest or a path in "today".
    # The *_rx keys and night_marker only filter a digest, they are not sources. The page opens on the map without one.
    sources = [k for k, v in today_cfg.items() if v and k not in ("jobs_rx", "alert_rx", "fail_rx", "night_marker")]
    out["configured"] = bool(sources or cfg.get("fleet") or cfg.get("digest"))

    out["word"] = word(today_cfg, today, missing)
    if today_cfg.get("todo_md"):
        got = todo(today_cfg["todo_md"], missing)
        if out["word"]:
            out["word"]["items"] += got["items"]
        else:
            out["word"] = got
    if cfg.get("fleet"):
        try:
            blocked = []
            out["needs"], out["done"] = fleet(cfg["fleet"], today, owner=cfg.get("owner") or "", blocked=blocked)
            out["blocked"] = blocked          # every blocked claim, the old ones too: the sky ages them
        except OSError as exc:
            missing.append("task list unreadable: %s (%s)" % (cfg["fleet"], exc.strerror or exc))
    if isinstance(today_cfg.get("git_dirs"), list) and today_cfg["git_dirs"]:
        out["done"] = sorted(out["done"] + git_done(today_cfg["git_dirs"], today, missing),
                             key=lambda d: d["at"].zfill(5), reverse=True)
    if today_cfg.get("ledger_jsonl"):
        try:
            latest = _ledger_latest(_read(today_cfg["ledger_jsonl"]))
        except OSError:
            missing.append("decision ledger unreadable: " + today_cfg["ledger_jsonl"])
        else:                               # one read: the open rows and their count
            out["ledger"] = ledger_rows(latest, today)
            out["parked"] = len(out["ledger"])
    if today_cfg.get("plan_md"):
        try:
            out["plan"] = plan(today_cfg["plan_md"], today, today_cfg.get("learning_log") or "")
        except OSError:
            missing.append("plan unreadable: " + today_cfg["plan_md"])
    digest_text, digest_lines = None, []
    if cfg.get("digest"):
        try:
            digest_text = _read(cfg["digest"], TAIL)     # one read: today's lines and the night-run line
        except OSError:
            missing.append("digest unreadable: " + cfg["digest"])
        else:
            digest_lines = _today_lines(digest_text, today)
    out["daily"] = daily(today_cfg, today, digest_lines, missing, has_digest=bool(cfg.get("digest")), chars=chars)
    out["night"], out["night_note"] = night(today_cfg, today, digest_text, missing, has_digest=bool(cfg.get("digest")))
    out["folders"], out["unmapped"] = folders(tags or {}, cfg.get("work_root") or "", cfg.get("planets"))
    # With planets listed, the leftover folders are on no planet at all: they are not on the map,
    # so there is no planet to fly to and "no description yet" would be the wrong thing to say.
    out["unmapped_off_map"] = bool(cfg.get("planets"))
    return out
