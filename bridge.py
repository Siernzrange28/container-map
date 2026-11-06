#!/usr/bin/env python3
"""Game bridge: a loopback listener with a read UI and a bounded agent control queue, and
an opt-in second listener for paired phones (config key "remote")."""

from __future__ import annotations

import argparse
import base64
import contextlib
import errno
import gzip
import hashlib
import hmac
import itertools
import ipaddress
import json
import math
import os
import re
import secrets
import shlex
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
try:
    import fcntl
except ImportError:        # Windows: the locks keep only this process's threads apart
    fcntl = None


HERE = os.path.dirname(os.path.abspath(__file__))
LOOPBACK = "127.0.0.1"
LOOPBACK_NAMES = ("127.0.0.1", "localhost")
COOKIE_NAME = "container_map_ui"
MAX_BODY = 16_384
MAX_TEXT_CHARS = 4_000
MAX_TEXT_BYTES = 8_192
MAX_PANE_LINES = 200
MAX_PANE_BYTES = 65_536
# the live terminal (GET /term streams a pane's screen as it changes; POST /keys types into a configured seat)
TERM_TICK = 0.1             # seconds between looks at the pane while its screen is changing or being typed into
TERM_IDLE_TICK = 0.35       # ... and once it has been still for TERM_QUIET seconds
TERM_QUIET = 2.0
TERM_SECONDS = 600.0        # one stream's life; the page opens the next one by itself
TERM_BEAT = 2.0             # an unchanged screen still sends a beat this often
TERM_RECHECK = 2.0          # how often a stream checks the pane is still the one it opened
TERM_TAIL = 60              # history rows read with each look (more scrolled at once = the page starts over)
TERM_HISTORY = 1000         # history rows sent when a stream (re)starts
TERM_KEYS_MAX = 4_096       # characters of typing per POST /keys
TERM_PASTE_MAX = 16_384     # characters per paste
TERM_KEYS_RATE = (40, 1.0)  # at most 40 POST /keys per second per seat
TERM_FORMAT = "\x1f".join(f"#{{{name}}}" for name in (
    "history_size", "history_limit", "cursor_x", "cursor_y", "pane_width", "pane_height", "alternate_on",
    "cursor_flag", "pane_in_mode", "pane_dead"))
QUEUE_LIMIT = 32
QUEUE_TTL = 30.0
CONTROL_INTERVAL = 0.05
SEND_INTERVAL = 1.0
REMOTE_CACHE_TTL = 10.0
REMOTE_TIMEOUT = 4.0
MAX_HOP_BYTES = 1_048_576


def _seconds_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if 1.0 <= value <= 3600.0 else default


# The render loop (world.sh) writes game.html every RENDER_SECONDS (plus the render itself). GET /,
# /state and the agent map serve the world inlined in that page while it is younger than
# WORLD_MAX_AGE (one slow or skipped cycle tolerated); only an older page, or ?fresh=1 (the page
# asks after an action it wants to see), makes the bridge render in a request thread.
RENDER_SECONDS = _seconds_env("CONTAINER_RENDER_SECONDS", 15.0)
WORLD_MAX_AGE = 3 * RENDER_SECONDS
WORLD_LINE_RE = re.compile(r"(?m)^const W = (.*);$")
# a machine's own synced render (synced_dir/<machine>/world.json, carried by the sync bus)
sys.path.insert(0, HERE)
import config as _config
import today as _today
_CFG = _config.load()
SYNCED_DIR = os.environ.get("BRIDGE_SYNCED_DIR", _CFG["synced_dir"] or os.path.join(HERE, "synced"))
SYNCED_STALE_S = 24 * 3600          # older than this is reported by age, never served
MAX_SYNCED_BYTES = 8 * 1024 * 1024
# file preview / open: read-only text under the container's own roots, never credentials
FILE_ROOTS = tuple(_config.roots(_CFG)) or (os.path.expanduser("~/work"),)
# where something may RUN (threads, doors, verbs, hand-offs): what the map draws, never file_roots
TASK_ROOTS = tuple(_config.task_roots(_CFG)) or FILE_ROOTS
# A path part, a file name or a suffix is matched in lower case: ".SSH/id" on a case-insensitive
# disk is the same file as ".ssh/id". Whole folders of cloud and container credentials are denied
# by name (.kube/config, .docker/config.json, ...).
FILE_DENY_PARTS = frozenset({".secrets", ".ssh", ".gnupg", ".aws", ".env", "credentials", "secrets", ".git",
                             ".kube", ".docker", ".azure", ".gcloud", ".password-store", ".terraform.d",
                             "terraform.tfstate.d", ".oci", ".vault-token", ".htpasswd", ".s3cfg", ".boto",
                             ".my.cnf", ".mylogin.cnf", ".msmtprc", ".authinfo", ".terraformrc", "terraform.rc",
                             ".yarnrc", ".yarnrc.yml", "kubeconfig", "wp-config.php", "settings-security.xml",
                             "credentials.json", "credentials.toml"})
FILE_DENY_SUFFIX = (".enc.env", ".pem", ".key", ".p12", ".pfx", ".tfvars", ".tfvars.json", ".tfstate",
                    ".tfstate.backup", ".ovpn", ".gpg", ".pgp", ".p8", ".ppk", ".jks", ".keytab")
FILE_DENY_PREFIX = ("id_rsa", "id_dsa", "id_ed25519", "id_ecdsa", ".env", ".bridge-live", "bridge.log", ".netrc",
                    ".npmrc", ".pypirc", ".git-credentials", ".pgpass", ".dockercfg",
                    "remote-sessions", "remote-audit", "remote-codes")
FILE_DENY_END = (".pem", ".key", ".p12", ".pfx", ".kdbx", ".keystore")
GZIP_MIN = 1_024                      # smaller bodies are sent as they are
# GET /vendor/<name>: the 3D view's two scripts (vendor3d.py), named by their digest, so a browser keeps them for good
VENDOR_PREFIX = "/vendor/"
VENDOR_CACHE = "public, max-age=31536000, immutable"
VENDOR_KEEP = 8                       # names packed and kept at once (three.js and a bundle per skin and edit)
# the 3D view's page addresses: /?scene3d=1&seed=7, ?hold=match, ?demo3d=sky&angle=top&px=96&region=code,
# ?perf3d=1, ?probe=1. The page reads them itself (it is the same page as /); each key at most once, short plain values.
VIEW_QUERY_KEYS = frozenset({"scene3d", "seed", "demo3d", "angle", "px", "region", "perf3d", "probe", "hold"})
VIEW_QUERY_VALUE = re.compile(r"[A-Za-z0-9_-]{1,40}")


def view_query(query: str) -> bool:
    """True when a query on / is only the 3D view's own parameters (any other query stays refused)."""
    seen = set()
    for part in (query or "").split("&"):
        key, sep, value = part.partition("=")
        if not sep or key not in VIEW_QUERY_KEYS or key in seen or not VIEW_QUERY_VALUE.fullmatch(value):
            return False
        seen.add(key)
    return bool(seen)


LIVE_FILE_NAME_RE = re.compile(r"bridge-[0-9]+\.json(\.new\..*)?")     # a credentials file by name
MAX_FILE_BYTES = 64 * 1024
OPEN_MIN_GAP = 1.0
# door seats: tmux windows door.sh opens IN a directory; talkable through these TUIs (areas.json may override)
DOOR_PREFIX = "door-"
DOOR_TALK_DEFAULT = ["claude", "node", "codex", "muse", "grok", "omp", "aider", "pi"]
DOOR_MIN_GAP = 3.0
DOOR_TIMEOUT = 15.0
# the hand-it-to-a-runner verb: one board item -> `dispatch <runner> "<text>" --bg` (the real tool, its
# receipts land in the digest and in dispatch_dir); the text is built HERE from the item on disk,
# never taken from the request. Runners come from the registry minus the ones it marks dead.
DISPATCH_MIN_GAP = 5.0
DISPATCH_TIMEOUT = 20.0
DISPATCH_BIN = _CFG.get("dispatch_bin") or "dispatch"
RUNNERS_PATH = _CFG.get("runners_json") or ""
RUNS_PATH = _CFG.get("task_runs_json") or os.path.join(HERE, ".task-runs.jsonl")
RULINGS_PATH = _CFG.get("rulings_json") or os.path.join(HERE, ".quest-rulings.json")
# the user's word (today.py): the commitments file the user owns, and the append-only file the user's check-in clicks write
COMMITMENTS_PATH = (_CFG.get("today") or {}).get("commitments_md") or ""
CHECKINS_PATH = (_CFG.get("today") or {}).get("checkins_jsonl") or ""
DEAD_MARK = "\U0001F534"            # the registry flags a dead seat with a red circle in its desc
# slice 2: universal runners from config — no dispatch tool needed. A runner is an argv; the item
# text goes in as {text} or on stdin; the run is detached, logged, time-capped, and its exit code
# is written back as a receipt row the renderer reads. Nothing is inferred from silence.
CONFIG_RUNNERS = _CFG.get("runners") if isinstance(_CFG.get("runners"), dict) else {}
RUNNER_DEFAULT_MINUTES = 30
# slice 6: verbs are files in verbs_dir (see verbs.py). Loaded fresh on every call so a verb an
# agent just wrote is offered without a restart.
import verbs as _verbs
VERBS_DIR = _CFG.get("verbs_dir") or os.path.join(HERE, "verbs")
MAX_VERB_TEXT = 4000
# T3-style tasks (tasks.py): you say what you want, pick a folder and an agent; the agent works there
# (in its own git worktree when the folder is in a repo), its output streams to the page, and you
# review the diff and merge it or set it aside. Only the UI session may start or close a task.
import tasks as _tasks
import wingmate as _mate
import meta as _meta
import cards as _cards
import districts as _districts
import sky as _sky
TASKS_DIR = _CFG.get("tasks_dir") or os.path.join(HERE, ".tasks")
MAX_TASK_BODY = 24_576
# workflows you can watch (workflows.py): any agent puts a few steps on the map and reports them; a task step
# runs through the thread engine above. The store is on disk beside the threads, so `cli.py wf` and this bridge share
# it; each event touches <workflows_dir>/.nudge, which the ticker stats twice a second (launch latency <= 1.5 s).
import workflows as _workflows
WORKFLOWS_DIR = _CFG.get("workflows_dir") or ""
NUDGE_SECONDS = 0.5                 # how often the ticker looks at .nudge (a stat): a step done -> its dependents launch
WORKFLOW_ROUTES = ("/workflow/start", "/workflow/retry", "/workflow/skip", "/workflow/stop", "/workflow/new",
                   "/workflow/gate")
# shows: an agent paints on the map for a while (memory only, gone on restart). Ops are data, validated and
# clamped here (port: three.quarks "effects as data", MIT, as our own schema); an unknown or bad op is skipped and
# listed while the rest is kept (a command list that skips a bad op instead of throwing on the first one); every
# op may ask the 3D view to draw it through planets (an X-ray overlay).
SHOW_OPS = ("label", "marker", "ring", "beam", "route", "radio", "fx", "card", "chart", "page")
SHOW_ICONS = ("flag", "pin", "star", "warn")
FX_PRESETS = ("sparkle", "ring", "burst", "text")
CHART_KINDS = ("spark", "bars")
STATE_COLOURS = {"done": "#4ADE80", "working": "#6FB7FF", "needs_you": "#FFB547", "failed": "#FF5A5F",
                 "waiting": "#6B7684", "ready": "#6B7684", "skipped": "#6B7684", "blocked": "#FF5A5F"}
SHOW_LIVE = _workflows.BOUNDS["shows_live"]            # 8
SHOW_ITEMS = _workflows.BOUNDS["show_items"]           # 16 ops a show
SHOW_TTL = (_workflows.BOUNDS["show_ttl_min_s"], _workflows.BOUNDS["show_ttl_max_s"], 300)   # min, max, default
SHOW_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
# the page op: an agent's own HTML page, opened by the person in a sandboxed panel of the desktop page. The bridge keeps
# the source in memory beside the show and serves it to the page's UI session only, as the document of the panel's frame
# (GET /show/page/frame, with headers of its own); every list of shows (/tasks, /workflows, /agent/show's reply) carries
# a page's title and size, never its source.
PAGE_BYTES = _workflows.BOUNDS["page_bytes"]           # 256 KiB of UTF-8
PAGE_TITLE = _workflows.BOUNDS["page_title"]           # 40
PAGE_LIVE = _workflows.BOUNDS["pages_live"]            # 4 pages, counted within the 8 live shows
PAGE_TTL = (SHOW_TTL[0], _workflows.BOUNDS["page_ttl_max_s"],
            _workflows.BOUNDS["page_ttl_default_s"])    # min 30 s, max 24 h, default 30 min
PAGE_AT = "place:pages"             # where a page with no anchor sits
PAGE_FRAME_PATH = "/show/page/frame"
# the frame document's own policy, in its headers (a srcdoc frame would inherit the map's instead): sandboxed even if it is
# opened on its own, script and style only inline, nothing loaded or sent anywhere, framed by the map and nobody else
PAGE_FRAME_CSP = ("sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                  "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; frame-src 'none'; "
                  "form-action 'none'; base-uri 'none'; frame-ancestors 'self'")
PAGE_FRAME_ALLOWLIST = "()"         # Connection-Allowlist of that document: no connection of any kind
PAGE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")   # every control character but \n \r \t
# a POST /agent/show that carries a page may be this large (256 KiB of astral text written \uXXXX is 786 KB)
SHOW_PAGE_BODY = 1_048_576
SHOW_RATE = (30, 10.0)              # shows put per window (seconds), all agents together
POINT_LIVE = _workflows.BOUNDS["point_live"]           # 8 pointers up at once
POINT_SECONDS = (_workflows.BOUNDS["point_min_s"], _workflows.BOUNDS["point_max_s"], 20)
# a planet's purpose and emblem, as the owner sets them from its card (POST /area/set): the nine purposes in
# areas.json's words (notes = knowledge, not sorted = unknown; the card's names are accepted too) and the twelve emblems
AREA_SET_KINDS = ("code", "knowledge", "reference", "data", "media", "config", "shared", "archive", "unknown")
AREA_KIND_ALIASES = {"notes": "knowledge", "unsorted": "unknown", "not sorted": "unknown", "bus": "shared"}
AREA_SET_GLYPHS = ("code", "notes", "money", "health", "career", "journal", "learning", "archive", "sync", "tools",
                   "home", "star")
# the re-render verb: one bounded, constant command per whitelisted machine (no request data in it)
RENDER_TIMEOUT = 180.0
RENDER_MIN_GAP = 60.0
# slice 4: machines are config (seats.json "machines"), not code. Each entry may carry
# "ssh": "user@host" (the hop becomes plain ssh), "hop": "fleet"|"ssh", and "render_cmd": one
# constant shell line run THERE by the re-render verb (no request data ever reaches it).
MACHINE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
SSH_TARGET_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}")
MACHINE_KEYS = {"seats", "ssh", "hop", "render_cmd"}
MAX_REMOTE_TABS = 128
MAX_REMOTE_PANES = 1_024
MAX_REMOTE_FIELD_CHARS = 4_096

WINDOW_FORMAT = "#{window_id}\x1f#{window_index}\x1f#{window_name}\x1f#{window_panes}"
PANE_FORMAT = "#{pane_id}\x1f#{pane_pid}\x1f#{pane_current_command}\x1f#{pane_dead}\x1f#{pane_in_mode}"
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
PLACEHOLDER_RE = re.compile(
    r"^(?:ask(?: anything)?|type a message|use /skills|placeholder|/effort adjusts reasoning depth vs speed)\b",
    re.I,
)
BUSY_RE = re.compile(r"(?:esc to interrupt|thinking(?:\.{3})?|working(?:\.{3})?|press esc to stop)", re.I)
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")    # the caller's own name for a request
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SEAT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REMOTE_STATUSES = frozenset({"idle", "working", "blocked", "done", "unknown"})


def _same(got: str, want: str) -> bool:
    """Constant-time equality for header values. hmac.compare_digest on two str raises TypeError
    for non-ASCII text, so both sides go in as UTF-8 bytes; a header nobody could have sent
    (a lone surrogate) is simply not equal."""
    try:
        return hmac.compare_digest(got.encode("utf-8", "surrogatepass"),
                                   want.encode("utf-8", "surrogatepass"))
    except UnicodeError:
        return False


def _uint(raw: str, top: int) -> int | None:
    """A non-negative integer written with ASCII decimals and no bigger than top, else None.
    str.isdigit() also accepts '\u00b2' and then int() raises, and int() itself refuses more
    than 4300 characters, so neither is left to chance."""
    if not (isinstance(raw, str) and raw.isascii() and raw.isdecimal()) or len(raw) > 30:
        return None
    value = int(raw)
    return value if value <= top else None

# `fleet run` joins its arguments into a remote shell command. Keep that command
# entirely constant and carry every variable value over stdin. The remote helper
# then invokes herdr with argv lists and shell=False.
REMOTE_HELPER_PY = r'''
import json
import re
import subprocess
import sys

TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
STATUSES = {"idle", "working", "blocked", "done", "unknown"}
PROMPTS = ("❯", "›", "⟩")
LIMIT = 524288

def command(argv):
    try:
        result = subprocess.run(argv, shell=False, capture_output=True, text=True,
                                timeout=3, check=False)
    except subprocess.TimeoutExpired:
        return {"returncode": 124, "stdout": "", "stderr": "", "timed_out": True}
    except OSError:
        return {"returncode": 127, "stdout": "", "stderr": "", "timed_out": False}
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    if len(stdout.encode("utf-8", "replace")) > LIMIT or len(stderr.encode("utf-8", "replace")) > LIMIT:
        return {"returncode": 125, "stdout": "", "stderr": "", "timed_out": False,
                "oversized": True}
    return {"returncode": result.returncode, "stdout": stdout, "stderr": stderr,
            "timed_out": False}

def agent_records(result):
    try:
        payload = json.loads(result["stdout"])
        agents = payload["result"]["agents"]
        return agents if isinstance(agents, list) else []
    except Exception:
        return []

def matching_agent(result, target):
    matches = [item for item in agent_records(result)
               if isinstance(item, dict) and item.get("pane_id") == target]
    return matches[0] if len(matches) == 1 else None

def allowed(actual, configured):
    return any(actual == item or actual.startswith(item + "-") or actual.startswith(item + ".")
               for item in configured)

def dirty_composer(value):
    clean = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", value)
    lines = clean.replace("\r", "").splitlines()
    last = lines[-1].lstrip() if lines else ""
    for prompt in PROMPTS:
        if last.startswith(prompt):
            return bool(last[len(prompt):].strip())
    return False

try:
    request = json.load(sys.stdin)
except Exception:
    print(json.dumps({"protocol": 1, "op": "invalid", "error": "invalid request"}))
    raise SystemExit(0)

op = request.get("op") if isinstance(request, dict) else None
response = {"protocol": 1, "op": op, "commands": {}}
if op == "state" and set(request) == {"op"}:
    response["commands"]["agent_list"] = command(["herdr", "agent", "list"])
    response["commands"]["snapshot"] = command(["herdr", "api", "snapshot"])
elif op == "read" and set(request) == {"op", "target"} and TARGET.fullmatch(str(request.get("target", ""))):
    response["commands"]["read"] = command(
        ["herdr", "agent", "read", request["target"], "--lines", "40", "--source", "recent"])
elif (op == "say" and set(request) == {"op", "target", "text", "agent", "talk_commands"}
      and TARGET.fullmatch(str(request.get("target", "")))
      and isinstance(request.get("text"), str) and isinstance(request.get("agent"), str)
      and isinstance(request.get("talk_commands"), list)
      and all(isinstance(item, str) for item in request["talk_commands"])):
    target = request["target"]
    listed = command(["herdr", "agent", "list"])
    response["commands"]["agent_list"] = listed
    if listed.get("timed_out") or listed.get("returncode") != 0:
        response["guard"] = "agent_list_failed"
    else:
        current = matching_agent(listed, target)
        response["agent"] = current
        actual = current.get("agent") if isinstance(current, dict) else None
        status = current.get("agent_status") if isinstance(current, dict) else None
        if current is None:
            response["guard"] = "missing_agent"
        elif actual != request["agent"] or not allowed(actual, request["talk_commands"]):
            response["guard"] = "agent_mismatch"
        elif status == "working":
            response["guard"] = "working"
        elif status not in {"idle", "blocked"}:
            response["guard"] = "status"
        else:
            read = command(["herdr", "agent", "read", target, "--lines", "3", "--source", "recent"])
            response["commands"]["read"] = read
            if read.get("timed_out") or read.get("returncode") != 0:
                response["guard"] = "read_failed"
            elif dirty_composer(read.get("stdout", "")):
                response["guard"] = "draft"
            else:
                rechecked = command(["herdr", "agent", "list"])
                response["commands"]["recheck"] = rechecked
                fresh = matching_agent(rechecked, target) if rechecked.get("returncode") == 0 else None
                response["agent"] = fresh
                fresh_actual = fresh.get("agent") if isinstance(fresh, dict) else None
                fresh_status = fresh.get("agent_status") if isinstance(fresh, dict) else None
                if rechecked.get("timed_out") or rechecked.get("returncode") != 0:
                    response["guard"] = "agent_list_failed"
                elif fresh is None:
                    response["guard"] = "missing_agent"
                elif fresh_actual != request["agent"] or not allowed(fresh_actual, request["talk_commands"]):
                    response["guard"] = "agent_mismatch"
                elif fresh_status == "working":
                    response["guard"] = "working"
                elif fresh_status not in {"idle", "blocked"}:
                    response["guard"] = "status"
                else:
                    response["guard"] = None
                    response["commands"]["prompt"] = command(
                        ["herdr", "agent", "prompt", target, request["text"]])
else:
    response["error"] = "invalid request"
print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
'''.strip()
REMOTE_HELPER_COMMAND = "python3 -c " + shlex.quote(REMOTE_HELPER_PY)


class BridgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class _Flight:
    """One world render in progress. Callers that arrive while it runs wait on `done` and share
    its outcome instead of starting a second render."""

    def __init__(self):
        self.done = threading.Event()
        self.world: dict | None = None
        self.error: BaseException | None = None
        self.waiters = 0                    # callers that joined it (counted under _world_lock)


def utc_stamp(epoch: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch or time.time()))


def clean_terminal(value: str) -> str:
    value = ANSI_RE.sub("", value).replace("\r", "").replace("\u00a0", " ")
    return "".join(ch for ch in value if ch in "\n\t" or ord(ch) >= 32)


# ---- the live terminal (GET /term, POST /keys). The page draws a pane's screen in xterm.js: colours kept, every
# other escape dropped (a pane cannot move the browser terminal's modes, title, clipboard or links), and typing goes
# back as tmux key names, so tmux turns them into whatever that pane's program asked for.
TERM_SGR_RE = re.compile(r"\x1b\[[0-9;:]*m")
TERM_ESC_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)?|[PX^_][^\x1b]*(?:\x1b\\)?"
                         r"|[ -/]*[0-~])")
TERM_CTRL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f]")      # every control character but \t


def term_line(raw: str) -> str:
    """One captured row as the page may draw it: its SGR colour runs and printable text, nothing else."""
    kept, at = [], 0
    for found in TERM_ESC_RE.finditer(raw):
        kept.append(TERM_CTRL_RE.sub("", raw[at:found.start()]))
        if TERM_SGR_RE.fullmatch(found.group(0)):
            kept.append(found.group(0))
        at = found.end()
    kept.append(TERM_CTRL_RE.sub("", raw[at:]))
    return "".join(kept)


def term_plain(line: str) -> str:
    return TERM_SGR_RE.sub("", line).rstrip()


# what xterm.js sends for a key -> the tmux key name. Cursor keys come in both of xterm's forms (CSI and SS3).
TERM_CSI_KEYS = {"A": "Up", "B": "Down", "C": "Right", "D": "Left", "H": "Home", "F": "End", "Z": "BTab",
                 "P": "F1", "Q": "F2", "R": "F3", "S": "F4"}
TERM_TILDE_KEYS = {"1": "Home", "2": "IC", "3": "DC", "4": "End", "5": "PPage", "6": "NPage", "7": "Home",
                   "8": "End", "15": "F5", "17": "F6", "18": "F7", "19": "F8", "20": "F9", "21": "F10",
                   "23": "F11", "24": "F12"}
TERM_MODS = {"2": "S-", "3": "M-", "4": "S-M-", "5": "C-", "6": "C-S-", "7": "C-M-", "8": "C-S-M-"}
TERM_CTRL_KEYS = {"\r": "Enter", "\n": "Enter", "\t": "Tab", "\x7f": "BSpace", "\x08": "C-h", "\x00": "C-Space",
                  "\x1c": "C-\\", "\x1d": "C-]", "\x1e": "C-^", "\x1f": "C-_"}
TERM_INPUT_RE = re.compile(r"\x1b\[([0-?]*)[ -/]*([@-~])|\x1bO([A-Za-z])|\x1b([\x20-\x7e])|(\x1b)"
                           r"|([\x00-\x1f\x7f])|([^\x00-\x1f\x7f-\x9f]+)|([\x80-\x9f])")


def term_keys(data: str) -> list[tuple[str, str]]:
    """xterm.js input as [("text", run) | ("key", tmux key name)]. Unknown sequences (mouse and focus reports,
    anything this table does not name) are dropped, never typed."""
    out: list[tuple[str, str]] = []
    for found in TERM_INPUT_RE.finditer(data):
        params, final, ss3, meta, esc, ctrl, text, _c1 = found.groups()
        name = None
        if final is not None:
            if not re.fullmatch(r"[0-9;]*", params):
                continue                    # mouse (<), private (?) and other reports
            parts = params.split(";") if params else []
            mod = TERM_MODS.get(parts[1], "") if len(parts) == 2 else ""
            if len(parts) > 2:
                continue
            if final == "~":
                name = TERM_TILDE_KEYS.get(parts[0]) if parts else None
            elif not parts or parts[0] == "1":
                name = TERM_CSI_KEYS.get(final)
            name = mod + name if name else None
        elif ss3 is not None:
            name = TERM_CSI_KEYS.get(ss3)
        elif meta is not None:
            name = "M-" + (meta if meta != ";" else "\\;")
        elif esc is not None:
            name = "Escape"
        elif ctrl is not None:
            name = TERM_CTRL_KEYS.get(ctrl) or "C-" + chr(ord(ctrl) + 96)
        elif text is not None:
            if out and out[-1][0] == "text":
                out[-1] = ("text", out[-1][1] + text)
            else:
                out.append(("text", text))
            continue
        if name:
            out.append(("key", name))
    return out


def tmux_arg(text: str) -> str:
    """One tmux argument that reads back as `text`: a final ';' is written '\\;' (tmux eats a bare final ';')."""
    return text[:-1] + "\\;" if text.endswith(";") else text


def tmux_literal(text: str) -> list[str]:
    """`send-keys -l` arguments that type `text` exactly. tmux ends a command at an argument ending in ';' and eats
    that ';' (and reads a final '\\;' as ';'), so a run that ends in ';' is split and each such ';' sent as '\\;'."""
    head = text.rstrip(";")
    args = [head] if head else []
    return args + ["\\;"] * (len(text) - len(head))


def unencodable_text(value: object) -> bool:
    """Whether a parsed JSON value holds a string (or a key) that cannot be written as UTF-8: a
    lone surrogate such as "\\ud800". JSON lets it in; the task store, the page, a terminal and
    the audit log cannot take it."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return False


def logical_line(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        raise BridgeError(400, "text must be a non-empty string")
    if unencodable_text(text):
        raise BridgeError(400, "text must be valid Unicode (it holds a lone surrogate)")
    if len(text) > MAX_TEXT_CHARS or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise BridgeError(400, "text is too large")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text) or any(
            marker in text for marker in ("\u0085", "\u2028", "\u2029")):
        raise BridgeError(400, "text must be one logical line with no control characters")
    return text


def _control_author(value: object) -> str:
    """Who a pointer, a focus or a follow is from, the name every rendering of it carries: one plain line of
    at most 24 characters; "an agent" when none is given."""
    if value in (None, ""):
        return "an agent"
    if (not isinstance(value, str) or not value.strip() or len(value) > 24 or unencodable_text(value)
            or any(ord(ch) < 32 or ord(ch) == 127 or ch in _workflows.BIDI or ch in "\u0085\u2028\u2029" for ch in value)):
        raise BridgeError(400, "by is the author's name: one plain line of at most 24 characters")
    return " ".join(value.split())


def safe_inline_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (raw.replace("&", "\\u0026").replace("<", "\\u003c")
               .replace(">", "\\u003e").replace("\u2028", "\\u2028")
               .replace("\u2029", "\\u2029"))


INLINE_TAG_RE = re.compile(r"<(script|style)(?=[\s>])[^>]*>", re.I)
NONCE_ATTR_RE = re.compile(r"\snonce\s*=", re.I)
END_TAG_RE = {name: re.compile("</" + name, re.I) for name in ("script", "style")}


def nonce_cuts(html: str) -> list[int]:
    """Where a CSP nonce goes: just before the `>` of every <script> and <style> start tag that has
    none, so no inline script of the page (the early theme one included) is blocked. The text up to
    the matching end tag is skipped, so a '<script>' inside a script's own strings is left alone."""
    cuts: list[int] = []
    pos = 0
    while True:
        found = INLINE_TAG_RE.search(html, pos)
        if not found:
            return cuts
        if not NONCE_ATTR_RE.search(found.group(0)):
            cuts.append(found.end() - 1)
        end = END_TAG_RE[found.group(1).lower()].search(html, found.end())
        if not end:
            return cuts
        pos = end.start()


WAIT_RE = re.compile(
    r"do you want to|allow (this|command|edit)|(?<![a-z-])approve (this|the|edit|command)|approve\?"
    r"|\(y/n\)|\[y/n\]|\[y/N\]|\[Y/n\]|1\. yes|esc to cancel|waiting for (your )?input|press enter to",
    re.I,
)
MENU_RE = re.compile(r"^\s*[❯›⟩]?\s*1\.\s*yes\b", re.I | re.M)
YN_RE = re.compile(r"\(y/n\)|\[y/n\]|\[y/N\]|\[Y/n\]", re.I)


def waiting_prompt(pane: str) -> dict | None:
    """If the visible pane is a permission/question prompt, return the keys it prints."""
    text = clean_terminal(pane)
    if not WAIT_RE.search(text):
        return None
    quote = "\n".join([line for line in text.splitlines() if line.strip()][-4:])[:280]
    if MENU_RE.search(text):
        return {"allow": "1", "deny": "2", "quote": quote}
    if re.search(r"press enter to", text, re.I) and not YN_RE.search(text):
        return {"allow": "", "deny": None, "quote": quote}
    return {"allow": "y", "deny": "n", "quote": quote}


def composer_probe(command: str, pane: str) -> tuple[str, str]:
    """Return (state, stable composer region). Unknown always means do not send."""
    lines = clean_terminal(pane).splitlines()[-14:]
    command = os.path.basename(command).lower()
    candidates: list[str] = []
    if command.startswith("grok"):
        pattern = re.compile(r"^\s*[│┃|]\s*❯\s*(.*?)\s*[│┃|]\s*$")
        candidates = [m.group(1).strip() for line in lines if (m := pattern.match(line))]
    elif command.startswith("claude") or command.startswith("muse"):
        pattern = re.compile(r"^\s*[❯›⟩]\s*(.*?)\s*$")
        candidates = [m.group(1).strip() for line in lines if (m := pattern.match(line))]
    else:
        return "unknown", ""
    if candidates:
        content = candidates[-1]
        if not content or PLACEHOLDER_RE.match(content):
            return "empty", "prompt:" + ("placeholder" if content else "empty")
        return "dirty", "prompt:" + content
    tail = "\n".join(lines[-8:])
    if BUSY_RE.search(tail):
        return "busy", "busy"
    return "unknown", tail


def remote_composer_has_draft(pane: str) -> bool:
    """Herdr's recent source exposes the composer as its final prompt line."""
    lines = clean_terminal(pane).splitlines()
    last = lines[-1].lstrip() if lines else ""
    for prompt in ("❯", "›", "⟩"):
        if last.startswith(prompt):
            return bool(last[len(prompt):].strip())
    return False


class SeatRegistry:
    def __init__(self, path: str):
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"cannot read seat registry: {exc}") from exc
        self._load(raw)

    @classmethod
    def empty(cls, session: str) -> "SeatRegistry":
        """ZERO-CONFIG: no seats.json. Seats are whatever tmux windows the world reports (door
        seats and plain windows are view-only until a policy names their talk command)."""
        self = cls.__new__(cls)
        self._load({"version": 1, "session": session or "none", "seats": [], "machines": {}})
        return self

    def _load(self, raw) -> None:
        if not isinstance(raw, dict) or set(raw) != {"version", "session", "seats", "machines"}:
            raise RuntimeError("seats.json must use the portal v1 schema exactly")
        if raw.get("version") != 1 or not isinstance(raw.get("session"), str) or not raw["session"]:
            raise RuntimeError("seats.json must have version 1 and a session")
        if not isinstance(raw.get("seats"), list):
            raise RuntimeError("seats.json seats must be a list")
        if not isinstance(raw.get("machines"), dict) or not all(
                isinstance(m, str) and MACHINE_RE.fullmatch(m) for m in raw["machines"]):
            raise RuntimeError("seats.json machines must be a map of machine names (a-z0-9-)")
        self.session = raw["session"]
        self.seats: list[dict] = []
        self.aliases: dict[str, dict] = {}
        self.by_window: dict[str, dict] = {}
        seat_ids: set[str] = set()
        for seat in raw["seats"]:
            required = {"id", "expected_name", "role", "talk_commands"}
            allowed = required | {"window_id", "quick_key"}
            if not isinstance(seat, dict) or not required <= set(seat) <= allowed:
                raise RuntimeError("each local seat must use the portal v1 schema exactly")
            if not all(isinstance(seat[k], str) and seat[k] for k in required - {"talk_commands"}):
                raise RuntimeError("seat identity fields must be strings")
            if not SEAT_ID_RE.fullmatch(seat["id"]):
                raise RuntimeError("seat id is invalid")
            if seat["id"] in seat_ids:
                raise RuntimeError("duplicate configured seat id")
            seat_ids.add(seat["id"])
            window_id = seat.get("window_id")
            if window_id is not None and (not isinstance(window_id, str)
                                          or not re.fullmatch(r"@[0-9]+", window_id)):
                raise RuntimeError("seat window_id must be a tmux @ id")
            quick_key = seat.get("quick_key")
            if quick_key is not None and (not isinstance(quick_key, str)
                                           or not re.fullmatch(r"[A-Za-z0-9]", quick_key)):
                raise RuntimeError("seat quick_key must be one alphanumeric character")
            if not isinstance(seat["talk_commands"], list) or not all(
                    isinstance(v, str) and v for v in seat["talk_commands"]):
                raise RuntimeError("talk_commands must be a string list")
            item = dict(seat, uid="seat:" + seat["id"])
            self.seats.append(item)
            if window_id:
                if window_id in self.by_window:
                    raise RuntimeError("duplicate configured window_id")
                self.by_window[window_id] = item
            aliases = [item["id"], item["uid"]]
            if window_id:
                aliases.append(window_id)
            if quick_key:
                aliases.append(quick_key)
            for alias in aliases:
                if alias in self.aliases:
                    raise RuntimeError("duplicate configured seat alias")
                self.aliases[alias] = item

        self.machines: dict[str, dict] = {}
        for machine in sorted(raw["machines"]):
            machine_value = raw["machines"][machine]
            if not isinstance(machine_value, dict) or "seats" not in machine_value or not set(machine_value) <= MACHINE_KEYS:
                raise RuntimeError("each machine must contain a seats list and only ssh / hop / render_cmd besides")
            ssh = machine_value.get("ssh")
            if ssh is not None and (not isinstance(ssh, str) or not SSH_TARGET_RE.fullmatch(ssh)):
                raise RuntimeError(f"machine {machine}: ssh must be user@host")
            hop = machine_value.get("hop") or ("ssh" if ssh else "fleet")
            if hop not in ("fleet", "ssh") or (hop == "ssh" and not ssh):
                raise RuntimeError(f"machine {machine}: hop must be fleet or ssh (ssh needs an ssh target)")
            render_cmd = machine_value.get("render_cmd")
            if render_cmd is not None and (not isinstance(render_cmd, str) or not render_cmd.strip()
                                           or any(ord(ch) < 32 for ch in render_cmd)):
                raise RuntimeError(f"machine {machine}: render_cmd must be one shell line")
            if not isinstance(machine_value["seats"], list):
                raise RuntimeError("machine seats must be a list")
            remote_seats: list[dict] = []
            by_pane: dict[str, dict] = {}
            remote_ids: set[str] = set()
            for seat in machine_value["seats"]:
                required = {"id", "pane_id", "agent", "talk_commands"}
                if not isinstance(seat, dict) or set(seat) != required:
                    raise RuntimeError("each remote seat must use the portal v1 schema exactly")
                if (not isinstance(seat["id"], str) or not SEAT_ID_RE.fullmatch(seat["id"])
                        or not isinstance(seat["pane_id"], str)
                        or not IDENTIFIER_RE.fullmatch(seat["pane_id"])
                        or not isinstance(seat["agent"], str) or not seat["agent"]
                        or len(seat["agent"]) > 128 or any(ord(ch) < 32 for ch in seat["agent"])):
                    raise RuntimeError("remote seat identity is invalid")
                if not isinstance(seat["talk_commands"], list) or not all(
                        isinstance(value, str) and value for value in seat["talk_commands"]):
                    raise RuntimeError("remote talk_commands must be a string list")
                if seat["id"] in remote_ids or seat["pane_id"] in by_pane:
                    raise RuntimeError("duplicate remote seat id or pane_id")
                item = dict(seat, uid=f"remote:{machine}:{seat['id']}")
                remote_ids.add(item["id"])
                by_pane[item["pane_id"]] = item
                remote_seats.append(item)
            self.machines[machine] = {"seats": remote_seats, "by_pane": by_pane,
                                      "via": (hop, ssh if hop == "ssh" else machine),
                                      "render_cmd": (render_cmd or "").strip()}

    def configured(self, value: str) -> dict | None:
        return self.aliases.get(value)

    def machine(self, value: object) -> dict:
        if not isinstance(value, str) or value not in self.machines:
            names = ", ".join(sorted(self.machines))
            raise BridgeError(400, f"machine must be one of: {names}" if names else "no machines configured in seats.json")
        return self.machines[value]

    def remote_seat(self, machine: str, pane_id: str) -> dict | None:
        return self.machine(machine)["by_pane"].get(pane_id)


def system_tmux(args: list[str], timeout: int = 8):
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["tmux", *args], 127, "", str(exc))


def hop_argv(machine: str, command: str, timeout: float, via=None) -> list[str]:
    """The constant argv that runs `command` on that machine: the owner's fleet tool, or plain
    ssh when the machine is configured with an ssh target. Never carries request data."""
    hop, target = via or ("fleet", machine)
    if hop == "ssh":
        return ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={max(1, min(int(timeout), 30))}",
                "-o", "StrictHostKeyChecking=accept-new", target, command]
    return ["fleet", "--quiet", "--timeout", str(max(1, int(timeout))), "run", machine, command]


def system_hop(machine: str, request: dict, timeout: float = REMOTE_TIMEOUT, via=None):
    """Run the constant remote adapter once; request data travels only on stdin."""
    argv = hop_argv(machine, REMOTE_HELPER_COMMAND, timeout, via)
    payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    try:
        stdout, stderr = process.communicate(payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    if len(stdout) > MAX_HOP_BYTES or len(stderr) > MAX_HOP_BYTES:
        return subprocess.CompletedProcess(argv, 125, "", "remote output exceeded limit")
    return subprocess.CompletedProcess(
        argv, process.returncode, stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"))


def system_render(machine: str, timeout: float = RENDER_TIMEOUT, via=None, command: str = ""):
    """Run that machine's own renderer once, over the hop, with a constant argv."""
    argv = hop_argv(machine, command, timeout, via)
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout + 5,
                              stdin=subprocess.DEVNULL, start_new_session=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 124, "", str(exc))


# ---- phone access (config key "remote") ------------------------------------------------------
# The loopback listener below is untouched. When "remote" is set the bridge opens a SECOND
# listener whose handler (RemoteHandler) serves paired phones only: nothing and no cookie before
# pairing, a short allowlist of routes after it, Host and Origin pinned to public_origin. Pairing
# codes are made from loopback only (the page, `container-map pair`).
# README: "Use it from your phone".
TAILNET = ipaddress.ip_network("100.64.0.0/10")    # Tailscale: WireGuard-encrypted end to end
REMOTE_KEYS = ("listen", "port", "public_origin", "allow_http", "trusted_proxy")
REMOTE_HOST_RE = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")
PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0 O 1 I L: read off a screen or aloud
PAIR_LENGTH = 8
PAIR_TTL = 600.0                    # a code lives ten minutes, once
PAIR_TTL_MAX = 7 * 86400.0          # `pair --hours N` may ask for longer, up to a week (still one use)
PAIR_PENDING = 5                    # codes alive at once; a sixth retires the oldest
PAIR_ATTEMPTS = 5                   # tries per peer per minute
PAIR_FAILURES = 20                  # wrong codes per peer, then PAIR_LOCKOUT seconds shut
PAIR_LOCKOUT = 900.0
DEVICE_IDLE = 30 * 86400.0          # a device unseen this long must pair again
DEVICE_MAX = 16
DEVICE_NAME_MAX = 40
DEVICE_SAVE_GAP = 60.0              # last_seen reaches the disk at most this often
DEVICE_RATE = 240                   # requests per minute per paired device
DEVICE_MATE_RATE = 4                # wingmate questions per minute per paired device
REMOTE_AUDIT_BYTES = 256 * 1024     # one file plus one rotation: never more than twice this
REMOTE_CONNECTIONS = 64             # requests served at once on the phone listener
REMOTE_RETRY_SECONDS = 30.0         # how often a phone listener that could not bind tries again
REMOTE_GET = frozenset({"/", "/state", "/tasks", "/task", "/task/diff", "/wingmate",
                        "/wingmate/answer", "/workflows", "/workflow", "/workflow/replay"})
REMOTE_GET_PREFIXES = (VENDOR_PREFIX,)     # the 3D view's scripts: /vendor/<name>, a paired device only
# a phone answers a workflow's gate (Approve / Reject: a decision, it starts nothing new by itself); start, retry,
# skip, stop and new can launch agents or shell lines and stay with the desktop page
REMOTE_POST = frozenset({"/task", "/task/followup", "/task/close", "/task/stop",
                         "/wingmate/ask", "/workflow/gate"})
# routes of the loopback bridge a phone must never reach (404, and one audit row when asked)
DESKTOP_ONLY = ("/agent/", "/file", "/control", "/open", "/door", "/say", "/permit",
                "/dispatch", "/verb", "/resolve", "/checkin", "/pane", "/remote/",
                "/task/check", "/task/rename", "/meta", "/phone", "/workflow/", "/area/", "/show/page")
# what a phone never sees in a workflow reply: where things are on disk (folders, kept files, worktrees), the branch a
# task's work sits on (a name inside the repo) and the shell lines a Start would run
PHONE_STRIP = frozenset({"path", "src", "worktree", "branch", "paths", "outputs", "shell_lines", "cwd", "repo", "evidence", "next_step", "newest_path"})
DESKTOP_FIELDS = ("verify", "retries", "setup")    # shell lines run as you: the desktop only
# A reverse proxy such as tailscale serve names the tailnet user it forwards for in this header.
# The phone listener copies it into its audit rows, only when the connection comes from
# remote.trusted_proxy (anyone else could type it). A record, never a gate: the pairing code and
# the device cookie decide who gets in.
TAILNET_LOGIN_HEADER = "Tailscale-User-Login"
TAILNET_LOGIN_FIELD = "tailscale_user_login"
TAILNET_LOGIN_MAX = 128


class Denied(BridgeError):
    """A refusal on the phone listener. status and message go to the client; reason (a fixed
    word, never a code or a cookie) goes to the audit; retry is the seconds a 429 asks to wait."""

    def __init__(self, status: int, message: str, reason: str = "", retry: float = 0.0):
        super().__init__(status, message)
        self.reason = reason
        self.retry = retry


def ip_text(value: object) -> str:
    """An IP address as one canonical string (an IPv4 address in IPv6 dress as the IPv4 one), or
    "" when `value` is not one. Brackets around an IPv6 address are tolerated."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        addr = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return ""
    if addr.version == 6 and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return str(addr)


class RemoteSettings:
    """A checked "remote" block. origin is scheme://authority as a browser writes it in Origin.
    trusted_proxy: the addresses of reverse proxies whose X-Forwarded-For is believed."""
    __slots__ = ("listen", "port", "origin", "authority", "https", "allow_http", "cookie",
                 "trusted_proxy")

    def __init__(self, listen: str, port: int, origin: str, authority: str, https: bool,
                 allow_http: bool, trusted_proxy: tuple = ()):
        self.listen, self.port, self.origin, self.authority = listen, port, origin, authority
        self.https, self.allow_http = https, allow_http
        self.trusted_proxy = tuple(trusted_proxy)
        self.cookie = "__Host-cm_device" if https else "cm_device"


def parse_remote(raw: object, bridge_port: int) -> tuple["RemoteSettings | None", str]:
    """(settings, "") for a usable "remote" block, (None, "") when there is none (access is
    off), and (None, why) when it is set but refused. The why is what the log and
    `container-map pair` print."""
    if raw is None or raw == {}:
        return None, ""
    if not isinstance(raw, dict):
        return None, ('remote must be an object like '
                      '{"listen": "<ip>", "public_origin": "https://<host>"}')
    unknown = sorted(str(k) for k in raw if k not in REMOTE_KEYS)
    if unknown:
        return None, (f"remote has unknown keys {', '.join(unknown)} "
                      f"(known: {', '.join(REMOTE_KEYS)})")
    listen = raw.get("listen")
    try:
        addr = ipaddress.ip_address(listen) if isinstance(listen, str) else None
    except ValueError:
        addr = None
    if addr is None:
        return None, ("remote.listen must be one IP address of this machine "
                      "(a Tailscale address, or 127.0.0.1 behind a proxy)")
    if addr.is_unspecified:
        return None, "remote.listen must name one interface, not 0.0.0.0 or ::"
    port = raw.get("port", bridge_port + 1)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        return None, ("remote.port must be a whole number from 1 to 65535 "
                      "(default: the bridge port + 1)")
    if port == bridge_port:
        return None, f"remote.port must differ from the bridge port ({bridge_port})"
    allow_http = raw.get("allow_http", False)
    if not isinstance(allow_http, bool):
        return None, "remote.allow_http must be true or false"
    proxies = raw.get("trusted_proxy", [])
    if not isinstance(proxies, list) or not all(ip_text(item) for item in proxies):
        return None, ('remote.trusted_proxy must be a list of the IP addresses of your reverse '
                      'proxies, like ["127.0.0.1"]')
    trusted = tuple(dict.fromkeys(ip_text(item) for item in proxies))
    origin = raw.get("public_origin")
    shape = ('remote.public_origin must look like "https://phone.example.com" '
             'or "http://100.101.102.103:7429"')
    if not isinstance(origin, str) or not origin:
        return None, shape
    if any(ch.isspace() or ord(ch) < 32 for ch in origin):
        return None, shape
    try:
        parts = urlsplit(origin)
        explicit = parts.port
        host = (parts.hostname or "").lower()
    except ValueError:
        return None, shape
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None, shape
    if parts.username is not None or parts.password is not None:
        return None, shape
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return None, shape
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not REMOTE_HOST_RE.fullmatch(host) or len(host) > 253:
            return None, shape
    https = parts.scheme.lower() == "https"
    shown = f"[{host}]" if ":" in host else host
    authority = shown if explicit in (None, 443 if https else 80) else f"{shown}:{explicit}"
    if not https and not (addr.version == 4 and addr in TAILNET) and not allow_http:
        return None, ("remote.public_origin is http:// and remote.listen is not a Tailscale "
                      "address (100.64.0.0/10), so the device cookie would cross the network in "
                      "clear text: use an https:// public_origin behind a reverse proxy, or set "
                      '"allow_http": true to accept that')
    origin = f"{parts.scheme.lower()}://{authority}"
    return RemoteSettings(str(addr), port, origin, authority, https, allow_http, trusted), ""


class SlidingLimit:
    """`limit` hits per `window` seconds for each key. take() counts a hit and returns 0.0, or
    counts nothing and returns the seconds until the oldest hit leaves the window."""

    def __init__(self, limit: int, window: float, clock):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def take(self, key: str) -> float:
        now = self.clock()
        with self.lock:
            row = [t for t in self.hits.get(key, ()) if now - t < self.window]
            if len(row) >= self.limit:
                self.hits[key] = row
                return max(0.1, self.window - (now - row[0]))
            row.append(now)
            self.hits[key] = row
            if len(self.hits) > 2048:       # a flood of peers must not grow this without end
                self.hits = {k: v for k, v in self.hits.items()
                             if v and now - v[-1] < self.window}
        return 0.0


class PairGate:
    """Per peer: PAIR_ATTEMPTS tries a minute, and PAIR_LOCKOUT seconds of refusing everything
    after PAIR_FAILURES wrong codes. A right code clears the peer's count."""

    def __init__(self, clock):
        self.clock = clock
        self.attempts = SlidingLimit(PAIR_ATTEMPTS, 60.0, clock)
        self.failures: dict[str, list[float]] = {}
        self.locked: dict[str, float] = {}
        self.lock = threading.Lock()

    def begin(self, peer: str) -> tuple[float, str]:
        """(0.0, "") when this peer may try a code now (the try is counted), else (seconds to
        wait, why)."""
        now = self.clock()
        with self.lock:
            until = self.locked.get(peer, 0.0)
            if until > now:
                return until - now, "pair-locked"
            self.locked.pop(peer, None)
        wait = self.attempts.take(peer)
        return (wait, "pair-rate") if wait else (0.0, "")

    def failed(self, peer: str) -> None:
        now = self.clock()
        with self.lock:
            row = [t for t in self.failures.get(peer, ()) if now - t < PAIR_LOCKOUT]
            row.append(now)
            if len(row) >= PAIR_FAILURES:
                self.locked[peer] = now + PAIR_LOCKOUT
                row = []
            self.failures[peer] = row
            if len(self.failures) > 2048:
                self.failures = {k: v for k, v in self.failures.items()
                                 if v and now - v[-1] < PAIR_LOCKOUT}
            if len(self.locked) > 2048:
                self.locked = {k: v for k, v in self.locked.items() if v > now}

    def passed(self, peer: str) -> None:
        with self.lock:
            self.failures.pop(peer, None)


def _code_digest(code: str) -> str:
    return hashlib.sha256(code.encode("ascii", "replace")).hexdigest()


class PairingCodes:
    """Single-use codes, kept only as hashes. In memory only by default; with a path (the phone
    listener's), the hashes and their deadlines are also kept in a mode-600 file, so a restart of the
    map does not void a long-lived code the owner has not typed in yet."""

    def __init__(self, clock, path: str | None = None):
        self.clock = clock
        self.path = path
        self.codes: dict[str, float] = {}      # sha256 of the code -> when it dies
        self.lock = threading.Lock()
        if path:
            try:
                with open(path, encoding="utf-8") as handle:
                    raw = json.load(handle).get("codes", {})
                self.codes = {k: float(v) for k, v in raw.items()
                              if re.fullmatch(r"[0-9a-f]{64}", str(k)) and isinstance(v, (int, float))}
            except (OSError, ValueError, AttributeError, TypeError):
                self.codes = {}

    def _save(self) -> None:
        if not self.path:
            return
        data = json.dumps({"version": 1, "codes": self.codes}).encode("utf-8")
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            temp = f"{self.path}.new.{secrets.token_hex(4)}"
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            pass                    # the code still works until a restart; the file is a convenience

    def _prune(self, now: float) -> bool:
        dead = [k for k, dies in self.codes.items() if dies < now]
        for key in dead:
            del self.codes[key]
        return bool(dead)

    def create(self, ttl: float | None = None) -> tuple[str, float]:
        life = PAIR_TTL if ttl is None else max(60.0, min(PAIR_TTL_MAX, float(ttl)))
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_LENGTH))
        now = self.clock()
        with self.lock:
            self._prune(now)
            while len(self.codes) >= PAIR_PENDING:
                del self.codes[min(self.codes, key=self.codes.get)]
            self.codes[_code_digest(code)] = now + life
            self._save()
        return code, life

    def consume(self, raw: str) -> bool:
        """True exactly once for a live code. Spaces and dashes are ignored, case is not."""
        code = re.sub(r"[\s-]", "", raw).upper()
        if len(code) != PAIR_LENGTH or any(ch not in PAIR_ALPHABET for ch in code):
            return False
        now = self.clock()
        with self.lock:
            pruned = self._prune(now)
            hit = self.codes.pop(_code_digest(code), None) is not None
            if hit or pruned:
                self._save()
            return hit

    def pending(self) -> int:
        with self.lock:
            if self._prune(self.clock()):
                self._save()
            return len(self.codes)


class DeviceSessions:
    """The paired phones: one row per device in a mode-600 file. The cookie value is 32 random
    bytes; only its SHA-256 is stored, with the device's name and when it was paired and last
    seen."""

    def __init__(self, path: str, clock):
        self.path, self.clock = path, clock
        self.lock = threading.Lock()
        # read on first use: a bridge without phone access never touches the file
        self.rows: list[dict] | None = None
        self.saved = 0.0
        self.warned = False

    def _ensure(self) -> list[dict]:
        if self.rows is None:
            self.rows = []
            try:
                with open(self.path, encoding="utf-8") as handle:
                    raw = json.load(handle)
            except FileNotFoundError:
                return self.rows
            except (OSError, ValueError) as exc:
                print(f"phone access: {self.path} is unreadable ({exc}); every device must "
                      "pair again", file=sys.stderr, flush=True)
                return self.rows
            for item in (raw.get("devices") if isinstance(raw, dict) else None) or []:
                numbers = isinstance(item, dict) and all(
                    isinstance(item.get(k), (int, float)) and not isinstance(item.get(k), bool)
                    for k in ("created", "last_seen"))
                if not numbers or not isinstance(item.get("id"), str):
                    continue
                if isinstance(item.get("name"), str) and re.fullmatch(
                        r"[0-9a-f]{64}", str(item.get("hash", ""))):
                    keys = ("id", "hash", "name", "created", "last_seen")
                    self.rows.append({k: item[k] for k in keys})
        now = self.clock()
        idle = [r for r in self.rows if now - r["last_seen"] > DEVICE_IDLE]
        if idle:                  # a device unseen for 30 days is no longer paired, listed or not
            self.rows[:] = [r for r in self.rows if r not in idle]
            self._save_quietly()
        return self.rows

    def _save(self) -> None:
        data = json.dumps({"version": 1, "devices": self.rows}, indent=1).encode("utf-8")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        temp = f"{self.path}.new.{secrets.token_hex(4)}"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        os.chmod(self.path, 0o600)
        self.saved = self.clock()

    def _save_quietly(self) -> None:
        try:
            self._save()
        except OSError as exc:
            if not self.warned:
                self.warned = True
                print(f"phone access: could not write {self.path}: {exc}",
                      file=sys.stderr, flush=True)

    def count(self) -> int:
        with self.lock:
            return len(self._ensure())

    def issue(self, name: str) -> tuple[dict, str]:
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self.lock:
            rows = self._ensure()
            ident = secrets.token_hex(4)
            while any(r["id"] == ident for r in rows):
                ident = secrets.token_hex(4)
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            row = {"id": ident, "hash": digest, "name": name, "created": now, "last_seen": now}
            rows.append(row)
            try:
                self._save()
            except OSError:
                rows.remove(row)
                raise
        return dict(row), token

    def find(self, token: str) -> dict | None:
        """The device this cookie value belongs to, now seen; None for an unknown, revoked or
        idle one."""
        if not token or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()
        now = self.clock()
        with self.lock:
            hit = None
            for row in self._ensure():
                if hmac.compare_digest(row["hash"], digest):
                    hit = row
            if hit is None:
                return None
            hit["last_seen"] = now
            if now - self.saved >= DEVICE_SAVE_GAP:
                self._save_quietly()
            return dict(hit)

    def revoke(self, ident: str) -> bool:
        with self.lock:
            rows = self._ensure()
            hit = next((r for r in rows if r["id"] == ident), None)
            if hit is None:
                return False
            rows.remove(hit)         # dead for the very next request, file write or not
            self._save()
            return True

    def listing(self) -> list[dict]:
        with self.lock:
            return [{"id": r["id"], "name": r["name"], "created": utc_stamp(r["created"]),
                     "last_seen": utc_stamp(r["last_seen"])} for r in self._ensure()]


class RemoteAudit:
    """Refused requests on the phone listener, one JSON row each: route, status, reason, peer,
    and the tailnet login a trusted proxy named (TAILNET_LOGIN_FIELD, only when it named one).
    Never a code, a cookie or a body. One file; at REMOTE_AUDIT_BYTES it becomes <file>.1 (the
    older one goes)."""

    def __init__(self, path: str, limit: int = REMOTE_AUDIT_BYTES):
        self.path, self.limit = path, limit
        self.lock = threading.Lock()
        self.warned = False

    def add(self, route: str, status: int, reason: str, peer: str, login: str = "") -> None:
        row = {"timestamp": utc_stamp(), "route": str(route)[:80], "status": int(status),
               "reason": str(reason)[:40], "peer": str(peer)[:64]}
        if login:
            row[TAILNET_LOGIN_FIELD] = str(login)[:TAILNET_LOGIN_MAX]
        data = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
        with self.lock:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                try:
                    if os.path.getsize(self.path) + len(data) > self.limit:
                        os.replace(self.path, self.path + ".1")
                except FileNotFoundError:
                    pass
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, data)
                finally:
                    os.close(fd)
            except OSError as exc:
                if not self.warned:
                    self.warned = True
                    print(f"phone access: could not write {self.path}: {exc}",
                          file=sys.stderr, flush=True)


class RemoteAccess:
    """Everything the phone listener knows: its settings, the pairing codes, the paired devices,
    the rate limits and the audit. BridgeApp owns one; configure() reads the settings once the
    port is known."""

    def __init__(self, raw: object, state_dir: str, clock=time.time):
        self.raw, self.clock = raw, clock
        self.settings: RemoteSettings | None = None
        self.refused = ""          # why a "remote" block that is set did not come up
        self.error = ""            # why the listener could not open its port
        self.server = None
        self.thread: threading.Thread | None = None
        self.codes = PairingCodes(clock, os.path.join(state_dir, "remote-codes.json"))
        self.gate = PairGate(clock)
        self.requests = SlidingLimit(DEVICE_RATE, 60.0, clock)
        self.mate = SlidingLimit(DEVICE_MATE_RATE, 60.0, clock)
        self.devices = DeviceSessions(os.path.join(state_dir, "remote-sessions.json"), clock)
        self.audit = RemoteAudit(os.path.join(state_dir, "remote-audit.log"))
        self.lock = threading.Lock()          # a bind attempt and a stop never overlap
        self.stopping = threading.Event()
        self.retry: threading.Thread | None = None
        self.reported: set[str] = set()       # bind failure kinds already said once

    def configure(self, bridge_port: int) -> None:
        self.settings, self.refused = parse_remote(self.raw, bridge_port)

    @property
    def listening(self) -> bool:
        return self.server is not None


# ---- workflows and shows: what the routes below share -----------------------------------------
def strip_paths(value: object) -> object:
    """A workflow reply as a phone may see it: every key of PHONE_STRIP dropped, at any depth (a step's
    folder, a kept file's source, a thread's worktree, the shell lines a Start runs)."""
    if isinstance(value, dict):
        return {k: strip_paths(v) for k, v in value.items() if k not in PHONE_STRIP}
    if isinstance(value, list):
        return [strip_paths(v) for v in value]
    return value


def _phone_folders(world, cfg):
    """Reduce private folders to identity and counts before stripping the paths used to match them.
    Rooms occur both inside planets and as characters, including in page_world's joined form.
    """
    private = cfg.get("private", [])
    private = [v.strip() for v in private if isinstance(v, str) and v.strip()] if isinstance(private, list) else []
    root = os.path.abspath(os.path.expanduser(cfg.get("work_root") or "."))
    private_names = {v for v in private if os.sep not in v and not v.startswith("~")}
    private_paths = [spelling for v in private if v not in private_names
                     for spelling in _spellings(os.path.join(root, os.path.expanduser(v)))]
    folders = []

    def collect(value):
        if isinstance(value, dict):
            if value.get("kind") in ("area", "dir"):
                folders.append(value)
            for v in value.values():
                collect(v)
        elif isinstance(value, list):
            for v in value:
                collect(v)
    collect(world)
    by_uid = {v.get("uid"): v for v in folders if v.get("uid")}

    def path_of(folder):
        path = folder.get("path")
        if isinstance(path, str) and path:
            return os.path.abspath(os.path.expanduser(path))
        parent = by_uid.get(folder.get("area"), {})
        if parent.get("path"):
            return os.path.join(parent["path"], str(folder.get("name") or ""))
        return ""

    def beneath(path, base):
        return bool(path and base and path.startswith(base.rstrip(os.sep) + os.sep))

    hidden = []
    for folder in folders:
        path, name = path_of(folder), str(folder.get("name") or "")
        real = os.path.realpath(path) if path else ""
        if (folder.get("hidden") or name.startswith(".") or name in private_names
                or os.path.basename(path) in private_names or path in private_paths
                or os.path.basename(real) in private_names or real in private_paths
                or (path and phone_private(path, cfg) is not None)):
            hidden.append((folder.get("uid"), path))
    # Only the outer private folder's name may travel; its descendant folders are contents too.
    hidden = [(uid, path) for uid, path in hidden
              if not any(beneath(path, base) for _, base in hidden)]
    ids = {uid for uid, _ in hidden if uid}
    bases = [path for _, path in hidden if path]
    summary = {"uid", "kind", "name", "files", "dirs", "size", "children_total", "root_files_total",
               "area", "gx", "gy", "hall", "block"}
    omitted = object()

    def scrub(value):
        if isinstance(value, dict):
            uid, path = value.get("uid"), path_of(value)
            if (value.get("area") in ids or any(beneath(path, base) for base in bases)
                    or any(str(uid or "").startswith(str(ident) + "/") for ident in ids)):
                return omitted
            if uid in ids or (path and path in bases):
                return dict({k: v for k, v in value.items() if k in summary}, hidden=True)
            return {k: got for k, v in value.items() if (got := scrub(v)) is not omitted}
        if isinstance(value, list):
            return [got for v in value if (got := scrub(v)) is not omitted]
        return value
    return scrub(world)


_PHONE_ID_KEY = secrets.token_bytes(16)     # per run: a phone's stand-in ids hold only while this bridge runs


def phone_opaque(kind: str, ident: object) -> str:
    """The id a phone sees for a task or workflow in a private folder. A real id is made from the prompt's first
    words (20261106-050601-medical-results-...), so it does not travel either."""
    mac = hmac.new(_PHONE_ID_KEY, f"{kind}:{ident}".encode("utf-8", "surrogatepass"), hashlib.sha256)
    return "private-" + mac.hexdigest()[:16]


def _spellings(path: str) -> set:
    """A path as written (with '..' folded) and as the file system resolves it (each link before any '..' after
    it, as the kernel does): a folder reached through a symlink, or a work_root that is one, is private either way."""
    raw = os.path.expanduser(path)
    return {os.path.abspath(raw), os.path.realpath(raw)}


def phone_private(path, cfg=None):
    """The private folder a task's folder lies in, by the name a phone may see (a dot-folder, or one the config's
    `private` list names, by name at any depth or by path), else None. The same rule as _phone_folders, run on the
    path as written and as resolved, against the work_root and the private paths as written and as resolved."""
    cfg = _CFG if cfg is None else cfg
    if not isinstance(path, str) or not path:
        return None
    private = cfg.get("private", [])
    private = [v.strip() for v in private if isinstance(v, str) and v.strip()] if isinstance(private, list) else []
    roots = _spellings(cfg.get("work_root") or ".")
    names = {v for v in private if os.sep not in v and not v.startswith("~")}
    bases = []                  # (a spelling of a private folder, the name a phone sees for it)
    for value in private:
        for root in roots:
            joined = os.path.join(root, os.path.expanduser(value))
            if value in names and not os.path.lexists(joined):
                continue        # a name counts at any depth (below); one at the top also counts where it leads
            written = os.path.abspath(joined)
            label = value if value in names else (os.path.basename(written.rstrip(os.sep)) or written)
            bases += [(spelling, label) for spelling in _spellings(joined)]
    for real in _spellings(path):
        for base, label in bases:
            if real == base or real.startswith(base.rstrip(os.sep) + os.sep):
                return label
        root = next((r for r in roots if real == r or real.startswith(r.rstrip(os.sep) + os.sep)), None)
        rel = os.path.relpath(real, root) if root else real.lstrip(os.sep)
        for part in rel.split(os.sep):
            if part in names or (part.startswith(".") and part not in (".", "..")):
                return part
    return None


PHONE_TASK_KEEP = ("id", "status", "created", "started", "ended", "mode", "model", "runner", "retries",
                   "retry_due")


def phone_task_row(row, cfg=None):
    """A task as a phone may see it: a task in a private folder is its state, the folder's name and a stand-in id
    (no title, words, path, or error that might quote a file); any other task as the desktop page sees it."""
    if not isinstance(row, dict):
        return row
    cfg = _CFG if cfg is None else cfg
    name = phone_private(row.get("target"), cfg)
    if name is not None:
        out = {k: row[k] for k in PHONE_TASK_KEEP if k in row}
        out.update(id=phone_opaque("task", row.get("id")), private=True, target=name,
                   title="a task in " + name, text="")
        return out
    return row


def phone_private_planets(world, cfg=None) -> dict:
    """{uid: folder name} for the world's planets and moons (rooms) that lie in a private folder, in either of the
    forms a world takes (rooms inside their planet, or as characters)."""
    cfg = _CFG if cfg is None else cfg
    folders = []

    def collect(value):
        if isinstance(value, dict):
            if value.get("kind") in ("area", "dir") and value.get("uid"):
                folders.append(value)
            for v in value.values():
                collect(v)
        elif isinstance(value, list):
            for v in value:
                collect(v)
    collect((world or {}).get("chars") or [])
    paths = {str(f["uid"]): f.get("path") for f in folders if isinstance(f.get("path"), str) and f.get("path")}
    out = {}
    for folder in folders:
        uid = str(folder["uid"])
        path = paths.get(uid) or (os.path.join(paths[str(folder.get("area"))], str(folder.get("name") or ""))
                                  if str(folder.get("area")) in paths else None)
        name = phone_private(path, cfg) if path else None
        if name is not None:
            out[uid] = name
    return out


def phone_workflow_private(flow, private) -> str | None:
    """The private folder a workflow works in, by its name: a step's folder (private["workflow"], read from the
    specs), a step's thread in one (private["task"]), or a step on one's planet (private["planet"]: a derived
    workflow, the night run, has no spec). None for any other workflow."""
    if not isinstance(flow, dict):
        return None
    tasks, flows = (private or {}).get("task") or {}, (private or {}).get("workflow") or {}
    planets = (private or {}).get("planet") or {}
    if str(flow.get("id")) in flows:
        return flows[str(flow.get("id"))]
    for step in flow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if step.get("tid") and str(step["tid"]) in tasks:
            return tasks[str(step["tid"])]
        for key in ("planet_uid", "moon_uid"):
            if step.get(key) and str(step[key]) in planets:
                return planets[str(step[key])]
    return None


PHONE_FLOW_KEEP = ("by", "state", "updated", "seq", "done", "total", "elapsed_s", "derived")


def phone_workflow_row(flow, private):
    """A workflow as a phone may see it: one that works in a private folder is its state, its count of steps and
    the folder's name (no title, goal, step names, reasons or places); any other as the desktop page sees it."""
    name = phone_workflow_private(flow, private)
    if name is None:
        return flow
    steps = [st for st in flow.get("steps") or [] if isinstance(st, dict)]
    keys = {st.get("key"): f"step-{i + 1}" for i, st in enumerate(steps)}
    out = {k: flow[k] for k in PHONE_FLOW_KEEP if k in flow}
    out.update(id=phone_opaque("workflow", flow.get("id")), private=True, title="a workflow in " + name, goal="",
               sentence="a workflow in " + name,
               current=[keys[k] for k in flow.get("current") or [] if k in keys],
               steps=[dict({"key": keys[st.get("key")], "label": keys[st.get("key")].replace("-", " "),
                            "kind": st.get("kind"), "state": st.get("state"),
                            "after": [keys[a] for a in st.get("after") or [] if a in keys]
                            if isinstance(st.get("after"), list) else []},
                           **({"tid": phone_opaque("task", st["tid"])} if st.get("tid") else {}))
                      for st in steps])
    return out


def phone_shows(shows, private):
    """The live shows a phone may see: one anchored (itself or any of its ops) on a planet or moon in a private
    folder is left out, its words and all."""
    planets = (private or {}).get("planet") or {}
    if not planets or not isinstance(shows, list):
        return shows

    def anchored(value):
        if isinstance(value, dict):
            return any((k in ("at", "target", "uid", "planet_uid", "moon_uid") and isinstance(v, str) and v in planets)
                       or anchored(v) for k, v in value.items())
        if isinstance(value, list):
            return any(anchored(v) for v in value)
        return False
    return [show for show in shows if not anchored(show)]


def _phone_contact(contact, private, keys=None):
    """A sky contact of a private folder's task or workflow: its state, said as the folder's name, on no planet
    (a sub-folder's planet would name it). keys: {workflow id: {step key: "step-N"}}, as phone_workflow_row names
    the steps."""
    if not isinstance(contact, dict):
        return contact
    ident = str(contact.get("id") or "")
    kind, _, rest = ident.partition(":")
    if kind == "task" and rest in private["task"]:
        said = "a task in " + private["task"][rest]
    elif kind == "workflow" and rest.partition(":")[0] in private["workflow"]:
        said = "a workflow in " + private["workflow"][rest.partition(":")[0]]
        wid, _, key = rest.partition(":")
        ident = "workflow:" + wid + (":" + ((keys or {}).get(wid) or {}).get(key, "step") if key else "")
    else:
        return contact
    return dict(contact, id=ident, title=said, voice=said, planet_uid=None,
                open={"uid": ident} if kind == "task" else None)


def phone_swap_ids(value, private):
    """Every real id of a private folder's task or workflow, anywhere in a phone's reply (a branch cm/<id>, a
    contact task:<id>, a log line), as its stand-in."""
    swaps = {str(ident): phone_opaque(kind, ident) for kind in ("task", "workflow")
             for ident in ((private or {}).get(kind) or {}) if ident}
    if not swaps:
        return value
    # whole ids only: a longer id that begins with a private one (…-diary-123abc-456def) is another thread
    pattern = re.compile(r"(?<![A-Za-z0-9_-])(?:" + "|".join(re.escape(k) for k in sorted(swaps, key=len, reverse=True))
                         + r")(?![A-Za-z0-9_-])")

    def walk(v):
        if isinstance(v, str):
            return pattern.sub(lambda m: swaps[m.group(0)], v)
        if isinstance(v, dict):
            return {(walk(k) if isinstance(k, str) else k): walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(value)


def phone_world(world, cfg=None, private=None):
    """The world as a phone may see it. `private`: the bridge's phone_private_ids() (every thread and workflow in a
    private folder, the old ones too); the world's own rows are read for more."""
    import constellations
    cfg = _CFG if cfg is None else cfg
    # a private folder's threads and workflows: their rows are their state and the folder's name, and so are their
    # contacts in the sky; their real ids (made from the prompt's words) are swapped for stand-ins everywhere
    private = {"task": dict((private or {}).get("task") or {}), "workflow": dict((private or {}).get("workflow") or {}),
               "planet": dict((private or {}).get("planet") or {}, **phone_private_planets(world, cfg))}
    if isinstance(world.get("agent_tasks"), list):
        for row in world["agent_tasks"]:
            name = phone_private(row.get("target"), cfg) if isinstance(row, dict) else None
            if name is not None and row.get("id"):
                private["task"][str(row["id"])] = name
        world = dict(world, agent_tasks=[phone_task_row(row, cfg) for row in world["agent_tasks"]])
    keys = {}
    if isinstance(world.get("workflows"), list):
        for flow in world["workflows"]:
            name = phone_workflow_private(flow, private)
            if name is not None and flow.get("id"):
                private["workflow"][str(flow["id"])] = name
                keys[str(flow["id"])] = {st.get("key"): f"step-{i + 1}" for i, st in enumerate(
                    st for st in flow.get("steps") or [] if isinstance(st, dict))}
        world = dict(world, workflows=[phone_workflow_row(flow, private) for flow in world["workflows"]])
    if isinstance(world.get("shows"), list):
        world = dict(world, shows=phone_shows(world["shows"], private))
    sky = world.get("sky")
    if (private["task"] or private["workflow"]) and isinstance(sky, dict) and isinstance(sky.get("contacts"), list):
        world = dict(world, sky=dict(sky, contacts=[_phone_contact(c, private, keys) for c in sky["contacts"]]))
    sent = strip_paths(_phone_folders(world, cfg))
    groups = world.get("constellations", [])
    sent["constellations"] = constellations.phone(groups)
    hidden = {ident.split(":", 1)[-1] for g in groups if g.get("private")
              for t in g.get("threads", []) for ident in t.get("member_ids", [t["id"]])}
    # and every private thread that is not drawn as a star (dropped as noise, folded under another lane's root)
    hidden |= {ident.split(":", 1)[-1] for g in groups if g.get("private") for ident in g.get("private_ids", [])}
    names = tuple(n + ": " for g in groups if g.get("private") for n in g.get("private_titles", []) if n)
    # Thread metadata is also exposed as ships and sky contacts. Hide those aliases on phones.
    ends = tuple(":" + tid for tid in hidden)
    def prune(value):
        if isinstance(value, list):
            return [prune(v) for v in value if not (isinstance(v, dict) and any(
                str(v.get(k, "")).endswith(":" + tid) for k in ("uid", "id") for tid in hidden))
                    and not (isinstance(v, str) and names and v.startswith(names))    # "<title>: <cause>" lines
                    and not (isinstance(v, str) and ends and v.endswith(ends))]       # a planet's "ship:t3:<id>"
        if isinstance(value, dict):
            return {k: prune(v) for k, v in value.items()}
        return value
    return phone_swap_ids(prune(sent), private)


class _BadOp(Exception):
    """One show op that cannot be drawn: it is skipped (and said why), the others are kept."""


def _show_plain(value: object, field: str, limit: int, clamped: list, required: bool = False) -> str | None:
    """A text field of a show: plain text (no control or direction characters: 400 for the whole show, a
    page must never be steered by them), cut to `limit` and named in `clamped` when longer. A missing
    required field or a value that is not text makes the op bad."""
    if value is None or value == "":
        if required:
            raise _BadOp(f"{field} is required")
        return None
    if not isinstance(value, str):
        raise _BadOp(f"{field} must be text")
    if unencodable_text(value) or any(ord(ch) < 32 or ord(ch) == 127 or ch in _workflows.BIDI
                                      or ch in "\u0085  " for ch in value):
        raise BridgeError(400, f"show text must be plain: no control or direction characters ({field})")
    text = " ".join(value.split())
    if not text and required:
        raise _BadOp(f"{field} is required")
    if len(text) > limit:
        text = text[:limit]
        if field.split(".")[-1] not in clamped:
            clamped.append(field.split(".")[-1])
    return text or None


def _is_number(value: object) -> bool:
    """A number a show may carry: an int of any size, or a float that is not nan or infinite. (math.isfinite turns
    its argument into a float first, so it raises OverflowError on an int past 1.8e308: ints are never asked.)"""
    return not isinstance(value, bool) and (isinstance(value, int) or (isinstance(value, float) and math.isfinite(value)))


def _show_number(value: object, field: str, lo: float, hi: float, default, clamped: list, whole: bool = False):
    if value is None:
        return default
    if not _is_number(value):
        raise _BadOp(f"{field} must be a number")
    got = int(value) if whole else value
    if got < lo or got > hi:                  # ints compare exactly with floats: a huge int is clamped, not converted
        got = max(lo, min(hi, got))
        if field not in clamped:
            clamped.append(field)
    return int(got) if whole else round(float(got), 3)


def _show_progress(value: object, clamped: list) -> dict | None:
    if value is None or value == "":
        return None
    if isinstance(value, str) and re.fullmatch(r"\s*\d{1,9}\s*/\s*\d{1,9}\s*", value):
        done, total = (int(x) for x in value.split("/"))
    elif (isinstance(value, dict) and set(value) == {"done", "total"}
          and all(isinstance(value[k], int) and not isinstance(value[k], bool) for k in value)):
        done, total = value["done"], value["total"]
    else:
        raise _BadOp("progress is done/total (e.g. 3/9)")
    top = 10 ** 6
    fixed_total = max(1, min(top, total))
    fixed_done = max(0, min(fixed_total, done))
    if (fixed_done, fixed_total) != (done, total) and "progress" not in clamped:
        clamped.append("progress")
    return {"done": fixed_done, "total": fixed_total}


def _show_values(value: object, clamped: list) -> list:
    if not isinstance(value, list) or not value:
        raise _BadOp("values is a list of numbers")
    if any(not _is_number(v) or (isinstance(v, int) and abs(v) > sys.float_info.max) for v in value):
        raise BridgeError(400, "chart values must be finite numbers")
    if len(value) > 64:
        value = value[:64]
        if "values" not in clamped:
            clamped.append("values")
    return [v if isinstance(v, int) else round(v, 6) for v in value]


def _show_rows(value: object, clamped: list) -> list:
    """A card's rows: [[k, v], ...] or {k: v}, at most 8 of [k <= 20, v <= 40]."""
    if value is None:
        return []
    if isinstance(value, dict):
        value = [[k, v] for k, v in value.items()]
    if not isinstance(value, list):
        raise _BadOp("rows is a list of [key, value] pairs")
    rows = []
    for row in value:
        if isinstance(row, dict) and set(row) <= {"k", "v"}:
            row = [row.get("k"), row.get("v")]
        if not (isinstance(row, (list, tuple)) and len(row) == 2):
            raise _BadOp("each row is [key, value]")
        k, v = row
        v = str(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
        rows.append([_show_plain(k, "rows", 20, clamped, required=True) or "",
                     _show_plain(v, "rows", 40, clamped) or ""])
    if len(rows) > 8:
        rows = rows[:8]
        if "rows" not in clamped:
            clamped.append("rows")
    return rows


def _show_bar(value: object, clamped: list) -> list | None:
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"\s*\d{1,9}\s*/\s*\d{1,9}\s*", value):
        value = [int(x) for x in value.split("/")]
    if not (isinstance(value, list) and len(value) == 2 and all(_is_number(x) for x in value)):
        raise _BadOp("bar is [a, b] (a of b)")
    top = 10 ** 6
    b = max(0, min(top, value[1]))
    a = max(0, min(b, value[0]))
    if [a, b] != list(value) and "bar" not in clamped:
        clamped.append("bar")
    return [a, b]


def clean_show(body: object, resolve) -> tuple[dict, list, list, list]:
    """A show as an agent posted it -> (show, skipped, clamped, errors). show = {id?, by, ttl, at, ops}; skipped =
    the names of the ops not kept (an unknown op, or a bad one: errors says why); clamped = the fields cut or moved
    into range. resolve(at) -> the uid or place an anchor names on this map, or None. A body that is not a show,
    text with control characters, or chart values that are not finite numbers are a 400 for the whole show."""
    if not isinstance(body, dict) or "ops" not in body or not set(body) <= {"id", "by", "ttl", "at", "ops"}:
        raise BridgeError(400, "a show is {ops[, at, by, ttl, id]}")
    if not isinstance(body["ops"], list):
        raise BridgeError(400, "ops is a list of ops ({op: " + "|".join(SHOW_OPS) + ", ...})")
    clamped, skipped, errors = [], [], []
    sid = body.get("id")
    if sid is not None and (not isinstance(sid, str) or not SHOW_ID_RE.fullmatch(sid)):
        raise BridgeError(400, "a show id is 1-40 lower-case letters, digits or dashes")
    try:
        by = _show_plain(body.get("by"), "by", 24, clamped) if body.get("by") is not None else None
    except _BadOp:
        raise BridgeError(400, "by is the author's name: text of at most 24 characters") from None
    ttl = body.get("ttl")
    if ttl is not None and not _is_number(ttl):
        raise BridgeError(400, "ttl is a number of seconds")
    raw_ttl = ttl
    # a show that carries a page lives by the page's bounds (up to 24 h, 30 min by default); if the page op turns out
    # bad and is skipped, the rest of the show falls back to the other shows' bounds below
    paged = show_has_page(body)
    bounds = PAGE_TTL if paged else SHOW_TTL
    ttl = _show_number(ttl, "ttl", bounds[0], bounds[1], bounds[2], clamped, whole=True)
    default_at = None
    if body.get("at") not in (None, ""):
        if not isinstance(body["at"], str):
            raise BridgeError(400, "at is a uid, place:<name> or a folder path")
        default_at = resolve(body["at"])
        if default_at is None:
            raise BridgeError(400, f"at {body['at'][:60]!r} is not on the map")
    ops = body["ops"]
    if len(ops) > SHOW_ITEMS:
        ops = ops[:SHOW_ITEMS]
        clamped.append("ops")
    kept = []
    for n, raw in enumerate(ops, 1):
        name = raw.get("op") if isinstance(raw, dict) else None
        label = name if isinstance(name, str) and re.fullmatch(r"[a-z_]{1,24}", name) else f"op {n}"
        if label not in SHOW_OPS:
            skipped.append(label)
            errors.append(f"{label}: not an op the map draws (one of {', '.join(SHOW_OPS)})")
            continue
        try:
            if label == "page" and any(op["op"] == "page" for op in kept):
                raise _BadOp("a show holds one page")
            kept.append(_clean_op(raw, label, default_at, resolve, clamped))
        except _BadOp as exc:
            skipped.append(label)
            errors.append(f"{label}: {exc}")
    if paged and not any(op["op"] == "page" for op in kept):
        scratch: list = []
        ttl = _show_number(raw_ttl, "ttl", SHOW_TTL[0], SHOW_TTL[1], SHOW_TTL[2], scratch, whole=True)
        if scratch and "ttl" not in clamped:
            clamped.append("ttl")
    show = {"by": by or "an agent", "ttl": ttl, "at": default_at, "ops": kept}
    if sid:
        show["id"] = sid
    return show, skipped, clamped, errors


def show_has_page(body: object) -> bool:
    """Whether a posted show names a page op (which decides its ttl bounds; whether the page is any good is for
    _clean_op, and for show_put when the body was over MAX_TASK_BODY)."""
    ops = body.get("ops") if isinstance(body, dict) else None
    return isinstance(ops, list) and any(isinstance(o, dict) and o.get("op") == "page" for o in ops[:SHOW_ITEMS])


OP_FIELDS = {"label": {"text"}, "marker": {"icon", "text"}, "ring": {"state", "progress"}, "beam": {"from", "to"},
             "route": {"stops"}, "radio": {"from", "text"}, "fx": {"preset", "color", "count", "life", "text"},
             "card": {"title", "rows", "text", "bar"}, "chart": {"label", "values", "unit", "kind"},
             "page": {"title", "html"}}
ANCHORED = ("label", "marker", "ring", "fx", "card", "chart", "page")


def _clean_op(raw: dict, name: str, default_at, resolve, clamped: list) -> dict:
    extra = set(raw) - OP_FIELDS[name] - {"op", "at", "xray"}
    if extra:
        raise _BadOp(f"unknown field {sorted(extra)[0]!r}")
    op = {"op": name}
    if raw.get("at") not in (None, ""):
        if not isinstance(raw["at"], str):
            raise _BadOp("at is a uid, place:<name> or a folder path")
        op["at"] = resolve(raw["at"])
        if op["at"] is None:
            raise _BadOp(f"at {raw['at'][:60]!r} is not on the map")
    elif name == "page":
        op["at"] = default_at or resolve(PAGE_AT)       # a page with no anchor sits at place:pages
    elif name in ANCHORED:
        if default_at is None:
            raise _BadOp("needs at (here or on the show)")
        op["at"] = default_at
    if "xray" in raw:
        if not isinstance(raw["xray"], bool):
            raise _BadOp("xray is true or false")
        if raw["xray"]:
            op["xray"] = True
    if name == "label":
        op["text"] = _show_plain(raw.get("text"), "text", 80, clamped, required=True)
    elif name == "marker":
        if raw.get("icon") not in SHOW_ICONS:
            raise _BadOp(f"icon is {', '.join(SHOW_ICONS)}")
        op["icon"] = raw["icon"]
        text = _show_plain(raw.get("text"), "text", 40, clamped)
        if text:
            op["text"] = text
    elif name == "ring":
        if raw.get("state") not in _workflows.STEP_STATES:
            raise _BadOp("state is a step state (" + ", ".join(_workflows.STEP_STATES) + ")")
        op["state"] = raw["state"]
        op["progress"] = _show_progress(raw.get("progress"), clamped)
    elif name == "beam":
        for end in ("from", "to"):
            if not isinstance(raw.get(end), str) or not raw[end]:
                raise _BadOp(f"{end} is a uid or place:<name>")
            op[end] = resolve(raw[end])
            if op[end] is None:
                raise _BadOp(f"{end} {raw[end][:60]!r} is not on the map")
    elif name == "route":
        stops = raw.get("stops")
        if not isinstance(stops, list) or len(stops) < 2 or not all(isinstance(x, str) and x for x in stops):
            raise _BadOp("stops is a list of 2 to 8 uids or places")
        if len(stops) > 8:
            stops = stops[:8]
            if "stops" not in clamped:
                clamped.append("stops")
        op["stops"] = []
        for x in stops:
            got = resolve(x)
            if got is None:
                raise _BadOp(f"stop {x[:60]!r} is not on the map")
            op["stops"].append(got)
    elif name == "radio":
        op["from"] = _show_plain(raw.get("from"), "from", 24, clamped) or "an agent"
        op["text"] = _show_plain(raw.get("text"), "text", 80, clamped, required=True)
    elif name == "fx":
        if raw.get("preset") not in FX_PRESETS:
            raise _BadOp(f"preset is {', '.join(FX_PRESETS)}")
        op["preset"] = raw["preset"]
        colour = raw.get("color")
        if colour is None:
            colour = STATE_COLOURS["done"] if raw["preset"] != "burst" else STATE_COLOURS["needs_you"]
        elif isinstance(colour, str) and colour in STATE_COLOURS:
            colour = STATE_COLOURS[colour]
        elif not (isinstance(colour, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", colour)):
            raise _BadOp("color is #RRGGBB or a step state")
        op["color"] = colour.upper()
        op["count"] = _show_number(raw.get("count"), "count", 1, 64, 24, clamped, whole=True)
        op["life"] = _show_number(raw.get("life"), "life", 0.2, 3.0, 1.5, clamped)
        text = _show_plain(raw.get("text"), "text", 24, clamped, required=raw["preset"] == "text")
        if text:
            op["text"] = text
    elif name == "card":
        op["title"] = _show_plain(raw.get("title"), "title", 40, clamped, required=True)
        op["rows"] = _show_rows(raw.get("rows"), clamped)
        text = _show_plain(raw.get("text"), "text", 200, clamped)
        if text:
            op["text"] = text
        bar = _show_bar(raw.get("bar"), clamped)
        if bar is not None:
            op["bar"] = bar
    elif name == "chart":
        op["label"] = _show_plain(raw.get("label"), "label", 40, clamped, required=True)
        op["values"] = _show_values(raw.get("values"), clamped)
        unit = _show_plain(raw.get("unit"), "unit", 8, clamped)
        if unit:
            op["unit"] = unit
        kind = raw.get("kind", "spark")
        if kind not in CHART_KINDS:
            raise _BadOp("kind is spark or bars")
        op["kind"] = kind
    elif name == "page":
        op["title"] = _show_plain(raw.get("title"), "title", PAGE_TITLE, clamped, required=True)
        html = raw.get("html")
        if not isinstance(html, str) or not html.strip():
            raise _BadOp("html is the whole page, as text")
        if unencodable_text(html) or PAGE_CONTROL_RE.search(html):
            raise BridgeError(400, "page html must be text with no control characters but newline, return and tab")
        size = len(html.encode("utf-8"))
        if size > PAGE_BYTES:      # a page cut short is a broken page: it is refused whole, never clamped
            raise _BadOp(f"html is {size} bytes; a page is at most {PAGE_BYTES}")
        op["bytes"] = size
        op["_html"] = html          # ShowStore.put keeps it apart from the public record: no list of shows carries it
    return op


class ShowStore:
    """The agents' shows, in memory only: at most SHOW_LIVE live (a new one retires the oldest), each for its ttl.
    A show never starts, stops or changes work; the page draws it with its author's name."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.items: dict[str, dict] = {}
        self.lock = threading.Lock()
        # a revision for every show put: it only grows (it starts from the clock, so it grows across a restart too),
        # and a show replaced under the same id gets a new one: the page reloads an open page on it, never on the clock
        self.rev = time.time_ns() // 1_000_000

    def _prune(self, now: float) -> None:
        self.items = {k: v for k, v in self.items.items() if v["_until"] > now}

    def put(self, show: dict) -> tuple[dict, str | None]:
        """Store a cleaned show; returns (its public record, the id it retired to make room or None). A page's
        source is split off the op and kept under a private key: no public record carries it."""
        now = self.clock()
        html = None
        ops = []
        for op in show["ops"]:
            if op["op"] == "page":
                html = op["_html"]
                op = {k: v for k, v in op.items() if k != "_html"}
            ops.append(op)
        with self.lock:
            self._prune(now)
            sid = show.get("id") or ("s-" + secrets.token_hex(4))
            retired = None
            if html is not None:        # at most PAGE_LIVE pages, within the SHOW_LIVE shows: the oldest page goes
                pages = [k for k, v in self.items.items() if v["_html"] is not None and k != sid]
                if len(pages) >= PAGE_LIVE:
                    retired = min(pages, key=lambda k: self.items[k]["_at"])
                    self.items.pop(retired)
            if retired is None and sid not in self.items and len(self.items) >= SHOW_LIVE:
                retired = min(self.items, key=lambda k: self.items[k]["_at"])
                self.items.pop(retired)
            self.rev += 1
            rec = {"id": sid, "by": show["by"], "at": show.get("at"), "ops": ops, "ttl": show["ttl"], "rev": self.rev,
                   "t": utc_stamp(now), "expires": utc_stamp(now + show["ttl"]),
                   "_at": now, "_until": now + show["ttl"], "_html": html}
            self.items[sid] = rec
            return self._public(rec, now), retired

    def page(self, sid: object) -> dict | None:
        """A live page, {id, title, by, rev, html}; None if no live show has that id or it holds no page."""
        now = self.clock()
        with self.lock:
            self._prune(now)
            rec = self.items.get(sid) if isinstance(sid, str) else None
            if rec is None or rec["_html"] is None:
                return None
            title = next(op["title"] for op in rec["ops"] if op["op"] == "page")
            return {"id": rec["id"], "title": title, "by": rec["by"], "rev": rec["rev"], "html": rec["_html"]}

    def clear(self, sid: object) -> bool:
        with self.lock:
            return self.items.pop(sid, None) is not None if isinstance(sid, str) else False

    @staticmethod
    def _public(rec: dict, now: float) -> dict:
        out = {k: v for k, v in rec.items() if not k.startswith("_")}
        out["expires_in"] = max(0, int(round(rec["_until"] - now)))
        return out

    def live(self) -> list[dict]:
        now = self.clock()
        with self.lock:
            self._prune(now)
            return [self._public(r, now) for r in sorted(self.items.values(), key=lambda r: r["_at"])]


class AreasBusy(Exception):
    """areas.json is being changed by another map or command and stayed so past the wait."""


AREAS_LOCK_WAIT = 10.0                  # seconds a writer waits for another one; the work under the lock takes ms
_AREAS_THREADS = threading.Lock()       # the threads of this process (and the whole story without fcntl)


@contextlib.contextmanager
def _areas_lock(dirfd: int, wait: float):
    """One writer of the areas file at a time, across this process's threads and across processes (the bridge's
    HTTP threads and the CLI): an exclusive flock on the folder that holds the file, read, backup and replace all
    inside it. One deadline, `wait` seconds from the call, covers both locks (the thread lock first, the flock with
    what is left); past it, AreasBusy. The lock dies with its holder."""
    deadline = time.monotonic() + wait
    if not _AREAS_THREADS.acquire(timeout=max(0.0, wait)):
        raise AreasBusy("areas.json is being changed by another thread of this map; try again")
    try:
        if fcntl is not None:
            delay = 0.005
            while True:
                try:
                    fcntl.flock(dirfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise AreasBusy("areas.json is being changed by another map or command; try again") from None
                    time.sleep(min(delay, left))
                    delay = min(delay * 2, 0.1)
        try:
            yield
        finally:
            if fcntl is not None:
                with contextlib.suppress(OSError):
                    fcntl.flock(dirfd, fcntl.LOCK_UN)
    finally:
        _AREAS_THREADS.release()


def _fd_write_all(fd: int, raw: bytes) -> None:
    view = memoryview(raw)
    while view:
        view = view[os.write(fd, view):]


def _areas_shelve_prev(dirfd: int, prev: str) -> None:
    """Move the areas.json.prev there is now to the first free areas.json.prev.N. Nothing is overwritten or deleted:
    the new name is made first as a hard link, which never replaces a name that is taken, and only then is the old
    name dropped (where links are not possible: a rename, after a check that the name is free; the lock is held)."""
    for n in range(1, 10_000):
        target = f"{prev}.{n}"
        try:
            os.link(prev, target, src_dir_fd=dirfd, dst_dir_fd=dirfd, follow_symlinks=False)
        except FileExistsError:
            continue
        except FileNotFoundError:
            return                                       # no .prev yet: nothing to move
        except OSError:                                  # no hard links here (or .prev is not a plain file)
            try:
                os.stat(target, dir_fd=dirfd, follow_symlinks=False)
            except FileNotFoundError:
                os.rename(prev, target, src_dir_fd=dirfd, dst_dir_fd=dirfd)
                return
        else:
            os.unlink(prev, dir_fd=dirfd)
            return
    raise OSError(errno.EEXIST, f"{prev}.N: ten thousand backups already")


def _areas_backup(dirfd: int, base: str, old: bytes, mode: int) -> str:
    """Keep the bytes about to be replaced as areas.json.prev (.prev is what the last change replaced).
    The .prev that was there is moved to the first free areas.json.prev.N first, so no backup is ever written over
    or deleted. Created O_EXCL and never through a link. Returns the name."""
    name = base + ".prev"
    _areas_shelve_prev(dirfd, name)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=dirfd)
    try:
        _fd_write_all(fd, old)
        os.fchmod(fd, mode)
        os.fsync(fd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(name, dir_fd=dirfd)                # the half-written copy this call made, nobody else's
        raise
    finally:
        os.close(fd)
    return name


def area_set_file(path: str, name: str, kind: str, glyph: str | None, wait: float = AREAS_LOCK_WAIT) -> dict:
    """The owner's ruling on what a planet holds, written into areas.json (the file the map reads its kinds from):
    that area's kind and emblem, guessed false; everything else in the file kept as it was. Atomic (temp + rename,
    the file's own mode, 600 when new). The bytes it replaces are kept beside it as areas.json.prev; the .prev
    it finds there is moved to the first free areas.json.prev.1, .2 ..., nothing deleted or written over. One
    writer at a time (the bridge and the CLI both come through here): read, backup and replace happen under one
    lock. A file or a folder that is a symbolic link is refused, not followed (links higher up the path are the
    owner's own layout and are walked); every step after the folder is opened goes through that open folder, so a
    swap cannot redirect it. Returns
    {kind, glyph, path}. Raises ValueError with a sentence, AreasBusy when the lock stays taken."""
    kind = AREA_KIND_ALIASES.get(kind, kind) if isinstance(kind, str) else kind
    if kind not in AREA_SET_KINDS:
        raise ValueError("kind is one of the nine purposes: " + ", ".join(AREA_SET_KINDS))
    if glyph in ("", None):
        glyph = None
    elif glyph not in AREA_SET_GLYPHS:
        raise ValueError("glyph is one of: " + ", ".join(AREA_SET_GLYPHS) + " (or none)")
    if not isinstance(name, str) or not name or "/" in name or len(name) > 200:
        raise ValueError("no such planet")
    folder, base = os.path.split(os.path.abspath(path))
    link = f"{base} or its folder is a link, so it was left alone (set areas_json to the real file)"
    if os.path.islink(folder):
        raise ValueError(link)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    try:
        dirfd = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise ValueError(link) from None
        raise
    try:
        with _areas_lock(dirfd, wait):
            return _area_set_locked(dirfd, base, path, name, kind, glyph, link)
    finally:
        os.close(dirfd)


def _area_set_locked(dirfd: int, base: str, path: str, name: str, kind: str, glyph: str | None, link: str) -> dict:
    old, mode = None, 0o600
    try:
        fd = os.open(base, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0), dir_fd=dirfd)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(link) from None
        raise
    else:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"{base} is not a plain file, so it was left alone")
            mode, old = stat.S_IMODE(info.st_mode), handle.read()
    data = {"version": 1, "areas": {}}
    if old is not None:
        try:
            data = json.loads(old.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ValueError(f"{base} is not valid JSON, so it was left alone: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("areas"), dict):
            raise ValueError(f"{base} is not a version 1 areas file, so it was left alone")
    row = data["areas"].get(name)
    row = dict(row) if isinstance(row, dict) else {}
    row["kind"] = kind
    if glyph:
        row["glyph"] = glyph
    else:
        row.pop("glyph", None)
    row["guessed"] = False
    data["areas"][name] = row
    if old is not None:
        _areas_backup(dirfd, base, old, mode)
    raw = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    temp = f"{base}.new.{os.getpid()}.{secrets.token_hex(3)}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dirfd)
    try:
        try:
            _fd_write_all(fd, raw)
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, base, src_dir_fd=dirfd, dst_dir_fd=dirfd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp, dir_fd=dirfd)
        raise
    with contextlib.suppress(OSError):
        os.fsync(dirfd)
    return {"kind": kind, "glyph": glyph, "path": path}


class BridgeApp:
    def __init__(self, *, seats_path: str, game_path: str, renderer_path: str,
                 live_path: str, log_path: str, tmux_runner=system_tmux,
                 world_loader=None, hop_runner=system_hop, synced_dir: str = SYNCED_DIR,
                 render_runner=system_render, file_roots=FILE_ROOTS, task_roots=None,
                 areas_path: str | None = None,
                 open_runner=None, door_runner=None, dispatch_runner=None,
                 dispatch_bin: str = DISPATCH_BIN, runners_path: str = RUNNERS_PATH,
                 runs_path: str = RUNS_PATH, rulings_path: str = RULINGS_PATH, runners=None,
                 verbs_dir: str = VERBS_DIR, commitments_path: str = COMMITMENTS_PATH,
                 checkins_path: str = CHECKINS_PATH, tasks_dir: str | None = None, task_runners=None,
                 mate_spec=None, mate_runner=None, tick_seconds: float | None = None,
                 agent_verify: bool | None = None, remote: dict | None = None,
                 remote_clock=time.time, workflows_dir: str | None = None,
                 ui_lock: bool | None = None, launch_key: str | None = None):
        self.registry = SeatRegistry(seats_path) if (seats_path and os.path.isfile(seats_path)) else SeatRegistry.empty(_CFG["tmux_session"])
        self.game_path = game_path
        self.renderer_path = renderer_path
        self.synced_dir = synced_dir
        self.render_runner = render_runner
        self.render_last: dict[str, float] = {}
        self.file_roots = tuple(os.path.realpath(r) for r in file_roots)
        if task_roots is None:      # the config's task roots, or the same set as a moved file_roots
            task_roots = TASK_ROOTS if file_roots is FILE_ROOTS else file_roots
        self.task_roots = tuple(os.path.realpath(r) for r in task_roots)
        self.areas_path = areas_path or _CFG["areas_json"] or os.path.join(HERE, "areas.json")
        # where the owner's purpose choices (POST /area/set) go: the areas file this map reads, else the one the
        # config would find next time (ROOT/.container-map/areas.json), never a file the map does not read
        root = _CFG.get("work_root") or ""
        self.areas_write_path = (areas_path or _CFG["areas_json"]
                                 or (os.path.join(root, ".container-map", "areas.json") if root else self.areas_path))
        self.open_runner = open_runner
        self.open_last = 0.0
        self.door_runner = door_runner
        self.door_last = 0.0
        self.dispatch_runner = dispatch_runner
        self.dispatch_bin = dispatch_bin
        self.runners_path = runners_path
        self.runs_path = runs_path
        self.rulings_path = rulings_path
        self.commitments_path = commitments_path
        self.checkins_path = checkins_path
        self.dispatch_last = 0.0
        self.runners = self._valid_runners(CONFIG_RUNNERS if runners is None else runners)
        self.verbs_dir = verbs_dir
        self.tasks = _tasks.store_for(_CFG, self.task_roots, tasks_dir or TASKS_DIR, task_runners)
        # phone access: the "remote" config block (None = what container.json says; tests pass
        # their own). Its sessions and audit files sit beside the tasks folder; nothing is read
        # or written until used.
        state_dir = os.path.dirname(os.path.abspath(tasks_dir or TASKS_DIR))
        self.remote = RemoteAccess(_CFG.get("remote") if remote is None else remote,
                                   state_dir, remote_clock)
        # a retry that could not go out is audited once per thread and message; the tick goes on
        self.tasks.on_tick_error = lambda tid, message: self.audit(
            {"event": "task-tick-error", "id": tid, "error": message})
        self._runner_threads: list = []
        # a verify line runs as the owner, so an agent may set one only when container.json allows it
        # (None = what the config says; tests pass it, so the owner's config never decides them)
        self.agent_verify = bool(_CFG.get("agent_verify") if agent_verify is None else agent_verify)
        self.runner_threads_lock = threading.Lock()
        # workflows: the config's folder (beside the threads); a bridge given its own tasks folder (a test, a copy)
        # keeps its workflows beside that one, never in the configured map's
        if workflows_dir is None:
            workflows_dir = (os.path.join(os.path.dirname(os.path.abspath(tasks_dir)), "workflows") if tasks_dir
                             else WORKFLOWS_DIR or _workflows.default_dir(_CFG))
        self.workflows = _workflows.store_for(dict(_CFG, workflows_dir=workflows_dir, agent_verify=self.agent_verify),
                                              task_store=self.tasks, roots=self.task_roots)
        self.workflows.same = lambda got, want: _same(got, want)     # one constant-time comparison for every token
        self.shows = ShowStore()
        self.show_rate = SlidingLimit(SHOW_RATE[0], SHOW_RATE[1], time.monotonic)
        self.points: list[float] = []          # when each live pointer (control "point") ends
        self.points_lock = threading.Lock()
        self._nudge_seen = None                 # the stat of <workflows_dir>/.nudge the ticker last acted on
        self._wf_errors: set = set()            # workflow tick errors already reported once
        # the oracle loop's own clock (start_ticker): retries must not wait for an open tab; 0 = off
        raw_tick = _CFG.get("tick_seconds", 10) if tick_seconds is None else tick_seconds
        valid_tick = isinstance(raw_tick, (int, float)) and not isinstance(raw_tick, bool)
        self.tick_seconds = float(raw_tick) if valid_tick and raw_tick > 0 else 0.0
        self._ticker: threading.Thread | None = None
        self._tick_stop = threading.Event()
        # the wingmate: one question at a time, answered on a thread so the bridge keeps serving the map
        self.mate_spec = _mate.spec_for(_CFG) if mate_spec is None else (mate_spec or None)
        self.mate_runner = mate_runner
        self.mate_jobs: dict[str, dict] = {}
        self.mate_lock = threading.Lock()
        self.mate_times: list[float] = []
        # the meta thread (meta.py): one run at a time, a few an hour; its runs and its chat sit
        # beside the tasks folder
        self.meta_dir = os.path.join(state_dir, "meta")
        self.meta_jobs: dict[str, dict] = {}
        self.meta_lock = threading.Lock()
        self.meta_times: list[float] = []
        self.meta_gather = None                 # None = meta.gather_worktrees (a test's own)
        self.meta_chat: list[dict] = self._meta_chat_load()
        self.ui_poll = {"after": None, "at": 0.0, "count": 0}   # is any UI actually consuming the control queue?
        self.live_path = live_path
        self.live_mirrors: tuple = ()      # more files for the same credentials (main() fills it)
        self.log_path = log_path
        self.tmux_runner = tmux_runner
        self.hop_runner = hop_runner
        self.world_loader = world_loader
        self.agent_token = secrets.token_urlsafe(32)
        self.ui_token = secrets.token_urlsafe(32)
        # the lock (opt-in, for a computer other people log into): GET / hands out the UI session only to whoever
        # brings this start's launch key. The key is random per start, kept in memory and in the 0600 live file
        # (where the agent token already is), and never printed by the bridge or written to its log.
        self.ui_lock = (bool(_CFG.get("ui_lock")) or os.environ.get("BRIDGE_UI_LOCK") == "1"
                        if ui_lock is None else bool(ui_lock))
        self.launch_key = launch_key if launch_key and len(launch_key) >= 24 else secrets.token_urlsafe(32)
        self.boot_id = secrets.token_hex(16)
        self.port = 0
        self.queue: list[dict] = []
        self.requests: dict[str, tuple[str, dict]] = {}
        self.sequence = 0
        self.last_control = 0.0
        self.last_send: dict[str, float] = {}
        self.remote_cache: dict[str, tuple[float, dict]] = {}
        self.remote_targets: dict[str, set[str]] = {}
        self.render_busy: set[str] = set()      # machines being re-rendered now (remote_lock)
        self._world_cache: tuple[float, dict] | None = None    # (when its facts were read, world)
        self._world_flight: _Flight | None = None
        self._page: dict | None = None      # game.html as last read: key, sha, html, world, at
        self._page_lock = threading.Lock()  # one reader of game.html at a time; a leaf
        self._snap_lock = threading.Lock()  # _snap_rev; a leaf
        self._snap_rev = 0.0                # the last /tasks snapshot's revision (snapshot_rev)
        self._served: dict = {}             # bytes built from one world: "page" parts, "state"
        self._gz_lock = threading.Lock()    # a leaf
        self._gz_last: tuple | None = None  # (bytes, their gzip): /state is polled, built once per world
        self._vendor_lock = threading.Lock()  # a leaf
        self._vendor: dict = {}             # /vendor/<name> -> (bytes, their gzip): the 3D view's scripts, packed once
        # The server runs one thread per request. One lock per concern; none is held across a
        # world render. Nesting, outermost first: send_lock -> remote_lock -> remote_targets_lock;
        # audit_lock, _world_lock and _page_lock are leaves; mate_lock, meta_lock, poll_lock and the
        # file locks never nest.
        self.lock = threading.RLock()               # control queue; open/door/dispatch stamps
        self.send_lock = threading.RLock()          # one terminal/herdr send at a time; last_send, key_times
        self.key_times: dict[str, list[float]] = {}  # POST /keys per seat, inside the last TERM_KEYS_RATE window
        self.term_wake: dict[str, threading.Event] = {}   # pane id -> set by POST /keys so its streams look now
        self.remote_lock = threading.RLock()        # remote_cache, render_last, render_busy, hops
        self.remote_targets_lock = threading.Lock()  # remote_targets (every pane read and send)
        self.poll_lock = threading.Lock()           # ui_poll
        self.audit_lock = threading.Lock()          # the audit log: one row per write
        self.runs_lock = threading.Lock()           # the runs jsonl
        self.rulings_lock = threading.Lock()        # the rulings file (read, change, replace)
        self.checkins_lock = threading.Lock()       # the check-ins jsonl
        self._world_lock = threading.Lock()  # _world_cache, _world_flight; not held while rendering

    def _tmux(self, args: list[str]):
        return self.tmux_runner(args, timeout=8)

    @staticmethod
    def _fields(output: str, count: int) -> list[list[str]]:
        # tmux ≥ 3.4 prints control bytes in -F output as octal escapes (\037); older builds print
        # the byte. Accept both, or every window vanishes and no seat binds (the "ambiguous or
        # missing window name" on every send, 2026-09-05).
        rows = []
        for line in output.replace("\\037", "\x1f").splitlines():
            parts = line.split("\x1f", count - 1)
            if len(parts) == count:
                rows.append(parts)
        return rows

    def windows(self) -> list[dict]:
        result = self._tmux(["list-windows", "-t", self.registry.session, "-F", WINDOW_FORMAT])
        if result.returncode:
            raise BridgeError(503, "configured tmux session is unavailable")
        rows = []
        for wid, index, name, pane_count in self._fields(result.stdout, 4):
            rows.append({"window_id": wid, "window_index": index, "name": name,
                         "pane_count": int(pane_count) if pane_count.isdigit() else 0})
        return rows

    def _configured_bindings(self, windows: list[dict]) -> tuple[dict[str, dict], dict[str, dict]]:
        """Bind policies to windows: live pin first, then one unclaimed exact name."""
        live_by_id = {window["window_id"]: window for window in windows}
        by_seat: dict[str, dict] = {}
        by_window: dict[str, dict] = {}
        pending: list[dict] = []
        for spec in self.registry.seats:
            window_id = spec.get("window_id")
            if window_id and window_id in live_by_id:
                by_seat[spec["id"]] = live_by_id[window_id]
                by_window[window_id] = spec
            else:
                pending.append(spec)

        proposals: dict[str, list[dict]] = {}
        for spec in pending:
            matches = [window for window in windows if window["name"] == spec["expected_name"]]
            if len(matches) == 1 and matches[0]["window_id"] not in by_window:
                proposals.setdefault(matches[0]["window_id"], []).append(spec)
        for window_id, specs in proposals.items():
            if len(specs) == 1:
                by_seat[specs[0]["id"]] = live_by_id[window_id]
                by_window[window_id] = specs[0]
        # door seats: a window door.sh opened in a directory is a worker seat for as long as it lives
        talk = self._door_talk()
        names = [w["name"] for w in windows]
        for window in windows:
            name = window["name"]
            if (name.startswith(DOOR_PREFIX) and SEAT_ID_RE.fullmatch(name) and names.count(name) == 1
                    and window["window_id"] not in by_window and name not in by_seat):
                spec = {"id": name, "uid": "seat:" + name, "expected_name": name, "role": "worker",
                        "talk_commands": talk, "window_id": None, "quick_key": None, "door": True}
                by_seat[name] = window
                by_window[window["window_id"]] = spec
        return by_seat, by_window

    def _door_talk(self) -> list[str]:
        try:
            with open(self.areas_path, encoding="utf-8") as handle:
                v = json.load(handle).get("door_talk_commands")
            if isinstance(v, list) and v and all(isinstance(x, str) and x for x in v):
                return list(v)
        except (OSError, ValueError, AttributeError):
            pass
        return list(DOOR_TALK_DEFAULT)

    def _pane(self, window: dict) -> dict:
        result = self._tmux(["list-panes", "-t", window["window_id"], "-F", PANE_FORMAT])
        if result.returncode:
            raise BridgeError(503, "tmux window is unavailable")
        rows = self._fields(result.stdout, 5)
        if len(rows) != 1 or window["pane_count"] != 1:
            raise BridgeError(409, "target window must contain exactly one pane")
        pane_id, pid, command, dead, in_mode = rows[0]
        return dict(window, pane_id=pane_id, pane_pid=pid, command=command,
                    dead=dead == "1", in_mode=in_mode == "1")

    def resolve(self, reference: str, *, configured_only: bool = False) -> dict:
        if not isinstance(reference, str) or not reference:
            raise BridgeError(400, "seat is required")
        windows = self.windows()
        by_seat, by_window = self._configured_bindings(windows)
        configured = self.registry.configured(reference)
        if not configured and isinstance(reference, str) and reference.startswith(DOOR_PREFIX) and reference in by_seat:
            configured = by_window[by_seat[reference]["window_id"]]      # a live door seat
        if configured:
            bound = by_seat.get(configured["id"])
            if not bound:
                raise BridgeError(409, "ambiguous or missing window name")
            matches = [bound]
        elif configured_only:
            raise BridgeError(403, "seat is not a configured control target")
        elif re.fullmatch(r"@[0-9]+", reference):
            matches = [w for w in windows if w["window_id"] == reference]
        else:
            matches = [w for w in windows if w["name"] == reference]
        if not matches:
            raise BridgeError(404, "seat is not live")
        if len(matches) != 1:
            raise BridgeError(409, "seat name is not unique; use its window id")
        target = self._pane(matches[0])
        spec = by_window.get(target["window_id"])
        target["spec"] = spec
        target["uid"] = spec["uid"] if spec else "seat:" + target["window_id"]
        return target

    @staticmethod
    def identity(target: dict) -> tuple[str, str, str, str, str]:
        return (target["window_id"], target["name"], target["pane_id"],
                target["pane_pid"], target["command"])

    @staticmethod
    def sendability(target: dict) -> tuple[bool, str]:
        spec = target.get("spec")
        if not spec:
            return False, "seat is read-only because it is not configured"
        if target["name"] != spec["expected_name"]:
            return False, "configured window name does not match"
        if target["dead"]:
            return False, "target pane is dead"
        if target["in_mode"]:
            return False, "target pane is in tmux copy mode"
        if not spec["talk_commands"]:
            return False, "configured runtime seat is never talkable"
        command = os.path.basename(target["command"])
        if not any(command == allowed or command.startswith(allowed + ".")
                   or command.startswith(allowed + "-")
                   for allowed in spec["talk_commands"]):
            return False, "target is not running its configured TUI"
        return True, ""

    def capture(self, target: dict, *, lines: int | None = None) -> tuple[str, bool]:
        args = ["capture-pane", "-p"]
        if lines is not None:
            args += ["-S", f"-{lines}"]
        args += ["-t", target["pane_id"]]
        result = self._tmux(args)
        if result.returncode:
            raise BridgeError(503, "tmux pane capture failed")
        text = clean_terminal(result.stdout)
        if lines is not None:
            text = "\n".join(text.splitlines()[-lines:])
        encoded = text.encode("utf-8")
        truncated = len(encoded) > MAX_PANE_BYTES
        if truncated:
            text = encoded[-MAX_PANE_BYTES:].decode("utf-8", "replace")
        return text, truncated

    def pane_snapshot(self, reference: str, lines: int) -> dict:
        target = self.resolve(reference)
        visible, _ = self.capture(target)
        state, region = composer_probe(target["command"], visible)
        revision_data = [*self.identity(target), state, region]
        revision = hashlib.sha256(json.dumps(revision_data).encode()).hexdigest()
        text, truncated = self.capture(target, lines=lines)
        sendable, reason = self.sendability(target)
        prompt = waiting_prompt(visible)
        return {"seat": reference, "uid": target["uid"], "window_id": target["window_id"],
                "window_index": target["window_index"], "name": target["name"],
                "pane_id": target["pane_id"], "command": target["command"],
                "configured": bool(target["spec"]), "sendable": sendable,
                "send_reason": reason or None, "terminal_state": state,
                "waiting": prompt is not None, "permit": prompt,
                "revision": revision, "text": text, "lines": len(text.splitlines()),
                "truncated": truncated}

    def term_read(self, pane_id: str, tail: int = TERM_TAIL) -> dict:
        """One look at a pane for the live terminal, in one tmux call: its numbers, then up to `tail` history rows
        and the screen (a capture from -tail returns min(tail, history) rows above the screen's)."""
        args = ["display-message", "-p", "-t", pane_id, TERM_FORMAT, ";",
                "capture-pane", "-p", "-e", "-t", pane_id, "-S", f"-{tail}"]
        result = self._tmux(args)
        if result.returncode:
            raise BridgeError(503, "tmux pane capture failed")
        head, _, body = (result.stdout or "").partition("\n")
        fields = head.replace("\\037", "\x1f").split("\x1f")
        try:
            hist, limit, cx, cy, width, height = (int(value) for value in fields[:6])
            alt, cursor, mode, dead = (value == "1" for value in fields[6:10])
        except ValueError as exc:
            raise BridgeError(502, "tmux pane numbers are unreadable") from exc
        if len(fields) != 10 or not (0 < width <= 1000 and 0 < height <= 500):
            raise BridgeError(502, "tmux pane numbers are unreadable")
        rows = [term_line(line) for line in body.split("\n")]
        if rows and rows[-1] == "":
            rows.pop()
        above = min(tail, hist)
        rows += [""] * max(0, above + height - len(rows))
        return {"hist": hist, "limit": limit, "cx": cx, "cy": cy, "w": width, "h": height, "alt": alt,
                "cursor": cursor, "mode": mode, "dead": dead,
                "above": rows[:above], "screen": rows[above:above + height]}

    @staticmethod
    def term_scrolled(before: dict, after: dict) -> int | None:
        """How many rows left the top of the screen into history between two looks; None when that cannot be told
        (the page then starts over). While history is under its limit that is how much it grew. At the limit the
        count does not move, so it is the smallest shift k that turns the old history tail into the new one with
        the old screen's top k rows at its end (k = 0 when the tail did not move: blank rows match nothing)."""
        if (before["w"], before["h"], before["alt"]) != (after["w"], after["h"], after["alt"]):
            return None
        grown = after["hist"] - before["hist"]
        if after["hist"] < after["limit"]:
            return grown if 0 <= grown <= len(after["above"]) else None
        old_tail, tail = before["above"], after["above"]
        if not old_tail and not tail:
            return 0                            # history-limit 0: rows leave for nowhere, there is no scrollback
        if len(old_tail) != len(tail) or not tail:
            return None
        if old_tail == tail:
            return 0
        for k in range(1, len(tail) + 1):
            pushed = tail[-k:]
            if tail[:-k] == old_tail[k:] and pushed[:len(before["screen"])] == before["screen"][:k]:
                return k
        return None

    def term_events(self, reference: str, *, tick: float = TERM_TICK, seconds: float = TERM_SECONDS,
                    history: int = TERM_HISTORY, clock=time.monotonic, sleep=None):
        """What GET /term streams, one dict per change: a "full" frame (history, then the screen) first and after
        anything that cannot be followed row by row; then "frame" (k rows scrolled off the top, the new screen,
        the cursor; "rows" are those k rows as history now holds them). Ends with "end" (why) when the pane goes
        away, changes or the stream has run `seconds`."""
        target = self.resolve(reference)
        pane, anchor = target["pane_id"], (target["window_id"], target["pane_id"], target["pane_pid"])
        deadline, check_at, before, said = clock() + seconds, clock() + TERM_RECHECK, None, clock()
        wake = self.term_wake.setdefault(pane, threading.Event())
        moved = clock()                         # the last change or keypress: until TERM_QUIET after it, look often

        def rest():
            nonlocal moved
            pause = tick if clock() - moved < TERM_QUIET else max(tick, TERM_IDLE_TICK)
            if sleep is not None:
                sleep(pause)
            elif wake.wait(pause):
                wake.clear()
                moved = clock()
                time.sleep(0.015)               # let the program draw what the key did before the next look

        while clock() < deadline:
            if clock() >= check_at:
                check_at = clock() + TERM_RECHECK
                try:
                    now = self.resolve(reference)
                except BridgeError as exc:
                    yield {"t": "end", "why": exc.message}
                    return
                if (now["window_id"], now["pane_id"], now["pane_pid"]) != anchor:
                    yield {"t": "end", "why": "the pane changed"}
                    return
            try:
                look = self.term_read(pane)
            except BridgeError as exc:
                try:                            # a closed window reads as "capture failed": say what happened
                    self.resolve(reference)
                    why = exc.message
                except BridgeError:
                    why = "the window closed"
                yield {"t": "end", "why": why}
                return
            if look["dead"]:
                yield {"t": "end", "why": "the pane is dead"}
                return
            frame = {"cx": look["cx"], "cy": look["cy"], "cursor": look["cursor"] and not look["mode"],
                     "mode": look["mode"], "screen": look["screen"]}
            scrolled = None if before is None else self.term_scrolled(before, look)
            if before is None or scrolled != 0 or look["screen"] != before["screen"]:
                moved = clock()
            if scrolled is None:
                # history and screen from ONE capture (two reads could both hold a row that scrolled between
                # them); that look is the next frame's baseline, its history cut to what a look reads
                if look["hist"] and history > TERM_TAIL:
                    try:
                        look = self.term_read(pane, tail=history)
                    except BridgeError as exc:
                        yield {"t": "end", "why": exc.message}
                        return
                frame = {"cx": look["cx"], "cy": look["cy"], "cursor": look["cursor"] and not look["mode"],
                         "mode": look["mode"], "screen": look["screen"]}
                yield {"t": "full", "w": look["w"], "h": look["h"], "history": look["above"][-history:], **frame}
                look = dict(look, above=look["above"][-TERM_TAIL:])
            elif scrolled or look["screen"] != before["screen"] or (look["cx"], look["cy"], look["cursor"],
                                                                     look["mode"]) != (before["cx"], before["cy"],
                                                                                       before["cursor"],
                                                                                       before["mode"]):
                yield {"t": "frame", "k": scrolled, "rows": look["above"][-scrolled:] if scrolled else [], **frame}
            elif clock() - said >= TERM_BEAT:
                yield {"t": "beat"}             # a page that went away is noticed on this write, not in 10 minutes
            else:
                before = look
                rest()
                continue
            said, before = clock(), look
            rest()
        yield {"t": "end", "why": "timeout"}

    def keys(self, body: object) -> dict:
        """POST /keys: what a person typed into the live terminal, sent to a configured seat as tmux keys. The same
        seats and the same checks as /say (a configured TUI, alive, not in copy mode), without its empty-composer
        rule: the person is looking at the live screen and typing into it. A paste goes in as one tmux paste, as
        bracketed paste when the program asked for it."""
        if not isinstance(body, dict) or set(body) not in ({"seat", "data"}, {"seat", "paste"}):
            raise BridgeError(400, "body must contain exactly seat and data (or seat and paste)")
        paste = "paste" in body
        value = body["paste" if paste else "data"]
        if not isinstance(value, str) or not value or unencodable_text(value):
            raise BridgeError(400, "keys must be a non-empty string")
        if len(value) > (TERM_PASTE_MAX if paste else TERM_KEYS_MAX):
            raise BridgeError(413, "too much at once")
        ops = [] if paste else term_keys(value)
        if not paste and not ops:
            return {"ok": True, "status": "nothing to send"}
        with self.send_lock:
            target = self.resolve(body["seat"], configured_only=True)
            sendable, reason = self.sendability(target)
            if not sendable:
                raise BridgeError(403 if not target["spec"]["talk_commands"] else 409, reason)
            now = time.monotonic()
            recent = [at for at in self.key_times.get(target["uid"], []) if now - at < TERM_KEYS_RATE[1]]
            if len(recent) >= TERM_KEYS_RATE[0]:
                raise BridgeError(429, "typing too fast for this seat; slow down")
            self.key_times[target["uid"]] = recent + [now]
            pane = target["pane_id"]
            if paste:
                buffer = "cm-paste-" + secrets.token_hex(4)
                args = ["set-buffer", "-b", buffer, "--", tmux_arg(value), ";",
                        "paste-buffer", "-p", "-d", "-b", buffer, "-t", pane]
            else:
                args = []
                for kind, item in ops:
                    args += [*([";"] if args else []), "send-keys", "-t", pane,
                             *(["-l", "--", *tmux_literal(item)] if kind == "text" else [item])]
            result = self._tmux(args)
            if result.returncode:
                raise BridgeError(502, "tmux could not type that")
            wake = self.term_wake.get(pane)
            if wake is not None:
                wake.set()
            entered = sum(1 for kind, item in ops if item == "Enter")
            if paste or entered:
                self.audit({"event": "keys", "seat_uid": target["uid"], "result": "typed",
                            "paste": paste, "enter": entered, "text_length": len(value)})
            return {"ok": True, "seat_uid": target["uid"], "status": "typed"}

    @staticmethod
    def _remote_string(value: object, field: str, *, required: bool = False) -> str:
        if value is None and not required:
            return ""
        if not isinstance(value, str) or (required and not value):
            raise BridgeError(502, f"remote {field} is invalid")
        if len(value) > MAX_REMOTE_FIELD_CHARS:
            raise BridgeError(502, f"remote {field} is too large")
        cleaned = clean_terminal(value).replace("\n", " ").strip()
        if required and not cleaned:
            raise BridgeError(502, f"remote {field} is invalid")
        return cleaned

    @staticmethod
    def _remote_identifier(value: object, field: str) -> str:
        if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
            raise BridgeError(502, f"remote {field} is invalid")
        return value

    def _remote_hop(self, machine: object, request: dict) -> tuple[dict, int]:
        self.registry.machine(machine)
        started = time.monotonic()
        try:
            result = self.hop_runner(machine, request, timeout=REMOTE_TIMEOUT, via=self.registry.machines[machine]["via"])
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(504, f"{machine} did not answer") from exc
        except OSError as exc:
            raise BridgeError(502, f"{machine} remote hop failed") from exc
        hop_ms = max(0, round((time.monotonic() - started) * 1000))
        if not isinstance(result, subprocess.CompletedProcess):
            raise BridgeError(502, f"{machine} remote hop returned an invalid result")
        if result.returncode == 124:
            raise BridgeError(504, f"{machine} did not answer")
        if result.returncode != 0:
            raise BridgeError(502, f"{machine} remote hop failed")
        stdout = result.stdout
        if not isinstance(stdout, str) or len(stdout.encode("utf-8", "replace")) > MAX_HOP_BYTES:
            raise BridgeError(502, f"{machine} remote hop returned invalid output")
        try:
            payload = json.loads(stdout)
        except (TypeError, ValueError, RecursionError) as exc:   # deep nesting from a hostile remote
            raise BridgeError(502, f"{machine} remote hop returned invalid JSON") from exc
        if (not isinstance(payload, dict) or payload.get("protocol") != 1
                or payload.get("op") != request.get("op")
                or not isinstance(payload.get("commands"), dict)
                or payload.get("error")):
            raise BridgeError(502, f"{machine} remote adapter response is invalid")
        return payload, hop_ms

    @staticmethod
    def _remote_command(machine: str, payload: dict, name: str) -> str:
        command = payload.get("commands", {}).get(name)
        if not isinstance(command, dict):
            raise BridgeError(502, f"{machine} herdr response omitted {name}")
        if command.get("timed_out") or command.get("returncode") == 124:
            raise BridgeError(504, f"{machine} did not answer")
        returncode = command.get("returncode")
        stdout = command.get("stdout")
        stderr = command.get("stderr", "")
        if (isinstance(returncode, bool) or not isinstance(returncode, int)
                or not isinstance(stdout, str) or not isinstance(stderr, str)
                or command.get("oversized")
                or len(stdout.encode("utf-8", "replace")) > MAX_HOP_BYTES
                or len(stderr.encode("utf-8", "replace")) > MAX_HOP_BYTES):
            raise BridgeError(502, f"{machine} herdr {name} response is invalid")
        if returncode != 0:
            raise BridgeError(502, f"{machine} herdr {name} failed")
        return stdout

    @staticmethod
    def _remote_json(machine: str, label: str, value: str) -> dict:
        try:
            payload = json.loads(value)
        except (ValueError, RecursionError) as exc:               # grok review (2): nested JSON
            raise BridgeError(502, f"{machine} herdr {label} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise BridgeError(502, f"{machine} herdr {label} returned invalid JSON")
        return payload

    def _remote_agents(self, machine: str, value: str) -> list[dict]:
        payload = self._remote_json(machine, "agent list", value)
        result = payload.get("result")
        agents = result.get("agents") if isinstance(result, dict) else None
        if not isinstance(agents, list) or len(agents) > MAX_REMOTE_PANES:
            raise BridgeError(502, f"{machine} agent list is invalid")
        normalized: list[dict] = []
        seen: set[str] = set()
        for raw in agents:
            if not isinstance(raw, dict):
                raise BridgeError(502, f"{machine} agent list is invalid")
            pane_id = self._remote_identifier(raw.get("pane_id"), "pane_id")
            agent = self._remote_string(raw.get("agent"), "agent", required=True)
            status = raw.get("agent_status")
            if status not in REMOTE_STATUSES:
                raise BridgeError(502, f"{machine} agent status is invalid")
            if pane_id in seen:
                raise BridgeError(502, f"{machine} agent list has duplicate pane ids")
            seen.add(pane_id)
            tab_id = raw.get("tab_id")
            if tab_id is not None:
                tab_id = self._remote_identifier(tab_id, "tab_id")
            normalized.append({
                "pane_id": pane_id,
                "agent": agent,
                "agent_status": status,
                "cwd": self._remote_string(raw.get("foreground_cwd") or raw.get("cwd"), "cwd"),
                "title": self._remote_string(raw.get("terminal_title_stripped")
                                             or raw.get("terminal_title"), "title"),
                "tab_id": tab_id,
            })
        return normalized

    def _remote_sendability(self, machine: str, pane: dict) -> tuple[bool, str]:
        agent = pane.get("agent")
        status = pane.get("agent_status")
        if not agent:
            return False, "pane has no detected agent"
        if status == "working":
            return False, "agent is working"
        if status not in ("idle", "blocked"):
            return False, f"agent status {status} is not talkable"
        spec = self.registry.remote_seat(machine, pane["pane_id"])
        if not spec or not spec["talk_commands"]:
            return False, "remote sends are disabled for this seat"
        if agent != spec["agent"]:
            return False, "configured remote agent does not match"
        if not any(agent == allowed or agent.startswith(allowed + "-")
                   or agent.startswith(allowed + ".") for allowed in spec["talk_commands"]):
            return False, "detected agent is not an allowed composer"
        return True, ""

    def _normalize_remote_state(self, machine: str, agent_text: str,
                                snapshot_text: str, hop_ms: int) -> dict:
        agents = self._remote_agents(machine, agent_text)
        agent_by_pane = {item["pane_id"]: item for item in agents}
        payload = self._remote_json(machine, "api snapshot", snapshot_text)
        result = payload.get("result")
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        if not isinstance(snapshot, dict):
            raise BridgeError(502, f"{machine} snapshot is invalid")
        raw_tabs, raw_panes = snapshot.get("tabs"), snapshot.get("panes")
        if (not isinstance(raw_tabs, list) or not isinstance(raw_panes, list)
                or len(raw_tabs) > MAX_REMOTE_TABS or len(raw_panes) > MAX_REMOTE_PANES):
            raise BridgeError(502, f"{machine} snapshot is invalid")

        tabs: list[dict] = []
        tabs_by_id: dict[str, dict] = {}
        for raw in raw_tabs:
            if not isinstance(raw, dict):
                raise BridgeError(502, f"{machine} snapshot tab is invalid")
            tab_id = self._remote_identifier(raw.get("tab_id"), "tab_id")
            if tab_id in tabs_by_id:
                raise BridgeError(502, f"{machine} snapshot has duplicate tab ids")
            tab = {"id": tab_id, "name": self._remote_string(raw.get("label"), "tab name"),
                   "panes": []}
            tabs.append(tab)
            tabs_by_id[tab_id] = tab

        seen_panes: set[str] = set()
        for raw in raw_panes:
            if not isinstance(raw, dict):
                raise BridgeError(502, f"{machine} snapshot pane is invalid")
            pane_id = self._remote_identifier(raw.get("pane_id"), "pane_id")
            tab_id = self._remote_identifier(raw.get("tab_id"), "tab_id")
            if pane_id in seen_panes:
                raise BridgeError(502, f"{machine} snapshot has duplicate pane ids")
            if tab_id not in tabs_by_id:
                raise BridgeError(502, f"{machine} pane references an unknown tab")
            seen_panes.add(pane_id)
            status = raw.get("agent_status", "unknown")
            if status not in REMOTE_STATUSES:
                raise BridgeError(502, f"{machine} pane status is invalid")
            detected = agent_by_pane.get(pane_id)
            if detected and detected.get("tab_id") and detected["tab_id"] != tab_id:
                raise BridgeError(502, f"{machine} agent and snapshot tab ids disagree")
            pane = {
                "pane_id": pane_id,
                "agent": detected["agent"] if detected else None,
                "agent_status": detected["agent_status"] if detected else status,
                "cwd": (detected["cwd"] if detected and detected["cwd"] else
                        self._remote_string(raw.get("foreground_cwd") or raw.get("cwd"), "cwd")),
                "title": (detected["title"] if detected and detected["title"] else
                          self._remote_string(raw.get("terminal_title_stripped")
                                             or raw.get("terminal_title") or raw.get("label"), "title")
                          or pane_id),
            }
            pane["talkable"], reason = self._remote_sendability(machine, pane)
            pane["talk_reason"] = reason or None
            tabs_by_id[tab_id]["panes"].append(pane)

        if set(agent_by_pane) - seen_panes:
            raise BridgeError(502, f"{machine} agent list references an unknown pane")
        for tab in tabs:
            if not tab["name"]:
                tab["name"] = tab["panes"][0]["title"] if tab["panes"] else tab["id"]
        return {"machine": machine, "reachable": True, "hop_ms": hop_ms, "tabs": tabs,
                "note": f"no agents detected on {machine}" if not agents else None}

    def remote_state(self, machine: object) -> dict:
        self.registry.machine(machine)
        now = time.monotonic()
        with self.remote_lock:
            cached = self.remote_cache.get(machine)
            if cached and now - cached[0] < REMOTE_CACHE_TTL:
                state = json.loads(json.dumps(cached[1]))
            else:
                payload, hop_ms = self._remote_hop(machine, {"op": "state"})
                agent_text = self._remote_command(machine, payload, "agent_list")
                snapshot_text = self._remote_command(machine, payload, "snapshot")
                state = self._normalize_remote_state(machine, agent_text, snapshot_text, hop_ms)
                self.remote_cache[machine] = (time.monotonic(), json.loads(json.dumps(state)))
            with self.remote_targets_lock:
                self.remote_targets[machine] = {
                    pane["pane_id"] for tab in state["tabs"] for pane in tab["panes"]
                }
            return state

    def remote_world(self, machine: object, meta_only: bool = False) -> dict:
        """The machine's own synced render: fleet-game/out/<machine>/world.json, carried here by
        the sync bus. It is that machine's claim about itself, dated by the file's mtime. Past
        SYNCED_STALE_S the age is reported and the world is NOT served (fail closed, say why).
        Reads one whitelisted local file; never hops, never executes anything."""
        self.registry.machine(machine)
        path = os.path.join(self.synced_dir, machine, "world.json")
        try:
            st = os.stat(path)
        except OSError as exc:
            raise BridgeError(404, f"no synced world for {machine}") from exc
        age = max(0.0, time.time() - st.st_mtime)
        out = {"machine": machine, "source": "synced", "age_s": int(age),
               "generated_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
               "stale": age > SYNCED_STALE_S, "stale_after_s": SYNCED_STALE_S}
        if out["stale"]:
            out["note"] = (f"synced world for {machine} is {int(age // 3600)}h old: past the "
                           f"{SYNCED_STALE_S // 3600}h limit, not served")
            return out
        if st.st_size > MAX_SYNCED_BYTES:
            raise BridgeError(502, f"{machine} synced world is too large")
        try:
            with open(path, encoding="utf-8") as handle:
                world = json.load(handle)
        except (OSError, ValueError, RecursionError) as exc:
            raise BridgeError(502, f"{machine} synced world is not valid JSON") from exc
        if not isinstance(world, dict) or not isinstance(world.get("chars"), list):
            raise BridgeError(502, f"{machine} synced world has no characters")
        out["generated"] = str(world.get("generated", ""))[:64]
        out["counts"] = {k: sum(1 for c in world["chars"] if isinstance(c, dict) and c.get("kind") == k)
                         for k in ("area", "dir", "seat", "cron", "proc", "runner", "channel", "machine")}
        if not meta_only:
            out["world"] = world
        return out

    def _checked_file(self, path: object, roots: tuple | None = None) -> str:
        """An absolute path, resolved, under one of the container's roots (the preview's, unless
        the caller names the narrower task roots), with no credential-like component anywhere in
        it. Anything else is refused before the disk is touched."""
        if not isinstance(path, str) or not path.startswith("/") or len(path) > 4096 or "\x00" in path:
            raise BridgeError(400, "path must be absolute")
        real = os.path.realpath(path)
        allowed = self.file_roots if roots is None else roots
        if not any(real == root or real.startswith(root + "/") for root in allowed):
            raise BridgeError(403, "path is outside the container roots")
        # the rules run on the path as asked, as resolved, and on every spelling between: a symlink named for
        # nothing (plain -> .config/gh) must not reach a credentials folder, nor a chain (docs -> .config/gh -> plain)
        # lead a request past a name it passes through
        if self._denied_path(real) or any(self._denied_path(p) for p in self._link_spellings(path)):
            raise BridgeError(403, "that path is never served")
        return real

    @staticmethod
    def _link_spellings(path: str) -> list:
        """The path as asked, then once more each time a symlink in it is replaced by its target, resolving as the
        kernel does: component by component from the left, a link before any '..' that follows it (64 at most).
        Each spelling keeps the rest of the path as written, so a name a chain passes through is in one of them."""
        seen, done, rest, hops = [path], "/", [p for p in path.split("/") if p], 0
        while rest:
            part = rest.pop(0)
            if part == ".":
                continue
            if part == "..":
                done = os.path.dirname(done.rstrip("/")) or "/"
                continue
            step = os.path.join(done, part)
            if not os.path.islink(step):
                done = step
                continue
            hops += 1
            try:
                target = os.readlink(step)
            except OSError:
                break
            if hops > 64:
                break
            rest = [p for p in target.split("/") if p] + rest
            if target.startswith("/"):
                done = "/"
            seen.append(os.path.join(done, *rest) if rest else done)
        seen.append(done)
        return seen

    @staticmethod
    def _denied_path(path: str) -> bool:
        parts = [part.lower() for part in path.split("/")]
        base = os.path.basename(path).lower()
        # ROOT/.container-map/run/ holds the bridge's credentials (bridge-<port>.json); chat-scan/ the owner's read
        # chats (quotes from every conversation), t3-remote/ another machine's thread list: never served to anyone
        in_run = any((a, b) in _workflows.PRIVATE_DIRS for a, b in zip(parts, parts[1:]))
        denied = (in_run or any(part in FILE_DENY_PARTS for part in parts)
                  or any(pair in _workflows.FILE_DENY_PAIRS for pair in zip(parts, parts[1:])))
        # the paired phones' device hashes and the audit of refused phone requests sit beside the
        # tasks folder (remote-sessions.json, remote-audit.log); a live file named by --live-file
        # may sit anywhere under a root (bridge-<port>.json)
        return bool(denied or base.endswith(FILE_DENY_SUFFIX) or LIVE_FILE_NAME_RE.fullmatch(base)
                    or base.startswith(FILE_DENY_PREFIX) or base.endswith(FILE_DENY_END)
                    or _workflows.FILE_DENY_NAME_RE.search(base))

    def read_file(self, path: object) -> dict:
        """First 64 KB of a text file as text; binary is reported, not decoded. Read-only."""
        real = self._checked_file(path)
        try:
            st = os.stat(real)
        except OSError as exc:
            raise BridgeError(404, "no such file") from exc
        if not os.path.isfile(real):
            raise BridgeError(400, "not a regular file")
        with open(real, "rb") as handle:
            head = handle.read(MAX_FILE_BYTES + 1)
        binary = b"\x00" in head[:4096]
        truncated = len(head) > MAX_FILE_BYTES
        text = "" if binary else head[:MAX_FILE_BYTES].decode("utf-8", "replace")
        return {"path": real, "size": st.st_size, "binary": binary, "truncated": truncated,
                "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
                "text": text}

    def open_door(self, body: object) -> dict:
        """The open-door verb: door.sh <target> — a tmux window IN that directory running the assigned
        worker (or a plain shell). target = an area name the world knows, or an absolute directory
        under the container roots. Rate-limited, audited, argv only, never a shell string."""
        if not isinstance(body, dict) or not {"target"} <= set(body) <= {"target", "worker"}:
            raise BridgeError(400, "body must be {target[, worker]}")
        target, worker = body["target"], body.get("worker")
        if (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.~/-]{1,200}", target)
                or ".." in target or (not target.startswith("/") and target.startswith("."))):
            raise BridgeError(400, "target must be an area name or an absolute directory")
        if worker is not None and (not isinstance(worker, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", worker)):
            raise BridgeError(400, "worker must be a runner name")
        if target.startswith("/"):
            real = self._checked_file(target, self.task_roots)
            if not os.path.isdir(real):
                raise BridgeError(404, "no such directory")
            target = real
        else:
            names = {c.get("name") for c in self.world().get("chars", []) if isinstance(c, dict) and c.get("kind") == "area"}
            if target not in names:
                raise BridgeError(404, f"{target} is not an area of this world")
        with self.lock:
            now = time.monotonic()
            if now - self.door_last < DOOR_MIN_GAP:
                raise BridgeError(429, "one door every few seconds")
            self.door_last = now
        argv = ["bash", os.path.join(HERE, "door.sh"), target] + (["--as", worker] if worker else [])
        door_env = dict(os.environ, CONTAINER_TMUX_SESSION=self.registry.session, CONTAINER_WORK_ROOT=_CFG["work_root"],
                        CONTAINER_AREAS_JSON=self.areas_path)
        self.audit({"event": "open_door", "target": target, "worker": worker or ""})
        try:
            if self.door_runner:
                result = self.door_runner(argv)
            else:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=DOOR_TIMEOUT,
                                        stdin=subprocess.DEVNULL, env=door_env)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError(502, f"door failed: {exc}") from exc
        out = str(getattr(result, "stdout", "") or "").strip()
        err = str(getattr(result, "stderr", "") or "").strip()
        if int(getattr(result, "returncode", 1)) != 0:
            raise BridgeError(502, (err or out or "door failed")[:200])
        m = re.search(r"window '([^']+)' in (\S+)", out)
        return {"ok": True, "window": m.group(1) if m else "", "path": m.group(2) if m else target, "message": out[:300]}

    # ---- the board acts: hand an item to a runner / retire a parked decision ------------------
    @staticmethod
    def _valid_runners(raw) -> dict:
        """config runners: name → {cmd: [str…], cwd: item|tool, report_dir: str, max_minutes: int}."""
        out = {}
        if not isinstance(raw, dict):
            return out
        for name, spec in raw.items():
            if not (isinstance(name, str) and re.fullmatch(r"[a-z0-9-]{1,32}", name) and isinstance(spec, dict)):
                continue
            cmd = spec.get("cmd")
            if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) and a for a in cmd)):
                continue
            cwd = spec.get("cwd", "item")
            minutes = spec.get("max_minutes", RUNNER_DEFAULT_MINUTES)
            if cwd not in ("item", "tool") or not isinstance(minutes, (int, float)) or minutes <= 0:
                continue
            report_dir = spec.get("report_dir") if isinstance(spec.get("report_dir"), str) else ""
            out[name] = {"cmd": list(cmd), "cwd": cwd, "report_dir": os.path.expanduser(report_dir),
                         "max_minutes": float(minutes), "desc": str(spec.get("desc", ""))[:200]}
        return out

    def dispatchable_runners(self) -> dict:
        """config runners first; then runners.json minus dead seats (red-circle desc) and the
        retiring sub seat, only when a registry is configured."""
        out = {k: dict(v, config=True) for k, v in self.runners.items()}
        if self.runners_path:
            try:
                with open(self.runners_path, encoding="utf-8") as handle:
                    reg = json.load(handle)
            except (OSError, ValueError, TypeError):
                reg = None
            if isinstance(reg, dict):
                for k, v in reg.items():
                    if (isinstance(v, dict) and isinstance(k, str) and not k.startswith("_") and k != "claude"
                            and not str(v.get("desc", "")).startswith(DEAD_MARK) and k not in out):
                        out[k] = v
        return out

    def _append_run_row(self, row: dict) -> None:
        with self.runs_lock:
            os.makedirs(os.path.dirname(self.runs_path) or ".", exist_ok=True)
            with open(self.runs_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    def _run_config_runner(self, name: str, spec: dict, key: str, kind: str, ident: str, text: str, item_cwd: str | None) -> dict:
        ts = time.strftime("%Y%m%d-%H%M%S")
        rid = f"r-{ts}-{name}"
        report_dir = spec["report_dir"] or os.path.join(os.path.dirname(self.runs_path) or HERE, "runs")
        os.makedirs(report_dir, exist_ok=True)
        log_path = os.path.join(report_dir, rid + ".log")
        cwd = (item_cwd or HERE) if spec["cwd"] == "item" else HERE
        inline = any("{text}" in a for a in spec["cmd"])
        argv = [a.replace("{text}", text) for a in spec["cmd"]]
        try:
            log = open(log_path, "w", encoding="utf-8")
            log.write(f"# {rid} · runner {name} · item {key} · cwd {cwd} · started {utc_stamp()}\n")
            log.flush()
            proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if not inline else subprocess.DEVNULL,
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                                    env=_tasks.agent_env())
            if not inline:
                try:
                    proc.stdin.write(text.encode("utf-8") + b"\n")
                    proc.stdin.close()
                except (OSError, ValueError):
                    pass
        except OSError as exc:
            raise BridgeError(502, f"runner failed to start: {exc}") from exc
        row = {"item": key, "kind": kind, "id": ident, "runner": name, "rid": rid, "out": log_path,
               "cwd": cwd, "pid": proc.pid, "at": utc_stamp()}
        self._append_run_row(row)
        cap = spec["max_minutes"] * 60

        def reap():
            killed = False
            try:
                rc = proc.wait(timeout=cap)
            except subprocess.TimeoutExpired:
                killed = True
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    rc = proc.wait(timeout=15)
                except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                    rc = proc.wait() if proc.poll() is None else proc.returncode
            try:
                log.write(f"\n# {rid} · exit {rc}{' · KILLED after cap' if killed else ''} · {utc_stamp()}\n")
                log.close()
            except (OSError, ValueError):
                pass
            self._append_run_row({"rid": rid, "done": True, "rc": int(rc if rc is not None else -1),
                                  "killed": killed, "at": utc_stamp()})

        t = threading.Thread(target=reap, name=f"runner-{rid}", daemon=True)
        t.start()
        with self.runner_threads_lock:
            self._runner_threads = [x for x in self._runner_threads if x.is_alive()] + [t]
        return {"ok": True, "rid": rid, "out": log_path, "runner": name, "cwd": cwd, "pid": proc.pid}

    def _board_item(self, kind: str, ident: str) -> tuple[str, str, str | None]:
        """(key, task text, start folder) for a board item, built from the world on disk."""
        world = self.world()
        if kind == "task":
            items = (world.get("tasks") or {}).get("items") if isinstance(world.get("tasks"), dict) else None
            item = next((i for i in (items or []) if isinstance(i, dict) and i.get("id") == ident), None)
            if not item:
                raise BridgeError(404, "no such task on the board")
            src = str((world.get("tasks") or {}).get("source") or "audit")
            text = (f"Task from the container audit ({src}): {item.get('finding', '')} "
                    f"Evidence: {item.get('evidence', '')} Path: {item.get('path', '')}"
                    + (f" Target: {item['target']}" if item.get("target") else "")
                    + (f" Reversible command, run only if you agree it is safe: {item['command']}" if item.get("command") else "")
                    + " Report back with what you did and the exit code.")
            folder = item.get("path")
        elif kind == "quest":
            quest = next((q for q in world.get("quests", []) if isinstance(q, dict) and q.get("id") == ident), None)
            if not quest:
                raise BridgeError(404, "no such parked decision on the board")
            text = (f"Parked decision from {quest.get('src', '?')} ({quest.get('days', '?')} days old): {quest.get('text', '')} "
                    "Find the current state on disk first. Do only what is mechanical and reversible, "
                    "and report what remains a human call, with evidence.")
            folder = None
        else:
            raise BridgeError(400, "kind must be task or quest")
        cwd = None
        if isinstance(folder, str) and folder.startswith("/"):
            try:
                real = self._checked_file(folder, self.task_roots)
                cwd = real if os.path.isdir(real) else (os.path.dirname(real) if os.path.isdir(os.path.dirname(real)) else None)
            except BridgeError:
                cwd = None
        return f"{kind}:{ident}", re.sub(r"\s+", " ", text).strip()[:4000], cwd

    def dispatch(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"kind", "id", "runner"}:
            raise BridgeError(400, "body must be {kind, id, runner}")
        kind, ident, runner = body["kind"], body["id"], body["runner"]
        if kind not in ("task", "quest"):
            raise BridgeError(400, "kind must be task or quest")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", ident):
            raise BridgeError(400, "id must name a board item")
        if not isinstance(runner, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", runner):
            raise BridgeError(400, "runner must be a runner name")
        allowed = self.dispatchable_runners()
        if runner not in allowed:
            raise BridgeError(400, f"{runner} is not a dispatchable runner" if allowed else "no runners configured")
        key, text, cwd = self._board_item(kind, ident)
        with self.lock:
            now = time.monotonic()
            if now - self.dispatch_last < DISPATCH_MIN_GAP:
                raise BridgeError(429, "one dispatch every few seconds")
            self.dispatch_last = now
        self.audit({"event": "dispatch", "item": key, "runner": runner, "cwd": cwd or ""})
        return self._hand(runner, key, kind, ident, text, cwd)

    def _hand(self, runner: str, key: str, kind: str, ident: str, text: str, cwd: str | None) -> dict:
        """One hand-off: a config runner (detached, receipted here) or the owner's dispatch tool."""
        if runner in self.runners:
            return self._run_config_runner(runner, self.runners[runner], key, kind, ident, text, cwd)
        argv = [self.dispatch_bin, runner, text, "--bg"]
        try:
            if self.dispatch_runner:
                result = self.dispatch_runner(argv, cwd)
            else:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=DISPATCH_TIMEOUT,
                                        stdin=subprocess.DEVNULL, cwd=cwd or HERE,
                                        start_new_session=True, env=_tasks.agent_env())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BridgeError(502, f"dispatch failed: {exc}") from exc
        out = str(getattr(result, "stdout", "") or "").strip()
        err = str(getattr(result, "stderr", "") or "").strip()
        m = re.search(r"dispatched (d-[A-Za-z0-9-]+) \(bg\)(?:\s*\S*\s*(\S+))?", out)
        if int(getattr(result, "returncode", 1)) != 0 or not m:
            raise BridgeError(502, (err or out or "dispatch failed")[:200])
        row = {"item": key, "kind": kind, "id": ident, "runner": runner, "rid": m.group(1),
               "out": m.group(2) or "", "cwd": cwd or "", "at": utc_stamp()}
        self._append_run_row(row)
        return {"ok": True, "rid": row["rid"], "out": row["out"], "runner": runner, "cwd": cwd or ""}

    # ---- verbs are files: list them, run one on a target ---------------------------------------
    def list_verbs(self) -> dict:
        verbs, errors = _verbs.load(self.verbs_dir)
        return _verbs.public(verbs, errors) | {"dir": self.verbs_dir, "loop": _verbs.loop_tail(self.verbs_dir)}

    def _verb_target(self, target: object) -> tuple[str, str, str]:
        """(path, name, kind) for an area name or an absolute path under the roots."""
        if (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9_.~/ -]{1,300}", target)
                or ".." in target or (not target.startswith("/") and target.startswith("."))):
            raise BridgeError(400, "target must be an area name or an absolute path")
        if target.startswith("/"):
            real = self._checked_file(target, self.task_roots)
            if os.path.isdir(real):
                return real, os.path.basename(real.rstrip("/")) or real, "dir"
            if os.path.isfile(real):
                return real, os.path.basename(real), "file"
            raise BridgeError(404, "no such file or directory")
        areas = {c.get("name"): c for c in self.world().get("chars", []) if isinstance(c, dict) and c.get("kind") == "area"}
        area = areas.get(target)
        if not area or not isinstance(area.get("path"), str):
            raise BridgeError(404, f"{target} is not an area of this world")
        return self._checked_file(area["path"], self.task_roots), target, "area"

    def run_verb(self, body: object) -> dict:
        if not isinstance(body, dict) or not {"name", "target"} <= set(body) <= {"name", "target", "text", "runner"}:
            raise BridgeError(400, "body must be {name, target[, text][, runner]}")
        chosen = body.get("runner")
        if chosen is not None and (not isinstance(chosen, str) or not re.fullmatch(r"[a-z0-9-]{1,32}", chosen)):
            raise BridgeError(400, "runner must be a runner name")
        verbs, _ = _verbs.load(self.verbs_dir)
        name = body["name"]
        if not isinstance(name, str) or name not in verbs:
            raise BridgeError(404, "no such verb")
        verb = verbs[name]
        path, tname, tkind = self._verb_target(body["target"])
        if verb["scope"] == "tool" and os.path.realpath(path) != os.path.realpath(HERE):
            raise BridgeError(400, f"{name} applies only to the tool directory")
        if not ("any" in verb["applies"] or tkind in verb["applies"] or (tkind == "dir" and "area" in verb["applies"] and body["target"] == tname)):
            raise BridgeError(400, f"{name} does not apply to a {tkind}")
        text = body.get("text", "")
        if not isinstance(text, str) or len(text) > MAX_VERB_TEXT or any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
            raise BridgeError(400, "text must be short plain text")
        if verb["text"] and not text.strip():
            raise BridgeError(400, f"{name} needs text")
        if chosen and not verb["runner"]:
            raise BridgeError(400, f"{name} is a command verb; it takes no runner")
        if chosen and chosen not in self.dispatchable_runners():
            raise BridgeError(400, f"{chosen} is not a dispatchable runner")
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        cwd = folder if verb["cwd"] == "target" else HERE
        subs = {"{path}": path, "{name}": tname, "{text}": text, "{verbs_dir}": self.verbs_dir, "{tool}": HERE,
                "{config}": _CFG.get("_path") or "", "{root}": _CFG.get("work_root") or ""}
        def fill(s):
            for k, v in subs.items():
                s = s.replace(k, v)
            return s
        key = f"verb:{name}:{path}"
        with self.lock:
            now = time.monotonic()
            if now - self.dispatch_last < DISPATCH_MIN_GAP:
                raise BridgeError(429, "one hand-off every few seconds")
            self.dispatch_last = now
        self.audit({"event": "verb", "verb": name, "target": path, "cwd": cwd})
        if verb["cmd"]:
            spec = {"cmd": [fill(a) for a in verb["cmd"]], "cwd": "item", "report_dir": "", "max_minutes": verb["max_minutes"]}
            return self._run_config_runner(name, spec, key, "verb", name, text, cwd) | {"verb": name, "target": path}
        runner = verb["runner"]
        offered = self.dispatchable_runners()
        if chosen:                                   # the click picked a runner for a runner verb
            if not verb["runner"]:
                raise BridgeError(400, f"{name} is a command verb; it takes no runner")
            if chosen not in offered:
                raise BridgeError(400, f"{chosen} is not a dispatchable runner")
            runner = chosen
        elif runner == "*":
            # the owner's own config runners first, then the cheap grind seats of a registry
            names = sorted(offered, key=lambda k: (not offered[k].get("config"), k not in ("deepseek", "omp", "qwen-flash", "ollama"), k))
            if not names:
                raise BridgeError(400, "no runners configured for a runner verb")
            runner = names[0]
        elif runner not in offered:
            raise BridgeError(400, f"{runner} is not a dispatchable runner")
        prompt = re.sub(r"\s+", " ", fill(verb["prompt"])).strip()[:6000]
        return self._hand(runner, key, "verb", name, prompt, cwd) | {"verb": name, "target": path}

    # ---- tasks: compose → an agent works in a folder (or its own worktree) → review → merge ----------
    def _task(self, fn, *args):
        try:
            return fn(*args)
        except _tasks.TaskError as exc:
            raise BridgeError(exc.status, exc.message) from exc
        except (OSError, ValueError) as exc:
            raise BridgeError(500, f"task store: {exc}") from exc

    def phone_private_ids(self) -> dict:
        """{"task": {id: folder}, "workflow": {id: folder}}: every thread in a private folder (the closed ones past
        the list's limit too) and every workflow with a step in one, by the folder's name a phone may see."""
        tasks = {}
        try:
            rows = list(self.tasks._rows())
        except Exception:
            rows = []
        for row in rows:
            name = phone_private(row.get("target")) if isinstance(row, dict) else None
            if name is not None and row.get("id"):
                tasks[str(row["id"])] = name
        planets = phone_private_planets(self._world_at_hand() or {})
        flows = {}
        try:
            listed = self.workflow_list()           # the store's and the derived ones (the night run)
        except Exception:
            listed = []
        for flow in listed:
            wid = flow.get("id") if isinstance(flow, dict) else None
            if wid:
                name = self._workflow_private(wid, {"task": tasks, "planet": planets}, flow)
                if name is not None:
                    flows[str(wid)] = name
        return {"task": tasks, "workflow": flows, "planet": planets}

    def _workflow_private(self, wid: object, private: dict, flow: dict | None = None) -> str | None:
        """The private folder a workflow has a step in (by the step folders its spec names), or a thread in."""
        try:
            folders = self.workflows.folders(str(wid))
        except Exception:
            folders = {}
        for at in folders.values():
            name = phone_private(at)
            if name is not None:
                return name
        if flow is None:
            try:
                flow = self.workflows.summary(str(wid))
            except Exception:
                flow = self._derived(wid)          # a derived workflow has no spec: its steps' planets say
        return phone_workflow_private(flow, private)

    def list_tasks(self, remote: bool = False, private: dict | None = None) -> dict:
        """GET /tasks. `remote` is the phone's: its sky and count come from the phone's own world (phone_world),
        so no private thread's id or title rides along in the sky's contacts."""
        try:
            self.tick_tasks()          # the oracle loop: failed verify, retries left → agent again
        except Exception:                           # a broken retry never hides the list
            pass
        try:
            self.tick_workflows()      # and the workflows' own (a step whose thread finished lets the next launch)
        except Exception:
            pass
        threads, workflows = self._task(self.tasks.list), self.workflow_list()
        out = {"tasks": threads, "runners": _tasks.public_runners(self.tasks.runners),
               "max_running": self.tasks.max_running, "workflows": workflows, "shows": self.shows.live()}
        snap = self._task_counts(threads, workflows, remote=remote, private=private)
        if snap is not None:
            out["sky"], out["counts"], out["rev"] = snap["sky"], snap["counts"], self.snapshot_rev()
        return out

    def snapshot_rev(self) -> float:
        """The revision of a /tasks snapshot: this clock's seconds, never lower than the last one given (a clock
        that jumps back cannot make a later reply look older). The page keeps the sky and count with the highest
        revision it has seen, the world's own being its `rendered` stamp (the same clock, at render time)."""
        with self._snap_lock:
            self._snap_rev = max(time.time(), self._snap_rev + 0.001)
            return round(self._snap_rev, 3)

    def _task_counts(self, threads, workflows, remote: bool = False, private: dict | None = None) -> dict | None:
        """The one count (sky.counts) for a /tasks reply, with the sky it was counted from: the world at
        hand rebuilt with THESE threads and workflows, so a thread that went to review since the last render is in
        the number the page shows, and its contact circles the right planet (the badges, the rail's row and the
        count move together). None when there is no world yet or it cannot be built (the page then keeps what it
        has). Returns {"sky", "counts"}; counts["total"] == len(sky["contacts"])."""
        try:
            world = self._world_at_hand()
            if not isinstance(world, dict):
                return None
            fresh = dict(world, agent_tasks=threads, workflows=workflows)
            if remote:          # counted from what a phone may see: the hidden threads are not in its number either
                fresh = phone_world(fresh, private=private)
            sky = _sky.build(fresh)
            return {"sky": sky, "counts": _sky.counts(sky, threads, workflows)}
        except Exception:
            return None

    # ---- workflows (workflows.py): the store's routes, the ticker's hook, the shows -------------
    def _wf(self, fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except _workflows.WorkflowError as exc:
            raise BridgeError(exc.status, exc.message) from exc
        except _tasks.TaskError as exc:
            raise BridgeError(exc.status, exc.message) from exc
        except (OSError, ValueError) as exc:
            raise BridgeError(500, f"workflow store: {exc}") from exc

    def _world_at_hand(self) -> dict | None:
        """The freshest world already read (the page's or the last render's, however old): what the ticker, the
        /tasks poll, shows and pointers place things with. Only a bridge that has seen no world yet renders one."""
        try:
            got = self._fresh_world(365 * 86400.0)
            return got if got is not None else self.cached_world()
        except Exception:
            return None

    def _wf_chars(self) -> dict | None:
        world = self._world_at_hand()
        if world is not None and isinstance(world.get("chars"), list):
            self.workflows.set_chars(world["chars"])
        return world

    def workflow_list(self, since: str | None = None) -> list:
        """The open workflows and the newest closed ones (summaries, no paths), placed on the map at hand, plus the
        derived ones the last render made (the night run): /tasks carries the same list the page was built with."""
        world = self._wf_chars()
        try:
            out = self.workflows.list(since)
        except Exception:                           # the thread list still answers without the workflows
            out = []
        have = {w.get("id") for w in out}
        for w in ((world or {}).get("workflows") or []):
            if isinstance(w, dict) and w.get("derived") and w.get("id") not in have:
                if since is None or (_workflows._epoch(w.get("updated")) or 0) > (_workflows._epoch(since) or 0):
                    out.append(w)
        return out

    def _derived(self, wid: object) -> dict | None:
        world = self._world_at_hand() or {}
        return next((w for w in (world.get("workflows") or []) if isinstance(w, dict) and w.get("derived")
                     and w.get("id") == wid), None)

    def workflow_read(self, path: str, query: dict) -> dict:
        """GET /workflows[?since=ISO] (changed summaries and the live shows), /workflow?id= (a summary, its last
        events, where each step works and what Start would run), /workflow/replay?id=[&page=N] (frames)."""
        if path == "/workflows":
            if not set(query) <= {"since"} or any(len(v) != 1 for v in query.values()):
                raise BridgeError(400, "only one since query parameter is allowed")
            since = query.get("since", [None])[0]
            if since is not None and _workflows._epoch(since) is None:
                raise BridgeError(400, "since is a time like 2026-10-12T07:00:00Z")
            return {"workflows": self.workflow_list(since), "shows": self.shows.live(), "now": utc_stamp()}
        if path == "/workflow":
            return self.workflow_detail(query)
        return self.workflow_replay(query)

    def workflow_detail(self, query: dict) -> dict:
        if set(query) != {"id"} or len(query["id"]) != 1:
            raise BridgeError(400, "one id query parameter is required")
        wid = query["id"][0]
        self._wf_chars()
        try:
            out = self.workflows.detail(wid)
        except _workflows.WorkflowError as exc:
            derived = self._derived(wid) if exc.status == 404 else None
            if derived is None:
                raise BridgeError(exc.status, exc.message) from exc
            return {"workflow": derived, "events": [], "derived": True}
        out["shell_lines"] = self._wf(self.workflows.shell_lines, wid)
        return out

    def workflow_replay(self, query: dict) -> dict:
        if not {"id"} <= set(query) <= {"id", "page"} or any(len(v) != 1 for v in query.values()):
            raise BridgeError(400, "one id (and optional page) query parameter is required")
        wid, page = query["id"][0], self._int_param(query, "page", 0, 10_000)
        self._wf_chars()
        try:
            return self.workflows.replay(wid, page)
        except _workflows.WorkflowError as exc:
            derived = self._derived(wid) if exc.status == 404 else None
            if derived is None:
                raise BridgeError(exc.status, exc.message) from exc
            frames = _workflows.summary_frames(derived)
            return {"id": wid, "title": derived.get("title"), "order": [s["key"] for s in derived["steps"]],
                    "steps": [{k: s.get(k) for k in ("key", "label", "kind", "after", "planet_uid", "moon_uid", "place")}
                              for s in derived["steps"]],
                    "frames": frames, "page": 0, "pages": 1, "total_frames": len(frames), "derived": True}

    def tick_workflows(self) -> list:
        """One engine tick (workflows.WorkflowStore.tick never overlaps itself): launches, rebinds, second
        opinions, kept outputs. What it did is audited; a workflow that could not be read is said once."""
        self._wf_chars()
        did = self.workflows.tick()
        for wid, key, what in did:
            self.audit({"event": "workflow-tick", "id": wid, "key": key, "what": what})
        errors, self.workflows.errors = list(self.workflows.errors), []
        for wid, message in errors:
            if (wid, message) not in self._wf_errors:
                self._wf_errors.add((wid, message))
                with contextlib.suppress(OSError):
                    self.audit({"event": "workflow-tick-error", "id": wid, "error": str(message)[:200]})
        return did

    def _nudged(self) -> bool:
        """Whether <workflows_dir>/.nudge moved since the ticker last looked (every workflow event touches it)."""
        try:
            st = os.stat(os.path.join(self.workflows.workflows_dir, ".nudge"))
            seen = (st.st_ino, st.st_size, st.st_mtime_ns)
        except OSError:
            seen = None
        moved = seen != self._nudge_seen
        self._nudge_seen = seen
        return moved and seen is not None

    def workflow_create(self, body: object, depth: int = 0) -> dict:
        if not isinstance(body, dict):
            raise BridgeError(400, "a workflow is a JSON object {title, steps, ...} (GET /agent/map lists the call; "
                                   "`container-map wf schema` the spec)")
        self._wf_chars()
        out = self._wf(self.workflows.create, body, depth=depth)
        self.audit({"event": "workflow-create", "id": out["id"], "by": out["workflow"]["by"]["name"],
                    "steps": out["workflow"]["total"], "start": out["start"], "via": "agent"})
        return out

    WF_AGENT_BODIES = {"step": ({"id", "key"}, {"id", "key", "state", "says", "progress", "output"}),
                       "pulse": ({"id"}, {"id", "key"}), "done": ({"id"}, {"id"}), "stop": ({"id"}, {"id", "why"})}

    def workflow_agent(self, action: str, body: object, token: str) -> dict:
        """An agent's update of its own workflow (the per-workflow token, or a task step's own token for its step)."""
        need, allowed = self.WF_AGENT_BODIES[action]
        if not isinstance(body, dict) or not need <= set(body) <= allowed:
            raise BridgeError(400, f"body must be {{{', '.join(sorted(need))}"
                                   + "".join(f"[, {k}]" for k in sorted(allowed - need)) + "}")
        if not token:
            raise BridgeError(403, "the workflow's token is needed in the X-Workflow-Token header (shown once when "
                                   "the workflow was made)")
        self._wf_chars()
        store = self.workflows
        if action == "step":
            return self._wf(store.step, body["id"], body["key"], token, body.get("state"), body.get("says"),
                            body.get("progress"), body.get("output"))
        if action == "pulse":
            return self._wf(store.pulse, body["id"], token, body.get("key"))
        if action == "done":
            return {"ok": True, "workflow": self._wf(store.done, body["id"], token)}
        return self._wf(store.stop, body["id"], body.get("why"), token)

    def workflow_owner(self, action: str, body: object, via: str = "ui", device: str | None = None,
                       extra: dict | None = None) -> dict:
        """The owner's buttons: start, gate (Approve / Reject), retry, skip, stop, new (a template or Run again).
        One audit row each, naming the device when it came from a phone."""
        if not isinstance(body, dict):
            raise BridgeError(400, "body must be a JSON object")
        self._wf_chars()
        store, row = self.workflows, {"event": "workflow-" + action, "via": via, **(extra or {})}
        if device:
            row["device"] = device

        def keys(need, extra=()):
            if not set(need) <= set(body) <= set(need) | set(extra):
                raise BridgeError(400, f"body must be {{{', '.join(need)}" + "".join(f"[, {k}]" for k in extra) + "}")

        if action == "start":
            keys(("id",))
            out = {"ok": True, "workflow": self._wf(store.start, body["id"])}
        elif action == "gate":
            keys(("id", "key", "pass"), ("why",))
            if not isinstance(body["pass"], bool):
                raise BridgeError(400, "pass is true (approve) or false (reject)")
            out = {"ok": True, "workflow": self._wf(store.gate, body["id"], body["key"], body["pass"], body.get("why"))}
            row.update(key=body["key"], passed=body["pass"])
        elif action in ("retry", "skip"):
            keys(("id", "key"))
            out = {"ok": True, "workflow": self._wf(getattr(store, action), body["id"], body["key"])}
            row["key"] = body["key"]
        elif action == "stop":
            keys(("id",), ("why", "tasks"))
            if not isinstance(body.get("tasks", False), bool):
                raise BridgeError(400, "tasks is true (stop its running tasks too) or false")
            out = self._wf(store.stop, body["id"], body.get("why"), owner=True, tasks=body.get("tasks", False))
        elif action == "new":
            if "from" in body:
                keys(("from",))
                out = self._wf(store.run_again, body["from"])
                row["from"] = body["from"]
            else:
                keys(("template", "at", "goal"), ("runner",))
                runner = body.get("runner") or self._default_runner()
                if not isinstance(runner, str) or not runner:
                    raise BridgeError(400, "no agent is set up to run it")
                out = self._wf(store.new_from_template, body["template"], body["at"], body["goal"], runner)
                row["template"] = body["template"]
            out.pop("token", None)          # the owner's own workflow: nothing of the user's needs its token, so no page holds it
            row["new"] = out["id"]
        else:
            raise BridgeError(404, "not found")
        wid = body.get("id") or out.get("id")
        if wid:
            row["id"] = wid
        self.audit(row)
        return out

    def _default_runner(self) -> str | None:
        """The composer's default agent: the first one offered that does work (not a checker only)."""
        return next((name for name, spec in (self.tasks.runners or {}).items()
                     if (spec or {}).get("role", "both") != "check"), None)

    def _anchor(self, world: dict | None):
        """resolve(at) for shows and pointers: a uid on the map (a planet, a folder, an agent, a machine), a
        place:<label> (a virtual waypoint), or an absolute folder path inside a planet -> its uid."""
        world = world or {}
        uids = set((world.get("control") or {}).get("visit_uids") or [])
        chars = [c for c in (world.get("chars") or []) if isinstance(c, dict)]
        for c in chars:
            if isinstance(c.get("uid"), str):
                uids.add(c["uid"])
            for r in (c.get("rooms") or []) if isinstance(c.get("rooms"), list) else []:
                if isinstance(r, dict) and isinstance(r.get("uid"), str):
                    uids.add(r["uid"])
        resolver = None

        def resolve(at: str):
            nonlocal resolver
            if not isinstance(at, str) or not at or len(at) > 1000 or "\x00" in at:
                return None
            if at.startswith("place:"):
                label = " ".join(at[6:].split())
                if not 1 <= len(label) <= _workflows.BOUNDS["place"] or any(
                        ord(ch) < 32 or ch in _workflows.BIDI for ch in label):
                    return None
                return "place:" + label
            if at in uids:
                return at
            if at.startswith("/"):
                if resolver is None:
                    resolver = _workflows._Resolver(chars)
                planet, moon, _place, _where = resolver.where(os.path.normpath(at))
                return moon or planet
            return None
        return resolve

    def show_put(self, body: object, oversize: bool = False) -> dict:
        """Take a show. oversize: its body was over MAX_TASK_BODY, which only a page earns: a 413 unless a page op
        validated (its reasons are in the message), and nothing is stored."""
        wait = self.show_rate.take("shows")
        if wait:
            raise BridgeError(429, f"{SHOW_RATE[0]} shows in {int(SHOW_RATE[1])} seconds is the limit")
        show, skipped, clamped, errors = clean_show(body, self._anchor(self._world_at_hand()))
        if oversize and not any(op["op"] == "page" for op in show["ops"]):
            raise BridgeError(413, "request body is too large" + ("; " + "; ".join(errors) if errors else ""))
        out = {"ok": True, "id": None, "skipped": skipped, "clamped": clamped, "errors": errors}
        if show["ops"]:
            rec, retired = self.shows.put(show)
            out.update(id=rec["id"], expires=rec["expires"], show=rec)
            if retired:
                out["retired"] = retired
            row = {"event": "show", "id": rec["id"], "by": rec["by"], "ops": len(rec["ops"]),
                   "skipped": len(skipped), "ttl": rec["ttl"]}
            page = next((op for op in rec["ops"] if op["op"] == "page"), None)
            if page:
                row["page_bytes"] = page["bytes"]
            self.audit(row)
        return out

    def show_frame(self, query: dict) -> str:
        """GET /show/page/frame?id=<show id>&rev=<its revision>: a live page's source, the document of the
        panel's frame, for the page's own UI session. rev names the version the panel asked for (it also keeps a
        reload from being answered from a cache); the version that is live now is what comes back."""
        if set(query) != {"id", "rev"} or any(len(v) != 1 for v in query.values()):
            raise BridgeError(400, "one id and one rev query parameter are required")
        sid, rev = query["id"][0], query["rev"][0]
        if not SHOW_ID_RE.fullmatch(sid):
            raise BridgeError(400, "a show id is 1-40 lower-case letters, digits or dashes")
        if not re.fullmatch(r"[0-9]{1,20}", rev):
            raise BridgeError(400, "rev is the revision number the show was listed with")
        got = self.shows.page(sid)
        if got is None:
            raise BridgeError(404, "no live page with that id")
        return got["html"]

    def show_clear(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"id"}:
            raise BridgeError(400, "body must be {id}")
        if not self.shows.clear(body["id"]):
            raise BridgeError(404, "no live show with that id")
        self.audit({"event": "show-clear", "id": body["id"]})
        return {"ok": True, "id": body["id"]}

    def area_set(self, body: object) -> dict:
        """The owner's choice of what a planet holds (its card's Change sheet): areas.json gets it, and the map is
        rendered again at once so the page shows the new body on its next read."""
        if not isinstance(body, dict) or not {"uid", "kind"} <= set(body) <= {"uid", "kind", "glyph"}:
            raise BridgeError(400, "body must be {uid, kind[, glyph]}")
        uid = body["uid"]
        world = self.cached_world()
        area = next((c for c in world.get("chars") or [] if isinstance(c, dict) and c.get("kind") == "area"
                     and c.get("uid") == uid), None) if isinstance(uid, str) else None
        if area is None or not isinstance(area.get("name"), str):
            raise BridgeError(404, "no planet with that uid on the map")
        try:
            got = area_set_file(self.areas_write_path, area["name"], body["kind"], body.get("glyph"))
        except ValueError as exc:
            raise BridgeError(400, str(exc)) from exc
        except AreasBusy as exc:
            raise BridgeError(503, str(exc)) from exc
        except OSError as exc:
            raise BridgeError(500, f"could not write the areas file: {exc}") from exc
        self.audit({"event": "area-set", "uid": uid, "kind": got["kind"], "glyph": got["glyph"]})

        def again():
            try:
                self.world()
            except Exception as exc:         # said in the audit, never raised into nothing
                with contextlib.suppress(OSError):
                    self.audit({"event": "area-set-render-error", "error": f"{type(exc).__name__}: {exc}"[:200]})
        threading.Thread(target=again, name="area-set-render", daemon=True).start()
        return {"ok": True, "uid": uid, "kind": got["kind"], "glyph": got["glyph"]}

    def tick_tasks(self) -> list[str]:
        """One oracle-loop tick (TaskStore.tick never overlaps itself); retries are audited."""
        retried = self.tasks.tick()
        for tid in retried:
            self.audit({"event": "task-retry", "id": tid})
        return retried

    def start_ticker(self) -> bool:
        """The oracle loop's own clock: a daemon thread ticks every tick_seconds whether or not a
        page is open, so retries do not stall when the tab closes. tick_seconds 0 turns it off."""
        if self.tick_seconds <= 0 or self._ticker is not None:
            return False
        self._tick_stop.clear()
        self._ticker = threading.Thread(target=self._tick_loop, name="task-ticker", daemon=True)
        self._ticker.start()
        return True

    def stop_ticker(self, timeout: float = 5.0) -> None:
        self._tick_stop.set()
        ticker, self._ticker = self._ticker, None
        if ticker is not None:
            ticker.join(timeout)

    def _tick_loop(self) -> None:
        """Every tick_seconds: the threads' oracle loop and the workflows' engine. Between ticks, every
        NUDGE_SECONDS: a stat of <workflows_dir>/.nudge, and an engine tick when it moved (a step reported done
        launches what waited on it within about a second, not at the next tick)."""
        last = None
        step = min(NUDGE_SECONDS, self.tick_seconds)
        due = time.monotonic() + self.tick_seconds
        self._nudged()                          # what is there now was there before the ticker: no tick for it
        while not self._tick_stop.wait(step):
            try:
                now = time.monotonic()
                nudged = self._nudged()
                if now >= due:
                    due = now + self.tick_seconds
                    self.tick_tasks()
                    self.tick_workflows()
                elif nudged:
                    self.tick_workflows()
                last = None
            except Exception as exc:        # logged once per distinct error, never raised
                message = f"{type(exc).__name__}: {exc}"[:200]
                if message != last:
                    last = message
                    print(f"task ticker: {message}", file=sys.stderr, flush=True)
                    try:
                        self.audit({"event": "task-tick-error", "error": message})
                    except OSError:
                        pass

    @staticmethod
    def _int_param(query: dict, name: str, default: int, top: int) -> int:
        value = _uint(query.get(name, [str(default)])[0], top)
        if value is None:
            raise BridgeError(400, f"{name} must be a non-negative integer")
        return value

    def task_detail(self, query: dict) -> dict:
        if not {"id"} <= set(query) <= {"id", "run", "since"} or any(len(v) != 1 for v in query.values()):
            raise BridgeError(400, "one id (and optional run, since) query parameter is required")
        run = self._int_param(query, "run", 0, 10_000)
        since = self._int_param(query, "since", 0, 1 << 40)
        return self._task(self.tasks.get, query["id"][0], run or None, since)

    def task_diff(self, query: dict) -> dict:
        if set(query) != {"id"} or len(query["id"]) != 1:
            raise BridgeError(400, "one id query parameter is required")
        return self._task(self.tasks.diff, query["id"][0])

    def create_task(self, body: object, via: str = "ui") -> dict:
        if not isinstance(body, dict) or not {"text", "target", "runner"} <= set(body) <= {"text", "target", "runner", "isolate", "model", "verify", "retries", "setup"}:
            raise BridgeError(400, "body must be {text, target, runner[, isolate, model, verify, retries, setup]}")
        if via == "agent" and not self.agent_verify and (
                body.get("verify") not in (None, "") or body.get("retries") not in (None, 0)):
            raise BridgeError(400, "verify is set from the page; "
                                   "agent_verify in container.json enables it for agents")
        # a setup line runs as you before any agent does: from an agent only under the same switch
        if via == "agent" and not self.agent_verify and body.get("setup") not in (None, ""):
            raise BridgeError(400, "setup is set from the page; "
                                   "agent_verify in container.json enables it for agents")
        isolate = True if via == "agent" else body.get("isolate", True)     # an agent's thread always gets its own worktree
        # a phone never works in the folder itself: a folder with no repo or no commit is refused
        # (the engine says why) instead of falling back to working in place
        phone = via.startswith("phone:")
        out = self._task(self.tasks.create, body["text"], body["target"], body["runner"], isolate, body.get("model"),
                         body.get("verify"), body.get("retries", 0), body.get("setup"), phone)
        self.audit({"event": "task", "id": out["id"], "runner": out["runner"], "model": out.get("model"), "target": out["target"], "mode": out["mode"],
                    "verify": bool(out.get("verify_cmd")), "retries": out.get("retries", 0), "setup": bool(out.get("setup_cmd")),
                    "via": via})
        return out

    # ---- for agents: the map and its cards as data, and the thread verbs (never merge or close) ----
    def _base_url(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    def _task_list_or_empty(self) -> list:
        try:
            return self.tasks.list()
        except Exception:                        # the map still answers without the task store
            return []

    def agent_map(self) -> dict:
        return _cards.map_card(self.cached_world(), self._task_list_or_empty(), self._base_url(), self.live_path,
                               self.agent_verify)

    def agent_unit(self, query: dict) -> dict:
        if set(query) != {"uid"} or len(query["uid"]) != 1 or not re.fullmatch(r"(area|dir):[^\x00]{1,400}", query["uid"][0]):
            raise BridgeError(400, "one uid query parameter (area:… or dir:…) is required")
        card = _cards.unit_card(self.cached_world(), self._task_list_or_empty(), query["uid"][0], self._base_url(), HERE, self.live_path,
                                self.agent_verify)
        if card is None:
            raise BridgeError(404, "no planet or folder with that uid on the map")
        return card

    # ---- the wingmate: GET /wingmate (who answers, with which models),
    #      POST /wingmate/ask {q[, model]} → {id}, GET /wingmate/answer?id= ----
    def mate_info(self) -> dict:
        spec = self.mate_spec
        models = _mate.listing(spec)
        default = _mate.default_of(spec) if spec else ""
        with self.mate_lock:
            pending = sum(1 for j in self.mate_jobs.values() if j["status"] == "thinking")
        return {"name": (spec or {}).get("name") or _mate.default_name(_CFG), "available": bool(spec),
                "default": default, "models": models,
                "model": next((m["label"] for m in models if m["name"] == default), ""),
                "pending": pending}

    def mate_ask(self, body: object, phone: bool = False) -> dict:
        if not isinstance(body, dict) or not {"q"} <= set(body) <= {"q", "model"}:
            raise BridgeError(400, "body must be {q[, model]}")
        if not isinstance(body["q"], str):
            raise BridgeError(400, "body must be {q[, model]}")
        if "model" in body and not (isinstance(body["model"], str)
                                    and _mate.MODEL_NAME_RE.fullmatch(body["model"])):
            raise BridgeError(400, "model must be a model name from GET /wingmate")
        q = body["q"].strip()
        if not q:
            raise BridgeError(400, "ask something")
        if len(q) > _mate.MAX_QUESTION or any(ord(c) < 32 and c not in "\n\t" for c in q):
            raise BridgeError(400, f"a question is plain text under {_mate.MAX_QUESTION} characters")
        if not self.mate_spec:
            raise BridgeError(409, "no wingmate model: put claude on PATH or set \"wingmate\" in container.json")
        chosen = _mate.model_spec(self.mate_spec, body.get("model"))
        if chosen is None:
            raise BridgeError(400, "no such model")
        with self.mate_lock:
            now = time.time()
            self.mate_times = [t for t in self.mate_times if now - t < 60]
            if any(j["status"] == "thinking" for j in self.mate_jobs.values()):
                raise BridgeError(409, "still thinking about the last question")
            if len(self.mate_times) >= 8:
                raise BridgeError(429, "eight questions a minute is the limit")
            self.mate_times.append(now)
            jid = secrets.token_hex(6)
            for old in sorted(self.mate_jobs, key=lambda k: self.mate_jobs[k]["at"])[:-19]:
                self.mate_jobs.pop(old, None)
            self.mate_jobs[jid] = {"status": "thinking", "at": now, "answer": "", "goto": None,
                                   "actions": [], "error": None, "seconds": 0.0,
                                   "model": chosen["model"]}
        try:
            world = self.cached_world()
        except BridgeError:
            world = {}
        # the workflows as the store has them now and the live shows: quoted in the brief as data (wingmate.py)
        world = dict(world, workflows=self.workflow_list() or world.get("workflows") or [], shows=self.shows.live())
        private = self.phone_private_ids() if phone else None
        if phone:                               # a phone's question is answered from the phone's own world
            world = phone_world(world, private=private)
        try:
            tasks = self.tasks.list()
            if phone:                           # and its own task list: a private folder's tasks as stubs
                tasks = phone_swap_ids([phone_task_row(row) for row in tasks], private)
        except Exception:                       # the wingmate still answers from the page's own snapshot
            tasks = None
        brief_text = _mate.brief(world, tasks)
        spec, runner, model = self.mate_spec, self.mate_runner, chosen["model_name"]

        def work():
            res = _mate.ask(spec, brief_text, q, model, runner)
            with self.mate_lock:
                job = self.mate_jobs.get(jid)
                if job is not None:
                    job.update(status="error" if res.get("error") else "done", answer=res.get("answer", ""),
                               goto=res.get("goto"), actions=res.get("actions", []), error=res.get("error"), seconds=res.get("seconds", 0.0))
        threading.Thread(target=work, name=f"wingmate-{jid}", daemon=True).start()
        self.audit({"event": "wingmate", "id": jid, "chars": len(q), "model": model})
        return {"id": jid, "status": "thinking", "name": spec.get("name") or _mate.DEFAULT_NAME,
                "model": chosen["model"]}

    def mate_answer(self, query: dict) -> dict:
        if set(query) != {"id"} or len(query["id"]) != 1 or not re.fullmatch(r"[0-9a-f]{12}", query["id"][0]):
            raise BridgeError(400, "one id query parameter is required")
        with self.mate_lock:
            job = self.mate_jobs.get(query["id"][0])
            if job is None:
                raise BridgeError(404, "no such question")
            keys = ("status", "answer", "goto", "actions", "error", "seconds", "model")
            return {"id": query["id"][0], **{k: job.get(k) for k in keys}}

    # ---- the meta thread: GET /meta (who answers, the chat, the last board), POST /meta/ask
    #      {[q][, model]} → {id}, GET /meta/answer?id=. meta.py gathers the facts and reads the
    #      answer, the playbook is the judgement, and every button the page draws from it is an
    #      existing, guarded route ----
    META_VIEW = ("status", "q", "model", "seconds", "error", "triage", "summary", "rows", "sends",
                 "renames", "checks", "drafts", "goto", "missing", "rejected", "unjudged", "items",
                 "notes", "run", "finished")

    def _meta_chat_load(self) -> list[dict]:
        """The last turns of the meta chat, from chat.jsonl (a file that only grows; a partial
        first line of the tail is skipped). None yet = no turns."""
        try:
            with open(os.path.join(self.meta_dir, "chat.jsonl"), "rb") as handle:
                handle.seek(max(0, handle.seek(0, os.SEEK_END) - 65536))
                tail = handle.read().decode("utf-8", "replace")
        except OSError:
            return []
        turns = []
        for line in tail.splitlines():
            try:
                turn = json.loads(line)
            except ValueError:
                continue
            if (isinstance(turn, dict) and isinstance(turn.get("q"), str)
                    and isinstance(turn.get("summary"), str)):
                keys = ("at", "id", "q", "summary", "model")
                turns.append({k: str(turn.get(k) or "")[:1200] for k in keys})
        return turns[-_meta.CHAT_TURNS:]

    def _meta_chat_add(self, turn: dict) -> str | None:
        """Keep a turn in memory and append it to chat.jsonl; why not, when it could not be."""
        with self.meta_lock:
            self.meta_chat = (self.meta_chat + [turn])[-_meta.CHAT_TURNS:]
        try:
            _config.make_state_dir(self.meta_dir, 0o700)
            fd = os.open(os.path.join(self.meta_dir, "chat.jsonl"),
                         os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, (json.dumps(turn, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                os.close(fd)
        except (OSError, ValueError) as exc:
            return f"the chat could not be saved: {exc}"
        return None

    def _meta_folder(self) -> str:
        """A new folder for one run under meta/, named by the second it started in (mode 700)."""
        _config.make_state_dir(self.meta_dir, 0o700)
        name = time.strftime("%Y%m%d-%H%M%S")
        for n in range(1, 50):
            path = os.path.join(self.meta_dir, name if n == 1 else f"{name}-{n}")
            try:
                os.mkdir(path, 0o700)
            except FileExistsError:
                continue
            os.chmod(path, 0o700)
            return path
        raise OSError(errno.EEXIST, "no free run folder for this second")

    @staticmethod
    def _meta_write(folder: str, name: str, text: str) -> None:
        fd = os.open(os.path.join(folder, name), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(text)

    def _meta_view(self, jid: str, job: dict) -> dict:
        return {"id": jid, **{k: job.get(k) for k in self.META_VIEW}}

    def meta_info(self) -> dict:
        spec = self.mate_spec
        models = _mate.listing(spec)
        default = _meta.default_model(spec) if spec else ""
        wait = max(float((spec or {}).get("max_seconds") or 0), _meta.MIN_SECONDS)
        with self.meta_lock:
            thinking = [(k, j) for k, j in self.meta_jobs.items() if j["status"] == "thinking"]
            done = sorted(((k, j) for k, j in self.meta_jobs.items() if j["status"] != "thinking"),
                          key=lambda kj: kj[1].get("finished", 0))
            return {"available": bool(spec), "models": models, "default": default,
                    "model": next((m["label"] for m in models if m["name"] == default), ""),
                    "max_seconds": wait, "pending": len(thinking),
                    "pending_id": thinking[0][0] if thinking else None,
                    "elapsed": round(time.time() - thinking[0][1]["at"], 1) if thinking else 0,
                    "chat": [dict(t) for t in self.meta_chat],
                    "last": self._meta_view(*done[-1]) if done else None}

    def meta_ask(self, body: object) -> dict:
        if not isinstance(body, dict) or not set(body) <= {"q", "model"}:
            raise BridgeError(400, "body must be {[q][, model]}")
        if "q" in body and not isinstance(body["q"], str):
            raise BridgeError(400, "q must be text")
        if "model" in body and not (isinstance(body["model"], str)
                                    and _mate.MODEL_NAME_RE.fullmatch(body["model"])):
            raise BridgeError(400, "model must be a model name from GET /meta")
        q = (body.get("q") or "").strip() or _meta.DEFAULT_QUESTION   # none = the Triage all button
        if len(q) > _mate.MAX_QUESTION or any(ord(c) < 32 and c not in "\n\t" for c in q):
            raise BridgeError(400, f"a question is plain text under {_mate.MAX_QUESTION} "
                                   "characters")
        if not self.mate_spec:
            raise BridgeError(409, "no wingmate model: put claude on PATH or set \"wingmate\" "
                                   "in container.json")
        model = body.get("model") or _meta.default_model(self.mate_spec)
        chosen = _mate.model_spec(self.mate_spec, model)
        if chosen is None:
            raise BridgeError(400, "no such model")
        try:
            playbook = _meta.load_playbook()
        except OSError as exc:
            raise BridgeError(409, f"the meta playbook (playbooks/meta.md) cannot be read: "
                                   f"{exc}") from exc
        triage = q == _meta.DEFAULT_QUESTION
        with self.meta_lock:
            now = time.time()
            self.meta_times = [t for t in self.meta_times if now - t < 600]
            if any(j["status"] == "thinking" for j in self.meta_jobs.values()):
                raise BridgeError(409, "still thinking about the last question")
            if len(self.meta_times) >= 4:
                raise BridgeError(429, "four meta runs in ten minutes is the limit")
            self.meta_times.append(now)
            jid = secrets.token_hex(6)
            for old in sorted(self.meta_jobs, key=lambda k: self.meta_jobs[k]["at"])[:-9]:
                self.meta_jobs.pop(old, None)
            self.meta_jobs[jid] = {
                "status": "thinking", "at": now, "q": q, "triage": triage, "model": chosen["model"],
                "seconds": 0.0, "error": None, "summary": "", "rows": [], "sends": [],
                "renames": [], "checks": [], "drafts": [], "goto": None, "missing": [],
                "rejected": [], "unjudged": [], "items": {}, "notes": [], "run": None}
            chat = [dict(t) for t in self.meta_chat]

        def work():
            try:
                done = self._meta_work(q, triage, model, playbook, chat)
            except Exception as exc:            # the job always ends, with the reason as it came
                done = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            if done["status"] == "done":
                said = self._meta_chat_add({
                    "at": utc_stamp(), "id": jid, "q": q, "summary": done["summary"][:1200],
                    "model": str(done.get("model") or "")})
                if said:
                    done["notes"] = done["notes"] + [said]
            with self.meta_lock:
                job = self.meta_jobs.get(jid)
                if job is not None:
                    job.update(done, finished=time.time())
        threading.Thread(target=work, name=f"meta-{jid}", daemon=True).start()
        self.audit({"event": "meta", "id": jid, "chars": len(q), "model": chosen["model_name"]})
        return {"id": jid, "status": "thinking", "model": chosen["model"]}

    def _meta_work(self, q: str, triage: bool, model: str, playbook: str, chat: list) -> dict:
        """One run, on its own thread: the facts, the prompt (written under meta/<time>/ before
        the model runs, so a run can be read back), the model, the lines. Returns the job's
        finished fields."""
        notes = []
        try:
            world = self.cached_world()
        except BridgeError as exc:
            world, notes = {}, notes + [f"the map's world could not be read: {exc.message}"]
        try:
            rows = self.tasks.list()
        except Exception as exc:
            rows, notes = [], notes + [f"the thread list could not be read: {exc}"]
        gather = self.meta_gather or _meta.gather_worktrees
        facts = gather(_meta.repos_of(rows), owners=_meta.owners_of(rows))
        notes += facts.get("notes", [])
        built = _meta.build(world, rows, facts, time.time(), self.tasks.runners)
        folder = None
        try:
            folder = self._meta_folder()
            self._meta_write(folder, "context.md", built["text"] + "\n")
            self._meta_write(folder, "prompt.md", _meta.prompt(playbook, built["text"], chat, q))
        except OSError as exc:
            notes.append(f"the run could not be saved: {exc}")
        if built["trimmed"]:
            notes.append("trimmed to fit: "
                         + ", ".join(f"{n} {label}" for label, n in built["trimmed"].items()))
        if built["left_out"]:
            notes.append("left out, their id or path is not a plain line: "
                         + ", ".join(f"{n} {what}" for what, n in built["left_out"].items()))
        res = _meta.run(self.mate_spec, model, playbook, built["text"], chat, q, self.mate_runner)
        out = {"model": res["model"], "seconds": res["seconds"], "notes": notes,
               "run": os.path.basename(folder) if folder else None}
        if res.get("truncated"):
            out["notes"] = notes = notes + [
                "the model's answer was cut at its size cap: lines past it were not read"]
        if res["error"]:
            return dict(out, status="error", error=res["error"])
        try:
            if folder:
                self._meta_write(folder, "answer.md", res["answer"])
        except OSError as exc:
            out["notes"] = notes + [f"the answer could not be saved: {exc}"]
        parsed = _meta.parse(res["answer"], built["refs"])
        judged = {r["ref"] for r in parsed["rows"]}
        unjudged = [r for r in built["refs"] if r not in judged][:100] if triage else []
        return dict(out, status="done", error=None, items=built["refs"], unjudged=unjudged,
                    **parsed)

    def meta_answer(self, query: dict) -> dict:
        if (set(query) != {"id"} or len(query["id"]) != 1
                or not re.fullmatch(r"[0-9a-f]{12}", query["id"][0])):
            raise BridgeError(400, "one id query parameter is required")
        with self.meta_lock:
            job = self.meta_jobs.get(query["id"][0])
            if job is None:
                raise BridgeError(404, "no such question")
            return self._meta_view(query["id"][0], job)

    def task_followup(self, body: object, via: str = "ui") -> dict:
        if not isinstance(body, dict) or set(body) != {"id", "text"}:
            raise BridgeError(400, "body must be {id, text}")
        out = self._task(self.tasks.followup, body["id"], body["text"])
        self.audit({"event": "task-followup", "id": out["id"], "run": out["runs"], "via": via})
        return out

    def task_check(self, body: object, via: str = "ui") -> dict:
        if not isinstance(body, dict) or not {"id", "runner"} <= set(body) <= {"id", "runner", "model", "text"}:
            raise BridgeError(400, "body must be {id, runner[, model, text]}")
        out = self._task(self.tasks.check, body["id"], body["runner"], body.get("model"), body.get("text", ""))
        self.audit({"event": "task-check", "id": out["id"], "run": out["runs"], "runner": body["runner"], "via": via})
        return out

    def task_rename(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"id", "title"}:
            raise BridgeError(400, "body must be {id, title}")
        out = self._task(self.tasks.rename, body["id"], body["title"])
        self.audit({"event": "task-rename", "id": out["id"], "chars": len(out["title"])})
        return out

    def task_stop(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"id"}:
            raise BridgeError(400, "body must be {id}")
        out = self._task(self.tasks.stop, body["id"])
        self.audit({"event": "task-stop", "id": out["id"]})
        return out

    def task_close(self, body: object, require_clear: bool = False) -> dict:
        """`require_clear`: the store refuses any close, merge or not, while a blocker stands,
        judged under its own locks (the phone: it never lands or sets aside past one)."""
        if not isinstance(body, dict) or not {"id", "action"} <= set(body) <= {"id", "action", "force"}:
            raise BridgeError(400, "body must be {id, action[, force]}")
        force = body.get("force", False)       # merge past the evidence's blockers (the page asks twice)
        if not isinstance(force, bool):
            raise BridgeError(400, "force must be true or false")
        out = self._task(self.tasks.close, body["id"], body["action"], force, require_clear)
        self.audit({"event": "task-" + str(body["action"]), "id": out["id"], "status": out["status"], "force": force})
        return out

    def task_blockers(self, tid: object) -> list[str]:
        """What the evidence says against landing this thread now; [] when nothing does. since
        is past any log, so only the summary is read."""
        got = self._task(self.tasks.get, tid, None, 1 << 40)
        return list(got.get("merge_blockers") or [])

    # ---- phone access: the second listener, and the loopback-only calls that pair and list ----
    def start_remote(self) -> bool:
        """Open the phone listener when "remote" is set and allowed. The loopback bridge runs
        either way; a refusal is printed, audited and shown by GET /phone, never silent.
        True when it is listening on return. If the bind fails (a Tailscale 100.x address does
        not exist yet when the service starts at boot, the port is held for a moment), a thread
        of its own tries again every REMOTE_RETRY_SECONDS until it works: this call never waits
        for that, and the loopback listener is already serving."""
        access = self.remote
        access.configure(self.port)
        if access.refused:
            self._remote_note("remote-refused", access.refused)
            return False
        settings = access.settings
        if settings is None:
            return False
        access.stopping.clear()
        with access.lock:
            if self._bind_remote(settings):
                return True
        access.retry = threading.Thread(target=self._retry_remote, name="phone-bind-retry",
                                        daemon=True)
        access.retry.start()
        return False

    def _bind_remote(self, settings) -> bool:
        """One attempt to open the listener (access.lock held). A failure is printed and audited
        once per kind (the errno), however often it repeats; the error text stays on the access
        object for GET /phone and `container-map pair`."""
        access = self.remote
        try:
            server = RemoteHTTPServer((settings.listen, settings.port), RemoteHandler, self)
        except OSError as exc:
            kind = errno.errorcode.get(exc.errno or 0, type(exc).__name__)
            access.error = (f"cannot listen on {settings.listen}:{settings.port}: "
                            f"{exc.strerror or exc}; trying again every "
                            f"{int(REMOTE_RETRY_SECONDS)} s")
            if kind not in access.reported:
                access.reported.add(kind)
                self._remote_note("remote-failed", access.error)
            return False
        access.error = ""
        access.server = server
        access.thread = threading.Thread(target=server.serve_forever, name="phone-listener",
                                         daemon=True)
        access.thread.start()
        where = f"{settings.listen}:{server.server_port} as {settings.origin}"
        print(f"phone access: listening on {where}", flush=True)
        if not settings.https and ipaddress.ip_address(settings.listen) not in TAILNET:
            print("phone access: allow_http is on outside the Tailscale range: the device "
                  "cookie travels in clear text", file=sys.stderr, flush=True)
        self.audit({"event": "remote-listening", "listen": settings.listen,
                    "port": server.server_port, "origin": settings.origin})
        return True

    def _retry_remote(self) -> None:
        access = self.remote
        while not access.stopping.wait(REMOTE_RETRY_SECONDS):
            with access.lock:
                if access.stopping.is_set() or access.settings is None:
                    return
                if self._bind_remote(access.settings):
                    return

    def _remote_note(self, event: str, why: str) -> None:
        print(f"phone access: not started: {why}", file=sys.stderr, flush=True)
        try:
            self.audit({"event": event, "why": why[:300]})
        except OSError:
            pass

    def stop_remote(self, timeout: float = 5.0) -> None:
        access = self.remote
        access.stopping.set()                 # the retry thread leaves at its next look
        with access.lock:
            server, thread, access.server, access.thread = access.server, access.thread, None, None
        retry, access.retry = access.retry, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout)
        if retry is not None and retry is not threading.current_thread():
            retry.join(timeout)

    def phone_status(self) -> dict:
        access, settings = self.remote, self.remote.settings
        return {"enabled": settings is not None, "listening": access.listening,
                "public_origin": settings.origin if settings else "",
                "error": access.refused or access.error, "pending": access.codes.pending(),
                "devices": access.devices.listing()}

    def phone_new_code(self, ttl: float | None = None) -> dict:
        access, settings = self.remote, self.remote.settings
        if access.refused:
            raise BridgeError(409, "phone access is refused: " + access.refused)
        if settings is None:
            raise BridgeError(409, 'phone access is off: put a "remote" block in container.json '
                                   "(README: Use it from your phone) and restart the map")
        if not access.listening:
            raise BridgeError(409, "the phone listener is not running: "
                                   + (access.error or "restart the map"))
        code, life = access.codes.create(ttl)
        self.audit({"event": "phone-code", "expires_in": int(life)})
        return {"code": code, "expires_in": int(life), "url": settings.origin + "/pair"}

    def phone_revoke(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"id"}:
            raise BridgeError(400, "body must be {id}")
        ident = body["id"]
        if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{8}", ident):
            raise BridgeError(400, "id must be a device id from the list")
        try:
            gone = self.remote.devices.revoke(ident)
        except OSError as exc:
            raise BridgeError(500, "the device is revoked in memory but its file could not be "
                                   f"written: {exc}") from exc
        if not gone:
            raise BridgeError(404, "no such device")
        self.audit({"event": "phone-revoked", "device": ident})
        return {"revoked": ident}

    def _rulings(self) -> dict:
        try:
            with open(self.rulings_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def resolve_quest(self, body: object) -> dict:
        """The user's ruling on a parked decision: done / stale retire it from the board (the file keeps the
        text and the date), reopen puts it back. The board never retires anything on its own."""
        if not isinstance(body, dict) or set(body) != {"id", "ruling"}:
            raise BridgeError(400, "body must be {id, ruling}")
        ident, ruling = body["id"], body["ruling"]
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", ident):
            raise BridgeError(400, "id must name a board item")
        if ruling not in ("done", "stale", "reopen"):
            raise BridgeError(400, "ruling must be done, stale or reopen")
        world = self.world() if ruling != "reopen" else None    # no render under the file lock
        with self.rulings_lock:
            rulings = self._rulings()
            if ruling == "reopen":
                if ident not in rulings:
                    raise BridgeError(404, "nothing retired under that id")
                rulings.pop(ident)
            else:
                quest = next((q for q in world.get("quests", []) if isinstance(q, dict) and q.get("id") == ident), None)
                items = (world.get("tasks") or {}).get("items") if isinstance(world.get("tasks"), dict) else None
                task = next((t for t in (items or []) if isinstance(t, dict) and t.get("id") == ident), None)
                if quest:
                    rulings[ident] = {"ruling": ruling, "at": utc_stamp(), "src": quest.get("src"),
                                      "days": quest.get("days"), "text": str(quest.get("text", ""))[:300]}
                elif task:
                    rulings[ident] = {"ruling": ruling, "at": utc_stamp(), "src": "task:" + str(task.get("area", "")),
                                      "days": None, "text": str(task.get("finding", ""))[:300]}
                else:
                    raise BridgeError(404, "no such item on the board")
            os.makedirs(os.path.dirname(self.rulings_path) or ".", exist_ok=True)
            temp = self.rulings_path + ".new"
            with open(temp, "w", encoding="utf-8") as handle:
                json.dump(rulings, handle, indent=1, sort_keys=True)
            os.replace(temp, self.rulings_path)
        self.audit({"event": "resolve_quest", "id": ident, "ruling": ruling})
        return {"ok": True, "id": ident, "ruling": ruling, "retired": len(rulings)}

    def check_in(self, body: object, via: str = "") -> dict:
        """The user's own word that the user did a check-in commitment today (or {undo: true} to take today's
        back). Appends one row to the check-ins file; only today, only ids in the user's commitments file
        whose proof is `check-in`. UI session + exact origin only. On one machine the bridge cannot
        tell the user's browser from a local program that fetched the page, so every row records the
        User-Agent that sent it (`via`): a click from anything but a browser is visible as such."""
        if not isinstance(body, dict) or not set(body) <= {"id", "undo"} or "id" not in body:
            raise BridgeError(400, "body must be {id} or {id, undo}")
        ident, undo = body["id"], body.get("undo", False)
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", ident) or not isinstance(undo, bool):
            raise BridgeError(400, "id must name a commitment; undo must be true or false")
        if not (self.commitments_path and self.checkins_path):
            raise BridgeError(400, "set today.commitments_md and today.checkins_jsonl in container.json")
        try:
            with open(self.commitments_path, encoding="utf-8", errors="replace") as handle:
                items = _today.parse_commitments(handle.read())
        except OSError:
            raise BridgeError(404, "commitments file unreadable")
        item = next((c for c in items if c["id"] == ident), None)
        if not item:
            raise BridgeError(404, "no such commitment")
        if not _today.is_checkin(item["proof"]):
            raise BridgeError(400, "that commitment is proved by its file, not by a click")
        row = {"id": ident, "day": datetime.now().date().isoformat(), "at": utc_stamp(),
               "via": " ".join(str(via).split())[:160]}
        if undo:
            row["undo"] = True
        try:
            with self.checkins_lock:
                os.makedirs(os.path.dirname(self.checkins_path) or ".", exist_ok=True)
                with open(self.checkins_path, "a+b") as handle:
                    handle.seek(0, os.SEEK_END)
                    lead = b""
                    if handle.tell():                       # a hand-edited file may have lost its last newline
                        handle.seek(-1, os.SEEK_END)
                        lead = b"" if handle.read(1) == b"\n" else b"\n"
                    handle.write(lead + (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError as exc:
            raise BridgeError(500, "check-in not saved: %s" % (exc.strerror or exc))
        self.audit({"event": "check_in", "id": ident, "undo": undo})
        return {"ok": True, "id": ident, "day": row["day"], "undo": undo}

    def open_file(self, body: object) -> dict:
        """The open-in-editor verb: runs the opener the owner configured in areas.json ("opener":
        ["code", "-g", "{path}"]) with the checked path substituted, detached. No opener → 400."""
        if not isinstance(body, dict) or set(body) != {"path"}:
            raise BridgeError(400, "body must be {path}")
        real = self._checked_file(body["path"])
        if not os.path.exists(real):
            raise BridgeError(404, "no such file")
        try:
            with open(self.areas_path, encoding="utf-8") as handle:
                opener = json.load(handle).get("opener")
        except (OSError, ValueError):
            opener = None
        if not (isinstance(opener, list) and opener and all(isinstance(a, str) and a for a in opener)):
            raise BridgeError(400, "no opener configured: set \"opener\": [\"code\", \"-g\", \"{path}\"] in areas.json")
        argv = [a.replace("{path}", real) for a in opener]
        with self.lock:
            now = time.monotonic()
            if now - self.open_last < OPEN_MIN_GAP:
                raise BridgeError(429, "one open per second")
            self.open_last = now
        self.audit({"event": "open_file", "path": real, "opener": argv[0]})
        try:
            if self.open_runner:
                self.open_runner(argv)
            else:
                subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as exc:
            raise BridgeError(502, f"opener failed: {exc}") from exc
        return {"ok": True, "path": real, "opener": argv[0]}

    def remote_render(self, machine: object) -> dict:
        """Re-render that machine's world THERE, once: a constant command over the fleet hop
        (no request data reaches it). Rate-limited per machine; every run is audited. The new
        world reaches this machine through the sync bus a few seconds later, dated by its own mtime."""
        spec = self.registry.machine(machine)
        if not spec.get("render_cmd"):
            raise BridgeError(400, f"no render_cmd configured for {machine} in seats.json")
        with self.remote_lock:
            now = time.monotonic()
            last = self.render_last.get(machine)
            if last is not None and now - last < RENDER_MIN_GAP:
                raise BridgeError(429, f"{machine} was rendered {int(now - last)}s ago; wait {int(RENDER_MIN_GAP - (now - last))}s")
            if machine in self.render_busy:     # it takes minutes; the gap above starts at its end
                raise BridgeError(429, f"{machine} is rendering now")
            self.render_busy.add(machine)
        try:
            started = datetime.now(timezone.utc).isoformat(timespec="seconds")
            result = self.render_runner(machine, RENDER_TIMEOUT, via=spec["via"],
                                        command=spec["render_cmd"])
            rc = int(getattr(result, "returncode", 1))
            out = str(getattr(result, "stdout", "") or "")
            err = str(getattr(result, "stderr", "") or "")
            lines = out.strip().splitlines() or err.strip().splitlines()
            tail = [line[:200] for line in lines[-3:]]
            self.audit({"event": "remote_render", "machine": machine, "rc": rc,
                        "started_at": started})
            if rc != 0:
                last_line = (tail[-1] if tail else "no output")[:120]
                raise BridgeError(502, f"{machine} renderer exited {rc}: {last_line}")
            with self.remote_lock:
                self.render_last[machine] = time.monotonic()
                self.remote_cache.pop(machine, None)
        finally:
            with self.remote_lock:
                self.render_busy.discard(machine)
        return {"ok": True, "machine": machine, "rc": rc, "started_at": started, "tail": tail,
                "note": "rendered there; the sync bus carries the new world back within about a minute"}

    def _checked_remote_target(self, machine: object, target: object) -> tuple[str, str]:
        self.registry.machine(machine)
        if not isinstance(target, str) or not IDENTIFIER_RE.fullmatch(target):
            raise BridgeError(400, "target must be a herdr pane_id")
        with self.remote_targets_lock:
            known = self.remote_targets.get(machine, set())
        if target not in known:
            raise BridgeError(400, "target was not returned by the last remote state")
        return machine, target

    def remote_pane(self, machine: object, target: object) -> dict:
        machine, target = self._checked_remote_target(machine, target)
        payload, _ = self._remote_hop(machine, {"op": "read", "target": target})
        text = clean_terminal(self._remote_command(machine, payload, "read"))
        text = "\n".join(text.splitlines()[-40:])
        encoded = text.encode("utf-8")
        truncated = len(encoded) > MAX_PANE_BYTES
        if truncated:
            text = encoded[-MAX_PANE_BYTES:].decode("utf-8", "replace")
        return {"machine": machine, "target": target, "text": text,
                "lines": len(text.splitlines()), "truncated": truncated}

    def remote_say(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"machine", "target", "text"}:
            raise BridgeError(400, "body must contain exactly machine, target, and text")
        text = logical_line(body["text"])
        machine, target = self._checked_remote_target(body["machine"], body["target"])
        spec = self.registry.remote_seat(machine, target)
        if not spec or not spec["talk_commands"]:
            raise BridgeError(403, "remote sends are disabled for this seat")
        send_key = f"remote:{machine}:{target}"
        with self.send_lock:
            now = time.monotonic()
            if now - self.last_send.get(send_key, 0.0) < SEND_INTERVAL:
                raise BridgeError(429, "rate limit: one terminal send per second per seat")
            payload, _ = self._remote_hop(machine, {
                "op": "say", "target": target, "text": text, "agent": spec["agent"],
                "talk_commands": list(spec["talk_commands"]),
            })
            guard = payload.get("guard")
            if guard in ("agent_list_failed", "read_failed"):
                name = "agent_list" if guard == "agent_list_failed" else "read"
                self._remote_command(machine, payload, name)
                raise BridgeError(502, f"{machine} remote send guard failed")
            if guard not in (None, "missing_agent", "agent_mismatch", "working", "status", "draft"):
                raise BridgeError(502, f"{machine} remote send response is invalid")

            list_name = "recheck" if "recheck" in payload["commands"] else "agent_list"
            agents = self._remote_agents(machine, self._remote_command(machine, payload, list_name))
            matches = [item for item in agents if item["pane_id"] == target]
            current = matches[0] if len(matches) == 1 else {
                "pane_id": target, "agent": None, "agent_status": "unknown"
            }
            sendable, reason = self._remote_sendability(machine, current)
            if not sendable:
                raise BridgeError(409, reason)
            if "read" not in payload["commands"]:
                raise BridgeError(502, f"{machine} remote send response omitted draft guard")
            read_text = self._remote_command(machine, payload, "read")
            if remote_composer_has_draft(read_text):
                raise BridgeError(409, "remote composer contains an unsent draft")
            if guard is not None:
                raise BridgeError(502, f"{machine} remote send guard was inconsistent")
            self._remote_command(machine, payload, "prompt")
            self.last_send[send_key] = now
            with self.remote_lock:
                self.remote_cache.pop(machine, None)
            self.audit({"event": "remote_say", "machine": machine, "target": target,
                        "agent": current["agent"], "result": "injected",
                        "text_length": len(text),
                        "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
            return {"ok": True, "machine": machine, "target": target, "status": "injected"}

    def _load_world(self) -> dict:
        if self.world_loader:
            world = self.world_loader()
        else:
            try:
                # --cached: the render loop's memory of unchanged folders makes this render cheap
                result = subprocess.run(
                    [sys.executable, self.renderer_path, "--json", "--cached"], capture_output=True,
                    text=True, timeout=30, cwd=os.path.dirname(self.renderer_path), check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise BridgeError(500, "world state failed") from exc
            if result.returncode:
                raise BridgeError(500, "world state failed")
            try:
                world = json.loads(result.stdout)
            except ValueError as exc:
                raise BridgeError(500, "world state is invalid") from exc
        if not isinstance(world, dict) or not isinstance(world.get("chars"), list):
            raise BridgeError(500, "world state is invalid")
        return self._finish_world(json.loads(json.dumps(world)))

    def _finish_world(self, world: dict) -> dict:
        """Stable uids (seats bound to their configured policy) and the control block."""
        try:
            _, live_specs = self._configured_bindings(self.windows())
        except BridgeError:
            live_specs = dict(self.registry.by_window)
        used: set[str] = set()
        for char in world["chars"]:
            if not isinstance(char, dict):
                continue
            kind, name = str(char.get("kind", "unit")), str(char.get("name", "unknown"))
            supplied_uid = char.get("uid")
            uid = supplied_uid if (isinstance(supplied_uid, str) and supplied_uid
                                   and len(supplied_uid) <= 256
                                   and not any(ord(ch) < 32 for ch in supplied_uid)) else None
            if kind == "seat":
                window_id = char.get("window_id")
                spec = live_specs.get(window_id) if isinstance(window_id, str) else None
                if not spec:
                    role_match = re.search(r"tmux window\s+([0-9]+)", str(char.get("role", "")))
                    spec = next((item for item in self.registry.seats
                                 if role_match and role_match.group(1) == item.get("quick_key")), None)
                if spec:
                    uid = spec["uid"]
            uid = uid or f"{kind}:{name}"
            base, suffix = uid, 2
            while uid in used:
                uid, suffix = f"{base}#{suffix}", suffix + 1
            used.add(uid)
            char["uid"] = uid
        world["control"] = {"boot_id": self.boot_id, "endpoint": "/control",
                            "seat_uids": [s["uid"] for s in self.registry.seats],
                            "visit_uids": ([c.get("uid") for c in world["chars"] if isinstance(c, dict)]
                                           + ["world:quest-board"])}
        return world

    def world(self) -> dict:
        """A world rendered now (single-flight): ?fresh=1, and whenever no fresh one is at hand."""
        return self._world_once(None)

    def cached_world(self, max_age: float | None = None) -> dict:
        """The freshest world at hand when it is at most max_age seconds old (default
        WORLD_MAX_AGE): the one inlined in the game.html the render loop last wrote, or the last
        one this bridge rendered, whichever was read later. Else a new render."""
        return self._world_once(WORLD_MAX_AGE if max_age is None else max_age)

    def _read_page(self) -> dict:
        """game.html as the render loop last wrote it (the loop replaces it whole, so a reader
        never sees half a page): {key, sha, html, world, at}. world is the inlined world, finished
        like a render's, or None when the page carries none; at = its mtime, which the loop sets
        to the moment it read the facts. Re-read only when mtime/size/inode moved, re-parsed only
        when the bytes changed (an unchanged cycle only touches the file)."""
        try:
            info = os.stat(self.game_path)
        except OSError as exc:
            raise BridgeError(404, "render game.html first") from exc
        key = (info.st_mtime_ns, info.st_size, info.st_ino)
        with self._page_lock:
            page = self._page
            if page and page["key"] == key:
                return page
            try:
                with open(self.game_path, "rb") as handle:
                    raw = handle.read()
                html = raw.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise BridgeError(404, "render game.html first") from exc
            sha = hashlib.sha256(raw).digest()
            if page and page["sha"] == sha:
                page = dict(page, key=key, at=info.st_mtime)
            else:
                world = None
                match = WORLD_LINE_RE.search(html)
                if match:
                    try:
                        world = json.loads(match.group(1))
                    except ValueError:
                        world = None
                if isinstance(world, dict) and isinstance(world.get("chars"), list):
                    world = self._finish_world(world)
                else:
                    world = None
                page = {"key": key, "sha": sha, "html": html, "world": world, "at": info.st_mtime}
            self._page = page
            return page

    def _fresh_world(self, max_age: float) -> dict | None:
        now = time.time()
        try:
            page = self._read_page()
        except BridgeError:
            page = None
        with self._world_lock:
            best = self._world_cache
        if best and not 0 <= now - best[0] <= max_age:     # wall clock: a jump back is stale too
            best = None
        if (page and page["world"] is not None and 0 <= now - page["at"] <= max_age
                and (best is None or page["at"] >= best[0])):
            best = (page["at"], page["world"])
        return best[1] if best else None

    def _world_once(self, max_age: float | None) -> dict:
        """One render at a time. A caller that arrives while a render runs waits for it and takes
        its result (ten page loads cost one render); callers share the dict and only read it. With
        max_age a world that fresh (the page's or the last render's) is returned without waiting."""
        if max_age is not None:
            fresh = self._fresh_world(max_age)
            if fresh is not None:
                return fresh
        with self._world_lock:
            flight = self._world_flight
            leader = flight is None
            if leader:
                flight = self._world_flight = _Flight()
            else:
                flight.waiters += 1
        if not leader:
            flight.done.wait()
            if flight.error is not None:        # a fresh error per caller, never one object shared
                if isinstance(flight.error, BridgeError):
                    raise BridgeError(flight.error.status, flight.error.message)
                raise BridgeError(500, "world state failed")
            return flight.world
        started = time.time()
        try:
            flight.world = self._load_world()
            return flight.world
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._world_lock:
                if flight.error is None:
                    self._world_cache = (started, flight.world)
                self._world_flight = None
            flight.done.set()

    def _built(self, kind: str, world: dict, page_sha: bytes | None, build):
        """Bytes made from one world (and one page), built once and served until either moves."""
        with self._world_lock:
            hit = self._served.get(kind)
            if hit and hit[0] is world and hit[1] == page_sha:
                return hit[2]
        value = build()
        with self._world_lock:
            self._served[kind] = (world, page_sha, value)
        return value

    def render_page(self, fresh: bool = False, remote: bool = False) -> tuple[bytes, str]:
        page = self._read_page()
        world = self.world() if fresh else self.cached_world()

        def build():
            # a page that can put rooms back (joinRooms) gets each planet's rooms by uid
            sent = _districts.page_world(world) if "function joinRooms(" in page["html"] else world
            # the page is live only because this bridge says so; remote tells it to hide the
            # desktop-only controls
            if remote:
                sent = phone_world(sent, private=self.phone_private_ids())
            flags = safe_inline_json({"live": True, "remote": remote})
            boot = "window.CM_BRIDGE = " + flags + ";\n"
            replacement = boot + "const W = " + safe_inline_json(sent) + ";"
            html, count = WORLD_LINE_RE.subn(lambda _: replacement, page["html"], count=1)
            if count != 1:
                raise BridgeError(500, "game.html has no replaceable world bootstrap")
            # every inline <script> and <style> carries the nonce; split there once
            bounds = [0] + nonce_cuts(html) + [len(html)]
            return [html[a:b].encode("utf-8") for a, b in zip(bounds, bounds[1:])]

        parts = self._built("page-remote" if remote else "page", world, page["sha"], build)
        nonce = secrets.token_urlsafe(18)
        return f' nonce="{nonce}"'.encode().join(parts), nonce

    def vendor_asset(self, name: str) -> tuple[bytes, bytes] | None:
        """GET /vendor/<name>: one of the 3D view's two scripts (vendor3d.py), as (raw, gzip), or None. Its own
        slot, apart from the page's and /state's: each name is built, checked against its digest and packed once,
        then kept (a name is its content, so it never goes stale). At most VENDOR_KEEP names are kept."""
        with self._vendor_lock:
            hit = self._vendor.get(name)
        if hit is not None:
            return hit
        import vendor3d as _vendor3d
        text = _vendor3d.serve(name, os.path.dirname(os.path.abspath(self.game_path)), HERE,
                               _CFG.get("skins_dir") or "")
        if text is None:
            return None
        raw = text.encode("utf-8")
        value = (raw, gzip.compress(raw, compresslevel=6, mtime=0))
        with self._vendor_lock:
            if len(self._vendor) >= VENDOR_KEEP:
                self._vendor.pop(next(iter(self._vendor)))
            self._vendor[name] = value
        return value

    def gzipped(self, data: bytes, keep: bool = False) -> bytes:
        """data as one gzip member (no timestamp). keep: the newest result is remembered by the
        identity of `data`, which _built hands out again until the world moves."""
        if keep:
            with self._gz_lock:
                hit = self._gz_last
            if hit is not None and hit[0] is data:
                return hit[1]
        packed = gzip.compress(data, compresslevel=6, mtime=0)
        if keep:
            with self._gz_lock:
                self._gz_last = (data, packed)
        return packed

    def state_bytes(self, fresh: bool = False, remote: bool = False) -> bytes:
        """GET /state: the world as the page reads it, serialised once per world. Only a page
        that can put rooms back (joinRooms) asks for /state, but the page on disk decides it."""
        world = self.world() if fresh else self.cached_world()
        try:
            joins = "function joinRooms(" in self._read_page()["html"]
        except BridgeError:
            joins = False
        return self._built(("state" if joins else "state-full") + ("-remote" if remote else ""), world, None, lambda: json.dumps(
            phone_world(_districts.page_world(world) if joins else world, private=self.phone_private_ids()) if remote else
            (_districts.page_world(world) if joins else world),
            separators=(",", ":")).encode("utf-8"))

    def target_summaries(self) -> list[dict]:
        result = []
        for spec in self.registry.seats:
            try:
                target = self.resolve(spec["id"], configured_only=True)
                sendable, reason = self.sendability(target)
                result.append({"uid": spec["uid"], "id": spec["id"], "role": spec["role"],
                               "quick_key": spec.get("quick_key"), "window_id": target["window_id"],
                               "name": target["name"], "command": target["command"],
                               "live": True, "sendable": sendable, "reason": reason or None})
            except BridgeError as exc:
                result.append({"uid": spec["uid"], "id": spec["id"], "role": spec["role"],
                               "quick_key": spec.get("quick_key"), "window_id": spec.get("window_id"),
                               "name": spec["expected_name"], "command": None, "live": False,
                               "sendable": False, "reason": exc.message})
        return result

    def say(self, body: object) -> dict:
        if not isinstance(body, dict) or set(body) != {"seat", "text", "revision"}:
            raise BridgeError(400, "body must contain exactly seat, text, and revision")
        if not isinstance(body["revision"], str) or not body["revision"]:
            raise BridgeError(400, "revision is required")
        text = logical_line(body["text"])
        with self.send_lock:
            first = self.resolve(body["seat"], configured_only=True)
            sendable, reason = self.sendability(first)
            if not sendable:
                raise BridgeError(403 if not first["spec"]["talk_commands"] else 409, reason)
            snapshot = self.pane_snapshot(first["spec"]["id"], 40)
            if snapshot["revision"] != body["revision"]:
                raise BridgeError(409, "pane revision changed; inspect it again")
            if snapshot["terminal_state"] != "empty":
                raise BridgeError(409, "target composer is not provably empty")
            now = time.monotonic()
            if now - self.last_send.get(first["uid"], 0.0) < SEND_INTERVAL:
                raise BridgeError(429, "rate limit: one terminal send per second per seat")
            second = self.resolve(first["spec"]["id"], configured_only=True)
            if self.identity(first) != self.identity(second):
                raise BridgeError(409, "target pane identity changed")
            visible, _ = self.capture(second)
            state, region = composer_probe(second["command"], visible)
            check = hashlib.sha256(json.dumps([*self.identity(second), state, region]).encode()).hexdigest()
            if state != "empty" or check != body["revision"]:
                raise BridgeError(409, "target composer changed before send")
            typed = self._tmux(["send-keys", "-t", second["pane_id"], "-l", "--", *tmux_literal(text)])
            if typed.returncode:
                raise BridgeError(502, "tmux literal send failed")
            entered = self._tmux(["send-keys", "-t", second["pane_id"], "Enter"])
            if entered.returncode:
                raise BridgeError(502, "text was typed but Enter could not be sent")
            self.last_send[first["uid"]] = now
            self.audit({"event": "say", "seat_uid": first["uid"], "result": "injected",
                        "text_length": len(text),
                        "text_sha256": hashlib.sha256(text.encode()).hexdigest()})
            return {"ok": True, "seat_uid": first["uid"], "status": "injected"}

    def permit(self, body: object) -> dict:
        """Human Allow/Deny for a pane that is already waiting on a printed prompt."""
        if not isinstance(body, dict) or set(body) != {"seat", "decision", "revision"}:
            raise BridgeError(400, "body must contain exactly seat, decision, and revision")
        if body["decision"] not in ("allow", "deny"):
            raise BridgeError(400, "decision must be allow or deny")
        if not isinstance(body["revision"], str) or not body["revision"]:
            raise BridgeError(400, "revision is required")
        with self.send_lock:
            first = self.resolve(body["seat"], configured_only=True)
            sendable, reason = self.sendability(first)
            if not sendable:
                raise BridgeError(403 if not first["spec"]["talk_commands"] else 409, reason)
            snapshot = self.pane_snapshot(first["spec"]["id"], 40)
            if snapshot["revision"] != body["revision"]:
                raise BridgeError(409, "pane revision changed; inspect it again")
            prompt = snapshot.get("permit")
            if not snapshot.get("waiting") or not prompt:
                raise BridgeError(409, "pane is not waiting on a permission prompt")
            if snapshot["terminal_state"] == "dirty" and not MENU_RE.search(snapshot.get("text") or ""):
                raise BridgeError(409, "composer holds an unsent human draft")
            key = prompt["allow"] if body["decision"] == "allow" else prompt.get("deny")
            if key is None:
                raise BridgeError(409, "this prompt has no deny key")
            now = time.monotonic()
            if now - self.last_send.get(first["uid"], 0.0) < SEND_INTERVAL:
                raise BridgeError(429, "rate limit: one terminal send per second per seat")
            second = self.resolve(first["spec"]["id"], configured_only=True)
            if self.identity(first) != self.identity(second):
                raise BridgeError(409, "target pane identity changed")
            visible, _ = self.capture(second)
            fresh = waiting_prompt(visible)
            state, region = composer_probe(second["command"], visible)
            check = hashlib.sha256(json.dumps([*self.identity(second), state, region]).encode()).hexdigest()
            if check != body["revision"] or not fresh:
                raise BridgeError(409, "prompt changed before permit")
            if key:
                typed = self._tmux(["send-keys", "-t", second["pane_id"], "-l", "--", key])
                if typed.returncode:
                    raise BridgeError(502, "tmux literal send failed")
            entered = self._tmux(["send-keys", "-t", second["pane_id"], "Enter"])
            if entered.returncode:
                raise BridgeError(502, "permit key was typed but Enter could not be sent")
            self.last_send[first["uid"]] = now
            self.audit({"event": "permit", "seat_uid": first["uid"], "result": "injected",
                        "decision": body["decision"], "key_length": len(key)})
            return {"ok": True, "seat_uid": first["uid"], "status": "injected",
                    "decision": body["decision"]}

    def _validate_control(self, body: object) -> tuple[str, dict]:
        if not isinstance(body, dict) or set(body) != {"request_id", "action"}:
            raise BridgeError(400, "body must contain exactly request_id and action")
        request_id, action = body["request_id"], body["action"]
        if not isinstance(request_id, str) or not REQUEST_ID_RE.fullmatch(request_id):
            raise BridgeError(400, "request_id format is invalid")
        if not isinstance(action, dict) or not isinstance(action.get("type"), str):
            raise BridgeError(400, "action must be an object with a type")
        kind = action["type"]
        expected = {"move": {"type", "direction", "tiles"},
                    "visit": {"type", "target"},
                    "compose": {"type", "target", "text"},
                    "dismiss": {"type"}, "stop": {"type"},
                    "focus": {"type", "target"}, "follow": {"type", "workflow"}}
        if kind == "point":         # an outlined arrow at a place, its caption in the status bar
            if not {"type", "target"} <= set(action) <= {"type", "target", "text", "seconds", "by"}:
                raise BridgeError(400, "point takes target[, text, seconds, by]")
            return request_id, self._point(action)
        # focus and follow may name their author too (the toast "Claude Code wants to show you SITE")
        fields = set(action) - {"by"} if kind in ("focus", "follow") else set(action)
        if kind not in expected or fields != expected[kind]:
            raise BridgeError(400, "unknown action or unexpected action fields")
        if kind == "focus":         # the page centres a planet (the owner's "Agents can move my view" decides)
            target = self._anchor(self._world_at_hand())(action["target"]) if isinstance(action["target"], str) else None
            if target is None:
                raise BridgeError(400, "focus target is not on the map (a uid such as area:<name>, or place:<name>)")
            return request_id, {"type": "focus", "target": target, "by": _control_author(action.get("by"))}
        if kind == "follow":        # the page selects a workflow and keeps it in view
            wid = action["workflow"]
            if not isinstance(wid, str) or not _workflows.ID_RE.fullmatch(wid):
                raise BridgeError(400, "follow names a workflow id (w-YYYYMMDD-HHMM-...)")
            try:
                self.workflows.summary(wid)
            except _workflows.WorkflowError as exc:
                if self._derived(wid) is None:
                    raise BridgeError(400, "follow names no workflow on this map") from exc
            return request_id, {"type": "follow", "workflow": wid, "by": _control_author(action.get("by"))}
        if kind == "move":
            if action["direction"] not in ("up", "down", "left", "right"):
                raise BridgeError(400, "direction must be up, down, left, or right")
            if isinstance(action["tiles"], bool) or not isinstance(action["tiles"], int) or not 1 <= action["tiles"] <= 12:
                raise BridgeError(400, "tiles must be an integer from 1 through 12")
        elif kind == "visit":
            world = self.world()
            targets = set(world.get("control", {}).get("visit_uids", []))
            if not isinstance(action["target"], str) or action["target"] not in targets:
                raise BridgeError(400, "visit target is not a current WORLD uid")
        elif kind == "compose":
            logical_line(action["text"])
            if not isinstance(action["target"], str):
                raise BridgeError(400, "compose target must be a configured seat uid")
            spec = self.registry.configured(action["target"])
            if not spec or not spec["talk_commands"]:
                raise BridgeError(400, "compose target must be a configured TUI seat uid")
            action = dict(action, target=spec["uid"])
        return request_id, action

    def _point(self, action: dict) -> dict:
        target = self._anchor(self._world_at_hand())(action["target"]) if isinstance(action["target"], str) else None
        if target is None:
            raise BridgeError(400, "point target is not on the map (a uid such as area:<name>, or place:<name>)")
        out = {"type": "point", "target": target}
        text = action.get("text")
        if text not in (None, ""):
            logical_line(text)
            if len(text) > 80 or any(ch in _workflows.BIDI for ch in text):
                raise BridgeError(400, "point text is one plain line of at most 80 characters")
            out["text"] = text
        seconds = action.get("seconds", POINT_SECONDS[2])
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not POINT_SECONDS[0] <= seconds <= POINT_SECONDS[1]:
            raise BridgeError(400, f"seconds is a whole number from {POINT_SECONDS[0]} to {POINT_SECONDS[1]}")
        out["seconds"] = seconds
        out["by"] = _control_author(action.get("by"))
        return out

    def enqueue(self, body: object) -> tuple[int, dict]:
        request_id, action = self._validate_control(body)
        action_key = json.dumps(action, sort_keys=True, separators=(",", ":"))
        with self.lock:
            old = self.requests.get(request_id)
            if old:
                if old[0] != action_key:
                    raise BridgeError(409, "request_id was already used for another action")
                return 200, old[1]
            now = time.time()
            self._prune(now)
            if time.monotonic() - self.last_control < CONTROL_INTERVAL:
                raise BridgeError(429, "control rate limit")
            if len(self.queue) >= QUEUE_LIMIT:
                raise BridgeError(429, "control queue is full")
            if action["type"] == "point":      # the cap is looked at only for a new request (a retry was answered above)
                with self.points_lock:         # and the pointer is taken in the same breath, so two cannot take the last
                    self.points = [t for t in self.points if t > now]
                    if len(self.points) >= POINT_LIVE:
                        raise BridgeError(429, f"{POINT_LIVE} pointers are up; wait for one to end")
                    self.points.append(now + action["seconds"])
            self.sequence += 1
            command = {"sequence": self.sequence, "request_id": request_id,
                       "created_at": utc_stamp(now), "expires_at": utc_stamp(now + QUEUE_TTL),
                       "action": action}
            self.queue.append(dict(command, _expires=now + QUEUE_TTL))
            response = {"ok": True, "boot_id": self.boot_id, "sequence": self.sequence,
                        "status": "queued", "command": command}
            self.requests[request_id] = (action_key, response)
            self.last_control = time.monotonic()
        if action["type"] in ("point", "focus", "follow"):
            self.audit({"event": "control-" + action["type"], "request_id": request_id,
                        "target": action.get("target") or action.get("workflow"), "by": action.get("by")})
        return 202, response

    def _prune(self, now: float | None = None):
        now = now or time.time()
        self.queue[:] = [item for item in self.queue if item["_expires"] > now]
        live_ids = {item["request_id"] for item in self.queue}
        self.requests = {key: value for key, value in self.requests.items() if key in live_ids}

    def note_ui_poll(self, after: int) -> None:
        with self.poll_lock:
            self.ui_poll = {"after": after, "at": time.time(),
                            "count": self.ui_poll.get("count", 0) + 1}

    def control_state(self, after: int) -> dict:
        with self.poll_lock:
            poll = self.ui_poll
        watchers = {"ui_last_after": poll.get("after"), "ui_polls": poll.get("count", 0),
                    "ui_seen_s_ago": (round(time.time() - poll["at"], 1) if poll.get("at")
                                      else None)}
        targets = self.target_summaries()          # tmux calls: never under the queue lock
        with self.lock:
            self._prune()
            public = [{k: v for k, v in item.items() if k != "_expires"} for item in self.queue]
            oldest = public[0]["sequence"] if public else self.sequence + 1
            commands = [item for item in public if item["sequence"] > after]
            return {"ok": True, "boot_id": self.boot_id, "after": after, "watchers": watchers,
                    "reset_required": bool(public and after < oldest - 1),
                    "queue": {"oldest_sequence": oldest, "latest_sequence": self.sequence,
                              "limit": QUEUE_LIMIT, "ttl_seconds": int(QUEUE_TTL)},
                    "commands": commands, "targets": targets}

    def audit(self, row: dict):
        data = json.dumps(dict(row, timestamp=utc_stamp()), separators=(",", ":")) + "\n"
        with self.audit_lock:
            fd = os.open(self.log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.fchmod(fd, 0o600)
                os.write(fd, data.encode("utf-8"))
            finally:
                os.close(fd)

    def client_gone(self, where: str = "") -> None:
        """The page gave up on a slow answer and closed its socket: one short line in the log."""
        try:
            self.audit({"event": "client-gone", "where": where[:120]})
        except OSError:
            pass

    def write_live(self):
        payload = {"version": 1, "boot_id": self.boot_id, "pid": os.getpid(),
                   "port": self.port, "agent_token": self.agent_token,
                   "control_url": f"http://{LOOPBACK}:{self.port}/control"}
        if self.ui_lock:
            payload["ui_lock"] = True
            payload["launch_key"] = self.launch_key
        if _CFG.get("work_root"):
            # which map this is: the legacy and per-user files are shared by every map that falls
            # back to them, and `install` must not take another map's bridge for this root's
            payload["root"] = os.path.realpath(_CFG["work_root"])
        data = json.dumps(payload, separators=(",", ":")).encode()

        def put(path):
            os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
            temp = path + ".new." + self.boot_id
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, path)
            os.chmod(path, 0o600)

        self.keep_out_of_git(os.path.dirname(self.live_path))
        put(self.live_path)
        for path in self.live_mirrors:      # for hooks that still read the old place: best effort
            try:
                put(path)
            except OSError:
                pass

    @staticmethod
    def keep_out_of_git(run):
        """ROOT/.container-map/run holds the agent token and sits inside the owner's folder, maybe
        inside their repo (a .container-map from before this existed has no exclude): a `*`
        .gitignore of its own keeps `git add .` and a thread's diff from ever taking the token
        in, and a .container-map this makes is hidden from the repo around it."""
        if os.path.basename(os.path.dirname(run)) != ".container-map" \
                or os.path.basename(run) != "run":
            return
        try:
            _config.make_state_dir(run, 0o700)
            fd = os.open(os.path.join(run, ".gitignore"), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                         0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("*\n")
        except OSError:     # already there, or a folder put() will report as unwritable
            pass

    def remove_live(self):
        """Only the primary file goes at a clean stop. A mirror (the old .bridge-live) stays where
        it is, stale: its readers check the pid, and deleting it would end the mirroring for good
        (it is only kept up while it exists)."""
        try:
            with open(self.live_path, encoding="utf-8") as handle:
                current = json.load(handle)
            if current.get("boot_id") == self.boot_id:
                os.unlink(self.live_path)
        except (OSError, ValueError):
            pass


class BridgeHTTPServer(ThreadingHTTPServer):
    """One daemon thread per request, so a slow render or diff never holds up the page, the wingmate
    or the agent API. Everything the handlers share is guarded in BridgeApp and tasks.TaskStore."""
    allow_reuse_address = True
    daemon_threads = True
    # listen backlog: socketserver's default of 5 drops the SYN of a sixth simultaneous connect (a
    # page load opens several at once) and the client retries a full second later
    request_queue_size = 64

    def __init__(self, address, handler, app: BridgeApp):
        self.app = app
        super().__init__(address, handler)

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            self.app.client_gone()
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server: BridgeHTTPServer
    timeout = 30                       # a client that stalls mid-request is dropped; the map keeps answering

    def log_message(self, *_):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            # the page gave up on a slow answer and closed the socket: one log line, not a traceback
            self.close_connection = True
            path = urlsplit(getattr(self, "path", "") or "").path
            self.app.client_gone(f"{getattr(self, 'command', '') or ''} {path}".strip())

    @property
    def app(self) -> BridgeApp:
        return self.server.app

    def _frame_sources(self) -> str:
        """The page's frame-src: exactly the path of the agent-page frame, on each name this loopback listener answers
        to (a CSP source with a path matches that path and no other). The phone's listener has no such frame."""
        return " ".join(f"http://{name}:{self.server.server_port}{PAGE_FRAME_PATH}" for name in LOOPBACK_NAMES)

    def _security_headers(self, nonce: str | None = None):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if nonce:
            handler_hash = base64.b64encode(hashlib.sha256(b"closeAll()").digest()).decode()
            policy = ("default-src 'none'; connect-src 'self'; img-src 'self' data:; "
                      "font-src data:; "                # the inlined Silkscreen face is a data: URI
                      f"frame-src {self._frame_sources()}; "        # the one document an agent's page is shown in
                      f"script-src 'nonce-{nonce}' 'unsafe-hashes' 'sha256-{handler_hash}'; "
                      "style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; "
                      "form-action 'self'")
            self.send_header("Content-Security-Policy", policy)

    def _term_stream(self, seat: str):
        """GET /term: the pane's screen as server-sent events named "term" (one JSON object each) until the page
        closes it or term_events ends. The first frame is read before the headers go out, so a seat that is not
        there is a plain JSON error."""
        events = self.app.term_events(seat)
        first = next(events)
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for event in itertools.chain([first], events):
                # a named event: the page listens for "term", never for "message" (test_game keeps the page free
                # of message listeners, the agent page frame's channel)
                self.wfile.write(b"event: term\ndata: " + json.dumps(event, separators=(",", ":")).encode("utf-8")
                                 + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass
        finally:
            events.close()

    def _json(self, status: int, value: object):
        self._raw_json(status, json.dumps(value, separators=(",", ":")).encode("utf-8"))

    def _accepts_gzip(self) -> bool:
        """Whether the client's Accept-Encoding takes gzip (q > 0; an explicit gzip beats "*")."""
        wanted: dict[str, float] = {}
        for line in self.headers.get_all("Accept-Encoding") or []:
            for item in line.split(","):
                name, _, params = item.strip().partition(";")
                quality = 1.0
                for param in params.split(";"):
                    key, _, value = param.strip().partition("=")
                    if key.strip().lower() == "q":
                        try:
                            quality = float(value)
                        except ValueError:
                            quality = 0.0
                wanted[name.strip().lower()] = quality
        explicit = wanted.get("gzip", wanted.get("x-gzip"))
        return (explicit if explicit is not None else wanted.get("*", 0.0)) > 0

    def _compressed(self, data: bytes, keep: bool = False) -> bytes:
        """The body to send: gzip when the client takes it and the body is worth it. Call between
        send_response and end_headers, and send the length of what this returns."""
        self.send_header("Vary", "Accept-Encoding")
        if len(data) < GZIP_MIN or not self._accepts_gzip():
            return data
        self.send_header("Content-Encoding", "gzip")
        return self.app.gzipped(data, keep)

    def _frame(self, html: str):
        """An agent's page as the document of its frame, with headers of its own: no X-Frame-Options (the policy's
        frame-ancestors says who may frame it), no map policy, no cookie set."""
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", PAGE_FRAME_CSP)
        self.send_header("Connection-Allowlist", PAGE_FRAME_ALLOWLIST)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _raw_json(self, status: int, data: bytes, compress: bool = False):
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if compress:                       # /state: polled by a phone, built once per world
            data = self._compressed(data, keep=True)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _vendor(self, url):
        """GET /vendor/<name>: a 3D script, cached for good (its name is its digest), as JavaScript, gzipped from its
        own slot. No query; an unknown name is a 404."""
        if url.query:
            raise BridgeError(400, "/vendor/ takes no query parameters")
        name = url.path[len(VENDOR_PREFIX):]
        if name.startswith("cm3dp-"):
            return self._vendor_map(name)
        found = self.app.vendor_asset(name)
        if found is None:
            raise BridgeError(404, "not found")
        raw, packed = found
        self.send_response(200)
        self.send_header("Cache-Control", VENDOR_CACHE)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")   # the browser refuses it to any other site
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Vary", "Accept-Encoding")
        for name, value in getattr(self, "_more", ()):
            self.send_header(name, value)
        data = raw
        if self._accepts_gzip():
            self.send_header("Content-Encoding", "gzip")
            data = packed
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _vendor_map(self, name: str):
        """GET /vendor/cm3dp-<skin>-<sha8>.<ext>: one map of a skin's realistic planets (vendor3d.serve_map), cached
        for good like the scripts; already compressed, so sent as it is. Read from disk each time (a browser asks once)."""
        import vendor3d as _vendor3d
        found = _vendor3d.serve_map(name, HERE, _CFG.get("skins_dir") or "")
        if found is None:
            raise BridgeError(404, "not found")
        data, media = found
        self.send_response(200)
        self.send_header("Cache-Control", VENDOR_CACHE)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Type", media)
        for header, value in getattr(self, "_more", ()):
            self.send_header(header, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, message: str):
        self._json(status, {"error": message})

    def _host_ok(self) -> bool:
        # the same loopback page reached by either name; anything else is misdirected
        got = self.headers.get("Host", "")
        return any(_same(got, f"{h}:{self.server.server_port}") for h in LOOPBACK_NAMES)

    def _origin_exact(self) -> bool:
        got = self.headers.get("Origin", "")
        return any(_same(got, f"http://{h}:{self.server.server_port}") for h in LOOPBACK_NAMES)

    def _origin_if_present(self) -> bool:
        origin = self.headers.get("Origin")
        return not origin or self._origin_exact()

    def _browser_fetch(self) -> bool:
        """Browsers send Fetch Metadata on every request; a local script has to forge it on purpose."""
        return (self.headers.get("Sec-Fetch-Site", "") == "same-origin"
                and self.headers.get("Sec-Fetch-Mode", "") in ("cors", "same-origin", "navigate"))

    def _ui_ok(self) -> bool:
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            value = jar.get(COOKIE_NAME).value if jar.get(COOKIE_NAME) else ""
        except cookies.CookieError:
            value = ""
        return _same(value, self.app.ui_token)

    def _ui_cookie(self) -> str:
        # Lax when locked: the launch link is opened from a terminal, a chat or a note, and a Strict cookie is withheld
        # on the redirect that follows a cross-site click (the visitor would land on the lock page again). Lax still
        # keeps it off every cross-site POST, and each change also needs the exact Origin and same-origin Fetch Metadata.
        same_site = "Lax" if self.app.ui_lock else "Strict"
        return f"{COOKIE_NAME}={self.app.ui_token}; HttpOnly; SameSite={same_site}; Path=/"

    def _locked(self, url):
        """The map is locked and this request has no UI session: the launch key in ?k= opens one (and a redirect
        takes the key out of the address bar); anything else gets the plain lock page."""
        if not self._origin_if_present():
            raise BridgeError(403, "forbidden origin")
        given = parse_qs(url.query, keep_blank_values=True)
        keys = given.get("k") or []
        if len(given) == 1 and len(keys) == 1 and _same(keys[0], self.app.launch_key):
            self.send_response(303)
            self._security_headers()
            self.send_header("Set-Cookie", self._ui_cookie())
            self.send_header("Location", url.path)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        data = LOCK_PAGE.encode("utf-8")
        self.send_response(403)
        self._security_headers()
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _agent_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        return header.startswith(prefix) and _same(header[len(prefix):], self.app.agent_token)

    def _body(self, limit: int = MAX_BODY) -> object:
        if self.headers.get_content_type() != "application/json":
            raise BridgeError(415, "Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as exc:
            raise BridgeError(411, "valid Content-Length required") from exc
        if length < 0 or length > limit:
            raise BridgeError(413, "request body is too large")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as exc:   # 16000 nested '[' is a RecursionError
            raise BridgeError(400, "request body is not valid JSON") from exc
        if unencodable_text(value):         # "\ud800" parses, and nothing downstream can write it
            raise BridgeError(400, "request body holds text that is not valid Unicode "
                                   "(a lone surrogate)")
        return value

    def _dispatch_error(self, exc: BridgeError):
        self._error(exc.status, exc.message)

    def send_error(self, code, message=None, explain=None):
        """The stdlib answers a bad request line, an unknown method or an oversized header in HTML;
        every failure here is a status with a JSON body of one key."""
        reason = self.responses.get(code, ("error",))[0]
        body = b"" if self.command == "HEAD" else json.dumps(
            {"error": str(message or reason)[:200]}, separators=(",", ":")).encode("utf-8")
        if self.request_version == "HTTP/0.9":      # the request line never parsed: still send a status
            self.request_version = "HTTP/1.0"
        self.send_response(code)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if body:
            self.wfile.write(body)

    def _url(self):
        try:
            return urlsplit(self.path)
        except ValueError as exc:                  # e.g. '//[' : an unterminated IPv6 literal
            raise BridgeError(400, "malformed request path") from exc

    def _internal_error(self, exc: Exception):
        """A failure nobody planned for: one audit line, a JSON 500, the connection closed. The
        client gets no detail; the audit row names the route, the exception and where it was raised."""
        try:
            frame = traceback.extract_tb(exc.__traceback__)[-1]
            self.app.audit({"event": "http-error", "method": str(self.command)[:16],
                            "path": str(self.path).split("?", 1)[0][:200],
                            "error": f"{type(exc).__name__}: {exc}"[:200],
                            "where": f"{os.path.basename(frame.filename)}:{frame.lineno}"})
        except Exception:                          # the audit file must not be the next failure
            pass
        self.close_connection = True
        try:
            self._error(500, "internal error")
        except OSError:                            # the client is already gone
            pass

    def _guarded(self, handler):
        """Host check, then the route. A BridgeError is its own status; anything else is a JSON 500
        (a dropped or stalled client is left to the stdlib, which already handles both)."""
        try:
            if not self._host_ok():
                return self._error(421, "misdirected host")
            handler()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            raise
        except BridgeError as exc:
            self._dispatch_error(exc)
        except Exception as exc:
            self._internal_error(exc)

    def _not_allowed(self):
        self.send_response(405)
        self._security_headers()
        self.send_header("Allow", "GET, POST")
        body = b"" if self.command == "HEAD" else b'{"error":"method not allowed"}'
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_PUT(self):
        self._guarded(self._not_allowed)

    do_DELETE = do_PATCH = do_HEAD = do_PUT

    def do_OPTIONS(self):
        self._guarded(lambda: self._error(403, "cross-origin requests are not supported"))

    def do_GET(self):
        self._guarded(self._get)

    def _get(self):
        url = self._url()
        try:
            if url.path in ("/", "/game.html"):
                if self.app.ui_lock and not self._ui_ok():
                    return self._locked(url)
                if self.app.ui_lock and set(parse_qs(url.query, keep_blank_values=True)) == {"k"}:
                    # the launch link opened again in a browser that is already in: the session it has is enough,
                    # so take the key out of the address bar as the first visit did
                    if not self._origin_if_present():
                        raise BridgeError(403, "forbidden origin")
                    self.send_response(303)
                    self._security_headers()
                    self.send_header("Location", url.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if not (url.query in ("", "fresh=1") or view_query(url.query)) or not self._origin_if_present():
                    raise BridgeError(403, "forbidden origin")
                # served from the page the render loop wrote; ?fresh=1 renders now; the 3D view's own parameters
                # (view_query) are read by the page and change nothing here
                data, nonce = (self.app.render_page(fresh=True) if url.query == "fresh=1"
                               else self.app.render_page())
                self.send_response(200)
                self._security_headers(nonce)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", self._ui_cookie())
                data = self._compressed(data)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if url.path.startswith(VENDOR_PREFIX):
                # a page of another site may not pull these in with a <script src> (no Origin rides on that): when the
                # browser says where the request comes from, it must be this page (or a typed address)
                if (not self._origin_if_present()
                        or self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")):
                    raise BridgeError(403, "forbidden origin")
                return self._vendor(url)
            if url.path == "/verbs":
                if not self._ui_ok():
                    raise BridgeError(403, "UI session required")
                return self._json(200, self.app.list_verbs())
            if url.path == "/state":
                if (url.query not in ("", "fresh=1") or not self._ui_ok()
                        or not self._origin_if_present()):
                    raise BridgeError(403, "UI session required")
                return self._raw_json(200, self.app.state_bytes(fresh=bool(url.query)), compress=True)
            if url.path == "/pane":
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if not set(query) <= {"seat", "lines"} or len(query.get("seat", [])) != 1:
                    raise BridgeError(400, "one seat query parameter is required")
                if any(len(values) != 1 for values in query.values()):
                    raise BridgeError(400, "query parameters must be singular")
                lines = _uint(query.get("lines", ["40"])[0], MAX_PANE_LINES)
                if not lines:
                    raise BridgeError(400, "lines must be an integer from 1 through 200")
                return self._json(200, self.app.pane_snapshot(query["seat"][0], lines))
            if url.path == "/term":
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) != {"seat"} or len(query["seat"]) != 1:
                    raise BridgeError(400, "one seat query parameter is required")
                return self._term_stream(query["seat"][0])
            if url.path == "/remote/state":
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) != {"machine"} or len(query["machine"]) != 1:
                    raise BridgeError(400, "one machine query parameter is required")
                return self._json(200, self.app.remote_state(query["machine"][0]))
            if url.path == "/file":
                # read-only, root-bounded and credential-denied: the card hands this call to agents
                if not (self._agent_ok() or self._ui_ok()) or not self._origin_if_present():
                    raise BridgeError(403, "agent token or UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if set(query) != {"path"} or len(query["path"]) != 1:
                    raise BridgeError(400, "one path query parameter is required")
                return self._json(200, self.app.read_file(query["path"][0]))
            if url.path == "/remote/world":
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if (not {"machine"} <= set(query) <= {"machine", "meta"}
                        or any(len(values) != 1 for values in query.values())):
                    raise BridgeError(400, "one machine query parameter is required")
                return self._json(200, self.app.remote_world(
                    query["machine"][0], meta_only=(query.get("meta", ["0"])[0] == "1")))
            if url.path == "/remote/pane":
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if (set(query) != {"machine", "target"}
                        or any(len(values) != 1 for values in query.values())):
                    raise BridgeError(400, "one machine and target query parameter are required")
                return self._json(200, self.app.remote_pane(
                    query["machine"][0], query["target"][0]))
            if url.path in ("/tasks", "/task", "/task/diff"):
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if url.path == "/tasks":
                    if query:
                        raise BridgeError(400, "/tasks takes no query parameters")
                    return self._json(200, self.app.list_tasks())
                return self._json(200, self.app.task_detail(query) if url.path == "/task" else self.app.task_diff(query))
            if url.path in ("/agent/map", "/agent/unit", "/agent/tasks", "/agent/task"):
                # agents read with their bearer token; the page reads the same data with its session
                if not (self._agent_ok() or self._ui_ok()) or not self._origin_if_present():
                    raise BridgeError(403, "agent token or UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if url.path == "/agent/map":
                    if query:
                        raise BridgeError(400, "/agent/map takes no query parameters")
                    return self._json(200, self.app.agent_map())
                if url.path == "/agent/tasks":
                    if query:
                        raise BridgeError(400, "/agent/tasks takes no query parameters")
                    return self._json(200, self.app.list_tasks())
                if url.path == "/agent/task":
                    return self._json(200, self.app.task_detail(query))
                return self._json(200, self.app.agent_unit(query))
            if url.path in ("/wingmate", "/wingmate/answer"):
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if url.path == "/wingmate":
                    if query:
                        raise BridgeError(400, "/wingmate takes no query parameters")
                    return self._json(200, self.app.mate_info())
                return self._json(200, self.app.mate_answer(query))
            if url.path in ("/meta", "/meta/answer"):
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                query = parse_qs(url.query, keep_blank_values=True)
                if url.path == "/meta":
                    if query:
                        raise BridgeError(400, "/meta takes no query parameters")
                    return self._json(200, self.app.meta_info())
                return self._json(200, self.app.meta_answer(query))
            if url.path in ("/workflows", "/workflow", "/workflow/replay"):
                # the page polls these with its session; agents read them with the bearer token
                if not (self._agent_ok() or self._ui_ok()) or not self._origin_if_present():
                    raise BridgeError(403, "agent token or UI session required")
                return self._json(200, self.app.workflow_read(url.path, parse_qs(url.query, keep_blank_values=True)))
            if url.path == PAGE_FRAME_PATH:
                # an agent's page is for the person's screen: the UI session only, never the agent token
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                return self._frame(self.app.show_frame(parse_qs(url.query, keep_blank_values=True)))
            if url.path == "/control":
                if not (self._agent_ok() or self._ui_ok()) or not self._origin_if_present():
                    raise BridgeError(403, "control authorization required")
                query = parse_qs(url.query, keep_blank_values=True)
                if not set(query) <= {"after"} or any(len(v) != 1 for v in query.values()):
                    raise BridgeError(400, "only one after query parameter is allowed")
                after = _uint(query.get("after", ["0"])[0], 1 << 62)
                if after is None:
                    raise BridgeError(400, "after must be a non-negative integer")
                if self._ui_ok():
                    self.app.note_ui_poll(after)
                return self._json(200, self.app.control_state(after))
            if url.path == "/phone":
                if not self._ui_ok() or not self._origin_if_present():
                    raise BridgeError(403, "UI session required")
                if url.query:
                    raise BridgeError(400, "/phone takes no query parameters")
                return self._json(200, self.app.phone_status())
            raise BridgeError(404, "not found")
        except BridgeError as exc:
            self._dispatch_error(exc)

    def do_POST(self):
        self._guarded(self._post)

    def _post(self):
        url = self._url()
        try:
            if url.query:
                raise BridgeError(400, "POST endpoints do not accept query parameters")
            if url.path == "/say":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.say(self._body()))
            if url.path == "/permit":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.permit(self._body()))
            if url.path == "/keys":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.keys(self._body(TERM_PASTE_MAX * 6 + 1024)))
            if url.path == "/door":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "UI session required")
                return self._json(200, self.app.open_door(self._body()))
            if url.path == "/open":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "UI session required")
                return self._json(200, self.app.open_file(self._body()))
            if url.path == "/dispatch":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "UI session required")
                return self._json(200, self.app.dispatch(self._body()))
            if url.path == "/verb":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "UI session required")
                return self._json(200, self.app.run_verb(self._body()))
            if url.path == "/resolve":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "UI session required")
                return self._json(200, self.app.resolve_quest(self._body()))
            if url.path == "/checkin":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.check_in(self._body(), self.headers.get("User-Agent", "")))
            if url.path in ("/task", "/task/followup", "/task/stop", "/task/close", "/task/check",
                            "/task/rename"):
                # starting an agent is the strongest verb here: exact same-origin UI session only
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                if url.path == "/task/close" and not self._browser_fetch():
                    raise BridgeError(403, "landing a thread is for the page in your browser; agents use /agent/* and leave merging to you")
                if url.path == "/task/rename" and not self._browser_fetch():
                    raise BridgeError(403, "renaming a thread is for the page in your browser; "
                                           "agents use /agent/* and leave titles to you")
                body = self._body(MAX_TASK_BODY)
                return self._json(200, {"/task": self.app.create_task, "/task/followup": self.app.task_followup,
                                        "/task/stop": self.app.task_stop, "/task/close": self.app.task_close,
                                        "/task/check": self.app.task_check,
                                        "/task/rename": self.app.task_rename}[url.path](body))
            if url.path in ("/agent/task", "/agent/task/followup", "/agent/task/check"):
                # an agent hands out and checks threads with its bearer token; landing them stays with the person
                if not self._agent_ok() or not self._origin_if_present():
                    raise BridgeError(403, "agent token required")
                body = self._body(MAX_TASK_BODY)
                fn = {"/agent/task": self.app.create_task, "/agent/task/followup": self.app.task_followup,
                      "/agent/task/check": self.app.task_check}[url.path]
                return self._json(200, fn(body, via="agent"))
            if url.path == "/wingmate/ask":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.mate_ask(self._body(4096)))
            if url.path == "/meta/ask":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.meta_ask(self._body(4096)))
            if url.path in ("/agent/workflow", "/agent/workflow/step", "/agent/workflow/pulse",
                            "/agent/workflow/done", "/agent/workflow/stop"):
                # an agent puts a workflow on the map and reports it: the bearer token, and for an update the
                # workflow's own token too (X-Workflow-Token). No agent route starts, passes a gate or merges.
                if not self._agent_ok() or not self._origin_if_present():
                    raise BridgeError(403, "agent token required")
                body = self._body(MAX_TASK_BODY)
                if url.path == "/agent/workflow":
                    raw = self.headers.get("X-Workflow-Depth", "0").strip() or "0"
                    if not raw.isdigit() or len(raw) > 3:
                        raise BridgeError(400, "X-Workflow-Depth is a whole number (CM_WORKFLOW_DEPTH)")
                    return self._json(201, self.app.workflow_create(body, int(raw)))
                return self._json(200, self.app.workflow_agent(url.path.rsplit("/", 1)[1], body,
                                                               self.headers.get("X-Workflow-Token", "")))
            if url.path in ("/agent/show", "/agent/show/clear"):
                if not self._agent_ok() or not self._origin_if_present():
                    raise BridgeError(403, "agent token required")
                body = self._body(SHOW_PAGE_BODY if url.path == "/agent/show" else MAX_TASK_BODY)
                if url.path == "/agent/show":
                    # a body over the usual limit is taken only for a page that validates (decided in show_put,
                    # before anything is stored)
                    big = int(self.headers.get("Content-Length", "0")) > MAX_TASK_BODY
                    return self._json(200, self.app.show_put(body, oversize=big))
                return self._json(200, self.app.show_clear(body))
            if url.path in WORKFLOW_ROUTES or url.path == "/area/set":
                # the owner's buttons: the page in the user's browser only (session, exact Origin, Fetch Metadata)
                if not self._ui_ok() or not self._origin_exact() or not self._browser_fetch():
                    raise BridgeError(403, "this is a button on the page in your browser; agents use /agent/workflow "
                                           "and leave starting, gates and merging to you")
                body = self._body(MAX_TASK_BODY)
                if url.path == "/area/set":
                    return self._json(200, self.app.area_set(body))
                action = url.path.rsplit("/", 1)[1]
                return self._json(201 if action == "new" else 200, self.app.workflow_owner(action, body))
            if url.path == "/remote/render":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "UI session required")
                body = self._body()
                if not isinstance(body, dict) or set(body) != {"machine"}:
                    raise BridgeError(400, "body must be {machine}")
                return self._json(200, self.app.remote_render(body["machine"]))
            if url.path == "/remote/say":
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                return self._json(200, self.app.remote_say(self._body()))
            if url.path in ("/phone/pair", "/phone/revoke"):
                # pairing a phone hands out control of the map: this page's own session only,
                # never the agent token
                if not self._ui_ok() or not self._origin_exact():
                    raise BridgeError(403, "same-origin UI session required")
                body = self._body()
                if url.path == "/phone/revoke":
                    return self._json(200, self.app.phone_revoke(body))
                ttl = None
                if body:
                    hours = body.get("hours") if isinstance(body, dict) and set(body) == {"hours"} else None
                    if isinstance(hours, bool) or not isinstance(hours, int) or not 1 <= hours <= PAIR_TTL_MAX // 3600:
                        raise BridgeError(400, 'body must be {} or {"hours": 1-168}')
                    ttl = hours * 3600.0
                return self._json(200, self.app.phone_new_code(ttl))
            if url.path == "/control":
                if not self._agent_ok() or not self._origin_if_present():
                    raise BridgeError(403, "agent control token required")
                status, value = self.app.enqueue(self._body())
                return self._json(status, value)
            raise BridgeError(404, "not found")
        except BridgeError as exc:
            self._dispatch_error(exc)


class RemoteHTTPServer(BridgeHTTPServer):
    """The phone listener: the same one-thread-per-request server, bound to remote.listen, with
    a cap on requests served at once (a stranger reaching the port cannot run the machine out
    of threads) and no reverse DNS lookup at bind time."""

    def __init__(self, address, handler, app: BridgeApp):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.slots = threading.BoundedSemaphore(REMOTE_CONNECTIONS)
        super().__init__(address, handler, app)

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_address = self.socket.getsockname()
        self.server_name, self.server_port = self.server_address[:2]

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


PAIR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>Pair this device</title>
<style nonce="__NONCE__">
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b1428;
color:#c8dcf5;font:16px/1.5 ui-monospace,Menlo,Consolas,monospace}
form{width:min(92vw,340px);display:grid;gap:12px}
h1{margin:0;font-size:14px;letter-spacing:.12em;color:#9bacc0}
input,button{height:44px;padding:0 12px;border:1px solid #344c6b;border-radius:0;
background:#080d20;color:#c8dcf5;font:inherit}
#code{font-size:22px;letter-spacing:.2em;text-transform:uppercase}
button{background:#ffd166;border-color:#ffd166;color:#04060c;font-weight:600}
#m{min-height:1.5em;color:#ff8a8a}p{margin:0;color:#9bacc0;font-size:14px}</style></head><body>
<form id="f" autocomplete="off"><h1>CONTAINER MAP</h1>
<p>On the computer: <b>container-map pair</b>, or Pair a phone in the star panel.
Type the code here.</p>
<input id="code" name="code" maxlength="12" autocapitalize="characters" autocorrect="off"
spellcheck="false" placeholder="CODE" required>
<input id="name" name="name" maxlength="40" value="phone" aria-label="device name">
<button type="submit">Pair</button><div id="m" role="status"></div></form>
<script nonce="__NONCE__">
const f=document.getElementById('f'), m=document.getElementById('m');
f.addEventListener('submit', async e=>{ e.preventDefault(); m.textContent='…';
  try{
    const r=await fetch('/pair',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({code:f.code.value,name:f.name.value})});
    let j={}; try{ j=await r.json(); }catch(x){}
    if(r.ok){ location.replace('/'); } else { m.textContent=j.error||('error '+r.status); }
  }catch(x){ m.textContent='could not reach the map'; }
});
</script></body></html>"""


def device_name(value: object) -> str:
    """What the device list calls a phone: printable, short, never empty."""
    text = re.sub(r"[^\w .,'()+-]", "", value if isinstance(value, str) else "")
    return re.sub(r"\s+", " ", text).strip()[:DEVICE_NAME_MAX] or "phone"


class RemoteHandler(Handler):
    """Requests from phones, on the listener opened for the "remote" block. Everything runs
    under the rules of README "Use it from your phone": Host and Origin pinned to
    public_origin, a per-device cookie for everything but /pair, a short route allowlist
    (anything else is a 404), no CORS."""
    timeout = 15
    _more: tuple | list = ()

    def _frame_sources(self) -> str:
        return "'none'"             # a phone is shown the card of an agent's page, never the page

    @property
    def remote(self) -> RemoteAccess:
        return self.app.remote

    def _peer(self) -> str:
        """Who is asking, for the pairing limit, its lockout and the audit. The address of the
        connection, unless that is one of remote.trusted_proxy: every phone then shares the
        proxy's address, so the client is the last hop of X-Forwarded-For (the address the
        proxy itself saw). A missing or malformed header leaves the proxy's address."""
        peer = str(self.client_address[0])
        if self._from_proxy():
            hops = [hop for line in self.headers.get_all("X-Forwarded-For") or []
                    for hop in line.split(",")]
            client = ip_text(hops[-1]) if hops else ""
            if client:
                return client
        return peer

    def _from_proxy(self) -> bool:
        """True when the connection itself comes from one of remote.trusted_proxy."""
        proxies = self.remote.settings.trusted_proxy
        return bool(proxies) and ip_text(str(self.client_address[0])) in proxies

    def _tailnet_login(self) -> str:
        """The tailnet user a trusted proxy named in TAILNET_LOGIN_HEADER, for the audit only.
        "" when the connection is not from remote.trusted_proxy (then the header is the caller's
        own claim), and "" for a header that is missing, sent twice, longer than
        TAILNET_LOGIN_MAX or not printable ASCII without spaces (a MIME-encoded word is
        printable ASCII, so it is kept as it came)."""
        if not self._from_proxy():
            return ""
        values = self.headers.get_all(TAILNET_LOGIN_HEADER) or []
        if len(values) != 1:
            return ""
        login = values[0].strip(" \t")      # only the blanks a header line may pad with
        if not login or len(login) > TAILNET_LOGIN_MAX or any(
                not "!" <= ch <= "~" for ch in login):
            return ""
        return login

    def _with_login(self, row: dict) -> dict:
        """An audit row, plus the tailnet login when a trusted proxy named one."""
        login = self._tailnet_login()
        return dict(row, **{TAILNET_LOGIN_FIELD: login}) if login else row

    def _security_headers(self, nonce: str | None = None):
        super()._security_headers(nonce)
        for name, value in self._more:
            self.send_header(name, value)

    def _host_ok(self) -> bool:
        return _same(self.headers.get("Host", "").lower(), self.remote.settings.authority)

    def _guarded(self, handler):
        self._more = []
        try:
            if not self._host_ok():
                raise Denied(421, "misdirected host", "bad-host")
            handler()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            raise
        except BridgeError as exc:
            self._dispatch_error(exc)
        except Exception as exc:
            self._internal_error(exc)

    def _dispatch_error(self, exc: BridgeError):
        if isinstance(exc, Denied):
            if exc.reason:
                try:
                    route = urlsplit(self.path).path
                except ValueError:
                    route = "?"
                self.remote.audit.add(route, exc.status, exc.reason, self._peer(),
                                      self._tailnet_login())
            if exc.retry:
                self._more = [("Retry-After", str(max(1, math.ceil(exc.retry))))]
        super()._dispatch_error(exc)

    # ---- who is asking ----
    def _token(self) -> str:
        want = self.remote.settings.cookie
        for line in self.headers.get_all("Cookie") or []:
            for part in line.split(";"):
                name, sep, value = part.strip().partition("=")
                if sep and name == want:
                    return value.strip()
        return ""

    def _device(self) -> dict | None:
        return self.remote.devices.find(self._token())

    def _same_site(self) -> None:
        site = self.headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            raise Denied(403, "forbidden origin", "cross-site")

    def _origin_pinned(self, *, required: bool) -> None:
        origin = self.headers.get("Origin")
        if origin is None:
            if required:
                raise Denied(403, "forbidden origin", "no-origin")
        elif not _same(origin, self.remote.settings.origin):
            raise Denied(403, "forbidden origin", "bad-origin")

    def _admit(self, device: dict, *, mutation: bool, page: bool = False) -> None:
        """A paired device's request: its own rate, the pinned Origin (required on every
        mutation) and, for everything a script on the page does, same-origin Fetch Metadata."""
        wait = self.remote.requests.take(device["id"])
        if wait:
            raise Denied(429, "too many requests: wait a moment", "device-rate", wait)
        self._origin_pinned(required=mutation)
        if not page:
            self._same_site()

    def _unknown(self, path: str) -> Denied:
        wanted = any(path.startswith(prefix) for prefix in DESKTOP_ONLY)
        return Denied(404, "not found", "route-not-allowed" if wanted else "")

    def _redirect(self, where: str):
        self.send_response(302)
        self._security_headers()
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookie(self, token: str) -> str:
        # Lax, not Strict: a phone opens the map from links in other apps (chat, notes, the home screen), and
        # Safari withholds a Strict cookie on those navigations, so a paired phone landed on /pair every time.
        # Lax still keeps the cookie off every cross-site POST, and each mutation also needs the exact Origin and
        # same-origin Fetch Metadata (_admit), so the change opens no request forgery.
        settings = self.remote.settings
        line = (f"{settings.cookie}={token}; HttpOnly; SameSite=Lax; Path=/; "
                f"Max-Age={int(DEVICE_IDLE)}")
        return line + ("; Secure" if settings.https else "")

    # ---- GET ----
    def _get(self):
        url = self._url()
        path = url.path
        if path == "/pair":
            return self._pair_page(url)
        if path not in REMOTE_GET and not path.startswith(REMOTE_GET_PREFIXES):
            raise self._unknown(path)
        device = self._device()
        if device is None:
            if path == "/":
                return self._redirect("/pair")          # no page, no world, no cookie
            raise Denied(401, "pair this device first", "no-session")
        self._admit(device, mutation=False, page=(path == "/"))
        if path.startswith(VENDOR_PREFIX):
            return self._vendor(url)
        query = parse_qs(url.query, keep_blank_values=True)
        if path == "/":
            if url.query:
                raise BridgeError(400, "the page takes no query parameters")
            data, nonce = self.app.render_page(remote=True)
            self.send_response(200)
            self._security_headers(nonce)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # a page load keeps the 30 days rolling
            self.send_header("Set-Cookie", self._cookie(self._token()))
            data = self._compressed(data)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path in ("/state", "/tasks", "/wingmate") and url.query:
            raise BridgeError(400, f"{path} takes no query parameters on a phone")
        if path == "/state":
            return self._raw_json(200, self.app.state_bytes(remote=True), compress=True)
        if path == "/tasks":
            private = self.app.phone_private_ids()
            got = self.app.list_tasks(remote=True, private=private)
            got["tasks"] = [phone_task_row(row) for row in got.get("tasks") or []]
            got["workflows"] = strip_paths([phone_workflow_row(w, private) for w in got.get("workflows") or []])
            got["shows"] = strip_paths(phone_shows(got["shows"], private))
            return self._json(200, phone_swap_ids(got, private))
        if path in ("/workflows", "/workflow", "/workflow/replay"):
            private = self.app.phone_private_ids()
            if path != "/workflows":
                self._not_private_workflow((query.get("id") or [""])[0], private)
            got = self.app.workflow_read(path, query)
            if path == "/workflows" and isinstance(got, dict):
                got["workflows"] = [phone_workflow_row(w, private) for w in got.get("workflows") or []]
                got["shows"] = phone_shows(got.get("shows"), private)
            return self._json(200, phone_swap_ids(strip_paths(got), private))
        if path in ("/task", "/task/diff"):
            private = self.app.phone_private_ids()
            self._not_private((query.get("id") or [""])[0], private)
            got = self.app.task_detail(query) if path == "/task" else self.app.task_diff(query)
            return self._json(200, phone_swap_ids(got, private))
        if path == "/wingmate":
            return self._json(200, self.app.mate_info())
        return self._json(200, self.app.mate_answer(query))

    def _pair_page(self, url):
        if url.query:
            raise BridgeError(400, "/pair takes no query parameters")
        if self._device() is not None:
            return self._redirect("/")
        nonce = secrets.token_urlsafe(18)
        data = PAIR_PAGE.replace("__NONCE__", nonce).encode("utf-8")
        self.send_response(200)
        self._security_headers(nonce)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- POST ----
    def _post(self):
        url = self._url()
        path = url.path
        if path != "/pair" and path not in REMOTE_POST:
            raise self._unknown(path)
        if url.query:
            raise BridgeError(400, "POST endpoints do not accept query parameters")
        if path == "/pair":
            return self._pair_post()
        device = self._device()
        if device is None:
            raise Denied(401, "pair this device first", "no-session")
        self._admit(device, mutation=True)
        who = "phone:" + device["id"]
        if path == "/wingmate/ask":
            wait = self.remote.mate.take(device["id"])
            if wait:
                raise Denied(429, "a phone may ask the wingmate a few questions a minute",
                             "mate-rate", wait)
            return self._json(200, self.app.mate_ask(self._body(4096), phone=True))
        body = self._body(MAX_TASK_BODY)
        private = self.app.phone_private_ids()
        ident = body.get("id", "") if isinstance(body, dict) else ""
        if path == "/workflow/gate":
            # Approve / Reject from the phone: one audit row, naming the device (and the tailnet login)
            self._not_private_workflow(ident, private)
            out = self.app.workflow_owner("gate", body, via=who, device=device["id"], extra=self._with_login({}))
            return self._json(200, phone_swap_ids(strip_paths(out), private))
        if path == "/task":
            body = self._phone_thread(body)
            if isinstance(body, dict) and phone_private(body.get("target")) is not None:
                raise Denied(403, "that folder is private: start its tasks from the desktop page", "private-folder")
            out = self.app.create_task(body, via=who)
        else:                   # followup, stop, close: never on a private folder's thread, before anything is read
            self._not_private(ident, private)
            if path == "/task/followup":
                out = self.app.task_followup(body, via=who)
            elif path == "/task/stop":
                out = self.app.task_stop(body)
            else:
                out = self._phone_close(body)
        self.app.audit(self._with_login({"event": "phone", "device": device["id"],
                                         "route": path}))
        return self._json(200, phone_swap_ids(phone_task_row(out), private))

    def _not_private(self, ident: object, private: dict) -> None:
        """A thread in a private folder is shown on a phone as its state and the folder's name only: its words,
        output, diff and landing stay on the desktop page. `private` holds every thread, the old ones too."""
        if str(ident).startswith("private-") or str(ident) in private["task"]:
            raise Denied(403, "that task works in a private folder: open it on the desktop page", "private-folder")

    def _not_private_workflow(self, ident: object, private: dict) -> None:
        """The same for a workflow with a step in a private folder: its detail, replay and gates stay on the desktop
        page (approving a gate there could start a thread in that folder)."""
        if (str(ident).startswith("private-") or str(ident) in private["workflow"]
                or (ident and self.app._workflow_private(ident, private) is not None)):
            raise Denied(403, "that workflow works in a private folder: open it on the desktop page",
                         "private-folder")

    @staticmethod
    def _phone_thread(body: object) -> object:
        """A thread from a phone: the words, the folder, the agent and the model. verify,
        retries and setup are shell lines run as you, so they stay with the desktop page; the
        work is in its own worktree or the thread is refused (create_task asks the engine to
        refuse a folder with no git repo or no commit, never to work there in place). Anything
        else wrong with the body is the thread engine's to say."""
        if isinstance(body, dict):
            if any(key in body for key in DESKTOP_FIELDS):
                raise Denied(403, "verify, retries and setup are set from the desktop page",
                             "desktop-only-field")
            if "isolate" in body and body["isolate"] is not True:
                raise Denied(403, "a thread started from a phone works only in its own worktree "
                                  "(in a git repo with a commit), never in the folder itself: "
                                  "working in place is for the desktop page", "desktop-only-field")
        return body

    def _phone_close(self, body: object) -> dict:
        """Landing a thread from a phone: only when nothing is against it, and never with
        force. The store checks the evidence again under its own lock, for a merge, a set-aside
        and a done alike (require_clear), so a verdict that lands between the two reads still
        stops."""
        if not isinstance(body, dict) or not {"id", "action"} <= set(body) <= {
                "id", "action", "force"}:
            raise BridgeError(400, "body must be {id, action[, force]}")
        if "force" in body and body["force"] is not False:
            raise Denied(403, "landing a thread past the evidence is for the desktop page",
                         "force")
        blockers = self.app.task_blockers(body["id"])
        if blockers:
            raise Denied(409, "the evidence says not yet: " + "; ".join(blockers)
                         + ". Landing or setting aside a thread with something against it is "
                         "for the desktop page", "blockers")
        return self.app.task_close({"id": body["id"], "action": body["action"]}, require_clear=True)

    def _pair_post(self):
        """Trade a pairing code for this device's cookie. The Origin and Fetch Metadata checks
        come before the code is looked at, so a request that fails them never tests a guess."""
        access, peer = self.remote, self._peer()
        self._origin_pinned(required=True)
        self._same_site()
        wait, why = access.gate.begin(peer)
        if wait:
            raise Denied(429, "too many pairing attempts: wait before trying again", why, wait)
        body = self._body(1024)
        shaped = isinstance(body, dict) and isinstance(body.get("code"), str)
        if not shaped or not set(body) <= {"code", "name"}:
            access.gate.failed(peer)
            raise BridgeError(400, "body must be {code[, name]}")
        if not isinstance(body.get("name", ""), str):
            access.gate.failed(peer)
            raise BridgeError(400, "body must be {code[, name]}")
        if access.devices.count() >= DEVICE_MAX:
            raise BridgeError(409, f"{DEVICE_MAX} devices are paired: revoke one from the "
                                   "desktop first")
        if not access.codes.consume(body["code"]):
            access.gate.failed(peer)
            raise Denied(403, "that code is wrong, already used or expired", "bad-code")
        access.gate.passed(peer)
        name = device_name(body.get("name"))
        try:
            device, token = access.devices.issue(name)
        except OSError as exc:
            raise BridgeError(500, f"could not save the device: {exc}") from exc
        self.app.audit(self._with_login({"event": "phone-paired", "device": device["id"],
                                         "name": name, "peer": peer}))
        answer = {"ok": True, "id": device["id"], "name": name}
        data = json.dumps(answer, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie", self._cookie(token))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


LOCK_PAGE = ("<!doctype html><meta charset=utf-8><title>container-map is locked</title>"
             "<body style=\"font:16px/1.5 system-ui,sans-serif;max-width:34em;margin:4em auto;padding:0 1em\">"
             "<h1>This map is locked</h1><p>This computer is shared, so the map opens only through the link "
             "that <code>container-map serve</code> printed in the terminal that started it. Open that link "
             "(it ends in <code>?k=</code> and a long code).</p>"
             "<p>Lost it? Run <code>container-map link</code> as the person who started the map.</p>")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="serve the local container game bridge")
    parser.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", "7428")))
    parser.add_argument("--seats", default=os.environ.get("BRIDGE_SEATS_PATH", _CFG["seats_json"] or os.path.join(HERE, "seats.json")))
    parser.add_argument("--game", default=os.environ.get("BRIDGE_GAME_PATH", _CFG["out"] or os.path.join(HERE, "game.html")))
    parser.add_argument("--renderer", default=os.environ.get("BRIDGE_RENDER_PATH", os.path.join(HERE, "render-game.py")))
    # default: ROOT/.container-map/run/bridge-<port>.json (config.default_live_path), set below
    parser.add_argument("--live-file", default=os.environ.get("BRIDGE_LIVE_PATH") or "")
    parser.add_argument("--log-file", default=os.environ.get("BRIDGE_LOG_PATH", os.path.join(HERE, "bridge.log")))
    args = parser.parse_args(argv)
    args.live_default = not args.live_file     # nobody chose a place: the legacy file may follow
    if args.live_default:
        args.live_file = _config.default_live_path(_CFG, args.port)
    return args


def _leave(signum, frame):
    """SIGTERM: leave through main()'s `finally`. The next TERM is ignored: the service manager and
    world.sh's trap both send one, and the second must not cut the cleanup short."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.exit(0)


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("BRIDGE_HOST", LOOPBACK) != LOOPBACK:
        print("bridge refuses every bind address except 127.0.0.1", file=sys.stderr)
        return 2
    args = parse_args(argv)
    if not 1 <= args.port <= 65535:
        print("bridge port must be from 1 through 65535", file=sys.stderr)
        return 2
    import remote_t3
    t3_refresh = remote_t3.Refresher(_CFG)
    # `container-map serve` hands the launch key over in the environment; taking it out keeps it from every runner
    # and verify command this process starts
    launch_key = os.environ.pop("BRIDGE_LAUNCH_KEY", "")
    app = BridgeApp(seats_path=args.seats, game_path=args.game, renderer_path=args.renderer,
                    live_path=args.live_file, log_path=args.log_file, launch_key=launch_key)
    server = BridgeHTTPServer((LOOPBACK, args.port), Handler, app)
    app.port = server.server_port
    if args.live_default:      # an old .bridge-live beside the code stays current for its readers
        mirror = _config.legacy_mirror(app.live_path, app.port)
        app.live_mirrors = (mirror,) if mirror else ()
    try:     # a service manager stops it with SIGTERM: leave through `finally` so the file goes
        signal.signal(signal.SIGTERM, _leave)
        # Ctrl-C stops it too. A parent that started us with SIGINT ignored (a backgrounded job,
        # the CLI's serve) must not hand that on to every runner and verify command we start.
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except ValueError:         # not the main thread (a test)
        pass
    # the handlers are in place before the credentials file is written, so a stop that lands
    # while the rest of the start is under way (the ticker, the phone listener) still takes the
    # file with it instead of killing the process with the file left behind
    try:
        app.write_live()
        app.start_ticker()
        t3_refresh.start()
        app.start_remote()
        print(f"bridge http://{LOOPBACK}:{app.port}/", flush=True)
        print(f"agent control credentials: {app.live_path} (mode 600)", flush=True)
        if app.ui_lock:
            print("map locked: it opens only through the link `container-map serve` printed", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        t3_refresh.close()
        app.stop_remote()
        app.stop_ticker()
        server.server_close()
        app.remove_live()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
