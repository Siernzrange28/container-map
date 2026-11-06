#!/usr/bin/env python3
"""districts.py — every directory of the container as an area you can walk into.

Called by render-game.py; returns characters plus a layout. Reads system evidence;
persists incremental cron syslog offsets only in ROOT/.container-map/.

An AREA is a planet: a top-level folder of the work root, or a folder the config adds
(`planets`, `extra_areas`). Nothing outside the config is an area.
Inside an area:
  - a DOOR    the instruction file a seat loads when it is opened here (CLAUDE.md / AGENTS.md),
              or none — in which case a seat opened here loads nothing, and the door says so
  - a WORKER  the runner areas.json assigns to sit at this door, or unassigned (drawn hollow)
  - ROOMS     every first-level subdirectory, each with its own counts
  - TASKS     every cron line whose output lands here, every running process that names this
              area, every tmux seat whose working directory is inside it
Every number is counted at render time from the filesystem, the crontab and the process
table. Every placement carries the evidence it rests on ("homed by its log path"). mtime is
labelled as mtime: a fresh file proves a write, never that anything here is alive.
"""
import json, math, os, re, subprocess, sys, time
from collections import Counter, defaultdict
import ships as _ships              # parse_iso: one portable ISO 8601 reader
import words as _words              # a job's name and its schedule in words
from config import AREA_GLYPHS, AREA_KINDS   # what a planet can be, and the emblems an owner may give one
import contextlib
import fcntl
import getpass
import hashlib
import marshal
import stat
import tempfile
import zlib


def _state_dir(path, mode=0o777):
    """makedirs for a folder under ROOT/.container-map; a fresh .container-map is kept out of the
    repo around it (config.make_state_dir)."""
    if ".container-map" in path:
        import config
        config.make_state_dir(path, mode)
    else:
        os.makedirs(path, mode=mode, exist_ok=True)


HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")
AREAS_JSON = os.path.join(HERE, "areas.json")
DOORS = ("CLAUDE.md", "AGENTS.md")
CODE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh", ".bash", ".go", ".rs",
            ".c", ".h", ".cpp", ".java", ".rb", ".lua", ".sql", ".toml", ".yaml", ".yml",
            ".html", ".css", ".scss", ".vue", ".svelte", ".php", ".kt", ".swift", ".cs", ".dart", ".ipynb"}
# a planet is Data or Media when half or more of its files are tables/databases/arrays or images/audio/video
DATA_EXT = {".csv", ".tsv", ".parquet", ".arrow", ".feather", ".db", ".sqlite", ".sqlite3", ".duckdb", ".h5", ".hdf5",
            ".xls", ".xlsx", ".npy", ".npz"}
MEDIA_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico", ".svg", ".tiff", ".tif", ".heic",
             ".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
ROOM_CHILD_CAP = 50        # entries listed per room panel
REGEN_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".cache", "dist", "build", ".tox", "site-packages", ".next", "target"}
REGEN_EXT = {".pyc", ".map"}
ROOT_FILE_CAP = 250        # files listed at an area's top level (the toolbox IS its top level)
WALK_BUDGET_S = 2.0        # per area, wall clock; a capped walk says so on the panel
SLOW_MOUNTS = ("/mnt/",)   # 9P: ~300 stats/s, so these areas are listed one level deep, not walked
ROW_WIDTH = 40             # tiles across the district grid
ORIGIN = (2, 17)           # tiles; below the halls render-game.py keeps


def run(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def human(n):
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}G"


def age_days(ts, now):
    return None if not ts else max(0, int((now - ts) // 86400))


def life(age, files):
    """mtime-based, and labelled as such everywhere it is shown."""
    if not files or age is None:
        return "EMPTY"
    return "LIVE" if age <= 2 else ("WARM" if age <= 30 else "COLD")


# ---------- the user's own classification: a markdown roots table (config `tags_md`; a CLAIM, dated) ----------
def load_container_tags(path, width=90):
    tags, asof = {}, None
    if not path or not os.path.isfile(path):
        return tags, asof
    # v0 (08-15): | root | size | last | ? | **TAG** | desc |   v1 (10-05): | root | size | last change | **BIN** | desc |
    # Try the six-cell row first: a v1 row has only five cells, so it can never match the v0 pattern.
    v0 = r"\|\s*`([^`]+?)/?`\s*\|[^|]*\|[^|]*\|[^|]*\|\s*\**([^|*]+?)\**\s*\|\s*([^|]*?)\s*\|"
    v1 = r"\|\s*`([^`]+?)/?`\s*\|[^|]*\|[^|]*\|\s*\**([^|*]+?)\**\s*\|\s*([^|]*?)\s*\|\s*$"
    with open(path, errors="replace") as handle:
        for line in handle:
            m = re.search(r"Roots map.*?(?:profiled|checked against disk) (\d{4}-\d{2}-\d{2})", line)
            if m:
                asof = m.group(1)
            m = re.match(v0, line) or re.match(v1, line)
            if m:
                tags[m.group(1)] = {"tag": m.group(2).strip(), "desc": m.group(3).strip()[:width]}
    return tags, asof


# ---------- areas.json: kind + worker per area (the user's config; hub guesses are marked) ----------
def load_areas_json(path=AREAS_JSON):
    if not path:
        return {"version": 1, "workers": {}, "areas": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        if raw.get("version") != 1 or not isinstance(raw.get("areas"), dict):
            raise ValueError("expected version 1 with an areas map")
        return raw
    except Exception:
        return {"version": 1, "workers": {}, "areas": {}}


# ---------- the walk ----------
def _is_link(entry):
    try:
        return entry.is_symlink()
    except OSError:
        return False


def _folder_key(info):
    """A folder's entries change only with its mtime/ctime/inode; one int keeps the memory small
    (int and tuple hashes are not salted per process, so the key survives between renders)."""
    return hash((info.st_mtime_ns, info.st_ctime_ns, info.st_ino))


def _scan(r, is_root, regen_dir, shallow, exclude):
    """One folder exactly as census's os.walk saw it: the subfolders it counts and the ones it
    descends into, and what its own files add up to (count, bytes, newest mtime and the first file
    that has it, extensions in first-seen order, regenerable files; at the root, every file).
    None when the folder cannot be listed (os.walk skips such a folder the same way)."""
    try:
        info = os.stat(r)
    except OSError:
        info = None
    dirs, files = [], []
    try:
        with os.scandir(r) as entries:
            for e in entries:
                try:
                    isdir = e.is_dir()
                except OSError:
                    isdir = False
                (dirs if isdir else files).append(e)
    except OSError:
        return None
    dirs = [d for d in dirs if d.name != ".git" and (not exclude or d.path not in exclude)]
    if shallow and not is_root:
        dirs, files = [], []        # list this room, do not descend
    nf = nb = regen = 0
    newest, newest_name = 0, ""
    ext = {}
    root_files = [] if is_root else None
    for e in files:
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        name, size, mtime = e.name, st.st_size, st.st_mtime
        nf += 1
        nb += size
        x = os.path.splitext(name)[1].lower()
        ext[x or "(none)"] = ext.get(x or "(none)", 0) + 1
        if regen_dir or x in REGEN_EXT:
            regen += 1
        if mtime > newest:
            newest, newest_name = mtime, name
        if root_files is not None:
            root_files.append((name, size, mtime))
    key = _folder_key(info) if info else None
    return (key, len(dirs), tuple(d.name for d in dirs if not _is_link(d)), nf, nb, newest,
            newest_name, tuple(ext.items()), regen,
            tuple(root_files) if root_files is not None else None)


def census(path, now, exclude=(), memory=None):
    """One depth-first walk per area, in os.walk order. Returns totals, per-room stats,
    per-(room, child) counts. Areas on a slow mount are listed one level deep only; their rooms
    carry walked=False. `exclude`: paths of other planets inside this one; they are not walked and
    not counted here. `memory` (a CensusMemory): folders it vouches for are not listed again; the
    numbers are the same either way, added up folder by folder in the same order."""
    t0 = time.time()
    shallow = os.path.realpath(path).startswith(SLOW_MOUNTS)
    exclude = set(exclude or ())
    tot = {"files": 0, "dirs": 0, "bytes": 0, "newest": 0, "newest_path": "", "capped": False,
           "shallow": shallow, "regen": 0}
    ext = Counter()
    rooms = {}
    child = defaultdict(lambda: {"files": 0, "bytes": 0, "newest": 0})
    root_files = []
    area = memory.area(path, exclude, shallow) if memory is not None else None
    files = nbytes = dirs = regen = 0
    newest, newest_path, root_descend = 0, "", None
    stack = [("", path, None, None, False)]          # (rel, folder, room, room child, regen)
    while stack:
        rel, r, top, sub, regen_dir = stack.pop()
        rec = (area.record(rel, r, top is None, regen_dir) if area is not None
               else _scan(r, top is None, regen_dir, shallow, exclude))
        if rec is None:
            continue
        _, nd, descend, nf, nb, nw, nw_name, ext_items, rg, rfiles = rec
        dirs += nd
        roomstat = None
        if top is not None:
            roomstat = rooms.get(top)
            if roomstat is None:
                roomstat = rooms[top] = {"files": 0, "dirs": 0, "bytes": 0, "newest": 0,
                                         "newest_path": ""}
            roomstat["dirs"] += nd
        else:
            root_descend = descend
        if nf:
            files += nf
            nbytes += nb
            regen += rg
            for k, n in ext_items:
                ext[k] += n
            if nw > newest:
                newest, newest_path = nw, os.path.join(r, nw_name)
            if roomstat is not None:
                roomstat["files"] += nf
                roomstat["bytes"] += nb
                if nw > roomstat["newest"]:
                    roomstat["newest"], roomstat["newest_path"] = nw, os.path.join(r, nw_name)
                if sub is not None:
                    c = child[(top, sub)]
                    c["files"] += nf
                    c["bytes"] += nb
                    c["newest"] = max(c["newest"], nw)
            else:
                root_files.extend(rfiles)
        if time.time() - t0 > WALK_BUDGET_S:
            tot["capped"] = True
            break
        for name in reversed(descend):
            if top is None:
                stack.append((name, os.path.join(r, name), name, None, name in REGEN_DIRS))
            else:
                stack.append((rel + "/" + name, os.path.join(r, name), top,
                              name if sub is None else sub, regen_dir or name in REGEN_DIRS))
    tot.update(files=files, dirs=dirs, bytes=nbytes, newest=newest, newest_path=newest_path,
               regen=regen)
    if area is not None:
        area.done(tot["capped"])
    # directories that exist but hold no files still count as rooms (an empty room is a finding)
    for name in root_descend or ():
        rooms.setdefault(name, {"files": 0, "dirs": 0, "bytes": 0, "newest": 0, "newest_path": ""})
    return tot, ext, rooms, child, root_files


# ---------- the walk's memory between renders ----------
# A folder's own entries change only when its mtime/ctime/inode do, so a folder whose key is
# unchanged is not listed again. A file edited in place does not touch its folder's mtime, so a
# folder holding a file written in the last HOT_S seconds is listed on every walk, and every other
# folder at least every VERIFY_AGE_S seconds (one of VERIFY_SLICES slices comes due per render):
# an in-place edit to an old file shows within that bound; adds, removes and renames show at once.
# Used by the render loop and the bridge (render-game.py --cached), never by a one-shot render.
# Kept in ROOT/.container-map/render-cache/: one file per planet, rewritten only when one of its
# folders changed, and an index of when each slice was last verified.
CENSUS_VERSION = 1
HOT_S = 3600.0
VERIFY_SLICES = 8
VERIFY_AGE_S = 120.0


def write_atomic(path, data, mode=0o600):
    fd, temp = tempfile.mkstemp(prefix=".tmp-", dir=os.path.dirname(path) or ".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


class AreaMemory:
    """One planet's folders as the last walks found them, and which of them this walk re-lists."""

    def __init__(self, memory, name, key, records, slices, exclude, shallow):
        self.memory, self.name, self.key = memory, name, key
        self.old, self.new = records, {}
        self.exclude, self.shallow = exclude, shallow
        now = memory.now
        self.fresh = not records or slices is None
        self.slices = list(slices) if slices is not None else [0.0] * VERIFY_SLICES
        self.due = {s for s, at in enumerate(self.slices) if now - at >= VERIFY_AGE_S}
        self.hot_after = now - HOT_S
        self.changed = False
        self.listed = 0                             # folders listed this walk (tests, bench)

    def record(self, rel, r, is_root, regen_dir):
        old = self.old.get(rel)
        if (old is not None and old[0] is not None and old[5] < self.hot_after
                and zlib.crc32(rel.encode("utf-8", "surrogateescape")) % VERIFY_SLICES
                not in self.due):
            try:
                info = os.stat(r)
                if _folder_key(info) == old[0]:
                    self.new[rel] = old
                    return old
            except OSError:
                pass
        self.listed += 1
        rec = _scan(r, is_root, regen_dir, self.shallow, self.exclude)
        if rec is not None:
            self.new[rel] = rec
            if rec != old:
                self.changed = True
        return rec

    def done(self, capped):
        if capped:                                  # keep what was not reached for the next walk
            for rel, rec in self.old.items():
                self.new.setdefault(rel, rec)
            return
        if len(self.new) != len(self.old):          # a folder went away
            self.changed = True
        now = self.memory.now
        if self.fresh:                              # stagger: one slice comes due per render
            step = VERIFY_AGE_S / VERIFY_SLICES
            self.slices = [now - s * step for s in range(VERIFY_SLICES)]
        else:
            for s in self.due:
                self.slices[s] = now
        if self.fresh or self.due:
            self.memory.index[self.name] = {"slices": self.slices}
            self.memory.index_changed = True


class CensusMemory:
    """What census remembers between renders, for every planet, in one folder."""

    def __init__(self, folder, now):
        self.folder, self.now = folder, now
        self.index_path = os.path.join(folder, "census-index.json")
        self.index, self.index_changed, self.areas = {}, False, []
        self.git, self.git_changed = {}, False      # repo path -> facts kept per HEAD
        try:
            with open(self.index_path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict) and raw.get("version") == CENSUS_VERSION:
                self.index = {k: v for k, v in raw.get("areas", {}).items()
                              if isinstance(v, dict) and isinstance(v.get("slices"), list)
                              and len(v["slices"]) == VERIFY_SLICES}
                self.git = {k: v for k, v in raw.get("git", {}).items() if isinstance(v, dict)
                            and {"head", "lastc", "branch", "times"} <= set(v)}
        except (OSError, ValueError, AttributeError):
            pass

    @staticmethod
    def area_key(path, exclude, shallow):
        key = json.dumps([CENSUS_VERSION, path, sorted(exclude), shallow])
        return key, hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()[:16]

    def records(self, key, name):
        try:
            with open(os.path.join(self.folder, f"census-{name}.marshal"), "rb") as handle:
                raw = marshal.loads(handle.read())      # load(file) reads object by object: slow
            if (isinstance(raw, dict) and raw.get("key") == key
                    and isinstance(raw.get("dirs"), dict)):
                return raw["dirs"]
        except (OSError, EOFError, ValueError, TypeError):
            pass
        return {}

    def area(self, path, exclude, shallow):
        key, name = self.area_key(path, exclude, shallow)
        area = AreaMemory(self, name, key, self.records(key, name),
                          (self.index.get(name) or {}).get("slices"), exclude, shallow)
        self.areas.append(area)
        return area

    def save(self):
        """Best effort: a cache that cannot be written only costs the next walk its speed."""
        try:
            _state_dir(self.folder, 0o700)
            for area in self.areas:
                if area.changed:
                    write_atomic(os.path.join(self.folder, f"census-{area.name}.marshal"),
                                 marshal.dumps({"key": area.key, "dirs": area.new}))
            if self.index_changed or self.git_changed:
                index = {"version": CENSUS_VERSION, "areas": self.index, "git": self.git}
                write_atomic(self.index_path, json.dumps(index).encode("utf-8"))
        except (OSError, ValueError) as exc:
            print(f"warn: render cache not saved: {exc}", file=sys.stderr)


def room_children(area_path, room, child, now, shallow=False):
    """Immediate children of a room as compact rows [name, files|null, size, age|null, flags];
    flags bit 1 = directory, bit 2 = has a door. Dirs carry their recursive counts."""
    out = []
    rp = os.path.join(area_path, room)
    if shallow:
        return out, None
    try:
        entries = list(os.scandir(rp))
    except OSError:
        return out, 0
    for e in entries:
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        if e.is_dir(follow_symlinks=False):
            c = child.get((room, e.name), {"files": 0, "bytes": 0, "newest": 0})
            door = any(os.path.isfile(os.path.join(rp, e.name, d)) for d in DOORS)
            out.append([e.name + "/", c["files"], human(c["bytes"]), age_days(c["newest"], now), 1 | (2 if door else 0)])
        elif e.is_symlink():
            target = os.readlink(e.path) if os.path.islink(e.path) else "?"
            out.append([e.name + " → " + target, None, "", age_days(st.st_mtime, now), 0])
        else:
            out.append([e.name, None, human(st.st_size), age_days(st.st_mtime, now), 0])
    out.sort(key=lambda x: (not (x[4] & 1), x[3] if x[3] is not None else 10 ** 6, x[0]))
    return out[:ROOM_CHILD_CAP], len(out)


# ---------- the render loop: has anything a render reads changed since the last render? ----------
# render-game.py --loop-render (world.sh, every 15 s) asks in two steps, cheap first: the digest of
# loop_inputs against the one its last full render started from (on a busy machine the process
# table alone usually differs, and the render starts at once), then every planet folder against
# the census memory that render saved. Both the same: the page on disk still says what a new render
# would, so the loop only touches it (the bridge reads its mtime as "true as of"). Adds, removes
# and renames anywhere move a folder key; files edited in place show at once in folders written in
# the last HOT_S seconds; anything else, and every "3m ago" the page prints, is re-rendered within
# LOOP_MAX_SKIP_S. The page itself (and the temp file it is written through) is not an input.
LOOP_MAX_SKIP_S = 60.0
_INFLIGHT_RX = re.compile(r"ralph |dispatch |claude -p|muse exec|grok --sandbox|codex exec")
_AGENT_RX = re.compile(_INFLIGHT_RX.pattern
                       + r"|(?:^|/)(?:claude|claude-worker|claude-max|codex|threads(?:\.py)?)(?:\s|$)")


def _stat_key(path):
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


def _entries(path, skip=()):
    """(name, mtime, size, inode) of a folder's entries, links not followed; [] if unreadable."""
    out = []
    try:
        with os.scandir(path) as entries:
            for e in entries:
                if e.name in skip or e.name.startswith(".tmp-"):
                    continue
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                out.append((e.name, st.st_mtime_ns, st.st_size, st.st_ino))
    except (OSError, TypeError, ValueError):
        pass
    out.sort()
    return out


def loop_inputs(cfg, out, tool_dir, now, run_cmd=run):
    """One digest of the cheap inputs of a render: the tool and its config, the sources, cron,
    the planet list, the process table and the tmux panes (stats, one ps, the panes). The
    planets' folders are the second, dearer check (loop_folders_unchanged)."""
    h = hashlib.sha1()

    def add(*parts):
        h.update(repr(parts).encode("utf-8", "surrogateescape") + b"\0")

    add("code", [e for e in _entries(tool_dir) if e[0].endswith(".py")])
    # (history_jsonl is an input the render itself appends to: _written, taken after the render)
    for key in ("_path", "areas_json", "seats_json", "digest", "fleet", "runners_json",
                "reads_jsonl", "tags_md", "tasks_json", "rounds_json", "pilot_log", "rulings_json",
                "task_runs_json", "audit_json"):
        add(key, _stat_key(cfg.get(key)))
    for key, value in sorted((cfg.get("today") or {}).items()):
        # most Today sources are one path; git_dirs (and any other list) is several
        for path in (value if isinstance(value, (list, tuple)) else [value]):
            if not isinstance(path, str) or not path:
                add(key, None)
            elif os.path.isdir(path):
                add(key, path, _entries(path), _stat_key(os.path.join(path, ".git", "HEAD")),
                    _stat_key(os.path.join(path, ".git", "index")))
            else:
                add(key, _stat_key(path))
    t3 = cfg.get("t3_state")
    for path in ([t3] if isinstance(t3, str) and t3 else t3 or []):
        add("t3", _stat_key(path), _stat_key(path + "-wal"))
    add("dispatch", _entries(cfg.get("dispatch_dir")), _entries(cfg.get("verbs_dir")))
    relay = cfg.get("relay_dir") or ""
    add("relay", [(ch, _stat_key(os.path.join(relay, ch, "inbox.md")))
                  for ch, *_ in _entries(relay)])
    tasks = cfg.get("tasks_dir") or ""
    add("tasks", [(t, _stat_key(os.path.join(tasks, t, "task.json")),
                   _stat_key(os.path.join(tasks, t, "events.jsonl"))) for t, *_ in _entries(tasks)])
    for machine in cfg.get("ship_machines") or []:
        add("deck", machine, _stat_key(os.path.join(cfg.get("synced_dir") or "", str(machine),
                                                    "world.json")))
    # cron: the lines, when each last fired, and the logs they write
    if cfg.get("crontab_file"):
        try:
            with open(cfg["crontab_file"], errors="replace") as handle:
                crontab = handle.read()
        except OSError:
            crontab = ""
    else:
        crontab = run_cmd(["crontab", "-l"])
    syslog = cfg.get("cron_syslog_file") or ""
    add("cron", crontab, _stat_key(syslog), _stat_key(syslog + ".1") if syslog else None)
    for path in re.findall(r">>?\s*([^\s;&|]+)", crontab):
        path = path.replace("~", HOME, 1) if path.startswith("~") else path
        add(path, _stat_key(path),
            _stat_key(path[:-len(".cron.log")] + ".log") if path.endswith(".cron.log") else None)
    # the planets (their folders are compared with the census memory in loop_unchanged); the
    # page's own folder, when a planet holds it, by its entries: writing the page moves its key
    roots, _, _ = planet_roots(cfg)
    add("roots", roots)
    out_dir, out_name = os.path.split(os.path.realpath(out)) if out else ("", "")
    if any(out_dir == r or out_dir.startswith(r.rstrip("/") + "/")
           for r in (os.path.realpath(p) for _, p, link in roots if not link)):
        add("page folder", _entries(out_dir, skip=(out_name,)))
    # the process table (in-flight runs, agent ships, processes homed to a planet) and the seats
    me, parent = os.getpid(), os.getppid()
    paths = [p for _, p, link in roots if not link] if cfg.get("processes", True) else []
    sdirs = _script_dirs(cfg) if paths else []
    rx = _AGENT_RX if cfg.get("agent_processes") else _INFLIGHT_RX
    rows = []
    for line in run_cmd(["ps", "-eo", "pid=,args="]).splitlines():
        pid, _, args = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) in (me, parent):
            continue
        args = args.strip()
        if rx.search(args) or any(d in args for d in sdirs) or any(p + "/" in args or args.endswith(p)
                                                       for p in paths):
            rows.append((int(pid), args))
    add("procs", sorted(rows))
    session = cfg.get("tmux_session")
    if session:
        windows = run_cmd(["tmux", "list-windows", "-t", session, "-F", "\x1f".join((
            "#{window_index}", "#{window_id}", "#{window_name}", "#{pane_id}",
            "#{pane_current_command}", "#{pane_pid}", "#{pane_current_path}"))])
        add("tmux", windows)
        for line in windows.splitlines():
            fields = re.split(r"\x1f|\\037", line, maxsplit=6)
            if len(fields) == 7 and re.fullmatch(r"%[0-9]+", fields[3]):
                pane = run_cmd(["tmux", "capture-pane", "-p", "-t", fields[3]])
                add(fields[3], pane.strip().splitlines()[-6:])
    return h.hexdigest()


def _memory_files(folder):
    """The census memory's files as they stand (the loop's own state file aside)."""
    return [e for e in _entries(folder) if not e[0].startswith("loop-")]


def loop_folders_unchanged(cfg, out, now):
    """True when every folder of every planet still has the key the census memory remembers it
    with, and every folder written in the last HOT_S seconds still adds up the same (an edit in
    place). Stops at the first difference. The page's own folder is left to loop_inputs."""
    if not cfg.get("_census_cache"):
        return False
    memory = CensusMemory(cfg["_census_cache"], now)
    roots, _, nested = planet_roots(cfg)
    out_dir = os.path.dirname(os.path.realpath(out)) if out else ""
    hot_after = now - HOT_S
    for _, path, link in roots:
        if link:
            continue
        exclude = set(nested.get(path, ()))
        shallow = os.path.realpath(path).startswith(SLOW_MOUNTS)
        records = memory.records(*CensusMemory.area_key(path, exclude, shallow))
        if not records:                             # never walked: nothing to compare with
            return False
        real_root = os.path.realpath(path)
        for rel, rec in records.items():
            if (os.path.join(real_root, rel) if rel else real_root) == out_dir:
                continue
            here = os.path.join(path, rel) if rel else path
            try:
                if _folder_key(os.stat(here)) != rec[0]:
                    return False
            except OSError:
                return False
            if rec[5] >= hot_after:
                regen = any(part in REGEN_DIRS for part in rel.split("/")) if rel else False
                now_rec = _scan(here, not rel, regen, shallow, exclude)
                if now_rec is None or now_rec[1:] != rec[1:]:
                    return False
    return True


def _written(cfg):
    """What a render writes that a render also reads (the census memory, the history it
    appends to once an hour), as it stands: taken after a loop render, so its own writes are not
    a change and anybody else's are."""
    stat = _stat_key(cfg.get("history_jsonl"))
    return {"memory": [list(e) for e in _memory_files(cfg.get("_census_cache"))],
            "history": list(stat) if stat else None}


def loop_unchanged(state_path, inputs, out, now, cfg):
    """True when the last full loop render started from the same cheap inputs less than
    LOOP_MAX_SKIP_S ago, its page is still there, nothing it writes has been written since by
    anyone else (a bridge render saves the census memory), and no folder changed since that
    memory was saved."""
    try:
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
        if not (state.get("inputs") == inputs and state.get("out") == os.path.realpath(out)
                and 0 <= now - float(state.get("rendered", 0)) < LOOP_MAX_SKIP_S
                and os.path.getsize(out) > 0):
            return False
        return state.get("written") == _written(cfg) and loop_folders_unchanged(cfg, out, now)
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def loop_record(state_path, inputs, out, now, cfg):
    """After a full loop render: the inputs it started from and what it wrote (_written)."""
    try:
        _state_dir(os.path.dirname(state_path), 0o700)
        write_atomic(state_path, json.dumps({"inputs": inputs, "out": os.path.realpath(out),
                                             "rendered": now, "written": _written(cfg)}
                                            ).encode("utf-8"))
    except OSError as exc:
        print(f"warn: loop state not saved: {exc}", file=sys.stderr)


def _git_head(path):
    """What HEAD points at, read from the files git keeps it in: [HEAD text, commit, packed-refs
    and refs/tags mtimes] (a new tag can change how rev-parse abbreviates the branch). None when
    the layout is anything else (reftable, a broken ref): then git itself is asked every time."""
    git = os.path.join(path, ".git")
    try:
        with open(os.path.join(git, "HEAD"), encoding="utf-8") as handle:
            head = handle.read().strip()
        if head.startswith("ref: "):
            ref, sha = head[5:], ""
            try:
                with open(os.path.join(git, ref), encoding="utf-8") as handle:
                    sha = handle.read().strip()
            except FileNotFoundError:
                with open(os.path.join(git, "packed-refs"), encoding="utf-8") as handle:
                    sha = next((line.split()[0] for line in handle
                                if line.rstrip("\n").endswith(" " + ref)), "")
        else:
            sha = head
        if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
            return None
        stamps = []
        for name in ("packed-refs", "refs/tags"):
            try:
                stamps.append(os.stat(os.path.join(git, name)).st_mtime_ns)
            except FileNotFoundError:
                stamps.append(0)
        return [head, sha] + stamps
    except (OSError, UnicodeError):
        return None


def git_facts(path, now, memory=None):
    """Branch, commits in 30 days, dirty tracked files, last commit age. With a memory, the three
    that only move with HEAD are kept per HEAD (the 30-day count is re-counted from the commit
    times against now); `git status` runs every time, an edit in place leaves no other trace."""
    if not os.path.isdir(os.path.join(path, ".git")):
        return None
    head = _git_head(path) if memory is not None else None
    kept = memory.git.get(path) if head else None
    if kept and kept.get("head") == head:
        lastc, branch = kept["lastc"], kept["branch"]
        n30 = sum(1 for t in kept["times"] if t >= now - 30 * 86400)
    else:
        lastc = run(["git", "-C", path, "log", "-1", "--format=%ct"]).strip()
        times = run(["git", "-C", path, "log", "--since=30 days ago", "--format=%ct"]).splitlines()
        n30 = len(times)
        branch = run(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if head:
            memory.git[path] = {"head": head, "lastc": lastc, "branch": branch,
                                "times": [int(t) for t in times if t.strip().isdigit()]}
            memory.git_changed = True
    dirty = len(run(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"]).splitlines())
    return {"branch": branch or "?", "commits_30d": n30, "dirty": dirty,
            "last_commit_age": age_days(int(lastc), now) if lastc.isdigit() else None}


def door_of(path):
    for d in DOORS:
        p = os.path.join(path, d)
        if os.path.isfile(p):
            try:
                with open(p, errors="replace") as handle:
                    lines = sum(1 for _ in handle)
            except OSError:
                lines = 0
            return {"file": d, "lines": lines}
    return None


# ---------- crons: every line, homed by the path its output lands in ----------
def cron_period_minutes(sched):
    """Rough period from the five fields; None when it cannot be read (e.g. @reboot)."""
    f = sched.split()
    if len(f) != 5:
        return None
    minute, hour, dom, mon, dow = f

    def slots(field, span):
        if field == "*":
            return span
        n = 0
        for part in field.split(","):
            m = re.fullmatch(r"(\*|\d+(?:-\d+)?)/(\d+)", part)
            if m:
                rng = m.group(1)
                if rng == "*":
                    lo, hi = 0, span - 1
                elif "-" in rng:
                    lo, hi = int(rng.split("-")[0]), int(rng.split("-")[1])
                else:
                    lo, hi = int(rng), span - 1
                n += max(1, (hi - lo) // int(m.group(2)) + 1)
            elif "-" in part:
                lo, hi = part.split("-")
                n += int(hi) - int(lo) + 1
            else:
                n += 1
        return max(1, n)
    per_day = slots(minute, 60) * slots(hour, 24)
    days = 7 if dow != "*" else (30 if dom != "*" else 1)
    return max(1, int(round(days * 1440 / per_day)))


def fmt_age(sec):
    sec = max(0, int(sec))
    if sec < 90:
        return f"{sec}s"
    if sec < 5400:
        return f"{sec // 60}m"
    if sec < 172800:
        return f"{sec // 3600}h"
    return f"{sec // 86400}d"


def _script_dirs(cfg):
    """The folders of the config's `script_dirs` ({folder: planet name}), absolute and without a trailing slash."""
    raw = (cfg or {}).get("script_dirs")
    return [os.path.abspath(os.path.expanduser(k)).rstrip("/") for k in raw if isinstance(k, str) and k] \
        if isinstance(raw, dict) else []


def script_homes(cfg, area_paths):
    """{folder: planet uid} for the config's `script_dirs`: a script that lives in the folder, or a process whose
    command line names it, is homed to that planet. A planet that is not on the map is skipped."""
    raw = (cfg or {}).get("script_dirs")
    out = {}
    for folder, planet in (raw.items() if isinstance(raw, dict) else ()):
        uid = "area:" + str(planet)
        if isinstance(folder, str) and folder and uid in area_paths:
            out[os.path.abspath(os.path.expanduser(folder)).rstrip("/")] = uid
    return out


def script_home(path, homes):
    """The planet a script path is homed to by a `script_dirs` folder, else None."""
    return next((uid for folder, uid in homes.items() if path.startswith(folder + "/")), None)


def home_for(p, area_paths):
    best = None
    for uid, ap in area_paths.items():
        if p == ap or p.startswith(ap + "/"):
            if best is None or len(ap) > len(area_paths[best]):
                best = uid
    return best


def cron_command(command):
    """Command cron logs/executes: unescape percent/backslash; split off stdin."""
    result, escaped = [], False
    for char in command:
        if escaped:
            if char in ("%", "\\"):
                result.pop()
            result.append(char)
            escaped = False
        elif char == "%":
            break
        else:
            result.append(char)
            escaped = char == "\\"
    return "".join(result)


def cron_firings(commands, cfg, now):
    """Last exact-command firing for this user, with offsets shared across renders.

    Cache only commands in this crontab. Track both files by inode so rotation reads
    the old file's unread tail once. A partial final line is retried next render.
    If the sidecar cannot be used, fall back to output mtimes instead of rescanning
    the entire system log on every render.
    """
    commands = set(commands)
    if not commands:
        return {}
    user = getpass.getuser()
    current = cfg.get("cron_syslog_file", "/var/log/syslog")
    if not current:
        return {}
    current = os.path.abspath(os.path.expanduser(current))
    side = os.path.join(os.path.expanduser(cfg.get("work_root") or HERE), ".container-map")
    cache_path = os.path.join(side, "cron-syslog.json")
    fired = {}
    try:
        _state_dir(side)
        with open(cache_path + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                with open(cache_path) as handle:
                    cache = json.load(handle)
                if (cache.get("version") != 1 or cache.get("user") != user
                        or cache.get("path") != current):
                    cache = {}
            except (OSError, ValueError, AttributeError):
                cache = {}
            prior_fired = cache.get("last_fired", {})
            if isinstance(prior_fired, dict):
                fired = {cmd: ts for cmd, ts in prior_fired.items()
                         if cmd in commands and isinstance(ts, (int, float)) and 0 < ts <= now}
            # A newly configured command may already have history before the saved offset.
            reseed = not commands.issubset(set(cache.get("commands", [])))
            previous = cache.get("files", {}) if not reseed else {}
            if not isinstance(previous, dict):
                previous = {}
            files, scanned = {}, 0
            for path in (current + ".1", current):
                try:
                    with open(path, "rb") as handle:
                        info = os.fstat(handle.fileno())
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        offset = 0
                        for old in previous.values():
                            if (isinstance(old, dict) and old.get("inode") == info.st_ino
                                    and old.get("device") == info.st_dev
                                    and isinstance(old.get("offset"), int)
                                    and 0 <= old["offset"] <= info.st_size):
                                offset = old["offset"]
                                break
                        handle.seek(offset)
                        while True:
                            start = handle.tell()
                            line = handle.readline()
                            if not line:
                                break
                            scanned += len(line)
                            if not line.endswith(b"\n"):
                                handle.seek(start)
                                break
                            if b"CRON[" not in line or b") CMD (" not in line:
                                continue
                            text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                            match = re.match(r"^(\S+)\s+\S+\s+CRON\[\d+\]: \(([^)]+)\) CMD \((.*)\)$", text)
                            if not match or match[2] != user or match[3] not in commands:
                                continue
                            when = _ships.parse_iso(match[1])
                            if when is None:
                                continue
                            try:
                                stamp = when.timestamp()
                            except (ValueError, OverflowError, OSError):
                                continue
                            if 0 < stamp <= now:
                                fired[match[3]] = max(fired.get(match[3], 0), stamp)
                        files[path] = {"inode": info.st_ino, "device": info.st_dev,
                                       "offset": handle.tell()}
                except OSError:
                    # Missing/rotating syslog is normal; output mtimes still work.
                    continue
            payload = {"version": 1, "path": current, "user": user,
                       "commands": sorted(commands), "files": files,
                       "last_fired": fired, "scan_bytes": scanned}
            fd, temporary = tempfile.mkstemp(prefix="cron-syslog-", suffix=".tmp", dir=side)
            with os.fdopen(fd, "w") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.write("\n")
            os.replace(temporary, cache_path)
    except (OSError, ValueError, TypeError):
        pass
    return fired


def cron_evidence_verdict(age, period):
    if period:
        k = age / 60.0 / period
        return "RUNNING" if k <= 2 else ("LATE" if k <= 10 else "STALE")
    return "RUNNING" if age < 86400 else "STALE"


def parse_crontab(now, area_paths, cfg):
    out = []
    homes = script_homes(cfg, area_paths)
    n = 0
    if cfg.get("crontab_file"):
        try:
            with open(cfg["crontab_file"], errors="replace") as handle:
                source = handle.read()
        except OSError:
            source = ""
    else:
        source = run(["crontab", "-l"])
    entries = []
    for raw in source.splitlines():
        line = raw.lstrip()
        if not line or line.startswith("#") or re.match(r"[A-Za-z_]+=", line):
            continue
        n += 1
        if line.startswith("@"):
            sched, cmd = line.split(None, 1) if " " in line else (line, "")
        else:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            sched, cmd = " ".join(parts[:5]), parts[5]
        entries.append((n, sched, cmd))
    firings = cron_firings((cron_command(cmd) for _, _, cmd in entries), cfg, now)
    for n, sched, raw_cmd in entries:
        cmd = cron_command(raw_cmd)
        paths = re.findall(r"(?:~|/)[^\s\"';&|>]+", cmd)
        paths = [p.replace("~", HOME, 1) if p.startswith("~") else p for p in paths]
        m = re.search(r">>?\s*([^\s;&|]+)", cmd)
        out_path = m.group(1) if m else None
        if out_path and out_path.startswith("~"):
            out_path = out_path.replace("~", HOME, 1)
        tool = ""
        for tok in cmd.split():
            base = os.path.basename(tok.rstrip(";"))
            if base in ("python3", "bash", "sh", "env", "nice", "flock", "timeout") or tok.startswith("-"):
                continue
            if re.match(r"[A-Za-z_][A-Za-z0-9_]*=[^$`'\"();]*\Z", tok):   # a plain NAME=value before the program
                continue                                                   # sets its environment: not the tool
            tool = base
            break
        if tool.startswith("f=") or tool.startswith("$"):
            tool = "inline shell"
        script_tok = next((tok for tok in cmd.split() if tok.startswith("/")), "")
        script_area = home_for(script_tok, area_paths) or script_home(script_tok, homes)
        area, homed_by, room = None, "", ""
        if out_path and out_path != "/dev/null":
            area = home_for(out_path, area_paths)
            if area:
                homed_by = "its log path"
        if not area:
            for p in paths:
                area = home_for(p, area_paths)
                if area:
                    homed_by = "a path in its command"
                    break
        if not area:
            script = next((tok for tok in cmd.split() if tok.startswith("/")), cmd.split()[0] if cmd.split() else "")
            area = home_for(script, area_paths)
            if not area:
                area = script_home(script, homes)
            homed_by = "its script location" if area else "no path evidence"
        homing_path = out_path if homed_by == "its log path" else (paths[0] if paths else "")
        ap = area_paths.get(area, "")
        if homing_path and ap and homing_path.startswith(ap + "/"):
            room = homing_path[len(ap) + 1:].split("/")[0]
        period = cron_period_minutes(sched)
        fired_age = max(0, now - firings[cmd]) if cmd in firings else None
        fired = f"{fmt_age(fired_age)} ago (cron syslog)" if fired_age is not None else ""
        evidence, logs = [], []
        if fired:
            evidence.append("fired " + fired)
        candidates = []
        if out_path and out_path != "/dev/null":
            if out_path.endswith(".cron.log"):
                candidates.append((out_path[:-len(".cron.log")] + ".log", "tool log"))
            candidates.append((out_path, "redirect log"))
        for path, kind in candidates:
            try:
                info = os.stat(path)
                if not stat.S_ISREG(info.st_mode) or not os.access(path, os.R_OK):
                    continue
                age = max(0, now - info.st_mtime)
                logs.append((age, kind))
                stale = cron_evidence_verdict(age, period) == "STALE"
                description = f"{fmt_age(age)} stale" if stale else f"written {fmt_age(age)} ago"
                empty = "; empty file" if info.st_size == 0 else ""
                evidence.append(f"{_words.page_word(os.path.basename(path))} {description} (mtime{empty})")
            except OSError:
                continue
        receipt_age, receipt_source = min(logs, key=lambda item: item[0]) if logs else (None, "none")
        if out_path == "/dev/null":
            evidence.append("output → /dev/null (no receipt)")
        elif not logs:
            evidence.append("output log missing or unreadable (no receipt)" if out_path
                            else "no output path in the line (no receipt)")
        if logs and fired_age is not None and fired_age <= receipt_age:
            receipt_source = "syslog"
        ages = [age for age in (receipt_age, fired_age) if age is not None]
        verdict = "UNKNOWN"
        if ages:
            verdict = cron_evidence_verdict(min(ages), period)
        elif out_path == "/dev/null":
            verdict = "NO RECEIPT"
        elif out_path:
            verdict = "NEVER"
        # the job by its script's (or program's) base name without the extension, and when it runs in words
        out.append({"uid": f"cron:{n}", "kind": "cron", "name": _words.job_name({"command": cmd, "tool": tool}),
                    "when": _words.schedule_words(sched),
                    "tool": tool if tool == "inline shell" else _words.page_word(tool),
                    "schedule": sched, "period_min": period, "command": _words.page_command(raw_cmd, cron=True)[:400],
                    "area": area, "room": _words.page_word(room), "homed_by": homed_by,
                    "log": _words.page_path(out_path or ""), "script": _words.page_path(script_tok),
                    "script_area": script_area,
                    "receipt": "; ".join(evidence), "receipt_age": receipt_age, "verdict": verdict,
                    "fired": fired, "fired_age": fired_age, "receipt_source": receipt_source,
                    "status": {"RUNNING": "LIVE", "LATE": "WARM", "STALE": "COLD", "NEVER": "DOWN",
                               "NO RECEIPT": "BLANK", "UNKNOWN": "BLANK"}[verdict],
                    "served": 0, "tour": sched, "idle": 0, "hp": 0, "cause": ""})
    return out


# ---------- processes: what is running right now, homed by the path it names ----------
def etime_to_s(s):
    """Parse [[dd-]hh:]mm:ss (BSD ps etime) to seconds. GNU etimes needs no parsing."""
    try:
        days, dash, rest = s.partition("-")
        if not dash:
            rest, days = days, 0
        else:
            days = int(days)
        parts = [int(p) for p in rest.split(":")]
        while len(parts) < 3:
            parts = [0] + parts
        h, m, sec = parts[-3:]
        return ((int(days) * 24 + h) * 60 + m) * 60 + sec
    except (ValueError, IndexError):
        return 0


def _ps_rows():
    """(pid, ppid, age_seconds, args). GNU ps first; BSD ps (macOS) fallback."""
    gnu = [l.split(None, 3)
           for l in run(["ps", "-eo", "pid,ppid,etimes,args", "--no-headers"]).splitlines()]
    gnu = [f for f in gnu if len(f) >= 4 and f[0].isdigit() and f[2].isdigit()]
    if gnu:
        return [(int(f[0]), int(f[1]), int(f[2]), f[3]) for f in gnu]
    bsd = []
    for line in run(["ps", "-ax", "-o", "pid,ppid,etime,command"]).splitlines():
        f = line.split(None, 3)
        if len(f) < 4 or not f[0].isdigit():
            continue
        bsd.append((int(f[0]), int(f[1]), etime_to_s(f[2]), f[3]))
    return bsd


def running_processes(area_paths, seat_pane_pids, exclude_pids=(), agents=True, homes=None):
    """Processes homed to a planet by the path their command line names. `agents` False (the owner
    did not turn agent_processes on) leaves out every agent run (_AGENT_RX): its pid, its tool and its
    command line, which can carry a private prompt, are not for the page."""
    rows = []
    ppid_of, args_of, et_of = {}, {}, {}
    for pid, ppid, age, args in _ps_rows():
        ppid_of[pid], args_of[pid], et_of[pid] = ppid, args, age

    def inside_seat(pid):
        for _ in range(8):
            if pid in seat_pane_pids:
                return True
            pid = ppid_of.get(pid, 0)
            if pid <= 1:
                return False
        return False
    my = os.getpid()
    homes = homes or {}
    for pid, args in args_of.items():
        if pid == my or pid == ppid_of.get(my) or inside_seat(pid) or pid in exclude_pids:
            continue
        if args.startswith(("ps ", "grep ", "ugrep ")):
            continue
        if not agents and _AGENT_RX.search(args):
            continue
        hit, hit_path = None, ""
        for uid, ap in sorted(area_paths.items(), key=lambda kv: -len(kv[1])):
            if ap + "/" in args or args.endswith(ap):
                hit, hit_path = uid, ap
                break
        if not hit:
            hit_path, hit = next(((d, uid) for d, uid in homes.items() if d in args), ("", None))
        if not hit:
            continue
        toks = args.split()
        name = ""
        for tok in toks:
            base = os.path.basename(tok)
            if base in ("python3", "python", "bash", "sh", "node", "env", "-c") or tok.startswith("-"):
                continue
            name = base
            break
        if not name:
            name = os.path.basename(toks[0]) if toks else "?"
        if toks and toks[0].endswith("bash") and len(toks) > 1 and toks[1] == "-c":
            name = "bash loop"
        room = ""
        m = re.search(re.escape(hit_path) + r"/([^/\s]+)", args)
        if m:
            room = m.group(1)
        shown = _words.page_word(name)                      # page fields from a command line: page-safe only
        name = shown if shown != _words.HIDDEN else "a program"
        rows.append({"pid": pid, "name": name[:20], "area": hit, "room": _words.page_word(room), "etimes": et_of[pid],
                     "args": _words.page_command(args)[:140]})
    groups = {}
    for r in rows:
        g = groups.setdefault((r["area"], r["name"]), {"pids": [], "etimes": 0, "args": r["args"], "room": r["room"]})
        g["pids"].append(r["pid"])
        g["etimes"] = max(g["etimes"], r["etimes"])
    out = []
    for (area, name), g in sorted(groups.items(), key=lambda kv: -kv[1]["etimes"]):
        cnt = len(g["pids"])
        out.append({"uid": f"proc:{area.split(':', 1)[1]}:{name}", "kind": "proc",
                    "name": name + (f" ×{cnt}" if cnt > 1 else ""), "proc_name": name, "count": cnt,
                    "pids": g["pids"][:6], "elapsed": fmt_age(g["etimes"]), "args": g["args"],
                    "area": area, "room": g["room"], "status": "WORKING", "served": 0,
                    "tour": f"running {fmt_age(g['etimes'])}", "idle": 0, "hp": 0, "cause": ""})
    return out


# ---------- dispatch reports that mention an area (a RECEIPT that a runner worked here) ----------
def dispatch_mentions(area_paths, dispatch_dir, work_root):
    counts = Counter()
    if not dispatch_dir or not os.path.isdir(dispatch_dir):
        return counts
    for f in os.listdir(dispatch_dir):
        if not f.endswith(".md"):
            continue
        try:
            with open(os.path.join(dispatch_dir, f), errors="replace") as handle:
                txt = handle.read(200_000)
        except OSError:
            continue
        for uid, ap in area_paths.items():
            if ap.startswith(work_root) and ap in txt:
                counts[uid] += 1
    return counts


# ---------- build ----------
def planet_roots(cfg):
    """(roots [(name, path, symlink target or "")], planet config by name, nested planets by
    path): the planets build() walks and the render loop checks."""
    # the planets: the owner's own list when there is one, else every top-level folder of the root
    roots, planet_cfg = [], {}
    if cfg.get("planets"):
        for p in cfg["planets"]:
            if os.path.isdir(p["path"]):          # a listed planet is walked even through a symlink: it is the user's by choice
                roots.append((p["name"], p["path"], ""))
                planet_cfg[p["name"]] = p
    else:
        try:
            for e in sorted(os.scandir(cfg["work_root"]), key=lambda e: e.name.lower()):
                if e.name == ".container-map":          # the tool's own sidecar under the root is not an area
                    continue
                if e.is_dir(follow_symlinks=False):
                    roots.append((e.name, e.path, ""))
                elif e.is_symlink() and os.path.isdir(e.path):
                    roots.append((e.name, e.path, os.readlink(e.path)))
        except OSError:
            pass
    for name, path in cfg.get("extra_areas", {}).items():
        if os.path.isdir(path) and not any(n == name for n, _, _ in roots):
            roots.append((name, path, ""))
    # a planet inside another planet belongs to itself: the parent's walk skips it
    real_paths = [p for _, p, link in roots if not link]
    nested = {p: {q for q in real_paths if q != p and q.startswith(p.rstrip("/") + "/")} for p in real_paths}
    return roots, planet_cfg, nested


def build(now, seats, roster, cfg=None):
    """seats: list of {uid, cwd, pane_pid}. roster: runners.json view from render-game. cfg: container.json."""
    if cfg is None:
        import config as _config
        cfg = _config.load()
    WORK = cfg["work_root"]
    tags, tags_asof = load_container_tags(cfg.get("tags_md"))
    areas_json = load_areas_json(cfg["areas_json"] if "areas_json" in cfg else AREAS_JSON)   # kinds + workers (not the container config)
    workers_cfg = areas_json.get("workers", {}) if isinstance(areas_json.get("workers"), dict) else {}
    areas_cfg = areas_json.get("areas", {})

    roots, planet_cfg, nested = planet_roots(cfg)

    def tag_of(name, path):
        """the roots-map row for this planet: its own name, else the top-level folder it lives in"""
        if name in tags:
            return tags[name]
        try:
            rel = os.path.relpath(path, WORK)
        except ValueError:
            return {}
        top = rel.split(os.sep)[0] if not rel.startswith("..") else ""
        return tags.get(top, {})

    # homing uses real areas only: a symlinked root is a pointer, never a home
    area_paths = {f"area:{n}": p for n, p, link in roots if not link}
    seat_pane_pids = {s.get("pane_pid") for s in seats if s.get("pane_pid")}
    crons = parse_crontab(now, area_paths, cfg)
    procs = (running_processes(area_paths, seat_pane_pids, cfg.get("_agent_ship_pids", ()),
                                agents=bool(cfg.get("agent_processes")),
                                homes=script_homes(cfg, area_paths))
             if cfg.get("processes", True) else [])
    mentions = dispatch_mentions(area_paths, cfg.get("dispatch_dir"), WORK)

    areas = []
    memory = CensusMemory(cfg["_census_cache"], now) if cfg.get("_census_cache") else None
    for name, path, link in roots:
        uid = f"area:{name}"
        if link:
            areas.append({"uid": uid, "kind": "area", "name": name, "path": path, "alias_of": link,
                          "status": "COLD", "life": "ALIAS", "files": 0, "dirs": 0, "size": "",
                          "rooms": [], "root_files": [], "ext": [], "door": None, "worker": None,
                          "crons": [], "procs": [], "seats": [], "served": 0, "tour": f"symlink → {link}",
                          "idle": 0, "hp": 0, "cause": "", "age": None})
            continue
        tot, ext, rooms_raw, child, root_files = census(path, now, nested.get(path, ()), memory)
        age = age_days(tot["newest"], now)
        lf = life(age, tot["files"])
        code_share = sum(v for k, v in ext.items() if k in CODE_EXT) / max(1, tot["files"])
        acfg = areas_cfg.get(name, {}) if isinstance(areas_cfg.get(name), dict) else {}
        pcfg = planet_cfg.get(name, {})
        md_share = ext.get(".md", 0) / max(1, tot["files"])
        data_share = sum(v for k, v in ext.items() if k in DATA_EXT) / max(1, tot["files"])
        media_share = sum(v for k, v in ext.items() if k in MEDIA_EXT) / max(1, tot["files"])
        # a guess when the owner has not ruled: half or more data files or media files, else mostly code files or a
        # git repo that is not mostly notes, else mostly notes. An areas.json kind the map does not know is no ruling.
        is_repo = os.path.isdir(os.path.join(path, ".git"))
        own_kind = acfg.get("kind") if acfg.get("kind") in AREA_KINDS + ("unknown",) else None
        kind = own_kind or pcfg.get("kind") or (
            "data" if data_share >= 0.5 else "media" if media_share >= 0.5
            else "code" if code_share >= 0.4 or (is_repo and md_share < 0.5) else "knowledge" if md_share >= 0.5 else "unknown")
        guessed = (bool(acfg.get("guessed", True)) if own_kind else True) and not pcfg.get("kind")
        glyph = acfg.get("glyph") if acfg.get("glyph") in AREA_GLYPHS else None
        trow = tag_of(name, path)
        worker = acfg.get("worker")
        wcfg = workers_cfg.get(worker, {}) if worker else {}
        wstatus = (roster.get(worker) or {}).get("status", "LIVE") if worker else None
        rooms = []
        shallow = tot.get("shallow", False)
        for rn, rs in sorted(rooms_raw.items(), key=lambda kv: (-kv[1]["files"], kv[0].lower())):
            rp = os.path.join(path, rn)
            kids, nkids = room_children(path, rn, child, now, shallow)
            rage = age_days(rs["newest"], now)
            rlife = "UNWALKED" if shallow else life(rage, rs["files"])
            rooms.append({"uid": f"dir:{name}/{rn}", "kind": "dir", "name": rn, "path": rp,
                          "files": None if shallow else rs["files"], "size": "" if shallow else human(rs["bytes"]),
                          "age": rage, "life": rlife, "status": rlife, "walked": not shallow,
                          "door": door_of(rp),
                          "git": (git_facts(rp, now, memory)
                                  if os.path.isdir(os.path.join(rp, ".git")) else None),
                          "hidden": rn.startswith("."), "children": kids, "children_total": nkids,
                          "area": uid, "served": 0, "tour": "", "idle": rage if rage is not None else 0,
                          "hp": 0, "cause": ""})
        root_files.sort(key=lambda t: -t[2])
        if shallow:
            lf = life(age, len(root_files)) if root_files else "EMPTY"
        a = {"uid": uid, "kind": "area", "name": name, "path": path, "alias_of": "",
             "status": lf, "life": lf, "age": age, "newest_path": tot["newest_path"],
             "files": tot["files"], "dirs": tot["dirs"], "size": human(tot["bytes"]), "capped": tot["capped"],
             "shallow": shallow,
             "ext": [{"ext": k, "n": v, "pct": round(100 * v / max(1, tot["files"]))} for k, v in ext.most_common(5)],
             "code_share": round(100 * code_share), "regen_share": round(100 * tot.get("regen", 0) / max(1, tot["files"])),
             "tag": pcfg.get("tag") or trow.get("tag"), "tag_desc": pcfg.get("desc") or trow.get("desc"), "tag_asof": tags_asof,
             "area_kind": kind, "kind_guessed": guessed, **({"glyph": glyph} if glyph else {}),
             "worker": worker, "worker_cmd": wcfg.get("cmd", ""), "worker_why": wcfg.get("why", ""),
             "worker_status": wstatus, "worker_note": acfg.get("note", ""),
             "door": door_of(path), "git": git_facts(path, now, memory),
             "rooms": rooms, "root_files": [[f, human(s), age_days(m, now)] for f, s, m in root_files[:ROOT_FILE_CAP]],
             "root_files_total": len(root_files),
             "crons": [c["uid"] for c in crons if c["area"] == uid],
             "procs": [p["uid"] for p in procs if p["area"] == uid],
             "seats": [s["uid"] for s in seats if s.get("cwd") and home_for(s["cwd"], area_paths) == uid],   # the deepest planet owns the seat
             "dispatch_mentions": mentions.get(uid, 0),
             "served": 0, "tour": "", "idle": age if age is not None else 0, "hp": 0, "cause": ""}
        a["tour"] = f"{a['files']} files · newest {age}d ago (mtime)" if age is not None else "no files"
        areas.append(a)

    # ---------- layout: blocks packed in rows, most recently touched first ----------
    order = sorted(areas, key=lambda a: (a["life"] == "ALIAS", a["age"] if a.get("age") is not None else 10 ** 6, -a["files"]))
    x, y, row_h = ORIGIN[0], ORIGIN[1], 0
    blocks, chars = [], []
    for a in order:
        n_rooms, n_tasks = len(a["rooms"]), len(a["crons"]) + len(a["procs"])
        iw = max(3, min(8, math.ceil(math.sqrt(max(1, n_rooms + n_tasks)))))
        rr = math.ceil(n_rooms / iw) if n_rooms else 0
        tr = math.ceil(n_tasks / iw) if n_tasks else 0
        w, h = iw + 2, max(3, 2 + rr + tr + (1 if rr and tr else 0) + 1)
        if x + w > ORIGIN[0] + ROW_WIDTH and x != ORIGIN[0]:
            x, y, row_h = ORIGIN[0], y + row_h + 2, 0
        seats_here = len(a["seats"])
        live_tasks = sum(1 for c in crons if c["area"] == a["uid"] and c["verdict"] == "RUNNING")
        heat = min(100, seats_here * 40 + len(a["procs"]) * 20 + live_tasks * 6 + (30 if a["life"] == "LIVE" else 0))
        blocks.append({"uid": a["uid"], "key": a["uid"], "label": a["name"].upper(), "x": x, "y": y, "w": w, "h": h,
                       "life": a["life"], "hp": heat, "files": a["files"], "seats": seats_here})
        a["hp"] = heat
        a.update({"gx": x + 1, "gy": y + 1, "hall": a["uid"], "block": {"x": x, "y": y, "w": w, "h": h}})
        chars.append(a)
        i = 0
        for room in a["rooms"]:
            room.update({"gx": x + 1 + (i % iw), "gy": y + 2 + (i // iw), "hall": a["uid"]})
            chars.append(room)
            i += 1
        ty = y + 2 + rr + (1 if rr and tr else 0)
        i = 0
        for t in [c for c in crons if c["area"] == a["uid"]] + [p for p in procs if p["area"] == a["uid"]]:
            t.update({"gx": x + 1 + (i % iw), "gy": ty + (i // iw), "hall": a["uid"]})
            chars.append(t)
            i += 1
        x += w + 1
        row_h = max(row_h, h)
    homed = {c["uid"] for c in chars}
    for t in crons + procs:            # a task homed to an area that failed to render still shows
        if t["uid"] not in homed:
            t.update({"gx": ORIGIN[0], "gy": y + row_h + 2, "hall": t.get("area") or ""})
            chars.append(t)
    summary = {"areas": len(areas), "rooms": sum(len(a["rooms"]) for a in areas),
               "crons": len(crons), "crons_running": sum(1 for c in crons if c["verdict"] == "RUNNING"),
               "crons_no_receipt": sum(1 for c in crons if c["receipt_source"] == "none"),
               "crons_fired_24h": sum(1 for c in crons if c["fired_age"] is not None and c["fired_age"] <= 86400),
               "procs": sum(p["count"] for p in procs), "proc_groups": len(procs),
               "doors": sum(1 for a in areas if a.get("door")), "workers": sum(1 for a in areas if a.get("worker")),
               "tags_asof": tags_asof,
               "untagged": sum(1 for a in areas if not a.get("tag") and a["name"] not in cfg.get("extra_areas", {})),
               "guessed": sum(1 for a in areas if a.get("kind_guessed") and a["life"] != "ALIAS")}
    if memory is not None:
        memory.save()
    return chars, blocks, summary


def page_world(world):
    """The world as the bridge sends it to the page: a planet lists each room by uid where that
    room is also a character with the same content (the page's joinRooms puts the object back),
    so the page's largest duplicate travels once. The world itself is left as it is."""
    chars = world.get("chars") if isinstance(world, dict) else None
    if not isinstance(chars, list):
        return world
    by_uid = {}
    for c in chars:
        if isinstance(c, dict) and isinstance(c.get("uid"), str):
            by_uid[c["uid"]] = None if c["uid"] in by_uid else c   # a shared uid is never a ref
    out = []
    for c in chars:
        if isinstance(c, dict) and c.get("kind") == "area" and isinstance(c.get("rooms"), list):
            refs = []
            for r in c["rooms"]:
                uid = r.get("uid") if isinstance(r, dict) else None
                same = by_uid.get(uid) if isinstance(uid, str) else None
                refs.append(uid if same is not None and (same is r or same == r) else r)
            c = dict(c, rooms=refs)
        out.append(c)
    return dict(world, chars=out)


if __name__ == "__main__":
    t0 = time.time()
    chars, blocks, summary = build(time.time(), [], {})
    print(json.dumps(summary, indent=1))
    print(f"{len(chars)} characters, {len(blocks)} blocks, {time.time() - t0:.2f}s")
