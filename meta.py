#!/usr/bin/env python3
"""meta.py — the meta thread: one chat that sees every map thread, T3 thread, tmux seat and git
worktree and says what to do with each.

The judgement is information, not code: the verdict rules live in playbooks/meta.md, which the
owner edits. This module gathers the facts (the world and thread list the page already shows,
plus read-only git for the worktrees), builds the prompt, runs the model the wingmate runs, and
reads the lines it answers with. It writes nothing and changes nothing: merging, closing,
renaming and removing a worktree stay the owner's clicks, drawn by the page from parse().
"""
from __future__ import annotations

import calendar
import os
import re
import shlex
import subprocess
import threading
import time

import tasks as _tasks
import wingmate as _mate

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYBOOK = os.path.join(HERE, "playbooks", "meta.md")
MAX_PLAYBOOK = 40000
DEFAULT_QUESTION = "Triage everything."     # what Triage all asks; the playbook names these words
CHAT_TURNS = 6
MAX_REPOS = 8
BUDGET_S = 20                 # seconds the worktree facts may take in all
CALL_S = 10                   # and one git call
MAX_WORKTREES = 150           # linked worktrees read in all
DIRTY_CAP = 200               # changed files counted per worktree; more reads as this many
MAX_CONTEXT = 60000
NOTE_ROOM = 400               # kept free for the note about what was left out
MAX_LINE = 700
MAX_ANSWER = 40000
MIN_SECONDS = 300             # the model has at least this long: it reads a whole map
MAX_ROWS = 300
MAX_ACTIONS = 60
MAX_MISSING = 20
MAX_REJECTED = 30
MAX_SUMMARY = 4000
MAX_REASON = 160
MAX_TITLE = 90
MAX_SEND = 4000
SHA_RE = re.compile(r"[0-9a-f]{7,64}")
# the words a verdict may use, by what kind of item it is on
VERDICTS = {
    "task": ("merge", "check", "follow-up", "set-aside", "stop", "rename", "keep"),
    "t3": ("close", "follow-up", "keep"),
    "seat": ("go", "keep"),
    "wt": ("retire", "land", "keep"),
}
ALIASES = {"followup": "follow-up", "setaside": "set-aside"}
BULLET = r"[\s>*·•-]*"        # what wingmate.plain leaves before a line (a list bullet becomes ·)
VERDICT_RE = re.compile(r"^" + BULLET + r"VERDICT\s+(\S.*?)\s*::\s*"
                        r"([A-Za-z][A-Za-z _-]{1,20}?)\s*::\s*(\S.*?)\s*$")
SEND_RE = re.compile(r"^" + BULLET + r"SEND\s+(\S.*?)\s*::\s*(\S.*?)\s*$")
RENAME_RE = re.compile(r"^" + BULLET + r"RENAME\s+(\S.*?)\s*::\s*(\S.*?)\s*$")
MISSING_RE = re.compile(r"^" + BULLET + r"MISSING\s+(\S.*?)\s*$")


# ---- the playbook ---------------------------------------------------------------------------
def load_playbook(path: str | None = None) -> str:
    """The playbook as it is on disk now (the owner edits it between runs). OSError if missing."""
    with open(path or PLAYBOOK, encoding="utf-8", errors="replace") as handle:
        text = handle.read(MAX_PLAYBOOK + 1)
    if not text.strip():
        raise OSError("the playbook is empty")
    return text[:MAX_PLAYBOOK]


# ---- bounded child processes ----------------------------------------------------------------
MAX_GIT_BYTES = 512 * 1024    # what one git call may print before it is stopped
MAX_ERR_BYTES = 64 * 1024     # and what any child may write to stderr
MAX_ANSWER_BYTES = 4 * MAX_ANSWER     # the model's stdout: MAX_ANSWER characters of 4 bytes


class Bounded(subprocess.CompletedProcess):
    """A finished child whose output was held under a byte limit. `truncated` is True when it
    was killed for writing more than that."""
    truncated = False


def _kill(proc) -> None:
    try:                                # the child leads its own group: a wrapper's children go too
        os.killpg(proc.pid, 9)
    except (OSError, AttributeError):
        try:
            proc.kill()
        except OSError:
            pass


def bounded_run(argv, cwd=None, timeout=None, env=None, input=None, max_out: int | None = None,
                max_err: int | None = None, **_ignored) -> Bounded:
    """subprocess.run that never holds more than `max_out` bytes of stdout (default MAX_GIT_BYTES)
    and `max_err` of stderr (MAX_ERR_BYTES): the child is killed the moment it writes past either,
    and the result says so (`truncated`). `input` (text) goes to its stdin. One deadline covers
    the child AND its output pipes: a wrapper that exits and leaves a worker holding the pipes
    open is as late as one that never exits. Past `timeout` seconds the child's whole process
    group is killed and TimeoutExpired raised, as subprocess.run does. Output is decoded as
    UTF-8, bad bytes replaced."""
    max_out = MAX_GIT_BYTES if max_out is None else max_out
    max_err = MAX_ERR_BYTES if max_err is None else max_err
    proc = subprocess.Popen(argv, cwd=cwd, env=env, start_new_session=True,
                            stdin=subprocess.DEVNULL if input is None else subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    held = {"out": bytearray(), "err": bytearray()}
    over, reaped = threading.Event(), threading.Event()

    def pump(pipe, key, limit):
        try:
            while True:
                data = os.read(pipe.fileno(), 65536)
                if not data:
                    return
                room = limit - len(held[key])
                held[key] += data[:max(room, 0)]
                if len(data) > room:
                    over.set()
                    if not reaped.is_set():     # never signal a group whose leader was reaped
                        _kill(proc)
                    return
        except (OSError, ValueError):
            return

    def feed():
        try:
            proc.stdin.write(input.encode("utf-8", "replace"))
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass

    readers = [threading.Thread(target=pump, args=(proc.stdout, "out", max_out), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err", max_err), daemon=True)]
    threads = readers + ([threading.Thread(target=feed, daemon=True)] if input is not None else [])
    for thread in threads:
        thread.start()
    deadline = None if timeout is None else time.monotonic() + timeout

    def left():
        return None if deadline is None else max(0.0, deadline - time.monotonic())

    expired = False
    for thread in readers:                      # EOF on both pipes: the child and whatever it left
        thread.join(left())
        if thread.is_alive():
            expired = True
            break
    if not expired:
        try:
            proc.wait(timeout=left())
        except subprocess.TimeoutExpired:
            expired = True
    if expired or over.is_set():
        _kill(proc)                             # the leader is not reaped yet: its group is ours
    for thread in threads:
        thread.join(2)
    reaped.set()
    proc.wait()
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe:
                pipe.close()
        except OSError:
            pass
    if expired and not over.is_set():
        raise subprocess.TimeoutExpired(argv, timeout)
    done = Bounded(argv, proc.returncode, held["out"].decode("utf-8", "replace"),
                   held["err"].decode("utf-8", "replace"))
    done.truncated = over.is_set()
    return done


# ---- worktrees: read-only git ---------------------------------------------------------------
def _call(runner, args: list[str], cwd: str, timeout: float, env: dict):
    """One read-only git call: (returncode, stdout (stderr when it failed), cut). `cut` is True
    when the runner stopped it for printing too much. `--no-optional-locks` keeps status from
    refreshing the index, and fsmonitor is off (it runs a command named in the config). A call
    that cannot start or runs out of time is (124, why, False)."""
    try:
        got = runner(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args], cwd=cwd,
                     capture_output=True, text=True, errors="replace", timeout=timeout,
                     stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, str(exc), False
    text = (got.stdout if got.returncode == 0 else got.stderr or got.stdout) or ""
    return got.returncode, text, bool(getattr(got, "truncated", False))


def parse_worktrees(out: str) -> list[dict]:
    """`git worktree list --porcelain -z` as [{path, head, branch, detached, bare, locked,
    prunable}]; the first is the repo's main worktree. The fields end in NUL and a record in an
    empty field, so a path holding a newline stays one path (it is refused later, by clean_path)."""
    rows, cur = [], None
    for field in out.split("\0"):
        if not field:
            cur = None
            continue
        key, _, value = field.partition(" ")
        if key == "worktree":
            cur = {"path": value, "head": "", "branch": None, "detached": False, "bare": False,
                   "locked": False, "prunable": False}
            rows.append(cur)
        elif cur is None:
            continue
        elif key == "HEAD":
            cur["head"] = value
        elif key == "branch":
            cur["branch"] = value[len("refs/heads/"):] if value.startswith("refs/heads/") else value
        elif key in ("detached", "bare", "locked", "prunable"):
            cur[key] = True
    return rows


def clean_path(path) -> bool:
    """An absolute path with no control character: the only kind that can be a ref or a cwd."""
    return (isinstance(path, str) and path.startswith("/")
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in path))


def owners_of(rows) -> dict:
    """{real path of a thread's worktree: the id of the newest thread that has it}. Only a real
    task id counts: task.json is written by agents."""
    out: dict = {}
    for t in rows or []:
        wt = t.get("worktree") if isinstance(t, dict) else None
        if isinstance(wt, str) and wt and _is_task_id(t.get("id")):
            out.setdefault(os.path.realpath(wt), t["id"])
    return out


def repos_of(rows, home: str = HERE) -> list[str]:
    """The folders to read worktrees of: the map's own repo (the folder this tool sits in, when it
    is a git work tree), then the repo of each map thread, each once."""
    out: list[str] = []
    if os.path.exists(os.path.join(home, ".git")):
        out.append(home)
    for t in rows or []:
        repo = t.get("repo") if isinstance(t, dict) else None
        if clean_path(repo) and repo not in out:
            out.append(repo)
    return out


def _count(status: str) -> int:
    return min(DIRTY_CAP, sum(1 for line in status.splitlines() if line.strip()))


def gather_worktrees(repos, budget_s: float = BUDGET_S, cap: int = MAX_WORKTREES,
                     runner=bounded_run, owners: dict | None = None,
                     clock=time.monotonic) -> dict:
    """Facts about the git worktrees of `repos` (folders in a repo, any worktree of it). Per repo
    (found once, by its main worktree; at most MAX_REPOS): base = the branch checked out in the
    main worktree, and for every linked worktree its path, branch, head, ahead/behind the base,
    changed-file count (capped), last commit date and subject, and `owner`: the thread id whose
    worktree it is (`owners`: {real path: id}), else None. Read-only git only, each call held
    under MAX_GIT_BYTES of output. A worktree whose path holds a control character is left out
    and counted. Stops at `budget_s` seconds or `cap` worktrees and says so:
    {repos, truncated, notes, seconds}."""
    started = clock()
    deadline = started + budget_s
    env = _tasks.agent_env()
    owners = owners or {}
    out: dict = {"repos": [], "truncated": False, "notes": [], "seconds": 0.0}
    seen: set = set()
    linked = skipped = refused = 0

    def git(args, cwd):
        left = deadline - clock()
        if left <= 0:
            out["truncated"] = True
            return None
        return _call(runner, args, cwd, min(CALL_S, left), env)

    for candidate in repos or []:
        if len(out["repos"]) >= MAX_REPOS:
            out["truncated"] = True
            out["notes"].append(f"only the first {MAX_REPOS} repos were read")
            break
        got = git(["worktree", "list", "--porcelain", "-z"], candidate)
        if got is None:
            break
        rows = parse_worktrees(got[1]) if got[0] == 0 and not got[2] else []
        if not rows or not clean_path(rows[0]["path"]):
            old_git = "unknown switch" in got[1]
            why = ("the worktree list was too long to read" if got[2] else
                   "git must be 2.36 or newer for worktree list -z" if old_git else
                   " ".join(got[1].split())[:200] or "git listed no usable worktree")
            out["repos"].append({"repo": candidate, "base": None, "main": None,
                                 "worktrees": [], "error": why})
            continue
        main = rows[0]
        if os.path.realpath(main["path"]) in seen:
            continue
        seen.add(os.path.realpath(main["path"]))
        base = main["branch"]
        mstatus = git(["status", "--porcelain"], main["path"])
        repo = {"repo": main["path"], "base": base, "error": None, "worktrees": [],
                "main": {"path": main["path"], "branch": base, "head": main["head"][:12],
                         "dirty": _dirty(mstatus)[0]}}
        out["repos"].append(repo)
        if not base:
            out["notes"].append(f"{_q(main['path'], 200)}: no branch is checked out in the main "
                                "worktree, so there is no base to compare with")
        for row in rows[1:]:
            if not clean_path(row["path"]):
                refused += 1
                continue
            if clock() >= deadline:
                out["truncated"] = True
            if linked >= cap and not out["truncated"]:
                out["truncated"] = True
                out["notes"].append(f"only the first {cap} worktrees were read")
            if out["truncated"]:
                skipped += 1
                continue
            linked += 1
            repo["worktrees"].append(_worktree(git, row, main, base, owners))
    if refused:
        out["notes"].append(f"{refused} worktree{'s' if refused != 1 else ''} with a control "
                            "character in the path left out")
    if skipped:
        out["notes"].append(f"worktree facts stopped at the {int(budget_s)} s budget or the "
                            f"{cap} worktree cap: {skipped} not read")
    elif out["truncated"] and not out["notes"]:
        out["notes"].append(f"worktree facts stopped at the {int(budget_s)} s budget")
    out["seconds"] = round(clock() - started, 1)
    return out


def _dirty(got):
    """(changed-file count or None, capped) from a `status --porcelain` call: a call stopped for
    printing too much has more than DIRTY_CAP files."""
    if got and got[2]:
        return DIRTY_CAP, True
    if got and got[0] == 0:
        n = _count(got[1])
        return n, n >= DIRTY_CAP
    return None, False


def _worktree(git, row: dict, main: dict, base, owners: dict) -> dict:
    head = row["head"] if SHA_RE.fullmatch(row["head"]) else ""
    item = {"path": row["path"], "branch": row["branch"], "head": head[:12],
            "detached": row["detached"], "locked": row["locked"], "prunable": row["prunable"],
            "ahead": None, "behind": None, "dirty": None, "dirty_capped": False, "date": "",
            "subject": "", "owner": owners.get(os.path.realpath(row["path"]))}
    tip = main["head"] if SHA_RE.fullmatch(main["head"]) else ""
    if base and tip and head:             # commits only the base has (behind), only the branch has
        got = git(["rev-list", "--left-right", "--count", f"{tip}...{head}"], main["path"])
        parts = got[1].split() if got and got[0] == 0 and not got[2] else []
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            item["behind"], item["ahead"] = int(parts[0]), int(parts[1])
    item["dirty"], item["dirty_capped"] = _dirty(git(["status", "--porcelain"], row["path"]))
    if head:
        got = git(["log", "-1", "--format=%ct%x1f%s", head], main["path"])
        ok = got and got[0] == 0 and not got[2]
        when, _, subject = got[1].strip().partition("\x1f") if ok else ("", "", "")
        if when.isdigit():
            item["date"], item["subject"] = _utc(int(when)), subject
    return item


# ---- the facts as text ----------------------------------------------------------------------
def _q(value, limit: int) -> str:
    return _mate._quote(value, limit)


def _n(value) -> str:
    return "?" if value is None else str(value)


# A status, a runner or a model name is shown as it is when it is a plain word, and quoted when it
# is anything else: task.json, the thread's events and the T3 database are all written by agents,
# so no field of them may reach the CONTEXT as a line of its own.
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
EVIDENCE = ("none", "verified", "checked", "verified and checked")


def _tok(value, allowed=None) -> str:
    text = "" if value is None else value if isinstance(value, str) else str(value)
    if allowed is not None:
        return text if text in allowed else _q(text, 40)
    return text if TOKEN_RE.fullmatch(text) else _q(text, 64)


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _is_task_id(value) -> bool:
    return isinstance(value, str) and bool(_tasks.TASK_ID_RE.fullmatch(value))


def _ref_ok(tail) -> bool:
    """What follows `t3:`, `seat:` or `wt:` in a ref: one printable line, with no `::` (the
    separator of the answer's lines)."""
    return (isinstance(tail, str) and 0 < len(tail) <= 300 and tail.isprintable()
            and "::" not in tail)


def _utc(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _age(iso, now: float):
    try:
        then = calendar.timegm(time.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(0, int(now - then))


def _span(seconds) -> str:
    if seconds is None:
        return "?"
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h" if seconds < 86400 else f"{seconds // 86400}d"


def _family(runner, model, runners) -> str:
    """The model family, worked out from the runner and the model as the thread engine does it
    (a model name such as "vendor/sonnet" or "Claude Sonnet" counts as much as a plain one). The
    one part of the answer that can be agent text, the name of a runner nothing knows
    ("runner:<name>"), is shown as a plain word or quoted."""
    family = _tasks.family_of(runner, runners, model)
    prefix = "runner:"
    if not family.startswith(prefix):
        return _tok(family)
    return prefix + _tok(family[len(prefix):]) if family[len(prefix):] else "unknown"


def _who(runner, model, runners) -> str:
    """`runner/model (family f)`: a name that is a plain word as it is, any other quoted."""
    name = _tok(runner) if runner else "?"
    return f"{name}{'/' + _tok(model) if model else ''} (family {_family(runner, model, runners)})"


def _task_item(t: dict, now: float, runners, wt: dict | None) -> str:
    bits = ["task:" + str(t.get("id")), _q(t.get("title"), MAX_TITLE),
            "status " + _tok(t.get("status"))]
    if t.get("status") == "failed" and t.get("retry_due"):
        bits.append("a retry is due")
    if t.get("runner"):
        bits.append("by " + _who(t.get("runner"), t.get("model"), runners))
    bits.append(f"created {_span(_age(t.get('created'), now))} ago")
    if t.get("status") in ("running", "checking", "stopping"):
        bits.append(f"running {_span(_age(t.get('started'), now))}")
    elif t.get("ended"):
        bits.append(f"ended {_span(_age(t.get('ended'), now))} ago")
    bits.append(f"runs {_int(t.get('runs'))}")
    if t.get("branch"):
        bits.append(f"branch {_q(t.get('branch'), 60)} → {_q(t.get('base_branch'), 40)}")
    if wt:
        bits.append(f"its worktree ahead {_n(wt['ahead'])} behind {_n(wt['behind'])} "
                    f"dirty {_n(wt['dirty'])}")
    elif t.get("worktree") and t.get("status") not in _tasks.CLOSED:
        bits.append("its worktree was not read")
    bits.append("evidence " + _tok(t.get("evidence") or "none", EVIDENCE))
    if t.get("verify_cmd"):
        v = t.get("verify") if isinstance(t.get("verify"), dict) else {}
        rc = v.get("rc")
        bits.append("verify " + ("not run yet" if rc is None else
                                 f"exit {rc}" if isinstance(rc, int) else "exit ?"))
    last = t.get("last_verdict") if isinstance(t.get("last_verdict"), dict) else None
    if last:
        who = _who(last.get("runner"), last.get("model"), runners)
        bits.append(f"last check {_tok(last.get('verdict'))} by {who}"
                    f"{', outdated' if last.get('stale') else ''}")
    else:
        bits.append("never checked")
    blockers = [b for b in (t.get("merge_blockers") or []) if isinstance(b, str)][:4]
    bits.append("merge_blockers " + ("[" + ", ".join(_q(b, 120) for b in blockers) + "]"
                                     if blockers else "none"))
    if t.get("note"):
        bits.append("note " + _q(t.get("note"), 160))
    return " · ".join(bits)[:MAX_LINE]


def _t3_item(c: dict, now: float) -> str:
    t3 = c.get("t3") if isinstance(c.get("t3"), dict) else {}
    bits = ["t3:" + str(c.get("uid"))[len("ship:t3:"):], _q(c.get("name"), 90),
            "state " + _tok(t3.get("state") or c.get("status") or "?")]
    if _age(t3.get("since"), now) is not None:
        bits.append(f"since {_span(_age(t3.get('since'), now))} ago")
    if c.get("cwd"):
        bits.append("cwd " + _q(c.get("cwd"), 120))
    bits.append("parent " + (_q(t3.get("parent"), 40) if t3.get("parent") else "none"))
    if c.get("cause"):
        bits.append("cause " + _q(c.get("cause"), 120))
    if c.get("pane"):
        bits.append("says " + _q(c.get("pane"), 100))
    bits.append("open link " + ("yes" if t3.get("open_url") else "no"))
    return " · ".join(bits)[:MAX_LINE]


def _seat_item(c: dict) -> str:
    bits = [str(c.get("uid")), _q(c.get("alias") or c.get("name"), 40),
            "status " + _tok(c.get("status") or "?")]
    if c.get("cwd"):
        bits.append("cwd " + _q(c.get("cwd"), 120))
    bits.append("shows " + _q(c.get("line") or c.get("pane"), 100))
    return " · ".join(bits)[:MAX_LINE]


def _wt_item(w: dict, owner_status, now: float) -> str:
    bits = ["wt:" + w["path"], "branch " + (_q(w["branch"], 60) if w["branch"] else "(detached)"),
            f"ahead {_n(w['ahead'])} behind {_n(w['behind'])}",
            "dirty " + _n(w["dirty"]) + ("+" if w["dirty_capped"] else "")]
    if w["date"]:
        bits.append(f"last commit {_span(_age(w['date'], now))} ago " + _q(w["subject"], 100))
    state = _tok(owner_status) if owner_status else "not on the list"
    bits.append(f"owner task:{_tok(w['owner'])} ({state})" if w["owner"] else "owner none")
    if w["locked"]:
        bits.append("locked")
    if w["prunable"]:
        bits.append("git says its folder is gone (prunable)")
    return " · ".join(bits)[:MAX_LINE]


def retire_command(repo: str, path: str) -> str:
    """The line the owner runs to retire a worktree; its branch stays. Shown, never run."""
    return f"git -C {shlex.quote(repo)} worktree remove {shlex.quote(path)}"


def _agents_line(world: dict, runners) -> str:
    rows = [r for r in (world.get("task_runners") or []) if isinstance(r, dict) and r.get("name")]
    if not rows and isinstance(runners, dict):
        rows = [{"name": k, "role": v.get("role"), "models": v.get("models")}
                for k, v in runners.items() if isinstance(v, dict)]
    parts = []
    for r in rows[:12]:
        role = {"check": "checks only", "work": "works only"}.get(r.get("role"), "works and checks")
        models = ", ".join(_tok(m.get("name")) for m in (r.get("models") or [])
                           if isinstance(m, dict))[:80]
        family = _family(r["name"], r.get("default_model"), runners)
        parts.append(f"{_tok(r['name'])} ({role}; family {family}"
                     f"{'; models ' + models if models else ''})")
    return "AGENTS (the names CHECK and DRAFT use): " + ("; ".join(parts) or "none known")


# what goes first when the text is over its cap (lower ranks first), and what it is called then
RANKS = {0: "closed map threads", 1: "settled T3 threads", 2: "idle seats", 3: "T3 threads",
         4: "worktrees", 5: "open map threads", 6: "seats that need the owner"}


def build(world: dict, rows, worktrees: dict | None, now: float, runners=None,
          cap: int = MAX_CONTEXT) -> dict:
    """The CONTEXT: {text, refs, trimmed, left_out, counts}. `refs` maps every ref in the text to
    what the page needs to draw its row (kind, id, title, state ...); parse() accepts only those.
    An item whose id or path is not a plain printable line is left out and counted (`left_out`).
    Past `cap` characters, closed threads and settled T3 threads go first (oldest first) and the
    text says what was left out."""
    world = world if isinstance(world, dict) else {}
    rows = [t for t in (rows or []) if isinstance(t, dict)]
    left_out = {"map threads": sum(1 for t in rows if not _is_task_id(t.get("id"))),
                "T3 threads": 0, "seats": 0, "worktrees": 0}
    rows = [t for t in rows if _is_task_id(t.get("id"))]
    chars = [c for c in (world.get("chars") or []) if isinstance(c, dict) and not c.get("remote")]
    wts = worktrees if isinstance(worktrees, dict) else {"repos": [], "truncated": False}
    by_path = {os.path.realpath(w["path"]): w
               for r in wts.get("repos", []) for w in r.get("worktrees", [])}
    status_of = {t["id"]: t.get("status") for t in rows}
    items = []              # (rank, age, section, ref, line, meta)
    for t in rows:
        path = t.get("worktree") if isinstance(t.get("worktree"), str) else ""
        wt = by_path.get(os.path.realpath(path)) if path else None
        closed = t.get("status") in _tasks.CLOSED
        items.append((0 if closed else 5, _age(t.get("ended") or t.get("created"), now) or 0,
                      "task", "task:" + t["id"], _task_item(t, now, runners, wt),
                      {"kind": "task", "id": t["id"], "title": str(t.get("title") or t["id"]),
                       "status": t.get("status"), "runner": t.get("runner"),
                       "model": t.get("model")}))
    for c in chars:
        uid = str(c.get("uid") or "")
        if (uid.startswith("ship:t3:") and len(uid) > len("ship:t3:")) and not _ref_ok(uid[8:]):
            left_out["T3 threads"] += 1
        elif uid.startswith("seat:") and c.get("kind") == "seat" and not _ref_ok(uid[5:]):
            left_out["seats"] += 1
        elif uid.startswith("ship:t3:") and len(uid) > len("ship:t3:"):
            t3 = c.get("t3") if isinstance(c.get("t3"), dict) else {}
            quiet = t3.get("state") in ("settled", "snoozed")
            tid = uid[len("ship:t3:"):]
            items.append((1 if quiet else 3, _age(t3.get("since"), now) or 0, "t3", "t3:" + tid,
                          _t3_item(c, now),
                          {"kind": "t3", "id": tid, "title": str(c.get("name") or uid),
                           "status": t3.get("state") or c.get("status"), "uid": uid,
                           "open_url": t3.get("open_url") or None}))
        elif c.get("kind") == "seat" and uid.startswith("seat:") and len(uid) > len("seat:"):
            needs = str(c.get("status") or "").upper() in ("WAITING", "DRAFT", "LIMITED", "ERROR")
            items.append((6 if needs else 2, 0, "seat", uid, _seat_item(c),
                          {"kind": "seat", "id": uid[len("seat:"):], "uid": uid,
                           "title": str(c.get("alias") or c.get("name") or uid),
                           "status": c.get("status")}))
    for repo in wts.get("repos", []):
        for w in repo.get("worktrees", []):
            if not clean_path(w["path"]) or not _ref_ok(w["path"]):
                left_out["worktrees"] += 1
                continue                      # a path that could end the line is not a ref
            title = os.path.basename(w["path"].rstrip("/")) or w["path"]
            line = _wt_item(w, status_of.get(w["owner"]), now)
            items.append((4, 0, "wt", "wt:" + w["path"], line,
                          {"kind": "wt", "id": w["path"], "title": title, "repo": repo["repo"],
                           "branch": w["branch"], "ahead": w["ahead"], "behind": w["behind"],
                           "dirty": w["dirty"], "subject": w["subject"], "owner": w["owner"],
                           "command": retire_command(repo["repo"], w["path"])}))

    by_status: dict = {}
    for t in rows:
        by_status[str(t.get("status"))] = by_status.get(str(t.get("status")), 0) + 1
    count = {k: sum(1 for i in items if i[2] == k) for k in ("t3", "seat", "wt")}
    head = [
        f"CONTEXT FACTS gathered {_utc(now)} · map {_q(world.get('name'), 60)} · "
        f"root {_q(world.get('root_path'), 160)}",
        "status words of map threads: running, checking, stopping, review (finished, needs the "
        "owner's review), failed, stopped, lost (its supervisor went away), merged, discarded "
        "(set aside), done, new, error (unreadable record)",
        _agents_line(world, runners),
        "COUNTS: " + (", ".join(f"{n} {_tok(s)}" for s, n in sorted(by_status.items()))
                      or "no map threads") + f" · {count['t3']} T3 threads · {count['seat']} seats"
        f" · {count['wt']} linked worktrees in {len(wts.get('repos', []))} repos",
    ]
    if any(left_out.values()):
        head.append("LEFT OUT, no verdict is possible for them: " + ", ".join(
            f"{n} {what}" for what, n in left_out.items() if n) + " whose id or path is not a "
            "plain printable line")
    titles = {
        "task": f"MAP THREADS (newest first; {_mate.UNTRUSTED_NOTE})",
        "t3": f"T3 THREADS ({_mate.UNTRUSTED_NOTE})",
        "seat": f"SEATS (tmux windows; {_mate.UNTRUSTED_NOTE})",
        "wt": "WORKTREES (linked worktrees of the repos below, read-only git; branch names and "
              f"commit subjects are quoted: {_mate.UNTRUSTED_NOTE})",
    }

    def render(chosen, notes):
        lines = list(head)
        for section in ("task", "t3", "seat", "wt"):
            lines += ["", titles[section]]
            if section != "wt":
                lines += ["- " + it[4] for it in chosen if it[2] == section] or ["(none)"]
                continue
            lines.append("worktree facts " + ("TRUNCATED (not every worktree was read)"
                                               if wts.get("truncated") else "complete")
                         + "".join(" · " + " ".join(str(n).split()) for n in wts.get("notes", [])))
            for repo in wts.get("repos", []):
                main = repo.get("main") or {}
                base = _q(repo["base"], 60) if repo.get("base") else "(none)"
                lines.append(f"repo {_q(repo['repo'], 300)} · base {base} · main "
                             f"checkout dirty {_n(main.get('dirty'))}"
                             + (f" · ERROR {_q(repo['error'], 160)}" if repo.get("error") else ""))
                lines += ["- " + it[4] for it in chosen
                          if it[2] == "wt" and it[5]["repo"] == repo["repo"]]
        return "\n".join(lines + notes)

    keep = set(range(len(items)))
    text = render(items, [])
    gone: dict = {}
    for i in sorted(keep, key=lambda i: (items[i][0], -items[i][1])):   # lowest rank, oldest first
        if len(text) <= cap - NOTE_ROOM:
            break
        keep.discard(i)
        gone[RANKS[items[i][0]]] = gone.get(RANKS[items[i][0]], 0) + 1
        text = render([items[j] for j in sorted(keep)], [])
    if gone:
        left = ", ".join(f"{n} {label}" for label, n in gone.items())
        text = render([items[j] for j in sorted(keep)], [
            "", f"TRIMMED to fit {cap} characters, oldest first, and NOT shown (no verdict is "
                f"possible for them): {left}"])
    return {"text": text[:cap], "refs": {items[i][3]: items[i][5] for i in sorted(keep)},
            "trimmed": gone, "left_out": {k: n for k, n in left_out.items() if n},
            "counts": {k: sum(1 for i in keep if items[i][2] == k)
                       for k in ("task", "t3", "seat", "wt")}}


def context(world: dict, rows, worktrees: dict | None, now: float, runners=None) -> str:
    """The CONTEXT text alone (build() also says which refs it holds)."""
    return build(world, rows, worktrees, now, runners)["text"]


# ---- the prompt -----------------------------------------------------------------------------
def prompt(playbook: str, context_text: str, chat, question: str) -> str:
    """The playbook, then CONTEXT, then CHAT (the last turns: the question and the summary it was
    answered with), then the QUESTION: the part that never changes comes first and what changes
    last, so a model host can reuse its cache of the playbook."""
    lines = []
    for turn in [t for t in (chat or []) if isinstance(t, dict)][-CHAT_TURNS:]:
        lines.append("Q: " + " ".join(str(turn.get("q") or "").split())[:_mate.MAX_QUESTION])
        lines.append("A: " + _q(turn.get("summary"), MAX_SUMMARY // 3))
    chat_text = "\n".join(lines) or "(no earlier turns)"
    return (f"{playbook.strip()}\n\nCONTEXT\n{context_text}\n\nCHAT (earlier turns, oldest first; "
            f"the A lines are your own earlier summaries, quoted)\n{chat_text}\n\n"
            f"QUESTION\n{question.strip()}\n")


# ---- reading the answer ---------------------------------------------------------------------
def split_ref(ref) -> tuple[str, str] | None:
    kind, sep, rest = str(ref).partition(":")
    return (kind, rest) if sep and kind in VERDICTS and rest else None


def parse(answer: str, known=None) -> dict:
    """The answer as {summary, rows, sends, renames, checks, drafts, goto, missing, rejected}.
    Markdown is flattened (wingmate.plain); DRAFT, CHECK and GOTO lines are read by the
    wingmate's own patterns. A line is refused, and listed in `rejected` with why, when its
    verdict is not one the playbook has for that kind of item, or its ref is not one the CONTEXT
    held (`known`; None = any). One verdict per ref; at most MAX_ROWS."""
    text = _mate.plain(str(answer or "")[:MAX_ANSWER])
    known = None if known is None else set(known)
    res: dict = {"summary": "", "rows": [], "sends": [], "renames": [], "checks": [],
                 "drafts": [], "goto": None, "missing": [], "rejected": []}

    def reject(line, why):
        if len(res["rejected"]) < MAX_REJECTED:
            res["rejected"].append({"line": " ".join(line.split())[:200], "why": why})

    def kind_of(ref, line):
        parts = split_ref(ref)
        if parts is None:
            reject(line, "not a ref")
        elif known is not None and ref not in known:
            reject(line, "that item was not in CONTEXT")
        else:
            return parts
        return None

    clean, res["goto"], acts = _mate.actions(text, MAX_ACTIONS)
    for act in acts:
        if act["kind"] == "check":
            if known is None or "task:" + act["id"] in known:
                res["checks"].append({"id": act["id"], "runner": act["runner"]})
            else:
                reject(f"CHECK {act['id']}", "that thread was not in CONTEXT")
        elif act["path"].startswith("/"):
            res["drafts"].append(act)
        else:
            reject("DRAFT " + act["path"], "a draft names an absolute folder")
    prose, judged, sent, renamed = [], set(), set(), set()
    for raw in clean.splitlines():
        line = raw.strip()
        m = VERDICT_RE.match(line)
        if m:
            ref, reason = m.group(1).strip(), m.group(3)
            word = m.group(2).strip().lower().replace(" ", "-").replace("_", "-")
            word = ALIASES.get(word, word)
            parts = kind_of(ref, line)
            if parts is None:
                continue
            if word not in VERDICTS[parts[0]]:
                reject(line, f"'{word}' is not a verdict for a {parts[0]} item")
            elif ref in judged:
                reject(line, "a second verdict for the same item")
            elif len(res["rows"]) >= MAX_ROWS:
                reject(line, f"more than {MAX_ROWS} rows")
            else:
                judged.add(ref)
                res["rows"].append({"ref": ref, "kind": parts[0], "id": parts[1],
                                    "verdict": word, "reason": reason[:MAX_REASON]})
            continue
        m = SEND_RE.match(line)
        if m:
            ref = m.group(1).strip()
            parts = kind_of(ref, line)
            if parts is None:
                continue
            if parts[0] not in ("task", "t3"):
                reject(line, "a follow-up goes to a map thread or a T3 thread")
            elif ref in sent:
                reject(line, "a second prompt for the same thread")
            else:
                sent.add(ref)
                res["sends"].append({"ref": ref, "kind": parts[0], "id": parts[1],
                                     "text": m.group(2)[:MAX_SEND]})
            continue
        m = RENAME_RE.match(line)
        if m:
            ref, title = m.group(1).strip(), m.group(2).strip().strip("\"'“”‘’").strip()
            parts = kind_of(ref, line)
            if parts is None:
                continue
            if parts[0] != "task":
                reject(line, "only a map thread can be renamed")
            elif not title or len(title) > MAX_TITLE or ref in renamed:
                reject(line, f"a title is 1 to {MAX_TITLE} characters, one per thread")
            else:
                renamed.add(ref)
                res["renames"].append({"ref": ref, "id": parts[1], "title": title})
            continue
        m = MISSING_RE.match(line)
        if m:
            if len(res["missing"]) < MAX_MISSING:
                res["missing"].append(m.group(1)[:200])
            continue
        prose.append(raw)
    res["summary"] = re.sub(r"\n{3,}", "\n\n", "\n".join(prose)).strip()[:MAX_SUMMARY]
    return res


# ---- running the model ----------------------------------------------------------------------
def default_model(spec: dict) -> str:
    """Sonnet when the spec offers it (a whole map is a long read), else the spec's own default."""
    names = [m["name"] for m in _mate.models_of(spec)]
    return "sonnet" if "sonnet" in names else _mate.default_of(spec)


def run(spec: dict, model: str | None, playbook: str, context_text: str, chat, question: str,
        runner=None) -> dict:
    """Run the wingmate's command once with the meta prompt on stdin: like wingmate.ask, with its
    own time (at least MIN_SECONDS) and answer cap (MAX_ANSWER characters; the command is
    stopped once it has written MAX_ANSWER_BYTES). Returns {answer (raw), rc, seconds, error,
    model (the label), family, truncated: the answer was cut or the command stopped for writing
    too much}. A runner returns (rc, out, err) or (rc, out, err, cut). Writes nothing."""
    res = {"answer": "", "rc": 2, "seconds": 0.0, "error": None, "model": None, "family": None,
           "truncated": False}
    if not isinstance(question, str) or not question.strip():
        return dict(res, error="ask something")
    if len(question) > _mate.MAX_QUESTION or any(ord(c) < 32 and c not in "\n\t" for c in question):
        return dict(res, error=f"a question is plain text under {_mate.MAX_QUESTION} characters")
    chosen = _mate.model_spec(spec, model or default_model(spec))
    if chosen is None:
        return dict(res, error="no such model")
    res.update(model=chosen["model"], family=chosen["family"])
    seconds = max(float(chosen["max_seconds"]), MIN_SECONDS)
    text = prompt(playbook, context_text, chat, question)
    env = _tasks.agent_env(chosen["env"])       # the clean environment an agent gets, plus its own
    started = time.monotonic()
    try:
        got = (runner or _run)(chosen["cmd"], text, seconds, env)
    except subprocess.TimeoutExpired:
        return dict(res, rc=124, seconds=round(time.monotonic() - started, 1),
                    error=f"no answer within {int(seconds)} s")
    except OSError as exc:
        return dict(res, rc=127, seconds=round(time.monotonic() - started, 1),
                    error=f"could not start the model: {exc}")
    took = round(time.monotonic() - started, 1)
    rc, out, err = got[:3]
    cut = (len(got) > 3 and bool(got[3])) or len(str(out or "")) > MAX_ANSWER
    answer = str(out or "")[:MAX_ANSWER]
    if rc != 0 and not answer.strip():
        tail = str(err or "").strip()[-300:]
        why = f"the model failed: {tail}" if tail else f"the model exited {rc}"
        return dict(res, rc=rc, seconds=took, error=why, truncated=cut)
    return dict(res, answer=answer, rc=rc, seconds=took, truncated=cut)


def _run(cmd: list[str], text: str, timeout: float, env: dict | None):
    """The model command with the prompt on stdin, held under MAX_ANSWER_BYTES of stdout and
    MAX_ERR_BYTES of stderr: (rc, stdout, stderr tail, stopped for writing too much)."""
    done = bounded_run(cmd, cwd=os.path.expanduser("~"), timeout=timeout, env=env, input=text,
                       max_out=MAX_ANSWER_BYTES, max_err=MAX_ERR_BYTES)
    return done.returncode, done.stdout, done.stderr[-2000:], done.truncated
