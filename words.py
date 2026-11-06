#!/usr/bin/env python3
"""words.py — the map's plain words: one glossary every surface reads (the page, Today, the sky, the CLI).

Skins rename characters (the carrier, the assistant), never states: a state is always said in the words
below, whatever the skin. render-game.py inlines WORDS into the page as `WORDS` (`CM.word(k)` reads it); the
page's ui/words.js holds the same helpers in JavaScript and test_plain_words.py runs both on the same inputs.

  WORDS              one flat table: an old or game word (lower case, as the page used to print it) -> the plain
                     word, plus prefixed rows the helpers read: "state:<s>" (a stored state), "verdict:<V>" (a cron
                     verdict), "count:<k>" / "count1:<k>" (a count's noun, many / one), "http:<n>" (an error line),
                     "touch:<desktop word>" (the word on a touch screen)
  STATE_WORDS        the "state:" rows as their own dict: a state as the engines store it -> the word shown
  short_title(t)     a task's title from its text: no "Goal:", no ids, no paths, at most 48 characters
  plain_text(t, n)   a note in plain words: no ids, no links, a path by its last part, at most n characters
  state_word(s)      the word for a stored state, else s
  rel_time(s)        seconds ago -> "just now", "5 min ago", "2 h ago", "3 days ago", "5 weeks ago"
  since_words(t, n)  an epoch stamp -> "Sep 6 (5 weeks ago)"
  count_line(c)      the headline of a counts dict: "13 need you · 53 decisions · 1 problem"
  job_name(cron)     a cron line's name: its script's base name, else its program's ("true", "nightly-export")
  page_command(c)    a command line read off the machine as the page may show it: plain words, paths, links
                     and operators kept, anything else [hidden] (page_word, page_path: one word, one path)
  schedule_words(s)  a crontab schedule -> "every 15 min", "hourly", "daily at 03:30", "Mondays at 09:00"
  job_line(n, v)     "job true: never ran"
  opinion_line(v, m) "second opinion (Codex (OpenAI)): found problems"
  group_causes(xs)   identical lines folded: [(line, n)], first seen first; grouped(line, n) -> "line ×n"
  wf_sentence(w)     a workflow summary -> the one sentence of what it is doing now (the summary's `sentence`)
  wf_marks(w)        a workflow summary -> what to mark on the map now: [{"uid": planet, "why": state}], the steps
                     the sentence speaks of (ui/words.js CM.wfMarks is its twin, CM.markPlanets the page's union)

Neutral words only: the private skin's names (its carrier, its assistant, its finish banner) live in that
skin's own file, never here. Stdlib only, no clock but the argument.
"""
from __future__ import annotations

import os
import re
import shlex
import time

WORDS = {
    # the glossary: old and game words -> the plain word
    "bogeys": "need you",
    "contacts": "need you",
    "in warp": "working",
    "holding": "on hold",
    "parked": "on hold",
    "landed today": "done today",
    "settled": "done today",
    "settled today": "done today",
    "back at base": "to review",
    "requesting orders": "needs your answer",
    "taking damage": "failed",
    "lost contact": "no news",
    "standing by": "to review",
    "going down": "stopped",
    "mission done": "finished",
    "thread": "task",
    "threads": "tasks",
    "goal": "task",
    "operation": "task",
    "mission": "task",
    "seat": "agent",
    "seats": "agents",
    "crew": "agents",
    "runner": "agent",
    "berth": "terminal slot",
    "board row": "decision",
    "quest": "decision",
    "ledger row": "decision",
    "berths": "terminal slots",
    "derelicts": "stopped agents",
    "comms": "Agents",
    "receipts": "Recent",
    "undefended": "no agent on it",
    "hub · you": "you",
    "this machine": "This machine",     # the sun: the machine the map runs on
    "home": "main session",
    "hub": "main session",
    "worktree": "safe copy",
    "verify": "test that must pass",
    "merge": "accept changes",
    "merged": "changes accepted",
    "set aside": "reject (copy kept)",
    "check": "second opinion",
    "door": "instructions file",
    "worker": "default agent",
    "stale": "late",
    "never": "never ran",
    "no receipt": "no log",
    "flows on": "Folder links",
    "[ ] rails": "Side panels",
    "h full hud": "More stats",
    "y sky / every unit": "Only what needs me / everything",
    "j next contact": "Next item for me",
    "wingmate": "assistant",
    "oldest unanswered": "oldest decision",
    "empty sky": "Nothing waiting on you",
    # a stored state -> the word shown (tasks.py statuses, workflow steps, sky contacts)
    "state:new": "starting",
    "state:running": "working",
    "state:checking": "second opinion running",
    "state:stopping": "stopping",
    "state:review": "to review",
    "state:failed": "failed",
    "state:stopped": "stopped",
    "state:lost": "lost",
    "state:merged": "changes accepted",
    "state:discarded": "rejected (copy kept)",
    "state:done": "done",
    "state:error": "unreadable record",
    "state:retrying": "retrying",
    "state:waiting": "waiting",
    "state:ready": "ready",
    "state:working": "working",
    "state:needs_you": "needs you",
    "state:skipped": "skipped",
    "state:blocked": "won't start",
    "state:planned": "planned",
    "state:stale": "late",
    # what a sky contact's state says on a row (sky.py STATES): the decision sources say "decision" instead
    "contact:blocked": "needs your answer",
    "contact:failed": "failed",
    "contact:review": "to review",
    "contact:ready": "to review",
    "contact:stale": "late",
    "contact:decision": "decision",
    # a cron verdict (districts.py) -> how the job is doing
    "verdict:NEVER": "never ran",
    "verdict:STALE": "late",
    "verdict:NO RECEIPT": "no log",
    "verdict:LATE": "a little late",
    "verdict:RUNNING": "on time",
    "verdict:UNKNOWN": "not checked",
    # a count's noun (sky.counts keys), many and one
    "count:need_you": "waiting for you",
    "count1:need_you": "waiting for you",
    "count:answer": "need your answer",
    "count1:answer": "needs your answer",
    "count:review": "to review",
    "count1:review": "to review",
    "count:decisions": "decisions",
    "count1:decisions": "decision",
    "count:problems": "problems",
    "count1:problems": "problem",
    "count:working": "working",
    "count1:working": "working",
    "count:on_hold": "on hold",
    "count1:on_hold": "on hold",
    "count:done_today": "done today",
    "count1:done_today": "done today",
    # an HTTP status or a dead connection -> one sentence (CM.errorLine); "http:" alone is any other status
    "http:0": "Can't reach the map server. Retry",
    "http:400": "The map server could not read that request.",
    "http:401": "This device is not paired with the map. Pair it again",
    "http:403": "The map server refused that from here.",
    "http:404": "That is gone from the map server. Reload",
    "http:409": "That changed while you looked. Reload",
    "http:413": "That is too big to send.",
    "http:429": "Too many requests. Wait a few seconds",
    "http:500": "The map server hit a problem. Retry",
    "http:502": "The map server is not answering. Retry",
    "http:503": "The map server is busy. Retry",
    "http:507": "Storage is full.",
    "http:": "Something went wrong on the map server. Retry",
    # the word a touch screen says instead of the desktop's
    "touch:click": "tap",
    "touch:Click": "Tap",
    "touch:clicks": "taps",
    "touch:hover": "touch",
    "touch:Hover": "Touch",
    "touch:right-click": "long-press",
}

STATE_WORDS = {k[len("state:"):]: v for k, v in WORDS.items() if k.startswith("state:")}

COUNT_NOUNS = ("need_you", "decisions", "problems")     # the headline, in this order
TITLE_MAX = 48

_BULLET = re.compile(r"^\s*(?:[-*•>#]+\s+|\d{1,3}[.)]\s+)?(?:\[[ xX]\]\s+)?")
_LEAD_DATE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\s*(?:[-–—:·|]\s*)?)+")
_PREFIX = re.compile(r"^(?:(?:goal|task|mission|operation|objective|todo|to-do|ask|prompt|thread)\s*[:\-–—]\s*)+", re.I)
_IDS = re.compile(r"\(\s*(?:T|w)-\d{8}[^)]*\)|\[\s*(?:T|w)-\d{8}[^\]]*\]|\b(?:T|w)-\d{8}(?:-\d{4})?(?:-[\w.]+)*"
                  r"|\b\d{8}-\d{6}-[a-z0-9-]+\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b")
_URL = re.compile(r"\bhttps?://[^\s)\]]+")
_PATH = re.compile(r"(?<![\w.:/])(?:~|\.{1,2})?/(?:[\w.@+-]+/)+([\w.@+-]+)/?")   # two parts at least: "/b" is no path
_MARK = re.compile(r"`+|\*+")
# what a title cut at a fixed width (tasks.py keeps 90 characters) leaves at its end: an opened bracket with no close,
# or the start of a task id ("Ship it (T-2026", "so they read (", "see w-20261"); both go, the words before them stay
_TAIL = re.compile(r"\s*(?:[(\[]\s*)?\b(?:T|w)-\d{0,8}(?:-[\w.]*)?\Z|\s*[(\[][^)\]]*\Z")
_SPACE = re.compile(r"\s+")
_CLAUSE = re.compile(r",\s|;\s|\s[—–-]\s|:\s|\s\(")


def _clean_line(text, cap=None) -> str:
    """One line of a title, in plain words: bullets, a leading date, "Goal:"-style prefixes, ids, links and
    markup go; a path is said by its last part. cap: True upper-cases the first letter, False keeps it as written,
    None (the default) upper-cases it only when something was taken off the front ("Goal: tidy" -> "Tidy")."""
    s = str(text if text is not None else "")
    first = next((ln for ln in s.splitlines() if ln.strip()), "")
    head = first.strip()
    first = _BULLET.sub("", first)
    first = _LEAD_DATE.sub("", first.strip())
    first = _PREFIX.sub("", first.strip())
    if cap is None:
        cap = first != head
    first = _URL.sub("", first)
    first = _IDS.sub("", first)
    first = _PATH.sub(lambda m: m.group(1), first)
    first = _MARK.sub("", first)
    first = _TAIL.sub("", first)
    first = _SPACE.sub(" ", first).strip(" -–—:·|,;")
    first = re.sub(r"\(\s*\)", "", first)
    first = _SPACE.sub(" ", first).strip()
    return first[:1].upper() + first[1:] if cap else first


def plain_text(text, n=260) -> str:
    """A note in plain words, all its lines as one: task ids and links go, a path is said by its last part, at
    most n characters (cut with "…"). Case and punctuation stay as written."""
    s = _SPACE.sub(" ", str(text if text is not None else "")).strip()
    s = _URL.sub("", s)
    s = _IDS.sub("", s)
    s = _PATH.sub(lambda m: m.group(1), s)
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    s = re.sub(r"\s+([,.;:])", r"\1", _SPACE.sub(" ", s))
    s = re.sub(r"\s*·\s*(?=[,.;:]|·|$)", "", s).strip(" ·-–—:,;")
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def short_title(text, n=TITLE_MAX, cap=None) -> str:
    """A task's title from its text, as a person would say it: the first line without "Goal:", task ids, links or
    paths, at most `n` characters. A longer line keeps its longest clause head that fits when it is long enough to
    say something (16+ characters), else it is cut at a word with "…". cap: as _clean_line (None: upper case only
    when a prefix was taken off)."""
    line = _clean_line(text, cap)
    if len(line) <= n:
        return line
    best = ""
    for m in _CLAUSE.finditer(line):
        if m.start() > n:
            break
        head = line[:m.start()].rstrip(" ,;:-–—")
        if len(head) >= 16:
            best = head                          # the longest clause head that fits
    if best:
        return best
    cut = line[:n - 1]
    if " " in cut[n // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:-–—") + "…"


def state_word(state) -> str:
    """The word a stored state is shown as; an unknown state is shown as itself."""
    if not isinstance(state, str):
        return ""
    return STATE_WORDS.get(state, state)


def rel_time(seconds) -> str:
    """How long ago, in the words a person uses: "just now", "5 min ago", "2 h ago", "3 days ago",
    "5 weeks ago", "4 months ago". Not a number (or not finite) gives ""."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds != seconds \
            or seconds in (float("inf"), float("-inf")):
        return ""
    s = max(0.0, float(seconds))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        return f"{int(s // 3600)} h ago"
    days = int(s // 86400)
    if days < 14:
        return "1 day ago" if days == 1 else f"{days} days ago"
    if days < 70:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def since_words(epoch, now) -> str:
    """An epoch stamp as a person says it, local time: "Sep 6 (5 weeks ago)"; today "today (2 h ago)". Not a
    stamp gives ""."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) or epoch != epoch \
            or isinstance(now, bool) or not isinstance(now, (int, float)):
        return ""
    try:
        when, today = time.localtime(epoch), time.localtime(now)
    except (ValueError, OverflowError, OSError):
        return ""
    day = "today" if when[:3] == today[:3] else f"{_MONTHS[when.tm_mon - 1]} {when.tm_mday}"
    return f"{day} ({rel_time(now - epoch)})"


def _whole(v) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else 0


def count_noun(key, n, counts=None) -> str:
    """The noun a count is said with: count_noun("problems", 1) -> "problem". With the sky's counts, "need_you" says
    its kind when every one of them is the same kind: "finished task to review", "question for you"."""
    if key == "need_you" and isinstance(counts, dict) and _whole(n):
        if _whole(counts.get("review")) == n and not _whole(counts.get("answer")):
            return "finished task to review" if n == 1 else "finished tasks to review"
        if _whole(counts.get("answer")) == n and not _whole(counts.get("review")):
            return "question for you" if n == 1 else "questions for you"
    return WORDS.get(("count1:" if n == 1 else "count:") + str(key), str(key))


def count_line(counts, keys=COUNT_NOUNS) -> str:
    """The headline of a sky.counts() dict: "13 need you · 53 decisions · 1 problem" (zeros left out; all zero
    says "Nothing waiting on you")."""
    counts = counts if isinstance(counts, dict) else {}
    parts = []
    for k in keys:
        n = counts.get(k)
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            parts.append(f"{n} {count_noun(k, n, counts)}")
    return " · ".join(parts) if parts else "Nothing waiting on you"


# ---------- jobs (cron lines) ----------
_SCRIPT_EXT = re.compile(r"\.(?:sh|bash|zsh|py|rb|pl|js|mjs|ts|php|lua)$", re.I)
_DOW = ("Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")
_AT = {"@reboot": "at start-up", "@yearly": "yearly", "@annually": "yearly", "@monthly": "monthly",
       "@weekly": "weekly", "@daily": "daily", "@midnight": "daily at midnight", "@hourly": "hourly"}


# Programs a job runs THROUGH: the job is what they run, never them ("python3 /x/backup.py" is "backup", not
# "python3"). Shells and interpreters read a script; wrappers (env, nice, flock, timeout, ...) only start the next word.
_JOB_SHELLS = frozenset(("bash", "sh", "zsh", "dash", "ksh", "fish"))
_JOB_THROUGH = re.compile(r"(?:python[\d.]*|pypy[\d.]*|node|nodejs|deno|bun|ruby|perl|php|lua|java|uv|uvx|npx|pnpx|"
                          r"npm|pnpm|yarn|tsx|ts-node|poetry|pipx|pipenv)\Z", re.I)
_JOB_WRAPPERS = frozenset(("env", "nice", "ionice", "nohup", "setsid", "time", "timeout", "flock", "chronic", "sudo",
                           "stdbuf", "taskset", "command", "exec", "xvfb-run", "run-one", "doas", "chrt", "unbuffer"))
_JOB_SUBCOMMANDS = frozenset(("run", "exec", "dlx", "x", "tool", "script"))     # "uv run", "npm run", "pnpm dlx"
# flags that take the next word (never a name): a wrapper's user, signal, adjustment, lock wait, unset variable, ...
_JOB_VALUED = {"sudo": ("-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-T", "-U", "--user", "--group", "--host",
                        "--prompt", "--close-from", "--chdir", "--role", "--type", "--command-timeout", "--other-user"),
               "doas": ("-u", "-C"),
               "env": ("-u", "-C", "-S", "--unset", "--chdir", "--split-string"),
               "timeout": ("-s", "-k", "--signal", "--kill-after"),
               "nice": ("-n", "--adjustment"),
               "ionice": ("-c", "-n", "-p", "-P", "-u", "--class", "--classdata", "--pid", "--pgid", "--uid"),
               "flock": ("-w", "-E", "--wait", "--timeout", "--conflict-exit-code"),
               "taskset": ("-c", "--cpu-list"), "stdbuf": ("-i", "-o", "-e"), "chrt": ("-p",),
               "xvfb-run": ("-s", "-n", "-e", "-f", "-p", "--server-args", "--server-num", "--error-file", "--auth-file",
                            "--xauth-protocol"),
               "uv": ("--with", "--python", "--project", "--directory", "-p", "--from"),
               "uvx": ("--with", "--python", "--from"), "python": ("-W", "-X"), "node": ("-r", "--require", "--import"),
               "perl": ("-I", "-M", "-m"), "ruby": ("-I", "-r", "-C"), "php": ("-d", "-f")}
# an interpreter's option letter that takes code in the line itself (`python3 -c "..."`, `node -e`, `perl -e`): a job whose
# work is that string is "inline script"; for a shell (`sh -c`, `bash -lc`) the string is read for the job it runs
_JOB_CODE_LETTERS = {"python": "c", "pypy": "c", "node": "ep", "nodejs": "ep", "deno": "", "bun": "", "perl": "eE", "ruby": "e",
                     "php": "r", "lua": "e"}
_JOB_HOUSEKEEPING = frozenset(("cd", "export", "set", "source", ".", "umask", "unset", "ulimit", "["))
_JOB_ENV = re.compile(r"[A-Za-z_]\w*=")
_JOB_NUMBER = re.compile(r"\d+(?:\.\d+)?[smhd]?\Z")
_JOB_SAFE = re.compile(r"[\w.+-]+\Z")                       # the only characters a job's name may hold
# a word that looks like a token or a key, never a name: a known secret prefix, or a long run of letters and digits
# with both in it and no extension (a script's name is short, or has one)
_JOB_SECRET = re.compile(r"(?:ghp_|gho_|ghs_|github_pat_|sk-|xox[abp]-|AKIA|ASIA|eyJ[A-Za-z0-9_-]{8,}|AIza|glpat-)|"
                         r"(?=[A-Za-z0-9_+/=.-]{20,}\Z)(?=.*\d)(?=.*[A-Za-z])")


def _job_safe_name(word) -> str:
    """The base name a picked word gives, or '' when the word cannot be a name: a quote, an `=` or a leading dash
    anywhere in it (an option, or a string the parser could not read), a space in its last part (a folder with a
    space is fine: `/a b/backup.py` is "backup"), or it looks like a secret."""
    w = str(word or "")
    if not w or w.startswith("-") or "=" in w or any(ch in w for ch in "'\"`") or any(ord(ch) < 32 for ch in w):
        return ""
    base = os.path.basename(w.rstrip("/")) or w
    if not _JOB_SAFE.match(base) or _JOB_SECRET.match(base) or _looks_secret(base):
        return ""
    name = _SCRIPT_EXT.sub("", base) or base
    if _JOB_SECRET.match(name):
        return ""
    return name[:24]


# What of a command line read off the machine (a cron line, a running program) may go on the page. A credential can
# hide in any shape there (an option's value, a header, JSON, a URL, a glued short option, a bare token), so the page
# never tries to recognise one: it shows only what it can tell is NOT one. Plain words and paths of ordinary
# characters, links without their user, query or fragment, and shell operators stay; everything else is [hidden],
# and so is the value of any option or variable whose name speaks of a credential. A cron line's stdin (after its
# first unescaped %) is hidden whole. Every page field built from a command line goes through page_word or
# page_path, never the raw text.
HIDDEN = "[hidden]"
# ordinary characters, once each $NAME or ${NAME} reference (a name, not a value) is taken out; a colon only in a
# time or a date-time (04:00, 2026-11-06T04:00:00Z). Two plain scans, no nested repetition: linear on any line.
_CMD_WORD_OK = re.compile(r"[A-Za-z0-9_./~+,@-]*\Z")
_CMD_REF = re.compile(r"\$\{[A-Za-z_]\w*\}|\$[A-Za-z_]\w*")
_CMD_TIME = re.compile(r"(?:\d{4}-\d{2}-\d{2}T)?\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\Z")
# a name that speaks of a credential (an environment variable, an option, a path part's key)...
_CMD_SECRET_NAME = re.compile(r"(?i)pass|pwd|secret|token|key|auth(?!or)|cred|cookie|session|bearer|user")
# ...unless it names what kind of thing the credential is, or where it is kept: that value is a word or a path
_CMD_SECRET_KIND = re.compile(r"(?i)[-_](?:type|method|mode|file|dir|path|size|len|length|bits|alg|algorithm|count|"
                          r"timeout|ttl)\Z")
# the shapes of the usual keys, anywhere in a word, and a UUID (a capability link's id)
_CMD_KEY_SHAPE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_\w{16,}|glpat-[\w-]{16,}|xox[abprs]-[\w-]{10,}|A[KS]IA[0-9A-Z]{16}"
    r"|AIza[\w-]{20,}|(?<![A-Za-z])sk-[\w-]{16,}|eyJ[\w-]{10,}"
    r"|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CMD_RUN = re.compile(r"[A-Za-z0-9]{20,}")
_CMD_WORD = re.compile(r"""\s+|(?:[^\s'"\\]|\\.|'[^']*'|"(?:[^"\\]|\\.)*")+""", re.S)
_CMD_OPERATORS = {"&&", "||", "|", "|&", ";", ";;", "&", "(", ")", "{", "}", "!", "#", "[", "]", "[[", "]]"}
_CMD_REDIRECT = re.compile(r"(\d*>>?|&>>?|\d*<)(.*)\Z", re.S)
_CMD_ASSIGN = re.compile(r"(--?[A-Za-z][\w-]*|[A-Za-z_]\w*)=(.*)\Z", re.S)
_CMD_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)([^?#]*)(.*)\Z", re.S)


def _looks_secret(text) -> bool:
    """True for a word that holds a usual key's shape or a long opaque run (20+ letters and digits that are not one
    plain lowercase word): a token, a hash, a key."""
    if _CMD_KEY_SHAPE.search(text):
        return True
    return any(not (run.isalpha() and run.islower() and len(run) < 32) for run in _CMD_RUN.findall(text))


def _shell_text(word) -> str:
    """`word` as the shell hands it to the program (quotes and backslashes resolved: --pass"word" is --password);
    the word itself when it cannot be read alone. Only for recognising an option's name, never for showing."""
    try:
        got = shlex.split(word)
    except ValueError:
        return word
    return got[0] if len(got) == 1 else word


_CMD_WEB = {"curl", "wget", "http", "https", "xh", "httpie", "aria2c"}   # their -u/-U value is user:password


def _secret_name(name) -> bool:
    name = str(name).lstrip("-")
    return bool(_CMD_SECRET_NAME.search(name)) and not _CMD_SECRET_KIND.search(name)


def page_word(word) -> str:
    """`word` when it is plainly not a credential (ordinary characters, no key shape, no long opaque run), else
    [hidden]. '' stays ''."""
    w = str(word or "")
    if not w:
        return ""
    plain = bool(_CMD_WORD_OK.match(_CMD_REF.sub("", w))) or bool(_CMD_TIME.match(w))
    return w if plain and not _looks_secret(w) else HIDDEN


def page_path(path) -> str:
    """`path` with each part that is not plainly safe [hidden] (a part `key=value` with a credential's name
    included); the slashes and the safe parts stay, so the page still says where."""
    parts = []
    for part in str(path or "").split("/"):
        key, eq, value = part.partition("=")
        if eq and not _secret_name(key) and page_word(key) == key and page_word(value) == value:
            parts.append(part)
        else:
            parts.append(page_word(part) if part else "")
    return "/".join(parts)


def _page_url(word) -> str:
    m = _CMD_URL.match(word)
    scheme, authority, path, rest = m.groups()
    user, at, host = authority.rpartition("@")
    host = host if re.fullmatch(r"[\w.-]+(?::\d{1,5})?", host) and not _looks_secret(host) else HIDDEN
    out = scheme + (HIDDEN + "@" if at else "") + host + page_path(path)
    if rest:
        out += rest[0] + HIDDEN
    return out


def _page_value(value) -> str:
    if _CMD_URL.match(value):
        return _page_url(value)
    if value[:1] in "/~." or "/" in value:
        return page_path(value)
    return page_word(value)


def page_command(command, cron=False) -> str:
    """`command` as the page may show it: each shell word kept when it is plainly safe, else [hidden] (see the
    note above); `cron`: everything after the first unescaped % is the job's stdin, hidden whole."""
    text, stdin = str(command or ""), ""
    if cron:
        m = re.search(r"(?<!\\)%", text)
        if m:
            text, stdin = text[:m.start()], "%" + HIDDEN
    out, pos, hide_next, web = [], 0, False, False
    while pos < len(text):
        m = _CMD_WORD.match(text, pos)
        if not m:                                   # an unbalanced quote: the rest of the line is one hidden word
            out.append(HIDDEN)
            break
        word, pos = m.group(0), m.end()
        if word.isspace():
            out.append(" ")
            continue
        tail = ""
        if len(word) > 1 and word[-1] in ";&|" and word[-2] not in ";&|>":
            word, tail = word[:-1], word[-1]
        quoted = re.fullmatch(r"\"([^\"\\`]*)\"|'([^']*)'", word)   # one quoted word: read as the word inside
        bare = (quoted.group(1) if quoted.group(1) is not None else quoted.group(2)) if quoted else word
        secret_next = False
        said = _shell_text(word)                    # what the program receives: decides what an option is
        if said in _CMD_OPERATORS and not quoted:
            web = False                             # a new command starts after an operator
        elif os.path.basename(said) in _CMD_WEB:
            web = True
        if hide_next or not bare:
            shown = HIDDEN if hide_next else ""
        elif bare in _CMD_OPERATORS and not quoted:
            shown = bare
        elif _CMD_REDIRECT.match(bare) and not quoted:
            op, rest = _CMD_REDIRECT.match(bare).groups()
            shown = op + (rest if re.fullmatch(r"&\d+|&-", rest) else (page_path(rest) if rest else ""))
        elif _CMD_ASSIGN.match(bare):
            name, value = _CMD_ASSIGN.match(bare).groups()
            if page_word(name) != name:
                shown = HIDDEN
            else:
                shown = name + "=" + (HIDDEN if _secret_name(name) else _page_value(value))
        elif re.fullmatch(r"-[pP].+", bare, re.S):     # a password glued to -p (mysql -pSECRET and kin)
            shown = HIDDEN
        elif web and re.fullmatch(r"-[uU].+", said, re.S):   # curl -uUSER:PASS, glued
            shown = HIDDEN
        elif said.startswith("-"):
            shown = page_word(bare)
            if said.startswith("--"):
                secret_next = "=" not in said and _secret_name(said)
                if "=" in said and _secret_name(said.split("=", 1)[0]):
                    shown = HIDDEN
            else:
                secret_next = web and said in ("-u", "-U")
        else:
            shown = _page_value(bare)
        hide_next = secret_next
        if quoted and shown:
            shown = word if shown == bare else HIDDEN
        out.append(shown + tail)
    return "".join(out) + stdin


def _job_segment(command: str) -> list:
    """The words of the first command in a cron line that does the work: shell operators split the line, a
    leading `cd x &&` / `export y;` is housekeeping, a redirection ends the command. A line shlex cannot read
    (an unbalanced quote) gives its plain words, quotes kept, so a word with one is never taken as a name."""
    try:
        lex = shlex.shlex(command, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:                                  # an unbalanced quote: the plain words are the best guess
        toks = command.split()
    segments, cur = [], []
    for tok in toks:
        if tok in ("&&", "||", ";", "|", "&", ";;"):
            segments.append(cur)
            cur = []
        elif re.match(r"\d*[<>]", tok) and not _JOB_ENV.match(tok):
            segments.append(cur)                        # a redirection ends the command; what follows is its target
            cur = ["\0"]
        elif cur and cur[0] == "\0":
            continue
        else:
            cur.append(tok)
    segments.append(cur)
    segments = [[w for w in s if w != "\0"] for s in segments]
    segments = [s for s in segments if s]
    for s in segments:
        head = [w for w in s if not _JOB_ENV.match(w)]
        if head and os.path.basename(head[0]) not in _JOB_HOUSEKEEPING:
            return s
    return segments[0] if segments else []


def _job_prog_key(prog: str) -> str:
    """The table key of a program: "python3.11" -> "python", "pypy3" -> "pypy"."""
    return re.sub(r"[\d.]+\Z", "", prog) if prog.startswith(("python", "pypy")) else prog


def job_script(command, _depth: int = 0) -> str:
    """The script or program a cron line's command runs, however it is started: `/usr/bin/python3 /x/backup.py`,
    `uv run backup.py`, `bash -c "cd /x && ./run.sh"`, `flock -n /tmp/a.lock /usr/local/bin/backup` -> its base name
    without the extension ("backup", "run"), "inline shell" for a command in a string it cannot read, "inline script"
    for code given to an interpreter on the line, '' when the line names nothing it can say. A shell or an
    interpreter is never the answer, and neither is a wrapper's own argument (a user, a signal, a variable), a
    word with a quote, a space or an `=` in it, an option, or a word that looks like a key."""
    if not isinstance(command, str) or not command.strip() or _depth > 2:
        return ""
    words = _job_segment(command)
    through, prog, skip, lock, found = False, "", 0, False, []
    for i, tok in enumerate(words):
        if skip:
            skip -= 1
            continue
        if _JOB_ENV.match(tok) and not found:
            continue
        base = os.path.basename(tok.rstrip("/")).lower()
        key = _job_prog_key(prog)
        if tok.startswith("-"):
            short = re.match(r"-([A-Za-z]+)\Z", tok)             # -c, -lc, -xe: a bundle of single letters
            if prog in _JOB_SHELLS and short and "c" in short.group(1):
                if i + 1 < len(words):                   # sh -c "cmd" (bash -lc, -xc too): the job is what the string runs
                    return job_script(words[i + 1], _depth + 1) or "inline shell"
                return "inline shell"
            if short and key in _JOB_CODE_LETTERS and any(ch in _JOB_CODE_LETTERS[key] for ch in short.group(1)):
                return "inline script"                   # python3 -c, node -e / -p, perl -e, ruby -e, php -r, lua -e
            if tok == "-m" and key == "python" and i + 1 < len(words):
                return _job_safe_name(words[i + 1].split(".")[-1]) or "inline script"   # python -m pkg.mod: the module
            if tok in _JOB_VALUED.get(key, ()):
                skip = 1
            continue
        if base in _JOB_SHELLS or _JOB_THROUGH.match(base) or base in _JOB_WRAPPERS:
            if not found:
                prog = base
                through = through or base in _JOB_SHELLS or bool(_JOB_THROUGH.match(base))
                lock = lock or base == "flock"
                continue
        if not found and (prog and base in _JOB_SUBCOMMANDS and tok == base):
            continue
        if _JOB_NUMBER.match(tok) and prog:             # timeout 30, nice -n 10
            continue
        if lock:                                        # flock's first word is the lock file
            lock = False
            continue
        if tok.startswith("$") and "/" not in tok:      # a bare variable names nothing; $HOME/bin/x.sh does
            return "inline shell"
        found.append(tok)
        if not through:
            break
    if not found:
        return ""
    # after an interpreter the script is the word with a script extension (its own flags may take values we do not
    # know: node --require x backup.js); else the first word. A pick that cannot be a name (a quote, a space, an
    # `=`, a key-like word) names nothing: the line is "a job"
    pick = next((t for t in found if _SCRIPT_EXT.search(t)), found[0])
    return _job_safe_name(pick)


def job_name(cron) -> str:
    """A cron line's name as a person says it: the script its command runs (job_script: "backup" for
    `/usr/bin/python3 /opt/backup.py`), else its script's, program's or own name without the extension ("true"),
    at most 24 characters; "a job" when the line names nothing but an interpreter or nothing at all."""
    cron = cron if isinstance(cron, dict) else {}
    named = job_script(cron.get("command"))
    if named:
        return named
    for key in ("script", "tool", "name"):
        v = cron.get(key)
        if isinstance(v, str) and v.strip():
            base = os.path.basename(v.strip().rstrip("/")) or v.strip()
            if _JOB_THROUGH.match(base.lower()) or base.lower() in _JOB_SHELLS or base.lower() in _JOB_WRAPPERS:
                continue                                # never the program a job runs through
            name = _job_safe_name(v.strip())             # the same rule: no option, quote, `=` or key-like word
            if name:
                return name
    return "a job"


def schedule_words(sched) -> str:
    """A crontab schedule in words: "*/15 * * * *" -> "every 15 min", "0 * * * *" -> "hourly", "30 3 * * *" ->
    "daily at 03:30", "0 9 * * 1" -> "Mondays at 09:00", "@reboot" -> "at start-up"; anything else "on its own
    schedule"."""
    s = str(sched or "").strip()
    if s in _AT:
        return _AT[s]
    f = s.split()
    if len(f) != 5:
        return "on its own schedule" if s else ""
    mi, hr, dom, mon, dow = f
    if mon != "*" or dom != "*":
        return "on its own schedule"
    m = re.fullmatch(r"\*/(\d{1,2})", mi)
    if m and hr == "*" and dow == "*":
        return "every minute" if m.group(1) == "1" else f"every {int(m.group(1))} min"
    if mi == "*" and hr == "*" and dow == "*":
        return "every minute"
    if mi.isdigit() and hr == "*" and dow == "*":
        return "hourly" if int(mi) == 0 else f"hourly at :{int(mi):02d}"
    m = re.fullmatch(r"\*/(\d{1,2})", hr)
    if mi.isdigit() and m and dow == "*":
        return f"every {int(m.group(1))} h"
    if mi.isdigit() and hr.isdigit():
        at = f"{int(hr):02d}:{int(mi):02d}"
        if dow == "*":
            return f"daily at {at}"
        if dow.isdigit() and 0 <= int(dow) <= 7:
            return f"{_DOW[int(dow)]} at {at}"
        if dow in ("1-5", "mon-fri", "MON-FRI"):
            return f"weekdays at {at}"
    return "on its own schedule"


def verdict_word(verdict) -> str:
    """A cron verdict (districts.py) as how the job is doing: NEVER -> "never ran", STALE -> "late", NO RECEIPT ->
    "no log"; anything else in lower case."""
    v = str(verdict or "")
    return WORDS.get("verdict:" + v, v.lower())


def job_line(name, verdict) -> str:
    """"job true: never ran"."""
    return f"job {name}: {verdict_word(verdict)}"


def opinion_line(verdict, model=None) -> str:
    """A second opinion's outcome, the outcome first and the model only in brackets: "second opinion (Codex
    (OpenAI)): found problems", "second opinion: passed", "second opinion running". An empty verdict gives ""."""
    v = str(verdict or "").strip()
    if not v:
        return ""
    who = f" ({str(model).strip()})" if isinstance(model, str) and model.strip() else ""
    low = v.lower()
    if low in ("checking", "running", "pending"):
        return f"second opinion{who} running"
    if low in ("pass", "passed", "ok", "approve", "approved", "lgtm"):
        said = "passed"
    elif low in ("fail", "failed", "reject", "rejected", "changes", "request_changes", "problems", "block"):
        said = "found problems"
    elif low in ("error", "unavailable", "timeout", "lost") or low.startswith("no verdict"):
        said = "gave no answer"
    else:
        said = low
    return f"second opinion{who}: {said}"


def group_causes(lines):
    """Identical lines folded into one, first seen first: ["a", "b", "a"] -> [("a", 2), ("b", 1)]."""
    seen, order = {}, []
    for line in lines or ():
        key = str(line)
        if key not in seen:
            seen[key] = 0
            order.append(key)
        seen[key] += 1
    return [(k, seen[k]) for k in order]


def grouped(line, n) -> str:
    """"job true: no log ×5" (a single one is just the line)."""
    return f"{line} ×{n}" if isinstance(n, int) and n > 1 else str(line)


# ---------- workflows ----------
def _plain(text, n=60) -> str:
    """A label or a reason in plain words for a sentence: no ids, no paths, no links, cut at n."""
    s = _clean_line(text, cap=False)
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def _place(step) -> str:
    p = step.get("place")
    if isinstance(p, str) and p.strip():
        p = p.strip()
        if p.startswith("place:") or p.startswith("machine:"):
            p = p.split(":", 1)[1]
        if "/" in p or "\\" in p:
            p = os.path.basename(p.rstrip("/\\")) or p
        return _plain(p, 24)
    uid = step.get("planet_uid")
    if isinstance(uid, str) and ":" in uid:
        return _plain(uid.split(":", 1)[1].split("/")[-1], 24)
    return "this machine"


def _progress(step) -> str:
    p = step.get("progress")
    if isinstance(p, dict):
        d, t = p.get("done"), p.get("total")
        if isinstance(d, (int, float)) and isinstance(t, (int, float)) and not isinstance(d, bool) \
                and not isinstance(t, bool) and t > 0:
            return f"{int(d)} of {int(t)}"
    return ""


def wf_sentence(summary) -> str:
    """The one plain sentence a workflow summary carries as `sentence` (workflows.py stores it; the route slot,
    the 3D MFD and Today's Running now print it as is): what it does now and where, then what comes next.
      working   "Now: Fix labels in ops · 120 of 800. Next: Docs in docs."
      checking  "Now: Fix labels in ops · second opinion running. Next: ..."
      a gate    "Waiting for you: approve 'Ship it' in site."
      a merge   "Waiting for you: accept the changes of 'Fix labels' in ops."
      a failure "'Build' failed in api: <why, at most 60>."
      done      "Done: all 4 steps finished."   planned  "Planned: 4 steps, first Find the cut labels in api."
    No ids, no paths; at most 140 characters; '' for anything that is not a summary with steps."""
    if not isinstance(summary, dict):
        return ""
    steps = [s for s in summary.get("steps") or [] if isinstance(s, dict)]
    if not steps:
        return ""
    state = summary.get("state")
    by_key = {s.get("key"): s for s in steps}
    current = [by_key[k] for k in summary.get("current") or [] if k in by_key]

    def label(s):
        text = _plain(s.get("label") or s.get("key") or "a step", 48)
        return text[:1].upper() + text[1:]

    def nxt(after):
        keys = {s.get("key") for s in after}
        for s in steps:
            if s.get("state") in ("waiting", "ready") and keys & set(s.get("after") or []):
                return s
        return next((s for s in steps if s.get("state") in ("waiting", "ready")), None)

    out = ""
    failed = next((s for s in steps if s.get("state") == "failed"), None)
    needs = [s for s in current if s.get("state") == "needs_you"] or \
        [s for s in steps if s.get("state") == "needs_you"]
    working = [s for s in current if s.get("state") == "working"] or \
        [s for s in steps if s.get("state") == "working"]
    if state == "failed" or (failed and not working and not needs):
        s = failed or steps[-1]
        why = _plain(s.get("why") or "", 60)
        out = f"'{label(s)}' failed in {_place(s)}" + (f": {why}." if why else ".")
    elif needs:
        s = needs[0]
        if s.get("kind") == "gate":
            out = f"Waiting for you: approve '{label(s)}' in {_place(s)}."
        elif s.get("kind") == "task":
            out = f"Waiting for you: accept the changes of '{label(s)}' in {_place(s)}."
        else:
            out = f"Waiting for you: '{label(s)}' in {_place(s)}."
    elif working:
        s = working[0]
        # a task step whose second opinion is reading the changes says so (its progress is finished by then)
        chk = s.get("check") if isinstance(s.get("check"), dict) else {}
        prog = WORDS["state:checking"] if s.get("task_status") == "checking" \
            or chk.get("state") in ("running", "checking", "pending") else _progress(s)
        out = f"Now: {label(s)} in {_place(s)}" + (f" · {prog}" if prog else "") + "."
        n = nxt(working)
        if n is not None:
            nexts = f" Next: {label(n)} in {_place(n)}."
            if len(out) + len(nexts) <= 140:
                out += nexts
    elif state == "done" or all(s.get("state") in ("done", "skipped") for s in steps):
        done = sum(1 for s in steps if s.get("state") == "done")
        out = f"Done: all {done} steps finished." if done == len(steps) else f"Done: {done} of {len(steps)} steps."
    elif state == "stopped":
        out = "Stopped by you."
    else:
        first = nxt([]) or steps[0]
        out = f"Planned: {len(steps)} steps, first {label(first)} in {_place(first)}."
    return out if len(out) <= 140 else out[:139].rstrip() + "…"


MARK_WHY = ("failed", "needs_you", "working", "planned")     # what a mark says, the owner's first


def wf_marks(summary) -> list:
    """What to mark on the map now, beside the sentence (cosmosjourneyer missionNodeBase.ts:26-80, idea only: a node
    says its next task in one sentence and names the objects to mark in the world for it): the planets of the steps
    wf_sentence speaks of, in the sentence's order of tests. A failure marks the failed step's planet; waiting for you
    marks every step that waits on you (a gate, a merge, an answer), the current ones first; at work, every working
    step's planet; planned, the first step that can start. Done, stopped or not a summary: none. A step on no planet
    (a virtual place) marks nothing; a planet marked twice keeps its first mark.
    Returns [{"uid": "area:ops", "why": "working"}, ...], why one of MARK_WHY."""
    if not isinstance(summary, dict):
        return []
    steps = [s for s in summary.get("steps") or [] if isinstance(s, dict)]
    if not steps:
        return []
    state = summary.get("state")
    by_key = {s.get("key"): s for s in steps}
    current = [by_key[k] for k in summary.get("current") or [] if isinstance(k, str) and k in by_key]
    failed = next((s for s in steps if s.get("state") == "failed"), None)
    needs = [s for s in current if s.get("state") == "needs_you"] + \
        [s for s in steps if s.get("state") == "needs_you" and s not in current]
    working = [s for s in current if s.get("state") == "working"] + \
        [s for s in steps if s.get("state") == "working" and s not in current]
    if state == "failed" or (failed and not working and not needs):
        marked, why = [failed or steps[-1]], "failed"
    elif needs:
        marked, why = needs, "needs_you"
    elif working:
        marked, why = working, "working"
    elif state in ("done", "stopped") or all(s.get("state") in ("done", "skipped") for s in steps):
        return []
    else:
        first = next((s for s in steps if s.get("state") in ("waiting", "ready")), steps[0])
        marked, why = [first], "planned"
    out, seen = [], set()
    for s in marked:
        uid = s.get("planet_uid")
        if isinstance(uid, str) and uid and uid not in seen:
            seen.add(uid)
            out.append({"uid": uid, "why": why})
    return out
