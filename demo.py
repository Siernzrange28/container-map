#!/usr/bin/env python3
"""demo.py — the workspaces behind `container-map demo`. Every run builds a fresh folder.

build_live(dest): seven small folders with code, notes, guides, data and an archive. Three are
repos. drive(dest) puts two tasks through the real engine: one retries, passes and merges; another
waits for the person's review. While served, start_running adds a bounded third task so a ship is
visibly working on first open. No model, agent CLI, network or login.

build_static(dest): the older generated workspace (six roots of different ages, a crontab, a
digest, a runner registry) for a static page: what the screenshots show.

Nothing is ever deleted and nothing is written beside this file: dest is a new folder, by default
under ~/.cache/container-map, and the caller says where it is.
"""
import json
import os
import random
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python3"
CLI = os.path.join(HERE, "cli.py")
AGENT = os.path.join(HERE, "demo_agent.py")
VERIFY = "python3 -m unittest -q"
SECTION_VERIFY = '! grep -q "TODO:" README.md'
TODO_TASK = ("Add complete(items, n) to todo.py: it marks the first n open items done and "
             "returns a new list. test_todo.py has the test.")
NOTES_TASK = "Write a welcome section in notes-site."
RUNNING_TASK = "Write a short guide to tiny-tools."
STEP_SECONDS = 180
# what points git at a repository other than the one in its working folder: set by a git hook, a
# rebase --exec or a bisect run, it would make the demo commit into the caller's repo
# (GIT_CONFIG too: it makes `git config` write the file it names, so make_repo's identity,
# signing and hooks settings would land in the caller's own repository's config)
GIT_REPO_VARS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                 "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
                 "GIT_PREFIX", "GIT_QUARANTINE_PATH", "GIT_CONFIG")

NOW = time.time()
DAY = 86400
rnd = random.Random(7)


class DemoError(Exception):
    """Something the demo could not do; the message is what the user reads, verbatim."""


# ---- where it goes ---------------------------------------------------------------------------

def new_folder(root=None, base=None):
    """A fresh, empty folder for one demo. With root: that folder (made if missing, refused when
    it holds anything: the demo never mixes with, or removes, what is already there). Without:
    demo-<stamp>-… under ~/.cache/container-map (or $XDG_CACHE_HOME/container-map)."""
    if root:
        root = os.path.abspath(os.path.expanduser(root))
        if os.path.exists(root) and (not os.path.isdir(root) or os.listdir(root)):
            raise DemoError(f"{root} is not an empty folder; the demo builds its workspace from "
                            f"scratch and never touches what is there: pick a new or empty one")
        os.makedirs(root, exist_ok=True)
        return root
    if base is None:
        cache = os.environ.get("XDG_CACHE_HOME", "")
        cache = cache if os.path.isabs(cache) else os.path.join(os.path.expanduser("~"), ".cache")
        base = os.path.join(cache, "container-map")
    os.makedirs(base, exist_ok=True)
    return os.path.realpath(tempfile.mkdtemp(prefix=time.strftime("demo-%Y%m%d-%H%M%S-"), dir=base))


def verify_line():
    """The verify line of the todo-app thread: `python3 -m unittest -q`, which is what the page
    shows, when the PATH has a python3; else the interpreter running the demo, quoted (a path
    with a space is one word), because a machine with only `python3.12` or a venv's python would
    otherwise fail every attempt with exit 127."""
    return VERIFY if shutil.which("python3") else f"{shlex.quote(PY)} -m unittest -q"


def clean_env(**extra):
    """This process's environment without the variables that point a map somewhere else
    (CONTAINER_*, BRIDGE_*) or git at another repository (GIT_REPO_VARS), plus extra."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CONTAINER_", "BRIDGE_")) and k not in GIT_REPO_VARS}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def write_file(path, text="", mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    if mode:
        os.chmod(path, mode)


# ---- the live demo -----------------------------------------------------------------------------

TODO_PY = '''"""A tiny todo list: an item is a dict with a text and a done flag."""


def add(items, text):
    """Return the list with one more open item at the end."""
    return items + [{"text": text, "done": False}]


def open_items(items):
    return [item for item in items if not item["done"]]
'''
TEST_TODO_PY = '''import unittest

import todo


class TodoTest(unittest.TestCase):
    def test_add_appends_an_open_item(self):
        self.assertEqual(todo.add([], "pay rent"), [{"text": "pay rent", "done": False}])

    def test_open_items_skips_the_done_ones(self):
        items = [{"text": "a", "done": True}, {"text": "b", "done": False}]
        self.assertEqual(todo.open_items(items), [{"text": "b", "done": False}])

    def test_complete_marks_the_first_n_open_items_done(self):
        items = [{"text": name, "done": False} for name in ("a", "b", "c")]
        self.assertEqual([item["done"] for item in todo.complete(items, 2)], [True, True, False])


if __name__ == "__main__":
    unittest.main()
'''
NOTES_README = """# notes-site

A small static site for my notes.

TODO: write the getting started section
"""
NOTES_INDEX = "<!doctype html>\n<title>notes</title>\n<h1>notes</h1>\n<p>Nothing here yet.</p>\n"


def git(repo, *args):
    try:
        done = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60,
                              stdin=subprocess.DEVNULL, env=clean_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DemoError(f"git {' '.join(args)} could not run in {repo}: {exc}")
    if done.returncode != 0:
        raise DemoError(f"git {' '.join(args)} failed in {repo} (exit {done.returncode}): "
                        f"{(done.stderr or done.stdout).strip()}")


def make_repo(path, files):
    """A git repo on branch main with one commit holding files; its own identity, no signing and
    no hooks, so the machine's git setup cannot get in the way of a demo."""
    for name, text in files.items():
        write_file(os.path.join(path, name), text)
    git(path, "init", "-q")
    git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    for key, value in (("user.name", "container-map demo"), ("user.email", "demo@localhost"),
                       ("commit.gpgsign", "false"), ("core.hooksPath", "/dev/null")):
        git(path, "config", key, value)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "initial commit")


def live_config(dest):
    side = os.path.join(dest, ".container-map")
    return {
        "version": 1, "name": "DEMO", "work_root": dest, "tmux_session": "none",
        "processes": False, "demo": True, "theme": "classic",
        "planets": [{"name": name, "path": os.path.join(dest, name), "kind": kind, "desc": desc}
                    for name, kind, desc in LIVE_FOLDERS],
        "crontab_file": os.path.join(side, "crontab.txt"), "cron_syslog_file": "",
        "detect_runners": False, "task_scope": False, "wingmate": {"off": True},
        "task_runs_json": os.path.join(side, "task-runs.jsonl"),
        "rulings_json": os.path.join(side, "rulings.json"),
        "synced_dir": os.path.join(side, "synced"),
        "runners": {"demo-agent": {
            "cmd": [PY, AGENT], "check_cmd": [PY, AGENT, "--check"],
            "label": "Demo agent (scripted)", "max_minutes": 20, "check_minutes": 2,
            "desc": "a script, not a model: it works on the sample tasks and checks its folder",
        }},
    }


LIVE_FOLDERS = (
    ("todo-app", "code", "A tiny task list app; its change is already merged"),
    ("notes-site", "code", "A small website; its change is waiting for your review"),
    ("notebook", "knowledge", "Notes and ideas in plain text"),
    ("handbook", "reference", "Guides for a small team"),
    ("sample-data", "data", "Small tables to explore"),
    ("archive", "archive", "Older work kept for later"),
    ("tiny-tools", "code", "Small scripts; watch an agent work here"),
)


def build_live(dest):
    """Fill the empty folder dest: seven varied folders, three git repos, an empty crontab. Returns the config's
    path (dest/.container-map/container.json, where `serve dest` looks)."""
    make_repo(os.path.join(dest, "todo-app"), {
        "todo.py": TODO_PY, "test_todo.py": TEST_TODO_PY, ".gitignore": "__pycache__/\n"})
    make_repo(os.path.join(dest, "notes-site"), {
        "README.md": NOTES_README, "index.html": NOTES_INDEX})
    make_repo(os.path.join(dest, "tiny-tools"), {
        "report.py": 'print("Folders ready to explore")\n',
        "README.md": "# Tiny tools\n\nSmall scripts for everyday work.\n"})
    samples = {
        "notebook": {"ideas.md": "# Ideas\n\nA reading list, a sketchbook, a place to start.\n",
                     "week.md": "# This week\n\nTry the map. Write one note. Make one small thing.\n"},
        "handbook": {"welcome.md": "# Welcome\n\nStart small. Ask for a change. Read it before accepting.\n",
                     "how-we-work.md": "# How we work\n\nKeep notes and check changes together.\n"},
        "sample-data": {"readings.csv": "day,temperature\nMonday,18\nTuesday,21\nWednesday,19\n",
                        "README.md": "# Sample data\n\nThree invented readings for a chart.\n"},
        "archive": {"old-plan.md": "# Earlier plan\n\nAn old idea kept for reference.\n",
                    "draft.txt": "An earlier draft of a welcome message.\n"},
    }
    for folder, files in samples.items():
        for name, text in files.items():
            write_file(os.path.join(dest, folder, name), text)
            if folder == "archive":
                stamp = time.time() - 120 * DAY
                os.utime(os.path.join(dest, folder, name), (stamp, stamp))
    side = os.path.join(dest, ".container-map")
    write_file(os.path.join(side, "crontab.txt"))
    write_file(os.path.join(side, "seats.json"), json.dumps(
        {"version": 1, "session": "none", "seats": [], "machines": {}}) + "\n")
    write_file(os.path.join(side, "areas.json"), json.dumps({"version": 1, "areas": {}}) + "\n")
    path = os.path.join(side, "container.json")
    write_file(path, json.dumps(live_config(dest), indent=2) + "\n")
    return path


def thread(dest, config, *args):
    """One `cli.py thread …` call on the demo's store: (exit code, its JSON object). The streamed
    log of a --wait goes to our stderr as it happens."""
    argv = [PY, CLI, "thread", *args, "--json", "--root", dest, "--config", config]
    try:
        done = subprocess.run(argv, stdout=subprocess.PIPE, text=True, cwd=HERE, env=clean_env(),
                              stdin=subprocess.DEVNULL, timeout=STEP_SECONDS)
    except subprocess.TimeoutExpired:
        raise DemoError(f"thread {args[0]} took more than {STEP_SECONDS} s; stopped waiting")
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise DemoError(f"thread {args[0]} (exit {done.returncode}) printed no JSON: "
                        f"{done.stdout.strip()[:300]!r}")
    if done.returncode != 0 and not data.get("thread"):
        raise DemoError(f"thread {args[0]} failed (exit {done.returncode}): {data.get('error')}")
    return done.returncode, data


def expect(ok, what, data):
    if not ok:
        t = data.get("thread") or {}
        raise DemoError(f"{what} (status {t.get('status')}, verify {t.get('verify')}, check "
                        f"{t.get('check')}, merge blockers {t.get('merge_blockers')}); "
                        f"`container-map thread show {t.get('id')}` reads its log")


def settle(dest, config, tid, timeout=60):
    """The thread's summary once its agent is done (a thread that needs no retry settles alone)."""
    end = time.time() + timeout
    while True:
        _, data = thread(dest, config, "show", tid)
        if data["thread"]["status"] not in ("new", "running", "checking", "stopping"):
            return data["thread"]
        if time.time() > end:
            raise DemoError(f"{tid} is still {data['thread']['status']} after {timeout} s")
        time.sleep(0.5)


def say(text):
    print(f"container-map demo: {text}", flush=True)


def drive(dest, config):
    """Two threads through the real engine. Returns (merged thread, thread left in review)."""
    say("thread on notes-site: write the README's missing section (verify: no TODO line left)")
    _, data = thread(dest, config, "new", os.path.join(dest, "notes-site"), NOTES_TASK,
                     "--agent", "demo-agent", "--verify", SECTION_VERIFY)
    notes_id = data["id"]
    verify = verify_line()
    say(f"thread on todo-app: add complete(); verify `{verify}`, 2 retries. "
        "Its first attempt has a bug.")
    rc, data = thread(dest, config, "new", os.path.join(dest, "todo-app"), TODO_TASK,
                      "--agent", "demo-agent", "--verify", verify, "--retries", "2", "--wait")
    tid, t = data["id"], data["thread"]
    expect(rc == 0 and t["status"] == "review" and t["attempts"] == 2,
           "the todo-app thread should pass its verify on the second attempt", data)
    say(f"{tid}: the verify failed on attempt 1 and passed on attempt 2; a check run reviews it")
    rc, data = thread(dest, config, "check", tid, "--agent", "demo-agent", "--wait")
    expect(rc == 0 and data["verdict"] == "PASS", "the check should end in PASS", data)
    rc, data = thread(dest, config, "merge", tid)
    expect(rc == 0 and data["merged"], "the todo-app thread should merge", data)
    say(f"{tid}: check PASS, merged into main")
    merged = data["thread"]
    review = settle(dest, config, notes_id)
    expect(review["status"] == "review", "the notes-site thread should stop in review",
           {"thread": review})
    say(f"{notes_id}: in review. Merge it yourself in the page (open the thread, merge).")
    return merged, review



def start_running(dest, config):
    """A real, bounded third task, only while serving the demo. The caller stops it on exit."""
    _, data = thread(dest, config, "new", os.path.join(dest, "tiny-tools"), RUNNING_TASK,
                     "--agent", "demo-agent", "--verify", "test -f report.py")
    say("tiny-tools: an agent is working. Open its ship to watch; Ctrl-C stops it too.")
    return data["id"]


# ---- the static demo (the old one) ---------------------------------------------------------------

def put(path, text="", age_days=0.0, size=None):
    if size is not None:
        text = ("x" * 63 + "\n") * max(1, size // 64)
    write_file(path, text)
    stamp = NOW - age_days * DAY
    os.utime(path, (stamp, stamp))


def tree(root, spec, age):
    """spec: {name: spec|int} — int = a file of that many bytes; dict = a directory."""
    for name, sub in spec.items():
        p = os.path.join(root, name)
        if isinstance(sub, dict):
            os.makedirs(p, exist_ok=True)
            tree(p, sub, age)
            stamp = NOW - age * DAY
            os.utime(p, (stamp, stamp))
        else:
            put(p, size=sub, age_days=age + rnd.random() * 3)


def build_static(dest):
    """Fill the empty folder dest with the generated workspace and demo.json, a config that points
    the renderer at it (page: dest/demo.html). Everything the page shows is still COUNTED from
    these files; only the files are invented. Returns demo.json's path."""
    rnd.seed(7)
    ws = os.path.join(dest, "workspace")
    os.makedirs(ws)
    # ---- a workspace: six roots of different kinds and ages ----
    tree(os.path.join(ws, "api"), {
        "CLAUDE.md": 900, "README.md": 2200, "package.json": 700,
        "src": {"server.ts": 8000, "routes.ts": 4200, "db.ts": 3100, "auth.ts": 2700},
        "tests": {"server.test.ts": 3000, "routes.test.ts": 2100},
        "node_modules": {f"pkg-{i}": {"index.js": 1500, "package.json": 400} for i in range(40)},
        ".logs": {},
    }, age=0.3)
    tree(os.path.join(ws, "notes"), {
        "CLAUDE.md": 600, "receipts.md": 100,
        "journal": {f"2026-08-{d:02d}.md": 1200 for d in range(1, 29)},
        "projects": {"launch-plan.md": 5400, "budget.md": 2100, "ideas.md": 900},
        ".logs": {f"watch-{n}.cron.log": 20000
                  for n in ("prices", "news", "mail", "backup", "sync")},
    }, age=1.0)
    tree(os.path.join(ws, "data"), {
        "exports": {f"export-{i}.csv": 40000 for i in range(12)},
        "cache": {f"blob-{i}.bin": 90000 for i in range(30)},
        "inbox": {"queue.json": 800},
    }, age=6.0)
    tree(os.path.join(ws, "scripts"), {
        "AGENTS.md": 500, "watch-prices": 1800, "watch-news": 1400, "watch-mail": 2200,
        "backup": 3000, "sync": 1200, "_attic": {"old-router": 4000},
    }, age=2.0)
    tree(os.path.join(ws, "infra"),
         {"docker-compose.yml": 1100, "Caddyfile": 300, ".env.example": 200}, age=40.0)
    tree(os.path.join(ws, "old-site"), {
        "index.html": 9000, "style.css": 2000, "img": {f"p{i}.png": 60000 for i in range(8)},
    }, age=210.0)
    put(os.path.join(ws, "stray-export.pptx"), size=22000, age_days=33)
    # ---- crontab: real lines, real log paths, some with no receipt, some stale ----
    logs = os.path.join(ws, "notes", ".logs")
    sc = os.path.join(ws, "scripts")
    put(os.path.join(dest, "crontab.txt"), "\n".join([
        f"*/5 * * * * {sc}/watch-prices >> {logs}/watch-prices.cron.log 2>&1",
        f"10,40 * * * * {sc}/watch-news >> {logs}/watch-news.cron.log 2>&1",
        f"*/15 * * * * {sc}/watch-mail >> {logs}/watch-mail.cron.log 2>&1",
        f"30 3 * * * {sc}/backup >> {logs}/backup.cron.log 2>&1",
        f"* * * * * {sc}/sync >/dev/null 2>&1",
        f"0 9 * * 1 {sc}/_attic/old-router >> {logs}/router.cron.log 2>&1",
    ]) + "\n")
    for n, age in (("watch-prices", 0.002), ("watch-news", 0.01), ("watch-mail", 0.005),
                   ("backup", 0.4), ("router", 19)):
        put(os.path.join(logs, f"{n}.cron.log"), size=3000, age_days=age)
    # ---- digest + fleet: receipts and parked decisions ----
    put(os.path.join(ws, "notes", "receipts.md"), "\n".join([
        "# receipts", "- 2026-09-01 09:00 deploy: api v0.4.2 shipped, 0 errors in 24h",
        "- 2026-09-02 07:00 backup: 1.2G bundle written, verified",
        "- 2026-09-03 18:00 prices-watch: BTC regime flat, no action",
        "- 2026-08-20 12:00 old-site: still on the old host — awaiting owner decision on "
        "decommission",
        "- 2026-09-04 08:30 mail-watch: 2 new customer replies filed, needs a human to answer "
        "one",
        "- 2026-09-04 21:00 sync: 3 conflict files in data/inbox — owner's call which copy wins",
    ]) + "\n")
    put(os.path.join(ws, "notes", "claims.md"),
        "# claims\n- 2026-08-28 CLAIM=builder api/tests: flaky auth test — human call whether "
        "to skip it\n")
    # ---- runners + dispatch reports ----
    put(os.path.join(dest, "runners.json"), json.dumps({
        "builder": {"cmd": "builder-cli", "desc": "cheap grind seat — mechanical edits"},
        "reviewer": {"cmd": "reviewer-cli", "desc": "adversarial review seat"},
        "scout": {"cmd": "scout-cli",
                  "desc": "🔴 DOWN since 2026-08-20 — provider returned HTTP 429 account suspended"},
    }, indent=1))
    reports = [("20260901", "builder"), ("20260902", "builder"), ("20260902", "reviewer"),
               ("20260903", "builder"), ("20260904", "reviewer"), ("20260818", "scout")]
    for i, (day, who) in enumerate(reports):
        put(os.path.join(dest, "dispatch", f"d-{day}-1200{i:02d}-{who}.md"),
            f"# {who} report\ntask done\n", age_days=4 - i)
    # ---- relay channels ----
    frame = ("---\nfrom: %s\nto: claude\ntime: 2026-09-04 20:00\nsubject: heartbeat\n---\n"
             "alive\n===END===\n")
    put(os.path.join(dest, "relay", "claude", "inbox.md"), frame % "builder", age_days=0.2)
    put(os.path.join(dest, "relay", "builder", "inbox.md"), frame % "claude", age_days=0.5)
    # ---- the board: audit items ----
    attic = os.path.join(ws, "_attic")
    items = [
        {"id": "old-site-decommission", "area": "old-site", "path": os.path.join(ws, "old-site"),
         "finding": "old-site has not changed in 210 days and still hosts nothing",
         "evidence": "11 files, newest 210d ago", "verdict": "ASK", "who": "owner",
         "effort": "small"},
        {"id": "api-node-modules", "area": "api", "path": os.path.join(ws, "api", "node_modules"),
         "finding": "node_modules is 80 of api's files and is rebuildable from package.json",
         "evidence": "40 packages, package.json present", "verdict": "MOVE",
         "target": os.path.join(attic, "api-node_modules"), "who": "agent", "effort": "small",
         "command": f"mkdir -p {attic} && mv {ws}/api/node_modules {attic}/api-node_modules && "
                    f"echo 'api/node_modules\\tmoved' >> {attic}/MANIFEST.tsv"},
        {"id": "stray-pptx", "area": "workspace", "path": os.path.join(ws, "stray-export.pptx"),
         "finding": "a 22 KB presentation sits loose at the workspace root",
         "evidence": "stray-export.pptx, 33d old", "verdict": "MOVE",
         "target": os.path.join(ws, "notes", "projects"), "who": "agent", "effort": "small",
         "command": f"mv {ws}/stray-export.pptx {ws}/notes/projects/"},
        {"id": "logs-in-notes", "area": "notes", "path": logs,
         "finding": "5 cron logs land inside notes/, a knowledge base",
         "evidence": "5 .cron.log files, written today", "verdict": "ASK", "who": "owner",
         "effort": "medium"},
    ]
    put(os.path.join(dest, "tasks.json"), json.dumps({
        "version": 1, "generated": "2026-09-05T00:00:00+00:00", "source": "demo audit",
        "items": items}, indent=1))
    # ---- the config that points the renderer at all of it ----
    cfg = {
        "version": 1, "name": "DEMO", "work_root": ws, "extra_areas": {}, "tmux_session": "",
        "processes": False, "crontab_file": os.path.join(dest, "crontab.txt"),
        "digest": os.path.join(ws, "notes", "receipts.md"),
        "fleet": os.path.join(ws, "notes", "claims.md"),
        "decision_markers": ["awaiting owner", "needs a human", "human call", "owner's call"],
        "dispatch_dir": os.path.join(dest, "dispatch"),
        "runners_json": os.path.join(dest, "runners.json"),
        "relay_dir": os.path.join(dest, "relay"), "hub_channel": "claude", "reads_jsonl": "",
        "tags_md": "", "machines": {"demo": ["claude"]},
        "tasks_json": os.path.join(dest, "tasks.json"), "rounds_json": "",
        "synced_dir": "", "file_roots": [], "out": os.path.join(dest, "demo.html"), "demo": True,
    }
    path = os.path.join(dest, "demo.json")
    write_file(path, json.dumps(cfg, indent=1))
    return path


if __name__ == "__main__":
    import cli
    sys.exit(cli.main(["demo", *sys.argv[1:]]))
