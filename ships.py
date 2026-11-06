#!/usr/bin/env python3
"""Read-only ship sources. Metadata only: no messages, prompts, auth or network.

Each optional source returns its own finding even when unavailable. Nothing is ever copied:
a failed T3 read is reported in place (a copy would carry message and auth tables).

sqlite3 is imported where the T3 database is read and nowhere else: the renderer imports this
module, and a Python built without _sqlite3 must still draw the map (without T3 threads).
"""
import json, os, re, subprocess, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import words as _words


def workspace_path(path):
    """T3 stores Windows UNC paths even when the workspace lives in WSL."""
    path = str(path or "")
    match = re.match(r"^\\\\wsl\.(?:localhost|\$)\\[^\\]+(\\.*)?$", path, re.I)
    if match:
        path = (match.group(1) or "/").replace("\\", "/")
    return os.path.normpath(path) if path else ""


_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:[.,](\d+))?)?)?"
                  r"(Z|[+-]\d{2}(?::?\d{2})?)?$", re.I)


def parse_iso(value):
    """An ISO 8601 stamp as a datetime (aware when it names an offset), or None. Python 3.10's
    datetime.fromisoformat refuses a trailing Z, +HHMM and fractions that are not 3 or 6 digits,
    and logs and agents write all of them; parsing it here keeps the floor at 3.10."""
    match = _ISO.match(str(value).strip()) if isinstance(value, str) else None
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    try:
        tz = None
        if zone:
            if zone.upper() == "Z":
                tz = timezone.utc
            else:
                digits = zone[1:].replace(":", "")
                offset = timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
                tz = timezone(-offset if zone[0] == "-" else offset)
        return datetime(int(year), int(month), int(day), int(hour or 0), int(minute or 0),
                        int(second or 0), int((fraction or "")[:6].ljust(6, "0")), tz)
    except (ValueError, OverflowError):
        return None


def stamp(value):
    """Epoch seconds of an ISO stamp (a naive one is UTC), 0 when it is not one."""
    when = parse_iso(value)
    if when is None:
        return 0
    try:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.timestamp()
    except (ValueError, OverflowError, OSError):
        return 0


def ship(uid, name, source, status, cwd="", voice="", **extra):
    return {"uid": uid, "name": name, "kind": "ship", "source": source,
            "role": source + " ship", "status": status, "cwd": cwd, "pane": voice,
            "cause": "", "served": 0, "tour": "observed at render", "idle": 0,
            "hp": 100, "talkable": False, "composable": False, **extra}


# COMMS LINE: what a seat's pane says, as one clean line for the comms panel. The pane's tail is
# mostly furniture: the composer prompt, the shell prompt, permission and mode banners, key hints,
# box borders and escape codes. The line is the last one left after those are dropped.
_ESC = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_BOX = "─━│┃┄┅┆┇┈┉┊┋┌┍┎┏┐┑┒┓└┕┖┗┘┙┚┛├┝┞┟┠┡┢┣┤┥┦┧┨┩┪┫┬┭┮┯┰┱┲┳┴┵┶┷┸┹┺┻┼╭╮╯╰═║╔╗╚╝╠╣╦╩╬▔▁"
_BANNER = re.compile(
    r"bypass permissions|shift\s*\+\s*tab|\? for shortcuts|accept edits|auto-?accept|plan mode|"
    r"permissions? (?:on|mode)|ctrl\s*\+\s*\w+ to |esc to (?:interrupt|cancel)|press up to edit|"
    r"\bwarnings? f\d\b|context left|auto-?compact|tokens? (?:used|left)|^\s*(?:⏎|↵)|"
    r"\bsend\s+⌃|\bnewline\b.*\btranscript\b", re.I)
_PROMPT = re.compile(
    r"^\s*(?:[❯›»>]\s*.*|[$%#]\s*|\S*@\S*[:~][^\s]*[$#%]\s*.*|(?:~|/)[^\s]*[$#%>]\s*.*|"
    r"\([\w.-]+\)\s*\S*[$#%>]\s*.*)$")
_LEAD = re.compile(r"^[^\w(\[\"'`/~.]+")        # bullets and glyphs before the words: ⏺ ⎿ ● ✻ · -
# a pane can print a key or a token; the comms line never echoes one (shapes of the usual
# secrets, "name=value" pairs for secret-like names, and any bare 32+ character token)
_SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|eyJ[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{8,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}|[A-Za-z0-9+/_-]{32,})")
_SECRET_KV = re.compile(r"(?i)\b([\w-]*(?:api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd)[\w-]*)\s*[=:]\s*\S+")


def redact(line):
    """`line` with anything that looks like a credential replaced by [redacted]."""
    line = _SECRET_KV.sub(lambda m: m.group(1) + "=[redacted]", line)
    return _SECRET.sub("[redacted]", line)


def comms_line(pane, width=60):
    """The last meaningful line of a pane's tail, cleaned: escape codes and control bytes out,
    box borders, prompts, permission and mode banners and key hints dropped, the leading glyphs
    and a trailing "(… esc to interrupt)" trimmed, cut to `width` with an ellipsis. "" when the
    tail holds nothing but furniture."""
    text = _CTRL.sub("", _ESC.sub("", str(pane or "")).replace("\r", "\n"))
    for raw in reversed(text.split("\n")):
        line = raw.strip().strip(_BOX).strip()
        line = re.sub(r"\s*\((?:[^()]*·\s*)?esc to interrupt[^()]*\)\s*$", "", line, flags=re.I)
        if not line or not line.strip(_BOX + " ·-=_*.") or _BANNER.search(line) or _PROMPT.match(line):
            continue
        line = redact(re.sub(r"\s+", " ", _LEAD.sub("", line)).strip())
        if not line:
            continue
        return line if len(line) <= width else line[:width - 1].rstrip() + "…"
    return ""


def finding(source, status, message, count=0):
    return {"source": source, "status": status, "message": message, "count": count}


# T3 keeps its threads in one SQLite file, in one of two generations. v1 (the installed app):
# projection_* tables with the settle and raised-hand fields as columns. v2 (the newer server, its
# file is statev2.sqlite): orchestration_v2_projection_* tables with the thread record in
# payload_json; v2 keeps the v1 tables as frozen import sources, so a v2 file is read from its v2
# tables only. Thread states follow T3's own definitions (threadSettled.ts, ThreadSettlementService.ts):
#   waiting  a pending approval, user-input or runtime request: the agent is blocked on the human
#   working  a live run (or a running v1 session/turn)
#   settled  settledOverride "settled" (an automatic settle writes it too)
#   snoozed  snoozedUntil in the future and the thread has not raised its hand since snoozedAt
#   failed   the latest run failed (v1: the latest turn errored or the session is in error)
#   ready    anything else that is not settled: finished work, an open loop in the human's inbox
# Kept: every thread that is not settled and not snoozed, whatever its age (an open loop ages, it
# does not vanish), plus settled and snoozed threads touched in the last 24 hours (so a day's
# settles and parks can be counted). Archived and deleted threads are never read.
T3_BUDGET_S = 1.0              # a T3 read still running after this is interrupted, reported in place
T3_V2_FILE = "statev2.sqlite"
_WAL_FIRST_FRAME = 32 + 24 + 512   # WAL header + frame header + the smallest page: below it, no frame
_T3_V2_TABLE = "orchestration_v2_projection_threads"
_T3_ACTIVE_RUNS = ("preparing", "queued", "starting", "running", "waiting")
_T3_STATES = ("waiting", "working", "failed", "ready", "snoozed", "settled")
_T3_STATUS = {"waiting": "WAITING", "working": "WORKING", "failed": "ERROR"}   # others: IDLE
# the columns each generation may read; anything else (messages, auth, whole payloads read by any
# query that is not ours) is denied by the authorizer
_T3_V1_ALLOWED = {
    "projection_projects": {"project_id", "title", "workspace_root"},
    "projection_threads": {"thread_id", "project_id", "title", "updated_at", "archived_at", "deleted_at",
                           "pending_approval_count", "pending_user_input_count", "settled_override",
                           "settled_at", "snoozed_until", "snoozed_at"},
    "projection_thread_sessions": {"thread_id", "status", "provider_name", "last_error", "updated_at"},
    "projection_turns": {"thread_id", "state", "completed_at", "row_id", "ROWID"},
    "projection_thread_activities": {"thread_id", "summary", "created_at", "sequence"},
}
_T3_V2_ALLOWED = {
    "projection_projects": {"project_id", "title", "workspace_root"},
    # payload_json is the whole thread record (branch, worktree, model): the query reads it only
    # through json_extract of the paths in _T3_V2_PATHS, never selected whole
    _T3_V2_TABLE: {"thread_id", "project_id", "title", "default_provider", "updated_at",
                   "archived_at", "deleted_at", "payload_json"},
    "orchestration_v2_projection_runs": {"thread_id", "ordinal", "status", "completed_at"},
    "orchestration_v2_projection_runtime_requests": {"thread_id", "status", "kind", "created_at"},
    "orchestration_v2_projection_subagents": {"thread_id", "child_thread_id"},
}
# v1 columns added by later T3 versions: an older app's file reads them as empty
_T3_V1_OPTIONAL = {
    "projection_threads": {"pending_approval_count": "0", "pending_user_input_count": "0",
                           "settled_override": "NULL", "settled_at": "NULL",
                           "snoozed_until": "NULL", "snoozed_at": "NULL"},
    "projection_thread_sessions": {"last_error": "NULL", "updated_at": "NULL"},
    "projection_turns": {"completed_at": "NULL"},
}
_T3_V2_PATHS = ("$.settledOverride", "$.settledAt", "$.snoozedUntil", "$.snoozedAt",
                "$.lineage.relationshipToParent", "$.lineage.parentThreadId")


def _t3_authorizer(sqlite3, allowed):
    def authorize(action, table, column, db, trigger):
        if action == sqlite3.SQLITE_READ and column not in allowed.get(table, ()):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_SELECT,
                                               sqlite3.SQLITE_FUNCTION) else sqlite3.SQLITE_DENY
    return authorize


def _t3_open(path, deadline):
    """(db, v2, sql): a strictly read-only connection to a T3 file that stops at `deadline`
    (time.monotonic()), whether the file is v2, and the thread query for its generation. Before
    the generation's authorizer goes on, only our own schema statements run (sqlite_master and
    table_info read the schema, never a row); every query after it is held to the allow-list."""
    import sqlite3
    # mode=ro is mandatory on the live DB and on copies. No backup(), checkpoint or PRAGMA that
    # mutates the source. T3 writes this WAL database from Windows; across the WSL drvfs boundary the
    # -shm file can't be shared, so a normal open fails with "disk I/O error". unix-none skips file
    # locks and EXCLUSIVE keeps the WAL index in this process's memory: the file and its -shm are only read.
    # unix-none also fools SQLite's close into thinking it holds the last connection: it would
    # checkpoint, and when the WAL has no valid frame (T3 stopped and the -wal is gone, or T3 holds
    # an empty -wal) that "succeeds" and SQLite creates or deletes the -wal under T3, whose next
    # commit then fails with a disk I/O error. So: with no frame to read, the file alone is the whole
    # database and is opened immutable (no -wal or -shm touched at all); with frames, close is told
    # never to checkpoint (Python 3.12+; on older Pythons a WAL restart caught mid-write is the one
    # remaining window).
    real = Path(path).resolve()
    try:
        frames = os.path.getsize(str(real) + "-wal") >= _WAL_FIRST_FRAME
    except OSError:
        frames = False
    query = "?mode=ro&vfs=unix-none" if frames else "?mode=ro&immutable=1"
    db = sqlite3.connect(real.as_uri() + query, uri=True, timeout=0.1)
    try:
        if frames and hasattr(db, "setconfig") and hasattr(sqlite3, "SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE"):
            db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        db.execute("PRAGMA locking_mode=EXCLUSIVE")
        db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        v2 = db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (_T3_V2_TABLE,)).fetchone() is not None
        sql = _T3_V2_SQL if v2 else _t3_v1_sql(db)
        db.set_authorizer(_t3_authorizer(sqlite3, _T3_V2_ALLOWED if v2 else _T3_V1_ALLOWED))
    except BaseException:
        db.close()
        raise
    return db, v2, sql


def _t3_v1_sql(db):
    def col(alias, table, name):
        present = name in cols[table]
        return f"{alias}.{name}" if present else _T3_V1_OPTIONAL[table][name]
    # table names are the constants above, never input
    cols = {table: {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            for table in _T3_V1_OPTIONAL}
    latest = ("(SELECT r.{} FROM projection_turns r WHERE r.thread_id = t.thread_id "
              "ORDER BY r.rowid DESC LIMIT 1)")
    return f"""
        SELECT t.thread_id, p.title, t.title, p.workspace_root, t.updated_at, s.provider_name,
               (s.status = 'running' OR EXISTS (
                   SELECT 1 FROM projection_turns r
                   WHERE r.thread_id = t.thread_id AND r.state = 'running')),
               (SELECT a.summary FROM projection_thread_activities a
                WHERE a.thread_id = t.thread_id
                ORDER BY a.created_at DESC, a.sequence DESC LIMIT 1),
               {col('t', 'projection_threads', 'pending_approval_count')},
               {col('t', 'projection_threads', 'pending_user_input_count')},
               {col('t', 'projection_threads', 'settled_override')},
               {col('t', 'projection_threads', 'settled_at')},
               {col('t', 'projection_threads', 'snoozed_until')},
               {col('t', 'projection_threads', 'snoozed_at')},
               s.status, {col('s', 'projection_thread_sessions', 'last_error')},
               {col('s', 'projection_thread_sessions', 'updated_at')},
               {latest.format('state')},
               {latest.format('completed_at') if 'completed_at' in cols['projection_turns'] else 'NULL'}
        FROM projection_threads t
        JOIN projection_projects p ON p.project_id = t.project_id
        LEFT JOIN projection_thread_sessions s ON s.thread_id = t.thread_id
        WHERE t.archived_at IS NULL AND t.deleted_at IS NULL
    """


def _t3_json(path):
    # a malformed record reads as empty fields instead of failing the whole read
    return f"json_extract(CASE WHEN json_valid(t.payload_json) THEN t.payload_json END, '{path}')"


_T3_V2_SQL = f"""
    SELECT t.thread_id, p.title, t.title, p.workspace_root, t.updated_at, t.default_provider,
           {', '.join(_t3_json(path) for path in _T3_V2_PATHS)},
           (SELECT s.thread_id FROM orchestration_v2_projection_subagents s
            WHERE s.child_thread_id = t.thread_id LIMIT 1),
           (SELECT r.status FROM orchestration_v2_projection_runs r
            WHERE r.thread_id = t.thread_id ORDER BY r.ordinal DESC LIMIT 1),
           (SELECT r.completed_at FROM orchestration_v2_projection_runs r
            WHERE r.thread_id = t.thread_id ORDER BY r.ordinal DESC LIMIT 1),
           (SELECT count(q.thread_id) FROM orchestration_v2_projection_runtime_requests q
            WHERE q.thread_id = t.thread_id AND q.status = 'pending'),
           (SELECT q.kind FROM orchestration_v2_projection_runtime_requests q
            WHERE q.thread_id = t.thread_id AND q.status = 'pending'
            ORDER BY q.created_at DESC LIMIT 1)
    FROM {_T3_V2_TABLE} t
    JOIN projection_projects p ON p.project_id = t.project_id
    WHERE t.archived_at IS NULL AND t.deleted_at IS NULL
"""


def _t3_rows(path, deadline):
    """(v2, rows) of one T3 file: which generation it is (picked from sqlite_master), and its
    thread rows as that generation's query returns them."""
    db, v2, sql = _t3_open(path, deadline)
    try:
        return v2, db.execute(sql).fetchall()
    finally:
        db.close()


def _count(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _iso(value):
    """`value` when it is an ISO stamp, else None."""
    return value if parse_iso(value) is not None else None


def _thread_id(value):
    """`value` when it looks like a T3 thread id, else None: a malformed record can hold a whole
    object where an id belongs, and its JSON text must not reach the page."""
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value) else None


def _t3_v1_record(row):
    (tid, project, title, cwd, updated, provider, working, voice, approvals, inputs,
     override, settled_at, snoozed_until, snoozed_at, session, error, session_at, turn, completed) = row
    approvals, inputs = _count(approvals), _count(inputs)
    failed = turn in ("error", "failed") or session in ("error", "failed")
    return {"tid": tid, "project": project, "title": title, "cwd": cwd, "updated": updated,
            "provider": provider, "working": bool(working), "pending": approvals + inputs,
            "asks": "approval" if approvals else "input", "settled": override == "settled",
            "settled_at": settled_at, "snoozed_until": snoozed_until, "snoozed_at": snoozed_at,
            "run": "completed" if turn == "completed" else turn, "completed": completed,
            "failed": failed, "voice": str(voice or ""), "parent": None,
            # when it failed: the turn's end, or the session's status edge (T3's client wakes a
            # snoozed thread on a session error newer than the snooze by that stamp)
            "failed_at": max((completed if turn in ("error", "failed") else None,
                              session_at if session in ("error", "failed") else None),
                             key=stamp),
            "cause": redact(" ".join(str(error).split()))[:80] if failed and error else ""}


def _t3_v2_record(row):
    (tid, project, title, cwd, updated, provider, override, settled_at, snoozed_until, snoozed_at,
     relation, lineage_parent, subagent_parent, run, completed, pending, kind) = row
    kind = str(kind or "")
    return {"tid": tid, "project": project, "title": title, "cwd": cwd, "updated": updated,
            "provider": provider, "working": run in _T3_ACTIVE_RUNS, "pending": _count(pending),
            "asks": kind.replace("_", " ") if re.fullmatch(r"[a-z][a-z_-]{0,30}", kind) else "request",
            "settled": override == "settled", "settled_at": settled_at,
            "snoozed_until": snoozed_until, "snoozed_at": snoozed_at, "run": run,
            "completed": completed, "failed": run == "failed", "voice": "", "cause": "",
            "parent": _thread_id(lineage_parent if relation == "subagent" else None) or _thread_id(subagent_parent),
            "failed_at": completed}


def _t3_snoozed(rec, now):
    """Parked on its snooze: the wake time is still ahead and the thread has not raised its hand
    (a pending request, a failure newer than the snooze, or work that completed after it).
    Malformed stamps never hide a thread."""
    until = stamp(rec["snoozed_until"])
    if until <= now or rec["pending"]:
        return False
    at, done = stamp(rec["snoozed_at"]), stamp(rec["completed"])
    if rec["failed"] and (not at or stamp(rec["failed_at"]) > at):
        return False
    if rec["run"] == "completed" and at and done > at:
        return False
    return True


def _t3_state(rec, now):
    """(state, since) for one thread record; since is when it entered that state, best known."""
    if rec["pending"]:
        return "waiting", rec["updated"]
    if rec["working"]:
        return "working", rec["updated"]
    if rec["settled"]:
        return "settled", rec["settled_at"] or rec["updated"]
    if _t3_snoozed(rec, now):
        return "snoozed", rec["snoozed_at"] or rec["updated"]
    if rec["failed"]:
        return "failed", rec["failed_at"] or rec["completed"] or rec["updated"]
    return "ready", rec["completed"] or rec["updated"]


_T3_SAID = {"working": "working", "settled": "done", "snoozed": "on hold", "failed": "failed"}


def _t3_voice(rec, state, now=None):
    """The ship's one line in plain words: the thread's state and when ("finished 2 h ago", "needs you ·
    approval", "working"), never T3's own activity news ("Context window updated" is not news to a person)."""
    if state == "waiting":
        return "needs you · " + rec["asks"]
    when = stamp(rec["completed"] or rec["updated"]) if now else 0
    ago = (" " + _words.rel_time(now - when)) if when and now >= when else ""
    if state == "ready":
        return ("finished" + ago) if rec["run"] == "completed" else ("open" + (" · last change" + ago if ago else ""))
    if state == "failed":
        return "failed" + ago
    if state == "working" and ago:
        return "last change" + ago            # the state itself is said beside it ("working")
    return _T3_SAID.get(state, state)


def t3_ship(rec, now, app="T3 (v2)", v2=True):
    state, since = _t3_state(rec, now)
    c = ship("ship:t3:" + str(rec["tid"]), str(rec["title"] or rec["project"] or rec["tid"]), "t3",
             _T3_STATUS.get(state, "IDLE"), workspace_path(rec["cwd"]), _t3_voice(rec, state, now),
             provider=rec["provider"] or "", updated_at=rec["updated"], app=app,
             t3={"state": state, "since": _iso(since),
                 "settled_at": _iso(rec["settled_at"]) if state == "settled" else None,
                 "parent": str(rec["parent"]) if rec["parent"] else None,
                 "app": "T3 (v2)" if v2 else "T3", "open_url": None})
    if state == "failed":
        c["cause"] = rec["cause"] or ("run failed" if v2 else "turn failed")
    return c


def t3_live_path(path):
    """Prefer a newer sibling v2 store without changing the configured path."""
    path = Path(path)
    sibling = path.with_name(T3_V2_FILE)
    try:
        if path.name == "state.sqlite" and sibling.is_file() and (
                not path.exists() or sibling.stat().st_mtime > path.stat().st_mtime):
            return str(sibling)
    except OSError:
        pass
    return str(path)


def t3_ships(path, now, app="T3", all_threads=None):
    """The threads of one T3 file as ships, with a finding. Each ship carries
    ship["t3"] = {"state", "since", "settled_at", "parent", "app", "open_url"} (open_url is filled
    by build(), which knows the link). The finding's "_threads" lists every live thread id the file
    holds, drawn or not; build() takes it out before the finding is shown."""
    configured = str(path)
    path = t3_live_path(path)
    selected = " · selected newer statev2.sqlite beside state.sqlite" if path != configured else ""
    if os.path.basename(str(path)) == T3_V2_FILE:
        app = "T3 (v2)"
    if not os.path.isfile(path):
        return [], finding("t3", "ABSENT", app + " database missing")
    deadline = time.monotonic() + T3_BUDGET_S
    try:
        import sqlite3
    except ImportError:            # a Python built without _sqlite3: no T3 rows, and the map still draws
        return [], finding("t3", "ERROR", app + " unavailable: this Python has no sqlite3 module")
    try:
        v2, rows = _t3_rows(path, deadline)
        if v2:
            app = "T3 (v2)"
        out, listed, counts = [], [], {}
        for row in rows:
            rec = (_t3_v2_record if v2 else _t3_v1_record)(row)
            listed.append(str(rec["tid"]))
            state, since = _t3_state(rec, now)
            if all_threads is not None:
                all_threads.append(t3_ship(rec, now, app, v2))
            if state in ("settled", "snoozed") and stamp(rec["updated"]) < now - 86400:
                continue
            counts[state] = counts.get(state, 0) + 1
            # the thread by its own title (the folder it works in is where the map draws it, not part of its name)
            c = t3_ship(rec, now, app, v2)
            out.append(c)
        message = " · ".join([f"{app} · {len(out)} threads"] +
                             [f"{counts[s]} {s}" for s in _T3_STATES if counts.get(s)])
        state = finding("t3", "LIVE", message + selected, len(out))
        state["_threads"] = listed
        return out, state
    except (OSError, sqlite3.Error, ValueError) as exc:
        return [], finding("t3", "ERROR", app + " unavailable: " + str(exc)[:160])


def t3_environment_id(path):
    """The environment id a T3 server writes next to its database, or "" when there is none."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(path)), "environment-id"),
                  encoding="utf-8", errors="replace") as handle:
            text = handle.read(256).strip()
    except OSError:
        return ""
    return text if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", text) else ""


def t3_open_url(link, environment_id, thread_id):
    """The Bridge deep link of one thread on the T3 server at `link`, or None when any part is
    missing. Only a plain http(s) origin or path is accepted: the page puts the link in an href."""
    link = str(link or "").strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/?#\"'<>`\\]+(?:/[^\s?#\"'<>`\\]*)?", link, re.I) \
            or not environment_id or not thread_id:
        return None
    return (link + "/bridge?focus=" + quote(str(environment_id), safe="-._") + ":"
            + quote(str(thread_id), safe="-._"))


def _program(argv):
    """Recognize only executable/script positions and flags before the first prompt."""
    names = {"codex", "claude", "claude-worker", "claude-max", "threads", "threads.py"}
    if not argv:
        return ""
    index = 0
    base = os.path.basename(argv[0])
    if base in ("node", "python", "python3", "bash", "sh") and len(argv) > 1:
        index, base = 1, os.path.basename(argv[1])
    if base not in names:
        return ""
    args = argv[index + 1:]
    if base == "codex":
        # Global options may precede exec. Stop at the subcommand or positional prompt.
        values = {"-m", "--model", "-c", "--config", "-C", "--cd", "-p", "--profile",
                  "--sandbox", "--ask-for-approval", "--enable", "--disable"}
        i = 0
        while i < len(args):
            token = args[i]
            if token == "exec":
                return "codex exec"
            if not token.startswith("-") or token == "--":
                break
            i += 2 if token in values else 1
        return ""
    if base in ("claude", "claude-worker", "claude-max"):  # claude-max: old name, still a pass-through
        values = {"--model", "--effort", "--permission-mode", "--output-format", "--input-format",
                  "--system-prompt", "--append-system-prompt", "--settings", "--setting-sources",
                  "--allowedTools", "--disallowedTools", "--mcp-config", "--resume"}
        i = 0
        while i < len(args):
            token = args[i]
            if token in ("-p", "--print") or token.startswith("--print="):
                return base
            if not token.startswith("-") or token == "--":
                break
            i += 2 if token in values else 1
        return ""
    return "threads"


def live_run_pids(tasks_dir, newest=300):
    """Pids of the agent runs the task store started and has no exit receipt for yet: a
    thread's agent and its reviewer (tasks.py writes a `child` event when it starts one and an
    `exit` event when it ends). Reads those two kinds of line from each task's events.jsonl
    and nothing else: no prompt, no log."""
    pids = set()
    try:
        names = sorted(n for n in os.listdir(tasks_dir) if not n.startswith("."))[-newest:]
    except OSError:
        return pids
    for name in names:
        children, exited = {}, set()
        try:
            events = os.path.join(tasks_dir, name, "events.jsonl")
            with open(events, encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    pid = event.get("pid")
                    if event.get("ev") == "child" and isinstance(pid, int) \
                            and not isinstance(pid, bool):
                        children[event.get("n")] = pid
                    elif event.get("ev") == "exit":
                        exited.add(event.get("n"))
        except OSError:
            continue
        # an ended run's pid may have been reused by someone else
        pids.update(pid for n, pid in children.items() if n not in exited)
    return pids


def thread_filter(tasks_dir, worktrees_dir=""):
    """is_thread(pid, pgid, cwd) for process_ships: True for a process the task store owns,
    which the page already draws as that thread's task. A thread's agent leads its own
    process group, so its helpers count too; and anything working inside the store's own
    folder or the worktrees folder (a thread's worktree) belongs to a thread."""
    base = os.path.realpath(os.path.expanduser(str(tasks_dir))) if tasks_dir else ""
    if not base:
        return lambda pid, pgid, cwd: False
    wts = (os.path.realpath(os.path.expanduser(str(worktrees_dir))) if worktrees_dir
           else base + "-worktrees")
    pids = live_run_pids(base)
    return lambda pid, pgid, cwd: (pid in pids or pgid in pids or cwd == base
                                   or cwd.startswith(base + "/") or cwd == wts
                                   or cwd.startswith(wts + "/"))


def _pgrp(root):
    try:
        stat = (root / "stat").read_text(errors="replace")
        return int(stat[stat.rindex(")") + 2:].split()[2])        # state ppid pgrp ...
    except (OSError, ValueError, IndexError):
        return 0


def process_ships(proc_root="/proc", ps_rows=None, tasks_dir="", dropped=None, worktrees_dir=""):
    """Background agent processes as ships. With `tasks_dir`, those that belong to a thread
    (its agent or reviewer) are left out, because the thread is already drawn as a task;
    their pids go into `dropped`."""
    out, unreadable, threads = [], 0, 0
    is_thread = thread_filter(tasks_dir, worktrees_dir)
    deadline = time.monotonic() + 0.65
    try:
        if ps_rows is None:
            ps = subprocess.run(["ps", "-eo", "pid=,stat="], capture_output=True, text=True, timeout=0.4)
            if ps.returncode:
                raise OSError("ps failed")
            ps_rows = ps.stdout
        if not os.path.isdir(proc_root):
            raise OSError("proc filesystem missing")
        for line in ps_rows.splitlines():
            if time.monotonic() >= deadline:
                return out, finding("process", "PARTIAL", f"Background agents · {len(out)} processes · scan exceeded collection budget", len(out))
            fields = line.split()
            if len(fields) != 2 or not fields[0].isdigit() or fields[1].startswith("Z"):
                continue
            pid = int(fields[0])
            root = Path(proc_root) / str(pid)
            try:
                if root.stat().st_uid != os.getuid():
                    continue
                with (root / "cmdline").open("rb") as reader:
                    argv = reader.read(262144).decode(errors="replace").rstrip("\0").split("\0")
                program = _program(argv)
                if not program:
                    continue
                cwd = os.readlink(root / "cwd")
                if is_thread(pid, _pgrp(root), cwd):
                    threads += 1
                    if dropped is not None:
                        dropped.add(pid)
                    continue
                out.append(ship(f"ship:process:{pid}", program + " · " + (os.path.basename(cwd) or cwd),
                                "process", "WORKING", cwd, f"{program} running · pid {pid}", pid=pid))
            except FileNotFoundError:
                continue                    # exited between ps and /proc
            except OSError:
                unreadable += 1
        message = f"Background agents · {len(out)} processes"
        if threads:
            message += f" · {threads} more belong to threads"
        if unreadable:
            message += f" · {unreadable} proc entries unreadable"
        return out, finding("process", "PARTIAL" if unreadable else "LIVE", message, len(out))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [], finding("process", "ERROR", "Background agents unavailable: " + str(exc)[:160])


def deck_ships(synced_dir, machines, now):
    out, findings = [], []
    for machine in machines:
        if not isinstance(machine, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", machine) or machine in (".", ".."):
            continue
        path = Path(synced_dir) / machine / "world.json"
        status, voice, age = "ABSENT", "no news yet", None
        try:
            age = max(0, now - path.stat().st_mtime)
            if age > 86400:
                status = "STALE"
                voice = "no news since " + _words.since_words(path.stat().st_mtime, now)
            else:
                with path.open(encoding="utf-8") as handle:
                    text = handle.read(8 * 1024 * 1024 + 1)
                if len(text) > 8 * 1024 * 1024:
                    raise ValueError("deck exceeds 8 MiB")
                raw = json.loads(text)
                if not isinstance(raw, dict) or not isinstance(raw.get("chars"), list):
                    raise ValueError("expected a world with chars")
                working = sum(1 for c in raw["chars"] if isinstance(c, dict) and
                              c.get("kind") in ("seat", "ship") and c.get("status") == "WORKING")
                status = "WORKING" if working else "IDLE"
                voice = f"updated {_words.rel_time(age)} · {working} working"
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            status, voice = "ERROR", "its snapshot could not be read: " + str(exc)[:140]
        out.append(ship("ship:deck:" + machine, machine, "deck", status, voice=voice,
                        machine=machine, deck_age_s=age, tour=voice, cause=voice if status in ("ABSENT", "STALE", "ERROR") else ""))
        findings.append(finding("deck:" + machine, status, machine + " · " + voice, 1))
    return out, findings


def dock(ships, areas):
    paths = sorted((a for a in areas if a.get("kind") == "area" and a.get("path") and not a.get("alias_of")),
                   key=lambda a: -len(a["path"]))
    for c in ships:
        if c["cwd"]:
            area = next((a for a in paths if c["cwd"] == a["path"] or c["cwd"].startswith(a["path"].rstrip("/") + "/")), None)
            if area:
                c["docked_at"] = area["uid"]
                area.setdefault("ships", []).append(c["uid"])


def remote_ships(cfg, now):
    from remote_t3 import cache_path, valid
    out, findings = [], []
    for remote in cfg.get("t3_remote", []):
        if not valid(remote):
            continue
        machine = remote["machine"]
        try:
            data = json.loads(cache_path(cfg.get("work_root", ""), machine).read_text())
            if data.get("error"):
                raise ValueError(str(data["error"]))
            stale = now - float(data["at"]) > 120
            for row in data["rows"]:
                rec = _t3_v2_record(row)
                c = t3_ship(rec, now)
                c["uid"] = "ship:t3:" + machine + ":" + str(rec["tid"])
                c["machine"] = machine
                c["t3"]["id"] = str(rec["tid"])
                c["t3"]["open_url"] = t3_open_url(cfg.get("t3_link", ""),
                                                   data.get("environment_id", ""), rec["tid"])
                if stale:
                    c["status"] = "STALE"
                    c["t3"]["state"] = "ready"
                out.append(c)
            findings.append(finding("t3:" + machine, "STALE" if stale else "LIVE",
                                    machine + " metadata cache", len(data["rows"])))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            findings.append(finding("t3:" + machine, "ERROR", machine + ": " + str(exc)[:200]))
    return out, findings


def build(cfg, now, areas, dropped=None, threads=None):
    """All ships and their findings. `dropped`, when given, collects the pids of thread agents
    left out of the process ships (the caller still keeps them out of its process rows)."""
    ships, findings = [], []
    t3 = cfg.get("t3_state")
    link = cfg.get("t3_link", "")
    owner = {}          # thread id -> its ship (None: listed but not drawn) from an earlier database
    for i, path in enumerate([t3] if isinstance(t3, str) and t3 else t3 or []):
        # the first database is the T3 in use; any other is an older app still holding threads,
        # or a v2 server (t3_ships names that one "T3 (v2)" itself)
        all_rows = []
        new, state = t3_ships(path, now, "T3" if i == 0 else "old T3 app", all_rows)
        listed = state.pop("_threads", None) or [c["uid"][len("ship:t3:"):] for c in new]
        env = t3_environment_id(t3_live_path(path)) if link and new else ""
        machine = "local-windows" if str(path).startswith("/mnt/c/") else "local-wsl"
        if threads is not None:
            for c in all_rows:
                tid = c["uid"][len("ship:t3:"):]
                if tid not in owner:
                    c["machine"] = machine
                    c["t3"]["id"] = tid
                    c["t3"]["open_url"] = t3_open_url(link, env, tid) if env else None
                    threads.append(c)
        kept, twins = [], 0
        for c in new:
            tid = c["uid"][len("ship:t3:"):]
            facts = c.get("t3") or {}
            if env and facts.get("app") == "T3 (v2)":
                facts["open_url"] = t3_open_url(link, env, tid)
            if tid in owner:
                # the same thread in two databases (a v2 server seeded from a copy of the first):
                # the earlier database is the one in use and wins, with its own state AND its own
                # link (none for a v1 app). The copy's link is never lent: a settle there would not
                # change the state shown here, so the contact would stay (or a settle here would
                # hide an open thread over there). List the copy first to make it the one in use.
                twins += 1
                continue
            kept.append(c)
        for tid in listed:
            owner.setdefault(str(tid), None)
        for c in kept:
            owner[c["uid"][len("ship:t3:"):]] = c
        if twins:
            state["message"] = str(state.get("message", "")) + \
                f" · {twins} also in an earlier T3 database, which wins"
            state["count"] = len(kept)
        ships.extend(kept); findings.append(state)
    remote, states = remote_ships(cfg, now)
    ships.extend(remote); findings.extend(states)
    if threads is not None:
        threads.extend(remote)
    if cfg.get("agent_processes"):
        new, state = process_ships(tasks_dir=cfg.get("tasks_dir") or "", dropped=dropped,
                                   worktrees_dir=cfg.get("worktrees_dir") or "")
        ships.extend(new); findings.append(state)
    if cfg.get("synced_dir") and cfg.get("ship_machines"):
        new, states = deck_ships(cfg["synced_dir"], cfg["ship_machines"], now)
        ships.extend(new); findings.extend(states)
    dock(ships, areas)
    return ships, findings
