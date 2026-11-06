#!/usr/bin/env python3
"""render-game.py — the container as a literal, playable game.

Read-only. Writes game.html (self-contained, no network, no assets) and, at most once an hour,
one line of per-root history (history.py) so the map can show what is getting worse.

Every character in the game is a real seat, runner or machine. Every stat on a character
is counted from disk at render time: jobs served from dispatch report filenames, tour dates
from those same filenames, status and cause-of-death quoted verbatim from runners.json,
live state from a tmux pane probe, quests from parked-decision lines in FLEET.md / DIGEST.md.

Nothing about a character is invented. The GAME is the interface; the DATA is the container.
"""
import base64, html, json, os, re, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config as _config
import themepacks as _themepacks
import ships as _ships                      # comms_line() cleans the seat panes below
CFG = _config.load()                      # every path below comes from container.json (or --config)
SKIN = _themepacks.active_skin(CFG, lambda msg: print(f"warn: {msg}", file=sys.stderr))
if SKIN is None:
    print("warn: no skin could be loaded (is skins/ missing?); the page draws plain craft", file=sys.stderr)
OUT = CFG["out"] or os.path.join(HERE, "game.html")
DISPATCH = CFG["dispatch_dir"]
RUNNERS = CFG["runners_json"]
DIGEST = CFG["digest"]
FLEET = CFG["fleet"]
READS = CFG["reads_jsonl"]
RELAY = CFG["relay_dir"]
SESSION = CFG["tmux_session"]
SEATS = CFG["seats_json"]
NOW = time.time()
# --cached (the bridge) and --loop-render (world.sh): remember unchanged folders between renders in
# ROOT/.container-map/render-cache; --loop-render also skips a render whose inputs did not change
CACHE_DIR = os.path.join(os.path.expanduser(CFG["work_root"] or HERE), ".container-map",
                         "render-cache")
LOOP = "--loop-render" in sys.argv and "--json" not in sys.argv
CACHED = LOOP or "--cached" in sys.argv
if LOOP:
    import hashlib as _hashlib
    import districts as _districts
    _LOOP_STATE = os.path.join(CACHE_DIR, "loop-%s.json" % _hashlib.sha1(
        os.path.realpath(OUT).encode("utf-8", "surrogateescape")).hexdigest()[:12])
    _LOOP_CFG = dict(CFG, _census_cache=CACHE_DIR)
    _LOOP_INPUTS = _districts.loop_inputs(_LOOP_CFG, OUT, HERE, NOW)
    if _districts.loop_unchanged(_LOOP_STATE, _LOOP_INPUTS, OUT, NOW, _LOOP_CFG):
        os.utime(os.path.realpath(OUT), (NOW, NOW))     # still true as of now (the bridge reads it)
        print(f"{OUT}  unchanged: same inputs as its last render; touched")
        sys.exit(0)
TMUX_SEP = "\x1f"
# A pane that is waiting on a human: permission prompts and questions, as the CLIs print them.
WAIT_RX = (r"do you want to|allow (this|command|edit)|(?<![a-z-])approve (this|the|edit|command)|approve\?"
           r"|\(y/n\)|\[y/n\]|\[y/N\]|\[Y/n\]|1\. yes|esc to cancel|waiting for (your )?input|press enter to")
# A human's unsent text in a ❯ composer: the pane is not waiting on the agent, it is waiting on YOU.
# Codex uses › with rotating placeholder suggestions that read like drafts, so only ❯ counts.
DRAFT_RX = r"❯[ \t]+(?![│|╭╮╰╯─\s])\S"

def has_draft(pane: str) -> bool:
    """True only if the COMPOSER line (a ❯ line among the bottom 3) carries real text.
    Scrollback lines that merely quote a ❯ do not count; a box border after ❯ does not count."""
    tail = [l for l in pane.splitlines() if l.strip()][-3:]
    comp = [l for l in tail if "❯" in l]
    return bool(comp) and bool(re.search(DRAFT_RX, comp[-1]))


def sh(cmd, timeout=20):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:
        return ""


def run(args, timeout=20):
    """Run a fixed argv command and return stdout; never put tmux identities in a shell."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def load_seat_policies():
    """Load valid v1 policies by tmux window id; malformed entries fail closed."""
    policies = {}
    if not SESSION:
        return policies                     # no tmux session configured: no seats, no policy, no warning
    try:
        raw = json.load(open(SEATS, encoding="utf-8"))
        if (not isinstance(raw, dict) or raw.get("version") != 1 or
                not {"version", "session", "seats"} <= set(raw) or
                not set(raw) <= {"version", "session", "seats", "machines"} or
                raw.get("session") != SESSION or not isinstance(raw.get("seats"), list)):
            raise ValueError("expected a version 1 seats list")
        # A seat is pinned by window_id, OR matched by expected_name when exactly one live window
        # carries that name (window ids drift whenever a window is recreated). Zero or several
        # matches fail closed. Keyed by window_id after resolution.
        live = {}
        for line in (run(["tmux", "list-windows", "-t", SESSION, "-F", "#{window_id}\t#{window_name}"]) if SESSION else "").splitlines():
            if "\t" in line:
                wid_, nm_ = line.split("\t", 1); live.setdefault(nm_, []).append(wid_)
        for item in raw["seats"]:
            required = {"id", "expected_name", "role", "talk_commands"}
            if not isinstance(item, dict) or not required <= set(item):
                continue
            sid, wid = item.get("id"), item.get("window_id")
            name, role = item.get("expected_name"), item.get("role")
            quick, commands = item.get("quick_key"), item.get("talk_commands")
            if wid is None and isinstance(name, str) and len(live.get(name, [])) == 1:
                wid = live[name][0]
            valid = (isinstance(sid, str) and re.fullmatch(r"[a-zA-Z0-9._-]+", sid) and
                     isinstance(wid, str) and re.fullmatch(r"@[0-9]+", wid) and
                     isinstance(name, str) and name and isinstance(role, str) and role and
                     (quick is None or (isinstance(quick, str) and re.fullmatch(r"[0-9]", quick))) and
                     isinstance(commands, list) and
                     all(isinstance(c, str) and c for c in commands))
            if valid and wid not in policies and not any(p["id"] == sid for p in policies.values()):
                policies[wid] = {"id": sid, "window_id": wid, "expected_name": name,
                                 "role": role, "quick_key": quick,
                                 "talk_commands": commands}
        # door seats: windows door.sh opened IN a directory (same rule and uid as the bridge)
        try:
            door_talk = json.load(open(CFG["areas_json"])).get("door_talk_commands") or ["claude", "node", "codex", "muse", "grok", "omp"]
        except Exception:
            door_talk = ["claude", "node", "codex", "muse", "grok", "omp"]
        for nm_, wids in live.items():
            if nm_.startswith("door-") and len(wids) == 1 and wids[0] not in policies and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", nm_):
                policies[wids[0]] = {"id": nm_, "window_id": wids[0], "expected_name": nm_, "role": "worker",
                                     "quick_key": None, "talk_commands": list(door_talk), "door": True}
    except Exception as e:
        print(f"warn: seats.json invalid: {e}", file=sys.stderr)
    return policies


def command_matches(command, prefixes):
    base = os.path.basename(command or "")
    return any(base == p or base.startswith(p + "-") or base.startswith(p + ".")
               for p in prefixes)


SEAT_POLICIES = load_seat_policies()


# ---------- runners: jobs served + tour of duty, from report filenames ----------
served, first, last = defaultdict(int), {}, {}
if os.path.isdir(DISPATCH):
    for f in os.listdir(DISPATCH):
        m = re.match(r"d-(\d{8})-\d+-([a-z0-9-]+)\.md$", f)
        if not m:
            continue
        day, who = m.group(1), m.group(2)
        served[who] += 1
        first[who] = min(first.get(who, day), day)
        last[who] = max(last.get(who, day), day)

# ---------- runners.json: role, backing, status marker, cause of death ----------
roster = {}
if os.path.isfile(RUNNERS):
    try:
        raw = json.load(open(RUNNERS))
        raw = raw.get("runners", raw)
        for name, v in raw.items():
            if name.startswith("_"):
                continue
            desc = v if isinstance(v, str) else (
                v.get("desc") or v.get("description") or v.get("cmd") or "")
            desc = re.sub(r"\s+", " ", str(desc)).strip()
            dead = "🔴" in desc
            mk = re.search(r"(DOWN|BROKEN|RETIRING|DEAD|FROZEN)", desc)
            cause = ""
            if dead:
                # the clause after the em dash is the owner's own stated cause
                c = re.split(r"—|--", desc, maxsplit=1)
                cause = c[1].strip() if len(c) > 1 else desc
            roster[name] = {
                "role": desc.split("—")[0].replace("🔴", "").strip()[:70] or "runner",
                "status": (mk.group(1) if mk else ("DOWN" if dead else "LIVE")),
                "cause": cause[:150],
            }
    except Exception as e:
        print(f"warn: runners.json unreadable: {e}", file=sys.stderr)

for who in served:                       # a runner with reports but no registry entry
    roster.setdefault(who, {"role": "runner (no registry entry)", "status": "LIVE", "cause": ""})

CHARS = []
maxserved = max(served.values()) if served else 1
for name, r in roster.items():
    d_first, d_last = first.get(name), last.get(name)
    fmt = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}" if d else None
    idle_days = None
    if d_last:
        try:
            idle_days = int((NOW - datetime.strptime(d_last, "%Y%m%d").timestamp()) // 86400)
        except Exception:
            pass
    CHARS.append({
        "name": name, "kind": "runner", "role": r["role"], "status": r["status"],
        "cause": r["cause"], "served": served.get(name, 0),
        "tour": f"{fmt(d_first)} → {fmt(d_last)}" if d_first else "never dispatched",
        "idle": idle_days, "hp": round(100 * served.get(name, 0) / maxserved),
    })

# ---------- seats: every live tmux window, enriched by fail-closed stable policy ----------
wins = "" if not SESSION else run(["tmux", "list-windows", "-t", SESSION, "-F",
            TMUX_SEP.join(("#{window_index}", "#{window_id}", "#{window_name}",
                           "#{pane_id}", "#{pane_current_command}", "#{pane_pid}",
                           "#{window_activity}", "#{pane_current_path}"))])
SEAT_CWD = []          # {uid, cwd, pane_pid}: which district each seat is sitting in
# A window whose foreground program is a plain shell has no agent in it: an empty berth, not a ship
# (the page draws it as one and leaves it out of the agent counts).
BARE_SHELLS = frozenset(("bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh", "-bash", "-zsh", "-sh"))
for line in wins.splitlines():
    # tmux octal-escapes non-printable bytes in -F output, so the 0x1F separator arrives
    # as the four characters backslash-0-3-7. Accept either form. The path comes last (it may
    # hold anything); a tmux that answers without the activity field still parses.
    fields = re.split(r"\x1f|\\037", line, maxsplit=7)
    if len(fields) == 7:
        fields.insert(6, "")
    if len(fields) != 8:
        continue
    idx, window_id, nm, pane_id, command, pane_pid, activity, cwd = fields
    activity = int(activity) if activity.isdigit() else None
    if (not re.fullmatch(r"[0-9]+", idx) or not re.fullmatch(r"@[0-9]+", window_id) or
            not re.fullmatch(r"%[0-9]+", pane_id)):
        continue
    pane = "\n".join(run(["tmux", "capture-pane", "-p", "-t", pane_id]).splitlines()[-6:])
    low = pane.lower()
    if re.search(r"usage limit|rate limit|session limit|resets \d|quota", low):
        st = "LIMITED"
    elif re.search(r"traceback|command not found|permission denied|fatal:", low):
        st = "ERROR"
    elif re.search(WAIT_RX, low):
        st = "WAITING"           # an approval or a question is sitting in the pane
    elif has_draft(pane):
        st = "DRAFT"             # your own unsent text is sitting in the composer
    elif re.search(r"esc to interrupt|thinking|working|tokens used", low):
        st = "WORKING"
    elif not pane.strip():
        st = "BLANK"
    else:
        st = "IDLE"
    tail = re.sub(r"[^\x20-\x7e]", "", pane.strip().splitlines()[-1] if pane.strip() else "")[:70]
    policy = SEAT_POLICIES.get(window_id)
    alias = policy["id"] if policy else None
    seat_id = alias or window_id
    talkable, reason = False, "window is not configured for talk"
    if policy:
        if nm != policy["expected_name"]:
            reason = "live window name does not match configured seat"
        elif not policy["talk_commands"]:
            reason = f"{policy['role']} seat is view only"
        elif not command_matches(command, policy["talk_commands"]):
            reason = f"current command {command or '(none)'} is not an allowed composer"
        else:
            talkable, reason = True, ""
    CHARS.append({"uid": f"seat:{seat_id}", "name": nm, "kind": "seat",
                  "source": "tmux",
                  "role": (f"{policy['role']} · tmux window {idx}" if policy else f"tmux window {idx}"),
                  "seat_role": policy["role"] if policy else "unconfigured",
                  "seat": seat_id, "alias": alias, "configured": bool(policy),
                  "window_index": int(idx), "window_id": window_id, "pane_id": pane_id,
                  "command": command, "quick_key": policy.get("quick_key") if policy else None,
                  "composable": bool(policy and policy["talk_commands"]),
                  "talkable": talkable, "talk_reason": reason, "status": st,
                  "cause": "", "served": 0, "tour": "live now",
                  # whole days since the window last saw activity, for a seat at rest (a busy one is 0)
                  "idle": (max(0, int((NOW - activity) // 86400)) if activity and st in ("IDLE", "BLANK") else 0),
                  "activity": activity, "shell": os.path.basename(command or "") in BARE_SHELLS, "hp": 100,
                  "pane": tail, "line": _ships.comms_line(pane), "cwd": cwd})
    SEAT_CWD.append({"uid": f"seat:{seat_id}", "cwd": cwd,
                     "pane_pid": int(pane_pid) if pane_pid.isdigit() else 0})

# ---------- machines: relay channels, frames carried ----------
if os.path.isdir(RELAY):
    for ch in sorted(os.listdir(RELAY)):
        ib = os.path.join(RELAY, ch, "inbox.md")
        if ch == "archive" or not os.path.isfile(ib):
            continue
        frames = sum(1 for l in open(ib, errors="replace") if l.startswith("===END==="))
        days = int((NOW - os.path.getmtime(ib)) // 86400)
        if frames == 0 and days > 60:
            continue                      # never carried anything and long cold: not a character
        CHARS.append({"name": ch, "kind": "channel", "role": "relay channel",
                      "status": "LIVE" if days <= 30 else "COLD", "cause": "",
                      "served": frames, "tour": f"last traffic {days}d ago",
                      "idle": days, "hp": min(100, frames * 12)})

# ---------- machines: nodes of the fleet ----------
# Liveness comes from the freshest evidence already on disk for that node (its relay channels
# and heartbeat files). We do NOT ssh from the render loop: a 15s rebuild cannot afford a
# network round trip per node, and a timeout would read as "dead" when it only means "slow".
NODES = dict(CFG["machines"]) if CFG["machines"] else {CFG["name"].lower(): []}
for node, chans in NODES.items():
    best, frames = None, 0
    for c in chans:
        ib = os.path.join(RELAY, c, "inbox.md")
        if os.path.isfile(ib):
            frames += sum(1 for l in open(ib, errors="replace") if l.startswith("===END==="))
            mt = os.path.getmtime(ib)
            best = mt if best is None else max(best, mt)
    days = int((NOW - best) // 86400) if best else None
    CHARS.append({"name": node, "kind": "machine",
                  "role": "fleet node (evidence: relay traffic)",
                  "status": "LIVE" if (days is not None and days <= 2) else
                            ("COLD" if days is not None and days <= 30 else "DOWN"),
                  "cause": "" if (days is not None and days <= 30) else "no relay traffic in 30d+",
                  "served": frames, "tour": f"last relay traffic {days}d ago" if days is not None
                            else "no relay channel", "idle": days if days is not None else 999,
                  "hp": min(100, frames * 8)})

# ---------- portals: gateways to remote machines (muse: portal kind only) ----------
try:
    REMOTE_MACHINES = list(json.load(open(SEATS)).get("machines", {}).keys()) if (SESSION and SEATS and os.path.isfile(SEATS)) else []
except Exception:
    REMOTE_MACHINES = []
_machine_status = {c["name"]: c["status"] for c in CHARS if c.get("kind") == "machine"}
for _m in REMOTE_MACHINES:
    _st = _machine_status.get(_m, "DOWN")
    CHARS.append({"uid": f"portal:{_m}", "name": f"{_m} gate", "kind": "portal",
                  "machine": _m, "role": f"gateway to {_m}",
                  "status": _st, "cause": "", "served": 0,
                  "tour": f"gateway to {_m}", "idle": 0, "hp": 0})

# ---------- districts: every directory of the container, with its door, worker, rooms and tasks ----------
# Replaces the old CRON YARD (crons grouped by tool) and REPO DISTRICT (six hard-coded repos):
# every cron line and every running process is now homed to the area it touches, and every
# root of /work is an area whether or not it is a repo. See districts.py for the evidence rules.
import districts as _districts
_THREAD_PIDS = set()               # thread agents and reviewers: drawn as tasks, not as ships
THREAD_SHIPS = []
SHIP_CHARS, SHIP_SOURCES = _ships.build(CFG, NOW, [], dropped=_THREAD_PIDS, threads=THREAD_SHIPS)
# An agent's argv can contain a private prompt. Its metadata ship replaces the generic
# process row, which otherwise includes argv, and never serializes that prompt.
_agent_pids = {c["pid"] for c in SHIP_CHARS if c.get("source") == "process"} | _THREAD_PIDS
_district_cfg = dict(CFG, _agent_ship_pids=_agent_pids)
# cached: the walk and the git facts remember what did not change since the last render
# (districts.CensusMemory); a one-shot render counts everything afresh
if CACHED:
    _district_cfg["_census_cache"] = CACHE_DIR
try:
    DISTRICT_CHARS, DISTRICT_BLOCKS, DISTRICT_SUMMARY = _districts.build(NOW, SEAT_CWD, roster, _district_cfg)
except Exception as e:                         # the rest of the world still renders
    print(f"warn: districts failed: {e}", file=sys.stderr)
    DISTRICT_CHARS, DISTRICT_BLOCKS, DISTRICT_SUMMARY = [], [], {"error": str(e)[:200]}
for c in DISTRICT_CHARS:
    c.setdefault("role", {"area": "district", "dir": "room", "cron": "scheduled task",
                          "proc": "running process"}.get(c["kind"], c["kind"]))
CHARS.extend(DISTRICT_CHARS)
_ships.dock(SHIP_CHARS, DISTRICT_CHARS)
CHARS.extend(SHIP_CHARS)

# ---------- the hub's office: rooms, one per tool class ----------
# The white avatar is the hub seat itself. A PostToolUse hook walks it to the unit it just
# touched, or, when the call touched nothing that has a character, to the room for that tool
# class: reading in the library, running commands at the terminal, editing at the desk,
# talking to seats and people in comms. A room has no stats; it is a place the work happens.
for room, role in (("library", "reading & searching"), ("terminal", "running commands"),
                   ("desk", "editing & writing"), ("comms", "talking to seats & people")):
    CHARS.append({"name": room, "kind": "room", "role": role, "status": "LIVE", "cause": "",
                  "served": 0, "tour": "hub office", "idle": 0, "hp": 0})

# ---------- quests: parked decisions, aged by the line that parked them ----------
GATE = re.compile("|".join(re.escape(m) for m in CFG["decision_markers"] if m) or r"(?!x)x", re.I)
DATE = re.compile(r"2[0-9]{3}-[0-9]{2}-[0-9]{2}")


def _days_ago(line):
    """Whole days since the first YYYY-MM-DD on the line; -1 (undated) when there is none or
    it is not a real date."""
    dm = DATE.search(line)
    if not dm:
        return -1
    try:
        return int((NOW - datetime.strptime(dm.group(), "%Y-%m-%d").timestamp()) // 86400)
    except ValueError:
        return -1


rows, seen = [], set()
# a cited approval ("per the owner"); the owner's own first name (config "owner") counts as well
_owner = re.escape(str(CFG.get("owner") or "").strip())
_who = r"(?:the\s+)?owner" + ("|" + _owner if _owner else "")
APPROVED = re.compile(r"per\s+(?:%s)|(?:%s)-approved|approved by (?:%s)" % (_who, _who, _who), re.I)
for path in (FLEET, DIGEST):
    if not os.path.isfile(path):
        continue
    base = os.path.basename(path)
    for n, line in enumerate(open(path, errors="replace"), 1):
        m = GATE.search(line)
        if not m:
            continue
        ctx = line[max(0, m.start() - 24):m.end()].lower()
        if APPROVED.search(ctx):
            continue                      # a cited approval is not a pending decision
        s0, e0 = m.span()
        snip = re.sub(r"\s+", " ", line[max(0, s0 - 90):min(len(line), e0 + 90)].strip())
        days = _days_ago(line)
        k = snip[:70]
        if k in seen:
            continue
        seen.add(k)
        rows.append({"days": days, "src": f"{base}:{n}", "text": snip})
# slice 3: decisions from any text file under the roots (DECIDE:/TODO:/FIXME:/QUESTION:), bounded walk
if CFG.get("decision_scan"):
    import decisions as _decisions
    rows += _decisions.scan(_config.roots(CFG), NOW, CFG.get("decision_words"), seen)
import hashlib
for r in rows:                      # a stable id per parked line: the text, not the line number
    r["id"] = "q-" + hashlib.sha1(r["text"][:70].encode("utf-8")).hexdigest()[:10]
# the user's rulings (the board's /resolve verb writes them): done / stale retire a line from the board.
# The file keeps the text and the date, so a retirement is itself a receipt; reopen puts it back.
RULINGS_PATH = CFG["rulings_json"] or os.path.join(HERE, ".quest-rulings.json")
RULINGS = {}
try:
    _ru = json.load(open(RULINGS_PATH))
    if isinstance(_ru, dict):
        RULINGS = {k: v for k, v in _ru.items() if isinstance(v, dict) and v.get("ruling") in ("done", "stale")}
except Exception:
    pass
RETIRED = sorted([{"id": k, "ruling": v.get("ruling"), "at": v.get("at"), "src": v.get("src"),
                   "days": v.get("days"), "text": str(v.get("text", ""))[:300]} for k, v in RULINGS.items()],
                 key=lambda r: str(r.get("at") or ""), reverse=True)[:40]
rows = [r for r in rows if r["id"] not in RULINGS]
# Priority ring: the config's "quest_priority" ([{"label": "JOB", "match": "<regex>"}, ...], in order) sorts the lines that
# match an earlier entry first; with none (the default) the board runs by age alone.
_PRIO = []
for _e in CFG.get("quest_priority") or []:
    try:
        _PRIO.append((str(_e["label"])[:12], re.compile(str(_e["match"]), re.I)))
    except (KeyError, TypeError, re.error):
        pass
def _prio(r):
    t = r.get("text") or ""
    return next((i for i, (_, rx) in enumerate(_PRIO) if rx.search(t)), len(_PRIO))
for r in rows:
    t = r.get("text") or ""
    r["focus"] = next((lab for lab, rx in _PRIO if rx.search(t)), "")
OLDEST = max((r["days"] for r in rows if r["days"] >= 0), default=0)   # -1 = undated
QUESTS = sorted(rows, key=lambda r: (_prio(r), -r["days"]))[:16]

# ---------- world activity: dated events per day (for the base's day/night sky) ----------
counts = defaultdict(int)
if os.path.isfile(DIGEST):
    for line in open(DIGEST, errors="replace"):
        if line.startswith("- "):
            m = DATE.search(line[:70])
            if m:
                counts[m.group()] += 1
for f in (os.listdir(DISPATCH) if os.path.isdir(DISPATCH) else []):
    m = re.match(r"d-(\d{4})(\d{2})(\d{2})-", f)
    if m:
        counts["-".join(m.groups())] += 3
today = date.today()
ACTIVITY = [{"d": (today - timedelta(days=i)).isoformat(),
             "n": counts.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(27, -1, -1)]

TICKER = []                       # newest DIGEST lines, verbatim, for the HUD ticker
if os.path.isfile(DIGEST):
    for line in open(DIGEST, errors="replace"):
        if line.startswith("- "):
            TICKER.append(re.sub(r"\s+", " ", line[2:].strip())[:170])
TICKER = TICKER[-8:][::-1]

# ---------- effort, from real measures only: the hub's own touches (the PostToolUse hook log) and
# minutes per job for runners (the dispatch DONE lines in the digest). Nothing is estimated.
EFFORT = {"window_min": 120, "touches": {}, "last": {}, "trace": []}
try:
    _cut = NOW - EFFORT["window_min"] * 60
    _rows = []
    for _line in open(CFG["pilot_log"] or "/dev/null/none", errors="replace"):
        try:
            _r = json.loads(_line)
        except ValueError:
            continue
        if isinstance(_r, dict) and _r.get("uid") and float(_r.get("ts", 0)) >= _cut:
            _rows.append(_r)
    for _r in _rows:
        EFFORT["touches"][_r["uid"]] = EFFORT["touches"].get(_r["uid"], 0) + 1
        EFFORT["last"][_r["uid"]] = max(EFFORT["last"].get(_r["uid"], 0), float(_r["ts"]))
    EFFORT["trace"] = [_r["uid"] for _r in _rows[-30:]]
except OSError:
    pass
_MIN = re.compile(r"dispatch d-\d{8}-\d+-([a-z0-9-]+): DONE · ([0-9.]+)min")
_mins = defaultdict(list)
if os.path.isfile(DIGEST):
    for _line in open(DIGEST, errors="replace"):
        _m = _MIN.search(_line)
        if _m:
            _mins[_m.group(1)].append(float(_m.group(2)))
for _c in CHARS:
    if _c.get("kind") == "runner" and _mins.get(_c["name"]):
        _v = _mins[_c["name"]]
        _c["timed_jobs"] = len(_v)
        _c["minutes_avg"] = round(sum(_v) / len(_v), 1)
        _c["minutes_last"] = _v[-1]

INFLIGHT = []                      # the pid and tool of other agents' processes: read only when agent_processes is on
for line in (sh("pgrep -af 'ralph |dispatch |claude -p|muse exec|grok --sandbox|codex exec'")
             if CFG.get("agent_processes") else "").splitlines():
    pid, _, rest = line.partition(" ")
    if not pid.isdigit() or "pgrep" in rest:
        continue
    m = re.search(r"(ralph|dispatch|muse|grok|codex|claude)", rest)
    et = sh(f"ps -o etimes= -p {pid}").strip()
    mins = (int(et) // 60 if et.isdigit()
            else _districts.etime_to_s(sh(f"ps -o etime= -p {pid}").strip()) // 60)
    INFLIGHT.append({"pid": pid, "who": m.group(1) if m else "?",
                     "min": mins})
INFLIGHT = INFLIGHT[:8]

for char in CHARS:
    char.setdefault("uid", f"{char['kind']}:{char['name']}")

# the two queues: audit items from tasks_json (who = owner | agent)
TASKS_PATH = CFG["tasks_json"] or "/dev/null/none"
TASKS = {"items": [], "generated": None, "source": None}
try:
    _t = json.load(open(TASKS_PATH))
    if isinstance(_t, dict) and isinstance(_t.get("items"), list):
        TASKS = {"items": [i for i in _t["items"] if isinstance(i, dict) and str(i.get("id")) not in RULINGS][:200],
                 "generated": _t.get("generated"), "source": _t.get("source")}
except Exception:
    pass
try:
    OPENER = bool(json.load(open(CFG["areas_json"])).get("opener"))
except Exception:
    OPENER = False

# board items handed to runners (bridge /dispatch appends a row per hand-off). Status is read from
# receipts, never assumed: the digest line dispatch writes, else the sentinel in the report, else
# a live dispatch process naming that report, else "no receipt".
RUNS_PATH = CFG["task_runs_json"] or os.path.join(HERE, ".task-runs.jsonl")
RUNS = {}
_dstat = {}
if os.path.isfile(DIGEST):
    for line in open(DIGEST, errors="replace"):
        m = re.search(r"dispatch (d-\d{8}-\d+-[a-z0-9-]+): (.+?) · ([0-9.]+)min", line)
        if m:
            _dstat[m.group(1)] = (m.group(2)[:70], m.group(3))
try:
    _runrows = [json.loads(l) for l in open(RUNS_PATH, errors="replace") if l.strip()]
except Exception:
    _runrows = []
_alive = sh("pgrep -af 'dispatch '") if _runrows else ""
_done = {str(r.get("rid")): r for r in _runrows if isinstance(r, dict) and r.get("done") and r.get("rid")}
for r in _runrows[-200:]:
    if not isinstance(r, dict) or not r.get("item"):
        continue
    rid, out = str(r.get("rid", "")), str(r.get("out", ""))
    mins = ""
    if rid in _dstat:
        status, mins = _dstat[rid]
    elif rid in _done:                       # a config runner's receipt row: the exit code, never a guess
        d = _done[rid]
        status = ("KILLED after cap" if d.get("killed") else ("DONE" if d.get("rc") == 0 else f"FAILED rc={d.get('rc')}"))
        try:
            took = _ships.parse_iso(d.get("at")) - _ships.parse_iso(r.get("at"))
            mins = str(round(took.total_seconds() / 60, 1))
        except Exception:                    # an unreadable stamp (None) or naive minus aware
            mins = ""
    elif r.get("pid") and os.path.isdir(f"/proc/{r['pid']}"):
        status = "running"
    else:
        tail = []
        try:
            if out and os.path.isfile(out):
                tail = open(out, errors="replace").read().rstrip().splitlines()[-1:]
        except Exception:
            tail = []
        if tail == [f"SENTINEL: {rid}"]:
            status = "DONE (sentinel in report; no digest line yet)"
        elif rid and rid in _alive:
            status = "running"
        else:
            status = "no receipt"
    RUNS.setdefault(str(r["item"]), []).append({"rid": rid, "runner": str(r.get("runner", "")), "at": r.get("at"),
                                                "status": status, "min": mins, "out": out, "cwd": str(r.get("cwd", ""))})
for _k in RUNS:
    RUNS[_k] = RUNS[_k][-5:]
# runners the board may hand to: config runners (slice 2) first, then the registry minus dead
# seats and the retiring sub seat
DISPATCHABLE = [k for k, v in (CFG.get("runners") or {}).items()
                if isinstance(v, dict) and isinstance(v.get("cmd"), list) and re.fullmatch(r"[a-z0-9-]{1,32}", str(k))]
try:
    _reg = json.load(open(RUNNERS))
    DISPATCHABLE += [k for k, v in _reg.items() if isinstance(v, dict) and not k.startswith("_") and k != "claude"
                     and not str(v.get("desc", "")).startswith("\U0001F534") and k not in DISPATCHABLE]
except Exception:
    pass
DISPATCHABLE.sort(key=lambda k: (k not in ("deepseek", "omp", "qwen-flash", "ollama"), k))   # cheap grind seats first

# slice 6: verbs are files; the page gets names/labels/applies, never commands
import verbs as _verbsmod
_vb, _vberr = _verbsmod.load(CFG.get("verbs_dir"))
VERBS = _verbsmod.public(_vb, _vberr) | {"dir": CFG.get("verbs_dir") or "", "loop": _verbsmod.loop_tail(CFG.get("verbs_dir"))}
# usage per verb from the receipts (rows keyed verb:<name>:<path>)
_vuse = {}
for _k, _rs in RUNS.items():
    if _k.startswith("verb:"):
        _n = _k.split(":", 2)[1]
        _vuse.setdefault(_n, {"runs": 0, "last": None})
        _vuse[_n]["runs"] += len(_rs)
        _vuse[_n]["last"] = max([_vuse[_n]["last"] or ""] + [str(r.get("at") or "") for r in _rs]) or None
for _it in VERBS["items"]:
    _it["use"] = _vuse.get(_it["name"], {"runs": 0, "last": None})
    _it["age_d"] = int((NOW - float(_it.pop("mtime") or NOW)) // 86400)

# an auditor's verdict per board item (a subagent run on the user's ask writes audit_json). Shown beside the
# item as advice with its evidence; the ruling stays the user's click.
AUDIT = {}
try:
    _a = json.load(open(CFG["audit_json"] or "/dev/null/none"))
    if isinstance(_a, dict) and isinstance(_a.get("items"), dict):
        AUDIT = {str(k): {"verdict": str(v.get("verdict", ""))[:40], "recommended": str(v.get("recommended", ""))[:400],
                          "evidence": str(v.get("evidence", ""))[:600], "at": str(_a.get("generated", ""))[:25]}
                 for k, v in _a["items"].items() if isinstance(v, dict)}
except Exception:
    pass

# the hub's rounds (rounds.py): a script's real checks, shown as the ship's work
ROUNDS = None
try:
    _r = json.load(open(CFG["rounds_json"] or "/dev/null/none"))
    if isinstance(_r, dict):
        ROUNDS = {k: _r.get(k) for k in ("started", "round", "step", "step_uid", "updated", "last_round_at", "next_round_at", "checks", "verified", "changes")}
except Exception:
    pass

# the first screen (today.py): what waits on the user, the plan's week, agents, done today, daily lanes,
# what each folder is for. A failure is shown on that screen, not hidden.
import today as _todaymod
try:
    TODAY = _todaymod.build(CFG, CHARS, SHIP_SOURCES, _districts.load_container_tags(CFG.get("tags_md"), 400)[0])
except Exception as exc:
    TODAY = {"error": "today page could not be built: " + type(exc).__name__ + ": " + str(exc)[:200]}

# T3-style tasks (tasks.py): what agents were asked from this page, and which agents can be asked
import tasks as _tasksmod
try:
    _TASK_STORE = _tasksmod.store_for(CFG, _config.task_roots(CFG))   # the bridge's own settings, so blockers agree
    TASK_RUNNERS = _tasksmod.public_runners(_TASK_STORE.runners)
    AGENT_TASKS, _history_rows = [], 0
    for _row in _TASK_STORE.list():             # every thread still open, and the newest 80 closed ones
        if _row.get("status") in _tasksmod.CLOSED or _row.get("status") == "error":
            _history_rows += 1
            if _history_rows > 80:
                continue
        AGENT_TASKS.append(_row)
except Exception as exc:                       # the rest of the world still renders
    print(f"warn: tasks unreadable: {exc}", file=sys.stderr)
    AGENT_TASKS, TASK_RUNNERS = [], []

# what is getting worse: an hourly snapshot per root, compared with a week ago (history.py)
import history as _history
import wingmate as _mate
try:
    _BAD_VERDICTS = ("STALE", "NEVER", "NO RECEIPT")
    _ITEMS = (TASKS.get("items") if isinstance(TASKS, dict) else None) or []
    _SNAP = {}
    _AREA_PATHS = sorted(((str(a.get("path") or "").rstrip("/"), a["name"]) for a in CHARS
                          if a.get("kind") == "area" and not a.get("alias_of") and a.get("path")),
                         key=lambda pn: -len(pn[0]))
    _FAILED_AT = defaultdict(int)
    for _t in AGENT_TASKS:
        if _t.get("status") in ("failed", "lost"):
            _tgt = str(_t.get("target") or "").rstrip("/")
            _hit = next((n for p, n in _AREA_PATHS if _tgt == p or _tgt.startswith(p + "/")), None)
            if _hit:
                _FAILED_AT[_hit] += 1
    for _a in CHARS:
        if _a.get("kind") != "area" or _a.get("alias_of"):
            continue
        _SNAP[_a["name"]] = [_a.get("age"), _a.get("files") or 0,
                             sum(1 for c in CHARS if c.get("kind") == "cron" and c.get("area") == _a["uid"] and c.get("verdict") in _BAD_VERDICTS),
                             sum(1 for t in _ITEMS if isinstance(t, dict) and t.get("area") == _a["name"] and t.get("who") != "agent"),
                             _FAILED_AT.get(_a["name"], 0)]
    if not CFG["demo"]:
        _history.record(CFG["history_jsonl"], NOW, _SNAP)
    _KINDS = {a["name"]: a.get("area_kind") for a in CHARS if a.get("kind") == "area"}
    TREND = _history.trend(CFG["history_jsonl"], NOW, kinds=_KINDS)
except Exception as exc:                       # history is a nice-to-have, never a dead page
    print(f"warn: history unreadable: {exc}", file=sys.stderr)
    TREND = {}
_MATE = _mate.spec_for(CFG)
_HOME_PLANET = str(CFG.get("home_planet") or "")
_WM = CFG.get("wingmate") if isinstance(CFG.get("wingmate"), dict) else {}
_MATE_NAME = str((_MATE or {}).get("name") or _WM.get("name")
                 or ((SKIN or {}).get("names") or {}).get("wingmate") or _themepacks.DEFAULT_WINGMATE)[:24]
MATE = {"name": _MATE_NAME, "available": bool(_MATE), "model": (_MATE or {}).get("model") or "",
        "models": _mate.listing(_MATE), "default": _mate.default_of(_MATE) if _MATE else "",
        "max_seconds": (_MATE or {}).get("max_seconds"),
        "mothership": str(_WM.get("mothership") or "hub"), "home_planet": str(_WM.get("home_planet") or _HOME_PLANET)}

WORLD = {"chars": CHARS, "ship_sources": SHIP_SOURCES, "today": TODAY, "trend": TREND, "wingmate": MATE,
         "agent_tasks": AGENT_TASKS, "task_runners": TASK_RUNNERS, "root_path": CFG["work_root"],
         "running": sum(c.get("kind") in ("seat", "ship") and c.get("status") == "WORKING" for c in CHARS),
         "quests": QUESTS, "activity": ACTIVITY, "inflight": INFLIGHT,
         "districts": DISTRICT_BLOCKS, "summary": DISTRICT_SUMMARY, "ticker": TICKER,
         "tasks": TASKS, "opener": OPENER, "rounds": ROUNDS, "tool": HERE, "runs": RUNS, "retired": RETIRED, "dispatchable": DISPATCHABLE, "audit": AUDIT, "verbs": VERBS, "name": CFG["name"], "demo": bool(CFG["demo"]), "effort": EFFORT,
         "lane_planets": CFG.get("lane_planets", {}),
         "generated": datetime.now().strftime("%a %b %-d, %-I:%M %p"),
         "rendered": round(NOW, 3),       # the world's revision: the page keeps a /tasks snapshot (bridge rev) that is newer
         "oldest": OLDEST}
import constellations as _constellations
WORLD["constellations"], WORLD["constellation_skipped"] = _constellations.build(
    CFG["work_root"], THREAD_SHIPS, NOW, CFG)
# the hangar's berths (config "hangar_slots"): how many ships the owner means to keep; the page shows used / slots and
# says OVER CAP past it, and nothing is ever stopped for it. A value that is not a whole number >= 0 is the default.
_SLOTS = CFG.get("hangar_slots", 6)
WORLD["hangar_slots"] = _SLOTS if isinstance(_SLOTS, int) and not isinstance(_SLOTS, bool) and 0 <= _SLOTS <= 999 else 6
# workflows (workflows.py): the open ones and the newest closed ones as summaries (read only here: the bridge and
# the CLI tick them), each step placed on this render's planets; last night's run joins them as a derived one.
# A failure leaves the list empty and says why, never a dead page.
import workflows as _wfmod
try:
    WORLD["workflows"] = _wfmod.store_for(CFG, task_store=globals().get("_TASK_STORE"),
                                          roots=_config.task_roots(CFG), chars=CHARS).list()
except Exception as exc:
    print(f"warn: workflows unreadable: {exc}", file=sys.stderr)
    WORLD["workflows"] = []
try:
    _NIGHT_WF = _wfmod.night_workflow(TODAY.get("night") if isinstance(TODAY, dict) else None, CHARS, NOW)
    if _NIGHT_WF:
        WORLD["workflows"].append(_NIGHT_WF)
except Exception as exc:
    print(f"warn: the night run's workflow not built: {exc}", file=sys.stderr)

# the sky (sky.py): every thing that needs a person as one list of contacts; running work is counted,
# never drawn. A failure is shown on the page as the sky's error, never a dead page.
import sky as _sky
try:
    WORLD["sky"] = _sky.build(WORLD, NOW)
except Exception as exc:
    print(f"warn: sky not built: {exc}", file=sys.stderr)
    WORLD["sky"] = {"contacts": [], "error": "sky could not be built: " + type(exc).__name__ + ": " + str(exc)[:200]}
# the one count every surface reads: each contact in exactly one bucket (sky.counts). A failure leaves every count at
# zero and says why under "error", never a dead page.
try:
    WORLD["counts"] = _sky.counts(WORLD["sky"], AGENT_TASKS, WORLD["workflows"])
except Exception as exc:
    print(f"warn: counts not built: {exc}", file=sys.stderr)
    WORLD["counts"] = {"need_you": 0, "answer": 0, "review": 0, "decisions": 0, "problems": 0, "working": 0,
                       "on_hold": 0, "done_today": 0, "oldest_decision": {"days": None, "source": None}, "total": 0,
                       "error": "counts could not be built: " + type(exc).__name__ + ": " + str(exc)[:200]}

# A file or folder name that is not UTF-8 reaches Python as lone surrogates (surrogateescape), which
# json prints but no UTF-8 file can hold. Every string the page or --json carries is made text that
# encodes: each unreadable byte reads as U+FFFD, so the page is always written.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def clean_text(text):
    return _LONE_SURROGATE.sub("\ufffd", text) if isinstance(text, str) else text


def clean_names(value):
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, dict):
        return {clean_names(k): clean_names(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_names(v) for v in value]
    return value


WORLD = clean_names(WORLD)

# ---------- emit ----------
# The vector look's chrome (a pack with "render": "vector" sets html[data-render="vector"]): its own style element after
# the main sheet, so the pack rewriter leaves it alone (its colours are rgb(), the spec's exact tokens, never recoloured),
# the phone block above keeps its own rules, and no rule here can touch another look. $V = the vector look anywhere,
# $D = a desktop or a tablet (the desktop layout), $C = a desktop without touch (the compass corner, the quick seats:
# a tablet keeps its touch placement), $M = a phone. No rounded corners, a 10 px top-left chamfer on panels, condensed
# tracked capitals for labels, the data face for values.
TAC_CSS = """
$V{--tac-space:rgb(4,8,8);--tac-deep:rgb(1,3,3);--tac-line:rgb(79,136,107);--tac-dim:rgb(38,74,56);--tac-faint:rgb(26,46,37);
--tac-panel:rgba(11,14,13,.82);--tac-solid:rgb(11,14,13);--tac-edge:rgb(42,59,51);--tac-val:rgb(232,236,232);--tac-body:rgb(201,210,204);
--tac-mut:rgb(127,145,138);--tac-mint:rgb(163,232,208);--tac-ok:rgb(79,209,197);--tac-y:rgb(229,184,0);--tac-y2:rgb(247,212,72);
--tac-ink:rgb(8,10,9);--tac-red:rgb(214,40,40);--tac-amber:rgb(229,158,43);--tac-won:rgb(52,112,78);--tac-idle:rgb(56,66,62);
--tac-sh:1px 1px 0 rgb(0,0,0);--tac-cut:polygon(10px 0,100% 0,100% 100%,0 100%,0 10px);
--tac-pill:polygon(0 0,calc(100% - 12px) 0,100% 50%,calc(100% - 12px) 100%,0 100%);
--font-mono:var(--font-data);--font-display:var(--font-tac);font-synthesis:none}
$V *{border-radius:0!important}
$V #pbody .cstate.bad{background:var(--tac-red);color:var(--tac-ink);border:0;font:600 var(--fs-body)/1.25 var(--font-tac);letter-spacing:.14em;text-transform:uppercase;text-shadow:none;padding:9px 12px 8px 34px;position:relative}
$V #pbody .cstate.bad::before{content:'';position:absolute;left:11px;top:50%;margin-top:-7px;width:0;height:0;border-left:8px solid transparent;border-right:8px solid transparent;border-bottom:14px solid var(--tac-ink)}
$V #pbody .cstate.warn{border-left:3px solid var(--tac-amber);color:var(--tac-val)}
$V #matec{filter:grayscale(1) sepia(1) hue-rotate(100deg) saturate(1.6) brightness(.92) contrast(1.15)}
$V #mate #mateq::placeholder,$V #matemodel{text-transform:uppercase;letter-spacing:.1em}
$V body{background:var(--tac-space);color:var(--tac-body)}
$V *{scrollbar-color:var(--tac-dim) transparent}
$V ::selection{background:var(--tac-y);color:var(--tac-ink)}
$V kbd.tkey{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:18px;padding:0 4px;border:1px solid currentColor;background:rgba(232,236,232,.06);font:var(--fs-small)/1 var(--font-data);letter-spacing:0;text-transform:none;text-shadow:none}
#tacrail,#tacfoot,#tvmission{display:none}
$D #tacrail,$D #tacfoot,$V #tvmission{display:block}
$D body #topbar,$D body #keyline,$D #nums,$D #hmore{display:none!important}
/* the top rail: view chip and dossier stamp, the map's mark between bracket ticks, boxed status counters */
$D #tacrail{position:absolute;z-index:32;left:0;right:0;top:0;height:44px;pointer-events:none;background:linear-gradient(rgba(1,3,3,.9),rgba(1,3,3,0));font:var(--fs-small)/1 var(--font-data);color:var(--tac-body);text-shadow:var(--tac-sh)}
$D #tacrail .tline{position:absolute;left:12px;right:12px;top:28px;height:1px;background:linear-gradient(90deg,var(--tac-line) 0,var(--tac-line) calc(50% - var(--gap,96px)),transparent calc(50% - var(--gap,96px)),transparent calc(50% + var(--gap,96px)),var(--tac-line) calc(50% + var(--gap,96px)))}
$D #tacrail .tline::before,$D #tacrail .tline::after,$D #tacfoot .bline::before,$D #tacfoot .bline::after{content:'';position:absolute;top:-4px;width:1px;height:9px;background:var(--tac-line)}
$D #tacrail .tline::before,$D #tacfoot .bline::before{left:0}$D #tacrail .tline::after,$D #tacfoot .bline::after{right:0}
$D #tacrail .tl{position:absolute;left:16px;top:7px;display:flex;align-items:center;gap:12px;white-space:nowrap;max-width:calc(50% - 140px);overflow:hidden}
$D #tacview{padding:3px 8px 2px;background:var(--tac-y);color:var(--tac-ink);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.22em;text-shadow:none}
$D #tacstamp{letter-spacing:.1em;color:var(--tac-val)}
$D #tacnote{color:var(--tac-red);overflow:hidden;text-overflow:ellipsis}
$D #tacrail .tmark{position:absolute;left:50%;top:19px;transform:translateX(-50%);display:flex;align-items:center;gap:12px;white-space:nowrap}
$D #tacmark{font:600 var(--fs-title)/1 var(--font-tac);letter-spacing:.32em;margin-right:-.32em;color:var(--tac-val)}
$D #tacrail .tmark i{width:6px;height:16px;border:1px solid var(--tac-line)}
$D #tacrail .tmark i:first-child{border-right:0}$D #tacrail .tmark i:last-child{border-left:0}
$D #tacrail .tscale{position:absolute;left:50%;top:33px;width:337px;margin-left:-168px;height:5px;background:repeating-linear-gradient(90deg,var(--tac-dim) 0 1px,transparent 1px 28px)}
$D #taccount{position:absolute;right:16px;top:5px;display:flex;gap:6px;pointer-events:auto}
$D #taccount button{display:flex;align-items:center;gap:7px;height:20px;padding:0 8px;background:rgba(11,14,13,.7);border:1px solid var(--tac-edge);color:var(--tac-mut);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.16em;text-transform:uppercase;white-space:nowrap;cursor:pointer;text-shadow:var(--tac-sh)}
$D #taccount button b{font:var(--fs-small)/1 var(--font-data);font-weight:400;letter-spacing:0;color:var(--tac-val)}
$D #taccount .ok{border-color:var(--tac-ok)}$D #taccount .ok b{color:var(--tac-ok)}
$D #taccount .need{border-color:var(--tac-y)}$D #taccount .need b{color:var(--tac-y)}
$D #taccount .bad{border-color:var(--tac-red)}$D #taccount .bad b{color:var(--tac-red)}
$D #taccount button:hover{color:var(--tac-val);border-color:var(--tac-val)}
$D #taccount .s{display:none}
@media (max-width:1280px){$D #taccount .w{display:none}$D #taccount .s{display:inline}$D #tackeys [data-key=h]{display:none}}
@media (max-width:1100px){$D #tackeys :is([data-key=t],[data-key=l]){display:none}$D #tacnote{display:none}}
@media (max-width:900px){$D #taccount .s{display:none}$D #tackeys [data-key=v]{display:none}$D #tackeys{gap:14px}$D #tacrail .tl{gap:8px;max-width:calc(50% - 96px)}$D #tacstamp{letter-spacing:.02em}}
/* the bottom rail: one receipts line above it, the key legend under it (each key a button), the monogram */
$D #tacfoot{position:absolute;z-index:32;left:0;right:0;bottom:0;height:48px;pointer-events:none;background:linear-gradient(rgba(1,3,3,0),rgba(1,3,3,.92) 70%);text-shadow:var(--tac-sh)}
$D #tacfoot .bline{position:absolute;left:12px;right:12px;bottom:30px;height:1px;background:var(--tac-dim)}
$D #tackeys{position:absolute;right:16px;bottom:4px;display:flex;align-items:center;gap:20px;pointer-events:auto}
$D #tackeys button{display:flex;align-items:center;gap:8px;height:24px;padding:0 2px;background:none;border:0;color:var(--tac-val);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.18em;text-transform:uppercase;white-space:nowrap;cursor:pointer;text-shadow:var(--tac-sh)}
$D #tackeys button:hover,$D #tackeys button:focus-visible{color:var(--tac-y)}
$D #tacmono{position:absolute;left:16px;bottom:7px;display:flex;align-items:center;color:var(--tac-val);font:600 var(--fs-title)/1 var(--font-tac);letter-spacing:.06em}
$D #tacmono s{display:inline-block;width:1px;height:16px;margin:0 4px;background:var(--tac-line);transform:skewX(-24deg)}
$D #wrap:not(.dash) #tacmono{left:166px}
$D #ticker{left:calc(var(--dashw) + 164px);right:calc(var(--sidew) + 16px);bottom:31px;z-index:33;width:auto;height:18px;min-height:0;padding:0;gap:12px;transform:none;background:none;border:0;box-shadow:none;color:var(--tac-val);text-shadow:var(--tac-sh)}
$D #ticker .tk{padding:0;border:0;font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.26em;color:var(--tac-mut)}
$D #tkbody{font:var(--fs-small)/18px var(--font-data);letter-spacing:.04em;color:var(--tac-val)}
$D #today.on~#ticker{display:none}
$D #today.on~#tacrail,$D #today.on~#tacfoot{background:var(--tac-deep)}
$D #taskbar{bottom:48px;background:rgba(1,3,3,.7);border-top:1px solid var(--tac-dim);font-family:var(--font-data)}
$D #staticbanner{top:44px}
$C #quick,$C body:not(.quiet) #quick{top:54px}
$D #theme,$D body:not(.quiet) #theme{top:54px}
$D #gohome{top:54px;background:var(--tac-panel);border:1px solid var(--tac-ok);color:var(--tac-ok);font-family:var(--font-tac);letter-spacing:.2em}
$D #demobanner{top:58px;bottom:auto;max-width:calc(100vw - var(--dashw) - var(--sidew) - 360px);left:calc(var(--dashw) + 330px);background:var(--tac-y);color:var(--tac-ink);font-family:var(--font-tac);letter-spacing:.2em;box-shadow:none}
$V #quick{background:var(--tac-panel);border:1px solid var(--tac-edge);box-shadow:none}
$V #quick button,$V #gbbtn{background:transparent;border:1px solid var(--tac-edge);box-shadow:none;color:var(--tac-val);font-family:var(--font-tac);letter-spacing:.14em;text-transform:uppercase}
/* the HUD: no box; the days number big and thin over its tiny tracked label, then the mono lines */
$D #hud,$D body:not(.quiet) #hud{top:58px;left:calc(var(--dashw) + 22px);width:auto;min-width:0;max-width:360px;padding:0;background:none;border:0;box-shadow:none;text-shadow:var(--tac-sh)}
$D #hud h1{display:none}
$D #hud .sc{margin:0;font:500 var(--fs-big)/1 var(--font-tac);font-size:64px;letter-spacing:.01em;color:var(--tac-val);text-shadow:2px 3px 0 rgba(0,0,0,.9),0 0 18px rgba(0,0,0,.6)}
$D #hud .sc:hover{color:var(--tac-y)}
$D #hud .sc small{margin-top:4px;font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.28em;color:var(--tac-body);text-shadow:var(--tac-sh)}
$D #hud .hl{margin-top:10px;padding-top:0;border-top:0;gap:14px;font:var(--fs-small)/var(--lh-small) var(--font-data);letter-spacing:.06em;color:var(--tac-body)}
$D #hud .hfull,$D #sector,$D #roundstag{font:var(--fs-small)/var(--lh-small) var(--font-data);letter-spacing:.04em;color:var(--tac-mut)}
$D #themeline,$D #zoomtag{color:var(--tac-body)}$V #themeline b{color:var(--tac-y)}
$V #glance-alert{background:var(--tac-y);color:var(--tac-ink);font-family:var(--font-tac);letter-spacing:.14em;text-transform:uppercase;text-shadow:none}
/* comms: a chamfered panel; callsign, state word in its colour, the last line muted */
$D #comms,$D body.quiet #comms,$D body:not(.quiet) #comms,$D body:not(.quiet):not(.mateoff) #comms{top:calc(var(--tac-hb,168px) + 14px);left:calc(var(--dashw) + 22px);width:380px;max-height:calc(100vh - var(--tac-hb,168px) - 340px);background:var(--tac-panel);border:1px solid var(--tac-edge);clip-path:var(--tac-cut);box-shadow:none}
$V #comms .ct{font:var(--fs-small)/1 var(--font-data);color:var(--tac-mut);border-color:var(--tac-faint)}
$V #comms .ct b{font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.26em;color:var(--tac-mint)}
$V #comms .pt{display:none}
$V #comms .row{background:transparent;border-top:1px solid var(--tac-faint);border-left:2px solid var(--tc,var(--tac-mut))}
$V #comms .row:hover{background:rgba(229,184,0,.08)}$V #comms .row.hot{background:rgba(229,158,43,.12)}
$V #comms .row.tf{--tc:var(--tac-ok)}$V #comms .row.ta{--tc:var(--tac-amber)}$V #comms .row.th{--tc:var(--tac-red)}$V #comms .row.tq{--tc:var(--tac-mut)}
$V #comms .nm{font:600 var(--fs-list)/1 var(--font-tac);letter-spacing:.1em;text-transform:uppercase;color:var(--tac-val)}
$V #comms .st{font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.18em;text-transform:uppercase;color:var(--tc,var(--tac-mut))}
$V #comms .tl{font-family:var(--font-data);color:var(--tac-mut)}
$V #comms .more,$V #comms .note{font-family:var(--font-data);color:var(--tac-mut);border-color:var(--tac-faint)}
/* the wingmate: chamfered, the portrait's pixels kept inside a thin bracket frame, the name tag under it */
$C #mate,$C #mateshow{left:calc(var(--dashw) + 166px);bottom:60px}
$C body:not(.quiet) #mate,$C body:not(.quiet) #mateshow{bottom:94px}
$D #mate{filter:none}
$C #mate{width:min(480px,calc(100vw - var(--dashw) - var(--sidew) - 190px))}
$C #wrap.panel-open #mate{width:min(480px,calc(100vw - var(--dashw) - min(480px,92vw) - var(--sidew) - 200px))}
$D #mate .mp{flex:0 0 122px;display:flex;flex-direction:column;align-items:center;padding:9px 8px 8px;background:rgba(11,14,13,.9);border:1px solid var(--tac-edge);border-right:0;clip-path:var(--tac-cut)}
$D #mate .mp::before{content:'';position:absolute;left:5px;top:5px;width:112px;height:112px;pointer-events:none;
background:linear-gradient(var(--tac-mint),var(--tac-mint)) 0 0/12px 1px no-repeat,linear-gradient(var(--tac-mint),var(--tac-mint)) 0 0/1px 12px no-repeat,
linear-gradient(var(--tac-mint),var(--tac-mint)) 100% 0/12px 1px no-repeat,linear-gradient(var(--tac-mint),var(--tac-mint)) 100% 0/1px 12px no-repeat,
linear-gradient(var(--tac-mint),var(--tac-mint)) 0 100%/12px 1px no-repeat,linear-gradient(var(--tac-mint),var(--tac-mint)) 0 100%/1px 12px no-repeat,
linear-gradient(var(--tac-mint),var(--tac-mint)) 100% 100%/12px 1px no-repeat,linear-gradient(var(--tac-mint),var(--tac-mint)) 100% 100%/1px 12px no-repeat}
$D #mate .mp b{position:static;margin-top:12px;padding:0;background:none;color:var(--tac-mint);font:600 var(--fs-title)/1 var(--font-tac);letter-spacing:.26em;margin-right:-.26em;text-shadow:var(--tac-sh)}
$D #mate .nb{border:1px solid var(--tac-edge)}
$V #mate .mp{background:rgba(11,14,13,.9)}
$V #mate .nb{background:var(--tac-panel);color:var(--tac-val);font-family:var(--font-data)}
$V #mate .nh{color:var(--tac-mut);font-family:var(--font-data)}
$V #mate .nh button,$V #matef button{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-val);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.14em;text-transform:uppercase}
$V #mateq{background:rgba(0,0,0,.35);border:1px solid var(--tac-edge);color:var(--tac-val)}
$V #mateshow{background:var(--tac-panel);border:1px solid var(--tac-mint);color:var(--tac-mint);box-shadow:none;font-family:var(--font-tac);letter-spacing:.24em}
/* the console: the left menu. Tracked heads, rows as menu items, the hovered or open row a yellow arrow pill */
$D #dash{top:44px;bottom:48px;background:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='41.569' height='72'%3E%3Cpath d='M20.785 12L41.569 24V48L20.785 60L0 48V24ZM20.785 0V12M20.785 60V72' fill='none' stroke='rgb(79,136,107)' stroke-opacity='.14'/%3E%3C/svg%3E") 0 0/41.569px 72px,linear-gradient(90deg,rgba(1,3,3,.7),rgba(4,8,8,.35));border-right:1px solid var(--tac-dim)}
$D #dash .dtabs{background:rgba(1,3,3,.92);border-bottom:1px solid var(--tac-dim)}
$D #dash .dtabs button{font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.26em;color:var(--tac-mut)}
$D #dash .dtabs button.on{color:var(--tac-val);border-bottom-color:var(--tac-y)}$D #dash .dtabs button b{font-family:var(--font-data);color:var(--tac-y)}
$D #dash .dsec{margin-top:22px}
$D #dash h3{margin:0 0 8px;font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.26em;color:var(--tac-mint)}
$D #dash h3 .dn{font-family:var(--font-data);font-weight:400;letter-spacing:0;color:var(--tac-val)}
$D #dash h3 a{font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.18em;text-transform:uppercase;color:var(--tac-mut)}$D #dash h3 a:hover{color:var(--tac-y)}
$D #dash .row{height:30px;margin:0 0 4px;padding:0 24px 0 10px;background:rgba(232,236,232,.04);border:0;border-left:3px solid rgba(232,236,232,.8);font:500 var(--fs-list)/1 var(--font-tac);letter-spacing:.1em;text-transform:uppercase;color:var(--tac-val)}
$D #dash .row .rt{color:inherit}
$D #dash .row .rm{font:var(--fs-small)/1 var(--font-data);letter-spacing:.02em;text-transform:none;color:var(--tac-mut)}
$D #dash .row.tall,$D #dash .row.more{height:auto;min-height:30px;padding-top:6px;padding-bottom:6px}$D #dash .row.tall .rm{line-height:1.3}
$D #dash .need .rm{color:var(--tac-amber)}$D #dash .bad .rm{color:var(--tac-red)}$D #dash .work .rm,$D #dash .ok .rm{color:var(--tac-ok)}
$D #dash .row i{flex:0 0 6px;height:6px;background:var(--tac-mut)}
$D #dash .need i{background:var(--tac-amber)}$D #dash .bad i{background:var(--tac-red)}$D #dash .work i,$D #dash .ok i{background:var(--tac-ok)}
$D #dash .row:hover,$D #dash .row:focus-visible,$D #dash .row.sel{background:linear-gradient(var(--tac-y2),var(--tac-y));border-left-color:var(--tac-y);color:var(--tac-ink);clip-path:var(--tac-pill);outline:0}
$D #dash .row:hover .rm,$D #dash .row:focus-visible .rm,$D #dash .row.sel .rm{color:var(--tac-ink)}
$D #dash .row:hover i,$D #dash .row:focus-visible i,$D #dash .row.sel i{background:var(--tac-ink)}
$D #dash .row.more{background:transparent;border-left-color:transparent}$D #dash .row.more .rt{font-size:var(--fs-small);color:var(--tac-mut)}
$D #dash .mut{font-family:var(--font-data);color:var(--tac-mut)}
/* the threads rail and the composer */
$D #side{top:44px;bottom:48px;background:var(--tac-panel);border-left:1px solid var(--tac-dim)}
$V #side{color:var(--tac-body)}
$V #side .shead{border-bottom:1px solid var(--tac-dim)}
$V #side .shead b,$V #tasks h3,$V #composer h2{font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.26em;color:var(--tac-mint)}
$V #side .shead span,$V #tasks .mut{font-family:var(--font-data);color:var(--tac-mut)}
$V #side .shead button,$V #composer button{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-val);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.16em;text-transform:uppercase}
$V #side .shead #snew{border-color:var(--tac-y);color:var(--tac-y)}
$V #composer{background:transparent;color:var(--tac-body)}
$V #composer h2 span{font-family:var(--font-data);letter-spacing:.02em;color:var(--tac-ok)}
$V #composer .sub{font-family:var(--font-data);color:var(--tac-mut)}
$V #composer .crow label,$V #composer .cfield label{font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.22em;color:var(--tac-mut)}
$V #composer textarea,$V #composer input[type=text],$V #composer select,$V #composer .chk{background:rgba(0,0,0,.35);border:1px solid var(--tac-edge);color:var(--tac-val);font-family:var(--font-data)}
$V #composer button.go{background:var(--tac-y);border-color:var(--tac-y);color:var(--tac-ink)}
$V #composer button.txt{border-color:transparent;color:var(--tac-mut)}
/* the mission card: a thread as an operation (the list row is its short form) */
$V .mc{display:block;margin:0 0 10px;padding:0;background:var(--tac-panel);border:1px solid var(--tac-edge);clip-path:var(--tac-cut);color:var(--tac-body);--mb:var(--tac-idle);--mbt:var(--tac-body)}
$V .mc.st-red{--mb:var(--tac-red);--mbt:var(--tac-val)}$V .mc.st-run{--mb:var(--tac-ok);--mbt:var(--tac-ink)}$V .mc.st-won{--mb:var(--tac-won);--mbt:var(--tac-val)}
$V #tasks .trow.mc{display:block;min-height:0;margin:0 0 10px;padding:0;border-left:1px solid var(--tac-edge);cursor:pointer}
$V #tasks .trow.mc .mcb b{display:block;font:600 var(--fs-list)/var(--lh-list) var(--font-tac);letter-spacing:.06em;color:inherit;overflow:visible}
$V #tasks .trow.mc .mcb small{display:block;margin:0;font:500 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.14em;color:inherit}
$V #tasks .trow.mc .mhz{flex:none;width:16px;height:14px;margin:0}
$V #tasks .trow.mc:hover{background:var(--tac-panel);border-color:var(--tac-line)}
$V #tasks .trow.mc.sel{background:var(--tac-panel);border-color:var(--tac-y)}
$V .mc .mcb{position:relative;display:flex;flex-direction:column;gap:2px;padding:7px 12px 7px 36px;background:var(--mb);color:var(--mbt)}
$V .mc:not(.st-red) .mcb{padding-left:12px}
$V .mc .mcb b{display:block;font:600 var(--fs-list)/var(--lh-list) var(--font-tac);letter-spacing:.06em;color:inherit;overflow-wrap:anywhere}
$V .mc .mcb small{display:block;margin:0;font:500 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.14em;text-transform:uppercase;color:inherit;opacity:.85}
$V .mc .mhz{position:absolute;left:11px;top:9px;width:16px;height:14px;background:var(--tac-ink);clip-path:polygon(50% 0,100% 100%,0 100%)}
$V .mc .mhz::after{content:'';position:absolute;left:7px;top:5px;width:2px;height:4px;background:var(--mb);box-shadow:0 6px 0 var(--mb)}
$V .mc:not(.st-red) .mhz{display:none}
$V .mc .mcd{display:flex;flex-wrap:wrap;gap:4px 14px;padding:8px 12px;font:var(--fs-small)/var(--lh-small) var(--font-data);color:var(--tac-body)}
$V .mc .mcd .mtg{color:var(--tac-val)}
$V .mc .mcd .ok,$V .mc dl.mtab dd.ok{color:var(--tac-ok)}$V .mc .mcd .warn,$V .mc dl.mtab dd.warn{color:var(--tac-amber)}$V .mc .mcd .bad,$V .mc dl.mtab dd.bad{color:var(--tac-red)}
$V .mc .mtgt{flex:1 1 100%;display:flex;justify-content:space-between;gap:12px;padding-bottom:6px;border-bottom:1px solid var(--tac-faint)}
$V .mc .mtgt span,$V .mc dl.mtab dt{font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.22em;color:var(--tac-mut)}
$V .mc .mtgt a{color:var(--tac-val);cursor:pointer;overflow-wrap:anywhere;text-align:right}$V .mc .mtgt a:hover{color:var(--tac-y)}
$V .mc dl.mtab{flex:1 1 100%;display:grid;grid-template-columns:auto minmax(0,1fr);gap:0 16px;margin:2px 0 0}
$V .mc dl.mtab dt,$V .mc dl.mtab dd{padding:4px 0;border-bottom:1px solid var(--tac-faint)}
$V .mc dl.mtab dd{margin:0;text-align:right;color:var(--tac-val);overflow-wrap:anywhere}
$V .mc .mcbar{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;min-height:34px;margin:0;padding:6px 12px;border:0;border-top:1px solid var(--tac-edge);background:rgba(0,0,0,.35);color:var(--tac-body);font:600 var(--fs-body)/1.2 var(--font-tac);letter-spacing:.16em;text-transform:uppercase;text-align:center}
$V .mc .mcbar.go{background:var(--tac-y);color:var(--tac-ink)}$V .mc button.mcbar.go{cursor:pointer}$V .mc button.mcbar.go:hover{background:var(--tac-y2)}
$V .mc .mcbar.bad{background:rgba(214,40,40,.16);color:var(--tac-red);letter-spacing:.06em;text-transform:none;font-family:var(--font-data);font-weight:400}
$V .mc .mcbar.run{color:var(--tac-ok)}$V .mc .mcbar.won{color:var(--tac-mint)}
$V .tvh #tvmission{margin:0 0 10px}
$V .tvh #tvmission~.pill,$V .tvh #tvmission~.tvt,$V .tvh #tvmission~.tvm{display:none}
$V .mc .mtgt small{display:block;color:var(--tac-mut);font-size:var(--fs-small)}
$V #side:has(#tbody:empty) #tasks{flex:1 1 auto;max-height:none}
$V #side:not(:has(#tbody:empty)) #tasks .mc :is(.mcd,.mcbar){display:none}
$V .tvh{border-bottom:1px solid var(--tac-dim)}
$V .chip2{background:transparent;border-color:var(--tac-edge);color:var(--tac-body);font-family:var(--font-data)}
$V .chip2.ok{color:var(--tac-ok);border-color:var(--tac-dim)}$V .chip2.bad{color:var(--tac-red)}$V .chip2.warn{color:var(--tac-amber)}
$V .tvtabs button{font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.2em;text-transform:uppercase;color:var(--tac-mut)}
$V .tvtabs button.on{color:var(--tac-val);border-bottom-color:var(--tac-y)}
$V .tv-msg{font-family:var(--font-data)}$V .tv-msg .who,$V .tv-div{font-family:var(--font-tac);letter-spacing:.18em;text-transform:uppercase}
$V .tvfoot{background:transparent;border-top:1px solid var(--tac-dim)}
$V .tvfoot .tbtns button,$V .tvfoot .tfollow button{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-val);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.16em;text-transform:uppercase}
$V .tvfoot .tbtns button.ok,$V .tvfoot .tfollow button{background:var(--tac-y);border-color:var(--tac-y);color:var(--tac-ink)}
$V .tvfoot .tbtns button.warn{border-color:var(--tac-red);color:var(--tac-red)}
$V .tvfoot .tfollow textarea,$V .tvfoot .tbtns select{background:rgba(0,0,0,.35);border:1px solid var(--tac-edge);color:var(--tac-val);font-family:var(--font-data)}
/* the card: a planetary data card on the right, in with a 0.2 s fade and a 10 px slide */
$D #panel{top:56px;bottom:60px;left:auto;right:calc(var(--sidew) + 16px);height:auto;padding:20px 20px 16px;background:var(--tac-panel);border:1px solid var(--tac-edge);border-top:2px solid var(--tac-val);clip-path:var(--tac-cut);box-shadow:none;opacity:0;visibility:hidden;transform:translateX(10px);transition:opacity .2s ease,transform .2s ease,visibility 0s linear .2s}
$D #panel.on{opacity:1;visibility:visible;transform:none;transition:opacity .2s ease,transform .2s ease,visibility 0s}
$D #panel.card{width:min(460px,calc(100vw - var(--dashw) - var(--sidew) - 40px))}
$V #panel{color:var(--tac-body)}
$D #panel .close{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-body);font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.18em;text-transform:uppercase}
$V #panel .ckind,$V #panel .kind,$V #panel .csec,$V #panel h3{font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.26em;text-transform:uppercase;color:var(--tac-mint)}
$V #panel .ctabs{flex:none;border-color:var(--tac-edge)}
$V #panel .card.tac .ckind{flex:1 1 auto;min-width:0}
$V #panel .ctabs button{padding:0 8px;font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.12em;text-transform:uppercase;white-space:nowrap;color:var(--tac-mut)}
$V #panel .ctabs button.on{background:rgba(232,236,232,.1);color:var(--tac-val)}
$V #panel .cname,$V #panel h2{margin:10px 0 6px;font:600 var(--fs-big)/1.05 var(--font-tac);font-size:34px;letter-spacing:.04em;text-transform:uppercase;color:var(--tac-val)}
$V #panel .cpurpose{font:var(--fs-body)/var(--lh-body) var(--font-data);color:var(--tac-body)}
$V #panel .clink{color:var(--tac-mint)}
$V #panel .tdiv{height:1px;margin:14px 0 6px;background:var(--tac-dim)}
$V #panel dl.tdl{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:0;margin:0}
$V #panel dl.tdl dt{padding:6px 0;border-bottom:1px solid var(--tac-faint);font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.22em;color:var(--tac-mut)}
$V #panel dl.tdl dd{margin:0;padding:6px 0;border-bottom:1px solid var(--tac-faint);font:var(--fs-list)/var(--lh-small) var(--font-data);text-align:right;color:var(--tac-val)}
$V #panel dl.tdl dd.bad{color:var(--tac-red)}$V #panel dl.tdl dd.ok{color:var(--tac-ok)}
$V #panel dl dt{font-family:var(--font-tac);letter-spacing:.14em;text-transform:uppercase;color:var(--tac-mut)}$V #panel dl dd{font-family:var(--font-data);color:var(--tac-val)}
$V #panel .cstate{margin-top:12px;padding:8px 12px;background:rgba(0,0,0,.3);border-left:2px solid var(--tac-ok);font:var(--fs-body)/var(--lh-body) var(--font-data);color:var(--tac-body)}
$V #panel .cstate.warn{border-left-color:var(--tac-amber)}$V #panel .cstate.bad{border-left-color:var(--tac-red)}
$V #panel .card.tac .csec{margin:18px 0 6px}
$V #panel .cprob{gap:10px;padding:7px 8px 7px 2px;border-left:0;border-bottom:1px solid var(--tac-faint);font:var(--fs-body)/var(--lh-body) var(--font-data);color:var(--tac-val)}
$V #panel .cprob::before{content:'';flex:0 0 11px;height:10px;margin-top:4px;background:var(--tac-red);clip-path:polygon(50% 0,100% 100%,0 100%)}
$V #panel .cprob:hover{background:rgba(214,40,40,.12)}
$V #panel .cprob i{font:600 var(--fs-small)/var(--lh-body) var(--font-tac);letter-spacing:.16em;text-transform:uppercase;color:var(--tac-red)}
$V #panel .cacts{gap:10px;margin:16px 0 4px}
$V #panel .cacts button{display:inline-flex;align-items:center;gap:10px;height:36px;padding:0 16px;background:transparent;border:1px solid var(--tac-val);color:var(--tac-val);font:600 var(--fs-body)/1 var(--font-tac);letter-spacing:.16em;text-transform:uppercase}
$V #panel .cacts button:hover{border-color:var(--tac-y);color:var(--tac-y)}
$V #panel .cacts button.primary{background:var(--tac-y);border-color:var(--tac-y);color:var(--tac-ink)}$V #panel .cacts button.primary:hover{background:var(--tac-y2);color:var(--tac-ink)}
$V #panel .crow{border-bottom:1px solid var(--tac-faint);font-family:var(--font-data)}$V #panel .crow .n{color:var(--tac-val)}$V #panel .crow .m{color:var(--tac-mut)}$V #panel .crow:hover{background:rgba(229,184,0,.08)}
$V #panel .tbl td{border-top:1px solid var(--tac-faint)}
$V #panel details summary{font-family:var(--font-tac);letter-spacing:.16em;text-transform:uppercase;color:var(--tac-mut)}
$V #panel .tbtns button,$V #panel .say button,$V #panel .terminalbar button{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-val);box-shadow:none;font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.14em;text-transform:uppercase}
$V #panel .live,$V #panel pre.cjson,$V #panel .say textarea{background:rgba(0,0,0,.4);border:1px solid var(--tac-edge);color:var(--tac-val)}
$V #panel .card.tac.tin{animation:tacin .2s ease}
@keyframes tacin{from{opacity:0;transform:translateX(10px)}to{opacity:1;transform:none}}
/* the board, the map key, the prompt: chamfered panels with the heavy top rule */
$D #quick:empty{display:none}
$D #quests,$D #legend,$D #prompter,$D #meta{z-index:33;background:rgba(11,14,13,.97);border:1px solid var(--tac-edge);border-top:2px solid var(--tac-val);clip-path:var(--tac-cut);box-shadow:none}
$V #quests h2,$V #legend h2,$V #prompter h2,$V #meta h2{font:600 var(--fs-title)/1.2 var(--font-tac);letter-spacing:.28em;color:var(--tac-val)}
$V #quests .sub,$V #legend .sub,$V #prompter .sub,$V #meta .sub{color:var(--tac-mut)}
$V #legend .lg{border-color:var(--tac-faint)}$V #legend .lg b,$V #quests .col h3{font-family:var(--font-tac);letter-spacing:.14em;color:var(--tac-mint)}
$V #quests .q,$V .planet-key-row{background:rgba(11,14,13,.6);border-color:var(--tac-edge)}
$V #quests .q .sr button,$V #quests .q .sr select,$V #prompter select,$V #prompter button,$V #quests .sweep select,$V #quests .sweep button{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-val);font-family:var(--font-data)}
$V #prompter textarea{background:rgba(0,0,0,.35);border:1px solid var(--tac-edge);color:var(--tac-val)}
/* Today: an operations briefing. The mark and the stamp come in as custom properties (--tac-mark, --tac-stamp) */
$D #today{top:44px;bottom:48px}
$V #today{background-color:var(--tac-space);color:var(--tac-body)}
$V #today .th{padding-bottom:14px;border-bottom:1px solid var(--tac-dim)}
$D #today .th{align-items:flex-end;gap:16px}
$D #today .th .gen{flex:1 1 auto;min-width:0}
$D #today .th button{flex:none;display:inline-flex}
$V #today .th h1{font:600 var(--fs-big)/1 var(--font-tac);font-size:44px;letter-spacing:.16em;color:var(--tac-val)}
$V #today .th h1{flex:none}
$V #today .th h1::before{content:var(--tac-mark,'CONTAINER') ' · OPERATIONS BRIEFING';display:block;margin-bottom:8px;font-size:var(--fs-small);line-height:1;letter-spacing:.3em;white-space:nowrap;color:var(--tac-mint)}
$V #today .th .gen{font:var(--fs-small)/var(--lh-small) var(--font-data);letter-spacing:.06em;text-transform:uppercase;color:var(--tac-mut)}
$V #today .th .gen::before{content:var(--tac-stamp,'');display:block;letter-spacing:.12em;color:var(--tac-val)}
$V #today .th button{align-items:center;gap:10px;height:36px;padding:0 16px;background:transparent;border:1px solid var(--tac-val);color:var(--tac-val);box-shadow:none;font-size:0;white-space:nowrap;cursor:pointer}
$V #today .th button::before{display:inline-flex;align-items:center;justify-content:center;min-width:20px;height:18px;padding:0 4px;border:1px solid currentColor;font:var(--fs-small)/1 var(--font-data)}
$V #today .th button::after{font:600 var(--fs-body)/1 var(--font-tac);letter-spacing:.16em}
$V #today .th button.tnew{background:var(--tac-y);border-color:var(--tac-y);color:var(--tac-ink)}
$V #today .th button.tnew::before{content:'N'}$V #today .th button.tnew::after{content:'NEW TASK'}
$V #today .th button[data-go=map]::before{content:'M'}$V #today .th button[data-go=map]::after{content:'WHERE THINGS LIVE'}
$V #today .th button:hover{border-color:var(--tac-y);color:var(--tac-y)}$V #today .th button.tnew:hover{background:var(--tac-y2);color:var(--tac-ink)}
$V #today h2{display:flex;align-items:center;gap:10px;font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.26em;color:var(--tac-mint)}
$V #today h2::after{content:'';flex:1 1 auto;min-width:24px;border-top:1px solid var(--tac-dim)}
$V #today h2 b{font-family:var(--font-data);font-weight:400;letter-spacing:.04em;color:var(--tac-y)}
$V #today .it{background:rgba(11,14,13,.72);border-left:2px solid var(--tac-dim);font:var(--fs-list)/var(--lh-list) var(--font-data);color:var(--tac-val)}
$V #today .it.need{border-left-color:var(--tac-amber)}$V #today .it.work{border-left-color:var(--tac-ok)}$V #today .it.bad{border-left-color:var(--tac-red)}
$V #today .it small,$V #today .mut{color:var(--tac-mut)}
$V #today .lane{background:rgba(11,14,13,.72);font-family:var(--font-data)}$V #today .lane b,$V #today .wl{font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.22em;color:var(--tac-y)}
$V #today a.go{color:var(--tac-mint)}
$V #today .fold a.go{font:600 var(--fs-list)/var(--lh-list) var(--font-tac);letter-spacing:.12em;text-transform:uppercase}
$V #today .ftag{font-family:var(--font-data)}
$V #today section button{background:transparent;border:1px solid var(--tac-edge);color:var(--tac-val);box-shadow:none;font:600 var(--fs-small)/1 var(--font-tac);letter-spacing:.14em;text-transform:uppercase}
$V #today code{background:rgba(232,236,232,.06);color:var(--tac-val)}
$V #today .it.task{--mb:var(--tac-idle);--mbt:var(--tac-val);position:relative;margin:10px 0;padding:7px 12px 0 36px;border:1px solid var(--tac-edge);background:var(--mb) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 14'%3E%3Cpath d='M8 0L16 14H0Z' fill='rgb(8,10,9)'/%3E%3Cpath d='M7 5h2v4H7zM7 10h2v2H7z' fill='rgb(214,40,40)'/%3E%3C/svg%3E") 11px 9px/16px 14px no-repeat;clip-path:var(--tac-cut);font:600 var(--fs-list)/var(--lh-list) var(--font-tac);letter-spacing:.06em;text-transform:uppercase;color:var(--mbt)}
$V #today .it.task::before{content:'OPERATION '}
$V #today .it.task.work{--mb:var(--tac-ok);--mbt:var(--tac-ink);padding-left:12px;background-image:none}
$V #today .it.task.need,$V #today .it.task.bad{--mb:var(--tac-red);--mbt:var(--tac-val)}
$V #today .it.task small{display:block;margin:7px -12px 0 -36px;padding:7px 12px;background:var(--tac-solid);font:var(--fs-small)/var(--lh-small) var(--font-data);letter-spacing:.04em;text-transform:none;color:var(--tac-body)}
$V #today .it.task.work small{margin-left:-12px}
$V #today .it.task:hover{border-color:var(--tac-y)}
/* a phone: the same tokens; the tab bar is the bottom rail with keycap labels, the sheets chamfered under a heavy rule */
$M #hud{background:var(--tac-solid);border-bottom:1px solid var(--tac-line)}
$M #hud h1{font:600 var(--fs-title)/1 var(--font-tac);letter-spacing:.3em;color:var(--tac-val)}
$M #hud .sc{color:var(--tac-val)}$M #hud .sc::after{font-family:var(--font-tac);letter-spacing:.2em;text-transform:uppercase}
$M #hud #mmore{border-color:var(--tac-edge);color:var(--tac-val);font-family:var(--font-tac);letter-spacing:.16em;text-transform:uppercase}
$M #nums button{background:rgba(11,14,13,.92);border-color:var(--tac-edge);color:var(--tac-mut);font-family:var(--font-tac);letter-spacing:.12em;text-transform:uppercase}
$M #nums button b{font-family:var(--font-data);font-weight:400}
$M #tabs{background:var(--tac-solid);border-top:1px solid var(--tac-line)}
$M #tabs button{color:var(--tac-mut);font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.16em;text-transform:uppercase}
$M #tabs button .tl{padding:3px 8px 2px;border:1px solid var(--tac-edge);background:rgba(232,236,232,.04)}
$M #tabs button[aria-selected=true]{color:var(--tac-ink);box-shadow:inset 0 2px 0 var(--tac-y)}
$M #tabs button[aria-selected=true] .tl{background:var(--tac-y);border-color:var(--tac-y)}
$M.land #tabs button[aria-selected=true]{box-shadow:inset -2px 0 0 var(--tac-y)}
$M #tabs .tabn:not([hidden]){background:var(--tac-red);color:var(--tac-val)}
$M #panel,$M #msheet{background:var(--tac-solid);border-top:2px solid var(--tac-val);clip-path:var(--tac-cut)}
$M.land #panel{border-top:0;border-left:2px solid var(--tac-val)}
$M #panel .cname,$M #panel h2{font-size:26px}
$M #msheet-title{font:600 var(--fs-small)/var(--lh-small) var(--font-tac);letter-spacing:.26em;color:var(--tac-mint)}
$M #msheet-body .mrow{font-family:var(--font-data);border-color:var(--tac-faint)}
$M #mfab{background:var(--tac-y);border:1px solid var(--tac-ink);color:var(--tac-ink);box-shadow:none;font-family:var(--font-tac)}
$M #mzoom button,$M #mclose,$M #quick button{background:rgba(11,14,13,.92);border-color:var(--tac-edge);color:var(--tac-val)}
$M #pickbar{background:var(--tac-panel);border:1px solid var(--tac-y)}
$M #mtoast.on{background:var(--tac-panel);border-color:var(--tac-edge);border-left-color:var(--tac-y);font-family:var(--font-data)}
$M #quests,$M #prompter,$M #legend,$M #meta,$M #side,$M #today{background:var(--tac-space)}
$M body[data-tab=wingmate] #mate{background:var(--tac-space)}
$M #mate .mp{border-bottom:1px solid var(--tac-mint)}
$M #mate .mp b{background:none;color:var(--tac-mint);font-family:var(--font-tac);letter-spacing:.26em}
""".replace("$C", 'html[data-render="vector"]:not(.m):not(.t)').replace("$D", 'html[data-render="vector"]:not(.m)').replace("$M", 'html.m[data-render="vector"]').replace(
    "$V", 'html[data-render="vector"]')
HTML = """<!doctype html><html lang="en" data-theme="__THEME__"><head><meta charset="utf-8">
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 32 32%27%3E%3Crect width=%2732%27 height=%2732%27 rx=%278%27 fill=%27%230d1526%27/%3E%3Cellipse cx=%2716%27 cy=%2716%27 rx=%2712%27 ry=%277%27 transform=%27rotate(-35 16 16)%27 fill=%27none%27 stroke=%27%236fb7ff%27 stroke-width=%271.5%27/%3E%3Ccircle cx=%2716%27 cy=%2716%27 r=%274%27 fill=%27%23ffd166%27/%3E%3Ccircle cx=%2726%27 cy=%2711%27 r=%273%27 fill=%27%23e8eef6%27/%3E%3C/svg%3E"><title>CONTAINER</title>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<script id="theme-early">try{var n=__THEMENAMES__,al=__THEMEALIAS__,t=null,g=false;try{g=sessionStorage.getItem('theme')==='gb';}catch(e){}t=g?'classic':localStorage.getItem('theme');t=al[t]||t;if(t&&(g||n.indexOf(t)>=0))document.documentElement.setAttribute('data-theme',t);}catch(e){}</script>
<script id="mobile-early">try{(window.mobEarly=function(){var h=document.documentElement,q=function(s){return matchMedia(s).matches;},m=q("(max-width:700px), (pointer:coarse) and (max-height:500px)");h.classList.toggle("m",m);h.classList.toggle("land",m&&innerWidth>innerHeight);h.classList.toggle("t",!m&&q("(pointer:coarse)"));})();}catch(e){}</script>
<style>
/* ---- type and space tokens (design round one): two faces, one scale, a 4-px grid. DISPLAY is the
   pixel face for the few words that are the game (HUD title, plates, name tags, big numbers), drawn
   at whole multiples of its 8-px grid so it stays crisp; everything else is the system mono. Nothing
   reads under 12 px except the eyebrow: 11 px, uppercase and letter-spaced, capitals as tall as 14 px
   lowercase. ---- */
:root{--font-display:"Silkscreen","Pixelify Sans",monospace;--font-tac:"Barlow Condensed","Arial Narrow","Roboto Condensed",sans-serif;--font-data:"Share Tech Mono",ui-monospace,Menlo,Consolas,monospace;--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace;
--fs-eyebrow:11px;--fs-small:12px;--fs-body:13px;--fs-list:14px;--fs-title:16px;--fs-hud:20px;--fs-big:28px;
--lh-eyebrow:14px;--lh-small:16px;--lh-body:18px;--lh-list:20px;--lh-title:22px;--lh-hud:24px;--lh-big:32px;
--sp1:4px;--sp2:8px;--sp3:12px;--sp4:16px;--sp5:20px;--row:32px}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#04060c;color:#cfd8e3;overflow:hidden;
font:var(--fs-body)/1.5 var(--font-mono)}
#wrap{position:relative;width:100vw;height:100vh;height:100dvh;--dashw:0px;--sidew:0px}
#wrap.dash{--dashw:300px}#wrap.side{--sidew:460px}
#stage{position:absolute;top:0;bottom:0;left:var(--dashw);right:var(--sidew);overflow:hidden}
/* ---- the rails: dashboard left, threads right; the stage between them is the playing field ---- */
#dash{position:absolute;z-index:6;left:0;top:30px;bottom:26px;width:300px;display:none;overflow:auto;background:#0b1428f8;border-right:2px solid #344c6b;padding:12px 12px;font-size:var(--fs-small);color:#d7e0ea}
#wrap.dash #dash{display:block}
#dash h3{margin:12px 0 4px;font-size:var(--fs-eyebrow);letter-spacing:.16em;color:#9bacc0;display:flex;gap:8px;align-items:center}#dash h3 a{margin-left:auto;color:#5fb4ff;cursor:pointer;font-weight:400;letter-spacing:0}
#dash .row{display:flex;gap:8px;align-items:flex-start;padding:8px 8px;margin:4px 0;background:#101c31;border:1px solid #344c6b;cursor:pointer;line-height:1.35}
#dash .row:hover{border-color:#5fb4ff}#dash .row i{flex:0 0 8px;height:8px;margin-top:4px;border-radius:0;background:#6b7684}
#dash .row .wl{flex:0 0 auto;font-size:var(--fs-eyebrow);letter-spacing:.12em;color:#ffd166;text-transform:uppercase;margin-top:2px}
#dash .row small{display:block;color:#9bacc0;margin-top:1px}#dash .mut{color:#9bacc0;margin:4px 0;font-size:var(--fs-small)}
#dash .ok i{background:#3dff9e}#dash .bad i{background:#ff4d5e}#dash .need i{background:#ffbd2e}#dash .work i{background:#3dff9e}
#side{position:absolute;z-index:6;right:0;top:30px;bottom:26px;width:460px;display:none;flex-direction:column;background:#0a1020;border-left:1px solid #344c6b;color:#d7e0ea}
#wrap.side #side{display:flex}
#side .shead{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid #344c6b;font-size:var(--fs-small)}#side .shead b{color:#cfe0f5;letter-spacing:.14em}#side .shead span{color:#6b86a6}
#side .shead button{background:transparent;border:1px solid #344c6b;color:#cfe0f5;cursor:pointer;font:var(--fs-small) var(--font-mono);padding:4px 8px}#side .shead #snew{margin-left:auto;background:#3dff9e;color:#04210f;border-color:#3dff9e;font-weight:600}
#tbody{flex:1 1 auto;min-height:0;display:flex;flex-direction:column;padding:0;border-top:1px solid #344c6b;overflow:hidden}
#tbody:empty{display:none}
#side.compose #tasks,#side.compose #tbody{display:none}
#hud{left:calc(var(--dashw) + 12px)}#mate{left:calc(var(--dashw) + 12px)}#mateshow{left:calc(var(--dashw) + 12px)}#comms{left:calc(var(--dashw) + 20px)}
#quick{left:calc(var(--dashw) + (100vw - var(--dashw) - var(--sidew))/2)}#ticker{left:calc(var(--dashw) + (100vw - var(--dashw) - var(--sidew))/2)}
#panel{right:var(--sidew)}
@media (max-width:1000px){#wrap.dash{--dashw:0px}#wrap.side{--sidew:0px}#dash{display:none!important}#side{width:100vw;left:0}#panel{right:0}}
canvas{display:block;image-rendering:pixelated;width:100%;height:100%;touch-action:none}
#hud{position:absolute;top:12px;left:12px;pointer-events:none;text-shadow:0 1px 3px #000}
#hud h1{margin:0;font-size:var(--fs-body);letter-spacing:.3em;color:#9fb4cc}
#hud .sc{font-size:var(--fs-big);color:#ffb547;line-height:1.1;margin-top:4px}
#hud .sc small{font-size:var(--fs-eyebrow);color:#9bacc0;letter-spacing:.08em;display:block;text-transform:uppercase}
#help{position:absolute;bottom:12px;left:12px;font-size:var(--fs-small);color:#9bacc0;pointer-events:none;
text-shadow:0 1px 3px #000}
#panel{position:absolute;z-index:6;right:0;top:0;height:100%;width:clamp(560px,50vw,840px);
background:#0d1119f7;border-left:2px solid #344c6b;padding:16px;overflow:hidden;
transform:translateX(100%);transition:transform .16s ease}
#panel.on{transform:none}
#pbody{height:100%;min-height:0;overflow:auto;padding-right:2px}
#panel.seat #pbody{overflow:hidden}
.seatview{height:100%;min-height:0;display:flex;flex-direction:column}
.seathead{flex:0 0 auto;padding-right:56px}
#panel h2{margin:0 0 2px;font-size:var(--fs-list);letter-spacing:.06em}
#panel .kind{font-size:var(--fs-eyebrow);color:#9bacc0;text-transform:uppercase;letter-spacing:.12em}
#panel dl{margin:16px 0 0;display:grid;grid-template-columns:82px 1fr;gap:8px 12px;font-size:var(--fs-small)}
#panel dt{color:#9bacc0}#panel dd{margin:0;color:#cfd8e3;word-break:break-word}
#panel .bar{height:7px;background:#1a212b;border-radius:0;margin-top:16px;overflow:hidden}
#panel .bar i{display:block;height:100%;background:#3fb950}
#panel .cause{margin-top:16px;padding:8px 12px;background:#2a1214;border:1px solid #5c1f22;
border-radius:0;color:#f0a3a3;font-size:var(--fs-small)}
#panel .close{position:absolute;z-index:2;top:10px;right:12px;color:#9bacc0;background:#141922;
border:1px solid #344c6b;border-radius:0;min-width:44px;min-height:34px;cursor:pointer}
#staticbanner{position:absolute;z-index:9;top:0;left:0;right:0;padding:8px 12px;background:#ffd166;color:#090e21;font:600 var(--fs-small) var(--font-mono);letter-spacing:.06em;text-align:center}#staticbanner a{color:#090e21}
#quests,#meta{position:absolute;z-index:6;left:50%;top:50%;transform:translate(-50%,-50%) scale(.96);
width:min(760px,92vw);max-height:78vh;overflow:auto;background:#0d1119f2;border:2px solid #344c6b;
border-radius:0;padding:20px 24px;opacity:0;pointer-events:none;transition:.15s;box-shadow:4px 4px 0 #000}
#quests.on,#meta.on{opacity:1;pointer-events:auto;transform:translate(-50%,-50%) scale(1)}
#quests h2,#meta h2{margin:0 0 4px;font-size:var(--fs-body);letter-spacing:.16em;color:#e3b341}
#quests .sub,#meta .sub{font-size:var(--fs-small);color:#9bacc0;margin-bottom:16px}
.q{display:flex;gap:12px;padding:8px 0;border-top:1px solid #344c6b;align-items:baseline}
.q:first-of-type{border-top:0}
.q .age{flex:0 0 62px;font-size:var(--fs-small)}.q .tx{flex:1;font-size:var(--fs-small)}
.q .sr{flex:0 0 auto;font-size:var(--fs-small);color:#9bacc0}
.old .age{color:#f85149}.mid .age{color:#d29922}.new .age{color:#58a6ff}
#glance-alert{margin-top:8px;font-size:var(--fs-small);color:#0d1119;background:#e3b341;padding:4px 8px;
border-radius:0;display:none;pointer-events:none}
#glance-alert.on{display:inline-block;animation:gl 1.1s ease-in-out infinite}
@keyframes gl{0%,100%{opacity:1}50%{opacity:.35}}
#permits{position:absolute;z-index:5;top:12px;left:50%;transform:translateX(-50%);
display:none;flex-direction:column;gap:8px;pointer-events:auto;max-width:min(420px,70vw)}
#permits.on{display:flex}
#permits .pcard{background:#e3b341;color:#0d1119;padding:8px 12px;border:2px solid #0d1119;
box-shadow:3px 3px 0 #000;font-size:var(--fs-small)}
#permits .pcard b{display:block;letter-spacing:.06em;margin-bottom:4px}
#permits .pq{font-size:var(--fs-small);color:#3a2a08;white-space:pre-wrap;max-height:4.5em;overflow:hidden;margin:0 0 8px}
#permits .prow{display:flex;gap:8px}
#permits button{cursor:pointer;border:1px solid #0d1119;background:#0d1119;color:#e3b341;
padding:4px 12px;font:var(--fs-small) var(--font-mono)}
#permits button.deny{background:#2a1214;color:#f0a3a3;border-color:#5c1f22}
#permits button.open{background:transparent;color:#0d1119}
#permitbox{margin-top:12px;padding:8px 12px;background:#3a2a08;border:1px solid #e3b341}
#permitbox .pq{font-size:var(--fs-small);color:#e6d7a8;white-space:pre-wrap;margin:0 0 8px}
#permitbox .prow{display:flex;gap:8px}
#permitbox button{cursor:pointer;border:1px solid #e3b341;background:#e3b341;color:#0d1119;
padding:8px 12px;font:var(--fs-small) var(--font-mono)}
#permitbox button.deny{background:#2a1214;color:#f0a3a3;border-color:#5c1f22}
#taskbar{position:absolute;left:0;right:0;bottom:0;height:34px;background:#0b0e14ee;
border-top:2px solid #344c6b;display:flex;align-items:center;gap:8px;padding:0 12px;
font-size:var(--fs-small);color:#9bacc0;overflow-x:auto;white-space:nowrap}
#taskbar .tb{display:flex;align-items:center;gap:8px;padding:4px 8px;background:#141922;
border:1px solid #344c6b;border-radius:0}
#taskbar .tb b{color:#cfd8e3;font-weight:400}
#taskbar .tb i{width:6px;height:6px;border-radius:0;background:#3fb950;display:inline-block}
#taskbar .idlebar{color:#9bacc0;border-style:dashed}
#taskbar .spacer{margin-left:auto;color:#9bacc0}
#help{bottom:44px}
.tag{position:absolute;font-size:var(--fs-eyebrow);color:#9bacc0;letter-spacing:.14em;pointer-events:none}
.terminalbar{display:flex;align-items:center;gap:8px;margin-top:12px;color:#9bacc0;font-size:var(--fs-small)}
.terminalbar .grow{flex:1}.terminalbar button,.say button,#quick button,#touchpad button,#theme button{
background:#141922;border:1px solid #344c6b;color:#aeb9c6;border-radius:0;cursor:pointer;
font:var(--fs-small) var(--font-mono);min-height:34px;padding:4px 8px;box-shadow:2px 2px 0 #000}
.terminalbar button:active,.say button:active,#quick button:active,#touchpad button:active,#theme button:active{
transform:translate(1px,1px);box-shadow:1px 1px 0 #000}
#panel .say textarea:focus{outline:2px solid #58a6ff;outline-offset:1px}
#theme{position:absolute;z-index:4;top:11px;right:12px}
#theme button.on{background:#8bac0f;border-color:#0f380f;color:#0f380f}
body.gb{background:#0f380f;color:#0f380f}
body.gb #hud h1{color:#9bbc0f}
body.gb #hud .sc{color:#9bbc0f}
body.gb #hud .sc small{color:#8bac0f}
body.gb #panel{background:#9bbc0ff2;border-left:2px solid #0f380f}
body.gb #panel h2{color:#0f380f}
body.gb #panel .kind,body.gb #panel dt{color:#306230}
body.gb #panel dd{color:#0f380f}
body.gb #panel .bar{background:#8bac0f}
body.gb #panel .bar i{background:#0f380f}
body.gb #panel .cause{background:#8bac0f;border-color:#0f380f;color:#0f380f}
body.gb #panel .live{background:#8bac0f;border-color:#0f380f;color:#0f380f}
body.gb #panel .say textarea{background:#8bac0f;border-color:#0f380f;color:#0f380f}
body.gb #panel .say textarea:focus{outline-color:#0f380f}
body.gb .terminalbar button,body.gb .say button,body.gb #quick button,body.gb #touchpad button,body.gb #theme button,body.gb #panel .close{background:#0f380f;border-color:#0f380f;color:#9bbc0f;box-shadow:2px 2px 0 #306230}
body.gb #quests,body.gb #meta{background:#9bbc0ff2;border-color:#0f380f;box-shadow:4px 4px 0 #0f380f}
body.gb #quests h2,body.gb #meta h2{color:#0f380f}
body.gb #quests .sub,body.gb #meta .sub{color:#306230}
body.gb .q{border-top-color:#306230}
body.gb .q .tx{color:#0f380f}
body.gb #taskbar{background:#0f380fee;border-top:2px solid #0f380f;color:#8bac0f}
body.gb #taskbar .tb{background:#0f380f;border-color:#0f380f;color:#8bac0f}
body.gb #taskbar .tb b{color:#9bbc0f}
body.gb #taskbar .spacer{color:#8bac0f}
body.gb .viewonly{border-color:#0f380f;color:#0f380f}
body.gb .saymeta{color:#306230}
body.gb #help{color:#8bac0f}
.terminalbar button:disabled,.say button:disabled{opacity:.45;cursor:not-allowed}
#panel .live{flex:1 1 60vh;min-height:260px;margin-top:8px;padding:12px 12px;background:#07090d;
border:1px solid #344c6b;border-radius:0;font-size:var(--fs-body);line-height:1.38;overflow:auto;
white-space:pre;overflow-wrap:normal;color:#aeb9c6;tab-size:4}
#panel .say{flex:0 0 auto;margin-top:12px}
#panel .say form{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:stretch}
#panel .say textarea{width:100%;min-height:84px;max-height:180px;resize:vertical;background:#07090d;
border:1px solid #344c6b;color:#d7dee7;padding:8px 12px;font:var(--fs-body)/1.4 var(--font-mono);
border-radius:0}
#panel .say textarea:disabled{opacity:.6}
#panel .say button{min-width:82px}
.saymeta{display:flex;gap:12px;align-items:center;margin-top:4px;font-size:var(--fs-small);color:#9bacc0}
.saymeta .count{margin-left:auto}
#panel .saym{margin-top:8px;font-size:var(--fs-small);color:#9bacc0;min-height:1em}
#panel .saym.err{color:#f85149}
.viewonly{padding:8px 12px;border:1px dashed #344c6b;border-radius:0;color:#9bacc0}
.tbl{width:100%;border-collapse:collapse;font-size:var(--fs-small);margin-top:8px}
.tbl td{padding:4px 8px;border-top:1px solid #344c6b;vertical-align:top;word-break:break-word}
.tbl td.LIVE{color:#3fb950}.tbl td.WARM{color:#58a6ff}.tbl td.COLD{color:#9bacc0}
.tbl td.DOWN,.tbl td.ERROR,.tbl td.NEVER,.tbl td.STALE{color:#f85149}
.tbl td.BLANK,.tbl td.EMPTY,.tbl td.UNWALKED,.tbl td.NO_RECEIPT,.tbl td.UNKNOWN{color:#9bacc0;font-style:italic}
.tbl td.RUNNING{color:#3fb950}.tbl td.LATE{color:#e3b341}
#panel h3{margin:16px 0 2px;font-size:var(--fs-eyebrow);letter-spacing:.14em;color:#9bacc0;text-transform:uppercase}
.cmd{margin:8px 0 0;padding:8px 12px;background:#07090d;border:1px solid #344c6b;border-radius:0;
font-size:var(--fs-small);white-space:pre-wrap;word-break:break-all;color:#d7dee7}
#panel .say button#doorcopy{margin-top:8px}
/* ---- QUIET LAYOUT (a layout the user picked): the map and one bar. H shows the full HUD. ---- */
body.quiet #hud,body.quiet #quick,body.quiet #comms,body.quiet #ticker,body.quiet #help,body.quiet #taskbar{display:none}
#topbar{position:absolute;z-index:5;top:0;left:0;right:0;height:30px;display:flex;align-items:center;gap:16px;padding:0 16px;background:#0b0e14ee;border-bottom:1px solid #344c6b;font:var(--fs-small) var(--font-mono);color:#9fb4cc;letter-spacing:.06em;white-space:nowrap;overflow:hidden}
#topbar b{color:#ffd166;letter-spacing:.2em}#topbar .hot{color:#e3b341}#topbar .old{color:#f85149}#topbar .sp{margin-left:auto;color:#9bacc0}
#topbar a[data-go]{cursor:pointer;text-decoration:none;color:inherit}#topbar a[data-go]:hover{color:#e6edf3;text-decoration:underline}
#keyline a{cursor:pointer;margin-right:16px}#keyline a:hover{color:#cfe0f5}
#hud .sc{pointer-events:auto;cursor:pointer}#hud .sc:hover{color:#ffd08a}#ticker{cursor:pointer}
/* ---- the wingmate: a comms portrait in the corner ---- */
#mate{position:fixed;z-index:31;left:12px;bottom:40px;display:flex;gap:0;align-items:stretch;width:min(470px,92vw);filter:drop-shadow(4px 4px 0 #030817)}
#mate .mp{flex:0 0 112px;position:relative;background:radial-gradient(circle at 50% 40%,#16386a 0%,#0a1a33 70%);border:2px solid #5fb4ff;border-right:0;padding:4px}
#mate .mp:after{content:'';position:absolute;inset:4px;background:repeating-linear-gradient(0deg,rgba(255,255,255,.05) 0 1px,transparent 1px 3px);pointer-events:none}
#mate .mp canvas{display:block;width:104px;height:104px;image-rendering:pixelated}
#mate .mp b{position:absolute;left:-2px;top:-18px;background:#5fb4ff;color:#04121f;font:600 var(--fs-eyebrow) var(--font-mono);letter-spacing:.16em;padding:2px 8px;text-transform:uppercase}
#mate .nb{flex:1;min-width:0;background:#0b1428f4;border:2px solid #5fb4ff;padding:8px 12px;font-size:var(--fs-body);line-height:1.45;color:#d7e0ea;display:flex;flex-direction:column}
#mate .nh{display:flex;flex-wrap:wrap;gap:2px 8px;align-items:center;font-size:var(--fs-small);color:#6b86a6;letter-spacing:.06em;margin-bottom:4px}
#mate .nh #matehide{margin-left:auto;background:transparent;border:1px solid #344c6b;color:#9bacc0;font:var(--fs-small) var(--font-mono);padding:1px 8px;cursor:pointer}
#mate .nh #matepill{background:#192c49;border:1px solid #344c6b;border-radius:0;color:#c9dcf6;font:600 var(--fs-eyebrow) var(--font-mono);letter-spacing:.1em;text-transform:uppercase;padding:1px 8px;cursor:pointer}
#mate .nh #matepill:hover:not(:disabled){border-color:#5fb4ff;color:#fff}#mate .nh #matepill:disabled{cursor:default}
#mate .nh #matealso{background:none;border:0;color:#7fb8ff;font:var(--fs-small) var(--font-mono);text-decoration:underline;padding:0;cursor:pointer}
#mate .nh #matealso:hover{color:#fff}
#matetxt{flex:1;min-height:40px;max-height:150px;overflow:auto;white-space:pre-wrap;word-break:break-word}
#matef{display:flex;gap:8px;margin-top:8px}#mateq{flex:1;min-width:0;background:#07090d;border:1px solid #344c6b;color:#e6edf3;padding:4px 8px;font:var(--fs-small) var(--font-mono)}
#mateq:focus{outline:2px solid #5fb4ff;outline-offset:1px}
/* ---- the radio: a seat calling in at the lower left, above the wingmate (placed from the script) ---- */
#radio{display:none;position:fixed;z-index:32;left:12px;bottom:200px;max-width:min(470px,calc(100vw - 24px));cursor:pointer;filter:drop-shadow(4px 4px 0 #030817)}
#radio.on{display:flex;align-items:stretch}
#radio canvas{display:block;flex:0 0 auto;width:96px;height:96px;image-rendering:pixelated;background:#0a1a33;border:2px solid #3ef2a0}
#radio .rt{flex:1 1 auto;min-width:240px;background:#181c48ee;border:2px solid #4a55a8;border-left:0;padding:8px 12px;display:flex;flex-direction:column;gap:6px}
#radio .rh{display:flex;gap:10px;align-items:baseline}
#radio b{color:#facc15;font:600 var(--fs-eyebrow) var(--font-mono);letter-spacing:.16em;text-transform:uppercase}
#radio small{color:#9bacc0;font:var(--fs-small) var(--font-mono)}
#radio .rl{color:#ffffff;font:var(--fs-body)/1.4 var(--font-mono);white-space:pre-wrap;word-break:break-word;min-height:1.4em}
#radio:focus-visible{outline:2px solid #ffe28a;outline-offset:2px}
html.m #radio{display:none!important}
body.gb #radio canvas{background:#0f380f;border-color:#306230}
body.gb #radio .rt{background:#0f380f;border-color:#306230}
body.gb #radio b,body.gb #radio small,body.gb #radio .rl{color:#9bbc0f}
#matef button{background:#192c49;border:1px solid #344c6b;color:#c9dcf6;cursor:pointer;font:var(--fs-small) var(--font-mono);padding:4px 12px}
#mateshow{position:fixed;z-index:31;left:12px;bottom:40px;background:#0b1428f2;border:2px solid #5fb4ff;color:#5fb4ff;font:600 var(--fs-eyebrow) var(--font-mono);letter-spacing:.14em;padding:8px 12px;cursor:pointer;box-shadow:3px 3px 0 #030817}
body.mateoff #mate{display:none}body:not(.quiet):not(.mateoff) #comms{bottom:200px}
#today.on~#mate,#today.on~#mateshow{display:none}   /* Today is a page of its own, at every width: the wingmate is a map card ("map only"), and it sat over the folder list so nothing under it could be read or tapped; it is back as soon as Today closes */
#wrap.panel-open #mate{width:min(470px,calc(100vw - var(--dashw) - min(480px,92vw) - var(--sidew) - 30px))}
@media (max-width:700px){#mate{width:calc(100vw - 24px);bottom:200px}#wrap.panel-open #mate{display:none}}
#keyline{position:absolute;z-index:5;bottom:0;left:0;right:0;height:26px;display:flex;align-items:center;padding:0 16px;background:#0b0e14cc;font:var(--fs-small) var(--font-mono);color:#9bacc0;letter-spacing:.06em}
body:not(.quiet) #topbar,body:not(.quiet) #keyline{display:none}
#quick{position:absolute;z-index:4;top:11px;left:50%;transform:translateX(-50%);display:flex;gap:8px}
#quick button{min-height:32px;padding:4px 8px;background:#0d1119e8}
#quick button b{color:#e6edf3;margin-right:4px}
#touchpad{display:none;position:absolute;z-index:4;left:12px;bottom:48px;width:144px;height:144px;
grid-template-columns:repeat(3,44px);grid-template-rows:repeat(3,44px);gap:4px;touch-action:none}
#touchpad button{padding:0;font-size:var(--fs-hud);min-width:44px;min-height:44px;background:#0d1119e8}
#touchpad [data-dir="up"]{grid-column:2}#touchpad [data-dir="left"]{grid-column:1;grid-row:2}
#touchpad [data-dir="down"]{grid-column:2;grid-row:2}#touchpad [data-dir="right"]{grid-column:3;grid-row:2}
@media (pointer:coarse),(max-width:700px){
  #touchpad{display:grid}#wrap.panel-open #touchpad{display:none}
  #panel{inset:0;width:100vw;height:100vh;height:100dvh;border:0;padding:max(12px,env(safe-area-inset-top))
    max(12px,env(safe-area-inset-right)) max(12px,env(safe-area-inset-bottom)) max(12px,env(safe-area-inset-left))}
  #panel .live{min-height:0;font-size:var(--fs-body)}#panel .say textarea{font-size:var(--fs-title);min-height:76px;max-height:128px}
  #panel .say button{min-width:72px;min-height:44px}#wrap.panel-open #quick,#wrap.panel-open #help{display:none}
  #quick{top:auto;bottom:42px;max-width:calc(100vw - 174px);overflow-x:auto;transform:none;left:168px}
  #quick button{min-width:56px;min-height:44px}#quests{width:96vw;max-height:88dvh}
}
/* ---- 16-bit HUD: sector readout, comms radio panel, digest ticker, sector-coloured panel ---- */
#hud h1{color:#ffd166;text-shadow:2px 2px 0 #000}
#hud .sc{text-shadow:2px 2px 0 #000}
#sector{margin-top:8px;font-size:var(--fs-eyebrow);letter-spacing:.18em;color:#9fb4cc;text-transform:uppercase;text-shadow:1px 1px 0 #000}
#sector b{font-weight:600;color:var(--sec,#9ad0ff)}
#comms{position:absolute;z-index:4;left:12px;bottom:80px;width:390px;font-size:var(--fs-small)}
#comms .ct{font-size:var(--fs-eyebrow);letter-spacing:.18em;color:#8b98a8;margin-bottom:4px;text-shadow:1px 1px 0 #000}
#comms .row{display:flex;gap:8px;align-items:center;background:#04060cee;border:2px solid #344c6b;
border-left:6px solid var(--c,#58a6ff);padding:4px 8px;margin-top:4px;cursor:pointer;box-shadow:3px 3px 0 #000}
#comms .row:hover{border-color:var(--c,#58a6ff)}
#comms .row.hot{border-color:#e3b341;animation:gl 1.1s ease-in-out infinite}
#comms .pt{width:22px;height:18px;flex:0 0 auto;background:var(--c,#58a6ff);
box-shadow:inset 0 0 0 3px #04060c,inset 0 0 0 5px var(--c,#58a6ff)}
#comms .nm{flex:0 0 78px;color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:1.2}
#comms .nm small{display:block;font-size:var(--fs-eyebrow);color:var(--c,#9bacc0);text-transform:uppercase;letter-spacing:.08em}
#comms .tl{flex:1;color:#aeb9c6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:var(--fs-small)}
#ticker{position:absolute;z-index:3;left:50%;transform:translateX(-50%);bottom:40px;width:min(760px,58vw);
display:flex;gap:12px;align-items:center;background:#04060cee;border:2px solid #344c6b;padding:4px 12px;
font-size:var(--fs-small);color:#aeb9c6;box-shadow:3px 3px 0 #000;pointer-events:none}
#ticker .tk{color:#ffd166;letter-spacing:.16em;font-size:var(--fs-eyebrow);flex:0 0 auto}
#tkbody{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#focus{margin-top:8px;padding:4px 8px;border:1px solid #344c6b;color:#ffd166;font:var(--fs-small) var(--font-mono);letter-spacing:.06em}
#gohome{position:fixed;z-index:30;top:38px;right:12px;padding:8px 12px;background:#141922;border:2px solid #3fb950;color:#7ee787;font:var(--fs-small) var(--font-mono);cursor:pointer;letter-spacing:.08em}#gohome:hover{background:#0d2a18}
#help{bottom:14px;left:50%;transform:translateX(-50%);text-align:center;white-space:nowrap}
#panel{border-left:3px solid var(--accent,#212a36)}
#panel h2{color:var(--accent,#cfd8e3)}
#taskbar .tb{border-color:#344c6b;box-shadow:2px 2px 0 #000}
@media (pointer:coarse),(max-width:980px){#comms,#ticker{display:none}#help{left:12px;transform:none;text-align:left;white-space:normal}}
/* Flight instruments: one frame weight, quiet navy panels, readable evidence. */
html,body{background:#080d20;color:#c8dcf5}
button:focus-visible,textarea:focus-visible,pre:focus-visible{outline:2px solid #ffcf69;outline-offset:3px}
#hud{top:20px;left:20px;max-width:310px;padding:16px 20px;background:#0b1428ed;
border:2px solid #344c6b;box-shadow:inset 0 2px #556786,4px 4px 0 #030817;text-shadow:none}
#hud h1{font-size:var(--fs-list);letter-spacing:.28em;color:#dbe9ff;text-shadow:none}
#hud h1::before{content:"";display:inline-block;width:8px;height:8px;background:#ffcf69;margin-right:12px;box-shadow:3px 3px 0 #745f40}
#hud .sc{font-size:var(--fs-hud);margin-top:12px;color:#ffb547;text-shadow:none}
#hud .sc small{font-size:var(--fs-small);color:#acbad0;margin-top:4px;letter-spacing:.06em}
#hud>div[style]:not(#roundstag){color:#8fa3c0!important;font-size:var(--fs-small)!important;margin-top:8px!important}
#sector{border-top:1px solid #344c6b;padding-top:8px;margin-top:12px;font-size:var(--fs-small);line-height:1.6;letter-spacing:.09em;color:#9fb3d1;max-width:270px}
#glance-alert{font-size:var(--fs-small);margin-top:12px;padding:8px 8px;border-radius:0;animation:none}
#quick{top:20px;padding:4px;background:#0b1428ed;border:2px solid #344c6b;gap:4px;box-shadow:4px 4px 0 #030817}
#quick button{border:1px solid #344c6b;border-radius:0;background:#15233c;color:#a8c2e1;min-height:34px;padding:4px 12px}
#quick button:hover{background:#263a57;color:#fff}#quick button b{display:inline-block;margin-right:8px;color:#ffcf69}
#theme{top:20px;right:20px}#gbbtn{border-radius:0;border:2px solid #344c6b;background:#0c172c;box-shadow:3px 3px 0 #030817;min-height:44px;color:#bbd1ec}
#comms{left:20px;bottom:122px;width:350px;background:#0a1328ed;border:2px solid #344c6b;padding:12px;box-shadow:4px 4px 0 #030817}
#comms .ct{color:#b0c7e4;font-size:var(--fs-small);letter-spacing:.09em;line-height:1.6;margin:0 0 8px}
#comms .row{padding:8px 8px;border:1px solid #344c6b;border-left:3px solid var(--c,#58a6ff);background:#101c31;box-shadow:none;gap:12px;min-height:42px}
#comms .row.hot{border-color:var(--c,#e3b341);animation:none;background:#2b2630}
#comms .nm{flex-basis:66px;font-size:var(--fs-small)}#comms .nm small{font-size:var(--fs-small);margin-top:2px}#comms .tl{font-size:var(--fs-small);color:#b6c9e1}
#comms .pt{width:20px;height:20px;clip-path:polygon(40% 0,60% 0,65% 40%,100% 70%,100% 90%,65% 75%,60% 100%,40% 100%,35% 75%,0 90%,0 70%,35% 40%);box-shadow:none}
#ticker{bottom:68px;width:min(780px,calc(100vw - 64px));min-height:32px;border:2px solid #344c6b;background:#0b1428f5;padding:8px 12px;font-size:var(--fs-small);box-shadow:3px 3px 0 #030817}
#ticker .tk{font-size:var(--fs-small);color:#ffd07a;border-right:1px solid #344c6b;padding-right:12px}
#help{bottom:39px;font-size:var(--fs-small);color:#9db3d1;background:#080f20db;padding:4px 8px;width:calc(100vw - 40px);max-width:calc(100vw - 40px);white-space:normal}
#taskbar{height:34px;background:#0b1428;border-top:2px solid #344c6b;padding:0 20px;gap:12px;font-size:var(--fs-small)}
#taskbar .tb{border:0;border-radius:0;box-shadow:none;background:transparent;padding:2px 0;color:#a6bdd9;white-space:nowrap}
#taskbar .spacer{color:#8ea4c1;white-space:nowrap}
#panel{background:#0b1428fa;border-left:3px solid var(--accent,#526e8e);padding:24px;box-shadow:-8px 0 0 #04091799}
#panel .kind{font-size:var(--fs-small);color:#91aacb}#panel h2{font-size:var(--fs-hud);letter-spacing:.02em;margin-top:4px}
#panel dl{font-size:var(--fs-small);gap:8px 12px}#panel dt{color:#819bbc}#panel dd{color:#c6d8ef}
#panel .live{font-size:var(--fs-list);line-height:1.6;background:#070e1e;border:2px solid #344c6b;border-radius:0;color:#c9dcf6}
#panel .say textarea{border:2px solid #344c6b;background:#0e1c32;border-radius:0;color:#deecff;padding:12px;font-size:var(--fs-list)}
#panel .say button,.terminalbar button,#panel .close{border-radius:0;background:#192c49;border-color:#344c6b;color:#c9dcf6}
#panel .say button:not(:disabled){background:#bdd9f8;color:#0d1a2c;border-color:#def0ff}
#quests,#meta{background:#0b1428f7;border:2px solid #344c6b;border-radius:0;box-shadow:6px 6px 0 #030817}
@media(max-width:1100px){#quick{left:auto;right:78px;transform:none}#quick button{padding:4px 8px}#hud{max-width:270px}#comms{width:310px}}
@media(pointer:coarse),(max-width:980px){#comms{display:block;bottom:144px;left:12px;width:min(300px,44vw);max-height:170px;overflow:auto}#ticker{display:flex;bottom:75px;width:calc(100vw - 24px)}#help{bottom:37px;left:12px;max-width:calc(100vw - 24px);font-size:var(--fs-small)}#hud{top:12px;left:12px;padding:12px;max-width:240px}#wrap.panel-open #comms,#wrap.panel-open #ticker{display:none}}
@media(max-width:700px){#quick{top:auto;bottom:112px;left:164px;right:12px;transform:none;padding:4px;overflow-x:auto;max-width:none}#quick button{min-width:64px;min-height:40px}#theme{top:12px;right:12px}#comms{top:auto;bottom:264px;max-height:100px;width:210px}#comms .ct{font-size:var(--fs-small)}#comms .row{min-height:40px}#comms .tl{font-size:var(--fs-small)}#hud .sc{font-size:var(--fs-hud);margin-top:8px}#sector{font-size:var(--fs-small)}#touchpad{bottom:108px}#panel{padding:16px}#panel .live{font-size:var(--fs-body)}#panel .say textarea{font-size:var(--fs-title)}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
/* ---- the board: two queues; file rows; preview ---- */
#quests,#meta{width:min(1180px,94vw);max-height:86vh}
#quests .cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}#quests .q .tx{min-width:0}#quests .q .tx pre.cmd{white-space:pre-wrap;word-break:break-all}
#quests .col h3{margin:0 0 2px;font-size:var(--fs-small);letter-spacing:.16em;color:#ffd166}
#quests .col .sub{font-size:var(--fs-small);color:#9bacc0;margin-bottom:8px}
#quests .q.task .tx small{color:#9bacc0}
#quests .q.task .tx pre.cmd{font-size:var(--fs-small);margin-top:4px}
#quests .q .sr{flex:0 0 160px;font-size:var(--fs-small);color:#9bacc0}
#quests .q .adv{font-size:var(--fs-small);color:#9fb3c8;margin-top:4px;border-left:2px solid #344c6b;padding-left:8px}#quests .q .adv b{color:#7ec8ff}#quests .q .adv b.ret{color:#ffbd55}#quests .q .adv b.open{color:#7ee787}#quests .q .adv small{display:block;color:#9bacc0}
#quests .q .run{font-size:var(--fs-small);color:#9bacc0;margin-top:4px}#quests .q .run .rs{color:#ffd166}#quests .q .run .rs.bad{color:#ff7b72}
#quests .q .run button.rout{background:none;border:none;color:#7ec8ff;font:var(--fs-small) var(--font-mono);cursor:pointer;padding:0;text-decoration:underline}
#quests .q.retired{opacity:.7}#quests .q .sr .bmsg{display:block;margin-top:4px;color:#ffd166}#quests .q .sr .bmsg.err{color:#ff7b72}
#quests .col .sub button.showret{background:none;border:none;color:#7ec8ff;font:var(--fs-small) var(--font-mono);cursor:pointer;padding:0;text-decoration:underline}
#quests .q .sr button,#quests .q .sr select{background:#141922;border:1px solid #344c6b;color:#aeb9c6;border-radius:0;font:var(--fs-small) var(--font-mono);padding:4px 8px;cursor:pointer;max-width:148px}
#quests .age.vATTIC,#quests .age.vMOVE,#quests .age.vMERGE,#quests .age.vSPLIT{color:#ffbd55}
/* ---- board skin: rail + chips + one recommended button ---- */
#quests .q{display:flex;gap:12px;align-items:stretch;padding:8px 8px 8px 0;margin:0 0 8px;background:#0d172bcc;border:1px solid #344c6b}
#quests .q .rail{flex:0 0 4px;background:#3a4a63}
#quests .q.st-open .rail,#quests .q.st-fix .rail,#quests .q.st-hand .rail{background:#7ee787}#quests .q.st-ret .rail{background:#ffbd55}#quests .q.st-none .rail{background:#3a4a63}#quests .q.st-gone .rail{background:#536c8d}
#quests .q.st-ret{opacity:.78}#quests .q.st-gone{opacity:.6}
#quests .q .body{flex:1;min-width:0}
#quests .q .head{display:flex;flex-wrap:wrap;gap:4px;align-items:center;margin-bottom:4px}
#quests .q .head .src{margin-left:auto;font-size:var(--fs-small);color:#9bacc0}
#quests .chip{display:inline-block;font:var(--fs-eyebrow) var(--font-mono);letter-spacing:.08em;padding:1px 8px;border:1px solid #344c6b;color:#9fb3c8;text-transform:uppercase;white-space:nowrap}
#quests .chip.st.open{color:#7ee787;border-color:#2e6b3e}#quests .chip.st.fix,#quests .chip.st.hand{color:#7ec8ff;border-color:#344c6b}#quests .chip.st.ret{color:#ffbd55;border-color:#7a5a20}#quests .chip.st.none{color:#9bacc0}#quests .chip.st.gone{color:#9bacc0}
#quests .chip.age{color:#c9d3df}#quests .chip.age.old{color:#ff7b72;border-color:#7a2f2a}#quests .chip.age.mid{color:#ffbd55}#quests .chip.age.new{color:#7ee787}
#quests .chip.v{color:#ffd166;border-color:#7a5a20}#quests .chip.area{color:#7ec8ff}#quests .chip.path.ok{color:#7ee787}#quests .chip.path.gone{color:#ff7b72;border-color:#7a2f2a}#quests .chip.tot{color:#e6edf3;border-color:#344c6b}
#quests .q .tx{font-size:var(--fs-small);line-height:1.35;color:#dbe4ee}#quests .q .tx b{color:#fff}
#quests .q .ev{font-size:var(--fs-small);color:#9bacc0;margin-top:2px}
#quests .q .adv{margin-top:4px;font-size:var(--fs-small);color:#c9d3df;border-left:0;padding-left:0}
#quests .q .adv .arrow{color:#ffd166;font-weight:600}
#quests .q details{margin-top:4px;font-size:var(--fs-small);color:#9bacc0}#quests .q details summary{cursor:pointer;color:#7ec8ff;list-style:none;display:inline-block}#quests .q details summary::before{content:'▸ '}#quests .q details[open] summary::before{content:'▾ '}
#quests .q details .ev{white-space:pre-wrap;color:#9bacc0;margin-top:2px}#quests .q details.cmdd pre.cmd{margin-top:4px}
#quests .q .sr{flex:0 0 150px;display:flex;flex-direction:column;gap:4px;align-items:stretch;padding-right:2px}
#quests .q .sr button,#quests .q .sr select{width:100%;max-width:none;min-height:24px}
#quests .q .sr button.run1{border-color:#3fb950;color:#7ee787;font-weight:600}#quests .q .sr button.run1:hover{background:#163020}
#quests .q .sr button.sug{border-color:#ffd166;color:#ffd166;box-shadow:0 0 0 1px #ffd16640,2px 2px 0 #000}
#quests .q .sr button.sug::after{content:' ◂ audit'}
#panel .verbs{margin-top:12px;border-top:1px dashed #344c6b;padding-top:8px}#panel .verbs h3{margin:0 0 8px;font-size:var(--fs-eyebrow);letter-spacing:.14em;color:#ffd166}#panel .verbs h3 small{display:block;letter-spacing:0;color:#9bacc0;font-size:var(--fs-small);margin-top:2px}
#panel .verb{padding:8px 0;border-bottom:1px solid #344c6b}#panel .verb .vh{display:flex;gap:8px;align-items:center}#panel .verb .vk{font-size:var(--fs-eyebrow);letter-spacing:.1em;color:#7ec8ff;text-transform:uppercase}#panel .verb .vu{font-size:var(--fs-small);color:#9bacc0;margin-left:auto}
#panel .verb button.vrun{background:#141922;border:1px solid #344c6b;color:#e6edf3;font:var(--fs-small) var(--font-mono);padding:4px 8px;cursor:pointer}#panel .verb .vd{font-size:var(--fs-small);color:#9bacc0;margin-top:4px}
#panel .verb select.vrunner{margin-top:4px;background:#141922;border:1px solid #344c6b;color:#aeb9c6;font:var(--fs-small) var(--font-mono);padding:4px 8px}
#panel .verb textarea.vtext{width:100%;min-height:44px;margin-top:4px;background:#0b1020;color:#e6edf3;border:1px solid #344c6b;font:var(--fs-small) var(--font-mono);padding:4px}#panel .verb .vmsg{font-size:var(--fs-small);color:#ffd166;margin-top:4px}#panel .verbs .verr{font-size:var(--fs-small);color:#ff7b72;margin-top:8px}
#quests .how{font-size:var(--fs-small);line-height:1.5;color:#c9d3df;background:#0d172b;border:1px solid #344c6b;padding:8px 12px;margin:0 0 12px}#quests .how b{color:#ffd166}
#quests .strip{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 8px}
#quests .sweep{display:flex;gap:8px;align-items:center;margin:0 0 8px;font-size:var(--fs-small);color:#9fb3c8}#quests .sweep select,#quests .sweep button{background:#141922;border:1px solid #344c6b;color:#aeb9c6;font:var(--fs-small) var(--font-mono);padding:4px 8px;cursor:pointer}#quests .sweep button.stop{border-color:#ff7b72;color:#ff7b72}
#quests .div{font:var(--fs-eyebrow) var(--font-mono);letter-spacing:.16em;color:#9fb4cc;text-transform:uppercase;margin:12px 0 8px;padding-bottom:4px;border-bottom:1px dashed #344c6b}
#quests .q .run{font-size:var(--fs-small);color:#9bacc0;margin-top:4px}
#quests .col .sub{margin-bottom:8px}

#quests .age.vASK{color:#c99bff}#quests .age.vKEEP{color:#55e69c}
#quests .q.task.agent{border-left:3px solid #55e69c;padding-left:8px}
#quests .q.task.you{border-left:3px solid #c99bff;padding-left:8px}
@media (max-width:900px){#quests .cols{grid-template-columns:1fr}}
/* the meta board (U): shown on the desktop page only; a phone or a touch layout never opens it */
#meta{z-index:32;background:#0b1428;display:none}#meta.on{display:block}html.m #meta{display:none!important}
#meta .mbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 10px}
#meta .mbar form{display:flex;flex:1 1 280px;gap:6px;min-width:0}
#meta .mbar input,#meta .mr textarea,#meta .mr input{background:#141922;border:1px solid #344c6b;color:#dbe4ee;border-radius:0;font:var(--fs-small) var(--font-mono);padding:5px 8px;min-width:0}
#meta .mbar input{flex:1}
#meta button{background:#141922;border:1px solid #344c6b;color:#aeb9c6;border-radius:0;font:var(--fs-small) var(--font-mono);padding:4px 10px;cursor:pointer}
#meta button:hover:not(:disabled){border-color:#7ec8ff;color:#e6edf3}#meta button:disabled{opacity:.5;cursor:default}
#meta button.go{border-color:#3fb950;color:#7ee787;font-weight:600}
#meta .mstat{font-size:var(--fs-small);color:#9bacc0}#meta .mstat.err{color:#ff7b72}
#meta .mchat{font-size:var(--fs-small);line-height:1.45;margin:0 0 8px}#meta .mchat .mq{color:#9fb3c8}#meta .mchat .mq::before{content:'you · '}
#meta .mchat .ma{color:#dbe4ee;white-space:pre-wrap;margin:0 0 6px;overflow-wrap:anywhere}
#meta .mg{margin:12px 0 6px;font:var(--fs-eyebrow) var(--font-mono);letter-spacing:.16em;color:#9fb4cc;text-transform:uppercase;border-bottom:1px dashed #344c6b;padding-bottom:4px}
#meta .mg b{color:#ffd166}#meta details.mg{border-bottom:0;padding:0}#meta details.mg>summary{cursor:pointer;border-bottom:1px dashed #344c6b;padding-bottom:4px}
#meta .mr{display:flex;gap:12px;align-items:flex-start;padding:6px 8px;margin:0 0 6px;background:#0d172bcc;border:1px solid #344c6b;border-left:3px solid #3a4a63}
#meta .mr[data-v=merge],#meta .mr[data-v=retire]{border-left-color:#3fb950}#meta .mr[data-v=follow-up],#meta .mr[data-v=check],#meta .mr[data-v=rename]{border-left-color:#7ec8ff}
#meta .mr[data-v=stop],#meta .mr[data-v=set-aside],#meta .mr[data-v=close]{border-left-color:#ff7b72}#meta .mr[data-v=go],#meta .mr[data-v=land]{border-left-color:#ffbd55}
#meta .mr .mb{flex:1;min-width:0}#meta .mr .mt{font-size:var(--fs-small);color:#e6edf3;overflow-wrap:anywhere}
#meta .mr .mt small{color:#9bacc0;margin-left:6px}#meta .mr .mw{font-size:var(--fs-small);color:#c9d3df;margin-top:2px;overflow-wrap:anywhere}
#meta .mr .ms{font-size:var(--fs-small);color:#9bacc0;margin-top:2px;overflow-wrap:anywhere}
#meta .mr .mf{display:flex;gap:6px;margin-top:6px;align-items:flex-start}#meta .mr .mf textarea{flex:1;min-height:64px;resize:vertical}#meta .mr .mf input{flex:1}
#meta .mr .ma2{flex:0 0 auto;display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;max-width:42%}
#meta .mr a.mlink{color:#7ec8ff;font-size:var(--fs-small)}#meta .mr .mmsg{font-size:var(--fs-small);color:#7ee787;margin-top:4px}#meta .mr .mmsg.err{color:#ff7b72}
#meta .mr pre.mcmd{margin:6px 0 0;padding:4px 8px;background:#06090f;border:1px solid #344c6b;color:#dbe4ee;font:var(--fs-small) var(--font-mono);white-space:pre-wrap;overflow-wrap:anywhere;user-select:all}
#meta .mmiss{margin:8px 0 0;font-size:var(--fs-small);color:#c9d3df}#meta .mmiss ul{margin:4px 0 0;padding-left:20px}#meta .mmiss li.mnote{color:#9bacc0}
/* pages agents made (the show op "page"): a card for each live page, a toast when one arrives, and the sandboxed panel. They sit above the 3D view's HUD (z 51-54). A phone has the cards and no panel. */
#pgdock{position:fixed;z-index:56;right:12px;top:110px;width:min(300px,calc(100vw - 24px));display:none;flex-direction:column;gap:6px}#pgdock.on{display:flex}
#pgdock .pgc{background:#0b1428f7;border:2px solid #344c6b;border-left:4px solid #7ec8ff;padding:6px 8px;box-shadow:3px 3px 0 #030817;font-size:var(--fs-small);color:#c9d3df;cursor:pointer}
#pgdock .pgc.ro{cursor:default}#pgdock .pgc:hover:not(.ro){border-color:#7ec8ff}
#pgdock .pgc b,#pgpanel #pghead b{color:#e6edf3;overflow-wrap:anywhere}#pgdock .pgc small{display:block;margin-top:2px;color:#9bacc0;overflow-wrap:anywhere}
.pgm{display:inline-block;margin-right:6px;padding:0 5px;border:1px solid #344c6b;color:#7ec8ff;font:var(--fs-eyebrow) var(--font-mono);letter-spacing:.14em;text-transform:uppercase}
#pgtoast{position:fixed;z-index:58;left:50%;top:14px;transform:translateX(-50%);max-width:min(560px,calc(100vw - 24px));display:none;align-items:center;gap:10px;padding:8px 12px;background:#0b1428;border:2px solid #7ec8ff;box-shadow:4px 4px 0 #030817;color:#e6edf3;font-size:var(--fs-small)}
#pgtoast.on{display:flex}#pgtoast span{overflow-wrap:anywhere}#pgtoast small{color:#9bacc0}
#pgtoast button,#pgpanel button{background:#141922;border:1px solid #344c6b;color:#aeb9c6;border-radius:0;font:var(--fs-small) var(--font-mono);padding:4px 10px;cursor:pointer}
#pgtoast button:hover,#pgpanel button:hover{border-color:#7ec8ff;color:#e6edf3}
#pgpanel{position:fixed;z-index:59;left:3vw;right:3vw;top:4vh;bottom:4vh;display:none;flex-direction:column;background:#0b1428;border:2px solid #344c6b;box-shadow:6px 6px 0 #030817}#pgpanel.on{display:flex}
#pgpanel .pgh{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid #344c6b}
#pgpanel #pghead{flex:1;min-width:0;font-size:var(--fs-small);color:#9bacc0;overflow-wrap:anywhere}
#pgtabs{display:flex;flex-wrap:wrap;gap:6px;padding:6px 12px;border-bottom:1px solid #344c6b}#pgtabs:empty{display:none}
#pgtabs button[aria-current=true]{border-color:#7ec8ff;color:#e6edf3}
#pgframe{flex:1;min-height:0;position:relative;background:#ffffff}#pgframe iframe{position:absolute;left:0;top:0;width:100%;height:100%;border:0;background:#ffffff}
#pgframe .pgmsg{padding:24px;color:#0b1428;font-size:var(--fs-body)}
html.m #pgpanel{display:none!important}html.m #pgtoast{left:8px;right:8px;top:8px;transform:none;max-width:none}
html:not(.m) #pgdock{right:calc(var(--railw,0px) + 12px);top:14px}   /* the map's top right, clear of the rail */
html.m #pgdock{top:auto;bottom:calc(var(--B,78px) + 10px);left:8px;right:96px;width:auto;flex-direction:row;gap:6px;overflow-x:auto}html.m #pgdock .pgc{flex:0 0 min(230px,72vw);padding:5px 7px}   /* a row above the tab bar that leaves the + and the zoom keys free */
body.gb #pgdock .pgc,body.gb #pgtoast,body.gb #pgpanel{background:#9bbc0f;border-color:#0f380f;color:#0f380f}
.tbl tr.frow{cursor:pointer}.tbl tr.frow:hover td{background:#141922}.tbl td.fname{color:#d7dee7}
#preview{margin-top:12px}#preview .terminalbar{margin-top:8px}#preview .terminalbar span:first-child{color:#aeb9c6;word-break:break-all}
#prompter{position:absolute;z-index:7;left:50%;top:50%;transform:translate(-50%,-50%);width:min(640px,92vw);background:#0b1428f7;border:2px solid #344c6b;box-shadow:6px 6px 0 #030817;padding:20px 20px;display:none}
#prompter.on{display:block}#prompter h2{margin:0 0 4px;font-size:var(--fs-body);letter-spacing:.16em;color:#ffd166}
#prompter .sub{font-size:var(--fs-small);color:#9bacc0;margin-bottom:8px;word-break:break-all}
#prompter textarea{width:100%;min-height:96px;background:#07090d;border:1px solid #344c6b;color:#d7dee7;padding:8px 12px;font:var(--fs-body)/1.4 var(--font-mono);resize:vertical}
#prompter .prow{display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap}
#prompter select,#prompter button{background:#141922;border:1px solid #344c6b;color:#aeb9c6;border-radius:0;font:var(--fs-small) var(--font-mono);padding:8px 12px;cursor:pointer;min-height:32px}
#legend{position:absolute;z-index:6;left:50%;top:50%;transform:translate(-50%,-50%);width:min(620px,92vw);background:#0b1428f7;border:2px solid #344c6b;box-shadow:6px 6px 0 #030817;padding:16px 20px;display:none;font-size:var(--fs-small);color:#d7dee7}
#legend.on{display:block}#legend h2{margin:0 0 8px;font-size:var(--fs-small);letter-spacing:.16em;color:#ffd166}#legend .lg{padding:4px 0;border-top:1px solid #344c6b}#legend .lg b{color:#4cc6ff}
/* Flight display: readable at desk distance, with amber reserved for decisions. */
*{scrollbar-width:thin;scrollbar-color:#526b8a #0b1428}
#hud{width:320px;max-width:320px;padding:16px 20px;border-color:#344c6b;background:#0b1428f5}
#hud h1{font-size:var(--fs-list);letter-spacing:.2em}#hud .sc{font-size:var(--fs-big);margin-top:12px}
#hud .sc small{font-size:var(--fs-small);letter-spacing:.03em;line-height:1.5}
#hud>div[style]{font-size:var(--fs-small)!important;line-height:1.55;letter-spacing:0!important}
#hud>div[style]:not(#roundstag){color:#a9bdd6!important}
#sector{font-size:var(--fs-small);letter-spacing:.02em;line-height:1.6;max-width:none;color:#b5c8df}
#quick{gap:8px;padding:8px;border-color:#344c6b}#quick button{font-size:var(--fs-body);min-height:40px;padding:8px 16px;white-space:nowrap}
#comms{width:380px;padding:12px;border-color:#344c6b;background:#0b1428f5}
#comms .ct{font-size:var(--fs-small);letter-spacing:.03em;color:#afc3dc}
#comms .row{min-height:48px;padding:8px 12px;gap:12px}
#comms .nm{flex-basis:78px;font-size:var(--fs-body)}#comms .nm small{font-size:var(--fs-small);letter-spacing:.02em}
#comms .tl{font-size:var(--fs-small);color:#ccdaeb}#comms .pt{width:24px;height:24px}
#ticker{font-size:var(--fs-small);min-height:36px;color:#c5d7ed}#ticker .tk{font-size:var(--fs-small)}
#help{font-size:var(--fs-small);line-height:1.65;color:#b0c4df}#taskbar{font-size:var(--fs-small);gap:16px}
#panel{width:clamp(600px,54vw,960px);background:#0b1428}#panel dl{font-size:var(--fs-body);line-height:1.6}
#panel .live{font-size:var(--fs-list);line-height:1.55;padding:16px}#panel .say textarea{font-size:var(--fs-list);min-height:104px}
#panel h3,.terminalbar,.saymeta,#panel .kind{font-size:var(--fs-small);color:#a6bdd9}
#panel .saym,.viewonly,.cmd,.tbl{font-size:var(--fs-body)}#panel .say button,.terminalbar button{font-size:var(--fs-small);min-height:40px}
#permits{top:84px;width:min(440px,calc(100vw - 32px));max-width:none;max-height:36vh;overflow:auto}
#permits .pcard{font-size:var(--fs-body);border-color:#ffcf69;background:#ffcf69;padding:12px}
#permits .pq{font-size:var(--fs-small);line-height:1.5}#permits button{font-size:var(--fs-small);min-height:36px}
#quests .sub,#quests .col .sub,#quests .q .sr,#quests .q .run{font-size:var(--fs-small);line-height:1.6}
#quests .q .tx{font-size:var(--fs-list);line-height:1.65}#quests .q .age{font-size:var(--fs-small)}
#prompter textarea{font-size:var(--fs-list);line-height:1.6}#prompter .sub,#legend{font-size:var(--fs-body);line-height:1.7}
#wrap.panel-open #comms,#wrap.panel-open #ticker{display:none}
@media(max-width:1100px){#quick button{padding:8px 8px}#hud{width:280px;max-width:280px}#comms{width:330px}}
@media(max-width:700px){
 #hud{top:12px;left:12px;width:236px;max-width:calc(100vw - 84px);padding:12px 12px}
 #hud h1{font-size:var(--fs-body)}#hud .sc{font-size:var(--fs-hud);margin-top:8px}#hud .sc small{font-size:var(--fs-small)}
 #hud>div[style]{font-size:var(--fs-small)!important;line-height:1.5}#sector{font-size:var(--fs-small);padding-top:8px;margin-top:8px}
 #quick{left:164px;right:12px;bottom:112px;padding:4px;gap:4px}#quick button{font-size:var(--fs-small);padding:4px 8px;min-height:44px}
 #comms{width:210px;max-height:104px;bottom:264px;padding:8px}#comms .ct{font-size:var(--fs-small)}
 #comms .row{min-height:44px;gap:8px;padding:8px}#comms .nm{font-size:var(--fs-small);flex-basis:67px}#comms .tl{font-size:var(--fs-small)}
 #ticker{font-size:var(--fs-small);bottom:72px;min-height:34px}#help{font-size:var(--fs-small);line-height:1.45;bottom:36px}
 #panel{width:100vw;padding:16px}#panel .live{font-size:var(--fs-list);min-height:0;padding:12px}#panel .say textarea{font-size:var(--fs-title);min-height:84px}
 #permits{top:12px;max-height:45vh}#taskbar{padding:0 12px}
}
.file-planet{width:28px;height:28px;vertical-align:middle;image-rendering:pixelated;margin-right:8px}
.file-theme{display:inline-block;font-size:var(--fs-small);color:#9db9d8;margin-right:12px;min-width:64px}
.tbl td.fname{min-width:220px}.tbl tr.frow:hover .file-theme{color:#e3efff}
#legend{width:min(720px,94vw);max-height:88dvh;overflow:auto}#legend .sub{font-size:var(--fs-small);color:#adbed5;margin-bottom:12px}
.planet-key{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.planet-key-row{display:grid;grid-template-columns:40px 1fr;align-items:center;padding:8px;background:#101e34;border:1px solid #344c6b}
.planet-key-row .file-planet{grid-row:span 2;width:36px;height:36px;margin:0}.planet-key-row .file-theme{display:none}
.planet-key-row b{font-size:var(--fs-body);color:#e5eef9}.planet-key-row small{font-size:var(--fs-small);color:#aabfd9;line-height:1.4}
#legend details{margin-top:12px;font-size:var(--fs-small)}#legend summary{cursor:pointer;color:#a8caff}
#comms .pt{clip-path:none;background:transparent;box-shadow:none;width:28px;height:28px}
#comms .pt .file-planet{margin:0}#comms .pt .file-theme{display:none}
@media(max-width:480px){.planet-key{gap:4px}.planet-key-row{padding:8px;grid-template-columns:34px 1fr}.planet-key-row .file-planet{width:28px;height:28px}.planet-key-row small{font-size:var(--fs-small)}.file-theme{min-width:0}}
/* ---- TODAY: the first screen (today.py). Plain text, readable from across the desk. M = the map. ---- */
#today{position:fixed;inset:0;z-index:30;display:none;overflow:auto;background:#070b14;color:#d7e0ea;
font:var(--fs-list)/1.55 var(--font-mono)}
#today.on{display:block}
#today .tw{max-width:980px;margin:0 auto;padding:24px 28px 60px}
#today .th{display:flex;align-items:baseline;gap:16px;border-bottom:2px solid #344c6b;padding-bottom:12px}
#today .th h1{margin:0;font-size:var(--fs-hud);letter-spacing:.2em;color:#ffd166}
#today .th .gen{color:#9bacc0;font-size:var(--fs-small)}
#today .th button{margin-left:auto}
#today button{background:#141922;border:1px solid #344c6b;color:#cfe0f5;border-radius:0;cursor:pointer;
font:var(--fs-body) var(--font-mono);padding:8px 12px;box-shadow:2px 2px 0 #000}
#today button:hover{background:#1f2c40}
#today section{margin-top:24px}
#today h2{margin:0 0 8px;font-size:var(--fs-small);letter-spacing:.18em;color:#9bacc0;text-transform:uppercase;font-weight:400}
#today h2 b{color:#ffd166;font-weight:400}
#today .it{padding:8px 12px;margin:8px 0;background:#0d1320;border-left:3px solid #344c6b}
#today .it.need{border-left-color:#e3b341}#today .it.work{border-left-color:#3fb950}#today .it.bad{border-left-color:#f85149}
#today .it small{display:block;color:#8b98a8;font-size:var(--fs-body);margin-top:2px}
#today .mut{color:#9bacc0;font-size:var(--fs-body);margin-top:8px}
#today .lane{display:grid;grid-template-columns:130px 1fr;gap:4px 16px;padding:8px 12px;margin:8px 0;background:#0d1320}
#today .lane b{color:#ffd166;font-weight:400;text-transform:uppercase;font-size:var(--fs-small);letter-spacing:.12em;padding-top:2px}
#today .fold{display:grid;grid-template-columns:minmax(150px,auto) 84px 1fr;gap:4px 12px;font-size:var(--fs-list)}
#today a.go{color:#a8caff;cursor:pointer;text-decoration:none}#today a.go:hover{text-decoration:underline}
#today .wl{display:inline-block;min-width:78px;color:#ffd166;font-size:var(--fs-eyebrow);letter-spacing:.12em;text-transform:uppercase}
#today .hist{float:right;margin-top:4px}#today .hist i{display:inline-block;width:11px;height:11px;margin-left:4px;border-radius:0;background:#26303f;border:1px solid #344c6b}
#today .hist i.y{background:#3fb950;border-color:#3fb950}#today .hist i.n{background:#f85149;border-color:#f85149}
#today .it .act{margin-top:8px}#today .it .act span{color:#8b98a8;font-size:var(--fs-body);margin-left:8px}
#today .ftag{font-size:var(--fs-eyebrow);letter-spacing:.1em;color:#8b98a8}#today .ftag.LIVE{color:#3fb950}#today .ftag.SHELF{color:#e3b341}
#today code{background:#141922;padding:0 4px;border-radius:0;color:#e6edf3}
#today ol{margin:8px 0 2px;padding-left:24px}
#topbar a.today{color:#ffd166;cursor:pointer;border:1px solid #6b5a2b;padding:1px 8px;border-radius:0;text-decoration:none}
/* ---- TASKS: composer (N), list (K), one task in the side panel ---- */
#composer{flex:1 1 auto;min-height:0;display:none;flex-direction:column;background:#0a1020;padding:16px 16px 12px;color:#d7e0ea;overflow:auto}
#composer.on{display:flex}#composer h2{margin:0 0 2px;font-size:var(--fs-eyebrow);letter-spacing:.16em;color:#9bacc0}#composer h2 span{color:#3dff9e;letter-spacing:.04em;font-weight:400;margin-left:8px}
#composer .sub{font-size:var(--fs-small);color:#6b86a6;margin-bottom:12px;line-height:1.5}
#composer textarea{width:100%;flex:0 0 auto;min-height:150px;max-height:46vh;resize:vertical;background:#0f1a2e;border:1px solid #344c6b;border-radius:0;color:#e6edf3;padding:12px 12px;font:var(--fs-list)/1.5 var(--font-mono)}
#composer textarea:focus,#composer input:focus,#composer select:focus{outline:2px solid #3dff9e;outline-offset:1px}
#composer .crow{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:12px;font-size:var(--fs-small);color:#a6bdd9}
#composer .crow label{display:flex;gap:8px;align-items:center;background:#0f1a2e;border:1px solid #344c6b;border-radius:0;padding:4px 12px 4px 12px;color:#9bacc0;font-size:var(--fs-small);letter-spacing:.06em}
#composer .crow label span{display:none}
#composer input[type=text],#composer select{background:transparent;border:0;color:#e6edf3;padding:2px 0;font:var(--fs-body) var(--font-mono);max-width:230px}
#composer label.folder{flex:1 1 100%}#composer label.folder input{width:100%;max-width:none}
#composer .chk{background:#0f1a2e;border:1px solid #344c6b;border-radius:0;padding:4px 12px;color:#a6bdd9;font-size:var(--fs-small)}
#composer .cbtns{display:flex;gap:12px;margin-top:16px;align-items:center;flex-wrap:wrap}
#composer button{background:#141f33;border:1px solid #344c6b;color:#cfe0f5;cursor:pointer;font:var(--fs-body) var(--font-mono);padding:8px 16px;border-radius:0}
#composer button.go{background:#3dff9e;color:#04210f;border-color:#3dff9e;font-weight:600}#composer button:disabled{opacity:.5;cursor:not-allowed}
#tasks{flex:0 1 auto;max-height:38%;overflow:auto;padding:4px 8px 8px;font-size:var(--fs-small)}
#tasks .thead{display:none}
#tasks button{background:#141922;border:1px solid #344c6b;color:#cfe0f5;cursor:pointer;font:var(--fs-small) var(--font-mono);padding:4px 12px}
#tasks h3{margin:8px 4px 2px;font-size:var(--fs-eyebrow);letter-spacing:.16em;color:#9bacc0;font-weight:400}
#tasks .trow{display:flex;gap:8px;align-items:flex-start;padding:8px 8px;margin:1px 0;border-left:2px solid transparent;border-radius:0;cursor:pointer}
#tasks .trow:hover{background:#111c30}#tasks .trow.sel{background:#13213a;border-left-color:#3dff9e}#tasks .trow i{flex:0 0 8px;height:8px;margin-top:8px;border-radius:0}
#tasks .trow b{display:block;color:#e6edf3;font-weight:400;font-size:var(--fs-body);line-height:1.35;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}#tasks .trow small{display:block;color:#7f93ab;margin-top:2px;font-size:var(--fs-small)}
#tasks .mut{color:#9bacc0;margin:4px 8px;font-size:var(--fs-small)}
/* ---- a thread, T3-style: header, tabs, transcript, actions + reply docked at the bottom ---- */
.tv{display:flex;flex-direction:column;height:100%;min-height:0}
.tvh{flex:0 0 auto;padding:12px 16px 0;border-bottom:1px solid #344c6b}
.tvh .pill{display:inline-block;font-size:var(--fs-eyebrow);letter-spacing:.12em;text-transform:uppercase;padding:2px 8px;border-radius:0;border:1px solid currentColor;margin-bottom:8px}
.tvh .tvt{font-size:var(--fs-list);line-height:1.35;color:#f2f6fb;font-weight:600;margin:0 0 8px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
.tvh .tvm{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.chip2{display:inline-flex;align-items:center;gap:4px;font-size:var(--fs-small);color:#a6bdd9;background:#0f1a2e;border:1px solid #344c6b;border-radius:0;padding:2px 8px;max-width:100%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
a.chip2{cursor:pointer}a.chip2:hover{border-color:#5fb4ff;color:#e6edf3}
.chip2.ok{color:#3dff9e;border-color:#1f7a4a}.chip2.bad{color:#ff8a8a;border-color:#7a2a33}.chip2.warn{color:#ffd34d;border-color:#7a6220}
.tvtabs{display:flex;gap:2px;align-items:flex-end}
.tvtabs button{background:transparent;border:0;border-bottom:2px solid transparent;color:#7f93ab;font:var(--fs-small) var(--font-mono);padding:8px 12px;cursor:pointer}
.tvtabs button.on{color:#e6edf3;border-bottom-color:#3dff9e}.tvtabs .grow{flex:1}
.tvpane{flex:1 1 auto;min-height:0;overflow:auto;padding:12px 16px}
.tvpane[hidden]{display:none}
#truns{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
#truns button{background:#0f1a2e;border:1px solid #344c6b;border-radius:0;color:#9bacc0;font:var(--fs-small) var(--font-mono);padding:4px 12px;cursor:pointer}
#truns button.on{color:#e6edf3;border-color:#5fb4ff}#truns button b{font-weight:400}
.tv-chat{font-size:var(--fs-body);line-height:1.5}
.tv-msg{margin:8px 0;padding:8px 12px;border-radius:0;max-width:100%;white-space:pre-wrap;word-break:break-word}
.tv-msg.you{background:#13213a;border:1px solid #344c6b;margin-left:28px;color:#e6edf3}
.tv-msg.agent{background:#0f1a2e;color:#dbe6f2;margin-right:28px}
.tv-msg.review{background:#171a2e;border:1px solid #344c6b;margin-right:28px;color:#dbe6f2}
.tv-msg.err{background:#1c1322;border:1px solid #344c6b;color:#ff9a92;margin-right:28px}
.tv-msg .who{font-size:var(--fs-eyebrow);letter-spacing:.08em;text-transform:uppercase;color:#6b86a6;margin-bottom:4px}
.tv-msg p{margin:0}.tv-msg p+p{margin-top:8px}
.tv-acts{margin:8px 0 8px 0;border:1px solid #344c6b;border-radius:0;background:#0c1527}
.tv-acts summary{cursor:pointer;list-style:none;padding:8px 12px;font-size:var(--fs-small);color:#9bacc0;display:flex;gap:8px;align-items:center}
.tv-acts summary::-webkit-details-marker{display:none}.tv-acts summary:before{content:'▸';color:#9bacc0}.tv-acts[open] summary:before{content:'▾'}
.tv-acts .tv-al{padding:2px 12px 8px;border-top:1px solid #344c6b;font-size:var(--fs-small);max-height:40vh;overflow:auto}
.tv-acts .tv-al div{white-space:pre-wrap;word-break:break-word;margin:2px 0;padding-left:1.2em;text-indent:-1.2em}
.tv-div{margin:12px 0 8px;font-size:var(--fs-small);color:#9bacc0;letter-spacing:.06em;display:flex;align-items:center;gap:8px}
.tv-div:before,.tv-div:after{content:'';flex:1;border-top:1px solid #344c6b}
.tv-div.ok{color:#3dff9e}.tv-div.bad{color:#ff8a8a}
.tl-more button{background:transparent;border:1px solid #344c6b;color:#a8caff;cursor:pointer;font:inherit;padding:4px 12px}.tl-more button:disabled{opacity:.5;cursor:wait}
.tv-live{display:inline-block;width:7px;height:7px;border-radius:0;background:#3dff9e;animation:tvp 1.1s ease-in-out infinite;margin-right:8px}
@keyframes tvp{0%,100%{opacity:.25}50%{opacity:1}}
.tl-text{color:#e6edf3}.tl-tool{color:#7ec8ff}.tl-cmd{color:#ffd166}.tl-file{color:#7ee787}.tl-err{color:#ff7b72}
.tl-result{color:#3dff9e;font-weight:600}.tl-info,.tl-meta,.tl-think{color:#9bacc0}.tl-raw{color:#aeb9c6}
.tvfoot{flex:0 0 auto;border-top:1px solid #344c6b;padding:8px 12px 12px;background:#0a1020}
.tvfoot .tbtns{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.tvfoot .tbtns button{background:#141f33;border:1px solid #344c6b;color:#cfe0f5;cursor:pointer;font:var(--fs-small) var(--font-mono);padding:8px 12px;border-radius:0;min-height:0}
.tvfoot .tbtns button.ok{background:#3dff9e;color:#04210f;border-color:#3dff9e;font-weight:600}.tvfoot .tbtns button.warn{border-color:#ff7b72;color:#ff9a92}
.tvfoot .tbtns select{background:#0f1a2e;border:1px solid #344c6b;color:#cfe0f5;font:var(--fs-small) var(--font-mono);padding:4px 8px;border-radius:0;max-width:190px}
.tvfoot .tclosed{color:#9bacc0;font-size:var(--fs-small);flex:1 1 100%}
.tvfoot .tfollow form{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:8px;align-items:end}
.tvfoot .tfollow textarea{width:100%;min-height:54px;max-height:30vh;resize:vertical;background:#0f1a2e;border:1px solid #344c6b;border-radius:0;color:#e6edf3;padding:8px 12px;font:var(--fs-body)/1.45 var(--font-mono)}
.tvfoot .tfollow textarea:focus{outline:2px solid #3dff9e;outline-offset:1px}
.tvfoot .tfollow button{background:#3dff9e;color:#04210f;border:1px solid #3dff9e;cursor:pointer;font:600 var(--fs-small) var(--font-mono);padding:8px 12px;border-radius:0}
.tvfoot .saym{margin-top:8px}
#tdiff .dfile{display:flex;gap:8px;align-items:center;padding:4px 8px;border-bottom:1px solid #344c6b;font-size:var(--fs-body)}
#tdiff .dfile .ds{width:16px;color:#ffd166}#tdiff .dfile .dp{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e6edf3}
#tdiff .dfile .dn{color:#7ee787;font-size:var(--fs-small)}#tdiff .dfile .dm{color:#ff7b72;font-size:var(--fs-small)}
#tdiff .dbar{display:inline-flex;width:60px;height:6px;background:#1c2a42;border-radius:0;overflow:hidden}#tdiff .dbar i{display:block;height:100%}
#tdiff h3{font-size:var(--fs-eyebrow);letter-spacing:.14em;color:#9bacc0;margin:4px 0 8px}
#panel pre.patch,#side pre.patch{margin-top:8px;max-height:60vh;overflow:auto;padding:12px;background:#05080f;border:1px solid #344c6b;font-size:var(--fs-small);line-height:1.45;white-space:pre;color:#aeb9c6}
pre.patch .pp{color:#7ee787}pre.patch .pm{color:#ff7b72}pre.patch .pa{color:#7ec8ff}pre.patch .ph{color:#ffd166}pre.patch .pf{color:#e6edf3;font-weight:600}
#topbar a.tlink{color:#3dff9e;cursor:pointer;border:1px solid #1f7a4a;padding:1px 8px;border-radius:0;text-decoration:none}
#topbar a.tlink.need{color:#ffbd2e;border-color:#7a5a20}
#today .th button.tnew{margin-left:auto;border-color:#3dff9e;color:#3dff9e}#today .th button.tnew+button{margin-left:0}
#today .it.task{cursor:pointer}#today .it.task:hover{background:#121c2e}
/* ---- the card (planets, folders, the star) ---- */
#panel.card{width:min(480px,92vw);padding:20px 20px 24px}
#panel .ctop{display:flex;align-items:center;gap:12px;padding-right:52px}
#panel .ckind{display:flex;align-items:center;gap:8px;font-size:var(--fs-eyebrow);letter-spacing:.14em;text-transform:uppercase;color:var(--accent,#7ec8ff)}
#panel .ckind img{width:16px;height:16px}
#panel .ctabs{margin-left:auto;display:flex;border:1px solid #344c6b}
#panel .ctabs button{background:transparent;border:0;color:#7f93ab;font:var(--fs-small) var(--font-mono);padding:4px 12px;cursor:pointer;min-height:0}
#panel .ctabs button.on{background:#1a2a44;color:#e6edf3}
#panel .cname{font-size:var(--fs-hud);letter-spacing:.02em;margin:12px 0 2px;color:#f2f6fb}
#panel .cpurpose{color:#9fb3c8;font-size:var(--fs-body);margin-bottom:16px;line-height:1.45}
#panel .cstats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:4px 0 12px}
#panel .cstats div{background:#101c31;border:1px solid #344c6b;padding:8px 8px;min-width:0}
#panel .cstats b{display:block;font-size:var(--fs-list);color:#e6edf3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#panel .cstats span{font-size:var(--fs-small);color:#7f93ab;letter-spacing:.06em}
#panel .cstate{padding:8px 12px;border-left:3px solid #3dff9e;font-size:var(--fs-body);line-height:1.45;background:#0e1a2e;color:#dbe6f2}
#panel .cstate.warn{border-color:#ffbd2e}#panel .cstate.bad{border-color:#ff4d5e}
#panel .csec{font-size:var(--fs-eyebrow);letter-spacing:.16em;color:#7f93ab;margin:16px 0 8px}
#panel .cprob{display:flex;gap:12px;align-items:center;padding:8px 12px;margin:4px 0;background:#1c1322;border:1px solid #344c6b;font-size:var(--fs-body);line-height:1.4}
#panel .cprob span{flex:1;min-width:0}
#panel .cprob button{background:transparent;border:1px solid #ff7b72;color:#ff9a92;font:var(--fs-small) var(--font-mono);padding:4px 8px;cursor:pointer;white-space:nowrap;min-height:0}
#panel .cacts{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 4px}
#panel .cacts button{background:#141f33;border:1px solid #344c6b;color:#d7e5f5;font:var(--fs-body) var(--font-mono);padding:8px 16px;cursor:pointer}
#panel .cacts button:hover{border-color:#5fb4ff}
#panel .cacts button.primary{background:#3dff9e;color:#04210f;border-color:#3dff9e;font-weight:600}
#panel .crow{display:flex;gap:8px;align-items:center;padding:8px 8px;border-bottom:1px solid #344c6b;cursor:pointer;font-size:var(--fs-body)}
#panel .crow:hover{background:#13213a}#panel .crow i{flex:0 0 8px;height:8px;border-radius:0}
#panel .crow img{width:16px;height:16px}
#panel .crow .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#e6edf3}
#panel .crow .m{color:#7f93ab;font-size:var(--fs-small);white-space:nowrap}
#panel .ctbl{font-size:var(--fs-body)}#panel .ctbl td{padding:4px 8px}
#panel .cmut{color:#7f93ab;font-size:var(--fs-small);margin:8px 0;line-height:1.45}
#panel details.call summary,#panel details.cmore summary{cursor:pointer;color:#9bacc0;font-size:var(--fs-small);margin:12px 0 4px}
#panel details.cmore{margin-top:20px;border-top:1px solid #344c6b;padding-top:8px}
#panel pre.cjson{max-height:40vh;overflow:auto;background:#05080f;border:1px solid #344c6b;padding:12px;font-size:var(--fs-small);line-height:1.45;color:#b9c7d8;white-space:pre-wrap;word-break:break-word;margin:0}
/* ---- rail offsets last, so they beat the older absolute positions above ---- */
#hud{left:calc(var(--dashw) + 12px)}#mate,#mateshow{left:calc(var(--dashw) + 12px)}#comms{left:calc(var(--dashw) + 20px)}
#quick,#ticker{left:calc(var(--dashw) + (100vw - var(--dashw) - var(--sidew))/2)}
#panel{right:var(--sidew)}
#wrap.dash{--dashw:310px}#dash{width:310px}#dash .row{font-size:var(--fs-small)}
@media (max-width:1000px){#wrap.dash{--dashw:0px}#wrap.side{--sidew:0px}#panel{right:0}}
/* ==== CONSOLE (design round one). The calm default: one HUD cluster top-left, one bottom strip
   (receipts and four numbers), comms that fold to a strip while every seat is idle, the wingmate.
   H adds the top bar, every count, the seat keys and the sector line. One type scale, a 4-px grid,
   one hairline colour, square corners, colour spent only on state. Last in the sheet: it wins. ==== */
html,body{font:var(--fs-body)/var(--lh-body) var(--font-mono)}
*:focus-visible,button:focus-visible,textarea:focus-visible,pre:focus-visible{outline:2px solid #ffe28a;outline-offset:2px}
#composer textarea:focus,#composer input:focus,#composer select:focus,.tvfoot .tfollow textarea:focus,#mateq:focus,#panel .say textarea:focus{outline:2px solid #ffe28a;outline-offset:1px}
body.quiet #hud,body.quiet #comms{display:block}body.quiet #ticker{display:flex}
body.quiet #topbar,body.quiet #keyline,body.quiet #hud .hfull,body.quiet #theme{display:none}
body:not(.quiet) #topbar{display:flex}body:not(.quiet) #keyline{display:none}
#dash,#side{top:0;bottom:0}
body:not(.quiet) #dash,body:not(.quiet) #side{top:30px}
/* the HUD cluster: the name, the one big number, theme and zoom; the sector line and rebuild time under H */
#hud{top:12px;width:auto;min-width:216px;max-width:300px;padding:12px 16px;background:#0b1428f5;border:2px solid #344c6b;box-shadow:4px 4px 0 #030817;pointer-events:none}
#hud h1{display:flex;align-items:center;gap:8px;margin:0;font:400 var(--fs-title)/var(--lh-title) var(--font-display);letter-spacing:.06em;color:#c8dcf5}
#hud h1::before{margin-right:0}
#hud .sc{margin-top:8px;font:400 var(--fs-big)/var(--lh-big) var(--font-display);letter-spacing:.02em;color:#ffb547}
#hud .sc small{margin-top:4px;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:#9bacc0}
#hud .hl{display:flex;gap:16px;margin-top:12px;padding-top:8px;border-top:1px solid #344c6b;font-size:var(--fs-small);line-height:var(--lh-small);letter-spacing:.06em;color:#9bacc0;white-space:nowrap}
#hud .hl #themeline b{color:#ffd166;font-weight:400}
#hud .hfull{margin-top:4px;font-size:var(--fs-small);line-height:var(--lh-small);letter-spacing:0;color:#9bacc0}
#sector{border:0;padding-top:0;max-width:none;text-transform:none;text-shadow:none}
#quick{top:12px}#theme{top:12px}
body:not(.quiet) #hud,body:not(.quiet) #quick,body:not(.quiet) #theme{top:42px}
/* the bottom strip: one receipts line, four numbers, the H switch; the full counts sit above it under H */
#ticker{left:var(--dashw);right:var(--sidew);bottom:0;transform:none;width:auto;height:var(--row);min-height:0;display:flex;align-items:center;gap:12px;padding:0 12px;background:#0b1428f5;border:0;border-top:1px solid #344c6b;box-shadow:none;font-size:var(--fs-small);color:#c8dcf5;pointer-events:auto;cursor:default}
#ticker .tk{flex:0 0 auto;padding-right:12px;border-right:1px solid #344c6b;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;color:#ffd166;cursor:pointer}
#tkbody{flex:1 1 auto;min-width:0;color:#9bacc0;cursor:pointer}
#nums{flex:0 0 auto;display:flex;gap:4px}
#nums button{display:flex;align-items:baseline;gap:8px;height:24px;padding:0 8px;background:transparent;border:1px solid transparent;color:#9bacc0;font:var(--fs-small)/24px var(--font-mono);white-space:nowrap;cursor:pointer}
#nums button:hover{border-color:#344c6b;color:#c8dcf5}
#nums button b{font:400 var(--fs-title)/24px var(--font-display);color:#c8dcf5}
#nums .zero b{color:#9bacc0}#nums .ok b{color:#3dff9e}#nums .need b{color:#ffd166}#nums .bad b{color:#f85149}
#hmore{flex:0 0 auto;height:24px;padding:0 8px;background:transparent;border:1px solid #344c6b;color:#9bacc0;font:var(--fs-small)/1 var(--font-mono);cursor:pointer}
#hmore:hover{color:#c8dcf5}
#taskbar{left:var(--dashw);right:var(--sidew);bottom:var(--row);height:var(--row);padding:0 12px;gap:16px;background:#0b1428f0;border-top:1px solid #344c6b;font-size:var(--fs-small)}
#taskbar .tb{color:#9bacc0}#taskbar .tb b{color:#c8dcf5}
@media (max-width:1500px){#wrap.dash.side #nums button span,#wrap.dash.side #ticker .tk{display:none}}
/* comms: name · state · one clean line per seat, 32 px rows; a single strip while every seat is idle */
#comms{left:calc(var(--dashw) + 12px);width:400px;padding:0;background:#0b1428f5;border:2px solid #344c6b;box-shadow:4px 4px 0 #030817;font-size:var(--fs-body)}
#comms .ct{display:flex;align-items:center;gap:8px;height:var(--row);margin:0;padding:0 12px;border-bottom:1px solid #344c6b;font:var(--fs-small)/1 var(--font-mono);letter-spacing:0;color:#9bacc0;text-shadow:none;cursor:pointer}
#comms .ct b{font:600 var(--fs-eyebrow)/1 var(--font-mono);letter-spacing:.12em;color:#c8dcf5}
#comms.fold .ct{height:28px;border-bottom:0}#comms.fold #cbody{display:none}
#comms .row{display:flex;align-items:center;gap:8px;height:var(--row);min-height:0;margin:0;padding:0 12px 0 10px;background:transparent;border:0;border-top:1px solid #344c6b;border-left:2px solid var(--c,#58a6ff);box-shadow:none;cursor:pointer}
#comms .row:first-child{border-top:0}#comms .row:hover{background:#13213a}#comms .row.hot{background:#2b2630;animation:none}
#comms .pt{flex:0 0 16px;width:16px;height:16px}#comms .pt .file-planet{width:16px;height:16px}
#comms .nm{flex:0 0 auto;max-width:112px;font-weight:600;font-size:var(--fs-body);line-height:var(--lh-body);color:#c8dcf5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#comms .st{flex:0 0 auto;font-size:var(--fs-small);color:var(--c,#9bacc0)}
#comms .st::before,#comms .tl:not(:empty)::before{content:'· ';color:#9bacc0}
#comms .tl{flex:1 1 auto;min-width:0;font-size:var(--fs-small);color:#9bacc0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#comms .more,#comms .note{display:flex;align-items:center;min-height:var(--row);padding:0 12px;border-top:1px solid #344c6b;font-size:var(--fs-small);color:#9bacc0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#comms .note{padding-top:6px;padding-bottom:6px;line-height:var(--lh-small);white-space:normal}   /* a note wraps, never cut */
/* the wingmate: the portrait stays, the name tag takes the display face, the words 14 px */
#mate{width:min(480px,calc(100vw - var(--dashw) - var(--sidew) - 24px))}
#mate .mp b{top:-26px;padding:4px 8px;font:400 var(--fs-title)/1 var(--font-display);letter-spacing:.06em}
#mate .nb{padding:8px 12px;font-size:var(--fs-list);line-height:var(--lh-list);color:#c8dcf5}
#mate .nh{margin-bottom:4px;font-size:var(--fs-small);color:#9bacc0}
#matetxt{font-size:var(--fs-list);line-height:var(--lh-list)}
#mateq{height:var(--row);font-size:var(--fs-body)}#matef button{height:var(--row)}
@media (min-width:701px){
 #mate,#mateshow{bottom:44px}body:not(.quiet) #mate,body:not(.quiet) #mateshow{bottom:76px}
 /* comms hang under the HUD, so the wingmate can grow with what it says without covering them */
 #comms,body.quiet #comms,body:not(.quiet):not(.mateoff) #comms{top:172px;bottom:auto;max-height:calc(100vh - 172px - 260px);overflow:auto}
 body:not(.quiet) #comms,body:not(.quiet):not(.mateoff) #comms{top:256px;max-height:calc(100vh - 256px - 300px)}
}
/* the left rail: NEEDS YOU, CREW, SYSTEM as titled sections of at most five 32 px rows; Today on a tab */
#dash{padding:0 12px 16px;background:#0b1428;border-right:1px solid #344c6b;font-size:var(--fs-list);color:#c8dcf5}
#dash .dtabs{position:sticky;top:0;z-index:1;display:flex;margin:0 -12px;padding:0 4px;background:#0b1428;border-bottom:1px solid #344c6b}
#dash .dtabs button{height:var(--row);padding:0 8px;background:transparent;border:0;border-bottom:2px solid transparent;color:#9bacc0;font:600 var(--fs-eyebrow)/1 var(--font-mono);letter-spacing:.12em;text-transform:uppercase;cursor:pointer}
#dash .dtabs button.on{color:#c8dcf5;border-bottom-color:#ffd166}#dash .dtabs button b{margin-left:8px;color:#ffd166}
#dash .dsec{margin-top:20px}#dash .dsec:first-of-type{margin-top:12px}
#dash h3{display:flex;align-items:center;gap:8px;margin:0 0 4px;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:#9bacc0}
#dash h3 .dn{color:#c8dcf5}
#dash h3 a{margin-left:auto;font:var(--fs-small)/1 var(--font-mono);letter-spacing:0;text-transform:none;color:#5fb4ff;cursor:pointer}
#dash .row{display:flex;align-items:center;gap:8px;height:var(--row);margin:0;padding:0 8px;background:transparent;border:0;border-bottom:1px solid #344c6b;font-size:var(--fs-list);line-height:var(--lh-list);cursor:pointer}
#dash .row:hover{background:#13213a}
#dash .row i{flex:0 0 8px;height:8px;margin:0}
#dash .rt{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#c8dcf5}
#dash .rm{flex:0 0 auto;max-width:45%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:var(--fs-small);color:#9bacc0}
#dash .row.tall,#dash .row.more{height:auto;min-height:var(--row);padding-top:6px;padding-bottom:6px}#dash .row.tall{flex-wrap:wrap;row-gap:2px}#dash .row.more .rt{white-space:normal}#dash .row.tall .rm{flex:1 0 100%;max-width:none;padding-left:16px;white-space:normal}
#dash .need .rm{color:#ffd166}#dash .bad .rm{color:#ff8a8a}#dash .work .rm,#dash .ok .rm{color:#3dff9e}
#dash .row.more{border-bottom:0}#dash .row.more i{background:transparent}#dash .row.more .rt{font-size:var(--fs-small);color:#5fb4ff}
#dash .mut{display:flex;align-items:center;height:var(--row);margin:0;padding:0 8px;font-size:var(--fs-small);color:#9bacc0}
/* the right rail: threads, the composer and one thread, on the same scale */
#side{background:#0b1428;border-left:1px solid #344c6b}
#side .shead{height:40px;padding:0 12px;gap:8px;border-bottom:1px solid #344c6b;font-size:var(--fs-small)}
#side .shead b{font:600 var(--fs-eyebrow)/1 var(--font-mono);letter-spacing:.12em;color:#c8dcf5}#side .shead span{color:#9bacc0}
#side .shead button{height:28px;padding:0 8px;background:transparent;border:1px solid #344c6b;color:#c8dcf5;font:var(--fs-small)/1 var(--font-mono)}
#side .shead #snew{margin-left:auto;background:transparent;border-color:#3dff9e;color:#3dff9e;font-weight:600}
#tasks{padding:0 12px 8px;font-size:var(--fs-list)}
#tasks h3{margin:16px 0 4px;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:#9bacc0}
#tasks .trow{min-height:var(--row);gap:8px;padding:8px;border-radius:0}
#tasks .trow b{font-size:var(--fs-list);line-height:var(--lh-list);color:#c8dcf5}#tasks .trow small{font-size:var(--fs-small);line-height:var(--lh-small);color:#9bacc0}
#tasks .mut{margin:4px 0;font-size:var(--fs-small);color:#9bacc0}
#composer{padding:16px 16px 12px;background:#0b1428;color:#c8dcf5}
#composer h2{margin:0 0 4px;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;color:#9bacc0}
#composer h2 span{margin-left:8px;color:#3dff9e;font-weight:600;letter-spacing:.02em}
#composer .sub{margin-bottom:12px;font-size:var(--fs-body);line-height:var(--lh-body);color:#9bacc0}
#composer textarea{min-height:144px;padding:12px;background:#080d20;border:1px solid #344c6b;border-radius:0;color:#c8dcf5;font:var(--fs-list)/var(--lh-list) var(--font-mono)}
#composer .cfield{display:flex;flex-direction:column;gap:4px;min-width:0}
#composer .crow label,#composer .cfield label{display:block;padding:0;background:transparent;border:0;border-radius:0;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:#9bacc0}
#composer .cwide{margin-top:12px}
#composer .crow{display:grid;gap:8px 12px;margin-top:12px;align-items:end}
#composer .crow.r1{grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto}
#composer .crow.r2{grid-template-columns:minmax(0,1fr) minmax(0,1.6fr) auto}
#composer input[type=text],#composer select{width:100%;max-width:none;height:var(--row);padding:0 8px;background:#080d20;border:1px solid #344c6b;border-radius:0;color:#c8dcf5;font:var(--fs-body)/1 var(--font-mono)}
#composer input:disabled{opacity:.55}
#composer .chk{display:flex;align-items:center;gap:8px;height:var(--row);padding:0 12px 0 8px;background:transparent;border:1px solid #344c6b;border-radius:0;color:#c8dcf5;font-size:var(--fs-body);white-space:nowrap}
#composer .cnarrow select{width:72px}
#composer .cbtns{margin-top:16px;gap:12px}
#composer button{height:var(--row);padding:0 16px;border-radius:0;font:var(--fs-body)/1 var(--font-mono)}
#composer button.go{background:#ffd166;border-color:#ffd166;color:#04060c;font-weight:600}
#composer button.txt{padding:0 4px;background:transparent;border-color:transparent;color:#9bacc0;text-decoration:underline}
/* the card: the name in the display face, stat tiles, problems in full, two actions */
#panel .ckind{font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em}
#panel .ctabs button{height:28px;padding:0 8px;font:var(--fs-small)/1 var(--font-mono)}
#panel .cname{margin:12px 0 4px;font:400 var(--fs-hud)/var(--lh-hud) var(--font-display);letter-spacing:.04em;color:#c8dcf5}
#panel .cpurpose{margin-bottom:12px;font-size:var(--fs-body);line-height:var(--lh-body);color:#9bacc0}
#panel .clink{color:#5fb4ff;text-decoration:underline;white-space:nowrap;cursor:pointer}
#panel .cstats{gap:8px;margin:4px 0 12px}
#panel .cstats div{padding:8px;background:#080d20;border:1px solid #344c6b}
#panel .cstats b{font:400 var(--fs-title)/var(--lh-title) var(--font-display);color:#c8dcf5}
#panel .cstats span{display:block;margin-top:4px;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:#9bacc0}
#panel .cstate{padding:8px 12px;background:#080d20;font-size:var(--fs-list);line-height:var(--lh-list)}
#panel .csec{margin:20px 0 4px;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;color:#9bacc0}
#panel .cprob{align-items:flex-start;gap:12px;min-height:var(--row);margin:0;padding:8px 12px 8px 10px;background:transparent;border:0;border-bottom:1px solid #344c6b;border-left:2px solid #f85149;font-size:var(--fs-list);line-height:var(--lh-list);color:#c8dcf5;cursor:pointer}
#panel .cprob:hover{background:#1c1322}
#panel .cprob span{white-space:normal;overflow-wrap:anywhere}
#panel .cprob i{flex:0 0 auto;font-style:normal;font-size:var(--fs-small);color:#ff8a8a;white-space:nowrap}
#panel .cacts{gap:12px;margin:16px 0 4px}
#panel .cacts button{height:var(--row);padding:0 16px;background:transparent;border:1px solid #344c6b;color:#c8dcf5;font:var(--fs-body)/1 var(--font-mono)}
#panel .cacts button.primary{background:#ffd166;border-color:#ffd166;color:#04060c;font-weight:600}
#panel .crow{min-height:var(--row);gap:8px;padding:0 8px;border-bottom:1px solid #344c6b;font-size:var(--fs-list)}
#panel .crow .n{color:#c8dcf5}#panel .crow .m{font-size:var(--fs-small);color:#9bacc0}
/* one thread */
.tvh{padding:12px 16px 0}
.tvh .pill{padding:2px 8px;border-radius:0;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono)}
.tvh .tvt{font-size:var(--fs-title);line-height:var(--lh-title);color:#c8dcf5}
.chip2{padding:2px 8px;border-radius:0;font-size:var(--fs-small)}
.tvtabs button{height:var(--row);padding:0 12px;font:var(--fs-body)/1 var(--font-mono);color:#9bacc0}
.tvtabs button.on{color:#c8dcf5;border-bottom-color:#c8dcf5}
.tvpane{padding:12px 16px}
.tv-chat{font-size:var(--fs-body);line-height:var(--lh-body)}
.tv-msg{padding:8px 12px;border-radius:0}
.tv-msg .who{font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;color:#9bacc0}
.tv-acts{border-radius:0}.tv-acts summary{font-size:var(--fs-small);color:#9bacc0}
.tvfoot{padding:8px 12px 12px;background:#0b1428}
.tvfoot .tbtns button{height:var(--row);padding:0 12px;border-radius:0;font:var(--fs-body)/1 var(--font-mono)}
.tvfoot .tbtns select{height:var(--row);border-radius:0}
.tvfoot .tfollow textarea{border-radius:0;font:var(--fs-body)/var(--lh-body) var(--font-mono)}
.tvfoot .tfollow button{border-radius:0}
.tvnote{margin:-2px 0 8px;font-size:var(--fs-small);line-height:var(--lh-small);color:#ffd166}
.tblockers{margin:8px 2px 0;font-size:var(--fs-small);color:#ff8a8a}
@media (max-width:1100px){#comms{width:340px}}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
#demobanner{position:absolute;z-index:5;top:auto;bottom:84px;left:calc(var(--dashw) + 20px);max-width:310px;line-height:1.5;background:#ffd166;color:#04060c;font:600 var(--fs-small) var(--font-mono);letter-spacing:.14em;padding:4px 12px;box-shadow:3px 3px 0 #000;pointer-events:none}
/* ==== MOBILE. A phone gets html.m (narrow, or a short touch screen turned sideways), a tablet html.t
   (touch, wider than 700 px). Every rule below starts with one of them and the new ids sit hidden
   until then, so a desktop page does not change. Opaque surfaces use the palette colours and
   translucent ones the alpha form, so every theme pack recolours the block. The tabs replace the
   keys: Today, Map, Threads and the wingmate are the four places, the card is a sheet over the map. ==== */
#tabs,#mfab,#mzoom,#msheet,#scrim,#mclose,#mtoast,#pickbar,#pgrip,#sback,#cpick,#cadv,#hud #mmore,#hud #mline,#hud #m3d{display:none}
#tab-today,#tab-map,#tab-threads,#tab-wingmate,#mz-in,#mz-out,#mz-c,#msheet-title,#msheet-body,#msheet-x{display:none}
html.m{--T:env(safe-area-inset-top);--B:calc(60px + max(18px,env(safe-area-inset-bottom)));--P:0px;--sal:env(safe-area-inset-left);--sar:env(safe-area-inset-right);--railw:0px;--row:44px;--fs-eyebrow:12px;--fs-small:13px;--fs-body:15px;--fs-list:15px;--lh-eyebrow:16px;--lh-small:18px;--lh-body:22px;--lh-list:22px;overscroll-behavior:none;touch-action:manipulation;-webkit-tap-highlight-color:transparent;-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
html.m.land{--B:0px;--railw:calc(64px + var(--sar))}
html.m input,html.m textarea,html.m pre.live{-webkit-user-select:text;user-select:text}
html.m :is(#tlog,#tdiff,#pbody,#matetxt){-webkit-user-select:text;user-select:text}   /* what an agent wrote and what a card says can be selected and copied */
html.m #pbody :is(button,.ctabs){-webkit-user-select:none;user-select:none}   /* ...but the card's buttons are taps */
html.m body,html.m :is(#pbody,#tasks,#composer,#today,#quests,#legend,#prompter,#msheet-body,#matetxt,.tvpane,#tlog){overscroll-behavior:contain}
html.m :is(input,textarea,select):not(#_):not(#__){font-size:16px}
/* what a phone does not carry: the key legend, the rails, the comms box, the receipts text */
html.m #dash,html.m #comms,html.m #theme,html.m #topbar,html.m #keyline,html.m #taskbar,html.m #help,html.m #mateshow,html.m body:not(.pad) #touchpad,html.m #hmore,html.m #ticker .tk,html.m #tkbody,html.m #glance-alert,html.m #hud .hl,html.m #hud .hfull,html.m #sclose,html.m #matehide{display:none!important}
html.m body.pad #touchpad{left:max(12px,var(--sal));bottom:calc(var(--B) + var(--P) + 68px)}
html.m #wrap{--dashw:0px;--sidew:0px}
html.m #stage{top:0;bottom:var(--B);left:0;right:var(--railw)}
/* the top bar: the name, the days number, More */
html.m #hud{position:fixed;z-index:20;top:0;left:0;right:var(--railw);width:auto;min-width:0;max-width:none;pointer-events:auto;height:calc(54px + var(--T));padding:var(--T) max(12px,var(--sar)) 0 max(16px,var(--sal));display:flex;align-items:center;gap:8px;background:#0b1428f5;border:0;border-bottom:1px solid #344c6b;box-shadow:none}
html.m #hud h1{white-space:nowrap}
html.m #hud .sc{margin:0;min-height:44px;display:flex;align-items:center;font:400 var(--fs-title)/var(--lh-title) var(--font-display);pointer-events:auto}
html.m #hud .sc small{display:none}
html.m #hud .sc::after{content:' decisions';margin-left:8px;font:600 var(--fs-small)/var(--lh-small) var(--font-mono);color:#9bacc0}
html.m #hud #mmore{display:block;margin-left:auto;min-width:44px;height:44px;padding:0 12px;background:transparent;border:1px solid #344c6b;color:#c8dcf5;font:600 var(--fs-small)/1 var(--font-mono);pointer-events:auto;cursor:pointer}
/* four numbers under it, equal columns; the row lets taps through, the numbers take them */
html.m #ticker{position:fixed;z-index:19;top:calc(var(--T) + 52px);left:0;right:var(--railw);bottom:auto;transform:none;width:auto;height:44px;min-height:0;padding:0 max(12px,var(--sar)) 0 max(12px,var(--sal));gap:0;display:flex;border:0;background:transparent;box-shadow:none;pointer-events:none}
html.m #nums{flex:1 1 auto;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px}
html.m #nums button{pointer-events:auto;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:0;height:44px;min-width:0;padding:0 2px;background:#0b1428e6;border:1px solid #344c6b;font:var(--fs-small)/var(--lh-small) var(--font-mono)}
html.m #nums button b{font:400 var(--fs-title)/20px var(--font-display)}
html.m body.m-pick #ticker{display:none}
html.m #quick{display:flex;position:fixed;z-index:4;top:auto;bottom:calc(var(--B) + var(--P) + 12px);left:max(12px,var(--sal));right:calc(var(--railw) + 80px);transform:none;max-width:none;padding:0;gap:8px;background:transparent;border:0;box-shadow:none;overflow-x:auto}
html.m #quick:empty{display:none}
html.m #quick button{min-width:56px;min-height:44px;padding:0 12px;background:#0b1428e6;font:var(--fs-body)/1 var(--font-mono)}
html.m #quick button b{display:none}
html.m #gohome:not([hidden]){display:flex;align-items:center;position:fixed;z-index:21;top:calc(var(--T) + 104px);right:calc(var(--railw) + 12px);min-height:44px;padding:0 12px}
html.m body.m-cover #gohome{display:none}
html.m.land #gohome:not([hidden]){top:calc(var(--T) + 48px)}
html.m body.m-pick #quick,html.m body.m-pick #gohome{display:none}
/* the new-thread button and the zoom column, only on the map and only while nothing covers it */
html.m #mfab{position:fixed;z-index:31;right:calc(var(--railw) + 14px);bottom:calc(var(--B) + var(--P) + 14px);height:42px;align-items:center;justify-content:center;gap:7px;padding:0 16px 0 13px;background:#0b1428;border:2px solid #c8dcf5;color:#c8dcf5;cursor:pointer}
html.m #mzoom,html.t #mzoom{flex-direction:column;gap:4px;width:44px}
html.m #mzoom{position:fixed;z-index:21;right:calc(var(--railw) + 18px);bottom:calc(var(--B) + var(--P) + 80px)}
html.m canvas#c,html.t canvas#c{-webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
html.m #mzoom button,html.t #mzoom button{display:block;touch-action:manipulation;width:44px;height:44px;padding:0;background:#0b1428e6;border:1px solid #344c6b;color:#c8dcf5;font:400 var(--fs-title)/1 var(--font-mono);cursor:pointer}
html.m body[data-tab=map]:not(.m-cover):not(.m-pick):not(.m-comp) #mfab{display:flex}
html.m body[data-tab=map].m-peek:not(.m-over):not(.m-pick):not(.m-comp) #mfab{display:flex;bottom:calc(var(--B) + var(--P) + 35dvh + 14px)}   /* above the peek sheet: + Task aims at the planet it shows */
/* one new-thread button, the same square in the same corner on every screen that lists work: Today and the thread list
   (not an open thread or the composer) carry it too, and their own header buttons step aside */
html.m #today button.tnew{display:none}   /* the Agents list keeps its own + New task: the glass button is the map's */
/* the tab bar: bottom on a portrait phone, a rail on the right when turned sideways */
html.m #tabs{display:flex;position:fixed;z-index:46;left:0;right:0;bottom:0;height:var(--B);padding:0 var(--sar) env(safe-area-inset-bottom) var(--sal);background:#0b1428;border-top:1px solid #344c6b}
html.m.kb #tabs{display:none}
html.m #tabs button{position:relative;flex:1 1 0;min-width:0;min-height:56px;display:flex;align-items:center;justify-content:center;padding:0 4px;background:transparent;border:0;border-radius:0;color:#9bacc0;font:600 var(--fs-small)/var(--lh-small) var(--font-mono);letter-spacing:.04em;cursor:pointer}
html.m #tabs button .tl{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
html.m #tabs button[aria-selected=true]{color:#ffd166;box-shadow:inset 0 3px 0 #ffd166}
html.m #tabs button:active{background:#13213a}
html.m #tabs .tabn:not([hidden]){display:block;position:absolute;top:4px;right:8px;min-width:16px;height:16px;padding:0 4px;background:#ffd166;color:#04060c;font:600 var(--fs-eyebrow)/16px var(--font-mono);font-style:normal;text-align:center}
html.m #tabs .tabn[data-n]:not([data-n=""])::after{content:attr(data-n)}
html.m #tabs .tabn.dot:not([hidden]){min-width:10px;width:10px;height:10px;padding:0;top:10px}
html.m.land #hud{height:calc(40px + var(--T))}
/* sideways, the map ends 68 px short of the tab rail: the zoom column and the new-thread button sit in that strip,
   so no planet, count or label is ever drawn under them */
html.m.land #stage{right:var(--railw)}
html.m.land #mzoom{right:calc(var(--railw) + 12px)}
html.m.land #mfab{right:calc(var(--railw) + 14px);bottom:calc(var(--P) + 14px)}
html.m.land #ticker{display:none}
html.m.land #tabs{top:0;left:auto;bottom:0;right:0;width:var(--railw);height:auto;flex-direction:column;padding:var(--T) var(--sar) 0 0;border-top:0;border-left:1px solid #344c6b}
html.m.land #tabs button[aria-selected=true]{box-shadow:inset -3px 0 0 #ffd166}
html.m.land #tabs button{padding:0 2px;letter-spacing:0}
html.m.land #hud #mmore{position:relative;height:40px}
html.m.land #hud #mmore::before{content:'';position:absolute;left:0;right:0;top:-2px;bottom:-2px}
/* Today: the first screen, sections in the order a phone reads them */
html.m #today{position:fixed;top:var(--T);bottom:calc(var(--B) + var(--P));left:0;right:var(--railw);z-index:30;padding-left:var(--sal)}
html.m #today .tw{display:flex;flex-direction:column;max-width:none;padding:18px 14px 28px}
html.m #today .th{order:-1;flex-wrap:wrap;gap:8px 12px;padding-bottom:8px}
html.m #today .th .gen{flex:0 0 auto;margin-left:auto}
html.m #today .th button[data-go=map]{display:none}
html.m #today section{margin-top:20px}
html.m #today .tcols,html.m #today .tcol{display:contents}   /* the desktop's two columns read as one in the comp's order: Need you (four rows, then "+N more"), Running now with its agents and the night run, Do today */
html.m #today section[data-sec=needs]{order:1}
html.m #today section[data-sec=decisions]{order:2}
html.m #today section[data-sec=tasks]{order:3}
html.m #today section[data-sec=agents]{order:4}
html.m #today section[data-sec=night]{order:5}
html.m #today section[data-sec=word]{order:6}
html.m #today section[data-sec=done]{order:7}
html.m #today section[data-sec=plan]{order:8}
html.m #today section[data-sec=daily]{order:9}
html.m #today section[data-sec=folders]{order:10}
html.m #today [data-sec=missing]{order:11}
html.m #today .tmini{display:none}
html.m #today .lane{grid-template-columns:minmax(0,1fr)}
html.m #today .fold{grid-template-columns:auto auto minmax(0,1fr);gap:0 12px;align-items:center;font-size:var(--fs-body)}
html.m #today a.go{display:inline-flex;align-items:center}
html.m #today .hist{float:none;display:block;margin:0 0 4px}
/* Threads: the list, one thread, the composer, each a full screen */
html.m #side{position:fixed;z-index:30;top:var(--T);bottom:calc(var(--B) + var(--P));left:0;right:var(--railw);width:auto;border-left:0;padding-left:var(--sal)}
html.m #side.pick{display:none!important}
html.m #side .shead{flex:0 0 auto;height:52px;padding:0 12px;gap:8px}
html.m #side .shead b{white-space:nowrap}
html.m #side .shead #snew{margin-left:auto;height:44px;min-width:44px;padding:0 12px}
html.m #side.tview #snew,html.m #side.compose #snew{display:none}
html.m #side.tview #sback{display:flex;align-items:center;height:44px;min-width:44px;padding:0 12px}
html.m #side.tview #tasks{display:none}
html.m #side.tview .shead b,html.m #side.tview #scount{display:none}
html.m #tasks{flex:1 1 auto;max-height:none;min-height:0;padding:0 12px 96px}
html.m #tasks .trow{padding:8px}
html.m #tbody{flex:1 1 auto;min-height:0}
html.m #side.tview #tbody{border-top:0}
html.m .tvh{padding:8px 12px 0}
html.m .tvtabs button{min-height:44px;min-width:44px}
html.m .tvpane{padding:12px}
html.m .tvfoot{padding:8px 12px calc(8px + env(safe-area-inset-bottom))}
html.m .tvfoot .tbtns button,html.m .tvfoot .tbtns select,html.m .tvfoot .tfollow button{min-height:44px}
html.m .tvfoot .tbtns select{max-width:none;flex:1 1 140px}
html.m .tvfoot .tfollow form{grid-template-columns:minmax(0,1fr)}
html.m .tv-msg.you{margin-left:12px}html.m .tv-msg.agent,html.m .tv-msg.review,html.m .tv-msg.err{margin-right:12px}
html.m #composer{flex:1 1 auto;min-height:0;padding:12px 16px 0}
html.m #composer .sub{display:none}
html.m #composer textarea{height:112px;min-height:96px;max-height:none}
html.m #composer .cwide{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 8px;align-items:center}
html.m #composer .cwide label{grid-column:1;grid-row:1}
html.m #composer .cwide #cfolder{grid-column:1 / -1;grid-row:2}   /* the folder gets the whole row: its end is the folder's name */
html.m #cpick{display:block;grid-column:2;grid-row:1;height:44px;padding:0 12px;white-space:nowrap}
html.m #composer .crow.r1,html.m #composer .crow.r2{grid-template-columns:minmax(0,1fr)}
html.m #composer .cchk{display:flex;flex-direction:row;align-items:center;gap:12px}
html.m #composer .cchk label{flex:1 1 auto;min-height:44px;display:flex;align-items:center}
html.m #composer .chk{flex:0 0 auto}
html.m #composer .cnarrow select{width:100%}
html.m #cadv{display:flex;flex:0 0 auto;align-items:center;justify-content:space-between;width:100%;margin-top:12px;padding:0 12px;background:transparent;color:#9bacc0}
html.m #cadv::after{content:'+'}
html.m #cadv.open::after{content:'−'}
html.m #composer .crow.r2{display:none;margin-top:8px}
html.m #cadv.open + .crow.r2{display:grid}
html.m #composer .cbtns{position:sticky;bottom:0;z-index:2;flex-wrap:wrap;gap:8px 12px;margin:auto -16px 0;padding:12px 16px calc(12px + env(safe-area-inset-bottom));background:#0b1428;border-top:1px solid #344c6b}
html.m #composer .cbtns .saym{order:-1;flex:1 1 100%;margin:0}
html.m #composer .cbtns .saym:empty{display:none}
html.m #composer #cgo{flex:1 1 auto}
html.m #composer button.txt{min-width:44px;padding:0 12px}
html.m #pickbar{position:fixed;z-index:36;left:12px;right:calc(var(--railw) + 12px);top:calc(var(--T) + 56px);min-height:52px;align-items:center;gap:12px;padding:0 8px 0 12px;background:#0b1428f5;border:2px solid #ffd166;color:#c8dcf5;font:var(--fs-body)/var(--lh-body) var(--font-mono)}
html.m body.m-pick #pickbar{display:flex}
html.m #pickbar span{flex:1 1 auto}
html.m #pickbar .pickx{flex:0 0 auto;min-height:44px;min-width:44px;padding:0 12px;background:transparent;border:1px solid #344c6b;color:#c8dcf5;font:var(--fs-small)/1 var(--font-mono)}
/* the card: a sheet from the bottom edge, a 35 percent peek, full on a seat or a pull */
html.m #panel{display:flex;flex-direction:column;left:0;right:0;top:auto;bottom:calc(var(--B) + var(--P));width:auto;height:35dvh;max-height:calc(100dvh - var(--T) - var(--B) - var(--P));z-index:32;padding:0 max(16px,var(--sar)) 0 max(16px,var(--sal));border-left:0;border-top:2px solid #344c6b;box-shadow:none;visibility:hidden;transform:translateY(100%);transition:transform .16s ease,height .16s ease,visibility 0s linear .16s}
html.m #panel.on{visibility:visible;transform:none;box-shadow:0 -4px 0 #04091799;transition:transform .16s ease,height .16s ease,visibility 0s}
html.m #panel.full{height:calc(100dvh - var(--T) - var(--B) - var(--P))}
html.m #pgrip{display:block;position:relative;flex:0 0 44px;width:100%;height:44px;margin:0;padding:0;background:transparent;border:0;border-radius:0;touch-action:none;cursor:grab}
html.m #pgrip::before{content:'';position:absolute;left:50%;top:20px;width:40px;height:4px;margin-left:-20px;background:#9bacc0}
html.m #pbody{flex:1 1 auto;height:auto;min-height:0;padding-bottom:16px}
html.m #panel .close{top:0;right:max(0px,var(--sar));width:44px;height:44px;min-width:44px;min-height:44px;padding:0;background:transparent;border:0;font-size:0}
html.m #panel .close::after{content:'×';color:#c8dcf5;font:400 var(--fs-hud)/44px var(--font-mono)}
html.m .seathead,html.m #panel .ctop{padding-right:0}
html.m #panel.card{width:auto;padding:0 max(16px,var(--sar)) 0 max(16px,var(--sal))}
html.m #panel dl{grid-template-columns:96px minmax(0,1fr);gap:4px 12px;margin-top:8px;font-size:var(--fs-body)}
html.m #panel .cstats{grid-template-columns:repeat(2,minmax(0,1fr))}
html.m #panel .ctabs button{height:44px;min-width:44px}
html.m #panel.seat #pbody{overflow:auto}
html.m .seatview{height:auto;min-height:100%}
html.m #panel .live{min-height:0;font-size:var(--fs-small)}
html.m #panel.seat .live{flex:0 0 auto;min-height:36dvh;max-height:50dvh}
html.m #panel .say form{grid-template-columns:minmax(0,1fr)}
html.m #panel .say textarea{font-size:16px;min-height:76px}
html.m #panel .say button,html.m #panel .terminalbar button{min-height:44px;min-width:44px}
html.m #panel .tbl td{padding:4px 8px}
html.m #panel tr.frow td{box-sizing:border-box;height:44px;padding:12px 8px}
html.t #panel tr.frow td{box-sizing:border-box;height:44px;padding:8px}
html.m #panel .clink{display:inline-flex;align-items:center;min-height:44px;min-width:44px}
html.m #panel details summary{display:flex;align-items:center}
html.m.land #panel{left:auto;right:var(--railw);top:calc(var(--T) + 40px);bottom:var(--P);max-height:none;width:min(420px,52vw);height:auto;padding:0 16px;border-top:0;border-left:2px solid #344c6b;transform:translateX(100%)}
html.m.land #panel.on{transform:none;box-shadow:-4px 0 0 #04091799}
html.m.land #panel.full{height:auto}
html.m.land #panel.card{width:min(420px,52vw)}
html.m.land #pgrip{display:none}
html.m.land #panel .close{right:0}
html.m.land .seathead,html.m.land #panel .ctop{padding-right:52px}   /* no grip row sideways: the header leaves room for the cross */
/* the board, the legend and the prompt: a whole screen with its own close */
html.m #quests,html.m #prompter,html.m #legend{position:fixed;z-index:34;left:0;right:var(--railw);top:var(--T);bottom:calc(var(--B) + var(--P));width:auto;max-width:none;max-height:none;transform:none;border:0;box-shadow:none;padding:56px 16px 16px;overflow:auto;background:#0b1428}
html.m #quests{opacity:1;pointer-events:none;visibility:hidden}
html.m #quests.on{pointer-events:auto;visibility:visible}
html.m #prompter:not(.on),html.m #legend:not(.on){visibility:hidden}
html.m #prompter.on,html.m #legend.on{display:block}
html.m #quests .cols{grid-template-columns:minmax(0,1fr)}
html.m #quests .q{flex-wrap:wrap}
html.m #quests .q .body{flex:1 1 calc(100% - 20px)}
html.m #quests .q .sr{flex:1 1 100%;flex-direction:row;flex-wrap:wrap;gap:8px}
html.m #quests .q .sr button,html.m #quests .q .sr select{width:auto;flex:1 1 120px;max-width:none;min-height:44px}
html.m #quests .q details summary{display:inline-flex;align-items:center;min-height:44px;min-width:44px}
html.m #quests .q .run button.rout,html.m #quests .col .sub button.showret{display:inline-flex;align-items:center;min-height:44px;min-width:44px}
html.m #quests .sweep select,html.m #quests .sweep button{min-height:44px}
html.m #prompter textarea{height:140px}
html.m #prompter .prow select{flex:1 1 140px}
html.m #prompter select,html.m #prompter button{min-height:44px}
html.m #legend .lg a[data-theme-pick]{display:inline-flex;align-items:center;min-height:44px;min-width:44px}
html.m #mclose{position:fixed;z-index:35;top:calc(var(--T) + 6px);right:calc(var(--railw) + 6px);width:44px;height:44px;align-items:center;justify-content:center;padding:0;background:#0b1428;border:1px solid #344c6b;color:#c8dcf5;font:400 var(--fs-hud)/1 var(--font-mono);cursor:pointer}
html.m body.m-over #mclose{display:flex}
/* the wingmate: a screen of its own, a portrait row, the words, the box docked under them */
html.m #mate{display:none!important}
html.m body[data-tab=wingmate] #mate{display:flex!important;flex-direction:column;position:fixed;z-index:30;top:var(--T);bottom:calc(var(--B) + var(--P));left:0;right:var(--railw);width:auto;padding-left:var(--sal);filter:none;background:#080d20}
html.m #mate .mp{flex:0 0 96px;display:flex;align-items:center;padding:8px 12px;border:0;border-bottom:2px solid #5fb4ff}
html.m #mate .mp canvas{width:80px;height:80px}
html.m #mate .mp b{position:static;margin-left:12px}
html.m #mate .nb{flex:1 1 auto;min-height:0;border:0;padding:12px 16px}
html.m #mate .nh button{min-height:44px;min-width:44px}
html.m #matetxt{flex:1 1 auto;min-height:0;max-height:none}
html.m #matef{margin-top:12px}
html.m #mateq{height:44px}
html.m #matef button{height:44px;min-width:56px}
html.m.land #mate .mp{flex-basis:72px}
html.m.land #mate .mp canvas{width:56px;height:56px}
/* seats that need an answer sit above the tabs on every screen */
html.m #permits{position:fixed;z-index:41;left:8px;right:calc(var(--railw) + 8px);top:auto;bottom:var(--B);transform:none;width:auto;max-width:none;max-height:45dvh}
html.m.kb #permits{bottom:var(--kb,0px);max-height:calc(var(--vvh,100dvh) * .3)}
html.m #permits .pcard{padding:12px}
html.m #permits .prow{flex-wrap:wrap}
html.m #permits button{min-height:48px;min-width:48px;padding:0 16px}
/* with the keyboard up the tab bar goes and the full screens take the visible part of the screen */
html.m.kb #today,html.m.kb #side,html.m.kb #quests,html.m.kb #prompter,html.m.kb #legend,html.m.kb body[data-tab=wingmate] #mate{top:var(--vvt,0px);bottom:auto;height:calc(var(--vvh,100dvh) - var(--P))}
html.m.kb #panel.full{top:var(--vvt,0px);bottom:auto;height:calc(var(--vvh,100dvh) - var(--P))}
html.m #staticbanner{z-index:48;padding-top:calc(var(--T) + 8px)}
html.m #demobanner{top:calc(var(--T) + 100px);bottom:auto;left:12px;right:calc(var(--railw) + 12px);max-width:none;text-align:center;letter-spacing:.06em}
/* the sheet for More and for a long press, and its toast */
html.m #scrim,html.t #scrim{position:fixed;inset:0;z-index:44;background:#04060ccc}
html.m #scrim.on,html.t #scrim.on{display:block}
html.m #msheet{display:flex;flex-direction:column;position:fixed;z-index:45;left:0;right:var(--railw);bottom:var(--B);max-height:calc(100dvh - var(--T) - var(--B) - 8px);padding-bottom:0;background:#0b1428;border-top:2px solid #344c6b;box-shadow:0 -4px 0 #04091799;visibility:hidden;transform:translateY(100%);transition:transform .16s ease,visibility 0s linear .16s}
html.m #msheet.on{visibility:visible;transform:none;transition:transform .16s ease,visibility 0s}
html.m.land #msheet,html.m.kb #msheet{bottom:0;padding-bottom:env(safe-area-inset-bottom)}
html.m #msheet .mshead,html.t #msheet .mshead{flex:0 0 auto;display:flex;align-items:center;height:52px;padding:0 4px 0 16px;border-bottom:1px solid #344c6b}
html.m #msheet-title,html.t #msheet-title{display:block;flex:1 1 auto;font:600 var(--fs-eyebrow)/var(--lh-eyebrow) var(--font-mono);letter-spacing:.12em;text-transform:uppercase;color:#9bacc0}
html.m #msheet-x,html.t #msheet-x{display:block;width:44px;height:44px;padding:0;background:transparent;border:0;color:#c8dcf5;font:400 var(--fs-hud)/1 var(--font-mono);cursor:pointer}
html.m #msheet-body,html.t #msheet-body{display:block;flex:1 1 auto;min-height:0;overflow:auto;padding:4px 0 8px}
html.m #msheet-body .mrow,html.t #msheet-body .mrow{display:block;width:100%;min-height:52px;padding:0 16px;text-align:left;background:transparent;border:0;border-bottom:1px solid #344c6b;color:#c8dcf5;font:var(--fs-body)/var(--lh-body) var(--font-mono);cursor:pointer}
html.m #msheet-body .mrow:active,html.t #msheet-body .mrow:active{background:#13213a}
html.m #mtoast.on{display:block;position:fixed;z-index:47;left:50%;bottom:calc(var(--B) + var(--P) + 16px);transform:translateX(-50%);max-width:calc(100vw - 32px);padding:8px 16px;background:#0b1428f0;border:1px solid #344c6b;border-left:3px solid #ffd166;color:#c8dcf5;font:600 var(--fs-body)/var(--lh-body) var(--font-mono);pointer-events:none}
html.m.land #mtoast.on{left:calc(50% - var(--railw) / 2)}
/* touch targets: nothing smaller than 44 px inside a card, the board, a thread or Today */
html.m :is(#panel,#side,#today,#quests,#prompter,#legend,#msheet) :is(button,[role=button],summary,select,.trow,a.go,tr.frow){min-height:44px;min-width:44px}
html.m :is(#panel,#side,#today,#quests,#prompter,#legend) input:not([type=checkbox]):not([type=radio]){min-height:44px}
html.m #panel .cprob,html.m #panel .crow{min-height:44px}
/* the mono palette keeps its four greens on the new pieces */
html.m body.gb #tabs{background:#0f380f;border-color:#306230}
html.m body.gb #tabs button{color:#8bac0f}
html.m body.gb #tabs button[aria-selected=true]{color:#9bbc0f;box-shadow:inset 0 3px 0 #9bbc0f}
html.m body.gb #mfab{background:#9bbc0f;border-color:#0f380f;color:#0f380f;box-shadow:3px 3px 0 #306230}
html.m body.gb #mzoom button{background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #hud{background:#0f380f;border-bottom-color:#306230}
html.m body.gb #hud .sc::after{color:#8bac0f}
html.m body.gb #hud #mmore{border-color:#306230;color:#9bbc0f}
html.m body.gb #nums button,html.m body.gb #nums button b{background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #pickbar{background:#9bbc0f;border-color:#0f380f;color:#0f380f}
html.m body.gb #pickbar .pickx{border-color:#306230;color:#0f380f}
html.m body.gb #mtoast.on{background:#0f380f;border-color:#306230;border-left-color:#9bbc0f;color:#9bbc0f}
html.m body.gb #mclose{background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #today,html.m body.gb #side{background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #today *,html.m body.gb #side *{color:inherit}
html.m body.gb #today :is(h1,b,a.go,button.tnew){color:#9bbc0f}
html.m body.gb #today :is(h2,.gen,.mut,.th){color:#8bac0f;border-color:#306230}
html.m body.gb #today :is(.it,.lane){background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #today button.tnew{background:#0f380f;border-color:#9bbc0f}
html.m body.gb #side .shead{border-color:#306230}
html.m body.gb #side :is(.shead b,.shead span,.shead button,#tasks .trow b,#tasks .trow small){color:#9bbc0f}
html.m body.gb #side .shead button{background:#0f380f;border-color:#306230}
html.m body.gb #side #tasks .trow{background:#0f380f;border-color:#306230}
html.m body.gb #side #tasks .trow.sel{background:#306230;border-left-color:#9bbc0f}
html.m body.gb #side .shead #snew{background:#0f380f;border-color:#9bbc0f;color:#9bbc0f}
html.m body.gb #side :is(#composer,#tbody,.tvh,.tvpane,.tvfoot,#composer .chk){background:#0f380f;border-color:#306230}
html.m body.gb #side :is(textarea,input,select,#composer button,.tvfoot button,.tvtabs button){background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #side #composer .cbtns{background:#0f380f;border-top-color:#306230}
html.m body.gb #side #composer button.go{background:#9bbc0f;border-color:#0f380f;color:#0f380f}
html.m body.gb #side :is(.tv-msg,.tv-acts,.tl-more button){background:#0f380f;border-color:#306230;color:#9bbc0f}
html.m body.gb #side .tv-msg.you{background:#306230}
html.t #msheet{display:flex;flex-direction:column;position:fixed;z-index:45;left:50%;bottom:0;width:min(520px,100vw);max-height:70dvh;background:#0b1428;border:2px solid #344c6b;border-bottom:0;visibility:hidden;transform:translate(-50%,100%);transition:transform .16s ease,visibility 0s linear .16s}
html.t #msheet.on{visibility:visible;transform:translate(-50%,0);transition:transform .16s ease,visibility 0s}
html.m body.gb #msheet{background:#9bbc0f;border-color:#0f380f;color:#0f380f}
html.m body.gb #msheet-body .mrow{border-color:#306230;color:#0f380f}
/* a tablet keeps the desktop layout and gains the touch controls */
html.t #mzoom{position:absolute;z-index:4;right:16px;bottom:56px;display:flex}
html.t #wrap.panel-open #mzoom,html.t #wrap.side #mzoom,html.t #today.on~#mzoom{display:none}
html.t #panel:not(.on){visibility:hidden}
html.t #panel{left:auto;right:var(--sidew)}   /* the card sits on the right as on a desktop; the touch rule above pinned it to the left edge, over the planet the camera had just centred */
html.t #mate{bottom:200px}   /* above the fly pad and the quick keys, which a touch screen shows at the bottom left: under the bubble the arrows did nothing */
@media (max-width:1000px){
  html.t #wrap.side #mate{display:none}   /* the thread takes the whole width: its reply box and Mark done sit where the bubble would be */
}
html.t #mate :is(button,[role=button]){min-height:44px;min-width:44px}   /* hide and send were 18 and 32 px high: a finger missed them */
html.t #mate #mateq{min-height:44px}
html.t :is(#panel,#side,#today,#quests,#prompter,#msheet) :is(button,[role=button],summary,select,.trow,a.go,tr.frow){min-height:44px;min-width:44px}
html.t :is(#panel,#side,#today,#quests,#prompter) input:not([type=checkbox]):not([type=radio]){min-height:44px}
html.t #composer .cchk label,html.t #composer .chk{display:flex;align-items:center;min-height:44px;min-width:44px}
html.t :is(input,textarea,select):not(#_):not(#__){font-size:16px}
/*__CM_CSS__*/
</style><style id="cm-tac">__TACCSS__
</style><style id="cm-fonts">__FONTFACE__</style></head><body><div id="wrap"><aside id="dash" aria-label="Dashboard"></aside><div id="stage"><canvas id="c"></canvas></div><div id="today" aria-label="Today"></div>
<aside id="rail" data-wall="rail" aria-label="For you, agents and workflows"><div id="railtabs" role="tablist" aria-label="Side panel"></div><button type="button" id="railnew" class="cmbtn" data-act="newtask" title="New task (N)">+ Task</button><div id="railbody" role="tabpanel"></div><div id="railcard" hidden><div class="rchead"><button type="button" id="railback" data-act="back">&larr; For you</button><kbd class="rkey">Esc</kbd></div><div id="railcardbody"></div></div></aside>
<div id="statusbar" data-wall="statusbar" aria-label="Status"><button type="button" id="sb-counts" aria-label="Open For you"></button><div id="sb-mid"><div id="sb-readout"></div><div id="sb-route" hidden></div></div><div id="sb-right"><button type="button" class="sbbtn" data-act="fly3d" title="Fly in 3D (I)" aria-label="Fly in 3D"><svg width="16" height="12" viewBox="0 0 16 12" aria-hidden="true"><path d="M15 6 L6 1.5 L4 5 L0.5 3.5 L2 6 L0.5 8.5 L4 7 L6 10.5Z" fill="currentColor"/></svg><span class="fw">Fly in 3D</span></button><div id="sb-ask"></div><button type="button" id="sb-help" aria-label="Help, keys and settings" title="Help, keys and settings (?)">?</button></div></div>
<div id="machines" class="mchip" data-wall="machines" role="group" aria-label="Machines" hidden></div><div id="legendchip" class="mchip" data-wall="legendchip" role="note" aria-label="What the map shows" hidden></div><button type="button" id="crumb" class="mchip" data-wall="crumb" hidden></button><div id="pressi" class="mchip" data-chip="press-i" role="status" hidden>Press <kbd>I</kbd> to fly in 3D</div>
<div id="helpsheet" role="dialog" aria-modal="true" aria-label="Help, keys and settings" hidden></div><div id="help" hidden></div>
<div id="a11ymap" role="list" aria-label="The map as a list"></div><div id="a11ylive" class="srlive" role="status" aria-live="polite"></div>
<div id="hud"><h1>CONTAINER</h1><div class="sc" data-count="decisions">0<small>decisions</small></div><div class="hl"><span id="themeline" title="V cycles the theme and texture pack"></span><span id="zoomtag" title="wheel or - / = zooms · 0 resets"></span></div><div id="hudgen" class="hfull">world rebuilt __GEN__</div><div id="sector" class="hfull"></div><div id="mline" aria-label="What waits on you"></div><button type="button" id="m3d" data-chip="3d" aria-label="Fly in 3D"><svg viewBox="0 0 16 12" aria-hidden="true"><path d="M15 6 L6 1.5 L4 5 L0.5 3.5 L2 6 L0.5 8.5 L4 7 L6 10.5Z"/></svg>3D</button><button type="button" id="mmore" aria-label="More">More</button></div>
<nav id="pov-switch" aria-label="Point of view" hidden><button type="button" data-pov="map" aria-pressed="true">2D map</button><button type="button" data-pov="cockpit" aria-pressed="false">Cockpit</button><button type="button" data-pov="chase" aria-pressed="false">Behind ship</button></nav>
<div id="quick" aria-label="Quick seats"></div><div id="theme"><button type="button" id="gbbtn" title="Toggle mono palette">MONO</button></div><div id="taskbar"></div>
<div id="comms" aria-label="Agents"><div class="ct" role="button" tabindex="0" id="ctoggle"><b>AGENTS</b><span id="csum"></span></div><div id="cbody"></div></div>
<div id="ticker" aria-label="Recent and status"><span class="tk">RECENT</span><div id="tkbody"></div><div id="nums" aria-label="Status"></div><button type="button" id="hmore" title="H: more stats">More stats</button></div>
<div id="permits" aria-label="Seats waiting on you"></div>
<div id="radio" role="button" tabindex="0" aria-live="polite" aria-label="A seat calls in: open it"><canvas id="radioc" width="96" height="96"></canvas><div class="rt"><div class="rh"><b id="radion"></b><small id="radiow"></small></div><div class="rl" id="radiol"></div></div></div>
<button type="button" id="gohome" hidden title="Return home (Home / Shift+H / Esc)">⌂ HOME</button>
<div id="mate" aria-label="your assistant"><div class="mp"><canvas id="matec" width="104" height="104"></canvas><b id="matename">__MATENAME__</b></div><div class="nb"><div class="nh"><button type="button" id="matepill" hidden></button><span id="matemodel"></span><button type="button" id="matealso" hidden></button><button type="button" id="matehide" title="hide (B brings it back)">hide</button></div><div id="matetxt"></div><form id="matef" title="__MATETITLE__"><input id="mateq" maxlength="500" enterkeyhint="send" placeholder="__MATEHINT__" __MATEDISABLED__ autocomplete="off" spellcheck="false"><button type="submit" __MATEDISABLED__>send</button></form></div></div><button type="button" id="mateshow" hidden title="show __MATENAME__ (B)">Show __MATENAME__</button>
<div id="touchpad" aria-label="Movement controls"><button type="button" data-dir="up" aria-label="Move up">&#8593;</button><button type="button" data-dir="left" aria-label="Move left">&#8592;</button><button type="button" data-dir="down" aria-label="Move down">&#8595;</button><button type="button" data-dir="right" aria-label="Move right">&#8594;</button></div>
<aside id="panel" aria-hidden="true"><button type="button" id="pgrip" aria-label="Resize the card"></button><button type="button" class="close" id="pclose" aria-label="Close">esc</button><div id="pbody"></div></aside>
<div id="legend"></div>
<div id="prompter"><h2>PROMPT</h2><div class="sub" id="ptarget"></div><textarea id="ptext" maxlength="3800" placeholder="what should be done here? It lands as a draft in the agent you pick, with the folder attached. You press send."></textarea><div class="prow"><select id="pseat"></select><button type="button" id="pgo">Draft to agent</button><button type="button" id="pcancel">cancel</button><span class="saym" id="pmsg"></span></div></div>
<aside id="side" aria-label="Agents"><div class="shead"><button type="button" id="sback" aria-label="Back to the agents">&lsaquo; Agents</button><b>AGENTS</b><span id="scount"></span><button type="button" id="snew">+ New task</button><button type="button" id="sclose" title="close the rail (])">×</button></div><div id="composer" role="form" aria-label="New task"><h2>New task <span id="ctarget"></span></h2><div class="sub">Say what to do. Click a planet to aim it. In a repo the agent works on a safe copy; you look at the changes, then accept or reject them (the copy is kept), reply, or ask another agent for a second opinion.</div><div class="connect" id="cconnect" hidden><b>Connect an agent</b>Install Claude Code or Codex on this computer and the map finds it by itself; to name one, see ? › Set up. Then come back here to start the task.</div><textarea id="ctext" maxlength="8000" placeholder="What should the agent do?  e.g. add a dark-mode toggle to the settings page and test it"></textarea><div class="cfield cwide"><label for="cfolder">Folder</label><input type="text" id="cfolder" list="cfolders" spellcheck="false" autocomplete="off" autocapitalize="off" placeholder="click a planet to aim"><button type="button" id="cpick">Pick on map</button></div><div class="crow r1"><div class="cfield"><label for="crunner">Agent</label><select id="crunner"></select></div><div class="cfield"><label for="cmodel">Model</label><select id="cmodel"></select></div><div class="cfield cchk"><label for="ciso">Safe copy</label><span class="chk"><input type="checkbox" id="ciso" checked><span>its own</span></span></div></div><button type="button" id="cadv" aria-expanded="false">Advanced</button><div class="crow r2"><div class="cfield" title="one shell line run in the task's fresh safe copy before the agent starts (first run only); if it fails, no agent is started"><label for="csetup">Setup</label><input type="text" id="csetup" maxlength="400" spellcheck="false" autocomplete="off" autocapitalize="off" placeholder="e.g. npm ci"></div><div class="cfield" title="one shell line that must exit 0 in the task's folder; with retries the agent is sent back with the failure until it passes"><label for="cverify">Test that must pass</label><input type="text" id="cverify" spellcheck="false" autocomplete="off" autocapitalize="off" placeholder="e.g. python3 -m unittest -q"></div><div class="cfield cnarrow"><label for="cretries">Retries</label><select id="cretries"><option value="0">none</option><option value="1">1</option><option value="2" selected>2</option><option value="3">3</option></select></div></div><datalist id="cfolders"></datalist><div class="cbtns"><button type="button" id="cgo" class="go">Start task</button><button type="button" id="ccancel" class="txt">cancel</button><span class="saym" id="cmsg"></span></div></div><div id="tasks" aria-label="Agents and tasks"></div><div id="tbody"></div></aside>
<div id="quests"><h2>Decisions</h2><div class="sub">Left: decisions only you can make. Right: small jobs an agent can do in the background; what it did appears under the row.</div><div id="qbody"></div></div>
<div id="meta" role="dialog" aria-label="Meta board"><h2>META BOARD</h2><div class="sub">One chat that sees every task, T3 thread, agent window and git worktree, and says what to do with each. It only advises: merging, closing, stopping and removing a worktree stay your clicks. U toggles it, Esc closes it, and a run goes on while it is closed.</div><div class="mbar"><button type="button" id="metamodel" hidden></button><button type="button" id="metago" class="go">Triage all</button><form id="metaform"><input id="metaq" maxlength="500" placeholder="Or ask it something: which of these touch the 3D code?" autocomplete="off" spellcheck="false"><button type="submit">Ask</button></form><span id="metastat" class="mstat"></span></div><div id="metachat" class="mchat"></div><div id="metaboard"></div><div id="metamiss" class="mmiss"></div></div>
<div id="pgdock" aria-label="Pages agents made"></div><div id="pgtoast" role="status" aria-live="polite"></div>
<div id="pgpanel" role="dialog" aria-label="A page an agent made"><div class="pgh"><div id="pghead"></div><button type="button" id="pgclose" aria-label="Close the page">close</button></div><div id="pgtabs"></div><div id="pgframe"></div></div>
<button type="button" id="mfab" aria-label="New task" data-wall><b aria-hidden="true">+</b>Task</button>
<div id="mzoom" aria-label="Zoom"><button type="button" id="mz-in" aria-label="Zoom in">+</button><button type="button" id="mz-out" aria-label="Zoom out">&minus;</button><button type="button" id="mz-c" aria-label="Recenter on the ship">&#9678;</button></div>
<div id="pickbar" role="status"><span>Tap a planet or folder to aim the task</span><button type="button" class="pickx">Cancel</button></div>
<button type="button" id="mclose" aria-label="Close">&times;</button>
<div id="scrim"></div>
<div id="msheet" role="dialog" aria-modal="true" aria-labelledby="msheet-title"><div class="mshead"><b id="msheet-title">More</b><button type="button" id="msheet-x" aria-label="Close">&times;</button></div><div id="msheet-body"></div></div>
<div id="mtoast" role="status" aria-live="polite"></div>
<nav id="tabs" role="tablist" aria-label="Views" data-wall><button type="button" role="tab" id="tab-today" data-tab="today" aria-selected="false"><svg class="ti" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.6M12 18.9v2.6M2.5 12h2.6M18.9 12h2.6M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/></svg><span class="tl">Today</span><i id="tabn-today" class="tabn" hidden></i></button><button type="button" role="tab" id="tab-map" data-tab="map" aria-selected="false"><svg class="ti" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="5.5"/><ellipse cx="12" cy="12" rx="10" ry="3.6" transform="rotate(-18 12 12)"/></svg><span class="tl">Map</span></button><button type="button" role="tab" id="tab-threads" data-tab="threads" aria-selected="false"><svg class="ti" viewBox="0 0 24 24" aria-hidden="true" fill="currentColor"><path d="M22 12 L9 5 L6.5 10 L2 8 L4 12 L2 16 L6.5 14 L9 19Z"/></svg><span class="tl">Agents</span><i id="tabn-threads" class="tabn" hidden></i></button><button type="button" role="tab" id="tab-wingmate" data-tab="wingmate" aria-selected="false"><svg class="ti" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M4 5h16v11H10l-4.5 3.5V16H4z"/></svg><span class="tl" id="tabl-wingmate"></span><i id="tabn-wingmate" class="tabn dot" hidden></i></button></nav>
</div><script id="cm-sfx">__SFX__</script>__CM3D_INLINE__<script>
const W = __WORLD__;
const WORDS = __WORDS__;
// ---- SEAMS (2026-10-12): the one registry the ui/*.js lanes and the 3D view plug into. Every default below keeps the page
// exactly as it was; a lane replaces the function behind a hook (CM.x=...), never the hook line in the page. ui/*.js are
// inlined at the marker below (words, kinds, labels, focus, chrome, phone, workflows2d, a11y: they declare functions and
// assign into CM, nothing else at load), ui/*.css at the end of the main stylesheet; start-up code goes in CM.onInit (run
// once, after the page booted). What each hook is asked, and where:
//   counts()            W.counts, sky.counts(): need_you, answer, review, decisions, problems, working, on_hold, ...
//   word(k)             the plain word for k (WORDS, words.py), else k
//   labelText(uid)      the one name a planet, moon or craft carries, on 2D plates and 3D labels
//   inFocus(m)          false: the planet's problem craft are not drawn (draw(): drawBattle, SKY_STAR too)
//   detailHidden(u)     true: the unit (a moon, a cron, a process) is not drawn (both unit loops in draw())
//   focusUid()          the uid of the planet in focus, else null
//   drawWorkflows2D(t)  after drawSystem, in world space
//   onTasks(j)          every /tasks reply, after the threads in it were applied (applyTasks); j.world: the threads came
//                       with a /state reply (applyWorld). The default keeps j.workflows / j.shows (arrays only) in
//                       W.workflows / W.shows. Code that changes them anywhere else (the /workflows poll) calls
//                       cmSceneTouch() after, so cmWorld().rev moves
//   dockOverride(u)     an area uid an agent docks at instead of the planet of its folder (layoutSystem), else null
//   action(a)           a /control action pumpActions does not know: true when it was taken
//   stripSet(slot,v)    a slot of the status bar;  railCard(kind,x)  a card in the right rail
//   backLayer(how)      a layer of the lanes' own: 'has' -> one is up (mobLayers); 'back' (mobBack) or 'esc' (Escape) ->
//                       close the top one, true when it did. Never replaced: a lane pushes {pri, fn} into backLayers in
//                       CM.onInit (3D 30, phone sheet 20, workflow selection and replay 10) and the highest pri whose
//                       fn(how) is true answers; a fn that throws counts as false
//   key3d(e)            every keydown first (a capture listener ahead of all the page's own): true takes the key
//   mode3d()            true while the 3D view owns the screen: frame() skips the 2D idle throttle and the canvas
//                       governor, step() runs the same flight and calls frame3d(t,dt) instead of draw(t), and a
//                       static page does not reload itself
// the seam defaults, which change nothing until a module uses them:
//   settingsExtra       rows {key,label,options,get,set,group} the "?" > Settings sheet lists after its own
//   drawBackdrop2D(cx)  at the top of draw(), after the background fill, before the nebula and the star field (screen space)
//   nebulaRecipe()      {seed, layers, colours} of the sky the 2D backdrop and the 3D sky share, else null
//   haptic(kind)        a phone vibration for 'tap' | 'done' | 'fail' | 'needs'
//   sceneClock          {scale, t}: how fast the world's own motion runs (replay x10, pause 0) and its clock
//   wfList() showList() the workflows and the agents' shows on the page (the /tasks reply's, through onTasks)
//   selectedWorkflow()  the id of the workflow the owner selected, else null;  selectWorkflow(id) selects (null clears)
//   workingPlanets()    a Set of the planet uids a working workflow step or a working thread (TASK_WORKING) is at
//   wfRows()            one plain row per workflow {id,title,done,total,state,place,sentence,step}: done = steps done,
//                       step = the step at work, 1-based ("2 of 4"), place = where it is (the step's place name)
//   labelChip(uid)      the text a planet's label carries after its name ("Fix labels · 2 of 4"), else ''
//   enter3d(opts)       into the 3D view (the 3D module implements it)
//   constellations()    root conversation groups with short display titles
const CM={
  constellations:()=>(W.constellations||[]).map(g=>({...g,threads:(g.threads||[]).map(t=>({...t,title:CM.shortTitle(t.title,40)}))})),
  counts:()=>W.counts||{},
  word:k=>(Object.prototype.hasOwnProperty.call(WORDS,k)&&WORDS[k])||k,
  labelText:uid=>{ const m=MOONS.find(x=>x.uid===uid)||unitByUid(uid); return m?String(m.label||m.name||uid):String(uid==null?'':uid); },
  inFocus:()=>true,
  detailHidden:()=>false,
  focusUid:()=>null,
  drawWorkflows2D(){},
  onTasks(j){ if(j&&Array.isArray(j.workflows)) W.workflows=j.workflows; if(j&&Array.isArray(j.shows)) W.shows=j.shows; },
  dockOverride:()=>null,
  action:()=>false,
  stripSet(){},
  railCard(){},
  backLayer:k=>CM.backLayers.slice().sort((a,b)=>b.pri-a.pri).some(l=>{ try{ return !!l.fn(k); }catch(e){ return false; } }),
  key3d:()=>false,
  mode3d:()=>false,
  frame3d(){},
  onInit:[],
  settingsExtra:[],
  drawBackdrop2D(){},
  nebulaRecipe:()=>null,
  haptic(){},
  sceneClock:{scale:1,t:0},
  wfList:()=>W.workflows||[],
  showList:()=>W.shows||[],
  selectedWorkflow:()=>CM._wfSel||null,
  selectWorkflow(id){ CM._wfSel=id||null; cmSceneTouch(); },
  workingPlanets:()=>{ const s=new Set();
    CM.wfList().forEach(w=>(w&&Array.isArray(w.steps)?w.steps:[]).forEach(st=>{ if(st&&st.state==='working'&&typeof st.planet_uid==='string'&&st.planet_uid) s.add(st.planet_uid); }));
    const areas=MOONS.map(m=>({uid:m.uid,path:String((unitByUid(m.uid)||{}).path||'')})).filter(a=>a.path).sort((a,b)=>b.path.length-a.path.length);
    agentTasks().forEach(t=>{ if(!t||!TASK_WORKING.includes(t.status)) return; const p=String(t.target||''), a=p&&areas.find(x=>p===x.path||p.startsWith(x.path+'/')); if(a) s.add(a.uid); });
    return s; },
  wfRows:()=>CM.wfList().filter(w=>w&&typeof w==='object').map(w=>{ const steps=(Array.isArray(w.steps)?w.steps:[]).filter(x=>x&&typeof x==='object'), cur=Array.isArray(w.current)?w.current:[];
    const at=steps.find(x=>cur.includes(x.key))||steps.find(x=>x.state==='working'||x.state==='needs_you')||steps.slice().reverse().find(x=>x.state==='done')||steps[0]||null;
    return {id:String(w.id||''), title:String(w.title||''), done:Number.isInteger(w.done)?w.done:steps.filter(x=>x.state==='done').length,
      total:Number.isInteger(w.total)?w.total:steps.length, state:String(w.state||''),
      place:at?String(at.place||(at.planet_uid?CM.labelText(at.planet_uid):'')):'', sentence:String(w.sentence||''), step:at?steps.indexOf(at)+1:0}; }),
  labelChip:uid=>'',
  enter3d(opts){},
  backLayers:[],
};
/*__CM_UI__*/
// cmWorld(): the scene the 3D view reads, page facts only, 2D (x, y) in world px. v1: the star, the planets,
// the craft and the ship are real; moons, foes, waiting, machines and shows stay empty until their lanes fill them.
// rev moves when the scene's data changed, never with the clock: cmSceneTouch() runs where a /state reply (applyWorld)
// or a /tasks reply (applyTasks, after CM.onTasks) has landed, and where the owner selects a workflow (CM.selectWorkflow),
// and bumps rev only when cmSceneSig() differs, so a module rebuilds and an effect fires only then. The
// signature reads the workflows, the shows and the selection through CM, so the lane that owns them keeps rev honest.
// The signature holds every fact cmWorldParts() reads: each room's size, age, files and visibility (moons' bytes, days
// and purpose), each planet's own problem craft and items, the machines, and each contact's title and age, not only
// the uids; the machines' places too (a layout that moves them moves nothing else when the roots ring keeps its size).
// cmWorld() also signs again at most once a second, so a fact changed where no reply touched lands within 1 s.
// Never a position that moves with the clock: a moon circles its planet every frame (its room's gx/gy), so the moons'
// x/y are set on each cmWorld() call (cmMoonsAt) and stay out of the signature.
const CM_WORLD={sig:null,rev:0,at:0};
function cmSceneSig(){
  const row=r=>Array.isArray(r)?[String(r[0]||''),r[2]==null?'':String(r[2]),Number(r[4])||0]:String(r);
  return JSON.stringify([String(W.name||''), LY.star.r,
    MOONS.map(m=>[m.uid,String(m.label),m.kind,m.life,m.r,m.x,m.y,m.bloat||0,m.allies||0,
      (m.rooms||[]).map(r=>r?[r.uid,String(r.name||''),r.size==null?'':String(r.size),r.age==null?null:r.age,r.hidden?1:0,(r.children||[]).map(row)]:null),
      (m.foes||[]).map(f=>[String(f.uid||''),f.kind||'',f.tier||'',String(f.name||f.full||''),f.contact?String(f.contact.id):null])]),
    units.filter(u=>isShip(u)&&!u.remote).map(u=>[u.uid,String(u.status||''),String(u.alias||u.name||''),u.docked_at||null]),
    units.filter(u=>u.kind==='machine').map(u=>{ const q=LY.planets&&LY.planets[u.name]; return [u.uid,String(u.name||''),String(u.status||''),q?q.x:null,q?q.y:null]; }),
    skyContacts().map(c=>[c.id,c.state,c.planet_uid||null,c.source,skyTier(c),String(c.title||''),Number(c.age_days)||0]),
    taskSig(agentTasks()), CM.wfList(), CM.showList(), CM.selectedWorkflow(), W.constellations||[], W.counts||{}]);
}
function cmSceneTouch(){ const sig=cmSceneSig(); CM_WORLD.at=performance.now(); if(sig!==CM_WORLD.sig){ CM_WORLD.sig=sig; CM_WORLD.rev++; } return CM_WORLD.rev; }
function cmWorld(){
  if(CM_WORLD.sig===null||performance.now()-CM_WORLD.at>=1000) cmSceneTouch();
  const tasks=agentTasks(), part=cmWorldParts(); cmMoonsAt();
  const ship=FL||{}, mom=mothership(), letters=wingLetters();
  const working=units.filter(u=>isShip(u)&&!u.remote&&(u.status==='WORKING'||u.status==='working')).length+tasks.filter(t=>TASK_WORKING.includes(t.status)).length;
  const wf=CM.wfList(), shows=CM.showList();
  return {v:1, rev:CM_WORLD.rev, t:performance.now(), skin:String((SKIN&&SKIN.name)||'neutral'), theme:document.documentElement.getAttribute('data-theme')||'',
    look:VEC?'vector':'pixel', reduced:!!REDUCED, focus:CM.focusUid(), selectedWorkflow:CM.selectedWorkflow(), light2d:[-0.5,0.74,-0.45],
    star:{x:LY.star.x,y:LY.star.y,r:LY.star.r,label:String(W.name||'CONTAINER'),load:working},
    planets:part.planets, moons:part.moons, foes:part.foes, waiting:part.waiting, machines:part.machines,
    craft:units.filter(u=>isShip(u)&&!u.remote).map(u=>({uid:u.uid,role:u===mom?'carrier':'agent',label:String(u.alias||u.name||u.uid),
      letter:letters[u.uid]||'',colour:shipColor(u),state:String(u.status||'').toLowerCase(),planet:u.docked_at||null,x:u.gx*TILE,y:u.gy*TILE,hdg:shipAngle(u)})),
    workflows:Array.isArray(wf)?wf:[], shows:Array.isArray(shows)?shows:[], counts:CM.counts(),
    constellations:CM.constellationLayout(), sky:CM.nebulaRecipe(), palette:part.palette,
    ship:{x:px,y:py,hdg:ship.hdg,spd:ship.spd,bank:ship.bank,roll:!!ship.roll,uturn:!!ship.uturn,boost:!!ship.boost,brake:!!ship.brake,meter:ship.meter,
      flight3d:!!P3.flight,alt:P3.flight?P3.flight.y:null,pitch:P3.flight?P3.flight.pitch:0,locked:!!FL.locked,rate:FL.rate||0,tilt:FL.tilt||0,
      rollDir:FL.roll?FL.roll.dir:0,
      velocity:P3.flight?[P3.flight.vx,P3.flight.vy,P3.flight.vz]:null}};
}
// the parts of the scene that move only with its data, built once per rev (the 3D view asks for cmWorld() every frame).
// planets: purpose and counts by the same buckets as sky.counts(); moons: each planet's sub-folders (dot-folders never
// drawn) with a purpose guessed from the extensions of their files, as the planets' own guess does; foes: the problems
// circling a planet (failed or stale contacts, failing crons, failed threads), waiting: what waits on the owner there
// (a decision, an answer, a review); machines: the owner's other machines. palette: purpose -> [r,g,b].
const CM_PURPOSE_OF={code:'code',knowledge:'notes',reference:'reference',archive:'archive',config:'config',shared:'shared',bus:'shared',data:'data',media:'media'};
const CM_PURPOSE_HEX={code:'#4e79c8',notes:'#3aa0b8',reference:'#c8a060',data:'#d08a3c',media:'#3fd6a0',config:'#7ec8f0',shared:'#8fa8c0',archive:'#8a6a40',unsorted:'#8a93a6'};
const CM_EXT={data:'csv tsv json jsonl ndjson parquet xlsx xls ods sqlite sqlite3 db npy npz h5 hdf5 feather arrow avro pkl',
  media:'png jpg jpeg gif webp svg bmp tif tiff heic psd mp4 mov webm mkv avi m4v mp3 wav flac ogg m4a aac opus',
  notes:'md markdown txt rst org pdf doc docx odt rtf tex',
  code:'py ts tsx js jsx mjs cjs sh bash go rs c h cpp java rb lua sql toml yaml yml html css scss vue svelte php kt swift cs dart ipynb'};
const CM_EXT_OF={}; Object.keys(CM_EXT).forEach(k=>CM_EXT[k].split(' ').forEach(e=>{ CM_EXT_OF[e]=k; }));
function cmGuessPurpose(rows){
  const n={data:0,media:0,notes:0,code:0}; let files=0;
  (Array.isArray(rows)?rows:[]).forEach(r=>{ if(!Array.isArray(r)||(Number(r[4])&1)||r[2]==='') return;   // folders and links are not files
    files++; const name=String(r[0]||''), i=name.lastIndexOf('.'); const k=i>0?CM_EXT_OF[name.slice(i+1).toLowerCase()]:null; if(k) n[k]++; });
  if(!files) return 'unsorted';
  if(n.data/files>=.5) return 'data'; if(n.media/files>=.5) return 'media'; if(n.code/files>=.4) return 'code'; if(n.notes/files>=.5) return 'notes';
  return 'unsorted';
}
function cmBytes(size){ const m=/^([0-9.]+)([BKMG])$/.exec(String(size||'')); return m?Math.round(parseFloat(m[1])*({B:1,K:1024,M:1048576,G:1073741824})[m[2]]):0; }
function cmHexRgb(h){ const m=/^#?([0-9a-f]{6})$/i.exec(String(h||'').trim()); if(!m) return null; const v=parseInt(m[1],16); return [v>>16&255,v>>8&255,v&255]; }
// purpose -> [r,g,b]: the folder-kinds module's PURPOSE colours when it is in (a hex, or {colour|color|base|body}), else ours
function cmPalette(){
  const src=(typeof PURPOSE==='object'&&PURPOSE)?PURPOSE:null, out={};
  Object.keys(CM_PURPOSE_HEX).forEach(k=>{ const v=src&&src[k]; const hex=typeof v==='string'?v:(v&&typeof v==='object'?(v.colour||v.color||v.base||v.body):null);
    out[k]=cmHexRgb(hex)||cmHexRgb(CM_PURPOSE_HEX[k]); });
  return out;
}
const CM_PARTS={rev:-1,v:null,at:[]};
// where each moon is now: its room's place on its orbit (the 2D layout moves it every frame), else its planet's centre
function cmMoonsAt(){ CM_PARTS.at.forEach(q=>{ const o=q[0], r=q[1], m=q[2];
  o.x=r.gx!=null?r.gx*TILE:m.x; o.y=r.gy!=null?r.gy*TILE:m.y; }); }
function cmWorldParts(){
  if(CM_PARTS.rev===CM_WORLD.rev&&CM_PARTS.v) return CM_PARTS.v;
  const DECIDE=['ledger','board','quest','fleet'], per={}, foes=[], waiting=[], at=[];
  const line=c=>{ try{ if(typeof CM.contactLine==='function'){ const t=CM.contactLine(c); if(t) return String(t).slice(0,90); } }catch(e){}
    return String((c&&(c.title||c.id))||'').split('\\n')[0].slice(0,90); };
  skyContacts().forEach(c=>{ const k=c.planet_uid; if(!k) return; const n=per[k]=per[k]||{need_you:0,problems:0,decisions:0,answer:0,review:0};
    if(DECIDE.includes(c.source)) n.decisions++; else if(c.state==='failed'||c.state==='stale') n.problems++; else { n.need_you++; if(c.state==='blocked') n.answer++; else if(c.state==='review'||c.state==='ready') n.review++; } });
  MOONS.forEach(m=>(m.foes||[]).forEach(f=>{ const c=f.contact, urgency=f.tier||(f.kind==='wreck'||f.kind==='cron'?'raider':'fresh');
    if(c){ const kind=DECIDE.includes(c.source)?'decision':(c.state==='failed'||c.state==='stale')?'problem':'need_you';
      (kind==='problem'?foes:waiting).push({uid:String(c.id),parent:m.uid,kind:kind==='problem'?'contact':kind,source:String(c.source||''),label:line(c),urgency:skyTier(c),days:Number(c.age_days)||0}); }
    else if(f.kind==='item') waiting.push({uid:String(f.uid),parent:m.uid,kind:'decision',source:'board',label:String(f.full||f.name||'').slice(0,90),urgency:urgency,days:0});
    else foes.push({uid:String(f.uid),parent:m.uid,kind:f.kind,source:f.kind,label:String(f.name||'').slice(0,90),urgency:urgency,days:0}); }));
  const v={palette:cmPalette(), foes:foes, waiting:waiting,
    planets:MOONS.map(m=>{ const a=unitByUid(m.uid), purpose=CM_PURPOSE_OF[m.kind]||'unsorted', n=per[m.uid]||{need_you:0,problems:0,decisions:0,answer:0,review:0}, conditions=[];
      if(m.life==='COLD') conditions.push('quiet'); if(m.life==='EMPTY'||m.life==='ALIAS') conditions.push('empty');
      if((m.bloat||0)>=50) conditions.push('rebuildable'); if(m.allies>0) conditions.push('working');
      return {uid:m.uid,label:String(m.label),x:m.x,y:m.y,r:m.r,purpose:purpose,glyph:purpose,conditions:conditions,seed:seedOf((a&&a.path)||m.label),
        counts:{need_you:n.need_you,problems:n.problems,decisions:n.decisions,answer:n.answer,review:n.review},
        health:n.problems?'broken':(n.need_you||n.decisions)?'waiting':m.life==='COLD'?'quiet':'ok'}; }),
    moons:[].concat(...MOONS.map(m=>(m.rooms||[]).filter(r=>r&&r.uid&&!r.hidden&&!String(r.name||'').startsWith('.')).map(r=>{
      const o={uid:String(r.uid),parent:m.uid,label:String(r.name||r.uid),purpose:cmGuessPurpose(r.children),bytes:cmBytes(r.size),
        days:Number.isFinite(Number(r.age))&&r.age!=null?Number(r.age):null,x:m.x,y:m.y};
      at.push([o,r,m]); return o; }))),
    machines:units.filter(u=>u.kind==='machine'&&LY.planets&&LY.planets[u.name]).map(u=>({uid:u.uid,label:String(u.name),x:LY.planets[u.name].x,y:LY.planets[u.name].y,
      state:String(u.status||'').toLowerCase()}))};
  CM_PARTS.rev=CM_WORLD.rev; CM_PARTS.v=v; CM_PARTS.at=at; cmMoonsAt(); return v;
}
// ---- THE 3D VIEW, page side. The view itself is world3d/*.js plus three.js, two scripts vendor3d.py names by
// their digest; nothing of it is in this page. The first entry (key I, CM.enter3d from a button, a card or the phone chip)
// loads them with this script's nonce (the page's CSP allows nothing else): from the bridge's /vendor/ address, from the
// folder beside a page on disk, or out of the page's own text/plain holders (three_inline). The view then owns the screen
// (CM.mode3d): step() still flies the ship (flightStep, fleetTick: px, py and FL stay the truth) and calls CM.frame3d,
// the 2D frame is not drawn, a static page does not reload. Leaving hands the screen back at the same zoom and place.
// No WebGL 2, files that do not load, or a lost graphics context twice: one sentence, and the map stays 2D.
// Page facts reach the view only through PAGE3D below (the modules never read page globals).
// Parameters (the query, or the fragment, which the phone listener allows): scene3d=1 [seed=N] opens the view at load;
// demo3d=<name> [angle, px, region] opens one effect alone; perf3d=1 the numbers overlay; probe=1 keeps the drawing
// buffer; hold=match holds the first frame (the 2D frame rebuilt in 3D).
const NONCE=(document.currentScript&&document.currentScript.nonce)||'';
const V3D=__V3D__;
const P3={mode:'off', reason:'', sentence:'', loading:null, disabled:false, hold:false, paused:false, sayT:0, stepBase:null, stepAt:-Infinity};
const P3_CRUISE=200, P3_BRAKE=110;      // u/s in 3D: the ship always flies
const P3_SENTENCE={
  'no-webgl2':"3D needs WebGL 2, which this browser doesn't have. The map stays 2D.",
  'bundle-error':"The 3D view's files did not load. The map stays 2D.",
  'not-built':"This page was made without the 3D view's files. The map stays 2D.",
  'lost-once':"3D lost its graphics context, so the map is back in 2D. Press I to fly in 3D again.",
  'context-lost':"3D lost its graphics context twice. The map stays 2D.",
  'error':"The 3D view stopped on an error. The map stays 2D."};
function p3Params(){ const out={};
  const read=q=>String(q||'').replace(/^[?#]/,'').split('&').forEach(kv=>{ if(!kv) return; const i=kv.indexOf('=');
    let k=i<0?kv:kv.slice(0,i), v=i<0?'1':kv.slice(i+1); try{ k=decodeURIComponent(k); v=decodeURIComponent(v); }catch(e){ return; }
    if(/^(scene3d|seed|demo3d|angle|px|region|alpha|perf3d|probe|hold)$/.test(k)) out[k]=v.slice(0,40); });
  try{ read(location.search); read(location.hash); }catch(e){}
  return out; }
const P3_PARAMS=p3Params();
// the sentence lives in the status bar: its own line #say3d in the bottom bar (#ticker), a polite live region
// a screen reader reads, kept until the next try or 30 s; the phone's toast (an aria-live region too) and the canvas
// flash show it as well. It is never cut short: it keeps its whole width (the receipts line beside it gives way), and
// where the bar is too narrow for that (over 60% of it) it wraps onto a second line instead.
function p3Say(text){
  try{ const bar=document.getElementById('ticker'); let el=document.getElementById('say3d');
    if(bar&&!el){ el=document.createElement('span'); el.id='say3d'; el.setAttribute('role','status'); el.setAttribute('aria-live','polite');
      const css={flex:'0 0 auto',maxWidth:'60%',overflow:'hidden',whiteSpace:'normal',lineHeight:'1.15',padding:'0 10px',borderLeft:'3px solid #FFB547',color:'#FFE3A3',fontWeight:'600'};
      Object.keys(css).forEach(k=>{ el.style[k]=css[k]; });
      const nums=document.getElementById('nums'); bar.insertBefore(el,nums&&nums.parentNode===bar?nums:null); }
    if(el){ el.textContent=String(text||''); el.hidden=!text; clearTimeout(P3.sayT); if(text) P3.sayT=setTimeout(()=>{ el.textContent=''; el.hidden=true; },30000); }
  }catch(e){}
  try{ toast(text,7000); }catch(e){} }
function p3Unsay(){ const el=document.getElementById('say3d'); if(el){ el.textContent=''; el.hidden=true; } clearTimeout(P3.sayT); }
function p3Fail(reason,err){
  P3.mode='off'; P3.reason=reason; P3.loading=reason==='bundle-error'?null:P3.loading; P3.hold=false; P3.paused=false;
  P3.sentence=P3_SENTENCE[reason]||P3_SENTENCE.error;
  if(reason==='context-lost'||reason==='not-built'||(reason==='no-webgl2'&&!CM.forceNoGL)) P3.disabled=true;
  if(err) console.warn('3D view:',reason,String((err&&err.message)||err));
  p3Say(P3.sentence);
  p3PovSync();
}
function p3Script(src,holder){ return new Promise((ok,no)=>{ const s=document.createElement('script'); s.nonce=NONCE;
  if(holder){ const h=document.getElementById(holder); if(!h){ no(new Error('not-built')); return; }
    s.textContent=h.textContent; s.dataset.cm3d=holder; document.head.appendChild(s); ok(); return; }
  s.src=src; s.async=false; s.onload=()=>ok(); s.onerror=()=>no(new Error('could not load '+src)); document.head.appendChild(s); }); }
function loadThree(){
  if(window.THREE&&window.CM3D&&typeof CM3D.enter==='function') return Promise.resolve();
  if(P3.loading) return P3.loading;
  if(!V3D||V3D.error||!V3D.three||!V3D.bundle) return Promise.reject(new Error('not-built'));
  const live=!!(window.CM_BRIDGE&&CM_BRIDGE.live), base=live?'/vendor/':String(V3D.dir||'cm3d/');
  P3.loading=(V3D.inline?p3Script(null,'cm3d-three'):p3Script(base+V3D.three))
    .then(()=>V3D.inline?p3Script(null,'cm3d-bundle'):p3Script(base+V3D.bundle))
    .then(()=>{ if(!window.THREE||!window.CM3D||typeof CM3D.enter!=='function') throw new Error('the 3D scripts loaded but did not start'); });
  P3.loading.catch(()=>{ P3.loading=null; });
  return P3.loading;
}
// what the view may know of the page, and the few things it may ask of it
const PAGE3D={
  world:()=>cmWorld(),
  view:()=>{ const r=STAGE&&STAGE.getBoundingClientRect?STAGE.getBoundingClientRect():{left:0,top:0};
    return {cx:px+camOff.x, cy:py+camOff.y, zoom:ZOOM, vw:VW, vh:VH, left:r.left||0, top:r.top||0, panel:PANEL_W||0, sheet:SHEET_CUR||0}; },
  // the 2D truth of the owner's ship; rollDeg: the barrel roll's angle now (degrees, signed by its side), uturnU: how far
  // through the U-turn it is (0-1), else null
  ship:()=>{ const now=p3FlyNow();
    return {x:px, y:py, hdg:FL.hdg, spd:FL.spd, bank:FL.bank, rolling:!!FL.roll, uturn:!!FL.uturn, boost:!!FL.boost, brake:!!FL.brake, meter:FL.meter,
      flight3d:!!P3.flight,alt:P3.flight?P3.flight.y:null,pitch:P3.flight?P3.flight.pitch:0,locked:!!FL.locked,rate:FL.rate||0,tilt:FL.tilt||0,
      rollDir:FL.roll?FL.roll.dir:0,
      rollDeg:FL.roll?FL.roll.dir*clamp((now-FL.roll.t0)/flyK().rollT,0,1)*360:0, uturnU:FL.uturn?clamp((now-FL.uturn.t0)/flyK().uturnT,0,1):null}; },
  place(x,y,hdg){ px=x; py=y; FL.hdg=hdg; heading=hdg; FL.spd=0; FL.roll=null; FL.uturn=null; FL.kb=true; pilot=null; P3.hold=true; P3.flight=null; },
  face(hdg){ FL.hdg=hdg; heading=hdg; },
  flightInput(input){ P3.controls={pitch:clamp(Number(input.pitch)||0,-1,1),level:!!input.level};
    if(P3.controls.pitch||P3.controls.level) P3.hold=false; },
  flightState(){ const f=P3.flight; return f?{speed:f.speed,alt:f.y,climb:f.climb,pitch:f.pitch,throttle:P3.throttle,contact:f.contact,contacts:f.contacts,edge:f.edge,
    velocity:[f.vx,f.vy,f.vz]}:null; },
  // the world's bounds the flight clamps the ship to (stepFlight), in u: the launch never picks a point outside them
  bounds:()=>{ const b=deck==='home'?{minGX:minGX,minGY:minGY,maxGX:maxGX,maxGY:maxGY}:remoteBounds(); return {x0:b.minGX*TILE, y0:b.minGY*TILE, x1:b.maxGX*TILE, y1:b.maxGY*TILE}; },
  // the entry's launch out of the sun's near field (world3d/core.js launch): the ship moves, the 2D camera's centre stays
  // a barrel roll or a U-turn under way is anchored to the old place (fleetTick rewrites px, py from it): it ends here, as place's do
  move(x,y,hdg){ camOff.x-=x-px; camOff.y-=y-py; px=x; py=y; FL.hdg=hdg; heading=hdg; FL.roll=null; FL.uturn=null; pilot=null; },
  // One engine/physics step per 3D frame, held through transitions, approach placement and pause. The ship starts at
  // cruise and always flies (flightStep3d); a placement waits for any flight key. __3dStep uses its own maneuver clock.
  fly(dt,stepped,still){
    if(!stepped){ P3.stepBase=null; P3.stepAt=performance.now(); }   // a live frame: the wall clock again
    if(P3.paused){ FL.kb=false; FL.spd=0; return; }
    FL.kb=true; pilot=null;
    const physics=window.CM3D&&CM3D.flightPhysics;
    if(physics&&!P3.flight){ const w=cmWorld(), bodies=w.planets.concat([w.star]);
      P3.flight=physics.create({x:px,y:physics.spawnHeight(px,py,bodies),z:py,heading:FL.hdg}); P3.throttle=1; FL.spd=flyK().cruise; }
    if(still){ FL.spd=0; return; }
    if(P3.hold){ FL.spd=0; return; }
    if(physics&&P3.flight){
      const turn=FL.uturn;
      if(dt>0) stepFlight(stepped?p3StepClock(dt):performance.now(),dt);
      const f=P3.flight, w=cmWorld(), height=Math.max(2000,(maxGX-minGX)*TILE,(maxGY-minGY)*TILE)*2;
      const bodies=w.planets.concat([w.star]), bounds={x0:minGX*TILE,x1:maxGX*TILE,z0:minGY*TILE,z1:maxGY*TILE,y0:-height,y1:height};
      if(turn){
        if(turn.alt3d==null) turn.alt3d=f.y;
        const u=clamp((p3FlyNow()-turn.t0)/flyK().uturnT,0,1), alt=turn.alt3d+turn.R*(1-Math.cos(Math.PI*u));
        physics.shift(f,px-f.x,alt-f.y,py-f.z,bodies,bounds); f.pitch=0; f.pitchRate=0;
        f.vx=FL.uturn?0:Math.cos(FL.hdg)*FL.spd; f.vy=0; f.vz=FL.uturn?0:Math.sin(FL.hdg)*FL.spd;
      } else {
        // Rolls add a small swept lateral slip; ordinary translation is owned here once per frame.
        physics.shift(f,px-f.x,0,py-f.z,bodies,bounds);
        // the nose's attitude: W/S (FL.nose) and R/F (the camera's keys) add, X holds it level; flight-rs's soft
        // ceiling at 0.4 of the map's half-size stops the nose pushing further out (no wall)
        const ctl=P3.controls||{}, nose=ctl.level?0:clamp((FL.nose||0)+(Number(ctl.pitch)||0),-1,1);
        const ceiling=Math.max(300,.2*Math.max(maxGX-minGX,maxGY-minGY)*TILE);
        physics.step(f,{heading:FL.hdg,speed:FL.spd,attitude:nose*flyK().pitch,ceiling:ceiling},dt,bodies,bounds);
        p3Glance(f,dt,flyK());
      }
      px=f.x; py=f.z; return;
    }
    if(dt>0) stepFlight(stepped?p3StepClock(dt):performance.now(),dt);
    const k=flyK(), floor=FL.brake||(!FL.locked&&(keys.s||keys.arrowdown))?k.brakeSpd:k.cruise;   // the skin's floors
    if(!FL.uturn&&FL.spd<floor) FL.spd=floor;
  },
  pause(on){ P3.paused=!!on; if(on){ clearKeys(); P3.controls={pitch:0,level:false}; P3.resumeSpeed=FL.spd; P3.pauseAt=performance.now(); FL.kb=false; FL.spd=0; }
    else { const elapsed=Math.max(0,performance.now()-(P3.pauseAt||performance.now()));
      [FL.roll,FL.uturn].forEach(m=>{ if(m) m.t0+=elapsed; }); FL.kb=true; if(P3.flight) FL.spd=P3.resumeSpeed||0; } },
  left(reason,err){ P3.mode='off'; P3.hold=false; P3.paused=false; P3.flight=null; P3.controls={pitch:0,level:false};
    clearKeys(); FL.kb=false; FL.spd=0; FL.roll=FL.uturn=null; if(reason) p3Fail(reason,err); p3PovSync(); },
  sentence:p3Say,
  flags:()=>({slowCompile:!!CM.slowCompile, compileStall:!!CM.compileStall, compileFail:!!CM.compileFail, phone:!!MOB, reduced:!!REDUCED}),
  sceneScale:()=>{ const v=Number(CM.sceneClock&&CM.sceneClock.scale); return Number.isFinite(v)&&v>=0?v:1; },
  haptic:k=>{ try{ CM.haptic(k); }catch(e){} },
  settings:{get:(k,d)=>p3Get(k,d), set:(k,v)=>p3Set(k,v)},
  // the 2D canvas at a viewport point (CSS px): [r,g,b,a], or null off the stage; the match frame is compared with it
  pixel2D(x,y){ try{ const r=STAGE.getBoundingClientRect(), bx=Math.floor((x-r.left)*DPR), by=Math.floor((y-r.top)*DPR);
    if(bx<0||by<0||bx>=cv.width||by>=cv.height) return null; return Array.from(cx.getImageData(bx,by,1,1).data); }catch(e){ return null; } },
  // the 2D frame of this moment, drawn under the 3D canvas while the first frame matches it (the parity reads it)
  draw2D(){ try{ draw(performance.now()); }catch(e){} },
  // the 2D canvas and the world rectangle it shows (its last frame): the entry lays it on the plane as the 2D layer
  openThread(thread){ p3Leave('thread card'); CM.openThread(thread); },
  map2D(){ try{ return {canvas:cv, x:camx, y:camy, w:VW/ZOOM, h:VH/ZOOM}; }catch(e){ return null; } },
  params:P3_PARAMS
};
// __3dStep's clock (ms): the wall clock held where the stepped run began, while each stepped frame moves the start of a
// barrel roll or a U-turn under way back by its dt; one that began since the last frame starts at the run's first
// frame. So after n frames of dt a roll has run exactly n*dt, and a live frame afterwards carries on from there.
function p3StepClock(dt){
  const base=P3.stepBase!=null?P3.stepBase:(P3.stepBase=performance.now());
  [FL.roll,FL.uturn].forEach(m=>{ if(!m) return; if(!(m.t0<=P3.stepAt)) m.t0=base; m.t0-=dt*1000; });
  P3.stepAt=base; return base; }
function p3FlyNow(){ return P3.stepBase!=null?P3.stepBase:performance.now(); }
const P3_STORE={renderScale:'cm3d.renderScale', fpsCap:'cm3d.fpsCap', antialias:'cm3d.antialias'};
function p3Get(k,d){ try{ const v=localStorage.getItem(P3_STORE[k]||('cm3d.'+k)); return v==null?d:v; }catch(e){ return d; } }
function p3Set(k,v){ try{ localStorage.setItem(P3_STORE[k]||('cm3d.'+k),String(v)); }catch(e){}
  if(window.CM3D&&typeof CM3D.settingsChanged==='function') CM3D.settingsChanged(k); }
// ---- the 3D planets' paint (world3d/space.js reads these through ctx.page): the 2D frame is the 3D view's first
// frame, so a planet's own 2D art (as drawn this frame, lit from the sun) is projected straight down onto its sphere while the
// camera still looks down the matched lens, and its surface (surfaceAt, the one function the 2D bodies paint with) is baked
// into an equirect texture for flight. Page facts only: nothing here draws.
PAGE3D.planetArt=function(uid){ try{
  const d=KIND_DRAWN.get(uid), m=MOONS.find(x=>x.uid===uid); if(!d||!d.core||!m||!d.core.width) return null;
  const n=kindsArtN(m.r), q=m.r/n, art=d.core, S=art.width*q, x0=Math.round((m.x-S/2)/q)*q, y0=Math.round((m.y-S/2)/q)*q;
  return {canvas:art, x0:x0, y0:y0, q:q, size:S, n:n, la:d.la, at:d.at, frame:FRAME_T}; }catch(e){ return null; } };
// the planet's turn as the 2D draws it now (radians; PHASES steps) and its period (s): the 3D surface turns from there
PAGE3D.planetPhase=function(uid){ try{
  const m=MOONS.find(x=>x.uid===uid), L=m&&planetLook(m); if(!L) return null;
  const seed=kindsInt(L.seeds[0]), period=kindsPeriod(seed)*(1+3*(m.decay||0));
  return {phase:bodyPhase(seed,period)*TAU/PHASES, period:period, offset:(seed%101)/101, reduced:!!REDUCED}; }catch(e){ return null; } };
// a sampler of the planet's surface for the 3D bake: at(lon, lat) -> [night, terminator, day, emissive|null, windows 0/1,
// window weight, aurora weight, height], each colour [r,g,b] 0-255, in the three toon bands the 2D art lights it with
PAGE3D.planetSurface=function(uid,detail){ try{
  const m=MOONS.find(x=>x.uid===uid), L=m&&planetLook(m); if(!L) return null;
  const P=L.pal||planetPal(L.purpose), seeds=kindsLayerSeeds(0,L.seeds), prof=L.profile||PURPOSE[L.purpose].profile;
  const quiet=(L.conditions||[]).includes('quiet'), rgb=h=>{ const v=parseInt(String(h).slice(1,7),16)||0; return [v>>16&255,v>>8&255,v&255]; };
  const o={phase:0,detail:Math.max(4,detail|0),seeds:seeds,profile:prof,night:false,tilt:KIND_TILT};
  return {purpose:L.purpose, palette:P.colours.map(rgb), glow:rgb(P.glow), atm:rgb(mixPixel(P.colours[4],'#8fd3ff',.55)),
    city:prof.includes('lights'), rim:prof.some(k=>KIND_LAYERS[k]&&KIND_LAYERS[k].rim), core:PURPOSE[L.purpose].core||null,
    shape:PURPOSE[L.purpose].shape, quiet:quiet, health:L.health, conditions:(L.conditions||[]).slice(), progress:L.progress,
    profile:prof.slice(), key:['s3',L.purpose,P.name,P.colours.join(''),P.glow,seeds.join(','),prof.join('+'),quiet?1:0,o.detail,THEME_NAME].join('|'),
    at(lon,lat){ o.lon0=lon; const s=surfaceAt(L.purpose,P,lon,lat,seeds[0],o); let c=s.c; if(quiet) c=kindsGrey(c,.5);
      const e=s.e?(quiet?kindsGrey(s.e,.35):s.e):null;
      return [rgb(kindsBand(c,0)),rgb(kindsBand(c,1)),rgb(c),e?rgb(e):null,s.en?1:0,s.w==null?1:s.w,s.aur||0,s.h==null?.5:s.h]; }}; }catch(e){ return null; } };
CM.enter3d=function(opts){
  opts=Object.assign({},opts||{});
  if(CM.mode3d()){ if(opts.target&&typeof CM3D.retarget==='function') CM3D.retarget(String(opts.target)); return; }
  if(P3.mode==='entering') return;
  if(P3.disabled){ p3Say(P3.sentence); return; }
  if(CM.forceNoGL){ p3Fail('no-webgl2'); return; }
  P3.mode='entering'; P3.reason=''; P3.sentence=''; p3Unsay();
  if(VIEW!=='map') setView('map');              // the 3D view is the map in depth
  loadThree().then(()=>{
    if(P3.mode!=='entering') return;              // left again while the files came
    const r=CM3D.enter(PAGE3D,opts);
    if(!r||!r.ok){ p3Fail((r&&r.reason)||'error',r&&r.error); return; }
    P3.mode='on'; p3PovSync();
  }).catch(err=>p3Fail(String(err&&err.message)==='not-built'?'not-built':'bundle-error',err));
};
CM.mode3d=()=>!!(window.CM3D&&typeof CM3D.owns==='function'&&CM3D.owns());
CM.frame3d=function(t,dt){ if(window.CM3D&&typeof CM3D.frame==='function') CM3D.frame(t,dt); p3PovSync(); };
function p3Leave(why){ if(window.CM3D&&typeof CM3D.leave==='function') CM3D.leave(why); P3.mode='off'; P3.hold=false; P3.paused=false; }
// One selector across the flat map and both flight cameras. Camera changes leave the physics state intact.
CM.pov=()=>P3.mode==='off'?'map':CM.mode3d()&&typeof CM3D.view3d==='function'?CM3D.view3d():p3Get('view3d','cockpit')==='chase'?'chase':'cockpit';
CM.setPov=function(view){
  if(!['map','cockpit','chase'].includes(view)) return false;
  if(view==='map'){ p3Leave('back'); p3PovSync(); return true; }
  if(CM.mode3d()){
    if(P3.mode==='off'||typeof CM3D.view3d!=='function'||!CM3D.view3d(view)) return false;
  } else { p3Set('view3d',view); CM.enter3d({}); }
  p3PovSync(); return true;
};
function p3PovSync(){
  const el=document.getElementById('pov-switch'); if(!el) return;
  const on=CM.mode3d(), view=CM.pov(), leaving=on&&P3.mode==='off';
  const hidden=VIEW!=='map'||P3.paused||(!on&&MOB&&['threads','wingmate'].includes(document.body.dataset.tab));
  if(el.hidden!==hidden) el.hidden=hidden;
  if(el.classList.contains('in3d')!==on) el.classList.toggle('in3d',on);
  let left='50%'; if(!on&&STAGE&&STAGE.getBoundingClientRect){ const r=STAGE.getBoundingClientRect(); left=(r.left+r.width/2)+'px'; }
  if(el.style.left!==left) el.style.left=left;
  el.querySelectorAll('[data-pov]').forEach(b=>{ const pressed=String(b.dataset.pov===view), disabled=leaving&&b.dataset.pov!=='map';
    if(b.getAttribute('aria-pressed')!==pressed) b.setAttribute('aria-pressed',pressed); if(b.disabled!==disabled) b.disabled=disabled; });
}
document.getElementById('pov-switch').addEventListener('click',e=>{ const b=e.target.closest('[data-pov]'); if(b) CM.setPov(b.dataset.pov); });
addEventListener('resize',p3PovSync);
const P3_FLIGHT=['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright','shift','q','e','c'];
const P3_SLEEP=['tab','enter','home','backspace','delete','pageup','pagedown'];
// every key first: in 2D only I (into 3D); in 3D, I or M leave, '.' pauses the world, ',' held runs it at a quarter,
// Escape goes on to the back layers (3D at 30: pause), the camera and HUD modules get the rest (CM3D.onKey), the
// flight keys go on to the 2D flight, and the page's own one-key shortcuts sleep under the view
CM.key3d=function(e){
  if(typeof GUIDE!=='undefined'&&GUIDE.el) return false;
  const k=String(e.key||'').toLowerCase();
  if(!CM.mode3d()){
    if(k==='i'&&!e.repeat&&P3.mode==='off'){ CM.enter3d({}); return true; }
    if(P3.mode==='entering'&&(k==='i'||k==='m')){ P3.mode='off'; return true; }
    return false;
  }
  if(k==='i'||k==='m'){ if(!e.repeat) p3Leave('key'); return true; }
  if(k==='.'){ if(!e.repeat&&typeof CM3D.worldPause==='function') CM3D.worldPause(); return true; }
  if(k===','){ if(typeof CM3D.worldSlow==='function') CM3D.worldSlow(true); return true; }
  if(k==='escape') return false;
  if(typeof CM3D.onKey==='function'){ let took=false; try{ took=!!CM3D.onKey(e); }catch(err){ if(typeof CM3D.fault==='function') CM3D.fault('onKey','key',err); } if(took){ p3PovSync(); return true; } }
  if(P3_FLIGHT.includes(k)){ if(P3.paused) return true; P3.hold=false; return false; }
  return k.length===1||P3_SLEEP.includes(k);
};
function p3Back(how){
  const on=CM.mode3d();
  if(how==='has') return on||P3.mode==='entering';
  if(!on){ if(P3.mode==='entering'&&how!=='has'){ P3.mode='off'; return true; } return false; }
  if(how==='esc'){ if(typeof CM3D.togglePause==='function') CM3D.togglePause(); return true; }
  if(how==='back'){ p3Leave('back'); return true; }
  return false;
}
window.__3dAudit=function(){
  if(window.CM3D&&typeof CM3D.audit==='function'&&CM3D.started&&CM3D.started()){ const a=CM3D.audit(); if(a.mode==='off'&&P3.reason) a.reason=P3.reason; return a; }
  return {mode:P3.mode==='entering'?'entering':'off', reason:P3.reason, renderer:null, three:window.THREE?String(THREE.REVISION):null, calls:0,
    triangles:0, textures:0, dpr:0, scale:1, fps:0, clock:{scale:1,t:0}, compile:{pending:0,placeholders:0,ms:0}, errors:[], parity:{},
    widthShare:{}, radiusPx:{}, buffer:{w:0,h:0}, lock:null, scene:{}, sky:{}, fx:{}, models:{}, craft:{}, camera:{}, hud:{}, transition:{}, input:{},
    ship:{x:px,y:py,alt:0,hdg:(Math.round(FL.hdg*1800/Math.PI)/10+360)%360,spd:FL.spd,bank:0,pitch:0,rollDeg:0,uturn:null,boost:false,brake:false},
    focus:CM.focusUid(), skin:String((SKIN&&SKIN.name)||'neutral'), look:VEC?'vector':'pixel', handoff:{phase:null,name:'',by:null,timedOut:false}};
};
CM.onInit.push(()=>{
  const opt=(...a)=>a.map(x=>({value:x[0],label:x[1]}));
  CM.settingsExtra.push(
    {key:'render-scale', group:'3D view', label:'Render scale', options:opt(['auto','Auto (keeps it smooth)'],['100','100%'],['85','85%'],['70','70%'],['50','50%']),
      get:()=>p3Get('renderScale','auto'), set:v=>p3Set('renderScale',v)},
    {key:'fps-cap', group:'3D view', label:'Frame rate', options:opt(['display','Match the display'],['60','60 a second'],['30','30 a second']),
      get:()=>p3Get('fpsCap',MOB?'30':'display'), set:v=>p3Set('fpsCap',v)},
    {key:'antialias', group:'3D view', label:'Smooth edges (from the next entry)', options:opt(['on','On'],['off','Off']),
      get:()=>p3Get('antialias','on'), set:v=>p3Set('antialias',v)},
    // the 1990s console look (240 lines, a 4x4 dither, 32 levels; world3d/core.js): off, the painted look, by default
    {key:'retro-3d', group:'3D view', label:'Retro 3D (240 lines, dithered)', options:opt(['off','Off'],['on','On']),
      get:()=>p3Get('retro3d','off')==='on'?'on':'off', set:v=>p3Set('retro3d',v==='on'?'on':'off')});
  CM.backLayers.push({pri:30, fn:p3Back});
  addEventListener('keyup',e=>{ if(e.key===','&&window.CM3D&&typeof CM3D.worldSlow==='function') CM3D.worldSlow(false); });
  addEventListener('blur',()=>{ if(window.CM3D&&typeof CM3D.worldSlow==='function') CM3D.worldSlow(false); });
  if(P3_PARAMS.scene3d==='1'||P3_PARAMS.demo3d) setTimeout(()=>CM.enter3d({instant:true}),0);
});
// ---- THE 3D VIEW: flight tuning, the HUD and cockpit settings, landing. The 3D view flies the page's own
// flightStep with k (flyK, FLIGHT): the skin's feel3d (the 3D bundle's CM3D.skin3d) gives the speeds, turn rates, bank,
// roll, U-turn and the meter; a skin without it flies the neutral numbers (200 u/s cruise, 55 degrees a
// second, the 2D FLY values for the rest). The rows below join "?" > Settings (CM.settingsExtra, v2 touch
// points); the camera and HUD modules read them through ctx.page.settings every frame. Enter on a locked planet in 3D
// sets CM3D.landOn: when the exit is over the page opens that planet as a click on it would.
function p3Feel(){ return (window.CM3D&&CM3D.skin3d&&CM3D.skin3d.feel3d)||{}; }
function p3Tune(){
  const f=p3Feel(), sp=f.speed||{}, m=f.meter||{}, n=(v,d)=>{ const x=Number(v); return Number.isFinite(x)&&x>0?x:d; };
  const cruise=n(sp.cruise,P3_CRUISE), turn=n(f.turn,55), boostK=n(f.boostK,n(sp.boost,0)/cruise||FLY.boostK);
  return {speed:cruise/SPEED, turn:turn*Math.PI/180/FLY.turn, boostK:boostK, brakeTurn:n(f.turnHard,turn*FLY.brakeTurn)/turn,
    burn:n(m.burn,FLY.burn), refill:n(m.refill,FLY.refill), lockout:m.lockout===true, boostUp:n(sp.boostUp,0), boostDown:n(sp.boostDown,1),
    bankSoft:n(f.bankSoft,FLY.bankSoft), bankHard:n(f.bankHard,FLY.bankHard), rollT:n(f.rollT,FLY.rollT), uturnT:n(f.uturnT,FLY.uturnT),
    cruise:cruise, brakeSpd:n(sp.brake,P3_BRAKE), turnDeg:turn, turnHardDeg:n(f.turnHard,turn*FLY.brakeTurn), pitch:n(f.pitch,30)}; }
// the cockpit glass in percent: the stored choice, else the skin's (feel3d.cockpit.glass, 0-1), else 20
function p3GlassDefault(){ const v=Number((p3Feel().cockpit||{}).glass); return Number.isFinite(v)&&v>=0&&v<=1?Math.round(v*100):20; }
function p3Glass(){ const v=Number(p3Get('glass','')); return p3Get('glass','')!==''&&Number.isFinite(v)?Math.max(0,Math.min(100,Math.round(v))):p3GlassDefault(); }
CM.onInit.push(()=>{
  const opt=(...a)=>a.map(x=>({value:x[0],label:x[1]})), onOff=opt(['off','Off'],['on','On']);
  CM.settingsExtra.push(
    {key:'hud-aspect', group:'3D view', label:'HUD frame', options:opt(['expand','Fill the screen'],['16:9','16:9 in the middle'],['4:3','4:3 in the middle']),
      get:()=>{ const v=p3Get('hudAspect','expand'); return ['expand','16:9','4:3'].includes(v)?v:'expand'; }, set:v=>p3Set('hudAspect',v)},
    {key:'glass', group:'3D view', label:'Cockpit glass',
      get options(){ const vals=[0,10,20,30,40,50,60,70,80,90,100], d=p3GlassDefault(); if(!vals.includes(d)) vals.push(d);
        return vals.sort((a,b)=>a-b).map(v=>({value:v,label:v+'%'+(v===d?' (the default)':'')})); },
      get:()=>p3Glass(), set:v=>p3Set('glass',Math.max(0,Math.min(100,Math.round(Number(v)||0))))},
    {key:'reduce-flashing', group:'3D view', label:'Reduce flashing (no flashes, light spill, shock or hit edge)', options:onOff,
      get:()=>p3Get('reduceFlashing','off')==='on'?'on':'off', set:v=>p3Set('reduceFlashing',v==='on'?'on':'off')},
    {key:'comfort', group:'3D view', label:'Comfort (the view rolls 8 degrees at most)', options:onOff,
      get:()=>p3Get('comfort','off')==='on'?'on':'off', set:v=>p3Set('comfort',v==='on'?'on':'off')},
    {key:'pitch-keys', group:'3D view', label:'W / ↑ in flight', options:opt(['dive','Nose down (pilot style)'],['climb','Nose up']),
      get:()=>p3Get('pitchKeys','dive')==='climb'?'climb':'dive', set:v=>{ p3Set('pitchKeys',v==='climb'?'climb':'dive'); P3.dives=null; }},
    {key:'view3d', group:'3D view', label:'Camera (V switches in flight)', options:opt(['cockpit','Cockpit'],['chase','Behind the ship']),
      get:()=>p3Get('view3d','cockpit')==='chase'?'chase':'cockpit', set:v=>p3Set('view3d',v)});
});
{ const left=PAGE3D.left;
  PAGE3D.left=function(reason,err){ left(reason,err); FL.locked=false; FL.drew=false;
    const uid=window.CM3D&&CM3D.landOn; if(!uid) return; CM3D.landOn=null;
    const u=reason?null:unitByUid(uid); if(u) setTimeout(()=>{ try{ activate(u); }catch(e){} },0); }; }
// the bridge sends each planet's rooms by uid (every room is a character already): put them back;
// an id whose character is not in this world is dropped, never left as a bare string
function joinRooms(w){ if(!w||!Array.isArray(w.chars)) return w; const by={};
  w.chars.forEach(c=>{ if(c&&typeof c.uid==='string') by[c.uid]=c; });
  w.chars.forEach(c=>{ if(c&&c.kind==='area'&&Array.isArray(c.rooms))
    c.rooms=c.rooms.map(r=>typeof r==='string'?by[r]:r).filter(r=>r!==undefined); });
  return w; }
joinRooms(W);
// task state is declared up front: place() runs during load and docks the task ships
const TASK_OPEN=['review','failed','stopped','lost'];
const TASK_LIVE=['running','checking','stopping'].concat(TASK_OPEN);
const TASK_COL={running:'#3dff9e',checking:'#ffd34d',stopping:'#ff8a4c',review:'#ffbd2e',failed:'#ff4d5e',stopped:'#9aa7bd',lost:'#ff8a4c',merged:'#5fb4ff',discarded:'#6b7684',done:'#5fb4ff',error:'#ff4d5e',new:'#9aa7bd'};
// a thread's state in the plain words of words.py (CM.stateWord): "to review", "changes accepted", "rejected (copy kept)"
const TASK_WORD={}; ['running','checking','stopping','review','failed','stopped','lost','merged','discarded','done','error','new'].forEach(s=>{ TASK_WORD[s]=CM.stateWord(s); });
const LAUNCHES={};
const TASK_WORKING=['running','checking','stopping'];   // an agent, or its reviewer, is doing something right now
// a failed run whose verify failed and that has retries left is restarted within seconds: say so, not 'failed'
function taskTitle(t,n){ return CM.shortTitle(String((t&&(t.title||t.text))||''),n||60)||'a task'; }   // a task as a person says it, never the raw "Goal: ... (T-id)" prompt
function taskWord(t){ t=t||{}; return t.status==='failed'&&t.retry_due?CM.stateWord('retrying'):(TASK_WORD[t.status]||t.status||''); }
function taskSig(a){ return a.map(t=>t.id+':'+t.status).join(','); }
// the workflows as Today reads them: ids, states, progress, when they last moved, each step's state and progress
function wfSig(){ const L=typeof CM.wfList==='function'?CM.wfList():[]; return JSON.stringify((Array.isArray(L)?L:[]).map(w=>w&&typeof w==='object'?[String(w.id||''),String(w.state||''),w.progress==null?null:w.progress,String(w.updated||''),
  (Array.isArray(w.steps)?w.steps:[]).map(x=>x&&typeof x==='object'?[String(x.state||''),x.progress&&typeof x.progress==='object'?[x.progress.done,x.progress.total]:x.progress==null?null:x.progress]:null)]:null)); }
let openTaskId=null, TASKS_AT=0, TV=null, TASK_EPOCH=0, TASKS_SEQ=0, TASKS_APPLIED=0;
let VIEW=(W.today||{}).configured===false?'map':'today'; try{ const sv=sessionStorage.getItem('view'); if(sv==='map'||sv==='today') VIEW=sv; }catch(e){}
// ---- THE SKY (sky.py, W.sky): every thing that needs a person as one list of contacts, worst state first, then oldest.
// Sky mode is on by default: running work is in warp (counted, never drawn), each contact is a foe on its planet (around
// the star when it has none) with its age tier on the craft, and no contact at all reads EMPTY SKY. Y shows every unit
// again. The sky belongs to the home world: a remote deck keeps its own problems, and a sky that could not be built
// (W.sky.error) leaves the map as it was and says so. ----
let SKY=true; try{ SKY=localStorage.getItem('sky')!=='0'; }catch(e){}
const SKY_STATES=['blocked','failed','review','ready','stale'], SKY_TIERS=['fresh','aging','raider'];
let SKY_FOCUS=-1, SKY_STAR={uid:'sky:star',label:'',x:0,y:0,r:0,ringR:0,outerR:0,allies:0,enemies:0,wrecks:0,foes:[]};
function skyData(){ const s=W.sky; return s&&typeof s==='object'&&Array.isArray(s.contacts)?s:null; }
function skyOk(){ const s=skyData(); return !!s&&!s.error; }
function skyContacts(){ const s=skyData(); return s&&!s.error?s.contacts.filter(c=>c&&typeof c==='object'&&typeof c.id==='string'&&c.id):[]; }
// a contact's age tier: the one sky.py gave, else from its age (under a day fresh, a week aging, older a raider)
function skyTier(c){ if(SKY_TIERS.includes(c&&c.tier)) return c.tier; const d=Number(c&&c.age_days); return !Number.isFinite(d)||c.age_days==null||d<1?'fresh':d<=7?'aging':'raider'; }
function skyAge(c){ const d=Number(c&&c.age_days); return c&&c.age_days!=null&&Number.isFinite(d)?CM.ageWords(d):''; }   // "today", "3 days", "5 weeks"
// in warp: an agent, a T3 thread or a map thread that is working right now
function inWarp(u){ return (isShip(u)&&u.status==='WORKING')||(u.kind==='task'&&TASK_WORKING.includes(u.status)); }
// what sky mode takes out of sight, the running work sky.py counts in hyper: a map thread at work (a planet's defender,
// or a failed one whose retry is due), a working T3 thread or background agent (a ship) and a working tmux seat (the
// carrier too). A seat at rest keeps its berth in the hangar, a bare shell is an empty berth, another machine's deck stays.
function warpHidden(u){ return (u.kind==='task'&&(TASK_WORKING.includes(u.status)||(u.status==='failed'&&!!u.retry_due)))
  ||(u.kind==='ship'&&!u.machine&&u.status==='WORKING')||(u.kind==='seat'&&!u.shell&&!u.remote&&u.status==='WORKING'); }
// IN WARP: the sky's own count (sky.py's hyper.n, which also holds threads too new to be drawn), else what sky mode hides
function warpCount(list){ const s=skyData(), h=s&&s.hyper&&typeof s.hyper==='object'?s.hyper.n:null;
  return Number.isInteger(h)&&h>=0?h:(list||units).filter(warpHidden).length; }
// sky mode on the deck on screen (draw time only: the deck is declared further down); a unit in warp is not drawn or
// picked then
function skyOn(){ return SKY&&deck==='home'&&skyOk(); }
// a T3 thread settled or snoozed (ships.py keeps it a day so the sky can count it as settled today or parked): done or
// parked, so out of the sky too, but never counted as in warp
function skyDone(u){ return u.kind==='ship'&&!u.machine&&!!u.t3&&typeof u.t3==='object'&&(u.t3.state==='settled'||u.t3.state==='snoozed'); }
function skyHidden(u){ return SKY&&(warpHidden(u)||skyDone(u))&&skyOn(); }
// a T3 ship's way into its thread (ships.py: the configured t3_link + /bridge?focus=...): an http(s) address or nothing
function t3Url(u){ const s=u&&u.kind==='ship'&&u.t3&&typeof u.t3==='object'?u.t3.open_url:null;
  return typeof s==='string'&&s.length<=2000&&/^https?:[/][/][^\\s"'<>]+$/i.test(s)?s:''; }
// what the sky calls a problem the map already drew (sky.py's contract ids): the same thing is drawn once. A cron line's
// uid already starts with "cron:", so its contact id is either that uid or the uid behind the source word again.
function legacyKeys(f){ const u=String(f.uid||'');
  return f.kind==='cron'?[u,'cron:'+u]:f.kind==='item'?['board:'+u.replace(/^quest:/,'')]:f.kind==='wreck'?[u]:[]; }
function idTail(id){ id=String(id||''); const i=id.indexOf(':'); return i<0?id:id.slice(i+1); }
// a lane word (a ledger row's lane, a commitment's lane) as a planet: the lane or one of its words is a planet's
// name, else the shipped synonyms below; '' = no planet carries it (the contact circles the star)
const LANE_PLANET=W.lane_planets||{};
function lanePlanet(lane,list){
  const areas=(list||W.chars||[]).filter(u=>u&&u.kind==='area'&&!u.alias_of), low=String(lane||'').toLowerCase(), words=[low].concat(low.split(/[^a-z0-9]+/).filter(Boolean));
  for(const w of words){ const a=areas.find(u=>String(u.name||'').toLowerCase()===w); if(a) return a.name; }
  for(const w of words){ const nm=LANE_PLANET[w]; if(nm&&areas.some(u=>u.name===nm)) return nm; }
  return '';
}
// the planet a contact belongs to: the one sky.py named, else the planet of the thing it opens, else what its source
// says (a thread's folder, a ship's or seat's dock, a cron line's area, a board item's area, a ledger row's lane)
function contactMoon(c,moons,list,areasByPath,items){
  const byUid=uid=>moons.find(m=>m.uid===uid)||null, byName=nm=>nm?moons.find(m=>m.label===nm)||null:null;
  const atPath=p=>{ p=String(p||''); const a=p&&(areasByPath||[]).find(x=>p===x.path||p.startsWith(x.path+'/')); return a?byUid(a.uid):null; };
  const ofUnit=u=>!u?null:u.kind==='area'?byUid(u.uid):(u.docked_at?byUid(u.docked_at):u.area?byUid(u.area):atPath(u.cwd||u.target||u.path));
  if(typeof c.planet_uid==='string'&&c.planet_uid){ const m=byUid(c.planet_uid); if(m) return m; }
  const find=uid=>uid?list.find(u=>u.uid===uid):null, tail=idTail(c.id), src=String(c.source||'');
  let m=c.open&&typeof c.open.uid==='string'?ofUnit(find(c.open.uid)):null; if(m) return m;
  if(src==='task'){ m=ofUnit(find('task:'+tail)); if(m) return m; const t=agentTasks().find(x=>String(x.id)===tail); return t?atPath(t.target):null; }
  if(src==='t3') return ofUnit(find('ship:t3:'+tail)||find(c.id));
  if(src==='seat') return ofUnit(find(c.id)||find(tail)||find('seat:'+tail));
  if(src==='cron') return ofUnit(find(tail)||find(c.id));
  if(src==='board'){ const it=(items||[]).find(x=>String(x.id)===tail); return it?byName(it.area):null; }
  if(src==='ledger'){ const r=((W.today||{}).ledger||[]).find(x=>x&&String(x.id)===tail); return r?byName(lanePlanet(r.lane,list)):null; }
  return null;
}
// the bridge that serves this page says so: window.CM_BRIDGE = {live, remote} is written into the page it
// sends (loopback: remote false; the phone listener: remote true). A copy opened from disk or from some
// other server has no such flag and keeps the old rule: live only on http://127.0.0.1 or localhost.
const CMB = (typeof window !== 'undefined' && window.CM_BRIDGE && typeof window.CM_BRIDGE === 'object') ? window.CM_BRIDGE : null;
const REMOTE = !!(CMB && CMB.remote === true);
const LIVE = CMB ? CMB.live === true : (location.protocol === 'http:' && (location.hostname === '127.0.0.1' || location.hostname === 'localhost'));
// DESK: the desktop-only controls (seats and panes, doors, verbs, open folder, board rulings, other machines,
// verify/setup/retries); a paired phone gets threads, the map and the wingmate and none of these
const DESK = LIVE && !REMOTE;
const VIEWONLY = REMOTE ? 'view only on a phone: talk to seats from the desktop' : 'static file — view only';
// what a paired phone may ask the bridge for; the bridge answers 404 to everything else, and the page does not try
const REMOTE_API = /^[/](state|tasks?|task[/](diff|followup|stop|close)|wingmate([/](ask|answer))?|workflows|workflow([/](replay|gate))?)([?]|$)/;
// the bridge answers on both loopback names; on a live page the way back to it is this origin, whatever its port
const LIVE_URL = LIVE ? location.origin + '/' : __LIVEURL__;
const SERVE_ARGS = __SERVEARGS__;      // what starts the map this page was rendered for
// STATIC OPEN (file:// or the synced copy): the bridge verbs are unreachable from here. Say so
// once, at the top, with a link to the live map at the port this page was rendered for. The page
// never probes that address and never jumps there by itself: another app may answer on that port.
function staticBanner(){
  const b=document.createElement('div'); b.id='staticbanner';
  b.appendChild(document.createTextNode('STATIC FILE · nothing here can act (no doors, no sends, '
    +'no board rulings) · live map at '));
  const a=document.createElement('a'); a.href=LIVE_URL; a.textContent=LIVE_URL; b.appendChild(a);
  b.appendChild(document.createTextNode(' · not running? start it with: container-map '+SERVE_ARGS
    +' (or python3 cli.py '+SERVE_ARGS+' from an unzipped copy)'));
  document.body.appendChild(b);
}
if(!LIVE && !(W&&W.demo)) window.addEventListener('DOMContentLoaded', staticBanner);
// ---- MOBILE: a phone gets html.m, a tablet html.t. Detection runs first, so fit() below measures the right layout.
// The head script (mobile-early) does the same before first paint; this copy is the one that always runs. ----
let MOB=false, MOB_LAND=false, MOB_BOOTED=false, MOB_RET='', MOB_WINGMATE=false, SHEET_H=0, SHEET_CUR=0, SAFE_T=0, FIT_HOLD=false, FIT_PEND=false, DPR_DROPPED=false, MOB_P=0;
const MOB_Q='(max-width:700px), (pointer:coarse) and (max-height:500px)';
function mobDetect(){
  if(typeof matchMedia!=='function') return false;
  const h=document.documentElement; if(!h||!h.classList) return false;
  const was=MOB, m=matchMedia(MOB_Q).matches;
  h.classList.toggle('m',m); h.classList.toggle('land',m&&innerWidth>innerHeight); h.classList.toggle('t',!m&&matchMedia('(pointer:coarse)').matches);
  MOB=m; MOB_LAND=m&&innerWidth>innerHeight;
  if(MOB!==was&&MOB_BOOTED) mobFlip();
  return MOB!==was;
}
mobDetect(); addEventListener('resize', mobDetect);
try{ const mq=matchMedia(MOB_Q), onq=()=>{ mobDetect(); fit(); };
  if(mq.addEventListener) mq.addEventListener('change',onq); else if(mq.addListener) mq.addListener(onq); }catch(e){}
const cv = document.getElementById('c'), cx = cv.getContext('2d');
let VW, VH, DPR, HOME_LIVE=false;     // HOME_LIVE: the home view (homeSettle) exists; set once the script has built it
const STAGE=document.getElementById('stage');
// A phone draws at its own pixel ratio up to 3, two when the canvas would pass 3.8 million pixels, and one step
// lower once the frame time says it cannot keep up (mobFrameTime). A desktop keeps its cap of 2.
function canvasDpr(dpr,vw,vh){
  dpr=Number(dpr)||1; if(!MOB) return Math.min(2,dpr);
  let d=Math.min(3,dpr); if(vw*vh*d*d>3.8e6) d=Math.min(d,2);
  return DPR_DROPPED?Math.max(1,d-1):d;
}
function fit(){ if(MOB&&FIT_HOLD&&VW){ FIT_PEND=true; return; }   // a field is focused: the keyboard resizes the screen, not the stage
  VW = (STAGE&&STAGE.clientWidth)||innerWidth; VH = (STAGE&&STAGE.clientHeight)||innerHeight; DPR = canvasDpr(devicePixelRatio,VW,VH);
  // a phone keeps the canvas it has when the size did not change: a reallocation clears it and costs a frame
  if(MOB&&cv.width===Math.floor(VW*DPR)&&cv.height===Math.floor(VH*DPR)){ cx.setTransform(DPR,0,0,DPR,0,0); cx.imageSmoothingEnabled = false; return; }
  cv.width = VW*DPR; cv.height = VH*DPR; cx.setTransform(DPR,0,0,DPR,0,0); cx.imageSmoothingEnabled = false;
  if(HOME_LIVE) homeSettle(); }          // a stage that changed size while the view is still the first one: the home view again
addEventListener('resize', fit); fit();
if(typeof ResizeObserver!=='undefined'&&STAGE){ try{ new ResizeObserver(()=>fit()).observe(STAGE); }catch(e){} }
// ---- type on the canvas: the page's two faces, sizes in screen pixels, never under 12. Text drawn
// in world units goes through wfont(): zoomed out it is scaled up, so it keeps its size on screen.
const FONT_DISPLAY='Silkscreen,"Pixelify Sans",monospace', FONT_MONO='ui-monospace,SFMono-Regular,Menlo,Consolas,"DejaVu Sans Mono",monospace';
// the vector look (VEC): condensed tracked labels and tabular data
const FONT_TAC='"Barlow Condensed","Arial Narrow",sans-serif', FONT_DATA='"Share Tech Mono",ui-monospace,Menlo,monospace';
const FONT_MIN=12;
function zs(){ return Math.max(1,1/ZOOM); }             // world units per screen pixel while zoomed out
function cfont(px,o){ o=o||{}; return (o.bold?'600 ':'')+Math.max(FONT_MIN,px)+'px '+(o.display?FONT_DISPLAY:FONT_MONO); }
function wfont(px,o){ o=o||{}; return (o.bold?'600 ':'')+(Math.max(FONT_MIN,px)*zs())+'px '+(o.display?FONT_DISPLAY:FONT_MONO); }
function legible(px){ return px*ZOOM>=FONT_MIN; }        // world text that would draw under 12 px is not drawn
// the canvas uses the display face only once it has loaded (until then the fallback draws the labels)
if(document.fonts&&document.fonts.load){ document.fonts.load('16px Silkscreen').catch(()=>{}); ['500 16px "Barlow Condensed"','600 16px "Barlow Condensed"','16px "Share Tech Mono"'].forEach(f=>document.fonts.load(f).catch(()=>{})); }

// ---- world layout: three halls, characters placed on a grid inside each ----
function isShip(u){ return u.kind==='ship'||(u.kind==='seat'&&!u.shell); }
// a tmux window whose program is a bare shell (render-game.py's "shell"): an empty berth on the dock ring, not a ship
function isBerth(u){ return u.kind==='seat'&&u.shell===true&&!u.remote; }
// a seat at rest: in sky mode it docks in the hangar (the star's ring) instead of at the planet it sits in
function atRest(u){ return u.kind==='seat'&&(u.status==='IDLE'||u.status==='BLANK'); }
const TILE = 48;
const SPEED = 480;           // px per second: the cruise speed of keyboard flight (FLIGHT), the touch pad and the agent pilot
let boostOn = false;
const HALLS = [
  {key:'runner',  label:'RUNNERS HALL', x:2,  y:2,  w:11, h:7},
  {key:'seat',    label:'SEAT DECK',    x:15, y:2,  w:7,  h:4},
  {key:'channel', label:'RELAY BAY',    x:15, y:8,  w:7,  h:7},
  {key:'machine', label:'MACHINE BAY',  x:2,  y:11, w:11, h:4},
  {key:'room',    label:'HUB OFFICE',   x:25, y:2,  w:6,  h:9},
];
// WORK DISTRICTS: one block per directory of the container, laid out by districts.py. Their
// units (area = door + worker slot, dir = room, cron = clock, proc = gear) arrive with grid
// positions already assigned, so the halls above never have to know how many there are.
// They replaced the CRON YARD (crons grouped by tool) and the REPO DISTRICT (six hard-coded repos).
// ---- 16-BIT SECTORS: place = hue, state = badge. -----------------------------------------
// Every hall and every district kind has its own saturated 16-bit-era palette (sunset orange, city
// blue, teal, gold, nebula purple, forest green), so the map reads by colour from across the room.
// Status semantics stay exactly where the glance rule put them: amber = needs you, purple = your
// draft, red = dead or stale, green = a seat working. Healthy things wear their sector's colour.
const SECTORS = {
  runner:   {floor:'#2b1407', floor2:'#3a1c0a', grid:'#7a3a12', accent:'#ff9a2e', glow:'#ff6a00', ink:'#ffd9b0'},
  seat:     {floor:'#081a3d', floor2:'#0c2450', grid:'#2456a8', accent:'#4cc6ff', glow:'#2b8cff', ink:'#d6ecff'},
  channel:  {floor:'#04262c', floor2:'#063238', grid:'#127078', accent:'#3ef2e0', glow:'#00c8b4', ink:'#c9fff7'},
  machine:  {floor:'#2c1f0a', floor2:'#3a2a0e', grid:'#7a5a1c', accent:'#ffcf4a', glow:'#e0a020', ink:'#fff0c2'},
  room:     {floor:'#141c34', floor2:'#1b2544', grid:'#3a4a80', accent:'#a8d8ff', glow:'#6aa8ff', ink:'#e8f2ff'},
};
// the folder kinds' places (code, knowledge, reference, data, media, config, shared, bus, archive, unknown) are aliases of the one
// PURPOSE palette (ui/kinds.js kindsSector): a folder's plate and wedge take its purpose's colours in every theme and skin, so the
// Not sorted rock is grey, never the old magenta 'unknown'. The places that are not folders keep the hues above (a theme's
// "sectors" block still recolours those).
Object.keys(KIND_PURPOSE).forEach(k=>Object.defineProperty(SECTORS,k,{enumerable:true,get:()=>kindsSector(KIND_PURPOSE[k])}));
const BLOCK_KIND = {};
const QUEST_HITS = {};
function sec(key){
  if(GB) return {floor:T.floor, floor2:T.floor, grid:T.grid, accent:T.edge, glow:T.heat, ink:T.ink, key:'gb'};
  const k = SECTORS[key] ? key : 'unknown', S = (!KIND_PURPOSE[k] && SECT && SECT[k]) || SECTORS[k]; return Object.assign({key:key}, S);
}
function sectorOfUnit(u){
  if(!u) return sec('room');
  if(isDK(u)) return sec(BLOCK_KIND[u.area||u.uid] || 'unknown');
  if(u.kind==='portal' || u.kind==='machine') return sec('machine');
  return sec(SECTORS[u.hall] ? u.hall : (SECTORS[u.kind] ? u.kind : 'seat'));
}
// dithered floor tiles: a 4x4 checker of two shades, repeated. Real pixels, not a gradient.
const PAT = {};
function pat(S){
  if(GB) return S.floor;
  if(PAT[S.key]) return PAT[S.key];
  const c = document.createElement('canvas'); c.width = 4; c.height = 4; const g = c.getContext('2d');
  g.fillStyle = S.floor; g.fillRect(0,0,4,4); g.fillStyle = S.floor2; g.fillRect(0,0,2,2); g.fillRect(2,2,2,2);
  return (PAT[S.key] = cx.createPattern(c, 'repeat'));
}
let sectorHere = null;
function whereAmI(){
  const b = nearestBody(px, py, deck==='home'?MOONS:remoteMoons, deck==='home'?(W.name||'CONTAINER'):String(deck).toUpperCase()); if(!b) return null;
  const k = b.kind; return {label:b.label, kind:k, S:sec(SECTORS[k] ? k : (k.indexOf('star')>=0 ? 'room' : (k.indexOf('machine')>=0 ? 'machine' : (k.indexOf('runner')>=0 ? 'runner' : 'unknown'))))};
}
// a plate: dithered floor, heat glow in the sector's own colour, a scan band on live plates,
// hard 2px frame, bevel, corner brackets, a label plate, and a quest marker when a parked
// decision names this place. Decoration reads state; it never invents it.
function drawFloor(x, y, w, h, S, heat, life, t, label, right, mark){
  const X=x*TILE, Y=y*TILE, Wd=w*TILE, H=h*TILE;
  cx.fillStyle = pat(S); cx.fillRect(X,Y,Wd,H);
  if(heat>0){ cx.save(); cx.globalAlpha = 0.08+0.18*heat; cx.fillStyle=S.glow; cx.fillRect(X,Y,Wd,H); cx.restore(); }
  cx.save(); cx.globalAlpha=0.35; cx.strokeStyle=S.grid; cx.lineWidth=1;
  for(let i=1;i<w;i++){cx.beginPath();cx.moveTo(X+i*TILE+.5,Y);cx.lineTo(X+i*TILE+.5,Y+H);cx.stroke();}
  for(let j=1;j<h;j++){cx.beginPath();cx.moveTo(X,Y+j*TILE+.5);cx.lineTo(X+Wd,Y+j*TILE+.5);cx.stroke();}
  cx.restore();
  const dim = (life==='COLD'||life==='EMPTY'||life==='ALIAS');
  cx.save(); cx.globalAlpha = dim ? 0.4 : 1; cx.strokeStyle=S.accent; cx.lineWidth=2;
  if(life==='EMPTY'||life==='ALIAS') cx.setLineDash([6,5]);
  cx.strokeRect(X+1,Y+1,Wd-2,H-2); cx.setLineDash([]); cx.restore();
  cx.strokeStyle='rgba(255,255,255,.12)'; cx.lineWidth=2; cx.beginPath(); cx.moveTo(X+4,Y+H-4); cx.lineTo(X+4,Y+4); cx.lineTo(X+Wd-4,Y+4); cx.stroke();
  cx.strokeStyle='rgba(0,0,0,.5)'; cx.beginPath(); cx.moveTo(X+4,Y+H-4); cx.lineTo(X+Wd-4,Y+H-4); cx.lineTo(X+Wd-4,Y+4); cx.stroke();
  cx.fillStyle=S.accent; const b=8;
  cx.fillRect(X,Y,b,3); cx.fillRect(X,Y,3,b); cx.fillRect(X+Wd-b,Y,b,3); cx.fillRect(X+Wd-3,Y,3,b);
  cx.fillRect(X,Y+H-3,b,3); cx.fillRect(X,Y+H-b,3,b); cx.fillRect(X+Wd-b,Y+H-3,b,3); cx.fillRect(X+Wd-3,Y+H-b,3,b);
  if(label){ const k=zs(); cx.save(); cx.translate(X,Y); cx.scale(k,k);   // the tab keeps its screen size
    cx.font=cfont(16,{display:true}); cx.textAlign='left';
    const tw = Math.ceil(cx.measureText(label).width)+16;
    cx.fillStyle='#04060c'; cx.fillRect(2, -22, tw+2, 24);
    cx.fillStyle=S.accent; cx.fillRect(0, -24, tw, 24);
    cx.fillStyle='#04060c'; cx.fillText(label, 8, -7);
    if(right&&Wd/k>tw+120){ cx.fillStyle=S.ink; cx.globalAlpha=.85; cx.font=cfont(12); cx.textAlign='right'; cx.fillText(right, Wd/k-6, -8); cx.globalAlpha=1; }
    if(mark){ const mx=tw+4, my=-24; cx.fillStyle='#ffb020'; cx.fillRect(mx,my,24,24); cx.fillStyle='#04060c';
      cx.font=cfont(16,{display:true}); cx.textAlign='center'; cx.fillText('!', mx+12, my+17); }
    cx.restore();
  }
}
// The backdrop is deliberately quieter than the foreground bodies and status badges.
function drawNebula(t){
  if(GB||(CM.backdropOn&&CM.backdropOn())) return;     // the baked nebula of this machine painted the sky already (ui/kinds.js, the planet kinds module)
  // Deep space with three still, dark nebula clouds; the saturated colour belongs to the bodies.
  cx.fillStyle=SPACE.base; cx.fillRect(0,0,VW,VH);
  const drift=0, R=Math.max(VW,VH), NB=SPACE.nebula;
  [[NB[0],.28,.30,.62],[NB[1],.78,.72,.55],[NB[2],.86,.14,.40]].forEach(([c,fx,fy,s],i)=>{
    const x=VW*fx+Math.cos(drift+i*2.1)*VW*.05-((camx*.015)%VW), y=VH*fy+Math.sin(drift*1.3+i)*VH*.05-((camy*.015)%VH);
    const g=cx.createRadialGradient(x,y,0,x,y,R*s); g.addColorStop(0,c+'58'); g.addColorStop(.5,c+'22'); g.addColorStop(1,c+'00');
    cx.fillStyle=g; cx.fillRect(0,0,VW,VH); });
}
function drawScanlines(){
  // Crisp geometry supplies the pixel language without a filter over evidence or type; a pack may ask
  // for CRT scanlines (phosphor), and only then: one dark line in three over the map.
  if(GB||!SPACE.scanlines) return;
  cx.save(); cx.fillStyle='rgba(0,0,0,.26)';
  for(let y=0;y<VH;y+=3) cx.fillRect(0,y,VW,1);
  cx.restore();
}
function questMarks(){
  Object.keys(QUEST_HITS).forEach(k=>{ QUEST_HITS[k]=false; });
  const qs = W.quests||[]; if(!qs.length) return;
  const names = HALLS.map(h=>[h.label, h.key]).concat((W.districts||[]).map(d=>[d.label, d.uid.replace('area:','')]));
  names.forEach(([label,name])=>{ if(!name||name.length<3) return;
    const rx = new RegExp('(^|[^a-z0-9_])'+name.replace(/[^A-Za-z0-9_-]/g,'')+'([^a-z0-9_]|$)','i');
    if(qs.some(q=>rx.test(q.text))) QUEST_HITS[label]=true; });
}
// ---- SYSTEM: the container as a star system. ----------------------------------------------
// The machine we stand on is the star. Its directories are moons on three orbits: touched in the
// last two days = close, within a month = further, cold = out at the rim (distance is mtime age,
// nothing else). Rooms are satellites of their moon; cron lines and processes ride the moon's outer
// ring. Seats dock on the innermost orbit with the hub's rooms. Runners are the allied fleet in the
// orange sector. Other machines are planets at the end of warp routes; relay channels are beacons
// along the route to the machine they name. Nothing moves on its own except what is really running.
const LY = {star:{x:0,y:0,r:96}, dock:250, orbits:{LIVE:440, WARM:780, COLD:1120},
            fleet:{x:-980,y:-640}, planets:{}};   // planets are placed per machine at layout time
const MOONS = [];
// ---- LAYOUT = PLACE. The star is /work on this machine. Every root is a PLANET on one orbit,
// grouped into SECTOR WEDGES by the owner's own kind (areas.json); each planet's first-level
// directories are its MOONS on a ring; each moon's contents are SATELLITES that appear as you
// approach; cron lines and processes SWARM the room they write into (a log dump is visible as a
// swarm). Age is brightness now, not distance: dim = warm, red-ringed = cold (reference, archive, knowledge and bus planets are never ringed), dashed = empty.
const SECTOR_ORDER = ['code','knowledge','shared','bus','config','reference','archive','unknown'];
// Folders and agents keep their layout positions until data changes; surfaces may still turn.
function orbitW(d,belt){ return Math.PI*2/(belt?300:(110+d*1.1)); }
// DECAY: how far a body has gone quiet. 0 under three days since its newest write, 1 at two months.
// It is the one number behind the fade, the cracks, the haze and the outward drift of a planet.
function ageDecay(age){ if(age==null||!Number.isFinite(Number(age))) return 0; return Math.max(0,Math.min(1,(Number(age)-2)/58)); }
// Decay is a verdict only where something should keep changing. Reference, archive, knowledge and
// sync-bus planets are meant to sit still: they keep their age as a plain fact ("quiet · Nd"),
// with no fade, cracks, haze or drift. Code, config, shared (and unclassified) planets decay in full.
const STILL_KINDS=['reference','archive','knowledge','bus'];
function sitsStill(kind){ return STILL_KINDS.includes(kind); }
function decayOf(kind,age){ return sitsStill(kind)?0:ageDecay(age); }
const BAD_VERDICTS=['STALE','NEVER','NO RECEIPT'];
// GRAVITY: a body's mass is its size on disk. From 100 MB up it has a well: rings you can see from
// afar, and a pull on your ship when you fly inside it. Bloat (rebuildable caches: node_modules,
// venvs, build output) tints the well purple; sheer dead weight is grey. A folder full of junk
// is heavy, and you feel it before you read a number. Boost (Shift) always escapes.
function parseSize(s){ const m=/^([0-9.]+)\\s*([KMGTP]?)B?$/i.exec(String(s||'').trim()); if(!m) return 0;
  return parseFloat(m[1])*({'':1,K:1e3,M:1e6,G:1e9,T:1e12,P:1e15}[m[2].toUpperCase()]||1); }
function massOf(bytes){ return Math.max(0,Math.log10(Math.max(1,bytes)/1e6)); }   // 0 at 1 MB, 2 at 100 MB, 3 at 1 GB, 4 at 10 GB
function wellRadius(r,mass){ return mass>=2?r+60*mass:0; }
function fmtBytes(b){ return b>=1e9?(b/1e9).toFixed(1)+' GB':b>=1e6?Math.round(b/1e6)+' MB':b>=1e3?Math.round(b/1e3)+' KB':Math.round(b)+' B'; }
let GRAV=null;   // {label,k} while the ship is being pulled
function orbOf(u,ox,oy,w){ const dx=u.gx*TILE-ox, dy=u.gy*TILE-oy; return {x:ox,y:oy,a:Math.atan2(dy,dx),d:Math.hypot(dx,dy),w:w}; }
function orbitTick(t){
  const list=deck==='home'?units:remoteUnits, s=0;
  for(const u of list){ const o=u.orb; if(!o) continue; const a=o.a+o.w*s;
    u.gx=(o.x+Math.cos(a)*o.d)/TILE; u.gy=(o.y+Math.sin(a)*o.d)/TILE; u.bgx=u.gx; u.bgy=u.gy; }
}
// What a deck's planets are measured against: trend, board items and the home planet of ONE world. The home
// deck reads this machine's world; a remote deck must read the synced world it was built from, or a planet
// that shares a name with a home planet inherits its storm ring, problems and HOME marker.
function homeScope(){ return {trend:W.trend||{}, items:taskItems(), home:String((W.wingmate&&W.wingmate.home_planet)||'')}; }
function syncedScope(s){ const it=s&&s.tasks&&s.tasks.items; return {trend:(s&&s.trend)||{}, items:Array.isArray(it)?it:[], home:String((s&&s.wingmate&&s.wingmate.home_planet)||'')}; }
const LAYOUT_SPREAD=.18;
function orbitOf(R,name){ return Math.round(R*(1+LAYOUT_SPREAD*(2*hash3(seedOf(String(name)),7,3,1)-1))); }
// the system laid to the map's shape: wider than tall on a desktop map, taller than wide on a phone held upright, so
// the overview fills the map the way the comps do; the vector look keeps its round plane (its tilt is its shape). On a desktop
// the shape is settled with the first view (layoutShapeSettle: the cards over the stage are in place by then) and kept: the
// system never re-lays itself once the owner has moved the view
const LAYOUT_SHAPE={aspect:null};
function layoutAspect(){ if(VEC) return [1,1]; if(MOB&&!MOB_LAND) return [1,1.7]; if(LAYOUT_SHAPE.aspect) return LAYOUT_SHAPE.aspect;
  const tall=typeof innerHeight==='number'&&innerHeight>innerWidth*1.1; return tall?[1,1.35]:[1.35,1]; }
// the shape that fits the map with its cards: the window's own first, then round, then the other way; the first whose home view
// clears every card wins, else the one with the fewest planets under one (a small window with the old cards over it)
function layoutShapeSettle(covers){
  const first=layoutAspect(), cands=[first,[1,1],first[0]>1?[1,1.35]:[1.35,1]]; let best=null;
  for(const A of cands){ LAYOUT_SHAPE.aspect=A; place(); const h=homeView(covers); if(!best||h.under<best.under) best={A:A,under:h.under}; if(h.under===0) break; }
  if(best.A!==LAYOUT_SHAPE.aspect){ LAYOUT_SHAPE.aspect=best.A; place(); }
}
function layoutSystem(list, moons, starName, scope){
  const SC=scope||homeScope();
  moons.length = 0;
  const localName=String(starName||W.name||'').toLowerCase();
  const roomsOf = {}, tasksOf = {};
  list.forEach(u=>{ if(u.kind==='dir'){ (roomsOf[u.area]=roomsOf[u.area]||[]).push(u); }
    else if(u.kind==='cron'||u.kind==='proc'){ (tasksOf[u.area]=tasksOf[u.area]||[]).push(u); } });
  const bodies = list.filter(u=>u.kind==='area').map(a=>{
    // a planet's size is its size on disk, as a sphere holds it (cube root: ~40 px for a few KB, 82 px at
    // 100 MB, 188 px at 3.5 GB, 272 px at 13 GB, at most 300); a planet with no size known goes by its file count
    const files=a.files||0, onDisk=a.alias_of?0:parseSize(a.size),
      r=a.alias_of?20:Math.round(Math.min(300,onDisk>0?36+10*Math.cbrt(onDisk/1e6):36+16*Math.log10(1+files)));
    const rooms=roomsOf[a.uid]||[], tasks=tasksOf[a.uid]||[];
    const swarm = rm => tasks.filter(tk=>tk.room===rm.name).length;
    const roomR = rm => rm.files==null?7:Math.max(6,Math.min(18,Math.round(4+3*Math.log10(1+(rm.files||0)))));
    const sizeR = rm => { const n=swarm(rm); return Math.max(roomR(rm), n?Math.ceil(n*18/(2*Math.PI))+6:0); };
    const ringR=Math.max(r+70, rooms.reduce((s,rm)=>s+sizeR(rm)*2+36,0)/(2*Math.PI));
    const loose=tasks.filter(tk=>!tk.room||!rooms.some(rm=>rm.name===tk.room));
    const outerR=loose.length?Math.max(ringR+40, loose.length*26/(2*Math.PI)):0;
    const extent=Math.max(r, rooms.length?ringR+Math.max(...rooms.map(sizeR))+8:0, outerR);
    const kind=BLOCK_KIND[a.uid]||'unknown', decay=a.alias_of?0:decayOf(kind,a.age);
    const bytes=onDisk, mass=massOf(bytes), bloat=Number(a.regen_share||0);
    return {a,files,r,rooms,tasks,loose,ringR,outerR,extent,kind,roomR,sizeR,swarm,decay,bytes,mass,bloat,weight:Math.max(extent,wellRadius(r,mass)*.6)*1.1+120,
      order:(SECTOR_ORDER.indexOf(kind)<0?SECTOR_ORDER.length:SECTOR_ORDER.indexOf(kind))};
  }).sort((x,y)=>x.order-y.order||y.files-x.files);
  const sum=bodies.reduce((n,b)=>n+b.weight,0);
  // the star is the machine that holds every planet: always the biggest body, the docked craft on a ring clear of it
  const biggest=bodies.reduce((n,b)=>Math.max(n,b.r),0);
  LY.star.r=Math.max(150,Math.round(biggest*1.2)); LY.dock=LY.star.r+170;
  // the roots ring: wide enough that planets sit in space, not on each other, and clear of the dock ring
  const R=Math.ceil(Math.max(1100, sum/Math.PI, LY.dock+biggest+260));
  LY.rootOrbit=R; LY.wedges=[]; LY.tilt=VEC?TILT_K:1;     // the vector look lays the system on a tilted plane: project() below
  // other machines sit well outside the roots ring, never on it
  const far=R+820; LY.planets={};
  list.filter(u=>u.kind==='machine' && String(u.name).toLowerCase()!==localName).forEach((u,i)=>{ const ang=-0.55+i*1.05;
    LY.planets[u.name]=project(Math.round(Math.cos(ang)*far),Math.round(Math.sin(ang)*far)); });
  LY.fleet=project(Math.round(Math.cos(-2.35)*(R+520))-180,Math.round(Math.sin(-2.35)*(R+520))-140);
  let angle=-Math.PI/2, wedge=null;
  bodies.forEach(b=>{
    const half=b.weight/sum*Math.PI, a0=angle, a1=angle+2*half;
    if(!wedge||wedge.kind!==b.kind){ wedge={kind:b.kind,a0:a0,a1:a1,n:0}; LY.wedges.push(wedge); } wedge.a1=a1; wedge.n++;
    // a quiet planet drifts outward, off the ring, by up to 160 px: neglect you can see from afar. Each planet also sits a
    // little in or out of the ring by a hash of its name (LAYOUT_SPREAD, the overview reads as a scattered system,
    // not a clock face; 0 puts every planet back on one ring); the room each planet's sector reserves keeps them apart
    const mid=angle+half, Rb=orbitOf(R,b.a.name)+b.decay*160, AS=layoutAspect(), x=LY.star.x+Math.cos(mid)*Rb*AS[0], y=project(x,LY.star.y+Math.sin(mid)*Rb*AS[1]).y;
    b.a.gx=x/TILE; b.a.gy=y/TILE;
    let th=mid+Math.PI;                                     // the room ring starts on the star-facing side
    const belt=b.kind==='archive';                          // the archive's rooms scatter through a belt
    b.rooms.forEach(rm=>{
      const sz=b.sizeR(rm), arc=(sz*2+22)/b.ringR; th+=arc/2;
      const rs=seedOf(rm.name), d=belt?b.ringR*(.58+hash3(rs,5,7,11)*.84):b.ringR, ta=belt?th+(hash3(rs,9,3,5)-.5)*arc*.9:th;
      const rx=x+Math.cos(ta)*d, ry=y+Math.sin(ta)*d;
      rm.gx=rx/TILE; rm.gy=ry/TILE; rm.sr=b.roomR(rm); rm.px=x; rm.py=y;
      rm.orb={x:x,y:y,a:ta,d:d,w:orbitW(d,belt)};
      const mine=b.tasks.filter(tk=>tk.room===rm.name);
      mine.forEach((tk,k)=>{ const ta=k*2*Math.PI/mine.length+th, tr=Math.max(rm.sr+12, sz-2);
        tk.gx=(rx+Math.cos(ta)*tr)/TILE; tk.gy=(ry+Math.sin(ta)*tr)/TILE; tk.orb=orbOf(tk,x,y,rm.orb.w); });
      th+=arc/2;
    });
    b.loose.forEach((tk,j)=>{ const ta=j*2*Math.PI/b.loose.length+.5; tk.gx=(x+Math.cos(ta)*b.outerR)/TILE; tk.gy=(y+Math.sin(ta)*b.outerR)/TILE;
      tk.orb=orbOf(tk,x,y,orbitW(b.outerR,false)); });
    const pt=planetType(b.a, list, b.tasks, SC.items);
    moons.push({uid:b.a.uid,label:b.a.name,x:x,y:y,r:b.r,kind:b.kind,life:b.a.life,files:b.files,age:b.a.age,decay:b.decay,
      trend:(SC.trend&&SC.trend[b.a.name])||null,home:!!SC.home&&b.a.name===SC.home,bytes:b.bytes,mass:b.mass,bloat:b.bloat,wellR:wellRadius(b.r,b.mass),
      seats:(b.a.seats||[]).length,worker:b.a.worker||null,hp:b.a.hp||0,ringR:b.rooms.length?b.ringR:0,
      outerR:b.outerR,orbit:orbitOf(R,b.a.name),orbitClass:'ROOT',extent:b.extent,type:pt[0],typeWhy:pt[1],trouble:pt[2],rooms:b.rooms,tasks:b.tasks,
      purpose:shortPurpose(b.a.tag_desc),allies:0,enemies:0,wrecks:0,foes:[]});
    angle=a1;
  });
  const nowhere=list.filter(u=>(u.kind==='cron'||u.kind==='proc')&&(!u.area||!list.some(a=>a.kind==='area'&&a.uid===u.area)));
  nowhere.forEach((tk,j)=>{ const ta=j*2*Math.PI/Math.max(1,nowhere.length)+0.3, rr=LY.dock+64; tk.gx=(LY.star.x+Math.cos(ta)*rr)/TILE; tk.gy=project(0,LY.star.y+Math.sin(ta)*rr).y/TILE; });
  // Worst-class: the single most troubled planet, its two worst rooms pulled in as close satellites
  const worst=moons.filter(m=>m.trouble>=3).sort((a,b)=>b.trouble-a.trouble)[0];
  if(worst){ worst.worst=true;      // the most troubled planet keeps its body: its trouble is the aura (ui/kinds.js), never a type
    const score=rm=>worst.tasks.filter(tk=>tk.room===rm.name&&['STALE','NEVER','NO RECEIPT'].includes(tk.verdict)).length*2+(SC.items.filter(t=>t.path&&rm.path&&String(t.path).startsWith(rm.path)).length);
    worst.rooms.slice().sort((a,b)=>score(b)-score(a)).slice(0,2).forEach((rm,i)=>{ if(score(rm)<=0) return; const ang=Math.atan2(worst.y-LY.star.y,worst.x-LY.star.x)+Math.PI+(i?0.7:-0.7);
      rm.gx=(worst.x+Math.cos(ang)*(worst.r+20))/TILE; rm.gy=(worst.y+Math.sin(ang)*(worst.r+20))/TILE; rm.satellite=true;
      rm.orb=orbOf(rm,worst.x,worst.y,orbitW(worst.r+20,false)); }); }
  // a seat working inside an area sits AT that planet, not at the star: that is where it can act
  const areasByPath=list.filter(u=>u.kind==='area'&&u.path).sort((a,b)=>b.path.length-a.path.length);
  const onPlanet=new Set(), hangar=!scope&&SKY&&skyOk();
  list.filter(u=>isShip(u)&&(u.cwd||CM.dockOverride(u))&&!(hangar&&atRest(u))).forEach(u=>{ const o=CM.dockOverride(u), a=o?{uid:o}:areasByPath.find(x=>u.cwd===x.path||String(u.cwd).startsWith(x.path+'/')); const m=a&&moons.find(mm=>mm.uid===a.uid); if(!m) return;
    const k=(m.docked=(m.docked||0)); const ang=Math.atan2(m.y-LY.star.y,m.x-LY.star.x)+Math.PI+0.9+k*0.5; u.gx=(m.x+Math.cos(ang)*(m.r+22))/TILE; u.gy=(m.y+Math.sin(ang)*(m.r+22))/TILE; u.docked_at=m.uid; m.docked=k+1; onPlanet.add(u.uid); });
  list.filter(u=>u.kind==='task').forEach((u,i)=>{ const a=areasByPath.find(x=>u.target===x.path||String(u.target).startsWith(x.path+'/')); const m=a&&moons.find(mm=>mm.uid===a.uid);
    if(!m){ const ang=Math.PI*.75+i*.35, rr=LY.dock+40; u.gx=(LY.star.x+Math.cos(ang)*rr)/TILE; u.gy=project(0,LY.star.y+Math.sin(ang)*rr).y/TILE; return; }
    const k=(m.tdock=(m.tdock||0)); const ang=Math.atan2(m.y-LY.star.y,m.x-LY.star.x)+Math.PI-0.95-k*0.42, rr=m.r+36;
    u.gx=(m.x+Math.cos(ang)*rr)/TILE; u.gy=(m.y+Math.sin(ang)*rr)/TILE; u.docked_at=m.uid; m.tdock=k+1; });
  const home=String((W.wingmate&&W.wingmate.mothership)||'hub');
  const dock = list.filter(u=>u.kind==='room').concat(list.filter(u=>isShip(u)&&!u.machine&&!onPlanet.has(u.uid)))
    .sort((a,b)=>(b.kind==='seat'&&b.name===home)-(a.kind==='seat'&&a.name===home))   // the carrier first: straight above the star
    .concat(list.filter(isBerth));                                                      // then the empty berths
  const dockR = Math.max(LY.dock, dock.length*56/(2*Math.PI));
  dock.forEach((u,i)=>{ const ang = -Math.PI/2 + i*(2*Math.PI/Math.max(1,dock.length));
    u.gx=(LY.star.x+Math.cos(ang)*dockR)/TILE; u.gy=project(0,LY.star.y+Math.sin(ang)*dockR).y/TILE; });
  list.filter(u=>u.kind==='runner').forEach((u,i)=>{ u.gx=(LY.fleet.x + (i%4)*104 + ((i/4|0)%2)*12)/TILE; u.gy=(LY.fleet.y + (i/4|0)*84)/TILE; });
  list.filter(u=>u.kind==='machine').forEach(u=>{ const p = LY.planets[u.name];
    if(p){ u.gx=p.x/TILE; u.gy=p.y/TILE; } else { u.gx=(LY.star.x+46)/TILE; u.gy=(LY.star.y-52)/TILE; } });
  list.filter(u=>u.kind==='portal').forEach(u=>{ const p = LY.planets[u.machine]; if(p){ u.gx=p.x/TILE; u.gy=(p.y+110)/TILE; } });
  list.filter(u=>u.kind==='ship'&&u.machine).forEach((u,i)=>{ const p=LY.planets[u.machine]||{x:LY.star.x+290,y:LY.star.y+i*70}; u.gx=(p.x+92)/TILE; u.gy=p.y/TILE; });
  const perRoute = {};
  list.filter(u=>u.kind==='channel').forEach(u=>{
    const m = Object.keys(LY.planets).find(k=>String(u.name).includes(k)), p = m ? LY.planets[m] : null;
    if(p){ const k = (perRoute[m]=(perRoute[m]||0)+1), f = 0.30 + 0.16*k;
      u.gx=(LY.star.x+(p.x-LY.star.x)*f)/TILE; u.gy=(LY.star.y+(p.y-LY.star.y)*f)/TILE; }
    else { const k = (perRoute._=(perRoute._||0)+1), ang = Math.PI*0.12 + k*0.42;
      u.gx=(LY.star.x+Math.cos(ang)*255)/TILE; u.gy=project(0,LY.star.y+Math.sin(ang)*255).y/TILE; }
  });
  // BATTLEFIELDS: a planet's problems are its foes (stale cron lines, open board items, failed
  // tasks); the task ships docked there that are working (running, being checked, stopping) are its
  // defenders: one waiting for review does nothing. Both counted, nothing invented.
  moons.forEach(m=>{
    const foes=[];
    m.tasks.filter(tk=>tk.kind==='cron'&&BAD_VERDICTS.includes(tk.verdict)).forEach(tk=>foes.push({kind:'cron',name:tk.name,uid:tk.uid}));
    SC.items.filter(it=>it.area===m.label).forEach(it=>foes.push({kind:'item',name:String(it.finding||it.id||'').slice(0,60),full:String(it.finding||it.id||''),uid:'quest:'+it.id}));
    list.filter(u=>u.kind==='task'&&u.docked_at===m.uid).forEach(u=>{
      if(u.status==='failed'||u.status==='lost') foes.push({kind:'wreck',name:u.name,uid:u.uid});
      else if(TASK_WORKING.includes(u.status)) m.allies++; });
    m.foes=foes; m.enemies=foes.length; m.wrecks=foes.filter(f=>f.kind==='wreck').length;
  });
  // SKY: on the home deck in sky mode the sky's contacts join the foes. A problem the sky already carries under its contract
  // id (the same cron line, board item or failed thread) is drawn once, as the contact; one it does not carry keeps its old
  // craft, so nothing the map knew is lost. A contact with no planet circles the star.
  if(!scope){ SKY_STAR.foes=[]; SKY_STAR.enemies=0;
    if(SKY&&skyOk()){
      const contacts=skyContacts(), ids=new Set(contacts.map(c=>c.id)), byMoon={};
      contacts.forEach(c=>{ const m=contactMoon(c,moons,list,areasByPath,SC.items), age=skyAge(c);
        const f={kind:'contact',src:String(c.source||''),state:String(c.state||''),tier:skyTier(c),
          name:((age?age+' · ':'')+String(c.title||c.id)).slice(0,60),full:String(c.title||c.id),uid:c.id,contact:c};
        if(m) (byMoon[m.uid]=byMoon[m.uid]||[]).push(f); else SKY_STAR.foes.push(f); });
      const retrying=new Set(list.filter(u=>u.kind==='task'&&warpHidden(u)&&u.status==='failed').map(u=>u.uid));   // a due retry is in warp, no wreck
      moons.forEach(m=>{ m.foes=m.foes.filter(f=>!legacyKeys(f).some(k=>ids.has(k))&&!(f.kind==='wreck'&&retrying.has(f.uid))).concat(byMoon[m.uid]||[]);
        m.enemies=m.foes.length; m.wrecks=m.foes.filter(f=>f.kind==='wreck').length; });
      SKY_STAR.enemies=SKY_STAR.foes.length;
    } }
  // each deck keeps its own geometry; draw() restores the displayed deck's before drawing, so a
  // home refresh can no longer paint the home wedges under a remote deck's planets
  moons.geo={R:R, wedges:(LY.wedges||[]).slice(), planets:Object.assign({},LY.planets), fleet:Object.assign({},LY.fleet), starR:LY.star.r, dock:LY.dock, k:LY.tilt};
}
// FLOWS: direction between files. A cron line's script lives in one place and writes its log in
// another; that is an arrow, aggregated per (from, to) pair with its count. Nothing else is drawn
// as a flow because nothing else on disk states a direction. Key F toggles.
let FLOWS = true;
function flowPairs(list){
  const pairs = {};
  list.filter(u=>u.kind==='cron'&&u.script_area&&u.area&&u.script_area!==u.area).forEach(u=>{
    const k=u.script_area+'>'+u.area+'>'+(u.room||''); (pairs[k]=pairs[k]||{from:u.script_area,to:u.area,room:u.room||'',n:0}).n++; });
  return Object.values(pairs);
}
// a planet's plate shows what the folder is FOR (the roots map's line), cut to its first clause
function shortPurpose(d){ d=String(d||'').split(/[;(]|\\s—\\s|: /)[0].replace(/`/g,'').trim(); return d.length>46?d.slice(0,45)+'…':d; }
function drawFlows(list, moons){
  if(!FLOWS||GB||QUIET) return;
  const at = uid => { const m=moons.find(m=>m.uid===uid); return m?{x:m.x,y:m.y}:null; };
  flowPairs(list).forEach(f=>{
    const a=at(f.from); let b=null;
    if(f.room){ const rm=list.find(u=>u.kind==='dir'&&u.area===f.to&&u.name===f.room); if(rm) b={x:rm.gx*TILE,y:rm.gy*TILE}; }
    if(!b) b=at(f.to); if(!a||!b) return;
    cx.save(); cx.strokeStyle='rgba(255,189,85,.35)'; cx.lineWidth=1.5; cx.setLineDash([4,10]);
    cx.beginPath(); cx.moveTo(a.x,a.y); cx.lineTo(b.x,b.y); cx.stroke(); cx.setLineDash([]);
    const ang=Math.atan2(b.y-a.y,b.x-a.x), hx=b.x-Math.cos(ang)*22, hy=b.y-Math.sin(ang)*22;
    cx.fillStyle='rgba(255,189,85,.8)'; cx.beginPath(); cx.moveTo(hx+Math.cos(ang)*8,hy+Math.sin(ang)*8);
    cx.lineTo(hx+Math.cos(ang+2.4)*7,hy+Math.sin(ang+2.4)*7); cx.lineTo(hx+Math.cos(ang-2.4)*7,hy+Math.sin(ang-2.4)*7); cx.fill();
    // the note only once it reads at 12 px without crowding the overview; the line and arrow always
    if(ZOOM>=.5){ const k=zs(), mx=(a.x+b.x)/2, my=(a.y+b.y)/2; cx.font=wfont(12); cx.textAlign='center';
      const txt=f.n+' cron'+(f.n>1?'s':'')+' write here', w=cx.measureText(txt).width+8*k;
      const P=plateUi(); cx.fillStyle=P.bg; cx.fillRect(mx-w/2,my-10*k,w,18*k); cx.fillStyle=inkOn('#ffbd55',P.bg,P.ink); cx.fillText(txt,mx,my+4*k); }
    cx.restore();
  });
}
// the system: the planets and the sun, nothing between them. The overview draws no routes to other machines (the
// machines are not drawn on the 2D map: the Machines chip lists them), no sector wedges or names, no dashed orbits and no
// folder links; the focus view (ui/focus.js) adds the open planet's stations and craft. Labels queue for runPlates.
function drawSystem(t, moons, list, starName, quests, oldest){
  PLATE_DISCS=moons.map(m=>{ const r=fcDispR(m); return {x:m.x,y:m.y,r:r,r0:r}; });   // tags dodge the bodies (the craft register their own hulls)
  if(VEC){ vecSystem(t,moons,list,starName,quests,oldest); return; }
  moons.forEach(m=>drawMoon(m,t));
  drawStar(t, starName, quests, oldest);
  if(deck==='home') fcIdleStack(t);
}
function nearestBody(x, y, moons, starName){
  let best=null, bd=Infinity;
  const consider = (label, kind, cxp, cyp, rad) => { const d=Math.hypot(x-cxp,y-cyp); if(d<=rad && d<bd){ bd=d; best={label:label, kind:kind}; } };
  { const q=unproject(x,y), d=Math.hypot(q.x-LY.star.x,q.y-LY.star.y);   // the star's reach is a ring on the plane: measured there
    if(d<=LY.dock+40&&d<bd){ bd=d; best={label:starName, kind:'the star · '+(starName===(W.name||'CONTAINER')?'this machine':'that machine')}; } }
  moons.forEach(m=>consider(m.label.toUpperCase()+' moon', m.kind, m.x, m.y, Math.max(m.outerR, m.ringR, m.r)+24));
  Object.keys(LY.planets).forEach(k=>consider(k.toUpperCase()+' planet', 'another machine', LY.planets[k].x, LY.planets[k].y, 96));
  return best;
}
// ---- BODIES: one 2px raster language for every planet, moon, rock, ship and file icon. -------
// The look follows evidence on disk (root type, folder name, file kind). The motion is decoration
// and nothing else: surfaces turn, clouds drift, belts tumble, engines burn only on a ship that is
// working. prefers-reduced-motion freezes all of it. Art is cached per animation frame (LRU).
const BODY_ART = new Map();
let FRAME_T = 0;
const TAU = Math.PI*2;
function mixPixel(a,b,k){
  const rgb=s=>[1,3,5].map(i=>parseInt(s.slice(i,i+2),16));
  const x=rgb(a),y=rgb(b);
  return '#'+x.map((n,i)=>Math.round(n+(y[i]-n)*k).toString(16).padStart(2,'0')).join('');
}
function artCache(key, make){
  let a=BODY_ART.get(key);
  if(a){ BODY_ART.delete(key); BODY_ART.set(key,a); return a; }
  a=make(); if(BODY_ART.size>=1800) BODY_ART.delete(BODY_ART.keys().next().value);
  BODY_ART.set(key,a); return a;
}
function seedOf(s){ let h=7; for(const c of String(s||'')) h=(h*31+c.charCodeAt(0))>>>0; return h; }
function hash3(x,y,z,s){
  let h=(Math.imul(x|0,374761393)+Math.imul(y|0,668265263)+Math.imul(z|0,1274126177)+Math.imul(s|0,1442695041))|0;
  h=Math.imul(h^(h>>>13),1274126177); h^=h>>>16; return (h>>>0)/4294967295;
}
function vnoise(x,y,z,s){
  const xi=Math.floor(x),yi=Math.floor(y),zi=Math.floor(z),xf=x-xi,yf=y-yi,zf=z-zi;
  const u=xf*xf*(3-2*xf),v=yf*yf*(3-2*yf),w=zf*zf*(3-2*zf),c=(i,j,k)=>hash3(xi+i,yi+j,zi+k,s),L=(a,b,t)=>a+(b-a)*t;
  return L(L(L(c(0,0,0),c(1,0,0),u),L(c(0,1,0),c(1,1,0),u),v),L(L(c(0,0,1),c(1,0,1),u),L(c(0,1,1),c(1,1,1),u),v),w);
}
// noise on a sphere: sample at the 3-D point itself, so a turning surface has no seam
function sfbm(lon,lat,s,k){
  const cl=Math.cos(lat),x=Math.cos(lon)*cl*k,y=Math.sin(lat)*k,z=Math.sin(lon)*cl*k;
  return vnoise(x,y,z,s)*.62+vnoise(x*2.3+5,y*2.3,z*2.3,s+7)*.38;
}
const hex2=a=>Math.round(Math.max(0,Math.min(1,a))*255).toString(16).padStart(2,'0');
// File and folder kinds: one saturated colour pair each, named for what they are.
const THEMES={
  code:   {name:'code',    group:'source code',             base:'#1f6bff', land:'#22e07a', hi:'#b8ffd9', glow:'#3d86ff'},
  docs:   {name:'docs',    group:'notes and documents',     base:'#00a8e8', land:'#3cf2e2', hi:'#d2fffb', glow:'#1cd0ff'},
  config: {name:'config',  group:'configuration',           base:'#8b4dff', land:'#ffa31a', hi:'#ecd6ff', glow:'#a46bff'},
  data:   {name:'data',    group:'structured data',         base:'#d6f4ff', land:'#2a95ff', hi:'#ffffff', glow:'#7fd4ff'},
  media:  {name:'media',   group:'images, audio and video', base:'#ff4f9a', land:'#ffd23f', hi:'#ffd6ea', glow:'#ff63b4'},
  logs:   {name:'logs',    group:'logs and receipts',       base:'#12c487', land:'#d8f23c', hi:'#d6fff0', glow:'#2dffb0'},
  archive:{name:'archive', group:'archives and packages',   base:'#ff8a3d', land:'#b8432a', hi:'#ffd6ad', glow:'#ff7a2e'},
  cache:  {name:'cache',   group:'rebuildable caches',      base:'#8f84ff', land:'#5640d4', hi:'#dcd8ff', glow:'#7f6bff'},
  live:   {name:'running', group:'running processes',       base:'#ff8a1f', land:'#ff3b30', hi:'#fff1a6', glow:'#ff9a2e'},
  binary: {name:'other',   group:'binaries, models, other', base:'#7090ff', land:'#2f3fb8', hi:'#d2dbff', glow:'#6f8cff'},
  trouble:{name:'trouble', group:'the most troubled root',  base:'#ff2d6f', land:'#6d1fd1', hi:'#ffc4d8', glow:'#ff2d6f'},
};
// Each root type (planetType) has a surface of its own.
const STYLE={
  city:   {a:'#1452ff', b:'#16d96a', c:'#f2fbff', d:'#ffd84a', glow:'#3d7bff'},
  ocean:  {a:'#0090d6', b:'#20e4dc', c:'#eaffff', glow:'#18c8ff'},
  desert: {a:'#ff9b3b', b:'#cf4a26', c:'#ffdba3', glow:'#ff8a3d'},
  ice:    {a:'#dcf6ff', b:'#2f9cff', c:'#ffffff', glow:'#8fdcff'},
  toxic:  {a:'#0fc27c', b:'#dcf23a', c:'#065f3f', glow:'#35ffa4'},
  blight:  {a:'#ff2b6a', b:'#6a1fd6', c:'#ffd3e2', glow:'#ff2b6a'},
  gas:    {a:'#8b7aff', b:'#ff6fd2', c:'#ffd9f3', d:'#4b35cf', glow:'#a07dff'},
  rock:   {a:'#8c96c6', b:'#454d78', c:'#cdd5f5', glow:'#8c96c6'},
  belt:   {a:'#c9743c', b:'#5a2c1b', c:'#ffbd73', glow:'#ff9a4d'},
  star:   {a:'#ffc53a', b:'#ff6f17', c:'#fff5be', glow:'#ffae2c'},
  station:{a:'#cdd6ff', b:'#7c5bff', c:'#4ff0ff', glow:'#b48cff'},
};
// A folder's colours come from its purpose (ui/kinds.js PURPOSE): THEMES carries one alias per purpose, 'p:code' ... 'p:unsorted'
// (not enumerable, so theme packs, the classic pin and the file kinds above are untouched), and a planet's type or kind maps to
// its purpose's alias. The file kinds above stay the colours of files by type (a .py, a .png), which are not folders.
PURPOSE_KEYS.forEach(p=>Object.defineProperty(THEMES,'p:'+p,{enumerable:false,get:()=>kindsTheme(p)}));
// STYLE carries the same aliases ('p:code' ... 'p:unsorted', not enumerable: applyTheme replaces only the surfaces, so the aliases
// stay and always read the active palette), so an old path asking STYLE for a folder's surface gets its purpose's colours
PURPOSE_KEYS.forEach(p=>Object.defineProperty(STYLE,'p:'+p,{enumerable:false,get:()=>kindsStyle(p)}));
const TYPE_THEME={city:'p:code',ocean:'p:notes',desert:'p:reference',ice:'data',toxic:'logs',blight:'trouble',station:'p:shared',asteroid:'p:archive',gas:'p:data',rock:'p:unsorted',aurora:'p:media',crystal:'p:config'};
const KIND_THEME=Object.fromEntries(Object.keys(KIND_PURPOSE).map(k=>[k,'p:'+KIND_PURPOSE[k]]));
const FILE_THEMES=[
  ['cache','lock pyc pyo map tmp temp cache','node_modules .venv venv __pycache__ .cache dist build target'],
  ['logs','log jsonl ndjson out err receipt','logs .logs receipts dispatch-reports'],
  ['code','py pyw js jsx ts tsx mjs cjs c h cc cpp hpp cs go rs java kt kts swift sh bash zsh fish ps1 rb php lua sql r jl ex exs erl hs clj scala dart vue svelte html css scss sass less ipynb','src lib app apps packages scripts tests test'],
  ['docs','md mdx txt rst org adoc pdf doc docx odt rtf tex epub','docs doc notes knowledge brain vault'],
  ['config','json yaml yml toml ini conf cfg xml env properties service timer plist desktop','config .config .github .codex .claude'],
  ['data','csv tsv parquet arrow feather db sqlite sqlite3 duckdb h5 hdf5 xls xlsx npy npz','data datasets database databases'],
  ['media','png jpg jpeg gif webp avif bmp ico svg tiff tif heic mp4 mov mkv webm avi mp3 wav flac ogg m4a aac woff woff2 ttf otf blend obj gltf glb','assets media images img audio video fonts'],
  ['archive','zip gz bz2 xz zst tar tgz 7z rar deb rpm dmg iso apk whl','archive archives backups attic reference'],
  ['binary','bin exe dll so dylib wasm safetensors gguf pt pth onnx','bin binaries models']
].map(([skin,ext,dirs])=>({skin,ext:new Set(ext.split(' ')),dirs:new Set(dirs.split(' '))}));
function fileTheme(name,isDir=false,fallback='binary'){
  const n=String(name||'').split(' → ')[0].split('/').pop().toLowerCase();
  if(isDir){const g=FILE_THEMES.find(g=>g.dirs.has(n));return g?g.skin:(THEMES[fallback]?fallback:'binary');}
  if(['readme','license','licence','changelog','authors','todo'].includes(n))return 'docs';
  if(['dockerfile','makefile','cmakelists.txt','.gitignore','.gitattributes','.editorconfig','.env','package-lock.json','yarn.lock','pnpm-lock.yaml','uv.lock'].includes(n))
    return n.includes('lock')?'cache':'config';
  const dot=n.lastIndexOf('.'),ext=dot>=0?n.slice(dot+1):'';
  const g=FILE_THEMES.find(g=>g.ext.has(ext));return g?g.skin:'binary';
}
function unitTheme(u){
  if(!u)return 'binary';
  if(u.kind==='area'){
    const m=(u.remote?remoteMoons:MOONS).find(m=>m.uid===u.uid);
    return m&&TYPE_THEME[m.type]||KIND_THEME[u.area_kind]||'binary';
  }
  if(u.kind==='dir')return fileTheme(u.name,true,KIND_THEME[BLOCK_KIND[u.area]]||'binary');
  if(u.kind==='proc')return 'live';
  return 'binary';
}
const SHADES={};
function shades(base){
  const k=base+(GB?'g':''); if(SHADES[k]) return SHADES[k];
  return SHADES[k]=GB?['#0f380f','#306230','#8bac0f','#9bbc0f','#9bbc0f']:
    [mixPixel(base,SHADE.dark[0],.80),mixPixel(base,SHADE.dark[1],.54),mixPixel(base,SHADE.dark[2],.24),base,mixPixel(base,SHADE.hi,.5)];
}
// one sphere's cells per radius and squash: normal, longitude, latitude and a fixed light
const GEO=new Map();
function sphereGeo(r,sq){
  const k=r+'|'+sq; let g=GEO.get(k); if(g) return g; g=[];
  for(let yy=-r-2;yy<=r+2;yy+=2) for(let xx=-r-2;xx<=r+2;xx+=2){
    const nx=(xx+1)/r, ny=(yy+1)/(r*sq), rr=nx*nx+ny*ny;
    if(rr>1.06) continue;
    if(rr>1){ g.push({x:xx,y:yy,edge:true}); continue; }
    const nz=Math.sqrt(1-rr);
    g.push({x:xx,y:yy,rr:rr,lon:Math.atan2(nx,nz),lat:Math.asin(Math.max(-1,Math.min(1,ny))),light:-nx*.5-ny*.45+nz*.74});
  }
  GEO.set(k,g); return g;
}
const PHASES=96;
function themePal(k){ const T=THEMES[k]||THEMES.binary; return {a:T.base,b:mixPixel(T.land,'#05030f',.25),c:T.hi,glow:T.glow}; }
// A turning sphere. `ph` is the animation frame (0..PHASES-1): the surface moves under a fixed light. Each cell's colour is
// surfaceAt (ui/kinds.js), the one surface function the purpose bodies and the 3D bake call too.
function bodyArt(r,style,theme,seed,ph,sq,fade){
  r=Math.max(2,Math.ceil(r/2)*2); sq=sq||1; fade=fade|0;
  const s=seed%997, grey=SHADE.grey;
  return artCache(['b',r,style,theme,s,ph,sq,fade,GB?1:0,THEME_NAME].join('|'),()=>{
    const art=document.createElement('canvas'); art.width=art.height=r*2+8;
    const g=art.getContext('2d'),mid=r+4,P=STYLE[style]||themePal(theme),TL=tileOf(style,theme);
    const phase=ph*TAU/PHASES, glow=P.glow;
    for(const e of sphereGeo(r,sq)){
      const X=mid+e.x,Y=mid+e.y;
      if(e.edge){ g.fillStyle=GB?'#0f380f':SHADE.edge; g.fillRect(X,Y,2,2); continue; }
      const cell=surfaceAt(style,P,e.lon+phase,e.lat,s,{phase:phase,lon0:e.lon,light:e.light,rr:e.rr});
      let lvl=cell.lvl, base=cell.c, emit=cell.e;
      if(TL){ base=tileTex(TL,e,phase,r,s); if(style!=='star'){ emit=null; lvl=Math.max(0,Math.min(4,Math.floor(e.light*4+.6))); } }
      if(fade&&!GB){ base=mixPixel(base,grey,fade*.19); if(emit) emit=mixPixel(emit,grey,fade*.19); if(fade>=3&&lvl>2) lvl=2; }
      const cs=shades(base); g.fillStyle=(emit&&!GB)?emit:cs[lvl]; g.fillRect(X,Y,2,2);
      if(!emit&&style!=='star'&&e.rr>.84){
        if(e.light>.25){ g.fillStyle=cs[4]; g.fillRect(X,Y,2,2); }
        else if(!GB&&e.light<0){ g.fillStyle=mixPixel(glow,SHADE.rim,.45); g.fillRect(X,Y,2,2); }
      }
    }
    return art; });
}
function bodyPhase(seed,period){
  if(REDUCED) return 0;
  return Math.floor(((FRAME_T/1000)/period+(seed%101)/101)*PHASES)%PHASES;
}
function drawBody(x,y,r,style,theme,seed,period,sq,target,fade){
  if(VEC) VEC_PIX++;
  const R=Math.max(2,Math.ceil(r/2)*2), art=bodyArt(r,style,theme,seed,bodyPhase(seed,period||90),sq,fade);
  kindsPaint(target||cx,'drawImage',art,Math.round(x/2)*2-R-4,Math.round(y/2)*2-R-4);   // counted (the sun's photosphere)
}
// cracks across a quiet planet: a few dark fissures from the rim inward, fixed per body, more of
// them the longer it has been silent. Haze is a grey glow over the atmosphere.
function drawCracks(x,y,r,seed,k){
  if(GB||k<=0) return; const n=Math.round(2+k*5), snap=v=>Math.round(v/2)*2;
  cx.fillStyle=SHADE.edge;
  for(let i=0;i<n;i++){ let a=hash3(i,21,1,seed)*TAU, cxp=x+Math.cos(a)*r*.96, cyp=y+Math.sin(a)*r*.96, b=a+Math.PI;
    const len=Math.round(r*(.35+hash3(i,22,2,seed)*.5*k)/3);
    for(let j=0;j<len;j++){ b+=(hash3(i,j,3,seed)-.5)*1.2; cxp+=Math.cos(b)*3; cyp+=Math.sin(b)*3;
      if(Math.hypot(cxp-x,cyp-y)>r-1) break; cx.fillRect(snap(cxp),snap(cyp),2,2); if(j%3===0) cx.fillRect(snap(cxp)+2,snap(cyp),2,2); } }
}
function drawWell(m,t){
  const n=Math.min(6,Math.round(m.mass)), heavy=m.bloat>=30, col=GB?'#306230':(heavy?'#8f84ff':'#8a94a6');
  cx.save();
  for(let i=1;i<=n;i++){ const rr=m.r+14+i*((m.wellR-m.r-14)/n), k=i/n, a0=(REDUCED?0:(t/(9000+i*1700))%TAU)*(i%2?1:-1);
    cx.globalAlpha=(GB?.5:(.06+.05*m.mass))*(1-k*.6);
    pixelRing(m.x,m.y,rr,rr*.86,col,a0,a0+TAU,0,true); }
  // grains falling in: what a heavy folder does to things nearby
  if(!GB&&!REDUCED){ cx.globalAlpha=.9; cx.fillStyle=heavy?'#dcd8ff':'#c9d1e0';
    for(let i=0;i<6+n*2;i++){ const cyc=t/(2600+(i%5)*400)+i*.29, ph=cyc%1, a=hash3(i,Math.floor(cyc),4,seedOf(m.uid))*TAU, d=m.wellR*(1-ph)+m.r*ph;
      if(d<=m.r+2) continue; cx.fillRect(Math.round((m.x+Math.cos(a)*d)/2)*2,Math.round((m.y+Math.sin(a)*d*.86)/2)*2,2,2); } }
  cx.restore();
}
function massWord(m){ if(!m||!m.wellR) return ''; return 'heavy · '+fmtBytes(m.bytes)+(m.bloat>=30?' · '+m.bloat+'% rebuildable bloat':m.bloat>=10?' · '+m.bloat+'% caches':''); }
function decayWord(age,kind){ const d=ageDecay(age); if(d<=0) return ''; if(sitsStill(kind)) return 'quiet · '+age+'d'; if(age<14) return 'going quiet · '+age+'d'; if(age<30) return 'fading · '+age+'d untouched'; if(age<60) return 'cold · '+age+'d untouched'; return 'derelict · '+age+'d untouched'; }
// a soft atmosphere in the body's own colour (off in the mono palette)
function glowAt(x,y,r,color,a){
  if(GB) return;
  a*=GLOWK; if(a<=.004) return;
  const g=cx.createRadialGradient(x,y,r*.55,x,y,r*1.9);
  g.addColorStop(0,color+hex2(a)); g.addColorStop(1,color+'00');
  cx.fillStyle=g; cx.beginPath(); cx.arc(x,y,r*1.9,0,TAU); kindsPaint(cx,'fill');   // counted: the sun's corona is a layer only when it painted
}
// An irregular rock (asteroid, small moon): lumpy outline, craters, tumbling in 24 steps.
function rockArt(r,style,seed,spin){
  r=Math.max(2,Math.ceil(r/2)*2);
  const s=seed%499;
  return artCache(['r',r,style,s,spin,GB?1:0,THEME_NAME].join('|'),()=>{
    const art=document.createElement('canvas'); art.width=art.height=r*2+12;
    const g=art.getContext('2d'),mid=r+6,P=STYLE[style]||themePal(style),TL=tileOf(style,style),a0=spin*TAU/24,ca=Math.cos(a0),sa=Math.sin(a0);
    for(let yy=-r-4;yy<=r+4;yy+=2)for(let xx=-r-4;xx<=r+4;xx+=2){
      const nx=(xx+1)/r,ny=(yy+1)/r,d=Math.hypot(nx,ny); if(d>1.25) continue;
      const th=Math.atan2(ny,nx)-a0, edge=.72+.44*vnoise(Math.cos(th)*1.7+3,Math.sin(th)*1.7+3,s*.01,s);
      if(d>edge+.12) continue;
      const X=mid+xx,Y=mid+yy;
      if(d>edge){ g.fillStyle=GB?'#0f380f':SHADE.edge; g.fillRect(X,Y,2,2); continue; }
      const q=d/edge, nz=Math.sqrt(Math.max(0,1-q*q)), light=(-nx*.5-ny*.45)/edge+nz*.74;
      let lvl=Math.max(0,Math.min(4,Math.floor(light*4+.6)));
      const ux=ca*nx+sa*ny, uy=-sa*nx+ca*ny, n=vnoise(ux*3.2+7,uy*3.2+7,1.5,s);
      let base=n>.68?P.c:P.a; if(n<.3){ base=P.b; lvl=Math.max(0,lvl-1); }
      if(TL) base=TL[(Math.floor((-sa*xx+ca*yy)/TILE_TEXEL)+(s>>5))&7][(Math.floor((ca*xx+sa*yy)/TILE_TEXEL)+(s>>2))&7];
      const cs=shades(base); g.fillStyle=cs[lvl]; g.fillRect(X,Y,2,2);
      if(q>.8&&light>.3){ g.fillStyle=cs[4]; g.fillRect(X,Y,2,2); }
    }
    return art; });
}
function drawRock(x,y,r,style,seed,period){
  if(VEC) VEC_PIX++;
  let spin=REDUCED?0:Math.floor(((FRAME_T/1000)/(period||30)+(seed%53)/53)*24)%24;
  if(seed%2) spin=(24-spin)%24;
  const R=Math.max(2,Math.ceil(r/2)*2);
  cx.drawImage(rockArt(r,style,seed,spin),Math.round(x/2)*2-R-6,Math.round(y/2)*2-R-6);
}
// ---- CRAFT: row art, nose up, rasterised once per heading (16) and palette. ----
// o outline · h m d hull light/mid/dark · a status colour · c canopy · e engine (drawn only while burning)
// b B blue / light blue (a crew ship's status colour) · R red · g white-hot flare (boost)
// C canopy glass · T orange · k K dark / crimson · x X y Y the two cargo containers · r rotor · p P solar panels
// w hull in shadow · v accent in shadow · i canopy mid · l a lamp (lit from inside, like e and g)
// A pack's sprite_palette recolours any letter but a and e; a pack without a craft keeps these. The characters' craft
// (pilot, pilot_boost, carrier, raider, and a skin's problem drone, drawn as foe_drone) come from the skin
// (skins/<name>.json), merged over these in applyTheme. A skin's craft baked from its 3D hulls (tools/bake_sprites.py)
// sit on a finer grid: craftCell draws them on smaller cells, so each keeps the size it had on the map.
const SPRITES={
  fighter:['....a....','...hch...','...hcm...','..hhmmd..','a.hhmmd.a','ahhhmmmda','ahhdmmdda','a..dmd..a','...e.e...'],
  cargo:['...ooo...','..oohoo..','.oohchoo.','oommdmmoo','oxxXdXxxo','oxxXdXxxo','oxxXdXxxo','ommmdmmmo','oyyYdYyyo','oyyYdYyyo','oyyYdYyyo','oohmdmhoo','.oomdmoo.','..oeoeo..','..ooooo..'],
  drone:['r.ooo.r','.ohhho.','ohmmmdo','ohmamdo','ohmmddo','.odddo.','r.ooo.r'],
  probe:['ooo.o.ooo','opPohoPpo','opPmamPpo','opPodoPpo','ooo.o.ooo'],
  beacon:['..a..','.hah.','hmamd','.hmd.','..d..','..d..'],
  gate:['....ooooo....','..ooaaaaaoo..','.ooahhhhhaoo.','.oahhooohhao.','oahho...ohhao','oaho.....ohao','oamo.....ohao','oamo.....omao','oammo...ommao','.oammooommao.','.ooammmmmaoo.','..ooaaaaaoo..','....ooooo....']
};
const SPRITE_CELL=3;   // craft on the map are drawn with 3 px cells: readable at the default zoom
// the grid width each skin craft was drawn for (the hand-made grids): a wider, baked grid gets cells cell*REF/width px
const CRAFT_REF={pilot:31,pilot_boost:31,carrier:27,raider:27,foe_drone:15};
function craftCell(name,cell){
  const rows=(GB?BASE_SPRITES:SPRITES)[name], ref=CRAFT_REF[name], w=rows&&rows[0]?rows[0].length:0;
  return !ref||w<=ref?cell:Math.max(1,Math.round(cell*ref/w));
}
function spriteArt(name,pal,dir,n,cell){
  cell=craftCell(name,cell||SPRITE_CELL);
  return artCache(['s',name,dir,n,cell,GB?1:0].concat(Object.keys(pal).map(k=>k+pal[k])).join('|'),()=>{
    const rows=(GB?BASE_SPRITES:SPRITES)[name]||SPRITES.fighter,w=Math.max(...rows.map(r=>r.length)),h=rows.length,c0=(w-1)/2,r0=(h-1)/2;
    const R=Math.ceil(Math.hypot(w,h)/2)+1,S=R*2+1,art=document.createElement('canvas'); art.width=art.height=S*cell+4;
    const g=art.getContext('2d'),a=dir*TAU/n,ca=Math.cos(a),sa=Math.sin(a),cells=[];
    for(let oy=0;oy<S;oy++)for(let ox=0;ox<S;ox++){
      const vx=ox-R,vy=oy-R,sx=Math.round(ca*vx+sa*vy+c0),sy=Math.round(-sa*vx+ca*vy+r0);
      const ch=(rows[sy]||'')[sx]; if(!ch||ch==='.'||!pal[ch]) continue;
      cells.push([ox*cell+2,oy*cell+2,pal[ch]]);
    }
    g.fillStyle=GB?'#0f380f':SHADE.out; cells.forEach(c=>g.fillRect(c[0]-1,c[1]-1,cell+2,cell+2));
    cells.forEach(c=>{ g.fillStyle=c[2]; g.fillRect(c[0],c[1],cell,cell); });
    return art; });
}
// A craft's hull for the plate layout: the circle (any heading) that holds every pixel spriteArt paints of its grid, from the
// grid itself, the cell size it is drawn with and the scale it is drawn at. A turned craft is sampled cell by cell, so a painted
// cell lies up to 1.42 cells (half a source cell and half a diagonal of the painted one) beyond the centre of a grid cell; the
// 1 px halo is on top. A radius typed beside the drawing was 46 for a carrier whose nose reaches 60: plates painted across it.
const HULL_CELLS=new WeakMap();
function craftHull(name,cell,k){
  const rows=(GB?BASE_SPRITES:SPRITES)[name]||SPRITES.fighter; let far=HULL_CELLS.get(rows);
  if(far===undefined){ const w=Math.max(...rows.map(r=>r.length)), c0=(w-1)/2, r0=(rows.length-1)/2; far=0;
    const edge=CRAFT_REF[name]&&w>CRAFT_REF[name]?1.5:1.42;   // a baked grid (even, no centre cell) turns about a half cell
    rows.forEach((row,y)=>{ for(let x=0;x<row.length;x++){ if(row[x]!=='.') far=Math.max(far,Math.hypot(x-c0,y-r0)+edge); } });
    HULL_CELLS.set(rows,far); }
  const c=craftCell(name,cell||SPRITE_CELL), w=rows[0]?rows[0].length:0;
  return (far*c+(CRAFT_REF[name]&&w>CRAFT_REF[name]?2:1))*(k||1);   // + the halo (a baked grid's small cells: 2 px)
}
// where a craft's engines are, in px from its centre with the nose up (x right, y toward the tail): the middle of each
// run of engine cells (e) in the lowest row that has any, drawn at `cell`
function craftNozzles(name,cell){
  const rows=(GB?BASE_SPRITES:SPRITES)[name]||[], c=craftCell(name,cell||SPRITE_CELL), w=rows[0]?rows[0].length:0, out=[];
  for(let y=rows.length-1;y>=0&&!out.length;y--){ const re=/e+/g; let m;
    while((m=re.exec(rows[y]))) out.push([((m.index+m.index+m[0].length-1)/2-(w-1)/2)*c,(y-(rows.length-1)/2)*c]); }
  return out;
}
// angle 0 = nose up; a screen heading h = atan2(dy,dx) is angle h + PI/2
function drawSprite(name,x,y,angle,pal,cell){
  if(VEC){ VEC_AUD.reroute++; vecCraft(name,x,y,angle,vecTone(pal&&pal.a)||VC.muted,null,cell); return; }   // never pixel art in the vector look
  const n=16, dir=((Math.round(angle/(TAU/n))%n)+n)%n, art=spriteArt(name,pal,dir,n,cell);
  cx.drawImage(art,Math.round(x/2)*2-art.width/2,Math.round(y/2)*2-art.height/2);
}
// the letters the big craft and the gate add: outline, blues, red, white-hot flare, crimsons, canopy glass, orange
const CRAFT_INK={o:'#0a1020',b:'#2f7bff',B:'#8fd4ff',R:'#ff4d5e',g:'#e6fbff',k:'#3a1624',K:'#b3263f',C:'#1f6f8f',T:'#ffa23a'};
// the shade letters the baked craft add (a pack that recolours the hull carries them along, hullPal)
const SHADE_INK={w:'#6f7fa8',v:'#1d4fb8',i:'#3fb6d4',l:'#7ff6ff'};
function hullPal(accent,engine){
  if(GB) return {h:'#9bbc0f',m:'#8bac0f',d:'#306230',a:'#0f380f',c:'#0f380f',e:engine?'#306230':'',x:'#306230',X:'#0f380f',y:'#8bac0f',Y:'#306230',r:'#0f380f',p:'#306230',P:'#0f380f',
    o:'#0f380f',b:'#306230',B:'#8bac0f',R:'#0f380f',g:'#9bbc0f',k:'#0f380f',K:'#306230',C:'#0f380f',T:'#9bbc0f',w:'#306230',v:'#0f380f',i:'#8bac0f',l:'#9bbc0f'};
  const pal={h:'#eef4ff',m:'#9fb2e0',d:'#44558a',a:accent,c:'#5ff2ff',e:engine||'',r:engine||'#9fb2e0',p:'#2f7bff',P:'#1a3f9e'};
  Object.assign(pal,CRAFT_INK,SHADE_INK,SKIN_PAL);
  // a pack's palette recolours the hull; the status colour (a) and the engine (e) stay the page's
  Object.keys(SPRITE_PAL).forEach(k=>{ if(k!=='a'&&k!=='e') pal[k]=k==='r'?(engine||SPRITE_PAL[k]):SPRITE_PAL[k]; });
  // the shade letters follow a pack that recolours the hull without naming them
  if(!SPRITE_PAL.w&&(SPRITE_PAL.m||SPRITE_PAL.d)) pal.w=mixPixel(pal.m,pal.d,.5);
  if(!SPRITE_PAL.v&&SPRITE_PAL.b) pal.v=mixPixel(pal.b,'#000000',.4);
  if(!SPRITE_PAL.i&&(SPRITE_PAL.c||SPRITE_PAL.C)) pal.i=mixPixel(pal.c,pal.C,.5);
  return pal;
}
const CARGO_COLS=['#ff4f9a','#ffd23f','#22e07a','#2a95ff','#ff8a3d','#b07dff','#3cf2e2'];
function cargoPal(name,engine){
  const s=seedOf(name),a=CARGO_COLS[s%CARGO_COLS.length],b=CARGO_COLS[(s>>>3)%CARGO_COLS.length];
  const own=SPRITE_PAL.x?{x:SPRITE_PAL.x,X:SPRITE_PAL.X||SPRITE_PAL.x,y:SPRITE_PAL.y||SPRITE_PAL.x,Y:SPRITE_PAL.Y||SPRITE_PAL.y||SPRITE_PAL.x}:null;
  return Object.assign(hullPal('#ffd23f',engine),GB?{}:(own||{x:a,X:mixPixel(a,SHADE.edge,.35),y:b,Y:mixPixel(b,SHADE.edge,.35)}));
}
// the pilot craft's palette: its accents (b B) wear the pilot's status colour when one is given (a crew member's)
function pilotPal(tint,engine){
  const p=hullPal('',engine||'');
  if(tint&&!GB){ p.b=tint; p.B=mixPixel(tint,'#ffffff',.55); p.v=mixPixel(tint,'#000000',.4); }
  return p;
}
// ---- EFFECTS: the page's look as cheap canvas effects. Each is a function of the clock (plus the
// short boost-ring list and the lock-on state) and never touches the world data. Under
// prefers-reduced-motion (REDUCED) each draws one static frame or nothing; in the mono palette (GB)
// they are flat, one dark colour, no additive glow. All of them together cost well under a millisecond
// a frame, and they draw inside the loop's own frames: the 30 fps idle cap still holds. ----
const FX={rings:[],ringAt:0,boostK:0,ux:0,uy:-1,sk:[[],[],[]],lock:null,toast:null};
const FX_DIR4=[[1,0],[0,1],[-1,0],[0,-1]], FX_DIR8=[[1,1],[-1,1],[-1,-1],[1,-1],[1,0],[0,1],[-1,0],[0,-1]];
const FX_HEAT=['#fff4d0','#ffc24a','#ff7a2e','#8a2b3a'];
const BOOST_RING=[[10,1],[15,.8],[20,.55],[25,.3]];   // the four boost-ring frames, 70 ms each: half-width px, alpha
function fxSnap(v){ return Math.round(v/2)*2; }
function fxSq(x,y,w,col){ cx.fillStyle=col; cx.fillRect(x-w/2,y-w/2,w,w); }
// engine trail: a few embers streaming back from each nozzle, white-hot to dull red; nothing is stored,
// the particle positions are a function of the clock. ang is the sprite angle (0 = nose up). heat: four colours, hot to cold.
function fxTrail(x,y,ang,t,back,count,offs,heat){
  if(REDUCED) return;
  const H=heat||FX_HEAT;
  const sa=Math.sin(ang), ca=Math.cos(ang);
  for(let o=0;o<offs.length;o++) for(let i=0;i<count;i++){
    const u=((t/420)+i/count+o*.31)%1, d=back+u*20, w=u<.4?4:2;
    cx.fillStyle=GB?'#306230':H[Math.min(3,Math.floor(u*4))];
    cx.fillRect(fxSnap(x-sa*d+ca*offs[o]-w/2),fxSnap(y+ca*d+sa*offs[o]-w/2),w,w);
  }
}
// boost ring: an ellipse across the heading, white rim with a cyan additive glow
function fxRing(x,y,h,f){
  const r=BOOST_RING[f][0], a=BOOST_RING[f][1], tilt=h+Math.PI/2;
  cx.save(); cx.beginPath(); cx.ellipse(x,y,r,r*.42,tilt,0,TAU);
  if(VEC){ cx.globalAlpha=a; cx.strokeStyle=VC.mint; cx.lineWidth=1/ZOOM; cx.stroke(); cx.restore(); return; }
  if(GB){ cx.globalAlpha=a; cx.strokeStyle='#0f380f'; cx.lineWidth=3; cx.stroke(); cx.restore(); return; }
  cx.globalCompositeOperation='lighter'; cx.strokeStyle='#38d8e6'; cx.lineWidth=8; cx.globalAlpha=a*.5; cx.stroke();   // the glow
  cx.globalCompositeOperation='source-over'; cx.strokeStyle='#e6fbff'; cx.lineWidth=3; cx.globalAlpha=a; cx.stroke();   // the rim
  cx.restore();
}
// Shift boost: one ring every 80 ms is left where the ship was; each plays its four frames and goes.
// Reduced motion: no ring is emitted, one still frame sits behind the ship while it boosts.
function fxBoostRings(x,y,heading,power,t){
  const boosting=boostOn&&power>.02, bx=Math.cos(heading), by=Math.sin(heading);
  if(REDUCED){ if(boosting) fxRing(x-bx*44,y-by*44,heading,1); return; }
  if(boosting&&t-FX.ringAt>=80){ FX.ringAt=t; FX.rings.push({x:x-bx*44,y:y-by*44,h:heading,t0:t}); if(FX.rings.length>8) FX.rings.shift(); }
  for(let i=FX.rings.length-1;i>=0;i--){ const r=FX.rings[i], f=Math.floor((t-r.t0)/70);
    if(f>3||f<0){ FX.rings.splice(i,1); continue; } fxRing(r.x,r.y,r.h,f); }
}
// twin bolts from a ship to a target: a 18 px bolt each side of the nose, white core, green body, additive glow
function fxBolts(sx,sy,tx,ty,k){
  const dx=tx-sx, dy=ty-sy, d=Math.hypot(dx,dy)||1, ux=dx/d, uy=dy/d, start=14, travel=Math.max(0,d-start-8),
    head=start+travel*k, tail=Math.max(start,head-18);
  cx.save(); cx.lineCap='butt';
  for(let s=-1;s<=1;s+=2){
    const ox=-uy*6*s, oy=ux*6*s;
    cx.beginPath(); cx.moveTo(sx+ux*tail+ox,sy+uy*tail+oy); cx.lineTo(sx+ux*head+ox,sy+uy*head+oy);
    if(GB){ cx.strokeStyle='#0f380f'; cx.lineWidth=3; cx.stroke(); continue; }
    cx.strokeStyle='#04180c'; cx.globalAlpha=.55; cx.lineWidth=7; cx.stroke();   // backing, so a bolt reads over a lit planet
    cx.globalCompositeOperation='lighter'; cx.strokeStyle='#22d46e'; cx.globalAlpha=.5; cx.lineWidth=8; cx.stroke();
    cx.globalAlpha=1; cx.strokeStyle='#3dff9e'; cx.lineWidth=3; cx.stroke();
    cx.strokeStyle='#eafff2'; cx.lineWidth=1; cx.stroke();
  }
  cx.restore();
}
// the six hit-spark frames (about 40 ms each): flash, yellow cross, orange star, then red fragments flying out and fading
function fxSpark(x,y,f){
  x=fxSnap(x); y=fxSnap(y);
  if(GB){ const c='#0f380f'; if(f<3){ fxSq(x,y,4+f*2,c); for(let i=0;i<4;i++) fxSq(x+FX_DIR4[i][0]*(4+f*3),y+FX_DIR4[i][1]*(4+f*3),2,c); }
    else for(let i=0;i<4;i++) fxSq(x+FX_DIR8[i][0]*(5+f*2),y+FX_DIR8[i][1]*(5+f*2),2,c); return; }
  if(f===0){ glowAt(x,y,7,'#ffe27a',.5); fxSq(x,y,6,'#ffffff'); }
  else if(f===1){ for(let i=0;i<4;i++) fxSq(x+FX_DIR4[i][0]*6,y+FX_DIR4[i][1]*6,4,'#ffe27a'); fxSq(x,y,4,'#ffffff'); }
  else if(f===2){ for(let i=0;i<4;i++){ fxSq(x+FX_DIR4[i][0]*10,y+FX_DIR4[i][1]*10,4,'#ff9a3c'); fxSq(x+FX_DIR8[i][0]*7,y+FX_DIR8[i][1]*7,2,'#ffe27a'); } fxSq(x,y,4,'#ffe27a'); }
  else{ const r=f===3?9:f===4?12:14; cx.save(); cx.globalAlpha=f===3?1:f===4?.65:.35;
    for(let i=0;i<4;i++){ fxSq(x+FX_DIR8[i][0]*r*.75,y+FX_DIR8[i][1]*r*.75,f===3?4:2,'#ff4d5e'); if(f===3) fxSq(x+FX_DIR4[i][0]*5,y+FX_DIR4[i][1]*5,2,'#ff9a3c'); }
    cx.restore(); }
}
// one ship's fire: every 650 ms a volley of twin bolts flies for 60% of the cycle, then the spark plays on the foe it
// was aimed at. foes are [x,y] of the battlefield's problems; live indexes the ones that are not wrecks.
function fxVolley(sx,sy,foes,live,k,t){
  const pool=live.length?live:foes.map((f,i)=>i), cyc=t/650+k*.37, n=Math.floor(cyc), ph=cyc-n,
    tgt=foes[pool[((n+k)%pool.length+pool.length)%pool.length]];
  if(REDUCED){ fxBolts(sx,sy,tgt[0],tgt[1],.55); fxSpark(tgt[0],tgt[1],2); return; }
  if(ph<.6) fxBolts(sx,sy,tgt[0],tgt[1],ph/.6);
  else fxSpark(tgt[0],tgt[1],Math.min(5,Math.floor((ph-.6)/.4*6)));
}
// the shield ring of a thread that is being checked: segmented cyan rim around the ship, its alpha pulsing 0.3 to 0.6 every 800 ms
function fxShield(x,y,r,t){
  const k=REDUCED?.45:.45+.15*Math.sin(t*TAU/800), a0=REDUCED?0:t/2400;
  cx.save();
  if(GB){ cx.globalAlpha=.8; pixelRing(x,y,r,r,'#306230',a0,a0+TAU,0,true); cx.restore(); return; }
  cx.globalCompositeOperation='lighter'; cx.globalAlpha=k*.7; pixelRing(x,y,r+3,r+3,'#287ded',0,TAU);
  cx.globalAlpha=Math.min(1,k+.3); pixelRing(x,y,r,r,'#5ff2ff',a0,a0+TAU,0,true);
  cx.restore();
}
// red lock-on reticle: four L-brackets with 6 px arms close from 12 px to 6 px outside the target in 200 ms,
// then a centre cross blinks every 150 ms. t0 is when the lock was taken.
function fxLockOn(x,y,r,t,t0){
  const k=REDUCED?1:Math.min(1,(t-t0)/200), d=Math.round(r+12-6*k);
  x=fxSnap(x); y=fxSnap(y); cx.fillStyle=GB?'#0f380f':'#ff4d5e';
  for(let sx=-1;sx<=1;sx+=2) for(let sy=-1;sy<=1;sy+=2){
    const bx=x+sx*d, by=y+sy*d;
    cx.fillRect(sx>0?bx-6:bx,sy>0?by-3:by,6,3); cx.fillRect(sx>0?bx-3:bx,sy>0?by-6:by,3,6);
  }
  if(k>=1&&(REDUCED||Math.floor(t/150)%2===0)){ cx.fillRect(x-4,y-1,9,3); cx.fillRect(x-1,y-4,3,9); }
}
// the foe under the pointer takes the lock (and a name plate); a lock lost for 120 ms is dropped and starts over next time
function fxLockFoe(f,x,y,t){
  const L=FX.lock;
  if(!L||L.uid!==f.uid||t-L.seen>120) FX.lock={uid:f.uid,t0:t,seen:t}; else L.seen=t;
  fxLockOn(x,y,16,t,FX.lock.t0);
  queuePlate({key:'foe:'+f.uid,kind:'seat',pri:PLATE_PRI.seat,x:x,y:y,r:28,face:'s',hot:true,
    segs:[{t:fitLabel(String(f.name||'problem'),260),col:GB?T.paper:'#ff8a8a'}]});
}
// Machine gate: a still event horizon, without implied traffic.
function fxGateShimmer(x,y,col,t){
  x=fxSnap(x); y=fxSnap(y);
  const a=0, k=.5;
  if(GB){ fxSq(x,y,4,'#306230'); fxSq(x+Math.round(Math.cos(a)*6/2)*2,y+Math.round(Math.sin(a)*6/2)*2,2,'#0f380f'); return; }
  cx.save(); cx.globalCompositeOperation='lighter';
  cx.globalAlpha=.1+.14*k; cx.fillStyle=col; cx.beginPath(); cx.arc(x,y,8,0,TAU); cx.fill();
  cx.globalAlpha=.9; cx.fillStyle='#e9fbff';
  for(let i=0;i<5;i++){ const aa=a+i*TAU/5, rr=(i%2)?6:3.5; cx.fillRect(fxSnap(x+Math.cos(aa)*rr),fxSnap(y+Math.sin(aa)*rr),2,2); }
  const b=-.8; cx.fillStyle='#ffffff'; cx.fillRect(fxSnap(x+Math.cos(b)*15)-1,fxSnap(y+Math.sin(b)*15)-1,3,3);
  cx.restore();
  glowAt(x,y,14,col,.16+.06*k);
}
// The carrier's navigation lamps stay lit; they do not imply new activity.
function fxRunningLights(x,y,t,bright){
  if(GB) return;
  const s=bright?4:3; cx.save(); cx.globalCompositeOperation='lighter';
  if(bright){ fxHalo(x,y-43,10,'#5ff2ff',.7); fxHalo(x,y+43,10,'#ff4d5e',.7); }
  fxSq(x,y-43,s,'#5ff2ff'); fxSq(x,y+43,s,'#ff4d5e');
  cx.restore();
}
// MERGE BANNER: a banner that slides in under the top bar when a thread is merged, with the thread's title (its words are the skin's)
function fxMergeBanner(title){ FX.toast={t0:FRAME_T,title:String(title||'').slice(0,56)}; BANNERED.set(FX.toast.title,Date.now()); if(MOB&&mapCovered()) mToast(SKIN_NAMES.merged+': '+FX.toast.title,3800); }   // the banner is drawn on the canvas; a covered canvas says it in a toast
// the same banner says the sky's word (M.word) when a contact leaves it: CLEARED, or EMPTY SKY for the last one (missionComplete)
function drawMergeBanner(t){
  if(VEC){ vecIntertitle(t); return; }   // the vector look's intertitle
  const M=FX.toast; if(!M) return;
  const head=M.word||SKIN_NAMES.merged;
  const age=t-M.t0, DUR=3800; if(age>DUR){ FX.toast=null; return; }
  if(age<0) return;
  const slide=REDUCED?1:Math.min(1,age/260), a=REDUCED?1:Math.min(1,(DUR-age)/400), w=Math.min(VW-24,420), h=60,
    x=Math.round(VW/2-w/2), y=Math.round(76-(1-slide)*30);
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.globalAlpha=a;
  const gold=GB?'#0f380f':'#ffd23f';
  cx.fillStyle=GB?'#8bac0f':'rgba(6,12,26,.93)'; cx.fillRect(x,y,w,h);
  cx.fillStyle=gold; cx.fillRect(x,y,w,3); cx.fillRect(x,y+h-3,w,3);
  cx.textAlign='center';
  cx.font=cfont(24,{display:true}); if('letterSpacing' in cx) cx.letterSpacing='3px';   // 24 px: three of the face's pixels
  cx.fillStyle=GB?'#306230':'#3a2a00'; cx.fillText(head,x+w/2+2,y+32);
  cx.fillStyle=GB?'#0f380f':'#ffe27a'; cx.fillText(head,x+w/2,y+30);
  if('letterSpacing' in cx) cx.letterSpacing='0px';
  cx.font=cfont(13); cx.fillStyle=GB?'#0f380f':'#dcecff'; cx.fillText(M.title||(M.word?'':'changes accepted'),x+w/2,y+50);
  if(x>=30){ const fly=REDUCED?0:Math.round((1-slide)*40);   // two pilot craft fly in toward the banner from its sides
    drawSprite('pilot',x-20-fly,y+h/2,Math.PI/2,pilotPal('',''),3); drawSprite('pilot',x+w+20+fly,y+h/2,-Math.PI/2,pilotPal('',''),3); }
  if(!REDUCED&&age<240){ cx.fillStyle=GB?'#0f380f':'#ffffff'; cx.globalAlpha=.55*a;   // the comms open on static
    for(let i=0;i<5;i++) cx.fillRect(x,y+((Math.floor(age/40)*37+i*13)%h),w,2); }
  cx.restore();
}
// ---- THE VECTOR LOOK (VEC: the pack says "render": "vector", the tactical theme). The map becomes an operations console:
// hairline phosphor orbits on a tilted plane, smooth shaded spheres, schematic craft, hatched threat zones, edge chevrons
// for troubled planets out of view, a brass compass, a boot sequence and a merge intertitle. Nothing here runs unless VEC
// is on: every other pack draws exactly as before. Hairlines are 1 screen pixel (1/ZOOM world units); glyphs keep their
// screen size at every zoom; renders are cached (VEC_ART) so the frame holds. ----
const VC0={space:'#040808',edge:'#010303',haze:'#0a1816',line:'#4f886b',dim:'#264a38',faint:'#1a2e25',text:'#e8ece8',body:'#c9d2cc',
  muted:'#7f918a',mint:'#a3e8d0',teal:'#4fd1c5',yellow:'#e5b800',red:'#d62828',hatch:'#800f0f',amber:'#e59e2b',brass:'#c5a059',brass2:'#a89568',
  panel:'#0b0e0d',border:'#2a3b33'};
let VC=Object.assign({},VC0);
// the console's colours from the pack (its ui block and the line colour its kinds carry), the spec's tokens where it is silent
function vecPal(P){
  const u=(P&&P.ui)||{}, k=(P&&P.kinds&&P.kinds.code)||{}, sp=(P&&P.space)||{}, pick=(c,d)=>/^#[0-9a-f]{6}$/i.test(String(c||''))?String(c).toLowerCase():d;
  return Object.assign({},VC0,{space:pick(u.bg,VC0.space),haze:pick(sp.nebula&&sp.nebula[1],VC0.haze),line:pick(k.hi,VC0.line),faint:pick(k.land,VC0.faint),
    text:pick(u.text,VC0.text),muted:pick(u.muted,VC0.muted),mint:pick(u.focus,VC0.mint),teal:pick(u.ok,VC0.teal),yellow:pick(u.accent,VC0.yellow),
    red:pick(u.danger,VC0.red),panel:pick(u.panel,VC0.panel),border:pick(u.border,VC0.border)});
}
// THE TILTED PLANE. The system is a plane seen at a slant: squashed vertically by TILT_K about the star. Only POSITIONS are
// projected, once, where the layout places a body (layoutSystem: planets, the dock ring, the machines). Every reader (the
// drawing, pickAt, the plates, the camera and home-view fit, the phone's pinch, tap and long press) reads those same stored
// numbers, so a body is hit exactly where it is drawn. Bodies, labels and glyphs keep their true shape; a planet's own moons
// circle it face-on. unproject() takes a drawn point back to the plane (the star's reach in nearestBody, the audit).
// With any other pack LY.tilt is 1 and both are the identity.
const TILT_K=.62;
function project(x,y){ const s=LY.star, k=LY.tilt||1; return {x:x, y:s.y+(y-s.y)*k}; }
function unproject(x,y){ const s=LY.star, k=LY.tilt||1; return {x:x, y:s.y+(y-s.y)/k}; }
let VEC_PIX=0;   // pixel-cell draws (drawBody, drawRock, pixelRing, a pixel sprite) made while VEC is on: window.__vecAudit, must stay 0
const VEC_AUD={frames:0,orbits:[],drawn:[],threats:[],chevrons:[],labels:[],tether:null,compass:null,reroute:0};
const VEC_ART=new Map();   // cached vector renders (sphere surfaces, terminators, silhouettes, the hatch tile): LRU, 240 at most
function vecCache(key,make){ let a=VEC_ART.get(key); if(a){ VEC_ART.delete(key); VEC_ART.set(key,a); return a; }
  a=make(); VEC_ART.set(key,a); if(VEC_ART.size>240) VEC_ART.delete(VEC_ART.keys().next().value); return a; }
function vecCanvas(w,h){ const c=document.createElement('canvas'); c.width=Math.max(1,Math.ceil(w)); c.height=Math.max(1,Math.ceil(h)); return c; }
// device pixels a world length covers now, in quarter octaves (a wheel zoom reuses a render instead of making one per frame)
function vecPx(world){ return Math.max(2,Math.pow(2,Math.round(Math.log2(Math.max(1,world*ZOOM*(DPR||1)))*4)/4)); }
function track(px){ if('letterSpacing' in cx) cx.letterSpacing=(px||0)+'px'; }
const VEC_SERIF='Georgia,"Times New Roman",serif';
function vecFont(px,w,face){ return (w?w+' ':'')+px+'px '+(face==='data'?FONT_DATA:face==='serif'?VEC_SERIF:FONT_TAC); }
// text with a soft dark halo instead of a box behind it
function vecHalo(txt,x,y,col,lw){ cx.lineJoin='round'; cx.lineWidth=lw||3; cx.strokeStyle='rgba(1,4,3,.82)'; cx.strokeText(txt,x,y); cx.fillStyle=col; cx.fillText(txt,x,y); }
// the page's status colours (made for the pixel look) as the console's
const VEC_TONE={'#3dff9e':'teal','#22e07a':'teal','#5fb4ff':'teal','#ffbd2e':'amber','#ffbd55':'amber','#ffd34d':'amber','#ff8a4c':'amber','#ff9a2e':'amber',
  '#ff4d5e':'red','#ff8a8a':'red','#ff2b6a':'red','#f85149':'red','#ff7777':'red','#dcecff':'text','#e6edf3':'text','#b5cbe3':'body','#c9d1e0':'body',
  '#abc2df':'body','#a7b9d3':'muted','#9aa7bd':'muted','#b8b0ff':'mint','#c98bff':'mint','#ffd166':'yellow'};
function vecTone(c){ const k=VEC_TONE[String(c||'').toLowerCase()]; return k?VC[k]:(c||VC.body); }
function vecShipCol(u){ const st=String(u.status||'');
  return (isDead(st)||st==='COLD'||st==='STALE')?VC.red:(st==='WAITING'||st==='blocked'||st==='LIMITED')?VC.amber:st==='DRAFT'?VC.mint:VC.teal; }
function vecFrame(){ VEC_AUD.frames++; VEC_AUD.labels.length=0; VEC_AUD.orbits.length=0; VEC_AUD.drawn.length=0; VEC_AUD.threats.length=0; VEC_AUD.chevrons=[]; VEC_AUD.tether=null; VEC_AUD.compass=null; }
// a 1 px ring (an ellipse when ry is given), dashes in screen pixels, turning by rot screen pixels
function vecRing(x,y,r,col,alpha,dash,rot,ry){ cx.save(); cx.globalAlpha*=alpha; cx.strokeStyle=col; cx.lineWidth=1/ZOOM;
  if(dash){ cx.setLineDash(dash.map(d=>d/ZOOM)); if(rot) cx.lineDashOffset=-rot/ZOOM; }
  cx.beginPath(); cx.ellipse(x,y,r,ry||r,0,0,TAU); kindsPaint(cx,'stroke'); cx.restore(); }   // counted: the sun's rings are a layer only when stroked
// ---- backdrop: the radial space colour, registration crosses every 96 px (screen space), a sparse 1 px star field ----
let VEC_BG=null;
function vecBackdrop(){
  const key=[VW,VH,DPR,VC.space,VC.haze,VC.line].join('|'); if(VEC_BG&&VEC_BG.key===key) return VEC_BG.c;
  const c2=vecCanvas(VW*DPR,VH*DPR), g=c2.getContext('2d'); g.scale(DPR,DPR);
  const R=Math.hypot(VW,VH)/2, gr=g.createRadialGradient(VW*.55,VH*.45,0,VW*.55,VH*.45,R);
  gr.addColorStop(0,VC.space); gr.addColorStop(1,VC.edge); g.fillStyle=gr; g.fillRect(0,0,VW,VH);
  const hz=g.createRadialGradient(VW*.28,VH*.3,0,VW*.28,VH*.3,R*.8); hz.addColorStop(0,VC.haze+'66'); hz.addColorStop(1,VC.haze+'00'); g.fillStyle=hz; g.fillRect(0,0,VW,VH);
  g.strokeStyle=VC.line; g.globalAlpha=.12; g.lineWidth=1; g.beginPath();
  for(let y=48;y<VH;y+=96) for(let x=48;x<VW;x+=96){ g.moveTo(x-3,y+.5); g.lineTo(x+4,y+.5); g.moveTo(x+.5,y-3); g.lineTo(x+.5,y+4); }
  g.stroke(); VEC_BG={key:key,c:c2}; return c2;
}
function vecSpace(t){
  cx.drawImage(vecBackdrop(),0,0,VW,VH);
  const cols=SPACE.stars&&SPACE.stars.length?SPACE.stars:['#8fa59b'];
  for(let i=0;i<STARS.length;i+=2){ const s=STARS[i], ly=s.z<.34?0:s.z<.7?1:2, par=STAR_PAR[ly]*.5;
    const x=(((s.fx*VW-camx*par)%VW)+VW)%VW, y=(((s.fy*VH-camy*par)%VH)+VH)%VH, tw=1;
    cx.globalAlpha=(.08+.3*s.z)*tw; cx.fillStyle=cols[(i%11===0&&cols[1])?1:0]; cx.fillRect(Math.round(x),Math.round(y),1,1); }
  cx.globalAlpha=1;
}
// ---- orbits: phosphor ellipses on the plane. The dock ring (agents), the roots ring every folder sits on, the orbit of each
// planet that drifted off it (decay), the band's inner edge dashed, the outer ring with ticks every 15 degrees, dashed spokes
// where one sector ends and the next begins, the sector names tracked small on the ring. sc < 1: the boot's expanding rings.
function vecOrbits(t,moons,sc){
  sc=sc==null?1:sc; const s=LY.star, K=LY.tilt||1, R=LY.rootOrbit||760, z=1/ZOOM, Ro=R+380, Ri=Math.max(LY.dock+120,R-380), W2=LY.wedges||[];
  if(sc===1){ cx.save(); cx.translate(s.x,s.y); cx.scale(1,K);   // the plane itself: a faint phosphor wash inside the outer ring
    const pg=cx.createRadialGradient(0,0,Ri*.4,0,0,Ro); pg.addColorStop(0,VC.line+'00'); pg.addColorStop(.6,VC.line+'12'); pg.addColorStop(.85,VC.line+'0a'); pg.addColorStop(1,VC.line+'00');
    cx.fillStyle=pg; cx.beginPath(); cx.arc(0,0,Ro,0,TAU); cx.fill(); cx.restore(); }
  cx.save(); cx.strokeStyle=VC.line; cx.lineWidth=z;
  const ring=(r,a,dash)=>{ const rr=r*sc; cx.globalAlpha=a; cx.setLineDash(dash?dash.map(d=>d*z):[]); cx.beginPath(); cx.ellipse(s.x,s.y,rr,rr*K,0,0,TAU); cx.stroke();
    if(sc===1) VEC_AUD.orbits.push({r:r,rx:rr,ry:rr*K}); };
  ring(LY.dock,.4); ring(R,.55);
  const seen=new Set(); (moons||[]).forEach(m=>{ if(!(m.decay>.05)) return; const rr=R+m.decay*160, key=Math.round(rr/24); if(seen.has(key)||seen.size>=6) return; seen.add(key); ring(rr,.2); });
  ring(Ri,.2,[3,7]); ring(Ro,.34);
  cx.globalAlpha=.5; cx.setLineDash([]); cx.beginPath();
  for(let i=0;i<24;i++){ const a=i*TAU/24, L=(i%3?5:11)*z, c=Math.cos(a), sn=Math.sin(a), o=Ro*sc;
    cx.moveTo(s.x+c*o,s.y+sn*o*K); cx.lineTo(s.x+c*(o-L),s.y+sn*(o-L)*K); }
  cx.stroke();
  if(W2.length>1){ cx.globalAlpha=.24; cx.setLineDash([3*z,6*z]); cx.beginPath();
    W2.forEach(w=>{ const c=Math.cos(w.a0), sn=Math.sin(w.a0); cx.moveTo(s.x+c*Ri*sc,s.y+sn*Ri*sc*K); cx.lineTo(s.x+c*Ro*sc,s.y+sn*Ro*sc*K); }); cx.stroke(); }
  cx.restore();
  if(sc<1) return;
  W2.forEach(w=>{ const mid=(w.a0+w.a1)/2, p=project(s.x+Math.cos(mid)*(R+330),s.y+Math.sin(mid)*(R+330));
    vecWorldText(String(w.kind).toUpperCase()+' · '+w.n,p.x,p.y,vecFont(12,500),VC.line,.85,3); });
}
// world-anchored text drawn at its screen size, kept out of the rails
function vecWorldText(txt,wx,wy,font,col,alpha,trk){
  const sx=(wx-camx)*ZOOM, sy=(wy-camy)*ZOOM; if(sx<-200||sx>VW+200||!vecInView(sy)) return;
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.globalAlpha=alpha==null?1:alpha; cx.font=font; track(trk||0); cx.textAlign='center'; cx.textBaseline='alphabetic';
  const w=cx.measureText(txt).width; vecHalo(txt,Math.round(sx),Math.round(sy),col,3); track(0); cx.restore();
  VEC_AUD.labels.push({x:sx-w/2-4,y:sy-14,w:w+8,h:18});   // the plates keep off it (plateObstacles)
}
// the rails (T1's top and bottom bands, desktop): nothing of the map's own text is printed in them
function vecRails(){ return MOB?{top:0,bottom:0}:{top:44,bottom:48}; }
function vecInView(sy){ const R=vecRails(); return sy>R.top+8&&sy<VH-R.bottom-6; }
// ---- spheres: the surface (banded by kind, greyed by decay) under a terminator turned toward the star, a thin atmosphere ----
const VEC_KIND={gas:'gas',ocean:'ocean',ice:'ice'};
function vecSurface(rq,style,seed,fade){
  return vecCache(['vs',rq,style,seed%64,fade,THEME_NAME].join('|'),()=>{
    const P=STYLE[style]||STYLE.rock||{a:'#6c6a66',b:'#474542',c:'#8f8c86'}, k=fade*.18, cls=VEC_KIND[style]||'rock', grey=c=>k>0?mixPixel(c,'#6b7280',k):c;
    const a=grey(P.a), b=grey(P.b), c=grey(P.c), S=Math.ceil(rq*2+4), m=S/2, out=vecCanvas(S,S), g=out.getContext('2d');
    g.save(); g.beginPath(); g.arc(m,m,rq,0,TAU); g.clip(); g.fillStyle=a; g.fillRect(0,0,S,S);
    const band=(y0,h,col,al)=>{ g.globalAlpha=al; g.fillStyle=col; g.fillRect(m-rq,m+y0*rq,rq*2,h*rq); };
    if(cls==='gas'){ for(let i=0;i<9;i++) band(-1+i*.22+(hash3(i,1,2,seed)-.5)*.06,.08+hash3(i,3,1,seed)*.08,i%2?b:c,.5); }
    else if(cls==='ice'){ for(let i=0;i<4;i++) band(-.7+i*.38,.05,b,.22); g.globalAlpha=.6; g.fillStyle=c; g.fillRect(m-rq,m-rq,rq*2,rq*.3); g.fillRect(m-rq,m+rq*.74,rq*2,rq*.26); }
    else{ for(let i=0;i<7;i++){ const bx=m+(hash3(i,5,1,seed)-.5)*1.5*rq, by=m+(hash3(i,6,2,seed)-.5)*1.5*rq, br=rq*(.25+hash3(i,7,3,seed)*.35), col=i%2?b:c;
        const gr=g.createRadialGradient(bx,by,0,bx,by,br); gr.addColorStop(0,col); gr.addColorStop(1,col+'00'); g.globalAlpha=cls==='ocean'?.35:.42; g.fillStyle=gr; g.fillRect(bx-br,by-br,br*2,br*2); }
      for(let i=0;i<5;i++) band(-.8+i*.36+(hash3(i,9,4,seed)-.5)*.1,.06,cls==='ocean'?c:b,cls==='ocean'?.16:.12); }
    g.globalAlpha=1; const lim=g.createRadialGradient(m,m,rq*.55,m,m,rq); lim.addColorStop(0,'rgba(0,0,0,0)'); lim.addColorStop(1,'rgba(0,0,0,.35)'); g.fillStyle=lim; g.fillRect(0,0,S,S);
    g.restore(); return out; });
}
function vecShade(rq){
  return vecCache('vh|'+rq,()=>{ const S=Math.ceil(rq*2+4), m=S/2, out=vecCanvas(S,S), g=out.getContext('2d');
    g.beginPath(); g.arc(m,m,rq+.5,0,TAU); g.clip();
    const lg=g.createLinearGradient(m-rq,0,m+rq,0); lg.addColorStop(0,'rgba(0,0,0,0)'); lg.addColorStop(.42,'rgba(0,0,0,.04)'); lg.addColorStop(.64,'rgba(0,0,0,.62)'); lg.addColorStop(1,'rgba(0,0,0,.9)');
    g.fillStyle=lg; g.fillRect(0,0,S,S);
    const hi=g.createRadialGradient(m-rq*.5,m,0,m-rq*.5,m,rq*.75); hi.addColorStop(0,'rgba(255,255,255,.16)'); hi.addColorStop(1,'rgba(255,255,255,0)'); g.fillStyle=hi; g.fillRect(0,0,S,S);
    return out; });
}
function vecSphere(x,y,r,style,seed,o){ o=o||{};
  const rq=vecPx(r), S=Math.ceil(rq*2+4), w=S*r/rq, fade=Math.min(4,Math.round((o.decay||0)*4));
  cx.save(); cx.imageSmoothingEnabled=true; if(o.decay) cx.globalAlpha*=1-.45*o.decay;
  cx.drawImage(vecSurface(rq,style,seed,fade),x-w/2,y-w/2,w,w);
  cx.translate(x,y);
  if(!o.flat){ cx.save(); cx.rotate(Math.atan2(LY.star.y-y,LY.star.x-x)+Math.PI); cx.drawImage(vecShade(rq),-w/2,-w/2,w,w); cx.restore(); }
  if(o.glow){ cx.globalAlpha*=.42; cx.strokeStyle=o.glow; cx.lineWidth=1/ZOOM; cx.beginPath(); cx.arc(0,0,r+.8/ZOOM,0,TAU); cx.stroke(); }
  cx.restore();
}
function vecGas(x,y,r,seed,t,decay){
  const P=STYLE.gas||{c:'#b3a27f',glow:'#e5b800'}, tilt=-.32, rx=r*1.8, ry=r*.42;
  glowAt(x,y,r,P.glow||VC.amber,.16);
  const half=(a0,a1,al)=>{ cx.save(); cx.strokeStyle=P.c; cx.lineWidth=1/ZOOM;
    [[1,al],[.9,al*.6],[1.08,al*.45]].forEach(([k,a])=>{ cx.globalAlpha=a; cx.beginPath(); cx.ellipse(x,y,rx*k,ry*k,tilt,a0,a1); cx.stroke(); }); cx.restore(); };
  half(Math.PI,TAU,.45); vecSphere(x,y,r,'gas',seed,{decay:decay,glow:P.glow}); half(0,Math.PI,.85);
}
function vecStation(x,y,r,seed,t){
  const P=STYLE.station||{c:'#c3cdc9',glow:'#a3e8d0'}, rx=Math.max(22,r*1.35), ry=rx*.42, tilt=-.25, rot=REDUCED?0:t/40000*TAU;
  const half=(a0,a1)=>{ cx.save(); cx.strokeStyle=P.c; cx.lineWidth=1/ZOOM; cx.globalAlpha=.8; cx.beginPath(); cx.ellipse(x,y,rx,ry,tilt,a0,a1); cx.stroke();
    cx.globalAlpha=.4; cx.beginPath(); cx.ellipse(x,y,rx*.88,ry*.88,tilt,a0,a1); cx.stroke(); cx.restore(); };
  half(Math.PI,TAU);
  cx.save(); cx.strokeStyle=P.c; cx.globalAlpha=.3; cx.lineWidth=1/ZOOM; cx.beginPath();
  for(let i=0;i<4;i++){ const a=rot+i*Math.PI/2, ex=Math.cos(a)*rx*.88, ey=Math.sin(a)*ry*.88; cx.moveTo(x,y); cx.lineTo(x+ex*Math.cos(tilt)-ey*Math.sin(tilt),y+ex*Math.sin(tilt)+ey*Math.cos(tilt)); }
  cx.stroke(); cx.restore();
  vecSphere(x,y,Math.max(6,r*.42),'station',seed,{glow:P.glow}); half(0,Math.PI);
}
function vecBelt(m,t,seed){
  const P=STYLE.belt||STYLE.rock||{c:'#7d7a74',glow:'#7f918a'}, spin=0, z=1/ZOOM;
  glowAt(m.x,m.y,m.r,P.glow||VC.muted,.1);
  if(m.ringR){ const n=Math.min(260,90+Math.round(m.ringR*.6)); cx.save(); cx.fillStyle=P.c||VC.muted;
    for(let i=0;i<n;i++){ const a=hash3(i,1,2,seed)*TAU+spin, q=hash3(i,3,4,seed), rr=m.ringR*(.5+(q+hash3(i,6,2,seed))*.5), s=(i%9===0?1.6:1)*z;
      cx.globalAlpha=.25+.45*hash3(i,8,1,seed); cx.fillRect(m.x+Math.cos(a)*rr-s/2,m.y+Math.sin(a)*rr-s/2,s,s); }
    cx.restore(); }
  for(let i=0;i<7;i++){ const a=i*2.39996+(seed%11), d=i===0?0:(.45+hash3(i,5,6,seed)*.65)*m.r;
    const rr=i===0?Math.max(10,Math.round(m.r*.5)):4+Math.floor(hash3(i,8,9,seed)*5)*2;
    vecSphere(m.x+Math.cos(a)*d,m.y+Math.sin(a)*d,rr,'belt',i%4,{}); }
}
function vecWell(m,t){
  const n=Math.min(6,Math.round(m.mass)), col=m.bloat>=30?'#8f84ff':VC.muted;
  for(let i=1;i<=n;i++){ const rr=m.r+14+i*((m.wellR-m.r-14)/n), k=i/n;
    vecRing(m.x,m.y,rr,col,Math.min(.5,(.12+.08*m.mass)*(1-k*.6)),[2,7],REDUCED?0:(t/(60+i*12))*(i%2?1:-1)); }
}
// ---- threat zones: a planet with problems lies in a hatched red disc on the plane (45-degree stripes, 1 px red edge), stronger
// with more problems; its problems are hazard triangles riding the disc's edge ----
function vecPattern(){
  const tile=vecCache('hatch|'+VC.hatch,()=>{ const c2=vecCanvas(10,10), g=c2.getContext('2d'); g.strokeStyle=VC.hatch; g.lineWidth=3.2; g.beginPath();
    for(let i=-1;i<=1;i++){ g.moveTo(i*10,10); g.lineTo(i*10+10,0); } g.stroke(); return c2; });
  const p=cx.createPattern(tile,'repeat');
  if(p&&p.setTransform&&typeof DOMMatrix!=='undefined') p.setTransform(new DOMMatrix([1/ZOOM,0,0,1/ZOOM,0,0]));   // stripes in screen pixels
  return p;
}
// the band rides the ring the problems fly on (FOE_SLANT, the same orbit every look uses), from just off the surface outward
const FOE_SLANT=.78;
function vecThreatR(m){ return m.r+44+3*Math.min(8,m.enemies||0); }
function vecThreat(m,t){
  const R=vecThreatR(m), r0=m.r+8, k=Math.min(1,(m.enemies||0)/8), pat=vecPattern();
  cx.save(); cx.beginPath(); cx.ellipse(m.x,m.y,R,R*FOE_SLANT,0,0,TAU); cx.moveTo(m.x+r0,m.y); cx.ellipse(m.x,m.y,r0,r0*FOE_SLANT,0,TAU,0,true);
  cx.globalAlpha=.06+.08*k; cx.fillStyle=VC.red; cx.fill('evenodd');
  if(pat){ cx.globalAlpha=.3+.25*k; cx.fillStyle=pat; cx.fill('evenodd'); }
  cx.globalAlpha=.45+.45*k; cx.strokeStyle=VC.red; cx.lineWidth=1/ZOOM; cx.beginPath(); cx.ellipse(m.x,m.y,R,R*FOE_SLANT,0,0,TAU); cx.stroke();
  cx.globalAlpha*=.5; cx.beginPath(); cx.ellipse(m.x,m.y,r0,r0*FOE_SLANT,0,0,TAU); cx.stroke(); cx.restore();
  VEC_AUD.threats.push({uid:m.uid,label:m.label,enemies:m.enemies,x:(m.x-camx)*ZOOM,y:(m.y-camy)*ZOOM,rx:R*ZOOM,ry:R*FOE_SLANT*ZOOM,targets:Math.min(8,m.enemies||0)});
}
// a hazard triangle: outline, a dark fill so it reads over the stripes, an inner dot (a wreck: an X)
function vecTri(x,y,s,wreck,col){
  col=col||VC.red;
  cx.save(); cx.beginPath(); cx.moveTo(x,y-s); cx.lineTo(x+s*.92,y+s*.62); cx.lineTo(x-s*.92,y+s*.62); cx.closePath();
  cx.fillStyle='rgba(18,2,2,.72)'; cx.fill(); cx.globalAlpha=wreck?.55:1; cx.lineWidth=1.4/ZOOM; cx.strokeStyle=col; cx.stroke();
  if(wreck){ const d=s*.32; cx.beginPath(); cx.moveTo(x-d,y-d+s*.15); cx.lineTo(x+d,y+d+s*.15); cx.moveTo(x+d,y-d+s*.15); cx.lineTo(x-d,y+d+s*.15); cx.lineWidth=1/ZOOM; cx.stroke(); }
  else{ cx.fillStyle=col; cx.beginPath(); cx.arc(x,y+s*.12,1.3/ZOOM,0,TAU); cx.fill(); }
  cx.restore();
}
// a contact's triangle by its age tier (sky mode): fresh small and yellow, aging amber, a raider big and red
function vecTier(f){ return f.kind!=='contact'?[6.5,VC.red]:f.tier==='raider'?[8.5,VC.red]:f.tier==='aging'?[6.5,VC.amber]:[5.5,VC.yellow]; }
function vecTargets(m,t){
  const er=m.r+26, n=Math.min(8,m.enemies||0), z=1/ZOOM, foes=[], live=[]; let locked=null;
  for(let i=0;i<n;i++){ const f=m.foes[i], a=i*TAU/n+(seedOf(m.uid)%7), x=m.x+Math.cos(a)*er, y=m.y+Math.sin(a)*er*FOE_SLANT;   // the problems' own orbit (drawBattle's)
    foes.push([x,y]); if(f.kind!=='wreck') live.push(i);
    const vt=vecTier(f); plateHull(x,y,Math.max(10,vt[0]+3.5)*z); vecTri(x,y,vt[0]*z,f.kind==='wreck',vt[1]);
    if(mouseW&&!dragging&&Math.hypot(mouseW.x-x,mouseW.y-y)<14*z) locked=[f,x,y]; }
  if(locked) vecLock(locked[0],locked[1],locked[2],t);
}
// the problem under the pointer: a red lock bracket closing in, and its text on a leader line
function vecLock(f,x,y,t){
  const L=FX.lock; if(!L||L.uid!==f.uid||t-L.seen>120) FX.lock={uid:f.uid,t0:t,seen:t}; else L.seen=t;
  const k=REDUCED?1:Math.min(1,(t-FX.lock.t0)/200), z=1/ZOOM, d=(15-5*k)*z, a=5*z;
  cx.save(); cx.strokeStyle=VC.red; cx.lineWidth=z; cx.beginPath();
  for(let sx=-1;sx<=1;sx+=2) for(let sy=-1;sy<=1;sy+=2){ cx.moveTo(x+sx*d,y+sy*(d-a)); cx.lineTo(x+sx*d,y+sy*d); cx.lineTo(x+sx*(d-a),y+sy*d); }
  cx.stroke(); cx.restore();
  queuePlate({key:'foe:'+f.uid,kind:'seat',pri:PLATE_PRI.seat,x:x,y:y,r:16*z,face:'s',hot:true,lead:VC.red,
    segs:[{t:fitLabel(String(f.full||f.name||'problem'),300),col:VC.red}]});
}
// ---- craft: schematic silhouettes. Every cell of the skin's sprite grid (whichever skin) in one colour with a 1 px darker
// outline, at the current scale, turned smoothly. Cached per sprite, colours and device cell size. ----
function vecHull(name,fill,edge,cq){
  const rows=SPRITES[name]||SPRITES.fighter;
  return vecCache(['vc',name,fill,edge,cq].join('|'),()=>{
    const w=Math.max(...rows.map(r=>r.length)), h=rows.length, W2=Math.ceil(w*cq)+4, H2=Math.ceil(h*cq)+4, U=4, c4=cq*U;
    // the grid's cells at 4x, blurred and cut back at half strength: the cell steps become one smooth contour (a schematic, not pixel art)
    const m1=vecCanvas(W2*U,H2*U), g1=m1.getContext('2d'); g1.fillStyle='#ffffff';
    rows.forEach((row,y)=>{ for(let x=0;x<row.length;x++){ const ch=row[x]; if(ch==='.'||ch===' '||ch==='e') continue; g1.fillRect(2*U+x*c4-.5,2*U+y*c4-.5,c4+1,c4+1); } });
    const m2=vecCanvas(W2*U,H2*U), g2=m2.getContext('2d'); g2.filter='blur('+(c4*.42).toFixed(2)+'px)'; g2.drawImage(m1,0,0); g2.filter='none';
    try{ const id=g2.getImageData(0,0,m2.width,m2.height), d=id.data; for(let i=3;i<d.length;i+=4){ const a=d[i]; d[i]=a<96?0:a>160?255:Math.round((a-96)*4); } g2.putImageData(id,0,0); }catch(e){}
    const tint=(src,col)=>{ const c2=vecCanvas(src.width,src.height), g=c2.getContext('2d'); g.drawImage(src,0,0); g.globalCompositeOperation='source-in'; g.fillStyle=col; g.fillRect(0,0,c2.width,c2.height); return c2; };
    const body=tint(m2,fill), rim=tint(m2,edge), big=vecCanvas(W2*U,H2*U), gb=big.getContext('2d');
    [[-1,0],[1,0],[0,-1],[0,1],[-1,-1],[1,-1],[-1,1],[1,1]].forEach(dd=>gb.drawImage(rim,dd[0]*U,dd[1]*U)); gb.drawImage(body,0,0);
    const out=vecCanvas(W2,H2), g=out.getContext('2d'); g.imageSmoothingEnabled=true; g.imageSmoothingQuality='high'; g.drawImage(big,0,0,W2,H2); return out; });
}
function vecCraft(name,x,y,angle,fill,edge,cell,k){
  cell=craftCell(name,cell||SPRITE_CELL); k=k||1;
  const cq=Math.max(.75,Math.pow(2,Math.round(Math.log2(Math.max(.75,cell*k*ZOOM*(DPR||1)))*4)/4)), img=vecHull(name,fill,edge||mixPixel(fill,'#000000',.62),cq), s=cell*k/cq;
  cx.save(); cx.imageSmoothingEnabled=true; cx.translate(x,y); cx.rotate(angle||0); cx.drawImage(img,-img.width*s/2,-img.height*s/2,img.width*s,img.height*s); cx.restore();
}
// a working craft's link to its planet: a thin dashed line running toward the surface
function vecBeam(u,sx,sy,col,t){
  const m=u.docked_at&&(deck==='home'?MOONS:remoteMoons).find(mm=>mm.uid===u.docked_at); if(!m) return;
  const z=1/ZOOM, dx=m.x-sx, dy=m.y-sy, d=Math.hypot(dx,dy), L=d-m.r-6*z; if(L<=8*z) return; const ux=dx/d, uy=dy/d;
  cx.save(); cx.strokeStyle=col||VC.teal; cx.globalAlpha=.6; cx.lineWidth=z; cx.setLineDash([2*z,4*z]); cx.lineDashOffset=REDUCED?0:-(t/40)*z;
  cx.beginPath(); cx.moveTo(sx+ux*8*z,sy+uy*8*z); cx.lineTo(sx+ux*L,sy+uy*L); cx.stroke(); cx.restore();
}
// a planet's folders: grey moons (smooth), cache folders as a dust of dots, hidden folders as small diamonds
function vecRoom(u,sx,sy,r,t){
  const shape=roomShape(u), seed=seedOf(u.uid), z=1/ZOOM;
  if(shape==='cloud'){ const k=Math.max(5,Math.min(14,Math.round(2*Math.log2(2+(u.files||0))))); cx.save(); cx.fillStyle=VC.muted;
    for(let i=0;i<k;i++){ const a=(REDUCED?0:t/(2600+(seed%5)*400))+i*2.39996, d=r*.25+hash3(i,7,1,seed)*r*1.05, s=(i%4===0?1.6:1)*z;
      cx.globalAlpha=.35+.35*hash3(i,2,3,seed); cx.fillRect(sx+Math.cos(a)*d-s/2,sy+Math.sin(a)*d*.75-s/2,s,s); }
    cx.restore(); return; }
  if(shape==='probe'){ const d=Math.max(3*z,r*.55); cx.save(); cx.strokeStyle=VC.line; cx.lineWidth=z; cx.beginPath(); cx.moveTo(sx,sy-d); cx.lineTo(sx+d,sy); cx.lineTo(sx,sy+d); cx.lineTo(sx-d,sy); cx.closePath(); cx.stroke(); cx.restore(); return; }
  vecSphere(sx,sy,Math.max(3,shape==='belt'?r*1.2:r),'rock',seed%4,{});
}
// ---- the sun (this machine): a soft amber glow, a white-hot core, a long thin horizontal flare streak; its name under it ----
function vecStar(t,name,quests,oldest){
  const s=LY.star, r=s.r, fl=0, z=1/ZOOM;
  cx.save();
  const g1=cx.createRadialGradient(s.x,s.y,0,s.x,s.y,r*3.4); g1.addColorStop(0,'rgba(255,234,167,'+(.5+fl).toFixed(3)+')'); g1.addColorStop(.25,'rgba(225,112,85,.2)'); g1.addColorStop(1,'rgba(225,112,85,0)');
  const painted=[];   // each layer on the audit's list only when its paint calls happened (kindsLayerPaint)
  kindsLayerPaint(painted,'glow',()=>{ cx.fillStyle=g1; cx.beginPath(); cx.arc(s.x,s.y,r*3.4,0,TAU); kindsPaint(cx,'fill'); });
  const L=Math.max(r*6,560*z), st=cx.createLinearGradient(s.x-L,0,s.x+L,0); st.addColorStop(0,'rgba(255,234,167,0)'); st.addColorStop(.3,'rgba(255,234,167,.14)'); st.addColorStop(.44,'rgba(255,246,220,.55)');
  st.addColorStop(.5,'rgba(255,250,235,.95)'); st.addColorStop(.56,'rgba(255,246,220,.55)'); st.addColorStop(.7,'rgba(255,234,167,.14)'); st.addColorStop(1,'rgba(255,234,167,0)');
  kindsLayerPaint(painted,'streak',()=>{ cx.fillStyle=st; kindsPaint(cx,'fillRect',s.x-L,s.y-.75*z,L*2,1.5*z); cx.globalAlpha=.16; kindsPaint(cx,'fillRect',s.x-L*.6,s.y-3*z,L*1.2,6*z); cx.globalAlpha=1; });
  // the white-hot disc reaches ~0.9 r: the star stays the biggest body on the map (LY.star.r is 1.2 x the biggest planet)
  const g2=cx.createRadialGradient(s.x,s.y,0,s.x,s.y,r*1.05); g2.addColorStop(0,'#ffffff'); g2.addColorStop(.38,'#fffaf0'); g2.addColorStop(.72,'rgba(255,240,200,.93)'); g2.addColorStop(.88,'rgba(255,222,150,.55)'); g2.addColorStop(1,'rgba(255,222,150,0)');
  kindsLayerPaint(painted,'disc',()=>{ cx.fillStyle=g2; cx.beginPath(); cx.arc(s.x,s.y,r*1.05,0,TAU); kindsPaint(cx,'fill'); });
  // two thin instrument rings round the disc, ticks every 10 degrees on the outer one (the IW sun reads as measured)
  kindsLayerPaint(painted,'rings',()=>{ vecRing(s.x,s.y,r*1.22,VC.brass,.38); vecRing(s.x,s.y,r*1.42,VC.brass,.22,[2,6]); });
  kindsSunDrew(painted,false);   // the vector sun has no stepped surface: the audit says so (ui/kinds.js)
  cx.strokeStyle=VC.brass; cx.globalAlpha=.42; cx.lineWidth=z; cx.beginPath();
  for(let i=0;i<36;i++){ const a=i*TAU/36, l=(i%9===0?10:4)*z, c=Math.cos(a), sn=Math.sin(a); cx.moveTo(s.x+c*r*1.22,s.y+sn*r*1.22); cx.lineTo(s.x+c*(r*1.22+l),s.y+sn*(r*1.22+l)); }
  cx.stroke(); cx.globalAlpha=1;
  cx.restore();
  const self=String(name)===String(W.name||'CONTAINER');
  tagPlate({name:starPlateName(name).toUpperCase(),suffix:self?'':'· THAT MACHINE',x:s.x,y:s.y,r:r*1.15,r0:r*.9,gap:14,face:'s',vhue:VC.brass,uid:BOARD.uid,
    always:QUIET?'':(quests.length+' '+CM.countNoun('decisions',quests.length)+' · oldest '+CM.ageWords(oldest||0)).toUpperCase()});
}
// the system in the vector look: warp routes, the orbits, flows, the planets, the sun, the other machines
function vecSystem(t,moons,list,starName,quests,oldest){
  // the overview: no routes to the other machines and no machines (the Machines chip lists them), no folder links;
  // the orbit ring and its sector names only in the full view
  if(!QUIET){ vecOrbits(t,moons,1); FC.drawn.rings++; }
  moons.forEach(m=>drawMoon(m,t));
  vecStar(t,starName,quests,oldest);
}
function vecMoonBody(m,t,seed,cold,empty,isHome){
  const z=1/ZOOM, pulse=REDUCED?0:Math.sin(t/900+seed%7), rot=REDUCED?0:t/60, focus=CM.focusUid()===m.uid;
  if(focus&&m.ringR&&m.type!=='asteroid'){ vecRing(m.x,m.y,m.ringR,VC.dim,.6,[2,5]); FC.drawn.rings++; }   // the rings and the threat zone: detail of the open planet
  if(focus&&m.outerR){ vecRing(m.x,m.y,m.outerR,VC.faint,.9,[2,6]); FC.drawn.rings++; }
  if(focus&&m.enemies) vecThreat(m,t);              // the zone lies on the plane, under the body
  if(focus&&m.wellR) vecWell(m,t);
  cx.save(); kindsDrawMoon(m,t); cx.restore();   // the purpose body, one art pixel per screen pixel, smoothed (ui/kinds.js)
  VEC_AUD.drawn.push({uid:m.uid,label:m.label,x:(m.x-camx)*ZOOM,y:(m.y-camy)*ZOOM,r:m.r*ZOOM});
  if(!focus) return;
  if(m.trend){ if(m.trend.worse) vecRing(m.x,m.y,m.r+8*z,VC.red,.5+.4*Math.abs(pulse),[5,4],rot); else if(m.trend.better) vecRing(m.x,m.y,m.r+8*z,VC.teal,.55); }
  if(isHome){ vecRing(m.x,m.y,m.r+12*z,VC.yellow,.75,[6,5],0); }
}
// ---- selection and hover: the dual-ring targeting bracket (an inner ring, an outer ring broken into two arcs, four ticks), slow pulse ----
function vecBracket(x,y,r,col,t,strong){
  const z=1/ZOOM, p=REDUCED?0:Math.sin(t/700)*1.5, r1=r+5*z, r2=r+(11+p)*z, g=.5;
  cx.save(); cx.strokeStyle=col; cx.lineWidth=z; cx.globalAlpha=strong?.95:.78;
  cx.beginPath(); cx.arc(x,y,r1,0,TAU); cx.stroke();
  cx.beginPath(); cx.arc(x,y,r2,Math.PI/2+g,Math.PI*1.5-g); cx.stroke(); cx.beginPath(); cx.arc(x,y,r2,-Math.PI/2+g,Math.PI/2-g); cx.stroke();
  cx.beginPath(); [[0,-1],[1,0],[0,1],[-1,0]].forEach(([dx,dy])=>{ cx.moveTo(x+dx*(r2+2*z),y+dy*(r2+2*z)); cx.lineTo(x+dx*(r2+8*z),y+dy*(r2+8*z)); }); cx.stroke();
  cx.restore();
}
function vecFocus(t){
  const at=u=>({x:u===BOARD?LY.star.x:u.gx*TILE,y:u===BOARD?LY.star.y:u.gy*TILE});
  if(hover&&!dragging){ const p=at(hover); vecBracket(p.x,p.y,focusRadius(hover),composerOpen()?VC.teal:VC.text,t,false); }
  const sel=openUnit&&openUnit!==hover?(openUnit===BOARD?BOARD:unitByUid(openUnit.uid)):null;
  if(sel&&(sel===BOARD||sel.gx!=null)){ const p=at(sel); vecBracket(p.x,p.y,focusRadius(sel),VC.mint,t,true); }
  for(let i=BURSTS.length-1;i>=0;i--){ const b=BURSTS[i], k=(t-b.t0)/420; if(k>=1){ BURSTS.splice(i,1); continue; }
    cx.save(); cx.globalAlpha=(1-k)*.85; cx.strokeStyle=VC.mint; cx.lineWidth=1/ZOOM; cx.beginPath(); cx.arc(b.x,b.y,b.r+k*30/ZOOM,0,TAU); cx.stroke(); cx.restore(); }
}
// ---- screen pass: the tether to the open card, the edge chevrons, the compass ----
function vecScreen(t){ cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.textBaseline='alphabetic'; vecTether(t); vecChevrons(t); vecCompass(t); cx.restore(); }
// the part of the stage the map really shows: inside the bands laid over it the full width (the rails, a phone's bars, the ticker)
function vecView(){
  let top=0, bottom=VH-(MOB?plateSheet():0); const left=0, right=VW-PANEL_W;
  overlayBoxes().forEach(b=>{ if(b.w<.6*(right-left)) return; if(b.y+b.h/2<VH/2) top=Math.max(top,b.y+b.h); else bottom=Math.min(bottom,b.y); });
  return {x0:left,y0:top,x1:right,y1:bottom};
}
// a straight tether from the selected body to the nearest edge of its open card
function vecTether(t){
  const u=openUnit; if(!u||(u!==BOARD&&u.gx==null)) return; const P=document.getElementById('panel'); if(!P||!P.classList||!P.classList.contains('on')) return;
  let r=null, sr=null; try{ r=P.getBoundingClientRect(); sr=STAGE&&STAGE.getBoundingClientRect(); }catch(e){}
  if(!r||!(r.width>1&&r.height>1)) return;
  const ox=sr?sr.left:0, oy=sr?sr.top:0, x0=r.left-ox, y0=r.top-oy, x1=x0+r.width, y1=y0+r.height;
  const wx=u===BOARD?LY.star.x:u.gx*TILE, wy=u===BOARD?LY.star.y:u.gy*TILE, sx=(wx-camx)*ZOOM, sy=(wy-camy)*ZOOM;
  if(sx>=x0&&sx<=x1&&sy>=y0&&sy<=y1) return;
  const nx=Math.max(x0,Math.min(sx,x1)), ny=Math.max(y0,Math.min(sy,y1)), d=Math.hypot(nx-sx,ny-sy), rr=focusRadius(u)*ZOOM+13; if(d<=rr+4) return;
  const ux=(nx-sx)/d, uy=(ny-sy)/d;
  cx.save(); cx.strokeStyle=VC.mint; cx.globalAlpha=.7; cx.lineWidth=1; cx.beginPath(); cx.moveTo(sx+ux*rr,sy+uy*rr); cx.lineTo(nx,ny); cx.stroke();
  cx.globalAlpha=.95; cx.fillStyle=VC.mint; cx.fillRect(Math.round(nx)-2,Math.round(ny)-2,4,4); cx.restore();
  VEC_AUD.tether={uid:u.uid,from:[sx,sy],to:[nx,ny]};
}
// damage arcs: each troubled planet out of the view gets a red curved chevron at the edge, pointing at it. The list is
// worked out once a frame from the camera, before the plates are laid out, so the plates keep off the chevrons (plateObstacles).
let VEC_CHEV={at:-1,list:[]};
function vecChevronList(){
  if(VEC_CHEV.at===VEC_AUD.frames) return VEC_CHEV.list;
  const V=vecView(), cxv=(V.x0+V.x1)/2, cyv=(V.y0+V.y1)/2, inset=24, out=[];
  if(V.x1-V.x0>2*inset+20&&V.y1-V.y0>2*inset+20) (deck==='home'?MOONS:remoteMoons).forEach(m=>{ if(!m.enemies) return;   // the chevron marks a threat zone (its foes), as the zone itself counts them
    const sx=(m.x-camx)*ZOOM, sy=(m.y-camy)*ZOOM, rr=vecThreatR(m)*ZOOM;   // any of its threat zone in view: no chevron
    if(sx+rr>V.x0&&sx-rr<V.x1&&sy+rr*FOE_SLANT>V.y0&&sy-rr*FOE_SLANT<V.y1) return;
    const dx=sx-cxv, dy=sy-cyv, hw=(V.x1-V.x0)/2-inset, hh=(V.y1-V.y0)/2-inset, k=Math.min(Math.abs(hw/(dx||1e-9)),Math.abs(hh/(dy||1e-9)));
    out.push({uid:m.uid,label:m.label,enemies:m.enemies,x:cxv+dx*k,y:cyv+dy*k,angle:Math.atan2(dy,dx),alpha:.5+.5*Math.min(1,m.enemies/8),target:{x:sx,y:sy}}); });
  VEC_CHEV={at:VEC_AUD.frames,list:out}; return out;
}
function vecChevrons(t){
  const out=vecChevronList();
  out.forEach(c=>{ const ux=Math.cos(c.angle), uy=Math.sin(c.angle), R=130, qx=c.x-ux*R, qy=c.y-uy*R;
    cx.save(); cx.lineCap='butt'; cx.globalAlpha=c.alpha*.7; cx.strokeStyle='rgba(0,0,0,.75)'; cx.lineWidth=9; cx.beginPath(); cx.arc(qx,qy,R,c.angle-.29,c.angle+.29); cx.stroke();
    cx.globalAlpha=c.alpha; cx.strokeStyle=VC.red; cx.lineWidth=5; cx.beginPath(); cx.arc(qx,qy,R,c.angle-.28,c.angle+.28); cx.stroke();
    cx.globalAlpha=c.alpha*.5; cx.lineWidth=1; cx.beginPath(); cx.arc(qx,qy,R+6,c.angle-.2,c.angle+.2); cx.stroke(); cx.globalAlpha=c.alpha;
    cx.lineWidth=2; cx.beginPath(); cx.moveTo(c.x-ux*6-uy*11,c.y-uy*6+ux*11); cx.lineTo(c.x+ux*8,c.y+uy*8); cx.lineTo(c.x-ux*6+uy*11,c.y-uy*6-ux*11); cx.stroke();
    cx.font=vecFont(12,0,'data'); cx.textAlign='center'; vecHalo(String(c.label).toUpperCase()+' '+c.enemies,Math.round(c.x-ux*26),Math.round(c.y-uy*26+4),VC.red,3);
    cx.restore(); });
  VEC_AUD.chevrons=out;
}
// what the compass's gold star points at: the oldest decision that waits on the owner (the planet it names, else the board at the
// star), else a thread to review, else a seat that asks, else home (the wingmate's home planet, or this machine)
function vecNeed(){
  const L=deck==='home'?MOONS:remoteMoons, US=deck==='home'?units:remoteUnits, qs=(curQuests()||[]).slice().sort((a,b)=>(Number(b.days)||0)-(Number(a.days)||0));
  if(qs.length){ const words=String(qs[0].text||'').toLowerCase().split(/[^a-z0-9_.-]+/), m=L.find(mm=>mm.label.length>=2&&words.includes(String(mm.label).toLowerCase()));
    return m?{x:m.x,y:m.y,what:m.label,why:'oldest decision'}:{x:LY.star.x,y:LY.star.y,what:'board',why:'oldest decision'}; }
  const tk=US.find(u=>u.kind==='task'&&u.status==='review'); if(tk) return {x:tk.gx*TILE,y:tk.gy*TILE,what:String(tk.name||''),why:'review'};
  const ws=US.find(u=>isShip(u)&&(u.status==='WAITING'||u.status==='blocked')); if(ws) return {x:ws.gx*TILE,y:ws.gy*TILE,what:String(ws.name||''),why:'asks'};
  const hp=String((W.wingmate&&W.wingmate.home_planet)||''), hm=hp&&L.find(m=>m.label===hp); if(hm) return {x:hm.x,y:hm.y,what:hm.label,why:'home'};
  return {x:LY.star.x,y:LY.star.y,what:'home',why:'home'};
}
// the brass compass, bottom left of the stage (desktop pointer only): two rings, ticks every 30 degrees, N E S W, the needle on
// the player's heading, a gold star on the bearing of what needs the owner. Nothing in it moves by itself.
function vecCompass(t){
  if(touchUI()||VW<360||VH<300) return;   // a phone or a tablet has its own controls in that corner
  const R=46, ox=75, oy=VH-vecRails().bottom-51, nd=vecNeed(), bx=nd.x-px, by=nd.y-py, near2=Math.hypot(bx,by)<24, bear=Math.atan2(by,bx);
  cx.save(); cx.translate(ox,oy);
  cx.fillStyle='rgba(4,7,6,.62)'; cx.beginPath(); cx.arc(0,0,R+2,0,TAU); cx.fill();
  cx.lineWidth=1; cx.strokeStyle=VC.brass; cx.globalAlpha=.95; cx.beginPath(); cx.arc(0,0,R,0,TAU); cx.stroke();
  cx.strokeStyle=VC.brass2; cx.globalAlpha=.75; cx.beginPath(); cx.arc(0,0,R-7,0,TAU); cx.stroke();
  cx.strokeStyle=VC.brass; cx.globalAlpha=.9; cx.beginPath();
  for(let i=0;i<12;i++){ const a=i*TAU/12-Math.PI/2, c=Math.cos(a), s=Math.sin(a), r0=i%3?R-7:R-11; cx.moveTo(c*r0,s*r0); cx.lineTo(c*(R-2),s*(R-2)); }
  cx.stroke();
  cx.font=vecFont(12,600); cx.textAlign='center'; cx.textBaseline='middle';
  [['N',-Math.PI/2],['E',0],['S',Math.PI/2],['W',Math.PI]].forEach(([l,a])=>{ cx.globalAlpha=1; cx.fillStyle=l==='N'?VC.text:VC.brass; cx.fillText(l,Math.cos(a)*(R-19),Math.sin(a)*(R-19)+1); });
  // the gold star: on the outer ring at the bearing, at the centre when the ship is there
  const sr=near2?0:R-3.5, gx=Math.cos(bear)*sr, gy=Math.sin(bear)*sr; cx.fillStyle='#f2c94c'; cx.strokeStyle='#3a2a00'; cx.lineWidth=1; cx.beginPath();
  for(let i=0;i<10;i++){ const a=-Math.PI/2+i*Math.PI/5, rr=i%2?2.3:5.6; cx.lineTo(gx+Math.cos(a)*rr,gy+Math.sin(a)*rr); } cx.closePath(); cx.fill(); cx.stroke();
  // the needle: the heading, off-white ahead, brass behind
  const h=heading, c=Math.cos(h), s=Math.sin(h), pxn=-s, pyn=c;
  cx.beginPath(); cx.moveTo(c*(R-24),s*(R-24)); cx.lineTo(pxn*3,pyn*3); cx.lineTo(-pxn*3,-pyn*3); cx.closePath(); cx.fillStyle=VC.text; cx.fill();
  cx.beginPath(); cx.moveTo(-c*14,-s*14); cx.lineTo(pxn*3,pyn*3); cx.lineTo(-pxn*3,-pyn*3); cx.closePath(); cx.fillStyle=VC.brass2; cx.fill();
  cx.fillStyle=VC.brass; cx.beginPath(); cx.arc(0,0,2,0,TAU); cx.fill();
  cx.restore();
  VEC_AUD.compass={x:ox,y:oy,r:R,heading:h,bearing:bear,star:[ox+gx,oy+gy],target:nd.what,why:nd.why};
}
// ---- the boot: once per tab session when the map is first shown, never under reduced motion, any key, click or tap skips it.
// A scanline sweeps down with a brief noise flicker (350 ms), the product mark sits between bracket ticks with the root path
// and the dossier stamp under it (500 ms), it dissolves (200 ms), the orbits grow out of the star while the map fades in
// (700 ms): 1750 ms in all. ----
const BOOT={on:false,done:false,t0:0,end:0,ms:0,reason:''};
const BOOT_SCAN=350, BOOT_HOLD=850, BOOT_GONE=1050, BOOT_END=1750;
const VEC_DAYS=['SUN','MON','TUE','WED','THU','FRI','SAT'], VEC_MONS=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
function vecStamp(sep){ const d=new Date(), p=n=>String(n).padStart(2,'0');
  return VEC_DAYS[d.getDay()]+' '+p(d.getDate())+' '+VEC_MONS[d.getMonth()]+' '+d.getFullYear()+' '+(sep||'·')+' '+p(d.getHours())+p(d.getMinutes())+' HRS'; }
function vecMark(){ let n=''; try{ const h=document.querySelector('#hud h1'); n=String((h&&h.textContent)||'').trim(); }catch(e){} return (n||'CONTAINER').toUpperCase(); }
function vecBootStart(t){
  if(BOOT.on||BOOT.done) return;
  if(!(MOB?!mapCovered():VIEW==='map')) return;          // it plays when the map is first on screen
  BOOT.done=true;
  if(REDUCED){ BOOT.reason='reduced motion'; return; }
  let seen=null; try{ seen=sessionStorage.getItem('vecboot'); sessionStorage.setItem('vecboot','1'); }catch(e){}
  if(seen){ BOOT.reason='once per session'; return; }
  BOOT.on=true; BOOT.t0=t; BOOT.reason='played';
}
// the boot's count line, the one count (W.counts): 13 NEED YOU · 53 DECISIONS · 1 PROBLEM · 1 WORKING ('' with no sky)
function bootLine(){ if(!skyOk()) return ''; const c=CM.counts();
  return CM.countLine(c).toUpperCase()+' · '+(Number.isInteger(c.working)?c.working:0)+' '+CM.countNoun('working',2).toUpperCase(); }
function vecBootEnd(why){ if(!BOOT.on) return; BOOT.on=false; BOOT.end=(typeof performance!=='undefined'&&performance.now)?performance.now():BOOT.t0+BOOT_END; BOOT.ms=Math.round(BOOT.end-BOOT.t0); if(why) BOOT.reason=why; }
['keydown','pointerdown','touchstart','wheel'].forEach(ev=>addEventListener(ev,()=>{ if(BOOT.on) vecBootEnd('skipped'); },{capture:true,passive:true}));
function vecBootDraw(t){
  if(!BOOT.on) return; const e=t-BOOT.t0; if(e>=BOOT_END){ vecBootEnd(''); return; } if(e<0) return;
  const bg=vecBackdrop(); cx.save(); cx.setTransform(DPR,0,0,DPR,0,0);
  if(e<BOOT_GONE){
    cx.drawImage(bg,0,0,VW,VH);
    if(e<BOOT_SCAN){ const y=Math.round(e/BOOT_SCAN*VH), gr=cx.createLinearGradient(0,y-60,0,y);
      gr.addColorStop(0,'rgba(163,232,208,0)'); gr.addColorStop(1,'rgba(163,232,208,.16)'); cx.fillStyle=gr; cx.fillRect(0,y-60,VW,60);
      cx.fillStyle=VC.mint; cx.globalAlpha=.9; cx.fillRect(0,y,VW,1); cx.globalAlpha=1;
      if(e<220){ const f=Math.floor(e/40); cx.fillStyle=VC.text;      // the noise flicker: a few seeded streaks a frame
        for(let i=0;i<46;i++){ cx.globalAlpha=.08+.18*hash3(i,f,1,7); cx.fillRect(Math.round(hash3(i,f,2,7)*VW),Math.round(hash3(i,f,3,7)*VH),Math.round(4+hash3(i,f,4,7)*90),1); }
        cx.globalAlpha=1; } }
    else{ const fin=Math.min(1,(e-BOOT_SCAN)/90), out=e>BOOT_HOLD?(e-BOOT_HOLD)/(BOOT_GONE-BOOT_HOLD):0, a=fin*(1-out);
      const mk=vecMark(), cxm=Math.round(VW/2), cym=Math.round(VH/2)-14, trk=13+out*18;
      cx.globalAlpha=a; cx.font=vecFont(44,600); track(trk); cx.textAlign='center'; cx.textBaseline='alphabetic';
      const w=cx.measureText(mk).width; cx.fillStyle=VC.text; cx.fillText(mk,cxm+trk/2,cym+15); track(0);
      const bx=Math.round(w/2+26); cx.strokeStyle=VC.line; cx.lineWidth=1; cx.beginPath();
      [-1,1].forEach(sd=>{ const x=cxm+sd*bx+.5; cx.moveTo(x-sd*8,cym-20.5); cx.lineTo(x,cym-20.5); cx.lineTo(x,cym+20.5); cx.lineTo(x-sd*8,cym+20.5);
        cx.moveTo(x+sd*10,cym+.5); cx.lineTo(x+sd*(10+Math.min(140,VW/2-bx-30)),cym+.5); }); cx.stroke();
      cx.font=vecFont(12,0,'data'); track(1.5); cx.fillStyle=VC.muted;
      cx.fillText(String(W.root_path||'').slice(0,72),cxm,cym+50); cx.fillText(vecStamp('·'),cxm,cym+68);
      const bl=bootLine(); if(bl){ cx.fillStyle=VC.mint; cx.fillText(bl,cxm,cym+86); } track(0); }
  } else{   // the map comes up: the backdrop over it thins out while the orbits grow from the star to their radii
    const k=(e-BOOT_GONE)/(BOOT_END-BOOT_GONE), ease=1-Math.pow(1-k,3);
    cx.globalAlpha=1-k; cx.drawImage(bg,0,0,VW,VH); cx.globalAlpha=1;
    cx.setTransform(DPR*ZOOM,0,0,DPR*ZOOM,-camx*ZOOM*DPR,-camy*ZOOM*DPR); vecOrbits(t,deck==='home'?MOONS:remoteMoons,Math.max(.02,ease));
  }
  cx.restore();
}
// ---- the merge intertitle: a full-width black band across the middle for 2.4 s, a thin brass rule, OPERATION <TITLE>
// in a serif, the dossier stamp in mono, then it fades. It replaces the merge banner in the vector look. ----
function vecInto(title){ const tk=agentTasks().find(x=>taskTitle(x).slice(0,56)===title); return tk&&tk.base_branch?String(tk.base_branch).toUpperCase():'YOUR BRANCH'; }
function vecIntertitle(t){
  const M=FX.toast; if(!M) return; const age=t-M.t0, DUR=2400; if(age>DUR){ FX.toast=null; return; } if(age<0) return;
  if(M.into==null&&!M.word) M.into=vecInto(M.title);
  const a=REDUCED?1:Math.max(0,Math.min(1,age/200,(DUR-age)/400)), cym=Math.round(VH/2), h=Math.round(Math.min(160,VH*.3)), rw=Math.round(Math.min(560,VW*.62));
  const grow=REDUCED?1:Math.min(1,age/320), t2=REDUCED?1:Math.min(1,Math.max(0,(age-120)/260)), t3=REDUCED?1:Math.min(1,Math.max(0,(age-420)/260));
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.globalAlpha=a; cx.fillStyle='rgba(0,0,0,.94)'; cx.fillRect(0,cym-h/2,VW,h);
  cx.fillStyle=VC.brass; const gw=Math.round(rw*grow); cx.fillRect(Math.round(VW/2-gw/2),cym-h/2+28,gw,1); cx.fillRect(Math.round(VW/2-gw/2),cym+h/2-28,gw,1);
  let fs=30, title='OPERATION '+String(M.title||'').toUpperCase(); cx.textAlign='center'; cx.textBaseline='alphabetic';
  for(;fs>18;fs-=2){ cx.font=vecFont(fs,0,'serif'); track(fs*.18); if(cx.measureText(title).width<=VW-60) break; }
  while(title.length>12&&cx.measureText(title).width>VW-60) title=title.slice(0,-2).trimEnd()+'…';
  cx.globalAlpha=a*t2; cx.fillStyle='#ece4d0'; cx.fillText(title,VW/2,cym+6); track(0);
  cx.globalAlpha=a*t3; cx.font=vecFont(13,0,'data'); track(2); cx.fillStyle=VC.brass2;
  cx.fillText(vecStamp('—')+' · '+(M.word?String(M.word).toUpperCase():String(TASK_WORD.merged||'merged').toUpperCase()+' INTO '+M.into),VW/2,cym+34); track(0);
  cx.restore();
}
// the hover tip in the console's style: a chamfered translucent card, heavy top rule, the name tracked, the lines in mono
function vecTip(lines){
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.textAlign='left'; cx.textBaseline='alphabetic';
  cx.font=vecFont(14,600); track(1.5); const w0=cx.measureText(String(lines[0]).toUpperCase()).width; track(0);
  cx.font=vecFont(12,0,'data'); const w=Math.ceil(Math.max(w0,...lines.slice(1).map(l=>cx.measureText(l).width)))+24, h=lines.length*17+16;
  let x=mouseW.sx+16, y=mouseW.sy+16; if(x+w>VW-8) x=Math.max(8,mouseW.sx-w-8); if(y+h>VH-vecRails().bottom-8) y=Math.max(vecRails().top+8,mouseW.sy-h-8);
  cx.beginPath(); cx.moveTo(x+8,y); cx.lineTo(x+w,y); cx.lineTo(x+w,y+h); cx.lineTo(x,y+h); cx.lineTo(x,y+8); cx.closePath();
  cx.fillStyle='rgba(11,14,13,.9)'; cx.fill(); cx.strokeStyle=VC.border; cx.lineWidth=1; cx.stroke();
  cx.fillStyle=VC.text; cx.fillRect(x+8,y,w-8,2);
  lines.forEach((l,i)=>{ if(i===0){ cx.font=vecFont(14,600); track(1.5); cx.fillStyle=VC.mint; cx.fillText(String(l).toUpperCase(),x+12,y+20); track(0); }
    else{ cx.font=vecFont(12,0,'data'); cx.fillStyle=i===lines.length-1?VC.muted:VC.body; cx.fillText(l,x+12,y+20+i*17); } });
  cx.restore();
}
// what a test, or a curious console, reads back about the vector look (screen positions are relative to the stage)
window.__vecAudit=function(){
  let sr=null; try{ sr=STAGE&&STAGE.getBoundingClientRect(); }catch(e){}
  const ox=sr?sr.left:0, oy=sr?sr.top:0, L=deck==='home'?MOONS:remoteMoons;
  return {vec:VEC, theme:THEME_NAME, tilt:LY.tilt||1, pixel:VEC_PIX, reroute:VEC_AUD.reroute, frames:VEC_AUD.frames, zoom:ZOOM, stage:{left:ox,top:oy,w:VW,h:VH},
    orbits:VEC_AUD.orbits.slice(), drawn:VEC_AUD.drawn.map(d=>Object.assign({page:[ox+d.x,oy+d.y]},d)),
    planets:L.map(m=>{ const q=unproject(m.x,m.y); return {uid:m.uid,label:m.label,enemies:m.enemies||0,x:(m.x-camx)*ZOOM,y:(m.y-camy)*ZOOM,r:m.r*ZOOM,
      plane:Math.hypot(q.x-LY.star.x,q.y-LY.star.y),ring:(m.orbit||LY.rootOrbit||0)+(m.decay||0)*160}; }),
    threats:VEC_AUD.threats.slice(), chevrons:VEC_AUD.chevrons.slice(), tether:VEC_AUD.tether, compass:VEC_AUD.compass, boot:Object.assign({},BOOT), view:vecView()};
};
const FILE_ICONS=new Map();
function filePlanet(name,isDir=false,fallback='binary',override=null){
  // 'planet:<uid>': that planet's own body (ui/kinds.js), so the card icon is the body on the map
  if(typeof override==='string'&&override.startsWith('planet:')){ const m=MOONS.find(x=>x.uid===override.slice(7)), L=m&&planetLook(m);
    if(L) return '<img class="file-planet" src="'+purposeIcon(L.purpose,28,{seed:(unitByUid(m.uid)||{}).path||m.label,conditions:L.conditions,health:L.health,night:L.nightLights,progress:L.progress}).toDataURL()+'" alt="" title="'+attr(L.name)+'">'; }
  const th=(override&&THEMES[override])?override:fileTheme(name,isDir,fallback),key=th+'|'+(isDir?1:0)+'|'+GB;
  if(!FILE_ICONS.has(key)){
    const art=document.createElement('canvas');art.width=art.height=28;
    art.getContext('2d').drawImage(bodyArt(10,'moon',th,isDir?11:5,0,1),0,0);
    FILE_ICONS.set(key,art.toDataURL());
  }
  return '<img class="file-planet" src="'+FILE_ICONS.get(key)+'" alt="" title="'+attr(THEMES[th].group)+'">';
}
const SHIP_ICONS=new Map();
function shipIcon(col){
  if(!SHIP_ICONS.has(col)) SHIP_ICONS.set(col,spriteArt('fighter',hullPal(col,''),0,16,2).toDataURL());
  return '<img class="file-planet" src="'+SHIP_ICONS.get(col)+'" alt="">';
}
// ---- THEMES AND TEXTURE PACKS ---------------------------------------------------------------
// themes/<name>.json, inlined by the renderer (the page works offline): UI colours (CSS variables,
// switched by html[data-theme]), the space background, every planet surface's palette, optional 8x8
// surface tiles and craft sprites. "classic" is the original look, extracted from the constants above, so
// the default is unchanged. config "theme" is the default; localStorage 'theme' (V cycles) wins.
const THEME_PACKS = __THEMEPACKS__;
const THEME_ALIAS = __THEMEALIAS__;      // renamed theme ids: a saved choice or a config may still say the old one
// The skin (skins/<name>.json): the characters' craft, the words on the plates, the wingmate's face. {} = none loaded.
const SKIN = __SKIN__;
// the 3D ship look: a skin that carries more than one look for its 3D craft lists them in its look3d.options
// ([[value, words], ...]); a "?" > Settings row stores the pick as cm3d.look3d, which world3d/craft.js reads (and applies
// at once). A skin with one look adds no row.
(()=>{ const L=SKIN&&SKIN.look3d, opts=L&&Array.isArray(L.options)?L.options.filter(o=>Array.isArray(o)&&typeof o[0]==='string'&&typeof o[1]==='string'):[];
  if(opts.length<2) return;
  CM.settingsExtra.push({key:'look-3d', group:'3D view', label:'3D ship look', options:opts.map(o=>({value:o[0],label:o[1]})),
    get:()=>p3Get('look3d',String(L.default||opts[0][0])), set:v=>p3Set('look3d',v)}); })();
// (a skin's old "worst" name is not read: the most troubled planet is said in plain words, never a class:)
const SKIN_NAMES = Object.assign({carrier:'CARRIER',merged:'THREAD MERGED',wingmate:'Scout'}, SKIN.names||{});
// the radio's and the sky's words. A state is said in plain words whatever the skin (skins rename characters, never
// states:, words.py); a skin may name only the two banners in its "sky" object, "cleared" (a contact resolved)
// and "empty" (none left): plain text, at most 16 characters. Anything else in a skin's "radio" or "sky" is ignored.
function skinWords(o,defs,max){ const out=Object.assign({},defs);
  if(o&&typeof o==='object') Object.keys(defs).forEach(k=>{ const v=o[k]; if(typeof v==='string'&&v.trim()&&v.length<=(max||24)&&!/[\\x00-\\x1f\\x7f<>&"\\\\]/.test(v)) out[k]=v; });
  return out; }
const RADIO_WORDS = {needs:CM.word('contact:blocked'),down:'stopped',finished:'finished',failed:CM.word('contact:failed'),
  review:CM.word('contact:review'),ready:CM.word('contact:ready'),stale:CM.word('contact:stale'),merged:CM.stateWord('merged')};
const SKY_WORDS = Object.assign(skinWords(SKIN.sky,{cleared:'CLEARED',empty:'ALL CLEAR'},16),
  {warp:CM.countNoun('working',2),contacts:CM.countNoun('need_you',2),parked:CM.countNoun('on_hold',2),settled:CM.countNoun('done_today',2)});
const SKIN_PAL = SKIN.sprite_palette||{};
// a skin's craft grids as the page names them: its problem drone is foe_drone (the page's own drone is a process)
function skinCraft(sp){ const out=Object.assign({},sp||{}); if(out.drone){ out.foe_drone=out.drone; delete out.drone; } return out; }
const THEME_CFG='__THEME__';
const THEME_NAMES=Object.keys(THEME_PACKS);
const GLOW_REF=.6;        // where the classic look sits on a pack's 0-1 glow scale
const TILE_TEXEL=4;       // one tile cell = two 2 px planet cells
const KIND_STYLE={code:'city',docs:'ocean',config:'station',data:'ice',media:'gas',logs:'toxic',archive:'desert',cache:'gas',live:'star',binary:'rock',trouble:'blight'};
// a folder's rooms tile like its purpose's body: the theme surface it takes its colours from (ui/kinds.js kindsSurface), read live
// so a theme that brings an aurora, crystal or asteroid surface is used at once
PURPOSE_KEYS.forEach(p=>Object.defineProperty(KIND_STYLE,'p:'+p,{enumerable:false,get:()=>kindsSurface(p)}));
const BASE_STYLE=JSON.parse(JSON.stringify(STYLE)), BASE_SPRITES=Object.assign({},SPRITES,skinCraft(SKIN.sprites)), BASE_KINDS={};
Object.keys(THEMES).forEach(k=>{ const X=THEMES[k]; BASE_KINDS[k]={base:X.base,land:X.land,hi:X.hi,glow:X.glow}; });
const SPACE_BASE={base:'#050508',nebula:['#2a1460','#0a3a5c','#5c2a0e'],stars:['#ffffff','#a5f3fc','#fef08a','#c0d8ff'],scanlines:false};
const SHADE_BASE={dark:['#0b0628','#180e44','#1e1652'],hi:'#ffffff',edge:'#05030f',rim:'#06041a',out:'#04030e',grey:'#5a6274'};
let PACK=null, THEME_NAME='classic', TILES={}, SPRITE_PAL={}, SPACE=SPACE_BASE, SHADE=SHADE_BASE, GLOWK=1, SECT=null;
// VEC: the pack asks for the vector look ("render": "vector"); the canvas and the chrome key on it (html[data-render])
let VEC=false;
function packLum(h){ const c=[1,3,5].map(i=>parseInt(h.slice(i,i+2),16)/255).map(v=>v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)); return .2126*c[0]+.7152*c[1]+.0722*c[2]; }
// a tiled pack shades toward its own darkest ink and lightest paper instead of the classic purple
function packShade(P){
  const cols=[P.ui.bg,P.ui.text].concat(Object.values(P.tile_palette||{})).sort((a,b)=>packLum(a)-packLum(b));
  const dk=cols[0];
  return {dark:[dk,dk,dk],hi:cols[cols.length-1],edge:dk,rim:dk,out:dk,grey:P.ui.muted};
}
function kindOf(P,k){ const s=P.style[KIND_STYLE[k]]; return s?{base:s.a,land:s.b,hi:s.c,glow:s.glow}:BASE_KINDS[k]; }
function themeStart(){
  if(GB) return 'classic';
  let pick=''; try{ pick=localStorage.getItem('theme')||''; }catch(e){}
  pick=THEME_ALIAS[pick]||pick;
  return THEME_PACKS[pick]?pick:THEME_CFG;
}
function applyTheme(name,save){
  let P=THEME_PACKS[name]||null;
  if(!P){ name=THEME_PACKS[THEME_CFG]?THEME_CFG:(THEME_PACKS.classic?'classic':''); P=THEME_PACKS[name]||null; }
  PACK=P; THEME_NAME=name||'classic';
  const S=P?P.style:BASE_STYLE;
  Object.keys(STYLE).forEach(k=>{ delete STYLE[k]; }); Object.keys(S).forEach(k=>{ STYLE[k]=Object.assign({},S[k]); });
  Object.keys(THEMES).forEach(k=>{ const K=P?(P.kinds?P.kinds[k]:kindOf(P,k)):BASE_KINDS[k]; if(K) Object.assign(THEMES[k],K); });
  Object.keys(SPRITES).forEach(k=>{ delete SPRITES[k]; }); Object.assign(SPRITES,BASE_SPRITES,(P&&P.sprites)||{});
  SPRITE_PAL=(P&&P.sprite_palette)||{};
  const tp=(P&&P.tile_palette)||{}; TILES={};
  Object.keys((P&&P.tiles)||{}).forEach(k=>{ TILES[k]=P.tiles[k].map(row=>row.split('').map(ch=>tp[ch])); });
  const sp=P&&P.space;
  SPACE=sp?{base:sp.base||P.ui.bg,nebula:sp.nebula,stars:sp.stars,scanlines:!!sp.scanlines}:SPACE_BASE;
  GLOWK=sp?sp.glow/GLOW_REF:1;
  SHADE=(P&&Object.keys(TILES).length)?packShade(P):SHADE_BASE; SECT=(P&&P.sectors)||null;
  Object.keys(SHADES).forEach(k=>{ delete SHADES[k]; }); Object.keys(PAT).forEach(k=>{ delete PAT[k]; }); BODY_ART.clear(); FILE_ICONS.clear(); SHIP_ICONS.clear();
  CM.invalidateBackdrop();   // the baked 2D backdrop is this theme's: another theme's colours bake again (ui/kinds.js)
  const wasVec=VEC;
  VEC=!GB&&!!(P&&P.render==='vector');
  if(VEC) VC=vecPal(P); VEC_ART.clear(); VEC_BG=null;
  const de=document.documentElement; if(de&&de.setAttribute){ de.setAttribute('data-theme',THEME_NAME); de.setAttribute('data-render',VEC?'vector':'pixel'); }
  if(save){ try{ localStorage.setItem('theme',THEME_NAME); }catch(e){} }
  themeUi();
  if(VEC!==wasVec) vecRelayout();
}
// the tilt is part of the layout: switching into or out of the vector look lays the system out again (and the home view with it)
function vecRelayout(){
  try{ place(); }catch(e){ return; }
  if(deck!=='home'&&remoteSynced){ try{ buildRemoteSystem(deck,remoteSynced,remoteState,remoteInfo); }catch(e){} }
  if(HOME.on){ HOME.laid=false; try{ homeSettle(); }catch(e){} }
}
function themeLegend(){
  if(THEME_NAMES.length<2) return '';
  const P=THEME_PACKS[THEME_NAME];
  return '<div class="lg"><b>THEME</b> · V cycles · '+THEME_NAMES.map(n=>'<a data-theme-pick="'+attr(n)+'"'+(n===THEME_NAME?' class="on"':'')+'>'+esc(n)+'</a>').join('')+
    (P?'<br>'+esc(P.mood):'')+(GB?'<br>off while the mono palette is on (MONO button)':'')+'</div>';
}
function themeUi(){
  const el=document.getElementById('themeline'); if(el&&THEME_NAMES.length>1) el.innerHTML='THEME · <b>'+esc(THEME_NAME)+'</b> · V';
  const L=document.getElementById('legend'); if(L&&L.classList&&L.classList.contains('on')) L.innerHTML=systemLegend();
}
function cycleTheme(d){
  if(THEME_NAMES.length<2) return;
  if(GB){ toast('themes are off in the mono palette (MONO button)',2600); return; }
  const i=Math.max(0,THEME_NAMES.indexOf(THEME_NAME)); applyTheme(THEME_NAMES[(i+(d||1)+THEME_NAMES.length)%THEME_NAMES.length],true);
  toast('theme · '+THEME_NAME+(PACK?' — '+PACK.mood:''),3200);
}
addEventListener('keydown',e=>{ if(typing()||e.repeat||modKey(e)) return; if(e.key==='v'||e.key==='V') cycleTheme(e.shiftKey?-1:1); });
document.addEventListener('click',e=>{
  const t=e.target&&e.target.closest?e.target:null; if(!t) return;
  const a=t.closest('[data-theme-pick]'); if(a){ e.preventDefault(); if(!GB) applyTheme(a.dataset.themePick,true); return; }
  if(t.closest('#themeline')||t.closest('#keyline a[data-k="v"]')) cycleTheme(1);
});
// the tile for a surface, or null: the procedural look. A moon takes its file kind's surface.
function tileOf(style,theme){
  if(GB) return null;
  const k=style==='moon'?(KIND_STYLE[theme]||'rock'):(STYLE[style]&&!KIND_STYLE[style]?style:(KIND_STYLE[style]||'rock'));
  return TILES[k]||(k==='belt'?TILES.rock:null)||null;
}
// the tile cell under one sphere cell: rows of texels by latitude, and per row a multiple of 8 texels
// around (so the tile repeats whole and the seam never shows), turning with the planet
function tileTex(t,e,phase,r,seed){
  const nv=Math.max(8,Math.round(Math.PI*r/TILE_TEXEL)), nq=Math.max(8,Math.round(TAU*r/TILE_TEXEL));
  const vi=Math.min(nv-1,Math.floor((e.lat/Math.PI+.5)*nv));
  const nu=8*Math.max(1,Math.round(nq*Math.cos(((vi+.5)/nv-.5)*Math.PI)/8));
  const ui=Math.floor(((((e.lon+phase)/TAU)%1)+1)%1*nu);
  return t[(vi+(seed>>3))&7][(ui+seed)&7];
}
function systemLegend(){
  return '<h2>WHAT YOU ARE LOOKING AT</h2><div class="lg">The <b>sun</b> is this machine. Each <b>planet</b> is one of its top-level folders: its body says what the folder holds, its size how much is on disk. Click a planet to see what is inside it (its folders, its problems, the agents there). <b>Craft</b> are agents; cargo craft are your tasks (N).</div>'+
    '<h2>WHAT A PLANET HOLDS</h2><div class="sub">The body is the folder\u2019s purpose: guessed from its files, or set by you on its card (Details \u203a Change). L closes.</div>'+
    kindsLegendHtml()+
    '<div class="lg"><b>'+esc(mateName())+'</b> explains whatever you point at and answers questions (/). <b>N</b> hands an agent a task; <b>K</b> lists tasks; <b>Q</b> is the board of decisions only you can close; <b>U</b> is the meta board, which tells you what to do with every task, thread, window and worktree.</div>'+
    themeLegend();
}
function pixelRing(x,y,rx,ry,color,start=0,end=Math.PI*2,tilt=0,dashed=false){
  if(VEC) VEC_PIX++;
  cx.fillStyle=color;
  const steps=Math.ceil((end-start)*rx/2);
  for(let i=0;i<=steps;i++){
    if(dashed&&i%7>2)continue;
    const a=start+(end-start)*i/steps,xx=Math.cos(a)*rx,yy=Math.sin(a)*ry;
    cx.fillRect(Math.round((x+xx*Math.cos(tilt)-yy*Math.sin(tilt))/2)*2,
      Math.round((y+xx*Math.sin(tilt)+yy*Math.cos(tilt))/2)*2,2,2);
  }
}
// a tilted ring band in front of / behind a body, with bright particles running along it
function ringBand(x,y,rx,ry,tilt,a0,a1,cols,t,seed){
  pixelRing(x,y,rx-5,ry-2,cols[2],a0,a1,tilt); pixelRing(x,y,rx,ry,cols[0],a0,a1,tilt); pixelRing(x,y,rx+4,ry+2,cols[1],a0,a1,tilt);
  if(GB) return;
  const ct=Math.cos(tilt),st=Math.sin(tilt); cx.fillStyle='#ffffff';
  for(let i=0;i<9;i++){
    const a=((REDUCED?0:t/7000)+i/9+(seed%13)/13)%1*TAU; if(!((a>=a0&&a<=a1)||(a+TAU>=a0&&a+TAU<=a1))) continue;
    const rr=rx-4+(i%3)*4, xx=Math.cos(a)*rr, yy=Math.sin(a)*(ry*rr/rx);
    cx.fillRect(Math.round((x+xx*ct-yy*st)/2)*2,Math.round((y+xx*st+yy*ct)/2)*2,2,2);
  }
}
// ---- ROOMS (a planet's folders): the shape says what kind of folder it is ----
const REGEN_DIRS=new Set(['node_modules','.venv','venv','__pycache__','.cache','dist','build','target','.next','.nuxt','.pytest_cache','.mypy_cache','.ruff_cache','.tox','.gradle','coverage','.parcel-cache','.turbo']);
function roomShape(u){
  const n=String(u.name||'').toLowerCase();
  if(REGEN_DIRS.has(n)) return 'cloud';
  if(BLOCK_KIND[u.area]==='archive') return 'belt';
  if(n.startsWith('.')) return 'probe';
  if(!u.files) return 'rock';
  return 'moon';
}
function drawRoom(u,sx,sy,r,t){
  const shape=roomShape(u), seed=seedOf(u.uid), theme=unitTheme(u);
  if(shape==='cloud'){
    const k=Math.max(5,Math.min(14,Math.round(2*Math.log2(2+(u.files||0))))), T=THEMES.cache;
    for(let i=0;i<k;i++){
      const a=(REDUCED?0:t/(2600+(seed%5)*400))+i*2.39996, d=r*.25+hash3(i,7,1,seed)*r*1.05;
      cx.fillStyle=GB?'#306230':[T.base,T.hi,T.land][i%3]; const z=i%4===0?4:2;
      cx.fillRect(Math.round((sx+Math.cos(a)*d)/2)*2,Math.round((sy+Math.sin(a)*d*.75)/2)*2,z,z);
    }
    return;
  }
  if(shape==='probe'){ drawSprite('probe',sx,sy,.4+(seed%7),hullPal(THEMES[theme].glow,'')); return; }
  if(shape==='belt'){ drawRock(sx,sy,Math.max(7,Math.min(20,Math.round(r*1.7))),'belt',seed,14+(seed%19)); return; }
  if(shape==='rock'||r<=5){ drawRock(sx,sy,Math.max(4,r),shape==='rock'?'rock':theme,seed,26+(seed%23)); return; }
  drawBody(sx,sy,r,'moon',theme,seed,36+(seed%41));
}
function selectedUid(u){return !!u&&((near&&near.uid===u.uid)||(openUnit&&openUnit.uid===u.uid));}
function detailAt(u,r=150){return selectedUid(u)||Math.hypot(u.gx*TILE-px,u.gy*TILE-py)<r;}
const CLICK_BOXES=[];   // label plates placed this frame, each with the uid it belongs to: a label is a click target too
// NAME PLATES, the map's signature: a body's name in the display face on an opaque plate, a bar
// in its sector's hue down the left edge, a red pip with its problem count and an amber ! when a
// parked decision names it. Hover (or the ship close by, or its card open) extends the tag with the
// one-line status. Tags are drawn in screen pixels at fixed sizes: crisp pixel type at every zoom, never under 12 px.
const TAG={h:24,bar:4,padL:10,padR:10,font:16,base:17,sub:12,subH:20,pip:18,gap:4};
function plateUi(){ const u=(PACK&&PACK.ui)||{};
  return GB?{bg:'#8bac0f',ink:'#0f380f',sub:'#306230',bad:'#0f380f',edge:'#306230',pip:'#9bbc0f',mark:'#306230'}:
    {bg:u.plate||'#090f22',ink:u.text||'#c8dcf5',sub:u.muted||'#9bacc0',bad:u.danger||'#f85149',edge:u.border||'#344c6b',pip:'#ffffff',mark:'#ffbd55'}; }
// a colour written on a plate, unless it would not read there (a light pack's paper plate): then the ink
function inkOn(col,bg,ink){ const hex=/^#[0-9a-f]{6}$/i; if(!hex.test(String(col))||!hex.test(String(bg))) return col;
  const a=packLum(col), b=packLum(bg); return (Math.max(a,b)+.05)/(Math.min(a,b)+.05)>=3?col:ink; }
function plateBody(uid){ return uid?(deck==='home'?MOONS:remoteMoons).find(m=>m.uid===uid)||null:null; }
function plateHot(uid){ return !!uid&&((hover&&hover.uid===uid)||(near&&near.uid===uid)||(openUnit&&openUnit.uid===uid)); }
// the fight around a planet, as a line on its tag (not a plate of its own among the ships): a battlefield
// always, an undefended planet when you are close or pointing at it
function battleNote(m,hot){
  const line=m?moonHover(m):''; if(!m||!line) return null;
  const amber=GB?T.paper:'#ffbd2e', hard=moonBadge(m).k==='problems';   // an agent can take a problem; a decision or a review is yours
  if(m.allies&&hard) return {t:'BATTLEFIELD · '+m.allies+' agent'+(m.allies>1?'s':'')+' vs '+line,col:amber};
  const close=hot||Math.hypot(m.x-px,m.y-py)<Math.max(m.ringR||0,m.r)+260;
  if(!close) return null;
  if(!hard) return {t:line,col:amber};
  // a phone has no N: it long-presses, and the words go on a second row so the tag stays inside the screen
  return MOB?{t:CM.word('undefended')+' · '+line,t2:'long-press to send an agent',col:null}
    :{t:CM.word('undefended')+' · '+line,col:null};   // the key that sends one is in "?" (P13)
}
// ---- PLATES: one label layout for the whole map ----
// The drawing code only queues a request (queuePlate) and registers each craft's hull (plateHull). Once the
// last craft, foe and the player ship are down, layoutPlates() places the queue against the hulls, the page's
// own boxes (comms, the wingmate, the HUD, the ticker, the minimap) and the viewport edge, and paintPlates() prints
// every plate in one pass on top, so a craft drawn later can never overprint a plate. Plates are laid out in
// world units but sized in screen pixels: the same on screen at every zoom. A plate is tried in 8 directions
// around its craft on a few rings; the slot it had last frame wins unless it is blocked, so plates do not
// jump as the camera moves. A plate with no free place is not drawn (it never lands on another); pointing at
// its craft or planet gives it the first pick. More than four plates at one planet fold into one count.
const PLATE_PRI={tag:0,badge:1,carrier:2,seat:3,thread:4,player:5};
const PLATE={h:20,gap:4,step:22,rings:7,stick:130,fold:4,edge:4};   // stick > the dearest slot: a free remembered slot always wins
const PLATE_DIRS=[[0,-1],[.7071,-.7071],[1,0],[.7071,.7071],[0,1],[-.7071,.7071],[-1,0],[-.7071,-.7071]];   // N NE E SE S SW W NW
const PLATE_FACE={n:0,ne:1,e:2,se:3,s:4,sw:5,w:6,nw:7};
const PLATE_OVERLAYS=['staticbanner','topbar','hud','quick','theme','taskbar','comms','ticker','permits','gohome','mate','mateshow','touchpad','help','legend','machines','legendchip','pressi','crumb','prompter','fcrumb','quests','radio'];
// what a phone or a tablet adds over the map: tab bar or rail, zoom and new-thread buttons, toast, pick bar and the More sheet (a desktop never reads these)
const PLATE_OVERLAYS_TOUCH=['tabs','mfab','mzoom','mclose','pickbar','mtoast','msheet'];
let PLATE_Q=[], PLATE_KEYS=new Map(), PLATE_HULLS=[], PLATE_DISCS=[], PLATE_FINAL=null, PLATE_FOLDS=[];
const PLATE_MEMORY=new Map(), DOCK_ZONE=new Map(), OVERLAY_CACHE={at:-1e9,vw:0,vh:0,rects:[]}, FIT_CACHE=new Map();
// the longest a line may be, so one long name never makes a plate that fits nowhere
function capPx(){ const w=VW-PANEL_W-32; return Number.isFinite(w)?Math.max(120,Math.min(420,w)):420; }
// text cut to maxPx at the plate's 12 px type, with an ellipsis when it was cut
function fitLabel(text,maxPx){
  text=String(text==null?'':text); const key=text+'|'+maxPx+(VEC?'|v':''), hit=FIT_CACHE.get(key); if(hit!==undefined) return hit;
  cx.save(); cx.font=VEC?vecFont(12,0,'data'):cfont(12); let out=text;
  if(cx.measureText(text).width>maxPx){
    let lo=0, hi=text.length;
    while(lo<hi){ const mid=(lo+hi+1)>>1; if(cx.measureText(text.slice(0,mid).trimEnd()+'…').width<=maxPx) lo=mid; else hi=mid-1; }
    out=text.slice(0,lo).trimEnd()+'…'; }
  cx.restore(); if(FIT_CACHE.size>300) FIT_CACHE.clear(); FIT_CACHE.set(key,out); return out;
}
// words of one plate: a piece already said by another (the hub's name inside "hub · you") is not said twice
function joinSeg(row,s){
  const low=String(s.t).toLowerCase();
  if(row.some(o=>String(o.t).toLowerCase().includes(low))) return;
  for(let i=row.length-1;i>=0;i--) if(low.includes(String(row[i].t).toLowerCase())) row.splice(i,1);
  row.push(s); row.sort((a,b)=>(a.ord||0)-(b.ord||0));
}
// a request: {key, kind, pri, x, y, r, face, gap, row, segs:[{t,col,ord}], style, fill, ink, uid, dock, cls, need, hot, tag}.
// (x,y) is what the plate names and r its hull in world units; the plate sits beside it, facing `face` first.
// Requests with one key are one plate: their words join (by ord), the best priority wins.
function queuePlate(q){
  if(!(Number.isFinite(q.x)&&Number.isFinite(q.y))) return;
  const row=q.row||0, cap=capPx(), segs=(q.segs||[]).map(s=>({t:q.kind==='tag'?s.t:fitLabel(s.t,cap),col:s.col,ord:s.ord||0}));
  const old=q.key?PLATE_KEYS.get(q.key):null;
  if(old){ while(old.rows.length<=row) old.rows.push([]);
    segs.forEach(s=>joinSeg(old.rows[row],s));
    old.pri=Math.min(old.pri,q.pri); old.hot=old.hot||!!q.hot; old.need=old.need||!!q.need; old.w=0; return; }
  const p=Object.assign({r:0,gap:PLATE.gap,face:'s',style:'plate',hot:false,w:0,h:0},q); delete p.segs; delete p.row;
  p.rows=[]; while(p.rows.length<=row) p.rows.push([]);
  segs.forEach(s=>joinSeg(p.rows[row],s));
  PLATE_Q.push(p); if(p.key) PLATE_KEYS.set(p.key,p);
}
function plateHull(x,y,r){ if(Number.isFinite(x)&&Number.isFinite(y)) PLATE_HULLS.push({x:x,y:y,r:r}); }
// a plate's size in screen pixels: its words in 12 px mono, 16 px of padding, 20 px a row
function plateMeasure(q){
  if(q.w>0&&q.h>0) return;
  if(!VEC&&q.style==='chamfer'){   // a focus tag: 14 px words on a chamfered plate, 10 px in from its rule, 8 px after
    const w=Math.max(...q.rows.map(row=>LABEL.measure(row.map(g=>String(g.t)).join(' · '),14,600)));
    q.w=Math.ceil(w)+18; q.h=23*q.rows.length; q.bw=q.w; q.bh=q.h; return; }
  if(!VEC){   // the classic look's plates are words in the labels' face: 13 px, stroked, no box; badges are pills
    const fill=q.style==='fill', coloured=q.style==='badge'||fill, rowH=fill?16:PLATE.h, sep=LABEL.measure(' · ',13,600); let w=0;
    q.rows.forEach(row=>{ let rw=0; row.forEach((g,i)=>{ rw+=LABEL.measure(String(g.t),coloured?12:13,600)+(i?sep:0); }); w=Math.max(w,rw); });
    q.w=Math.ceil(w)+(coloured?12:8); q.h=rowH*q.rows.length; q.bw=q.w; q.bh=q.h; return; }
  const fill=q.style==='fill', rowH=fill?16:PLATE.h;
  cx.save(); cx.font=VEC?((q.style==='badge'||fill)?vecFont(12,600):vecFont(12,0,'data')):cfont(12,{bold:q.style==='badge'||fill}); if(VEC) track((q.style==='badge'||fill)?1.2:0);
  const sep=cx.measureText(' · ').width; let w=0;
  q.rows.forEach(row=>{ let rw=0; row.forEach((s,i)=>{ rw+=cx.measureText(String(s.t)).width+(i?sep:0); }); w=Math.max(w,rw); });
  if(VEC) track(0); cx.restore();
  q.w=Math.ceil(w)+(fill?8:16); q.h=rowH*q.rows.length; q.bw=q.w; q.bh=q.h;
}
// the page's own boxes that sit over the map (screen pixels, relative to the map): re-read four times a second
function minimapBox(){ const phone=VW<=700, w=phone?108:158, h=phone?78:112; return {x:VW-w-12,y:phone?72:VH-h-120,w:w,h:h}; }
function minimapShown(){ return !QUIET&&!(CM.chromeReady&&CM.chromeOn&&CM.chromeOn())&&!(VW<=700&&document.getElementById('panel').classList.contains('on')); }
function overlayBoxes(fresh){
  const now=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();
  if(!fresh&&Math.abs(now-OVERLAY_CACHE.at)<250&&OVERLAY_CACHE.vw===VW&&OVERLAY_CACHE.vh===VH) return OVERLAY_CACHE.rects;
  const out=[]; let ox=0, oy=0;
  try{ const sr=STAGE&&STAGE.getBoundingClientRect(); if(sr&&Number.isFinite(sr.left)&&Number.isFinite(sr.top)){ ox=sr.left; oy=sr.top; } }catch(e){}
  (touchUI()?PLATE_OVERLAYS.concat(PLATE_OVERLAYS_TOUCH):PLATE_OVERLAYS).forEach(id=>{
    let r=null;
    try{ const el=document.getElementById(id); r=el&&el.getBoundingClientRect?el.getBoundingClientRect():null;
      const cs=r&&typeof getComputedStyle==='function'?getComputedStyle(el):null;
      if(cs&&(cs.visibility==='hidden'||parseFloat(cs.opacity)<.05)) r=null; }catch(e){}   // faded out or hidden: nothing there
    if(!r||!(r.width>1&&r.height>1)||!Number.isFinite(r.left)||!Number.isFinite(r.top)) return;   // display:none measures 0 x 0
    const b={id:id,x:r.left-ox,y:r.top-oy,w:r.width,h:r.height};
    if(b.x>=VW||b.y>=VH||b.x+b.w<=0||b.y+b.h<=0) return;   // wholly outside the stage (the status bar's assistant line): it covers none of the map
    if(b.w*b.h>.6*VW*VH) return;   // a sheet over the whole map hides it: nothing to lay out around
    out.push(b); });
  if(minimapShown()) out.push(Object.assign({id:'minimap'},minimapBox()));
  if(VEC&&!MOB){ const R=vecRails();   // the vector look's rails (top and bottom bands) and the compass corner: plates and planets keep out
    out.push({id:'vec-rail-top',x:0,y:0,w:VW,h:R.top},{id:'vec-rail-bottom',x:0,y:VH-R.bottom,w:VW,h:R.bottom});
    if(!touchUI()) out.push({id:'vec-compass',x:0,y:VH-150,w:150,h:150}); }
  OVERLAY_CACHE.at=now; OVERLAY_CACHE.vw=VW; OVERLAY_CACHE.vh=VH; OVERLAY_CACHE.rects=out; return out;
}
// everything a plate must stay clear of, in world units: craft hulls, the page's boxes, and the planets' discs
function plateObstacles(){
  const ob=[], k=1/ZOOM;
  PLATE_HULLS.forEach(h=>ob.push({t:'hull',x:h.x,y:h.y,r:h.r}));
  PLATE_DISCS.forEach(d=>ob.push({t:'disc',x:d.x,y:d.y,r:d.r,r0:d.r0}));
  overlayBoxes().forEach(b=>ob.push({t:'box',id:b.id,x:camx+b.x*k,y:camy+b.y*k,w:b.w*k,h:b.h*k}));
  if(VEC) vecChevronList().forEach(c=>{ const bx=c.x-Math.cos(c.angle)*26, by=c.y-Math.sin(c.angle)*26;   // the edge chevrons and their words
    ob.push({t:'box',id:'vec-chevron',x:camx+(Math.min(c.x,bx)-44)*k,y:camy+(Math.min(c.y,by)-26)*k,w:(Math.abs(c.x-bx)+88)*k,h:(Math.abs(c.y-by)+44)*k}); });
  if(VEC) VEC_AUD.labels.forEach(b=>ob.push({t:'box',id:'vec-label',x:camx+b.x*k,y:camy+b.y*k,w:b.w*k,h:b.h*k}));   // the sector names on the ring
  (FC.edgeBoxes||[]).forEach(b=>ob.push({t:'box',id:'edge-arrow',x:camx+b.x*k,y:camy+b.y*k,w:b.w*k,h:b.h*k}));   // the named edge arrows (ui/focus.js)
  return ob;
}
// the part of the map you can see (the open card covers the right of it, on a phone the bottom of it): plates stay inside, 4 px from the edge
function plateSheet(){ return MOB&&Number.isFinite(SHEET_CUR)?SHEET_CUR:0; }
function plateClamp(){
  const k=1/ZOOM, e=PLATE.edge, R=VEC?vecRails():{top:0,bottom:0}, w=VW-PANEL_W-2*e, h=VH-plateSheet()-2*e-R.top-R.bottom;
  if(!(w>0&&h>0)) return {x:-1e9,y:-1e9,w:2e9,h:2e9};   // no stage to measure: no edge
  return {x:camx+e*k,y:camy+(e+R.top)*k,w:w*k,h:h*k};
}
function plateHit(a,b,pad){ return a.x<b.x+b.w+pad&&a.x+a.w+pad>b.x&&a.y<b.y+b.h+pad&&a.y+a.h+pad>b.y; }
function plateHitDisc(a,c,r){ const dx=Math.max(a.x-c.x,0,c.x-(a.x+a.w)), dy=Math.max(a.y-c.y,0,c.y-(a.y+a.h)); return dx*dx+dy*dy<r*r; }
// where a plate sits for a direction and ring: the nearest corner or edge of its base box a gap away from the hull
function plateSlot(q,bw,bh,w,h,di,ring,k){
  const u=PLATE_DIRS[di], d=q.r+(q.gap+ring*PLATE.step)*k, ax=q.x+u[0]*d, ay=q.y+u[1]*d;
  return {x:u[0]>.3?ax:u[0]<-.3?ax-bw:ax-bw/2, y:u[1]>.3?ay:u[1]<-.3?ay-bh:ay-bh/2, w:w, h:h};
}
// The layout. reqs: queued plates (their own size read from their words); obstacles: [{t:'hull',x,y,r} |
// {t:'box',x,y,w,h} | {t:'disc',x,y,r,r0}]; clampRect: the visible map. All in world units. Plates go in priority
// order (a pointed-at plate first); each takes the cheapest free slot: distance and direction cost, a bonus for
// last frame's slot (PLATE_MEMORY). Returns {placed:[{q,x,y,w,h,dir,ring}], dropped:[{key,kind,why}]}.
function layoutPlates(reqs,obstacles,clampRect){
  const k=1/ZOOM, pad=2*k, cr=clampRect, placed=[], out=[], dropped=[];
  const allBoxes=obstacles.filter(o=>o.t==='box'), allHulls=obstacles.filter(o=>o.t==='hull'), allDiscs=obstacles.filter(o=>o.t==='disc');
  const eff=q=>q.kind==='tag'&&q.hot?-1:q.hot?Math.min(q.pri,.5):q.pri;
  const order=reqs.slice().sort((a,b)=>eff(a)-eff(b)||(a.key<b.key?-1:a.key>b.key?1:0));
  const reach=(PLATE.rings*PLATE.step+60)*k, slack=40*k;
  order.forEach(q=>{
    plateMeasure(q);
    const w=q.w*k, h=q.h*k, bw=(q.bw||q.w)*k, bh=(q.bh||q.h)*k, tag=q.kind==='tag'||q.style==='chamfer';   // a focus tag keeps off the bodies like a planet's
    if(q.x<cr.x-reach||q.x>cr.x+cr.w+reach||q.y<cr.y-reach||q.y>cr.y+cr.h+reach) return;   // far off screen: not this frame's business
    const pref=PLATE_FACE[q.face]!=null?PLATE_FACE[q.face]:4, mem=q.key?PLATE_MEMORY.get(q.key):null;
    const slide=q.x>=cr.x-slack&&q.x<=cr.x+cr.w+slack&&q.y>=cr.y-slack&&q.y<=cr.y+cr.h+slack;   // a craft on screen keeps its plate on screen
    // only what is within this plate's reach can block it
    const R=reach+Math.max(w,h)+q.r, nearTo=(o,e)=>Math.abs(o.x-q.x)<R+e&&Math.abs(o.y-q.y)<R+e;
    const boxes=allBoxes.filter(o=>o.x<q.x+R&&o.x+o.w>q.x-R&&o.y<q.y+R&&o.y+o.h>q.y-R);
    const hulls=allHulls.filter(o=>nearTo(o,o.r)), discs=allDiscs.filter(o=>nearTo(o,o.r));
    let best=null;
    const tryslot=(di,ring,hard)=>{
      const c=plateSlot(q,bw,bh,w,h,di,ring,k);
      let cost=ring*16+Math.min(Math.abs(di-pref),8-Math.abs(di-pref))*6;
      if(mem&&mem.dir===di&&mem.ring===ring) cost-=PLATE.stick;
      let sx=0, sy=0;
      if(c.x<cr.x) sx=cr.x-c.x; else if(c.x+c.w>cr.x+cr.w) sx=cr.x+cr.w-c.w-c.x;
      if(c.y<cr.y) sy=cr.y-c.y; else if(c.y+c.h>cr.y+cr.h) sy=cr.y+cr.h-c.h-c.y;
      if(sx||sy){ if(!slide||c.w>cr.w||c.h>cr.h||Math.abs(sx)+Math.abs(sy)>90*k) return; c.x+=sx; c.y+=sy; cost+=(Math.abs(sx)+Math.abs(sy))/k*.4; }
      if(placed.some(p=>plateHit(c,p,pad))) return;               // never on another plate
      for(const b of boxes) if(plateHit(c,b,0)) return;           // never under the page's own boxes
      for(const o of hulls) if(plateHitDisc(c,o,o.r)){ if(hard) return; cost+=500; }   // its own craft's too: a slid slot may reach it
      for(const d of discs){ if(Math.abs(d.x-q.x)<1&&Math.abs(d.y-q.y)<1) continue;   // a tag's own planet is what it names
        if(tag?plateHitDisc(c,d,d.r):plateHitDisc(c,d,d.r0||d.r)){ if(tag){ if(hard||q.style==='chamfer') return; cost+=300; } else cost+=40; } }   // a focus tag never covers a body: it folds instead
      if(!best||cost<best.cost) best={c:c,di:di,ring:ring,cost:cost};
    };
    for(let pass=0;pass<(tag?2:1)&&!best;pass++){   // a tag, if nothing is free, may sit over a craft (never over a plate or a box)
      if(mem&&mem.ring<PLATE.rings) tryslot(mem.dir,mem.ring,pass===0);
      for(let ring=0;ring<PLATE.rings;ring++){
        if(best&&best.cost<=ring*16) break;
        for(let di=0;di<8;di++) tryslot(di,ring,pass===0); }
    }
    if(!best){ dropped.push({key:q.key,kind:q.kind,why:'no free place'}); return; }
    placed.push(best.c); out.push({q:q,x:best.c.x,y:best.c.y,w:w,h:h,dir:best.di,ring:best.ring});
    if(q.key) PLATE_MEMORY.set(q.key,{dir:best.di,ring:best.ring,t:FRAME_T});
  });
  if(PLATE_MEMORY.size>240) PLATE_MEMORY.forEach((v,key)=>{ if(Math.abs(FRAME_T-v.t)>4000) PLATE_MEMORY.delete(key); });
  return {placed:out,dropped:dropped};
}
// a planet's dock: the ships and threads working there. More than four plates fold into one count; pointing at
// the planet, a craft of it or the fold (or flying close, or opening its card) unfolds them, and they stay out
// while the pointer is over the dock's box, so they do not flicker when the fold goes away under it.
function dockCounts(dock){
  let ships=0, threads=0, need=0;
  (deck==='home'?units:remoteUnits).forEach(u=>{ if(u.docked_at!==dock) return;
    if(u.kind==='task'){ threads++; if(u.status==='review') need++; }
    else if(isShip(u)){ ships++; const st=String(u.status||''); if(st==='WAITING'||st==='blocked'||st==='DRAFT') need++; } });
  return {ships:ships,threads:threads,need:need};
}
function dockWords(c){
  const p=[]; if(c.ships) p.push(c.ships+(c.ships===1?' ship':' ships')); if(c.threads) p.push(c.threads+(c.threads===1?' task':' tasks'));
  if(c.need) p.push(c.need+(c.need===1?' needs you':' need you')); return p.join(' · ');
}
function dockOpen(dock){
  if(plateHot(dock)||(hover&&hover.docked_at===dock)||(near&&near.docked_at===dock)||(openUnit&&openUnit.docked_at===dock)) return true;
  const z=DOCK_ZONE.get(dock); if(!z||!z.open||!mouseW||dragging) return false;
  const p=16/ZOOM; return mouseW.x>=z.x0-p&&mouseW.x<=z.x1+p&&mouseW.y>=z.y0-p&&mouseW.y<=z.y1+p;
}
function foldDock(reqs){
  const by=new Map(), folds=[]; let out=reqs;
  reqs.forEach(q=>{ if(q.dock&&q.cls){ if(!by.has(q.dock)) by.set(q.dock,[]); by.get(q.dock).push(q); } });
  by.forEach((list,dock)=>{
    if(list.length<=PLATE.fold||dockOpen(dock)) return;
    const gone=new Set(list); out=out.filter(q=>!gone.has(q));
    let cx0=0, cy0=0; list.forEach(q=>{ cx0+=q.x; cy0+=q.y; }); cx0/=list.length; cy0/=list.length;
    const reach=list.reduce((a,q)=>Math.max(a,Math.hypot(q.x-cx0,q.y-cy0)+q.r),0), m=plateBody(dock);
    let face='s'; if(m){ const ux=cx0-m.x, uy=cy0-m.y; let bd=-2, bi=4;   // away from the planet
      PLATE_DIRS.forEach((d,i)=>{ const dot=(d[0]*ux+d[1]*uy)/(Math.hypot(ux,uy)||1); if(dot>bd){ bd=dot; bi=i; } });
      face=Object.keys(PLATE_FACE).find(f=>PLATE_FACE[f]===bi); }
    const c=dockCounts(dock), text=dockWords(c);
    out.push({key:'fold:'+dock,kind:'fold',pri:PLATE_PRI.badge,style:'plate',x:cx0,y:cy0,r:reach,gap:PLATE.gap,face:face,uid:dock,dock:null,hot:false,w:0,h:0,
      need:c.need>0,rows:[[{t:text,col:c.need?(GB?T.paper:'#ffbd2e'):(GB?T.paper:'#b5cbe3'),ord:0}]]});
    folds.push({dock:dock,text:text,plates:list.length,ships:c.ships,threads:c.threads,need:c.need}); });
  PLATE_FOLDS=folds; return out;
}
// each dock's box (its plates, craft and fold) for dockOpen's hysteresis
function dockZones(placed,reqs){
  const zones=new Map(), grow=(dock,x0,y0,x1,y1)=>{ const z=zones.get(dock)||{x0:1e18,y0:1e18,x1:-1e18,y1:-1e18};
    z.x0=Math.min(z.x0,x0); z.y0=Math.min(z.y0,y0); z.x1=Math.max(z.x1,x1); z.y1=Math.max(z.y1,y1); zones.set(dock,z); };
  reqs.forEach(q=>{ if(q.dock&&q.cls) grow(q.dock,q.x-q.r,q.y-q.r,q.x+q.r,q.y+q.r); });
  placed.forEach(p=>{ const dk=p.q.dock||(p.q.kind==='fold'?p.q.uid:null); if(dk) grow(dk,p.x,p.y,p.x+p.w,p.y+p.h); });
  const folded=new Set(PLATE_FOLDS.map(f=>f.dock));
  zones.forEach((z,dock)=>{ const old=DOCK_ZONE.get(dock), fold=placed.find(p=>p.q.kind==='fold'&&p.q.uid===dock);
    const keep=fold?{x:fold.x,y:fold.y,w:fold.w,h:fold.h}:(old&&old.fold);
    if(!fold&&keep) grow(dock,keep.x,keep.y,keep.x+keep.w,keep.y+keep.h);
    DOCK_ZONE.set(dock,{x0:z.x0,y0:z.y0,x1:z.x1,y1:z.y1,open:!folded.has(dock),fold:keep||null}); });
  DOCK_ZONE.forEach((z,dock)=>{ if(!zones.has(dock)) DOCK_ZONE.delete(dock); });
}
// the frame's plate pass: fold, lay out, remember the result for the audit, paint
function runPlates(){
  const lab=labelsRun();   // the names first, in their slots (ui/labels.js); the craft plates keep off them
  const reqs=foldDock(PLATE_Q.slice()), clamp=plateClamp(), wings=wingDiscs(), obs0=plateObstacles(), obs=obs0.concat(lab.boxes), res=layoutPlates(reqs,obs,clamp);
  wingPlace(wings,res.placed.concat(lab.asPlaced));
  PLATE_FINAL={placed:res.placed,labels:lab.all,dropped:res.dropped,clamp:clamp,boxes:obs0.filter(o=>o.t==='box'),folds:PLATE_FOLDS,
    camx:camx,camy:camy,zoom:ZOOM,vw:VW,vh:VH,panel:PANEL_W,sheet:plateSheet(),t:FRAME_T,wings:wings};
  dockZones(res.placed,PLATE_Q);
  paintWings(wings);
  labelsPaint(lab);
  paintPlates(res.placed);
}
// a plate's slot in the words of the labels: below its craft (own, then +1 a row further), above (-1), beside
function plateSlotName(dir,ring){ return dir===4?(ring?'+1':'own'):dir===0?'-1':(dir>=1&&dir<=3)?'right':'left'; }
// the final rects, in screen pixels (and world units): what a test, or a curious console, reads back
window.__plateAudit=function(){
  const F=PLATE_FINAL; if(!F) return null;
  const z=F.zoom, scr=r=>({x:(r.x-F.camx)*z,y:(r.y-F.camy)*z,w:r.w*z,h:r.h*z});
  return {frame:F.t,zoom:z,vw:F.vw,vh:F.vh,panel:F.panel,view:{x:0,y:0,w:F.vw-F.panel,h:F.vh-(F.sheet||0)},
    plates:labelsAudit(F).concat(F.placed.map(p=>{ const s=scr(p), q=p.q, a={x:(q.x-F.camx)*z,y:(q.y-F.camy)*z}, r=(q.r||0)*z;
      const dx=Math.max(s.x-a.x,0,a.x-s.x-s.w), dy=Math.max(s.y-a.y,0,a.y-s.y-s.h);
      return {key:q.key,kind:q.kind,pri:q.pri,uid:q.uid||null,dock:q.dock||null,ring:p.ring,dir:p.dir,slot:plateSlotName(p.dir,p.ring),
        gap:Math.round((Math.hypot(dx,dy)-r)*100)/100,stroke:3,px:q.kind==='tag'?15:(q.style==='chamfer'?14:13),
        text:q.kind==='tag'?q.tag.label:q.rows.map(r=>r.map(g=>g.t).join(' · ')).join(' / '),
        x:s.x,y:s.y,w:s.w,h:s.h,wx:p.x,wy:p.y,ww:p.w,wh:p.h}; })),
    overlays:F.boxes.map(b=>Object.assign({id:b.id},scr(b))), dropped:F.dropped.slice(), folds:F.folds.slice(),
    wings:[].concat(...(F.wings||[]).map(q=>q.ms.map(w=>({uid:w.uid,text:w.t,dock:w.dock,row:!!q.row,hidden:!!w.hidden,moved:!!w.moved,
      x:w.px,y:w.py,w:w.w,h:w.h,craft:{x:w.sx,y:w.sy}}))))};
};
// ---- painting: every plate in one pass, in screen pixels, on top of the world ----
function paintTag(q,x,y){
  const P=plateUi(), t=q.tag, R=(a,b,c,d)=>cx.fillRect(x+a,y+b,c,d), ext=t.rows.length*TAG.subH, ew=Math.max(t.w,t.dw);
  if(!GB){ cx.fillStyle='rgba(0,0,0,.45)'; R(2,2,t.w,TAG.h); if(ext) R(2,TAG.h+2,ew,ext); }   // hard pixel shadow
  cx.fillStyle=P.bg; R(0,0,t.w,TAG.h); if(ext) R(0,TAG.h,ew,ext);
  cx.fillStyle=t.hue; R(0,0,TAG.bar,t.high);
  cx.textAlign='left'; cx.font=cfont(TAG.font,{display:true}); cx.fillStyle=P.ink; cx.fillText(t.label,x+TAG.bar+TAG.padL,y+TAG.base);
  let px0=TAG.bar+TAG.padL+t.tw+TAG.padR;
  const pip=(fill,ink,text,width)=>{ cx.fillStyle=fill; R(px0,(TAG.h-TAG.pip)/2,width,TAG.pip);
    cx.fillStyle=ink; cx.textAlign='center'; cx.fillText(text,x+px0+width/2,y+TAG.base); px0+=width+TAG.gap; };
  if(t.probs){ const pf=t.pcol==='need'?P.mark:P.bad; pip(pf,inkOn(P.pip,pf,P.bg),String(t.probs),t.pw); }   // red = problems, amber = what waits on you
  if(t.mark) pip(P.mark,GB?P.pip:'#04060c','!',TAG.pip);
  cx.textAlign='left'; cx.font=cfont(TAG.sub);
  t.rows.forEach((r,i)=>{ cx.fillStyle=r.col; cx.fillText(r.t,x+TAG.bar+TAG.padL,y+TAG.h+14+i*TAG.subH); });
}
function paintPlate(q,x,y){
  if(!VEC){   // words stroked then filled, no box; a badge is a pill in its colour
    const s=q.style, fill=s==='fill', coloured=s==='badge'||fill, rowH=fill?16:PLATE.h, px=coloured?12:13, sep=LABEL.measure(' · ',px,600);
    if(coloured){ cx.save(); cx.fillStyle=q.fill||'#FFB547'; cx.beginPath(); if(cx.roundRect) cx.roundRect(x,y+1,q.w,q.h-2,(q.h-2)/2); else cx.rect(x,y+1,q.w,q.h-2); cx.fill(); cx.restore(); }
    q.rows.forEach((row,ri)=>{ let tw=0; row.forEach((g,i)=>{ tw+=LABEL.measure(String(g.t),px,600)+(i?sep:0); });
      let tx=x+(q.w-tw)/2; const ty=y+ri*rowH+Math.round((rowH-px)/2)-1;
      row.forEach((g,i)=>{ if(i){ LABEL.text(cx,' · ',tx,ty,{px:px,col:'#9AA8BC',stroke:!coloured}); tx+=sep; }
        LABEL.text(cx,String(g.t),tx,ty,{px:px,col:coloured?(q.ink||'#05070D'):(GB?T.paper:(g.col||LABEL.INK)),stroke:!coloured,halo:GB?'#9bbc0f':LABEL.HALO}); tx+=LABEL.measure(String(g.t),px,600); }); });
    return; }
  const P=plateUi(), s=q.style, fill=s==='fill', coloured=s==='badge'||fill, rowH=fill?16:PLATE.h, bg=coloured?q.fill:P.bg;
  cx.font=cfont(12,{bold:coloured}); cx.textAlign='left'; cx.fillStyle=bg; cx.fillRect(x,y,q.w,q.h);
  const sepW=cx.measureText(' · ').width;
  q.rows.forEach((row,ri)=>{
    let tw=0; row.forEach((g,i)=>{ tw+=cx.measureText(String(g.t)).width+(i?sepW:0); });
    let tx=x+(q.w-tw)/2; const ty=y+ri*rowH+(fill?12:14);
    row.forEach((g,i)=>{ if(i){ cx.fillStyle=P.sub; cx.fillText(' · ',tx,ty); tx+=sepW; }
      cx.fillStyle=coloured?(q.ink||'#090e21'):inkOn(g.col,bg,P.ink); cx.fillText(String(g.t),tx,ty); tx+=cx.measureText(String(g.t)).width; });
  });
}
function paintPlates(list){
  if(VEC){ vecPaintPlates(list); list.forEach(p=>{ if(p.q.uid) CLICK_BOXES.push({x:p.x,y:p.y,w:p.w,h:p.h,uid:p.q.uid}); }); return; }
  const P=plateUi(), at=(wx,wy)=>({x:(wx-camx)*ZOOM,y:(wy-camy)*ZOOM});
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.textBaseline='alphabetic';
  list.forEach(p=>{ if(p.ring<2||p.q.style==='chamfer') return;   // a plate pushed away from its craft keeps a line to it (a focus tag draws its own elbow)
    const q=p.q, a=at(q.x,q.y), s=at(p.x,p.y), w=p.w*ZOOM, h=p.h*ZOOM;
    const nx=Math.max(s.x,Math.min(a.x,s.x+w)), ny=Math.max(s.y,Math.min(a.y,s.y+h)), d=Math.hypot(nx-a.x,ny-a.y)||1, hr=Math.min(q.r*ZOOM,d);
    cx.save(); cx.globalAlpha=.7; cx.strokeStyle=P.sub; cx.lineWidth=1; cx.beginPath(); cx.moveTo(Math.round(a.x+(nx-a.x)/d*hr)+.5,Math.round(a.y+(ny-a.y)/d*hr)+.5);
    cx.lineTo(Math.round(nx)+.5,Math.round(ny)+.5); cx.stroke(); cx.restore(); });
  list.forEach(p=>{ const q=p.q, s=at(p.x,p.y), x=Math.round(s.x), y=Math.round(s.y);
    if(q.style==='chamfer') fcTagPaint(cx,q,x,y,Math.round(p.w*ZOOM),Math.round(p.h*ZOOM),at(q.x,q.y));
    else if(q.kind==='tag') paintTag(q,x,y); else paintPlate(q,x,y); });
  cx.restore();
  list.forEach(p=>{ if(p.q.uid) CLICK_BOXES.push({x:p.x,y:p.y,w:p.w,h:p.h,uid:p.q.uid}); });
}
// ---- the plates in the vector look. A planet's tag is a cluster tag: the name in tracked condensed capitals, CLUSTER muted
// beside it, a red triangle and the problem count, no box (a soft dark halo), an angled leader from the body to a rule along
// the label. Craft and problem plates are mono call signs with the same halo. Same layout, same click boxes. ----
const VT={pad:3,gap:7,track:2.2};
function vecTagPlate(o){
  const m=plateBody(o.uid), detail=o.detail||''; let label=String(o.name), mark=false;
  if(/ !$/.test(label)){ label=label.slice(0,-2); mark=true; }
  const bdg=m?moonBadge(m):{n:0,col:''}, probs=bdg.n, hot=plateHot(o.uid), rows=[];
  if(o.always) rows.push({t:o.always,col:VC.muted,keep:true});
  if(hot&&detail) rows.push({t:detail,col:o.warning?VC.red:o.tone?VC.muted:VC.body});
  const note=battleNote(m,hot);
  if(note){ const nc=note.col?VC.amber:VC.red; rows.push({t:note.t,col:nc,keep:!!m.allies}); if(note.t2) rows.push({t:note.t2,col:nc,keep:!!m.allies}); }
  if(MOB){ const room=Math.max(120,VW-PANEL_W-2*PLATE.edge-2*VT.pad); rows.forEach(r=>{ r.t=fitLabel(r.t,room); }); }
  const suffix=o.suffix==null?(MOB?'':'CLUSTER'):String(o.suffix);   // a phone's map is small: the name and the count only
  cx.save(); cx.font=vecFont(15,600); track(VT.track); const tw=Math.ceil(cx.measureText(label).width);
  cx.font=vecFont(12,500); const sw=suffix?Math.ceil(cx.measureText(suffix).width):0; track(0);
  cx.font=vecFont(13,0,'data'); const pw=probs?14+Math.ceil(cx.measureText(String(probs)).width):0;
  const w=VT.pad+tw+(sw?VT.gap+sw:0)+(pw?VT.gap+pw:0)+(mark?VT.gap+12:0)+VT.pad;
  cx.font=vecFont(12,0,'data'); rows.forEach(r=>{ r.w=2*VT.pad+Math.ceil(cx.measureText(r.t).width); });
  cx.restore();
  const dw=rows.reduce((a,r)=>Math.max(a,r.w),0), kept=rows.filter(r=>r.keep), lw=Math.max(w,...kept.map(r=>r.w));
  const hue=o.warning?VC.red:mark?VC.amber:(o.vhue||VC.line);
  queuePlate({key:'tag:'+(o.uid||label),kind:'tag',pri:PLATE_PRI.tag,style:'tag',x:o.x,y:o.y,r:o.r,gap:(o.gap||12)+6,face:o.face,uid:o.uid||null,hot:hot,
    w:Math.max(w,dw,lw),h:TAG.h+rows.length*TAG.subH,bw:lw,bh:TAG.h+kept.length*TAG.subH,
    tag:{label:label,suffix:suffix,mark:mark,hue:hue,probs:probs,pcol:bdg.col,tw:tw,sw:sw,pw:pw,w:w,dw:dw,rows:rows,r0:o.r0!=null?o.r0:o.r,high:TAG.h+rows.length*TAG.subH},rows:[[]]});
}
// a small hazard triangle in screen pixels (the tag's problem count)
function vecTriGlyph(x,y,s,col){ cx.save(); cx.beginPath(); cx.moveTo(x,y-s); cx.lineTo(x+s*.95,y+s*.65); cx.lineTo(x-s*.95,y+s*.65); cx.closePath();
  cx.fillStyle=col||VC.red; cx.fill(); cx.fillStyle='#120202'; cx.fillRect(Math.round(x)-1,Math.round(y-s*.35),2,Math.max(2,Math.round(s*.55))); cx.restore(); }
function vecPaintTag(q,x,y){
  const t=q.tag, base=y+TAG.base; let px0=x+VT.pad;
  cx.textAlign='left'; cx.font=vecFont(15,600); track(VT.track); vecHalo(t.label,px0,base,q.hot?VC.mint:VC.text,3.5); px0+=t.tw;
  if(t.sw){ px0+=VT.gap; cx.font=vecFont(12,500); vecHalo(t.suffix,px0,base,VC.muted,3); px0+=t.sw; }
  track(0);
  if(t.pw){ const pc=t.pcol==='need'?VC.amber:VC.red; px0+=VT.gap; vecTriGlyph(px0+5,base-5,5.5,pc); cx.font=vecFont(13,0,'data'); vecHalo(String(t.probs),px0+13,base,pc,3); px0+=t.pw; }
  if(t.mark){ px0+=VT.gap; cx.strokeStyle=VC.amber; cx.lineWidth=1; cx.strokeRect(px0+.5,base-11.5,11,13); cx.font=vecFont(12,600); cx.fillStyle=VC.amber; cx.textAlign='center'; cx.fillText('!',px0+6,base); cx.textAlign='left'; }
  cx.font=vecFont(12,0,'data'); t.rows.forEach((r,i)=>vecHalo(r.t,x+VT.pad,y+TAG.h+14+i*TAG.subH,r.col,3));
}
function vecPaintPlate(q,x,y){
  const s=q.style, fill=s==='fill', coloured=s==='badge'||fill, rowH=fill?16:PLATE.h;
  cx.font=coloured?vecFont(12,600):vecFont(12,0,'data'); track(coloured?1.2:0); cx.textAlign='left';
  if(coloured){ cx.fillStyle=vecTone(q.fill); cx.fillRect(x,y+2,q.w,q.h-4); }
  const sepW=cx.measureText(' · ').width;
  q.rows.forEach((row,ri)=>{ let tw=0; row.forEach((g,i)=>{ tw+=cx.measureText(String(g.t)).width+(i?sepW:0); });
    let tx=x+(q.w-tw)/2; const ty=y+ri*rowH+(fill?12:14);
    row.forEach((g,i)=>{ const txt=String(g.t);
      if(i){ if(coloured){ cx.fillStyle='#050807'; cx.fillText(' · ',tx,ty); } else vecHalo(' · ',tx,ty,VC.muted,3); tx+=sepW; }
      if(coloured){ cx.fillStyle='#050807'; cx.fillText(txt,tx,ty); } else vecHalo(txt,tx,ty,vecTone(g.col),3);
      tx+=cx.measureText(txt).width; }); });
  track(0);
}
function vecPaintPlates(list){
  const at=(wx,wy)=>({x:(wx-camx)*ZOOM,y:(wy-camy)*ZOOM}), px5=v=>Math.round(v)+.5;
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.textBaseline='alphabetic'; cx.lineWidth=1;
  list.forEach(p=>{ const q=p.q, a=at(q.x,q.y), s=at(p.x,p.y);
    if(q.kind==='tag'){   // the leader: from the body's edge, angled, to a rule along the label (over it when the tag sits below)
      const t=q.tag, below=s.y>a.y, ry=below?s.y:s.y+TAG.h-1, left=a.x<=s.x+t.w/2, ax=left?s.x:s.x+t.w;
      const dx=ax-a.x, dy=ry-a.y, d=Math.hypot(dx,dy)||1, r0=(t.r0||0)*ZOOM+3;
      cx.save(); cx.strokeStyle=t.hue; cx.globalAlpha=.8; cx.beginPath();
      if(d>r0+2){ cx.moveTo(px5(a.x+dx/d*r0),px5(a.y+dy/d*r0)); cx.lineTo(px5(ax),px5(ry)); } else cx.moveTo(px5(ax),px5(ry));
      cx.lineTo(px5(left?s.x+t.w:s.x),px5(ry)); cx.stroke(); cx.restore(); return; }
    if(!(q.lead||p.ring>=2)) return;   // a plate pushed away from its craft, or one that names a problem, keeps a line to it
    const w=p.w*ZOOM, h=p.h*ZOOM, nx=Math.max(s.x,Math.min(a.x,s.x+w)), ny=Math.max(s.y,Math.min(a.y,s.y+h)), d=Math.hypot(nx-a.x,ny-a.y)||1, hr=Math.min(q.r*ZOOM,d);
    cx.save(); cx.globalAlpha=.8; cx.strokeStyle=q.lead||VC.dim; cx.beginPath(); cx.moveTo(px5(a.x+(nx-a.x)/d*hr),px5(a.y+(ny-a.y)/d*hr)); cx.lineTo(px5(nx),px5(ny)); cx.stroke(); cx.restore(); });
  list.forEach(p=>{ const q=p.q, s=at(p.x,p.y), x=Math.round(s.x), y=Math.round(s.y);
    if(q.kind==='tag') vecPaintTag(q,x,y); else vecPaintPlate(q,x,y); });
  cx.restore();
}
// a planet's tag: queued like any plate. {name,x,y,r,face,gap,color,detail,warning,uid,tone}: (x,y) the body's centre,
// r its outer edge in world units, gap the screen pixels between that edge and the tag
function tagPlate(o){
  if(VEC){ vecTagPlate(o); return; }
  const P=plateUi(), m=plateBody(o.uid), detail=o.detail||'';
  let label=String(o.name), mark=false;
  if(/ !$/.test(label)){ label=label.slice(0,-2); mark=true; }    // the parked-decision marker is a pip now
  const bdg=m?moonBadge(m):{n:0,col:''}, hue=m&&!GB?sec(m.kind).accent:o.color, probs=bdg.n, hot=plateHot(o.uid);
  const rows=[];                                                  // the tag's extension: status, then the fight
  if(hot&&detail) rows.push({t:detail,col:o.warning?P.bad:inkOn(o.tone||P.sub,P.bg,P.sub)});
  const note=battleNote(m,hot);
  if(note){ const nc=note.col?inkOn(note.col,P.bg,P.ink):P.bad; rows.push({t:note.t,col:nc,keep:!!m.allies}); if(note.t2) rows.push({t:note.t2,col:nc,keep:!!m.allies}); }
  if(MOB){ const room=Math.max(120,VW-PANEL_W-2*PLATE.edge-TAG.bar-TAG.padL-TAG.padR); rows.forEach(r=>{ r.t=fitLabel(r.t,room); }); }   // a phone's tag never runs past the screen
  cx.save(); cx.textBaseline='alphabetic';
  cx.font=cfont(TAG.font,{display:true}); const tw=Math.ceil(cx.measureText(label).width);
  const pw=probs?Math.max(TAG.pip,Math.ceil(cx.measureText(String(probs)).width)+8):0;
  const w=TAG.bar+TAG.padL+tw+TAG.padR+(pw?pw+TAG.gap:0)+(mark?TAG.pip+TAG.gap:0);
  cx.font=cfont(TAG.sub); rows.forEach(r=>{ r.w=TAG.bar+TAG.padL+Math.ceil(cx.measureText(r.t).width)+TAG.padR; });
  cx.restore();
  const dw=rows.reduce((a,r)=>Math.max(a,r.w),0), kept=rows.filter(r=>r.keep), lw=Math.max(w,...kept.map(r=>r.w));
  // placed by what always shows (the name, a battlefield line): hover never moves a tag, it only extends it
  queuePlate({key:'tag:'+(o.uid||label),kind:'tag',pri:PLATE_PRI.tag,style:'tag',x:o.x,y:o.y,r:o.r,gap:o.gap,face:o.face,uid:o.uid||null,hot:hot,
    w:Math.max(w,dw,lw),h:TAG.h+rows.length*TAG.subH,bw:lw,bh:TAG.h+kept.length*TAG.subH,
    tag:{label:label,mark:mark,hue:hue,probs:probs,pcol:bdg.col,tw:tw,pw:pw,w:w,dw:dw,rows:rows,high:TAG.h+rows.length*TAG.subH},rows:[[]]});
}
const BELT_PERIOD=300;          // seconds per turn of the archive belt
function drawMoon(m,t){
  const R=fcDispR(m), f=CM.focusUid(), mine=f===m.uid, extent=mine?R+900/ZOOM:R+180;
  if(m.x+extent<camx||m.x-extent>camx+VW/ZOOM||m.y+extent<camy||m.y-extent>camy+VH/ZOOM)return;
  const still=sitsStill(m.kind),cold=m.life==='COLD'&&!still,empty=m.life==='EMPTY'||m.life==='ALIAS',seed=seedOf(m.label);
  const isHome=m.home!==undefined?m.home:m.label===String((W.wingmate&&W.wingmate.home_planet)||'');
  if(VEC){ vecMoonBody(m,t,seed,cold,empty,isHome); vecPlanetTag(m,t,cold); return; }
  // the body the planet kinds module draws, at least 18 px across (28 on a phone); in focus its well, trend and home rings stay off (one ring, P35);
  // every other planet drops to 35% while one is open
  const body=(R!==m.r||mine)?Object.assign({},m,{r:R},mine?{wellR:0,trend:null,home:false,noAura:true}:{}):m;
  if(mine) fcRim(m,R,t);   // the open planet: a soft rim in its status colour instead of the overview's aura
  cx.save(); if(f&&!mine) cx.globalAlpha*=.35;
  drawMoonBody(body,t);
  cx.restore();
  fcPlanet(m,R,t);
}
// the vector look keeps its cluster tag (name, CLUSTER, the problem count), its rings only in focus
function vecPlanetTag(m,t,cold){
  const S=sec(m.kind), marker=QUEST_HITS[m.label.toUpperCase()]?' !':'', worse=!!(m.trend&&m.trend.worse), dw=decayWord(m.age,m.kind), mw=massWord(m);
  const details=fmtN(m.files)+' files'+(mw?' · '+fmtBytes(m.bytes):'')+(dw?' · '+dw:'');
  if(CM.focusUid()&&CM.focusUid()!==m.uid){ FC.planets.push({uid:m.uid,x:(m.x-camx)*ZOOM,y:(m.y-camy)*ZOOM,r:fcDispR(m)*ZOOM,other:true}); return; }
  tagPlate({name:CM.labelText(m.uid).toUpperCase()+marker,x:m.x,y:m.y,r:Math.max(m.r,CM.inFocus(m)?Math.max(m.ringR,m.outerR):0),r0:m.r,gap:12,face:'s',
    color:marker?'#ffbd55':worse?'#ff2b6a':cold?'#ff4d5e':S.accent,detail:details,warning:cold||worse,uid:m.uid,tone:null});
  FC.planets.push({uid:m.uid,x:(m.x-camx)*ZOOM,y:(m.y-camy)*ZOOM,r:fcDispR(m)*ZOOM,other:false,mine:CM.focusUid()===m.uid});
}
// a planet's body in the pixel look (the vector look's is vecMoonBody): the body its purpose gives it, lit from the sun, and the
// overlays that say its condition (ui/kinds.js kindsDrawMoon). drawMoon draws the room rings around it and the tag. The well,
// the week's trend and the home ring are detail: drawn only while the planet is the focus.
function drawMoonBody(m,t){
  const seed=seedOf(m.label), pulse=REDUCED?0:Math.sin(t/900+seed%7), focus=CM.focusUid()===m.uid;
  const isHome=m.home!==undefined?m.home:m.label===String((W.wingmate&&W.wingmate.home_planet)||'');
  cx.save();
  if(focus&&m.wellR) drawWell(m,t);
  kindsDrawMoon(m,t);
  cx.restore();
  if(!focus) return;
  if(m.trend&&!GB){ if(m.trend.worse){ cx.save(); cx.globalAlpha=.5+.4*Math.abs(pulse); pixelRing(m.x,m.y,m.r+12,m.r+12,'#ff2b6a',REDUCED?0:(t/1400)%TAU,(REDUCED?0:(t/1400)%TAU)+TAU,0,true); cx.restore(); }
    else if(m.trend.better){ cx.save(); cx.globalAlpha=.55; pixelRing(m.x,m.y,m.r+12,m.r+12,'#3dff9e',0,TAU,0,true); cx.restore(); } }
  if(isHome&&!GB){ cx.save(); cx.globalAlpha=.75; pixelRing(m.x,m.y,m.r+18,m.r+18,'#ffd23f',0,TAU,0,true); cx.restore(); }
}
// per-frame sparkle on top of the cached surface: ice glints, toxic bubbles, blight lightning
function surfaceFx(m,st,seed,t){
  if(GB||REDUCED) return;
  const r=m.r, snap=v=>Math.round(v/2)*2;
  if(st==='ice'){
    for(let i=0;i<3;i++){ const cyc=t/1500+i*.37+(seed%9)/9, k=Math.floor(cyc); if(cyc-k>.28) continue;
      const a=hash3(i,k,1,seed)*TAU, d=Math.sqrt(hash3(i,k,2,seed))*r*.55;
      const x=snap(m.x-r*.2+Math.cos(a)*d), y=snap(m.y-r*.2+Math.sin(a)*d);
      cx.fillStyle='#ffffff'; cx.fillRect(x-2,y,6,2); cx.fillRect(x,y-2,2,6); }
  }else if(st==='toxic'){
    for(let i=0;i<5;i++){ const cyc=t/560+i*.61, k=Math.floor(cyc), ph=cyc-k;
      const a=hash3(i,k,3,seed)*TAU, d=Math.sqrt(hash3(i,k,4,seed))*r*.78;
      const z=ph<.55?2:4; cx.fillStyle=ph<.55?STYLE.toxic.b:'#f6ffb0'; cx.fillRect(snap(m.x+Math.cos(a)*d)-z/2+1,snap(m.y+Math.sin(a)*d)-z/2+1,z,z); }
  }else if(st==='blight'){
    const k=Math.floor(t/1700); if(t/1700-k>.09) return;
    let a=hash3(k,1,1,seed)*TAU, x=m.x+Math.cos(a)*r*.7, y=m.y+Math.sin(a)*r*.7; cx.fillStyle=STYLE.blight.c;
    for(let i=0;i<10;i++){ const b=a+Math.PI+(hash3(k,i,2,seed)-.5)*1.7; x+=Math.cos(b)*r*.15; y+=Math.sin(b)*r*.15;
      if(Math.hypot(x-m.x,y-m.y)>r-2) break; cx.fillRect(snap(x),snap(y),2,2); }
  }
}
// THE ARCHIVE is an asteroid belt: its folders are the big rocks (drawn as rooms, sized by files),
// the grains between them are texture, and a tumbling rubble cluster sits where a planet would.
function drawBelt(m,t,seed){
  const P=STYLE.belt; glowAt(m.x,m.y,m.r,P.glow,.14);
  if(m.ringR){
    const n=Math.min(420,120+Math.round(m.ringR)), spin=0;
    for(let i=0;i<n;i++){ const a=hash3(i,1,2,seed)*TAU+spin, q=hash3(i,3,4,seed), rr=m.ringR*(.5+(q+hash3(i,6,2,seed))*.5);
      cx.fillStyle=GB?'#306230':(i%6===0?P.c:(i%2?mixPixel(P.a,'#05030f',.3):P.b)); const z=i%9===0?4:2;
      cx.fillRect(Math.round((m.x+Math.cos(a)*rr)/2)*2,Math.round((m.y+Math.sin(a)*rr)/2)*2,z,z); }
    // small loose rocks between the folders: texture only, not clickable, never counted
    for(let i=0;i<34;i++){ const a=hash3(i,11,2,seed)*TAU+spin, rr=m.ringR*(.55+hash3(i,12,4,seed)*.9);
      drawRock(m.x+Math.cos(a)*rr,m.y+Math.sin(a)*rr,3+Math.floor(hash3(i,13,1,seed)*3)*2,'belt',seed+i*977,20+(i%9)*3); }
  }
  for(let i=0;i<10;i++){ const a=i*2.39996+(seed%11), d=i===0?0:(.45+hash3(i,5,6,seed)*.65)*m.r;
    const rr=i===0?Math.max(12,Math.round(m.r*.62)):5+Math.floor(hash3(i,8,9,seed)*6)*2;
    drawRock(m.x+Math.cos(a)*d,m.y+Math.sin(a)*d,rr,'belt',seed+i*131,18+i*4); }
}
function drawGasGiant(x,y,r,seed,t){
  const P=STYLE.gas, tilt=-.32, rx=r*1.8, ry=r*.42, cols=GB?['#306230','#0f380f','#8bac0f']:[P.c,P.b,P.d];
  glowAt(x,y,r,P.glow,.22+(REDUCED?0:.05*Math.sin(t/1000+seed%5)));
  ringBand(x,y,rx,ry,tilt,Math.PI,TAU,GB?cols:cols.map(c=>mixPixel(c,'#05030f',.4)),t,seed);
  drawBody(x,y,r,'gas',null,seed,55,.88);
  ringBand(x,y,rx,ry,tilt,0,Math.PI,cols,t,seed);
}
// THE BUS is a ring station, not a planet: things pass through it.
function drawStation(x,y,r,seed,t){
  const P=STYLE.station, rx=Math.max(22,r*1.35), ry=rx*.42, tilt=-.25, rot=REDUCED?0:(t/40000)%1, ct=Math.cos(tilt), st=Math.sin(tilt);
  const pt=(a,k)=>[x+(Math.cos(a)*rx*k)*ct-(Math.sin(a)*ry*k)*st, y+(Math.cos(a)*rx*k)*st+(Math.sin(a)*ry*k)*ct];
  const snap=v=>Math.round(v/2)*2;
  glowAt(x,y,r,P.glow,.2);
  const half=(a0,a1)=>{
    pixelRing(x,y,rx+3,ry+2,GB?'#306230':mixPixel(P.a,'#05030f',.55),a0,a1,tilt);
    pixelRing(x,y,rx,ry,GB?'#8bac0f':P.a,a0,a1,tilt);
    pixelRing(x,y,rx-3,ry-2,GB?'#0f380f':P.b,a0,a1,tilt);
    if(GB) return;
    for(let i=0;i<24;i++){ const a=((rot+i/24)%1)*TAU; if(a<a0||a>a1) continue; const p=pt(a,1);
      cx.fillStyle=i%6===0?'#ff4f9a':P.c; cx.fillRect(snap(p[0]),snap(p[1]),2,2); }
  };
  half(Math.PI,TAU);
  cx.fillStyle=GB?'#306230':P.b;
  for(let i=0;i<4;i++){ const a=rot*TAU+i*Math.PI/2; for(let k=.3;k<.95;k+=.12){ const p=pt(a,k); cx.fillRect(snap(p[0]),snap(p[1]),2,2); } }
  drawBody(x,y,Math.max(6,r*.42),'moon','config',seed,28);
  half(0,Math.PI);
  if(!GB&&!REDUCED&&Math.floor(t/500)%2){ const a=pt(0,1),b=pt(Math.PI,1);
    cx.fillStyle='#ff3b30'; cx.fillRect(snap(a[0])-1,snap(a[1])-1,4,4); cx.fillStyle='#22e07a'; cx.fillRect(snap(b[0])-1,snap(b[1])-1,4,4); }
}
// the sun's plate: on this machine's own map "This machine" (P8: the sun is never named like a folder), its
// name on hover; a synced map of another machine keeps that machine's name
function starPlateName(name){ return String(name)===String(W.name||'CONTAINER')?CM.word('this machine'):String(name); }
function drawStar(t,name,quests,oldest){
  const s=LY.star,P=STYLE.star,seed=seedOf(name), f=CM.focusUid(), dim=f&&f!==BOARD.uid?.4:1, R=fcSunR();
  // each layer is on the audit's list only when its paint calls happened (kindsLayerPaint counts them): the mono palette and a
  // glow that draws nothing leave no layer
  const painted=[];
  cx.save(); cx.globalAlpha*=dim;
  kindsLayerPaint(painted,'corona',()=>glowAt(s.x,s.y,R,P.glow,.38+(REDUCED?0:.08*Math.sin(t/1100))));
  kindsLayerPaint(painted,'rays',()=>{ if(GB) return; const n=18, a0=REDUCED?0:t/14000, k=REDUCED?0:Math.floor(t/240);
    for(let i=0;i<n;i++){ const a=a0+i*TAU/n, L=R+12+((i*7+k)%5)*5; cx.fillStyle=i%2?P.a:P.c;
      for(let d=R+4;d<L;d+=4) kindsPaint(cx,'fillRect',Math.round((s.x+Math.cos(a)*d)/2)*2,Math.round((s.y+Math.sin(a)*d)/2)*2,2,2); } });
  kindsLayerPaint(painted,'photosphere',()=>drawBody(s.x,s.y,R,'star',null,seed,160));
  kindsSunDrew(painted,true);   // the audit reads what was painted; the limb is measured from the surface's steps (ui/kinds.js)
  cx.restore();
  // "This machine" on its own map, amber; the badge counts what waits with no planet
  const loose=skyContacts().filter(c=>!c.planet_uid), bad=loose.some(c=>CM.bucket(c)==='problem');
  const word=starPlateName(name), text=String(name)===String(W.name||'CONTAINER')?CM.labelText(BOARD.uid):word;
  FC.lb.push({key:'tag:'+BOARD.uid,uid:BOARD.uid,x:s.x,y:s.y,r:R,text:text,col:LABEL.SUN,badge:LABEL.badgeOf(loose.length,bad),pri:1e7,kind:'sun',alpha:dim<1?.6:1,
    slots:MOB?['right','left','own','-1','+1']:null});   // a phone says it beside the sun, a desktop under it
}
// another machine in the vector look: a shaded sphere inside a thin tilted ring, its name tag, red and dashed when it is down
function vecMachine(u,p,r,cold,style,seed,t){
  const col=cold?VC.red:VC.line, half=(a0,a1,al)=>{ cx.save(); cx.strokeStyle=col; cx.lineWidth=1/ZOOM; cx.globalAlpha=al; cx.beginPath(); cx.ellipse(p.x,p.y,r+24,r*.38,-.3,a0,a1); cx.stroke(); cx.restore(); };
  glowAt(p.x,p.y,r,cold?VC.red:(STYLE[style]||STYLE.rock).glow,.14);
  half(Math.PI,TAU,.4); cx.save(); cx.imageSmoothingEnabled=true; { const n=kindsArtN(r), q=r/n; kindsBlit(cx,machineArt(n,seed,cold?VC.red:VC.amber||'#F5B342'),p.x,p.y,q); } cx.restore(); half(0,Math.PI,.8);
  if(cold) vecRing(p.x,p.y,r+5/ZOOM,VC.red,.8,[3,4]);
  tagPlate({name:String(u.name).toUpperCase(),suffix:'· MACHINE',x:p.x,y:p.y,r:r,r0:r,gap:21,face:'n',detail:String(u.status||''),warning:cold,uid:u.uid});
  if(detailAt(u,240)){ const si=SYNCED[u.name], txt=si?(si.present?'synced world '+ageS(si.age_s)+' old'+(si.stale?' · too old to show':''):'no synced world yet'):'', ly=p.y+r+30/ZOOM;
    vecWorldText(String(u.tour||''),p.x,ly+18/ZOOM,vecFont(12,0,'data'),cold?VC.red:VC.body,1,0);
    if(txt) vecWorldText(txt,p.x,ly+36/ZOOM,vecFont(12,0,'data'),si.stale?VC.red:VC.body,1,0);
    if(si&&si.counts) vecWorldText(si.counts.area+' areas · '+si.counts.cron+' crons',p.x,ly+54/ZOOM,vecFont(12,0,'data'),VC.body,1,0); }
}
// another machine is a distant galaxy (ui/kinds.js machineArt; the 3D view draws the same, the 3D module): amber while it answers, red when
// it is down. It never borrows a folder's body, so a machine cannot be read as a folder.
function drawPlanet(u,t){
  const p=LY.planets[u.name],S=sec('machine'),r=62,cold=isDead(u.status)||u.status==='COLD',seed=seedOf(u.name);
  const col=cold?'#FF5A5F':KIND_STATUS.sun;
  if(VEC){ vecMachine(u,p,r,cold,'rock',seed,t); return; }
  glowAt(p.x,p.y,r,col,.12);
  { const n=kindsArtN(r), q=r/n; kindsBlit(cx,machineArt(n,seed,col),p.x,p.y,q); }
  if(cold)pixelRing(p.x,p.y,r+5,r+5,col,0,TAU,0,true);
  tagPlate({name:String(u.name).toUpperCase(),x:p.x,y:p.y,r:r,gap:21,face:'n',color:col,detail:String(u.status||''),warning:cold,uid:u.uid});
  if(detailAt(u,240)){
    const si=SYNCED[u.name];
    const txt=si?(si.present?'synced world '+ageS(si.age_s)+' old'+(si.stale?' · too old to show':''):'no synced world yet'):'';
    const k=zs(), ly=p.y+r+62;
    cx.font=wfont(12);cx.textAlign='center';cx.fillStyle=cold?'#ff7777':'#abc2df';
    cx.fillText(String(u.tour||''),p.x,ly+18*k);
    if(txt){cx.fillStyle=si.stale?'#ff4d5e':'#abc2df';cx.fillText(txt,p.x,ly+36*k);}
    if(si&&si.counts)cx.fillText(si.counts.area+' areas · '+si.counts.cron+' crons',p.x,ly+54*k);
  }
}
const DK = ['area','dir','cron','proc'];
const isDK = u => !!u && DK.includes(u.kind);
let minGX=0, minGY=0, maxGX=40, maxGY=30;
function bounds(){
  const xs=units.map(u=>u.gx), ys=units.map(u=>u.gy);
  if(!xs.length){ minGX=-24; minGY=-20; maxGX=24; maxGY=20; return; }
  minGX=Math.min(...xs)-4; minGY=Math.min(...ys)-4; maxGX=Math.max(...xs)+4; maxGY=Math.max(...ys)+4;
}
// ---- backdrop: deterministic starfield (decoration only, never game state) ----
const STARS = [];
for(let _i=0;_i<300;_i++){ const _h=((_i*2654435761)>>>0);
  STARS.push({fx:(_h%1600)/1600, fy:(((_h>>>7)%1600))/1600, z:(((_h>>>13)%100))/100, tw:((_h>>>19)%7)}); }
const REDUCED = (typeof matchMedia!=='undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches);
// three depth layers drift at their own speed under the camera (far, mid, near); while Shift boosts the stars
// stretch into streaks along the way the ship is going, and a few near motes zip past
const STAR_PAR=[.04,.09,.16], STAR_STREAK=[8,16,30], MOTES=[];
for(let _i=0;_i<28;_i++){ const _h=(((_i+7)*2246822519)>>>0); MOTES.push({fx:(_h%1600)/1600, fy:((_h>>>9)%1600)/1600}); }
function drawStars(t){
  const mx=px-lastPx, my=py-lastPy, sp=Math.hypot(mx,my), boosting=!REDUCED&&boostOn&&sp>.5;
  if(boosting){ FX.ux=mx/sp; FX.uy=my/sp; }
  FX.boostK+=((boosting?1:0)-FX.boostK)*.2; if(FX.boostK<.02) FX.boostK=0;
  const bk=FX.boostK, sk=FX.sk; sk[0].length=sk[1].length=sk[2].length=0;
  for(let i=0;i<STARS.length;i++){ const s=STARS[i], ly=s.z<.34?0:s.z<.7?1:2, par=STAR_PAR[ly];
    const sx2 = (((s.fx*VW - camx*par)%VW)+VW)%VW;
    const sy2 = (((s.fy*VH - camy*par)%VH)+VH)%VH;
    const tw = 1;
    cx.globalAlpha = GB ? 0.3 : (0.3+0.7*s.z)*tw;
    cx.fillStyle = GB ? '#0f380f' : ((i%19===0&&SPACE.stars[2])||(i%23===0&&SPACE.stars[3])||(i%7===0&&SPACE.stars[1])||SPACE.stars[0]);
    const x=Math.round(sx2/2)*2,y=Math.round(sy2/2)*2;
    cx.fillRect(x,y,2,2);
    if(s.z>.94){cx.fillRect(x-2,y,6,2);cx.fillRect(x,y-2,2,6);}
    if(bk&&s.z>.25) sk[ly].push(x+1,y+1);
  }
  if(bk){   // one path per layer: far dim, near bright
    cx.lineCap='butt'; cx.lineWidth=2;
    for(let ly=0;ly<3;ly++){ const p=sk[ly]; if(!p.length) continue;
      const L=STAR_STREAK[ly]*bk; cx.globalAlpha=(.35+.25*ly)*bk+.1; cx.strokeStyle=GB?'#306230':(SPACE.stars[Math.min(ly,SPACE.stars.length-1)]||'#cfe4ff');
      cx.beginPath(); for(let i=0;i<p.length;i+=2){ cx.moveTo(p[i],p[i+1]); cx.lineTo(p[i]+FX.ux*L,p[i+1]+FX.uy*L); } cx.stroke(); }
    cx.globalAlpha=.4*bk; cx.strokeStyle=GB?'#306230':(SPACE.stars[1]||'#cfe4ff'); cx.beginPath();
    for(let i=0;i<MOTES.length;i++){ const o=MOTES[i], x=(((o.fx*VW-camx*.55)%VW)+VW)%VW, y=(((o.fy*VH-camy*.55)%VH)+VH)%VH;
      cx.moveTo(x,y); cx.lineTo(x+FX.ux*46*bk,y+FX.uy*46*bk); }
    cx.stroke();
  }
  cx.globalAlpha = 1;
}
const units = [];
// Runners (a registry, not a place) and the hub's four office rooms (no stats of their own) are not
// drawn on the map: agents that can take work are offered where you hand work out.
const OFF_MAP = new Set(['runner','room']);
function place(){
  units.length = 0;
  HALLS.filter(h=>!OFF_MAP.has(h.key)).forEach(h=>{
    const mine = W.chars.filter(c=>c.kind===h.key);
    mine.forEach((c,i)=>{
      const cols = h.w-2;
      units.push(Object.assign({}, c, {
        gx: h.x+1+(i%cols), gy: h.y+1+Math.floor(i/cols)*2, hall:h.key
      }));
    });
  });
  W.chars.filter(c=>c.kind==='area').forEach(a=>{ BLOCK_KIND[a.uid] = a.alias_of ? 'config' : (a.area_kind||'unknown'); });
  W.chars.filter(c=>isDK(c) && c.gx!=null && c.gy!=null).forEach(c=>units.push(Object.assign({}, c)));
  W.chars.filter(c=>c.kind==='ship').forEach(c=>units.push(Object.assign({},c,{gx:0,gy:0,hall:'seat'})));
  W.chars.filter(c=>c.kind==='portal').forEach(c=>{
    const m = units.find(u=>u.kind==='machine' && u.name===c.machine);
    if(m) units.push(Object.assign({}, c, {gx: m.gx, gy: m.gy+1, hall:'machine'}));
    else units.push(Object.assign({}, c, {gx: 3, gy: 13, hall:'machine'}));
  });
  agentTasks().filter(t=>TASK_LIVE.includes(t.status)).forEach(t=>units.push({uid:'task:'+t.id,kind:'task',task_id:t.id,name:taskTitle(t),
    status:t.status,retry_due:!!t.retry_due,target:t.target,runner:t.runner,model:t.model||'',path:t.target,gx:0,gy:0,hall:'seat'}));
  layoutSystem(units, MOONS); bounds(); questMarks();
}
place();
// ---- portals: which deck is shown. Persisted so the 15 s reload does not bounce home. ----
const REMOTE_MACHINES = (W.chars.filter(c=>c.kind==='portal').map(c=>c.machine).length
  ? W.chars.filter(c=>c.kind==='portal').map(c=>c.machine) : []);
let deck = 'home';
try{ const d=sessionStorage.getItem('deck');
  if(d==='home'||REMOTE_MACHINES.includes(d)) deck=d; }catch(e){}
/* crossing reset happens after its let (TDZ) */
function saveDeck(){ try{ sessionStorage.setItem('deck', deck); }catch(e){} }
let remoteState=null, remoteUnits=[], remoteHalls=[], remoteNote='', remoteMoons=[], remoteSynced=null, remoteInfo=null;
let portalAlert=null, fading=null, crossing=null
crossing=null; // reload must not freeze portals;
function remoteBounds(){
  if(!remoteHalls.length) return {minGX:minGX,minGY:minGY,maxGX:maxGX,maxGY:maxGY};
  let x0=Math.min(...remoteHalls.map(h=>h.x))-1, y0=Math.min(...remoteHalls.map(h=>h.y))-1;
  let x1=Math.max(...remoteHalls.map(h=>h.x+h.w))+1, y1=Math.max(...remoteHalls.map(h=>h.y+h.h))+1;
  return {minGX:minGX,minGY:minGY,maxGX:maxGX,maxGY:maxGY,x0:x0,y0:y0,x1:x1,y1:y1,
    minGXr:x0,minGYr:y0,maxGXr:x1,maxGYr:y1};
}
function buildRemoteDeck(machine, st){
  remoteUnits=[]; remoteHalls=[]; remoteNote=String((st&&st.note)||'');
  const tabs=(st&&Array.isArray(st.tabs))?st.tabs:[];
  if(!tabs.length){
    remoteHalls=[{key:'r0',label:String(machine).toUpperCase()+' DECK',x:2,y:2,w:9,h:5}];
  }else{
    let hx=2;
    tabs.forEach((tab,i)=>{
      const npp=Array.isArray(tab.panes)?tab.panes.length:0;
      const hw=npp>4?11:(npp>1?8:6);
      const label=String(tab.name||tab.title||tab.id||('TAB '+(i+1))).toUpperCase().slice(0,24)||('TAB '+(i+1));
      const h={key:'r'+i,label:label,x:hx,y:2,w:hw,h:6};
      remoteHalls.push(h);
      (Array.isArray(tab.panes)?tab.panes:[]).forEach((p,j)=>{
        const cols=h.w-2;
        const hasAgent=p.agent!=null&&p.agent!==''&&p.agent!==false;
        const st2=hasAgent?String(p.agent_status||'unknown'): 'COLD';
        remoteUnits.push({
          uid:'remote:'+machine+':'+String(p.pane_id||('p'+j)),
          kind:'seat', name:String(p.agent||p.pane_id||'pane'),
          role:'remote seat · '+machine+' '+String(p.pane_id||''),
          seat_role:'remote', seat:String(p.pane_id||''), alias:null,
          configured:!!hasAgent, window_index:0, window_id:'',
          pane_id:String(p.pane_id||''), command:String(p.agent||''),
          quick_key:null, composable:!!hasAgent,
          talkable:p.talkable===true, talk_reason:String(p.talk_reason||(hasAgent?'':'view only')),
          status:st2, cause:'', served:0, tour:String(p.cwd||p.title||machine),
          idle:0, hp:100, pane:String(p.title||p.cwd||''),
          remote:true, machine:machine, agent:p.agent||null,
          agent_status:hasAgent?String(p.agent_status||'unknown'):null,
          cwd:String(p.cwd||''), title:String(p.title||''),
          gx:h.x+1+(j%(h.w-2)), gy:h.y+1+Math.floor(j/(h.w-2))*2, hall:h.key
        });
      });
      hx+=hw+2;
    });
  }
  const edge=remoteHalls.length?remoteHalls[remoteHalls.length-1]:{x:2,y:2,w:11,h:6};
  remoteUnits.push({uid:'portal:__home__',kind:'portal',name:'home gate',machine:'__home__',
    role:'return gateway home',status:'LIVE',cause:'',served:0,tour:'return home',idle:0,hp:0,
    remoteReturn:true,gx:edge.x+edge.w-1,gy:edge.y+edge.h+1,hall:'__return__'});
}
function currentTargets(){ return deck==='home' ? units.concat([BOARD]) : remoteUnits.concat([BOARD]); }
// HIT-TESTING: a thing is clickable over its whole drawn size (a planet's disc, a ship's hull, the
// star), and its label plate counts too. When targets overlap, the most specific (smallest) wins,
// so a moon or a docked ship is picked over the planet behind it.
function hitRadius(u){
  if(!u) return 0;
  if(u===BOARD) return LY.star.r+12;
  const mm=deck==='home'?MOONS:remoteMoons, small=Math.max(1,.75/ZOOM), crafty=Math.max(1,.75/Math.max(ZOOM,CRAFT_ZMIN));
  if(u.kind==='area'){ const m=mm.find(x=>x.uid===u.uid); return (m?fcDispR(m):24)+8; }   // a planet is hit over the size it is drawn at
  if(u.kind==='machine') return LY.planets[u.name]?70:22*small;
  if(u.kind==='dir') return Math.max(13,(u.sr||8)+7)*small;
  if(isShip(u)&&u===mothership()) return Math.max(52*crafty,craftHull('carrier',SPRITE_CELL,carrierScale()));   // the hull as drawn (drawCarrier's scale)
  if(u.kind==='task'||isShip(u)) return 30*crafty;
  return 24*small;
}
function pickAt(wx,wy,slop){
  if(deck==='home'){ const p=CM.constellationHit(wx,wy,slop); if(p) return p; }
  const pass=min=>{ let best=null,bestR=Infinity,bestD=Infinity;
    currentTargets().forEach(u=>{ if(u.kind==='machine'&&deck==='home') return;   // other machines are not drawn on the 2D map
      if(QUIET&&(u.kind==='cron'||u.kind==='proc')) return;          // not drawn in the quiet map, so not a target
      if(skyHidden(u)) return;                                       // in warp (sky mode): not drawn, so not a target
      const at=FC.pos.get(u.uid);                                    // the focus view drew it here (a station, a craft at the open planet)
      if(!at&&u!==BOARD&&CM.detailHidden(u)) return;                 // not drawn (a moon on the overview, an idle agent at the carrier): not a target
      const d=Math.hypot((at?at.x:u.gx*TILE)-wx,(at?at.y:u.gy*TILE)-wy), hr=Math.max(at?at.r:hitRadius(u),min); if(d>hr) return;
      if(hr<bestR||(hr===bestR&&d<bestD)){ best=u; bestR=hr; bestD=d; } });
    return best; };
  const best=pass(0); if(best) return best;
  for(let i=CLICK_BOXES.length-1;i>=0;i--){ const b=CLICK_BOXES[i];
    if(wx>=b.x&&wx<=b.x+b.w&&wy>=b.y&&wy<=b.y+b.h){ const u=unitByUid(b.uid); if(u) return u; } }
  // a finger is wider than a pointer: with a slop (world units) nothing exact is left, so the nearest thing within the slop is taken
  return slop>0?pass(slop):null;
}
const BOARD = {uid:'world:quest-board',gx:0,gy:0};
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
let px = 0, py = 118, near = null, openUnit = null;
let camx=0, camy=0, lastFrame=0, pilot=null, activeAction=null;
// ZOOM: wheel or -/= keys, 0 resets to the home zoom (the fit of the world; a phone's 0.22). The world is drawn scaled about the ship; hit-testing and
// culling are done in world units, so nothing about the data changes with the zoom.
let ZOOM=MOB?0.22:1, ZOOM_STORED=false; try{ const z=parseFloat(sessionStorage.getItem('zoom')); if(z>=0.2&&z<=2&&z!==0.72){ ZOOM=z; ZOOM_STORED=true; } }catch(e){}   // 0.72 was the old default: a tab that kept it starts at 1 again
function setZoom(z){ ZOOM=Math.max(zoomFloor(),Math.min(fcZoomMax(),z)); try{ sessionStorage.setItem('zoom',String(ZOOM)); }catch(e){} }   // past 2 only while a planet is open
// the zoom that shows the whole world: the far edge of the farthest planet, from the ship, inside the stage with a margin
// (the world is a ring about the star, so at 1 a stranger sees only the star). Never past 1, never under 0.2.
function fitZoom(vw,vh){
  vw=Number(vw); vh=Number(vh); if(!(vw>200&&vh>200)) return 1;
  if((VEC&&(LY.tilt||1)<1)||!VEC){ let X=0, Y=0;   // the tilted plane, and the classic system laid to the screen's shape (layoutAspect): fit each axis on its own
    MOONS.forEach(m=>{ X=Math.max(X,Math.abs(m.x-px)+(m.r||0)+40); Y=Math.max(Y,Math.abs(m.y-py)+(m.r||0)+40); });
    if(X>0&&Y>0) return Math.max(zoomFloor(),Math.min(1,Math.floor(Math.min((vw-48)/(2*X),(vh-48)/(2*Y))*100)/100)); }
  let R=0; MOONS.forEach(m=>{ const d=Math.hypot(m.x-px,m.y-py)+(m.r||0)+40; if(d>R) R=d; });
  if(!(R>0)) return 1;
  return Math.max(zoomFloor(),Math.min(1,Math.floor(Math.min(vw-48,vh-48)/(2*R)*100)/100));
}
// the farthest out the map zooms: 0.2, and on a phone as far as its fit-all view needs (the whole system on a 390 px screen)
function zoomFloor(){ return MOB?0.06:0.2; }
// HOME VIEW. A phone, a desktop and a tablet start at the
// zoom that shows the whole world AND an offset that keeps every planet out from under the fixed cards laid over the stage: the
// HUD, comms, the wingmate, the ticker, the rails, the zoom column. The cards are read from the page when the view is made, in
// stage pixels. From the plain fit downward the first zoom with an offset that clears them all wins (the planets, the star and
// the ship); among offsets the one nearest the ship-centred view. Nothing clears (a small window): the fewest planets under a card.
const HOME={on:false,laid:false,sig:'',until:0,z:0,ox:0,oy:0,px:0,py:0,vw:0,vh:0};
function homeCovers(){
  const out=overlayBoxes(true).slice();
  let ox=0, oy=0;
  try{ const sr=STAGE&&STAGE.getBoundingClientRect(); if(sr&&Number.isFinite(sr.left)&&Number.isFinite(sr.top)){ ox=sr.left; oy=sr.top; } }catch(e){}
  // every always-on box a lane lays over the map says so with data-wall (the rail, the status bar, the phone's chips and sheet,
  // the workflow strip): the home view and the focus view keep out from under it without naming another lane's ids
  try{ Array.prototype.forEach.call(document.querySelectorAll('[data-wall]')||[],el=>{ const r=el&&el.getBoundingClientRect&&el.getBoundingClientRect(); if(!r||!(r.width>1&&r.height>1)) return;
      let cs=null; try{ cs=getComputedStyle(el); }catch(e){} if(cs&&(cs.display==='none'||cs.visibility==='hidden')) return;
      const x0=Math.max(0,r.left-ox), y0=Math.max(0,r.top-oy), x1=Math.min(VW,r.right-ox), y1=Math.min(VH,r.bottom-oy);
      if(x1>x0&&y1>y0) out.push({id:el.id||'wall',x:x0,y:y0,w:x1-x0,h:y1-y0}); }); }catch(e){}
  ['dash','side'].forEach(id=>{            // the rails sit beside the stage, not on it: clipped to it they are nothing, unless a layout lets one over it
    try{ const el=document.getElementById(id), r=el&&el.getBoundingClientRect&&el.getBoundingClientRect(); if(!r||!(r.width>1&&r.height>1)) return;
      const x0=Math.max(0,r.left-ox), y0=Math.max(0,r.top-oy), x1=Math.min(VW,r.right-ox), y1=Math.min(VH,r.bottom-oy);
      if(x1>x0&&y1>y0&&(x1-x0)*(y1-y0)<=.6*VW*VH) out.push({id:id,x:x0,y:y0,w:x1-x0,h:y1-y0}); }catch(e){} });
  try{ const mt=document.getElementById('matetxt'), b=out.find(o=>o.id==='mate');      // the wingmate grows upward as it types: hold the room it will take
    if(b&&mt){ const grow=Math.max(0,(parseFloat(getComputedStyle(mt).maxHeight)||150)-mt.getBoundingClientRect().height); b.y-=grow; b.h+=grow; } }catch(e){}
  return out;
}
function homeView(cards){
  // a phone fits the whole system too (fit-all, no zoom buttons; its bodies stay 28 px, its names 13 px)
  const plain=fitZoom(VW,VH), mid={zoom:plain,ox:0,oy:0,under:0};
  if(!(VW>200&&VH>200)||!MOONS.length) return mid;
  const covers=cards||homeCovers(); if(!covers.length&&!MOB){ FC.scaleNow=FC.SCALE; FC.floorNow=null; return mid; }
  const aw=VW-PANEL_W, vh=VH-(MOB?plateSheet():0), cx0=aw/2, cy0=vh/2, M=MOB?10:24;   // M: the margin the plain fit keeps
  // what must stay clear: a body at the size it is drawn at (r world units, never under its floor in pixels: fl) and its name
  // under it (lab pixels); the sun the same; the ship a small disc
  const fl=MOB?FC.FLOOR.phone:FC.FLOOR.desk, sfl=MOB?FC.SUNFLOOR.phone:FC.SUNFLOOR.desk, lab=MOB?24:28;
  let discs=MOONS.map(m=>({x:m.x-px,y:m.y-py,r:(m.r||0)*FC.SCALE,fl:fl,pad:10,lab:lab,hw:0,hh:0,m:m}));
  if((W.constellations||[]).some(g=>(g.threads||[]).length)) CM.constellationLayout().forEach(g=>{ const r=g.rim; discs.push({x:LY.star.x+Math.cos(g.angle)*r-px,y:LY.star.y+Math.sin(g.angle)*r-py,r:1,fl:0,pad:8,lab:24,hw:0,hh:0}); });
  if(LY&&LY.star) discs.push({x:LY.star.x-px,y:LY.star.y-py,r:LY.star.r||0,fl:sfl,pad:4,lab:lab,hw:0,hh:0});
  discs.push({x:0,y:0,r:20,fl:0,pad:4,lab:0,hw:0,hh:0});
  const names=[];   // the sector names are not drawn any more
  const rs=(it,z)=>Math.max(it.r*z,it.fl||0);
  const solve=(items,soft)=>{
    const span=(z,vert)=>{ let lo=-1e9, hi=1e9; const c=vert?cy0:cx0, size=vert?vh:aw;     // the shifts that keep every disc and its name on the stage
      discs.forEach(it=>{ const rr=rs(it,z)+it.lab, p=c+(vert?it.y:it.x)*z; lo=Math.max(lo,M+rr-p); hi=Math.min(hi,size-M-rr-p); }); return [lo,hi]; };
    const grid=(r,step)=>{ const g=[Math.min(r[1],Math.max(r[0],0))]; for(let v=r[0];v<=r[1];v+=step) g.push(v); g.push(r[1]); return g; };
    let dep=0;                                                      // how far the items under a card reach into it, in pixels (the tie-break when none clears)
    const under=(list,z,sx,sy,limit)=>{ let n=0; dep=0;             // items under a card; stops once it cannot beat limit
      for(const it of list){ const x=cx0+it.x*z+sx, y=cy0+it.y*z+sy, rr=rs(it,z)+it.pad;
        for(const b of covers){ const dx=Math.max(b.x-it.hw-x,0,x-b.x-b.w-it.hw), dy=Math.max(b.y-it.hh-y,0,y-b.y-b.h-it.hh), q=dx*dx+dy*dy;
          if(q<rr*rr){ n++; dep+=rr-Math.sqrt(q); break; } }
        if(n>=limit) return n; }
      return n; };
    let best=null;                                                  // fewest items under a card, then (none) the fewest soft ones and the nearest to centred in the free map, (some) the least reach
    // centred: the system's middle in the middle of the largest clear rectangle of the map (ui/focus.js fcLargest), not the
    // ship's place, so a rail or a card on one side does not leave the other side empty
    const sys=discs.filter(it=>it.r>0), bx=sys.length?(Math.min(...sys.map(it=>it.x-it.r))+Math.max(...sys.map(it=>it.x+it.r)))/2:0,
      by=sys.length?(Math.min(...sys.map(it=>it.y-it.r))+Math.max(...sys.map(it=>it.y+it.r)))/2:0, FR=fcLargest(covers,aw,vh), fx=FR.x+FR.w/2, fy=FR.y+FR.h/2;
    // from the zoom at which the system's box would just fill the stage (a system laid wider than tall fits further in than
    // the plain fit's circle says), down to the floor; a zoom that does not fit is skipped by span()
    const top=sys.length?Math.min(1,(aw-2*M)/Math.max(1,(Math.max(...sys.map(it=>it.x+it.r))-Math.min(...sys.map(it=>it.x-it.r)))),
      (vh-2*M)/Math.max(1,(Math.max(...sys.map(it=>it.y+it.r))-Math.min(...sys.map(it=>it.y-it.r))))):plain;
    for(let k=Math.round(Math.max(plain,top)*100); k>=Math.round(zoomFloor()*100); k--){
      const z=k/100, rx=span(z,false), ry=span(z,true); if(rx[0]>rx[1]||ry[0]>ry[1]) continue;   // the ring does not fit the stage at this zoom
      const step=Math.max(4,Math.ceil(Math.sqrt((rx[1]-rx[0])*(ry[1]-ry[0])/6000))), xs=grid(rx,step), ys=grid(ry,step);     // 4 px, coarser only to keep a roomy stage near 6000 offsets
      xs.forEach(sx=>ys.forEach(sy=>{ const n=under(items,z,sx,sy,best?best.n+1:1e9); if(best&&n>best.n) return;
        const ex=sx-(fx-cx0-bx*z), ey=sy-(fy-cy0-by*z), k2=n?dep:under(soft,z,sx,sy,1e9), k3=n?0:ex*ex+ey*ey;
        if(!best||n<best.n||k2<best.k2||(k2===best.k2&&k3<best.k3)) best={n:n,z:z,sx:sx,sy:sy,k2:k2,k3:k3}; }));
      if(best&&best.n===0) break;
    }
    return best; };
  // the bodies drawn half again and more (FC.SCALE) when the map has the room, at their plain size when no view clears the
  // cards with them; the size chosen is the one the frame draws (FC.scaleNow, fcDispR)
  let pick=solve(discs,names); FC.scaleNow=FC.SCALE; FC.floorNow=null;
  // crowded (the old cards over a small stage): the plain size, then the spec's own floor (18 px across), then the size on disk
  // alone with a name's room as the old page kept it: the first that clears every card wins, else the fewest under one
  // (no pick at all: the system does not fit the stage at any zoom with this size: the smaller sizes are tried too)
  [[1,null,null],[1,9,null],[1,.01,8]].forEach(([sc,fl1,lb1])=>{ if(pick&&pick.n===0) return;
    const big=discs; discs=big.map(it=>it.m?Object.assign({},it,{r:(it.m.r||0)*sc},fl1?{fl:fl1}:{},lb1?{lab:lb1}:{}):it);
    const p2=solve(discs,names); if(p2&&(!pick||p2.n<pick.n)){ pick=p2; FC.scaleNow=sc; FC.floorNow=fl1; } else discs=big; });
  return pick?{zoom:pick.z,ox:-pick.sx/pick.z,oy:-pick.sy/pick.z,under:pick.n}:mid;   // under: the planets no view could keep out from under a card
}
function homeZoom(){ return homeView().zoom; }
function zoomHome(){ const h=homeView(); setZoom(h.zoom); camOff.x=h.ox; camOff.y=h.oy; }       // the 0 key and the zoom column's long press
function homeNote(){ HOME.z=ZOOM; HOME.ox=camOff.x; HOME.oy=camOff.y; HOME.px=px; HOME.py=py; HOME.vw=VW; HOME.vh=VH; }
// the first load kept no choice, so the home view is made while the map is up (the wingmate and the zoom column are not there under
// Today), and made again when the stage changes size and, for the first seconds, when a card changes (the display face and the
// static-file banner arrive after the script), for as long as nothing was zoomed, panned or flown. A stored zoom never starts it.
function homeSettle(){
  if(!HOME.on||VIEW!=='map') return;
  if(ZOOM!==HOME.z||camOff.x!==HOME.ox||camOff.y!==HOME.oy||px!==HOME.px||py!==HOME.py){ HOME.on=false; return; }
  const cards=homeCovers(), sig=cards.map(b=>b.id+':'+[b.x,b.y,b.w,b.h].map(Math.round).join(',')).join(';');
  if(HOME.laid&&HOME.vw===VW&&HOME.vh===VH&&(sig===HOME.sig||Date.now()>HOME.until)) return;
  if(!MOB&&!VEC) layoutShapeSettle(cards);   // the system laid to the shape that fits the map and its cards
  const h=homeView(cards); ZOOM=h.zoom; camOff.x=h.ox; camOff.y=h.oy; homeNote();
  if(!HOME.laid) HOME.until=Date.now()+4000;
  HOME.laid=true; HOME.sig=sig;
}
// what a test, or a curious console, reads back: the cards, every planet's disc on the stage, which sit under a card
window.__fitAudit=function(){
  const aw=VW-PANEL_W, covers=homeCovers().map(b=>({id:b.id,x:b.x,y:b.y,w:b.w,h:b.h}));
  const planets=MOONS.map(m=>({uid:m.uid,label:m.label,x:aw/2+(m.x-px-camOff.x)*ZOOM,y:VH/2+(m.y-py-camOff.y)*ZOOM,r:fcDispR(m)*ZOOM}));   // at the size it is drawn
  const hit=(p,b)=>{ const dx=Math.max(b.x-p.x,0,p.x-b.x-b.w), dy=Math.max(b.y-p.y,0,p.y-b.y-b.h); return dx*dx+dy*dy<p.r*p.r; };
  return {zoom:ZOOM,camOff:{x:camOff.x,y:camOff.y},vw:VW,vh:VH,panel:PANEL_W,home:Object.assign({},HOME),covers:covers,planets:planets,
    under:planets.filter(p=>covers.some(b=>hit(p,b))).map(p=>p.label),
    outside:planets.filter(p=>p.x-p.r<0||p.y-p.r<0||p.x+p.r>aw||p.y+p.r>VH).map(p=>p.label)};
};
const actionQueue=[], keys={};
try{ const s=sessionStorage.getItem('pos'); if(s){ const p=JSON.parse(s);
  if(Array.isArray(p)&&Number.isFinite(p[0])&&Number.isFinite(p[1])){px=p[0];py=p[1];} } }catch(e){}
px=clamp(px,minGX*TILE,maxGX*TILE); py=clamp(py,minGY*TILE,maxGY*TILE);
setInterval(()=>{ try{ sessionStorage.setItem('pos', JSON.stringify([px,py])); }catch(e){} }, 900);
addEventListener('beforeunload', ()=>{ try{ sessionStorage.setItem('pos', JSON.stringify([px,py])); }catch(e){} });
function typing(){ const t=document.activeElement; return !!t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)||t.isContentEditable===true); }
// a browser or system shortcut (Ctrl/Cmd/Alt + key) is never a page action; AltGr, which some layouts type letters with, is not one
function modKey(e){ return !!(e.metaKey||((e.ctrlKey||e.altKey)&&!(e.getModifierState&&e.getModifierState('AltGraph')))); }
// a focused button, summary or link is activated by Space and Enter: the page must not eat them
function activates(t){ return !!t&&(/^(BUTTON|SUMMARY|A)$/.test(t.tagName)||(t.getAttribute&&/^(button|link|tab|menuitem|switch)$/.test(t.getAttribute('role')||''))); }
function clearKeys(){ Object.keys(keys).forEach(k=>delete keys[k]); }
function cancelAgentPilot(){
  if(pilot && pilot.source==='agent') pilot=null;
  activeAction=null; actionQueue.length=0;
  lastDock=null; visitHoldUntil=0;
}
const camOff={x:0,y:0}; let mouseW=null, hover=null;
function humanMove(){ cancelAgentPilot(); camOff.x=0; camOff.y=0; }
function portalActivate(u){
  if(!DESK){ show(u); return; }
  crossTo(u.machine);
}
function activate(u){
  if(!u) return;
  if(composerOpen()&&u!==BOARD&&(u.kind==='area'||u.kind==='dir')){ aimAt(u); BURSTS.push({x:u.gx*TILE,y:u.gy*TILE,r:focusRadius(u),t0:performance.now()}); return; }
  // a planet opens in focus: the camera eases there (FOCUS_CAM, ui/focus.js) and the ship stays where it is; anything else the ship flies to
  if(deck==='home'&&VIEW==='map'&&u.kind!=='area'&&(u===BOARD||u.gx!=null)){ const a=approach(u), d=Math.hypot(a.x-px,a.y-py); if(d>60&&d<2600) pilot={source:'human',x:clamp(a.x,minGX*TILE,maxGX*TILE),y:clamp(a.y,minGY*TILE,maxGY*TILE)}; }
  if(u===BOARD){ showStar(); return; }
  if(u.kind==='task'){ openTask(u.task_id); return; }
  if(deck==='home' && u.kind==='portal' && !u.remoteReturn){ portalActivate(u); return; }
  if(deck!=='home' && u.kind==='portal' && u.machine==='__home__'){ goHome(); return; }
  show(u);
}
// ---- remote decks: a planet's own synced world, with live herdr seats on top --------------
// Every machine renders itself into fleet-game/out/<host>/world.json (its own claim about itself);
// the sync bus carries the file here. The bridge serves it with its age and refuses it past 24 h.
// Live herdr stays the authority for seats and for talking. So a crossing shows:
//   synced world + live seats      when both answer
//   synced world + snapshot seats  when herdr does not answer (seats view only, labelled snapshot)
//   herdr-only rectangular deck    when there is no fresh synced world
//   "did not answer"               when neither exists — and the portal says which, and how old
function ageS(s){ s=Number(s)||0; return s<90?Math.round(s)+'s':(s<5400?Math.round(s/60)+'m':(s<172800?Math.round(s/3600)+'h':Math.round(s/86400)+'d')); }
const SYNCED = {};
function pollSynced(){
  if(!DESK) return;
  REMOTE_MACHINES.forEach(m=>api('/remote/world?machine='+encodeURIComponent(m)+'&meta=1')
    .then(j=>{ SYNCED[m]={present:true, stale:!!j.stale, age_s:j.age_s, generated:String(j.generated||''), counts:j.counts||null}; })
    .catch(e=>{ SYNCED[m]={present:false, err:String((e&&e.body&&e.body.error)||(e&&e.message)||'')}; }));
}
function liveSeatUnits(machine, st){
  const out=[]; const tabs=(st&&Array.isArray(st.tabs))?st.tabs:[];
  tabs.forEach((tab,i)=>{ (Array.isArray(tab.panes)?tab.panes:[]).forEach((p,j)=>{
    const hasAgent=p.agent!=null&&p.agent!==''&&p.agent!==false;
    out.push({uid:'remote:'+machine+':'+String(p.pane_id||('p'+j)), kind:'seat', name:String(p.agent||p.pane_id||'pane'),
      role:'remote seat · '+machine+' '+String(p.pane_id||''), seat_role:'remote', seat:String(p.pane_id||''), alias:null,
      configured:!!hasAgent, window_index:i*100+j, window_id:'', pane_id:String(p.pane_id||''), command:String(p.agent||''),
      quick_key:null, composable:!!hasAgent, talkable:p.talkable===true, talk_reason:String(p.talk_reason||(hasAgent?'':'view only')),
      status:hasAgent?String(p.agent_status||'unknown'):'COLD', cause:'', served:0, tour:String(p.cwd||p.title||machine),
      idle:0, hp:100, pane:String(p.title||p.cwd||''), remote:true, machine:machine, agent:p.agent||null,
      agent_status:hasAgent?String(p.agent_status||'unknown'):null, cwd:String(p.cwd||''), title:String(p.title||''), hall:'seat'});
  }); });
  return out;
}
function buildRemoteSystem(machine, synced, live, info){
  remoteUnits=[]; remoteHalls=[]; remoteMoons.length=0;
  remoteNote = live ? String(live.note||'') : ('live herdr did not answer'+(info&&info.liveErr?' ('+info.liveErr+')':'')+' — the seats shown are the snapshot, view only');
  const pre='remote:'+machine+':';
  (synced.chars||[]).forEach(c=>{
    if(!c||typeof c!=='object') return;
    if(c.kind==='portal' || OFF_MAP.has(c.kind)) return;            // no nested warps; no registry rows as bodies
    if(c.kind==='machine' && c.name===machine) return;              // that machine is the star here
    if(c.kind==='seat' && live) return;                             // live herdr is the authority for seats
    const u=Object.assign({}, c, {uid:pre+String(c.uid||(c.kind+':'+c.name)), remote:true, machine:machine, synced:true,
      hall:(c.kind==='seat'||c.kind==='room')?c.kind:(c.hall||c.kind)});
    if(c.area) u.area=pre+String(c.area);
    if(c.kind==='seat'){ u.snapshot=true; u.talkable=false; u.composable=false;
      u.talk_reason='snapshot from the synced world; live herdr is needed to talk'; u.pane_id=String(c.pane_id||''); }
    if(c.kind==='area'){ BLOCK_KIND[u.uid] = c.alias_of ? 'config' : (c.area_kind||'unknown'); }
    remoteUnits.push(u);
  });
  if(live) liveSeatUnits(machine, live).forEach(u=>remoteUnits.push(u));
  layoutSystem(remoteUnits, remoteMoons, machine, syncedScope(synced));
  remoteUnits.push({uid:'portal:__home__',kind:'portal',name:'home gate',machine:'__home__',role:'return gateway home',
    status:'LIVE',cause:'',served:0,tour:'return home',idle:0,hp:0,remoteReturn:true,gx:0,gy:project(0,LY.dock+64).y/TILE,hall:'__return__'});
}
async function fetchRemote(machine){
  const [st, wd] = await Promise.allSettled([
    api('/remote/state?machine='+encodeURIComponent(machine)),
    api('/remote/world?machine='+encodeURIComponent(machine))]);
  const live = st.status==='fulfilled' ? (st.value||{}) : null;
  const liveErr = st.status==='rejected' ? String((st.reason&&st.reason.body&&st.reason.body.error)||(st.reason&&st.reason.message)||'did not answer') : '';
  const w = wd.status==='fulfilled' ? wd.value : null;
  const synced = (w && !w.stale && w.world) ? w.world : null;
  const info = {liveErr:liveErr, present:!!w, stale:!!(w&&w.stale), age_s:w?w.age_s:null, generated:w?String(w.generated||''):'',
    worldErr: wd.status==='rejected' ? String((wd.reason&&wd.reason.body&&wd.reason.body.error)||'') : ''};
  return {live:live, synced:synced, info:info};
}
async function crossTo(machine){
  if(!DESK||crossing) return;
  if(!REMOTE_MACHINES.includes(machine)) return;
  crossing=machine;
  const t0=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();
  fading={t0:t0,dur:400,text:'crossing to '+machine+'…'};
  await new Promise(r=>setTimeout(r,420));
  try{
    const r=await fetchRemote(machine);
    if(!r.live && !r.synced){
      let why = machine+' did not answer'+(r.info.liveErr?' ('+r.info.liveErr+')':'');
      why += r.info.stale ? ' · synced world is '+ageS(r.info.age_s)+' old, past 24h, not shown' : (r.info.present ? '' : ' · no synced world yet');
      portalAlert={machine:machine,msg:why,until:Date.now()+4500};
    } else enterRemoteDeck(machine, r.live, r.synced, r.info);
  }catch(e){
    portalAlert={machine:machine,msg:machine+' did not answer',until:Date.now()+3000};
  }finally{ fading=null; crossing=null; }
}
function ensureHomeGate(){
  if(!remoteUnits.some(u=>u.uid==='portal:__home__')){
    const edge = remoteHalls.length ? remoteHalls[remoteHalls.length-1] : {x:2,y:2,w:11,h:6};
    remoteUnits.push({uid:'portal:__home__',kind:'portal',name:'home gate',machine:'__home__',
      role:'return gateway home',status:'LIVE',cause:'',served:0,tour:'return home — Home / Shift+H / Esc',idle:0,hp:0,
      remoteReturn:true,gx:edge.x+edge.w-1,gy:edge.y+edge.h+1,hall:'__return__'});
  }
}
function parkAtHomeGate(){
  ensureHomeGate();
  const g = remoteUnits.find(u=>u.uid==='portal:__home__');
  if(g){ px=g.gx*TILE; py=g.gy*TILE+36; try{ sessionStorage.setItem('pos', JSON.stringify([px,py])); }catch(e){} }
}
function enterRemoteDeck(machine, live, synced, info){
  deck=machine; saveDeck(); crossing=null;
  ensureHomeGate();
  remoteState=live; remoteSynced=synced; remoteInfo=info||{};
  if(synced) buildRemoteSystem(machine, synced, live, info); else { remoteMoons.length=0; remoteSynced=null; buildRemoteDeck(machine, live||{}); }
  closeAll();
  if(remoteMoons.length){ px=0; py=118; }
  else if(remoteUnits.length){ px=remoteUnits[0].gx*TILE; py=remoteUnits[0].gy*TILE+36; }
  else if(remoteHalls.length){ px=(remoteHalls[0].x+2)*TILE; py=(remoteHalls[0].y+2)*TILE; }
  ensureHomeGate();
  // if the player is outside any hall and the deck is remote, park them at the home gate
  const gate = remoteUnits.find(u=>u.uid==='portal:__home__');
  if(gate && !remoteHalls.length){ parkAtHomeGate(); }
  try{ sessionStorage.setItem('pos', JSON.stringify([px,py])); }catch(e){}
  paintDeckChrome();
  // one-shot floating way-out so a remote deck can never trap the player
  const hb=document.getElementById('gohome');
  if(hb) hb.hidden = (deck==='home');
}
function paintDeckChrome(){
  taskbar(); glanceTick(); tickerTick();
  const sc=document.querySelector('#hud .sc');
  if(sc&&deck==='home'){ sc.setAttribute('data-count','decisions'); hudWorld(); }
  else if(sc){ // another machine's snapshot: its own oldest decision, not this machine's count
    sc.removeAttribute('data-count');
    sc.textContent=String((remoteSynced&&remoteSynced.oldest!=null)?remoteSynced.oldest:'?'); const small=document.createElement('small');
    small.textContent='days, the oldest decision on '+deck; sc.appendChild(small); }
}
function goHome(){
  if(deck==='home') return;
  crossing=null;
  const t0=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();
  fading={t0:t0,dur:400,text:'returning home…'};
  setTimeout(()=>{ deck='home'; saveDeck(); crossing=null; remoteState=null; remoteSynced=null; remoteInfo=null; remoteUnits=[]; remoteHalls=[];
    remoteMoons.length=0; remoteNote=''; closeAll(); px=0; py=118; fading=null; paintDeckChrome();
    const hb=document.getElementById('gohome'); if(hb) hb.hidden=true; },420);
}
async function refreshRemote(){
  if(deck==='home'||crossing||!DESK) return;
  const dm=deck, sel=openUnit&&openUnit.uid, keep=[px,py];
  try{ const r=await fetchRemote(dm); if(deck!==dm) return;
    if(!r.live && !r.synced) return;                              // keep what we have; the banner carries the age
    remoteState=r.live; remoteSynced=r.synced; remoteInfo=r.info;
    if(r.synced) buildRemoteSystem(dm, r.synced, r.live, r.info); else { remoteMoons.length=0; buildRemoteDeck(dm, r.live||{}); }
    px=keep[0]; py=keep[1];
    if(sel){ const u=remoteUnits.find(x=>x.uid===sel); if(u){ openUnit=u; const st=document.getElementById('pstatus'); if(st) st.textContent=String(u.status||''); } }
    paintDeckChrome();
  }catch(e){}
}
function curQuests(){ return deck==='home' ? (W.quests||[]) : ((remoteSynced&&remoteSynced.quests)||[]); }
let PROMPT_TARGET=null;
// ---- PLANET TYPES: a planet's type is its purpose (ui/kinds.js planetLook), named in plain words; its condition (quiet,
// late jobs, caches, the worst) is an overlay now, never a type. The old ids stay as keys (aliases) for older readers. ----
const TYPE_NAME={city:'Code', ocean:'Notes', desert:'Reference', gas:'Data', aurora:'Media', crystal:'Config', station:'Shared', asteroid:'Archive', rock:'Not sorted',
  ice:'quiet', toxic:'late jobs', blight:'most troubled'};
const TYPE_RULE={city:PURPOSE.code.rule, ocean:PURPOSE.notes.rule, desert:PURPOSE.reference.rule, gas:PURPOSE.data.rule, aurora:PURPOSE.media.rule,
  crystal:PURPOSE.config.rule, station:PURPOSE.shared.rule, asteroid:PURPOSE.archive.rule, rock:PURPOSE.unsorted.rule,
  ice:'nothing written in over 30 days', toxic:'3 or more scheduled jobs here are late or never ran', blight:'the most troubled planet on the map'};
// [type, why, trouble]: the type is the purpose's body id (what drawMoon and the cards read as m.type), the why says whether
// the owner set it or the files suggest it, the trouble counts stale cron lines and board items (the layout's worst pick)
function planetType(a, list, tasks, boardItems){
  const bad=(tasks||[]).filter(u=>u.kind==='cron'&&['STALE','NEVER','NO RECEIPT'].includes(u.verdict)).length;
  const items=(boardItems||taskItems()).filter(t=>t.area===a.name).length, trouble=bad*2+items;
  if(a.alias_of) return ['rock','a symlink, nothing of its own',0];
  const p=KIND_PURPOSE[String(a.area_kind||BLOCK_KIND[a.uid]||'unknown')]||'unsorted';
  return [PURPOSE[p].body,(a.kind_guessed===false?'set by you: ':'guessed from the files: ')+PURPOSE[p].rule,trouble];
}
// ---- EFFORT: only what was measured. hub touches per body (hook log, last 2 h) and minutes per job for runners.
function effortLine(u){
  const E=W.effort||{}, n=(E.touches||{})[u.uid];
  if(n){ const last=(E.last||{})[u.uid]; return 'hub effort: '+n+' touch'+(n>1?'es':'')+' in '+(E.window_min||120)+' min'+(last?' · last '+CM.relTime(Date.now()/1000-last):''); }
  if(u.kind==='runner'&&u.timed_jobs) return 'effort: avg '+u.minutes_avg+' min per job over '+u.timed_jobs+' timed · last '+u.minutes_last+' min';
  return '';
}
function drawEffort(list){
  const E=W.effort||{}, T=E.touches||{};
  const tr=(E.trace||[]).slice(-12); let prev=null;
  tr.forEach((uid,i)=>{ const u=list.find(x=>x.uid===uid); if(!u){ prev=null; return; }
    if(prev&&prev!==u){ cx.save(); cx.globalAlpha=0.10+0.5*(i/tr.length); cx.strokeStyle='#e6edf3'; cx.lineWidth=1.5; cx.setLineDash([3,7]);
      cx.beginPath(); cx.moveTo(prev.gx*TILE,prev.gy*TILE); cx.lineTo(u.gx*TILE,u.gy*TILE); cx.stroke(); cx.restore(); }
    prev=u; });
  list.forEach(u=>{ const n=T[u.uid]; const txt = n ? 'hub ×'+n : ((u.kind==='runner'&&u.timed_jobs) ? u.minutes_avg+'m/job' : null); if(!txt) return;
    queuePlate({key:'e:'+u.uid,kind:'seat',pri:PLATE_PRI.seat,style:'fill',fill:n?'#e6edf3':'#ff9a2e',ink:'#04060c',x:Math.round(u.gx*TILE),y:Math.round(u.gy*TILE),
      r:18,face:'s',segs:[{t:txt,col:'#04060c'}]});
    if(n){ cx.save(); cx.globalAlpha=Math.min(.6,.12*n); cx.strokeStyle='#e6edf3'; cx.lineWidth=2; cx.beginPath(); cx.arc(u.gx*TILE,u.gy*TILE-6,26+2*Math.min(n,8),0,Math.PI*2); cx.stroke(); cx.restore(); }
  });
}
function tipLines(u){
  if(u===BOARD){ const c=CM.counts(), sk=deck==='home'&&skyOk(), nd=sk&&Number.isInteger(c.decisions)?c.decisions:curQuests().length, od=sk&&c.oldest_decision&&Number.isInteger(c.oldest_decision.days)?c.oldest_decision.days:(W.oldest||0);
    return [CM.word('this machine'), nd+' '+CM.countNoun('decisions',nd)+(nd?' · oldest '+CM.ageWords(od):''), 'click: open']; }
  if(u.kind==='task') return [String(u.name||'').slice(0,60), 'task · '+(TASK_WORD[u.status]||u.status)+' · '+agentLine(u.runner,u.model), shortPath(u.target), 'click: open the task'];
  const L=[String(u.name||u.uid).toUpperCase()+' · '+(u.kind==='cron'?'scheduled job':String(u.kind||''))];
  if(u.kind==='area'){ const m=(deck==='home'?MOONS:remoteMoons).find(x=>x.uid===u.uid); L.push(fmtN(u.files)+' files · '+String(u.life||'').toLowerCase()+' · '+(u.worker||'no worker')+((u.seats&&u.seats.length)?' · '+u.seats.length+' agent'+(u.seats.length>1?'s':'')+' here':''), String(u.path||'')); if(m&&m.type) L.push(TYPE_NAME[m.type]+' — '+m.typeWhy); }
  else if(u.kind==='dir') L.push((u.files==null?'not walked':fmtN(u.files)+' files')+' · '+ageStr(u.age)+(u.door?' · door':'')+(u.git?' · git':''), String(u.path||''));
  else if(u.kind==='cron') L.push((u.verdict?CM.verdictWord(u.verdict):String(u.status||''))+' · '+String(u.when||u.schedule||''), typeof u.receipt_age==='number'?'its log was written '+CM.relTime(u.receipt_age):'no log found');
  else if(u.kind==='proc') L.push('running '+String(u.elapsed||'')+' · '+String(u.count||1)+' process'+((u.count||1)>1?'es':''), String(u.args||'').slice(0,72));
  else if(isShip(u)) L.push(String(u.status||''), String(u.pane||'').slice(0,72));
  else if(u.kind==='runner') L.push(String(u.status||'')+' · '+(u.served||0)+' jobs · '+String(u.tour||''), String(u.cause||u.role||'').slice(0,72));
  else L.push(String(u.status||'')+' · '+String(u.tour||u.role||''));
  if(u.kind!=='seat' && typeof effortLine==='function'){ const e=effortLine(u); if(e) L.push(e); }
  L.push(u.kind==='portal' ? 'click: cross · E: inspect' : 'click: fly · E: inspect · P: prompt · drag: pan · C: recenter');
  return L.filter(Boolean);
}
function openPrompter(t){
  const P=document.getElementById('prompter'); if(!P) return;
  if(!DESK){ toast(REMOTE?'desktop only: drafts go to seats on the desktop':'static file: live mode needed'); return; }
  const path=String(t.path||''), name=String(t.name||''), kind=String(t.kind||'target');
  PROMPT_TARGET={path:path, name:name, kind:kind, uid:t.uid||null};
  document.getElementById('ptarget').textContent=kind+' · '+name+(path?' · '+path:'');
  const sel=document.getElementById('pseat');
  let lastSeat=''; try{ lastSeat=localStorage.getItem('promptSeat')||''; }catch(e){}
  const folder = kind==='area' ? path : (kind==='dir' ? path : (path ? path.replace(/[/][^/]*$/, '') : ''));
  PROMPT_TARGET.door = kind==='area' ? name : folder;
  seatOptions(sel, folder, lastSeat);
  closeAll(); P.classList.add('on'); const ta=document.getElementById('ptext'); ta.value=''; document.getElementById('pmsg').textContent=''; setTimeout(()=>ta.focus(),40);
}
document.getElementById('pcancel').addEventListener('click',()=>document.getElementById('prompter').classList.remove('on'));
document.getElementById('pgo').addEventListener('click',()=>{
  const sel=document.getElementById('pseat'), ta=document.getElementById('ptext'), msg=document.getElementById('pmsg');
  const txt=ta.value.trim(); if(!txt){ msg.textContent='write what should be done'; return; }
  if(sel.value==='__door__'){ document.getElementById('pgo').disabled=true; openDoor(PROMPT_TARGET.door, msg).then(s=>{ document.getElementById('pgo').disabled=false; if(s){ seatOptions(sel, PROMPT_TARGET.door.startsWith('/')?PROMPT_TARGET.door:(unitByUid('area:'+PROMPT_TARGET.door)||{}).path||'', s.uid); sel.value=s.uid; msg.textContent+=' — now pick Draft to seat again'; } }); return; }
  const seat=unitByUid(sel.value);
  if(!seat){ msg.textContent='pick a seat'; return; }
  const text=txt+' — target: '+(PROMPT_TARGET&&PROMPT_TARGET.path||'')+(PROMPT_TARGET&&PROMPT_TARGET.kind?' ('+PROMPT_TARGET.kind+')':'');
  drafts[seat.uid]=text; saveDrafts(); try{ localStorage.setItem('promptSeat',seat.uid); }catch(e){}
  document.getElementById('prompter').classList.remove('on'); show(seat);
});
document.getElementById('ptext').addEventListener('keydown',e=>{ if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); document.getElementById('pgo').click(); } });
// ---- GETTING THERE: a seat that is going to act on a folder must sit IN that folder ---------
// A prompt about a planet offers "open a door here first": the bridge runs door.sh (a real
// tmux window in that directory, running the assigned worker or a plain shell). The world then
// shows that seat docked AT the planet, and the draft goes into it. The human still sends.
function seatsIn(path){ return units.filter(u=>u.kind==='seat'&&!u.remote&&u.cwd&&(u.cwd===path||String(u.cwd).startsWith(path+'/'))); }
async function openDoor(target, statusEl){
  if(!DESK){ if(statusEl) statusEl.textContent=REMOTE?'desktop only':'static file — live mode needed'; return null; }
  if(statusEl){ statusEl.className='saym'; statusEl.textContent='opening a door in '+target+'…'; }
  try{
    const j=await api('/door',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:target})});
    // the world rebuilds every 15 s; ask for a fresh state until the new seat shows up (≤ 20 s)
    for(let i=0;i<10;i++){ await new Promise(r=>setTimeout(r,2000)); try{ applyWorld(await api('/state?fresh=1')); }catch(e){}
      const s=units.find(u=>u.kind==='seat'&&u.name===j.window); if(s){ if(statusEl) statusEl.textContent='door open: seat '+j.window+' in '+j.path; return s; } }
    if(statusEl) statusEl.textContent='door opened ('+j.window+') but the seat has not appeared in the world yet; try again in a moment';
    return null;
  }catch(e){ if(statusEl){ statusEl.className='saym err'; statusEl.textContent=e.message; } return null; }
}
function seatOptions(sel, path, lastSeat){
  const here=seatsIn(path||'/nowhere'), all=units.filter(u=>u.kind==='seat'&&!u.remote&&u.composable);
  const opt=(s,tag)=>'<option value="'+attr(s.uid)+'"'+(s.uid===lastSeat?' selected':'')+'>'+esc(s.alias||s.name)+tag+(s.talkable?'':' (view only right now)')+'</option>';
  sel.innerHTML=(path?'<option value="__door__">open a door here first (a seat in this folder)</option>':'')+
    here.filter(s=>s.composable).map(s=>opt(s,' · in this folder')).join('')+
    all.filter(s=>!here.includes(s)).map(s=>opt(s,' · elsewhere: '+String(s.cwd||'?'))).join('')||'<option value="">no seat can be drafted to</option>';
  if(path && here.some(s=>s.composable)) sel.value=here.find(s=>s.composable).uid;
}
function remoteDoor(u){
  return '<div class="say"><div class="viewonly">open door on '+esc(u.machine)+' — it runs THERE, not here: ssh in and run <code>door.sh '+esc(shq(u.kind==='area'?u.name:u.path))+'</code> from the tool on that machine</div></div>';
}
const A11Y_LIST_KEYS=/^(Tab|Enter| |Arrow(Up|Down|Left|Right)|Home|End)$/;   // the keys #a11ymap answers: ahead of the 3D view, which never swallows them (Tab keeps its own way out)
addEventListener('keydown',e=>{ if(A11Y_LIST_KEYS.test(String(e.key||''))&&e.target&&e.target.closest&&e.target.closest('#a11ymap')){ e.stopImmediatePropagation(); if(e.key!=='Tab'&&typeof a11yKeys==='function') a11yKeys(e); } },true);
addEventListener('keydown',e=>{ if(!typing()&&!modKey(e)&&CM.key3d(e)){ e.preventDefault(); e.stopImmediatePropagation(); } },true);
addEventListener('keydown',e=>{
  const k=e.key.toLowerCase();
  if(typing()){ if(e.key==='Escape'&&!(e.target.closest&&e.target.closest('#pterm'))) e.target.blur(); return; }   // Esc in the live terminal is the program's
  if(modKey(e)) return;
  if(e.key==='Escape'&&CM.backLayer('esc')) return;
  if(k==='n'&&!e.repeat&&!composerOpen()){ e.preventDefault(); openComposer(VIEW==='map'?(hover&&hover!==BOARD?hover:(near&&near!==BOARD?near:null)):null); return; }
  if(VIEW==='today'){ if((k==='m'||k==='t')&&!e.repeat){ e.preventDefault(); setView('map'); } return; }   // T toggles Today, M still returns
  if(k==='k'&&!e.repeat){ toggleTasks(); return; }
  if(k==='t'&&!e.repeat){ setView('today'); return; }
  if(k==='shift') keys.shift=1;
  if(CM.mode3d()&&['q','e','c'].includes(k)){ humanMove(); pilot=null; keys[k]=1; FL.kb=true; if(!e.repeat) flightTap(k); return; }   // 3D: tilt and brake
  if(['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright'].includes(k)){
    humanMove(); pilot=null; keys[k]=1; FL.kb=true; if(!e.repeat) flightTap(k);   // the keyboard flies the ship (FLIGHT)
  }
  if(e.key==='Escape'){
    const panelOpen = openUnit || (document.getElementById('quests')&&document.getElementById('quests').classList.contains('on'))
      || (document.getElementById('legend')&&document.getElementById('legend').classList.contains('on'))
      || (document.getElementById('meta')&&document.getElementById('meta').classList.contains('on'))
      || (document.getElementById('help')&&document.getElementById('help').classList.contains('on'));
    if(panelOpen) closeAll();
    else if(deck!=='home') goHome();
    else closeAll();
  }
  if(!e.repeat && (e.key==='Home' || e.key==='Backspace' || (k==='h' && e.shiftKey))){ e.preventDefault(); goHome(); return; }
  if((k==='e'||(k==='enter'&&!activates(document.activeElement))) && near) activate(near);
  if(k==='q'&&!e.repeat) toggleQuests();
  if(k==='p'&&!e.repeat){ const t=hover||near; if(t&&t!==BOARD&&t.kind!=='task') openPrompter(t); }
  if(k==='c'&&!e.repeat){ camOff.x=0; camOff.y=0; }
  if(k==='h'&&!e.repeat){ setQuiet(!QUIET); }
  if(k==='l'&&!e.repeat){ const L=document.getElementById('legend'); if(L){ L.classList.toggle('on'); if(L.classList.contains('on')) L.innerHTML=systemLegend(); } }
  if(['1','3','4','6'].includes(k)&&!e.repeat){
    const u=units.find(x=>x.kind==='seat'&&x.quick_key===k); if(u){humanMove();show(u);}
  }
  if([' ','ArrowUp','ArrowDown','ArrowLeft','ArrowRight'].includes(e.key)&&!(e.key===' '&&activates(document.activeElement))) e.preventDefault(); });
addEventListener('keyup',e=>{ delete keys[e.key.toLowerCase()]; });
addEventListener('blur',clearKeys);
document.addEventListener('click',e=>{ const b=e.target&&e.target.closest&&e.target.closest('#gohome'); if(b){ e.preventDefault(); goHome(); } });
(function(){ const hb=document.getElementById('gohome'); if(hb) hb.hidden=(deck==='home'); })(); addEventListener('visibilitychange',()=>{if(document.hidden)clearKeys();});

const COL = {WAITING:'#e3b341', blocked:'#e3b341', DRAFT:'#d2a8ff', LIVE:'#3fb950', WORKING:'#3fb950', IDLE:'#58a6ff', COLD:'#6e7681',
  WARM:'#58a6ff', EMPTY:'#6e7681', ALIAS:'#6e7681', UNWALKED:'#8b98a8',
  BLANK:'#6e7681', LIMITED:'#f85149', ERROR:'#f85149', DOWN:'#f85149',
  BROKEN:'#f85149', RETIRING:'#d29922', FROZEN:'#58a6ff', DEAD:'#f85149',
  idle:'#58a6ff', working:'#3fb950', blocked:'#d29922', done:'#3fb950', unknown:'#6e7681'};
const isDead = s => ['DOWN','BROKEN','DEAD','ERROR'].includes(s);

// ---- palette: deep-space (default) or a 4-shade mono green (session toggle) ----
let GB = false;
try{ GB = sessionStorage.getItem('theme')==='gb'; }catch(e){}
function toggleGB(){ GB=!GB; try{ sessionStorage.setItem('theme', GB?'gb':'space'); }catch(e){} }
const GBC = {WAITING:'#0f380f',DRAFT:'#0f380f',LIVE:'#0f380f',WORKING:'#0f380f',IDLE:'#306230',COLD:'#306230',
  BLANK:'#306230',LIMITED:'#0f380f',ERROR:'#0f380f',DOWN:'#0f380f',BROKEN:'#0f380f',RETIRING:'#0f380f',
  FROZEN:'#306230',DEAD:'#0f380f',blocked:'#0f380f',idle:'#306230',working:'#0f380f',done:'#0f380f',unknown:'#306230'};
const T = {
  get bg(){ return GB?'#9bbc0f':'#04060c'; },
  get floor(){ return GB?'#8bac0f':'#0b1220'; },
  get grid(){ return GB?'#306230':'#1c2430'; },
  get edge(){ return GB?'#0f380f':'#2b3a52'; },
  get dim(){ return GB?'#306230':'#394454'; },
  get label(){ return GB?'#0f380f':'#7d8ea6'; },
  get ink(){ return GB?'#0f380f':'#04060c'; },
  get paper(){ return GB?'#0f380f':'#e6edf3'; },
  get plate(){ return GB?'rgba(15,56,15,.85)':'rgba(4,6,12,.8)'; },
  get platetx(){ return GB?'#9bbc0f':'#8b98a8'; },
  get near(){ return GB?'#ffffff':'#e6edf3'; },
  get heat(){ return GB?'#0f380f':'#3fb950'; },
  get track(){ return GB?'#8bac0f':'#151b24'; },
};
applyTheme(themeStart(),false);

// The player is the skin's pilot craft. Its letter grid 'pilot' turns to 16 headings by the same engine as every
// craft. A crew member's pilot craft wears its status colour on the accent letters (b B). Shift swaps in
// 'pilot_boost' (the same craft with a longer flare; the pilot grid when the skin has none) and leaves boost rings.
const PILOT_CELL=3, CREW_CELL=2, CARRIER_DIR=Math.PI/2;   // the carrier flies nose right, as it always sat
// zoomed out a craft grows a little so it stays identifiable, but only down to CRAFT_ZMIN: past that it
// shrinks with the world, so the star and the planets stay bigger than the craft at every zoom. Its world
// position never changes.
const CRAFT_ZMIN=.65;
function craftScale(base){ const k=Math.max(1,Math.round(Math.min(base/ZOOM,base/CRAFT_ZMIN)*2)/2); return ZOOM>1?k/Math.pow(ZOOM,.75):k; }   // zoomed in past 1 (a planet's focus) a craft grows slowly, never with the world
// a crew member's craft: the same one, smaller, in the seat's status colour, engines lit while it works
function drawCrew(x,y,angle,col,working,t){
  const turn=angle+Math.PI/2, k=craftScale(.75), flick=REDUCED?0:Math.floor(t/70)%2, own=liveryOn();
  cx.save(); cx.translate(Math.round(x/2)*2,Math.round(y/2)*2); cx.scale(k,k);
  if(own) fxHalo(0,0,56,col,.26);
  if(working) fxTrail(0,0,turn,t,24,2,[-3,3],own?FX_OWN:null);
  drawSprite('pilot',0,0,turn,friendPal(col,working?(own?FX_OWN[flick?1:2]:(flick?'#ffd34d':'#ff6a2e')):''),CREW_CELL);
  cx.restore();
}
// the carrier: the biggest craft on the map at every zoom (craftScale, like the pilot craft), nose right; and never smaller
// than half the biggest planet across (its hull's circle as wide as that planet's radius), so on a map of big folders it
// reads as the second thing after the star and the big planets, above the small planets and the crew (the 10-10 size
// rule): a world size, so the order holds at every zoom. Quarter steps; at least the craft scale.
const CARRIER_BASE=.8;
function carrierScale(){
  const R=MOONS.reduce((m,p)=>Math.max(m,p.r||0),0), hull=craftHull('carrier',SPRITE_CELL,1);
  return Math.max(craftScale(CARRIER_BASE),hull>0?Math.floor(R/(2*hull)*4)/4:1); }
function drawCarrier(x,y,t,working){
  const flick=REDUCED?0:Math.floor(t/110)%2, by=y, k=carrierScale(), own=liveryOn();
  cx.save(); cx.translate(Math.round(x/2)*2,Math.round(by/2)*2); cx.scale(k,k);
  const noz=craftNozzles('carrier'), tail=noz.length?Math.max(...noz.map(n=>n[1])):60, offs=noz.length?noz.map(n=>n[0]):[-31,0,31];
  if(own){ fxHalo(0,0,100,'#9fd0ff',.26); if(working) offs.forEach(o=>fxHalo(-tail,o,14,'#ff9a2e',.55)); }   // the halo, then each exhaust's glow (nose right)
  if(working) fxTrail(0,0,CARRIER_DIR,t,tail,3,offs);   // every exhaust the grid draws
  drawSprite('carrier',0,0,CARRIER_DIR,friendPal('',working?(flick?'#ffd34d':'#ff6a2e'):'','#5ff2ff'));
  fxRunningLights(0,0,t,own);
  cx.restore();
}
function mothership(){ return units.find(u=>u.kind==='seat'&&!u.remote&&(u.name===String((W.wingmate&&W.wingmate.mothership)||'hub')))||null; }
function launchPoint(){ const m=mothership(); return m?{x:m.gx*TILE,y:m.gy*TILE}:{x:LY.star.x,y:LY.star.y}; }
// the player's craft. In the pixel look it banks, rolls and loops (pilotPose), wears the skin's livery, and its engine
// says what it does: a magenta core parked, a magenta flame under thrust, a long cyan-white flame on boost, a pink puff braking
function drawPilot(x,y,angle,power,t){
  const turn=angle+Math.PI/2, k=craftScale(.9), burn=power>.02, boosting=burn&&boostOn, flick=REDUCED?0:Math.floor(t/70)%2, own=liveryOn();
  fxBoostRings(x,y,angle,power,t);
  if(VEC){ vecCraft(boosting&&SPRITES.pilot_boost?'pilot_boost':'pilot',x,y,turn,VC.text,'#18221e',PILOT_CELL,k); return; }   // the player: off-white
  cx.save(); cx.translate(Math.round(x/2)*2,Math.round(y/2)*2); cx.scale(k,k);
  const name=boosting&&SPRITES.pilot_boost?'pilot_boost':'pilot', P=VEC?POSE_FLAT:pilotPose(t), brake=FL.brake&&flightOn();
  if(!own){ if(burn) fxTrail(0,0,turn,t,boosting?50:36,3,[-5,0,5]);
    drawPose(name,0,0,turn,pilotPal('',burn?(flick?'#ffd34d':'#ff6a2e'):''),PILOT_CELL,P); cx.restore(); return; }
  fxHalo(0,0,60*P.grow,'#cfe6ff',.22);
  fxFlame(0,0,turn,30*P.lon*P.grow,(boosting?30:brake?5:burn?11:0)*P.lon,boosting?FX_BOOST:brake?FX_BRAKE:FX_OWN,t);
  if(burn&&!brake) fxTrail(0,0,turn,t,boosting?50:36,3,[-5,0,5],boosting?FX_BOOST:FX_OWN);
  drawPose(name,0,0,turn,friendPal('',boosting?(flick?'#ffffff':'#00e5ff'):brake?'#ff9ad5':burn?FX_OWN[flick?1:2]:FX_OWN[3]),PILOT_CELL,P);
  if(FL.roll&&!REDUCED) fxRollCage(0,0,turn,P,t);
  cx.restore();
}
// ---- OWN CRAFT: the owner's own craft (the pilot, the crew of seats, the carrier) read as the user's at a glance, and the
// pilot flies on the keyboard like the classic flight game's fighter. In the pixel look (not VEC, not mono, a theme
// without tiles) they wear the skin's livery over the theme's craft palette, sit in a soft additive halo, and each
// seat carries its letter tag; the flight model and the radio below work in every theme. ----
const FX_OWN=['#ffd6ea','#ff55bb','#ff2a85','#a8205c'];   // the friendly engine: hot magenta (the last one is the parked core)
const FX_BOOST=['#ffffff','#c8fbff','#00e5ff','#1a6a8a'];   // boost: cyan to white
const FX_BRAKE=['#ffd6ea','#ff9ad5'];                       // braking: a pink puff
function liveryOn(){ return !GB&&!VEC&&!!SKIN.livery&&!Object.keys(TILES).length; }
// a friendly craft's palette: the pack's craft palette, the skin's livery over it (liveryOn), and a seat's status colour
// on the accent letters (b B) so the old meanings stay: green working, blue idle, amber needs you, purple draft, red dead
function friendPal(tint,engine,accent){
  const p=hullPal(accent||'',engine||''); if(liveryOn()) Object.assign(p,SKIN.livery);
  if(tint&&!GB){ p.b=tint; p.B=mixPixel(tint,'#ffffff',.55); p.v=mixPixel(tint,'#000000',.4); }
  return p;
}
// a problem raider's palette: where the friendly craft wear the livery, its light hull goes gunmetal, so friend and foe
// never share a white hull (the crimson plating and the flickering eye stay)
const FOE_HULL={h:'#9aa1b0',m:'#5b6170',w:'#383c46'};
function foePal(accent,engine){ return Object.assign(hullPal(accent,engine),liveryOn()?FOE_HULL:{}); }
// a soft additive glow, r in the units it is drawn in (off in the mono palette and the vector look; scaled by the pack's glow)
function fxHalo(x,y,r,col,a){
  if(GB||VEC) return; a*=GLOWK; if(a<=.004||!/^#[0-9a-f]{6}$/i.test(String(col))) return;
  const g=cx.createRadialGradient(x,y,0,x,y,r); g.addColorStop(0,col+hex2(a)); g.addColorStop(.5,col+hex2(a*.45)); g.addColorStop(1,col+'00');
  cx.save(); cx.globalCompositeOperation='lighter'; cx.fillStyle=g; cx.beginPath(); cx.arc(x,y,r,0,TAU); cx.fill(); cx.restore();
}
// the engine flame: squares stepping back from the nozzle (back units behind the centre), white-hot to the colour's edge
function fxFlame(x,y,ang,back,len,cols,t){
  if(!(len>0)) return;
  const sa=Math.sin(ang), ca=Math.cos(ang), j=REDUCED?0:(Math.floor(t/50)%3)*2-2, L=Math.max(2,len+j);
  cx.save(); if(!GB) cx.globalCompositeOperation='lighter';
  for(let d=0;d<L;d+=2){ const u=d/L, w=Math.max(2,Math.round((1-u)*7/2)*2);
    cx.globalAlpha=GB?1:.95-.75*u; cx.fillStyle=GB?'#306230':cols[Math.min(cols.length-1,Math.floor(u*cols.length))];
    cx.fillRect(fxSnap(x-sa*(back+d)-w/2),fxSnap(y+ca*(back+d)-w/2),w,w); }
  cx.restore();
}
// The pose a frame draws the pilot in: lat scales the craft across (the bank and the barrel roll: cos of the roll angle,
// so 1 -> -1 -> 1 in a roll), lon along it (the half loop of a U-turn), grow is how much nearer the camera it is, lit the
// side of the raised wing. Reduced motion: always flat.
const POSE_FLAT={lat:1,lon:1,grow:1,lit:0,spin:0};
function pilotPose(t){
  if(REDUCED||!flightOn()) return POSE_FLAT;
  let r=FL.bank, spin=0;
  if(FL.roll){ spin=clamp((t-FL.roll.t0)/FLY.rollT,0,1); r+=FL.roll.dir*spin*TAU; }
  let lat=Math.cos(r), lon=1, grow=1;
  if(Math.abs(lat)<.08) lat=lat<0?-.08:.08;
  if(FL.uturn){ const u=clamp((t-FL.uturn.t0)/FLY.uturnT,0,1); lon=Math.max(.1,Math.abs(Math.cos(Math.PI*u))); grow=1+.25*Math.sin(Math.PI*u); }
  return {lat:lat,lon:lon,grow:grow,lit:Math.sin(r),spin:spin};
}
// a craft in a pose: the turned grid scaled across and along its own axes (rotate to the heading, scale, rotate back),
// pixels kept crisp (no smoothing); the raised wing drawn a second time, additively, so it catches the light
function drawPose(name,x,y,angle,pal,cell,P){
  if(P.lat===1&&P.lon===1&&P.grow===1){ drawSprite(name,x,y,angle,pal,cell); return; }
  const n=16, dir=((Math.round(angle/(TAU/n))%n)+n)%n, art=spriteArt(name,pal,dir,n,cell), a=dir*TAU/n, w=art.width, h=art.height;
  cx.save(); cx.translate(Math.round(x/2)*2,Math.round(y/2)*2); cx.rotate(a); cx.scale(P.lat*P.grow,P.lon*P.grow);
  cx.rotate(-a); cx.drawImage(art,-w/2,-h/2); cx.rotate(a);
  if(!GB&&Math.abs(P.lit)>.15){ cx.beginPath(); cx.rect(P.lit>0?-w:0,-h,w,2*h); cx.clip(); cx.rotate(-a);
    cx.globalCompositeOperation='lighter'; cx.globalAlpha=Math.min(.5,Math.abs(P.lit)*.5); cx.drawImage(art,-w/2,-h/2); }
  cx.restore();
}
// the roll's wake (the public look): two thin corkscrew trails off the wingtips, pale blue and white, fading behind
// the craft; a skin's fx2d.roll "deflector" draws its hexagon cage instead
function fxRollCage(x,y,ang,P,t){ return (SKIN&&SKIN.fx2d&&SKIN.fx2d.roll==='deflector') ? fxRollDeflector(x,y,ang,P,t) : fxRollWake(x,y,ang,P,t); }
function fxRollWake(x,y,ang,P,t){
  const ca=Math.cos(ang), sa=Math.sin(ang), turnAt=P.spin*TAU*1.5+t/90, fade=1-.5*Math.abs(P.spin-.5), steps=18;
  cx.save(); if(!GB) cx.globalCompositeOperation='lighter'; cx.lineWidth=1.6; cx.lineCap='round';
  [0,Math.PI].forEach((off,j)=>{ let x0=null, y0=null;
    for(let i=0;i<=steps;i++){ const along=6+i*3.2, th=turnAt+off-i*.42, across=Math.cos(th)*30;   // local +y is behind the nose
      const sx=x+ca*across-sa*along, sy=y+sa*across+ca*along;
      if(x0!==null){ cx.globalAlpha=GB?.8:(1-i/steps)*.7*fade*(Math.sin(th)>0?1:.45); cx.strokeStyle=GB?'#306230':(j?'#ffffff':'#9fdcff');
        cx.beginPath(); cx.moveTo(x0,y0); cx.lineTo(sx,sy); cx.stroke(); }
      x0=sx; y0=sy; } });
  cx.restore();
}
// a skin's deflector: hexagon sparks on a cage that spins round the craft as it rolls
function fxRollDeflector(x,y,ang,P,t){
  const n=16, ca=Math.cos(ang), sa=Math.sin(ang), turnAt=P.spin*TAU*1.5+t/90, fade=1-.5*Math.abs(P.spin-.5);
  cx.save(); if(!GB) cx.globalCompositeOperation='lighter';
  [-14,14].forEach((along,j)=>{ cx.globalAlpha=GB?.8:.35*fade; cx.strokeStyle=GB?'#306230':(j?'#ffff66':'#80ff80'); cx.lineWidth=2;   // the cage's two hoops
    cx.beginPath(); cx.ellipse(x-sa*along,y+ca*along,38,9,ang,0,TAU); cx.stroke(); });
  for(let i=0;i<n;i++){ const strand=i%2, th=turnAt+i*TAU/n+strand*Math.PI, along=((i>>1)/(n/2-1)-.5)*48, across=Math.cos(th)*38, depth=Math.sin(th);
    const sx=x+ca*across-sa*along, sy=y+sa*across+ca*along, r=depth>0?5:3;   // two helical strands of sparks, near ones big and bright
    cx.globalAlpha=GB?1:(depth>0?.95:.45)*fade; cx.fillStyle=GB?'#0f380f':(i%4===0?'#ffffff':strand?'#80ff80':'#ffff66');
    cx.beginPath(); for(let k=0;k<6;k++){ const q=k*TAU/6; cx.lineTo(sx+Math.cos(q)*r,sy+Math.sin(q)*r); } cx.closePath(); cx.fill(); }
  cx.restore();
}
// ---- FLIGHT: the keyboard flies the pilot with momentum. W/Up thrusts along the heading, A/D or Left/Right turn
// it (a turn rate, not a strafe), S/Down brakes hard (and tightens a turn to twice the rate); no key coasts to a stop
// with drag, so the ship can still park over a planet. Shift boosts from a meter (about 2 s of burn, 4.5 s to refill,
// 1.7x the speed, a turning circle 1.8x wider); the brake draws from the same meter. The craft banks into a turn (35
// degrees, 80 in a brake turn) and springs level when the key is let go. Double-tap left or right: a barrel roll;
// double-tap down: a U-turn (a half loop onto the reverse heading). The touch pad, click-to-fly and the agent pilot
// move the ship as they always have. ----
const FLY={acc:900,decel:700,drag:2.2,dragK:60,brake:1500,turn:2.4,boostK:1.7,boostAcc:2600,boostTurn:1.7/1.8,brakeTurn:2,
  burn:2,refill:4.5,bankSoft:35,bankHard:80,bankW:15.6,tap:250,rollT:400,rollShift:36,uturnT:1200};
const FL={kb:false,spd:0,hdg:-Math.PI/2,bank:0,bankV:0,turn:0,lastTurn:1,hard:false,meter:1,spent:false,boost:false,brake:false,power:0,roll:null,uturn:null,tap:{},locked:false,drew:false,
  tilt:0,nose:0,rate:0,homing:0};
// k, the flight's tuning: the 3D view flies this same flightStep, fleetTick and flightTap with its own
// numbers (p3Tune: the skin's feel3d); without k (2D, DRIVER_FLY) every value is FLY's, so the 2D flight never changes.
// speed and turn multiply SPEED and FLY.turn; burn/refill/lockout run the meter (with lockout, letting go of
// Shift or S after drawing locks both until it is full); boostUp/boostDown (s) wind the boost up and down (0: as in 2D);
// cruise/brakeSpd are the 3D view's floors (the ship always flies there).
const FLK2D={speed:1,turn:1,boostK:FLY.boostK,brakeTurn:FLY.brakeTurn,burn:FLY.burn,refill:FLY.refill,lockout:false,boostUp:0,boostDown:0,
  bankSoft:FLY.bankSoft,bankHard:FLY.bankHard,rollT:FLY.rollT,uturnT:FLY.uturnT,cruise:P3_CRUISE,brakeSpd:P3_BRAKE};
function flyK(){ if(!CM.mode3d()) return FLK2D; const sk=window.CM3D&&CM3D.skin3d; if(!P3.k||P3.kSkin!==sk){ P3.k=p3Tune(); P3.kSkin=sk; } return P3.k; }
function flightOn(){ return FL.kb&&!pilot; }
function headingNorm(h){ return Math.atan2(Math.sin(h),Math.cos(h)); }
// a critically damped spring, solved exactly over dt (stable at any frame rate): [position, velocity]
function springTo(x,v,target,w,dt){ const c1=x-target, c2=v+w*c1, e=Math.exp(-w*dt); return [target+(c1+c2*dt)*e,(c2-w*(c1+c2*dt))*e]; }
function flightStep(dt,quiet,k){
  k=k||FLK2D;
  const K=quiet?{}:keys, thr=!!(K.w||K.arrowup), brk=!!(K.s||K.arrowdown), turn=((K.d||K.arrowright)?1:0)-((K.a||K.arrowleft)?1:0);
  FL.turn=FL.uturn?0:turn; if(turn) FL.lastTurn=turn;
  if(FL.uturn){ FL.boost=FL.brake=FL.hard=false; FL.power=1; return; }   // the loop flies itself (fleetTick)
  if(CM.mode3d()&&P3.flight){ flightStep3d(dt,K,k,turn); return; }
  if(!K.shift) FL.spent=false;
  if(FL.locked&&FL.meter>=1) FL.locked=false;
  FL.boost=!!K.shift&&(thr||FL.spd>1||(CM.mode3d()&&P3.flight))&&!FL.spent&&!FL.locked&&FL.meter>0;
  FL.brake=brk&&!FL.boost&&!FL.locked;
  const burning=FL.boost||(FL.brake&&(FL.spd>1||turn!==0));
  if(burning){ if(FL.meter>0){ FL.drew=true; FL.meter=Math.max(0,FL.meter-dt/k.burn); if(FL.meter<=0&&FL.boost) FL.spent=true; } }
  else FL.meter=Math.min(1,FL.meter+dt/k.refill);
  if(FL.drew&&!K.shift&&!brk){ FL.drew=false; if(k.lockout&&FL.meter<1) FL.locked=true; }   // Boost waits for the meter to refill
  FL.hard=FL.brake&&FL.meter>0;
  const cruise=SPEED*k.speed, top=cruise*(FL.boost?k.boostK:1), drag=FLY.dragK+FL.spd*FLY.drag;
  if(FL.boost) FL.spd=Math.min(top,FL.spd+(k.boostUp>0?cruise*(k.boostK-1)/k.boostUp:FLY.boostAcc)*dt);
  else if(FL.brake) FL.spd=Math.max(0,FL.spd-(FL.hard?FLY.brake:drag)*dt);
  else if(k.boostDown>0&&FL.spd>top) FL.spd=Math.max(top,FL.spd-cruise*(k.boostK-1)/k.boostDown*dt);
  else if(thr) FL.spd=FL.spd>top?Math.max(top,FL.spd-FLY.decel*dt):Math.min(top,FL.spd+FLY.acc*dt);
  else FL.spd=Math.max(0,FL.spd-drag*dt);
  FL.hdg=headingNorm(FL.hdg+turn*FLY.turn*k.turn*(FL.boost?FLY.boostTurn:1)*(FL.hard?k.brakeTurn:1)*dt);
  px+=Math.cos(FL.hdg)*FL.spd*dt; py+=Math.sin(FL.hdg)*FL.spd*dt;
  FL.power=(thr||FL.boost)?1:0;
}
// ---- the 3D view's flight (flight-rs's handling). The ship always flies forward along its nose,
// never parks and never slides. A/D (the stick's sides) turn at the skin's turn rate; Q/E tilt the wings hard over: with
// A/D on the same side a tight turn (turnHard), alone a slow one (half the turn rate), a double tap a barrel roll. W/S or
// Up/Down (the stick up and down) set the nose's attitude, not a rate: held it eases to pitch degrees, let go it levels
// itself; W dives (pilot style) unless Settings says it climbs (R/F climb and dive either way). Shift boosts and C brakes
// from one meter: each burns it (burn s from full), it refills off them (refill s); with the skin's lockout an empty
// meter locks both until it is full again (letting go early leaves it free). The speed winds to its target over the boost
// span in boostUp s and back over boostDown. In the last stretch before the map's edge the ship turns hard back in
// (flight-rs's arena edge, for a rectangle); the physics' bounds stay the wall behind that.
function p3PitchDives(){ if(P3.dives==null) P3.dives=p3Get('pitchKeys','dive')!=='climb'; return P3.dives; }
function p3Smooth(x,t,s,cap,dt){ const n=dt*30, d=(t-x)*(1-Math.pow(1-s,n)), c=cap*n; return x+clamp(d,-c,c); }   // flight-rs's smooth_step: s a 30 Hz tick, cap a tick
function flightStep3d(dt,K,k,sx){
  const tilt=(K.e?1:0)-(K.q?1:0), sy=((K.w||K.arrowup)?1:0)-((K.s||K.arrowdown)?1:0);
  const wantB=!!K.shift&&!K.c, wantK=!!K.c&&!K.shift;
  if(!wantB&&!wantK) FL.spent=false;
  const using=(wantB||wantK)&&!FL.locked&&!FL.spent&&FL.meter>0;
  if(using){ FL.drew=true; FL.meter=Math.max(0,FL.meter-dt/k.burn); if(FL.meter<=0){ if(k.lockout) FL.locked=true; else FL.spent=true; } }
  else { FL.meter=Math.min(1,FL.meter+dt/k.refill); if(FL.meter>=1){ FL.locked=false; FL.drew=false; } }
  FL.boost=using&&wantB; FL.brake=using&&wantK; FL.tilt=tilt;
  FL.hard=tilt!==0&&(sx===0||tilt===sx);
  const target=FL.boost?k.cruise*k.boostK:FL.brake?k.brakeSpd:k.cruise, span=Math.max(1,k.cruise*(k.boostK-1));
  const rate=target>FL.spd?span/(k.boostUp||.5):span/(k.boostDown||1);
  FL.spd=FL.spd<target?Math.min(target,FL.spd+rate*dt):Math.max(target,FL.spd-rate*dt);
  let turn=FL.hard?(sx?sx*k.turnHardDeg:tilt*k.turnDeg*.5):sx*k.turnDeg;
  // the edge: inside its last stretch (m) with the nose less than 30 degrees into the map, a hard turn back in, toward the
  // side the middle is on when the nose points straight out (FL.homing: the edge has the stick)
  const x0=minGX*TILE, x1=maxGX*TILE, y0=minGY*TILE, y1=maxGY*TILE, m=clamp(.15*Math.min(x1-x0,y1-y0),150,450);
  let nx=(px<x0+m?1:0)-(px>x1-m?1:0), ny=(py<y0+m?1:0)-(py>y1-m?1:0); FL.homing=0;
  if(nx||ny){
    const n=Math.hypot(nx,ny), fx=Math.cos(FL.hdg), fy=Math.sin(FL.hdg); nx/=n; ny/=n;
    if(fx*nx+fy*ny<.5){
      let side=fx*ny-fy*nx;
      if(Math.abs(side)<.05) side=fx*((y0+y1)/2-py)-fy*((x0+x1)/2-px);
      turn=side>=0?k.turnHardDeg:-k.turnHardDeg; FL.homing=1;
    }
  }
  FL.rate=turn; FL.hdg=headingNorm(FL.hdg+turn*Math.PI/180*dt);
  FL.nose=p3PitchDives()?-sy:sy; FL.power=1;
}
// pressed against a body (f: the physics after its step) the nose swings to the way the ship slides (straight in: to the
// right) at the tight turn's rate twice over, so it glances off instead of crawling round the sphere with its nose in it
// (the flight never stops). The edge's turn (flightStep3d, FL.homing) has the stick first: against a body that reaches
// the map's wall the two would cancel and pin the ship there.
function p3Glance(f,dt,k){
  if(!f.contact||FL.homing) return;
  const vh=Math.hypot(f.vx,f.vz), want=vh>FL.spd*.05?Math.atan2(f.vz,f.vx):FL.hdg+Math.PI/2, most=2*k.turnHardDeg*Math.PI/180*dt;
  FL.hdg=headingNorm(FL.hdg+clamp(headingNorm(want-FL.hdg),-most,most));
}
// every frame, whoever flies: the bank spring, the roll's sideways slip, the U-turn's path; the meter refills off the keyboard
function fleetTick(t,dt){
  const on=flightOn(), k=flyK();
  if(!on){ FL.spd=0; FL.turn=0; FL.tilt=0; FL.rate=0; FL.nose=0; FL.boost=FL.brake=FL.hard=false; FL.power=0; FL.roll=null; FL.uturn=null; FL.meter=Math.min(1,FL.meter+dt/k.refill); }
  const fly3=CM.mode3d()&&!!P3.flight;
  // 3D: Q/E (or the edge's hard turn) hold the wings at bankHard whatever the stick says, else the stick's side at
  // bankSoft (flight-rs: s .2, the full bank in about half a second); 2D: the spring into the turn
  const target=fly3?(FL.tilt?FL.tilt*k.bankHard:FL.homing?Math.sign(FL.rate)*k.bankHard:FL.turn*k.bankSoft)*Math.PI/180:FL.turn?FL.turn*(FL.hard?k.bankHard:k.bankSoft)*Math.PI/180:0;
  if(REDUCED){ FL.bank=target; FL.bankV=0; }
  else if(fly3){ FL.bank=p3Smooth(FL.bank,target,.2,k.bankHard*Math.PI/180/(.53*30),dt); FL.bankV=0; }
  else { const s=springTo(FL.bank,FL.bankV,target,FLY.bankW,dt); FL.bank=Math.abs(s[0])<1e-4&&!target?0:s[0]; FL.bankV=s[1]; }
  if(FL.roll){ const R=FL.roll, u=(t-R.t0)/k.rollT;   // a frame stamped before the tap's own time (u<0) waits, never ends the roll
    if(u>=1) FL.roll=null;
    else if(u>0){ const v=FLY.rollShift*Math.PI/2*Math.sin(Math.PI*u)/(k.rollT/1000); px+=R.lx*v*dt; py+=R.ly*v*dt; } }
  if(FL.uturn){ const U=FL.uturn, u=clamp((t-U.t0)/k.uturnT,0,1), fx=Math.cos(U.h0), fy=Math.sin(U.h0);
    const F=U.R*Math.sin(Math.PI*u), S=U.side*U.R*.45*(1-Math.cos(Math.PI*u))/2;   // out along the old heading and back: a tight teardrop
    px=U.x+fx*F-fy*S; py=U.y+fy*F+fx*S; FL.hdg=headingNorm(u<.5?U.h0:U.h0+Math.PI);
    if(u>=1){ FL.uturn=null; FL.spd=U.v; } }
}
// a second press of left, right or down within FLY.tap ms: the barrel roll, or the U-turn
function flightTap(k){
  if(CM.mode3d()&&P3.flight){   // 3D: only a double tap of Q or E (within 0.3 s) rolls, on the spot (the roll is the look)
    const d=k==='q'?-1:k==='e'?1:0; if(!d) return;
    const now=performance.now(), key=d<0?'Q':'E', last=FL.tap[key]; FL.tap[key]=now;
    if(last==null||now-last>300||FL.roll||FL.uturn||REDUCED) return;
    FL.tap[key]=null; FL.roll={t0:now,dir:d,lx:0,ly:0}; return;
  }
  const g=(k==='a'||k==='arrowleft')?'L':(k==='d'||k==='arrowright')?'R':(k==='s'||k==='arrowdown')?'S':''; if(!g) return;
  const now=performance.now(), last=FL.tap[g]; FL.tap[g]=now;
  if(last==null||now-last>FLY.tap||FL.roll||FL.uturn) return;
  FL.tap[g]=null;
  if(g==='S'){ const v=Math.max(FL.spd,SPEED*.35);
    if(REDUCED){ FL.hdg=headingNorm(FL.hdg+Math.PI); return; }
    FL.uturn={t0:now,h0:FL.hdg,x:px,y:py,v:v,R:v*(flyK().uturnT/1000)/Math.PI,side:FL.lastTurn||1}; return; }
  const dir=g==='L'?-1:1, lx=-Math.sin(FL.hdg)*dir, ly=Math.cos(FL.hdg)*dir;   // the side of the heading the tap names
  if(REDUCED){ px+=lx*FLY.rollShift; py+=ly*FLY.rollShift; return; }
  FL.roll={t0:now,dir:dir,lx:lx,ly:ly};
}
// what a test, or a curious console, reads back: speed in px/s, heading and bank in degrees, the meter 0-1
window.__fleetAudit=function(){
  const deg=r=>Math.round(r*1800/Math.PI)/10;
  return {speed:Math.round(FL.spd*10)/10,heading:(deg(FL.hdg)+360)%360,bank:deg(FL.bank),meter:Math.round(FL.meter*1000)/1000,
    rolling:!!FL.roll,uturning:!!FL.uturn,boosting:FL.boost,braking:FL.brake,keyboard:flightOn(),x:px,y:py,locked:!!FL.locked,uturnR:FL.uturn?Math.round(FL.uturn.R*10)/10:null};
};
// the boost meter: a thin bar under the pilot, only while it burns or refills (screen pixels, inside the craft's hull circle)
function drawFleetMeter(){
  if(!flightOn()||(FL.meter>=1&&!FL.boost&&!FL.brake)) return;
  const pr=craftHull('pilot',PILOT_CELL,craftScale(.9))*ZOOM, x=Math.round((px-camx)*ZOOM)-16, y=Math.round((py-6-camy)*ZOOM+pr*.78), w=32;
  const low=FL.meter<.25, blink=FL.spent&&!REDUCED&&Math.floor(FRAME_T/160)%2;
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0);
  cx.fillStyle=GB?'#0f380f':'#04060c'; cx.fillRect(x-1,y-1,w+2,6);
  cx.fillStyle=GB?'#8bac0f':'#1a2238'; cx.fillRect(x,y,w,4);
  cx.fillStyle=GB?'#306230':(blink?'#7a1a24':low?'#ff4d5e':FL.boost?'#7ff4ff':'#ffd23f'); cx.fillRect(x,y,Math.round(w*FL.meter),4);
  cx.restore();
}
// ---- WING TAGS: each seat's letter over its craft, a down-pointing tag in the seat's status colour with a white letter,
// the same size on screen at every zoom (the player's own craft has none). sprite() queues one per seat. The plate pass
// sets them first: one over each craft, or, where a dock's tags would overlap, one row over the dock in the order its
// craft sit left to right; each tag's disc is an obstacle the plates keep off (a folded dock's tags go into its fold).
// After the plates are laid out a tag (or row) that a plate still covers moves beside or above; it never sits on a
// planet's tag or another seat's tag (it is left out instead). Tags paint under the plates. ----
const WING={w:16,w2:24,h:15,top:3,gap:3};
let WING_Q=[], WING_LETTERS={key:'',map:{}};
// the letter of each seat: its first; two when two seats share the first (the first two, else the first and the last)
function wingLetters(){
  const seats=units.filter(u=>u.kind==='seat'&&!u.remote), key=seats.map(u=>u.uid+'='+(u.alias||u.name)).join('|');
  if(key===WING_LETTERS.key) return WING_LETTERS.map;
  const word=u=>String(u.alias||u.name||'').toUpperCase().replace(/[^A-Z0-9]/g,'')||'?', first={}, map={}, taken={};
  seats.forEach(u=>{ const c=word(u)[0]; first[c]=(first[c]||0)+1; });
  seats.forEach((u,i)=>{ const w=word(u); let s=w[0];
    if(first[s]>1){ s=[w.slice(0,2),w[0]+w[w.length-1],w[0]+String(i%10)].find(c=>c.length===2&&!taken[c])||w.slice(0,2); }
    taken[s]=1; map[u.uid]=s; });
  WING_LETTERS={key:key,map:map}; return map;
}
function wingQueue(u,x,y,r,col){
  if(VEC||!Number.isFinite(x)||!Number.isFinite(y)) return;
  const st=String(u.status||'');
  WING_Q.push({uid:u.uid,dock:u.docked_at||null,x:x,y:y,r:r,col:col,need:st==='WAITING'||st==='blocked'||st==='DRAFT'});
}
function wingHit(a,b){ return a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y; }
// this frame's tags in screen pixels, grouped into squads (one tag, or a dock's row); their discs join the plate obstacles
function wingDiscs(){
  const folded=new Set(PLATE_FOLDS.map(f=>f.dock)), letters=wingLetters(), k=1/ZOOM, list=WING_Q.filter(w=>!(w.dock&&folded.has(w.dock)));
  WING_Q=[];
  const groups=new Map(), squads=[];
  list.forEach(w=>{ w.t=letters[w.uid]||'?'; w.w=w.t.length>1?WING.w2:WING.w; w.h=WING.h;
    w.sx=(w.x-camx)*ZOOM; w.sy=(w.y-camy)*ZOOM; w.bx=Math.round(w.sx-w.w/2); w.by=Math.round(w.sy-w.r*ZOOM-WING.gap-w.h);
    const g=w.dock||w.uid; if(!groups.has(g)) groups.set(g,[]); groups.get(g).push(w); });
  groups.forEach(ms=>{
    const box=w=>({x:w.bx,y:w.by,w:w.w,h:w.h}), clash=ms.some((a,i)=>ms.some((b,j)=>j>i&&wingHit(box(a),box(b))));
    if(!clash){ ms.forEach(w=>squads.push({ms:[w],x:w.bx,y:w.by,w:w.w,h:w.h,need:w.need})); return; }
    ms.sort((a,b)=>a.sx-b.sx||(a.uid<b.uid?-1:1));
    const wide=ms.reduce((s,w)=>s+w.w,0)+2*(ms.length-1), mid=ms.reduce((s,w)=>s+w.sx,0)/ms.length;
    squads.push({ms:ms,x:Math.round(mid-wide/2),y:Math.min(...ms.map(w=>w.by)),w:wide,h:WING.h,row:true,need:ms.some(w=>w.need)}); });
  squads.forEach(q=>{ wingSet(q,q.x,q.y); q.ms.forEach(w=>PLATE_HULLS.push({x:camx+(w.px+w.w/2)*k,y:camy+(w.py+w.h/2)*k,r:(w.w/2+2)*k})); });
  return squads;
}
function wingSet(q,x,y){ let at=x; q.x=x; q.y=y; q.ms.forEach(w=>{ w.px=at; w.py=y; at+=w.w+2; }); }
function wingPlace(squads,placed){
  const z=ZOOM, rects=placed.map(p=>({x:(p.x-camx)*z,y:(p.y-camy)*z,w:p.w*z,h:p.h*z,tag:p.q.kind==='tag'})).concat(overlayBoxes()), done=[];   // the page's own boxes too
  squads.sort((a,b)=>(b.need?1:0)-(a.need?1:0));
  const vw=VW-PANEL_W, vh=VH-plateSheet();
  squads.forEach(q=>{ const x0=q.x, y0=q.y, W2=q.row?0:q.w+2, H2=q.h+2, seen=q.ms.some(w=>w.sx>=0&&w.sx<=vw&&w.sy>=0&&w.sy<=vh);
    const at=(dx,dy)=>{ const c={x:x0+dx,y:y0+dy,w:q.w,h:q.h};   // a craft on screen keeps its tag on screen
      if(seen&&vw>0&&vh>0){ c.x=Math.max(4,Math.min(vw-4-c.w,c.x)); c.y=Math.max(4,Math.min(vh-4-c.h,c.y)); } return c; };
    const cands=q.row?[at(0,0),at(0,-H2),at(0,-2*H2),at(-q.w/2,-H2),at(q.w/2,-H2),at(-q.w,0),at(-q.w,-H2),at(-1.5*q.w,0),at(-1.5*q.w,-H2)]
      :[at(0,0),at(-W2,0),at(W2,0),at(0,-H2),at(-W2,-H2),at(W2,-H2),at(0,-2*H2)];
    let pick=null, fair=null;
    for(const c of cands){ if(done.some(d=>wingHit(c,d))||rects.some(b=>b.tag&&wingHit(c,b))) continue;
      if(!rects.some(b=>wingHit(c,b))){ pick=c; break; } if(!fair) fair=c; }
    pick=pick||fair;
    if(!pick){ q.ms.forEach(w=>{ w.hidden=true; }); return; }
    wingSet(q,Math.round(pick.x),Math.round(pick.y)); q.ms.forEach(w=>{ w.moved=q.row||pick!==cands[0]; }); done.push(pick); });
}
// one tag: flat on top, then down to a point, drawn row by row so its edges stay crisp; a dark rim, the letter white
function wingShape(g,x,y,w,h,col,text){
  const rim=GB?'#0f380f':'#04060c', fill=GB?col:mixPixel(col,'#04060c',.22), hi=GB?col:mixPixel(col,'#ffffff',.4),
    row=i=>i<WING.top?w:Math.max(0,Math.round(w*(h-i)/(h-WING.top)));   // the tag's width on row i (0 = the top, h = the point)
  g.fillStyle=rim; for(let i=-1;i<=h;i++){ const ww=Math.max(1,row(Math.max(0,i))+2); g.fillRect(x+Math.floor((w-ww)/2),y+i,ww,1); }
  for(let i=0;i<h;i++){ const ww=row(i); if(ww<=0) break; g.fillStyle=i===0?hi:fill; g.fillRect(x+Math.floor((w-ww)/2),y+i,ww,1); }
  g.font=cfont(12,{bold:true}); g.textAlign='center'; g.textBaseline='alphabetic';
  g.fillStyle=rim; g.fillText(text,x+w/2+1,y+11); g.fillStyle=GB?'#9bbc0f':'#ffffff'; g.fillText(text,x+w/2,y+10);
}
function paintWings(squads){
  cx.save(); cx.setTransform(DPR,0,0,DPR,0,0);
  squads.forEach(q=>q.ms.forEach(w=>{ if(w.hidden||!w.moved) return;   // a tag set aside, or in a dock's row, keeps a line to its craft
    cx.save(); cx.globalAlpha=q.row?.45:.7; cx.strokeStyle=GB?'#306230':w.col; cx.lineWidth=1; cx.beginPath();
    cx.moveTo(Math.round(w.px+w.w/2)+.5,Math.round(w.py+w.h)+.5); cx.lineTo(Math.round(w.sx)+.5,Math.round(w.sy-w.r*ZOOM*.6)+.5); cx.stroke(); cx.restore(); }));
  squads.forEach(q=>q.ms.forEach(w=>{ if(!w.hidden) wingShape(cx,w.px,w.py,w.w,w.h,w.col,w.t); }));
  cx.restore();
}
// a ship's colour is its state: amber needs you, purple your draft, red dead, green working
function shipColor(u){
  const S=sectorOfUnit(u),st=String(u.status||'');
  if(GB) return GBC[st]||'#306230';
  return (isDead(st)||st==='COLD'||st==='STALE')?'#ff4d5e':(st==='WAITING'||st==='blocked')?'#ffbd2e':st==='DRAFT'?'#c98bff':
    (st==='WORKING'||st==='working')?'#3dff9e':st==='LIMITED'?'#ff8a4c':(st==='IDLE'||st==='idle')?'#5fb4ff':S.accent;
}
// docked ships face the planet they work on; ships on the dock ring fly along it
function shipAngle(u){
  const m=u.docked_at&&(deck==='home'?MOONS:remoteMoons).find(mm=>mm.uid===u.docked_at);
  if(m) return Math.atan2(m.y-u.gy*TILE,m.x-u.gx*TILE)+Math.PI/2;
  return Math.atan2(u.gy*TILE-LY.star.y,u.gx*TILE-LY.star.x)+Math.PI;
}
// a working ship's beam to its planet: dots that run toward the surface
function workBeam(u,sx,sy,col,t){
  const m=u.docked_at&&(deck==='home'?MOONS:remoteMoons).find(mm=>mm.uid===u.docked_at); if(!m||GB) return;
  const dx=m.x-sx,dy=m.y-sy,d=Math.hypot(dx,dy),L=d-m.r-6; if(L<=6) return;
  const ux=dx/d,uy=dy/d,off=REDUCED?0:(t/45)%8;
  cx.save(); cx.globalAlpha=.8; cx.fillStyle=col;
  for(let s=10+off;s<L;s+=8) cx.fillRect(Math.round((sx+ux*s)/2)*2,Math.round((sy+uy*s)/2)*2,2,2);
  cx.restore();
}
// a machine's warp gate: the ring sprite in the machine's status colour, shimmering inside
function drawGate(x,y,col,t){
  const pal=hullPal(col,''); if(GB) pal.a='#306230';   // the ring's rim must not be the outline's own shade
  drawSprite('gate',x,y,0,pal);
  fxGateShimmer(x,y,col,t);
}
// a craft in the vector look: its skin's silhouette in its role's colour (teal seats, a mint-outlined carrier, amber when it asks,
// red when dead), the working beam as a dashed line. Returns the hull radius the plates keep off.
function vecSprite(u,sx,sy,t,o){
  const col=vecShipCol(u);
  if(isShip(u)){
    if(o.working&&!o.dead) vecBeam(u,sx,sy-6,VC.teal,t);
    if(o.carrier){ const k=carrierScale(); vecCraft('carrier',sx,sy-6,CARRIER_DIR,'#0c1a16',VC.mint,SPRITE_CELL,k); return craftHull('carrier',SPRITE_CELL,k); }
    if(u.kind==='seat'&&!u.remote){ const k=craftScale(.75); vecCraft('pilot',sx,sy-6,shipAngle(u)+Math.PI/2,col,null,CREW_CELL,k); return craftHull('pilot',CREW_CELL,k); }
    vecCraft('fighter',sx,sy-6,shipAngle(u),col); return craftHull('fighter');
  }
  if(u.kind==='channel'){ const on=true; vecCraft('beacon',sx,sy-8,0,on?VC.line:VC.dim); return 12; }
  if(u.kind==='portal'){ vecCraft('gate',sx,sy-8,0,'#0c1a16',col); vecRing(sx,sy-8,9,col,.5); return craftHull('gate'); }
  vecCraft('probe',sx,sy-8,0,VC.line); return 12;
}
// a dead craft's red cross, an unknown one's dashed ring (the selection bracket is drawn by vecFocus)
function vecMarks(u,sx,sy,st,dead){
  const z=1/ZOOM;
  if(dead){ cx.save(); cx.strokeStyle=VC.red; cx.lineWidth=1.4*z; cx.beginPath(); cx.moveTo(sx+12*z,sy-24*z); cx.lineTo(sx+20*z,sy-16*z); cx.moveTo(sx+20*z,sy-24*z); cx.lineTo(sx+12*z,sy-16*z); cx.stroke(); cx.restore(); }
  if(st==='BLANK'||st==='unknown'||st==='FROZEN') vecRing(sx,sy-8,Math.max(16,12*z),VC.muted,.7,[3,3]);
}
function sprite(u,sx,sy,t){
  if(u.kind==='task'){ drawTaskShip(u,Math.round(sx/2)*2,Math.round(sy/2)*2,t); return; }   // may be in flight from the star
  if(sx<camx-160||sx>camx+VW/ZOOM+160||sy<camy-80||sy>camy+VH/ZOOM+80)return;
  sx=Math.round(sx/2)*2;sy=Math.round(sy/2)*2;
  if(isBerth(u)){ drawBerth(u,sx,sy); return; }
  const st=String(u.status||''),dead=isDead(st),hot=st==='WAITING'||st==='blocked',
    draft=st==='DRAFT',working=st==='WORKING'||st==='working';
  const col=shipColor(u), selected=selectedUid(u), closeBy=selected||hover===u||detailAt(u,120);
  const carrier=isShip(u)&&u===mothership();
  let hy=sy-8, hr=12;   // the craft's hull (centre, radius): plates keep off it, and the one beside it names it
  if(VEC) hr=vecSprite(u,sx,sy,t,{dead:dead,working:working,carrier:carrier});   // the vector look: schematic silhouettes
  else if(isShip(u)){
    const flick=REDUCED?0:Math.floor(t/90)%2;
    if(working&&!dead) workBeam(u,sx,sy-6,col,t);
    hy=sy-6;
    if(carrier){ drawCarrier(sx,sy-6,t,working&&!dead); hr=craftHull('carrier',SPRITE_CELL,carrierScale()); }
    else if(u.kind==='seat'&&!u.remote){ drawCrew(sx,sy-6,shipAngle(u),col,working&&!dead,t); hr=craftHull('pilot',CREW_CELL,craftScale(.75)); }
    else{ if(working&&!dead) fxTrail(sx,sy-6,shipAngle(u),t,14,2,[-4,4]);
      drawSprite('fighter',sx,sy-6,shipAngle(u),hullPal(col,working&&!dead?(flick?'#ffd34d':'#ff6a2e'):'')); hr=craftHull('fighter'); }
  }else if(u.kind==='channel'){
    const on=true;
    drawSprite('beacon',sx,sy-8,0,hullPal(on?col:mixPixel(col,'#05030f',.55),''));
  }else if(u.kind==='portal'){
    drawGate(sx,sy-8,col,t); hr=craftHull('gate');
  }else{
    drawSprite('probe',sx,sy-8,0,hullPal(col,''));
  }
  if(VEC&&isShip(u)) hy=sy-6;
  plateHull(sx,hy,hr);
  if(VEC){ vecMarks(u,sx,sy,st,dead); }
  else{
  if(u.kind==='seat'&&!u.remote) wingQueue(u,sx,hy,hr,col);   // a seat's craft (the crew, the carrier) carries its letter tag
  if(dead){cx.fillStyle='#ff4d5e';for(let i=0;i<5;i++){
    cx.fillRect(sx+16+i*2,sy-30+i*2,2,2);cx.fillRect(sx+24-i*2,sy-30+i*2,2,2);}}
  if(st==='BLANK'||st==='unknown'||st==='FROZEN'){
    cx.strokeStyle=GB?'#0f380f':'#8092ad';cx.lineWidth=2;cx.setLineDash([4,4]);cx.strokeRect(sx-19,sy-27,38,36);cx.setLineDash([]);
  }
  if(selected){cx.strokeStyle=GB?'#0f380f':'#dcecff';cx.lineWidth=2;cx.strokeRect(sx-23,sy-29,46,42);}
  }
  // One plate per craft: the carrier's name, the seat's name and its state word are one line (the hub's
  // "<carrier> · home · hub · WORKING"); a status badge is a plate of its own. Names only up close (or when the ship needs you).
  const base={unit:u.uid,uid:u.uid,dock:u.docked_at||null,cls:isShip(u)?'ship':null,need:hot||draft,x:sx,y:hy,r:hr,hot:selected||hover===u};
  const words=(txt,color,ord)=>queuePlate(Object.assign({},base,{key:'c:'+u.uid,kind:carrier?'carrier':'seat',
    pri:carrier?PLATE_PRI.carrier:PLATE_PRI.seat,face:'s',segs:[{t:txt,col:color,ord:ord}]}));
  // the carrier: in its own slot under the hull, "N idle" while agents wait parked at it (they are stacked there, ui/focus.js),
  // its name ("<carrier> · main session") when there are none or the owner points at it; a seat named "hub" is that
  // main session, so its name is not said again. On the overview an agent at a planet says nothing until pointed at: the
  // planet's badge counts what waits there (one badge a planet)
  const idle=carrier&&deck==='home'?FC.stack.size:0, hushed=!carrier&&!!u.docked_at&&!closeBy&&!CM.focusUid();
  if(carrier&&idle&&!closeBy) words(idle+' idle',GB?T.paper:'#BFD0E6',0);
  else if(carrier) words(SKIN_NAMES.carrier+' · '+CM.word('home'),GB?T.paper:'#dcecff',0);
  if(!hushed&&(closeBy||u.kind==='portal'||hot||draft)&&!(carrier&&/^hub$/i.test(String(u.name)))) words(String(u.name),selected?T.paper:(GB?T.paper:'#b5cbe3'),1);
  if(!hushed&&(hot||draft||st==='LIMITED'||dead&&u.kind==='seat')){
    const label=draft?'YOUR DRAFT':hot?'NEEDS YOU':st==='LIMITED'?'LIMITED':st;
    queuePlate(Object.assign({},base,{key:'b:'+u.uid,kind:'badge',pri:PLATE_PRI.badge,face:'n',style:'badge',fill:col,ink:'#090e21',segs:[{t:label,col:col}]}));
  }else if(isShip(u)&&(working||st==='done')&&closeBy){
    words(working?'WORKING':'DONE',col,3);
  }
  const note=(txt,color,id)=>queuePlate(Object.assign({},base,{key:'x:'+u.uid+':'+id,kind:'seat',pri:PLATE_PRI.seat,face:'s',cls:null,segs:[{t:txt,col:color}]}));
  if(u.kind==='portal'&&REMOTE_MACHINES.includes(u.machine)&&!DESK) note(REMOTE?'desktop only':'static file · open the live URL',GB?T.paper:'#a7b9d3','static');
  if(st==='RETIRING') words('RETIRING',col,3);
  if(u.kind==='ship'&&u.source==='deck'&&closeBy) note(u.pane||u.status,col,'pane');
}
// ---- WORK DISTRICTS: sprites for the four district kinds. Nothing here idles or wanders:
// a gear turns because a process exists, a clock's hands are the wall clock, a box is a directory.
function fmtN(n){ n=Number(n)||0; return n>=1e6?(n/1e6).toFixed(1)+'m':(n>=1000?(n/1000).toFixed(n>=10000?0:1)+'k':String(n)); }
function districtSprite(u,sx,sy,t){
  if(sx<camx-160||sx>camx+VW/ZOOM+160||sy<camy-80||sy>camy+VH/ZOOM+80)return;
  sx=Math.round(sx/2)*2;sy=Math.round(sy/2)*2;
  const S=sectorOfUnit(u),st=String(u.status||''),stale=['COLD','DOWN','STALE','NEVER','ERROR'].includes(st);
  const col=GB?'#306230':stale?'#ff4d5e':u.kind==='cron'&&st==='WARM'?'#ffbd55':S.accent;
  if(u.kind==='area'){
    // the planet itself is drawn by drawMoon; door and worker live in the panel, not on the surface
    if(u.alias_of&&detailAt(u)) queuePlate({key:'c:'+u.uid,kind:'seat',pri:PLATE_PRI.seat,x:sx,y:sy,r:38,face:'n',uid:u.uid,segs:[{t:'alias → '+u.alias_of,col:T.paper}]});
    return;
  }
  if(u.kind==='dir'){
    const r=Math.max(7,Math.min(18,u.sr||10)), dec=decayOf(BLOCK_KIND[u.area],u.age), dim=dec>0;
    if(dim){ cx.save(); cx.globalAlpha=1-.55*dec; }
    if(VEC) vecRoom(u,sx,sy,r,t); else drawRoom(u,sx,sy,r,t);
    if(dim) cx.restore();
    if(dec>=.6&&roomShape(u)==='moon'&&!VEC) drawCracks(sx,sy,r,seedOf(u.uid),(dec-.6)/.4);
    { const rb=parseSize(u.size); if(rb>=5e8&&!GB){ const rm=massOf(rb), heavy=REGEN_DIRS.has(String(u.name).toLowerCase()); cx.save();
        for(let i=1;i<=Math.min(3,Math.round(rm)-1);i++){ cx.globalAlpha=.16-.03*i; if(VEC) vecRing(sx,sy,r+8+i*9,heavy?'#8f84ff':VC.muted,2,[2,6],0,(r+8+i*9)*.86); else pixelRing(sx,sy,r+8+i*9,(r+8+i*9)*.86,heavy?'#8f84ff':'#8a94a6',0,TAU,0,true); } cx.restore();
        if(selectedUid(u)||hover===u) queuePlate({key:'x:'+u.uid+':heavy',kind:'seat',pri:PLATE_PRI.seat,x:sx,y:sy,r:r+8,face:'n',hot:true,
          segs:[{t:'heavy · '+fmtBytes(rb)+(heavy?' · rebuildable':''),col:heavy?'#b8b0ff':'#c9d1e0'}]}); } }
    if(st==='EMPTY'||st==='UNWALKED'){ if(VEC) vecRing(sx,sy,r+4,VC.muted,.7,[2,4]); else pixelRing(sx,sy,r+4,r+4,GB?'#306230':'#a6b8cc',0,TAU,0,true); }
    if(u.git){cx.fillStyle='#ffbd55';cx.fillRect(sx+r+2,sy-2,3,3);}
    // every supplied child is shown once you fly up to the folder: folders as small moons, files as crates
    if(u.children&&u.children.length&&(selectedUid(u)||Math.hypot(sx-px,sy-py)<42)){
      let at=0,ring=r+16;
      while(at<u.children.length){
        const n=Math.min(u.children.length-at,Math.floor(TAU*ring/11)), spin=0;
        for(let i=0;i<n;i++){
          const k=u.children[at+i],a=i*TAU/n-.5+spin,x=Math.round((sx+Math.cos(a)*ring)/2)*2,y=Math.round((sy+Math.sin(a)*ring)/2)*2;
          const isDir=!!(k[4]&1),th=fileTheme(k[0],isDir,unitTheme(u)),TH=THEMES[th]||THEMES.binary;
          if(VEC){ cx.save(); cx.strokeStyle=isDir?VC.body:VC.line; cx.lineWidth=1/ZOOM; cx.globalAlpha=.85; if(isDir){ cx.beginPath(); cx.arc(x,y,3,0,TAU); cx.stroke(); } else cx.strokeRect(x-2.5,y-2.5,5,5); cx.restore(); }
          else if(isDir) drawBody(x,y,4,'moon',th,seedOf(k[0]),30);
          else{ cx.fillStyle=GB?'#0f380f':'#05030f'; cx.fillRect(x-3,y-3,6,6); cx.fillStyle=GB?'#306230':TH.base; cx.fillRect(x-2,y-2,4,4); cx.fillStyle=GB?'#9bbc0f':TH.hi; cx.fillRect(x-2,y-2,2,2); }
        }at+=n;ring+=12;
      }
    }
  }else if(u.kind==='cron'){
    const on=true;
    if(VEC) vecCraft('probe',sx,sy-10,(seedOf(u.uid)%7),on?(stale?VC.red:st==='WARM'?VC.amber:VC.line):VC.dim);
    else drawSprite('probe',sx,sy-10,(seedOf(u.uid)%7),hullPal(on?(stale?'#ff4d5e':st==='WARM'?'#ffbd55':'#3dff9e'):'#44558a',''));
  }else if(u.kind==='proc'){
    const f=0;
    if(VEC) vecCraft('drone',sx,sy-10,0,VC.amber);
    else drawSprite('drone',sx,sy-10,0,hullPal('#ff8a1f',f?'#fff1a6':'#ff8a1f'));
  }
  if(selectedUid(u)){
    const detail=u.kind==='dir'?(u.files==null?'not walked':fmtN(u.files)+' files'):st==='BLANK'?'no receipt':st;
    const room={key:'c:'+u.uid,kind:'seat',pri:PLATE_PRI.seat,x:sx,y:sy,r:u.kind==='dir'?Math.max(7,Math.min(18,u.sr||10)):12,face:'s',uid:u.uid,hot:true};
    queuePlate(Object.assign({},room,{segs:[{t:String(u.name),col:T.paper}]}));      // the name over its detail: one plate, two rows
    queuePlate(Object.assign({},room,{row:1,segs:[{t:detail,col:stale?'#ff4d5e':col}]}));
  }
}
function drawDistricts(){
  const ds = W.districts||[];
  ds.forEach(h=>{
    const S = sec(BLOCK_KIND[h.uid]||'unknown');
    const right = h.life==='ALIAS' ? 'alias' : (fmtN(h.files)+' files · '+String(h.life).toLowerCase()+(h.seats?' · '+h.seats+' seat'+(h.seats>1?'s':''):''));
    drawFloor(h.x,h.y,h.w,h.h,S,Math.min(1,(h.hp||0)/100),h.life,lastFrame,String(h.label).slice(0,18),right,QUEST_HITS[h.label]);
  });
  if(ds.length){
    const x0=Math.min(...ds.map(d=>d.x)), y0=Math.min(...ds.map(d=>d.y));
    cx.fillStyle='#7d8ea6'; cx.font=cfont(12,{bold:true}); cx.textAlign='left';
    if(legible(12)) cx.fillText('WORK DISTRICTS · one block per directory · door = instruction file · figure = assigned worker · box = room · clock = cron line · gear = running process · ! = a parked decision names it', x0*TILE+4, y0*TILE-26);
  }
}
function drawHalls(halls, us){
  halls.forEach(h=>{
    const heat = Math.min(1, us.filter(u=>u.hall===h.key).reduce((a,u)=>a+(u.hp||0),0)/400);
    const S = sec(SECTORS[h.key] ? h.key : 'seat');
    drawFloor(h.x,h.y,h.w,h.h,S,heat,heat>0?'LIVE':'WARM',lastFrame,h.label,'',QUEST_HITS[h.label]);
  });
}
const TRAIL=[]; let lastPx=px, lastPy=py, heading=-Math.PI/2, wantHeading=-Math.PI/2, thrust=0, lastDrawT=0;
function draw(t){
  FRAME_T=t; CLICK_BOXES.length=0; PLATE_Q=[]; PLATE_KEYS.clear(); PLATE_HULLS=[]; PLATE_DISCS=[];   // plates claim their places afresh each frame
  { const G=(deck==='home'?MOONS:remoteMoons).geo; if(G){ LY.rootOrbit=G.R; LY.wedges=G.wedges; LY.planets=G.planets; LY.fleet=G.fleet; LY.tilt=G.k||1; if(G.starR){ LY.star.r=G.starR; LY.dock=G.dock; } } }
  if(MOB){ SHEET_CUR+=(SHEET_H-SHEET_CUR)*.25; if(Math.abs(SHEET_H-SHEET_CUR)<.5) SHEET_CUR=SHEET_H; }   // the card sheet takes the bottom of the stage: aim at what is left above it
  fcFrameStart(t);   // the focus camera eases before the frame reads it (ui/focus.js); the overview's books start empty
  { const sn=ZOOM>1?2/ZOOM:2; camx = Math.round((px + camOff.x - (VW-PANEL_W)/2/ZOOM)/sn)*sn; camy = Math.round((py + camOff.y - (VH-SHEET_CUR)/2/ZOOM)/sn)*sn; }
  let remoteBanner=null;
  if(GB) VEC=false;   // the mono palette is never the vector look (MONO reloads the page; a test may flip GB directly)
  if(VEC){ vecFrame(); vecBootStart(t); }
  cx.fillStyle=T.bg; cx.fillRect(0,0,VW,VH);
  CM.drawBackdrop2D(cx);
  if(VEC) vecSpace(t);
  else{ drawNebula(t);
  drawStars(t); }
  cx.save(); cx.scale(ZOOM,ZOOM); cx.translate(-camx, -camy);
  if(deck==='home'){
    drawSystem(t, MOONS, units, W.name||'CONTAINER', W.quests||[], W.oldest||0);
    CM.drawConstellations2D(t);
    CM.drawWorkflows2D(t);
    // Relay age is metadata; it cannot stand in for a live delivery event.
    MOONS.forEach(m=>{ if(CM.inFocus(m)) drawBattle(m,t); });   // the problems first: the owner's ships fly in front of them
    if(SKY_STAR.enemies&&skyOn()&&CM.inFocus(SKY_STAR)){ SKY_STAR.x=LY.star.x; SKY_STAR.y=LY.star.y; SKY_STAR.r=LY.star.r; drawBattle(SKY_STAR,t); }   // contacts with no planet
    if(skyOn()&&!QUIET) drawDerelicts();                                   // the hangar's dead runners, on the outer belt (the full view: stopped agents live in Details)
    units.forEach(u=>{ if(u.kind==='machine') return;   // other machines are planets that draw themselves; this one is the star
      if(QUIET && (u.kind==='cron'||u.kind==='proc')) return;              // quiet map: crons live in the planet panel
      if(skyHidden(u)) return;                                             // sky mode: running work is in warp
      if(CM.detailHidden(u)) return;
      (isDK(u)?districtSprite:sprite)(u, u.gx*TILE, u.gy*TILE, t); });
    drawFocus(t);
    if(!QUIET) drawEffort(units);
    if(portalAlert && Date.now()<portalAlert.until){
      const pu=units.find(u=>u.kind==='portal'&&u.machine===portalAlert.machine);
      if(pu){ cx.fillStyle='#f85149'; cx.font=wfont(12); cx.textAlign='center';
        cx.fillText(String(portalAlert.msg), pu.gx*TILE, pu.gy*TILE-48); }
    }
  } else {
    if(remoteMoons.length){
      drawSystem(t, remoteMoons, remoteUnits, String(deck).toUpperCase(), curQuests(), (remoteSynced&&remoteSynced.oldest)||0);
      const ri=remoteInfo||{};
      remoteBanner='SYNCED WORLD · '+String(deck)+' rendered '+(ri.generated||'?')+' · '+ageS(ri.age_s)+' old · '+(remoteState?'agents live from herdr':'herdr did not answer: the agents are the last snapshot, view only');
      if(remoteNote){ cx.fillStyle='#9fb4cc'; cx.font=wfont(12); cx.textAlign='center'; cx.fillText(remoteNote, 0, LY.dock+100); }
    } else {
      drawHalls(remoteHalls, remoteUnits);
      cx.fillStyle=T.label; cx.font=wfont(12); cx.textAlign='left';
      if(remoteHalls.length) cx.fillText(String(deck).toUpperCase()+' DECK · live herdr only'+((remoteInfo&&remoteInfo.stale)?' (its synced world is '+ageS(remoteInfo.age_s)+' old, too old to show)':' (no synced world yet)')+' · return via home gate', remoteHalls[0].x*TILE+4, remoteHalls[0].y*TILE-20);
      const seats=remoteUnits.filter(u=>!u.remoteReturn);
      if(!seats.length && remoteHalls.length){
        cx.fillStyle=T.label; cx.font=wfont(12); cx.textAlign='center';
        cx.fillText(remoteNote||('no agents detected on '+deck), (remoteHalls[0].x+remoteHalls[0].w/2)*TILE, (remoteHalls[0].y+3)*TILE);
      }
    }
    remoteUnits.forEach(u=>{ if(u.kind==='machine' && LY.planets[u.name] && remoteMoons.length) return;
      if(CM.detailHidden(u)) return;
      (isDK(u)?districtSprite:sprite)(u, u.gx*TILE, u.gy*TILE, t); });
  }
  // player = the hub seat itself. Under a PostToolUse hook it walks to whatever the hub just
  // touched; under WASD it is you. Either way the label says who it is.
  // thruster trail: only while actually moving, in the colour of the sector underfoot
  if(!REDUCED&&Math.hypot(px-lastPx,py-lastPy)>0.5){ TRAIL.push([px,py]); if(TRAIL.length>14) TRAIL.shift(); } else if(TRAIL.length) TRAIL.shift();
  const mvx=px-lastPx, mvy=py-lastPy;   // this frame's movement, read before the trail code forgets it
  lastPx=px; lastPy=py;
  const trailCol = (sectorHere||sec('room')).accent;
  if(VEC){ if(TRAIL.length>1){ cx.save(); cx.strokeStyle=VC.text; cx.globalAlpha=.3; cx.lineWidth=1/ZOOM; cx.beginPath(); TRAIL.forEach(([tx,ty],i)=>{ if(i) cx.lineTo(tx,ty-6); else cx.moveTo(tx,ty-6); }); cx.lineTo(px,py-6); cx.stroke(); cx.restore(); } }
  else TRAIL.forEach(([tx,ty],i)=>{cx.save();cx.globalAlpha=.06+.30*(i/TRAIL.length);cx.fillStyle=trailCol;const r=2+2*Math.floor(3*i/TRAIL.length);cx.fillRect(Math.round((tx-r)/2)*2,Math.round((ty-6-r)/2)*2,2*r,2*r);cx.restore();});
  // the ship turns smoothly toward where it is really flying (32 headings, eased) and burns only
  // while moving: the exhaust is the propulsion, not decoration. The agent pilot flies the same way.
  { const sp=Math.hypot(mvx,mvy), dtf=Math.min(.05,Math.max(0,(t-(lastDrawT||t))/1000)); lastDrawT=t;
    if(sp>0.3){ wantHeading=Math.atan2(mvy,mvx); thrust=Math.min(1,thrust+0.25); } else thrust=Math.max(0,thrust-0.12);
    const dA=(((wantHeading-heading)%TAU)+TAU*1.5)%TAU-Math.PI;
    heading=REDUCED?wantHeading:heading+dA*Math.min(1,dtf*10);
    if(flightOn()){ heading=wantHeading=FL.hdg; thrust=FL.power; } else FL.hdg=heading;   // on the keyboard the craft points where it is flown
    drawPilot(px,py-6,heading,thrust,t); }
  { const moving=pilot&&pilot.source==='agent',docked=lastDock&&lastDock.deck===deck&&performance.now()<lastDock.until;
    const target=unitByUid(moving?pilot.targetUid:docked?lastDock.uid:'');
    // you ("hub · you" -> you); the main session flying the ship for you says so ("main session → api")
    const label=moving?(CM.word('hub')+' → '+(target?target.name:'moving')):docked&&target?(CM.word('hub')+' · at '+target.name):CM.word('hub · you');
    // the pilot ship's plate rides on the carrier's when the two overlap (the hub is both): one plate for the craft, not four
    const pr=craftHull('pilot',PILOT_CELL,craftScale(.9)), ph=py-6, M=mothership(), onCarrier=deck==='home'&&!!M&&Math.hypot(px-M.gx*TILE,ph-(M.gy*TILE-6))<pr+craftHull('carrier',SPRITE_CELL,carrierScale());
    const face=VW<=700?'n':'s';
    plateHull(px,ph,pr);
    // parked on the sun (where a session starts) the ship is part of "This machine": no second name over the sun's face
    const parked=deck==='home'&&!moving&&!flightOn()&&Math.hypot(px-LY.star.x,ph-LY.star.y)<fcSunR()&&!(hover&&hover.uid==='player');
    if(!parked) queuePlate({key:onCarrier?'c:'+M.uid:'player',kind:'player',pri:PLATE_PRI.player,x:px,y:ph,r:pr,face:face,segs:[{t:label,col:T.paper,ord:2}]});
    if(GRAV) queuePlate({key:'gravity',kind:'player',pri:PLATE_PRI.player,x:px,y:ph,r:pr,face:face,
      segs:[{t:'GRAVITY · '+GRAV.label+' · '+fmtBytes(GRAV.m.bytes)+(GRAV.m.bloat>=30?' · '+GRAV.m.bloat+'% bloat':'')+' · Shift to break free',
        col:GB?T.paper:(GRAV.m.bloat>=30?'#b8b0ff':'#c9d1e0')}]}); }
  cx.restore();
  fcEdgePlan();  // the named edge arrows of off-map bodies: their boxes are obstacles for the labels and the plates (ui/focus.js)
  runPlates();   // every craft, foe and the player are down: lay the plates out against them, then print them on top
  fcEdge(t); fcCrumb(); fcFrameEnd(t);   // the named edge arrows, the crumb chip in focus, the frame's books for __mapAudit
  if(VEC) vecScreen(t);   // the tether to the open card, the edge chevrons, the compass
  drawFleetMeter();
  if(remoteBanner){ cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.font=cfont(12,{bold:true}); cx.textAlign='center';
    const bw=cx.measureText(remoteBanner).width+20; cx.fillStyle='#04060c'; cx.fillRect(VW/2-bw/2+2, 128, bw, 20);
    cx.fillStyle= remoteState ? '#ffd166' : '#e3b341'; cx.fillRect(VW/2-bw/2, 126, bw, 20); cx.fillStyle='#04060c'; cx.fillText(remoteBanner, VW/2, 140); cx.restore(); }
  drawScanlines();
  // a planet answers hover with its own tag (the status line) and the wingmate's words: no third box over it
  const tipOff=!!(CM.chromeReady&&CM.chromeOn&&CM.chromeOn());   // a desktop: hovering rewrites the status bar's readout, nothing floats
  if(hover && mouseW && !dragging && hover.kind!=='area' && VEC && !tipOff) vecTip(tipLines(hover));
  else if(hover && mouseW && !dragging && hover.kind!=='area' && !tipOff){ const lines=tipLines(hover); cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.font=cfont(13); cx.textAlign='left';
    const w=Math.max(...lines.map(l=>cx.measureText(l).width))+20, h=lines.length*19+14; let x=mouseW.sx+16, y=mouseW.sy+16;
    if(x+w>VW-8) x=Math.max(8,mouseW.sx-w-8); if(y+h>VH-40) y=Math.max(8,mouseW.sy-h-8);
    cx.fillStyle='rgba(4,6,12,.94)'; cx.fillRect(x,y,w,h); cx.strokeStyle='#536c8d'; cx.lineWidth=2; cx.strokeRect(x+1,y+1,w-2,h-2);
    lines.forEach((l,i)=>{ cx.fillStyle=i===0?'#ffd166':(i===lines.length-1?'#afc3dc':'#d7dee7'); cx.font=cfont(13,{bold:i===0}); cx.fillText(l,x+10,y+19+i*19); });
    cx.restore(); }
  // a small toast under the top bar (was a 22px banner across the middle of the map)
  if(flightFlash && Date.now()<flightFlash.until && VEC){ cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.font=vecFont(13,0,'data'); cx.textAlign='center'; cx.textBaseline='alphabetic';
    const tw=cx.measureText(flightFlash.text).width+28, fy=MOB?Math.round(SAFE_T)+96:vecRails().top+12; cx.fillStyle='rgba(11,14,13,.9)'; cx.fillRect(VW/2-tw/2,fy,tw,24);
    cx.strokeStyle=VC.border; cx.lineWidth=1; cx.strokeRect(Math.round(VW/2-tw/2)+.5,fy+.5,Math.round(tw)-1,23); cx.fillStyle=VC.yellow; cx.fillRect(VW/2-tw/2,fy,2,24); cx.fillStyle=VC.text; cx.fillText(flightFlash.text,VW/2,fy+16); cx.restore(); }
  else if(flightFlash && Date.now()<flightFlash.until){ cx.save(); cx.setTransform(DPR,0,0,DPR,0,0); cx.font=cfont(13,{bold:true}); cx.textAlign='center';
    const tw=cx.measureText(flightFlash.text).width+28, fy=MOB?Math.round(SAFE_T)+96:40; cx.fillStyle='#0b1428f0'; cx.fillRect(VW/2-tw/2, fy, tw, 24);
    cx.fillStyle='#ffd166'; cx.fillRect(VW/2-tw/2, fy, 3, 24); cx.fillStyle='#ffe3a3'; cx.fillText(flightFlash.text, VW/2, fy+16); cx.restore(); }
  drawMergeBanner(t);
  // ---- minimap: fixed to the viewport; hidden behind the full-screen mobile panel ----
  if(minimapShown()) (function(){
    const mb=minimapBox(), mw=mb.w, mh=mb.h, mx=mb.x, my=mb.y;
    let x0=Infinity, y0=Infinity, x1=-Infinity, y1=-Infinity;
    const mhalls=(deck==='home'?[]:remoteHalls);
    const mus=(deck==='home'?units:remoteUnits);
    mhalls.forEach(function(h){ x0=Math.min(x0,h.x); y0=Math.min(y0,h.y); x1=Math.max(x1,h.x+h.w); y1=Math.max(y1,h.y+h.h); });
    if(deck==='home'){ x0=minGX; y0=minGY; x1=maxGX; y1=maxGY; }
    else if(remoteMoons.length && remoteUnits.length){ x0=Math.min(...remoteUnits.map(u=>u.gx))-4; y0=Math.min(...remoteUnits.map(u=>u.gy))-4; x1=Math.max(...remoteUnits.map(u=>u.gx))+4; y1=Math.max(...remoteUnits.map(u=>u.gy))+4; }
    if(!isFinite(x0)){ x0=0; y0=0; x1=32; y1=24; }
    function dot(gx,gy){ return [mx+6+((gx-x0)/((x1-x0)||1))*(mw-12), my+6+((gy-y0)/((y1-y0)||1))*(mh-12)]; }
    cx.save();
    cx.fillStyle=GB?'rgba(139,188,15,.92)':VEC?'rgba(11,14,13,.86)':'rgba(13,17,25,.92)'; cx.fillRect(mx,my,mw,mh);
    cx.strokeStyle=VEC?VC.border:T.edge; cx.lineWidth=1; cx.strokeRect(mx+.5,my+.5,mw-1,mh-1);
    mhalls.forEach(function(h){
      const a=dot(h.x,h.y), b=dot(h.x+h.w,h.y+h.h);
      const MS = h.uid ? sec(BLOCK_KIND[h.uid]||'unknown') : sec(SECTORS[h.key] ? h.key : 'seat');
      cx.fillStyle=MS.floor2; cx.fillRect(a[0],a[1],b[0]-a[0],b[1]-a[1]);
      cx.strokeStyle=MS.accent; cx.strokeRect(a[0]+.5,a[1]+.5,b[0]-a[0]-1,b[1]-a[1]-1);
      if(b[1]-a[1]>=16&&b[0]-a[0]>=12){ cx.fillStyle=T.label; cx.font=cfont(12); cx.textAlign='left';   // an initial only where it fits at 12 px
        cx.fillText(String(h.label||h.key||'?')[0].toUpperCase(), a[0]+2, a[1]+13); }
    });
    (deck==='home'?MOONS:remoteMoons).forEach(function(m){ const p=dot(m.x/TILE,m.y/TILE); cx.fillStyle=VEC?(m.enemies?VC.red:VC.line):sec(m.kind).accent; cx.beginPath(); cx.arc(p[0],p[1],2.6,0,Math.PI*2); cx.fill(); });
    if(deck==='home'||remoteMoons.length){
      if(deck==='home') Object.keys(LY.planets).forEach(function(k){ const p=dot(LY.planets[k].x/TILE,LY.planets[k].y/TILE); cx.strokeStyle='#ffcf4a'; cx.lineWidth=1; cx.beginPath(); cx.arc(p[0],p[1],3.5,0,Math.PI*2); cx.stroke(); });
      const sp=dot(0,0); cx.fillStyle='#ffd166'; cx.beginPath(); cx.arc(sp[0],sp[1],4,0,Math.PI*2); cx.fill();
    }
    mus.forEach(function(u){
      const p=dot(u.gx,u.gy);
      cx.fillStyle=COL[u.status]||'#8b98a8';
      cx.beginPath(); cx.arc(p[0],p[1],2,0,Math.PI*2); cx.fill();
    });
    const pp=dot(px/TILE,py/TILE);
    cx.strokeStyle=GB?'rgba(15,56,15,.7)':'rgba(255,255,255,.65)'; cx.lineWidth=1;
    cx.beginPath();
    cx.moveTo(pp[0]-5,pp[1]); cx.lineTo(pp[0]+5,pp[1]);
    cx.moveTo(pp[0],pp[1]-5); cx.lineTo(pp[0],pp[1]+5);
    cx.stroke();
    cx.fillStyle=GB?'#0f380f':'#fff';
    cx.beginPath(); cx.arc(pp[0],pp[1],3,0,Math.PI*2); cx.fill();
    cx.strokeStyle=GB?'#9bbc0f':'#000';
    cx.beginPath(); cx.arc(pp[0],pp[1],3,0,Math.PI*2); cx.stroke();
    cx.restore();
  })();
  if(near&&!MOB){ cx.fillStyle=GB?'#0f380f':'#e6edf3'; cx.font=VEC?vecFont(13,0,'data'):cfont(13); cx.textAlign='center';   // a phone has no Enter or E
    let hint='Enter / E: inspect '+near.name+' · P: prompt';
    if(near===BOARD) hint='Enter / E: this machine';
    else if(near.kind==='portal'&&near.machine==='__home__') hint='Enter / E: return home';
    else if(near.kind==='portal') hint='Enter / E: cross to '+near.machine;
    cx.fillText(hint, VW/2, VH-110); }
  if(fading){
    const now=(typeof performance!=='undefined'&&performance.now)?performance.now():Date.now();
    const a=REDUCED?1:Math.max(0,Math.min(1,(now-fading.t0)/fading.dur));
    cx.fillStyle='rgba(0,0,0,'+a.toFixed(3)+')'; cx.fillRect(0,0,VW,VH);
    if(a>0.25){ cx.fillStyle='#e6edf3'; cx.font=cfont(13); cx.textAlign='center';
      cx.fillText(String(fading.text||''), VW/2, VH/2); }
  }
  if(VEC) vecBootDraw(t);   // the boot covers everything while it plays
}

function finishPilot(){
  const trip=pilot;
  const u=trip&&trip.targetUid?currentTargets().find(u=>u.uid===trip.targetUid):null;
  if(trip&&trip.targetUid){
    if(!u||trip.targetDeck!==deck){pilot=null;activeAction=null;lastDock=null;return;}
    const p=approach(u);
    if(Math.hypot(px-p.x,py-p.y)>2){trip.x=p.x;trip.y=p.y;return;}
    lastDock={uid:u.uid,deck,until:performance.now()+900};
    visitHoldUntil=performance.now()+650;
  }
  pilot=null;activeAction=null;
  if(trip&&trip.arrive)trip.arrive(u);
}
function moveToward(target,dt){
  if(target.targetUid){
    const u=target.targetDeck===deck&&currentTargets().find(u=>u.uid===target.targetUid);
    if(!u){pilot=null;activeAction=null;lastDock=null;flightFlash={text:'TARGET LEFT THIS DECK',until:Date.now()+2200};return;}
    Object.assign(target,approach(u));
  }
  const dx=target.x-px,dy=target.y-py,d=Math.hypot(dx,dy),step=SPEED*dt;
  if(d<=Math.max(2,step)){px=target.x;py=target.y;finishPilot();return;}
  px+=dx/d*step;py+=dy/d*step;
}
// the canvas is fully covered when a phone is on Today, Threads or the wingmate, or the board, legend or prompt fills the screen:
// the world still moves (the ship, the pilot, the actions), it is just not drawn
function mapCovered(){ if(!MOB) return false; const B=document.body; return B.dataset.tab!=='map'||B.classList.contains('m-over'); }
// one frame of the ship at clock t (ms) over dt (s): the touch pad, the keyboard flight or the pilot, the bank, roll and
// U-turn (fleetTick), gravity, the deck's edges. The 2D loop runs it every frame; the 3D view's __3dStep runs the same
// frame on its own stepped clock (PAGE3D.fly).
function stepFlight(t,dt){
  // the touch pad moves the ship straight, as it always has; the keyboard flies it (flightStep); else the pilot, if any
  let dx=(keys['v-right']?1:0)-(keys['v-left']?1:0);
  let dy=(keys['v-down']?1:0)-(keys['v-up']?1:0);
  if(!typing()&&(dx||dy)){
    const n=Math.hypot(dx,dy); boostOn=!!keys.shift; const v=SPEED*(boostOn?1.8:1); px+=dx/n*v*dt;py+=dy/n*v*dt; FL.kb=false;
  } else if(flightOn()){ flightStep(dt,typing(),flyK()); boostOn=FL.boost; }
  else { boostOn=false; if(!typing()&&pilot) moveToward(pilot,dt); }
  fleetTick(t,dt);
  if(CM.mode3d()&&P3.flight) return;   // spatial flight owns translation, clearance and bounds; 2D gravity stays on the map
  // gravity: inside a heavy planet's well the ship drifts toward it, harder the closer and the heavier
  GRAV=null;
  if(deck==='home'&&!(pilot&&pilot.source==='agent')&&!typing()){
    let pull=null, best=0;
    for(const m of MOONS){ if(!m.wellR) continue; const d=Math.hypot(m.x-px,m.y-py); if(d>=m.wellR||d<=m.r+28) continue;
      const k=(1-d/m.wellR)*m.mass; if(k>best){ best=k; pull={m:m,d:d,k:k}; } }
    if(pull){ const v=Math.min(260,70*pull.k), ux=(pull.m.x-px)/pull.d, uy=(pull.m.y-py)/pull.d; px+=ux*v*dt; py+=uy*v*dt; GRAV={label:pull.m.label,k:pull.k,v:v,m:pull.m}; }
  }
  if(deck==='home'){ px=clamp(px,minGX*TILE,maxGX*TILE);py=clamp(py,minGY*TILE,maxGY*TILE); }
  else{
    let x0=minGX*TILE,y0=minGY*TILE,x1=maxGX*TILE,y1=maxGY*TILE;
    if(remoteHalls.length){
      x0=Math.min(...remoteHalls.map(h=>h.x))*TILE-TILE; y0=Math.min(...remoteHalls.map(h=>h.y))*TILE-TILE;
      x1=(Math.max(...remoteHalls.map(h=>h.x+h.w))+1)*TILE; y1=(Math.max(...remoteHalls.map(h=>h.y+h.h))+2)*TILE;
    }
    if(remoteUnits.length){
      x0=Math.min(x0,...remoteUnits.map(u=>u.gx*TILE))-TILE; y0=Math.min(y0,...remoteUnits.map(u=>u.gy*TILE))-TILE;
      x1=Math.max(x1,...remoteUnits.map(u=>u.gx*TILE))+TILE; y1=Math.max(y1,...remoteUnits.map(u=>u.gy*TILE))+TILE;
    }
    px=clamp(px,x0,x1); py=clamp(py,y0,y1);
  }
}
function step(t){
  const dt=Math.min(.05,Math.max(0,(t-(lastFrame||t))/1000)); lastFrame=t;
  if(!CM.mode3d()) stepFlight(t,dt);   // PAGE3D.fly advances the shared engine and spatial movement together
  hover=mouseW?pickAt(mouseW.x,mouseW.y):null;
  { const want=dragging?'grabbing':hover?'pointer':'crosshair'; if(cv.style.cursor!==want) cv.style.cursor=want; }
  near=null;let best=44;
  currentTargets().forEach(u=>{if(skyHidden(u))return;const at=FC.pos.get(u.uid);if(!at&&u!==BOARD&&CM.detailHidden(u))return;const d=Math.hypot((at?at.x:u.gx*TILE)-px,(at?at.y:u.gy*TILE)-py);if(d<best){best=d;near=u;}});   // what is not drawn is never near
  if(CM.mode3d()){ pumpActions(); CM.frame3d(t,dt); FRAME_T=t; wantFrame(); return; }   // the 3D view owns the screen
  pumpActions();if(!mapCovered())draw(t);else FRAME_T=t;wantFrame();
}
// frame scheduling: no frames while the tab is hidden (the loop picks up again when it shows), and
// after IDLE_MS with no input, no flight and no pull, at most IDLE_FPS a second: the orbits and
// twinkles still move, they are drawn less often (full rate again on the next input)
const IDLE_MS=4000, IDLE_FPS=30;
let lastInput=0, lastPaint=0, framing=false;
['keydown','pointerdown','pointermove','wheel','touchstart'].forEach(ev=>
  addEventListener(ev,()=>{ lastInput=performance.now(); },{passive:true}));
function wantFrame(){
  if(!framing&&!document.hidden){ framing=true; requestAnimationFrame(frame); } }
function frame(t){
  framing=false;
  if(document.hidden) return;
  if(CM.mode3d()){ lastPaint=t; step(t); return; }
  const busy=t-lastInput<IDLE_MS||pilot||GRAV||dragging||actionQueue.length||activeAction
    ||typing()||Object.values(keys).some(Boolean)||FL.spd>0||FL.roll||FL.uturn;
  if(!busy&&t-lastPaint<1000/IDLE_FPS-2){ framing=true; requestAnimationFrame(frame); return; }
  lastPaint=t; mobFrameTime(t,busy); step(t);
}
// frame time on a phone: busy frames on the map come one per refresh; when their average stays over 28 ms for 90 frames the
// canvas gives up one step of pixel ratio, once. Throttled idle frames and a covered map say nothing about the cost.
let FT_PREV=0, FT_EMA=0, FT_SLOW=0;
function mobFrameTime(t,busy){
  if(!MOB||DPR_DROPPED) return;
  if(!busy||mapCovered()){ FT_PREV=0; return; }
  const was=FT_PREV; FT_PREV=t; if(!was) return;                                    // the first frame of a run measures nothing
  const dt=t-was; if(dt<=0||dt>250) return;                                          // a stall (a keyboard, a tab switch) is not the cost of drawing
  FT_EMA=FT_EMA?FT_EMA*.9+dt*.1:dt; FT_SLOW=FT_EMA>28?FT_SLOW+1:0;
  if(FT_SLOW>=90){ DPR_DROPPED=true; FT_SLOW=0; fit(); }
}
addEventListener('visibilitychange',()=>{ if(!document.hidden) wantFrame(); });
function qOpen(){ return document.getElementById('quests').classList.contains('on'); }

let paneTimer=null,paneEpoch=0,paneRevision=null,autoFollow=true,agentTyper=null;
let drafts={};
try{const d=JSON.parse(sessionStorage.getItem('paneDrafts')||'{}');if(d&&typeof d==='object')drafts=d;}catch(e){}
function saveDrafts(){try{sessionStorage.setItem('paneDrafts',JSON.stringify(drafts));}catch(e){}}
function stopPane(){termStop();paneEpoch++;if(paneTimer){clearInterval(paneTimer);paneTimer=null;}paneRevision=null;if(agentTyper){clearInterval(agentTyper.timer);agentTyper=null;}}
function api(path, opt){
  if(!LIVE) return Promise.reject(new Error('static file — view only'));
  if(REMOTE && !REMOTE_API.test(String(path))) return Promise.reject(new Error('desktop only: not available from a phone'));
  opt = opt||{};
  opt.credentials='same-origin';
  // a failure reads as one sentence (CM.errorLine), never "http 404" or "Failed to fetch"
  return fetch(path,opt).catch(()=>{ throw new Error(CM.errorLine(0)); }).then(async r=>{
    let j={};try{j=await r.json();}catch(e){}
    if(!r.ok){const x=new Error(j.error||CM.errorLine(r.status));x.body=j;x.status=r.status;throw x;}return j;
  });
}
function seatTarget(u){return String(u.seat||u.window_id||u.name);}
function setTalkState(ok,reason){
  const b=document.getElementById('sendbtn'),m=document.getElementById('saym');
  if(b)b.disabled=!ok||!String((document.getElementById('sayi')||{}).value||'').trim();
  if(m){m.className='saym'+(ok?'':' err');m.textContent=reason||(ok?'ready':'not sendable');}
}
function updateCount(){
  const t=document.getElementById('sayi'),c=document.getElementById('saycount'),b=document.getElementById('sendbtn');
  if(!t)return;if(c)c.textContent=t.value.length+'/4000';
  if(!b)return;
  if(openUnit&&openUnit.remote){ b.disabled=!DESK||!openUnit.talkable||!t.value.trim(); return; }
  b.disabled=!DESK||!openUnit||!openUnit.talkable||paneRevision==null||!t.value.trim();
}
async function loadRemotePane(u,epoch){
  try{
    const j=await api('/remote/pane?machine='+encodeURIComponent(u.machine)+'&target='+encodeURIComponent(u.pane_id));
    if(epoch!==paneEpoch||!openUnit||openUnit.uid!==u.uid)return;
    const el=document.getElementById('plive');if(!el)return;
    const bottom=el.scrollHeight-el.scrollTop-el.clientHeight<28;
    el.textContent=(j&&(j.text!=null?j.text:j.output))||'(empty pane)';
    if(autoFollow&&bottom)el.scrollTop=el.scrollHeight;
    if(j&&typeof j.talkable==='boolean'){ openUnit.talkable=j.talkable; openUnit.talk_reason=String(j.talk_reason||j.talkReason||''); setTalkState(openUnit.talkable,openUnit.talk_reason); }
    updateCount();
  }catch(e){
    if(epoch!==paneEpoch)return;
    const m=document.getElementById('saym');if(m){m.className='saym err';m.textContent=e.message;}
    const b=document.getElementById('sendbtn');if(b)b.disabled=true;
  }
}
async function loadPane(u,epoch){
  try{
    const j=await api('/pane?seat='+encodeURIComponent(seatTarget(u))+'&lines=120');
    if(epoch!==paneEpoch||!openUnit||openUnit.uid!==u.uid)return;
    const el=document.getElementById('plive');if(!el)return;
    const bottom=el.scrollHeight-el.scrollTop-el.clientHeight<28;
    el.textContent=j.text||'(empty pane)';
    if(autoFollow&&bottom)el.scrollTop=el.scrollHeight;
    paneRevision=j.revision==null?null:j.revision;
    openUnit.talkable=j.sendable===true;
    openUnit.talk_reason=String(j.send_reason||'');
    const rev=document.getElementById('panerev');if(rev&&!TERM.live)rev.textContent=paneRevision==null?'no revision':'rev '+paneRevision;
    setTalkState(openUnit.talkable,openUnit.talk_reason);updateCount();
    renderPermit(j);
  }catch(e){
    if(epoch!==paneEpoch)return;paneRevision=null;
    const m=document.getElementById('saym');if(m){m.className='saym err';m.textContent=e.message;}
    const b=document.getElementById('sendbtn');if(b)b.disabled=true;
  }
}
// ---- VERBS ARE FILES: every verb whose `applies` names this unit's kind (or any) is offered here.
// A verb is a manifest in verbs_dir; a runner (or you) can add one by writing a file and the next
// render offers it. Runs are receipted like any hand-off; use counts come from those receipts.
function verbKind(u){ return u.kind==='area'?'area':(u.kind==='dir'||u.kind==='room'?'dir':(u.kind==='seat'?'seat':(u.kind==='machine'||u.kind==='portal'?'machine':(u.kind==='star'?'star':u.kind)))); }
function verbsFor(kind){ const V=(W.verbs&&W.verbs.items)||[]; return V.filter(v=>(v.applies||[]).includes('any')||(v.applies||[]).includes(kind)||(kind==='area'&&(v.applies||[]).includes('dir'))); }
function verbTarget(u){ return u.kind==='area'?String(u.name):String(u.path||''); }
function verbsHtml(u){
  if(u.remote) return '';
  const path=String(u.path||''); if(!path&&u.kind!=='area') return '';
  const list=verbsFor(verbKind(u)).filter(v=>v.scope!=='tool'||path===String(W.tool||'')); const errs=(W.verbs&&W.verbs.errors)||[];
  const loop=String((W.verbs&&W.verbs.loop)||'');
  if(!list.length&&!errs.length&&!loop) return '';
  const rows=list.map(v=>{ const key='verb:'+v.name+':'+path; const use=v.use||{runs:0};
    return '<div class="verb"><div class="vh"><button type="button" class="vrun" data-verb="'+attr(v.name)+'" data-target="'+attr(verbTarget(u))+'" data-text="'+(v.text?'1':'')+'">'+esc(v.label)+'</button>'+
      '<span class="vk">'+esc(v.kind)+'</span><span class="vu">'+(use.runs?use.runs+'× · last '+CM.agoOf(use.last||0):(v.age_d>30?'never used in '+v.age_d+'d · attic candidate':'never used'))+'</span></div>'+
      (v.desc?'<div class="vd">'+esc(v.desc)+'</div>':'')+(v.text?'<textarea class="vtext" data-verb="'+attr(v.name)+'" maxlength="4000" placeholder="what you want (goes in as {text})"></textarea>':'')+
      (v.kind==='runner'&&(W.dispatchable||[]).length?'<select class="vrunner" data-verb="'+attr(v.name)+'"><option value="">runner: first offered</option>'+(W.dispatchable||[]).map(r=>'<option value="'+attr(r)+'">'+esc(r)+'</option>').join('')+'</select>':'')+
      '<div class="vmsg" data-verb="'+attr(v.name)+'"></div>'+runsHtml(key)+'</div>'; }).join('');
  return '<div class="verbs"><h3>VERBS · '+list.length+' apply here <small>'+esc(String(W.verbs.dir||''))+' · one file per verb; a runner can write one</small></h3>'+rows+
    (loop?'<details class="vd" style="overflow-wrap:anywhere"><summary>LAST IMPROVEMENT PASS</summary>'+esc(loop)+'</details>':'')+
    (errs.length?'<div class="verr">skipped manifests: '+esc(errs.join(' · '))+'</div>':'')+'</div>';
}
async function runVerb(btn){
  const name=btn.dataset.verb, target=btn.dataset.target, box=btn.closest('.verb'), msg=box.querySelector('.vmsg'), ta=box.querySelector('.vtext');
  const text=ta?ta.value:''; const rsel=box.querySelector('.vrunner'); const runner=rsel?rsel.value:'';
  if(!DESK){ msg.textContent=REMOTE?'desktop only':'static file: live mode needed'; return; }
  if(btn.dataset.text&&!text.trim()){ msg.textContent='say what you want first'; ta&&ta.focus(); return; }
  btn.disabled=true; msg.textContent='…';
  try{ const j=await api('/verb',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({name:name,target:target},text?{text:text}:{},runner?{runner:runner}:{}))});
    msg.textContent='started '+j.rid+(j.runner?' on '+j.runner:'')+' in '+j.cwd+' · receipt lands here';
    toast('verb '+name+' → '+j.rid);
    setTimeout(async()=>{ try{ applyWorld(await api('/state?fresh=1')); if(openUnit) show(openUnit); }catch(e){} }, 4000);
  }catch(e){ msg.textContent=e.message; btn.disabled=false; }
}
// ---- CARDS: a planet or folder as a short card. "For you": one sentence of status, the problems
// with a fix button each, three actions, what is inside. "For agents": the same place as data,
// read from the bridge (/agent/unit, cards.py), with the calls an agent makes from there. ----
let CARD_PROBS=[], CARD_AGENT=null;
function cardAge(d){ return d==null?'—':CM.daysAgo(Math.max(0,d)); }   // "today", "1 day ago", "3 weeks ago"
// the card's tile: the number big and its unit under it, so "2 days ago" is never cut in the narrow tile:
// "2" over "days since last write"; "today" or "—" over "last write"
function ageTile(d){ const w=cardAge(d), m=/^(\\d+) (\\w+) ago$/.exec(w);
  return m?'<b>'+esc(m[1])+'</b><span>'+esc(m[2])+' since last write</span>':'<b>'+esc(w)+'</b><span>last write</span>'; }
// each problem in plain words ("job true: never ran", "deploy of the export · decision · 41 days"); identical causes fold
// into one row with ×N and the oldest age ("job true: no log ×8"), n = how many it holds (the card counts them all)
function cardProblems(u){
  const items=[];
  if(u.kind==='area'){ const m=MOONS.find(x=>x.uid===u.uid); if(m) m.foes.forEach(f=>{ if(f.kind==='contact'){ const c=f.contact||{};
      items.push({key:CM.contactLine(Object.assign({},c,{age_days:null}),{noPlace:true}),age:c.age_days,kind:'contact',contact:c,rank:CM.bucket(c)==='problem'?0:CM.bucket(c)==='decision'?2:1}); }
    else items.push({key:f.kind==='cron'?CM.jobLine(f.name,f.verdict||(unitByUid(f.uid)||{}).verdict):f.kind==='wreck'?'task “'+CM.shortTitle(f.name,60,false)+'” failed':CM.plainText(f.full||f.name,120),age:null,kind:f.kind,rank:f.kind==='item'?2:0}); }); }
  else units.filter(c=>c.kind==='cron'&&c.area===u.area&&c.room===u.name&&BAD_VERDICTS.includes(c.verdict)).forEach(c=>items.push({key:CM.jobLine(c.name,c.verdict),age:null,kind:'cron'}));
  const groups=new Map();
  items.forEach(p=>{ const g=groups.get(p.key); if(!g) groups.set(p.key,Object.assign({n:1},p));
    else { g.n++; if(Number.isFinite(Number(p.age))&&p.age!=null&&!(Number(g.age)>=Number(p.age))){ g.age=p.age; if(p.contact) g.contact=p.contact; } } });
  // problems first (an agent can take them), then what is back for you, then the decisions: the card's "1 problem" is its top row
  return Array.from(groups.values()).sort((a,b)=>(a.rank||0)-(b.rank||0)).map(g=>Object.assign(g,{what:CM.grouped(g.key,g.n)+(g.age!=null&&Number.isFinite(Number(g.age))?' · '+(g.n>1?'oldest ':'')+CM.ageWords(g.age):'')}));
}
function cardProbCount(probs){ return (probs||[]).reduce((a,p)=>a+(Number.isInteger(p.n)?p.n:1),0); }
// what waits at a card's planet in the count's own nouns (moonCounts: problems, need_you, decisions), the same numbers as its
// badge and the rail's System row. A folder inside a planet holds only late jobs (problems); a planet whose only craft are old
// ones the sky does not carry has their rows as problems. moonCountLine says it: "1 problem · 2 need you · 6 decisions"
function cardCounts(m,probs){ const c=m?moonCounts(m):{problems:0,need_you:0,decisions:0};
  if(!(c.problems+c.need_you+c.decisions)&&probs&&probs.length) c.problems=cardProbCount(probs);
  return c; }
// a problem row hands it to an agent; a sky contact's row opens what it is (a decision or a review is the owner's, not an agent's)
function cardProbRow(p,i){ return p.kind==='contact'
  ?'<div class="cprob" role="button" tabindex="0" data-copen="'+i+'" title="open what needs you"><span>'+esc(p.what)+'</span><i>open ›</i></div>'
  :'<div class="cprob" role="button" tabindex="0" data-cfix="'+i+'" title="send an agent on this one"><span>'+esc(p.what)+'</span><i>send ›</i></div>'; }
function cardState(u,m,probs,working){
  if(u.alias_of) return ['ok','A shortcut to '+u.alias_of+'; nothing of its own lives here.'];
  const cc=cardCounts(m,probs), n=cc.problems, own=moonCountLine({need_you:cc.need_you,decisions:cc.decisions});   // own = what waits on you here
  const heavy=m&&m.wellR?(' Heavy: '+fmtBytes(m.bytes)+(m.bloat>=30?', '+m.bloat+'% of it rebuildable caches.':'.')):'';
  if(m&&m.trend&&m.trend.worse) return ['bad','Got worse this week: '+m.trend.why+'.'+heavy];
  if(n&&!working) return ['bad',n+' problem'+(n>1?'s':'')+' and nobody on '+(n>1?'them':'it')+'.'+(own?' Waiting on you: '+own+'.':'')+heavy];
  if(n) return ['warn',n+' problem'+(n>1?'s':'')+'; '+working+' agent'+(working>1?'s':'')+' working here.'+(own?' Waiting on you: '+own+'.':'')+heavy];
  if(own) return ['warn','Waiting on you: '+own+'.'+(working?' '+working+' agent'+(working>1?'s':'')+' working here.':'')+heavy];
  if(u.age!=null&&decayOf(BLOCK_KIND[u.kind==='area'?u.uid:u.area],u.age)>=.5) return ['warn','Quiet: nothing written in '+u.age+' days. Fine if it is finished.'+heavy];
  if(u.age==null) return ['warn','Empty: no files yet.'];
  return ['ok','Healthy: written '+cardAge(u.age)+', nothing is wrong.'+(working?' '+working+' agent'+(working>1?'s':'')+' working here.':'')+heavy];
}
// The vector look's planetary data card (VEC): under the panel's heavy top rule the eyebrow "<KIND> · CLUSTER", the
// name, what the folder is for, a divider, the facts as label / value rows (a fact the page does not have is left out,
// never shown as a dash), the problems as TARGETS with a red triangle each, DEPLOY AGENT [N] and ASK <wingmate>. The
// same data- hooks as the classic card, so every click does what it did. A card that replaces another slides in.
let TAC_CARD=null;
function tacCardHead(u,probs,seatsHere,threads,st,tn){
  const cc=cardCounts(u.kind==='area'?MOONS.find(x=>x.uid===u.uid):null,probs), area=u.kind==='area', kind=String((area?BLOCK_KIND[u.uid]:BLOCK_KIND[u.area])||'folder').toUpperCase();
  const brow=area?kind+' · CLUSTER':'FOLDER · '+String(u.area||'').replace('area:','').toUpperCase()+' CLUSTER';
  const facts=[['CLASS',tn?String(tn).replace(/-class$/i,'').toUpperCase():'',''],['FILES',u.files==null?'':fmtN(u.files),''],['ON DISK',u.size&&u.size!=='—'?String(u.size):'',''],['LAST WRITE',u.age==null?'':cardAge(u.age),''],
    ['AGENTS',String(seatsHere.length),seatsHere.some(c=>c.status==='WORKING')?'ok':''],['TASKS',String(threads.length),threads.some(t=>TASK_WORKING.includes(t.status))?'ok':''],
    ['PROBLEMS',String(cc.problems),cc.problems?'bad':''],['WAITING FOR YOU',cc.need_you?String(cc.need_you):'',''],['DECISIONS',cc.decisions?String(cc.decisions):'','']].filter(f=>f[1]!=='');
  const P=document.getElementById('panel'), fresh=!!(P&&P.classList.contains('on'))&&TAC_CARD!==u.uid; TAC_CARD=u.uid;
  let h='<div class="card tac'+(fresh?' tin':'')+'"><div class="ctop"><div class="ckind">'+esc(brow)+'</div>'+
    '<div class="ctabs"><button type="button" data-ctab="human" class="on">For you</button><button type="button" data-ctab="agent">For agents</button></div></div>'+
    '<h2 class="cname">'+esc(u.name)+'</h2><div class="cpurpose">'+esc(u.tag_desc||shortPath(u.path))+(W.opener&&DESK?' <a class="clink" role="button" tabindex="0" data-cact="open">open folder</a>':'')+'</div>'+
    '<div class="cpane" data-cpane="human"><div class="tdiv"></div><dl class="tdl">'+facts.map(f=>'<dt>'+f[0]+'</dt><dd'+(f[2]?' class="'+f[2]+'"':'')+'>'+esc(f[1])+'</dd>').join('')+'</dl>'+
    '<div class="cstate '+st[0]+'">'+esc(st[1])+'</div>';
  if(probs.length) h+='<div class="csec">TARGETS · '+esc((moonCountLine(cc)||cc.problems+' '+CM.countNoun('problems',cc.problems)).toUpperCase())+'</div>'+probs.slice(0,6).map(cardProbRow).join('')+(probs.length>6?'<div class="cmut">+'+(probs.length-6)+' more · the agents tab lists all of them</div>':'');
  return h+'<div class="cacts"><button type="button" class="primary" data-cact="thread">Deploy agent <kbd class="tkey">N</kbd></button><button type="button" data-cact="mate">Ask '+esc(mateName())+'</button></div>';
}
function renderCard(u){
  const m=u.kind==='area'?MOONS.find(x=>x.uid===u.uid):null, probs=cardProblems(u); CARD_PROBS=probs; CARD_AGENT=null;
  const seatsHere=units.filter(c=>isShip(c)&&!c.remote&&c.cwd&&u.path&&(c.cwd===u.path||String(c.cwd).startsWith(u.path+'/')));
  const threads=agentTasks().filter(t=>u.path&&(t.target===u.path||String(t.target||'').startsWith(u.path+'/')));
  const working=seatsHere.filter(c=>c.status==='WORKING').length+threads.filter(t=>TASK_WORKING.includes(t.status)).length;
  const st=cardState(u,m,probs,working), tn=m&&TYPE_NAME[m.type]?TYPE_NAME[m.type]:'';
  const icon=u.kind==='dir'?filePlanet(u.name,true,unitTheme(u)):filePlanet('',false,'binary',m?'planet:'+m.uid:'binary');
  const fourth=u.kind==='area'?['<b>'+(seatsHere.length+threads.filter(t=>TASK_LIVE.includes(t.status)).length)+'</b><span>agents · tasks</span>']:['<b>'+(u.children_total==null?'—':fmtN(u.children_total))+'</b><span>entries</span>'];
  let h;
  if(!VEC&&!MOB){   // the planet card of the comps (ui/chrome.js); a phone keeps its sheet's card: sections Problems / Waiting for you / Agents here, the rest under More
    document.getElementById('pbody').innerHTML=chromePlanetCardHtml(u,{m:m,probs:probs,seatsHere:seatsHere,threads:threads,district:districtPanel(u),verbs:verbsHtml(u)});
    document.querySelectorAll('#pbody .bar i[data-w]').forEach(i=>{ i.style.width=i.getAttribute('data-w')+'%'; });
    const panel=document.getElementById('panel'); panel.classList.remove('seat'); panel.classList.add('card','on'); panel.setAttribute('aria-hidden','false'); document.getElementById('wrap').classList.add('panel-open'); syncPanelW(); CM.panelSync();
    const dc=document.getElementById('doorcopy'); if(dc) dc.addEventListener('click',()=>{ const el=document.getElementById('doorcmd'); if(el&&navigator.clipboard) navigator.clipboard.writeText(el.textContent).then(()=>{dc.textContent='copied';setTimeout(()=>dc.textContent='copy command',900);}).catch(()=>{}); });
    return; }
  if(VEC) h=tacCardHead(u,probs,seatsHere,threads,st,tn);
  else {
  h='<div class="card"><div class="ctop"><div class="ckind">'+icon+(u.kind==='dir'?'folder in '+esc(String(u.area||'').replace('area:','')):'planet'+(tn?' · '+esc(tn):''))+'</div>'+
    '<div class="ctabs"><button type="button" data-ctab="human" class="on">For you</button><button type="button" data-ctab="agent">For agents</button></div></div>'+
    '<h2 class="cname">'+esc(u.name)+'</h2><div class="cpurpose">'+esc(u.tag_desc||shortPath(u.path))+(W.opener&&DESK?' <a class="clink" role="button" tabindex="0" data-cact="open">open folder</a>':'')+'</div>'+
    '<div class="cpane" data-cpane="human">'+
    '<div class="cstats"><div><b>'+(u.files==null?'—':fmtN(u.files))+'</b><span>files</span></div><div><b>'+esc(u.size||'—')+'</b><span>on disk</span></div><div>'+ageTile(u.age)+'</div><div>'+fourth[0]+'</div></div>'+
    '<div class="cstate '+st[0]+'">'+esc(st[1])+'</div>';
  // problems in full, each one a row that hands it to an agent; the card itself has two actions
  if(probs.length) h+='<div class="csec">WAITING HERE</div>'+probs.slice(0,6).map(cardProbRow).join('')+(probs.length>6?'<div class="cmut">+'+(probs.length-6)+' more · the agents tab lists all of them</div>':'');
  h+='<div class="cacts"><button type="button" class="primary" data-cact="thread">Send an agent</button><button type="button" data-cact="mate">Ask '+esc(mateName())+'</button></div>';
  }
  if(threads.length) h+='<div class="csec">TASKS HERE</div>'+threads.slice(0,5).map(t=>'<div class="crow" data-ctid="'+attr(t.id)+'"><i style="background:'+(TASK_COL[t.status]||'#9aa7bd')+'"></i><span class="n">'+esc(taskTitle(t))+'</span><span class="m">'+esc(taskWord(t))+'</span></div>').join('');
  if(seatsHere.length) h+='<div class="csec">AGENTS HERE</div>'+seatsHere.slice(0,6).map(c=>'<div class="crow" data-cuid="'+attr(c.uid)+'"><i style="background:'+(COL[c.status]||'#8b98a8')+'"></i><span class="n">'+esc(c.alias||c.name)+'</span><span class="m">'+esc(String(c.status||'').toLowerCase())+'</span></div>').join('');
  if(u.kind==='area'){
    const rooms=(u.rooms||[]).slice().sort((a,b)=>(b.files||0)-(a.files||0)), files=u.root_files||[];
    if(rooms.length){ h+='<div class="csec">INSIDE · '+rooms.length+' folder'+(rooms.length>1?'s':'')+'</div>'+rooms.slice(0,8).map(r=>'<div class="crow" data-cuid="'+attr(r.uid)+'">'+filePlanet(r.name,true,unitTheme(u))+'<span class="n">'+esc(r.name)+'</span><span class="m">'+(r.files==null?'':fmtN(r.files)+' files · ')+esc(cardAge(r.age))+'</span></div>').join('');
      if(rooms.length>8) h+='<details class="call"><summary>all '+rooms.length+' folders</summary>'+rooms.slice(8).map(r=>'<div class="crow" data-cuid="'+attr(r.uid)+'">'+filePlanet(r.name,true,unitTheme(u))+'<span class="n">'+esc(r.name)+'</span><span class="m">'+(r.files==null?'':fmtN(r.files)+' files · ')+esc(cardAge(r.age))+'</span></div>').join('')+'</details>'; }
    if(files.length) h+='<div class="csec">FILES · '+(u.root_files_total||files.length)+' · click to read</div><table class="tbl ctbl">'+files.slice(0,8).map(f=>fileRow(f[0], u.path+'/'+f[0], [f[1], cardAge(f[2])])).join('')+'</table>'+
      (files.length>8?'<details class="call"><summary>all '+files.length+' files</summary><table class="tbl ctbl">'+files.slice(8).map(f=>fileRow(f[0], u.path+'/'+f[0], [f[1], cardAge(f[2])])).join('')+'</table></details>':'');
  } else {
    const kids=u.children||[];
    if(kids.length) h+='<div class="csec">INSIDE · '+(u.children_total||kids.length)+' · click a file to read</div><table class="tbl ctbl">'+kids.slice(0,14).map(k=>((k[4]&1)||String(k[0]).indexOf(' → ')>=0)?
      '<tr><td class="fname">'+filePlanet(k[0],true,unitTheme(u))+esc(k[0])+'</td><td>'+(k[1]==null?'':fmtN(k[1])+' files')+'</td><td>'+esc(k[2]||'')+'</td></tr>':
      fileRow(k[0], u.path+'/'+k[0], [k[2]||'', cardAge(k[3])])).join('')+'</table>';
  }
  // everything else, one click away: the facts behind the card, cron lines, verbs, the door command
  const d=districtPanel(u);
  h+='<details class="cmore"><summary>Details · git, instructions, cron lines, verbs, terminal command</summary><dl>'+d.rows.map(r=>'<dt>'+esc(r[0])+'</dt><dd>'+esc(r[1])+'</dd>').join('')+'</dl>'+d.extra.replace(/<div class="say"><button type="button" class="newtask"[\\s\\S]*?<\\/div>/,'')+verbsHtml(u)+'</details>';
  h+='</div><div class="cpane" data-cpane="agent" hidden><div class="cmut">The same place as data. Any agent can read this card and act on it through the bridge with its token; merging and closing threads stay with you.</div>'+
    '<div class="cacts"><button type="button" class="primary" data-cact="brief">Copy brief for an agent</button><button type="button" data-cact="json">Copy JSON</button></div><div id="cagent"><div class="cmut">'+(DESK?'reading the card…':REMOTE?'the agent view is on the desktop':'static copy of the page: open the live page for the agent view')+'</div></div></div></div>';
  document.getElementById('pbody').innerHTML=h;
  const panel=document.getElementById('panel'); panel.classList.remove('seat'); panel.classList.add('card','on'); panel.setAttribute('aria-hidden','false'); document.getElementById('wrap').classList.add('panel-open'); syncPanelW(); CM.panelSync();
  const dc=document.getElementById('doorcopy'); if(dc) dc.addEventListener('click',()=>{ const el=document.getElementById('doorcmd'); if(el&&navigator.clipboard) navigator.clipboard.writeText(el.textContent).then(()=>{dc.textContent='copied';setTimeout(()=>dc.textContent='copy command',900);}).catch(()=>{}); });
}
async function loadCardAgent(u){
  let box=document.getElementById('cagent'); if(!box||!DESK) return;
  try{ const c=await api('/agent/unit?uid='+encodeURIComponent(u.uid)); if(!openUnit||openUnit.uid!==u.uid||!document.getElementById('cagent')) return; CARD_AGENT=c; box=document.getElementById('cagent');
    box.innerHTML='<div class="csec">BRIEF · what you paste into any agent</div><pre class="cjson">'+esc(c.brief||'')+'</pre>'+
      '<div class="csec">CALLS · '+esc((c.auth&&c.auth.base_url)||'')+' · header '+esc((c.auth&&c.auth.header)||'')+'</div>'+
      '<table class="tbl ctbl">'+(c.actions||[]).map(a=>'<tr><td><b>'+esc(a.method)+'</b> '+esc(a.path)+'</td><td>'+esc(a.do)+'</td></tr>').join('')+'</table>'+
      '<div class="cmut">token: '+esc((c.auth&&c.auth.token_file)||'')+'</div><details class="cmore"><summary>the card as JSON</summary><pre class="cjson">'+esc(JSON.stringify(c,null,1))+'</pre></details>';
  }catch(e){ box.innerHTML='<div class="saym err">'+esc(e.message)+'</div>'; }
}
document.getElementById('pbody').addEventListener('click',e=>{
  const card=e.target.closest('.card'); if(!card||!openUnit) return; const u=openUnit;
  const tab=e.target.closest('[data-ctab]'); if(tab){ card.querySelectorAll('[data-ctab]').forEach(b=>b.classList.toggle('on',b===tab)); card.querySelectorAll('[data-cpane]').forEach(p=>{ p.hidden=p.dataset.cpane!==tab.dataset.ctab; }); if(tab.dataset.ctab==='agent'&&!CARD_AGENT) loadCardAgent(u); return; }
  const cop=e.target.closest('[data-copen]'); if(cop){ const p=CARD_PROBS[Number(cop.dataset.copen)]; if(p&&p.contact) openContact(p.contact); return; }
  const fix=e.target.closest('[data-cfix]'); if(fix){ const p=CARD_PROBS[Number(fix.dataset.cfix)]; if(!p) return; openComposer(u);
    const ta=document.getElementById('ctext'); if(ta) ta.value='Fix this in '+u.path+': '+p.what+'. Find the cause, fix it, and show how you verified it.'; return; }
  const a=e.target.closest('[data-cact]'); if(a){ const k=a.dataset.cact;
    if(k==='thread') return openComposer(u);
    if(k==='mate'){ mateShow(true); const q=document.getElementById('mateq'); if(q){ q.value='About '+u.name+': '; q.focus(); } return; }
    if(k==='open') return api('/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:String(u.path||'')})}).then(()=>toast('opened '+shortPath(u.path))).catch(err=>toast(err.message));
    if(k==='brief'||k==='json'){ const go=c=>{ const txt=k==='brief'?c.brief:JSON.stringify(c,null,1); if(navigator.clipboard) navigator.clipboard.writeText(txt).then(()=>{ a.textContent='copied'; setTimeout(()=>{ a.textContent=k==='brief'?'Copy brief for an agent':'Copy JSON'; },1200); }).catch(()=>{ a.textContent='copy failed'; }); };
      if(CARD_AGENT) go(CARD_AGENT); else api('/agent/unit?uid='+encodeURIComponent(u.uid)).then(c=>{ CARD_AGENT=c; go(c); }).catch(err=>{ a.textContent=err.message; }); return; }
  }
  const r=e.target.closest('[data-cuid]'); if(r){ const t=unitByUid(r.dataset.cuid); if(t){ humanMove(); activate(t); } return; }
  const tr=e.target.closest('[data-ctid]'); if(tr){ openTask(tr.dataset.ctid); return; }
});
// the camera keeps what you opened in the visible part of the map, beside the card
let PANEL_W=0;
function syncPanelW(){ const P=document.getElementById('panel'); PANEL_W=(P&&P.classList.contains('on')&&!(CM.chromeOn&&CM.chromeOn())&&(MOB?MOB_LAND:(VW>760||document.documentElement.classList.contains('t'))))?Math.min(P.offsetWidth,VW*.6):0; }
addEventListener('resize',()=>setTimeout(syncPanelW,50));
function showStar(){
  const old=document.getElementById('sayi');if(openUnit&&old){drafts[openUnit.uid]=old.value;saveDrafts();}
  stopPane(); stopTaskPoll(); openTaskId=null; openUnit=BOARD;
  const S=W.summary||{}, tc=taskCounts(), seats=units.filter(u=>isShip(u)&&!u.remote), sk=skyOk(), cc=CM.counts();
  // the one count (W.counts) where the sky is read: the same working, review and decision numbers as the status bar and the rails
  const working=sk&&Number.isInteger(cc.working)?cc.working:seats.filter(u=>u.status==='WORKING').length, nrev=sk&&Number.isInteger(cc.review)?cc.review:tc.review;
  const ndec=sk&&Number.isInteger(cc.decisions)?cc.decisions:(W.quests||[]).length;
  const moons=MOONS.slice().sort((a,b)=>(b.enemies+b.allies)-(a.enemies+a.allies));
  const worse=MOONS.filter(m=>m.trend&&m.trend.worse), quiet=MOONS.filter(m=>m.decay>=.5&&m.life!=='ALIAS');
  const rows=[['root',W.root_path||''],['planets',S.areas!=null?S.areas+' root folders · '+S.rooms+' folders inside them':MOONS.length+' root folders'],
    ['agents',seats.length+' ships · '+working+' working'],['tasks',(sk&&Number.isInteger(cc.working)?cc.working:tc.running)+' working · '+nrev+' to review'],
    ['crons',S.crons!=null?S.crons+' lines · '+S.crons_no_receipt+' without a receipt':'—'],
    ['the board',ndec+' '+CM.countNoun('decisions',ndec)+' · oldest '+CM.ageWords(sk&&cc.oldest_decision&&Number.isInteger(cc.oldest_decision.days)?cc.oldest_decision.days:(W.oldest||0))],
    ['heaviest',MOONS.filter(m=>m.wellR).sort((a,b)=>b.bytes-a.bytes).slice(0,4).map(m=>m.label+' '+fmtBytes(m.bytes)+(m.bloat>=30?' ('+m.bloat+'% bloat)':'')).join(', ')||'nothing over 100 MB'],
    ['getting worse',worse.length?worse.map(m=>m.label+' ('+m.trend.why+')').join(', '):'nothing this week'],
    ['going quiet',quiet.length?quiet.map(m=>m.label+' '+CM.ageWords(m.age)).join(', '):'nothing over a month old (planets that sit still are not counted)'],
    ['rebuilt',W.generated||'']];
  const panel=document.getElementById('panel'); panel.style.setProperty('--accent',sec('machine').accent);
  const fields=MOONS.slice().sort((a,b)=>b.bytes-a.bytes).map(m=>'<tr class="frow" data-uid="'+attr(m.uid)+'"><td>'+esc(m.label)+'</td><td>'+esc(TYPE_NAME[m.type]||'')+'</td><td>'+fmtN(m.files)+' files</td><td>'+(m.bytes?fmtBytes(m.bytes):'')+(m.bloat>=10?' <span class="chip '+(m.bloat>=30?'bad':'')+'">'+m.bloat+'% bloat</span>':'')+'</td><td>'+(m.age==null?'—':esc(CM.ageWords(m.age)))+'</td><td>'+
    (()=>{ const ml=moonLine(m), hard=moonBadge(m).k==='problems';
      return ml&&m.allies&&hard?'<span class="chip bad">battlefield · '+m.allies+' vs '+esc(ml)+'</span>':ml?'<span class="chip'+(hard?' bad':'')+'">'+esc(ml)+'</span>':m.allies?'<span class="chip ok">'+m.allies+' agent'+(m.allies>1?'s':'')+'</span>':''; })()+
    (m.trend&&m.trend.worse?' <span class="chip bad">worse</span>':m.trend&&m.trend.better?' <span class="chip ok">better</span>':'')+'</td></tr>').join('');
  document.getElementById('pbody').innerHTML='<div class="kind">the star · this machine</div><h2>'+esc(W.name||'CONTAINER')+'</h2><dl>'+rows.map(r=>'<dt>'+esc(r[0])+'</dt><dd>'+esc(r[1])+'</dd>').join('')+'</dl>'+
    '<div class="tbtns" style="margin-top:14px"><button type="button" data-star="task">new task</button><button type="button" data-star="tasks">agents and tasks</button><button type="button" data-star="board">decisions</button><button type="button" data-star="today">today</button><button type="button" data-star="legend">what am I looking at</button></div>'+
    (DESK?'<h3>PHONE</h3><div id="phonebox" class="saym">…</div>':'')+
    '<h3>EVERY PLANET · HEAVIEST FIRST · CLICK ONE</h3><table class="tbl">'+fields+'</table>';
  panel.classList.remove('seat'); panel.classList.add('card','on'); panel.setAttribute('aria-hidden','false'); document.getElementById('wrap').classList.add('panel-open'); syncPanelW(); CM.panelSync();
  if(typeof mateExplain==='function') mateExplain(BOARD,true);
  if(DESK) phoneBox();
}
// ---- PHONE: pair a phone with this map (the bridge makes the code, from this page or `container-map pair`) ----
function phoneErr(e){ return String((e&&e.body&&e.body.error)||(e&&e.message)||e); }
async function phoneBox(){
  const box=document.getElementById('phonebox'); if(!box) return;
  let j; try{ j=await api('/phone'); }catch(e){ box.className='saym err'; box.textContent=phoneErr(e); return; }
  const rows=(j.devices||[]).map(d=>'<div class="crow" style="display:flex;align-items:center;gap:8px;padding:8px"><span class="n" style="flex:1 1 auto;min-width:0;overflow-wrap:anywhere">'+esc(d.name)+'<br><span class="m">paired '+esc(String(d.created).slice(0,10))+' · seen '+esc(String(d.last_seen).slice(0,16).replace('T',' '))+'</span></span><button type="button" data-phone-revoke="'+attr(d.id)+'">revoke</button></div>').join('');
  box.className='';
  box.innerHTML=!j.enabled?'<div class="viewonly">Phone access is off'+(j.error?': '+esc(j.error):'. Set "remote" in container.json (README, Use it from your phone) and restart the map.')+'</div>'
    :'<div class="saymeta"><span>'+esc(j.public_origin)+'</span></div>'+(j.listening?'<div class="tbtns"><button type="button" id="phonepair">Pair a phone</button></div><div id="phonecode" class="saym"></div>':'<div class="saym err">'+esc(j.error||'the phone listener is not running')+'</div>')+(rows||'<div class="viewonly">no phone is paired</div>');
}
document.getElementById('pbody').addEventListener('click',async e=>{
  const pair=e.target.closest('#phonepair'), rev=e.target.closest('[data-phone-revoke]'); if(!pair&&!rev) return;
  const out=document.getElementById('phonecode');
  try{
    if(pair){ pair.disabled=true; const j=await api('/phone/pair',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
      if(out){ out.className='saym'; out.innerHTML='code <b>'+esc(String(j.code).replace(/(.{4})(.{4})/,'$1 $2'))+'</b> · open '+esc(j.url)+' on the phone · one use, '+Math.round(j.expires_in/60)+' minutes'; } pair.disabled=false; }
    else { await api('/phone/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:rev.dataset.phoneRevoke})}); phoneBox(); }
  }catch(err){ if(out){ out.className='saym err'; out.textContent=phoneErr(err); } if(pair) pair.disabled=false; }
});
document.getElementById('pbody').addEventListener('click',e=>{
  const b=e.target.closest('[data-star]'); if(b){ const k=b.dataset.star; if(k==='task') openComposer(null); else if(k==='tasks'){ closeAll(); toggleTasks(true); } else if(k==='board'){ closeAll(); toggleQuests(true); } else if(k==='today') setView('today'); else if(k==='legend'){ const L=document.getElementById('legend'); if(L){ L.classList.add('on'); L.innerHTML=systemLegend(); } } return; }
  const r=e.target.closest('tr.frow[data-uid]'); if(r&&openUnit===BOARD){ const u=unitByUid(r.dataset.uid); if(u){ humanMove(); warpTo(u); } }
});
function show(u){
  if(u&&u.kind==='thread-star'){ CM.openThread(u.thread); return; }
  const old=document.getElementById('sayi');if(openUnit&&old){drafts[openUnit.uid]=old.value;saveDrafts();}
  openUnit = u; if(typeof mateExplain==='function') mateExplain(u,true);
  stopPane();
  try{ document.getElementById('panel').style.setProperty('--accent', sectorOfUnit(u).accent); }catch(e){}
  if((u.kind==='area'||u.kind==='dir')&&!u.remote){ renderCard(u); return; }
  document.getElementById('panel').classList.remove('card');
  const rows = isDK(u) ? [['status',u.status]] : [['role',u.role],['status',u.status],['served', u.kind==='channel'
      ? u.served+' frames' : (u.served? u.served+' jobs':'never dispatched')],
    ['tour',u.tour]];
  if(u.kind==='portal')rows.push(['machine',u.machine==='__home__'?String(W.name||'home').toLowerCase()+' (home)':u.machine]);
  if(u.remote&&u.kind==='seat')rows.push(['machine',u.machine],['pane id',u.pane_id||''],['agent',u.agent||'(none)'],['cwd',u.cwd||''],['title',u.title||'']);
  if(u.kind==='seat'&&!u.remote)rows.push(['seat',seatTarget(u)],['window',u.window_id||''],['pane id',u.pane_id||''],['command',u.command||'']);
  if(u.kind==='ship')rows.push(['source',u.source],['cwd',u.cwd||'(machine deck)']);
  if(u.kind==='ship'&&u.t3&&typeof u.t3==='object') rows.push(['task',[u.t3.state,u.t3.app].filter(Boolean).map(String).join(' · ')]);
  if(u.pane) rows.push(['pane',u.pane]);
  if(u.idle!=null && u.kind!=='seat' && !isDK(u)) rows.push(['last seen', CM.daysAgo(u.idle)]);
  { const el=(typeof effortLine==='function')?effortLine(u):''; if(el) rows.push(['effort', el]); }
  const hp=clamp(Number(u.hp)||0,0,100);
  let extra = (u.served?'<div class="bar"><i style="width:'+hp+'%"></i></div>':'')+
    (u.cause?'<div class="cause"><b>CAUSE</b><br>'+esc(u.cause)+'</div>':'');
  if(isDK(u)){ const d=districtPanel(u); rows.push(...d.rows); extra += d.extra; }
  { const href=t3Url(u); if(href) extra += '<div class="say"><a class="go" data-t3open="1" href="'+attr(href)+'" target="_blank" rel="noopener noreferrer">open in T3 ›</a></div>'; }
  if(u.kind==='portal'){
    if(u.machine==='__home__'){
      extra += '<div class="say"><div class="viewonly">return gateway home</div><form id="homef"><button type="submit" id="homebtn">Return home</button></form><div class="saym" id="saym"></div></div>';
    } else if(!DESK){
      extra += '<div class="say"><div class="viewonly">'+(REMOTE?'other machines are reached from the desktop, not from a phone.':'this is a static copy of the page, so the gateway cannot cross or re-render from here. <a href="'+LIVE_URL+'">Open the live game</a> (the world seat must be running).')+'</div><div class="saym" id="saym"></div></div>';
    } else {
      const si=SYNCED[u.machine]; const syncedTxt = si ? (si.present ? ('synced world '+ageS(si.age_s)+' old'+(si.stale?' — too old to show':'')+(si.generated?' · rendered '+esc(si.generated):'')) : 'no synced world yet') : 'synced world: unknown';
      extra += '<div class="say"><div class="viewonly">gateway to '+esc(u.machine)+' · status '+esc(u.status)+'<br>'+syncedTxt+'</div><form id="crossf"><button type="submit" id="crossbtn">Cross to '+esc(u.machine)+'</button></form>'+
        '<form id="renderf" style="margin-top:8px"><button type="submit" id="renderbtn">Re-render '+esc(u.machine)+' now</button></form><div class="saymeta"><span>runs the renderer on that machine once over ssh (constant command, once a minute, audited); the sync bus carries the new world back</span></div><div class="saym" id="saym"></div></div>';
    }
  } else if(u.remote){
    extra += '<div class="terminalbar"><span>'+esc(u.machine)+' · '+esc(u.pane_id||'')+'</span><span class="grow"></span><span id="panerev">remote snapshot</span><button type="button" id="copybtn">copy</button><button type="button" id="followbtn">follow on</button></div>';
    extra += '<pre class="live" id="plive" tabindex="0">'+(u.pane?esc(u.pane):'(empty pane)')+'</pre>';
    if(u.talkable){
      extra += '<div class="say"><form id="sayf"><textarea id="sayi" maxlength="4000" autocomplete="off" placeholder="Draft for '+attr(u.pane_id)+' on '+attr(u.machine)+'; Ctrl/Cmd+Enter sends"'+(DESK?'':' disabled')+'></textarea><button type="submit" id="sendbtn" disabled>Send to '+esc(u.pane_id)+'</button></form><div class="saymeta"><span>Human send only; agent compose never submits.</span><span class="count" id="saycount">0/4000</span></div><div class="saym" id="saym">'+esc(LIVE?(u.talk_reason||'ready'):'static file — view only')+'</div></div>';
    }else{
      extra += '<div class="say"><div class="viewonly">'+esc(DESK?(u.talk_reason||'view only'):VIEWONLY)+'</div><div class="saym" id="saym"></div></div>';
    }
  }
  if(u.kind==='seat'&&!u.remote){
    extra += '<div class="terminalbar"><span>'+esc(u.window_id||'')+' · '+esc(u.command||'(none)')+'</span><span class="termnote" id="termnote" role="status" aria-live="polite"></span><span class="grow"></span><span id="panerev">snapshot</span><button type="button" id="copybtn">copy</button><button type="button" id="followbtn">follow on</button></div>';
    extra += '<div class="live term" id="pterm" hidden></div>';
    extra += '<pre class="live" id="plive" tabindex="0">'+(u.pane?esc(u.pane):'(empty pane)')+'</pre>';
    if(u.composable){
      extra += '<div id="permitbox" hidden><div class="pq" id="permitq"></div><div class="prow"><button type="button" id="allowbtn">Allow</button><button type="button" class="deny" id="denybtn">Deny</button></div></div>';
      extra += '<div class="say"><form id="sayf"><textarea id="sayi" maxlength="4000" autocomplete="off" placeholder="Draft for '+attr(seatTarget(u))+'; Ctrl/Cmd+Enter sends"'+(DESK?'':' disabled')+'></textarea><button type="submit" id="sendbtn" disabled>Send to '+esc(seatTarget(u))+'</button></form><div class="saymeta"><span>Human send only; agent compose never submits. Allow/Deny is a separate verb for a printed prompt.</span><span class="count" id="saycount">0/4000</span></div><div class="saym'+(u.talkable?'':' err')+'" id="saym">'+esc(DESK?(u.talk_reason||'loading live pane…'):VIEWONLY)+'</div></div>';
    }else{
      extra += '<div class="say"><div class="viewonly">'+esc(DESK?(u.talk_reason||'view only'):VIEWONLY)+'</div><div class="saym" id="saym"></div></div>';
    }
  }
  extra += verbsHtml(u);
  document.getElementById('pbody').innerHTML = !VEC&&!MOB ? chromeUnitCardHtml(u,rows,extra) :   // plain words on top, the internals under More (the desktop and a tablet)
    '<div class="'+(u.kind==='seat'?'seatview':'')+'"><div class="seathead"><div class="kind">'+esc(u.kind)+'</div><h2>'+esc(u.name)+'</h2>'+
    '<dl>'+rows.map(r=>'<dt>'+esc(r[0])+'</dt><dd'+(r[0]==='status'?' id="pstatus"':'')+'>'+esc(r[1])+'</dd>').join('')+'</dl></div>'+extra+'</div>';
  const panel=document.getElementById('panel');panel.classList.toggle('seat',(u.kind==='seat')&&VEC);panel.classList.add('on');panel.setAttribute('aria-hidden','false');document.getElementById('wrap').classList.add('panel-open');syncPanelW();CM.panelSync();
  const dc=document.getElementById('doorcopy'); if(dc) dc.addEventListener('click',()=>{
    const el=document.getElementById('doorcmd'); if(!el||!navigator.clipboard||!navigator.clipboard.writeText){dc.textContent='copy unavailable';return;}
    navigator.clipboard.writeText(el.textContent).then(()=>{dc.textContent='copied';setTimeout(()=>dc.textContent='copy command',900);}).catch(()=>{dc.textContent='copy failed';}); });
  if(u.kind==='portal'){
    const cf=document.getElementById('crossf');if(cf)cf.addEventListener('submit',ev=>{ev.preventDefault();crossTo(u.machine);});
    const rf=document.getElementById('renderf');if(rf)rf.addEventListener('submit',ev=>{ev.preventDefault();
      const b=document.getElementById('renderbtn'),m=document.getElementById('saym'); if(b)b.disabled=true; if(m){m.className='saym';m.textContent='rendering on '+u.machine+'…';}
      api('/remote/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({machine:u.machine})})
        .then(j=>{ if(m){m.textContent='rendered there ('+esc((j.tail||[]).slice(-1)[0]||'ok')+') — syncing back'; } setTimeout(pollSynced,20000); setTimeout(pollSynced,60000); })
        .catch(e=>{ if(m){m.className='saym err';m.textContent=e.message;} })
        .finally(()=>{ if(b) setTimeout(()=>{b.disabled=false;},60000); }); });
    const hf=document.getElementById('homef');if(hf)hf.addEventListener('submit',ev=>{ev.preventDefault();goHome();});
  } else if(u.remote){
    const copy=document.getElementById('copybtn');if(copy)copy.addEventListener('click',()=>{
      const el=document.getElementById('plive');if(!el)return;
      if(!navigator.clipboard||!navigator.clipboard.writeText){copy.textContent='copy unavailable';return;}
      navigator.clipboard.writeText(el.textContent).then(()=>{copy.textContent='copied';setTimeout(()=>copy.textContent='copy',900);}).catch(()=>{copy.textContent='copy failed';});
    });
    const follow=document.getElementById('followbtn');if(follow)follow.addEventListener('click',()=>{autoFollow=!autoFollow;follow.textContent='follow '+(autoFollow?'on':'off');if(autoFollow){const el=document.getElementById('plive');el.scrollTop=el.scrollHeight;}});
    const epoch=++paneEpoch;
    if(DESK && !u.snapshot){loadRemotePane(u,epoch);paneTimer=setInterval(()=>loadRemotePane(u,epoch),2000);}
    const f=document.getElementById('sayf');
    const t=document.getElementById('sayi');
    if(t){t.value=typeof drafts[u.uid]==='string'?drafts[u.uid].slice(0,4000):'';t.addEventListener('input',()=>{drafts[u.uid]=t.value;saveDrafts();updateCount();});t.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();if(f)f.requestSubmit();}});updateCount();}
    if(f) f.addEventListener('submit',ev=>{
      ev.preventDefault();
      const t=document.getElementById('sayi'),m=document.getElementById('saym'),b=document.getElementById('sendbtn');
      if(!t||!openUnit||!openUnit.talkable||!t.value.trim())return;
      const sendText=t.value.replace(/\\s*[\\r\\n]+\\s*/g,' ').trim();if(!sendText)return;
      if(sendText!==t.value){t.value=sendText;drafts[u.uid]=sendText;saveDrafts();updateCount();}
      b.disabled=true;
      m.className='saym'; m.textContent='sending…';
      api('/remote/say',{method:'POST', headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({machine:u.machine,target:u.pane_id,text:sendText})})
        .then(()=>{t.value='';drafts[u.uid]='';saveDrafts();m.textContent='sent';updateCount();loadRemotePane(u,epoch);})
        .catch(e=>{m.className='saym err';m.textContent=e.message;updateCount();});
    });
  }
  if(u.kind==='seat'&&!u.remote){
    const copy=document.getElementById('copybtn');if(copy)copy.addEventListener('click',()=>{
      const el=document.getElementById('plive');if(!el)return;
      if(!navigator.clipboard||!navigator.clipboard.writeText){copy.textContent='copy unavailable';return;}
      navigator.clipboard.writeText(el.textContent).then(()=>{copy.textContent='copied';setTimeout(()=>copy.textContent='copy',900);}).catch(()=>{copy.textContent='copy failed';});
    });
    const follow=document.getElementById('followbtn');if(follow)follow.addEventListener('click',()=>{autoFollow=!autoFollow;follow.textContent='follow '+(autoFollow?'on':'off');if(autoFollow){const el=document.getElementById('plive');el.scrollTop=el.scrollHeight;}});
    const epoch=++paneEpoch;
    if(DESK){loadPane(u,epoch);paneTimer=setInterval(()=>loadPane(u,epoch),2000);termOpen(u);}
    const f=document.getElementById('sayf');
    const t=document.getElementById('sayi');
    if(t){t.value=typeof drafts[u.uid]==='string'?drafts[u.uid].slice(0,4000):'';t.addEventListener('input',()=>{if(agentTyper&&agentTyper.el===t){clearInterval(agentTyper.timer);agentTyper=null;}drafts[u.uid]=t.value;saveDrafts();updateCount();});t.addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();f.requestSubmit();}});updateCount();}
    const allow=document.getElementById('allowbtn'), deny=document.getElementById('denybtn');
    if(allow) allow.addEventListener('click',()=>doPermit(u,'allow'));
    if(deny) deny.addEventListener('click',()=>doPermit(u,'deny'));
    if(DESK && u.status==='WAITING') renderPermit({waiting:true,permit:{quote:u.pane||'',allow:'y',deny:'n'}});
    if(f) f.addEventListener('submit',ev=>{
      ev.preventDefault();
      const t=document.getElementById('sayi'),m=document.getElementById('saym'),b=document.getElementById('sendbtn');
      if(!t||!openUnit||!openUnit.talkable||paneRevision==null||!t.value.trim())return;
      const sendText=t.value.replace(/\\s*[\\r\\n]+\\s*/g,' ').trim();if(!sendText)return;
      if(sendText!==t.value){t.value=sendText;drafts[u.uid]=sendText;saveDrafts();updateCount();}
      b.disabled=true;
      m.className='saym'; m.textContent='sending…';
      api('/say',{method:'POST', headers:{'Content-Type':'application/json'},
                  body:JSON.stringify({seat:seatTarget(u),text:sendText,revision:paneRevision})})
        .then(()=>{t.value='';drafts[u.uid]='';saveDrafts();m.textContent='sent';updateCount();loadPane(u,epoch);})
        .catch(e=>{m.className='saym err';m.textContent=e.message;updateCount();});
    });
  }
}
// ---- district panels: the stat block for an area, a room, a cron line or a process ----
function ageStr(a){ return a==null ? '—' : CM.daysAgo(Math.max(0,a)); }
function fmtMin(m){ return m>=1440?(Math.round(m/144)/10)+'d':(m>=60?Math.round(m/60)+'h':m+'m'); }
function shq(s){ return /^[A-Za-z0-9_.~\\/-]+$/.test(s)?s:"'"+String(s).replace(/'/g,"'\\\\''")+"'"; }
function vcls(s){ s=String(s||''); return /^[A-Z_ ]+$/.test(s)?s.replace(/ /g,'_'):''; }
function tbl(rows){ return '<table class="tbl">'+rows.map(r=>'<tr>'+r.map(c=>'<td'+(c[1]?' class="'+vcls(c[1])+'"':'')+'>'+esc(c[0])+'</td>').join('')+'</tr>').join('')+'</table>'; }
function doorBlock(cmd, worker){
  return '<div class="say"><div class="viewonly">open door — runs in YOUR terminal: a tmux window in this directory'+
    (worker?' as '+esc(worker):' as a plain shell (no worker assigned in areas.json)')+
    '. A Claude-style harness started there loads this directory\\'s door file by itself. The game does not run this for you yet.</div>'+
    '<pre class="cmd" id="doorcmd">'+esc(cmd)+'</pre><button type="button" id="doorcopy">copy command</button></div>';
}
function districtPanel(u){
  const rows=[], cmdDoor='bash '+String(W.tool||'.')+'/door.sh '+shq(u.kind==='area'?u.name:u.path);
  let extra='';
  const pool = u.remote ? remoteUnits : units;
  if(u.synced) rows.push(['source', 'synced world of '+u.machine+' · rendered '+((remoteInfo&&remoteInfo.generated)||'?')+' · '+ageS(remoteInfo&&remoteInfo.age_s)+' old — a snapshot that machine made of itself, not live']);
  if(u.kind==='area'){
    rows.push(['path',u.path]);
    if(u.alias_of){ rows.push(['alias of',u.alias_of]); return {rows:rows, extra:'<div class="viewonly">a symlink: the real area is '+esc(u.alias_of)+'. Nothing is homed here.</div>'}; }
    if(u.tag || !u.remote) rows.push(['owner tag', u.tag ? (u.tag+' — '+(u.tag_desc||'')+' (roots map, as of '+(u.tag_asof||'undated')+')') : 'not in the roots map: unclassified']);
    rows.push(['on disk', fmtN(u.files)+' files · '+fmtN(u.dirs)+' dirs · '+u.size+(u.capped?' · walk hit its time budget':'')+(u.shallow?' · listed one level deep (9P mount)':'')]);
    rows.push(['newest', u.age==null?'no files':(ageStr(u.age)+' (mtime) · '+String(u.newest_path||'').replace(u.path+'/',''))]);
    rows.push(['mix', (u.ext||[]).map(e=>e.ext+' '+e.pct+'%').join(' · ')+' · code '+u.code_share+'%']);
    rows.push(['kind', u.area_kind+(u.kind_guessed?' — hub guess; rule on it in areas.json':' — owner ruled')]);
    { const m=(deck==='home'?MOONS:remoteMoons).find(x=>x.uid===u.uid); if(m&&m.type) rows.push(['type', TYPE_NAME[m.type]+' — '+m.typeWhy+(u.regen_share!=null?' · rebuildable share '+u.regen_share+'%':'')]); }
    rows.push(['door', u.door ? (u.door.file+' · '+u.door.lines+' lines: a seat opened here loads it') : 'none — a seat opened here loads NO instructions']);
    rows.push(['worker', u.worker ? (u.worker+' ('+(u.worker_status||'?')+') · '+(u.worker_cmd||'')+(u.worker_why?' — '+u.worker_why:'')) : ('unassigned — door.sh opens a plain shell'+(u.worker_note?' · '+u.worker_note:''))]);
    if(u.git) rows.push(['git', u.git.branch+' · '+u.git.commits_30d+' commits in 30d · '+u.git.dirty+' dirty · last commit '+ageStr(u.git.last_commit_age)]);
    rows.push(['agents here', u.seats&&u.seats.length ? u.seats.map(s=>String(s).replace('seat:','')).join(', ') : 'none']);
    rows.push(['receipts', u.dispatch_mentions+' dispatch reports mention this path']);
    rows.push(['tasks', (u.crons||[]).length+' cron lines · '+(u.procs||[]).length+' process groups']);
    extra += u.remote ? remoteDoor(u) : doorBlock(cmdDoor, u.worker);
    extra += '<div class="say"><button type="button" class="newtask" data-path="'+attr(u.path||'')+'">new task here</button> <button type="button" class="promptbtn" data-uid="'+attr(u.uid)+'">prompt an agent about this</button></div>';
    const crons = pool.filter(x=>x.kind==='cron'&&x.area===u.uid), procs = pool.filter(x=>x.kind==='proc'&&x.area===u.uid);
    if(crons.length) extra += '<h3>cron lines homed here</h3>'+tbl(crons.map(c=>[[c.verdict,c.status],[c.schedule],[c.tool],[c.room||''],[c.receipt]]));
    if(procs.length) extra += '<h3>running here now</h3>'+tbl(procs.map(p=>[[p.name,'LIVE'],[p.elapsed],[p.room||''],[p.args]]));
    extra += '<h3>rooms ('+(u.rooms||[]).length+')</h3>'+tbl((u.rooms||[]).map(r=>[[r.name,r.status],[r.files==null?'not walked':fmtN(r.files)+' files'],[r.size||''],[ageStr(r.age)],[(r.door?'door ':'')+(r.git?'git':'')]]));
    if((u.root_files||[]).length) extra += '<h3>files at the top level ('+u.root_files_total+') · click a file to read</h3><table class="tbl">'+u.root_files.map(f=>fileRow(f[0], u.path+'/'+f[0], [f[1], ageStr(f[2])])).join('')+'</table>';
  } else if(u.kind==='dir'){
    rows.push(['path',u.path]);
    rows.push(['on disk', u.files==null?'not walked (9P mount)':fmtN(u.files)+' files · '+u.size]);
    rows.push(['newest', ageStr(u.age)+' (mtime)']);
    rows.push(['door', u.door?(u.door.file+' · '+u.door.lines+' lines'):'none']);
    rows.push(['git', u.git?(u.git.branch+' · '+u.git.commits_30d+' commits in 30d · '+u.git.dirty+' dirty · last commit '+ageStr(u.git.last_commit_age)):'not a repo root']);
    extra += u.remote ? remoteDoor(u) : doorBlock(cmdDoor, null);
    extra += '<div class="say"><button type="button" class="newtask" data-path="'+attr(u.path||'')+'">new task here</button> <button type="button" class="promptbtn" data-uid="'+attr(u.uid)+'">prompt an agent about this</button></div>';
    const kids=u.children||[];
    if(kids.length||u.children_total) extra += '<h3>inside ('+(u.children_total==null?'not walked':u.children_total)+') · click a file to read</h3><table class="tbl">'+kids.map(k=>((k[4]&1)||String(k[0]).indexOf(' → ')>=0) ? '<tr><td>'+filePlanet(k[0],true,unitTheme(u))+esc(k[0])+'</td><td>'+(k[1]==null?'':fmtN(k[1])+' files')+'</td><td>'+esc(k[2])+'</td><td>'+ageStr(k[3])+'</td><td>'+((k[4]&2)?'door':'')+'</td></tr>' : fileRow(k[0], u.path+'/'+k[0], ['', k[2], ageStr(k[3]), ''])).join('')+'</table>';
  } else if(u.kind==='cron'){
    rows.push(['schedule',u.schedule+(u.period_min?' (about every '+fmtMin(u.period_min)+')':'')]);
    rows.push(['verdict',u.verdict+' — '+u.receipt]);
    rows.push(['log',u.log||'(none)']);
    rows.push(['homed by',u.homed_by+(u.room?' → '+u.room:'')]);
    extra += '<pre class="cmd">'+esc(u.command)+'</pre>';
  } else if(u.kind==='proc'){
    rows.push(['processes',u.count+' · pid '+(u.pids||[]).join(', ')]);
    rows.push(['running for',u.elapsed]);
    rows.push(['homed by','a path in its command line'+(u.room?' → '+u.room:'')]);
    extra += '<pre class="cmd">'+esc(u.args)+'</pre>';
  }
  return {rows:rows, extra:extra};
}
// ---- THE BOARD: two queues, kept apart on purpose ------------------------------------------
// ON YOU: parked decisions mined from the decision files (as before) plus audit items only the owner can
// rule on. AGENT-READY: audit items an agent can do mechanically once the user nods; "draft to seat"
// puts the task into that seat's say box as a draft — the user presses send, the game never does.
function taskItems(){ const T=(W.tasks&&W.tasks.items)||[]; return Array.isArray(T)?T:[]; }
function areaUnit(name){ return units.find(u=>u.kind==='area'&&u.name===name) || units.find(u=>u.kind==='dir'&&u.path===name); }
function warpTo(u){ if(!u) return; humanMove(); pilot=null; px=u.gx*TILE; py=u.gy*TILE+64; closeAll(); show(u); }
// hand to an agent: the bridge runs `dispatch <runner> "<item text>" --bg` from the item folder;
// the runner is picked from the registry minus dead seats. Status comes back from receipts only.
function runSel(kind,id){ const rs=(W.dispatchable||[]); if(!rs.length) return '';
  return '<select class="runsel" data-kind="'+attr(kind)+'" data-id="'+attr(id)+'"><option value="">hand to agent…</option>'+rs.map(r=>'<option value="'+attr(r)+'">'+esc(r)+'</option>').join('')+'</select>'; }
function runsHtml(key){ const rs=(W.runs&&W.runs[key])||[]; return rs.map(r=>{ const bad=/FAIL|KILL|no receipt|UNVERIFIED/i.test(r.status);
  return '<div class="run" title="'+attr(r.rid||'')+'"><span class="rs'+(bad?' bad':'')+'">'+esc(r.status)+'</span> · '+esc(r.runner)+(r.min?' · '+esc(r.min)+' min':'')+(r.at?' · '+CM.agoOf(r.at):'')+(r.out?' · <button type="button" class="rout" data-path="'+attr(r.out)+'">report</button>':'')+'</div>'; }).join(''); }
// ---- board rows: one state per item, read from the auditor file (advice) and the user's rulings ----
// state: open (audit says still the user's) · ret (audit says retire) · fix (agent task, command needs
// a fix) · none (unaudited). The state colours the rail, sorts the column and highlights the one
// button the audit recommends. The click is still the user's.
function auditOf(id){ return (W.audit&&W.audit[id])||null; }
function stateOf(id){ const a=auditOf(id); if(!a) return 'none'; const v=String(a.verdict||'');
  if(/still-open/.test(v)) return 'open'; if(/changed/.test(v)) return 'fix'; if(/not-his/.test(v)) return 'hand';
  if(/resolved|already|stale|duplicate|not-a-decision|wrong/.test(v)) return 'ret'; return 'none'; }
const STATE_LABEL={open:'still yours',ret:'can close',fix:'command needs a fix',hand:'not yours',none:'not checked'};
function suggested(id){ const a=auditOf(id); if(!a) return ''; const r=String(a.recommended||'').toLowerCase()+' '+String(a.verdict||'');
  if(/retire as done|resolved-done|already-done|already-decided/.test(r)) return 'done';
  if(/retire as stale|resolved-stale|stale|duplicate|not-a-decision|wrong/.test(r)) return 'stale'; return ''; }
function advHtml(id){ const a=auditOf(id); if(!a) return '';
  return '<div class="adv"><span class="arrow">→</span> '+esc(a.recommended||'')+
    (a.evidence?'<details><summary>why</summary><div class="ev">'+esc(a.evidence)+'</div></details>':'')+'</div>'; }
function chip(cls,text){ return '<span class="chip '+cls+'">'+esc(text)+'</span>'; }
function ageChip(days){ const d=Number(days); if(days==null||!Number.isFinite(d)||d<0) return chip('age','undated'); return chip('age '+(d>14?'old':(d>3?'mid':'new')), CM.ageWords(d)); }   // "5 weeks", never "40D"
// a parked line reads as what it asks ("Deploy of job-1455 awaiting decision on the rollout"): its bullet, date and ids
// gone (CM.shortTitle); the file and line it came from ride the row's tooltip, never its text
function questRow(x){ const id=String(x.id||''), st=stateOf(id), sug=suggested(id);
  return '<div class="q st-'+st+'" title="'+attr([x.src,id].filter(Boolean).join(' · '))+'">'+
    '<div class="rail"></div><div class="body">'+
      '<div class="head">'+chip('st '+st, STATE_LABEL[st])+ageChip(x.days)+'</div>'+
      '<div class="tx">'+esc(CM.shortTitle(x.text,90)||String(x.text||''))+'</div>'+advHtml(id)+runsHtml('quest:'+id)+
    '</div><div class="sr">'+
      '<button type="button" class="rule'+(sug==='done'?' sug':'')+'" data-id="'+attr(id)+'" data-ruling="done" title="your ruling: it got done. Retires the line; the file keeps it">done</button>'+
      '<button type="button" class="rule'+(sug==='stale'?' sug':'')+'" data-id="'+attr(id)+'" data-ruling="stale" title="your ruling: no longer needed. Retires the line; the file keeps it">not needed</button>'+
      runSel('quest',id)+'</div></div>'; }
function retiredRow(x){ return '<div class="q st-gone" title="'+attr(x.src||'')+'"><div class="rail"></div><div class="body"><div class="head">'+chip('st gone','closed · '+(x.ruling==='stale'?'not needed':String(x.ruling||'')))+(x.days!=null?ageChip(x.days):'')+'</div><div class="tx">'+esc(CM.shortTitle(x.text,90)||String(x.text||''))+'</div></div><div class="sr"><button type="button" class="rule" data-id="'+attr(x.id)+'" data-ruling="reopen">reopen</button></div></div>'; }
// the runner the RUN button uses: the one with the most recent DONE receipt, else the first offered
function defaultRunner(){ const rs=(W.dispatchable||[]); let best=null, bt=0;
  Object.values(W.runs||{}).forEach(list=>(list||[]).forEach(r=>{ if(/^DONE/.test(String(r.status||''))&&r.runner&&rs.includes(r.runner)){ const t=Date.parse(r.at||0)||0; if(t>bt){ bt=t; best=r.runner; } } }));
  return best||rs[0]||''; }
function taskRow(t){
  const v=String(t.verdict||'ASK'), who=t.who==='agent'?'agent':'you', id=String(t.id||''), st=stateOf(id), sug=suggested(id);
  const seats=units.filter(u=>u.kind==='seat'&&!u.remote&&u.composable), dr=defaultRunner();
  const draft = who==='agent' ? (dr?'<button type="button" class="run1" data-id="'+attr(id)+'" data-runner="'+attr(dr)+'" title="an agent does this in the background; the receipt lands under this row">▶ RUN with '+esc(dr)+'</button>':'')+
    '<select class="draftsel" data-id="'+attr(id)+'"><option value="">or: draft to an agent…</option><option value="__door__">open an instructions file in its folder first</option>'+
    seats.map(s=>'<option value="'+attr(s.uid)+'">'+esc(s.alias||s.name)+(s.talkable?'':' (view only now)')+'</option>').join('')+'</select>' :
    '<button type="button" class="rule'+(sug==='done'?' sug':'')+'" data-id="'+attr(id)+'" data-ruling="done" title="your ruling: handled. Retires the row; the file keeps it">done</button>'+
    '<button type="button" class="rule'+(sug==='stale'?' sug':'')+'" data-id="'+attr(id)+'" data-ruling="stale" title="your ruling: no longer needed. Retires the row; the file keeps it">not needed</button>';
  const ver=(W.rounds&&W.rounds.verified&&W.rounds.verified[t.id])?chip('path '+(W.rounds.verified[t.id].ok?'ok':'gone'), (W.rounds.verified[t.id].ok?'path ok':'PATH GONE')+' · '+CM.agoOf(W.rounds.verified[t.id].at)):'';
  return '<div class="q task st-'+st+' '+esc(who)+'"><div class="rail"></div><div class="body">'+
      '<div class="head">'+chip('v v'+v, v)+chip('area', String(t.area||''))+chip('st '+st, STATE_LABEL[st])+ver+'<span class="src">'+esc(t.effort||'')+'</span></div>'+
      '<div class="tx"><b>'+esc(t.finding||'')+'</b></div><div class="ev">'+esc(t.evidence||'')+(t.target?' → '+esc(t.target):'')+'</div>'+
      (t.command?'<details class="cmdd"><summary>command</summary><pre class="cmd">'+esc(t.command)+'</pre></details>':'')+
      advHtml(id)+runsHtml('task:'+id)+
    '</div><div class="sr">'+
      draft+(who==='agent'?runSel('task',id).replace('hand to agent…','another agent…'):'')+'<button type="button" class="go" data-area="'+attr(t.area||'')+'" data-path="'+attr(t.path||'')+'">show on map</button></div></div>';
}
function orderRows(list, idOf){ const rank={open:0,fix:0,hand:1,none:2,ret:3};
  return list.slice().sort((a,b)=>{ const ra=rank[stateOf(idOf(a))], rb=rank[stateOf(idOf(b))]; if(ra!==rb) return ra-rb; return (Number(b.days)||0)-(Number(a.days)||0); }); }
function countStates(ids){ const c={open:0,ret:0,fix:0,hand:0,none:0}; ids.forEach(i=>{ c[stateOf(i)]++; }); return c; }
function stripHtml(c, total){ return '<div class="strip">'+chip('tot', total+' in all')+
  (c.open?chip('st open', c.open+' still yours'):'')+(c.fix?chip('st fix', c.fix+' need a command fixed'):'')+(c.hand?chip('st hand', c.hand+' not yours'):'')+
  (c.ret?chip('st ret', c.ret+' can close'):'')+(c.none?chip('st none', c.none+' not checked'):'')+'</div>'; }
function divider(text){ return '<div class="div">'+esc(text)+'</div>'; }
function decisionRow(c){ const v=CM.plainText(c.voice||'',160), t=CM.shortTitle(c.title||c.voice||'',90)||CM.contactWord(c);
  return '<div class="q st-open" data-cid="'+attr(c.id)+'"><div class="rail"></div><div class="body"><div class="head">'+ageChip(c.age_days)+'</div>'+
    '<div class="tx">'+esc(t)+'</div>'+(v&&v!==t&&v!==String(c.title||'')?'<div class="ev">'+esc(v)+'</div>':'')+
    '</div><div class="sr"><button type="button" class="open1" data-cid="'+attr(c.id)+'">'+(c.source==='ledger'?'see it on Today':'open')+'</button></div></div>'; }
// Every decision W.counts counts is on the board (P2: one home per kind): the parked lines and the board items
// it lists already, then the ones that live elsewhere (the ledger, a claim waiting on you) read from the sky. A parked
// line the sky folded into a ledger row (the same open loop) is not listed twice. Home deck only: another machine's board
// is that machine's lines.
function boardDecisions(qs){ if(deck!=='home'||!skyOk()) return {qs:qs,extra:[]};
  const dec=skyContacts().filter(c=>CM.bucket(c)==='decision'), ids=new Set(dec.map(c=>c.id));
  return {qs:qs.filter(q=>ids.has('quest:'+String(q.id||''))||!dec.some(c=>c.source==='quest')),
    extra:dec.filter(c=>c.source!=='quest'&&c.source!=='board').sort((a,b)=>(Number(b.age_days)||0)-(Number(a.age_days)||0))}; }
// ---- SWEEP: the loop. Hand every agent-ready item to ONE runner, one at a time, and wait for
// that item's receipt (exit code / digest line) before the next. Stops on the first failure or on
// the stop button. Progress is on the board; nothing is marked done without a receipt.
let SWEEP=null;
function runStatus(key){ const rs=(W.runs&&W.runs[key])||[]; return rs.length?rs[rs.length-1]:null; }
function isSettled(r){ return !!r && !/^running$|^no receipt$/.test(String(r.status||'')); }
async function sweepAll(runner){
  if(!DESK){ toast(REMOTE?'desktop only':'static file: live mode needed'); return; }
  if(SWEEP||!runner) return;
  const todo=taskItems().filter(t=>t.who==='agent'&&!(runStatus('task:'+t.id)&&/^DONE/.test(runStatus('task:'+t.id).status)));
  SWEEP={stop:false,i:0,n:todo.length,runner:runner,cur:'',err:''};
  renderBoard();
  for(const t of todo){
    if(SWEEP.stop) break;
    SWEEP.i++; SWEEP.cur=String(t.id); renderBoard();
    let j=null;
    try{ j=await api('/dispatch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'task',id:String(t.id),runner:runner})}); }
    catch(e){ SWEEP.err=String(t.id)+': '+e.message; break; }
    const t0=Date.now();
    while(!SWEEP.stop){
      await new Promise(r=>setTimeout(r,8000));
      try{ applyWorld(await api('/state?fresh=1')); }catch(e){}
      renderBoard();
      const st=runStatus('task:'+t.id);
      if(st&&st.rid===j.rid&&isSettled(st)){ if(!/^DONE/.test(String(st.status))) SWEEP.err=String(t.id)+': '+st.status; break; }
      if(Date.now()-t0>45*60000){ SWEEP.err=String(t.id)+': no receipt after 45 min'; break; }
    }
    if(SWEEP.err) break;
    await new Promise(r=>setTimeout(r,6000));
  }
  const fin=SWEEP; SWEEP=null; renderBoard();
  toast(fin.err?'sweep stopped at '+fin.i+'/'+fin.n+': '+fin.err:(fin.stop?'sweep stopped by you at '+fin.i+'/'+fin.n:'sweep done: '+fin.n+' handed off, each with its result'));
}
function sweepHtml(){ const rs=(W.dispatchable||[]); if(!rs.length) return '';
  if(SWEEP) return '<div class="sweep"><button type="button" class="sweepbtn stop">stop sweep</button><span>'+SWEEP.i+'/'+SWEEP.n+' · '+esc(SWEEP.runner)+' · now: '+esc(SWEEP.cur)+(SWEEP.err?' · '+esc(SWEEP.err):'')+'</span></div>';
  return '<div class="sweep"><select id="sweepsel">'+rs.map(r=>'<option value="'+attr(r)+'">'+esc(r)+'</option>').join('')+'</select><button type="button" class="sweepbtn">sweep all: one at a time, each with its result</button></div>'; }
let showRetired=false;
function renderBoard(){
  const tasks=taskItems(), mine=tasks.filter(t=>t.who!=='agent'), theirs=tasks.filter(t=>t.who==='agent');
  const BD=boardDecisions(curQuests()), qs=BD.qs, extra=BD.extra, ret=(W.retired||[]);
  const leftIds=qs.map(q=>String(q.id||'')).concat(mine.map(t=>String(t.id||''))), rightIds=theirs.map(t=>String(t.id||''));
  const cl=countStates(leftIds), cr=countStates(rightIds);
  const gen=(W.audit&&Object.keys(W.audit).length&&W.audit[Object.keys(W.audit)[0]].at)?' · checked '+esc(String(W.audit[Object.keys(W.audit)[0]].at).slice(0,10)):'';
  const retNote=ret.length?' · '+ret.length+' closed by you <button type="button" class="showret">'+(showRetired?'hide':'show')+'</button>':'';
  // left column: parked lines and the user's audit calls interleaved by state, retire-ready last under a divider
  const left=orderRows(qs.map(q=>({kind:'q',days:q.days,id:q.id,row:q})).concat(mine.map(t=>({kind:'t',days:0,id:t.id,row:t}))), r=>String(r.id||''));
  let leftHtml='', lastGroup='';
  left.forEach(r=>{ const st=stateOf(String(r.id||'')); const g=(st==='ret')?'ret':(st==='none'?'none':'open');
    if(g!==lastGroup){ lastGroup=g; leftHtml+=divider(g==='ret'?'THE CHECK SAYS THESE CAN CLOSE · '+cl.ret+' · one click each; the file keeps the line':(g==='none'?'NOT CHECKED YET · '+cl.none:'STILL YOURS · '+(cl.open+cl.fix+cl.hand)+' · oldest first')); }
    leftHtml+= r.kind==='q'?questRow(r.row):taskRow(r.row); });
  const ledg=extra.filter(c=>c.source==='ledger'), rest=extra.filter(c=>c.source!=='ledger');
  if(rest.length) leftHtml+=divider('TASKS WAITING ON YOU · '+rest.length+' · they go on when you answer')+rest.map(decisionRow).join('');
  if(ledg.length) leftHtml+=divider('In the ledger · '+ledg.length+' · a row closes when you rule on it')+ledg.map(decisionRow).join('');
  const nLeft=qs.length+mine.length+extra.length;
  const right=orderRows(theirs, t=>String(t.id||'')).map(taskRow).join('');
  document.getElementById('qbody').innerHTML =
    '<div class="how"><b>How this works</b> · Left: decisions only you can make. <b>done</b> = handled, <b>not needed</b> = no longer matters; either closes the row. Right: <b>▶ RUN</b> gives the row to an agent that works in the background; what it did appears under the row within minutes. <b>sweep all</b> works down the whole column. <b>show on map</b> only flies you there.</div>'+
    '<div class="cols"><div class="col"><h3>Yours · <span data-count="decisions">'+nLeft+'</span> '+CM.countNoun('decisions',nLeft)+'</h3>'+stripHtml(cl, nLeft)+
    '<div class="sub">decisions from your notes, the board, the ledger and the tasks waiting on you'+gen+retNote+'. The highlighted button is what the check suggests.</div>'+
    (leftHtml||'<div class="q"><div class="body">Nothing waiting on you.</div></div>')+
    (showRetired?divider('Closed by you · '+ret.length)+ret.map(retiredRow).join(''):'')+'</div>'+
    '<div class="col"><h3>An agent can do · '+theirs.length+'</h3>'+stripHtml(cr, theirs.length)+sweepHtml()+
    '<div class="sub">Small, reversible jobs. Draft to agent: the text lands in that agent&#39;s window and you send it. Hand to agent: it runs in the background and what it did shows here.</div>'+
    (right||'<div class="q"><div class="body">Nothing an agent can do on its own right now.</div></div>')+'</div></div>';
}
function toggleQuests(force){
  const q = document.getElementById('quests');
  if(force===true && q.classList.contains('on')) return;
  q.classList.toggle('on');
  if(!q.classList.contains('on')) return;
  renderBoard();
}
function boardMsg(el, text, err){ const sr=el.closest('.sr')||el.parentElement; let m=sr.querySelector('.bmsg'); if(!m){ m=document.createElement('span'); m.className='bmsg'; sr.appendChild(m); } m.className='bmsg'+(err?' err':''); m.textContent=text; }
async function boardAct(path, body, el, okText){
  if(!DESK){ boardMsg(el,REMOTE?'desktop only':'static file: live mode needed',true); return null; }
  el.disabled=true; boardMsg(el,'…');
  try{
    const j=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    try{ applyWorld(await api('/state?fresh=1')); }catch(e){}
    renderBoard();
    if(okText) toast(okText(j));
    return j;
  }catch(e){ el.disabled=false; boardMsg(el,e.message,true); return null; }
}
document.getElementById('qbody').addEventListener('click', e=>{
  const r=e.target.closest('button.rule');
  if(r){ boardAct('/resolve',{id:r.dataset.id,ruling:r.dataset.ruling},r, j=>'ruling '+j.ruling+' recorded · '+j.retired+' retired'); return; }
  const o=e.target.closest('button.rout'); if(o){ previewFile(o.dataset.path,o); return; }
  const r1=e.target.closest('button.run1'); if(r1){ boardAct('/dispatch',{kind:'task',id:r1.dataset.id,runner:r1.dataset.runner},r1, j=>'running on '+j.runner+' · what it did lands under the row'); return; }
  const oc=e.target.closest('button.open1'); if(oc){ const c=skyContacts().find(x=>x.id===oc.dataset.cid); if(c){ toggleQuests(false); openContact(c); } return; }
  const sh=e.target.closest('button.showret'); if(sh){ showRetired=!showRetired; renderBoard(); return; }
  const sw=e.target.closest('button.sweepbtn'); if(sw){ if(SWEEP){ SWEEP.stop=true; renderBoard(); } else { const sel=document.getElementById('sweepsel'); sweepAll(sel?sel.value:''); } return; }
});
document.getElementById('qbody').addEventListener('change', e=>{
  const s=e.target.closest('select.runsel'); if(!s||!s.value) return;
  boardAct('/dispatch',{kind:s.dataset.kind,id:s.dataset.id,runner:s.value},s, j=>'handed to '+j.runner+' · what it did lands under the row');
});
document.getElementById('qbody').addEventListener('click', e=>{
  const g=e.target.closest('button.go'); if(!g) return;
  const u=areaUnit(g.dataset.area)||units.find(x=>x.path===g.dataset.path); if(u) warpTo(u);
});
document.getElementById('qbody').addEventListener('change', e=>{
  const s=e.target.closest('select.draftsel'); if(!s||!s.value) return;
  const t=taskItems().find(x=>x.id===s.dataset.id); if(!t) return;
  if(s.value==='__door__'){ const p=String(t.path||''); const folder=(units.some(u=>u.kind==='dir'&&u.path===p)||units.some(u=>u.kind==='area'&&u.path===p))?p:p.replace(/[/][^/]*$/, '');
    const st=s.parentElement.querySelector('small')||s.parentElement; openDoor(t.area&&units.some(u=>u.kind==='area'&&u.name===t.area&&u.path===folder)?t.area:folder, st).then(seat2=>{ if(seat2){ s.innerHTML='<option value="'+attr(seat2.uid)+'" selected>'+esc(seat2.name)+' · in this folder</option>'; s.dispatchEvent(new Event('change',{bubbles:true})); } }); return; }
  const seat=unitByUid(s.value); if(!seat) return;
  const text='Task from the container audit ('+String((W.tasks&&W.tasks.source)||'audit')+'): '+String(t.finding||'')+
    ' Evidence: '+String(t.evidence||'')+' Path: '+String(t.path||'')+(t.target?' Target: '+String(t.target):'')+
    (t.command?' Reversible command, run only if you agree it is safe: '+String(t.command):'')+' Report back with what you did and the exit code.';
  drafts[seat.uid]=text; saveDrafts(); closeAll(); show(seat);
});
// ---- FILE PREVIEW: click a file row in any panel; the bridge reads it (text only, capped) ----
function fileRow(name, path, cells){ return '<tr class="frow" data-path="'+attr(path)+'"><td class="fname">'+filePlanet(name)+esc(name)+'</td>'+cells.map(c=>'<td>'+esc(c)+'</td>').join('')+'</tr>'; }
async function previewFile(path, anchorEl){
  let box=document.getElementById('preview'); if(!box){ box=document.createElement('div'); box.id='preview'; }
  anchorEl.insertAdjacentElement('afterend', box);
  box.innerHTML='<h3>file</h3><div class="saym">reading…</div>';
  if(!DESK){ box.innerHTML='<h3>file</h3><div class="viewonly">'+(REMOTE?'files are read from the desktop':'static file — live mode needed to read files')+'</div>'; return; }
  try{
    const j=await api('/file?path='+encodeURIComponent(path));
    const opener = (W.opener ? '<button type="button" class="openbtn" data-path="'+attr(path)+'">open in editor</button>' : '<span class="saymeta">set "opener" in areas.json to open files from here</span>');
    box.innerHTML='<h3>file · '+esc(j.size)+' · modified '+esc(j.mtime)+(j.truncated?' · first 64 KB shown':'')+'</h3>'+
      '<div class="terminalbar"><span>'+esc(path)+'</span><span class="grow"></span><button type="button" class="copypath" data-path="'+attr(path)+'">copy path</button><button type="button" class="promptbtn" data-path="'+attr(path)+'">prompt about this file</button>'+opener+'</div>'+
      (j.binary ? '<div class="viewonly">binary file, not shown</div>' : '<pre class="live" style="max-height:46vh">'+esc(j.text||'(empty file)')+'</pre>')+'<div class="saym" id="openmsg"></div>';
  }catch(e){ box.innerHTML='<h3>file</h3><div class="saym err">'+esc(e.message)+'</div>'; }
}
document.getElementById('pbody').addEventListener('click', e=>{
  const r=e.target.closest('tr.frow'); if(r){ previewFile(r.dataset.path, r.closest('table')); return; }
  const vb=e.target.closest('button.vrun'); if(vb){ runVerb(vb); return; }
  const pb=e.target.closest('button.promptbtn'); if(pb){ const u=pb.dataset.uid?unitByUid(pb.dataset.uid):null; openPrompter(u||{path:pb.dataset.path, name:String(pb.dataset.path||'').split('/').pop(), kind:'file'}); return; }
  const c=e.target.closest('button.copypath'); if(c){ navigator.clipboard&&navigator.clipboard.writeText(c.dataset.path).then(()=>{c.textContent='copied';}).catch(()=>{}); return; }
  const o=e.target.closest('button.openbtn'); if(o){ const m=document.getElementById('openmsg'); o.disabled=true;
    api('/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:o.dataset.path})})
      .then(j=>{ if(m){m.className='saym';m.textContent='opened with '+(j.opener||'the configured opener');} })
      .catch(err=>{ if(m){m.className='saym err';m.textContent=err.message;} }).finally(()=>{ setTimeout(()=>{o.disabled=false;},1500); }); }
});
addEventListener('keydown', e=>{ if(!typing() && !modKey(e) && e.key.toLowerCase()==='f' && !e.repeat) FLOWS=!FLOWS; });
addEventListener('keydown', e=>{ if(typing()||modKey(e)) return; if(e.key==='-'||e.key==='_') setZoom(ZOOM/1.15); else if(e.key==='='||e.key==='+') setZoom(ZOOM*1.15); else if(e.key==='0') zoomHome(); });
cv.addEventListener('wheel', e=>{ e.preventDefault(); setZoom(ZOOM*(e.deltaY<0?1.1:1/1.1)); }, {passive:false});
setInterval(()=>{ const z=document.getElementById('zoomtag'); if(!z) return; const txt='ZOOM '+ZOOM.toFixed(2)+'×'; if(z.textContent!==txt) z.textContent=txt; }, 300);
function closeAll(){
  { const C=document.getElementById('composer'); if(C&&C.classList.contains('on')){ C.classList.remove('on'); document.getElementById('side').classList.remove('compose'); AIM=null; } }
  { const P=document.getElementById('prompter'); if(P) P.classList.remove('on'); }
  { const L=document.getElementById('legend'); if(L) L.classList.remove('on'); }
  { const M=document.getElementById('meta'); if(M) M.classList.remove('on'); }
  { const G=document.getElementById('pgpanel'); if(G&&G.classList.contains('on')) pgClose(); }
  if(!FOCUS_CAM.uid&&FOCUS_CAM.phase!=='user'&&FOCUS_CAM.phase!=='out'){ camOff.x=0; camOff.y=0; }   // with a planet open the focus camera eases back to the view it left (ui/focus.js)
  const input=document.getElementById('sayi');if(openUnit&&input){drafts[openUnit.uid]=input.value;saveDrafts();}
  stopPane();
  const panel=document.getElementById('panel');panel.classList.remove('on');panel.setAttribute('aria-hidden','true');
  document.getElementById('quests').classList.remove('on');
  document.getElementById('wrap').classList.remove('panel-open');openUnit=null; PANEL_W=0;
}
function esc(s){ return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function attr(s){return esc(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function taskbar(){
  const t = document.getElementById('taskbar');
  if(deck!=='home'){
    const n = remoteUnits.filter(u=>u.kind==='seat'&&!u.remoteReturn).length;
    const syn = (typeof remoteInfo!=='undefined'&&remoteInfo&&remoteInfo.generated)?' · synced '+esc(remoteInfo.generated):'';
    t.innerHTML = '<span class="tb"><i></i>'+esc(deck)+' deck</span>'
      + '<span class="tb">'+esc(n)+' agent'+(n===1?'':'s')+' live</span>'
      + '<span class="spacer">home rebuilt '+esc(W.generated)+syn+'</span>';
    return;
  }
  const live = W.chars.filter(isShip).length;
  const jobs = (W.inflight||[]).map(j=>
    '<span class="tb"><i></i><b>'+esc(j.who)+'</b> '+esc(j.min)+'m</span>').join('');
  const s = W.summary||{};
  t.innerHTML = (jobs || '<span class="tb">'+esc(W.running||0)+' working ships</span>')
    + '<span class="tb">'+esc(live)+' ships</span>'
    + (W.ship_sources||[]).map(s=>'<span class="tb">'+esc(s.message)+'</span>').join('')
    + (s.areas!=null ? '<span class="tb">'+esc(s.areas)+' areas · '+esc(s.rooms)+' rooms · '+esc(s.doors)+' doors · '+esc(s.workers)+' workers</span>'
      + '<span class="tb"><i style="background:'+(s.crons_no_receipt?'#e3b341':'#3fb950')+'"></i>'+esc(s.crons)+' crons · '+esc(s.crons_running)+' running · '+esc(s.crons_no_receipt)+' no receipt</span>'
      + '<span class="tb"><i></i>'+esc(s.procs)+' processes in '+esc(s.proc_groups)+' groups</span>' : '')
    + '<span class="tb">'+esc(W.chars.length)+' units</span>'
    + '<span class="tb">'+esc(W.quests.length+((W.tasks&&W.tasks.items)||[]).filter(t=>t.who!=='agent').length)+' on you · '+esc(((W.tasks&&W.tasks.items)||[]).filter(t=>t.who==='agent').length)+' agent-ready</span>'
    + (LIVE?'<span class="tb"><i></i>'+(REMOTE?'phone':'live origin')+'</span>':'<span class="tb idlebar">static · view only</span>')
    + '<span class="spacer">world rebuilt '+esc(W.generated)+'</span>';
}
function quickbar(){
  const q=document.getElementById('quick');
  q.innerHTML=units.filter(u=>u.kind==='seat'&&['1','3','4','6'].includes(u.quick_key)).sort((a,b)=>Number(a.quick_key)-Number(b.quick_key)).map(u=>
    '<button type="button" data-uid="'+attr(u.uid)+'"><b>'+esc(u.quick_key)+'</b>'+esc(u.alias||u.name)+'</button>').join('');
}
// the HUD numbers come from W: at load (the bridge serves a page whose baked text is older than W) and on every refresh
// the card's big number is the open decisions (W.counts), with how old the oldest is and where it waits:
// "53 decisions · oldest 54 days (board)" (P5: never a bare count of days with no noun)
function hudWorld(){
  const sc=document.querySelector('#hud .sc'), c=CM.counts(), n=Number.isInteger(c.decisions)?c.decisions:0, od=c.oldest_decision||{};
  if(sc){ sc.textContent=String(n); const small=document.createElement('small');
    small.textContent=CM.countNoun('decisions',n)+(n&&Number.isInteger(od.days)?' · oldest '+od.days+' day'+(od.days===1?'':'s')+(od.source?' ('+({board:'board',quest:'your notes',ledger:'ledger',fleet:'task list'}[od.source]||od.source)+')':''):'');
    sc.appendChild(small); }
  const gen=document.getElementById('hudgen'); if(gen) gen.textContent='updated '+String(W.generated||'');
}
function applyWorld(w){
  if(!w||!Array.isArray(w.chars)||!Array.isArray(w.quests))return;
  joinRooms(w);
  if(Array.isArray(w.constellations)) W.constellations=w.constellations;
  const selected=openUnit&&openUnit.uid;
  const selRemote=openUnit&&openUnit.remote;
  const keepTasks=(Date.now()-TASKS_AT<20000)?W.agent_tasks:null;   // a fresher /tasks poll beats a 15 s old render
  const fromWorld=Array.isArray(w.agent_tasks)?w.agent_tasks:null, worldRunners=Array.isArray(w.task_runners)?w.task_runners:null;
  const prevTasks=W.agent_tasks;   // applyTasks compares the incoming list with this one, so put it back before calling
  const seatsWere=radioSnap();     // each seat, ship and contact before this load: a change calls in on the radio (threads: applyTasks)
  // the sky and the count are one snapshot: a /tasks reply's (CM._snapRev, bridge rev) stays over a world rendered before
  // it (w.rendered, the same clock); a world rendered after it replaces both and becomes the revision in hand
  const worldRev=typeof w.rendered==='number'&&Number.isFinite(w.rendered)?w.rendered:0, keepSnap=(CM._snapRev||0)>worldRev?{sky:W.sky,counts:W.counts}:null;
  Object.assign(W, w); if(keepTasks) W.agent_tasks=keepTasks;
  if(keepSnap){ W.sky=keepSnap.sky; W.counts=keepSnap.counts; } else CM._snapRev=worldRev;
  if(!keepTasks&&fromWorld&&typeof applyTasks==='function'){ W.agent_tasks=prevTasks; const prev=TASKS_AT; applyTasks({tasks:fromWorld,runners:worldRunners,world:true}); TASKS_AT=prev; }
  place();quickbar();radioWorld(seatsWere);skyWorld(seatsWere);
  if(VIEW==='today') renderToday();
  if(typeof renderDash==='function') renderDash();
  hudWorld();
  taskbar();
  cmSceneTouch();
  if(selected&&!openTaskId){
    if(selRemote) return;
    const u=units.find(x=>x.uid===selected);
    if(u){openUnit=u;const st=document.getElementById('pstatus');if(st)st.textContent=String(u.status||'');}
    else closeAll();
  }
}
hudWorld();

function unitByUid(uid){ if(uid===BOARD.uid)return BOARD;
  return units.find(u=>u.uid===uid)||remoteUnits.find(u=>u.uid===uid); }
function approach(u){
  const moons=deck==='home'?MOONS:remoteMoons;
  const m=u.kind==='area'&&moons.find(m=>m.uid===u.uid);
  const radius=u.uid===BOARD.uid?LY.star.r:m?m.r:u.kind==='machine'&&LY.planets[u.name]?62:0;
  const offset=radius>20?radius+18:30;
  return {x:u.gx*TILE,y:u.gy*TILE+offset};
}
let flightFlash=null,lastDock=null,visitHoldUntil=0;
function toast(text,ms){ flightFlash={text:String(text),until:Date.now()+(ms||2800)}; if(MOB) mToast(text,ms); }
function startVisit(u,arrive){
  // Track a stable target, not coordinates that go stale during the 15-second world refresh.
  const current=currentTargets().find(v=>v.uid===u.uid);
  if(!current){activeAction=null;flightFlash={text:'TARGET IS NOT ON THIS DECK',until:Date.now()+2200};return;}
  const p=approach(current);
  pilot={source:'agent',targetUid:current.uid,targetDeck:deck,x:p.x,y:p.y,arrive};
  lastDock=null;
  flightFlash={text:'HUB → '+String(current.name||current.uid).toUpperCase(),until:Date.now()+3000};
  camOff.x=0;camOff.y=0;
  if(!typing()&&!openTaskId&&!composerOpen())closeAll();   // never clobber a task you are watching
}
function fillAgentDraft(u,text){
  show(u);const t=document.getElementById('sayi'),m=document.getElementById('saym');if(!t)return;
  const existing=String(drafts[u.uid]||t.value||'');
  if(existing){if(m){m.className='saym err';m.textContent='agent compose skipped — human draft preserved';}return;}
  const wanted=String(text).slice(0,4000),chunk=Math.max(1,Math.ceil(wanted.length/80));let at=0;
  if(m){m.className='saym';m.textContent='agent is typing…';}
  const timer=setInterval(()=>{if(!document.body.contains(t)){clearInterval(timer);agentTyper=null;return;}at=Math.min(wanted.length,at+chunk);t.value=wanted.slice(0,at);drafts[u.uid]=t.value;saveDrafts();updateCount();if(at>=wanted.length){clearInterval(timer);agentTyper=null;if(m)m.textContent='agent prepared this draft; review it and send explicitly';}},14);
  agentTyper={timer,el:t};
}
function pumpActions(){
  if(activeAction||pilot||!actionQueue.length||performance.now()<visitHoldUntil)return;
  const a=actionQueue.shift();activeAction=a;
  if(a.action==='dismiss'){closeAll();activeAction=null;return;}
  if(a.action==='stop'){pilot=null;activeAction=null;actionQueue.length=0;return;}
  if(a.action==='move'){
    const dirs={up:[0,-1],down:[0,1],left:[-1,0],right:[1,0]},v=dirs[a.direction];
    if(!v){activeAction=null;return;}const tiles=clamp(Number(a.tiles)||1,1,12);
    pilot={source:'agent',x:clamp(px+v[0]*tiles*TILE,minGX*TILE,maxGX*TILE),y:clamp(py+v[1]*tiles*TILE,minGY*TILE,maxGY*TILE)};return;
  }
  if(!['visit','compose'].includes(a.action)&&CM.action(a)){activeAction=null;return;}
  const u=unitByUid(String(a.target||''));
  if(!u){activeAction=null;return;}
  if(a.action==='visit'){   // the hub flies there and docks; nothing opens on you (your rule, 2026-10-07)
    startVisit(u,fresh=>{ if(fresh&&typeof mateSay==='function'&&MATE&&!MATE.thinking) mateSay('The hub is at '+String(fresh.name||fresh.uid)+'. Click it if you want to see what it is doing.'); }); return; }
  if(a.action==='compose'&&u.kind==='seat'&&u.configured&&u.composable&&typeof a.text==='string'){
    startVisit(u,fresh=>{if(fresh&&fresh.kind==='seat'&&fresh.configured&&fresh.composable)fillAgentDraft(fresh,a.text);});return;
  }
  activeAction=null;
}
let controlBoot=null,controlAfter=0,controlDelay=700;
function acceptActions(j){
  if(!j||!Array.isArray(j.commands))return;
  if(controlBoot!==null&&j.boot_id!==controlBoot){controlBoot=j.boot_id;controlAfter=0;pilot=null;activeAction=null;actionQueue.length=0;return;}
  controlBoot=j.boot_id;
  const incoming=j.commands.slice().sort((a,b)=>Number(a.sequence)-Number(b.sequence));
  for(const command of incoming){
    const seq=Number(command&&command.sequence),a=command&&command.action;
    if(!Number.isSafeInteger(seq)||seq<=controlAfter||!a||typeof a!=='object')continue;
    if(actionQueue.length>=32)break;controlAfter=seq;
    const normalized=Object.assign({seq},a,{action:a.type});
    if(normalized.action==='stop'){pilot=null;activeAction=null;actionQueue.length=0;continue;}
    actionQueue.push(normalized);
  }
}
async function pollControl(){
  if(!DESK)return;
  try{acceptActions(await api('/control?after='+encodeURIComponent(controlAfter)));controlDelay=700;}
  catch(e){ if(sessionLost(e)) return; controlDelay=Math.min(5000,controlDelay*1.5);}
  setTimeout(pollControl,controlDelay);
}

// ---- pointer: click = fly / inspect; drag = pan the camera; hover = tooltip ----
// A mouse takes the first path in each handler, exactly as before. A finger (pointerType touch) takes the second: one finger pans
// after a slop and taps to pick with a fat radius, a held finger opens a menu, two fingers pinch about their midpoint.
let pointerStart=null, dragging=false, lastMouse=null, suppressClickUntil=0;
const FINGERS=new Map(), TAP_SLOP=10, PRESS_SLOP=8, PRESS_MS=450, TAP_PX=22;
let GEST=null, PINCH=null, PRESS_T=0, TOUCH_AT=0;
function touchUI(){ return MOB||document.documentElement.classList.contains('t'); }
function stageXY(e){ const r=cv.getBoundingClientRect(), w=r.width||VW, h=r.height||VH;
  return {x:e.clientX-r.left, y:e.clientY-r.top, kx:VW/w, ky:VH/h}; }
// the world point under a client point, from the camera the last frame drew
function worldAt(e){ const p=stageXY(e); return {x:p.x*p.kx/ZOOM+camx, y:p.y*p.ky/ZOOM+camy}; }
cv.addEventListener('pointerdown',e=>{
  if(e.pointerType==='touch'){ fingerDown(e); return; }
  pointerStart=[e.clientX,e.clientY]; lastMouse=[e.clientX,e.clientY]; dragging=false;});
cv.addEventListener('pointermove',e=>{
  if(e.pointerType==='touch'){ fingerMove(e); return; }
  const r=cv.getBoundingClientRect();
  mouseW={x:(e.clientX-r.left)*(VW/r.width)/ZOOM+camx, y:(e.clientY-r.top)*(VH/r.height)/ZOOM+camy, sx:e.clientX-r.left, sy:e.clientY-r.top};
  if(pointerStart && (e.buttons&1)){
    if(!dragging && Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])>12) dragging=true;
    if(dragging && lastMouse){ camOff.x -= (e.clientX-lastMouse[0])*(VW/r.width)/ZOOM; camOff.y -= (e.clientY-lastMouse[1])*(VH/r.height)/ZOOM; }
  }
  lastMouse=[e.clientX,e.clientY];
});
cv.addEventListener('pointerleave',()=>{ mouseW=null; hover=null; });
cv.addEventListener('pointerup',e=>{
  if(e.pointerType==='touch'){ fingerUp(e,false); return; }
  if(dragging){ dragging=false; pointerStart=null; suppressClickUntil=Date.now()+200; return; }
  if(!pointerStart||Math.hypot(e.clientX-pointerStart[0],e.clientY-pointerStart[1])>12){pointerStart=null;return;}
  pointerStart=null;humanMove();const r=cv.getBoundingClientRect();
  const wx=(e.clientX-r.left)*(VW/r.width)/ZOOM+camx,wy=(e.clientY-r.top)*(VH/r.height)/ZOOM+camy;
  const hit=pickAt(wx,wy);
  BURSTS.push({x:hit?(hit===BOARD?LY.star.x:hit.gx*TILE):wx,y:hit?(hit===BOARD?LY.star.y:hit.gy*TILE):wy,r:hit?focusRadius(hit):6,t0:performance.now()});
  if(hit)activate(hit);else pilot={source:'human',x:clamp(wx,minGX*TILE,maxGX*TILE),y:clamp(wy,minGY*TILE,maxGY*TILE)};
});
cv.addEventListener('pointercancel',e=>{ if(e.pointerType==='touch'){ fingerUp(e,true); return; } pointerStart=null;});
// a phone only dispatches a tap to what has a click listener near the finger; this one does nothing, the tap itself is the pointerup
cv.addEventListener('click',()=>{});
cv.addEventListener('contextmenu',e=>{ if(touchUI()||Date.now()-TOUCH_AT<1500) e.preventDefault(); });
function fingerDown(e){
  TOUCH_AT=Date.now(); if(e.isPrimary){ FINGERS.clear(); PINCH=null; GEST=null; }   // the first finger of a gesture: anything left from a lost pointerup goes
  FINGERS.set(e.pointerId,{x:e.clientX,y:e.clientY});
  try{ cv.setPointerCapture(e.pointerId); }catch(x){}
  clearTimeout(PRESS_T); PRESS_T=0;
  if(FINGERS.size===1){
    GEST={x0:e.clientX,y0:e.clientY,lx:e.clientX,ly:e.clientY,t0:Date.now(),pan:false,tap:true,eat:false};
    pointerStart=null; dragging=false; mouseW=null; hover=null;
    PRESS_T=setTimeout(fingerPress,PRESS_MS);
  } else if(FINGERS.size===2) pinchStart();
}
function pinchMid(){
  const p=Array.from(FINGERS.values()); if(p.length<2) return null;
  const a=p[0], b=p[1], r=cv.getBoundingClientRect();
  return {d:Math.hypot(a.x-b.x,a.y-b.y)||1, x:(a.x+b.x)/2-r.left, y:(a.y+b.y)/2-r.top, kx:VW/(r.width||VW), ky:VH/(r.height||VH)};
}
// the second finger: no tap, no long press any more; the zoom to start from and the distance between the fingers are kept
function pinchStart(){
  clearTimeout(PRESS_T); PRESS_T=0; const m=pinchMid(); if(!m) return;
  dragging=true; if(GEST){ GEST.tap=false; GEST.pan=false; }
  PINCH={d0:m.d, Z0:ZOOM, x:m.x, y:m.y};
}
function fingerMove(e){
  const f=FINGERS.get(e.pointerId); if(!f) return; f.x=e.clientX; f.y=e.clientY;
  if(PINCH){
    // zoom about the old midpoint (the world under it stays under it), then follow the midpoint as it moves
    const m=pinchMid(); if(!m) return;
    const zp=ZOOM; setZoom(PINCH.Z0*m.d/PINCH.d0); const z1=ZOOM;
    camOff.x += (PINCH.x*m.kx-(VW-PANEL_W)/2)*(1/zp-1/z1);
    camOff.y += (PINCH.y*m.ky-(VH-SHEET_CUR)/2)*(1/zp-1/z1);
    camOff.x -= (m.x-PINCH.x)*m.kx/z1; camOff.y -= (m.y-PINCH.y)*m.ky/z1;
    PINCH.x=m.x; PINCH.y=m.y; return;
  }
  const g=GEST; if(!g) return;
  const dist=Math.hypot(e.clientX-g.x0,e.clientY-g.y0);
  if(!g.pan&&dist>TAP_SLOP){ g.pan=true; g.tap=false; dragging=true; clearTimeout(PRESS_T); PRESS_T=0; }
  else if(!g.pan&&dist>PRESS_SLOP){ clearTimeout(PRESS_T); PRESS_T=0; }          // too far to be a long press, not yet a pan
  if(g.pan){ const p=stageXY(e);
    camOff.x -= (e.clientX-g.lx)*p.kx/ZOOM; camOff.y -= (e.clientY-g.ly)*p.ky/ZOOM; g.lx=e.clientX; g.ly=e.clientY; }
}
function fingerUp(e,cancel){
  if(!FINGERS.delete(e.pointerId)) return;
  try{ cv.releasePointerCapture(e.pointerId); }catch(x){}
  const g=GEST;
  if(FINGERS.size){
    if(PINCH){ PINCH=null;
      if(FINGERS.size>=2) pinchStart();                                            // a third finger lifted: the other two carry on
      else if(g){ const f=Array.from(FINGERS.values())[0]; g.pan=true; g.tap=false; g.lx=f.x; g.ly=f.y; } }   // one is left: it pans from where it is
    return;
  }
  clearTimeout(PRESS_T); PRESS_T=0; PINCH=null; GEST=null; mouseW=null; hover=null;
  const moved=dragging; dragging=false;
  if(moved){ suppressClickUntil=Date.now()+200; return; }
  if(g&&g.eat){ suppressClickUntil=Date.now()+400; return; }                      // the lift of a long press is not a tap, and not a click on the sheet it opened
  if(cancel||!g||!g.tap) return;
  if(Date.now()-g.t0>=PRESS_MS||Math.hypot(e.clientX-g.x0,e.clientY-g.y0)>=TAP_SLOP) return;
  fingerTap(e);
}
// a card, the board, the legend or the prompter is "a sheet"; the composer is not
function sheetUp(){ return !composerOpen()&&['panel','quests','legend','prompter'].some(id=>{ const el=document.getElementById(id); return el&&el.classList.contains('on'); }); }
function fingerTap(e){
  const w=worldAt(e), hit=pickAt(w.x,w.y,TAP_PX/ZOOM);
  // empty ground: a sheet open (or a planet in focus) is put away; the ship never flies on a missed tap:
  // flying is a long press (Fly here) or the 3D view
  if(!hit){ if(sheetUp()||(!composerOpen()&&CM.focusUid())){ closeAll(); const L=document.getElementById('legend'); if(L) L.classList.remove('on'); if(MOB) mobSync(); } return; }
  humanMove();
  BURSTS.push({x:hit===BOARD?LY.star.x:hit.gx*TILE,y:hit===BOARD?LY.star.y:hit.gy*TILE,r:focusRadius(hit),t0:performance.now()});
  activate(hit);
}
// the rows a held finger offers: the same actions the keys had (E, N, P, the wingmate, flying)
function flyTo(hit,w){ const a=hit?approach(hit):w; humanMove(); pilot={source:'human',x:clamp(a.x,minGX*TILE,maxGX*TILE),y:clamp(a.y,minGY*TILE,maxGY*TILE)}; }
// in the order.8 gives: Open · New task here · Fly here · Fly in 3D · Ask <assistant>, then a prompt for an agent's terminal
function pressRows(hit,w){
  if(!hit) return [{label:'Fly here',run:()=>flyTo(null,w)},{label:'Recenter',run:()=>{ camOff.x=0; camOff.y=0; }}];
  const rows=[{label:'Open',run:()=>{ humanMove(); activate(hit); }}];
  if(hit===BOARD||hit.kind==='area'||hit.kind==='dir'||hit.kind==='task'||hit.cwd||hit.path) rows.push({label:'New task here',run:()=>{ MOB_CMAP=!!MOB&&!!document.body&&document.body.dataset.tab==='map'; openComposer(hit===BOARD?null:hit); }});
  rows.push({label:'Fly here',run:()=>flyTo(hit,w)});
  rows.push({label:'Fly in 3D',run:()=>CM.enter3d(hit===BOARD?{}:{target:String(hit.uid||'')})});
  rows.push({label:'Ask '+mateName(),run:()=>{ MATE.mute=0; mateExplain(hit,true); mtab('wingmate'); }});
  if(hit!==BOARD&&hit.kind!=='task') rows.push({label:'Prompt an agent',run:()=>openPrompter(hit)});
  return rows;
}
function fingerPress(){
  PRESS_T=0; const g=GEST; if(!g||g.pan||PINCH||FINGERS.size!==1) return;
  g.eat=true; g.tap=false;                                                         // whatever this does, the lift is not a tap
  if(!touchUI()||composerOpen()||typeof msheetOpen!=='function') return;
  const f=Array.from(FINGERS.values())[0], w=worldAt({clientX:f.x,clientY:f.y}), hit=pickAt(w.x,w.y,TAP_PX/ZOOM);
  CM.haptic('tap');
  BURSTS.push({x:hit?(hit===BOARD?LY.star.x:hit.gx*TILE):w.x,y:hit?(hit===BOARD?LY.star.y:hit.gy*TILE):w.y,r:hit?focusRadius(hit):6,t0:performance.now()});
  msheetOpen(hit?String(hit===BOARD?(W.name||'CONTAINER'):(hit.alias||hit.name||hit.uid||'Here')):'Here',pressRows(hit,w));
}
document.getElementById('quick').addEventListener('click',e=>{const b=e.target.closest('button[data-uid]');if(!b)return;const u=unitByUid(b.dataset.uid);if(u){humanMove();show(u);}});
document.querySelectorAll('#touchpad button').forEach(b=>{
  const key='v-'+b.dataset.dir,off=()=>{delete keys[key];};
  b.addEventListener('pointerdown',e=>{e.preventDefault();humanMove();pilot=null;keys[key]=1;b.setPointerCapture(e.pointerId);});
  b.addEventListener('pointerup',off);b.addEventListener('pointercancel',off);b.addEventListener('lostpointercapture',off);
});
document.getElementById('pclose').addEventListener('click',closeAll);
const gbbtn=document.getElementById('gbbtn');
if(gbbtn){ if(GB)gbbtn.classList.add('on');
  gbbtn.addEventListener('click',()=>{ toggleGB();
    try{ sessionStorage.setItem('pos', JSON.stringify([px,py])); }catch(e){}
    location.reload(); }); }

if(GB)document.body.classList.add('gb');
taskbar();quickbar();
if(deck!=='home'){
  if(!DESK){ deck='home'; saveDeck(); }
  else{
    const dm=deck;
    remoteMoons.length=0; buildRemoteDeck(dm,{tabs:[],note:'restoring '+dm+'…'});
    fetchRemote(dm).then(r=>{ if(deck!==dm) return;
      if(!r.live && !r.synced){ deck='home'; saveDeck(); remoteUnits=[]; remoteHalls=[]; remoteNote=''; return; }
      remoteState=r.live; remoteSynced=r.synced; remoteInfo=r.info;
      if(r.synced) buildRemoteSystem(dm, r.synced, r.live, r.info); else buildRemoteDeck(dm, r.live||{});
    }).catch(()=>{ if(deck===dm){ deck='home'; saveDeck(); remoteUnits=[]; remoteHalls=[]; remoteNote=''; } });
  }
}
setInterval(refreshRemote, 30000); setInterval(pollSynced, 60000); pollSynced();
// a bridge restart issues a new session cookie; a tab from before it is refused with 403. Reload
// (position, deck, drafts survive in sessionStorage) unless a say box is being typed in.
function sessionLost(e){ const msg=String((e&&e.body&&e.body.error)||(e&&e.message)||''); if(/UI session|forbidden origin|pair this device/i.test(msg) && !typing()){ location.reload(); return true; } return false; }
if(LIVE){setInterval(()=>api('/state').then(applyWorld).catch(sessionLost),15000);pollControl();}
else setInterval(()=>{if(!typing()&&!CM.mode3d())location.reload();},15000);
// ---- GLANCE layer ------------------------------------------------------------------------
// Desk routing is a status encoding, never a liveness signal: a seat that needs you takes one
// step toward the quest board; everything else stays at its station. No idle roaming.
function glanceRoute(){
  units.forEach(u=>{
    if(u.bgx==null){ u.bgx=u.gx; u.bgy=u.gy; }
    const hot = u.kind==='seat' && (u.status==='WAITING'||u.status==='blocked'||u.status==='DRAFT');
    u.gx = u.bgx + (hot ? Math.sign(BOARD.gx-u.bgx) : 0);
    u.gy = u.bgy + (hot ? Math.sign(BOARD.gy-u.bgy) : 0);
  });
}
// Chime: WebAudio needs a user gesture first; until then the flash carries it alone.
let audioCtx=null; function armAudio(){ try{ if(!audioCtx){ audioCtx=new (window.AudioContext||window.webkitAudioContext)(); } audioCtx.resume(); }catch(e){} }
addEventListener('keydown', armAudio); addEventListener('pointerdown', armAudio);
function chime(){ if(!audioCtx || audioCtx.state!=='running') return;
  try{ if(window.cmSfx&&typeof window.cmSfx.enabled==='function'&&!window.cmSfx.enabled()) return; }catch(e){}   // X mutes it too
  try{
  [[880,0],[1174,0.13]].forEach(([f,d])=>{ const o=audioCtx.createOscillator(), g=audioCtx.createGain();
    o.type='sine'; o.frequency.value=f; g.gain.value=0.0001; o.connect(g); g.connect(audioCtx.destination);
    const t0=audioCtx.currentTime+d; g.gain.setValueAtTime(0.0001,t0); g.gain.exponentialRampToValueAtTime(0.08,t0+0.01);
    g.gain.exponentialRampToValueAtTime(0.0001,t0+0.22); o.start(t0); o.stop(t0+0.24); }); }catch(e){} }
function renderPermit(snap){
  const box=document.getElementById('permitbox'); if(!box) return;
  const waiting=snap && snap.waiting && snap.permit;
  box.hidden=!waiting;
  if(!waiting) return;
  const q=document.getElementById('permitq');
  if(q) q.textContent=snap.permit.quote||'this seat is waiting on you';
  const d=document.getElementById('denybtn');
  if(d) d.hidden=!snap.permit.deny;
}
async function doPermit(u, decision){
  if(!DESK || !u) return;
  const m=document.getElementById('saym');
  try{
    const snap=await api('/pane?seat='+encodeURIComponent(seatTarget(u))+'&lines=40');
    if(!snap.waiting || !snap.permit) throw new Error('pane is not waiting on a prompt');
    if(decision==='deny' && snap.permit.deny==null) throw new Error('this prompt has no deny key');
    await api('/permit',{method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({seat:seatTarget(u), decision:decision, revision:snap.revision})});
    if(m){ m.className='saym'; m.textContent=decision+' sent'; }
    u.status='WORKING';
    if(openUnit && openUnit.uid===u.uid) loadPane(u, paneEpoch);
    glanceTick();
  }catch(e){
    if(m){ m.className='saym err'; m.textContent=e.message; }
    else { const el=document.getElementById('glance-alert'); if(el){ el.textContent=e.message; el.className='on'; } }
  }
}
function glancePermits(){
  const box=document.getElementById('permits'); if(!box) return;
  if(!DESK){ box.innerHTML=''; box.className=''; return; }
  const hot=units.filter(u=>u.kind==='seat' && !u.remote && u.status==='WAITING');
  box.className=hot.length?'on':'';
  box.innerHTML=hot.map(u=>'<div class="pcard"><b>'+esc(u.alias||u.name)+' needs you</b>'+
    (u.pane?'<div class="pq">'+esc(u.pane)+'</div>':'')+
    '<div class="prow"><button type="button" data-uid="'+attr(u.uid)+'" data-permit="allow">Allow</button>'+
    '<button type="button" class="deny" data-uid="'+attr(u.uid)+'" data-permit="deny">Deny</button>'+
    '<button type="button" class="open" data-uid="'+attr(u.uid)+'" data-permit="open">inspect</button></div></div>').join('');
  mobPerm();
}
function glanceTick(){
  const pool = (deck!=='home' && typeof remoteUnits!=='undefined') ? remoteUnits : units;
  const hot = pool.filter(u=>u.kind==='seat' && (u.status==='WAITING'||u.status==='blocked'||u.status==='DRAFT'));
  let seen; try{ seen = new Set(JSON.parse(sessionStorage.getItem('chimed')||'[]')); }catch(e){ seen=new Set(); }
  const fresh = hot.filter(u=>!seen.has(u.uid));
  if(fresh.length){ chime(); fresh.forEach(u=>seen.add(u.uid)); }
  [...seen].forEach(id=>{ if(!hot.some(u=>u.uid===id)) seen.delete(id); });   // forgotten once answered, so it can chime again
  try{ sessionStorage.setItem('chimed', JSON.stringify([...seen])); }catch(e){}
  let el = document.getElementById('glance-alert');
  if(!el){ el=document.createElement('div'); el.id='glance-alert'; document.getElementById('hud').appendChild(el); }
  el.textContent = hot.length ? (hot.length+' seat'+(hot.length>1?'s need you: ':' needs you: ')+hot.map(u=>u.name).join(', ')) : '';
  el.className = hot.length ? 'on' : '';
  document.title = (hot.length ? '(!) ' : '') + 'CONTAINER';
  glancePermits();
}
const permitsEl=document.getElementById('permits');
if(permitsEl) permitsEl.addEventListener('click', e=>{
  const b=e.target.closest('button[data-permit]'); if(!b) return;
  const u=unitByUid(b.dataset.uid); if(!u) return;
  if(b.dataset.permit==='open'){ humanMove(); show(u); return; }
  doPermit(u, b.dataset.permit);
});
// Click-to-focus: clicking a character opens it exactly as walking up does.
// (clicks are handled on pointerup above, with the same pick as hover: what lights up is what opens)
setInterval(glanceTick, 1000); glanceTick();
const _stepGlance = step; step = function(t){ orbitTick(t); glanceRoute(); return _stepGlance(t); };
wantFrame();
// ---- HUD: sector readout, COMMS (what each seat's pane actually says), receipts ticker -------
// The comms box is the radio panel: a portrait per seat, coloured by its state, and the LAST LINE
// OF ITS PANE as the message. Nothing is written for the seat; the pane tail is the voice.
// The ticker cycles the newest digest lines: receipts other seats posted, verbatim.
let tickI = -1;
let QUIET=true; try{ QUIET=localStorage.getItem('quiet')!=='0'; }catch(e){}
document.body.classList.toggle('quiet',QUIET);
function setQuiet(v){ QUIET=!!v; document.body.classList.toggle('quiet',QUIET); try{ localStorage.setItem('quiet',QUIET?'1':'0'); }catch(e){} }
let HERE_LABEL='';
function topbarTick(){
  let tb=document.getElementById('topbar');
  if(!tb){ tb=document.createElement('div'); tb.id='topbar'; document.getElementById('wrap').appendChild(tb);
    const kl=document.createElement('div'); kl.id='keyline';
    kl.innerHTML=[['n','N: new task'],['k','K: tasks'],['j','J: '+CM.word('j next contact')],['y','Y: '+CM.word('y sky / every unit')],['t','T: today'],['e','E or click: open'],['l','L: what am I looking at'],['q','Q: decisions'],['u','U: meta board'],['slash','/: ask '+mateName()],['h','H: '+CM.word('h full hud')],['v','V: theme'],['dash','[ ]: '+CM.word('[ ] rails')],['wasd','WASD: fly'],['esc','Esc: close']].map(k=>'<a data-k="'+k[0]+'">'+esc(k[1])+'</a>').join('');
    kl.addEventListener('click',e=>{ const a=e.target.closest('a[data-k]'); if(!a) return; const k=a.dataset.k;
      if(k==='j') return snapContact(1); if(k==='y') return setSky(!SKY);
      if(k==='n') openComposer(VIEW==='map'?(near&&near!==BOARD?near:null):null); else if(k==='k') toggleTasks(); else if(k==='t') setView('today'); else if(k==='e'){ if(near) activate(near); else showStar(); }
      else if(k==='l'){ const L=document.getElementById('legend'); if(L){ L.classList.toggle('on'); if(L.classList.contains('on')) L.innerHTML=systemLegend(); } }
      else if(k==='q') toggleQuests(); else if(k==='u') toggleMeta(); else if(k==='slash'){ const q=document.getElementById('mateq'); if(q){ mateShow(true); q.focus(); } } else if(k==='h') setQuiet(!QUIET); else if(k==='dash'){ dashShow(!document.getElementById('wrap').classList.contains('dash')); } else if(k==='wasd') toast('W or Up thrusts · A D turn · S brakes · Shift boosts · double-tap A or D rolls, S turns about',3600); else if(k==='esc') closeAll(); });
    tb.addEventListener('click',e=>{ if(e.target.closest('a.today')) setView('today'); else if(e.target.closest('a.tnewlink')) openComposer(null); else if(e.target.closest('a.tlink')) toggleTasks();
      else { const g=e.target.closest('[data-go]'); if(!g) return; const k=g.dataset.go;
        if(k==='tasks') toggleTasks(true); else if(k==='seats'){ const w=units.find(u=>u.kind==='seat'&&(u.status==='WAITING'||u.status==='DRAFT'))||units.find(u=>isShip(u)&&u.status==='WORKING'); if(w){ humanMove(); show(w); } }
        else if(k==='here'){ const b=nearestBody(px,py,MOONS,W.name||'CONTAINER'); const m=b&&MOONS.find(x=>x.label.toUpperCase()+' moon'===b.label); const u=m&&unitByUid(m.uid); if(u) activate(u); else showStar(); }
        else if(k==='today') setView('today'); } });
    document.getElementById('wrap').appendChild(kl); }
  const seatsHome=units.filter(u=>u.kind==='seat'&&!u.remote);
  const need=seatsHome.filter(u=>u.status==='WAITING'||u.status==='DRAFT').length;
  const running=(deck==='home'?units:remoteUnits).filter(u=>(isShip(u)&&u.status==='WORKING')||(u.kind==='task'&&TASK_WORKING.includes(u.status))).length;
  const findings=deck==='home'?(W.ship_sources||[]).filter(s=>['ABSENT','ERROR','PARTIAL','SNAPSHOT'].includes(s.status)):[];
  { const sd=deck==='home'?skyData():null; if(sd&&sd.error) findings.push({message:String(sd.error).slice(0,140)}); }
  const tc=taskCounts();
  const html='<a class="today">◀ TODAY</a><b>'+esc(deck==='home'?(W.name||'CONTAINER'):String(deck).toUpperCase())+'</b>'+
    '<a class="tlink tnewlink">+ NEW TASK</a><a class="tlink'+(tc.review?' need':'')+'">TASKS'+(tc.running?' · '+tc.running+' working':'')+(tc.review?' · '+tc.review+' to review':'')+'</a>'+
    (need?'<a class="hot" data-go="seats">'+need+' seat'+(need>1?'s need':' needs')+' you</a>':('<a data-go="seats">'+(running?'agents working':'seats quiet')+'</a>'))+
    '<a data-go="tasks">'+running+' running</a>'+(HERE_LABEL?'<a data-go="here">at '+esc(HERE_LABEL)+'</a>':'')+
    findings.map(s=>'<a class="old" data-go="today">'+esc(s.message)+'</a>').join('')+
    '<span class="sp">rebuilt '+esc(W.generated||'')+'</span>';
  if(tb.innerHTML!==html) tb.innerHTML=html;
}
// a retired box (hidden by the desktop chrome, ): its painting is skipped, never its data
function chromeHides(id){ const el=document.getElementById(id); return !!(el&&CM.chromeReady&&CM.chromeOn&&CM.chromeOn()&&!el.getClientRects().length); }
function hudTick(){
  p3PovSync();
  const rest=chromeHides('hud');
  if(!rest) topbarTick();
  const se=rest?null:document.getElementById('sector');
  if(deck!=='home'){ if(se){ se.innerHTML='SECTOR · <b>'+esc(String(deck).toUpperCase())+'</b> · THE STAR · THAT MACHINE'; se.style.setProperty('--sec','#ffd166'); } }
  else { const here = whereAmI(); sectorHere = here ? here.S : null; HERE_LABEL = here ? String(here.label||'') : '';
  if(se){ if(here){ se.innerHTML='SECTOR · <b>'+esc(here.label)+'</b> · '+esc(here.kind); se.style.setProperty('--sec', here.S.accent); }
    else { se.innerHTML='SECTOR · <b>open space</b>'; se.style.setProperty('--sec','#9fb4cc'); } } }
  if(W.demo){ let b=document.getElementById('demobanner'); if(!b){ b=document.createElement('div'); b.id='demobanner';
    b.textContent='DEMO · sample folders · a script plays the AI agent · no login'; document.getElementById('wrap').appendChild(b); } }
  // the rounds tag only while a rounds loop actually ran today; a dead loop is not a HUD line
  const R=W.rounds, roundsFresh=!!(R&&R.updated&&(Date.now()-Date.parse(R.updated))<86400000);
  let rt=document.getElementById('roundstag'); if(!roundsFresh){ if(rt) rt.remove(); }
  else { if(!rt){ rt=document.createElement('div'); rt.id='roundstag'; rt.className='hfull'; document.getElementById('hud').appendChild(rt); }
  if(R&&R.updated){ const age=(Date.now()-Date.parse(R.updated))/1000; const dead=age>1500;
    rt.style.color = dead?'#f85149':'#8ea2b8'; const idle = !R.step_uid && R.next_round_at; const left = idle ? Math.max(0,(Date.parse(R.next_round_at)-Date.now())/1000) : 0;
    rt.textContent = dead ? 'HUB ROUNDS · stopped '+CM.relTime(age) : ('HUB ROUNDS · round '+(R.round||0)+' · '+(idle ? 'next round in '+ageS(left)+' · touch .rounds-now to start one' : String(R.step||''))+(R.last_round_at?' · last full round '+CM.agoOf(R.last_round_at):'')); }
  else rt.textContent='HUB ROUNDS · not running (python3 rounds.py in a window)'; }
  if(!chromeHides('comms')) commsTick(); if(!chromeHides('ticker')) stripTick(); if(VEC) tacTick();
}
// COMMS rows: name · state word · one clean line. render-game.py cleans a tmux pane into u.line (the last
// line that is not a prompt, a banner or a box border); other seats and ships get the same cleaning here,
// on their one line. The panel folds to a single strip while every seat is idle.
const COMMS_WORD={WAITING:'needs you',DRAFT:'your draft',WORKING:'working',IDLE:'idle',LIMITED:'limited',ERROR:'error',BLANK:'blank',blocked:'needs you',idle:'idle',working:'working'};
const COMMS_QUIET=['IDLE','BLANK','idle','unknown','FROZEN'];
const COMMS_ESC=/\\x1b\\[[0-9;?]*[ -\\/]*[@-~]|\\x1b\\][^\\x07\\x1b]*(?:\\x07|\\x1b\\\\)|[\\x00-\\x1f\\x7f]/g;
const COMMS_NOISE=/bypass permissions|shift\\s*\\+\\s*tab|\\? for shortcuts|accept edits|auto-?accept|plan mode|permissions? (?:on|mode)|ctrl\\s*\\+\\s*\\w+ to |esc to (?:interrupt|cancel)|press up to edit|\\bwarnings? f\\d\\b|context left|auto-?compact/i;
const COMMS_PROMPT=/^(?:[❯›»>]|[$%#]\\s*$|\\S*@\\S*[:~]\\S*[$#%]|[~\\/]\\S*[$#%>](?:\\s|$))/;
function cleanLine(s,width){
  width=width||60; s=String(s||'').replace(COMMS_ESC,' ').replace(/[─━│┃╭╮╯╰═║┌┐└┘]/g,' ').replace(/\\s+/g,' ').trim();
  if(!s||COMMS_NOISE.test(s)||COMMS_PROMPT.test(s)) return '';
  s=s.replace(/^[^\\w(\\["'`\\/~.]+/,'').trim();
  return s.length>width?s.slice(0,width-1).trimEnd()+'…':s;
}
function commsLine(u){ return typeof u.line==='string'?u.line:cleanLine(u.pane); }
let COMMS_OPEN=false; try{ COMMS_OPEN=sessionStorage.getItem('comms')==='open'; }catch(e){}
// the vector look colours a seat's state: friendly (working), amber (needs you), hostile (broken), quiet
function tacComms(st){ return ['WORKING','working','LIVE'].includes(st)?'tf':['WAITING','DRAFT','blocked','LIMITED'].includes(st)?'ta':['ERROR','DOWN'].includes(st)?'th':'tq'; }
function commsRow(u,name,st){
  const hot=st==='WAITING'||st==='DRAFT'||st==='blocked', line=commsLine(u);
  return '<div class="row'+(hot?' hot':'')+(VEC?' '+tacComms(st):'')+'" role="button" tabindex="0" data-uid="'+attr(u.uid)+'" style="--c:'+attr(COL[st]||'#8b98a8')+'">'+
    '<span class="pt">'+shipIcon(shipColor(u))+'</span><b class="nm">'+esc(name)+'</b><span class="st">'+esc(COMMS_WORD[st]||String(st||'').toLowerCase())+'</span>'+
    '<span class="tl">'+esc(line)+'</span></div>';
}
function commsTick(){
  const box=document.getElementById('comms'), cb=document.getElementById('cbody'), sum=document.getElementById('csum'); if(!cb) return;
  let seats, notes=[];
  if(deck!=='home') seats=remoteUnits.filter(u=>u.kind==='seat'&&!u.remoteReturn);
  else { const rank={WAITING:0,DRAFT:1,LIMITED:2,ERROR:2,WORKING:3,IDLE:4,BLANK:5};
    seats=units.filter(u=>isShip(u)&&!u.remote).sort((a,b)=>((rank[a.status]??9)-(rank[b.status]??9))||((a.window_index||0)-(b.window_index||0)));
    notes=(W.ship_sources||[]).filter(s=>!['LIVE','WORKING','IDLE'].includes(s.status)); }
  const busy=seats.filter(u=>!COMMS_QUIET.includes(u.status)).length, fold=!busy&&!COMMS_OPEN;
  // "1 working · 10 idle" (P22): the terminal slots and the stopped agents sit in the opened list, not in the line
  const word=seats.length?(busy?busy+' working · '+(seats.length-busy)+' idle':'all '+seats.length+' agent'+(seats.length>1?'s':'')+' idle'):(deck!=='home'?'no live agents on '+deck:'no agents');
  const s=word+(notes.length?' · '+notes.length+' note'+(notes.length>1?'s':''):'')+(busy?'':(fold?' · open':' · fold'));
  if(sum&&sum.textContent!==s) sum.textContent=s;
  if(box) box.classList.toggle('fold',fold);
  const html=fold?'':seats.slice(0,5).map(u=>commsRow(u,deck==='home'?(u.alias||u.name):u.name,u.status)).join('')+
    (seats.length>5?'<div class="more">+'+(seats.length-5)+' more agents</div>':'')+
    notes.map(n=>'<div class="note">'+esc(CM.plainText(n.message,140))+'</div>').join('')+(deck==='home'?'<div class="note">'+esc(berthLine())+'</div>':'');
  if(cb.innerHTML!==html) cb.innerHTML=html;
}
// ---- RADIO: a seat that starts to need you, finishes or goes down calls in at the lower left for about three seconds:
// its craft with its letter tag in a frame (the wingmate's face when the seat is the wingmate's namesake), its callsign,
// and its own last line (or the state words the page has), typed out. Nothing is written for it. One call at a time,
// the rest queued; a click opens the seat. Only real changes call (between two world loads; for map threads between two
// task lists, the 4 s poll's included), never the first load. On a phone the toast says it (the box would sit over the
// tab bar). ----
const RADIO={q:[],cur:null,timer:0,typer:0,said:{}};   // said: what the radio last called a map thread, as a sky state
// SOUND AND VOICE (sfx.py fills the cm-sfx script with window.cmSfx): a page without it is silent, never broken. Cues are
// the sound's own business (armed by a gesture, rate limited, muted with X); the radio speaks a call only when voice-out is on.
function cmSfxOk(){ return typeof window!=='undefined'&&!!window.cmSfx&&typeof window.cmSfx==='object'; }
function sfxCue(name){ if(!cmSfxOk()||typeof window.cmSfx.cue!=='function') return; try{ window.cmSfx.cue(name); }catch(e){} }
function sfxSpeak(text){ if(!cmSfxOk()) return; try{ if(typeof window.cmSfx.voiceOut==='function'&&window.cmSfx.voiceOut()&&typeof window.cmSfx.speak==='function') window.cmSfx.speak(String(text||'').slice(0,200)); }catch(e){} }
// what the radio compares between two world loads: each home seat and ship by its state (a T3 ship by its thread's
// state too), each map thread by its status, each sky contact by its state
function radioState(u){ return String(u.status||'')+'|'+(u.t3&&typeof u.t3==='object'?String(u.t3.state||''):''); }
function radioSnap(){ const m={};
  units.forEach(u=>{ if(isShip(u)&&!u.remote) m[u.uid]=radioState(u); });
  const sc=skyContacts(); sc.forEach(c=>{ m['sky:'+c.id]=String(c.state||''); });
  m['sky?']=skyOk()?'1':''; m.__contacts=sc; return m; }
function radioWord(was,st){
  if(st==='WAITING'||st==='blocked') return RADIO_WORDS.needs;
  if(isDead(st)) return RADIO_WORDS.down;
  if(st==='done'||((was==='WORKING'||was==='working')&&(st==='IDLE'||st==='idle'))) return RADIO_WORDS.finished;
  return '';
}
function radioShipWord(was,u){
  const w=String(was||'').split('|'), t3=u.t3&&typeof u.t3==='object'?String(u.t3.state||''):'';
  if(t3&&t3!==(w[1]||'')){ if(t3==='waiting') return RADIO_WORDS.needs; if(t3==='failed') return RADIO_WORDS.failed; if(t3==='ready') return RADIO_WORDS.finished; }
  return radioWord(w[0],String(u.status||''));
}
function radioTaskWord(was,st){ return st===was?'':st==='review'?RADIO_WORDS.review:(st==='failed'||st==='lost')?RADIO_WORDS.failed:''; }
// a map thread's status as the sky states it (sky.py's TASK_STATE): what its contact will read once the world catches up
function taskSkyState(st){ return st==='review'||st==='stopped'?'review':st==='failed'||st==='lost'?'failed':''; }
// map threads call in where their status changes: every /tasks list the page takes (the 4 s poll, or the one inside a world
// load) goes through applyTasks, which hands the list it had and the list it got here; a world load never compares threads
// again, so a change seen first by the poll is called once, then, and never lost
function radioTasks(was,list){
  (list||[]).forEach(t=>{ if(!t||t.id==null) return; const k='task:'+t.id, before=was.get(t.id), st=String(t.status||'');
    if(before===undefined||before===st||t.retry_due) return; const word=radioTaskWord(before,st); if(!word) return;
    RADIO.said[k]=taskSkyState(st);
    const v=t.check&&t.check.verdict&&t.check.verdict!=='checking'?' · '+CM.opinionLine(t.check.verdict):'';
    radioCall({uid:k,name:runnerLabel(t.runner)||'task',word:word,line:(CM.shortTitle(t.title,60)||'a task')+v,col:TASK_COL[st]||'#9aa7bd',carrier:false,mate:false,tag:'T',tid:String(t.id),cue:'warp-in'}); });
}
// who calls in on the radio for a contact, in plain words: where a decision waits (the ledger, the board, your notes, the
// task list), "agent" for a T3 thread or a terminal, "job" for a cron line; the word is the contact's (CM.contactWord)
const SKY_SOURCE_CALL={ledger:'ledger',t3:'agent',task:'task',seat:'agent',cron:'job',board:'board',fleet:'task list',quest:'your notes',night:'night run'};
function contactWord(c){ return CM.contactWord(c)||RADIO_WORDS.needs; }
// the radio's line: the source's own words (its voice) as a short plain line, and how old
function contactLine(c){ return CM.contactLine(Object.assign({},c,{title:(c&&(c.voice||c.title))||''}),{noPlace:true,noWord:true}); }
function radioWorld(before){
  if(!before) return;
  const called=new Set(), letters=wingLetters(), ms=mothership();
  units.forEach(u=>{ if(!isShip(u)||u.remote) return; const was=before[u.uid], now=radioState(u);
    if(was===undefined||was===now) return; const word=radioShipWord(was,u); if(!word) return;
    const name=String(u.alias||u.name||'seat'); called.add(u.uid);
    radioCall({uid:u.uid,name:name,word:word,line:commsLine(u)||'',col:shipColor(u),carrier:u===ms,
      mate:name.toLowerCase()===String(mateName()).toLowerCase(),tag:letters[u.uid]||name[0].toUpperCase(),cue:word===RADIO_WORDS.needs?'alert':'warp-in'}); });
  // a contact that drops into the sky, or whose state changes there, calls in, unless the seat or ship behind it already
  // did on this load, or the radio already called its map thread in that state (radioTasks, maybe a poll earlier)
  if(before['sky?']) skyContacts().forEach(c=>{ const st=String(c.state||''); if(before['sky:'+c.id]===st) return;
    const tail=idTail(c.id); if([c.id,c.open&&c.open.uid,'ship:'+c.id,tail,'task:'+tail].some(k=>k&&called.has(k))) return;
    if(c.source==='task'&&RADIO.said['task:'+tail]===st) return;
    const src=String(c.source||''), look=TIER_LOOK[skyTier(c)]||TIER_LOOK.fresh;
    radioCall({uid:'sky:'+c.id,name:SKY_SOURCE_CALL[src]||src||'contact',word:contactWord(c),line:contactLine(c),col:look.eye,carrier:false,mate:false,
      tag:(SKY_SOURCE_CALL[src]||src||'?')[0].toUpperCase(),contact:c,tier:skyTier(c),cue:c.state==='blocked'?'alert':'warp-in'}); });
}
function radioCall(c){ RADIO.q=RADIO.q.filter(x=>x.uid!==c.uid); RADIO.q.push(c); if(RADIO.q.length>4) RADIO.q.shift(); sfxCue(c.cue||'warp-in'); if(!RADIO.cur) radioNext(); }
// a call opens what called: a seat or ship its panel, a thread its thread, a contact what it opens
function radioOpen(c){ if(!c) return;
  if(c.contact) return openContact(c.contact);
  if(c.tid){ setView('map'); return openTask(c.tid); }
  const u=unitByUid(c.uid); if(u){ setView('map'); humanMove(); show(u); } }
function radioNext(){
  const el=document.getElementById('radio'); clearInterval(RADIO.typer); clearTimeout(RADIO.timer);
  RADIO.cur=RADIO.q.shift()||null;
  if(!RADIO.cur){ if(el) el.classList.remove('on'); return; }
  const c=RADIO.cur, text=c.line||c.word, call=String(c.name).toUpperCase();
  sfxSpeak(call+', '+c.word+(c.line?'. '+c.line:''));
  if(MOB){ if(el) el.classList.remove('on'); toast(call+' · '+c.word+(c.line?' · '+c.line:''),3000); RADIO.timer=setTimeout(radioNext,3200); return; }
  if(!el) return;
  const nm=document.getElementById('radion'), wd=document.getElementById('radiow'), ln=document.getElementById('radiol');
  if(nm) nm.textContent=call; if(wd) wd.textContent=c.word; radioPortrait(c); radioPlace(el); el.classList.add('on');
  if(ln){ if(REDUCED) ln.textContent=text;
    else { let i=0; ln.textContent=''; RADIO.typer=setInterval(()=>{ i=Math.min(text.length,i+2); ln.textContent=text.slice(0,i); if(i>=text.length) clearInterval(RADIO.typer); },40); } }
  RADIO.timer=setTimeout(radioNext,Math.min(5000,Math.max(3000,text.length*20+1600)));
}
// above the wingmate (its name tag included) and level with its left edge; with the wingmate hidden, low on the left
function radioPlace(el){
  const box=id=>{ const e=document.getElementById(id), r=e&&e.getBoundingClientRect?e.getBoundingClientRect():null; return r&&r.width>1&&r.height>1?r:null; };
  const m=box('mate'), d=box('dash');
  el.style.left=Math.round(m?m.left:(d?d.right:0)+12)+'px';
  el.style.bottom=Math.round(m?innerHeight-m.top+28:80)+'px';
}
// the caller in the frame: its craft, big, with its tag; the carrier for the hub; the wingmate's face for its namesake
function radioPortrait(c){
  const pc=document.getElementById('radioc'); if(!pc||!pc.getContext) return; const g=pc.getContext('2d'); if(!g) return;
  const W2=pc.width, H2=pc.height; g.imageSmoothingEnabled=false; g.clearRect(0,0,W2,H2);
  g.fillStyle=GB?'#0f380f':'#0a1a33'; g.fillRect(0,0,W2,H2);
  const rows=c.mate&&MATE_FRAMES.IDLE;
  if(rows){ const h=rows.length, w=Math.max(...rows.map(r=>r.length)), P=Math.max(1,Math.floor(Math.min(W2/w,H2/h))), ox=Math.floor((W2-w*P)/2), oy=Math.floor((H2-h*P)/2);
    rows.forEach((row,y)=>{ for(let x=0;x<row.length;x++){ const col=MATE_PAL[row[x]]; if(row[x]==='.'||!col) continue; g.fillStyle=col; g.fillRect(ox+x*P,oy+y*P,P,P); } }); }
  else{ const look=c.contact?(TIER_LOOK[c.tier]||TIER_LOOK.fresh):null;
    const art=look?spriteArt('raider',foePal(look.eye,look.fire),6,16,2):c.carrier?spriteArt('carrier',friendPal('','#ff6a2e','#5ff2ff'),4,16,2):spriteArt('pilot',friendPal(c.col,c.word===RADIO_WORDS.finished?'':FX_OWN[2]),0,16,2);
    g.drawImage(art,Math.round((W2-art.width)/2),Math.round((H2-art.height)/2)+8);
    const tw=c.tag.length>1?WING.w2:WING.w; wingShape(g,Math.round(W2/2-tw/2),6,tw,WING.h,c.col,c.tag); }
  g.fillStyle='rgba(255,255,255,.05)'; for(let y=0;y<H2;y+=3) g.fillRect(0,y,W2,1);   // the comms lines over the picture
}
{ const re=document.getElementById('radio'); if(re&&re.addEventListener) re.addEventListener('click',()=>{ const c=RADIO.cur; radioNext(); radioOpen(c); }); }
// ---- THE SKY, acted on: J snaps to the next contact (Shift+J the one before), O opens it, Y switches sky mode, X mutes the
// cues, G held listens for a spoken command where the browser can. Every action is reachable without flying. ----
let SKY_FOCUS_ID='';
function homeAreasByPath(){ return units.filter(u=>u.kind==='area'&&u.path).sort((a,b)=>b.path.length-a.path.length); }
function contactMoonNow(c){ return contactMoon(c,MOONS,units,homeAreasByPath(),taskItems()); }
// what a contact opens: a link (a T3 thread in the Bridge) in a new tab, a map thread its thread, a seat or ship its panel,
// a ledger row Today (read only: only the owner's dated ruling closes it), else its planet's card or the star
function openContact(c){
  if(!c) return;
  const o=c.open&&typeof c.open==='object'?c.open:null, src=String(c.source||''), tail=idTail(c.id);
  if(o&&typeof o.url==='string'&&/^https?:[/][/][^\\s"'<>]+$/i.test(o.url)){ try{ window.open(o.url,'_blank','noopener,noreferrer'); }catch(e){} return; }
  if(src==='ledger'){ setView('today'); toast('This decision closes when you rule on it in the ledger',4200); return; }
  const tid=src==='task'?tail:(o&&typeof o.uid==='string'&&o.uid.indexOf('task:')===0?o.uid.slice(5):'');
  if(tid&&agentTasks().some(t=>String(t.id)===tid)){ setView('map'); openTask(tid); return; }
  const u=(o&&typeof o.uid==='string'&&unitByUid(o.uid))||unitByUid(c.id)||(src==='t3'?unitByUid('ship:t3:'+tail):null)
    ||(src==='seat'?unitByUid('seat:'+tail)||unitByUid(tail):null)||(src==='cron'?unitByUid(tail):null);
  if(u&&u!==BOARD){ setView('map'); humanMove(); activate(u); return; }
  const m=contactMoonNow(c), pu=m&&unitByUid(m.uid);
  if(pu){ flyOpen(pu); return; }
  setView('map'); showStar();
}
function snapContact(step){
  const list=skyContacts();
  if(!list.length){ toast(skyOk()?SKY_WORDS.empty:'no sky in this world (sky.py did not run)',2400); return null; }
  const at=list.findIndex(c=>c.id===SKY_FOCUS_ID), i=at<0?(step<0?list.length-1:0):((at+step)%list.length+list.length)%list.length, c=list[i];
  SKY_FOCUS=i; SKY_FOCUS_ID=c.id;
  const m=contactMoonNow(c), pu=m&&unitByUid(m.uid), a=pu?approach(pu):{x:LY.star.x,y:LY.star.y+LY.star.r+18};
  setView('map'); humanMove(); pilot={source:'human',x:a.x,y:a.y};
  toast((i+1)+' of '+list.length+' · '+CM.contactLine(c)+CM.tx(' · O opens',''),4200);
  sfxCue('lock'); return c;
}
function openFocused(){ const c=skyContacts().find(x=>x.id===SKY_FOCUS_ID); if(c) openContact(c); else snapContact(1); }
function setSky(v){
  SKY=!!v; try{ localStorage.setItem('sky',SKY?'1':'0'); }catch(e){}
  place(); stripTick();
  toast(SKY?(skyOk()?'Only what needs you'+CM.tx(' · Y shows everything',''):'Everything is shown: nothing could be read about what needs you'):'Everything'+CM.tx(' · Y shows only what needs you',''),3000);
}
function toggleSound(){
  if(!cmSfxOk()||typeof window.cmSfx.enabled!=='function'||typeof window.cmSfx.setEnabled!=='function'){ toast('no sound on this page',2000); return; }
  try{ window.cmSfx.setEnabled(!window.cmSfx.enabled()); toast('sound '+(window.cmSfx.enabled()?'on':'off')+' · X',2000); }catch(e){ toast('sound: '+e.message,2400); }
}
// Shift+X: the radio reads its calls aloud (off until turned on; only with a voice that runs on this device, and the X mute
// silences it too)
function toggleVoiceOut(){
  const S=cmSfxOk()?window.cmSfx:null;
  if(!S||typeof S.voiceOut!=='function'||typeof S.setVoiceOut!=='function'){ toast('no voice on this page',2000); return; }
  try{ S.setVoiceOut(!S.voiceOut()); const on=!!S.voiceOut();
    const why=on&&typeof S.voiceReady==='function'&&!S.voiceReady()?' · no voice on this device yet: it stays silent':'';
    const muted=on&&typeof S.enabled==='function'&&!S.enabled()?' · sound is muted (X)':'';
    toast('radio voice '+(on?'on':'off')+why+muted+' · Shift+X',2600); }catch(e){ toast('voice: '+e.message,2400); }
}
// voice in: push to talk (G held), navigation only (next, prev, open, briefing, sky); nothing is changed by voice
function sfxListen(){ return cmSfxOk()&&window.cmSfx.listen&&typeof window.cmSfx.listen==='object'?window.cmSfx.listen:null; }
function pageVoice(cmd){ const k=String((cmd&&typeof cmd==='object'?(cmd.kind||cmd.name):cmd)||'').toLowerCase();
  if(k==='next') snapContact(1); else if(k==='prev'||k==='previous') snapContact(-1); else if(k==='open') openFocused();
  else if(k==='briefing') setView('today'); else if(k==='sky') setSky(!SKY); }
{ const L=sfxListen(); if(L){ try{ L.onCommand=pageVoice;
  L.onMiss=heard=>toast(heard?'voice: not a command · '+String(heard).slice(0,40)+' · say next, previous, open, briefing or sky':'voice: heard nothing',2600); }catch(e){} } }
function listenStart(){ const L=sfxListen(); if(!L||!L.supported){ toast('voice: '+String((L&&L.reason)||'not on this page'),2600); return; }
  try{ const ok=L.start(); if(ok===false&&L.error) toast('voice: '+String(L.error).slice(0,80),2600);
    else if(ok!==false) toast('listening'+(L.note?' · '+String(L.note).slice(0,90):''),2200); }catch(e){ toast('voice: '+e.message,2600); } }
function listenStop(){ const L=sfxListen(); if(!L||!L.supported) return; try{ L.stop(); }catch(e){}
  // the recogniser reports a failure after the key is up (not-allowed, network, language-not-supported): say it once
  setTimeout(()=>{ const er=L.error; if(er&&er!=='aborted'&&er!=='no-speech') toast('voice: '+String(er).slice(0,80),2600); },600); }
addEventListener('keydown',e=>{ if(typing()||modKey(e)||e.repeat||composerOpen()) return; const k=String(e.key||'').toLowerCase();
  if(k==='y'){ e.preventDefault(); setSky(!SKY); }
  else if(k==='j'){ e.preventDefault(); snapContact(e.shiftKey?-1:1); }
  else if(k==='o'){ e.preventDefault(); openFocused(); }
  else if(k==='x'){ e.preventDefault(); if(e.shiftKey) toggleVoiceOut(); else toggleSound(); }
  else if(k==='g'){ e.preventDefault(); listenStart(); } });
addEventListener('keyup',e=>{ if(String(e.key||'').toLowerCase()==='g') listenStop(); });
// MISSION COMPLETE: a contact that leaves the sky between two loads raises the banner only when this load shows what
// resolved it: its T3 thread settled, its map thread closed (merged, set aside, marked done), its ledger row ruled (the
// ledger was read and the row is no longer open), its claim marked done today. Leaving alone proves nothing (a T3 database
// that could not be read, a failed thread restarted, a seat answered and back at work). One banner per load (its title, or "N contacts" when
// several resolved), EMPTY SKY when the sky is empty and every contact that left was resolved; never on the first load,
// and not twice for a thread whose merge already raised the banner.
const BANNERED=new Map();
function missionComplete(title,word){
  title=String(title||'').slice(0,56); word=String(word||SKY_WORDS.cleared).slice(0,16);
  const seen=BANNERED.get(title); if(word!==SKY_WORDS.empty&&seen&&Date.now()-seen<60000) return false;
  BANNERED.set(title,Date.now()); FX.toast={t0:FRAME_T,title:title,word:word};
  if(MOB&&mapCovered()) mToast(word+': '+title,3800);
  return true;
}
const SKY_RESOLVED_TASK=['merged','discarded','done'];   // tasks.py CLOSED: a person merged, set aside or marked it done
function skyResolved(c){
  const src=String(c&&c.source||''), tail=idTail(c&&c.id), T=W.today&&typeof W.today==='object'?W.today:{};
  if(src==='t3'){ const ou=c.open&&typeof c.open.uid==='string'?c.open.uid:'', u=units.find(x=>x.uid==='ship:t3:'+tail||(ou&&x.uid===ou));
    return !!u&&!!u.t3&&typeof u.t3==='object'&&u.t3.state==='settled'; }
  if(src==='task'){ const t=agentTasks().find(x=>x&&String(x.id)===tail); return !!t&&SKY_RESOLVED_TASK.includes(t.status); }
  if(src==='ledger') return Number.isInteger(T.parked)&&Array.isArray(T.ledger)&&!T.ledger.some(r=>r&&String(r.id)===tail);
  if(src==='fleet') return Array.isArray(T.done)&&T.done.some(d=>d&&String(d.id)===tail);
  return false;
}
function skyWorld(before){
  if(!before||!before['sky?']||!skyOk()) return;
  const prev=before.__contacts||[], ids=new Set(skyContacts().map(c=>c.id)), gone=prev.filter(c=>c&&!ids.has(c.id));
  if(SKY_FOCUS_ID&&!ids.has(SKY_FOCUS_ID)) SKY_FOCUS_ID='';
  const done=gone.filter(skyResolved); if(!done.length) return;
  const title=done.length===1?String(done[0].title||done[0].id):done.length+' '+SKY_WORDS.contacts;
  if(!ids.size&&done.length===gone.length){ missionComplete(title,SKY_WORDS.empty); sfxCue('empty-sky'); }
  else if(missionComplete(title,SKY_WORDS.cleared)) sfxCue('settle');
}
// the bottom strip's four numbers: the one count (W.counts, sky.counts), whatever the view: need you, decisions,
// problems, working. Each button is marked data-count so __wordsAudit() reads it back against W.counts.
const STRIP_KEYS=['need_you','decisions','problems','working'], STRIP_CLS={need_you:'need',decisions:'need',problems:'bad',working:'ok'};
function stripCounts(){ const c=CM.counts(), out={};
  STRIP_KEYS.forEach(k=>{ out[k]=Number.isInteger(c[k])&&c[k]>0?c[k]:0; }); return out; }
function stripTick(){
  const el=document.getElementById('nums'); if(!el) return; const c=stripCounts();
  const n=(k,v,word,cls)=>'<button type="button" data-num="'+k+'" data-count="'+k+'" class="'+(v?cls:'zero')+'" title="'+v+' '+attr(word)+'"><b>'+v+'</b><span>'+esc(word)+'</span></button>';
  const cc=CM.counts(), h=STRIP_KEYS.map(k=>n(k,c[k],CM.countNoun(k,c[k],cc),STRIP_CLS[k])).join('');
  if(el.innerHTML!==h) el.innerHTML=h;
  const hm=document.getElementById('hmore'); if(hm){ const t=QUIET?'More stats':'Fewer stats'; if(hm.textContent!==t) hm.textContent=t; }
}
function flyOpen(u){ if(!u) return false; setView('map'); humanMove(); const a=approach(u); pilot={source:'human',x:a.x,y:a.y}; activate(u); return true; }
document.getElementById('nums').addEventListener('click',e=>{ const b=e.target.closest('[data-num]'); if(!b) return; stripClick(b.dataset.num); });
// the strip's four numbers as buttons: each opens the first thing it counts (the tests call this, the click above does)
function stripClick(k){
  if(k==='need_you'){ const c=skyContacts().find(x=>{ const g=CM.bucket(x); return g==='answer'||g==='review'; }); if(c) return openContact(c); toast(CM.word('empty sky'),2400); return; }
  if(k==='decisions'){ setView('map'); return toggleQuests(true); }
  if(k==='working'){ const w=units.find(u=>isShip(u)&&!u.remote&&u.status==='WORKING'); if(w){ humanMove(); return show(w); } return toggleTasks(true); }
  if(k==='problems'){ const bad=x=>moonCounts(x).problems, m=MOONS.filter(bad).sort((a,b)=>bad(b)-bad(a))[0];
    if(m&&flyOpen(unitByUid(m.uid))) return;
    const c=skyContacts().find(x=>CM.bucket(x)==='problem'); if(c) return openContact(c);   // a problem with no planet circles the sun: open it
    toast('No problems on the map',2400); return; }
}
document.getElementById('hmore').addEventListener('click',()=>setQuiet(!QUIET));
{ const ct=document.getElementById('ctoggle'); if(ct) ct.addEventListener('click',()=>{ COMMS_OPEN=document.getElementById('comms').classList.contains('fold');
  try{ sessionStorage.setItem('comms',COMMS_OPEN?'open':'fold'); }catch(e){} commsTick(); }); }
function tickerTick(){
  const tb=document.getElementById('tkbody'); if(!tb||chromeHides('ticker')) return;
  if(deck!=='home'){ tb.textContent=(typeof remoteInfo!=='undefined'&&remoteInfo&&remoteInfo.generated)?('synced '+remoteInfo.generated+' · live seats only here'):''; return; }
  const L=W.ticker||[]; if(!L.length){ tb.textContent='no digest lines on disk'; return; }
  if(REDUCED&&tickI>=0) return;            // reduced motion: the newest receipt stays put (click the strip for Today)
  tickI=(tickI+1)%L.length; tb.textContent=L[tickI];
}
const cbodyEl=document.getElementById('cbody');
if(cbodyEl) cbodyEl.addEventListener('click', e=>{ const r=e.target.closest('.row[data-uid]'); if(!r) return;
  const u=unitByUid(r.dataset.uid); if(u){ humanMove(); show(u); } });
// rows that act like buttons (rails, comms) answer Enter and Space as well as the mouse
document.addEventListener('keydown',e=>{ const t=e.target; if(!(t&&t.getAttribute&&t.getAttribute('role')==='button'&&t.tagName!=='BUTTON')) return;
  if(e.key==='Enter'||e.key===' '){ e.preventDefault(); e.stopPropagation(); t.click(); } });
// ---- TACTICAL CHROME (the vector look, VEC). A top rail: the view as a chip and the dossier stamp from this browser's
// clock, the map's mark between bracket ticks, the status counters boxed (the strip's own numbers). A bottom rail: the
// key legend, each key a button that presses that key, and the monogram. Built the first time the vector look is on
// (no other look gets the elements) and refreshed on the HUD tick; the stylesheet hides them in every other look.
const TAC_DAYS=['SUN','MON','TUE','WED','THU','FRI','SAT'], TAC_MONTHS=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
function tacStamp(d){ d=d||new Date(); const p=n=>String(n).padStart(2,'0'); return TAC_DAYS[d.getDay()]+' '+p(d.getDate())+' '+TAC_MONTHS[d.getMonth()]+' '+d.getFullYear()+' · '+p(d.getHours())+p(d.getMinutes())+' HRS'; }
// only keys the page answers in that view, in its own words: Q opens the board, T Today, H the full HUD; Today takes N, M, V
const TAC_KEYS={map:[['n','N','New task'],['k','K','Tasks'],['j','J','Next item'],['y','Y','Show all'],['q','Q','Decisions'],['l','L','Map key'],['t','T','Today'],['v','V','Theme'],['h','H','More stats'],['Escape','Esc','Back']],
  today:[['n','N','New task'],['m','M','Map'],['v','V','Theme']]};
let TAC_BUILT=false;
function tacBuild(){
  const wrap=document.getElementById('wrap'); if(!wrap) return;
  const top=document.createElement('div'); top.id='tacrail';
  top.innerHTML='<div class="tline"></div><div class="tl"><b id="tacview"></b><span id="tacstamp"></span><span id="tacnote"></span></div>'+
    '<div class="tmark"><i></i><b id="tacmark"></b><i></i></div><div class="tscale"></div><div id="taccount" aria-label="Status"></div>';
  const bot=document.createElement('div'); bot.id='tacfoot';
  bot.innerHTML='<div class="bline"></div><div id="tacmono" aria-hidden="true"><b>C</b><s></s><b>M</b></div><nav id="tackeys" aria-label="Keys"></nav>';
  wrap.appendChild(top); wrap.appendChild(bot); TAC_BUILT=true;
  // a legend key does what the key does: the page's own key handlers get the same keydown
  document.getElementById('tackeys').addEventListener('click',e=>{ const b=e.target.closest('[data-key]'); if(!b) return; const key=b.dataset.key;
    try{ b.blur(); }catch(err){}
    dispatchEvent(new KeyboardEvent('keydown',{key:key,bubbles:true,cancelable:true})); dispatchEvent(new KeyboardEvent('keyup',{key:key,bubbles:true})); });
  // a counter does what its number in the strip does
  document.getElementById('taccount').addEventListener('click',e=>{ const b=e.target.closest('[data-tnum]'); if(!b) return;
    const n=document.querySelector('#nums [data-num="'+b.dataset.tnum+'"]'); if(n) n.click(); });
}
const TAC_MEMO={};   // what each rail piece last showed: written to the page only when it changes
function tacSet(id,v,html){ const el=document.getElementById(id); if(!el||TAC_MEMO[id]===v) return; TAC_MEMO[id]=v; if(html) el.innerHTML=v; else el.textContent=v; }
function tacTick(){
  if(!TAC_BUILT) tacBuild(); if(!TAC_BUILT) return;
  const view=VIEW==='today'?'today':'map', stamp=tacStamp(), name=String(deck==='home'?(W.name||'CONTAINER'):deck).toUpperCase();
  tacSet('tacview',view==='today'?'TODAY':'MAP'); tacSet('tacstamp',stamp); tacSet('tacmark',name);
  const fnd=deck==='home'?(W.ship_sources||[]).filter(s=>['ABSENT','ERROR','PARTIAL','SNAPSHOT'].includes(s.status)):[];
  { const sd=deck==='home'?skyData():null; if(sd&&sd.error) fnd.push({message:String(sd.error).slice(0,140)}); }
  tacSet('tacnote',fnd.length?String(fnd[0].message||'')+(fnd.length>1?' · +'+(fnd.length-1):''):'');
  // a narrow window takes the short word (.s), a narrower one the number alone; the whole word stays in the title
  const c=stripCounts(), n=(k,v,w,s,cls)=>'<button type="button" data-tnum="'+k+'" data-count="'+k+'" class="'+(v?cls:'zero')+'" title="'+attr(v+' '+w)+'"><b>'+v+'</b><span class="w">'+esc(w)+'</span><span class="s">'+esc(s)+'</span></button>';
  tacSet('taccount',STRIP_KEYS.map(k=>{ const w=CM.countNoun(k,c[k]); return n(k,c[k],w,k==='need_you'?'need you':k==='decisions'?'decide':w,STRIP_CLS[k]); }).join(''),true);
  tacSet('tackeys',TAC_KEYS[view].map(k=>'<button type="button" data-key="'+attr(k[0])+'" title="'+attr(k[1]+': '+k[2])+'"><kbd class="tkey">'+esc(k[1])+'</kbd>'+esc(k[2])+'</button>').join(''),true);
  // the mark interrupts the rail's line: the gap follows the mark's width
  const mk=document.getElementById('tacmark'), tp=document.getElementById('tacrail');
  if(mk&&tp&&mk.offsetWidth>0){ const g=Math.round(mk.offsetWidth/2+28)+'px'; if(TAC_MEMO.gap!==g){ TAC_MEMO.gap=g; tp.style.setProperty('--gap',g); } }
  // the comms hang under the HUD, which has no box and grows with its lines (the alert, the full HUD): only vector rules read this
  const hud=document.getElementById('hud'), wr=document.getElementById('wrap');
  if(hud&&wr&&hud.getBoundingClientRect){ const hb=Math.round(hud.getBoundingClientRect().bottom)+'px'; if(hb!=='0px'&&TAC_MEMO.hb!==hb){ TAC_MEMO.hb=hb; wr.style.setProperty('--tac-hb',hb); } }
  // Today's dossier header reads the mark and the stamp from custom properties: its own markup stays as it is
  const td=document.getElementById('today'), tk=name+'|'+stamp;
  if(td&&TAC_MEMO.today!==tk){ TAC_MEMO.today=tk; td.style.setProperty('--tac-mark',JSON.stringify(name)); td.style.setProperty('--tac-stamp',JSON.stringify(stamp)); }
  // the console row of what is open wears the pill
  const uid=openUnit&&openUnit.uid;
  document.querySelectorAll('#dash .row').forEach(r=>{ const on=!!((uid&&r.dataset.uid===uid)||(openTaskId&&r.dataset.tid===openTaskId)); if(r.classList.contains('sel')!==on) r.classList.toggle('sel',on); });
}
setInterval(hudTick, 500); hudTick(); setInterval(tickerTick, 4000); tickerTick();
// ---- TODAY (today.py): the first screen. What the user said they'd do, what waits on the user, the plan's week, agents,
// the daily lanes, what each folder is for. Every line is from a file on disk; M opens the map.
function setView(v){ VIEW=v==='map'?'map':'today'; try{ sessionStorage.setItem('view',VIEW); }catch(e){}
  const el=document.getElementById('today'); clearKeys();
  if(el){ el.classList.toggle('on',VIEW==='today'); if(VIEW==='today') renderToday(); }
  if(HOME_LIVE) homeSettle();
  if(MOB) mobSync(); p3PovSync(); }
// `code` spans open only after a space or "(" so a literal backtick (Ctrl+`) stays text
// inside an attribute quotes must be escaped too (esc() is for text)
function escA(s){ return esc(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function todayCode(s){ return esc(s).replace(/(^|[\\s(])`([^`\\s][^`]*)`/g,'$1<code>$2</code>'); }
function agentKind(a){ return a.source==='t3'?(a.app&&a.app!=='T3'?a.app+' agent':'T3 agent'):a.source==='process'?'background agent':a.source==='deck'?'machine':'terminal window'; }
function renderToday(){
  const el=document.getElementById('today'); if(!el) return;
  const T=W.today||{}, A=T.agents||{}, working=A.working||[], waiting=A.waiting||[], idle=A.idle||[];
  // plain words (words.py, ui/words.js): no ids, paths or config keys in what the user reads; those sit under Details.
  // One function for both: the desktop reads two columns, a phone one (ui/phone.css lays the same sections out in its
  // order); an empty section folds to its heading and one "Quiet" word
  const det=(label,inner,cls)=>inner?'<details class="tdet'+(cls?' '+cls:'')+'"><summary>'+esc(label)+'</summary>'+inner+'</details>':'';
  const nc=(typeof CM.counts==='function'&&CM.counts())||{}, num=k=>Number.isInteger(nc[k])?nc[k]:null;
  const noun=(k,n)=>typeof CM.countNoun==='function'?CM.countNoun(k,n):k;
  const th=(uid,size)=>typeof CM.planetThumb==='function'?CM.planetThumb(uid,size):'<i class="tth"></i>';
  const place=uid=>uid&&typeof CM.labelText==='function'?String(CM.labelText(uid)||''):'';
  const head=(title,n,right)=>'<h2>'+title+(n!=null&&n!==''?' <span class="n">'+n+'</span>':'')+(right?'<span class="r">'+right+'</span>':'')+'</h2>';
  const quiet=(title,why)=>'<h2 class="tq">'+title+' <span class="q"'+(why?' title="'+escA(why)+'"':'')+'>Quiet</span></h2>';
  const keyT=typeof CM.isTouch==='function'&&CM.isTouch()?'':' <span class="key">T</span>';
  const MAPI='<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="3"/><circle cx="8" cy="8" r="6.5"/><path d="M8 0.5v2M8 13.5v2M0.5 8h2M13.5 8h2"/></svg>';
  const need=todayNeed(), AT=agentTasks(), run=AT.filter(t=>TASK_WORKING.includes(t.status));
  // with a sky to read, a task back for your review is a row of Need you: Running now holds the working and the failed ones
  const open=AT.filter(t=>TASK_OPEN.includes(t.status)&&!(need&&['review','stopped'].includes(t.status)));
  const wfAll=typeof CM.wfRows==='function'?CM.wfRows():[], wf=wfAll.filter(r=>r&&['working','needs_you','failed'].includes(r.state)&&!r.night&&!todayDerived(r.id));
  const N=T.night&&typeof T.night==='object'?T.night:null;
  // the header: the day, the three numbers that matter, where they are, the way back to the map
  const nNeed=num('need_you')!=null?num('need_you'):(need?need.length:waiting.length), nRun=num('working')!=null?num('working'):run.length+working.length;
  const nDone=N&&Number.isInteger(N.ran)?N.ran:num('done_today');
  const sub=[nNeed?'<span class="a" data-count="need_you"><b>'+nNeed+'</b> need you</span>':'',
    nRun?'<span class="c" data-count="working"><b>'+nRun+'</b> running</span>':'',
    nDone?'<span class="g"><b>'+nDone+'</b> '+(N&&Number.isInteger(N.ran)?'done overnight':'done today')+'</span>':''].filter(Boolean);
  let h='<div class="tw'+(T.configured===false?' taskhome':'')+'"><div class="th"><div class="thl"><h1>'+(T.configured===false?'For you':'Today')+' <span>· '+esc(T.date||'')+'</span></h1>'+
    '<div class="tsub">'+(sub.length?sub.join('<i>·</i>'):'<span>Nothing waiting on you</span>')+'</div></div>'+
    '<span class="gen">'+esc(todayAgo())+'</span>'+todayWhere(th)+
    (LIVE?'<button type="button" class="tnew" data-tnew="1">+ New task</button>':'')+
    '<button type="button" class="tgo" data-go="map">Back to the map'+keyT+'</button></div>';
  if(T.error) h+='<section><div class="tdl bad">'+esc(T.error)+'</div></section>';
  h+='<div class="tcols"><div class="tcol">';
  // NEED YOU: the sky's need-you contacts (an answer or a review: CM.bucket), oldest first: the rows W.counts.need_you
  // counts, so the Today badge, the headline and this list say one number. Each row two lines at most:
  // what, then who asks · where · how long; "Show on map" focuses its planet. With no sky to read, the terminals asking
  // for permission, as before.
  h+='<section data-sec="needs">';
  const needRow=(c,i)=>{ const uid=typeof c.planet_uid==='string'?c.planet_uid:'', where=place(uid), word=CM.contactWord(c)||'needs you';
    const title=CM.shortTitle(c.title||c.voice||'',60)||word, said=CM.plainText(c.voice||c.note||'',150);
    const body=said&&said!==title&&said.indexOf(title)<0?said:'', age=c.age_days!=null?CM.ageWords(c.age_days):'';
    const verb=i===0?(CM.bucket(c)==='answer'?'Answer':'Review'):'';
    return '<div class="tn need'+(i===0?' first':'')+'" data-cid="'+escA(c.id)+'" role="button" tabindex="0">'+th(uid,44)+
      '<div class="tnb"><div class="tt">'+esc(title)+'</div>'+(body?'<div class="ts">'+esc(body)+'</div>':'')+
      '<div class="tm"><span><b>'+esc(word)+'</b>'+(where&&where!==uid?' · '+esc(where):'')+(age?' · <span class="wt">waiting </span>'+esc(age):'')+'</span>'+
      (uid?'<span class="tsm" data-show="'+escA(uid)+'">Show on map ›</span>':'')+'</div></div>'+
      '<div class="ta">'+(verb?'<span class="tbtn amber">'+verb+'</span>':'')+(uid?'<button type="button" class="tbtn tic" data-show="'+escA(uid)+'" aria-label="Show on map" title="Show on map">'+MAPI+'</button>':'')+'</div></div>'; };
  if(need){
    if(!need.length) h+=quiet('Need you',CM.word('empty sky'));
    else { h+=head('Need you','<b data-count="need_you">'+need.length+'</b>','oldest first');
      const rows=need.slice(0,20).map(needRow);
      h+=rows.slice(0,4).join('')+(rows.length>4?det('+'+(rows.length-4)+' more',rows.slice(4).join(''),'tmore'):'');
      if(need.length>20) h+='<div class="mut">and '+(need.length-20)+' more in the Agents rail</div>'; } }
  else if(!waiting.length) h+=quiet('Need you',CM.word('empty sky'));
  else { h+=head('Need you',waiting.length,'oldest first');
    waiting.forEach((w,i)=>{ h+='<div class="tn need'+(i===0?' first':'')+'" data-uid="'+escA(w.uid)+'" role="button" tabindex="0"><i class="tth"></i><div class="tnb"><div class="tt">'+esc(w.name)+' is asking for permission</div>'+
      '<div class="tm"><span>'+esc(CM.plainText(w.voice,140))+'</span></div></div><div class="ta"></div></div>'; }); }
  h+='</section>';
  // A map with no personal daily sources still has reviews and tasks. Keep the first phone page useful.
  if(T.configured===false){
    h+='<section data-sec="running">'+(run.length?head('Running now',run.length):quiet('Running now',''));
    run.slice(0,8).forEach(t=>{ h+='<div class="tcard ttask c" data-tid="'+escA(t.id)+'" role="button" tabindex="0"><div class="tt">'+esc(CM.shortTitle(t.title,60)||'A task')+'</div><div class="tnow">An agent is working in '+esc(shortPath(t.target).split('/').pop())+'</div></div>'; });
    h+='</section><section data-sec="done">';
    const merged=AT.filter(t=>t.status==='merged');
    if(merged.length){ h+=head('Merged',merged.length); merged.slice(0,3).forEach(t=>{ h+='<div class="tcard ttask g" data-tid="'+escA(t.id)+'" role="button" tabindex="0"><div class="tt">'+esc(CM.shortTitle(t.title,60)||'A task')+'</div><div class="tnow">You kept these changes</div></div>'; }); }
    const ndec=num('decisions'); if(ndec) h+='<button type="button" class="tbtn" data-board="1">'+ndec+' '+esc(noun('decisions',ndec))+' to look at</button>';
    h+='</section></div>'; if(el.innerHTML!==h) el.innerHTML=h; return;
  }
  // ALSO WAITING: the decisions (the claims and the ledger: W.counts.decisions) and the problems, in one line; every
  // decision is on the board (Q). The rows themselves wait under Details, oldest first, read only: a ledger row closes
  // only by the owner's dated ruling
  const ledger=ledgerRows(T), claims=Array.isArray(T.blocked)&&T.blocked.length?T.blocked:(T.needs||[]), ndec=num('decisions'), nprob=num('problems');
  h+='<section data-sec="decisions">';
  const also=[ndec?'<span data-count="decisions"><b>'+ndec+'</b> '+esc(noun('decisions',ndec))+'</span>':'',
    nprob?'<span data-count="problems"><b>'+nprob+'</b> '+esc(noun('problems',nprob))+'</span>':''].filter(Boolean);
  if(also.length) h+='<h3 class="talso">Also waiting: '+also.join(' and ')+(ndec?' <a class="go" data-board="1">Open the board ›</a>':'')+'</h3>';
  else if(!claims.length&&!ledger.length&&!T.parked) h+='<h3 class="talso">Nothing to decide.</h3>';
  let dl='';
  claims.forEach(n=>{ dl+='<div class="tdl need" data-claim="'+escA(String(n.id||''))+'">'+esc(CM.shortTitle(n.title,60))+'<small>'+esc(CM.plainText(n.note,200))+'</small></div>'; });
  if(ledger.length){ const od=ledgerDays(ledger[0]);
    dl+='<div class="mut" data-ledger-head="1">'+ledger.length+' in the ledger, oldest first'+(od>=0?' · the oldest waits '+od+' day'+(od===1?'':'s'):'')+'</div>';
    ledger.slice(0,ledgerMax()).forEach(r=>{ const d=ledgerDays(r);
      dl+='<div class="tdl need ledger" data-ledger="'+escA(r.id)+'">'+esc(CM.shortTitle(r.title||r.id,60))+'<small>'+[d>=0?'waiting '+CM.ageWords(d):'',r.lane?String(r.lane):'',r.blocks?'holds up '+CM.plainText(r.blocks,60):'',
        (r.estimate||r.opened_is_estimate)?'start date guessed':''].filter(Boolean).map(esc).join(' · ')+'</small></div>'; });
    if(ledger.length>ledgerMax()) dl+='<div class="mut">and '+(ledger.length-ledgerMax())+' more in the ledger</div>'; }
  else if(T.parked) dl+='<div class="mut">'+Number(T.parked)+' older decisions wait in the ledger.</div>';
  if(dl&&ndec) dl+='<div class="mut">All '+ndec+' are on the board: <a class="go" data-board="1">open the board</a></div>';
  if(dl) h+=det(claims.length||ledger.length?'The decisions, oldest first':'Older decisions',dl,'tdec');
  h+='</section>';
  // DO TODAY: what the user said they'd do (today.py word), each a box the user ticks (a dated check-in, desktop only), its proof
  // and when it was last kept on the right
  const Y=T.word, items=Y&&Array.isArray(Y.items)?Y.items:[];
  h+='<section data-sec="word">';
  if(!items.length) h+=quiet('Do today',Y?'nothing written yet':'')+(Y?'<div class="tcap">What you said you&#39;d do: nothing written yet.</div>':'');
  else { h+=head('Do today',items.length)+'<div class="tcap">What you said you&#39;d do'+(Y.summary?' · '+esc(Y.summary):'')+'</div>';
    items.forEach(c=>{ const can=DESK&&c.checkin&&c.id&&((c.state!=='idle'&&c.state!=='off')||c.today), col=c.state==='ok'?'g':c.state==='bad'?'r':c.state==='idle'||c.state==='off'?'':'a';
      const right=[c.status,c.last?'last '+c.last:''].filter(Boolean).join(' · ');
      h+='<div class="tdo '+col+(c.today?' on':'')+'" title="'+escA([c.lane,c.proof_label?'proof: '+c.proof_label:''].filter(Boolean).join(' · '))+'">'+
        (can?'<button type="button" class="tbx'+(c.today?' on':'')+'" data-checkin="'+escA(c.id)+'"'+(c.today?' data-undo="1">take back today':'>I did it today')+'</button>':'<i class="tbx'+(c.today?' on':'')+'"></i>')+
        '<span class="tdt">'+todayCode(c.what)+'</span><span class="tdw">'+esc(right)+'</span>'+
        '<span class="hist">'+(c.history||[]).map(x=>'<i class="'+(x.kept===true?'y':x.kept===false?'n':'')+'" title="'+escA(x.label)+'"></i>').join('')+'</span>'+
        (can?'<span class="tck" id="ck-'+escA(c.id)+'"></span>':'')+'</div>'; });
    h+='<div class="mut tkept">'+(Y.kind==='todo'?'Open items are the unchecked boxes. Change the list in ':'Kept means a dated line in its file or a tick here. Change the list in ')+
      (DESK&&W.opener?'<a class="go" data-open="'+escA(Y.path||'')+'">'+esc(String(Y.path||'').split('/').pop())+'</a>':esc(String(Y.path||'').split('/').pop()))+'.</div>'; }
  h+='</section>';
  // the plan's week, the daily lanes and the folders' purposes: there when the user wants them, folded under their names
  const P=T.plan;
  if(P){ const wk=P.week||{}; let ph='';
    if(wk.title) ph+='<div class="tdl work">Week '+esc(wk.n)+' of '+esc(P.weeks||'?')+': '+todayCode(wk.title)+'<ol>'+
      (wk.steps||[]).map(x=>'<li>'+todayCode(x)+'</li>').join('')+'</ol>'+(wk.done_when?'<small>done when: '+todayCode(wk.done_when)+'</small>':'')+'</div>';
    if(P.start) ph+='<div class="mut">start a session: '+todayCode(P.start)+'</div>';
    const L=P.last_session;
    ph+='<div class="mut">'+(L&&L.date&&(!P.begins||L.date>=P.begins)?'last session logged: '+esc(L.date)+' · '+todayCode(L.text)
      :'no session logged for this plan yet'+(L&&L.date?' (learning log last written '+esc(L.date)+')':''))+'</div>';
    if(DESK&&W.opener) ph+='<div class="mut"><button type="button" data-open="'+escA(P.path)+'">open the plan</button> <span id="todaymsg"></span></div>';
    h+='<section data-sec="plan"><details class="tdet tfold"><summary>'+esc(P.title||'plan')+' · '+esc(P.phase||'')+'</summary>'+ph+'</details></section>'; }
  if((T.daily||[]).length) h+='<section data-sec="daily"><details class="tdet tfold"><summary>Every day</summary>'+T.daily.map(l=>'<div class="lane"><b>'+esc(l.name)+'</b><div>'+
    (l.lines||[]).map(x=>'<div>'+todayCode(x)+'</div>').join('')+'</div></div>').join('')+'</details></section>';
  const F=T.folders||[];
  if(F.some(f=>f&&String(f.desc||'').trim())){       // the table shows once one folder has a description (P51); until then each planet's card says it
    let fh='<div class="fold">'+F.map(f=>'<a class="go" data-area="'+escA(f.name)+'">'+esc(f.name)+'/</a><span class="ftag '+escA(f.tag)+'">'+esc(f.tag)+
      '</span><span>'+todayCode(f.desc)+'</span>').join('')+'</div>';
    if((T.unmapped||[]).length&&T.unmapped_off_map) fh+='<div class="mut">not on the map: '+T.unmapped.map(u=>esc(u)+'/').join(', ')+'</div>';
    else if(F.length&&(T.unmapped||[]).length) fh+='<div class="mut">no description yet: '+T.unmapped.map(u=>'<a class="go" data-area="'+escA(u)+'">'+esc(u)+'/</a>').join(', ')+'</div>';
    fh+='<div class="mut">'+esc(CM.tx('Click a folder to see it on the map.'))+'</div>';
    h+='<section data-sec="folders"><details class="tdet tfold"><summary>What each folder is for</summary>'+fh+'</details></section>'; }
  (T.missing||[]).forEach(m=>{ h+='<div class="tdl bad" data-sec="missing">'+esc(CM.plainText(m,200))+'</div>'; });
  h+='</div><div class="tcol">';
  // RUNNING NOW: each working or waiting workflow as one card (CM.wfRows: "Fix the phone labels · 2 of 4 · ops", its
  // route of planets, the progress bar, the sentence), then each task an agent is on (its title, its try, where), then
  // each agent session at work. A card opens the workflow; a task row its thread
  const rowsN=wf.length+run.length+open.length+working.length;
  h+='<section data-sec="tasks">';
  if(!rowsN) h+=quiet('Running now',(W.task_runners||[]).length?'no agent is working right now':'');
  else h+=head('Running now',rowsN,!need&&open.some(t=>t.status==='review')?open.filter(t=>t.status==='review').length+' to review':'');
  wf.forEach(r=>{ h+=todayWfCard(r,th); });
  // a task reads as its title and one state word; the model shows only in the second opinion's line (P16)
  run.concat(open).slice(0,8).forEach(t=>{ const st=TASK_WORKING.includes(t.status)?'c':t.status==='review'||t.status==='stopped'?'a':'r';
    const ck=t.check&&t.check.verdict&&t.check.verdict!=='checking'?CM.opinionLine(t.check.verdict,t.check.model&&typeof agentLine==='function'?agentLine(t.check.runner,t.check.model):(typeof runnerLabel==='function'?runnerLabel(t.check.runner):'')):'';
    const tries=Number(t.retries)>0&&Number(t.attempts)>0?'try '+Math.min(Number(t.attempts),Number(t.retries)+1)+' of '+(Number(t.retries)+1):'';
    const who=typeof runnerLabel==='function'?runnerLabel(t.runner):String(t.runner||''), where=shortPath(t.target).split('/').pop();
    h+='<div class="tcard ttask '+st+'" data-tid="'+escA(t.id)+'" role="button" tabindex="0"><div class="tt">'+esc(CM.shortTitle(t.title,60)||'a task')+(tries?'<span class="tr">'+esc(tries)+'</span>':'')+'</div>'+
      '<div class="tnow"><b class="sw '+st+'">'+esc(taskWord(t))+'</b>'+(who||where?' · '+esc(who)+(where?' in <b>'+esc(where)+'</b>':''):'')+[ck,taskAge(t.ended||t.started)].filter(Boolean).map(x=>' · '+esc(x)).join('')+'</div></div>'; });
  { const shut=AT.filter(t=>['merged','done','discarded'].includes(t.status)).slice(0,3);
    if(shut.length) h+=det('Closed lately',shut.map(t=>'<div class="mut">'+esc(TASK_WORD[t.status]||t.status)+': <a class="go" data-tid="'+escA(t.id)+'">'+esc(CM.shortTitle(t.title,60)||'a task')+'</a> · '+esc(taskAge(t.ended||t.created))+'</div>').join(''),'tfold'); }
  h+='</section>';
  // the agent sessions at work, under Running now; the idle ones under Details
  h+='<section data-sec="agents">';
  working.forEach(w=>{ h+='<div class="tcard tagent c" data-uid="'+escA(w.uid)+'" role="button" tabindex="0"><div class="tt">'+esc(w.name)+(w.n>1?' ×'+w.n:'')+'</div>'+
    '<div class="tnow">'+esc([agentKind(w), w.where?shortPath(w.where).split('/').pop():'', CM.plainText(w.voice||'',100)].filter(Boolean).join(' · '))+'</div></div>'; });
  { const idleBy={}; idle.forEach(i=>{ (idleBy[agentKind(i)]=idleBy[agentKind(i)]||[]).push(i); });
    const ih=Object.keys(idleBy).map(k=>'<div class="mut">idle '+esc(k)+'s: '+idleBy[k].map(i=>'<a class="go" data-uid="'+escA(i.uid)+'">'+esc(i.name)+'</a>').join(', ')+'</div>').join('')+
      ((A.problems||[]).length?'<div class="mut bad">not reporting: '+A.problems.map(esc).join(' · ')+'</div>':'');
    if(ih) h+=det(idle.length?idle.length+' agents idle':'Agents',ih,'tfold'); }
  if(!(W.task_runners||[]).length) h+='<div class="tdl tnone">Connect an agent to hand out tasks.'+det('How','Install claude or codex (the map finds them), or list <code>runners</code> in container.json.')+'</div>';
  h+='</section>';
  // THE NIGHT RUN: one card, its ticks and Replay; what it did under Details (a count the record does not state stays
  // unsaid: today.py keeps it null, never a made-up 0)
  h+='<section data-sec="night">';
  if(N){ const has=k=>Number.isInteger(N[k])&&N[k]>=0, nn=k=>has(k)?N[k]:0, its=(Array.isArray(N.items)?N.items:[]).filter(i=>i&&typeof i==='object');
    const said=[['ran','ran'],['ready','ready for your keep or drop'],['failed','failed']].filter(p=>has(p[0])).map(p=>N[p[0]]+' '+p[1])
      .concat(nn('refused')?[N.refused+' refused']:[]);
    const vs=its.map(i=>String(i.verdict||'')), bad=nn('failed')||vs.some(v=>/fail|refus|error/i.test(v)), needs=nn('ready')||vs.some(v=>/ready|pass|keep/i.test(v));
    const mark=v=>/fail|refus|error/i.test(v)?'<i class="r">✕</i>':/ready|pass|keep|done|merged/i.test(v)?'<i class="g">✓</i>':'<i>·</i>';
    const n=has('ran')?N.ran:its.length, nw=wfAll.find(r=>r&&r.night)||(typeof CM.wfList==='function'?CM.wfList():[]).find(w=>w&&w.derived==='night');
    const short=[['ready','ready'],['failed','failed']].filter(p=>has(p[0])).map(p=>N[p[0]]+' '+p[1]).concat(nn('refused')?[N.refused+' refused']:[]);
    h+='<details class="tcard tnight '+(bad?'bad':needs?'need':'work')+'"><summary><div class="tnl"><div class="tt">Night run'+(n?' · '+n+' task'+(n===1?'':'s'):'')+'</div>'+
      '<div class="tnow">'+esc(short.length?short.join(', '):said.length?said.join(' · '):'what it left is below')+'</div></div>'+
      '<span class="tk">'+its.slice(0,8).map(i=>mark(String(i.verdict||''))).join('')+'</span>'+
      (nw?'<button type="button" class="tbtn" data-wf-replay="'+escA(String(nw.id||''))+'">Replay</button>':'')+'</summary>';
    h+='<div class="tdl '+(bad?'bad':needs?'need':'work')+'" data-night="sum">'+(said.length?said.join(' · '):'the record states no counts'+(its.length?', only the '+its.length+' item'+(its.length>1?'s':'')+' below':''))+(N.date?'<small>the night of '+esc(N.date)+'</small>':'')+'</div>';
    h+=its.slice(0,8).map(i=>{ const v=String(i.verdict||'');
      return '<div class="tdl '+(/fail|refus|error/i.test(v)?'bad':/ready|pass|keep/i.test(v)?'need':'')+'" data-night="item" data-item="'+escA(String(i.id||''))+'">'+esc(CM.shortTitle(i.title||'',60)||'a task')+'<small>'+
        [i.repo?String(i.repo).replace(/[/]+$/,'').split('/').pop():'',v,(i.minutes!=null&&i.minutes!=='')?i.minutes+' min':''].filter(x=>x!=null&&x!=='').map(x=>esc(String(x))).join(' · ')+'</small></div>'; }).join('')+
      (its.length>8?'<div class="mut">and '+(its.length-8)+' more</div>':'')+
      '<div class="mut">'+(N.source==='digest'?'from the digest'+(N.line?': '+esc(CM.plainText(N.line,160)):''):'from the night run&#39;s own record')+'</div>'+
      '<div class="mut">You keep or drop each one in the night run.</div></details>'; }
  else h+='<div class="tdl tnone" data-night="none">'+(T.night_note&&typeof T.night_note==='string'?esc(CM.plainText(T.night_note,200)):'No night run on record yet.'+
    det('Set up','Tell the map where the night run writes its report: <code>today.night_json</code> in container.json, or the digest line that marks it: <code>today.night_marker</code>.'))+'</div>';
  h+='</section>';
  // done today: the agents' tasks marked done, under one folded line
  const D=T.done||[];
  h+='<section data-sec="done">'+(D.length?det('Done today · '+D.length,D.slice(0,8).map(d=>'<div class="tdl">'+esc(d.at)+' · '+esc(CM.shortTitle(d.title,60))+(d.checked?' <span class="ftag LIVE">second opinion passed</span>':'')+'</div>').join('')+
    (D.length>8?'<div class="mut">and '+(D.length-8)+' more in the task list</div>':''),'tfold'):'')+'</section>';
  // the map in miniature (desktop): ui/phone.js paints the live map into it while Today is up
  h+='<div class="tmini" data-go="map" role="button" tabindex="0" aria-label="Open the map"><canvas id="tminic" width="520" height="158"></canvas><span class="cap">The map</span><span class="tbtn">Open'+keyT+'</span></div>';
  h+='</div></div></div>';
  if(el.innerHTML!==h) el.innerHTML=h;
}
// the ledger's open rows (today.py: the last line per id, open only, the age counted from "opened"), oldest first.
// Declared here, inside the part of the script that renderToday is read with, so it runs on its own (test_today).
function ledgerDays(r){ if(r&&r.age_days!=null&&Number.isFinite(Number(r.age_days))) return Math.max(0,Math.floor(Number(r.age_days)));
  const t=Date.parse(String((r&&r.opened)||'')); return Number.isFinite(t)?Math.max(0,Math.floor((Date.now()-t)/86400000)):-1; }
function ledgerRows(T){ return ((T&&Array.isArray(T.ledger))?T.ledger:[]).filter(r=>r&&typeof r==='object'&&r.id!=null)
  .slice().sort((a,b)=>(ledgerDays(b)-ledgerDays(a))||String(a.opened||'').localeCompare(String(b.opened||''))||String(a.id).localeCompare(String(b.id))); }
function ledgerMax(){ return 20; }
// Today's Needs you rows: the sky's answer and review contacts, oldest first (null with no sky to read, or where the
// page around renderToday has none, as in test_today)
function todayNeed(){ if(typeof skyOk!=='function'||typeof skyContacts!=='function'||typeof reviewContacts!=='function'||!skyOk()) return null;
  return skyContacts().filter(c=>CM.bucket(c)==='answer').concat(reviewContacts()).sort((a,b)=>(Number(b.age_days)||0)-(Number(a.age_days)||0)); }
// the night run's own workflow (workflows.py night_workflow: "derived": "night") is the Night run card, never a Running now row
function todayDerived(id){ const w=(typeof CM.wfList==='function'?CM.wfList():[]).find(x=>x&&String(x.id)===String(id)); return !!(w&&w.derived); }
// "updated 2 min ago": the render's clock (W.generated, "Tue Oct 13, 12:07 AM", read as this year) against now
function todayAgo(){
  const g=String(W.generated||''), parts=g.split(', '), d=(parts[0]||'').split(' '), t=(parts[1]||'').split(/[: ]/);
  const mo='JanFebMarAprMayJunJulAugSepOctNovDec'.indexOf(d[1]||'_')/3, day=Number(d[2]), hr=Number(t[0])%12+(t[2]==='PM'?12:0), mi=Number(t[1]);
  if(!(Number.isInteger(mo)&&mo>=0&&day>0&&Number.isFinite(hr)&&Number.isFinite(mi))) return g?'updated '+g:'';
  const now=new Date(), at=new Date(now.getFullYear(),mo,day,hr,mi); if(at-now>86400000) at.setFullYear(now.getFullYear()-1);
  return 'updated '+(typeof CM.relTime==='function'?CM.relTime(Math.max(0,(now-at)/1000)):g);
}
// the header's "Where" chips: the planets with something for the user, each with its badge (red with a problem, else amber)
function todayWhere(th){
  if(typeof MOONS==='undefined'||typeof moonCounts!=='function'||typeof CM.labelText!=='function') return '';
  const L=MOONS.map(m=>{ const n=moonCounts(m); return {m:m,n:n,k:(n.problems||0)+(n.need_you||0)+(n.decisions||0)}; }).filter(x=>x.n.problems||x.n.need_you)
    .sort((a,b)=>((b.n.need_you||0)-(a.n.need_you||0))||(b.k-a.k)).slice(0,3);
  return L.length?'<div class="twhere"><span class="lbl">Where</span>'+L.map(x=>'<a class="twc" role="button" tabindex="0" data-show="'+escA(x.m.uid)+'">'+th(x.m.uid,28)+
    esc(CM.labelText(x.m.uid))+'<i class="'+(x.n.problems?'r':'a')+'">'+x.k+'</i></a>').join('')+'</div>':'';   // links, not buttons: a look's header-button rule (the vector look's) leaves them their names
}
// one workflow as a Running now card: its title and "2 of 4", the route of its planets with each step's state, the
// progress bar (one class per 5 percent: no style from markup), the sentence ("Now: Fix the label slots in ops"); its
// aria-label is the row in one line ("Fix the phone labels · 2 of 4 · ops"). Ids stay in data-wf, never in the text
function todayWfCard(r,th){
  const w=(typeof CM.wfList==='function'?CM.wfList():[]).find(x=>x&&String(x.id)===r.id)||{};
  const own=(Array.isArray(w.steps)?w.steps:[]).filter(x=>x&&typeof x==='object');
  // a stop's progress: {done, total} (a step's own) or 0..1 (a row's stop)
  const prog=v=>v&&typeof v==='object'&&v.total>0?{done:v.done,total:v.total,f:Math.max(0,Math.min(1,v.done/v.total))}:typeof v==='number'&&Number.isFinite(v)?{f:Math.max(0,Math.min(1,v))}:null;
  // the 2D workflows module's row.stops (ui/workflows2d.js CM.wfRows): key, label, short, state, word, place, uid, a window of at most 7 round the step
  // at work; the step's own progress rides the row (row.progress), so it goes to the stop at work. The step itself is found by
  // its state in that window, not by its number (the window may start past the first step)
  const fromRow=Array.isArray(r.stops)&&r.stops.length;
  const steps=fromRow?r.stops.filter(x=>x&&typeof x==='object').map(x=>({planet_uid:x.uid,label:x.label||x.name,state:x.state,kind:x.kind,
      progress:x.progress||((x.state==='working'||x.state==='needs_you')&&r.progress?r.progress:null)}))
    :own.map(x=>({planet_uid:x.planet_uid,label:x.label,state:x.state,kind:x.kind,progress:x.progress}));
  const total=r.total||steps.length, at=r.step||Math.min(total,r.done+1);
  const cur=(fromRow?steps.find(s=>['working','needs_you','failed'].includes(s.state)):null)||steps[at-1]||own[at-1]||null, cp=cur?prog(cur.progress):null;
  const pr=cp?cp.f:null;
  const frac=typeof r.progress==='number'&&Number.isFinite(r.progress)?Math.max(0,Math.min(1,r.progress)):total?Math.max(0,Math.min(1,(r.done+(cur&&cur.state==='working'&&pr!=null?pr:0))/total)):0;
  const title=CM.shortTitle(r.title||'',60)||'a workflow', col=r.state==='needs_you'?'a':r.state==='failed'?'r':'c', line=[title,at+' of '+total,r.place].filter(Boolean).join(' · ');
  const word=s=>{ const p=prog(s.progress); return s.state==='done'?['g','done']:s.state==='working'?['c',p&&p.total?p.done+' of '+p.total:p?Math.round(p.f*100)+'%':'working']
    :s.state==='needs_you'?['a','you']:s.state==='failed'?['r','failed']:s.state==='skipped'?['','skipped']:s.kind==='gate'?['','you']:['','waiting']; };   // a gate ahead waits for the user
  // five stops at most: the first, the one at work and its two neighbours, the last
  let idx=steps.map((s,i)=>i);
  if(idx.length>5){ const c=Math.max(0,cur&&steps.includes(cur)?steps.indexOf(cur):at-1); idx=Array.from(new Set([0,c-1,c,c+1,steps.length-1].filter(i=>i>=0&&i<steps.length))).sort((a,b)=>a-b); }
  const stops=idx.map((i,k)=>{ const s=steps[i], wd=word(s), prev=k?steps[idx[k-1]]:null;
    return (prev?'<i class="tln'+(prev.state==='done'&&s.state!=='waiting'?' g':'')+'"></i>':'')+'<div class="tst">'+th(String(s.planet_uid||''),34)+
      '<b title="'+escA(String(s.label||''))+'">'+esc(CM.shortTitle(s.label||'',40))+'</b><span class="'+wd[0]+'">'+esc(wd[1])+'</span></div>'; }).join('');
  const now=r.sentence?esc(CM.plainText(r.sentence,140)):cur?(cur.state==='needs_you'?'Waiting for you: ':cur.state==='failed'?'Stopped at: ':'Now: ')+'<b>'+esc(CM.shortTitle(cur.label||'',48))+'</b>'+
    (r.place?' in '+esc(r.place):'')+(cur.state==='working'&&cp&&cp.total?' · '+cp.done+' of '+cp.total:''):'';
  return '<div class="tcard trun '+col+'" data-wf="'+escA(r.id)+'" role="button" tabindex="0" aria-label="'+escA(line)+'">'+
    '<div class="tt">'+esc(title)+'<span class="tr">'+at+' of '+total+'</span></div>'+
    (stops?'<div class="troute">'+stops+'</div>':'')+'<div class="tdr"><i class="l'+Math.round(frac*20)+'"></i></div>'+
    '<div class="tdrl"><span><b>'+r.done+' of '+total+'</b> steps done'+(pr!=null?' · step '+at+' at '+Math.round(pr*100)+'%':'')+'</span></div>'+
    (now?'<div class="tnow">'+now+'</div>':'')+
    '<div class="tbtns">'+(cur&&cur.planet_uid?'<button type="button" class="tbtn" data-show="'+escA(String(cur.planet_uid))+'">Show on map</button>':'')+'<button type="button" class="tbtn" data-follow="'+escA(r.id)+'">Follow in 3D</button></div></div>';
}
document.getElementById('today').addEventListener('click',e=>{
  // Show on map, Follow in 3D, Replay and a workflow card sit inside rows that open something else: they go first (ui/phone.js)
  const sw=e.target.closest('[data-show]'); if(sw){ e.preventDefault(); todayShow(sw.dataset.show); return; }
  const fw=e.target.closest('[data-follow]'); if(fw){ e.preventDefault(); todayFollow(fw.dataset.follow); return; }
  const rp=e.target.closest('[data-wf-replay]'); if(rp){ e.preventDefault(); if(typeof CM.wfReplay!=='function') todayReplay(rp.getAttribute('data-wf-replay')); return; }   // inside the night card's summary: no fold; the workflow module answers it once it is in
  const wr=e.target.closest('[data-wf]'); if(wr){ todayWorkflow(wr.dataset.wf); return; }
  const g=e.target.closest('[data-go]'); if(g){ setView(g.dataset.go); return; }
  if(e.target.closest('[data-board]')){ setView('map'); toggleQuests(true); return; }
  const cr=e.target.closest('[data-cid]'); if(cr){ const c=skyContacts().find(x=>x.id===cr.dataset.cid); setView('map'); if(c) openContact(c); return; }
  if(e.target.closest('[data-tnew]')){ openComposer(null); return; }
  const tk=e.target.closest('[data-tid]'); if(tk){ setView('map'); openTask(tk.dataset.tid); return; }
  const a=e.target.closest('[data-uid]'); if(a){ const u=unitByUid(a.dataset.uid); setView('map'); if(u){ humanMove(); show(u); } return; }
  const f=e.target.closest('[data-area]'); if(f){ const u=units.find(x=>x.kind==='area'&&x.name===f.dataset.area); setView('map'); if(u){ humanMove(); show(u); } return; }
  const k=e.target.closest('[data-checkin]'); if(k){ const id=k.dataset.checkin, undo=!!k.dataset.undo, m=document.getElementById('ck-'+id);
    k.disabled=true;
    api('/checkin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(undo?{id:id,undo:true}:{id:id})})
      .then(j=>{ if(m) m.textContent=(j.undo?'taken back for ':'logged for ')+j.day+' · the page catches up within 15 s'; })
      .catch(err=>{ k.disabled=false; if(m) m.textContent=err.message; });
    return; }
  const o=e.target.closest('[data-open]'); if(o){ const m=document.getElementById('todaymsg'); o.disabled=true;
    api('/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:o.dataset.open})})
      .then(j=>{ if(m) m.textContent='opened with '+(j.opener||'the configured opener'); })
      .catch(err=>{ if(m) m.textContent=err.message; }).finally(()=>{ setTimeout(()=>{ o.disabled=false; },1500); }); }
});
// ---- TASKS (tasks.py): say what you want → an agent works in that folder (its own git worktree
// in a repo) → watch it live → review the diff → merge, follow up, or set it aside. -------------
function agentTasks(){ return Array.isArray(W.agent_tasks)?W.agent_tasks:[]; }
function taskCounts(){ const c={running:0,review:0}; agentTasks().forEach(t=>{ if(TASK_WORKING.includes(t.status)) c.running++; else if(TASK_OPEN.includes(t.status)) c.review++; }); return c; }
function taskAge(iso){ return CM.agoOf(iso||''); }   // "2 h ago", "1 day ago" (CM.relTime)
// what W.counts.working counts (sky.counts), row by row: every agent at work now. A working agent session or T3 thread
// (what sky mode hides as in warp, a T3 thread once even when two apps show it), a task running or being checked (or
// failed with its retry due), and each workflow whose own note step works, its author once. Both rails list these rows
// and print their number with data-count="working", so __wordsAudit holds them to the status bar's
function workingRows(){
  const rows=[], t3=new Set(), who=new Set();
  units.filter(u=>u&&u.kind!=='task'&&!u.remote&&warpHidden(u)).forEach(u=>{
    if(u.source==='t3'){ const tid=String((u.t3&&typeof u.t3==='object'&&u.t3.thread_id)||String(u.uid||'').replace(/^ship:t3:/,'')); if(t3.has(tid)) return; t3.add(tid); }
    rows.push({kind:'agent',u:u}); });
  agentTasks().filter(t=>t&&(TASK_WORKING.includes(t.status)||t.status==='new'||(t.status==='failed'&&!!t.retry_due))).forEach(t=>rows.push({kind:'task',t:t}));
  CM.wfList().forEach(w=>{ if(!w||typeof w!=='object') return;
    const st=(Array.isArray(w.steps)?w.steps:[]).find(x=>x&&x.kind==='note'&&x.state==='working'); if(!st) return;
    const by=w.by&&typeof w.by==='object'?w.by:{}, k=String(by.seat||by.name||w.id||''); if(who.has(k)) return; who.add(k);
    rows.push({kind:'wf',w:w,step:st}); });
  return rows;
}
// what W.counts.review counts, row by row, oldest first: each task back from its agent for your review (the live list,
// so a task that came back since the last render is a row at once; its sky contact when the sky has it), then each other
// contact the sky puts in the review bucket (a T3 thread that replied, a workflow waiting on a merge); null with no sky
function taskDays(t){ const at=Date.parse(String((t&&(t.ended||t.started||t.created))||'')); return Number.isFinite(at)?Math.max(0,Math.floor((Date.now()-at)/86400000)):null; }
function reviewContacts(){ if(!skyOk()) return null; const C=skyContacts(), byId=new Map(C.map(c=>[c.id,c]));
  const tasks=agentTasks().filter(t=>t&&(t.status==='review'||t.status==='stopped')).map(t=>byId.get('task:'+t.id)||
    {id:'task:'+t.id,source:'task',state:'review',title:CM.shortTitle(String(t.title||t.text||''))||'a task',age_days:taskDays(t),planet_uid:null});
  return tasks.concat(C.filter(c=>c.source!=='task'&&CM.bucket(c)==='review')).sort((a,b)=>(Number(b.age_days)||0)-(Number(a.age_days)||0)); }
// a number on screen that is one of W.counts' (data-count, read back by __wordsAudit): "<b>13</b> to review"
function countHtml(noun,n,words){ return '<span data-count="'+attr(noun)+'"><b>'+n+'</b> '+esc(words)+'</span>'; }
// the rail's header: what works now and what waits for your review, the same numbers as the status bar
function sideCountHtml(){ const w=workingRows().length, rc=reviewContacts(), r=rc?rc.length:taskCounts().review;
  return [w?countHtml('working',w,'working'):'', r?(rc?countHtml('review',r,'to review'):r+' to review'):''].filter(Boolean).join(' · '); }
function sideCount(){ const sc=document.getElementById('scount'); if(!sc) return; const h=sideCountHtml(); if(sc.innerHTML!==h) sc.innerHTML=h; }
// a path as the map names it: below the root, the part after it; the root itself, its folder's name (never the raw absolute path)
function shortPath(p){ p=String(p||''); const r=String(W.root_path||''); if(r&&p===r) return r.replace(/[/]+$/,'').split('/').pop()||r; return r&&p.startsWith(r+'/')?p.slice(r.length+1):p; }
function applyTasks(j){
  if(!j||!Array.isArray(j.tasks)) return;
  TASKS_AT=Date.now();
  const sig=taskSig;
  const before=sig(agentTasks()), known=new Set(agentTasks().map(t=>t.id)), ids=new Set(j.tasks.map(t=>t.id)), was=new Map(agentTasks().map(t=>[t.id,String(t.status||'')]));
  j.tasks.forEach(t=>{ if(t.status==='merged'&&was.has(t.id)&&was.get(t.id)!=='merged') fxMergeBanner(taskTitle(t)); });
  j.tasks.forEach(t=>{ if(!known.has(t.id)&&t.status==='running'&&Date.now()-Date.parse(t.started||0)<15000&&LAUNCHES[t.id]==null) LAUNCHES[t.id]=performance.now(); });
  Object.keys(LAUNCHES).forEach(id=>{ if(!ids.has(id)) delete LAUNCHES[id]; });
  W.agent_tasks=j.tasks; if(Array.isArray(j.runners)) W.task_runners=j.runners;
  radioTasks(was,j.tasks);
  tvFollow(j.tasks);
  const wfBefore=wfSig();
  CM.onTasks(j);
  pgSync(j);
  const seatsWere=j.world?null:radioSnap();   // the contacts before this reply's sky (skyWorld says when one was cleared)
  const took=CM.takeSnapshot(j);   // the reply's sky and count together (sky.build + sky.counts with these threads), or neither
  const changed=sig(j.tasks)!==before||took.sky;
  if(changed) place();   // place() puts each contact at its planet again: the badges follow
  if((changed||wfSig()!==wfBefore)&&VIEW==='today') renderToday();   // a workflow's step, state or progress moved: Today's Running now follows
  if(took.sky&&seatsWere) skyWorld(seatsWere);
  if(!j.world) cmSceneTouch();   // a /state reply touches once, after its own place()
  const list=document.getElementById('tasks');
  if(sideOpen()&&(changed||!list.innerHTML)){ const top=list.scrollTop; renderTaskList(); list.scrollTop=top; sideCount(); }
  if(took.counts||took.sky) countsRefresh(); else if(changed) renderDash();
}
// the surfaces that print the one count, read again after /tasks moved it (the status strip also re-reads it every 500 ms):
// the strip, the rail's header and rows, the HUD, Today, the phone's badges and an open planet card (its numbers are its planet's)
function countsRefresh(){ stripTick(); sideCount(); renderDash(); hudWorld(); if(VIEW==='today') renderToday(); if(MOB) mobSync(); cardRefresh(); }
function cardRefresh(){ const p=document.getElementById('panel'); if(!p||!openUnit||openTaskId||!(openUnit.kind==='area'||openUnit.kind==='dir')||openUnit.remote) return;
  if(!(p.classList&&p.classList.contains('on'))) return;
  const u=units.find(x=>x.uid===openUnit.uid); if(!u) return; const b=document.getElementById('pbody'), top=b?b.scrollTop:0; openUnit=u; renderCard(u); if(b) b.scrollTop=top; }
// The open thread panel follows its task: a retry, a check or a reply starts a new run behind its back, so a
// status or run count that differs from what the panel last showed wakes its poll again.
function tvFollow(tasks){
  if(!TV||!LIVE) return;
  const t=tasks.find(x=>x.id===TV.id);
  if(!t||(t.status===TV.status&&t.runs===TV.runs)) return;
  if(!TV.timer){ const ep=TV.epoch; TV.timer=setInterval(()=>loadTask(ep),1500); }
  loadTask(TV.epoch);
}
async function pollTasks(){
  if(!LIVE) return;
  const seq=++TASKS_SEQ;
  try{ const j=await api('/tasks'); if(seq<TASKS_APPLIED) return; TASKS_APPLIED=seq; applyTasks(j); }catch(e){ sessionLost(e); }
}
// proper names: a runner's own label and a model's full name ("Claude Opus 5.5"), never the bare key
function runnerOf(name){ return (W.task_runners||[]).find(r=>r.name===name)||null; }
function runnerLabel(name){ const r=runnerOf(name); return r?(r.label||r.name):String(name||''); }
function modelLabel(runner,model){ const r=runnerOf(runner), m=r&&(r.models||[]).find(x=>x.name===model); return m?(m.label||m.name):String(model||''); }
function agentLine(runner,model){ if(!model||model==='default') return runnerLabel(runner); const m=modelLabel(runner,model); return m; }
function mateName(){ return String((W.wingmate&&W.wingmate.name)||SKIN_NAMES.wingmate); }
function composerOpen(){ return document.getElementById('composer').classList.contains('on'); }
// ---- RAILS: the dashboard on the left, the threads on the right, the map between them ----
function sideOpen(){ return document.getElementById('wrap').classList.contains('side'); }
function sideShow(on){ const w=document.getElementById('wrap'); w.classList.toggle('side',!!on);
  if(!on&&composerOpen()){ document.getElementById('composer').classList.remove('on'); document.getElementById('side').classList.remove('compose','pick'); AIM=null; }   // the composer lives in the rail: hiding the rail closes it, so N opens it again
  try{ sessionStorage.setItem('side',on?'on':'off'); }catch(e){} fit();
  if(on){ renderTaskList(); sideCount(); } }
function dashShow(on){ const w=document.getElementById('wrap'); w.classList.toggle('dash',!!on); try{ localStorage.setItem('dash',on?'on':'off'); }catch(e){} fit(); if(on) renderDash(); }
let DASH_HTML='', DASH_TAB='console'; try{ if(localStorage.getItem('dashTab')==='today') DASH_TAB='today'; }catch(e){}
const DASH_ROWS=5;
// one rail row, 32 px: a state pip, the words, a short tag on the right (the rest in its title)
// what waits at a planet (or circling the sun: SKY_STAR) in the count's buckets: {problems, need_you, decisions}. With the
// sky on, its contact craft by CM.bucket (each contact is drawn in one place, so the places add up to W.counts); a craft
// that is not a contact (the sky off) is a decision when it is a board item, else a problem
function moonCounts(m){ const n={problems:0,need_you:0,decisions:0};
  ((m&&m.foes)||[]).forEach(f=>{ if(!f) return;
    if(f.kind==='contact'&&f.contact){ const b=CM.bucket(f.contact); n[b==='answer'||b==='review'?'need_you':b==='decision'?'decisions':'problems']++; }
    else if(!(SKY&&skyOk())) n[f.kind==='item'?'decisions':'problems']++; });
  return n; }
// "1 problem · 2 need you · 6 decisions" (problems first); '' when nothing waits
function moonCountLine(n){ return ['problems','need_you','decisions'].filter(k=>n&&n[k]).map(k=>n[k]+' '+CM.countNoun(k,n[k])).join(' · '); }
// the craft at a planet the sky does not carry (no contact: an old-style enemy, a wreck's raider), said as problems when the
// planet has no contact at all; a contact is never counted here, and neither is a board item (a decision is not a problem)
function moonOldFoes(m){ const n=moonCounts(m); if(n.problems+n.need_you+n.decisions) return 0; return ((m&&m.foes)||[]).filter(f=>f&&f.kind!=='contact'&&f.kind!=='item').length; }
//, gap (a): the planet's badge, its tag's second row, its card's state and tiles and the overview's chips all read
// moonCounts, so each contact is in ONE bucket and a planet's numbers add up to the status bar's. A planet whose only craft
// are old ones the sky does not carry (no contact: a late job, a failed thread) says them as problems, as before.
function moonLine(m){ const old=moonOldFoes(m); return moonCountLine(moonCounts(m))||(old?old+' '+CM.countNoun('problems',old):''); }
// the badge (a number only, red if any problem, amber otherwise): ONE number, what waits at the planet that is not a
// decision (its problems + what needs you; the headline splits decisions off, and the board holds them all); k is the bucket
// that gives the colour (problems when any, else need_you), line the breakdown the hover row and the card say: "3 · 1 problem,
// 2 need you" (one bucket: just "2 need you"). A planet whose only craft are old ones the sky does not carry says them as problems
function moonBadge(m){ const n=moonCounts(m), t=n.problems+n.need_you, old=t?0:moonOldFoes(m);
  if(!t) return old?{k:'problems',n:old,col:'bad',line:old+' '+CM.countNoun('problems',old)}:{k:'',n:0,col:'',line:''};
  const parts=['problems','need_you'].filter(x=>n[x]).map(x=>n[x]+' '+CM.countNoun(x,n[x]));
  return {k:n.problems?'problems':'need_you',n:t,col:n.problems?'bad':'need',line:(parts.length>1?t+' · ':'')+parts.join(', ')}; }
// what the hover row under a tag says: the badge's breakdown, then the decisions ("3 · 1 problem, 2 need you · 3 decisions")
function moonHover(m){ const n=moonCounts(m), b=moonBadge(m); return [b.line,n.decisions?n.decisions+' '+CM.countNoun('decisions',n.decisions):''].filter(Boolean).join(' · '); }
// every place a count sits, for __wordsAudit: each planet's buckets and badge, then what circles the sun
CM.planetCounts=function(){ if(!(SKY&&skyOk())||deck!=='home') return [];
  return MOONS.map(m=>({uid:m.uid,label:m.label,n:moonCounts(m),badge:moonBadge(m)}))
    .concat([{uid:'sky:star',label:CM.word('this machine'),n:moonCounts(SKY_STAR),badge:moonBadge(SKY_STAR)}]); };
function dashRow(cls,data,text,tag,title){ return '<div class="row '+cls+'" role="button" tabindex="0" '+data+(title?' title="'+attr(title)+'"':'')+'><i></i><span class="rt">'+esc(text)+'</span>'+(tag?'<span class="rm">'+esc(tag)+'</span>':'')+'</div>'; }
// a titled section: the eyebrow, at most five rows, then one "+N more" row that opens the whole list
// parts {noun, n:[the number of that noun each row prints]}: the rows split that count (data-parts, __wordsAudit adds
// them up), so the "+N more" row says and carries what the hidden rows hold ("+3 more · 4 problems")
function dashSection(title,go,rows,empty,more,count,parts){
  const over=Math.max(0,rows.length-DASH_ROWS), data='data-dash="'+go[0]+'"';
  const rest=parts&&over?parts.n.slice(DASH_ROWS).reduce((a,b)=>a+(Number(b)||0),0):0;
  const tail=[over?'+'+over+' more'+(rest?' · '+rest+' '+CM.countNoun(parts.noun,rest):''):'',more||''].filter(Boolean).join(' · ');
  return '<section class="dsec"'+(parts?' data-parts="'+attr(parts.noun)+'"':'')+'><h3>'+esc(title)+(rows.length?'<span class="dn"'+(count?' data-count="'+attr(count)+'"':'')+'>'+rows.length+'</span>':'')+'<a '+data+'>'+esc(go[1])+'</a></h3>'+
    (rows.length?rows.slice(0,DASH_ROWS).join(''):'<div class="mut">'+esc(empty)+'</div>')+
    (tail?dashRow('more',data+(rest?' data-part="'+rest+'"':''),tail,'',''):'')+'</section>';
}
function renderDash(){
  const el=document.getElementById('dash'); if(!el||!document.getElementById('wrap').classList.contains('dash')) return;
  const T=W.today||{}, A=T.agents||{}, working=A.working||[], waiting=A.waiting||[], Y=T.word||{}, AT=agentTasks();
  const words=(Y.items||[]).map(c=>dashRow(c.state==='ok'?'ok':c.state==='bad'?'bad':c.state==='idle'||c.state==='off'?'':'need','data-lane="'+attr(c.lane)+'"',String(c.what||''),String(c.lane||''),String(c.status||'')));
  let h='<div class="dtabs" role="tablist"><button type="button" role="tab" data-dtab="console"'+(DASH_TAB==='console'?' class="on" aria-selected="true"':'')+'>Console</button>'+
    '<button type="button" role="tab" data-dtab="today"'+(DASH_TAB==='today'?' class="on" aria-selected="true"':'')+'>Today'+(words.length?'<b>'+words.length+'</b>':'')+'</button></div>';
  if(DASH_TAB==='today'){
    h+=dashSection('Your word'+(Y.summary?' · '+Y.summary:''),['today','open'],words,'no commitments written yet','');
  } else {
    // Needs you = the sky's need-you contacts (an answer or a review: CM.bucket), oldest first, so its number is W.counts'
    // need_you; decisions and problems have their own homes (the board, System)
    const nc=CM.counts(), od=nc.oldest_decision||{};
    const need=skyContacts().filter(c=>{ const g=CM.bucket(c); return g==='answer'||g==='review'; })
      .sort((a,b)=>(Number(b.age_days)||0)-(Number(a.age_days)||0))
      .map(c=>dashRow('need','data-cid="'+attr(c.id)+'"',CM.shortTitle(c.title||c.voice||'',60)||CM.contactWord(c),CM.contactWord(c),CM.contactLine(c)));
    h+=dashSection('Needs you',['board','decisions'],need,CM.word('empty sky'),
      nc.decisions?nc.decisions+' '+CM.countNoun('decisions',nc.decisions)+(Number.isInteger(od.days)?' · oldest '+od.days+' days':''):'','need_you');
    // Agents = every agent at work (workingRows, the rows W.counts.working counts), so its number is the status bar's
    const crew=workingRows().map(r=>r.kind==='task'?dashRow('work','data-tid="'+attr(r.t.id)+'"',CM.shortTitle(r.t.title||'',60)||'a task',taskWord(r.t),agentLine(r.t.runner,r.t.model)+' · '+shortPath(r.t.target))
      :r.kind==='wf'?dashRow('work','data-dash="threads"',CM.shortTitle(r.w.title||'',60)||'a workflow','working',String(r.step.label||''))
      :dashRow('work','data-uid="'+attr(r.u.uid)+'"',String(r.u.alias||r.u.name||'an agent'),'working',shortPath(r.u.cwd||'')));
    const idle=(A.idle||[]).map(x=>x.name);
    h+=dashSection('Agents',['threads','all agents'],crew,'Nobody is working',
      idle.length?idle.length+' idle: '+idle.slice(0,4).join(', ')+(idle.length>4?'…':''):'','working');
    // System: every place something waits, in the count's own nouns (a decision is not a problem): "1 problem · 8
    // decisions" (moonCounts). With the sky on, each contact sits where the map draws it, a planet's craft or circling
    // the sun when no planet carries it ("This machine"), so the rows split W.counts and their problems add up to it
    // (data-parts, __wordsAudit). A row with two or more counts wraps them under its name instead of cutting them.
    const seen=new Set(), sys=[], parts=[], skyOn=SKY&&skyOk();
    const sysRow=(cls,data,label,n,title)=>{ const tag=moonCountLine(n); parts.push(n.problems);
      return dashRow(cls+(tag.includes(' · ')?' tall':''),data+' data-part="'+n.problems+'"',label,tag,title); };
    const places=MOONS.map(m=>({m:m,n:moonCounts(m)})).concat(skyOn?[{m:null,n:moonCounts(SKY_STAR)}]:[])
      .filter(x=>x.n.problems+x.n.need_you+x.n.decisions>0&&(skyOn||!x.m.allies));
    const all=n=>n.problems+n.need_you+n.decisions;
    places.sort((a,b)=>(b.n.problems-a.n.problems)||(all(b.n)-all(a.n))).forEach(x=>{ const n=x.n, cls=n.problems?'bad':'need';
      if(!x.m){ sys.push(sysRow(cls,'data-dash="star"',CM.word('this machine'),n,'in no folder: they circle the sun')); return; }
      seen.add(x.m.uid);
      sys.push(sysRow(cls,'data-uid="'+attr(x.m.uid)+'"',x.m.label,n,x.m.allies?x.m.allies+' agent'+(x.m.allies>1?'s':'')+' on it':CM.word('undefended'))); });
    MOONS.filter(m=>m.trend&&m.trend.worse&&!seen.has(m.uid)).forEach(m=>{ seen.add(m.uid); parts.push(0); sys.push(dashRow('bad','data-uid="'+attr(m.uid)+'"',m.label,'worse','worse this week: '+m.trend.why)); });
    MOONS.filter(m=>m.wellR&&!seen.has(m.uid)).sort((a,b)=>b.bytes-a.bytes).slice(0,3).forEach(m=>{ parts.push(0); sys.push(dashRow('','data-uid="'+attr(m.uid)+'"',m.label,fmtBytes(m.bytes),'heavy · '+fmtBytes(m.bytes)+(m.bloat>=30?' · '+m.bloat+'% bloat':''))); });
    h+=dashSection('System',['star','overview'],sys,'every planet is healthy','',null,skyOn?{noun:'problems',n:parts}:null);
  }
  if(DASH_HTML!==h){ DASH_HTML=h; el.innerHTML=h; }
}
document.getElementById('dash').addEventListener('click',e=>{
  const tab=e.target.closest('[data-dtab]'); if(tab){ DASH_TAB=tab.dataset.dtab==='today'?'today':'console'; try{ localStorage.setItem('dashTab',DASH_TAB); }catch(err){} renderDash(); return; }
  const r=e.target.closest('[data-dash],[data-uid],[data-tid],[data-lane],[data-cid]'); if(!r) return;
  if(r.dataset.cid){ const c=skyContacts().find(x=>x.id===r.dataset.cid); setView('map'); if(c) openContact(c); return; }
  if(r.dataset.dash==='today') return setView('today'); if(r.dataset.dash==='board'){ setView('map'); return toggleQuests(true); }
  if(r.dataset.dash==='threads') return toggleTasks(true); if(r.dataset.dash==='star'){ setView('map'); return showStar(); }
  if(r.dataset.tid){ setView('map'); return openTask(r.dataset.tid); }
  let u=r.dataset.uid?unitByUid(r.dataset.uid):null;
  if(r.dataset.lane){ const nm=lanePlanet(r.dataset.lane,units); u=nm?units.find(x=>x.kind==='area'&&x.name===nm):null; if(!u) return setView('today'); }
  if(u){ setView('map'); humanMove(); const a=approach(u); pilot={source:'human',x:a.x,y:a.y}; activate(u); }
});
document.getElementById('sclose').addEventListener('click',()=>sideShow(false));
document.getElementById('snew').addEventListener('click',()=>openComposer(VIEW==='map'?(near&&near!==BOARD?near:null):null));
addEventListener('keydown',e=>{ if(typing()||modKey(e)||e.repeat) return; if(CM.chromeReady&&CM.chromeOn&&CM.chromeOn()) return;   // the desktop's rail answers ] and [
  if(e.key==='['){ e.preventDefault(); dashShow(!document.getElementById('wrap').classList.contains('dash')); } else if(e.key===']'){ e.preventDefault(); sideShow(!sideOpen()); } });
{ let d=null, sd=null; try{ d=localStorage.getItem('dash'); sd=sessionStorage.getItem('side'); }catch(e){}
  dashShow(d==null?innerWidth>=1280:d==='on');
  sideShow(sd==null?agentTasks().some(t=>TASK_LIVE.includes(t.status))&&innerWidth>=1100:sd==='on'); }
if(!ZOOM_STORED){ ZOOM=fitZoom(VW,VH); HOME.on=true; homeNote(); }   // first load: the whole world, not the star alone, on a phone too (fit-all); the cards over it are read once the map is up (homeSettle). A stored zoom is never touched
HOME_LIVE=true; setInterval(()=>{ if(HOME.on&&HOME.laid&&Date.now()<HOME.until) homeSettle(); },300);
function folderOf(t){
  if(!t) return '';
  if(t.kind==='area'||t.kind==='dir') return String(t.path||'');
  if(t.kind==='task') return String(t.target||'');
  if(t.cwd) return String(t.cwd);
  const p=String(t.path||''); return p?p.replace(/[/][^/]*$/,''):'';
}
function fillModels(){
  const runners=W.task_runners||[], name=document.getElementById('crunner').value, r=runners.find(x=>x.name===name), ms=document.getElementById('cmodel');
  if(!ms) return; const models=(r&&r.models)||[];
  let last=''; try{ last=localStorage.getItem('taskModel:'+name)||''; }catch(e){}
  const pick=models.some(m=>m.name===last)?last:(r&&r.default_model)||(models[0]&&models[0].name)||'';
  ms.innerHTML=models.length?models.map(m=>'<option value="'+attr(m.name)+'"'+(m.name===pick?' selected':'')+'>'+esc(m.label||m.name)+(m.desc?' — '+esc(m.desc):'')+'</option>').join(''):'<option value="">its default</option>';
  ms.disabled=!models.length;
}
const SETUP_PH='e.g. npm ci';
// setup runs only in a thread's own worktree (tasks.py drops it elsewhere and says so): grey the field out instead
function syncSetup(){ const iso=document.getElementById('ciso'), su=document.getElementById('csetup'); if(!iso||!su) return;
  su.disabled=!iso.checked; su.placeholder=iso.checked?SETUP_PH:'only in its own worktree'; }
function openComposer(t){
  const C=document.getElementById('composer'), runners=(W.task_runners||[]).filter(r=>r&&r.role!=='check'), sel=document.getElementById('crunner');   // the agents that take a task (a check-only one is a second opinion, not a hand)
  let last=''; try{ last=localStorage.getItem('taskRunner')||''; }catch(e){}
  sel.innerHTML=runners.length?runners.map(r=>'<option value="'+attr(r.name)+'"'+(r.name===last?' selected':'')+'>'+esc(r.label||r.name)+(r.desc?' — '+esc(r.desc):'')+(r.detected?' (found on PATH)':'')+'</option>').join('')
    :'<option value="">Connect an agent first</option>';
  fillModels(); if(!sel.dataset.bound){ sel.dataset.bound='1'; sel.addEventListener('change',fillModels); }
  { const iso=document.getElementById('ciso'); if(iso&&!iso.dataset.bound){ iso.dataset.bound='1'; iso.addEventListener('change',syncSetup);
      const box=iso.closest('.chk'); if(box) box.addEventListener('click',e=>{ if(e.target!==iso&&!iso.disabled) iso.click(); }); }   // its words and the box round it toggle it too
    syncSetup(); }
  ['ciso','csetup','cverify','cretries'].forEach(id=>{ const f=document.getElementById(id), w=f&&f.closest&&f.closest('.cfield'); if(w) w.style.display=REMOTE?'none':''; });
  { const adv=document.getElementById('cadv'); if(adv) adv.style.display=REMOTE?'none':''; }   // the Advanced row holds only the fields a phone does not get
  { const fu=!t&&typeof CM.focusUid==='function'?unitByUid(CM.focusUid()):null, ou=!t&&!fu&&openUnit&&(openUnit.kind==='area'||openUnit.kind==='dir')?openUnit:null;   // no target named: the planet in focus or open, else the root
    if(!t&&(fu||ou)) t=fu||ou;
    document.getElementById('cfolder').value=folderOf(t)||String(W.root_path||''); }
  document.getElementById('cfolders').innerHTML=units.filter(u=>(u.kind==='area'||u.kind==='dir')&&u.path&&!u.remote).map(u=>'<option value="'+attr(u.path)+'">').join('');
  const msg=document.getElementById('cmsg'); msg.className='saym';
  msg.textContent=!LIVE?'This is a saved copy of the map: open the live map to start tasks.':'';
  { const cn=document.getElementById('cconnect'); if(cn) cn.hidden=!!runners.length; }   // no agent to send: the step that connects one (P32)
  document.getElementById('cgo').disabled=!LIVE||!runners.length;
  { const P=document.getElementById('panel'); P.classList.remove('on'); P.setAttribute('aria-hidden','true'); document.getElementById('wrap').classList.remove('panel-open'); if(openUnit&&openUnit!==BOARD&&openUnit.kind!=='task') openUnit=null; }
  const Q=document.getElementById('quests'); if(Q) Q.classList.remove('on');
  if(VIEW!=='map') setView('map');
  sideShow(true); C.classList.add('on'); document.getElementById('side').classList.add('compose'); aimAt(t); CM.taskCard&&CM.taskCard(); setTimeout(()=>document.getElementById('ctext').focus(),40);
}
let AIM=null;   // the body the new thread is aimed at (click the map while the composer is open)
// a long folder shows its END in the box (the folder's name), not the start every path shares; a focused box scrolls itself
function folderTail(){ const f=document.getElementById('cfolder'); if(f&&document.activeElement!==f){ try{ f.scrollLeft=f.scrollWidth; }catch(e){} } }
function aimAt(u){
  const chip=document.getElementById('ctarget'), f=document.getElementById('cfolder');
  document.getElementById('side').classList.remove('pick');   // a phone hides the rail while you pick on the map
  if(u&&(u.kind==='area'||u.kind==='dir'||u.kind==='task'||u.cwd||u.path)){ const path=folderOf(u); if(path){ f.value=path; } AIM=u.uid; }
  else if(!u){ AIM=null; }
  if(chip) chip.textContent=f.value?('in '+(shortPath(f.value)||f.value)):'';
  folderTail();
  if(u&&typeof mateSay==='function'&&MATE&&!MATE.thinking){ MATE.mute=Date.now()+6000; mateSay('Aimed at '+shortPath(folderOf(u)||'')+'. Say what to do there, then Start.',{instant:true}); }
}
document.getElementById('cfolder').addEventListener('blur',folderTail);
document.getElementById('cfolder').addEventListener('input',()=>{ const chip=document.getElementById('ctarget'); if(chip) chip.textContent=document.getElementById('cfolder').value?('in '+(shortPath(document.getElementById('cfolder').value)||document.getElementById('cfolder').value)):''; AIM=null; });
async function submitComposer(){
  const ta=document.getElementById('ctext'), text=ta.value.trim(), target=document.getElementById('cfolder').value.trim();
  const runner=document.getElementById('crunner').value, isolate=REMOTE?true:document.getElementById('ciso').checked, model=(document.getElementById('cmodel')||{}).value||'';
  const verify=REMOTE?'':((document.getElementById('cverify')||{}).value||'').trim(), retries=verify?Number((document.getElementById('cretries')||{}).value||0):0;
  const setup=REMOTE?'':((document.getElementById('csetup')||{}).value||'').trim();
  const msg=document.getElementById('cmsg'), go=document.getElementById('cgo');
  msg.className='saym err';
  if(!LIVE){ msg.textContent='static copy of the page: open the live page to start tasks'; return; }
  if(!text){ msg.textContent='say what the agent should do'; ta.focus(); return; }
  if(!target.startsWith('/')){ msg.textContent='pick a folder (an absolute path)'; return; }
  if(!runner){ msg.textContent='pick an agent'; return; }
  go.disabled=true; msg.className='saym'; msg.textContent='starting '+runner+'…';
  try{
    const body={text:text,target:target,runner:runner,isolate:isolate}; if(model) body.model=model; if(verify){ body.verify=verify; body.retries=retries; } if(setup&&isolate) body.setup=setup;
    const t=await api('/task',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    try{ localStorage.setItem('taskRunner',runner); if(model) localStorage.setItem('taskModel:'+runner,model); }catch(e){}
    ta.value=''; { const cv=document.getElementById('cverify'); if(cv) cv.value=''; const cs=document.getElementById('csetup'); if(cs) cs.value=''; } document.getElementById('composer').classList.remove('on'); document.getElementById('side').classList.remove('compose'); AIM=null;
    LAUNCHES[t.id]=performance.now();
    if(!agentTasks().some(x=>x.id===t.id)) W.agent_tasks=[t].concat(agentTasks());   // shown at once, whatever a poll returns
    await pollTasks();
    openTask(t.id);
    toast(agentLine(runner,t.model)+' is on it · '+(t.mode==='worktree'?'its own worktree on '+t.branch:'working in place'),4000);
    if(typeof mateSay==='function') mateSay(agentLine(runner,t.model)+' is on it in '+shortPath(t.target)+(t.mode==='worktree'?', in its own worktree. ':'. ')+'Watch the panel; I will tell you when it needs you.');
  }catch(e){ msg.className='saym err'; msg.textContent=e.message; if(!sessionLost(e)&&typeof mateSay==='function') mateSay('That did not start: '+e.message); }
  finally{ go.disabled=false; }
}
// ---- the task list (K) ----
function verifyChip(t){ const v=t&&t.verify; if(!t||!t.verify_cmd) return ''; const ok=v&&v.rc===0, bad=v&&v.rc!=null&&v.rc!==0; return ' <span style="color:'+(ok?'#3dff9e':bad?'#ff4d5e':'#9aa7bd')+'">'+(ok?'✓ verified':bad?'✗ unverified':'verify pending')+'</span>'; }
function verdictChip(t){ const c=t&&t.check; if(!c||!c.verdict) return ''; const v=String(c.verdict); const col=c.stale?'#9aa7bd':v==='PASS'?'#3dff9e':v==='FAIL'?'#ff4d5e':v==='checking'?'#ffd34d':v==='stopping'?'#ff8a4c':'#9aa7bd';
  // the outcome first, the model only in brackets and without its effort setting: "second opinion (Codex (OpenAI)): found problems"
  const who=c.runner?String(agentLine(c.runner,c.model)).split(' · ')[0]:'';
  return ' <span style="color:'+col+'">'+esc((c.stale?'outdated ':'')+(v==='PASS'?'✓ ':v==='FAIL'?'✗ ':'')+CM.opinionLine(v,who))+'</span>'; }
// ---- the vector look's mission card (VEC): a thread as an operation. A solid banner by state (red with a hazard
// triangle when it failed or waits on your review, teal while an agent works, green once merged), OPERATION <TITLE>,
// the runner under it; the target and VERIFY / CHECK / ATTEMPTS / EVIDENCE from the thread's own record (verified and
// checked = CONFIRMED, one of them = UNCONFIRMED, neither = NO EVIDENCE); one bar: the real merge control while it can
// land (the footer's own action, data-tact), the first blocker in red while it cannot. The list row is the short form.
function tacBand(s){ return ['failed','review','lost','error'].includes(s)?'red':(TASK_WORKING.includes(s)||s==='new')?'run':(s==='merged'||s==='done')?'won':'idle'; }
function tacEvidence(t){ const e=String(t.evidence||'none'); return e==='verified and checked'?['CONFIRMED','ok']:(e==='verified'||e==='checked')?['UNCONFIRMED','warn']:['NO EVIDENCE','bad']; }
function tacVerify(t){ if(!t.verify_cmd) return ['NONE SET','']; const v=t.verify; return v&&v.rc===0?['PASSED','ok']:v&&v.rc!=null?['FAILED · EXIT '+v.rc,'bad']:['PENDING','warn']; }
function tacCheck(t){ const c=t.check, v=c&&c.verdict?String(c.verdict):''; if(!v) return ['NONE','']; const w=v==='PASS'?'PASSED':v==='FAIL'?'FAILED':v.toUpperCase();
  return [(c.stale?'OUTDATED · ':'')+w+(c.runner?' · '+agentLine(c.runner,c.model).toUpperCase():''),c.stale?'warn':v==='PASS'?'ok':v==='FAIL'?'bad':'warn']; }
function tacMission(t,full){
  t=t||{}; const blk=t.merge_blockers||[], open=TASK_OPEN.includes(t.status), ev=tacEvidence(t), where=shortPath(t.target)||'—', into=String(t.base_branch||'?').toUpperCase();
  const when=t.ended?taskAge(t.ended)+(t.rc!=null?' · exit '+t.rc:''):(t.started?'started '+taskAge(t.started):'');
  let h='<div class="mcb"><i class="mhz" aria-hidden="true"></i><b>OPERATION '+esc(taskTitle(t).toUpperCase())+'</b><small>'+esc(agentLine(t.runner,t.model))+' · '+esc(taskWord(t))+(full&&when?' · '+esc(when):'')+'</small></div><div class="mcd">';
  if(full){ const rows=[['VERIFY',tacVerify(t)],['CHECK',tacCheck(t)],['ATTEMPTS',[String(t.attempts||0)+(t.retries?' OF '+(1+t.retries):''),'']],['EVIDENCE',ev]];
    if(t.setup) rows.push(['SETUP',t.setup.rc===0?['PASSED','ok']:['FAILED · EXIT '+t.setup.rc,'bad']]);
    h+='<div class="mtgt"><span>TARGET</span><a data-tact="fly" title="'+attr(t.target||'')+'">'+esc(where)+
      '<small>'+esc(t.mode==='worktree'?'⎇ '+(t.branch||'')+' ← '+(t.base_branch||'HEAD'):'in place'+(t.repo?'':' · not a repo'))+'</small></a></div><dl class="mtab">'+
      rows.map(r=>'<dt>'+r[0]+'</dt><dd'+(r[1][1]?' class="'+r[1][1]+'"':'')+'>'+esc(r[1][0])+'</dd>').join('')+'</dl>'; }
  else h+='<span class="mtg">'+esc(where)+'</span><span class="'+ev[1]+'">'+esc(ev[0])+'</span><span>'+esc(taskAge(t.ended||t.started||t.created))+'</span>';
  h+='</div>';
  if(open&&t.mode==='worktree'&&blk.length) h+='<div class="mcbar bad">✗ '+esc(blk[0])+(blk.length>1?' · +'+(blk.length-1)+' more':'')+'</div>';
  else if(open&&!(REMOTE&&blk.length)){ const word=t.mode==='worktree'?'MERGE INTO '+esc(into):'MARK DONE';
    h+=full?'<button type="button" class="mcbar go" data-tact="'+(t.mode==='worktree'?'merge':'done')+'">'+word+'</button>':'<div class="mcbar go">READY · '+word+'</div>'; }
  else if(full&&TASK_WORKING.includes(t.status)) h+='<div class="mcbar run">'+esc(taskWord(t).toUpperCase())+(t.started?' · STARTED '+esc(taskAge(t.started).toUpperCase()):'')+'</div>';
  else if(full&&t.status==='merged') h+='<div class="mcbar won">MERGED INTO '+esc(String((t.closed_note&&t.closed_note.into)||t.base_branch||'?').toUpperCase())+'</div>';
  return h;
}
// the open thread's card follows every refresh of its record (the panel may have opened before the look changed)
function tacMissionMeta(j){
  let mc=document.getElementById('tvmission');
  if(!mc){ const hd=document.querySelector('#tbody .tvh'); if(!hd) return; mc=document.createElement('div'); mc.id='tvmission'; hd.insertBefore(mc,hd.firstChild); }
  const cls='mc st-'+tacBand(j.status), h=tacMission(j,true);
  if(mc.className!==cls) mc.className=cls;
  if(TAC_MEMO.mission!==h){ TAC_MEMO.mission=h; mc.innerHTML=h; }
}
function taskListRow(t){
  if(VEC) return '<div class="trow mc st-'+tacBand(t.status)+(openTaskId===t.id?' sel':'')+'" data-tid="'+attr(t.id)+'">'+tacMission(t,false)+'</div>';
  const col=CM.taskCol(t.status);
  // the title as a person says it and one state word in its state's colour (P44, as sideRow's); the model shows only in
  // the second opinion's chip (P16)
  return '<div class="trow'+(openTaskId===t.id?' sel':'')+'" data-tid="'+attr(t.id)+'"><i style="background:'+col+'"></i><div class="tb"><b>'+esc(CM.shortTitle(t.title,60)||'a task')+'</b>'+
    '<small><span class="sw" style="color:'+col+'">'+esc(taskWord(t))+'</span> · '+esc(shortPath(t.target).split('/').pop()||shortPath(t.target))+' · '+esc(taskAge(t.ended||t.started||t.created))+verifyChip(t)+verdictChip(t)+'</small></div></div>';
}
// a row of the rail that is not a task: an agent at work, a workflow's step, a contact (a T3 thread that replied, a
// workflow waiting on you): the words, one state word in its colour, where and when
function sideRow(data,col,title,word,rest){ return '<div class="trow" '+data+'><i style="background:'+col+'"></i><div class="tb"><b>'+esc(title)+'</b>'+
  '<small><span class="sw" style="color:'+col+'">'+esc(word)+'</span>'+(rest?' · '+esc(rest):'')+'</small></div></div>'; }
function workingRowHtml(r){
  if(r.kind==='task') return taskListRow(r.t);
  if(r.kind==='wf'){ const st=r.step||{}, w=r.w||{}; return sideRow('data-wf="'+attr(w.id||'')+'"','#6fb7ff',CM.shortTitle(w.title||'',60)||'a workflow','working',
    [String(st.label||''),String(st.place||(st.planet_uid?CM.labelText(st.planet_uid):''))].filter(Boolean).join(' · ')); }
  const u=r.u||{}; return sideRow('data-uid="'+attr(u.uid||'')+'"','#6fb7ff',String(u.alias||u.name||'an agent'),'working',
    [u.cwd?shortPath(u.cwd).split('/').pop():'',u.t3&&u.t3.since?CM.agoOf(u.t3.since):''].filter(Boolean).join(' · ')); }
function contactRowHtml(c){
  const t=c.source==='task'?agentTasks().find(x=>String(x.id)===idTail(c.id)):null; if(t) return taskListRow(t);
  const b=CM.bucket(c), col=CM.STATE_COL[b==='answer'||b==='review'?'needs':b==='problem'?'broken':'waiting'];
  const where=c.planet_uid?CM.labelText(c.planet_uid):'';
  return sideRow('data-cid="'+attr(c.id)+'"',col,CM.shortTitle(c.title||c.voice||'',60)||CM.contactWord(c),CM.contactWord(c),
    [where&&where!==c.planet_uid?where:'',CM.daysAgo(c.age_days)].filter(Boolean).join(' · ')); }   // "8 days ago", as a task row says it
// The rail lists what the counts count, so its numbers are the status bar's: WORKING every agent at work
// (workingRows), TO REVIEW every review contact (reviewContacts), NEEDS YOUR ANSWER every answer contact; a failed task
// and the closed ones follow. Each number that is one of W.counts' is marked data-count. Without a sky to read, the
// tasks alone, as before.
function renderTaskList(){
  const el=document.getElementById('tasks'), T=agentTasks(), rc=reviewContacts();
  const work=workingRows(), shut=T.filter(t=>!TASK_LIVE.includes(t.status)).slice(0,20);
  const head=(title,n,noun)=>'<h3>'+esc(title)+' · '+(noun?'<span data-count="'+attr(noun)+'">'+n+'</span>':n)+'</h3>';
  const grp=(title,rows,empty,noun)=>head(title,rows.length,noun)+(rows.length?rows.join(''):'<div class="mut">'+esc(empty)+'</div>');
  let h=(LIVE?'':'<div class="mut">static copy: the list is from the last render</div>')+
    grp('WORKING',work.map(workingRowHtml),'No agent is working','working');
  if(rc){ const ans=skyContacts().filter(c=>CM.bucket(c)==='answer').sort((a,b)=>(Number(b.age_days)||0)-(Number(a.age_days)||0));
    const bad=T.filter(t=>t.status==='failed'&&!t.retry_due||t.status==='lost');
    if(ans.length) h+=grp('NEEDS YOUR ANSWER',ans.map(contactRowHtml),'','answer');
    h+=grp('TO REVIEW',rc.map(contactRowHtml),'Nothing back yet','review');
    if(bad.length) h+=grp('FAILED',bad.map(taskListRow),''); }
  else h+=grp('BACK FROM THE AGENT',T.filter(t=>TASK_OPEN.includes(t.status)).map(taskListRow),'Nothing back yet');
  h+=grp('CLOSED',shut.map(taskListRow),'None yet');
  // which agents a task can go to (the composer's list): said only when there is one
  const rs=(W.task_runners||[]).map(r=>r.label||r.name).filter(Boolean);
  if(rs.length) h+='<div class="mut">agents for tasks: '+esc(rs.join(' · '))+'</div>';
  el.innerHTML=h;
}
function toggleTasks(force){
  if(CM.chromeReady&&CM.chromeOn&&CM.chromeOn()&&force!==false){ sideShow(false); if(VIEW!=='map') setView('map'); CM.railTab('agents'); pollTasks(); return; }   // the rail's Agents tab; an open composer closes
  const on=force==null?!sideOpen():!!force;
  sideShow(on); if(on){ if(VIEW!=='map') setView('map'); renderTaskList(); pollTasks(); }
}
// ---- one task in the side panel: the conversation, the live log, the diff, the actions ----
function stopTaskPoll(){ if(TV&&TV.timer) clearInterval(TV.timer); TASK_EPOCH++; TV=null; }
function openTask(id,run){
  const t=agentTasks().find(x=>x.id===id);
  const input=document.getElementById('sayi'); if(openUnit&&input){ drafts[openUnit.uid]=input.value; saveDrafts(); }
  stopPane(); stopTaskPoll();
  openTaskId=id; openUnit={uid:'task:'+id,kind:'task',name:t?taskTitle(t):'a task',task_id:id};
  { const P=document.getElementById('panel'); P.classList.remove('on'); P.setAttribute('aria-hidden','true'); document.getElementById('wrap').classList.remove('panel-open'); }
  if(VIEW!=='map') setView('map'); sideShow(true);
  if(t&&typeof mateExplain==='function') mateExplain({uid:'task:'+id,kind:'task',task_id:id,name:taskTitle(t),status:t.status,runner:t.runner,model:t.model,target:t.target},true);
  document.getElementById('side').style.setProperty('--accent',TASK_COL[t&&t.status]||'#7ec8ff');
  { const C=document.getElementById('composer'); C.classList.remove('on'); document.getElementById('side').classList.remove('compose'); }
  if(VEC) TAC_MEMO.mission=null;
  document.getElementById('tbody').innerHTML='<div class="tv"><div class="tvh">'+(VEC?'<div class="mc st-'+tacBand(t&&t.status)+'" id="tvmission">'+tacMission(t||{id:id,title:id},true)+'</div>':'')+'<span class="pill" id="tvstatus">thread</span><div class="tvt" id="ttitle">'+esc(t?(CM.shortTitle(taskTitle(t),140,true)||taskTitle(t)):id)+'</div><div class="tvm" id="tvmeta"></div><div class="tvnote" id="tvnote" hidden></div>'+
    '<div class="tvtabs"><button type="button" data-ttab="chat" class="on">Conversation</button><button type="button" data-ttab="diff">Changes<span id="tvdiffn"></span></button><span class="grow"></span><button type="button" data-tact="fly" title="fly to its folder">⌖ map</button></div></div>'+
    '<div class="tvpane" data-tpane="chat"><div id="truns"></div><div class="tv-chat" id="tlog" tabindex="0"><div class="tv-div tl-ph">loading…</div></div></div>'+
    '<div class="tvpane" data-tpane="diff" hidden><div id="tdiff"></div></div><div class="tvfoot" id="tact"></div></div>';
  renderTaskList(); CM.taskCard&&CM.taskCard();   // a desktop shows the task as the rail's card
  TV={id:id,run:run||0,shown:null,offset:0,q:[],cps:[],total:0,lo:0,cap:LOG_KEEP,status:null,epoch:++TASK_EPOCH,timer:null,follow:true,busy:false,acts:'',cur:null,counts:null,user:null,diffLoaded:false,tab:'chat'};
  const ep=TV.epoch; loadTask(ep); if(TV&&TV.epoch===ep) TV.timer=setInterval(()=>loadTask(ep),1500);   // a static page stops the panel in loadTask: nothing to poll
}
const TLINE_KINDS=new Set(['text','tool','cmd','file','err','result','info','think','meta','raw']);
// The transcript: the agent's words are messages, its tool calls fold into one activity row per
// burst ("read 4 · ran 2 · edited 3"), errors stand out, run boundaries are thin dividers.
function tvActsLabel(c){ const b=[]; if(c.tool) b.push((c.tool===1?'1 tool call':c.tool+' tool calls')); if(c.cmd) b.push('ran '+c.cmd+(c.cmd===1?' command':' commands')); if(c.file) b.push('edited '+c.file+(c.file===1?' file':' files')); if(c.think) b.push('thought '+c.think+'×'); if(c.raw) b.push(c.raw+' output line'+(c.raw===1?'':'s')); return b.join(' · ')||'activity'; }
function tvAppend(log,l){
  const k=TLINE_KINDS.has(l.k)?l.k:'raw'; let s=String(l.s||'');
  if(TV&&TV.cwd&&k!=='meta') s=s.split(TV.cwd+'/').join('').split(TV.cwd).join('.');
  const close=()=>{ TV.cur=null; TV.counts=null; };
  if(k==='raw'&&s.startsWith('# setup · ')) return null;   // the setup receipt gets its own row (loadTask)
  if(k==='text'){
    if(!TV.cur||TV.cur.kind!=='text'){ const el=document.createElement('div'); el.className='tv-msg '+(TV.runKind==='check'?'review':'agent'); el.innerHTML='<div class="who">'+esc(TV.runKind==='check'?'reviewer':'agent')+'</div>'; log.appendChild(el); TV.cur={kind:'text',el:el}; }
    const p=document.createElement('p'); p.textContent=s; TV.cur.el.appendChild(p); return p;
  }
  if(k==='err'){ const el=document.createElement('div'); el.className='tv-msg err'; el.textContent='✖ '+s; log.appendChild(el); close(); return el; }
  if(k==='result'){ const el=document.createElement('div'); el.className='tv-div ok'; el.textContent='✔ '+s; log.appendChild(el); close(); return el; }
  if(k==='meta'||k==='info'){ const el=document.createElement('div'); el.className='tv-div'; el.textContent=s.replace(/^run \\d+ · /,''); log.appendChild(el); close(); return el; }
  if(!TV.cur||TV.cur.kind!=='acts'){ const d=document.createElement('details'); d.className='tv-acts'; d.innerHTML='<summary><span class="tvl"></span></summary><div class="tv-al"></div>'; log.appendChild(d); TV.cur={kind:'acts',el:d}; TV.counts={tool:0,cmd:0,file:0,think:0,raw:0}; }
  TV.counts[k]=(TV.counts[k]||0)+1;
  const lead={tool:'▸ ',cmd:'',file:'✎ ',think:'… ',raw:''}[k]||'';
  const row=document.createElement('div'); row.className='tl-'+k; row.textContent=lead+s; TV.cur.el.querySelector('.tv-al').appendChild(row);
  TV.cur.el.querySelector('.tvl').textContent=tvActsLabel(TV.counts);
  return row;
}
// A transcript is capped: the newest LOG_KEEP entries are drawn, older ones are dropped from the page and
// come back LOG_PAGE at a time ("show earlier"), re-read from the log by byte offset. TV.cps keeps one
// {off,n} per 64 KB read (the byte where a read began and how many entries came before it), so an earlier
// page is read from the nearest mark, not from the start. TV.total counts entries read, TV.lo is the first
// one still drawn, TV.q the drawn line nodes as [entry, node].
const LOG_KEEP=3000, LOG_PAGE=3000, LOG_CATCHUP=40;
function tvFeed(log,lines,since){
  if(!TV.cps.length||since-TV.cps[TV.cps.length-1].off>=65536) TV.cps.push({off:since,n:TV.total});
  const skip=Math.max(0,lines.length-TV.cap);    // read but never drawn: the cap would drop them at once
  TV.total+=skip;
  for(let i=skip;i<lines.length;i++){ const node=tvAppend(log,lines[i]); if(node) TV.q.push([TV.total,node]); TV.total++; }
  tvTrim(log);
}
function tvDrop(node){
  const par=node.parentNode; node.remove(); if(!par||par.id==='tlog') return;
  const box=par.classList.contains('tv-al')?par.parentNode:par;
  if(box&&box.parentNode&&!(TV.cur&&box===TV.cur.el)&&!box.querySelector('p,.tv-al>div')) box.remove();
}
function tvTrim(log){
  const over=TV.total-TV.lo-TV.cap; if(over>0) TV.lo+=over;
  while(TV.q.length&&TV.q[0][0]<TV.lo) tvDrop(TV.q.shift()[1]);
  tvMore(log);
}
function tvMore(log){
  let el=log.querySelector('.tl-more');
  if(TV.lo<=0){ if(el) el.remove(); return; }
  if(!el){ el=document.createElement('div'); el.className='tv-div tl-more'; const you=log.querySelector('.tv-msg.you'); if(you&&you.parentNode===log) you.insertAdjacentElement('afterend',el); else log.prepend(el); }
  const txt=TV.lo+' earlier line'+(TV.lo===1?'':'s')+' not shown';
  if(el.dataset.n!==txt){ el.dataset.n=txt; el.innerHTML='<button type="button" data-tmore="1">show earlier</button><span>'+esc(txt)+'</span>'; }
}
async function tvEarlier(){
  const tv=TV, log=document.getElementById('tlog'); if(!tv||!log||tv.busy||tv.lo<=0||!LIVE) return;
  tv.busy=true; const ep=tv.epoch, btn=log.querySelector('[data-tmore]'); if(btn) btn.disabled=true;
  try{
    const want=Math.max(0,tv.lo-LOG_PAGE); let cp=tv.cps[0]||{off:0,n:0};
    for(const c of tv.cps){ if(c.n<=want) cp=c; else break; }
    let off=cp.off, n=cp.n; const got=[];
    for(let guard=0;n<tv.lo&&guard<400;guard++){
      const j=await api('/task?id='+encodeURIComponent(tv.id)+'&run='+tv.shown+'&since='+off);
      if(TV!==tv||ep!==tv.epoch) return;
      for(const l of (j.lines||[])){ if(n>=tv.lo) break; if(n>=want) got.push(l); n++; }
      if(!j.more||j.offset<=off) break;
      off=j.offset;
    }
    const keep=[TV.cur,TV.counts]; TV.cur=null; TV.counts=null;
    const box=document.createElement('div'), nodes=[];
    got.forEach((l,i)=>{ const node=tvAppend(box,l); if(node) nodes.push([want+i,node]); });
    TV.cur=keep[0]; TV.counts=keep[1];
    let top=tv.q.length?tv.q[0][1]:null; while(top&&top.parentNode!==log) top=top.parentNode;
    const more=log.querySelector('.tl-more'), pane=log.parentElement, h0=pane.scrollHeight, t0=pane.scrollTop;
    let anchor=more;
    while(box.firstChild){ const c=box.firstChild; if(top) log.insertBefore(c,top); else if(anchor){ anchor.insertAdjacentElement('afterend',c); anchor=c; } else log.appendChild(c); }
    tv.q=nodes.concat(tv.q); tv.lo=want; tv.cap=(tv.total-want)+500;
    tvMore(log); pane.scrollTop=t0+(pane.scrollHeight-h0);
  }catch(e){ if(log&&TV===tv) log.insertAdjacentHTML('beforeend','<div class="tl-err">'+esc(e.message)+'</div>'); }
  finally{ if(TV===tv) tv.busy=false; const b=log.querySelector('[data-tmore]'); if(b) b.disabled=false; }
}
function tvRunStart(log,j){
  TV.cur=null; TV.counts=null; log.innerHTML=''; TV.q=[]; TV.cps=[]; TV.total=0; TV.lo=0; TV.cap=LOG_KEEP;
  const p=(j.prompts||[]).find(x=>x.n===j.run); TV.runKind=p&&p.kind==='check'?'check':'task';
  if(p){ const el=document.createElement('div'); el.className='tv-msg you'; el.innerHTML='<div class="who">'+esc(p.kind==='check'?'you asked '+agentLine(p.runner,p.model)+' to check':p.auto?'the oracle · auto-retry':(p.followup?'you · reply':'you'))+(p.at?' · '+esc(taskAge(p.at)):'')+'</div>'+esc(p.text); log.appendChild(el); }
  TV.verifyShown=false; TV.setupShown=false;
}
async function loadTask(ep){
  if(!TV||ep!==TV.epoch||TV.busy) return;
  const log=document.getElementById('tlog'); if(!log){ stopTaskPoll(); return; }
  if(!LIVE){
    const t=agentTasks().find(x=>x.id===TV.id);
    log.innerHTML='<div class="tl-info">static copy of the page: the live log needs the live page</div>';
    if(t) renderTaskMeta(Object.assign({prompts:[{n:1,text:t.text||t.title}]},t));
    stopTaskPoll(); return;
  }
  TV.busy=true;
  try{
    for(let guard=0;guard<LOG_CATCHUP;guard++){
      const since=TV.offset;
      const j=await api('/task?id='+encodeURIComponent(TV.id)+(TV.run?'&run='+TV.run:'')+'&since='+since);
      if(!TV||ep!==TV.epoch||j.id!==TV.id) return;
      if(j.cwd) TV.cwd=String(j.cwd);
      if(TV.shown!==j.run){ if(TV.shown!=null&&TV.offset){ TV.offset=0; TV.shown=j.run; tvRunStart(log,j); continue; } TV.shown=j.run; tvRunStart(log,j); }
      const pane=log.parentElement, bottom=pane.scrollHeight-pane.scrollTop-pane.clientHeight<60;
      if(j.lines&&j.lines.length){ const ph=log.querySelector('.tl-ph'); if(ph) ph.remove(); tvFeed(log,j.lines,since); }
      if(j.verify_tail&&!TV.verifyShown&&!j.more&&j.status!=='running'){ TV.verifyShown=true; const ok=/\\$ exit 0\\s*$/.test(j.verify_tail.trim()); const el=document.createElement('div'); el.className='tv-msg '+(ok?'review':'err');
        el.innerHTML='<div class="who">'+(ok?'✓ verify passed':'✗ verify failed')+'</div>'+esc(j.verify_tail.trim().slice(-1200)); log.appendChild(el); TV.cur=null; }
      if(j.setup_tail&&!TV.setupShown&&j.setup&&j.setup.n===j.run){ TV.setupShown=true; const sv=j.setup, d=document.createElement('details'); d.className='tv-acts';
        d.innerHTML='<summary><span class="tvl" style="color:'+(sv.rc===0?'#3dff9e':'#ff8a8a')+'">'+esc((sv.rc===0?'✓ setup':'✗ setup failed · exit '+sv.rc)+(sv.seconds!=null?' · '+sv.seconds+'s':'')+(j.setup_cmd?' · '+j.setup_cmd:''))+'</span></summary><div class="tv-al"><div class="tl-raw">'+esc(j.setup_tail.trim().slice(-1200))+'</div></div>';
        const you=log.querySelector('.tv-msg.you'); if(you) you.insertAdjacentElement('afterend',d); else log.prepend(d); }
      { let ph=log.querySelector('.tl-ph'); const live=j.status==='running'||j.status==='checking';
        if(!log.querySelector('.tv-msg.agent,.tv-msg.review,.tv-acts,.tv-msg.err,.tv-div.ok')){ if(!ph){ ph=document.createElement('div'); ph.className='tv-div tl-ph'; log.appendChild(ph); } ph.innerHTML=live?'<span class="tv-live"></span>waiting for the agent’s first words…':'(no output)'; }
        else if(live||j.more){ if(!ph){ ph=document.createElement('div'); ph.className='tv-div tl-ph'; } ph.innerHTML=j.more?'<span class="tv-live"></span>reading the log · '+Math.round(j.offset/1048576)+' of '+Math.round(j.size/1048576)+' MB':'<span class="tv-live"></span>working…'; log.appendChild(ph); }
        else if(ph) ph.remove(); }
      if(TV.follow&&bottom) pane.scrollTop=pane.scrollHeight;
      TV.offset=j.offset;
      renderTaskMeta(j);
      TV.status=j.status; TV.runs=j.runs;
      if(!j.more){
        if(j.retry_due){ if(!TV.retryUntil) TV.retryUntil=Date.now()+90000; } else TV.retryUntil=0;
        const watching=TASK_WORKING.includes(j.status)||(j.retry_due&&Date.now()<TV.retryUntil);   // a due retry starts within seconds
        if(!watching&&TV.timer){ clearInterval(TV.timer); TV.timer=null; }
        break; }
    }
  }catch(e){ if(log&&TV&&ep===TV.epoch) log.insertAdjacentHTML('beforeend','<div class="tl-err">'+esc(e.message)+'</div>'); if(sessionLost(e)) return; }
  finally{ if(TV&&ep===TV.epoch) TV.busy=false; }
}
function renderTaskMeta(j){
  const col=TASK_COL[j.status]||'#9aa7bd';
  if(VEC) tacMissionMeta(j);
  document.getElementById('side').style.setProperty('--accent',col);
  const st=document.getElementById('tvstatus'); if(st){ st.textContent=taskWord(j); st.style.color=col; }
  const nt=document.getElementById('tvnote'); if(nt){ nt.textContent=j.note||''; nt.hidden=!j.note; }
  const meta=document.getElementById('tvmeta');
  if(meta){ const vb=j.check&&j.check.verdict?j.check.verdict:null;
    // one plain line on top (who, where, when), the second opinion and the test in words; the branch, the mode, exit codes and the
    // evidence word under Details
    const plain=VEC?'':'<div class="tvplain">'+esc([agentLine(j.runner,j.model).split(' · ')[0],j.target?'in '+(chromePlaceOf(j.target)||shortPath(j.target)):'',j.ended?'finished '+taskAge(j.ended):(j.started?'started '+taskAge(j.started):'')].filter(Boolean).join(' · '))+'</div>'+
      (vb&&!j.check.stale?'<div class="tvop '+(vb==='PASS'?'ok':vb==='FAIL'?'bad':'')+'">'+esc(CM.opinionLine(vb,String(agentLine(j.check.runner,j.check.model)).split(' · ')[0]))+'</div>':'')+
      (j.verify_cmd?'<div class="tvop '+(j.verify&&j.verify.rc===0?'ok':j.verify&&j.verify.rc!=null?'bad':'')+'">'+esc((j.verify&&j.verify.rc===0?'The test passes':j.verify&&j.verify.rc!=null?'The test fails':'The test has not run yet')+(j.retries?' · attempt '+(j.attempts||1)+' of '+(1+j.retries):''))+'</div>':'');
    meta.innerHTML=(VEC?'':plain+'<details class="tvdet"><summary>Details</summary><div class="tvchips">')+'<span class="chip2" title="agent and model">'+esc(agentLine(j.runner,j.model))+'</span>'+
      '<a class="chip2" data-tact="fly" title="'+attr(j.target||'')+'">📁 '+esc(shortPath(j.target))+'</a>'+
      (j.mode==='worktree'?'<span class="chip2" title="its own worktree, cut from '+attr(j.base_branch||'HEAD')+'">⎇ '+esc(j.branch||'')+' ← '+esc(j.base_branch||'HEAD')+'</span>':'<span class="chip2">in place'+(j.repo?'':' · not a repo')+'</span>')+
      '<span class="chip2">'+esc(j.ended?taskAge(j.ended)+(j.rc!=null?' · exit '+j.rc:''):(j.started?'started '+taskAge(j.started):''))+'</span>'+
      (vb?'<span class="chip2 '+(j.check.stale?'':vb==='PASS'?'ok':vb==='FAIL'?'bad':'warn')+'" title="'+(j.check.stale?'the work changed after this check':'')+'">'+esc((j.check.stale?'outdated check: ':'')+(vb==='PASS'?'✓ check passed':vb==='FAIL'?'✗ check failed':vb))+' · '+esc(agentLine(j.check.runner,j.check.model))+'</span>':'')+
      (j.verify_cmd?'<span class="chip2 '+(j.verify&&j.verify.rc===0?'ok':j.verify&&j.verify.rc!=null?'bad':'')+'" title="'+attr(j.verify_cmd)+'">'+esc(j.verify&&j.verify.rc===0?'✓ verify passed':j.verify&&j.verify.rc!=null?'✗ verify failed · exit '+j.verify.rc:'verify pending')+(j.retries?' · attempt '+esc(String(j.attempts||1))+' of '+esc(String(1+j.retries)):'')+'</span>':'')+
      (j.setup?'<span class="chip2 '+(j.setup.rc===0?'ok':'bad')+'" title="'+attr(j.setup_cmd||'')+'">'+esc(j.setup.rc===0?'✓ setup':'✗ setup · exit '+j.setup.rc)+'</span>':'')+
      (j.evidence&&j.mode==='worktree'?'<span class="chip2 '+(j.evidence==='none'?'warn':j.evidence==='verified and checked'?'ok':'')+'" title="verify passing is not proof of correctness; a check by another agent is the independent evidence">evidence: '+esc(j.evidence)+'</span>':'')+(VEC?'':'</div></details>'); }
  const runs=document.getElementById('truns'), prompts=j.prompts||[];
  if(runs) runs.innerHTML=prompts.length>1?prompts.map(p=>'<button type="button" data-trun="'+attr(String(p.n))+'"'+(p.n===j.run?' class="on"':'')+'>'+
    esc(p.kind==='check'?'check · '+agentLine(p.runner,p.model):(p.n===1?'task':p.auto?'auto-retry':'reply'))+(p.rc!=null?(p.rc===0?' ✓':' ✗'):(p.n===j.runs&&(j.status==='running'||j.status==='checking')?' ●':''))+'</button>').join(''):'';
  const blk=j.merge_blockers||[];
  const key=[j.status,j.mode,j.runs,j.base_branch,JSON.stringify(j.closed_note||null),JSON.stringify(j.check||null),JSON.stringify(blk)].join('|');
  if(TV&&TV.acts===key) return;
  if(TV){ const moved=TV.acts&&TV.acts.split('|').slice(0,3).join('|')!==[j.status,j.mode,j.runs].join('|'); TV.acts=key; TV.blockers=blk; TV.mergeArmed=false; if(moved){ TV.diffLoaded=false; if(TV.tab==='diff') loadDiff(); } }
  const act=document.getElementById('tact'); if(!act) return;
  let h='<div class="tbtns">';
  const open=['review','failed','stopped','lost'].includes(j.status);
  const landable=!(REMOTE&&blk.length);   // a phone lands a thread only when nothing is against it
  if(j.status==='running'||j.status==='checking') h+='<button type="button" class="warn" data-tact="stop">'+(VEC?'■ stop ':'Stop ')+(j.status==='checking'?'the check':'')+'</button>';
  if(j.status==='stopping') h+='<span class="tclosed">stopping…</span>';
  if(open&&landable){
    if(j.mode==='worktree') h+='<button type="button" class="'+(blk.length?'warn':'ok')+'" data-tact="merge"'+(blk.length?' title="the evidence says not yet: click twice to accept anyway"':' title="its changes go into '+attr(j.base_branch||'?')+'"')+'>'+(VEC?'Merge into '+esc(j.base_branch||'?'):'Accept changes')+'</button><button type="button" data-tact="discard">'+(VEC?'Set aside':'Reject (copy kept)')+'</button>';
    else h+='<button type="button" class="ok" data-tact="done">Mark done</button>';
    if(!REMOTE){ const rs=(W.task_runners||[]).filter(r=>r.role!=='work').sort((a,b)=>(a.name===j.runner)-(b.name===j.runner)||((b.role==='check')-(a.role==='check')));
      if(rs.length) h+='<select id="tcheckr" title="who gives the second opinion">'+rs.map(r=>'<option value="'+attr(r.name)+'">'+esc(agentLine(r.name,r.default_model))+(r.name===j.runner?' (same agent)':r.role==='check'?' (reviewer)':'')+'</option>').join('')+'</select><button type="button" data-tact="check">'+(VEC?'Check it':'Ask a second opinion')+'</button>'; }
  }
  if(['merged','discarded','done'].includes(j.status)){
    const c=j.closed_note||{};
    h+='<span class="tclosed">'+esc(VEC?(j.status==='merged'?'merged into '+(c.into||'?')+' at '+String(c.sha||'').slice(0,10):j.status==='discarded'?'set aside · the work stays on branch '+(c.branch||j.branch||''):'closed'+(c.note?' · '+c.note:''))
      :(j.status==='merged'?'Accepted: the changes are in '+(c.into||j.base_branch||'the main copy'):j.status==='discarded'?'Rejected: the copy is kept':'Closed'+(c.note?' · '+c.note:'')))+'</span>';
  }
  if(W.opener&&DESK) h+='<button type="button" data-tact="open">Open folder</button>';
  h+='</div>';
  if(open&&j.mode==='worktree'&&blk.length) h+='<div class="tblockers">'+blk.map(b=>'<div>✗ '+esc(b)+'</div>').join('')+(REMOTE?'<div>accept or reject it on your computer</div>':'')+'</div>';
  if(open) h+='<div class="tfollow"><form id="tfollowf"><textarea id="tfollowi" maxlength="8000" placeholder="Reply to '+attr(runnerLabel(j.runner))+'… what to change or do next (Ctrl+Enter)"></textarea><button type="submit">Send</button></form></div>';
  h+='<div class="saym" id="tmsg"></div>';
  act.innerHTML=h;
}
const TASK_ACTS=new Set(['diff','map','open','stop','merge','discard','done','check','fly']);
function tvTab(name){
  if(!TV) return; TV.tab=name;
  document.querySelectorAll('#tbody [data-ttab]').forEach(b=>b.classList.toggle('on',b.dataset.ttab===name));
  document.querySelectorAll('#tbody [data-tpane]').forEach(p=>{ p.hidden=p.dataset.tpane!==name; });
  if(name==='diff'&&!TV.diffLoaded) loadDiff();
}
async function taskAction(what){
  const m=document.getElementById('tmsg'), id=TV&&TV.id; if(!id||!TASK_ACTS.has(what)) return;
  if(what==='diff') return tvTab('diff');
  if(what==='fly'){ const t=agentTasks().find(x=>x.id===id); const u=t&&(units.find(x=>(x.kind==='area'||x.kind==='dir')&&x.path===t.target)||units.find(x=>x.kind==='area'&&t.target&&String(t.target).startsWith(x.path+'/'))); if(u){ humanMove(); const a=approach(u); pilot={source:'human',x:a.x,y:a.y}; } else { const ts=unitByUid('task:'+id); if(ts){ humanMove(); px=ts.gx*TILE; py=ts.gy*TILE+60; } } return; }
  if(what==='check'){ const sel=document.getElementById('tcheckr'), runner=sel?sel.value:''; if(!runner) return;
    if(m){ m.className='saym'; m.textContent='asking '+runner+' for a verdict…'; }
    try{ const j=await api('/task/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,runner:runner})});
      toast(runner+' is checking the work',3000); if(typeof mateSay==='function') mateSay(runner+' is reading the work as a reviewer; it ends with VERDICT: PASS or FAIL.');
      await pollTasks(); if(TV&&TV.id===id){ TV.run=0; TV.offset=0; TV.shown=null; TV.acts=''; if(!TV.timer){ const ep=TV.epoch; TV.timer=setInterval(()=>loadTask(ep),1500); } loadTask(TV.epoch); }
    }catch(e){ if(m){ m.className='saym err'; m.textContent=e.message; } }
    return; }
  if(what==='map'){ const t=unitByUid('task:'+id); if(t){ humanMove(); px=t.gx*TILE; py=t.gy*TILE+60; } return; }
  if(what==='open'){ const t=agentTasks().find(x=>x.id===id); return api('/open',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:String(t&&t.target||'')})})
    .then(j=>{ if(m){ m.className='saym'; m.textContent='opened with '+(j.opener||'the configured opener'); } }).catch(e=>{ if(m){ m.className='saym err'; m.textContent=e.message; } }); }
  let force=false;
  if(REMOTE&&(what==='merge'||what==='discard'||what==='done')&&TV.blockers&&TV.blockers.length){
    if(m){ m.className='saym err'; m.textContent='the evidence says not yet: '+TV.blockers.join(' · ')+'. Accept or reject it on your computer.'; } return; }
  if(what==='merge'&&TV.blockers&&TV.blockers.length){   // blocked: the first click arms, the second merges anyway
    if(!TV.mergeArmed){ TV.mergeArmed=true; const b=document.querySelector('#tact [data-tact="merge"]'); if(b) b.textContent='Merge anyway';
      if(m){ m.className='saym err'; m.textContent='the evidence says not yet: '+TV.blockers.join(' · ')+'. Click “Merge anyway” to land it regardless.'; } return; }
    force=true; TV.mergeArmed=false;
  }
  else if(what==='merge'&&!confirm('Merge this task’s branch into your branch? Git refuses if it would overwrite uncommitted work.')) return;
  const path=what==='stop'?'/task/stop':'/task/close', body=what==='stop'?{id:id}:{id:id,action:what};
  if(force) body.force=true;
  if(m){ m.className='saym'; m.textContent='…'; }
  try{ const j=await api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(m) m.textContent=j.message||(TASK_WORD[j.status]||j.status);
    toast(j.message||('task '+(TASK_WORD[j.status]||j.status)),3500);
    await pollTasks(); if(TV&&TV.id===id){ TV.acts=''; TV.offset=0; TV.shown=null; TV.diffLoaded=false; if(!TV.timer){ const ep=TV.epoch; TV.timer=setInterval(()=>loadTask(ep),1500); } loadTask(TV.epoch); }
  }catch(e){ if(m){ m.className='saym err'; m.textContent=e.message; } }
}
const NL=String.fromCharCode(10);
function colorPatch(p){
  return String(p).split(NL).map(l=>{ const c=(l.startsWith('+++')||l.startsWith('---'))?'ph':l.startsWith('@@')?'pa':l.startsWith('+')?'pp':l.startsWith('-')?'pm':l.startsWith('diff ')?'pf':'';
    return c?'<span class="'+c+'">'+esc(l)+'</span>':esc(l); }).join(NL);
}
async function loadDiff(){
  const box=document.getElementById('tdiff'), id=TV&&TV.id; if(!box||!id) return;
  const seq=(TV.diffSeq=(TV.diffSeq||0)+1);   // only the newest request may draw: answers can arrive out of order
  box.innerHTML='<div class="saym">reading the changes…</div>';
  try{
    const d=await api('/task/diff?id='+encodeURIComponent(id));
    if(!TV||TV.id!==id||seq!==TV.diffSeq) return; TV.diffLoaded=true;
    const files=d.files||[], add=files.reduce((s,f)=>s+(f.add||0),0), del=files.reduce((s,f)=>s+(f.del||0),0), top=Math.max(1,...files.map(f=>(f.add||0)+(f.del||0)));
    const n=document.getElementById('tvdiffn'); if(n) n.textContent=files.length?' · '+files.length:'';
    box.innerHTML='<h3>'+files.length+' file'+(files.length===1?'':'s')+(files.length?' · <span style="color:#7ee787">+'+add+'</span> <span style="color:#ff7b72">−'+del+'</span>':'')+'</h3>'+(d.note?'<div class="saymeta">'+esc(d.note)+'</div>':'')+
      (files.length?files.map(f=>{ const a=f.add||0,r=f.del||0,w=a+r; return '<div class="dfile"><span class="ds">'+esc(f.status||'M')+'</span><span class="dp" title="'+attr(f.path)+'">'+esc(f.path)+(f.was_dirty?' <small>(modified before the thread)</small>':'')+'</span><span class="dbar"><i style="width:'+Math.round(60*a/top)+'px;background:#3fb950"></i><i style="width:'+Math.round(60*r/top)+'px;background:#f85149"></i></span><span class="dn">+'+a+'</span><span class="dm">−'+r+'</span></div>'; }).join('')
        :'<div class="viewonly">no changes'+(d.where?' in '+esc(d.where):'')+'</div>')+
      (d.patch?'<pre class="patch">'+colorPatch(d.patch)+'</pre>':'')+(d.truncated?'<div class="saymeta">the patch was cut at 200 KB</div>':'');
  }catch(e){ if(!TV||TV.id!==id||seq!==TV.diffSeq) return; box.innerHTML='<div class="saym err">'+esc(e.message)+'</div>'; }
}
// ---- FOCUS: what you point at lights up, what you clicked bursts, what is open keeps a ring.
// The map answers the cursor; nothing here changes the data. ----
const BURSTS=[];
function focusRadius(u){ if(!u) return 0; if(u===BOARD) return fcSunR()+10; if(u.kind==='area'){ const m=MOONS.find(x=>x.uid===u.uid); return (m?fcDispR(m):24)+8/ZOOM; } return Math.max(14,hitRadius(u)); }
function drawFocus(t){
  const ring=(u,col,dash,spin,width)=>{ if(!u||u.gx==null&&u!==BOARD) return; const x=u===BOARD?LY.star.x:u.gx*TILE, y=u===BOARD?LY.star.y:u.gy*TILE, r=focusRadius(u);
    cx.save(); cx.strokeStyle=col; cx.lineWidth=width||2.5; if(dash) cx.setLineDash(dash); cx.lineDashOffset=-spin; cx.beginPath(); cx.arc(x,y,r,0,TAU); cx.stroke(); cx.restore(); };
  if(composerOpen()){ const a=AIM?unitByUid(AIM):null; if(a){ const x=a.gx*TILE, y=a.gy*TILE, r=focusRadius(a)+10, k=REDUCED?0:Math.sin(t/300)*4; cx.save(); cx.strokeStyle=GB?'#9bbc0f':'#3dff9e'; cx.lineWidth=3;
      [[-1,0],[1,0],[0,-1],[0,1]].forEach(([dx,dy])=>{ cx.beginPath(); cx.moveTo(x+dx*(r+k),y+dy*(r+k)); cx.lineTo(x+dx*(r+k+18),y+dy*(r+k+18)); cx.stroke(); });
      cx.setLineDash([4,6]); cx.beginPath(); cx.arc(x,y,r,0,TAU); cx.stroke(); cx.restore();
      queuePlate({key:'aim',kind:'seat',pri:PLATE_PRI.seat,x:x,y:y,r:r+18,face:'n',hot:true,segs:[{t:'AIMED · '+fitLabel(String(a.name||''),240),col:GB?T.paper:'#3dff9e'}]}); } }
  if(VEC){ vecFocus(t); return; }
  if(hover&&!dragging){ const pulse=REDUCED?0:Math.sin(t/160)*.5+.5; cx.save(); cx.globalAlpha=.55+.45*pulse; ring(hover,composerOpen()?'#3dff9e':(GB?'#9bbc0f':'#ffd166'),[6,6],REDUCED?0:t/40,3); cx.restore();
    if(!GB){ const x=hover===BOARD?LY.star.x:hover.gx*TILE, y=hover===BOARD?LY.star.y:hover.gy*TILE; glowAt(x,y,focusRadius(hover)*.8,'#ffd166',.16); } }
  const sel=openUnit&&openUnit!==hover&&!(openUnit.kind==='area'&&FOCUS_CAM.uid===openUnit.uid)?(openUnit===BOARD?BOARD:unitByUid(openUnit.uid)):null;   // the open planet is the focus view itself: no ring
  if(sel){ cx.save(); cx.globalAlpha=.9; ring(sel,GB?'#9bbc0f':'#dcecff',[2,5],REDUCED?0:t/60,2); cx.restore(); }
  for(let i=BURSTS.length-1;i>=0;i--){ const b=BURSTS[i], k=(t-b.t0)/420; if(k>=1){ BURSTS.splice(i,1); continue; }
    cx.save(); cx.globalAlpha=(1-k)*.9; cx.strokeStyle=GB?'#9bbc0f':'#ffe3a3'; cx.lineWidth=3-2*k; cx.beginPath(); cx.arc(b.x,b.y,b.r+k*46,0,TAU); cx.stroke();
    cx.fillStyle=GB?'#9bbc0f':'#fff1a6'; for(let j=0;j<8;j++){ const a=j*TAU/8+k*1.2, d=b.r+k*58; cx.fillRect(Math.round((b.x+Math.cos(a)*d)/2)*2,Math.round((b.y+Math.sin(a)*d)/2)*2,2,2); } cx.restore(); }
}
// ---- BATTLEFIELDS: the problems of a planet (stale cron lines, open board items, failed tasks)
// circle it as red craft; the task ships docked there are its defence and fire at them while
// they work. A hazard ring marks the fight; a red ring with no defenders means UNDEFENDED. ----
// a contact's craft by its age tier: fresh ones small and yellow, aging amber, raiders (over a week) big and red
const TIER_LOOK={fresh:{k:.36,eye:'#ffe27a',eye2:'#fff1a6',fire:'#ffd34d',fire2:'#ffb02e'},aging:{k:.45,eye:'#ffb02e',eye2:'#ffd08a',fire:'#ff8a2e',fire2:'#ff6a2e'},
  raider:{k:.56,eye:'#ff4d5e',eye2:'#ff8a8a',fire:'#ffb02e',fire2:'#ff5a2e'}};
// work in warp at a planet (sky mode): one small still chevron per agent, up to three, beside the body; never animated
function warpTick(m){
  const n=Math.min(3,m.allies||0), z=zs(), x=m.x+m.r*.72, y=m.y-m.r*.72-6*z; if(!n) return;
  cx.save(); cx.strokeStyle=VEC?VC.teal:(GB?'#306230':'#5ff2ff'); cx.globalAlpha=.85; cx.lineWidth=(VEC?1:2)*z; cx.beginPath();
  for(let i=0;i<n;i++){ const ox=x+i*7*z; cx.moveTo(ox,y-4*z); cx.lineTo(ox+4*z,y); cx.lineTo(ox,y+4*z); }
  cx.stroke(); cx.restore();
}
// problem i of a planet's first eight: its angle on the orbit and its point (the orbit is squashed to .78 in y)
function foePos(m,i,t){
  const n=Math.min(8,m.enemies), er=m.r+26, a=i*TAU/n+(seedOf(m.uid)%7);
  return {a:a,x:m.x+Math.cos(a)*er,y:m.y+Math.sin(a)*er*.78};
}
function drawBattle(m,t){
  if(!m.enemies&&!m.allies&&!(m.rooms&&m.rooms.length)&&m.uid!=='sky:star') return;
  if(VEC){
    if(!m.enemies) return;
    const extent=Math.max(m.r,m.ringR,m.outerR)+120;
    if(m.x+extent<camx||m.x-extent>camx+VW/ZOOM||m.y+extent<camy||m.y-extent>camy+VH/ZOOM)return;
    vecTargets(m,t); return; }   // the hatched zone (drawn under the planet) replaces the ring; problems are hazard triangles
  // the pixel look: the open planet's focus view (ui/focus.js): its stations, its problems on one outer orbit by urgency, what
  // waits on the owner after them, its agents on the working ring, each with a tag
  drawFocusView(m,t);
}
// ---- THE HANGAR (sky mode and the full view): an empty berth is a window with a bare shell in it, an open bracket on
// the dock ring with its window's name up close; the comms line counts the berths against config "hangar_slots"
// (terminal slots used of the limit, idle, over the limit); runners the registry calls dead, down or frozen (sky.py's derelicts) drift
// as derelicts on a belt beyond the planets in sky mode. Nothing here is a contact, nothing is animated, and nothing is
// ever stopped. ----
function drawBerth(u,sx,sy){
  const col=VEC?VC.dim:(GB?'#306230':'#4a5d80'), w=14, h=10, y=sy-6;
  cx.save(); cx.strokeStyle=col; cx.lineWidth=VEC?1/ZOOM:2; cx.beginPath();
  cx.moveTo(sx-w,y-h+4); cx.lineTo(sx-w,y+h); cx.lineTo(sx+w,y+h); cx.lineTo(sx+w,y-h+4); cx.stroke(); cx.restore();
  plateHull(sx,y,12);
  const hot=hover===u, sel=selectedUid(u);
  if(sel||hot||detailAt(u,120)) queuePlate({unit:u.uid,uid:u.uid,dock:null,cls:null,need:false,x:sx,y:y,r:12,hot:sel||hot,key:'c:'+u.uid,kind:'seat',pri:PLATE_PRI.seat,face:'s',
    segs:[{t:String(u.name)+' · empty berth',col:GB?T.paper:'#8fa3bf',ord:1}]});
}
function hangarCount(){
  const agents=units.filter(u=>!u.remote&&((u.kind==='seat'&&!u.shell)||(u.kind==='ship'&&u.source==='process')));
  const slots=Number.isInteger(W.hangar_slots)&&W.hangar_slots>=0?W.hangar_slots:6;
  return {used:agents.length,slots:slots,idle:agents.filter(u=>u.status==='IDLE'||u.status==='BLANK').length,empty:units.filter(isBerth).length,
    over:agents.length>slots,derelicts:derelictRunners().length};
}
// the terminal slots in plain words (config "hangar_slots"): "3 of 6 terminal slots used · 2 idle · 2 empty · 1 stopped agent"
function berthLine(){ const h=hangarCount();
  return h.used+' of '+h.slots+' terminal slots used'+(h.over?' (over the limit)':'')+' · '+h.idle+' idle'+(h.empty?' · '+h.empty+' empty':'')+(h.derelicts?' · '+h.derelicts+' stopped agent'+(h.derelicts>1?'s':''):''); }
function derelictRunners(){ return (W.chars||[]).filter(c=>c&&c.kind==='runner'&&['DEAD','DOWN','FROZEN'].includes(String(c.status||''))); }
function drawDerelicts(){
  const list=derelictRunners(); if(!list.length) return;
  const R=(LY.rootOrbit||1100)+560, out=[];
  list.forEach((c,i)=>{ const a=Math.PI*.62+i*.09, x=LY.star.x+Math.cos(a)*R, y=project(x,LY.star.y+Math.sin(a)*R).y; out.push([c.name,x,y]);
    if(x<camx-80||x>camx+VW/ZOOM+80||y<camy-80||y>camy+VH/ZOOM+80) return;
    if(VEC){ const z=1/ZOOM, s=6*z; cx.save(); cx.strokeStyle=VC.dim; cx.lineWidth=z; cx.beginPath(); cx.moveTo(x-s,y-s); cx.lineTo(x+s,y+s); cx.moveTo(x+s,y-s); cx.lineTo(x-s,y+s); cx.stroke(); cx.restore(); }
    else drawSprite('cargo',x,y,a+.9,Object.assign(cargoPal('derelict:'+c.name,''),GB?{}:{h:'#6b7280',m:'#4b5563',d:'#2b3140',x:'#4b5563',X:'#2b3140',y:'#4b5563',Y:'#2b3140',c:'#3a4150'}));
    if(ZOOM>=.5){ cx.save(); cx.font=wfont(12); cx.textAlign='center'; cx.fillStyle=VEC?VC.muted:(GB?'#306230':'#7d8797'); cx.fillText(String(c.name)+' · '+String(c.status).toLowerCase(),x,y+24*zs()); cx.restore(); } });
  return out;
}
// ---- task ships on the map: a cargo ship per open task, docked at the planet it works on ----
function drawTaskShip(u,sx,sy,t){
  const st=String(u.status||''), col=TASK_COL[st]||'#9aa7bd', working=st==='running'||st==='checking', flick=REDUCED?0:Math.floor(t/90)%2;
  let x=sx, y=sy, ang=shipAngle(u), flying=false;
  const l=LAUNCHES[u.task_id];
  if(l!=null){ const k=(performance.now()-l)/2600;
    if(k<1&&!REDUCED){ const e=k<.5?2*k*k:1-Math.pow(-2*k+2,2)/2, L=launchPoint(); x=L.x+(sx-L.x)*e; y=L.y+(sy-L.y)*e;
      ang=Math.atan2(sy-L.y,sx-L.x)+Math.PI/2; flying=true; }
    else delete LAUNCHES[u.task_id]; }
  const wreck=st==='failed'||st==='lost';
  if(VEC){   // a thread in the vector look: teal while it works, yellow when it waits for review, red when it failed
    const vc=st==='review'?VC.yellow:wreck?VC.red:(working||flying)?VC.teal:VC.muted;
    if(working&&!flying) vecBeam(u,x,y,VC.teal,t);
    vecCraft('cargo',x,y,ang,vc);
    if(st==='checking') vecRing(x,y,30,VC.teal,REDUCED?.6:.45+.2*Math.sin(t*TAU/800),[4,3],REDUCED?0:t/50);
    if(st==='review'&&(REDUCED||Math.floor(t/650)%2)) vecRing(x,y,30,VC.yellow,.8,[3,4]);
  }else{
  if(working&&!flying) workBeam(u,x,y,col,t);
  if(working||flying) fxTrail(x,y,ang,t,28,2,[-6,6]);
  drawSprite('cargo',x,y,ang,Object.assign(cargoPal(u.task_id,(working||flying)?(flick?'#ffd34d':'#ff6a2e'):''),GB?{}:(wreck?{c:col,h:'#8a94a6',m:'#5b6473'}:{c:col})));
  if(wreck&&!GB&&!REDUCED){ cx.fillStyle='#8a94a6'; for(let k=0;k<3;k++){ const ph=((t/900)+k*.33)%1; cx.fillRect(Math.round((x+Math.sin(ph*6+k)*4)/2)*2,Math.round((y-12-ph*22)/2)*2,2,2); } }
  if(st==='checking') fxShield(x,y,30,t);
  if(st==='review'&&!REDUCED&&Math.floor(t/650)%2) pixelRing(x,y,30,30,col,0,TAU,0,true);
  }
  const selected=openTaskId===u.task_id, closeBy=selected||hover===u||detailAt(u,140);
  plateHull(x,y,craftHull('cargo'));
  // the thread's title, cut with an ellipsis, only when you are close, pointing at it or have it open
  if(closeBy) queuePlate({key:'c:'+u.uid,kind:'thread',pri:PLATE_PRI.thread,x:x,y:y,r:craftHull('cargo'),face:'s',uid:u.uid,unit:u.uid,dock:u.docked_at||null,cls:'thread',
    need:st==='review',hot:selected||hover===u,segs:[{t:(st==='running'?'▶ ':st==='review'?'● ':'')+fitLabel(String(u.name||''),220),col:col}]});
  if(selected&&!VEC){ cx.strokeStyle=GB?'#0f380f':'#dcecff'; cx.lineWidth=2; cx.strokeRect(Math.round(x)-18,Math.round(y)-27,36,54); }
}
document.getElementById('cgo').addEventListener('click',submitComposer);
document.getElementById('ccancel').addEventListener('click',()=>{ document.getElementById('composer').classList.remove('on'); document.getElementById('side').classList.remove('compose'); AIM=null; });
document.getElementById('ctext').addEventListener('keydown',e=>{ if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); submitComposer(); } });
document.getElementById('tasks').addEventListener('click',e=>{
  if(e.target.closest('[data-tnew]')){ openComposer(null); return; }
  const r=e.target.closest('[data-tid]'); if(r){ openTask(r.dataset.tid); return; }
  const c=e.target.closest('[data-cid]'); if(c){ const k=skyContacts().find(x=>x.id===c.dataset.cid); if(k) openContact(k); return; }
  const w=e.target.closest('[data-wf]'); if(w){ CM.selectWorkflow(w.dataset.wf||null); return; }
  const a=e.target.closest('[data-uid]'); if(a){ const u=unitByUid(a.dataset.uid); if(u){ setView('map'); humanMove(); activate(u); } }
});
['pbody','tbody'].forEach(hid=>{ const host=document.getElementById(hid);
host.addEventListener('click',e=>{
  const a=e.target.closest('[data-tact]'); if(a){ taskAction(a.dataset.tact); return; }
  const tb=e.target.closest('[data-ttab]'); if(tb){ tvTab(tb.dataset.ttab); return; }
  if(e.target.closest('[data-tmore]')){ tvEarlier(); return; }
  const r=e.target.closest('[data-trun]'); if(r&&TV){ const n=Number(r.dataset.trun); if(n&&n!==TV.shown){ if(TV.timer) clearInterval(TV.timer); TV.run=n; TV.offset=0; TV.shown=null; TV.acts=''; TV.busy=false; TV.epoch=++TASK_EPOCH; const ep=TV.epoch; TV.timer=setInterval(()=>loadTask(ep),1500); loadTask(ep); } return; }
  const nt=e.target.closest('button.newtask'); if(nt){ openComposer({kind:'dir',path:nt.dataset.path}); return; }
});
host.addEventListener('submit',e=>{
  if(e.target.id!=='tfollowf') return; e.preventDefault();
  const ta=document.getElementById('tfollowi'), m=document.getElementById('tmsg'), id=TV&&TV.id; if(!ta||!id) return;
  const text=ta.value.trim(); if(!text){ if(m){ m.className='saym err'; m.textContent='say what to do next'; } return; }
  if(m){ m.className='saym'; m.textContent='sending…'; }
  api('/task/followup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,text:text})})
    .then(async()=>{ ta.value=''; await pollTasks(); if(TV&&TV.id===id){ TV.run=0; TV.offset=0; TV.shown=null; TV.acts=''; if(!TV.timer){ const ep=TV.epoch; TV.timer=setInterval(()=>loadTask(ep),1500); } loadTask(TV.epoch); } })
    .catch(err=>{ if(m){ m.className='saym err'; m.textContent=err.message; } });
});
host.addEventListener('keydown',e=>{ if(e.target.id==='tfollowi'&&e.key==='Enter'&&(e.ctrlKey||e.metaKey)){ e.preventDefault(); const f=document.getElementById('tfollowf'); if(f) f.requestSubmit(); } });
});
// ---- THE META BOARD (U): one chat that sees every task, T3 thread, agent window and git worktree and says what to do with each (meta.py;
// the verdict rules are playbooks/meta.md, the owner's to edit). A row is advice. Its buttons open the task panel with its own guarded
// buttons, or call the routes the page already uses (send a follow-up, rename); nothing here merges, closes, stops or removes
// anything. Desktop page only: the bridge answers a phone 404, and the key does nothing there. ----
const META={res:null,info:null,busy:false,id:null,since:0,tick:null,err:'',done:{},edits:{},seq:0,model:''};
try{ META.model=localStorage.getItem('metaModel')||''; }catch(e){}
const META_GROUPS=[['merge','Merge'],['follow-up','Follow up'],['check','Check'],['rename','Rename'],['set-aside','Set aside'],['stop','Stop'],
  ['close','Close in T3'],['go','Needs you'],['land','Land'],['retire','Retire the worktree'],['keep','Keep']];
function metaEl(id){ return document.getElementById(id); }
function metaIsOpen(){ return metaEl('meta').classList.contains('on'); }
function metaTask(id){ return agentTasks().find(t=>t.id===id)||null; }
function metaClose(){ metaEl('meta').classList.remove('on'); }
function metaStat(){
  const el=metaEl('metastat'); if(!el||!metaIsOpen()) return;
  const R=META.res, n=Math.round((Date.now()-META.since)/1000);
  el.className='mstat'+(META.err?' err':'');
  el.textContent=META.busy?'thinking… '+n+' s (a whole map takes a minute or two; you can close this, it keeps going)'
    :META.err?META.err:R&&R.status==='done'?'answered by '+(R.model||'the model')+' in '+(R.seconds!=null?Number(R.seconds).toFixed(0):'?')+' s · '+(R.rows||[]).length+' verdicts':'';
}
// what the owner typed into a row's box survives a redraw (sending one row must not reset another)
function metaKeep(){ document.querySelectorAll('#metaboard [data-ref]').forEach(row=>{ const f=row.querySelector('textarea,input'); if(f) META.edits[row.dataset.ref]=f.value; }); }
function metaRow(r){
  const R=META.res, it=(R.items||{})[r.ref]||{}, now=r.kind==='task'?metaTask(r.id):null;
  const send=(R.sends||[]).find(x=>x.ref===r.ref), ren=(R.renames||[]).find(x=>x.ref===r.ref), chk=(R.checks||[]).find(x=>'task:'+x.id===r.ref);
  const title=String((now&&now.title)||it.title||r.id), done=META.done[r.ref], id=attr(r.id);
  const btn=(a,label,extra)=>'<button type="button" data-ma="'+a+'" data-id="'+id+'"'+(extra||'')+'>'+label+'</button>';
  let sub='', body='', acts='';
  if(r.kind==='task'){
    const st=(now&&now.status)||it.status||'';
    sub=esc(TASK_WORD[st]||st)+(it.runner?' · '+esc(agentLine(it.runner,it.model)):'');
    if(r.verdict==='check'&&chk) acts=btn('check','open · check with '+esc(runnerLabel(chk.runner)),' data-runner="'+attr(chk.runner)+'"');
    else if(r.verdict==='follow-up'&&send) body='<div class="mf"><textarea maxlength="8000" aria-label="follow-up">'+esc(META.edits[r.ref]!=null?META.edits[r.ref]:send.text)+'</textarea>'+btn('send','send')+'</div>';
    else if(r.verdict==='rename'&&ren) body='<div class="mf"><input maxlength="90" value="'+attr(META.edits[r.ref]!=null?META.edits[r.ref]:ren.title)+'" aria-label="new title">'+btn('rename','rename')+'</div>';
    else acts=btn('open',{merge:'open to merge','set-aside':'open to set aside',stop:'open to stop',check:'open to check'}[r.verdict]||'open');
  } else if(r.kind==='t3'){
    sub='T3 thread · '+esc(it.status||'');
    const link=/^https?:[/][/]/.test(String(it.open_url||''))?'<a class="mlink" href="'+attr(it.open_url)+'" target="_blank" rel="noopener noreferrer">open in T3</a>':'';
    acts=link+(it.uid&&unitByUid(it.uid)?btn('seat','on the map',' data-uid="'+attr(it.uid)+'"'):'');
    if(r.verdict==='follow-up'&&send) body='<div class="mf"><textarea maxlength="8000" aria-label="prompt text for T3">'+esc(META.edits[r.ref]!=null?META.edits[r.ref]:send.text)+'</textarea>'+btn('copy','copy')+'</div>';
  } else if(r.kind==='seat'){
    sub='agent window · '+esc(String(it.status||'').toLowerCase());
    acts=it.uid&&unitByUid(it.uid)?btn('seat','go to it',' data-uid="'+attr(it.uid)+'"'):'';
  } else {
    sub='worktree on '+esc(it.branch||'(detached)')+' · ahead '+esc(it.ahead==null?'?':it.ahead)+' behind '+esc(it.behind==null?'?':it.behind)+' · '
      +(it.dirty==null?'changes unknown':it.dirty?esc(it.dirty)+' changed file'+(it.dirty===1?'':'s'):'clean')+(it.owner?' · task '+esc(it.owner):' · no task')
      +(it.subject?' · last: '+esc(it.subject):'');
    body='<div class="ms">'+esc(r.id)+'</div>'+(r.verdict==='retire'&&it.command?'<pre class="mcmd" title="click to select, then run it yourself in a terminal">'+esc(it.command)+'</pre>':'');
  }
  return '<div class="mr" data-v="'+attr(r.verdict)+'" data-ref="'+attr(r.ref)+'"><div class="mb"><div class="mt">'+esc(title)+'<small>'+sub+'</small></div><div class="mw">'+esc(r.reason)+'</div>'+body
    +(done?'<div class="mmsg'+(done.err?' err':'')+'">'+esc(done.text)+'</div>':'')+'</div>'+(acts?'<div class="ma2">'+acts+'</div>':'')+'</div>';
}
function metaDraw(fresh){
  if(!fresh) metaKeep();                                // a new answer starts with the model's own text
  const info=META.info||{}, R=META.res, pill=metaEl('metamodel'), models=info.models||[];
  const cur=models.find(m=>m.name===META.model)||models.find(m=>m.name===info.default)||models[0];
  pill.hidden=models.length<2; if(cur){ pill.textContent=cur.label||cur.name; pill.title='answers with '+(cur.label||cur.name)+' (click to switch)'; }
  metaEl('metago').disabled=META.busy||info.available===false; metaEl('metaq').disabled=info.available===false;
  metaEl('metaq').placeholder=info.available===false?'no model is set up: put claude on PATH or set "wingmate" in container.json':'Or ask it something: which of these touch the 3D code?';
  metaEl('metachat').innerHTML=(info.chat||[]).map(t=>'<div class="mq">'+esc(t.q)+'</div><div class="ma">'+esc(t.summary)+'</div>').join('');
  let h='';
  if(R&&R.status==='done'){
    const rows=R.rows||[], by={};
    rows.forEach(r=>{ (by[r.verdict]=by[r.verdict]||[]).push(r); });
    META_GROUPS.forEach(g=>{ const list=by[g[0]]||[]; if(!list.length) return;
      const body=list.map(metaRow).join(''), head=esc(g[1])+' · <b>'+list.length+'</b>';
      h+=g[0]==='keep'?'<details class="mg"><summary>'+head+'</summary>'+body+'</details>':'<div class="mg">'+head+'</div>'+body; });
    if(!rows.length&&R.summary) h+='<div class="mq" style="margin-top:8px">'+esc(R.q)+'</div><div class="ma">'+esc(R.summary)+'</div>';
    const miss=R.missing||[], notes=(R.notes||[]).slice(), un=R.unjudged||[], bad=(R.rejected||[]).length;
    if(un.length) notes.push(un.length+' item'+(un.length===1?'':'s')+' got no verdict from the model: '+un.slice(0,6).map(x=>((R.items||{})[x]||{}).title||x).join(', ')+(un.length>6?', …':''));
    if(bad) notes.push(bad+' line'+(bad===1?'':'s')+' of its answer did not fit the format and were ignored');
    if(R.run) notes.push('what it was sent is kept in meta/'+R.run+' beside the tasks folder');
    h+='<div class="mg">What I couldn’t see · <b>'+miss.length+'</b></div><ul>'+miss.map(m=>'<li>'+esc(m)+'</li>').join('')+notes.map(m=>'<li class="mnote">'+esc(m)+'</li>').join('')+'</ul>';
  } else if(R&&R.status==='error') h='<div class="mstat err">'+esc(R.error||'it could not answer')+'</div>'+((R.notes||[]).length?'<ul>'+R.notes.map(m=>'<li class="mnote">'+esc(m)+'</li>').join('')+'</ul>':'');
  else if(!META.busy) h='<div class="sub">Press Triage all: it reads everything and gives each item a verdict with a reason. The facts and the prompt it was sent are saved with the run.</div>';
  metaEl('metaboard').innerHTML=h; metaEl('metamiss').innerHTML=''; metaStat();
}
async function metaLoad(){
  const seq=++META.seq; let fresh=false;      // only the newest request may change the board: an older answer that arrives late is dropped
  try{ const j=await api('/meta'); if(seq!==META.seq) return; META.info=j;
    if(!j.models.some(m=>m.name===META.model)) META.model=j.default;
    if(j.pending_id&&!META.busy){ META.busy=true; META.id=j.pending_id; META.since=Date.now()-j.elapsed*1000; metaWatch(); }
    // the bridge's last finished run is the newest there is (a poll that failed, another tab): a different one replaces the board, and
    // what was typed into or sent from the old rows goes with it. One that finished before the one held never replaces it.
    if(j.last&&(!META.res||(j.last.id!==META.res.id&&!(Number(j.last.finished)<Number(META.res.finished))))){
      META.res=j.last; META.done={}; META.edits={}; fresh=true; }
  }catch(e){ if(seq!==META.seq) return; META.err=e.message; sessionLost(e); }
  metaDraw(fresh);
}
async function metaWatch(){
  const id=META.id, limit=Date.now()+(((META.info||{}).max_seconds||300)+60)*1000;
  if(!META.tick) META.tick=setInterval(metaStat,1000);
  try{
    while(Date.now()<limit){
      await new Promise(r=>setTimeout(r,1500));
      const a=await api('/meta/answer?id='+encodeURIComponent(id)); if(a.status==='thinking') continue;
      META.busy=false; META.res=a; META.done={}; META.edits={}; META.err=a.status==='error'?(a.error||'it could not answer'):'';
      try{ const seq=++META.seq, j=await api('/meta'); if(seq===META.seq) META.info=j; }catch(e){}
      metaDraw(true); if(!metaIsOpen()) toast('The meta board has an answer: press U',4000);
      return;
    }
    throw new Error('no answer yet; it may still be running: reopen the board in a minute');
  }catch(e){ META.busy=false; META.err=e.message; metaDraw(); sessionLost(e); }
}
async function metaAsk(q){
  if(META.busy) return;
  META.err=''; META.busy=true; META.since=Date.now(); metaDraw();
  try{ const body={}; if(q) body.q=q; if(META.model) body.model=META.model;
    const j=await api('/meta/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    META.id=j.id; metaEl('metaq').value=''; metaWatch();
  }catch(e){ META.busy=false; META.err=e.message; if(/still thinking/.test(e.message)) await metaLoad(); else metaDraw(); sessionLost(e); }
}
async function metaShow(on){
  if(!on){ metaClose(); return; }
  if(!LIVE||!DESK||MOB){ toast(!LIVE?'open the live page to use the meta board':'the meta board is on the desktop page',3000); return; }
  if(VIEW!=='map') setView('map');                 // the Today page sits over the board
  metaEl('meta').classList.add('on'); metaDraw(); await metaLoad();
}
function toggleMeta(){ return metaShow(!metaIsOpen()); }
function metaSay(ref,text,err){ META.done[ref]={text:text,err:!!err}; metaDraw(); }
async function metaPost(path,body){ return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); }
async function metaClick(e){
  const b=e.target.closest('button[data-ma]'); if(!b) return;
  const row=b.closest('[data-ref]'), ref=row.dataset.ref, id=b.dataset.id, what=b.dataset.ma;
  if(what==='open'){ metaClose(); openTask(id); return; }
  if(what==='check'){ const runner=b.dataset.runner; metaClose(); openTask(id);   // the checker is preselected; the owner presses "check it"
    setTimeout(()=>{ const sel=document.getElementById('tcheckr'); if(sel&&runner&&Array.from(sel.options).some(o=>o.value===runner)) sel.value=runner;
      toast('Ready to have '+runnerLabel(runner)+' check it: press check it',4000); },1800); return; }
  if(what==='seat'){ const u=unitByUid(b.dataset.uid); if(u){ metaClose(); if(VIEW!=='map') setView('map'); humanMove(); show(u); } return; }
  if(what==='copy'){ const ta=row.querySelector('textarea'); try{ await navigator.clipboard.writeText(ta?ta.value:''); metaSay(ref,'copied'); }catch(err){ metaSay(ref,'could not copy: select the text and copy it',true); } return; }
  b.disabled=true;
  try{
    if(what==='send'){ const text=row.querySelector('textarea').value.trim(); if(!text){ b.disabled=false; metaSay(ref,'say what to do next',true); return; }
      await metaPost('/task/followup',{id:id,text:text}); await pollTasks(); metaSay(ref,'sent: the agent is on it'); }
    else if(what==='rename'){ const title=row.querySelector('input').value.trim();
      await metaPost('/task/rename',{id:id,title:title}); await pollTasks(); metaSay(ref,'renamed'); }
  }catch(err){ b.disabled=false; metaSay(ref,err.message,true); sessionLost(err); }
}
function metaSubmit(e){ e.preventDefault(); const q=metaEl('metaq').value.trim(); if(q) metaAsk(q); }
function metaModelNext(){ const ms=(META.info||{}).models||[], i=ms.findIndex(m=>m.name===META.model), n=ms[(i+1)%ms.length];
  if(!n) return; META.model=n.name; try{ localStorage.setItem('metaModel',n.name); }catch(e){} metaDraw(); }
metaEl('metaboard').addEventListener('click',metaClick);
metaEl('metago').addEventListener('click',()=>metaAsk(''));
metaEl('metaform').addEventListener('submit',metaSubmit);
metaEl('metamodel').addEventListener('click',metaModelNext);
addEventListener('keydown',e=>{ if(typing()||modKey(e)||e.repeat||composerOpen()) return; if(String(e.key||'').toLowerCase()==='u'){ e.preventDefault(); toggleMeta(); } });
// ---- PAGES AGENTS MADE (the show op "page", POST /agent/show): a card for each live page, a toast when one arrives, and a panel that runs one in
// a sandbox. A page's source never rides on a list of shows: the panel's frame is a document of its own, GET /show/page/frame, which the bridge
// answers to this session alone, with headers of its own (a policy that sandboxes it and lets it load nothing, a connection allowlist that lets
// it connect to nothing), on the desktop page only; a phone has the card and says to open it there. The frame is <iframe sandbox="allow-scripts">
// without allow-same-origin, so the page can run its own script and nothing else: no reach into this page, the bridge or the cookie, and nothing
// here listens for a message from it. This page's own policy and allowlist stay as they were: its links out work. ----
const PG={open:null,seen:new Set(),stamp:{},primed:false,timer:null,dock:'',gone:false};
function pgEl(id){ return document.getElementById(id); }
function pgCan(){ return LIVE&&DESK&&!MOB; }
function pgIsOpen(){ return PG.open!=null; }
// the live pages as the cards say them: from the shows in the last /tasks reply (title and size, never the source)
function pgList(){
  const out=[];
  CM.showList().forEach(x=>{
    if(!x||typeof x!=='object'||!Array.isArray(x.ops)) return;
    const op=x.ops.find(o=>o&&o.op==='page'); if(!op) return;
    const end=Date.parse(x.expires), left=Number.isFinite(end)?Math.round((end-Date.now())/1000):(Number(x.expires_in)||0);
    out.push({id:String(x.id||''),title:String(op.title||''),by:String(x.by||'an agent'),at:String(op.at||x.at||''),bytes:Number(op.bytes)||0,left:left,
      rev:String(x.rev!=null?x.rev:0),stamp:x.rev!=null?'r'+x.rev:String(x.t||'')+'|'+String(x.expires||'')+'|'+String(op.bytes||'')});
  });
  return out;
}
function pgLeft(sec){ return sec<=0?'0 s':sec<90?sec+' s':sec<5400?Math.round(sec/60)+' min':(Math.round(sec/360)/10)+' h'; }
function pgPlace(at){ return at.indexOf('place:')===0?at.slice(6):at?CM.labelText(at):'the map'; }
function pgDock(){
  const el=pgEl('pgdock'); if(!el) return;
  const L=pgList(), can=pgCan();
  const h=L.map(p=>'<div class="pgc'+(can?'':' ro')+'"'+(can?' role="button" tabindex="0" data-pg="'+attr(p.id)+'" title="open this page"':'')+'><span class="pgm">page</span><b>'+esc(p.title)
    +'</b><small>by '+esc(p.by)+' · at '+esc(pgPlace(p.at))+' · '+(p.left>0?esc(pgLeft(p.left))+' left':'expired')+(can?'':' · open it on the desktop')+'</small></div>').join('');
  if(h!==PG.dock){ PG.dock=h; el.innerHTML=h; }
  el.classList.toggle('on',L.length>0);
}
// the panel's header and the list of live pages to switch between; the frame itself is left alone, so a page keeps running while the clock moves
function pgChrome(){
  const L=pgList(), cur=L.find(p=>p.id===PG.open);
  pgEl('pghead').innerHTML=cur?'<span class="pgm">page</span><b>'+esc(cur.title)+'</b> · by '+esc(cur.by)+' · '+(cur.left>0?'expires in '+esc(pgLeft(cur.left)):'expired')
    :'<span class="pgm">page</span><b>This page is gone</b>';
  pgEl('pgtabs').innerHTML=L.length>1?L.map(p=>'<button type="button" data-pgtab="'+attr(p.id)+'" aria-current="'+(p.id===PG.open)+'">'+esc(p.title)+'</button>').join(''):'';
}
// the panel's frame: the page's own document (GET /show/page/frame, the bridge's headers on it), named by id and revision, so a new revision is a new
// address and loads afresh. The frame is the whole of what is isolated: nothing of the page's source passes through this page.
function pgFrame(p){ return '<iframe sandbox="allow-scripts" referrerpolicy="no-referrer" title="A page an agent made" src="/show/page/frame?id='+encodeURIComponent(p.id)+'&amp;rev='+encodeURIComponent(p.rev)+'"></iframe>'; }
function pgShow(id){
  if(!pgCan()){ toast('open the live page on the desktop to see a page',3000); return; }
  const p=pgList().find(x=>x.id===id);
  PG.open=id; PG.gone=!p;
  pgEl('pgpanel').classList.add('on'); pgEl('pgtoast').classList.remove('on'); pgChrome();
  pgEl('pgframe').innerHTML=p?pgFrame(p):'<div class="pgmsg">This page expired.</div>';
  try{ pgEl('pgclose').focus(); }catch(e){}
}
function pgClose(){ PG.open=null; PG.gone=false; pgEl('pgpanel').classList.remove('on'); pgEl('pgframe').innerHTML=''; }   // an empty frame stops the page's scripts and its loading
// the open page was taken down or ran out: its frame goes (its scripts stop with it) and the panel says so
function pgGone(){ PG.gone=true; pgEl('pgframe').innerHTML='<div class="pgmsg">This page expired.</div>'; }
function pgToast(fresh){
  const el=pgEl('pgtoast'); if(!el||!fresh.length) return;
  const p=fresh[fresh.length-1];
  el.innerHTML='<span><b>'+esc(p.by)+'</b> made a page: '+esc(p.title)+(fresh.length>1?' (and '+(fresh.length-1)+' more)':'')+'</span>'
    +(pgCan()?'<button type="button" data-pg="'+attr(p.id)+'">Open</button>':'<small>open it on the desktop</small>')+'<button type="button" data-pgx="1" aria-label="Dismiss">×</button>';
  el.classList.add('on'); clearTimeout(PG.timer); PG.timer=setTimeout(()=>el.classList.remove('on'),12000);
}
// after every /tasks reply (applyTasks): the cards, a toast for a page that was not there before (never an auto-open), and the open page again if it was replaced.
// A reply that carries no shows (the threads of a /state reply) says nothing about pages: it neither primes nor announces.
function pgSync(j){
  if(!j||!Array.isArray(j.shows)) return;
  const L=pgList(), ids=new Set(L.map(p=>p.id)), fresh=PG.primed?L.filter(p=>!PG.seen.has(p.id)):[];
  PG.seen.forEach(id=>{ if(!ids.has(id)){ PG.seen.delete(id); delete PG.stamp[id]; } });
  const replaced=PG.open!=null&&L.some(p=>p.id===PG.open&&PG.stamp[p.id]!==undefined&&PG.stamp[p.id]!==p.stamp);
  L.forEach(p=>{ PG.seen.add(p.id); PG.stamp[p.id]=p.stamp; });
  PG.primed=true;
  pgDock();
  if(PG.open!=null){
    if(!ids.has(PG.open)){ if(!PG.gone) pgGone(); pgChrome(); }
    else if(replaced||PG.gone) pgShow(PG.open);                  // a page of that id is there again (put anew)
    else pgChrome(); }
  if(fresh.length) pgToast(fresh);
}
function pgClick(e){
  const t=e.target; if(!t||!t.closest) return;
  if(t.closest('[data-pgx]')){ pgEl('pgtoast').classList.remove('on'); return; }
  const tab=t.closest('[data-pgtab]'); if(tab){ pgShow(tab.dataset.pgtab); return; }
  const open=t.closest('[data-pg]'); if(open) pgShow(open.dataset.pg);
}
pgEl('pgdock').addEventListener('click',pgClick);
pgEl('pgdock').addEventListener('keydown',e=>{ if(e.key==='Enter'||e.key===' '){ const c=e.target&&e.target.closest&&e.target.closest('[data-pg]'); if(c){ e.preventDefault(); pgShow(c.dataset.pg); } } });
pgEl('pgtoast').addEventListener('click',pgClick);
pgEl('pgtabs').addEventListener('click',pgClick);
pgEl('pgclose').addEventListener('click',pgClose);
CM.onInit.push(()=>{ CM.backLayers.push({pri:40,fn:k=>{ if(!pgIsOpen()) return false; if(k!=='has') pgClose(); return true; }}); });   // Esc and a phone's back close it first
// ---- THE WINGMATE (the skin's name by default; "wingmate".name in container.json): a crew member in the corner. It explains whatever you point at or open (from the page's
// own data, instantly), speaks up when something needs you, and answers a typed question through
// the bridge (wingmate.py: the manual + a snapshot of this map, to a small model). Text only. ----
const MATE={text:'',typer:null,talking:false,thinking:false,lastSaid:0,lastHover:null,hoverT:0,hoverDone:false,tipI:0,hidden:false,mute:0,
  name:String((W.wingmate&&W.wingmate.name)||SKIN_NAMES.wingmate),models:[],model:'',last:null};
try{ MATE.hidden=localStorage.getItem('mate')==='off'; }catch(e){}
if(MOB) MATE.hidden=false;      // a phone has no hide button: the wingmate has a tab
// The models it can answer with (wingmate.py -> W.wingmate.models). The pill in its header picks one; the pick is kept in localStorage "mateModel".
MATE.models=((W.wingmate&&W.wingmate.models)||[]).filter(m=>m&&m.name);
{ let saved=''; try{ saved=localStorage.getItem('mateModel')||''; }catch(e){}
  const def=String((W.wingmate&&W.wingmate.default)||'');
  MATE.model=MATE.models.some(m=>m.name===saved)?saved:MATE.models.some(m=>m.name===def)?def:(MATE.models[0]?MATE.models[0].name:''); }
function mateInfo(name){ return MATE.models.find(m=>m.name===name)||null; }
function mateTrim(label){ return String(label||'').replace(/^Claude\\s+/i,''); }
function mateShort(m){ return mateTrim(m&&(m.label||m.name)); }
// The second opinion offered after an answer: the first model of another family (the small Haiku tier is no second opinion, so a bigger one comes first
// when there is one), else the next frontier Claude model after the one that answered.
function mateNext(name){
  const cur=mateInfo(name); if(!cur) return null;
  const big=m=>!/haiku/i.test(m.name+' '+m.label), other=MATE.models.filter(m=>m.family!==cur.family);
  if(other.length) return other.find(big)||other[0];
  const i=MATE.models.indexOf(cur), after=MATE.models.slice(i+1).concat(MATE.models.slice(0,i));
  return after.find(big)||after[0]||null;
}
function matePillDraw(){
  const p=document.getElementById('matepill'); if(!p) return; const m=mateInfo(MATE.model), many=MATE.models.length>1;
  p.hidden=!(m&&W.wingmate&&W.wingmate.available); if(!m) return;
  p.textContent=mateShort(m); p.disabled=!many; p.title='answers with '+m.label+(many?' (click to switch the model)':'');
}
// The small header line (who answered and how long) and the one "ask <model> too" link; line null = the idle text.
function mateHead(line,also){
  const md=document.getElementById('matemodel'); if(md) md.textContent=line!=null?line:((W.wingmate&&W.wingmate.available)?'':'explains the map');
  const al=document.getElementById('matealso'); if(al){ al.hidden=!also; al.textContent=also?('ask '+mateShort(also)+' too'):''; al.title=also?('ask '+also.label+' the same question and keep both answers'):''; }
}
// Every answer to the last question; once there are two, each sits under its model's label.
function mateStack(last){ return last.answers.length<2?last.answers[0].text:last.answers.map(a=>'\u2014 '+a.label+'\\n'+a.text).join('\\n\\n'); }
// The wingmate's comms portrait comes from the skin: three frames (IDLE, BLINK, TALK) of letter rows and their palette.
const MATE_PAL=(SKIN.portrait&&SKIN.portrait.palette)||{}, MATE_FRAMES=(SKIN.portrait&&SKIN.portrait.frames)||{};
function mateDraw(t){
  const c=document.getElementById('matec'); if(!c||!c.getContext) return; const g=c.getContext('2d'); if(!g) return;
  const blink=!!(MATE.talking||MATE.thinking)&&!REDUCED&&(t%4300)<150, talk=MATE.talking&&!REDUCED&&Math.floor(t/120)%2===0, think=MATE.thinking;
  const rows=MATE_FRAMES[blink&&MATE_FRAMES.BLINK?'BLINK':talk&&MATE_FRAMES.TALK?'TALK':'IDLE']; if(!rows) return;
  const h=rows.length, w=Math.max(...rows.map(r=>r.length));
  const P=Math.max(1,Math.floor(Math.min(c.width/w,c.height/h))), ox=Math.floor((c.width-w*P)/2), oy=Math.floor((c.height-h*P)/2);
  g.clearRect(0,0,c.width,c.height);
  rows.forEach((row,y)=>{ for(let x=0;x<row.length;x++){ const ch=row[x]; if(ch==='.') continue; let col=MATE_PAL[ch]; if(!col) continue;
    if(GB){ const v=parseInt(col.slice(1,3),16)+parseInt(col.slice(3,5),16)+parseInt(col.slice(5,7),16); col=v<180?'#0f380f':v<380?'#306230':v<560?'#8bac0f':'#9bbc0f'; }
    g.fillStyle=col; g.fillRect(ox+x*P,oy+y*P,P,P); } });
  if((talk||think)&&!GB&&!REDUCED){ g.fillStyle='rgba(95,180,255,.10)'; const k=Math.floor(t/60)%c.height; g.fillRect(0,k,c.width,2); }   // a comms line rolling while it speaks
  if(think){ g.fillStyle=GB?'#9bbc0f':'#ffd34d'; for(let i=0;i<3;i++){ if(Math.floor(t/300)%4>i) g.fillRect(c.width-22+i*6,6,4,4); } }
}
function mateSay(text,opts){
  opts=opts||{}; const el=document.getElementById('matetxt'); if(!el) return; text=String(text||'');
  if(!opts.keep){ MATE.last=null; mateHead(); }                 // only answers to a question (keep) leave the model line and the second-opinion link standing
  if(MATE.typer){ clearInterval(MATE.typer); MATE.typer=null; }
  MATE.text=text; MATE.lastSaid=Date.now(); MATE.talking=true;
  if(REDUCED||opts.instant){ el.textContent=text; MATE.talking=false; return; }
  let i=0; el.textContent='';
  MATE.typer=setInterval(()=>{ i+=2; el.textContent=text.slice(0,i); if(i>=text.length){ clearInterval(MATE.typer); MATE.typer=null; MATE.talking=false; } },16);
}
function mateShow(on){ MATE.hidden=!on; document.body.classList.toggle('mateoff',MATE.hidden); const b=document.getElementById('mateshow'); if(b) b.hidden=!MATE.hidden; if(!MOB){ try{ localStorage.setItem('mate',MATE.hidden?'off':'on'); }catch(e){} } }
// a phone and a touch tablet (a coarse pointer) have no mouse and no keys: their words say what to touch. What only a phone carries (its tab
// bar, the + button, More) a tablet does not: it keeps the desktop's layout with the zoom column, so a third string names those for the phone.
function byInput(desk,touch,phone){ return MOB?(phone===undefined?touch:phone):touchUI()?touch:desk; }
// the first k causes of a planet's craft, identical ones folded: "job true: never ran ×5, job true: late ×8"
function foeCauses(foes,k){ return CM.groupCauses((foes||[]).map(f=>CM.shortTitle(String((f&&(f.full||f.name))||''),48)).filter(Boolean))
  .slice(0,k||2).map(g=>CM.grouped(g[0],g[1])).join(', '); }
function mateExplain(u,force){
  if(!u||MATE.hidden) return; if(!force&&MATE.thinking) return;
  if(Date.now()<MATE.mute) return;                // an answer that just flew you somewhere stays on screen
  const mm=deck==='home'?MOONS:remoteMoons, ck=byInput('click','tap'), Ck=byInput('Click','Tap'); let s='';
  if(u===BOARD){ const S=W.summary||{}; s='This is the sun: this machine'+(W.name?' ('+W.name+')':'')+'. '+(S.areas!=null?S.areas+' top folders (the planets), '+S.rooms+' folders inside them, '+S.crons+' scheduled jobs, '+S.procs+' processes. ':'')+Ck+' it for the overview of every planet.'; }
  else if(u.kind==='area'){ const m=mm.find(x=>x.uid===u.uid)||{}, dw=decayWord(u.age,m.kind);
    const tn=TYPE_NAME[m.type]||'', art=/^[aeiou]/i.test(tn)?'an ':'a ';
    // the most troubled planet says so in its own words (typeWhy starts "the most troubled planet on this map: ")
    const lead=m.type==='blight'&&m.typeWhy?m.typeWhy+'; ':tn?art+tn+' ('+m.typeWhy+'): ':'';
    s=String(u.name).toUpperCase()+' is '+(u.alias_of?'a symlink to '+u.alias_of+': nothing of its own.':(lead+fmtN(u.files)+' files'+(u.age!=null?', last write '+CM.daysAgo(u.age):'')+(dw?', '+dw:'')+'.'));
    if(m.wellR) s+=' It is heavy: '+fmtBytes(m.bytes)+' on disk'+(m.bloat>=30?', '+m.bloat+'% of it rebuildable caches, so mostly dead weight you could regenerate':m.bloat>=10?', '+m.bloat+'% caches':'')+'; its gravity well pulls your ship in.';
    if(m.trend&&m.trend.worse) s+=' It got WORSE this week: '+m.trend.why+'; that is the red storm ring.';
    // what waits here in the count's nouns, and its first two causes with identical ones folded ("job true: never ran ×5")
    const waits=m.enemies?moonCountLine(moonCounts(m))||(m.enemies+' '+CM.countNoun('problems',m.enemies)):'', causes=m.enemies?foeCauses(m.foes,2):'';
    if(m.enemies&&m.allies) s+=' A battlefield: '+m.allies+' agent'+(m.allies>1?'s':'')+' working against '+waits+(causes?' ('+causes+')':'')+'.';
    else if(m.enemies) s+=' '+waits+(causes?' ('+causes+')':'')+' and no agent on it: '+byInput('press N here','long-press it')+' to send one.';
    else if(m.allies) s+=' '+m.allies+' agent'+(m.allies>1?'s are':' is')+' working here.';
    if(u.tag_desc) s+=' Purpose: '+String(u.tag_desc).slice(0,80)+'.';
    s+=' '+byInput('Click to open it; N starts a task here.','Tap to open it; hold it to start a task here.','Tap to open it; the + button starts a task here.'); }
  else if(u.kind==='dir'){ const sh=roomShape(u); s=u.name+' is a folder inside '+String(u.area||'').replace('area:','')+': '+(u.files==null?'not walked yet':fmtN(u.files)+' files')+(u.age!=null?', last write '+CM.daysAgo(u.age):'')+(u.door?', has an instruction file':'')+(u.git?', a git repo':'')+'. '+(sh==='cloud'?'A dust cloud: a rebuildable cache. ':sh==='probe'?'A satellite: a hidden folder. ':sh==='rock'?'A rock: small or empty. ':'')+Ck+' to see what is inside.'; }
  else if(u.kind==='task'){ s='Task \u201c'+String(u.name).slice(0,50)+'\u201d: '+(TASK_WORD[u.status]||u.status)+', '+agentLine(u.runner,u.model)+' in '+shortPath(u.target)+'. '+({running:'It is working now; '+ck+' to watch what it does.',review:'It finished: '+ck+' to see the changes, then accept them, follow up or reject them (the copy is kept).',failed:'It failed: '+ck+' to read why; a follow-up continues on the same safe copy.',stopped:'You stopped it; follow up or reject it (the copy is kept).',lost:'Its supervisor went away; '+ck+' to see what it left.'}[u.status]||Ck+' to open it.'); }
  else if(isShip(u)){ s=(u.alias||u.name)+' is an agent ('+(u.source||u.kind)+'): '+String(u.status||'').toLowerCase()+(u.cwd?', in '+shortPath(u.cwd):'')+'. '+(u.status==='WAITING'?'It is waiting on YOU: '+ck+' to answer it.':u.status==='DRAFT'?'Your unsent draft is sitting in it.':Ck+' to read its last lines'+(u.kind==='seat'&&!u.remote?' and send it a message.':'.')); }
  else if(u.kind==='cron'){ s=u.name+' is a scheduled job ('+String(u.when||u.schedule||'')+'): '+(u.verdict?CM.verdictWord(u.verdict):String(u.status||''))+'. '+(BAD_VERDICTS.includes(u.verdict)?'That makes it a problem of its planet.':'It is doing its job.')+(u.room?' It writes into '+u.room+'.':''); }
  else if(u.kind==='proc'){ s=u.name+' is a running process ('+String(u.elapsed||'')+', '+String(u.count||1)+' of them). '+Ck+' for the command.'; }
  else if(u.kind==='channel'){ s=u.name+' is a relay channel between machines, last traffic '+(u.idle!=null?CM.daysAgo(u.idle):'unknown')+'. Freighters fly along it when something moved today.'; }
  else if(u.kind==='portal'){ s=u.machine==='__home__'?'The gate home.':'The gate to '+u.machine+' ('+String(u.status||'')+'): '+ck+' to cross to its own map.'; }
  else if(u.kind==='machine'){ s=u.name+' is another machine of yours: '+String(u.status||'')+'. Its gate crosses to it.'; }
  else s=String(u.name||u.uid)+' \u00b7 '+String(u.kind||'');
  mateSay(s);
}
function mateTips(){
  const tips=[], AT=agentTasks(), tx=byInput;    // a phone has no keys: its tips say what to touch
  AT.filter(t=>t.status==='review').forEach(t=>tips.push({p:0,s:'\u201c'+taskTitle(t,40)+'\u201d is done and waiting for you: open it '+tx('in the Agents rail (K)','in the Agents rail (the review count at the bottom opens it)','in the Agents tab')+', see the changes, then accept or reject them.'}));
  AT.filter(t=>t.status==='failed').forEach(t=>tips.push({p:0,s:'\u201c'+taskTitle(t,40)+'\u201d failed. Open it '+tx('in the Agents rail (K)','in the Agents rail (the review count at the bottom opens it)','in the Agents tab')+' to read why; a follow-up continues on the same safe copy.'}));
  units.filter(u=>u.kind==='seat'&&u.status==='WAITING').forEach(u=>tips.push({p:0,s:(u.alias||u.name)+' is waiting on you (a permission or a question). '+tx('Click','Tap')+' its ship.'}));
  MOONS.filter(m=>m.enemies&&!m.allies).forEach(m=>{ const c=foeCauses(m.foes,2); tips.push({p:1,s:m.label+' has '+CM.word('undefended')+': '+(moonCountLine(moonCounts(m))||(m.enemies+' '+CM.countNoun('problems',m.enemies)))+(c?' ('+c+')':'')+'. '+tx('Press N on it','Long-press it')+' to send an agent.'}); });
  MOONS.filter(m=>m.trend&&m.trend.worse).forEach(m=>tips.push({p:1,s:m.label+' got worse this week: '+m.trend.why+'. The red storm ring is that.'}));
  MOONS.filter(m=>m.decay>=.5&&m.life!=='ALIAS').forEach(m=>tips.push({p:2,s:m.label+' has had no writes for '+m.age+' days; it fades and drifts outward. Fine if it is finished, a sign if it is not.'}));
  { const c=CM.counts(), sk=skyOk(), nd=sk&&Number.isInteger(c.decisions)?c.decisions:(W.quests||[]).length, od=sk&&c.oldest_decision&&Number.isInteger(c.oldest_decision.days)?c.oldest_decision.days:(W.oldest||0);
    if(nd) tips.push({p:2,s:nd+' '+CM.countNoun('decisions',nd)+(nd===1?' waits':' wait')+' for you'+(od>0?', the oldest '+CM.ageWords(od):'')+'. Only you can close them: '+tx('press Q.','tap the decisions number at the top.')}); }
  [tx('Click anything: planets, moons, ships, labels, the star. E opens what you are next to.','Tap anything: planets, moons, ships, labels, the star.'),
   tx('N starts a task: pick the folder, the agent and the model; in a repo it works on a safe copy.','Hold a planet and pick New task here: pick the folder, the agent and the model; in a repo it works on a safe copy.','The + button starts a task: pick the folder, the agent and the model; in a repo it works on a safe copy.'),
   tx('Wheel zooms, drag pans, 0 resets the zoom, C recenters on your ship.','Pinch zooms, drag pans. The target button recenters on your ship, and holding it resets the zoom.'),
   tx('K lists tasks; T is the Today page; L explains the map; Q is the board.','Tap the strip at the bottom for the Today page; the review count beside it opens your agents and tasks.','The tabs below are Today, the map, your agents and me. More, at the top, explains the map and opens the board.'),'Ask me anything in the box: where something is, what a planet means, what to do next.','A code, config or shared planet fades and drifts outward the longer nothing is written in it (reference, archive, knowledge and bus planets sit still); a red storm ring means it got worse this week.','A battlefield is a planet with agents working on its problems: red craft are the problems, bolts are the agents working.','Heavy folders have a gravity well: rings around them, and a pull on your ship when you fly inside. Purple wells are mostly rebuildable caches: junk you could regenerate.']
    .forEach(s=>tips.push({p:3,s:s}));
  return tips.sort((a,b)=>a.p-b.p);
}
function mateIdle(){
  if(MATE.hidden||MATE.thinking||typing()) return;
  if(Date.now()-MATE.lastSaid<30000) return;
  const tips=mateTips(); if(!tips.length) return;
  const urgent=tips.filter(t=>t.p===0), pick=urgent.length?urgent[MATE.tipI%urgent.length]:tips[MATE.tipI%tips.length]; MATE.tipI++;
  mateSay(pick.s);
}
function mateGreet(){
  // the counts it says are the one count (W.counts), the same numbers as the status line
  const S=W.summary||{}, c=CM.counts(), w=Number.isInteger(c.working)?c.working:0;
  const bits=[(S.areas!=null?S.areas:MOONS.length)+' planets', w+' agent'+(w===1?'':'s')+' working', c.need_you?c.need_you+' '+CM.countNoun('need_you',c.need_you):'',
    c.problems?c.problems+' '+CM.countNoun('problems',c.problems):''].filter(Boolean);
  mateSay('I am '+MATE.name+', flying with you. '+bits.join(', ')+'. '+byInput('Hover','Tap')+' anything and I explain it. Tell me what to build or fix and I draft the task; ask where things are and I take you there.'+(MATE.models.length>1?' The pill at the top of my window switches the model that answers ('+MATE.models.map(mateShort).join(', ')+').':'')+((W.wingmate&&W.wingmate.available)?'':' (No model is set for me, so I only explain what is on the map.)'));
}
// the bridge keeps one question in flight until the wingmate's command ends (max_seconds): wait that long and
// five seconds more, or a give-up while it still runs would make every re-ask a 409 "still thinking"
function mateWaitMs(){ const s=Number(W.wingmate&&W.wingmate.max_seconds); return Math.max(95000,(Number.isFinite(s)?s:0)*1000+5000); }
async function mateAsk(q,opts){
  opts=opts||{}; q=String(q||'').trim(); if(!q) return;
  if(!LIVE){ mateSay('This is a static copy of the page; open the live one and I can answer.'); return; }
  if(!(W.wingmate&&W.wingmate.available)){ mateSay('No model is set up for me yet, so I can only explain what you point at. Put claude, codex or gemini on your PATH or set "wingmate" in container.json.'); return; }
  // opts.second: the same question to another model, its words appended under the first answer (words only: its DRAFT/CHECK/GOTO lines are not acted on)
  const last=opts.second?MATE.last:null, model=opts.model||MATE.model, info=mateInfo(model), label=info?info.label:model;
  MATE.thinking=true;
  if(last){ mateSay(mateStack(last)+'\\n\\n\u2014 '+label+' \u2026',{instant:true,keep:true}); }
  else { mateSay('\u2026',{instant:true}); const box=document.getElementById('mateq'); if(box) box.value=''; }
  mateHead('\u2026 '+mateShort(info||{name:model}),null);
  const fail=msg=>{ MATE.thinking=false;
    if(last){ mateSay(mateStack(last)+'\\n\\n\u2014 '+label+': '+msg,{instant:true,keep:true}); mateHead(last.head,mateNext(last.model)); } else mateSay(msg); };
  try{
    const body={q:q}; if(model) body.model=model;
    const j=await api('/wingmate/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const id=j.id, waitMs=mateWaitMs(), until=Date.now()+waitMs; let got=null;
    while(Date.now()<until){ await new Promise(r=>setTimeout(r,900)); const a=await api('/wingmate/answer?id='+encodeURIComponent(id)); if(a.status!=='thinking'){ got=a; break; } }
    MATE.thinking=false;
    if(!got){ fail('No answer after '+Math.round(waitMs/1000)+' seconds; ask again in a moment.'); return; }
    if(got.status==='error'){ fail(got.error||'I could not answer that.'); return; }
    const said=got.answer||'(no answer)', gl=got.model||label, head='\u2014 '+mateTrim(gl)+' \u00b7 '+(got.seconds!=null?Number(got.seconds).toFixed(1):'?')+' s';
    if(last){ last.answers.push({label:gl,text:said}); last.head=head; mateSay(mateStack(last),{instant:true,keep:true}); mateHead(head,null); return; }
    mateSay(said,{keep:true});
    const mine={q:q,model:model,answers:[{label:gl,text:said}],head:head}, keepHead=()=>{ MATE.last=mine; mateHead(head,mateNext(model)); };   // a draft or check opening its panel says something else first
    keepHead();
    (got.actions||[]).forEach(act=>{
      if(act.kind==='draft'){ MATE.mute=Date.now()+15000; const u=units.find(x=>(x.kind==='area'||x.kind==='dir')&&x.path===act.path)||null;
        openComposer(u||{kind:'dir',path:act.path}); const ta=document.getElementById('ctext'); if(ta) ta.value=act.text||'';
        const cv=document.getElementById('cverify'); if(cv) cv.value=act.verify||'';
        const rs=document.getElementById('crunner'); if(rs&&act.runner&&Array.from(rs.options).some(o=>o.value===act.runner)){ rs.value=act.runner; fillModels(); }
        const ms=document.getElementById('cmodel'); if(ms&&act.model&&Array.from(ms.options).some(o=>o.value===act.model)) ms.value=act.model;
        setTimeout(()=>{ mateSay((got.answer?got.answer+' ':'')+'I drafted the thread, aimed at '+shortPath(act.path)+'. Change anything, then press Start.',{instant:true,keep:true}); keepHead(); },400); }
      else if(act.kind==='check'){ MATE.mute=Date.now()+15000; if(agentTasks().some(t=>t.id===act.id)){ openTask(act.id);
        setTimeout(()=>{ const sel=document.getElementById('tcheckr'); if(sel&&act.runner&&Array.from(sel.options).some(o=>o.value===act.runner)) sel.value=act.runner; mateSay('Ready to have '+(runnerLabel(act.runner)||'another agent')+' check that thread: press “check it”.',{instant:true,keep:true}); keepHead(); },1800); } }
    });
    if(got.goto&&!(got.actions||[]).length){ const u=unitByUid(got.goto); if(u){ MATE.mute=Date.now()+12000; if(VIEW!=='map') setView('map'); setTimeout(()=>{ humanMove(); pilot=null; px=u.gx*TILE; py=u.gy*TILE+64; activate(u); },700); } }
  }catch(e){ fail(e.message||'I could not ask that.'); sessionLost(e); }
}
{ const f=document.getElementById('matef'); if(f) f.addEventListener('submit',e=>{ e.preventDefault(); mateAsk(document.getElementById('mateq').value); });
  const h=document.getElementById('matehide'); if(h) h.addEventListener('click',()=>mateShow(false));
  const sh=document.getElementById('mateshow'); if(sh) sh.addEventListener('click',()=>mateShow(true));
  const nm=document.getElementById('matename'); if(nm) nm.textContent=MATE.name;
  mateHead(); matePillDraw();
  const pl=document.getElementById('matepill'); if(pl) pl.addEventListener('click',()=>{
    const i=MATE.models.findIndex(m=>m.name===MATE.model), n=MATE.models[(i+1)%MATE.models.length]; if(!n) return;
    MATE.model=n.name; try{ localStorage.setItem('mateModel',n.name); }catch(e){} matePillDraw(); });
  const al=document.getElementById('matealso'); if(al) al.addEventListener('click',()=>{
    const l=MATE.last, n=l&&mateNext(l.model); if(l&&n&&!MATE.thinking) mateAsk(l.q,{model:n.name,second:true}); });
  const sc=document.querySelector('#hud .sc'); if(sc) sc.addEventListener('click',()=>toggleQuests(true));
  const tk=document.getElementById('ticker'); if(tk) tk.addEventListener('click',e=>{ if(!e.target.closest('#nums,#hmore')) setView('today'); });
  addEventListener('keydown',e=>{ if(typing()||modKey(e)||composerOpen()) return; if(e.key==='/'){ e.preventDefault(); mateShow(true); const q=document.getElementById('mateq'); if(q) q.focus(); } else if(e.key.toLowerCase()==='b'&&!e.repeat) mateShow(MATE.hidden); });
  mateShow(!MATE.hidden);
  setInterval(()=>{ if(MATE.hidden) return; if((!MOB||document.body.dataset.tab==='wingmate')&&!(CM.chromeReady&&CM.chromeOn&&CM.chromeOn())) mateDraw(performance.now());   // the portrait is hidden in the status bar's line: not drawn
    const hv=(VIEW==='map'&&!composerOpen())?hover:null;
    if(hv!==MATE.lastHover){ MATE.lastHover=hv; MATE.hoverT=Date.now(); MATE.hoverDone=false; }
    else if(hv&&!MATE.hoverDone&&Date.now()-MATE.hoverT>420&&!MATE.thinking){ MATE.hoverDone=true; mateExplain(hv); }
    mateIdle(); },120);
  setTimeout(mateGreet,400); }
if(LIVE){ setInterval(pollTasks,4000); pollTasks(); }
// ---- MOBILE: four places behind a tab bar (Today, Map, Threads, the wingmate), the card a sheet over the map.
// Nothing here wraps another function. mobSync() reads the page's own state (which rail is open, which layer is
// up) and mirrors it into classes, badges, the sheet height and the history; it is safe to call at any time. ----
const MH={depth:0,busy:0,timer:0,sched:0}, MP={on:false,seat:false,full:false};
let MOB_SEEN=Date.now(), MOB_MO=null, MOB_HO=null;
let MOB_CMAP=false;      // the composer was opened from the map (+ Task, a long press): a sheet over the Map tab, not the Agents tab
const HINT_RE=/\\s*\\([A-Z]\\)|\\s*\\(\\/\\)|\\s*·?\\s*Ctrl\\+Enter|;?\\s*Ctrl\\/Cmd\\+Enter sends/g, HINT_E=/\\s*\\(\\s*\\)/g, HINT_T=/\\([A-Z]\\)|\\(\\/\\)|Ctrl\\+Enter|Ctrl\\/Cmd\\+Enter sends/;
const mq$=id=>document.getElementById(id);
const MSHEETS=['panel','quests','legend','prompter'];
function mobTab(){
  if(sideOpen()&&!mq$('side').classList.contains('pick')) return composerOpen()&&MOB_CMAP?'map':'threads';
  if(MOB_WINGMATE) return 'wingmate';
  return VIEW==='today'?'today':'map';
}
function mobSheetUp(){ return MSHEETS.some(id=>mq$(id).classList.contains('on')); }
// one history entry per layer that rose over the tab: a more-sheet, a card or the board, the composer, one thread
function mobLayers(){
  return (mq$('msheet').classList.contains('on')?1:0)+(mobSheetUp()?1:0)+(composerOpen()?1:0)+((openTaskId!=null&&sideOpen())?1:0)+(CM.backLayer('has')?1:0);
}
function mobSafe(){
  try{ if(!mobSafe.el){ const d=document.createElement('div'); d.style.cssText='position:fixed;left:0;top:0;width:0;height:0;visibility:hidden;pointer-events:none;padding-top:env(safe-area-inset-top)'; document.body.appendChild(d); mobSafe.el=d; }
    SAFE_T=parseFloat(getComputedStyle(mobSafe.el).paddingTop)||0; }catch(e){}
}
function mobBadge(name,n,dot){
  const el=mq$('tabn-'+name); if(!el) return;
  const show=dot?!!n:n>0, v=dot?'':(n>99?'99+':(n>0?String(n):''));
  if(el.getAttribute('data-n')!==v) el.setAttribute('data-n',v);
  if(el.hidden===show) el.hidden=!show;
}
// the seats waiting on you dock above the tab bar on every tab: the screens stop above them (--P), never under them
function mobPerm(){
  if(!MOB) return;
  const box=document.getElementById('permits'); if(!box) return;       // not mq$: glancePermits runs before that helper exists
  const r=box.classList.contains('on')&&box.getBoundingClientRect?box.getBoundingClientRect():null, p=r&&r.height>0?Math.ceil(r.height):0;
  if(p===MOB_P) return;
  MOB_P=p; document.documentElement.style.setProperty('--P',p+'px');
}
function mobSync(){
  if(!MOB||!mq$('panel')) return;
  mobPerm();
  const B=document.body, panel=mq$('panel'), side=mq$('side'), pOn=panel.classList.contains('on');
  if(MOB_WINGMATE&&pOn) MOB_WINGMATE=false;                       // a card opened from the wingmate's words means the map was wanted
  if(side.classList.contains('pick')&&!composerOpen()) side.classList.remove('pick');
  if(MOB_CMAP&&!composerOpen()){ MOB_CMAP=false; if(openTaskId==null&&sideOpen()) sideShow(false); }   // the map's composer closed (cancel, Esc): back to the map; a task it started opens in Agents
  const tab=mobTab();
  if(B.dataset.tab!==tab){ if(B.dataset.tab==='wingmate') MOB_SEEN=Date.now(); B.dataset.tab=tab; }   // what was said while you were on the tab was seen: the dot counts from when you left
  p3PovSync();
  document.querySelectorAll('#tabs button[data-tab]').forEach(b=>{ const on=b.dataset.tab===tab?'true':'false'; if(b.getAttribute('aria-selected')!==on) b.setAttribute('aria-selected',on); });
  const sl=mq$('tabl-wingmate'); if(sl&&sl.textContent!=='Ask') sl.textContent='Ask';      // the tab says what it is for; the assistant's name rides its label
  side.classList.toggle('tview',openTaskId!=null&&!composerOpen()&&sideOpen());
  // the card sheet: half height, full on a seat or a pull; the camera aims at what is left above it
  const seat=panel.classList.contains('seat')||!!panel.querySelector('#plive');
  if(pOn&&(!MP.on||seat!==MP.seat)) MP.full=seat;
  MP.on=pOn; MP.seat=seat;
  panel.classList.toggle('full',pOn&&MP.full&&!MOB_LAND);
  { const gp=mq$('pgrip'); if(gp){ const ex=String(pOn&&MP.full&&!MOB_LAND); if(gp.getAttribute('aria-expanded')!==ex) gp.setAttribute('aria-expanded',ex); } }
  SHEET_H=pOn&&!MOB_LAND?Math.round(innerHeight*.35)+MOB_P:0;      // the peek: the camera aims at the map left above it
  const over=['quests','legend','prompter'].some(id=>mq$(id).classList.contains('on'));
  B.classList.toggle('m-over',over); B.classList.toggle('m-cover',over||pOn); B.classList.toggle('m-pick',side.classList.contains('pick'));
  B.classList.toggle('m-peek',pOn&&!MP.full&&!MOB_LAND); B.classList.toggle('m-full',pOn&&MP.full&&!MOB_LAND);
  B.classList.toggle('m-comp',tab==='map'&&composerOpen());
  // badges: what waits on you
  const waiting=units.filter(u=>u.kind==='seat'&&!u.remote&&u.status==='WAITING').length, open=agentTasks().filter(t=>TASK_OPEN.includes(t.status)).length;
  { const nc=CM.counts(); mobBadge('today',Number.isInteger(nc.need_you)?nc.need_you:waiting+open); }   // the one count (W.counts)
  { const nc=CM.counts(); mobBadge('threads',Number.isInteger(nc.working)?nc.working:agentTasks().filter(t=>TASK_WORKING.includes(t.status)).length); }   // Agents: who is at work now
  // the assistant's tab: a numbered dot and the words for it ("1 new message from Scout", P38), never a bare square
  { const nw=tab!=='wingmate'&&MATE.lastSaid>MOB_SEEN?1:0, tw=mq$('tab-wingmate'), bw=mq$('tabn-wingmate'), al='Ask '+mateName()+(nw?' · '+nw+' new message':'');
    mobBadge('wingmate',nw);
    if(tw&&tw.getAttribute&&tw.getAttribute('aria-label')!==al) tw.setAttribute('aria-label',al);
    if(bw&&bw.setAttribute) bw.setAttribute('title',nw?nw+' new message from '+mateName():''); }
  if(typeof phoneSync==='function') phoneSync(tab);      // the top line, the walls (ui/phone.js)
  mobHistory();
}
// History: while any layer is up there is ONE entry above the page, so Back (or the browser's back gesture) closes the
// topmost layer. It is pushed and popped after the tick that changed the layers, one traversal at a time, because a
// push issued while a go(-1) is still travelling lands on the wrong entry.
function mobHistory(){ if(MH.sched) return; MH.sched=setTimeout(()=>{ MH.sched=0; mobHistoryRun(); },0); }
function mobHistoryRun(){
  if(typeof history==='undefined'||!history.pushState||MH.busy) return;
  const want=mobLayers()>0;
  if(want&&!MH.depth){ try{ history.pushState({cmLayer:1},''); MH.depth=1; }catch(e){} }
  else if(!want&&MH.depth){ MH.depth=0; MH.busy=1; clearTimeout(MH.timer); MH.timer=setTimeout(()=>{ MH.busy=0; mobHistory(); },700);
    try{ history.go(-1); }catch(e){ MH.busy=0; } }
}
addEventListener('popstate',()=>{ if(!MOB) return;
  if(MH.busy){ MH.busy=0; clearTimeout(MH.timer); mobHistory(); return; }      // our own go(-1) arrived
  MH.depth=0; mobBack(); mobSync(); });                                          // the user went back: close the topmost layer
function mobBack(){
  if(CM.backLayer('back')) return;
  if(mq$('msheet').classList.contains('on')) return msheetClose();
  if(mobSheetUp()) return mobCloseSheet();
  if(composerOpen()) return composerClose();
  if(openTaskId!=null&&sideOpen()) threadBack();
}
function mobCloseSheet(){ closeAll(); mq$('legend').classList.remove('on'); mq$('prompter').classList.remove('on'); mobSync(); }
function composerClose(){ mq$('composer').classList.remove('on'); mq$('side').classList.remove('compose','pick'); AIM=null; mobSync(); }
// one thread open in the Threads tab: leave it, empty it, stop polling it
function threadClose(){
  if(openTaskId==null&&!TV) return;
  stopTaskPoll(); const tb=mq$('tbody'); if(tb) tb.innerHTML='';
  if(openUnit&&openUnit.kind==='task') openUnit=null;
  openTaskId=null; renderTaskList();
}
function threadBack(){
  threadClose();
  if(MOB_RET==='today'){ MOB_RET=''; mtab('today'); } else mobSync();
}
function mobClear(){
  const sh=mq$('msheet'); if(sh.classList.contains('on')){ sh.classList.remove('on'); mq$('scrim').classList.remove('on'); }
  closeAll(); mq$('legend').classList.remove('on'); mq$('prompter').classList.remove('on'); mq$('side').classList.remove('pick');
  threadClose();
  const a=document.activeElement; if(a&&a.blur&&a!==document.body) a.blur();
}
function mtab(name){
  if(!MOB||!['today','map','threads','wingmate'].includes(name)) return;
  mobClear(); MOB_RET=''; MOB_CMAP=false;
  MOB_WINGMATE=name==='wingmate';
  sideShow(name==='threads');
  setView(name==='today'?'today':'map');
  if(name==='threads') pollTasks();
  mobSync();
}
let MSHEET_RET=null;      // what had the focus before the sheet took it
function msheetOpen(title,rows){
  const sh=mq$('msheet'), body=mq$('msheet-body'); if(!sh||!body) return;
  if(!sh.classList.contains('on')){ const a=document.activeElement; MSHEET_RET=a&&a!==document.body?a:null; }
  mq$('msheet-title').textContent=String(title||'');
  body.textContent='';
  (rows||[]).forEach(r=>{ const b=document.createElement('button'); b.type='button'; b.className='mrow'; b.textContent=String((r&&r.label)||'');
    b.addEventListener('click',()=>{ if(Date.now()<suppressClickUntil) return; msheetClose(); try{ if(r&&typeof r.run==='function') r.run(); }catch(err){ toast(String((err&&err.message)||err)); } });   // the lift of the press which opened the sheet lands on whatever row is under the finger: not a tap
    body.appendChild(b); });
  sh.classList.add('on'); mq$('scrim').classList.add('on'); mobSync();
  const first=body.querySelector('button'); if(first){ try{ first.focus({preventScroll:true}); }catch(e){} }   // a modal dialog takes the focus in
}
function msheetClose(){
  const was=mq$('msheet').classList.contains('on');
  mq$('msheet').classList.remove('on'); mq$('scrim').classList.remove('on'); mobSync();
  const r=MSHEET_RET; MSHEET_RET=null;
  if(was&&r&&r.isConnected&&r.focus){ try{ r.focus({preventScroll:true}); }catch(e){} }    // ...and gives it back
}
// keys have no place on a phone: each one that matters became a row here, a button or a tab
function mobKey(k){ return ()=>{ const a=document.activeElement; if(a&&a.blur&&a!==document.body) a.blur(); dispatchEvent(new KeyboardEvent('keydown',{key:k,bubbles:true})); }; }
function mobMore(){
  msheetOpen('More',[
    {label:'Help / quick tour',run:()=>CM.startGuide()},
    {label:'The board',run:()=>toggleQuests(true)},
    {label:'What am I looking at',run:mobKey('l')},
    {label:'Flows '+(FLOWS?'on':'off')+', tap to flip',run:mobKey('f')},
    {label:'Theme: '+THEME_NAME+', tap for the next',run:mobKey('v')},
    {label:'Mono palette',run:()=>{ const b=mq$('gbbtn'); if(b) b.click(); }},
    {label:'Fly pad '+(document.body.classList.contains('pad')?'on':'off')+', tap to flip',run:()=>document.body.classList.toggle('pad')},
    {label:'Vibrate '+(hapticOn()?'on':'off')+', tap to flip',run:()=>hapticSet(!hapticOn())}]);
}
function mToast(text,ms){
  const el=mq$('mtoast'), B=document.body; if(!el) return;
  if(B.dataset.tab==='map'&&!B.classList.contains('m-cover')) return;     // the map draws its own flash
  el.textContent=String(text); el.classList.add('on'); clearTimeout(mToast.t); mToast.t=setTimeout(()=>el.classList.remove('on'),ms||2800);
}
// the page's key hints mean nothing on a phone: "(N)", "Ctrl+Enter" and the like come out of the page's own words (button, summary
// and label text, dashboard links, the legend) and its placeholders, never out of what a person or an agent wrote: thread titles,
// transcripts and terminal text, card text, the wingmate's replies
const HINT_UI='button,summary,label,a[data-dash],#legend', HINT_NOT='pre,textarea,code,dd,#tlog,.live,#matetxt,.tv-msg,.trow,#tdiff,#quick';
function hintScrub(n){
  if(!n) return;
  if(n.nodeType===3){ const p=n.parentNode; if(p&&p.closest&&HINT_T.test(n.nodeValue)&&p.closest(HINT_UI)&&!p.closest(HINT_NOT)) n.nodeValue=n.nodeValue.replace(HINT_RE,'').replace(HINT_E,''); return; }
  if(n.nodeType!==1) return;
  if(n.hasAttribute&&n.hasAttribute('placeholder')){ const ph=n.getAttribute('placeholder'); if(HINT_T.test(ph)) n.setAttribute('placeholder',ph.replace(HINT_RE,'').replace(HINT_E,'')); }
  if(/^(PRE|TEXTAREA|SCRIPT|STYLE|CODE)$/.test(n.tagName)||n.id==='tlog'||(n.classList&&n.classList.contains('live'))) return;
  for(let c=n.firstChild;c;c=c.nextSibling) hintScrub(c);
}
function mobVV(){
  if(!MOB) return;
  // a pinch-zoomed page shrinks the visual viewport too: that is not a keyboard, so the layout keeps its own height
  const vv=window.visualViewport, zoomed=!!vv&&Math.abs((vv.scale||1)-1)>0.01, h=vv&&!zoomed?vv.height:innerHeight, t=vv&&!zoomed?vv.offsetTop:0,
    kb=Math.max(0,Math.round(innerHeight-h-t)), r=document.documentElement;
  r.style.setProperty('--vvh',h+'px'); r.style.setProperty('--vvt',t+'px'); r.style.setProperty('--kb',kb+'px');
  r.classList.toggle('kb',kb>80);
}
function mobObserve(){
  if(MOB_MO||typeof MutationObserver!=='function') return;
  MOB_MO=new MutationObserver(()=>{ if(MOB) mobSync(); });
  ['wrap','panel','quests','legend','prompter','composer','today','side'].forEach(id=>{ const el=mq$(id); if(el) MOB_MO.observe(el,{attributes:true,attributeFilter:['class']}); });
  const tb=mq$('tbody'); if(tb) MOB_MO.observe(tb,{childList:true});
  MOB_HO=new MutationObserver(ms=>{ if(!MOB) return; ms.forEach(m=>{
    if(m.type==='childList') m.addedNodes.forEach(hintScrub); else hintScrub(m.target); }); });
  ['panel','side','today','quests','mate','legend'].forEach(id=>{ const el=mq$(id); if(el) MOB_HO.observe(el,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['placeholder']}); });
}
// A phone borrows three of the desktop's switches (the rail, the quiet HUD, the wingmate) in memory only: no stored
// preference is written, so a window that is narrowed and widened again gets its own layout back (mobStop reads them again).
function mobStart(){
  mq$('wrap').classList.remove('dash');
  mq$('hud').setAttribute('data-wall',''); mq$('mzoom').hidden=true;      // the top line is a wall the map lays out around; no zoom stack on a phone
  if(!QUIET){ QUIET=true; document.body.classList.add('quiet'); }       // the full HUD and its key are not on a phone
  MATE.hidden=false; mateShow(true);
  mobObserve(); ['panel','side','today','quests','mate','legend'].forEach(id=>hintScrub(mq$(id)));
  mobSafe(); mobVV(); mobSync();
}
function mobStop(){
  const B=document.body, r=document.documentElement;
  B.classList.remove('m-over','m-cover','m-pick','pad','m-peek','m-full','m-comp'); delete B.dataset.tab;
  mq$('hud').removeAttribute('data-wall'); mq$('mzoom').hidden=false; MOB_CMAP=false;
  mq$('panel').classList.remove('full'); mq$('side').classList.remove('tview','pick');
  ['--vvh','--vvt','--kb','--P'].forEach(v=>r.style.removeProperty(v));
  r.classList.remove('kb'); SHEET_H=0; MP.on=false; FIT_HOLD=false; MOB_P=0;
  let d=null, q=null, m=null; try{ d=localStorage.getItem('dash'); q=localStorage.getItem('quiet'); m=localStorage.getItem('mate'); }catch(e){}
  const dash=d==null?innerWidth>=1280:d==='on';
  mq$('wrap').classList.toggle('dash',dash); if(dash) renderDash();
  QUIET=q!=='0'; B.classList.toggle('quiet',QUIET);
  mateShow(m!=='off');
}
function mobFlip(){ if(MOB) mobStart(); else mobStop(); fit(); }
// the buttons the markup carries
mq$('tabs').addEventListener('click',e=>{ const b=e.target.closest&&e.target.closest('button[data-tab]'); if(b&&MOB) mtab(b.dataset.tab); });
function mobNewTask(){ if(!MOB) return; const t=mobAim(); MOB_CMAP=document.body.dataset.tab==='map'; openComposer(t); }   // the map's + Task: a sheet over the map, aimed at the planet in view
mq$('mfab').addEventListener('click',mobNewTask);
// the zoom column (a phone and a tablet): + and - are the = and - keys, the ring recenters like C, and a long press on it resets like 0
const ZOOM_STEP=1.25;
function zoomBy(k){ setZoom(ZOOM*k); }
function recenter(){ camOff.x=0; camOff.y=0; }
mq$('mz-in').addEventListener('click',()=>zoomBy(ZOOM_STEP));
mq$('mz-out').addEventListener('click',()=>zoomBy(1/ZOOM_STEP));
{ const c=mq$('mz-c'); let t=0, held=false;
  const stop=()=>{ clearTimeout(t); t=0; };
  c.addEventListener('pointerdown',()=>{ held=false; stop(); t=setTimeout(()=>{ t=0; held=true; CM.haptic('tap'); zoomHome(); },PRESS_MS); });
  ['pointerup','pointercancel','pointerleave'].forEach(ev=>c.addEventListener(ev,stop));
  c.addEventListener('click',()=>{ if(held){ held=false; return; } recenter(); });
  c.addEventListener('contextmenu',e=>e.preventDefault()); }
mq$('mmore').addEventListener('click',()=>{ if(MOB) mobMore(); });
mq$('scrim').addEventListener('click',()=>{ if(Date.now()<suppressClickUntil) return; msheetClose(); });   // the click that ends the long press which opened the sheet lands here, not on the canvas
mq$('msheet-x').addEventListener('click',()=>{ if(Date.now()<suppressClickUntil) return; msheetClose(); });
mq$('msheet').addEventListener('keydown',e=>{ if(e.key==='Escape'&&mq$('msheet').classList.contains('on')){ e.preventDefault(); e.stopPropagation(); msheetClose(); } });
mq$('mclose').addEventListener('click',()=>{ if(MOB) mobCloseSheet(); });
mq$('sback').addEventListener('click',()=>{ if(MOB) threadBack(); });
mq$('cpick').addEventListener('click',()=>{ if(!MOB) return; const a=document.activeElement; if(a&&a.blur) a.blur(); mq$('side').classList.add('pick'); mobSync(); });
document.querySelector('#pickbar .pickx').addEventListener('click',()=>{ if(MOB){ mq$('side').classList.remove('pick'); mobSync(); } });
mq$('cadv').addEventListener('click',()=>{ if(!MOB) return;
  const c=mq$('cadv'), on=!c.classList.contains('open'); c.classList.toggle('open',on); c.setAttribute('aria-expanded',String(on)); });
// opening a thread from Today remembers the way back; the folder chip and the map button fly there, so show the map
mq$('today').addEventListener('click',e=>{ if(MOB&&e.target.closest&&e.target.closest('[data-tid]')) MOB_RET='today'; },true);
mq$('side').addEventListener('click',e=>{ if(MOB&&e.target.closest&&e.target.closest('[data-tact="fly"],[data-tact="map"]')) setTimeout(()=>mtab('map'),0); });
// the card's grip: pull up for full, down for half, down again to close, tap to flip
{ const g=mq$('pgrip'); let st=null;
  const full=on=>{ MP.full=on; mobSync(); };
  g.addEventListener('pointerdown',e=>{ if(!MOB||MOB_LAND) return; st={y:e.clientY,t:Date.now(),done:false}; try{ g.setPointerCapture(e.pointerId); }catch(x){} });
  g.addEventListener('pointermove',e=>{ if(!st||st.done) return; const dy=e.clientY-st.y;
    if(dy<-32){ st.done=true; full(true); } else if(dy>32){ st.done=true; if(MP.full) full(false); else mobCloseSheet(); } });
  g.addEventListener('pointerup',e=>{ const s=st; st=null; if(!s||s.done) return; if(Math.abs(e.clientY-s.y)<10&&Date.now()-s.t<500) full(!MP.full); });
  g.addEventListener('pointercancel',()=>{ st=null; });
  g.addEventListener('click',e=>{ if(e.detail===0&&MOB&&!MOB_LAND) full(!MP.full); });   // Enter or Space on the focused grip (a tap or a drag has its own handlers)
}
// the keyboard: the visual viewport says what is left; no canvas resize while a field has focus
if(window.visualViewport){ visualViewport.addEventListener('resize',mobVV); visualViewport.addEventListener('scroll',mobVV); }
addEventListener('resize',()=>{ if(MOB){ mobVV(); mobSafe(); mobSync(); } });
addEventListener('focusin',e=>{ if(!MOB) return; const t=e.target; if(!(t&&/^(INPUT|TEXTAREA)$/.test(t.tagName))) return; FIT_HOLD=true;
  setTimeout(()=>{ if(document.activeElement===t&&t.scrollIntoView) t.scrollIntoView({block:'center',inline:'nearest'}); },250); });
addEventListener('focusout',()=>{ if(!MOB) return; setTimeout(()=>{ if(!typing()){ FIT_HOLD=false; if(FIT_PEND){ FIT_PEND=false; fit(); } } },150); });
setInterval(()=>{ if(MOB){ mobSafe(); mobSync(); } },1000);
setView(VIEW);
MOB_BOOTED=true; if(MOB) mobStart();
CM.onInit.forEach(f=>{ try{ f(); }catch(e){ console.error('CM.onInit', e); } });   // the ui/*.js lanes start here, once
</script></body></html>"""
HTML = HTML.replace("__TACCSS__", TAC_CSS, 1)


def script_json(value):
    """JSON safe inside a script element, including HTML and JS line separators."""
    return (clean_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            .replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def write_page(path, text, stamp):
    """Replace the page whole, so the bridge (which serves the world inlined in it) never reads
    half a page, and stamp its mtime with the moment its facts were read: the bridge compares that
    with its own renders. Written in place when its folder takes no new file."""
    real = os.path.realpath(path)
    data = clean_text(text).encode("utf-8")
    try:
        mode = os.stat(real).st_mode & 0o777
    except OSError:
        mask = os.umask(0)
        os.umask(mask)
        mode = 0o666 & ~mask
    try:
        _districts.write_atomic(real, data, mode)
    except PermissionError:
        with open(real, "wb") as handle:
            handle.write(data)
    os.utime(real, (stamp, stamp))


if "--json" in sys.argv:
    json.dump(WORLD, sys.stdout)
    sys.exit(0)

FONT_FILE = os.path.join(HERE, "fonts", "Silkscreen-Regular.ttf")     # OFL 1.1: fonts/OFL.txt
# the vector look's faces (OFL 1.1: fonts/OFL-BarlowCondensed.txt, fonts/OFL-ShareTechMono.txt). Share Tech
# Mono has a Reserved Font Name, so it ships as the unmodified file; Barlow is a subset (Latin + punctuation).
VECTOR_FONTS = (
    ("Barlow Condensed", 500, "BarlowCondensed-Medium.woff2", "woff2"),
    ("Barlow Condensed", 600, "BarlowCondensed-SemiBold.woff2", "woff2"),
    ("Share Tech Mono", 400, "ShareTechMono-Regular.ttf", "truetype"),
)


def live_port(environ=None):
    """The port the bridge was told (BRIDGE_PORT, which `serve --port N` and `render --port N`
    set), else 7428. A value that is not a port is ignored."""
    raw = (os.environ if environ is None else environ).get("BRIDGE_PORT", "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and 1 <= int(raw) <= 65535 else 7428


def live_url(environ=None):
    """Where the live map answers for this render. A static copy of the page links here."""
    return f"http://127.0.0.1:{live_port(environ)}/"


def serve_args(environ=None):
    """The arguments that start the map this page links to, for the static page's hint."""
    port = live_port(environ)
    return "serve ROOT" + ("" if port == 7428 else f" --port {port}")


def font_face(path=FONT_FILE):
    """The display face as an @font-face with a data URI, so the page stays one file that works
    offline. A missing file (an install without fonts/) leaves the fallback stack in charge."""
    try:
        with open(path, "rb") as handle:
            data = base64.b64encode(handle.read()).decode("ascii")
    except OSError:
        return ""
    return ('@font-face{font-family:"Silkscreen";font-style:normal;font-weight:400;font-display:block;'
            f'src:url(data:font/ttf;base64,{data}) format("truetype")}}')


def vector_faces(fonts=VECTOR_FONTS, folder=os.path.join(HERE, "fonts")):
    """The vector look's faces as data: @font-face rules; a missing file is skipped (its fallback stack
    takes over)."""
    rules = []
    for family, weight, name, fmt in fonts:
        try:
            with open(os.path.join(folder, name), "rb") as handle:
                data = base64.b64encode(handle.read()).decode("ascii")
        except OSError:
            continue
        mime = "font/woff2" if fmt == "woff2" else "font/ttf"
        rules.append(f'@font-face{{font-family:"{family}";font-style:normal;font-weight:{weight};font-display:swap;'
                     f'src:url(data:{mime};base64,{data}) format("{fmt}")}}')
    return "".join(rules)


# themes/*.json: the packs inlined, the stylesheet turned into variables, one rule per pack
PACKS = _themepacks.load_dir(os.path.join(HERE, "themes"), lambda msg: print(f"warn: {msg}", file=sys.stderr))
THEME = (_themepacks.resolve_theme(CFG["theme"], PACKS)
         or ("classic" if "classic" in PACKS else next(iter(PACKS), "classic")))
if PACKS and THEME not in (CFG["theme"], _themepacks.THEME_ALIASES.get(CFG["theme"])):
    print(f"warn: theme {CFG['theme']!r} is not a pack in themes/ ({', '.join(PACKS)}); using {THEME!r}", file=sys.stderr)
# sound and voice (sfx.py): window.cmSfx in its own script ahead of the page script, which calls it only behind a
# guard; a failure leaves the slot empty (a silent page), and the text can never close its own element
import sfx as _sfx
try:
    SFX_JS = re.sub(r"(?i)</script", r"<\\/script", str(_sfx.script() or ""))
except Exception as exc:
    print(f"warn: sound script not built: {exc}", file=sys.stderr)
    SFX_JS = ""
# the seams: the modules' ui/*.js join the main script at /*__CM_UI__*/ (right after the CM registry),
# their ui/*.css the end of the main stylesheet at /*__CM_CSS__*/ (before the theme rewriter, like the rest of that
# sheet: a colour written as rgb() is left as it is), and words.py's table is WORDS. Each goes in first, before every
# other placeholder is filled, and a part that holds a placeholder of its own (__NAME__) is refused: a later replace
# would rewrite it. "</script" (and "</style" in the css) can never close the element it sits in.
UI_JS = ("words", "kinds", "labels", "focus", "chrome", "phone", "workflows2d", "constellations", "a11y", "term")
UI_CSS = ("chrome", "phone", "labels", "workflows2d", "hud3d", "a11y", "term")
PLACEHOLDER = re.compile(r"__[A-Z0-9_]+__")


def seam_text(text, where):
    """`text` as it may go into the page, or SystemExit naming `where` and the placeholder it holds."""
    found = PLACEHOLDER.search(text)
    if found:
        raise SystemExit(f"render-game.py: {where} holds the placeholder {found.group(0)}, which a later step of the "
                         "render would replace; rename it (no __NAME__ in ui/ files or words.py)")
    return text


def ui_part(names, ext, folder=os.path.join(HERE, "ui")):
    """ui/<name><ext> for each name, in that order, joined; a missing file is skipped with a warning (the CM defaults
    stand in for it)."""
    parts = []
    for name in names:
        rel = f"ui/{name}{ext}"
        try:
            with open(os.path.join(folder, name + ext), encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print(f"warn: {rel} not read ({exc.strerror or exc}); the page runs without it", file=sys.stderr)
            continue
        text = seam_text(text, rel)
        text = re.sub(r"(?i)</script", r"<\\/script", text) if ext == ".js" else re.sub(r"(?i)</style", r"<\\/style", text)
        parts.append(f"/* {rel} */\n" + text.rstrip("\n") + "\n")
    return "".join(parts)


def seams(page):
    import words as _words
    words_json = seam_text(script_json(_words.WORDS), "words.py WORDS")
    for marker in ("/*__CM_UI__*/", "/*__CM_CSS__*/", "__WORDS__"):
        if page.count(marker) != 1:
            raise SystemExit(f"render-game.py: the page template must hold {marker} exactly once")
    return (page.replace("/*__CM_UI__*/", ui_part(UI_JS, ".js"), 1)
                .replace("/*__CM_CSS__*/", ui_part(UI_CSS, ".css"), 1)
                .replace("__WORDS__", words_json, 1))


# the 3D view's two scripts (vendor3d.py): three.js and this skin's bundle, named by their digest. The page is told the
# names (V3D); a page the bridge serves loads them from its /vendor/ address, a page on disk from the folder beside it
# (written here, only the missing files), and a one-file page (three_inline) carries both as text/plain holders that
# go in after everything else, so no later replace can touch three's own text. A failure leaves the 3D view off with
# a sentence, never a broken page.
import vendor3d as _vendor3d
SKIN_NAME = str((SKIN or {}).get("name") or CFG.get("skin") or "neutral")
if not _vendor3d.SKIN_RE.match(SKIN_NAME):
    SKIN_NAME = "neutral"
V3D_INLINE = ""
try:
    V3D = _vendor3d.page_refs(HERE, SKIN_NAME, CFG.get("skins_dir") or "", bool(CFG.get("three_inline")))
    if V3D["inline"]:
        V3D_INLINE = _vendor3d.inline_holders(HERE, SKIN_NAME, CFG.get("skins_dir") or "")
    else:
        _vendor3d.write_beside(OUT, SKIN_NAME, HERE, CFG.get("skins_dir") or "")
except (OSError, ValueError) as exc:
    print(f"warn: the 3D view's scripts were not built ({exc}); the map stays 2D", file=sys.stderr)
    V3D, V3D_INLINE = {"error": str(exc)[:200]}, ""

# the world goes in last: a "__GEN__" in some file name must stay a file name (the bridge serves
# the world inlined in this page, so it must read back exactly as rendered)
html = (_themepacks.theme_page(seams(HTML), PACKS)
            .replace("__FONTFACE__", font_face() + vector_faces())
            .replace("__THEME__", THEME)
            .replace("__LIVEURL__", script_json(live_url()))
            .replace("__SERVEARGS__", script_json(serve_args()))
            .replace("__THEMENAMES__", script_json(list(PACKS)))
            .replace("__THEMEALIAS__", script_json({old: new for old, new in _themepacks.THEME_ALIASES.items() if new in PACKS}))
            .replace("__MATENAME__", html.escape(MATE["name"]))
            .replace("__MATEDISABLED__", "" if MATE["available"] else "disabled")
            # the bar holds about twenty letters: the hint is short, the form's tooltip says the rest
            .replace("__MATEHINT__", "Ask the AI chat helper" if MATE["available"] else "AI chat helper: not set up (optional)")
            .replace("__MATETITLE__", "Ask about anything on the map; the assistant answers with claude, codex or gemini"
                     if MATE["available"] else
                     "The assistant needs claude, codex or gemini installed and on your PATH")
            .replace("__SKIN__", script_json(SKIN or {}))
            .replace("__OLDEST__", str(WORLD["oldest"]))
            .replace("__GEN__", html.escape(WORLD["generated"]))
            .replace("__THEMEPACKS__", script_json(PACKS))
            .replace("__SFX__", SFX_JS)
            .replace("__V3D__", script_json(V3D))
            .replace("__WORLD__", script_json(WORLD)))
# three's own text holds "__THREE__" (its devtools hook): it goes in after every placeholder is filled, at the one
# marker the template has before the main script (the first occurrence: the world JSON comes after it)
html = html.replace("__CM3D_INLINE__", V3D_INLINE, 1)
write_page(OUT, html, NOW)
if LOOP:
    _districts.loop_record(_LOOP_STATE, _LOOP_INPUTS, OUT, NOW, _LOOP_CFG)
print(f"{OUT}  ({len(CHARS)} characters, {len(QUESTS)} quests, {len(html)} bytes)")
