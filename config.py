#!/usr/bin/env python3
"""config.py — where the container lives. Every path the game reads comes from ONE file.

Resolution order: `--config PATH` on the command line, then $CONTAINER_CONFIG, then
./container.json next to this file, then the built-in generic defaults below (which describe a
generic workspace, not any particular machine). Missing optional sources are simply absent: a hall
with no source is not drawn, never invented. (`cli.py thread …` adds one step: the nearest
map above the thread's folder, find_root below.) The bridge's live file,
the folder walk-up and the git exclude helper also live here.
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
# what a planet (a top-level folder) can be: its "kind" in the config's planet list or in areas.json. data and media are
# guessed from the files too (districts.py); the map draws one body per purpose (ui/kinds.js)
AREA_KINDS = ("code", "knowledge", "shared", "bus", "config", "reference", "archive", "data", "media")
# the emblems an owner may give a planet in areas.json ("glyph"), so a folder reads as what it is to the user without a guess
AREA_GLYPHS = ("code", "notes", "money", "health", "career", "journal", "learning", "archive", "sync", "tools", "home", "star")
DEFAULTS = {
    "version": 1,
    "name": "CONTAINER",            # the star's label (this machine)
    "owner": "",                    # the owner's first name, used only to recognise notes like 'per <owner>' or 'needs <owner>'
    "work_root": "~/work",          # its subdirectories are the areas (planets)
    "private": [],                  # folder names or paths: phones see only their names and counts
    "extra_areas": {},              # {"notes": "~/Documents/notes"}: more planets outside work_root
    "script_dirs": {},              # {"~/bin": "scripts"}: a script in that folder (a cron line's, a running process's) is homed to the planet of that name; {} = none
    "planets": [],                  # YOUR planets, in order: [{"name": "Areas", "path": "~/work/notes/areas", "kind": "knowledge", "desc": "…"}];
                                    # [] = every top-level folder of work_root. A planet inside another planet is cut out of the parent's count.
    "tmux_session": "",             # seats = windows of this tmux session; "" = the session you are in (if any); "none" = no seats
    "hangar_slots": 6,              # berths on the hangar plate: agent seats and ships are counted against it (1-99; display only, nothing is stopped)
    "areas_json": "",               # kinds/workers per area; "" = next to the config, else ROOT/.container-map/areas.json, else none
    "seats_json": "",               # seat policies (talk commands, machines); "" = same lookup as areas_json
    "processes": True,              # home running processes to areas (reads the process table)
    "t3_state": "",                 # optional T3 metadata SQLite (strictly read-only); a list = several T3 apps, first = the main one
    "t3_remote": [],                # optional SSH metadata sources, refreshed by the bridge
    "t3_link": "",                  # the web address of that T3 server (http(s)://host:port): a T3 thread gets an "open in T3" link; "" = none
    "agent_processes": False,       # background codex exec / claude -p / threads ships
    "ship_machines": [],            # one snapshot ship per machine under synced_dir
    "crontab_file": "",             # "" = `crontab -l`; a path = read cron lines from that file
    "cron_syslog_file": "/var/log/syslog",  # when each cron line last fired; "" = log mtimes only
    "digest": "",                   # digest markdown: ticker lines + parked decisions
    "fleet": "",                    # fleet/intent markdown: parked decisions
    "decision_markers": ["awaiting decision", "needs a human", "human call", "awaiting owner",
                         "owner's call", "blocked on owner"],
    "dispatch_dir": "",             # d-YYYYMMDD-HHMMSS-<runner>.md reports → runners hall stats
    "runners_json": "",             # the runner registry → runners hall
    "relay_dir": "",                # relay channels (inbox.md per dir) → relay bay + machines
    "hub_channel": "claude",        # the relay channel rounds.py reads (rounds.py is not in the public tree: unused there)
    "reads_jsonl": "",              # digest read receipts
    "tags_md": "",                  # a markdown roots-map table (| `root/` | … | **TAG** | desc |)
    "machines": {},                 # {"home": ["claude"], "pi-4": ["pi","claude-pi"]} relay evidence per machine
    "tasks_json": "",               # the board's audit items ({items:[{who, verdict, …}]})
    "rounds_json": "",              # the rounds state file the HUD shows; "" = rounds.json next to the tool (never with --root or demo)
    "synced_dir": "",               # synced worlds of other machines (fleet-game/out)
    "file_roots": [],               # extra roots the file preview may read (work_root + extra_areas always may)
    "out": "",                      # output html; "" = ./game.html
    "lane_planets": {},             # optional conversation group aliases to folder names
    "constellations": [],           # optional groups: [{name, key, keywords: [], private: false, fallback: false}]
    "constellation_rules": [],      # optional group names in keyword matching order
    "demo": False,                  # demo banner: a generated workspace, not a real machine
    "pilot_log": "",                # the hub hook's touch log (jsonl: ts, tool, uid) → effort badges and trace
    "rulings_json": "",             # the user's rulings on parked decisions (done/stale) → retired from the board
    "task_runs_json": "",           # jsonl: board items handed to runners (rid, runner, out) → status per item
    "dispatch_bin": "",             # the dispatch tool the board hands items through ("" = `dispatch` on PATH)
    "audit_json": "",               # an auditor's verdict per board item ({items:{id:{verdict,recommended,evidence,at}}}) → shown beside it
    "runners": {},                  # slice 2: {name: {"cmd": [argv…], "cwd": "item"|"tool", "report_dir": "", "max_minutes": 30}}; "{text}" in an argv = the item text, else text on stdin
    "tasks_dir": "",                # T3-style tasks (tasks.py): one folder of receipts per task; "" = WORK_ROOT/.container-map/tasks
    "workflows_dir": "",            # workflows (workflows.py): one folder per workflow; "" = WORK_ROOT/.container-map/workflows
    "workflow_launches_per_day": 40,  # workflows: task launches agent-made workflows may start together since local midnight (yours are not counted)
    "workflow_artifact_mb": 32,     # workflows: how much one workflow may snapshot of its steps' outputs, in MB (8 MB a file at most)
    "three_inline": False,          # 3D: put three.js and the 3D code in the page itself (one file) instead of the cached /vendor/ route
    "worktree_setup": {},           # {"/abs/repo" or "*": "npm ci"}: one shell line a new worktree thread runs before its agent (first run only); exact repo first, then "*"; a thread's own setup wins
    "task_scope": True,             # each thread's supervisor runs in its own systemd --user scope, so restarting the map never kills a running thread
    "tick_seconds": 10,             # tasks: the bridge's own clock for the oracle loop (a failed verify with retries left goes back to its agent) even when no page is open; whole seconds, 0 = off (a float is ignored)
    "no_ai_trailers": False,        # tasks: tell agents not to sign commits, and refuse to merge a branch whose commits carry Co-Authored-By
    # tasks: gitignore-style patterns; untracked files they name are not committed with a thread's
    # leftovers (a tracked file always is); [] = commit everything
    "merge_skip": ["__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", "node_modules/",
                   ".DS_Store", ".mimocode/", "*.egg-info/", ".venv/"],
    "merge_requires_check": False,  # tasks: a merge is blocked until a check run has a fresh PASS
    "worktrees_dir": "",            # tasks: where new thread worktrees go, outside the receipts; "" = <tasks_dir>-worktrees
    "agent_verify": False,          # agents may set a thread's verify shell line + retries; False = the page only (the line runs as you)
    "detect_runners": True,         # zero-config: agent CLIs on PATH (claude, codex, gemini, aider) become runners when "runners" is empty
    "home_planet": "",             # planet name carrying the home ring; empty = none
    "wingmate": {},                 # your wingmate on the map: {"name": "Scout", "cmd": [argv…], "env": {}, "max_seconds": 60, "models": […], "default": "haiku"}; {} = claude on PATH (haiku, sonnet, opus; + Codex if codex is too) if any, else it explains the map only
    "history_jsonl": "",            # hourly per-root snapshot (age, stale crons, trouble) so the map can show what is getting worse; "" = WORK_ROOT/.container-map/history.jsonl
    "verbs_dir": "",                # slice 6: verb manifests (<name>.json) the map offers on matching units; "" = ROOT/.container-map/verbs (zero-config) else the tool's verbs/
    "decision_scan": True,          # parked decisions from DECIDE:/TODO:/FIXME:/QUESTION: lines in text files under the roots (bounded walk)
    "decision_words": ["DECIDE:", "TODO:", "FIXME:", "QUESTION:"],
    "quest_priority": [],           # [{"label": "WORK", "match": "<regex>"}, ...]: board lines matching an earlier entry sort first and carry its label; [] = by age only
    "today": {},                    # the first screen's sources: {todo_md, git_dirs}; docs/CONFIG.md lists the rest (file readers for other formats)
    "remote": {},                   # phone access, off by default: {"listen": "<ip>", "port": <bridge port + 1>, "public_origin": "https://<host>" or "http://<tailnet ip or name>:<port>", "allow_http": false}; a second listener for paired devices (README: Use it from your phone)
    "ui_lock": False,               # shared computer: the page opens only through the link `serve` prints (a launch key, new at every start); False = any local process may open it
    "theme": "classic",             # the map's theme and texture pack: a file name in themes/ (classic, earthen, blueprint, synthwave, phosphor); V in the page cycles, and the browser's choice wins
    "skin": "neutral",              # the craft, the plate words and the wingmate's face: a file name in skins/ (or skins_dir); fixed per map, not switched in the page
    "skins_dir": "",                # your own skins: a folder of <name>.json, looked in before the tool's skins/
}
TODAY_PATHS = ("todo_md", "plan_md", "learning_log", "applied_csv", "drafts_dir", "ledger_jsonl", "commitments_md",
               "checkins_jsonl", "night_json")
TODAY_PATH_LISTS = ("git_dirs",)
# t3_link lands in a link on the page: an http(s) address that starts with its host (a letter, digit
# or [ for IPv6; no user@), with no space, quote, ? or #
T3_LINK = re.compile(r"https?://[A-Za-z0-9\[][^\s/?#\"'<>\\`@]*(?:/[^\s?#\"'<>\\`]*)?\Z", re.I)


def _expand(v):
    return os.path.expanduser(v) if isinstance(v, str) and v.startswith("~") else v


def root_arg(argv=None):
    """ZERO-CONFIG: `--root DIR` (or $CONTAINER_ROOT) points the map at any directory with no
    config file at all. With a root given, prefer ROOT/.container-map/container.json
    (or --config / $CONTAINER_CONFIG). The tool's config is reused only for its exact work_root."""
    argv = sys.argv if argv is None else argv
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--root="):
            return a.split("=", 1)[1]
    return os.environ.get("CONTAINER_ROOT") or ""


def config_path(argv=None):
    argv = sys.argv if argv is None else argv
    for i, a in enumerate(argv):
        if a == "--config" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--config="):
            return a.split("=", 1)[1]
    if os.environ.get("CONTAINER_CONFIG"):
        return os.environ["CONTAINER_CONFIG"]
    root = root_arg(argv)
    if root:
        own = os.path.join(os.path.abspath(os.path.expanduser(root)), ".container-map", "container.json")
        if os.path.isfile(own):
            return own
        # `serve /work` must keep the sources configured for /work. A different root never
        # inherits the owner's sources; the stranger/zero-config boundary stays intact.
        local = os.path.join(HERE, "container.json")
        try:
            with open(local, encoding="utf-8") as handle:
                raw = json.load(handle)
            configured = raw.get("work_root") if isinstance(raw, dict) else None
            if isinstance(configured, str) and configured and os.path.realpath(os.path.expanduser(configured)) == os.path.realpath(os.path.expanduser(root)):
                return local
        except (OSError, ValueError):
            pass
        return None
    local = os.path.join(HERE, "container.json")
    return local if os.path.isfile(local) else None


def find_root(start, names=("container.json",)):
    """The nearest folder at or above `start` (a folder, a file in one, or a path not made yet)
    that holds ROOT/.container-map/<one of names>, else "". How a command that was given no
    --root and no --config finds the map it belongs to."""
    seen = []
    start = os.path.expanduser(start)
    for base in (os.path.abspath(start), os.path.realpath(start)):
        if base in seen:
            continue
        seen.append(base)
        here = base
        while True:
            if any(os.path.exists(os.path.join(here, ".container-map", n)) for n in names):
                return here
            parent = os.path.dirname(here)
            if parent == here:
                break
            here = parent
    return ""


def problem(path):
    """Why the config file at `path` cannot be used, in one line ("" when it can). load() only
    warns and carries on with the defaults; the commands that must not hide it (doctor) ask here."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except OSError as exc:
        return f"cannot be read: {exc.strerror or exc}"
    except ValueError as exc:
        return f"is not valid JSON: {exc}"
    return "" if isinstance(raw, dict) else "must hold one JSON object ({ … }) at the top level"


# ---- the bridge's credentials file ----------------------------------------------------------
# One file per root and per port, inside the root's own .container-map folder: two maps on two
# ports never overwrite each other, and nothing is written next to the code (a pip install puts
# that in site-packages). The bridge, gamectl and the CLI all resolve it here.
LEGACY_LIVE = os.path.join(HERE, ".bridge-live")      # where it used to be: beside the code
DEFAULT_PORT = 7428


def live_path(root, port):
    return os.path.join(os.path.abspath(os.path.expanduser(root)), ".container-map", "run",
                        f"bridge-{int(port)}.json")


def _port_of(raw):
    return int(raw) if str(raw or "").isdigit() and 1 <= int(raw) <= 65535 else 0


def user_live_path(port):
    """Where the credentials go when neither the root nor the folder of the code can take them
    (a read-only mount, a root-owned checkout): a per-user runtime folder."""
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "container-map", f"bridge-{int(port)}.json")


def _writable_below(path):
    """True when `path` is a folder this process can write to, or could make: its nearest
    existing ancestor is writable. Makes nothing."""
    here = os.path.abspath(path)
    while not os.path.exists(here):
        parent = os.path.dirname(here)
        if parent == here:
            return False
        here = parent
    return os.path.isdir(here) and os.access(here, os.W_OK | os.X_OK)


def default_live_path(cfg=None, port=0):
    """Where the bridge for this config's root keeps its credentials: ROOT/.container-map/run when
    that can be written; else beside the code (the old place) when that can; else a per-user
    runtime folder. Makes nothing."""
    cfg = CFG if cfg is None else cfg
    port = port or _port_of(os.environ.get("BRIDGE_PORT")) or DEFAULT_PORT
    root = cfg.get("work_root") or ""
    if root and os.path.isdir(root) and _writable_below(os.path.join(root, ".container-map",
                                                                       "run")):
        return live_path(root, port)
    if os.access(HERE, os.W_OK | os.X_OK) or os.access(LEGACY_LIVE, os.W_OK):
        return LEGACY_LIVE
    return user_live_path(port)


def read_live(path):
    """The live file as a dict, or None when it is missing, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def pid_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True               # EPERM: it exists, it is somebody else's
    return True


def live_alive(path):
    """The live file's dict when it names a process that is still running, else None."""
    info = read_live(path)
    return info if info and pid_alive(info.get("pid")) else None


def legacy_mirror(primary, port):
    """The legacy file to keep up to date too (owner hooks read it): only one that already
    exists, is not `primary`, and is not another running map's (a live pid on another port)."""
    if os.path.abspath(primary) == os.path.abspath(LEGACY_LIVE) or not os.path.isfile(LEGACY_LIVE):
        return ""
    info = read_live(LEGACY_LIVE)
    if info and pid_alive(info.get("pid")) and info.get("port") != port:
        return ""
    return LEGACY_LIVE


def resolve_live(root="", port=0, environ=None, cwd=None):
    """The credentials file of the bridge this shell means, the way gamectl and the CLI find it:
    $BRIDGE_LIVE_PATH; else the root (given, $CONTAINER_ROOT, or the nearest map above `cwd`)
    and the port (given, $BRIDGE_PORT, else the one bridge that is set up there); else the legacy
    file. RuntimeError when several bridges are set up and nothing says which."""
    env = os.environ if environ is None else environ
    if env.get("BRIDGE_LIVE_PATH"):
        return env["BRIDGE_LIVE_PATH"]
    root = root or env.get("CONTAINER_ROOT") or find_root(cwd or os.getcwd(),
                                                          ("container.json", "run"))
    port = port or _port_of(env.get("BRIDGE_PORT"))
    if not root:
        return LEGACY_LIVE
    root = os.path.abspath(os.path.expanduser(root))
    if port:
        own = live_path(root, port)
        if os.path.exists(own):
            return own
        if os.path.exists(user_live_path(port)):            # the root could not take it
            return user_live_path(port)
        old = read_live(LEGACY_LIVE)
        return LEGACY_LIVE if old and old.get("port") == port else own
    found = sorted(glob.glob(os.path.join(root, ".container-map", "run", "bridge-*.json")))
    running = [p for p in found if live_alive(p)]
    pool = running or found
    if not pool:       # a root that could not take the file: the per-user folder, running ones only
        pool = [p for p in sorted(glob.glob(os.path.join(os.path.dirname(user_live_path(1)),
                                                         "bridge-*.json"))) if live_alive(p)]
    if len(pool) == 1:
        return pool[0]
    if pool:
        ports = ", ".join(os.path.basename(p)[len("bridge-"):-len(".json")] for p in pool)
        raise RuntimeError(f"{len(pool)} bridges are set up for {root} (ports {ports}): "
                           "pass --port N")
    return LEGACY_LIVE


# ---- keeping the map's own folder out of the repo it sits in ---------------------------------
def _side_of(path):
    """The .container-map folder that `path` is or sits in, else ""."""
    side = os.path.abspath(path)
    while os.path.basename(side) != ".container-map":
        if os.path.dirname(side) == side:
            return ""
        side = os.path.dirname(side)
    return side


def hide_from_git(path):
    """Add `.container-map/` to the .git/info/exclude of the repo that holds the map's folder
    (`path` is that folder or anything inside it), so it is not an untracked change in the
    owner's repo. Idempotent and quiet: no repo, no git, a read-only .git all mean there is
    nothing to do. True when it wrote."""
    import subprocess
    side = _side_of(path)
    if not side:
        return False
    parent = os.path.dirname(side)
    try:
        got = subprocess.run(["git", "-C", parent, "rev-parse", "--git-path", "info/exclude"],
                             capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL)
        if got.returncode != 0 or not got.stdout.strip():
            return False
        target = os.path.join(parent, got.stdout.strip().splitlines()[-1])
        try:
            with open(target, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except FileNotFoundError:
            text = ""
        if any(line.strip() in (".container-map", ".container-map/", "/.container-map",
                                "/.container-map/") for line in text.splitlines()):
            return False
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(("" if not text or text.endswith("\n") else "\n") + ".container-map/\n")
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def make_state_dir(path, mode=0o777):
    """os.makedirs(path, exist_ok=True) for a folder the tool keeps for the map. When that also
    makes the .container-map folder it sits in, the repo around it stops listing it."""
    side = _side_of(path)
    fresh = bool(side) and not os.path.isdir(side)
    os.makedirs(path, mode=mode, exist_ok=True)
    if fresh:
        hide_from_git(side)


def current_tmux_session():
    """The tmux session this process runs inside, else ""."""
    if not os.environ.get("TMUX"):
        return ""
    try:
        import subprocess
        out = subprocess.run(["tmux", "display-message", "-p", "#S"], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _sidecar(cfg, kind, root):
    """areas.json / seats.json lookup: explicit > next to the config file > ROOT/.container-map/ >
    (zero-config: none) > the tool's own file (legacy layout)."""
    explicit = cfg.get(kind + "_json") or ""
    if explicit:
        return _expand(explicit)
    if cfg.get("_path"):
        beside = os.path.join(os.path.dirname(os.path.abspath(cfg["_path"])), kind + ".json")
        if os.path.isfile(beside):
            return beside
    if root:
        own = os.path.join(root, ".container-map", kind + ".json")
        return own if os.path.isfile(own) else ""
    own = os.path.join(HERE, kind + ".json")
    return own if os.path.isfile(own) else ""


def _kind(value):
    """What a JSON value is, in the words a person writes config in."""
    if isinstance(value, bool):
        return "true or false"
    for kind, name in ((int, "a whole number"), (float, "a decimal number"), (str, "text"),
                       (list, "a list"), (dict, "an object")):
        if isinstance(value, kind):
            return name
    return "null" if value is None else type(value).__name__


def _link_ok(value):
    """An http(s) address a link can use: the characters T3_LINK allows, a host, a number port if any."""
    from urllib.parse import urlsplit
    if not T3_LINK.match(value):
        return False
    try:
        parts = urlsplit(value)
        parts.port                                      # raises ValueError for a port that is not a number
    except ValueError:
        return False
    return bool(parts.hostname) and parts.username is None


def _dropped(key, value):
    """Why load() ignores `key` from the config file, or "" when it keeps it. Keys that start
    with an underscore are the file's own comments (the shipped examples use them)."""
    if key.startswith("_"):
        return ""
    if key not in DEFAULTS:
        return "no setting by that name (a typo?)"
    if type(value) == type(DEFAULTS[key]):
        if key == "hangar_slots" and not 1 <= value <= 99:
            return "expected a whole number from 1 to 99; the default is used"
        if key == "t3_link" and value.strip() and not _link_ok(value.strip()):
            return "expected an http:// or https:// address with no space, quote, ? or #; the default is used"
        return ""
    if key == "t3_state" and isinstance(value, list) and all(isinstance(x, str) for x in value):
        return ""
    want = _kind(DEFAULTS[key]) + (" or a list of text" if key == "t3_state" else "")
    return f"expected {want}, got {_kind(value)}; the default is used"


def load(path=None):
    cfg = dict(DEFAULTS)
    path = path or config_path()
    dropped = []                         # [{"key", "reason"}]: what the file said and load() ignored
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    why = _dropped(k, v)
                    if why:
                        dropped.append({"key": k, "reason": why})
                    elif not k.startswith("_"):
                        cfg[k] = v
        except (OSError, ValueError) as exc:
            print(f"warn: config {path} unreadable: {exc}", file=sys.stderr)
    for k in ("work_root", "crontab_file", "cron_syslog_file", "digest", "fleet", "dispatch_dir",
              "runners_json", "relay_dir", "reads_jsonl", "tags_md", "tasks_json", "rounds_json",
              "synced_dir", "out", "pilot_log", "rulings_json", "task_runs_json", "dispatch_bin",
              "audit_json"):
        cfg[k] = _expand(cfg[k])
    cfg["t3_link"] = cfg["t3_link"].strip().rstrip("/")
    t3 = cfg["t3_state"]
    cfg["t3_state"] = [_expand(x) for x in t3 if x] if isinstance(t3, list) else _expand(t3)
    raw_today = cfg["today"]
    cfg["today"] = {k: (_expand(v) if k in TODAY_PATHS else v) for k, v in cfg["today"].items() if isinstance(v, str)}
    for k in TODAY_PATH_LISTS:                      # a list of folders (git_dirs): text entries only
        got = (raw_today or {}).get(k)
        if isinstance(got, list):
            cfg["today"][k] = [_expand(v) for v in got if isinstance(v, str) and v.strip()]
    cfg["extra_areas"] = {k: _expand(v) for k, v in cfg["extra_areas"].items() if isinstance(v, str)}
    cfg["script_dirs"] = {_expand(k): v for k, v in cfg["script_dirs"].items() if isinstance(k, str) and k.strip()
                          and isinstance(v, str) and v.strip()}
    planets, seen = [], set()
    for p in (cfg.get("planets") if isinstance(cfg.get("planets"), list) else []):
        if not (isinstance(p, dict) and isinstance(p.get("name"), str) and p["name"].strip() and isinstance(p.get("path"), str) and p["path"].strip()):
            continue
        name = p["name"].strip()[:40]
        if name in seen or "/" in name:
            continue
        seen.add(name)
        kind = p.get("kind") if p.get("kind") in AREA_KINDS else None
        planets.append({"name": name, "path": os.path.abspath(os.path.expanduser(p["path"].strip())), "kind": kind,
                        "desc": str(p.get("desc") or "")[:200], "tag": str(p.get("tag") or "")[:12]})
    cfg["planets"] = planets
    cfg["file_roots"] = [_expand(v) for v in cfg["file_roots"] if isinstance(v, str)]
    cfg["private"] = [v.strip() for v in cfg["private"] if isinstance(v, str) and v.strip()]
    cfg["_path"] = path or ""
    cfg["_dropped"] = dropped            # doctor prints these: a key that is ignored must not vanish
    root = root_arg()
    root = os.path.abspath(os.path.expanduser(root)) if root else ""
    cfg["_root_arg"] = bool(root)
    if not cfg["rounds_json"] and not root and not cfg["demo"]:
        # the renderer and rounds.py must look in the same place; a map of another root (or the
        # demo) never inherits a file that sits in the tool's own folder
        cfg["rounds_json"] = os.path.join(HERE, "rounds.json")
    if os.environ.get("CONTAINER_OUT"):
        cfg["out"] = os.path.abspath(os.path.expanduser(os.environ["CONTAINER_OUT"]))
    if root:
        cfg["work_root"] = root
        if cfg["name"] == DEFAULTS["name"]:
            cfg["name"] = (os.path.basename(root.rstrip("/")) or root).upper()[:24]
        if not cfg["out"] and os.path.isdir(root):      # a root that is not there is not made
            side = os.path.join(root, ".container-map")
            fresh = not os.path.isdir(side)
            try:
                os.makedirs(side, exist_ok=True)
                cfg["out"] = os.path.join(side, "game.html")
            except OSError:
                pass
            else:
                if fresh:
                    hide_from_git(side)
    if cfg["tmux_session"] == "none":
        cfg["tmux_session"] = ""
    elif cfg["tmux_session"] == "" and not cfg["demo"]:
        cfg["tmux_session"] = current_tmux_session()
    if not cfg["verbs_dir"]:
        cfg["verbs_dir"] = os.path.join(root, ".container-map", "verbs") if root else os.path.join(HERE, "verbs")
    cfg["verbs_dir"] = _expand(cfg["verbs_dir"])
    cfg["tasks_dir"] = _expand(cfg["tasks_dir"]) or os.path.join(cfg["work_root"] or HERE, ".container-map", "tasks")
    # workflows sit beside the threads (workflows.default_dir says the same); a value that is not text is the default
    _wf = cfg.get("workflows_dir")
    cfg["workflows_dir"] = (_expand(_wf) if isinstance(_wf, str) else "") or os.path.join(
        cfg["work_root"] or HERE, ".container-map", "workflows")
    # beside the receipts, not in them (tasks.default_worktrees_dir says the same)
    cfg["worktrees_dir"] = (_expand(cfg["worktrees_dir"])
                            or cfg["tasks_dir"].rstrip("/") + "-worktrees")
    cfg["history_jsonl"] = _expand(cfg["history_jsonl"]) or os.path.join(cfg["work_root"] or HERE, ".container-map", "history.jsonl")
    cfg["areas_json"] = _sidecar(cfg, "areas", root)
    cfg["seats_json"] = _sidecar(cfg, "seats", root)
    return cfg


def task_roots(cfg):
    """Where a thread, a door, a verb or a hand-off may run: the work root, the extra areas and the
    planets, which is what the map draws. file_roots is deliberately not here; it only widens what
    the file preview may read."""
    out = [cfg["work_root"]] + list(cfg["extra_areas"].values())
    out += [p["path"] for p in cfg.get("planets", [])]
    return [os.path.realpath(p) for p in out if p]


def roots(cfg):
    """Every path the file preview may read: the task roots, and file_roots."""
    out = task_roots(cfg) + list(cfg["file_roots"])
    return [os.path.realpath(p) for p in out if p]


CFG = load()

if __name__ == "__main__":
    print(json.dumps(CFG, indent=1))
