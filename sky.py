#!/usr/bin/env python3
"""sky.py — the sky: every thing that needs a person, merged into one list of contacts.

Running work is counted (hyper), never drawn; an empty list is a finished day. render-game.py puts
build()'s result in WORLD["sky"] (a failure there leaves {"contacts": [], "error": "..."}).

Shape:
  {"contacts": [{"id": "<source>:<key>", "source": one of SOURCES, "state": one of STATES,
                 "title": str (<= 120), "since": iso | None, "age_days": int | None,
                 "tier": one of TIERS, "planet_uid": str | None,
                 "open": {"uid": str} | {"url": str} | None, "voice": str (<= 80)}],
   "hyper": {"n": int, "by_source": {"t3": int, "task": int, "seat": int}},
   "parked": int, "settled_today": int, "derelicts": int,
   "by_source": {source: int}, "oldest_days": int | None}
Contacts come in STATES order, then oldest first (an undated one last), then by id.

Where each source comes from (every one is read from the world dict with .get, and a source that
is absent or unreadable gives no contacts, never an error):
  seat    chars kind "seat" (not remote) in WAITING (blocked) or DRAFT (ready: the person's own
          unsent text); WORKING ones are hyper, except a bare shell's (shell true: an empty berth,
          no agent). id = the seat's uid ("seat:<seat id>").
  t3      chars kind "ship", source "t3": ship["t3"]["state"] waiting -> blocked, failed -> failed,
          ready -> ready, working -> hyper, snoozed -> parked, settled today -> settled_today.
          Without ship["t3"] the ship's status decides (WAITING, WORKING, FAILED/ERROR). The same
          thread listed by two databases counts once, the one labelled "T3" first. A subagent is
          folded into the thread at the top of its line: its raised hand makes that thread blocked,
          its work is hyper, anything else it does stays with it.
  ship    other agent ships (background processes) that are WORKING count as hyper seats.
  task    agent_tasks: review and stopped -> review, failed and lost -> failed; running, checking,
          stopping, starting and a failed one with a retry due are hyper. id = "task:<id>".
  cron    chars kind "cron" whose verdict is STALE or NO RECEIPT (stale) or NEVER (failed);
          dated by the freshest receipt. id = the cron's uid ("cron:<n>").
  board   tasks["items"] not handed to an agent (who != "agent"): stale, dated by the item's own
          stamp or else the audit's generated stamp.
  fleet   today["blocked"] (every claim whose newest line is blocked, however old: an old one ages
          into a raider), else today["needs"] (the last week's): blocked, dated by the claim id.
  ledger  today["ledger"] open rows: blocked when the row names what it blocks, else stale; age is
          computed from "opened" (a stored age is a snapshot and goes stale).
  quest   quests (parked lines with a decision marker): stale, aged by the date on their line. A
          quest whose normalized text equals a shown ledger row's normalized title is the same open
          loop and is left to the ledger row; any other overlap stays visible in by_source.
  night   today["night"]: each item whose verdict is ready (ready), failed (failed) or refused
          (blocked); with no items, one contact worded by the night's line, failed when its failed
          count is above 0, else ready when its ready count is.
  workflow  workflows (workflows.py summaries): a failed step (failed), a gate waiting for the person
          (blocked: needs your answer), a task step waiting on the user (review: accept the changes, or a
          second opinion still to pass it), a workflow with no news past its stale_after_min (stale).
          id = "workflow:<id>:<step key>" ("workflow:<id>" for stale); it opens the step's planet. A
          thread a workflow step runs is that step's, so its own task contact is dropped. A derived
          workflow (the night run) adds nothing: the night source already lists its items.
Derelicts are runners marked DEAD, DOWN or FROZEN: counted, never contacts.

A row that is not the shape its source writes is skipped. Text is the source's own words, cut to
the contract's length. Read-only and pure: no file, no network, no clock but `now`.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import words
from ships import parse_iso

SOURCES = ("ledger", "t3", "task", "seat", "cron", "board", "fleet", "quest", "night", "workflow")
STATES = ("blocked", "failed", "review", "ready", "stale")      # severity, worst first
# counts(): every contact lands in exactly one bucket, in this order of tests (a decision source first, any state)
COUNT_BUCKETS = ("decision", "answer", "problem", "review")
TIERS = ("fresh", "aging", "raider")                            # 0 whole days, 1-7, 8 and more

TITLE_MAX, VOICE_MAX = 120, 80
BAD_CRON = {"STALE": "stale", "NO RECEIPT": "stale", "NEVER": "failed"}
TASK_STATE = {"review": "review", "stopped": "review", "failed": "failed", "lost": "failed"}
TASK_WORKING = ("running", "checking", "stopping", "new")
T3_STATE = {"waiting": "blocked", "failed": "failed", "ready": "ready"}
T3_STATUS = {"WAITING": "blocked", "FAILED": "failed", "ERROR": "failed"}
SEAT_STATE = {"WAITING": "blocked", "DRAFT": "ready"}
NIGHT_STATE = {"ready": "ready", "failed": "failed", "refused": "blocked"}
DERELICT = ("DEAD", "DOWN", "FROZEN")
MIN_EPOCH = 946684800        # 2000-01-01: a smaller number is a count or a typo, not a stamp
BIG = 10 ** 15               # past this an int is no stamp, age or count (and a float cannot hold it exactly)
MAX_DAYS = 36500             # a stored age beyond a century is a typo

_DATE_ONLY = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTH_DAY = re.compile(r"(\d{1,2})-(\d{1,2})")
_CLAIM_ID = re.compile(r"T-(\d{4})(\d{2})(\d{2})(?:-(\d{2})(\d{2}))?(?!\d)")
_NOT_WORD = re.compile(r"[\W_]+")          # any script's letters and digits are words


def empty():
    """The sky with nothing in it."""
    return {"contacts": [], "hyper": {"n": 0, "by_source": {"t3": 0, "task": 0, "seat": 0}},
            "parked": 0, "settled_today": 0, "derelicts": 0, "by_source": {}, "oldest_days": None}


# ---------- small helpers ----------
def _text(value):
    """One line of text: whitespace runs (newlines included) become one space. A number past what a
    count can be (str() of a huge int raises) and anything that is not text or a number is no text."""
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value) if _number(value) else ""


def _cut(value, n):
    text = _text(value)
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _norm(text):
    """Case folded, every run of non-alphanumerics one space: two spellings of a title compare equal."""
    return _NOT_WORD.sub(" ", _text(text).casefold()).strip()


def _title_hash(text):
    return hashlib.sha1(_norm(text).encode("utf-8")).hexdigest()[:12]


def _rows(value):
    """The dict rows of a list (anything else gives none)."""
    return [r for r in value if isinstance(r, dict)] if isinstance(value, (list, tuple)) else []


def _number(value):
    """A finite number a float can hold (an int past 10**15 is not a count of seconds or days here)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return -BIG < value < BIG
    return isinstance(value, float) and math.isfinite(value)


def _pick(table, value):
    """table[value] for a text value; anything else (a list, a number, None) finds nothing."""
    return table.get(value) if isinstance(value, str) else None


def _key(value):
    """A row's id as text: a string or a number; anything else (missing, a list, true) is no id."""
    return value if isinstance(value, str) else str(value) if _number(value) else ""


def _when(value):
    """(epoch seconds, date) for a stamp: a date-only stamp is local midnight and keeps its date
    (its age is counted in calendar days); epoch numbers and ISO stamps (naive = UTC) give no date.
    (None, None) when it is not a stamp."""
    if isinstance(value, bool) or value is None:
        return None, None
    if isinstance(value, (int, float)):
        return (float(value), None) if _number(value) and value >= MIN_EPOCH else (None, None)
    if isinstance(value, datetime):
        when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return when.timestamp(), None
    if isinstance(value, date):
        return _day_when(value)
    if not isinstance(value, str):
        return None, None
    text = value.strip()
    m = _DATE_ONLY.fullmatch(text)
    if m:
        try:
            return _day_when(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            return None, None
    when = parse_iso(text)
    if when is None:
        return None, None
    try:
        return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).timestamp(), None
    except (ValueError, OverflowError, OSError):
        return None, None


def _day_when(day):
    try:
        return datetime(day.year, day.month, day.day).timestamp(), day
    except (ValueError, OverflowError, OSError):
        return None, None


class _Clock:
    """`now` as epoch seconds and as the local calendar day."""

    def __init__(self, now):
        if isinstance(now, datetime):
            now = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()
        self.now = float(now) if _number(now) and 0 < now < BIG else time.time()
        self.today = datetime.fromtimestamp(self.now).date()

    def age(self, ts, day):
        if day is not None:
            return max(0, (self.today - day).days)
        if ts is None:
            return None
        return max(0, int((self.now - ts) // 86400))

    def local_day(self, ts):
        try:
            return datetime.fromtimestamp(ts).date()
        except (ValueError, OverflowError, OSError):
            return None


def _tier(age):
    if age is None or age < 1:
        return "fresh"
    return "aging" if age <= 7 else "raider"


def _iso(ts):
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts is not None else None
    except (ValueError, OverflowError, OSError):
        return None


class _Planets:
    """Which planet (area uid) a path or an area name belongs to: the deepest area whose folder
    holds the path. A symlinked area resolves to the area it points at when that one is on the map."""

    def __init__(self, chars):
        areas = [c for c in chars if c.get("kind") == "area" and isinstance(c.get("uid"), str)]
        self.uids = {c["uid"] for c in areas if not c.get("alias_of")}
        self.by_path, self.by_name = {}, {}
        for c in areas:
            if not c.get("alias_of") and isinstance(c.get("path"), str) and c["path"]:
                self.by_path[os.path.normpath(c["path"])] = c["uid"]
            if not c.get("alias_of") and isinstance(c.get("name"), str):
                self.by_name.setdefault(c["name"], c["uid"])
        for c in areas:
            link, path = c.get("alias_of"), c.get("path")
            if isinstance(link, str) and link and isinstance(path, str) and path:
                target = self.by_path.get(os.path.normpath(link))
                if target:
                    self.by_path.setdefault(os.path.normpath(path), target)

    def of_path(self, path):
        if not isinstance(path, str) or not path.startswith("/"):
            return None
        p = os.path.normpath(path)
        while True:
            hit = self.by_path.get(p)
            if hit:
                return hit
            up = os.path.dirname(p)
            if up == p:
                return None
            p = up

    def of_uid(self, uid):
        return uid if isinstance(uid, str) and uid in self.uids else None

    def of_name(self, name):
        return self.by_name.get(name) if isinstance(name, str) else None


def _contact(clock, source, key, state, title, when=(None, None), planet=None, open_=None, voice=None):
    ts, day = when
    if day is None and ts is not None and not 0 < ts < BIG:
        ts = None                                # arithmetic on a nonsense age is no stamp
    age = clock.age(ts, day)
    title = _cut(title, TITLE_MAX) or _cut(key, TITLE_MAX)
    return {"id": source + ":" + key if not key.startswith(source + ":") else key,
            "source": source, "state": state, "title": title, "since": _iso(ts), "age_days": age,
            "tier": _tier(age), "planet_uid": planet, "open": open_,
            "voice": _cut(voice if _text(voice) else title, VOICE_MAX), "_ts": ts}


# ---------- the sources ----------
def _seats(chars, clock, planets, hyper):
    out = []
    for c in chars:
        if c.get("remote"):
            continue
        status = c.get("status")
        if c.get("kind") == "ship" and c.get("source") not in ("t3", "deck"):
            if status == "WORKING":
                hyper["seat"] += 1
            continue
        if c.get("kind") != "seat":
            continue
        if status == "WORKING":
            if c.get("shell") is not True:      # a bare shell is a berth, not an agent at work
                hyper["seat"] += 1
            continue
        state = _pick(SEAT_STATE, status)
        uid = c.get("uid")
        if not state or not isinstance(uid, str) or not uid:
            continue
        when = _when(c.get("since"))
        if when[0] is None:
            when = _when(c.get("window_activity"))
        if when[0] is None:
            when = _when(c.get("activity"))     # render-game.py's seat: tmux #{window_activity}, epoch
        out.append(_contact(clock, "seat", uid, state, c.get("name") or uid, when,
                            planets.of_uid(c.get("docked_at")) or planets.of_path(c.get("cwd")),
                            {"uid": uid}, c.get("line") or c.get("pane")))
    return out


def _thread_id(ship):
    t3 = ship.get("t3") if isinstance(ship.get("t3"), dict) else {}
    tid = t3.get("thread_id")
    if isinstance(tid, str) and tid:
        return tid
    uid = ship.get("uid")
    if isinstance(uid, str) and uid.startswith("ship:t3:") and len(uid) > 8:
        return uid[8:]
    return None


def _t3_role(ship, clock):
    """("contact", state) | ("hyper",) | ("parked",) | ("settled", today?) | (None,)"""
    t3 = ship.get("t3") if isinstance(ship.get("t3"), dict) else None
    state = t3.get("state") if t3 else None
    if isinstance(state, str) and state:
        if state in T3_STATE:
            return ("contact", T3_STATE[state])
        if state == "working":
            return ("hyper",)
        if state == "snoozed":
            return ("parked",)
        if state == "settled":
            ts, day = _when(t3.get("settled_at"))
            settled_day = day or (clock.local_day(ts) if ts is not None else None)
            return ("settled", settled_day == clock.today)
        return (None,)
    status = ship.get("status")
    if status == "WORKING":
        return ("hyper",)
    state = _pick(T3_STATUS, status)
    return ("contact", state) if state else (None,)


def _t3(chars, clock, planets, hyper, counts):
    rank = {"T3": 0, "T3 (v2)": 1}
    best = {}                                    # thread id -> (rank, order, ship)
    for order, c in enumerate(chars):
        if c.get("kind") != "ship" or c.get("source") != "t3":
            continue
        tid = _thread_id(c)
        if not tid:
            continue
        t3 = c.get("t3") if isinstance(c.get("t3"), dict) else {}
        r = _pick(rank, t3.get("app") or c.get("app"))
        r = 2 if r is None else r
        if tid not in best or r < best[tid][0]:
            best[tid] = (r, order, c)
    ships = {tid: v[2] for tid, v in sorted(best.items(), key=lambda kv: kv[1][1])}
    roles = {tid: _t3_role(s, clock) for tid, s in ships.items()}

    def parent(tid):
        t3 = ships[tid].get("t3") if isinstance(ships[tid].get("t3"), dict) else {}
        p = t3.get("parent")
        return p if isinstance(p, str) and p in ships and p != tid else None

    roots = {}                                   # thread -> the thread at the top of its line
    for start in ships:
        line, on_line, tid = [], set(), start
        while tid not in roots:
            if tid in on_line:                   # a loop in the links: each thread on it is its own root
                for t in line[line.index(tid):]:
                    roots[t] = t
                break
            line.append(tid)
            on_line.add(tid)
            up = parent(tid)
            if up is None:
                roots[tid] = tid
                break
            tid = up
        for t in reversed(line):
            if t not in roots:
                roots[t] = roots[parent(t)]
    raised = {}                                  # root id -> the subagent whose hand is up
    for tid, top in roots.items():
        if top == tid:
            continue
        if roles[tid] == ("hyper",):
            hyper["t3"] += 1
        elif roles[tid] == ("contact", "blocked") and top not in raised:
            raised[top] = ships[tid]
    out = []
    for tid, s in ships.items():
        if roots[tid] != tid:
            continue
        role, child = roles[tid], raised.get(tid)
        t3 = s.get("t3") if isinstance(s.get("t3"), dict) else {}
        voice, when = s.get("pane"), _when(t3.get("since"))
        if when[0] is None:
            when = _when(s.get("updated_at"))
        if child is not None and (role[0] != "contact" or STATES.index(role[1]) > 0):
            role = ("contact", "blocked")
            ct3 = child.get("t3") if isinstance(child.get("t3"), dict) else {}
            voice = child.get("pane") or voice
            cwhen = _when(ct3.get("since"))
            when = cwhen if cwhen[0] is not None else when
        if role[0] == "hyper":
            hyper["t3"] += 1
        elif role[0] == "parked":
            counts["parked"] += 1
        elif role[0] == "settled":
            counts["settled_today"] += 1 if role[1] else 0
        elif role[0] == "contact":
            url = t3.get("open_url")
            uid = s.get("uid")
            open_ = ({"url": url} if isinstance(url, str) and url
                     else {"uid": uid} if isinstance(uid, str) else None)
            out.append(_contact(clock, "t3", tid, role[1], s.get("name") or tid, when,
                                planets.of_uid(s.get("docked_at")) or planets.of_path(s.get("cwd")),
                                open_, voice))
    return out


def _tasks(rows, clock, planets, hyper):
    out = []
    for t in _rows(rows):
        tid, status = t.get("id"), t.get("status")
        if not isinstance(tid, str) or not tid or not isinstance(status, str):
            continue
        if status in TASK_WORKING or (status == "failed" and t.get("retry_due")):
            hyper["task"] += 1
            continue
        state = _pick(TASK_STATE, status)
        if not state:
            continue
        when = (None, None)
        for key in ("ended", "started", "created"):
            when = _when(t.get(key))
            if when[0] is not None:
                break
        # the title as a person says it (no "Goal:", ids or paths); a task with no words of its own is "a task"
        title = words.short_title(_text(t.get("title")) or _text(t.get("text"))) or "a task"
        verdict, model = None, None
        for key in ("check", "last_verdict"):
            v = t.get(key)
            if isinstance(v, dict) and isinstance(v.get("verdict"), str) and v["verdict"]:
                verdict, model = v["verdict"], v.get("label") or v.get("model")
                break
        opinion = words.opinion_line(verdict, model if isinstance(model, str) else None) if verdict else ""
        out.append(_contact(clock, "task", tid, state, title, when, planets.of_path(t.get("target")),
                            {"uid": "task:" + tid}, title + (" · " + opinion if opinion else "")))
    return out


def _crons(chars, clock, planets):
    out = []
    for c in chars:
        if c.get("kind") != "cron":
            continue
        state, uid = _pick(BAD_CRON, c.get("verdict")), c.get("uid")
        if not state or not isinstance(uid, str) or not uid:
            continue
        ages = [a for a in (c.get("receipt_age"), c.get("fired_age")) if _number(a) and a >= 0]
        when = (clock.now - min(ages), None) if ages else (None, None)
        # "job true: never ran": the job by its script's (or program's) name, its verdict in words; the voice adds
        # when it runs ("job true runs daily at 03:30: never ran"), never the receipt's raw evidence
        name = words.job_name(c)
        when_words = words.schedule_words(c.get("schedule"))
        out.append(_contact(clock, "cron", uid, state, words.job_line(name, c.get("verdict")), when,
                            planets.of_uid(c.get("area")), {"uid": uid},
                            f"job {name}" + (f" runs {when_words}" if when_words else "") + ": "
                            + words.verdict_word(c.get("verdict"))))
    return out


def _board(tasks, clock, planets):
    if not isinstance(tasks, dict):
        return []
    generated = _when(tasks.get("generated"))
    out = []
    for item in _rows(tasks.get("items")):
        iid = item.get("id")
        if not _key(iid) or item.get("who") == "agent":
            continue
        when = (None, None)
        for key in ("opened", "created", "at"):
            when = _when(item.get(key))
            if when[0] is not None:
                break
        if when[0] is None:
            when = generated
        finding = _text(item.get("finding")) or str(iid)
        planet = planets.of_name(item.get("area")) or planets.of_path(item.get("path"))
        out.append(_contact(clock, "board", str(iid), "stale", finding, when, planet, None, finding))
    return out


def _claim_when(tid):
    m = _CLAIM_ID.match(tid)
    if not m:
        return None, None
    y, mo, d, hh, mm = m.groups()
    try:
        if hh is None:
            return _day_when(date(int(y), int(mo), int(d)))
        return datetime(int(y), int(mo), int(d), int(hh), int(mm)).timestamp(), None
    except (ValueError, OverflowError, OSError):
        return None, None


def _fleet(today, clock):
    out = []
    rows = today.get("blocked") if isinstance(today.get("blocked"), list) else today.get("needs")
    for row in _rows(rows):
        tid = row.get("id")
        if not isinstance(tid, str) or not tid:
            continue
        title = _text(row.get("title")) or tid
        out.append(_contact(clock, "fleet", tid, "blocked", title, _claim_when(tid), None, None,
                            row.get("note") or title))
    return out


def _ledger(today, clock):
    """(contacts, the titles they came from): the quest de-dupe compares against rows that show."""
    out, titles = [], []
    for row in _rows(today.get("ledger")):
        lid, title = row.get("id"), _text(row.get("title"))
        if not _key(lid) or not title:
            continue
        when = _when(row.get("opened"))
        age = row.get("age_days")
        c = _contact(clock, "ledger", str(lid), "blocked" if _text(row.get("blocks")) else "stale",
                     title, when, None, None, title)
        if when[0] is None and isinstance(age, int) and not isinstance(age, bool) and 0 <= age <= MAX_DAYS:
            c["age_days"], c["tier"] = age, _tier(age)       # undated row: its own stored age, sorted by it
            c["_ts"] = clock.now - age * 86400
        out.append(c)
        titles.append(title)
    return out, titles


def _quests(rows, ledger_titles, clock):
    hashes = {_title_hash(t) for t in ledger_titles if _norm(t)}
    out = []
    for q in _rows(rows):
        text = _text(q.get("text"))
        if not text:
            continue
        if _title_hash(text) in hashes:
            continue                              # the ledger row is the same open loop
        qid = q.get("id")
        key = qid if isinstance(qid, str) and qid else "q-" + _title_hash(text)[:10]
        days = q.get("days")
        when = (None, None)
        if isinstance(days, int) and not isinstance(days, bool) and 0 <= days <= MAX_DAYS:
            when = _day_when(clock.today - timedelta(days=days))
        out.append(_contact(clock, "quest", key, "stale", text, when, None, None, text))
    return out


def _night_when(value, clock):
    if isinstance(value, str):
        m = _MONTH_DAY.fullmatch(value.strip())
        if m:
            try:
                day = date(clock.today.year, int(m.group(1)), int(m.group(2)))
                if day > clock.today:
                    day = date(clock.today.year - 1, day.month, day.day)
                return _day_when(day)
            except ValueError:
                return None, None
    return _when(value)


def _night(today, clock, planets):
    night = today.get("night")
    if not isinstance(night, dict):
        return []
    when = _night_when(night.get("date"), clock)
    stamp = _text(night.get("date")) or "night"
    out = []
    items = _rows(night.get("items"))
    for i, item in enumerate(items):
        verdict = _text(item.get("verdict"))
        state = _pick(NIGHT_STATE, verdict.lower()) or ("failed" if "fail" in verdict.lower() else None)
        if not state:
            continue
        iid = item.get("id")
        key = _key(iid) or f"{stamp}:{i}"
        title = _text(item.get("title")) or key
        repo = item.get("repo")
        out.append(_contact(clock, "night", key, state, title, when,
                            planets.of_path(repo) or planets.of_name(repo), None,
                            title + " — " + verdict))
    if items:
        return out

    def count(field):
        n = night.get(field)
        return n if isinstance(n, int) and _number(n) and n > 0 else 0

    failed, ready = count("failed"), count("ready")
    if failed or ready:                          # one line, one contact: as bad as its worst count
        words = _text(night.get("line")) or (f"{failed} failed" if failed else f"{ready} ready")
        out.append(_contact(clock, "night", stamp, "failed" if failed else "ready", words, when,
                            None, None, words))
    return out


def _workflows(rows, clock, contacts):
    """The contacts so far without the task contacts a workflow step owns, plus the workflows' own."""
    out, owned = [], set()
    for w in _rows(rows):
        wid = w.get("id")
        if not isinstance(wid, str) or not wid or w.get("derived"):
            continue
        title = _text(w.get("title")) or wid
        when = _when(w.get("updated"))
        steps = _rows(w.get("steps"))
        for st in steps:
            if isinstance(st.get("tid"), str) and st["tid"]:
                owned.add("task:" + st["tid"])
        if w.get("state") == "stale":
            cur = next((st for st in steps if st.get("key") in (w.get("current") or [])), None) or {}
            planet = cur.get("planet_uid") if isinstance(cur.get("planet_uid"), str) else None
            out.append(_contact(clock, "workflow", wid, "stale", title + ": no news", when, planet,
                                {"uid": planet} if planet else None, _text(w.get("sentence")) or title))
            continue
        for st in steps:
            key, state, kind = st.get("key"), st.get("state"), st.get("kind")
            if not isinstance(key, str) or not key:
                continue
            if state == "failed":
                mine, voice = "failed", _text(st.get("why")) or "failed"
            elif state == "needs_you" and kind == "gate":
                mine, voice = "blocked", "waiting for your approval"
            elif state == "needs_you":
                mine, voice = "review", _text(st.get("why")) or "waiting for you"
            else:
                continue
            planet = st.get("planet_uid") if isinstance(st.get("planet_uid"), str) else None
            label = _text(st.get("label")) or key
            out.append(_contact(clock, "workflow", wid + ":" + key, mine,
                                title + ": " + label + (" failed" if mine == "failed" else ""), when, planet,
                                {"uid": planet} if planet else None, label + " — " + voice))
    return [c for c in contacts if c["id"] not in owned] + out


def _derelicts(chars):
    return sum(1 for c in chars if c.get("kind") == "runner" and c.get("status") in DERELICT)


# ---------- the rollup ----------
def build(world, now=None):
    """WORLD["sky"] for one render. world: the dict render-game.py assembles (read with .get only);
    now: epoch seconds (render-game.py passes its NOW), None = the current time."""
    world = world if isinstance(world, dict) else {}
    clock = _Clock(now)
    chars = _rows(world.get("chars"))
    today = world.get("today") if isinstance(world.get("today"), dict) else {}
    planets = _Planets(chars)
    hyper = {"t3": 0, "task": 0, "seat": 0}
    counts = {"parked": 0, "settled_today": 0}

    ledger, ledger_titles = _ledger(today, clock)
    contacts = (_seats(chars, clock, planets, hyper) + _t3(chars, clock, planets, hyper, counts)
                + _tasks(world.get("agent_tasks"), clock, planets, hyper)
                + _crons(chars, clock, planets) + _board(world.get("tasks"), clock, planets)
                + _fleet(today, clock) + ledger + _quests(world.get("quests"), ledger_titles, clock)
                + _night(today, clock, planets))
    contacts = _workflows(world.get("workflows"), clock, contacts)
    counts["settled_today"] += sum(1 for r in _rows(today.get("done")) if _key(r.get("id")))

    seen, unique = set(), []
    for c in contacts:
        if c["id"] not in seen:
            seen.add(c["id"])
            unique.append(c)
    unique.sort(key=lambda c: (STATES.index(c["state"]), c["_ts"] is None,
                               c["_ts"] if c["_ts"] is not None else 0.0, c["id"]))
    by_source = {}
    for c in unique:
        c.pop("_ts")
        by_source[c["source"]] = by_source.get(c["source"], 0) + 1
    ages = [c["age_days"] for c in unique if c["age_days"] is not None]
    return {"contacts": unique, "hyper": {"n": sum(hyper.values()), "by_source": hyper},
            "parked": counts["parked"], "settled_today": counts["settled_today"],
            "derelicts": _derelicts(chars), "by_source": by_source,
            "oldest_days": max(ages) if ages else None}


DECISION_SOURCES = ("ledger", "board", "quest", "fleet")
COUNT_KEYS = ("need_you", "answer", "review", "decisions", "problems", "working", "on_hold", "done_today",
              "oldest_decision", "total")
_DONE_TASK = ("merged", "done")


def bucket(contact):
    """The one COUNT_BUCKETS bucket a contact lands in: a decision source (any state) is a decision; else a blocked
    one needs an answer, a failed or stale one is a problem, a review or ready one is to review."""
    if not isinstance(contact, dict):
        return "problem"
    if contact.get("source") in DECISION_SOURCES:
        return "decision"
    state = contact.get("state")
    if state == "blocked":
        return "answer"
    if state in ("review", "ready"):
        return "review"
    return "problem"


def _today_stamp(value, today):
    ts, day = _when(value)
    if day is not None:
        return day == today
    if ts is None:
        return False
    try:
        return datetime.fromtimestamp(ts).date() == today
    except (ValueError, OverflowError, OSError):
        return False


def counts(sky=None, tasks=None, workflows=None, now=None):
    """The one count every surface reads (WORLD["counts"] at render, every /tasks reply): each contact of
    `sky` (build()'s result) in exactly one of COUNT_BUCKETS (bucket()), plus the work in progress from `tasks`
    (the threads, tasks.py summaries) and `workflows` (their summaries). Shape:
      {"need_you": answer + review, "answer", "review", "decisions", "problems",
       "working": agents at work now (sessions + threads running or being checked + workflows whose own note
                  step is working, each workflow's author once),
       "on_hold": parked threads, "done_today": settled today + threads accepted today + workflows done today,
       "oldest_decision": {"days", "source"} of the oldest decision (None, None without one),
       "total": len(contacts)}
    The four buckets always add up to total. A sky that is not build()'s shape counts as empty."""
    sky = sky if isinstance(sky, dict) else {}
    clock = _Clock(now)
    contacts = _rows(sky.get("contacts"))
    out = {"need_you": 0, "answer": 0, "review": 0, "decisions": 0, "problems": 0, "working": 0,
           "on_hold": 0, "done_today": 0, "oldest_decision": {"days": None, "source": None}, "total": len(contacts)}
    plural = {"decision": "decisions", "answer": "answer", "problem": "problems", "review": "review"}
    oldest = None
    for c in contacts:
        b = bucket(c)
        out[plural[b]] += 1
        if b == "decision":
            age = c.get("age_days")
            if isinstance(age, int) and not isinstance(age, bool) and (oldest is None or age > oldest[0]):
                oldest = (age, c.get("source"))
    assert out["decisions"] + out["answer"] + out["problems"] + out["review"] == out["total"]
    out["need_you"] = out["answer"] + out["review"]
    if oldest is not None:
        out["oldest_decision"] = {"days": oldest[0], "source": oldest[1]}
    hyper = sky.get("hyper") if isinstance(sky.get("hyper"), dict) else {}
    by = hyper.get("by_source") if isinstance(hyper.get("by_source"), dict) else {}

    def n(v):
        return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0

    working = n(by.get("seat")) + n(by.get("t3"))
    rows = _rows(tasks) if tasks is not None else None
    if rows is None:
        working += n(by.get("task"))
    else:
        working += sum(1 for t in rows if t.get("status") in TASK_WORKING
                       or (t.get("status") == "failed" and t.get("retry_due")))
    authors = set()
    done_wf = 0
    for w in _rows(workflows):
        steps = _rows(w.get("steps"))
        if any(s.get("kind") == "note" and s.get("state") == "working" for s in steps):
            by_ = w.get("by") if isinstance(w.get("by"), dict) else {}
            who = _text(by_.get("seat")) or _text(by_.get("name")) or _text(w.get("id"))
            authors.add(who)
        if w.get("state") == "done" and _today_stamp(w.get("updated"), clock.today):
            done_wf += 1
    working += len(authors)
    out["working"] = working
    out["on_hold"] = n(sky.get("parked"))
    out["done_today"] = n(sky.get("settled_today")) + done_wf + sum(
        1 for t in (rows or []) if t.get("status") in _DONE_TASK and _today_stamp(t.get("ended"), clock.today))
    return out
