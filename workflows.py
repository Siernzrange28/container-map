#!/usr/bin/env python3
"""workflows.py — workflows you can watch: a few steps, each where it works (a folder, a machine or a named
place), in a state, with what it produced. Any agent can put one on the map and update it live; a step that
is an agent task runs through the thread engine (tasks.py: its own worktree, a test that must pass, retries,
a second opinion from another model family, your merge).

One workflow = one folder, <workflows_dir>/<id>/ (workflows_dir: "" = WORK_ROOT/.container-map/workflows):

  workflow.json          the spec as created (mode 600, written once, its digest pinned in the create event)
  events.NNNNNN.jsonl    append-only segments (EVENTS), rolled at 1 MB
  latest.json            coalesced progress (pulses and same-state note updates write only this)
  artifacts/<key>/       snapshots of the files a step declared as output

State is derived from the events (and, for task steps, from TaskStore.summary), never stored. Every change of a
derived state is also written down as a `derived` event {key, state, says, why, progress, output, artifact,
planet_uid, place, by, t}, so the history and a replay need neither the thread engine nor the map that drew it. Nothing is deleted: `retire`
moves closed workflows to an attic with a MANIFEST and an UNDO script. Every folder here is mode 700 and every
file 600.

A summary (list(), summary()) carries SUMMARY_KEYS, each step STEP_KEYS (plus `where`, and for a task step
`runner`, `task_status`, `wait`, `check`, `branch`, `files_changed` (a count; the names and the worktree are in
detail()'s `results`)); `sentence` is words.wf_sentence(summary), the one plain line of what
the workflow is doing now. Summaries name places, never paths: paths are only in detail() (desktop, agents).

The engine (tick): every mutation of one workflow runs under that workflow's lock and re-reads its
state inside it, so the bridge and the CLI can both tick it; what counts across workflows (the open cap at
create, the day's shared launch count and the launch it allows) runs under the folder's lock, taken last.
An agent reports a note step only once what it waits on is through, so no agent route passes a gate. A ready task step: `launch` -> TaskStore.create
(origin {workflow, step, attempt}) -> `task` | `deferred` (429: a free slot later, attempt + 1; no task was
made, so the launch is given back to the day's count) |
`launch_failed` (the step fails, its dependents are blocked). A launch with no answer is found again by its
origin (a map killed between the two), or deferred after 120 s. A finished task step's result (branch,
worktree, files changed) and every done step's outputs are kept before anything after it launches. The engine
asks for the second opinion once verify passed and never merges. Templates (TEMPLATES, instantiate, save_template) let the owner start one
themselves. Stdlib only.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import threading
import time
from datetime import datetime

KINDS = ("note", "task", "gate")
STEP_STATES = ("waiting", "ready", "working", "needs_you", "done", "failed", "skipped", "blocked")
# worst first, as a workflow is summed up from its steps
WORKFLOW_STATES = ("stopped", "failed", "needs_you", "working", "stale", "planned", "done")
EVENTS = ("create", "start", "step", "output", "gate", "launch", "task", "deferred", "launch_failed",
          "duplicate", "check", "result", "derived", "artifact", "retry", "skip", "stop", "done")
WAITS = ("clear", "merged")
# what a summary holds; `sentence` = words.wf_sentence(summary), stored with the summary
SUMMARY_KEYS = ("id", "title", "goal", "by", "state", "updated", "seq", "done", "total", "elapsed_s", "current",
                "sentence", "steps")
STEP_KEYS = ("key", "label", "kind", "state", "planet_uid", "moon_uid", "place", "after", "says", "progress", "output",
             "artifact", "tid", "attempt", "why")
STARTS = ("now", "plan")

BOUNDS = {
    "title": 60, "goal": 140, "by_name": 40, "label": 60, "says": 200, "output_text": 300,
    "steps": 24, "after": 8, "inputs": 8, "retries": 3, "place": 24,
    "launches": 48,                 # task launches per workflow, over all attempts
    "depth": 2,                     # a workflow created from inside a workflow task: refused past this depth
    "stale_after_min": 15,          # default: working with no event, pulse or live agent this long reads stale
    "open": 16, "closed_listed": 50,
    "body_kb": 24,
    "segment_mb": 1, "events_mb": 8,
    "artifact_mb": 8, "workflow_artifact_mb": 32,
    "dir_mb": 512, "creates_per_day": 200, "create_gap_s": 2,
    "updates_burst": 4, "updates_per_s": 1, "updates_overall_per_s": 20,
    "replay_frames": 2000, "events_shown": 50,
    "shows_live": 8, "show_items": 16, "show_ttl_min_s": 30, "show_ttl_max_s": 900,
    "page_bytes": 262144, "page_title": 40, "pages_live": 4, "page_ttl_max_s": 86400, "page_ttl_default_s": 1800,
    "point_live": 8, "point_min_s": 5, "point_max_s": 120,
}

ID_RE = re.compile(r"\Aw-\d{8}-\d{4}-[a-z0-9-]{1,40}-[0-9a-f]{6}\Z")
KEY_RE = re.compile(r"\A[a-z0-9-]{1,24}\Z")
PLACE_RE = re.compile(r"\Aplace:.{1,24}\Z", re.S)
MACHINE_RE = re.compile(r"\Amachine:[A-Za-z0-9._-]{1,64}\Z")
TEMPLATE_NAME_RE = re.compile(r"\A[a-z0-9][a-z0-9-]{0,39}\Z")

CLOSED = ("done", "stopped")          # a closed workflow launches nothing and is listed as history
NOTE_STATES = ("working", "done", "failed", "skipped")      # what an agent may say a note step is
TERMINAL = ("done", "failed", "skipped")
COALESCE_S = 10.0                     # a same-state note update within this many seconds only rewrites latest.json
LIVE_THREAD = ("running", "checking", "stopping")    # thread statuses tasks.py gives only while a pid is alive
LOST_LAUNCH_S = 120.0                 # a launch with no answer for this long (and no thread found) is deferred
DEFER_S = 10.0                        # a step deferred twice in a row waits this long before the next attempt
DEFER_MAX_S = 300.0                   # (doubling, to 300); after one deferral the next tick tries again
LAUNCHES_PER_DAY = 40                 # config workflow_launches_per_day: agent-made launches since local midnight
CAP_WHY = ("today's agent launch limit ({n}) is used up; it starts tomorrow or when you press Retry")
SLOT_WHY = "waiting for a free agent slot"
LOST_WHY = "launch lost (map restarted)"
NO_CHECKER = "no agent of another model family is set up, so nobody gives this work a second opinion"
OWNER = "you"
BIDI = frozenset("‎‏‪‫‬‭‮⁦⁧⁨⁩؜")
SECRET_NAME_RE = re.compile(r"(?i)^\.env|\.pem$|\.key$|^id_|^\.bridge-live$|^remote-codes\.json$|secret")
# what the bridge's /file route refuses (bridge.py FILE_DENY_*), matched in lower case: never kept as an artifact
FILE_DENY_PARTS = frozenset({".secrets", ".ssh", ".gnupg", ".aws", ".env", "credentials", "secrets", ".git",
                             ".kube", ".docker", ".azure", ".gcloud", ".password-store", ".terraform.d",
                             "terraform.tfstate.d", ".oci", ".vault-token", ".htpasswd", ".s3cfg", ".boto",
                             ".my.cnf", ".mylogin.cnf", ".msmtprc", ".authinfo", ".terraformrc", "terraform.rc",
                             ".yarnrc", ".yarnrc.yml", "kubeconfig", "wp-config.php", "settings-security.xml",
                             "credentials.json", "credentials.toml"})
FILE_DENY_PREFIX = ("id_rsa", "id_dsa", "id_ed25519", "id_ecdsa", ".env", ".bridge-live", "bridge.log", ".netrc",
                    ".npmrc", ".pypirc", ".git-credentials", ".pgpass", ".dockercfg",
                    "remote-sessions", "remote-audit", "remote-codes")
FILE_DENY_END = (".enc.env", ".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore", ".tfvars", ".tfvars.json",
                 ".tfstate", ".tfstate.backup", ".ovpn", ".gpg", ".pgp", ".p8", ".ppk", ".jks", ".keytab")
# a folder under another ("~/.config/gcloud") and a file named for what it holds anywhere in its name
# (service-account-ci.json, my-service-account.json): searched, not matched at the start
FILE_DENY_PAIRS = ((".config", "gcloud"), (".config", "gh"), (".config", "rclone"), (".config", "doctl"),
                   (".config", "op"), (".config", "hub"), (".m2", "settings.xml"), (".gradle", "gradle.properties"))
FILE_DENY_NAME_RE = re.compile(r"(service[-_]?account|client[-_]secrets?|application[-_]default[-_]credentials)"
                               r"[^/]*\.json")
LIVE_FILE_NAME_RE = re.compile(r"bridge-[0-9]+\.json(\.new\..*)?")   # a bridge's credentials file by name
RUN_DIR = (".container-map", "run")    # <root>/.container-map/run/ holds the bridge's credentials (bridge.py)
# the map's private folders: run/ (credentials), chat-scan/ (the owner's read chats: quotes), t3-remote/ (another
# machine's thread list). Never served by /file, never taken as a step's output (it would be copied as an artifact)
PRIVATE_DIRS = (RUN_DIR, (".container-map", "chat-scan"), (".container-map", "t3-remote"))
SPEC_KEYS = {"title", "goal", "by", "start", "stale_after_min", "steps", "at", "template"}
STEP_FIELDS = {"key", "label", "at", "kind", "after", "inputs", "task", "note"}
TASK_FIELDS = {"text", "runner", "model", "verify", "retries", "setup", "check_runner", "wait"}
STATE_ORDER = {s: i for i, s in enumerate(WORKFLOW_STATES)}


class WorkflowError(Exception):
    """A refusal with the HTTP status the bridge answers with and a plain sentence for the person."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- small helpers ----------
def _iso(t) -> str | None:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(t))) if t is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _epoch(text) -> float | None:
    """An ISO stamp ("...Z" or with an offset; naive = UTC) as epoch seconds, else None."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(s)
    except ValueError:
        return None
    if when.tzinfo is None:
        from datetime import timezone
        when = when.replace(tzinfo=timezone.utc)
    try:
        return when.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()


def _same(got: str, want: str) -> bool:
    """Constant-time equality of two hex digests (bridge.py's _same, for the tokens)."""
    try:
        return hmac.compare_digest(str(got).encode("ascii", "replace"), str(want).encode("ascii", "replace"))
    except (TypeError, ValueError):
        return False


def _slug(text: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return (out[:40].rstrip("-") or "workflow")


def _and(names) -> str:
    q = [f"'{n}'" for n in names]
    return q[0] if len(q) == 1 else ", ".join(q[:-1]) + " and " + q[-1]


def _cut(text, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _unencodable(value: str) -> bool:
    try:
        value.encode("utf-8")
        return False
    except UnicodeEncodeError:
        return True


def _plain(value, name: str, limit: int, required: bool = True, multiline: bool = False) -> str | None:
    """Plain text an agent sent: a string, no control or bidi characters (a newline or tab only where
    multiline), valid UTF-8, at most `limit` characters once trimmed. Missing = None unless required."""
    if value is None or (isinstance(value, str) and not value.strip() and not required):
        if required:
            raise WorkflowError(400, f"{name} is missing")
        return None
    if not isinstance(value, str):
        raise WorkflowError(400, f"{name} must be text")
    allowed = "\n\t" if multiline else ""
    if any((ord(c) < 32 and c not in allowed) or 127 <= ord(c) < 160 or c in BIDI for c in value):
        raise WorkflowError(400, f"{name} must be plain text: no control or direction characters")
    if _unencodable(value):
        raise WorkflowError(400, f"{name} holds a character that is not valid text")
    value = value.strip()
    if required and not value:
        raise WorkflowError(400, f"{name} is empty")
    if len(value) > limit:
        raise WorkflowError(400, f"{name} must be {limit} characters at most (it has {len(value)})")
    return value


def _int(value, name: str, lo: int, hi: int, default: int) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise WorkflowError(400, f"{name} must be a whole number from {lo} to {hi}")
    return value


def _progress(value) -> dict | None:
    """"120/800" or {"done": 120, "total": 800} -> {"done", "total"} (0 <= done <= total <= 10^9)."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        m = re.fullmatch(r"\s*(\d{1,10})\s*/\s*(\d{1,10})\s*", value)
        if not m:
            raise WorkflowError(400, "progress is two whole numbers, done/total (120/800)")
        a, b = int(m.group(1)), int(m.group(2))
    elif isinstance(value, dict) and set(value) == {"done", "total"}:
        a, b = value["done"], value["total"]
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (a, b)):
            raise WorkflowError(400, "progress is two whole numbers, done/total (120/800)")
    else:
        raise WorkflowError(400, "progress is two whole numbers, done/total (120/800)")
    if not 0 <= a <= b <= 10 ** 9 or b == 0:
        raise WorkflowError(400, "progress must be done/total with 0 <= done <= total and total above 0")
    return {"done": a, "total": b}


def _private_dir(path: str) -> None:
    """A folder of this store: made if need be, mode 700 whatever the umask."""
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _write_private(path: str, data: bytes, exclusive: bool = False) -> None:
    """Whole or not at all, mode 600. exclusive: refused (FileExistsError) when the file exists. The
    temporary file has a fresh random name and is created O_EXCL|O_NOFOLLOW, so a link planted under a
    name it might take is never followed; os.replace then swaps the name itself, never a link's target."""
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, data)
        finally:
            os.close(fd)
        return
    tmp = f"{path}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)                        # our own half-written temporary file, never another
        raise


def _jbytes(obj, indent=None) -> bytes:
    return (json.dumps(obj, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def _inside(real: str, roots) -> bool:
    return any(real == r or real.startswith(r.rstrip("/") + "/") for r in roots)


def _repo_of(path: str) -> str | None:
    """The folder holding the nearest .git above `path` (the repo a task there works on), else None."""
    p = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(p, ".git")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return None
        p = up


def _local_midnight(now: float) -> float:
    day = datetime.fromtimestamp(now)
    return datetime(day.year, day.month, day.day).timestamp()


# ---------- the order of the steps ----------
def dag_order(steps: list) -> list:
    """The steps' keys in an order where each comes after everything it waits on (ties in spec order).
    Refuses (400) a wait on a step that does not exist, then a cycle, naming the steps."""
    keys = [s["key"] for s in steps]
    known = set(keys)
    for s in steps:
        for dep in s["after"]:
            if dep not in known:
                raise WorkflowError(400, f"step '{s['key']}' waits on '{dep}', which does not exist")
    waits = {s["key"]: set(s["after"]) for s in steps}
    ready = [key for key in keys if not waits[key]]
    scheduled = set(ready)
    order = []
    for key in ready:
        order.append(key)
        for dependent in keys:
            waits[dependent].discard(key)
            if dependent not in scheduled and not waits[dependent]:
                scheduled.add(dependent)
                ready.append(dependent)
    if len(order) != len(keys):
        remaining = [key for key in keys if key not in scheduled]
        route = []
        node = remaining[0]
        while node not in route:
            route.append(node)
            node = next(dep for dep in keys if dep in waits[node])
        ring = set(route[route.index(node):])
        cycle = [key for key in keys if key in ring]
        if len(cycle) == 1:
            raise WorkflowError(400, f"step '{cycle[0]}' waits on itself")
        raise WorkflowError(400, f"steps {_and(cycle)} wait on each other")
    return order


# ---------- validation ----------
def _at(value, key: str, roots, kind: str) -> str | None:
    """A step's place: None (this machine), machine:<name>, place:<label>, or a folder path inside the roots."""
    if value in (None, ""):
        if kind == "task":
            raise WorkflowError(400, f"step '{key}' is a task, so it needs a folder: "
                                     "set its 'at' to a folder on the map")
        return None
    if not isinstance(value, str) or "\x00" in value or len(value) > 1000 or _unencodable(value):
        raise WorkflowError(400, f"step '{key}': 'at' is a folder path, machine:<name> or place:<name>")
    if value.startswith("machine:"):
        if not MACHINE_RE.fullmatch(value):
            raise WorkflowError(400, f"step '{key}': a machine name is letters, digits, dots and dashes (64 at most)")
        if kind == "task":
            raise WorkflowError(400, f"step '{key}' is a task, so it needs a folder on this map, not a machine")
        return value
    if value.startswith("place:"):
        label = _plain(value[6:], f"step '{key}': the place name", BOUNDS["place"])
        if kind == "task":
            raise WorkflowError(400, f"step '{key}' is a task, so it needs a folder on this map, not a place")
        return "place:" + label
    if not value.startswith("/"):
        raise WorkflowError(400, f"step '{key}': 'at' is a folder path, machine:<name> or place:<name>")
    norm = os.path.normpath(value)
    real = os.path.realpath(norm)
    if roots and not _inside(real, roots):
        raise WorkflowError(403, f"step '{key}' is at {norm}, which is outside the folders this map shows")
    if kind == "task":
        folder = os.path.dirname(real) if os.path.isfile(real) else real
        if not os.path.isdir(folder):
            raise WorkflowError(404, f"step '{key}' works in {norm}, which does not exist")
    return norm


def _task_spec(raw, key: str, runners: dict | None) -> dict:
    if not isinstance(raw, dict):
        raise WorkflowError(400, f"step '{key}' is a task: give it a task object {{text, runner, ...}}")
    extra = set(raw) - TASK_FIELDS
    if extra:
        raise WorkflowError(400, f"step '{key}': unknown task field '{sorted(extra)[0]}'")
    import tasks as _tasks
    text = _plain(raw.get("text"), f"step '{key}': the task text", _tasks.MAX_TEXT, multiline=True)
    runner = raw.get("runner")
    if not isinstance(runner, str) or not _tasks.RUNNER_RE.fullmatch(runner):
        raise WorkflowError(400, f"step '{key}': name the agent that does it (runner)")
    if runners is not None:
        if runner not in runners:
            offered = ", ".join(sorted(runners)) or "none is set up"
            raise WorkflowError(400, f"step '{key}': pick one of the offered agents ({offered})")
        if runners[runner].get("role") == "check":
            raise WorkflowError(400, f"step '{key}': {runner} only checks work; pick an agent that does it")
    model = raw.get("model")
    if model in ("",):
        model = None
    if model is not None:
        if not isinstance(model, str) or not _tasks.MODEL_RE.fullmatch(model):
            raise WorkflowError(400, f"step '{key}': model must be a model name")
        if runners is not None and not any(m["name"] == model for m in runners[runner].get("models", [])):
            raise WorkflowError(400, f"step '{key}': {runner} offers no model named {model}")
    verify = _plain(raw.get("verify"), f"step '{key}': verify", _tasks.MAX_VERIFY, required=False)
    setup = _plain(raw.get("setup"), f"step '{key}': setup", _tasks.MAX_VERIFY, required=False)
    retries = _int(raw.get("retries"), f"step '{key}': retries", 0, BOUNDS["retries"], 0)
    if retries and not verify:
        raise WorkflowError(400, f"step '{key}': retries need a verify command to retry against")
    checker = raw.get("check_runner")
    if checker in ("",):
        checker = None
    if checker is not None:
        if not isinstance(checker, str) or not _tasks.RUNNER_RE.fullmatch(checker):
            raise WorkflowError(400, f"step '{key}': check_runner must name an agent")
        if _tasks.same_family(runner, checker, runners, model):        # the model the task runs decides
            raise WorkflowError(400, "a second opinion must come from a different model family")
        if runners is not None:
            if checker not in runners:
                offered = ", ".join(sorted(runners)) or "none is set up"
                raise WorkflowError(400, f"step '{key}': pick a checker among the offered agents ({offered})")
            if runners[checker].get("role") == "work":
                raise WorkflowError(400, f"step '{key}': {checker} does work but does not check it; "
                                         "pick another checker")
    wait = raw.get("wait")
    if wait is not None and wait not in WAITS:
        raise WorkflowError(400, f"step '{key}': wait is clear or merged")
    return {"text": text, "runner": runner, "model": model, "verify": verify, "retries": retries,
            "setup": setup, "check_runner": checker, "wait": wait}


def validate(spec, roots=(), runners: dict | None = None, agent_verify: bool = False, owner: bool = False) -> tuple:
    """(clean spec, notes) or WorkflowError. The checks run in this order: the fields, each step, then the
    order of the steps (dag_order: a wait on a missing step, then a cycle), then what the order decides (a
    task's inputs, its default wait). A spec whose task steps carry verify, setup or retries is forced to
    start: plan unless agent_verify is on (or the owner made it): those lines run as the person."""
    if not isinstance(spec, dict):
        raise WorkflowError(400, "a workflow spec is a JSON object {title, steps, ...}")
    extra = set(spec) - SPEC_KEYS
    if extra:
        raise WorkflowError(400, f"unknown field '{sorted(extra)[0]}' "
                                 f"(the fields are {', '.join(sorted(SPEC_KEYS))})")
    roots = [os.path.realpath(r) for r in roots if r]
    title = _plain(spec.get("title"), "the title", BOUNDS["title"])
    goal = _plain(spec.get("goal"), "the goal", BOUNDS["goal"], required=False) or ""
    by = spec.get("by")
    if isinstance(by, str):
        by = {"name": by}
    if by is None:
        by = {"name": "an agent"}
    if not isinstance(by, dict) or set(by) - {"name", "seat"}:
        raise WorkflowError(400, "by is {name, seat}: who made this workflow")
    by = {"name": _plain(by.get("name"), "by.name", BOUNDS["by_name"], required=False) or "an agent",
          "seat": _plain(by.get("seat"), "by.seat", BOUNDS["by_name"], required=False)}
    start = spec.get("start", "now")
    if start not in STARTS:
        raise WorkflowError(400, "start is now or plan")
    stale = _int(spec.get("stale_after_min"), "stale_after_min", 1, 1440, BOUNDS["stale_after_min"])
    raw_steps = spec.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise WorkflowError(400, "a workflow needs at least one step")
    if len(raw_steps) > BOUNDS["steps"]:
        raise WorkflowError(400, f"a workflow holds at most {BOUNDS['steps']} steps; this one has {len(raw_steps)}")
    steps, seen = [], set()
    for i, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise WorkflowError(400, f"step {i + 1} is not an object")
        key = raw.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key):
            raise WorkflowError(400, f"step {i + 1}: its key is 1-24 lower-case letters, digits or dashes")
        if key in seen:
            raise WorkflowError(400, f"two steps are called '{key}'")
        seen.add(key)
        extra = set(raw) - STEP_FIELDS
        if extra:
            raise WorkflowError(400, f"step '{key}': unknown field '{sorted(extra)[0]}'")
        label = _plain(raw.get("label"), f"step '{key}': the label", BOUNDS["label"])
        kind = raw.get("kind", "note")
        if kind not in KINDS:
            raise WorkflowError(400, f"step '{key}': kind is note, task or gate")
        after = raw.get("after") or []
        if not isinstance(after, list) or not all(isinstance(a, str) for a in after):
            raise WorkflowError(400, f"step '{key}': after is a list of step keys")
        after = list(dict.fromkeys(after))
        if len(after) > BOUNDS["after"]:
            raise WorkflowError(400, f"step '{key}' waits on {len(after)} steps; {BOUNDS['after']} at most")
        at = _at(raw.get("at"), key, roots, kind)
        inputs = raw.get("inputs") or []
        if inputs and kind != "task":
            raise WorkflowError(400, f"step '{key}': inputs are read by task steps only")
        if not isinstance(inputs, list) or len(inputs) > BOUNDS["inputs"]:
            raise WorkflowError(400, f"step '{key}': inputs is a list of {BOUNDS['inputs']} at most")
        clean_inputs = []
        for inp in inputs:
            if not isinstance(inp, dict) or set(inp) != {"from", "path"} or not isinstance(inp["from"], str):
                raise WorkflowError(400, f"step '{key}': each input is {{from, path}}")
            name = inp["path"]
            if (not isinstance(name, str) or not name or "/" in name or "\x00" in name or name.startswith(".")
                    or len(name) > 120 or _unencodable(name)):
                raise WorkflowError(400, f"step '{key}': an input's path is the file's name, as its step declared it")
            clean_inputs.append({"from": inp["from"], "path": name})
        task = _task_spec(raw.get("task"), key, runners) if kind == "task" else None
        if kind != "task" and raw.get("task") is not None:
            raise WorkflowError(400, f"step '{key}': only a task step has a task")
        note = _plain(raw.get("note"), f"step '{key}': the note", BOUNDS["says"], required=False)
        step = {"key": key, "label": label, "kind": kind, "at": at, "after": after}
        if clean_inputs:
            step["inputs"] = clean_inputs
        if task:
            step["task"] = task
        if note:
            step["note"] = note
        steps.append(step)
    order = dag_order(steps)
    by_key = {s["key"]: s for s in steps}
    ancestors = {}
    for k in order:
        acc = set()
        for dep in by_key[k]["after"]:
            acc |= {dep} | ancestors[dep]
        ancestors[k] = acc
    for s in steps:
        for inp in s.get("inputs", []):
            if inp["from"] not in ancestors[s["key"]]:
                raise WorkflowError(400, f"step '{s['key']}' reads an input from '{inp['from']}', "
                                         f"which it does not wait on")
    for s in steps:
        task = s.get("task")
        if task and task["wait"] is None:              # merged when a later task step works on the same repo
            repo = _repo_of(s["at"])
            later = [t for t in steps if t.get("task") and s["key"] in ancestors[t["key"]]]
            task["wait"] = "merged" if repo and any(_repo_of(t["at"]) == repo for t in later) else "clear"
    notes = []
    shell = [s for s in steps
             if s.get("task") and (s["task"]["verify"] or s["task"]["setup"] or s["task"]["retries"])]
    if shell and start == "now" and not (agent_verify or owner):
        start = "plan"
        notes.append("steps " + _and([s["key"] for s in shell]) + " run shell lines as you (verify, setup or "
                     "retries), so this workflow waits for your Start")
    clean = {"title": title, "goal": goal, "by": by, "start": start, "stale_after_min": stale, "steps": steps,
             "order": order}
    main_at = spec.get("at")
    if main_at is not None:
        clean["at"] = _at(main_at, "the workflow", roots, "note")
    if spec.get("template") is not None:
        clean["template"] = _plain(spec.get("template"), "template", 40)
    return clean, notes


# ---------- where a step is on the map ----------
class _Resolver:
    """A step's `at` -> (planet_uid, moon_uid, place name, where) on the current map (render-game.py's chars):
    the deepest planet whose folder holds it (sky._Planets), and the room (moon) of that planet that does."""

    def __init__(self, chars=()):
        import sky as _sky
        rows = [c for c in (chars or []) if isinstance(c, dict)]
        self.planets = _sky._Planets(rows)
        self.names, self.rooms = {}, []
        for c in rows:
            if c.get("kind") != "area" or not isinstance(c.get("uid"), str):
                continue
            self.names.setdefault(c["uid"], c.get("name") if isinstance(c.get("name"), str) else None)
            for r in c.get("rooms") or []:
                if (isinstance(r, dict) and isinstance(r.get("uid"), str)
                        and isinstance(r.get("path"), str) and r["path"]):
                    self.rooms.append((os.path.normpath(r["path"]), r["uid"], c["uid"]))
        self.rooms.sort(key=lambda r: -len(r[0]))

    def where(self, at) -> tuple:
        if at is None:
            return None, None, "this machine", "here"
        if at.startswith("machine:"):
            return None, None, at[8:], "machine"
        if at.startswith("place:"):
            return None, None, at[6:], "place"
        planet = self.planets.of_path(at)
        moon = None
        if planet:
            for path, uid, area in self.rooms:
                if area == planet and (at == path or at.startswith(path.rstrip("/") + "/")):
                    moon = uid
                    break
        name = self.names.get(planet) if planet else None
        return planet, moon, name or os.path.basename(at.rstrip("/")) or at, "folder"


# ---------- the plain sentence ----------
def _fallback_sentence(s: dict) -> str:
    """The summary's one line when words.wf_sentence gives none (the words module owns the real one)."""
    steps = s.get("steps") or []

    def at(st):
        return f" in {st.get('place')}" if st.get("place") else ""
    state = s.get("state")
    pick = (lambda want: next((st for st in steps if st.get("state") == want), None))
    if state == "needs_you":
        st = pick("needs_you")
        if st and st.get("kind") == "gate":
            out = f"Waiting for you: approve '{st['label']}'{at(st)}."
        elif st:
            out = f"Waiting for you: {st.get('why') or 'look at'} '{st['label']}'{at(st)}."
        else:
            out = "Waiting for you."
    elif state == "failed":
        st = pick("failed")
        out = (f"'{st['label']}' failed{at(st)}: {_cut(st.get('why') or 'see its details', 60)}." if st
               else "A step failed.")
    elif state in ("working", "stale"):
        st = pick("working") or pick("ready")
        if st:
            prog = st.get("progress")
            tail = f" · {prog['done']} of {prog['total']}" if isinstance(prog, dict) else ""
            out = ("No news from" if state == "stale" else "Now:") + f" {st['label']}{at(st)}{tail}."
            nxt = next((n for n in steps if n.get("state") == "waiting"), None)
            if nxt:
                out += f" Next: {nxt['label']}{at(nxt)}."
        else:
            out = "Working."
    elif state == "planned":
        first = steps[0] if steps else None
        out = f"Planned: {len(steps)} steps, starting with {first['label']}{at(first)}." if first else "Planned."
    elif state == "done":
        out = f"Done: {s.get('done', 0)} of {s.get('total', 0)} steps."
    elif state == "stopped":
        out = f"Stopped after {s.get('done', 0)} of {s.get('total', 0)} steps."
    else:
        out = ""
    return _cut(out, 140)


def sentence_of(summary: dict) -> str:
    try:
        import words as _words
        said = _words.wf_sentence(summary)
    except Exception:                      # the words are a nicety: never a dead summary
        said = ""
    return said if isinstance(said, str) and said.strip() else _fallback_sentence(summary)


# ---------- replay ----------
FRAME_DETAIL = ("says", "why", "progress", "output", "artifact")     # what a step frame carries beyond its state


def _detail_of(step: dict) -> dict:
    """A summary step's (or a frame's) detail as a replay folds it: says as text, the rest as they are."""
    out = {k: step.get(k) for k in FRAME_DETAIL}
    out["says"] = out["says"] or ""
    return out


def fold(frames: list, steps: list, detail: bool = False) -> dict:
    """What a replay's frames add up to: {"state", "steps": {key: state}}, from every step waiting and the
    workflow planned; with detail, also "detail": {key: {says, why, progress, output, artifact}} (a frame
    sets the fields it carries). The page folds the same frames through its live render path."""
    out = {"state": "planned", "steps": {s["key"]: "waiting" for s in steps}}
    more = {s["key"]: _detail_of({}) for s in steps}
    for f in frames:
        if f.get("key") is None:
            out["state"] = f.get("state")
        elif f.get("key") in out["steps"]:
            out["steps"][f["key"]] = f.get("state")
            for k in FRAME_DETAIL:
                if k in f:
                    more[f["key"]][k] = (f[k] or "") if k == "says" else f[k]
    if detail:
        out["detail"] = more
    return out


def summary_frames(summary: dict) -> list:
    """Replay frames for a workflow that has no events of its own (a derived one, the night run): each step
    in order goes working, then to its state."""
    t = _epoch(summary.get("updated")) or 0.0
    steps = summary.get("steps") or []
    total = len(steps)
    frames, seq = [], 0
    for i, st in enumerate(steps):
        for state in (("working", st.get("state")) if st.get("state") not in ("waiting",) else ("waiting",)):
            seq += 1
            frames.append({"seq": seq, "t": t - (total - i) * 60, "key": st["key"], "state": state,
                           **_detail_of(st), "planet_uid": st.get("planet_uid"),
                           "place": st.get("place"), "by": (summary.get("by") or {}).get("name") or "an agent"})
    seq += 1
    frames.append({"seq": seq, "t": t, "key": None, "state": summary.get("state"), "says": "", "why": None,
                   "planet_uid": None, "place": None, "by": "map"})
    return frames


# ---------- templates (the owner runs a workflow themselves) ----------
TEMPLATES = {
    "fix-and-check": {
        "label": "Fix and check", "desc": "an agent does it in its own safe copy, another model family checks it, "
                                          "then you look at the changes",
        "spec": {"title": "{goal}", "goal": "{goal}", "at": "{at}", "steps": [
            {"key": "fix", "label": "Do it", "kind": "task", "at": "{at}",
             "task": {"text": "{goal}", "runner": "{runner}", "check_runner": "{checker}", "wait": "merged"}},
            {"key": "look", "label": "Look at the changes", "kind": "gate", "at": "{at}", "after": ["fix"]}]},
    },
    "review-folder": {
        "label": "Review this folder",
        "desc": "an agent reads the folder and writes REVIEW.md, changing nothing else",
        "spec": {"title": "{goal}", "goal": "{goal}", "at": "{at}", "steps": [
            {"key": "review", "label": "Write the review", "kind": "task", "at": "{at}",
             "task": {"text": "Read this folder and write REVIEW.md: what is broken, what is unclear, what to do "
                              "first. Change nothing else. Goal: {goal}", "runner": "{runner}"}},
            {"key": "read", "label": "Read the review", "kind": "gate", "at": "{at}", "after": ["review"]}]},
    },
    "two-opinions": {
        "label": "Two opinions", "desc": "two agents of different model families do the same thing side by side; "
                                         "you pick one",
        "spec": {"title": "{goal}", "goal": "{goal}", "at": "{at}", "steps": [
            {"key": "one", "label": "First opinion", "kind": "task", "at": "{at}",
             "task": {"text": "{goal}", "runner": "{runner}", "wait": "clear"}},
            {"key": "two", "label": "Second opinion", "kind": "task", "at": "{at}",
             "task": {"text": "{goal}", "runner": "{checker}", "wait": "clear"}},
            {"key": "pick", "label": "Pick one", "kind": "gate", "at": "{at}", "after": ["one", "two"]}]},
    },
}


def checker_for(runner: str, runners: dict | None, does: str = "check") -> str | None:
    """The first configured agent of another model family that can do `does`: "check" (a second opinion on
    the work: any role but work-only) or "work" (the second agent of two-opinions does the work itself: any
    role but check-only). None when there is none."""
    import tasks as _tasks
    barred = "work" if does == "check" else "check"
    for name, spec in (runners or {}).items():
        if name != runner and spec.get("role") != barred and not _tasks.same_family(runner, name, runners):
            return name
    return None


def _fill(value, holes: dict):
    if isinstance(value, str):
        for k, v in holes.items():
            value = value.replace("{" + k + "}", v)
        return value
    if isinstance(value, list):
        return [_fill(v, holes) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, holes) for k, v in value.items()}
    return value


def instantiate(name: str, at: str, goal: str, runner: str, runners: dict | None = None,
                saved_dir: str | None = None) -> dict:
    """A template with its holes filled: {at} the planet's folder, {goal} the user's one sentence (<= 140), {runner}
    the agent the user picked, {checker} the first agent of another family that checks (none: the check is dropped
    and the step's note says so). Returns a spec that passes validate(): start plan, by you, no shell lines
    in a built-in template."""
    goal = _plain(goal, "the goal", BOUNDS["goal"])
    if not isinstance(at, str) or not at.startswith("/"):
        raise WorkflowError(400, "a template runs at a folder: give its path")
    if not isinstance(runner, str) or not runner:
        raise WorkflowError(400, "pick the agent that does it")
    if runners is not None and runner not in runners:
        raise WorkflowError(400, "connect an agent first: "
                            + (f"{runner} is not set up" if runners else "none is set up"))
    tpl = TEMPLATES.get(name)
    saved = None
    if tpl is None:
        saved = _read_saved(saved_dir, name)
        if saved is None:
            raise WorkflowError(404, f"no template called {name}")
    spec = copy.deepcopy(tpl["spec"] if tpl else saved)
    # {checker} as a step's runner (two-opinions) does the work; as its check_runner it only reads it
    works = any((st.get("task") or {}).get("runner") == "{checker}" for st in spec["steps"])
    checker = checker_for(runner, runners, "work" if works else "check")
    holes = {"at": os.path.normpath(at), "goal": goal, "runner": runner, "checker": checker or ""}
    if not checker:
        kept = []
        for st in spec["steps"]:
            task = st.get("task") or {}
            if task.get("runner") == "{checker}":
                gone = st["key"]                      # two-opinions: the second agent's step goes
                for other in spec["steps"]:
                    if gone in (other.get("after") or []):
                        other["after"] = [a for a in other["after"] if a != gone]
                        other["note"] = NO_CHECKER
                continue
            if task.get("check_runner") == "{checker}":
                task.pop("check_runner")
                st["note"] = NO_CHECKER
            kept.append(st)
        spec["steps"] = kept
    spec = _fill(spec, holes)
    if len(spec.get("title") or "") > BOUNDS["title"]:
        spec["title"] = _cut(spec["title"], BOUNDS["title"])
    spec["start"] = "plan"
    spec["by"] = {"name": OWNER, "seat": None}
    spec["template"] = name
    return spec


def _goal_hole(text: str, goal: str) -> str:
    """A saved task text with {goal} put back: the whole text when it is the goal, whatever its length;
    inside a longer text only where the goal stands as words of its own and is long enough (8 characters
    or two words) not to be a common word that happens to be there ("Fix" in "Fix the tests")."""
    if text == goal:
        return "{goal}"
    if len(goal) < 8 and len(goal.split()) < 2:
        return text
    return re.sub(r"(?<!\w)" + re.escape(goal) + r"(?!\w)", lambda _m: "{goal}", text)


def _read_saved(saved_dir: str | None, name: str) -> dict | None:
    if not saved_dir or not isinstance(name, str) or not TEMPLATE_NAME_RE.fullmatch(name):
        return None
    try:
        with open(os.path.join(saved_dir, name + ".json"), encoding="utf-8") as handle:
            rec = json.load(handle)
    except (OSError, ValueError):
        return None
    return rec.get("spec") if isinstance(rec, dict) and isinstance(rec.get("spec"), dict) else None


def list_templates(saved_dir: str | None = None) -> list:
    """The three built-ins, then the user's saved ones by name: [{name, label, desc, steps, saved}]."""
    out = [{"name": k, "label": v["label"], "desc": v["desc"], "steps": len(v["spec"]["steps"]), "saved": False}
           for k, v in TEMPLATES.items()]
    try:
        names = sorted(n[:-5] for n in os.listdir(saved_dir or "") if n.endswith(".json"))
    except OSError:
        names = []
    for n in names:
        spec = _read_saved(saved_dir, n)
        if spec is not None and n not in TEMPLATES:
            out.append({"name": n, "label": _cut(spec.get("title") or n, 60), "desc": "saved by you",
                        "steps": len(spec.get("steps") or []), "saved": True})
    return out


def schema() -> dict:
    """The spec and show-op schema an agent reads to discover the API (`wf schema`)."""
    return {
        "spec": {
            "title": f"text <= {BOUNDS['title']}", "goal": f"text <= {BOUNDS['goal']}",
            "by": {"name": f"text <= {BOUNDS['by_name']}", "seat": "text or null"},
            "start": "now | plan (steps with verify, setup or retries always wait for the owner's Start)",
            "stale_after_min": f"whole minutes, default {BOUNDS['stale_after_min']}",
            "steps": f"<= {BOUNDS['steps']} steps",
            "step": {"key": "[a-z0-9-]{1,24}", "label": f"text <= {BOUNDS['label']}",
                     "at": "a folder path on the map | machine:<name> | place:<label <= 24> | omitted (this machine)",
                     "kind": "note (you report it) | task (an agent of the map does it) | gate (the owner approves)",
                     "after": f"<= {BOUNDS['after']} step keys", "inputs": "[{from, path}] task steps only",
                     "task": {"text": "what to do", "runner": "an offered agent", "model": "optional",
                              "verify": "a shell line that must pass", "retries": f"0-{BOUNDS['retries']}",
                              "setup": "a shell line", "check_runner": "an agent of another model family",
                              "wait": "clear | merged"}},
        },
        "states": {"step": list(STEP_STATES), "workflow": list(WORKFLOW_STATES)},
        "update": {"state": list(NOTE_STATES), "says": f"text <= {BOUNDS['says']}", "progress": "done/total",
                   "output": "an absolute file path on the map, or text"},
        # what POST /agent/show (and `container-map show`) takes; the bridge validates it (bridge.py clean_show)
        "show": {"by": "text <= 24, default 'an agent' (every rendering names the author)",
                 "ttl": f"seconds, {BOUNDS['show_ttl_min_s']}-{BOUNDS['show_ttl_max_s']}, default 300 (a show "
                        f"with a page op: up to {BOUNDS['page_ttl_max_s']}, default {BOUNDS['page_ttl_default_s']})",
                 "id": "optional [a-z0-9-]{1,40}: a show with the same id is replaced",
                 "at": "where its ops are drawn: a uid (area:..., dir:...), place:<label <= 24> or a folder path",
                 "ops": f"<= {BOUNDS['show_items']} ops; an unknown or bad op is skipped and listed, the rest kept; "
                        "an out-of-range value is clamped and named in 'clamped'",
                 "live": f"<= {BOUNDS['shows_live']} at once (a new one retires the oldest; at most "
                         f"{BOUNDS['pages_live']} of them pages); memory only"},
        "show_ops": {"every op": {"at": "uid | place:<label> | folder path (default: the show's at)",
                                  "xray": "true: the 3D view draws it through planets"},
                     "label": {"text": "<= 80"},
                     "marker": {"icon": ["flag", "pin", "star", "warn"], "text": "<= 40"},
                     "ring": {"state": "a step state", "progress": "done/total"},
                     "beam": {"from": "uid | place:<label>", "to": "uid | place:<label>"},
                     "route": {"stops": "2-8 uids or places"}, "radio": {"from": "name <= 24", "text": "<= 80"},
                     "fx": {"preset": ["sparkle", "ring", "burst", "text"], "color": "#RRGGBB or a step state",
                            "count": "1-64", "life": "0.2-3 seconds", "text": "<= 24 (preset text)"},
                     "card": {"title": "<= 40", "rows": "<= 8 of [k <= 20, v <= 40]", "text": "<= 200",
                              "bar": "[a, b], 0 <= a <= b <= 10^6"},
                     "chart": {"label": "<= 40", "values": "<= 64 finite numbers", "unit": "<= 8",
                               "kind": ["spark", "bars"]},
                     "page": {"title": f"<= {BOUNDS['page_title']}",
                              "html": f"a whole HTML page, <= {BOUNDS['page_bytes']} bytes of UTF-8, no control "
                                      "characters but newline, return and tab. The person opens it in a sandboxed "
                                      "panel on the desktop: scripts run, but all JS and CSS must be inline in "
                                      "<script> and <style> blocks, images and fonts are data: or blob: URIs; "
                                      "the page's document is served with a policy and a connection allowlist "
                                      "that stop every request it can make (fetch, scripts, styles, images, fonts, "
                                      "frames, forms, links out). "
                                      "One page op a show; a page with no at (on the op or the show) sits at "
                                      "place:pages"}},
        "control": {"point": {"target": "uid | place:<label>", "text": "<= 80", "seconds": "5-120",
                              "by": "<= 24, default an agent"},
                    "focus": {"target": "uid", "by": "<= 24, default an agent"},
                    "follow": {"workflow": "a workflow id", "by": "<= 24, default an agent"}},
        "bounds": dict(BOUNDS),
        "templates": [t["name"] for t in list_templates()],
    }


# ---------- the night run as a workflow (an automatic producer) ----------
NIGHT_STATE = {"ready": "done", "failed": "failed"}


def night_workflow(night, chars=(), now=None) -> dict | None:
    """The night report today.py read (today.night: {date, items: [{id, title, repo, verdict, minutes}], ...})
    as a read-only derived workflow "Night run · 3 tasks", one step per task that ran, with its verdict. A
    refused item never ran ("3 ran, 2 ready, 1 failed, 1 refused" is three tasks): it is not a step; the
    night's line (the goal) still says it and the sky's night source lists it. None when no item ran. It
    carries "derived": "night": the sky already lists the items, so it adds no contact."""
    if not isinstance(night, dict):
        return None
    items = [i for i in (night.get("items") or []) if isinstance(i, dict)
             and not str(i.get("verdict") or "").strip().lower().startswith("refus")]
    if not items:
        return None
    day = night.get("date")
    if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        day = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
    resolver = _Resolver(chars)
    wid = f"w-{day.replace('-', '')}-0400-night-run-{hashlib.sha256(day.encode()).hexdigest()[:6]}"
    try:
        at4 = datetime.strptime(day + " 04:00", "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        at4 = 0.0
    steps, minutes = [], 0.0
    for i, item in enumerate(items[:BOUNDS["steps"]]):
        verdict = str(item.get("verdict") or "").lower()
        state = NIGHT_STATE.get(verdict) or ("failed" if "fail" in verdict else "done")
        repo = item.get("repo") if isinstance(item.get("repo"), str) else None
        planet = moon = None
        place, where = "the night queue", "machine"
        if repo:
            planet = resolver.planets.of_path(repo) or resolver.planets.of_name(repo)
            if planet:
                place, where = resolver.names.get(planet) or repo, "folder"
        mins = item.get("minutes")
        mins = mins if isinstance(mins, (int, float)) and not isinstance(mins, bool) and mins >= 0 else None
        minutes += mins or 0
        says = {"done": "ready for your keep or drop", "failed": "failed in the night run"}[state]
        if mins:
            says += f" · {mins:g} min"
        label = _cut(item.get("title") or item.get("id") or f"task {i + 1}", BOUNDS["label"])
        steps.append({"key": f"n{i + 1}", "label": label,
                      "kind": "task", "state": state, "planet_uid": planet, "moon_uid": moon, "place": place,
                      "after": [], "says": says, "progress": None, "output": None, "artifact": None,
                      "tid": None, "attempt": 1, "why": None if state == "done" else says, "where": where})
    n = len(steps)
    out = {"id": wid, "title": f"Night run · {n} task" + ("" if n == 1 else "s"),
           "goal": _cut(night.get("line") or "", BOUNDS["goal"]),
           "by": {"name": "night queue", "seat": None},
           "state": "failed" if any(s["state"] == "failed" for s in steps) else "done",
           "updated": _iso(at4 + minutes * 60), "seq": n,
           "done": sum(s["state"] == "done" for s in steps),
           "total": n, "elapsed_s": int(minutes * 60), "current": [], "sentence": ""}
    out["derived"] = "night"
    out["steps"] = steps
    out["sentence"] = sentence_of(out)
    return out


# ---------- the store ----------
class _Bucket:
    """A token bucket: `burst` at once, `rate` a second after that."""

    def __init__(self, burst: float, rate: float):
        self.burst, self.rate, self.level, self.at = float(burst), float(rate), float(burst), None

    def take(self, now: float) -> bool:
        if self.at is not None:
            self.level = min(self.burst, self.level + max(0.0, now - self.at) * self.rate)
        self.at = now
        if self.level >= 1.0:
            self.level -= 1.0
            return True
        return False


class _Rec:
    """One workflow as read inside its lock: its folder, workflow.json, the events and latest.json."""

    def __init__(self, path, meta, events, latest):
        self.path, self.meta, self.events, self.latest = path, meta, events, latest
        self.spec = meta["spec"]
        self.id = meta["id"]
        self.steps = {s["key"]: s for s in self.spec["steps"]}
        self.seq = events[-1]["seq"] if events else 0


class WorkflowStore:
    """Every workflow under one folder. Each mutation of one workflow runs under that workflow's lock and
    re-reads its state inside it, so the bridge and the CLI can both tick it safely."""

    _SEGMENTS: dict = {}                    # segment path -> ((mtime_ns, size), rows): parsed once per change
    _SEGMENTS_LOCK = threading.Lock()
    # how a token's digest is compared (constant time); the bridge hands in its own _same, so the workflow and
    # step tokens are checked by the one comparison every bridge credential goes through
    same = staticmethod(_same)

    def __init__(self, workflows_dir: str, task_store=None, roots=(), artifact_mb: int = 32, now=None,
                 launches_per_day: int = LAUNCHES_PER_DAY, agent_verify: bool = False, chars=()):
        self.workflows_dir = workflows_dir
        self.task_store = task_store
        self.roots = list(roots)
        self.artifact_mb = artifact_mb
        self.now = now
        self.launches_per_day = launches_per_day
        self.agent_verify = bool(agent_verify)
        self.segment_bytes = BOUNDS["segment_mb"] * 1024 * 1024
        self.events_cap = BOUNDS["events_mb"] * 1024 * 1024
        self.file_cap = BOUNDS["artifact_mb"] * 1024 * 1024
        self.last_create = -1e9
        self._gate = threading.Lock()
        self._tick_lock = threading.Lock()
        self._closed: dict = {}                # id -> the stat of its newest segment when it was seen closed
        self._summaries: dict = {}             # id -> (_record_key, summary) of a closed, settled workflow
        self._buckets: dict = {}
        self._overall = _Bucket(BOUNDS["updates_overall_per_s"], BOUNDS["updates_overall_per_s"])
        self.errors: list = []                 # (id, message): records that could not be read on the last list
        self._resolver = None
        self._chars = None
        self.set_chars(chars)

    # ---- plumbing ----
    def _clock(self) -> float:
        return float(self.now()) if callable(self.now) else time.time()

    def set_chars(self, chars) -> None:
        """The map's units (render-game.py's CHARS, the bridge's cached world): where steps are drawn."""
        chars = list(chars or [])
        if self._chars is not None and chars == self._chars:
            return
        self._chars = chars
        self._resolver = None
        self._summaries = {}                   # where a step is drawn depends on the map

    def _where(self, at) -> tuple:
        if self._resolver is None:
            self._resolver = _Resolver(self._chars)
        return self._resolver.where(at)

    def _real_roots(self) -> list:
        return [os.path.realpath(r) for r in self.roots if r]

    def _runners(self) -> dict | None:
        return getattr(self.task_store, "runners", None) if self.task_store is not None else None

    def _ensure_dir(self) -> None:
        if not os.path.isdir(self.workflows_dir):
            try:
                import config as _config
                _config.make_state_dir(self.workflows_dir, 0o700)
            except Exception:
                os.makedirs(self.workflows_dir, mode=0o700, exist_ok=True)
        os.chmod(self.workflows_dir, 0o700)

    def _dir(self, wid: object) -> str:
        if not isinstance(wid, str) or not ID_RE.fullmatch(wid):
            raise WorkflowError(400, "id must name a workflow (w-YYYYMMDD-HHMM-...)")
        path = os.path.join(self.workflows_dir, wid)
        if not os.path.isfile(os.path.join(path, "workflow.json")):
            raise WorkflowError(404, "no such workflow")
        return path

    @contextlib.contextmanager
    def _lock(self, path: str):
        import tasks as _tasks
        try:
            with _tasks._lock_file(os.path.join(path, ".lock"), 30.0, "this workflow"):
                yield
        except _tasks.LockBusy as exc:
            raise WorkflowError(503, exc.message) from exc

    @contextlib.contextmanager
    def _store_lock(self):
        """The whole folder's lock (<workflows_dir>/.lock), for what counts across workflows: the admission of
        a new one (the open cap, the day's creates) and the day's shared launch count with the launch it allows.
        Threads of this process and other processes (the bridge and a CLI) queue on it. Taken last: a holder
        of a workflow's lock may take it, never the other way round."""
        import tasks as _tasks
        self._ensure_dir()
        try:
            with _tasks._lock_file(os.path.join(self.workflows_dir, ".lock"), 30.0, "the workflows folder"):
                yield
        except _tasks.LockBusy as exc:
            raise WorkflowError(503, exc.message) from exc

    def _record_key(self, wid: str):
        """What a closed workflow's summary is cached against: its newest segment and latest.json (stat)."""
        path = os.path.join(self.workflows_dir, wid)
        segs = self._segments(path)
        seg = self._seg_stat(segs[-1]) if segs else None
        if seg is None:
            return None
        return seg, self._seg_stat(os.path.join(path, "latest.json"))

    @staticmethod
    def _settled(summ: dict) -> bool:
        """A closed workflow whose summary nothing outside its own files can change any more: no task step
        whose thread is still open (running, in review, or a failure that retries)."""
        if summ.get("state") not in CLOSED:
            return False
        for s in summ.get("steps") or []:
            if s.get("kind") != "task":
                continue
            status = s.get("task_status")
            if status in ("new", "running", "checking", "stopping", "review", "error"):
                return False
            if status == "failed" and s.get("state") != "failed":
                return False
        return True

    def _closed_summary(self, wid: str) -> dict:
        """summary(wid), from the cache when the workflow is closed, settled and untouched since."""
        key = self._record_key(wid)
        hit = self._summaries.get(wid)
        if hit is not None and key is not None and hit[0] == key:
            return copy.deepcopy(hit[1])
        s = self.summary(wid)
        if key is not None and self._settled(s):
            self._summaries[wid] = (key, copy.deepcopy(s))
        else:
            self._summaries.pop(wid, None)
        return s

    def _segments(self, path: str) -> list:
        try:
            names = sorted(n for n in os.listdir(path) if re.fullmatch(r"events\.\d{6}\.jsonl", n))
        except OSError:
            return []
        return [os.path.join(path, n) for n in names]

    def _rows_of(self, seg: str) -> list:
        """A segment's events. Segments only grow, so one read before is read on from where its last whole
        line ended (a new file under the same name, or a shorter one, is read whole)."""
        try:
            st = os.stat(seg)
        except OSError:
            return []
        with self._SEGMENTS_LOCK:
            hit = self._SEGMENTS.get(seg)
        if hit and hit[0] == (st.st_ino, st.st_size, st.st_mtime_ns):
            return hit[2]
        start, rows = 0, []
        if hit and hit[0][0] == st.st_ino and st.st_size >= hit[1]:
            start, rows = hit[1], list(hit[2])
        try:
            with open(seg, "rb") as handle:
                handle.seek(start)
                data = handle.read()
        except OSError:
            return []
        end = data.rfind(b"\n") + 1                    # a line still being written waits for its newline
        for line in data[:end].split(b"\n"):
            if not line:
                continue
            try:
                row = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("ev"), str) and isinstance(row.get("seq"), int):
                rows.append(row)
        with self._SEGMENTS_LOCK:
            self._SEGMENTS[seg] = ((st.st_ino, st.st_size, st.st_mtime_ns), start + end, rows)
        return rows

    def _read(self, path: str) -> _Rec:
        try:
            with open(os.path.join(path, "workflow.json"), "rb") as handle:
                raw = handle.read()
            meta = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise WorkflowError(409, f"this workflow's record cannot be read ({type(exc).__name__})") from exc
        if (not isinstance(meta, dict) or not isinstance(meta.get("spec"), dict)
                or meta.get("id") != os.path.basename(path)):
            raise WorkflowError(409, "this workflow's record names another workflow; refusing to act on it")
        events = []
        for seg in self._segments(path):
            events.extend(self._rows_of(seg))
        first = events[0] if events else None
        pin = hashlib.sha256(raw).hexdigest()
        if not first or first.get("ev") != "create" or not _same(first.get("pin", ""), pin):
            raise WorkflowError(409, "this workflow's record was altered after it was made; refusing to act on it")
        try:
            with open(os.path.join(path, "latest.json"), encoding="utf-8") as handle:
                latest = json.load(handle)
        except (OSError, ValueError):
            latest = {}
        if not isinstance(latest, dict):
            latest = {}
        return _Rec(path, meta, events, latest)

    def _events_bytes(self, path: str) -> int:
        total = 0
        for seg in self._segments(path):
            with contextlib.suppress(OSError):
                total += os.path.getsize(seg)
        return total

    def _append(self, rec: _Rec, row: dict) -> dict:
        """One event, numbered, stamped and written to the newest segment (a new one past 1 MB)."""
        row = {"seq": rec.seq + 1, "ev": row["ev"], "t": round(self._clock(), 3),
               **{k: v for k, v in row.items() if k != "ev"}}
        segs = self._segments(rec.path)
        seg = segs[-1] if segs else os.path.join(rec.path, "events.000001.jsonl")
        with contextlib.suppress(OSError):
            if os.path.getsize(seg) >= self.segment_bytes:
                n = int(os.path.basename(seg).split(".")[1]) + 1
                seg = os.path.join(rec.path, f"events.{n:06d}.jsonl")
        line = (json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(seg, os.O_WRONLY | os.O_APPEND | os.O_CREAT | NOFOLLOW, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
        rec.events.append(row)
        rec.seq = row["seq"]
        self._nudge()
        return row

    def _nudge(self) -> None:
        """<workflows_dir>/.nudge changes with every event: the bridge's tick loop stats it each second."""
        path = os.path.join(self.workflows_dir, ".nudge")
        with contextlib.suppress(OSError):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | NOFOLLOW, 0o600)
            try:
                os.write(fd, b"1")
                if os.utime in os.supports_fd:
                    os.utime(fd)
            finally:
                os.close(fd)

    def _write_latest(self, rec: _Rec) -> None:
        _write_private(os.path.join(rec.path, "latest.json"), _jbytes(rec.latest))

    # ---- tokens ----
    def _auth(self, rec: _Rec, token, key: str | None = None) -> tuple:
        """("workflow", None) for the workflow's token; ("step", key) for a task step's own token (its own key
        only); else 403."""
        if not isinstance(token, str) or not token:
            raise WorkflowError(403, "this workflow's token is needed (shown once when it was made)")
        got = _digest(token)
        if self.same(got, rec.meta.get("token_sha256", "")):
            return "workflow", None
        for e in reversed(rec.events):
            if e["ev"] == "launch" and e.get("token_sha256") and self.same(got, e["token_sha256"]):
                if key is not None and key != e.get("key"):
                    raise WorkflowError(403, f"this token belongs to step '{e.get('key')}' only")
                return "step", e.get("key")
        raise WorkflowError(403, "wrong token for this workflow")

    def _rate(self, wid: str, now: float) -> bool:
        bucket = self._buckets.setdefault(wid, _Bucket(BOUNDS["updates_burst"], BOUNDS["updates_per_s"]))
        return bucket.take(now) and self._overall.take(now)

    # ---- agents ----
    def create(self, spec: dict, by: dict | None = None, depth: int = 0, owner: bool = False) -> dict:
        """Validate `spec` and write a new workflow: {"id", "token", "start", "notes", "workflow"} (the token is
        never stored, only its sha256). owner: made by the person (a template, Run again)."""
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
            depth = 0
        if depth > BOUNDS["depth"]:
            raise WorkflowError(400, f"a workflow made from inside a workflow task nests {BOUNDS['depth']} deep at "
                                     f"most; this one would be {depth} deep")
        try:
            size = len(json.dumps(spec).encode("utf-8"))
        except (TypeError, ValueError):
            raise WorkflowError(400, "a workflow spec is a JSON object {title, steps, ...}")
        if size > BOUNDS["body_kb"] * 1024:
            raise WorkflowError(413, f"a workflow spec is {BOUNDS['body_kb']} KB at most")
        clean, notes = validate(spec, self._real_roots(), self._runners(), self.agent_verify, owner)
        if isinstance(by, dict) and by:
            clean["by"] = {"name": _plain(by.get("name"), "by.name", BOUNDS["by_name"], required=False) or "an agent",
                           "seat": _plain(by.get("seat"), "by.seat", BOUNDS["by_name"], required=False)}
        now = self._clock()
        with self._gate:
            if time.monotonic() - self.last_create < BOUNDS["create_gap_s"]:
                raise WorkflowError(429, "one new workflow every couple of seconds")
            # counted and made under the folder's lock, so a bridge and a CLI creating at once cannot both
            # take the last open place; every workflow is counted, however many newer closed ones there are
            # (a closed, settled one is read from the cache, so a long history stays cheap)
            with self._store_lock():
                ids = self._ids()
                open_now = 0
                for wid in ids:
                    with contextlib.suppress(WorkflowError, OSError, ValueError, KeyError, TypeError):
                        if self._closed_summary(wid)["state"] not in CLOSED:
                            open_now += 1
                if open_now >= BOUNDS["open"]:
                    raise WorkflowError(429, f"{BOUNDS['open']} workflows are open; let one finish or stop one first")
                today = time.strftime("%Y%m%d", time.localtime(now))
                if (sum(1 for w in ids if w[2:10] == today) >= BOUNDS["creates_per_day"]
                        or self._dir_bytes() >= BOUNDS["dir_mb"] * 1024 * 1024):
                    raise WorkflowError(507, "workflow storage is full: "
                                             "`wf retire --older 30d` moves old ones to an attic folder")
                wid = (f"w-{time.strftime('%Y%m%d-%H%M', time.localtime(now))}-{_slug(clean['title'])}-"
                       f"{secrets.token_hex(3)}")
                token = secrets.token_urlsafe(32)
                path = os.path.join(self.workflows_dir, wid)
                os.mkdir(path, 0o700)
                os.chmod(path, 0o700)
                meta = {"version": 1, "id": wid, "created": _iso(now), "spec": clean,
                        "token_sha256": _digest(token), "depth": depth, "owner": bool(owner)}
                raw = _jbytes(meta, indent=1)
                _write_private(os.path.join(path, "workflow.json"), raw, exclusive=True)
                # the create event before the folder's lock is let go, so the next count sees this one open;
                # nobody else writes here first (every other writer needs this event to read the record)
                self._append(_Rec(path, meta, [], {}), {"ev": "create", "pin": hashlib.sha256(raw).hexdigest(),
                                                        "by": clean["by"]["name"], "owner": bool(owner)})
            self.last_create = time.monotonic()
        with self._lock(path):
            rec = self._read(path)
            summ = self._sync(rec)
        return {"id": wid, "token": token, "start": clean["start"], "notes": notes, "workflow": summ}

    def step(self, wid: str, key: str, token: str, state: str | None = None, says: str | None = None,
             progress: str | None = None, output: str | None = None) -> dict:
        """A note step's update (a task step's token may set says/progress/output only). A same-state update
        within 10 s, or one over the rate, only rewrites latest.json ({"coalesced": true}); one that carries an
        output is never coalesced (the file would be lost) and is refused with 429 over the rate; a state change is
        written, except that a finished step's change (reopened, or moved to another finished state) over the
        rate is refused with 429: forward moves never meet the rate, but working/done/working/... does.
        Past the update budget (8 MB) only a step's move into done, failed or skipped from an open state is
        taken: that move is exempt from the budget and the rate. A repeat of a terminal state
        ("done" again with new progress) or a move between terminal states is an ordinary update, so a token
        holder cannot grow the log past the budget by saying "done" over and over."""
        path = self._dir(wid)
        with self._lock(path):
            rec = self._read(path)
            scope, own = self._auth(rec, token, key if isinstance(key, str) else None)
            st = rec.steps.get(key) if isinstance(key, str) else None
            if st is None:
                raise WorkflowError(404, f"no step '{key}' in this workflow")
            summ = self._derive(rec)
            if summ["state"] == "planned":
                raise WorkflowError(409, "this workflow waits for its owner's Start")
            if summ["state"] == "stopped":
                raise WorkflowError(409, "this workflow was stopped")
            if state is not None:
                if state not in NOTE_STATES:
                    raise WorkflowError(400, "state is working, done, failed or skipped")
                if st["kind"] == "gate":
                    raise WorkflowError(403, "only the owner passes a gate")
                if st["kind"] == "task":
                    raise WorkflowError(403, "the thread engine decides a task step's state; send says, progress "
                                             "or output")
            cur = next(s for s in summ["steps"] if s["key"] == key)
            if state is not None and cur["state"] in ("waiting", "blocked"):
                # a step is reported only once what it waits on is through: otherwise "done" on a note after
                # an unanswered gate would pass the gate for the owner and launch the work behind it
                if cur["state"] == "blocked":
                    raise WorkflowError(409, f"step '{key}' {cur['why'] or 'cannot start: a step before it failed'}")
                by_key = {s["key"]: s for s in summ["steps"]}
                pending = [d for d in st["after"] if by_key[d]["state"] not in ("done", "skipped")]
                raise WorkflowError(409, f"step '{key}' waits on {_and(pending) if pending else 'an earlier step'}; "
                                         "report it once that is through")
            says = _plain(says, "says", BOUNDS["says"], required=False)
            prog = _progress(progress)
            out = self._output(output) if output not in (None, "") else None
            now = self._clock()
            # exempt from the budget and the rate: only the move that finishes an open step, at most
            # once per step until an ordinary (capped) update reopens it
            exempt = state in TERMINAL and cur["state"] not in TERMINAL
            if state is None and says is None and prog is None and out is None:
                raise WorkflowError(400, "say what changed: state, says, progress or output")
            same = state in (None, cur["state"])
            last_t = max([e["t"] for e in rec.events if e["ev"] == "step" and e.get("key") == key] or [0.0])
            coalesce = same and out is None and (now - last_t < COALESCE_S or not self._rate(wid, now))
            if coalesce:
                entry = rec.latest.setdefault("steps", {}).setdefault(key, {})
                if says is not None:
                    entry["says"] = says
                if prog is not None:
                    entry["progress"] = prog
                entry["t"] = now
                rec.latest["t"] = now
                self._write_latest(rec)
                return {"ok": True, "coalesced": True}
            if not exempt and self._events_bytes(path) >= self.events_cap:
                raise WorkflowError(413, "this workflow used its update budget; start a new one")
            if same and out is not None and not self._rate(wid, now):
                # an output is never folded into latest.json (that would drop a declared file), so a same-state
                # update that carries one meets the per-workflow rate instead and is refused past it, nothing
                # written: 10,000 outputs in a burst append a handful, not 20,000 lines
                raise WorkflowError(429, f"step '{key}' takes {BOUNDS['updates_burst']} outputs at once and one a "
                                         "second after that, so send this again in a second")
            if not same and cur["state"] in TERMINAL and not self._rate(wid, now):
                # reopening a finished step is the one state change that can repeat (working, done, working,
                # ...): it meets the per-workflow rate (4 at once, 1 a second), so that loop cannot fill the
                # budget in a second; the move that finishes the step stays exempt
                raise WorkflowError(429, f"step '{key}' is finished; a finished step changes state once a "
                                         "second at most, so send this again in a second")
            row = {"ev": "step", "key": key, "by": rec.spec["by"]["name"] if scope == "workflow" else own}
            if state is not None:
                row["state"] = state
            if says is not None:
                row["says"] = says
            if prog is not None:
                row["progress"] = prog
            self._append(rec, row)
            entry = rec.latest.get("steps", {}).get(key)
            if entry:                                    # the event is newer than anything coalesced
                rec.latest["steps"].pop(key, None)
                self._write_latest(rec)
            if out is not None:
                self._append(rec, {"ev": "output", "key": key, **out})
            if state == "done" or (out is not None and cur["state"] == "done"):
                self._keep_outputs(rec, key, cur.get("tid"))      # a done step's files are kept now
            summ = self._sync(rec)
            return {"ok": True, "workflow": summ}

    def _output(self, value) -> dict:
        """--output PATH|TEXT: an absolute path must be a regular file inside the map's roots (not a link, not a
        secret-shaped name, not under .git); anything else is text."""
        if not isinstance(value, str):
            raise WorkflowError(400, "output is a file path or text")
        if not value.startswith("/"):
            return {"text": _plain(value, "output", BOUNDS["output_text"])}
        why = self._refuse_path(value)
        if why:
            raise WorkflowError(400, f"output {os.path.basename(value)} refused: {why}")
        return {"path": os.path.normpath(value), "text": ""}

    @staticmethod
    def _refuse_name(name: str) -> str | None:
        if not name or name.startswith(".") or SECRET_NAME_RE.search(name):
            return "a hidden or secret-shaped file is never copied"
        low = name.lower()
        if low.startswith(FILE_DENY_PREFIX) or low.endswith(FILE_DENY_END) or LIVE_FILE_NAME_RE.fullmatch(low) \
                or FILE_DENY_NAME_RE.search(low):
            return "a secret-shaped file is never copied"
        return None

    @staticmethod
    def _refuse_parts(path: str) -> str | None:
        """The folders the bridge's /file route never serves from (bridge.py _checked_file)."""
        parts = [p.lower() for p in path.split(os.sep)]
        if any(p in FILE_DENY_PARTS for p in parts):
            return "it is inside a folder of credentials or of git's own"
        if any((a, b) in FILE_DENY_PAIRS for a, b in zip(parts, parts[1:])):
            return "it is inside a folder of credentials or of git's own"
        if any((a, b) == RUN_DIR for a, b in zip(parts, parts[1:])):
            return "it is inside the map's run folder (where a bridge keeps its credentials)"
        if any((a, b) in PRIVATE_DIRS for a, b in zip(parts, parts[1:])):
            return "it is inside the map's private folders (the owner's read chats, another machine's threads)"
        return None

    def _refuse_real(self, real: str) -> str | None:
        """The rules for where a file really is (a resolved path): its name, inside the roots, no folder of
        credentials or of git's own on the way, not in the map's own records. Applied before the open to the
        path's realpath and after it to where the descriptor really went (/proc/self/fd)."""
        why = self._refuse_name(os.path.basename(real))
        if why:
            return why
        if not _inside(real, self._real_roots()):
            return "it is outside the folders this map shows"
        why = self._refuse_parts(real)
        if why:
            return why
        stores = [os.path.realpath(self.workflows_dir)]
        if self.task_store is not None:
            stores.append(os.path.realpath(getattr(self.task_store, "tasks_dir", "") or "/nonexistent"))
        if any(s and _inside(real, [s]) for s in stores):
            return "it is inside the map's own records"
        return None

    def _refuse_path(self, value: str) -> str | None:
        if "\x00" in value or len(value) > 1000 or _unencodable(value):
            return "not a file path"
        norm = os.path.normpath(value)
        why = self._refuse_name(os.path.basename(norm))
        if why:
            return why
        why = self._refuse_parts(norm)
        if why:
            return why
        why = self._refuse_real(os.path.realpath(norm))
        if why:
            return why
        try:
            st = os.lstat(norm)
        except OSError:
            return "no such file"
        if stat.S_ISLNK(st.st_mode):
            return "it is a link"
        if not stat.S_ISREG(st.st_mode):
            return "it is not a regular file"
        return None

    def pulse(self, wid: str, token: str, key: str | None = None) -> dict:
        """Still alive: writes latest.json only; over the rate it is dropped with {"coalesced": true}."""
        path = self._dir(wid)
        with self._lock(path):
            rec = self._read(path)
            scope, own = self._auth(rec, token, key)
            key = own if scope == "step" else key
            if key is not None and key not in rec.steps:
                raise WorkflowError(404, f"no step '{key}' in this workflow")
            now = self._clock()
            if not self._rate(wid, now):
                return {"ok": True, "coalesced": True}
            rec.latest["pulse"] = now
            if key is not None:
                rec.latest.setdefault("steps", {}).setdefault(key, {})["pulse"] = now
            self._write_latest(rec)
            return {"ok": True}

    def done(self, wid: str, token: str) -> dict:
        """The agent says the workflow is over: note and task steps that never started read skipped; a gate
        still waits for the owner."""
        path = self._dir(wid)
        with self._lock(path):
            rec = self._read(path)
            scope, _ = self._auth(rec, token)
            if scope != "workflow":
                raise WorkflowError(403, "a step's token cannot end the workflow")
            if not any(e["ev"] == "done" for e in rec.events):
                self._append(rec, {"ev": "done", "by": rec.spec["by"]["name"]})
            return self._sync(rec)

    def stop(self, wid: str, why: str | None = None, token: str | None = None, owner: bool = False,
             tasks: bool = False) -> dict:
        """No new launches; with owner and tasks, the workflow's running tasks are stopped too."""
        path = self._dir(wid)
        why = _plain(why, "why", BOUNDS["says"], required=False)
        with self._lock(path):
            rec = self._read(path)
            if not owner:
                scope, _ = self._auth(rec, token)
                if scope != "workflow":
                    raise WorkflowError(403, "a step's token cannot stop the workflow")
            stopped_tasks, errors = [], []
            if not any(e["ev"] == "stop" for e in rec.events):
                self._append(rec, {"ev": "stop", "by": OWNER if owner else rec.spec["by"]["name"], "why": why,
                                   "tasks": bool(owner and tasks)})
            if owner and tasks and self.task_store is not None:
                for s in self._derive(rec)["steps"]:
                    if s.get("tid") and s.get("task_status") in ("new", "running", "checking"):
                        try:
                            self.task_store.stop(s["tid"])
                            stopped_tasks.append(s["tid"])
                        except Exception as exc:          # said, never swallowed
                            errors.append(f"{s['key']}: {getattr(exc, 'message', exc)}")
            summ = self._sync(rec)
            return {"ok": True, "workflow": summ, "stopped_tasks": stopped_tasks, "errors": errors}

    # ---- the owner (the page's buttons, `wf start` / `wf gate` on a terminal) ----
    def _owner_action(self, wid: str, fn):
        path = self._dir(wid)
        with self._lock(path):
            rec = self._read(path)
            summ = self._derive(rec)
            fn(rec, summ)
            return self._sync(rec, by=OWNER)

    def start(self, wid: str) -> dict:
        def act(rec, summ):
            if summ["state"] != "planned":
                raise WorkflowError(409, "this workflow has already started")
            self._append(rec, {"ev": "start", "by": OWNER})
        return self._owner_action(wid, act)

    def shell_lines(self, wid: str) -> list:
        """The lines Start runs as the person ("These run as you: ..."): each task step's verify and setup."""
        rec = self._read(self._dir(wid))
        return [{"key": s["key"], "label": s["label"], "verify": s["task"].get("verify"),
                 "setup": s["task"].get("setup"), "retries": s["task"].get("retries", 0)}
                for s in rec.spec["steps"] if s.get("task") and (s["task"].get("verify") or s["task"].get("setup"))]

    def gate(self, wid: str, key: str, passed: bool, why: str | None = None) -> dict:
        why = _plain(why, "why", BOUNDS["says"], required=False)

        def act(rec, summ):
            st = next((s for s in summ["steps"] if s["key"] == key), None)
            if st is None:
                raise WorkflowError(404, f"no step '{key}' in this workflow")
            if st["kind"] != "gate":
                raise WorkflowError(409, f"step '{key}' is not a gate")
            if st["state"] != "needs_you":
                raise WorkflowError(409, "this gate is not waiting for you" if st["state"] != "waiting"
                                    else "this gate has not been reached yet")
            self._append(rec, {"ev": "gate", "key": key, "pass": bool(passed), "by": OWNER, "why": why})
        return self._owner_action(wid, act)

    def retry(self, wid: str, key: str) -> dict:
        def act(rec, summ):
            st = next((s for s in summ["steps"] if s["key"] == key), None)
            if st is None:
                raise WorkflowError(404, f"no step '{key}' in this workflow")
            if summ["state"] == "stopped":
                raise WorkflowError(409, "this workflow was stopped; Run again makes a new copy")
            deferred = st["kind"] == "task" and st["state"] == "ready" and bool(st["why"])
            if st["state"] != "failed" and not deferred:
                raise WorkflowError(409, "only a failed or deferred step can be retried")
            self._append(rec, {"ev": "retry", "key": key, "by": OWNER})     # the user's click: launched now, not counted
        return self._owner_action(wid, act)

    def skip(self, wid: str, key: str) -> dict:
        def act(rec, summ):
            st = next((s for s in summ["steps"] if s["key"] == key), None)
            if st is None:
                raise WorkflowError(404, f"no step '{key}' in this workflow")
            if st["state"] in ("done", "skipped"):
                raise WorkflowError(409, "this step is already finished")
            self._append(rec, {"ev": "skip", "key": key, "by": OWNER})
        return self._owner_action(wid, act)

    def run_again(self, wid: str) -> dict:
        """A finished or stopped workflow -> a new planned copy of its spec (by: you), its own id and token."""
        rec = self._read(self._dir(wid))
        if self._derive(rec)["state"] not in ("done", "stopped", "failed"):
            raise WorkflowError(409, "only a finished or stopped workflow can run again")
        spec = self._spec_of(rec)
        spec["start"], spec["by"] = "plan", {"name": OWNER, "seat": None}
        out = self.create(spec, owner=True)
        out["from"] = wid
        return out

    @staticmethod
    def _spec_of(rec: _Rec) -> dict:
        spec = copy.deepcopy(rec.spec)
        spec.pop("order", None)
        for s in spec["steps"]:
            task = s.get("task")
            if task:
                for k in [k for k, v in task.items() if v is None]:
                    task.pop(k)
        return spec

    # ---- templates ----
    def templates_dir(self) -> str:
        return os.path.join(self.workflows_dir, "templates")

    def list_templates(self) -> list:
        return list_templates(self.templates_dir())

    def instantiate(self, name: str, at: str, goal: str, runner: str) -> dict:
        return instantiate(name, at, goal, runner, self._runners(), self.templates_dir())

    def new_from_template(self, name: str, at: str, goal: str, runner: str) -> dict:
        """The owner's "Run a workflow here": a planned workflow from a template (POST /workflow/new)."""
        return self.create(self.instantiate(name, at, goal, runner), owner=True)

    def save_template(self, wid: str, name: str) -> dict:
        """A finished workflow's spec kept as the user's own template (<workflows_dir>/templates/NAME.json, mode 600),
        with the holes put back where the values equal its at/goal. An existing name is refused, untouched."""
        if not isinstance(name, str) or not TEMPLATE_NAME_RE.fullmatch(name):
            raise WorkflowError(400, "a template name is 1-40 lower-case letters, digits or dashes")
        rec = self._read(self._dir(wid))
        if self._derive(rec)["state"] not in ("done", "stopped", "failed"):
            raise WorkflowError(409, "only a finished workflow can be kept as a template")
        if name in TEMPLATES:
            raise WorkflowError(409, "pick another name; nothing was overwritten")
        spec = self._spec_of(rec)
        at = spec.get("at") or next((s["at"] for s in spec["steps"] if s.get("at") and s["at"].startswith("/")), None)
        goal = spec.get("goal") or ""
        for s in spec["steps"]:
            if at and s.get("at") == at:
                s["at"] = "{at}"
            task = s.get("task")
            if task and goal:
                task["text"] = _goal_hole(task["text"], goal)
        if goal:
            spec["goal"] = "{goal}"
            if spec.get("title") == goal:
                spec["title"] = "{goal}"
        if at:
            spec["at"] = "{at}"
        spec.pop("template", None)
        spec["by"] = {"name": OWNER, "seat": None}
        folder = self.templates_dir()
        self._ensure_dir()
        _private_dir(folder)
        file = os.path.join(folder, name + ".json")
        try:
            _write_private(file, _jbytes({"version": 1, "name": name, "from": wid, "spec": spec}, indent=1),
                           exclusive=True)
        except FileExistsError:
            raise WorkflowError(409, "pick another name; nothing was overwritten") from None
        return {"ok": True, "name": name}

    # ---- reading ----
    def _ids(self) -> list:
        try:
            return sorted((n for n in os.listdir(self.workflows_dir) if ID_RE.fullmatch(n)
                           and os.path.isfile(os.path.join(self.workflows_dir, n, "workflow.json"))), reverse=True)
        except OSError:
            return []

    def _dir_bytes(self) -> int:
        total = 0
        for top, _dirs, files in os.walk(self.workflows_dir):
            for f in files:
                with contextlib.suppress(OSError):
                    total += os.lstat(os.path.join(top, f)).st_size
        return total

    def list(self, since: str | None = None) -> list:
        """Open workflows and the newest closed ones, as summaries, newest first; with since, only those
        changed after it."""
        self.errors = []
        opened, closed = [], []
        for wid in self._ids():
            hit = self._summaries.get(wid)
            if (len(closed) >= BOUNDS["closed_listed"] and hit is not None
                    and hit[0] == self._record_key(wid)):
                continue                           # closed history past the listed 50: not derived again
            try:
                s = self._closed_summary(wid)
            except (WorkflowError, OSError, ValueError, KeyError, TypeError) as exc:
                self.errors.append((wid, getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}"[:200]))
                continue
            if s["state"] in CLOSED:
                if len(closed) < BOUNDS["closed_listed"]:
                    closed.append(s)
            else:
                opened.append(s)
        out = sorted(opened + closed, key=lambda s: s.get("updated") or "", reverse=True)
        cut = _epoch(since) if since else None
        if cut is not None:
            out = [s for s in out if (_epoch(s.get("updated")) or 0) > cut]
        return out

    def summary(self, wid: str) -> dict:
        return self._derive(self._read(self._dir(wid)))

    def folders(self, wid: str) -> dict:
        """Each step's folder (`at`), by step key: what the bridge's private-folder rule for phones reads."""
        return {st["key"]: st.get("at") for st in self._read(self._dir(wid)).spec["steps"]}

    def detail(self, wid: str) -> dict:
        """Desktop and agents: the summary, the last 50 events and each step's folder (paths live only here)."""
        rec = self._read(self._dir(wid))
        summ = self._derive(rec)
        attempt = {s["key"]: s.get("attempt") for s in summ["steps"]}
        return {"workflow": summ, "events": self._public_events(rec.events)[-BOUNDS["events_shown"]:],
                "paths": {s["key"]: s.get("at") for s in rec.spec["steps"]},
                "outputs": {e["key"]: e.get("path") for e in rec.events if e["ev"] == "output" and e.get("path")},
                "results": {e["key"]: {k: e.get(k) for k in ("tid", "branch", "worktree", "files_changed",
                                                             "files_count", "error")}
                            for e in rec.events if e["ev"] == "result" and e.get("attempt") == attempt.get(e.get("key"))}}

    @staticmethod
    def _public_events(events: list) -> list:
        return [{k: v for k, v in e.items() if k != "token_sha256"} for e in events]

    def events(self, wid: str, after: int = 0, limit: int = 50) -> list:
        rec = self._read(self._dir(wid))
        rows = [e for e in self._public_events(rec.events) if e["seq"] > (after if isinstance(after, int) else 0)]
        limit = limit if isinstance(limit, int) and 0 < limit <= 2000 else BOUNDS["events_shown"]
        return rows[-limit:]

    def replay(self, wid: str, page: int = 0) -> dict:
        """Replay frames (at most BOUNDS["replay_frames"] a page): folding them gives the final summary. The
        frames are the `derived` events (each with its planet and who moved it, so the page draws the path
        between planets without the thread engine), then a frame for any change not yet written down."""
        rec = self._read(self._dir(wid))
        summ = self._derive(rec)
        by_key = {s["key"]: s for s in summ["steps"]}
        frames = []
        for e in rec.events:
            if e["ev"] != "derived":
                continue
            f = {"seq": e["seq"], "t": e["t"], "key": e.get("key"), "state": e.get("state"),
                 "says": e.get("says") or "", "why": e.get("why"), "planet_uid": e.get("planet_uid"),
                 "place": e.get("place"), "by": e.get("by")}
            if f["key"] is not None:
                f.update(progress=e.get("progress"), output=e.get("output"), artifact=e.get("artifact"))
            if f["key"] is not None and f["planet_uid"] is None and f["key"] in by_key:
                f["planet_uid"] = by_key[f["key"]]["planet_uid"]     # drawn on today's map
                f["place"] = f["place"] or by_key[f["key"]]["place"]
            frames.append(f)
        folded = fold(frames, summ["steps"], detail=True)
        now, seq = self._clock(), rec.seq
        for s in summ["steps"]:
            # a step whose state, words, progress, output or kept file moved on since its last breadcrumb (said
            # without a change of state) gets one closing frame, so the frames fold to the summary in full
            if (folded["steps"].get(s["key"]) != s["state"]
                    or folded["detail"].get(s["key"]) != _detail_of(s)):
                seq += 1
                frames.append({"seq": seq, "t": now, "key": s["key"], "state": s["state"], **_detail_of(s),
                               "planet_uid": s["planet_uid"], "place": s["place"], "by": "map",
                               "synthetic": True})
        if folded["state"] != summ["state"]:
            seq += 1
            frames.append({"seq": seq, "t": now, "key": None, "state": summ["state"], "says": "", "why": None,
                           "planet_uid": None, "place": None, "by": "map", "synthetic": True})
        size = BOUNDS["replay_frames"]
        pages = max(1, (len(frames) + size - 1) // size)
        page = page if isinstance(page, int) and 0 <= page < pages else 0
        order = rec.spec.get("order") or [s["key"] for s in rec.spec["steps"]]
        return {"id": rec.id, "title": summ["title"], "order": order,
                "steps": [{k: s[k] for k in ("key", "label", "kind", "after", "planet_uid", "moon_uid", "place")}
                          for s in summ["steps"]],
                "frames": frames[page * size:(page + 1) * size], "page": page, "pages": pages,
                "total_frames": len(frames), "final": {"state": summ["state"],
                                                       "steps": {s["key"]: s["state"] for s in summ["steps"]},
                                                       "detail": {s["key"]: _detail_of(s) for s in summ["steps"]}}}

    # ---- deriving the state ----
    def _task_row(self, tid: str) -> dict | None:
        if self.task_store is None or not isinstance(tid, str):
            return None
        try:
            return self.task_store.summary(os.path.join(self.task_store.tasks_dir, tid))
        except Exception:                         # an unreadable thread record reads as a failure of the step
            return {"status": "error"}

    def _second_opinion(self, task: dict, held: dict) -> bool:
        """Whether the verdict in force came from the step's named checker, or from another agent of a model
        family other than the maker's (an owner-run check by someone else still counts; the maker's family
        never does)."""
        who = held.get("runner")
        if who == task.get("check_runner"):
            return True
        import tasks as _tasks
        return isinstance(who, str) and bool(who) and not _tasks.same_family(
            task["runner"], who, self._runners(), task.get("model"), held.get("model"))

    def _task_state(self, st: dict, row: dict | None) -> tuple:
        """(state, says, why, check) of a task step from its thread's summary (and the 0 rule)."""
        task = st["task"]
        if row is None:
            return "working", "", "the thread engine is not connected here", None
        status = row.get("status")
        chk = row.get("check") if isinstance(row.get("check"), dict) else None
        held = row.get("last_verdict") if isinstance(row.get("last_verdict"), dict) else None
        check = None
        if chk:
            running = chk.get("verdict") in ("checking", "stopping")
            check = {"runner": chk.get("runner"), "state": "running" if running else "done",
                     "verdict": None if running else ((held or {}).get("verdict") or chk.get("verdict"))}
        if status in ("new", "running", "stopping"):
            return "working", "", None, check
        if status == "checking":
            return "working", "a second opinion is reading the changes", None, check
        if status == "failed" and row.get("retry_due"):
            return "working", "retrying: the test that must pass failed", None, check
        if status == "review":
            blockers = [b for b in (row.get("merge_blockers") or []) if isinstance(b, str)]
            if task.get("wait") == "merged":
                return "needs_you", "", "accept changes to continue", check
            verdict = (held or {}).get("verdict") if held and not held.get("stale") else None
            if verdict == "PASS" and task.get("check_runner") and not self._second_opinion(task, held):
                verdict = None                    # a PASS from the maker's own family is no second opinion
            if task.get("check_runner") and verdict != "PASS":
                why = ("the second opinion said FAIL" if verdict == "FAIL" else
                       "waiting for a second opinion that passes it")
                return "needs_you", "", why, check
            if blockers:
                return "needs_you", "", blockers[0], check
            return "done", "", None, check
        if status in ("merged", "done"):
            return "done", "", None, check
        if status == "discarded":
            return "skipped", "", "its changes were set aside", check
        if status == "failed":
            verify = row.get("verify") if isinstance(row.get("verify"), dict) else None
            if verify and verify.get("rc") not in (None, 0):
                why = "the test that must pass failed"
            else:
                why = f"the agent failed (exit {row.get('rc')})" if row.get("rc") is not None else "the agent failed"
            return "failed", "", why, check
        if status == "lost":
            return "failed", "", "the agent's process was lost", check
        if status == "stopped":
            return "failed", "", "the agent was stopped", check
        return "failed", "", "its thread record cannot be read", check

    def _derive(self, rec: _Rec) -> dict:
        """The summary from workflow.json, the events, latest.json and the threads."""
        spec, now = rec.spec, self._clock()
        order = spec.get("order") or [s["key"] for s in spec["steps"]]
        created_t = rec.events[0]["t"] if rec.events else now
        start_t = created_t if spec.get("start") == "now" else None
        stopped = ended = None
        info = {k: {"note": None, "says": None, "progress": None, "output": None, "artifact": None, "gate": None,
                    "skip": False, "attempts": {}, "attempt": 0, "retry_seq": 0, "checks": {}, "last_t": None}
                for k in rec.steps}
        last_t = created_t
        for e in rec.events:
            ev, key = e["ev"], e.get("key")
            last_t = max(last_t, e["t"])
            if ev == "start" and start_t is None:
                start_t = e["t"]
            elif ev == "stop" and stopped is None:
                stopped = e
            elif ev == "done" and ended is None:
                ended = e
            if key not in info:
                continue
            i = info[key]
            if ev == "step":
                if "state" in e:
                    i["note"] = e
                if "says" in e:
                    i["says"] = e["says"]
                if "progress" in e:
                    i["progress"] = e["progress"]
                i["last_t"] = e["t"]
            elif ev == "output":
                i["output"] = e
            elif ev == "artifact" and not e.get("refused"):
                i["artifact"] = e.get("name")
            elif ev == "gate":
                i["gate"] = e
            elif ev == "skip":
                i["skip"] = True
            elif ev == "retry":
                i["retry_seq"] = e["seq"]
                i["note"] = i["gate"] = None
            elif ev in ("launch", "task", "deferred", "launch_failed", "duplicate", "check", "result"):
                n = e.get("attempt")
                if not isinstance(n, int):
                    continue
                a = i["attempts"].setdefault(n, {"seq": e["seq"]})
                a[ev] = e
                i["attempt"] = max(i["attempt"], n)
        latest_steps = rec.latest.get("steps") if isinstance(rec.latest.get("steps"), dict) else {}
        pulse_t = rec.latest.get("pulse") if isinstance(rec.latest.get("pulse"), (int, float)) else None
        latest_t = rec.latest.get("t") if isinstance(rec.latest.get("t"), (int, float)) else None
        out_steps, state_of, root_fail, gated = {}, {}, {}, {}
        for key in order:
            st = rec.steps[key]
            i = info[key]
            planet, moon, place, where = self._where(st.get("at"))
            says, why, tid, check, task_status, branch = "", None, None, None, None, None
            result = None
            deps = st["after"]
            # behind a gate the owner has not passed (or skipped), at any depth: a gate's pass and skip are
            # final, so this never turns a step that was reported in its turn back to waiting
            gated[key] = any((rec.steps[d]["kind"] == "gate" and state_of[d] not in ("done", "skipped"))
                             or gated[d] for d in deps)
            needed = {inp["from"] for inp in st.get("inputs", [])}
            bad = [d for d in deps
                   if state_of[d] in ("failed", "blocked") or (state_of[d] == "skipped" and d in needed)]
            ok = all(state_of[d] == "done" or (state_of[d] == "skipped" and d not in needed) for d in deps)
            attempt = i["attempt"]
            if start_t is None:
                state = "waiting"
            elif i["skip"]:
                state = "skipped"
            elif st["kind"] == "note" and i["note"] is not None and not gated[key]:
                state = i["note"]["state"]
            elif st["kind"] == "gate" and i["gate"] is not None:
                state = "done" if i["gate"].get("pass") else "failed"
                says = "approved" if i["gate"].get("pass") else ""
                why = None if i["gate"].get("pass") else (i["gate"].get("why") or "rejected")
            elif st["kind"] == "task" and attempt and i["attempts"][attempt]["seq"] > i["retry_seq"]:
                a = i["attempts"][attempt]
                if "launch_failed" in a:
                    state, why = "failed", a["launch_failed"].get("why") or "the agent could not be started"
                elif "task" in a:
                    tid = a["task"].get("tid")
                    row = self._task_row(tid)
                    task_status = (row or {}).get("status")
                    branch = (row or {}).get("branch")
                    result = a.get("result")
                    state, says, why, check = self._task_state(st, row)
                elif "deferred" in a:
                    state, why = "ready", a["deferred"].get("why") or SLOT_WHY
                else:
                    state, says = "working", "starting the agent"
            elif bad:
                state = "blocked"
                src = bad[0]
                root = root_fail.get(src, src)
                root_fail[key] = root
                why = (f"won't start: '{rec.steps[root]['label']}' "
                       + ("was skipped" if state_of[src] == "skipped" else "failed"))
            elif ok:
                state = "ready"
            else:
                state = "waiting"
            if state == "failed":
                root_fail.setdefault(key, key)
            if stopped is not None and state in ("ready", "waiting"):
                state, why = "waiting", "the workflow was stopped"
            if ended is not None and state in ("ready", "waiting", "blocked") and st["kind"] != "gate":
                state, why = "skipped", "the workflow ended before this step"
            if state == "ready" and st["kind"] == "gate":
                state = "needs_you"                       # a gate that is reached waits for the owner
            # what the agent said, newest first (latest.json coalesces same-state updates)
            lat = latest_steps.get(key) if isinstance(latest_steps.get(key), dict) else {}
            if i["says"] is not None and not says:
                says = i["says"]
            if isinstance(lat.get("says"), str) and (i["last_t"] is None or lat.get("t", 0) >= i["last_t"]):
                says = lat["says"]
            progress = i["progress"]
            if isinstance(lat.get("progress"), dict):
                progress = lat["progress"]
            if not says and st.get("note"):
                says = st["note"]
            o = i["output"]
            output = None
            if o:
                name = os.path.basename(o.get("path") or "")
                text = o.get("text") or ""
                output = f"{name}: {text}" if name and text else (name or text or None)
            state_of[key] = state
            row = {"key": key, "label": st["label"], "kind": st["kind"], "state": state, "planet_uid": planet,
                   "moon_uid": moon, "place": place, "after": list(deps), "says": says or "", "progress": progress,
                   "output": output, "artifact": i["artifact"], "tid": tid, "attempt": attempt, "why": why,
                   "where": where}
            if st["kind"] == "task":
                files = (result or {}).get("files_changed")
                row.update(runner=st["task"]["runner"], task_status=task_status, wait=st["task"].get("wait"),
                           check=check, branch=branch or (result or {}).get("branch"),
                           files_changed=(result or {}).get("files_count") if isinstance(files, list) else None)
            out_steps[key] = row
        steps = [out_steps[s["key"]] for s in spec["steps"]]
        states = [s["state"] for s in steps]
        activity = max([t for t in (last_t, pulse_t, latest_t) if isinstance(t, (int, float))] or [created_t])
        if stopped is not None:
            state = "stopped"
        elif all(s in ("done", "skipped") for s in states):
            state = "done"
        elif start_t is None:
            state = "planned"
        elif any(s == "failed" for s in states) and not any(s in ("ready", "working", "needs_you") for s in states):
            state = "failed"
        elif "needs_you" in states:
            state = "needs_you"
        elif any(s in ("ready", "working") for s in states):
            state = "working"
            # a task step whose agent process is alive is news enough: the thread engine watches it (its pid,
            # its max_minutes) and says lost or failed when it is not, so a long agent run that never pulses
            # is not a "no news" problem in the sky
            busy = any(s["kind"] == "task" and s["state"] == "working" and s.get("task_status") in LIVE_THREAD
                       for s in steps)
            if not busy and now - activity > spec.get("stale_after_min", BOUNDS["stale_after_min"]) * 60:
                state = "stale"
        elif any(s in ("failed", "blocked") for s in states):
            state = "failed"
        else:
            state = "working"
        finished = state in ("done", "stopped", "failed")
        if start_t is None:
            elapsed = 0
        else:
            elapsed = int(max(0.0, (last_t if finished else now) - start_t))
        current = [s["key"] for s in steps if s["state"] in ("working", "needs_you")]
        if not current and state in ("working", "stale"):
            current = [s["key"] for s in steps if s["state"] == "ready"]
        out = {"id": rec.id, "title": spec["title"], "goal": spec.get("goal") or "", "by": dict(spec["by"]),
               "state": state, "updated": _iso(activity), "seq": rec.seq,
               "done": sum(1 for s in states if s in ("done", "skipped")), "total": len(steps),
               "elapsed_s": elapsed, "current": current, "sentence": "", "steps": steps}
        out["sentence"] = sentence_of(out)
        return out

    # ---- derived events (history, replay) ----
    def _sync(self, rec: _Rec, by: str | None = None) -> dict:
        """Write a `derived` event for every step (and the workflow) whose derived state changed since the
        last one: {key, state, says, why, planet_uid, place, by, t}. Returns the summary.
        port: space-truckers route-planning/cargoUnit.js:101-117 (BSD-3) — the run kept as breadcrumbs
        (where, when, who) so any past run can be drawn again; here one per change of state, not per frame."""
        summ = self._derive(rec)
        last = {}
        for e in rec.events:
            if e["ev"] == "derived":
                last[e.get("key")] = e.get("state")
        whom = {}
        for e in rec.events:
            if e["ev"] in ("step", "gate", "skip", "retry") and e.get("key") in rec.steps and e.get("by"):
                whom[e["key"]] = e["by"]
        for s in summ["steps"]:
            if last.get(s["key"], "waiting") != s["state"]:
                who = by or whom.get(s["key"])
                if not who:
                    who = s.get("runner") if s["kind"] == "task" else (OWNER if s["kind"] == "gate"
                                                                         else rec.spec["by"]["name"])
                self._append(rec, {"ev": "derived", "key": s["key"], "state": s["state"], "says": s["says"],
                                   "why": s["why"], "progress": s["progress"], "output": s["output"],
                                   "artifact": s["artifact"], "planet_uid": s["planet_uid"], "place": s["place"],
                                   "by": who})
        if last.get(None, "planned") != summ["state"]:
            self._append(rec, {"ev": "derived", "key": None, "state": summ["state"], "says": summ["sentence"],
                               "why": None, "planet_uid": None, "place": None, "by": by or "map"})
        summ["seq"] = rec.seq
        return summ

    # ---- the engine ----
    def _ledger(self, now: float) -> str:
        """The day's launch ledger, <workflows_dir>/launches/YYYY-MM-DD.jsonl (local date): one line per counted
        launch, written with its `launch` event under the folder's lock, so every store on this folder (the
        bridge, a CLI) counts the same launches, whichever workflows it ticks."""
        return os.path.join(self.workflows_dir, "launches", time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl")

    def _launches_today(self, now: float) -> int:
        """Launches of agent-made workflows since local midnight (the owner's are not counted): the lines of
        today's ledger, less the launches a refund line gives back (the thread engine had no free slot, so no
        agent ran). Call it under the folder's lock when a launch depends on the answer."""
        midnight = _local_midnight(now)
        try:
            with open(self._ledger(now), "rb") as handle:
                data = handle.read()
        except OSError:
            return 0
        counted, refunds = [], set()
        for line in data.split(b"\n"):
            try:
                row = json.loads(line.decode("utf-8", "replace")) if line.strip() else None
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("t"), (int, float)) and row["t"] >= midnight:
                which = (row.get("id"), row.get("key"), row.get("attempt"))
                if row.get("refund"):
                    refunds.add(which)
                else:
                    counted.append(which)
        return sum(1 for which in counted if which not in refunds)

    def _count_launch(self, rec: _Rec, key: str, n: int, now: float, refund: bool = False) -> None:
        """One line in today's ledger for a counted launch (before its event: a crash between the two counts
        one launch too many, never one too few). refund: the line that gives that launch back when the thread
        engine refused it for want of a free slot (no task was made); `now` is the launch's own, so the two
        lines land in the same day's ledger."""
        folder = os.path.join(self.workflows_dir, "launches")
        _private_dir(folder)
        row = {"t": round(now, 3), "id": rec.id, "key": key, "attempt": n}
        if refund:
            row["refund"] = True
        line = _jbytes(row)
        fd = os.open(self._ledger(now), os.O_WRONLY | os.O_APPEND | os.O_CREAT | NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            _write_all(fd, line)
        finally:
            os.close(fd)

    def tick(self) -> list:
        """Launch ready task steps, rebind lost launches, ask second opinions, keep outputs: the transaction of
       , one workflow at a time under its lock. Returns [(id, key, what)] of what it did. One tick
        at a time in this process: a caller that finds another running returns at once (that one does it)."""
        if not self._tick_lock.acquire(blocking=False):
            return []
        try:
            done = []
            for wid in self._ids():
                path = os.path.join(self.workflows_dir, wid)
                segs = self._segments(path)
                seen = self._seg_stat(segs[-1]) if segs else None
                if seen is not None and self._closed.get(wid) == seen:
                    continue                       # closed and untouched since: nothing can launch
                try:
                    with self._lock(path):
                        rec = self._read(path)
                        done += self._tick_one(rec)
                        segs = self._segments(path)
                        if rec.events and segs and self._finished_for_tick(self._derive(rec)):
                            self._closed[wid] = self._seg_stat(segs[-1])
                except WorkflowError as exc:
                    self.errors.append((wid, exc.message))
                except Exception as exc:          # one bad workflow never stops the others
                    self.errors.append((wid, f"{type(exc).__name__}: {exc}"[:200]))
            return done
        finally:
            self._tick_lock.release()

    @staticmethod
    def _seg_stat(seg: str):
        try:
            st = os.stat(seg)
        except OSError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    @staticmethod
    def _finished_for_tick(summ: dict) -> bool:
        """Nothing a tick does can matter until the workflow's own files change: done (its done steps' results
        and outputs were kept by the tick that saw it so), or stopped with no step still at work or waiting on
        the owner (a running task in a stopped workflow can still finish, and its outputs are kept then)."""
        if summ["state"] == "done":
            return True
        return summ["state"] == "stopped" and not any(s["state"] in ("working", "needs_you") for s in summ["steps"])

    def _tick_one(self, rec: _Rec) -> list:
        summ = self._derive(rec)
        closed = summ["state"] in ("planned", "stopped", "done")
        did = [] if closed else self._recover(rec)
        if did:
            summ = self._derive(rec)
        # what a step made is written down and its files kept BEFORE anything after it launches (a dependent's
        # inputs resolve to the snapshot at launch) and before a finished workflow returns (its last
        # step's outputs are kept too)
        did += self._settle(rec, summ)
        if closed:
            self._sync(rec)
            return did
        owner_run = bool(rec.meta.get("owner")) or any(e["ev"] == "start" for e in rec.events)
        for key in rec.spec.get("order") or list(rec.steps):
            st = rec.steps[key]
            row = next(s for s in summ["steps"] if s["key"] == key)
            if st["kind"] != "task" or row["state"] != "ready" or summ["state"] == "stopped":
                continue
            what = self._launch(rec, st, owner_run)
            if what:
                did.append((rec.id, key, what))
            if what == "slots":
                break                              # no room for anyone else either this tick
        summ = self._derive(rec)
        for s in summ["steps"]:
            st = rec.steps[s["key"]]
            if st["kind"] == "task" and s["tid"] and st["task"].get("check_runner"):
                what = self._ask_check(rec, st, s)
                if what:
                    did.append((rec.id, s["key"], what))
        self._sync(rec)
        return did

    def _settle(self, rec: _Rec, summ: dict) -> list:
        """For each task step whose agent finished: its result {branch, worktree, files_changed}, once per
        attempt; for each done step: its declared outputs, snapshotted."""
        did = []
        noted = {(e.get("key"), e.get("attempt")) for e in rec.events if e["ev"] == "result"}
        for s in summ["steps"]:
            st = rec.steps[s["key"]]
            if st["kind"] == "task" and s.get("tid") and (s["key"], s.get("attempt")) not in noted:
                what = self._note_result(rec, st, s)
                if what:
                    did.append((rec.id, s["key"], what))
            if s["state"] == "done":
                for what in self._keep_outputs(rec, s["key"], s.get("tid")):
                    did.append((rec.id, s["key"], what))
        return did

    def _note_result(self, rec: _Rec, st: dict, s: dict) -> str | None:
        """When a task step's thread first reads finished (review, merged, done), what it made, from the thread
        engine: its branch and worktree (TaskStore.summary) and the files it changed (TaskStore.diff, the same
        view as the desktop's /task/diff), as a `result` event. A thread first seen already merged has no
        worktree left to read: its files are said unknown (None), never an empty list. Once per attempt
        (_settle skips an attempt that has one)."""
        status = s.get("task_status")
        if status not in ("review", "merged", "done") or self.task_store is None:
            return None
        n = s.get("attempt")
        row = self._task_row(s["tid"]) or {}
        files, error = None, None
        if status == "review":
            try:
                view = self.task_store.diff(s["tid"])
                files = [f["path"] for f in view.get("files") or []
                         if isinstance(f, dict) and isinstance(f.get("path"), str)]
            except Exception as exc:              # said on the event, never swallowed
                error = _cut(getattr(exc, "message", None) or f"{type(exc).__name__}: {exc}", 200)
        self._append(rec, {"ev": "result", "key": st["key"], "attempt": n, "tid": s["tid"],
                           "branch": row.get("branch"), "worktree": row.get("worktree"),
                           "files_changed": files[:100] if files is not None else None,
                           "files_count": len(files) if files is not None else None, "error": error})
        return "result"

    def _recover(self, rec: _Rec) -> list:
        """A launch with no answer for its attempt: the thread made with exactly that origin is bound (the
        oldest, a second one is a duplicate); none and older than 120 s: deferred, launch lost."""
        did, now = [], self._clock()
        attempts = {}
        for e in rec.events:
            if e["ev"] in ("launch", "task", "deferred", "launch_failed") and isinstance(e.get("attempt"), int):
                attempts.setdefault((e["key"], e["attempt"]), {})[e["ev"]] = e
        for (key, n), a in sorted(attempts.items(), key=lambda kv: kv[1].get("launch", {}).get("seq", 0)):
            if "launch" not in a or {"task", "deferred", "launch_failed"} & set(a):
                continue
            found = []
            if self.task_store is not None:
                found = self.task_store.find_by_origin({"workflow": rec.id, "step": key, "attempt": n})
            if found:
                self._append(rec, {"ev": "task", "key": key, "attempt": n, "tid": found[0], "rebound": True})
                for extra in found[1:]:
                    self._append(rec, {"ev": "duplicate", "key": key, "attempt": n, "tid": extra})
                did.append((rec.id, key, "rebound"))
            elif now - a["launch"]["t"] > LOST_LAUNCH_S:
                self._append(rec, {"ev": "deferred", "key": key, "attempt": n, "why": LOST_WHY})
                did.append((rec.id, key, "lost"))
        return did

    def _launch(self, rec: _Rec, st: dict, owner_run: bool) -> str | None:
        key, task = st["key"], st["task"]
        now = self._clock()
        ts = self.task_store
        if ts is None:
            return None
        import tasks as _tasks
        if time.monotonic() - getattr(ts, "last_create", -1e9) < _tasks.CREATE_MIN_GAP:
            return None                                  # the engine spaces creates out: next tick
        events = [e for e in rec.events if e.get("key") == key]
        attempts = [e["attempt"] for e in events if e["ev"] in ("launch", "deferred", "launch_failed", "task")
                    and isinstance(e.get("attempt"), int)]
        n = max(attempts or [0]) + 1
        retry_seq = max([e["seq"] for e in events if e["ev"] == "retry"] or [0])
        defers = []
        for e in events:
            if e["ev"] == "deferred" and e["seq"] > retry_seq:
                defers.append(e)
            elif e["ev"] in ("task", "launch_failed") and e["seq"] > retry_seq:
                defers = []
        # after one deferral the next tick tries again; two or more in a row back off 10 s, 20 s,
        # ... 300 s, so the bridge's 1 s nudge loop (each deferral is an event) is not a 1 s launch loop
        if len(defers) > 1:
            wait = min(DEFER_MAX_S, DEFER_S * (2 ** (len(defers) - 2)))
            if now - defers[-1]["t"] < wait:
                return None
        retried = retry_seq > 0 and not any(e["ev"] == "launch" and e["seq"] > retry_seq for e in events)
        counted = not owner_run and not retried
        launches = sum(1 for e in rec.events if e["ev"] == "launch")
        if launches >= BOUNDS["launches"]:
            self._append(rec, {"ev": "launch_failed", "key": key, "attempt": n, "code": 429,
                               "why": f"this workflow used its {BOUNDS['launches']} agent launches"})
            return "launch cap"
        token = secrets.token_urlsafe(24)
        # the day's count and the launch it allows are one step under the folder's lock: two stores (the
        # bridge's tick and a CLI's) on two workflows cannot both take the last launch of the day
        with (self._store_lock() if counted else contextlib.nullcontext()):
            if counted:
                cap = self.launches_per_day
                if self._launches_today(now) >= cap:
                    why = CAP_WHY.format(n=cap)
                    last = next((e for e in reversed(events) if e["ev"] in ("launch", "deferred", "retry")), None)
                    if not (last and last["ev"] == "deferred" and last.get("cap")):
                        self._append(rec, {"ev": "deferred", "key": key, "attempt": n, "why": why, "cap": True})
                    return "daily cap"
                self._count_launch(rec, key, n, now)
            self._append(rec, {"ev": "launch", "key": key, "attempt": n, "token_sha256": _digest(token),
                               "counted": counted, "by": "map"})
        text = task["text"] + self._inputs_text(rec, st)
        env = {"CM_WORKFLOW": rec.id, "CM_WORKFLOW_STEP": key, "CM_WORKFLOW_TOKEN": token,
               "CM_WORKFLOW_DEPTH": str(int(rec.meta.get("depth") or 0) + 1)}
        try:
            row = ts.create(text, st["at"], task["runner"], True, task.get("model"), task.get("verify"),
                            task.get("retries") or 0, task.get("setup"), False, title=st["label"],
                            origin={"workflow": rec.id, "step": key, "attempt": n}, env=env)
        except _tasks.TaskError as exc:
            if exc.status == 429:
                # refused before any task exists (tasks.py create): no agent ran, so the launch is given back
                # to today's count, or a step waiting for a slot would spend the day's cap on retries alone
                self._append(rec, {"ev": "deferred", "key": key, "attempt": n, "why": f"{SLOT_WHY}: {exc.message}"})
                if counted:
                    with self._store_lock():
                        self._count_launch(rec, key, n, now, refund=True)
                return "slots"
            self._append(rec, {"ev": "launch_failed", "key": key, "attempt": n, "code": exc.status,
                               "why": exc.message})
            return "launch failed"
        except Exception as exc:                 # said on the step, never swallowed
            self._append(rec, {"ev": "launch_failed", "key": key, "attempt": n, "code": 500,
                               "why": f"{type(exc).__name__}: {exc}"[:200]})
            return "launch failed"
        self._append(rec, {"ev": "task", "key": key, "attempt": n, "tid": row["id"]})
        return "launched"

    def _inputs_text(self, rec: _Rec, st: dict) -> str:
        """A task's inputs as read-only references to the snapshots their steps kept (path and sha256)."""
        lines = []
        for inp in st.get("inputs", []):
            art = next((e for e in reversed(rec.events) if e["ev"] == "artifact" and e.get("key") == inp["from"]
                        and not e.get("refused") and e.get("name") == inp["path"]), None)
            if art:
                where = os.path.join(rec.path, "artifacts", inp["from"], art["name"])
                lines.append(f"- {inp['path']} from step '{inp['from']}': {where} (sha256 {art['sha256'][:16]})")
            else:
                lines.append(f"- {inp['path']} from step '{inp['from']}': not found (that step kept no such file)")
        if not lines:
            return ""
        return "\n\nRead-only inputs from earlier steps (read them, do not change them):\n" + "\n".join(lines)

    def _ask_check(self, rec: _Rec, st: dict, s: dict) -> str | None:
        """Verify passed (status review) and a checker is named: ask the second opinion once per attempt."""
        if s.get("task_status") != "review":
            return None
        n = s["attempt"]
        if any(e["ev"] == "check" and e.get("key") == st["key"] and e.get("attempt") == n for e in rec.events):
            return None
        import tasks as _tasks
        try:
            self.task_store.check(s["tid"], st["task"]["check_runner"])
        except _tasks.TaskError as exc:
            if exc.status in (429, 409):
                return None                          # no room or still busy: the next tick asks
            self._append(rec, {"ev": "check", "key": st["key"], "attempt": n, "tid": s["tid"],
                               "runner": st["task"]["check_runner"], "error": exc.message})
            return "check refused"
        self._append(rec, {"ev": "check", "key": st["key"], "attempt": n, "tid": s["tid"],
                           "runner": st["task"]["check_runner"]})
        return "check asked"

    # ---- artifacts ----
    def _keep_outputs(self, rec: _Rec, key: str, tid: str | None = None) -> list:
        """Snapshot every file the step declared as output and has not kept yet."""
        did = []
        kept = {e.get("src") for e in rec.events if e["ev"] == "artifact" and e.get("key") == key}
        for e in [e for e in rec.events if e["ev"] == "output" and e.get("key") == key and e.get("path")]:
            if e["path"] in kept:
                continue
            kept.add(e["path"])
            did.append(self._snapshot(rec, key, e["path"], tid))
        return did

    def _snapshot(self, rec: _Rec, key: str, src: str, tid: str | None) -> str:
        why = self._refuse_path(src)
        if why:
            self._append(rec, {"ev": "artifact", "key": key, "src": src, "refused": why})
            return "refused"
        used = sum(e.get("size", 0) for e in rec.events if e["ev"] == "artifact" and not e.get("refused"))
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(src, flags)
        except OSError as exc:
            self._append(rec, {"ev": "artifact", "key": key, "src": src,
                               "refused": f"it could not be opened ({exc.strerror or exc})"})
            return "refused"
        try:
            st = os.fstat(fd)
            # where the open really went: O_NOFOLLOW guards only the last part of the path, so every rule the
            # path passed before the open is applied again to the descriptor's real path (a folder on the
            # way swapped for a link into the credentials of the same root is refused like one leading out)
            proc = f"/proc/self/fd/{fd}"
            real = os.path.realpath(proc) if os.path.isdir("/proc/self/fd") else os.path.realpath(src)
            moved = self._refuse_real(real)
            refuse = None
            if not stat.S_ISREG(st.st_mode):
                refuse = "it is not a regular file"
            elif moved == "it is outside the folders this map shows":
                refuse = "a folder on its way was swapped for a link that leads outside the map"
            elif moved:
                refuse = f"a folder on its way was swapped for a link: {moved}"
            elif st.st_nlink > 1:
                refuse = ("it has more than one name (a hard link), so where it really lives cannot be told; "
                          "copy it to a file of its own")
            elif st.st_size > self.file_cap:
                mb = 1024 * 1024
                refuse = f"it is {st.st_size // mb} MB; a step keeps {self.file_cap // mb} MB a file at most"
            elif used + st.st_size > self.artifact_mb * 1024 * 1024:
                refuse = f"this workflow keeps {self.artifact_mb} MB of outputs at most"
            if refuse:
                self._append(rec, {"ev": "artifact", "key": key, "src": src, "refused": refuse})
                return "refused"
            folder = os.path.join(rec.path, "artifacts")
            _private_dir(folder)
            folder = os.path.join(folder, key)
            _private_dir(folder)
            base = os.path.basename(src)
            stem, ext = os.path.splitext(base)
            name, k = base, 1
            while os.path.exists(os.path.join(folder, name)):
                k += 1
                name = f"{stem}-{k}{ext}"
            digest = hashlib.sha256()
            out = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            size = 0
            try:
                os.fchmod(out, 0o600)
                while True:
                    chunk = os.read(fd, 1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.file_cap:
                        raise OSError("the file grew past the cap while it was copied")
                    digest.update(chunk)
                    os.write(out, chunk)
            finally:
                os.close(out)
        finally:
            os.close(fd)
        self._append(rec, {"ev": "artifact", "key": key, "src": src, "name": name, "sha256": digest.hexdigest(),
                           "size": size, "mtime": _iso(st.st_mtime), "tid": tid})
        return "kept"

    # ---- retiring (never deleting) ----
    def retire(self, older_days: int, attic: str) -> dict:
        """Move closed workflows older than older_days to the attic (MANIFEST + UNDO); nothing is deleted."""
        if not isinstance(older_days, int) or isinstance(older_days, bool) or older_days < 1:
            raise WorkflowError(400, "older is a whole number of days, 1 or more")
        if not isinstance(attic, str) or not attic.startswith("/"):
            raise WorkflowError(400, "the attic is an absolute folder")
        now = self._clock()
        moved = []
        for wid in self._ids():
            try:
                s = self.summary(wid)
            except WorkflowError:
                continue
            if s["state"] not in CLOSED or now - (_epoch(s["updated"]) or now) < older_days * 86400:
                continue
            moved.append(wid)
        if not moved:
            return {"moved": [], "attic": attic}
        os.makedirs(attic, exist_ok=True)
        manifest = os.path.join(attic, "MANIFEST.tsv")
        undo = os.path.join(attic, "UNDO.sh")
        fresh = not os.path.exists(undo)
        with open(manifest, "a", encoding="utf-8") as m, open(undo, "a", encoding="utf-8") as u:
            if fresh:
                u.write("#!/bin/sh\n# puts the retired workflows back where they were\nset -e\n")
            for wid in moved:
                src, dst = os.path.join(self.workflows_dir, wid), os.path.join(attic, wid)
                shutil.move(src, dst)
                m.write(f"{wid}\t{src}\t{dst}\t{_iso(now)}\n")
                u.write(f"mv '{dst}' '{src}'\n")
        os.chmod(undo, 0o700)
        return {"moved": moved, "attic": attic}


def default_dir(cfg: dict) -> str:
    """Where a config keeps its workflows: workflows_dir, else WORK_ROOT/.container-map/workflows."""
    own = cfg.get("workflows_dir") if isinstance(cfg.get("workflows_dir"), str) else ""
    if own:
        return os.path.expanduser(own)
    return os.path.join(cfg.get("work_root") or os.path.dirname(os.path.abspath(__file__)),
                        ".container-map", "workflows")


def store_for(cfg: dict, task_store=None, roots=(), chars=()) -> WorkflowStore:
    """The workflow store a loaded config describes (the bridge and `cli.py workflow` both build theirs here)."""
    mb = cfg.get("workflow_artifact_mb")
    mb = mb if isinstance(mb, int) and not isinstance(mb, bool) and mb > 0 else BOUNDS["workflow_artifact_mb"]
    cap = cfg.get("workflow_launches_per_day")
    cap = cap if isinstance(cap, int) and not isinstance(cap, bool) and cap >= 0 else LAUNCHES_PER_DAY
    return WorkflowStore(default_dir(cfg), task_store=task_store, roots=roots, artifact_mb=mb,
                         launches_per_day=cap, agent_verify=bool(cfg.get("agent_verify")), chars=chars)
