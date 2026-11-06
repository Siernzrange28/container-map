#!/usr/bin/env python3
"""test_zero.py — the stranger gate for slice 1 (zero-config run).

A fresh directory with a few subfolders, a git repo, code and notes — and NO config file — must
render in under 10 s with nothing but python3, and the page must contain nothing that is not in
that directory: no path, seat, host or name from the machine the tool was built on.

Run: python3 test_zero.py
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MARKERS = os.path.join(HERE, "leak-markers.txt")      # the owner's machine words; never exported


def leak_patterns():
    """Regexes for what must not reach a page: the words in leak-markers.txt (private tree only;
    public-export.sh scans every shipped file for the same words), this machine's home directory,
    and the names of the owner's receipt files."""
    found = []
    try:
        with open(MARKERS, encoding="utf-8") as handle:
            found = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    except OSError:
        pass
    home = os.path.expanduser("~")
    if len(home) > 6:
        found.append(re.escape(home))
    return found + ["DIGEST", "FLEET"]


# the owner's own tools: the improvement loop that edits this tool (with its test and the verbs that
# drive it) and a screenshot helper for the build machine's browser. The export leaves them out, and
# nothing it ships may import or name them.
OWNER_ONLY = ("selfimprove.py", "test_selfimprove.py", "snap.mjs", "verbs/improve-map.json",
              "verbs/improve-loop.json", "verbs/gates.json")


def leak_in(html):
    """(pattern, surrounding text) of the first leak in `html`, or None."""
    for pattern in leak_patterns():
        found = re.search(pattern, html)
        if found:
            return pattern, html[max(0, found.start() - 80):found.end() + 80]
    return None


def clean_env(home):
    """A stranger's environment: their HOME, their crontab (a shim printing two lines, so the
    build machine's crontab never reaches the page), no tmux."""
    shim = os.path.join(home, "bin")
    os.makedirs(shim, exist_ok=True)
    crontab = os.path.join(shim, "crontab")
    if not os.path.exists(crontab):
        with open(crontab, "w") as f:
            f.write("#!/bin/sh\nprintf '%s\\n' '0 9 * * 1 /usr/bin/true # weekly-check' '*/30 * * * * /usr/bin/true >> /tmp/x.log 2>&1'\n")
        os.chmod(crontab, 0o755)
    env = {"PATH": shim + ":" + os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home, "LANG": "C.UTF-8"}
    return env


class ZeroConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cm-zero-")
        self.root = os.path.join(self.tmp, "stranger-work")
        for d in ("alpha-app", "notes", "scratch"):
            os.makedirs(os.path.join(self.root, d))
        with open(os.path.join(self.root, "alpha-app", "main.py"), "w") as f:
            f.write("print('hi')\n")
        with open(os.path.join(self.root, "alpha-app", "README.md"), "w") as f:
            f.write("# alpha\nDECIDE: keep the old parser or drop it (2026-01-01)\n")
        with open(os.path.join(self.root, "notes", "todo.md"), "w") as f:
            f.write("- buy milk\n")
        with open(os.path.join(self.root, "scratch", "blob.bin"), "wb") as f:
            f.write(os.urandom(2048))
        if shutil.which("git"):
            subprocess.run(["git", "init", "-q"], cwd=os.path.join(self.root, "alpha-app"), check=False,
                           env=clean_env(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_renders_fast_from_nothing_and_leaks_nothing(self):
        env = clean_env(self.tmp)
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, os.path.join(HERE, "render-game.py"), "--root", self.root],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)
        dt = time.monotonic() - t0
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertLess(dt, 10.0, f"render took {dt:.1f}s")
        out = os.path.join(self.root, ".container-map", "game.html")
        self.assertTrue(os.path.isfile(out), "page goes to ROOT/.container-map/game.html")
        with open(out, encoding="utf-8", errors="replace") as handle:
            html = handle.read().replace(HERE, "<TOOL>")     # the tool may name its own dir
        self.assertIsNone(leak_in(html), "page leaks something from the build machine")
        r = subprocess.run([sys.executable, os.path.join(HERE, "render-game.py"), "--root", self.root, "--json"],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        world = json.loads(r.stdout)
        areas = sorted(c["name"] for c in world["chars"] if c.get("kind") == "area")
        self.assertEqual(areas, ["alpha-app", "notes", "scratch"])
        self.assertEqual(world["name"], "STRANGER-WORK")
        self.assertFalse([c for c in world["chars"] if c.get("kind") == "seat"], "no tmux → no seats")
        self.assertFalse(world.get("tasks", {}).get("items"), "no audit file → no tasks")
        q = [x for x in world["quests"] if "old parser" in x["text"]]
        self.assertEqual(len(q), 1, "the DECIDE: line in alpha-app/README.md is a parked decision")
        self.assertTrue(q[0]["src"].endswith("alpha-app/README.md:2"), q[0]["src"])
        self.assertGreater(q[0]["days"], 200, "aged by the date in the line (2026-01-01)")
        self.assertEqual(world["oldest"], q[0]["days"])

    def test_own_config_is_not_consulted_when_a_root_is_given(self):
        env = clean_env(self.tmp)
        r = subprocess.run([sys.executable, os.path.join(HERE, "config.py"), "--root", self.root],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout)
        self.assertEqual(cfg["_path"], "")
        self.assertEqual(cfg["work_root"], self.root)
        self.assertEqual(cfg["areas_json"], "")
        self.assertEqual(cfg["seats_json"], "")
        self.assertEqual(cfg["tmux_session"], "")
        for k in ("digest", "fleet", "dispatch_dir", "runners_json", "tasks_json"):
            self.assertEqual(cfg[k], "", k)

    def test_sidecar_config_under_root_is_honoured(self):
        side = os.path.join(self.root, ".container-map")
        os.makedirs(side, exist_ok=True)
        with open(os.path.join(side, "container.json"), "w") as f:
            json.dump({"version": 1, "name": "MINE", "extra_areas": {"extra": os.path.join(self.tmp, "extra")},
                       "runners": {"shell": {"cmd": ["/bin/sh", "-c", "cat"], "cwd": "item"}}}, f)
        os.makedirs(os.path.join(self.tmp, "extra"))
        env = clean_env(self.tmp)
        r = subprocess.run([sys.executable, os.path.join(HERE, "render-game.py"), "--root", self.root, "--json"],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        world = json.loads(r.stdout)
        self.assertEqual(world["name"], "MINE")
        self.assertEqual(world["dispatchable"], ["shell"], "sidecar runners are offered on the board")
        vd = os.path.join(side, "verbs")
        os.makedirs(vd, exist_ok=True)
        with open(os.path.join(vd, "hello.json"), "w") as f:
            json.dump({"name": "hello", "label": "hello", "applies": ["dir"], "cmd": ["echo", "{path}"]}, f)
        with open(os.path.join(vd, "LOOP.md"), "w") as f:
            f.write("- fixture local pass: refused\n")
        r = subprocess.run([sys.executable, os.path.join(HERE, "render-game.py"), "--root", self.root, "--json"],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)
        world = json.loads(r.stdout)
        self.assertEqual([v["name"] for v in world["verbs"]["items"]], ["hello"], "sidecar verbs are offered")
        self.assertEqual(world["verbs"]["dir"], vd)
        self.assertEqual(world["verbs"]["loop"], "- fixture local pass: refused")
        self.assertIn("extra", [c["name"] for c in world["chars"] if c.get("kind") == "area"])

    def test_cli_help_and_config(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py")], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertIn("ZERO-CONFIG", r.stdout)
        r = subprocess.run([sys.executable, os.path.join(HERE, "cli.py"), "config", self.root],
                           capture_output=True, text=True, env=clean_env(self.tmp), timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["work_root"], self.root)


class GenericDefaults(unittest.TestCase):
    """What a stranger gets with no file: nothing that belongs to one person's folders, tools or habits."""

    def test_the_built_in_defaults_name_no_extra_planet_script_folder_or_priority(self):
        import config
        for key in ("extra_areas", "script_dirs", "today"):
            self.assertEqual(config.DEFAULTS[key], {}, key)
        self.assertEqual(config.DEFAULTS["quest_priority"], [])

    def test_script_dirs_is_the_only_source_of_a_script_folder_planet(self):
        import districts
        areas = {"area:scripts": "/x/scripts", "area:app": "/x/app"}
        self.assertEqual(districts.script_homes({}, areas), {})
        self.assertEqual(districts.script_homes({"script_dirs": {}}, areas), {})
        homes = districts.script_homes({"script_dirs": {"/x/bin": "scripts", "/y/bin": "nothere"}}, areas)
        self.assertEqual(homes, {"/x/bin": "area:scripts"})          # a planet the map does not draw is skipped
        self.assertEqual(districts.script_home("/x/bin/job.sh", homes), "area:scripts")
        self.assertIsNone(districts.script_home("/x/binary/job.sh", homes))

    def test_the_shipped_examples_offer_only_generic_today_sources(self):
        with open(os.path.join(HERE, "container.sources-example.json"), encoding="utf-8") as handle:
            example = json.load(handle)
        self.assertEqual(sorted(example["today"]), ["git_dirs", "todo_md"])
        self.assertEqual(example["script_dirs"], {})
        text = json.dumps(example)
        for key in ("applied_csv", "jobs_rx", "drafts_dir", "commitments_md", "checkins_jsonl", "night_marker",
                    "ledger_jsonl", "plan_md"):
            self.assertNotIn(key, text)

    def test_a_markdown_checklist_is_a_todo_list(self):
        import today
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# list\n- [ ] write the **readme**\n* [x] ship it\n- [X] tag it\nplain line\n- [ ] \n")
            missing = []
            got = today.todo(path, missing)
            self.assertEqual([(i["what"], i["state"]) for i in got["items"]],
                             [("write the readme", "due"), ("ship it", "ok"), ("tag it", "ok")])
            self.assertEqual((got["summary"], got["kind"], missing), ("1 open · 2 done", "todo", []))
            self.assertEqual(len({i["id"] for i in got["items"]}), 3)
            missing = []
            today.todo(os.path.join(tmp, "absent.md"), missing)
            self.assertEqual(len(missing), 1)

    def test_git_dirs_lists_the_commits_made_today(self):
        import today
        from datetime import date
        if not shutil.which("git"):
            self.skipTest("no git")
        with tempfile.TemporaryDirectory() as tmp:
            repo = os.path.join(tmp, "app")
            os.makedirs(repo)
            env = dict(os.environ, GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@x", GIT_COMMITTER_NAME="a",
                       GIT_COMMITTER_EMAIL="a@x", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
            for argv in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "first change"]):
                subprocess.run(["git", "-C", repo, *argv], check=True, env=env, capture_output=True)
            missing = []
            rows = today.git_done([repo, os.path.join(tmp, "not-a-repo")], date.today(), missing)
            self.assertEqual([(r["title"], r["repo"]) for r in rows], [("first change", "app")])
            self.assertEqual(len(missing), 1)
            self.assertIn("not-a-repo", missing[0])
            cfg = {"work_root": tmp, "today": {"git_dirs": [repo]}}
            built = today.build(cfg, [], [], {})
            self.assertTrue(built["configured"])
            self.assertEqual([d["title"] for d in built["done"]], ["first change"])
            self.assertEqual(today.build({"work_root": tmp}, [], [], {})["done"], [])      # unnamed = not drawn


class RenderMemory(unittest.TestCase):
    """--cached (the bridge's renders) and --loop-render (the render loop) state the facts a
    one-shot render states, and the loop leaves the page of an unchanged tree alone."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cm-memory-")
        self.root = os.path.join(self.tmp, "work")
        for rel in ("app/src/main.py", "app/README.md", "notes/a/b/c.md", "notes/top.md",
                    "data/x.csv"):
            path = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write(rel + "\n")
        old = time.time() - 40 * 86400
        for folder, dirs, files in os.walk(self.root, topdown=False):
            for name in files:
                os.utime(os.path.join(folder, name), (old, old))
            os.utime(folder, (old, old))
        side = os.path.join(self.root, ".container-map")
        os.makedirs(side)
        crontab = os.path.join(self.tmp, "crontab")
        open(crontab, "w").close()
        with open(os.path.join(side, "container.json"), "w") as handle:
            json.dump({"version": 1, "name": "MEMORY", "crontab_file": crontab,
                       "cron_syslog_file": "", "processes": False, "tmux_session": "none",
                       "decision_scan": False}, handle)
        self.env = clean_env(self.tmp)
        # The render loop skips a render whose inputs did not change, and the process table is one
        # of them: any agent run that starts or ends on the machine running the tests changed it
        # and made the loop render when the test expected it not to. The fixture has its own ps.
        shim = os.path.join(self.tmp, "psbin")
        os.makedirs(shim)
        with open(os.path.join(shim, "ps"), "w") as handle:
            handle.write("#!/bin/sh\nprintf '%s\\n' '    1 /sbin/init' '  100 /usr/sbin/cron -f'\n")
        os.chmod(os.path.join(shim, "ps"), 0o755)
        self.env["PATH"] = shim + ":" + self.env["PATH"]
        self.page = os.path.join(side, "game.html")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, *args):
        r = subprocess.run([sys.executable, os.path.join(HERE, "render-game.py"), "--root",
                            self.root, *args], capture_output=True, text=True, env=self.env,
                           cwd=self.tmp, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return r.stdout

    def facts(self, *args):
        world = json.loads(self.render("--json", *args))
        return {k: v for k, v in world.items() if k not in ("generated", "rendered", "inflight")}   # the clock's stamps

    def test_cached_renders_state_the_facts_of_a_one_shot_render(self):
        one = self.facts()
        self.assertEqual(self.facts("--cached"), one)           # fills the memory
        self.assertTrue(os.listdir(os.path.join(self.root, ".container-map", "render-cache")))
        self.assertEqual(self.facts("--cached"), one)           # reads it
        with open(os.path.join(self.root, "notes", "a", "b", "new.md"), "w") as handle:
            handle.write("new\n")
        changed = self.facts("--cached")
        self.assertNotEqual(changed, one)
        self.assertEqual(changed, self.facts())

    def test_the_loop_leaves_an_unchanged_page_alone_and_renders_a_change(self):
        self.assertIn("characters", self.render("--loop-render"))
        with open(self.page, "rb") as handle:
            body = handle.read()
        # the first render wrote its own history row; the next two quiet cycles must both skip
        # (the process table is the fixture's, so nothing on the host can move the inputs)
        said, quiet = [], 0
        for _ in range(3):
            said.append(self.render("--loop-render").strip()[-60:])
            quiet = quiet + 1 if "unchanged" in said[-1] else 0
            if quiet == 2:
                break
        self.assertEqual(quiet, 2, said)
        with open(self.page, "rb") as handle:
            self.assertEqual(handle.read(), body)                # touched, not rewritten
        self.assertGreater(os.path.getmtime(self.page), time.time() - 60)
        with open(os.path.join(self.root, "data", "y.csv"), "w") as handle:
            handle.write("y\n")
        self.assertIn("characters", self.render("--loop-render"))
        with open(self.page, encoding="utf-8") as handle:
            world = json.loads(re.search(r"(?m)^const W = (.*);$", handle.read()).group(1))
        data = next(c for c in world["chars"] if c.get("kind") == "area" and c["name"] == "data")
        self.assertEqual(data["files"], 2)


EXPORT = os.path.join(HERE, "public-export.sh")


def _pip_ready():
    probe = subprocess.run([sys.executable, "-c", "import pip, setuptools"], capture_output=True)
    return probe.returncode == 0


def _setuptools_ready():
    probe = subprocess.run([sys.executable, "-c", "import setuptools"], capture_output=True)
    return probe.returncode == 0


def _pyproject():
    with open(os.path.join(HERE, "pyproject.toml"), encoding="utf-8") as handle:
        return handle.read()


def _cli_constants():
    """VERSION and MIN_PYTHON as cli.py declares them, read without importing it."""
    with open(os.path.join(HERE, "cli.py"), encoding="utf-8") as handle:
        text = handle.read()
    version = re.search(r'(?m)^VERSION = "([^"]+)"$', text)
    floor = re.search(r"(?m)^MIN_PYTHON = \((\d+), (\d+)\)$", text)
    return (version.group(1) if version else None,
            (int(floor.group(1)), int(floor.group(2))) if floor else None)


def _licence_id():
    """LICENCE as setup.py declares it (the one place the SPDX id is typed)."""
    with open(os.path.join(HERE, "setup.py"), encoding="utf-8") as handle:
        found = re.search(r'(?m)^LICENCE = "([^"]+)"', handle.read())
    return found.group(1) if found else None


def _plain_marker():
    """One plain word the export scan must refuse: the first alphabetic line of leak-markers.txt
    (the word itself is not spelled here, a shipped file may not), else a stand-in assembled
    from halves for a tree whose scan has the words built in."""
    try:
        with open(MARKERS, encoding="utf-8") as handle:
            for line in handle:
                if re.fullmatch(r"[A-Za-z]+", line.strip()):
                    return line.strip()
    except OSError:
        pass
    return "zep" + "h"


def _copy_tree(dest):
    """The tree under test, minus VCS and build leftovers, so a build never writes into it."""
    shutil.copytree(HERE, dest, ignore=shutil.ignore_patterns(
        ".git", ".claude", "__pycache__", "*.egg-info", "build", "dist", ".container-map", "*.pyc"))


class Packaging(unittest.TestCase):
    """What pip and an sdist depend on: one version, one Python floor, data files beside the
    modules, a licence that says what the metadata says. Builds run on a copy of the tree."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cm-pack-")
        cls.tree = os.path.join(cls.tmp, "tree")
        _copy_tree(cls.tree)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setup_py(self, *args):
        r = subprocess.run([sys.executable, "setup.py", "-q", *args], cwd=self.tree,
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])

    def test_version_and_python_floor_are_declared_once_in_cli(self):
        version, floor = _cli_constants()
        self.assertRegex(version or "", r"^\d+\.\d+\.\d+$", "cli.py: VERSION = \"x.y.z\"")
        self.assertEqual(floor, (3, 10), "cli.py: MIN_PYTHON = (3, 10)")
        text = _pyproject()
        self.assertNotRegex(text, r"(?m)^version\s*=", "pyproject must not repeat the version")
        self.assertNotRegex(text, r"(?m)^requires-python\s*=", "pyproject repeats the floor")
        self.assertRegex(text, r'(?m)^dynamic = \[.*"version".*\]')
        self.assertRegex(text, r'(?m)^dynamic = \[.*"requires-python".*\]')

    @unittest.skipUnless(_setuptools_ready(), "setuptools is not installed")
    def test_metadata_carries_the_version_and_the_floor(self):
        version, floor = _cli_constants()
        eggs = os.path.join(self.tmp, "eggs")
        os.makedirs(eggs)
        self.setup_py("egg_info", "-e", eggs)
        info = [d for d in os.listdir(eggs) if d.endswith(".egg-info")]
        self.assertEqual(len(info), 1, info)
        with open(os.path.join(eggs, info[0], "PKG-INFO"), encoding="utf-8") as handle:
            meta = handle.read()
        self.assertIn(f"\nVersion: {version}\n", meta)
        self.assertIn(f"\nRequires-Python: >={floor[0]}.{floor[1]}\n", meta)
        # setuptools 77+ writes an SPDX expression, older ones the free-text field
        self.assertRegex(meta, rf"(?m)^License(-Expression)?: {re.escape(_licence_id())}$")
        for name in ("LICENSE", "NOTICE"):
            self.assertIn(f"\nLicense-File: {name}\n", meta)

    @unittest.skipUnless(_setuptools_ready(), "setuptools is not installed")
    def test_every_data_folder_a_page_reads_lands_beside_the_modules(self):
        lib = os.path.join(self.tmp, "lib")
        self.setup_py("build_py", "-d", lib)
        for rel in ("cli.py", "render-game.py", "world.sh", "container.example.json",
                    "themes/classic.json", "skins/neutral.json", "fonts/Silkscreen-Regular.ttf",
                    "fonts/OFL.txt", "verbs/size.json", "verbs/git-status.json", "words.py", "workflows.py",
                    "meta.py", "container_map_mcp.py", "playbooks/meta.md", "vendor3d.py", "ui/words.js", "ui/a11y.js", "ui/chrome.css", "ui/hud3d.css",
                    "world3d/core.js", "world3d/hud.js", "vendor/README.txt"):
            self.assertTrue(os.path.isfile(os.path.join(lib, rel)), rel)

    @unittest.skipUnless(_setuptools_ready(), "setuptools is not installed")
    def test_an_sdist_carries_what_a_build_needs_and_installs(self):
        out = os.path.join(self.tmp, "sdist")
        self.setup_py("sdist", "-d", out)
        archives = os.listdir(out)
        version, _ = _cli_constants()
        self.assertEqual(len(archives), 1, archives)
        self.assertTrue(archives[0].endswith(".tar.gz"), archives)
        with tarfile.open(os.path.join(out, archives[0])) as archive:
            top = archive.getnames()[0].split("/")[0]
            names = {n.split("/", 1)[1] for n in archive.getnames() if "/" in n}
            unpacked = os.path.join(self.tmp, "unpacked")
            os.makedirs(unpacked)
            try:
                archive.extractall(unpacked, filter="data")
            except TypeError:                        # python without extraction filters
                archive.extractall(unpacked)
        self.assertIn(version, top)
        want = {"setup.py", "pyproject.toml", "README.md", "LICENSE", "NOTICE", "render-game.py",
                "world.sh", "demo.sh", "door.sh", "container.example.json",
                "container.sources-example.json", "testenv.py", "test_cli.py"}
        for folder, kinds in (("themes", (".json",)), ("skins", (".json",)),
                              ("fonts", (".ttf", ".txt")), ("verbs", (".json",)),
                              ("docs", (".md", ".png")), ("ui", (".js", ".css")), ("world3d", (".js",)),
                              ("vendor", (".js", ".txt")), ("tests/fixtures", (".json",))):
            want |= {f"{folder}/{n}" for n in os.listdir(os.path.join(self.tree, folder))
                     if n.endswith(kinds)}
        mods = re.findall(r'(?m)^\s+"([a-z_]+)",$', _pyproject())
        want |= {m + ".py" for m in mods}
        self.assertEqual(sorted(want - names), [], "missing from the sdist")
        if not _pip_ready():
            self.skipTest("pip is not usable here: the install from the sdist was not tried")
        target = os.path.join(self.tmp, "from-sdist")
        r = subprocess.run([sys.executable, "-m", "pip", "install", os.path.join(unpacked, top),
                            "--target", target, "--no-build-isolation", "--no-index",
                            "--disable-pip-version-check", "-q"], capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
        for rel in ("cli.py", "render-game.py", "themes/classic.json", "skins/neutral.json",
                    "fonts/OFL.txt", "verbs/size.json", "words.py", "ui/words.js", "world3d/core.js"):
            self.assertTrue(os.path.isfile(os.path.join(target, rel)), rel)

    def test_the_install_guide_matches_the_release_and_the_composer(self):
        with open(os.path.join(HERE, "docs", "INSTALL.md"), encoding="utf-8") as handle:
            guide = handle.read()
        with open(os.path.join(HERE, "render-game.py"), encoding="utf-8") as handle:
            page = handle.read()
        version, floor = _cli_constants()
        self.assertIn(f"unzip container-map-{version}.zip", guide)
        self.assertIn(f"python3 -m zipfile -e container-map-{version}.zip", guide)
        self.assertIn(f"Python {floor[0]}.{floor[1]} or newer", guide)
        # PEP 668: --user installs fail on an OS-managed Python; a venv or pipx is the way
        self.assertIsNone(re.search(r"(?m)^\s*(python3 -m )?pip3? install --user", guide),
                          "the guide must not tell anyone to run pip install --user")
        for word in ("externally-managed-environment", "python3 -m venv", "pipx install"):
            self.assertIn(word, guide)
        # the walkthrough names the composer's own labels and the thread panel's own buttons
        for label in ("Folder", "Agent", "Model", "Safe copy", "Setup", "Test that must pass", "Retries"):
            self.assertRegex(page, rf'<label for="c[a-z]+">{label}</label>')
            self.assertIn(f"**{label}**", guide)
        for button in ("Changes", "Set aside", "Check it", "Merge into", "Mark done"):
            self.assertIn(button, page)
            self.assertIn(button, guide)

    def test_licence_notice_and_metadata_agree(self):
        licence = _licence_id()
        self.assertEqual(licence, "MIT", "setup.py: LICENCE = \"<SPDX id>\"")
        text = _pyproject()
        # the table form is deprecated (rejected by setuptools after 2027-02-18) and the string
        # form needs setuptools 77: the field is dynamic, filled in by setup.py for either
        self.assertNotRegex(text, r"(?m)^license\s*=", "pyproject must not repeat the licence")
        self.assertRegex(text, r'(?m)^dynamic = \[.*"license".*\]')
        self.assertNotIn("License ::", text, "licence classifiers are deprecated")
        holder = re.search(r'(?m)^authors = \[\{name = "([^"]+)"\}\]$', text)
        self.assertTrue(holder, "pyproject: authors = [{name = \"…\"}] names the copyright holder")
        with open(os.path.join(HERE, "LICENSE"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[0], "MIT License")
        self.assertRegex(lines[2], r"^Copyright \(c\) 20\d\d " + re.escape(holder.group(1)) + "$")
        self.assertIn("Permission is hereby granted, free of charge", "\n".join(lines))
        with open(os.path.join(HERE, "README.md"), encoding="utf-8") as handle:
            readme = handle.read()
        self.assertRegex(readme, rf"(?m)^{licence}\. See \[LICENSE\]\(LICENSE\) and "
                                 r"\[NOTICE\]\(NOTICE\)\.$")
        with open(os.path.join(HERE, "NOTICE"), encoding="utf-8") as handle:
            notice = handle.read()
        self.assertNotIn(licence, notice.split("\n\n")[0], "NOTICE points at LICENSE, no id")
        for word in ("LICENSE", "Silkscreen", "SIL Open Font License", "fonts/OFL.txt",
                     "fonts/Silkscreen-Regular.ttf"):
            self.assertIn(word, notice)
        self.assertTrue(os.path.isfile(os.path.join(HERE, "fonts", "OFL.txt")))


@unittest.skipUnless(os.path.isfile(EXPORT) and shutil.which("bash"),
                     "only the private repo carries public-export.sh")
class ExportedTree(unittest.TestCase):
    """The stranger's install, from the exported tree only: doctor, init, render; pip too."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cm-export-")
        cls.tree = os.path.join(cls.tmp, "tree")
        r = subprocess.run(["bash", EXPORT, cls.tree], capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise AssertionError(f"public-export.sh exit {r.returncode}:\n{r.stdout}{r.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def stranger_root(self, name):
        root = os.path.join(self.tmp, name)
        for d in ("app", "notes"):
            os.makedirs(os.path.join(root, d), exist_ok=True)
        with open(os.path.join(root, "notes", "plan.md"), "w") as f:
            f.write("TODO: pick a name\n")
        return root

    @staticmethod
    def snapshot(folder):
        """Every file and folder under folder as (size, mtime): what a demo must leave alone."""
        seen = {}
        for base, dirs, files in os.walk(folder):
            for name in dirs + files:
                full = os.path.join(base, name)
                st = os.stat(full)
                seen[os.path.relpath(full, folder)] = (st.st_size, st.st_mtime_ns)
        return seen

    def assert_demo_loop(self, argv, home, tool):
        """`demo --no-serve` through argv in a stranger's HOME: one merged thread that needed two
        verify attempts and passed its check, one thread waiting in review, and nothing written
        into the installed tool."""
        env = dict(clean_env(home), PYTHONDONTWRITEBYTECODE="1")
        before = self.snapshot(tool)
        r = subprocess.run([*argv, "--no-serve"], capture_output=True, text=True, env=env,
                           cwd=home, timeout=240)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
        self.assertEqual(self.snapshot(tool), before, "the demo wrote beside the installed code")
        cache = os.path.join(home, ".cache", "container-map")
        (name,) = os.listdir(cache)
        r = subprocess.run([sys.executable, os.path.join(tool, "cli.py"), "thread", "list",
                            "--json", "--root", os.path.join(cache, name)],
                           capture_output=True, text=True, env=env, cwd=home, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
        threads = json.loads(r.stdout)["threads"]
        self.assertEqual(sorted(t["status"] for t in threads), ["merged", "review"], threads)
        merged = next(t for t in threads if t["status"] == "merged")
        self.assertEqual((merged["attempts"], merged["check"]["verdict"]), (2, "PASS"))

    def assert_page_clean(self, page, tool):
        with open(page, encoding="utf-8", errors="replace") as f:
            html = f.read().replace(tool, "<TOOL>")
        self.assertIsNone(leak_in(html), "page leaks something from the build machine")

    def test_the_exported_tests_run_from_the_exported_tree(self):
        # every test module that imports testenv (or anything else of the tree) finds it there
        home = os.path.join(self.tmp, "home-t")
        r = subprocess.run([sys.executable, "-m", "unittest", "-q", "test_cli.Version",
                            "test_cli.Arguments", "test_wingmate"],
                           capture_output=True, text=True, env=clean_env(home), cwd=self.tree,
                           timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
        self.assertTrue(os.path.isfile(os.path.join(self.tree, "testenv.py")))

    def test_every_test_file_that_can_ship_ships_and_each_one_imports_from_the_tree(self):
        # a test file left out of ALLOW is fine only when it names the build machine or is one of
        # the owner's own tools (OWNER_ONLY); a clean one that is missing was forgotten
        # (test_processes.py and test_worktree_hygiene.py were)
        private = sorted(n for n in os.listdir(HERE) if re.fullmatch(r"test_[a-z_]+\.py", n))
        shipped = sorted(n for n in os.listdir(self.tree) if re.fullmatch(r"test_[a-z_]+\.py", n))
        # the test of the owner's private skin is the one left-out file that names neither the machine nor an
        # owner's tool: the export's IP scan would refuse it, so it stays home (it checks itself that it names
        # what the scan refuses; this shipped file does not spell those words)
        private_skin_test = {"test_skin_private.py"}
        for name in private:
            if name in shipped:
                self.assertNotIn(name, OWNER_ONLY, f"{name} is the owner's and must not ship")
                continue
            if name in OWNER_ONLY:
                continue
            with open(os.path.join(HERE, name), encoding="utf-8") as handle:
                text = handle.read()
            if name in private_skin_test:
                continue
            self.assertIsNotNone(leak_in(text),
                                 f"{name} is clean but not in the export's ALLOW list")
        for name in ("test_processes.py", "test_worktree_hygiene.py"):
            self.assertIn(name, shipped)
        modules = ", ".join(n[:-3] for n in shipped)
        home = os.path.join(self.tmp, "home-i")
        r = subprocess.run([sys.executable, "-c", f"import {modules}"], capture_output=True,
                           text=True, env=clean_env(home), cwd=self.tree, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])

    def scratch_export(self, name, plant):
        """Export a copy of the private tree after `plant(tree)` changed it; (exit code, stdout,
        stderr). The copy carries its own public-export.sh, so the script under test is the one
        in the tree, not a stand-in."""
        tree = os.path.join(self.tmp, name + "-src")
        _copy_tree(tree)
        plant(tree)
        r = subprocess.run(["bash", os.path.join(tree, "public-export.sh"),
                            os.path.join(self.tmp, name + "-out")], capture_output=True, text=True,
                           timeout=120)
        return r.returncode, r.stdout, r.stderr

    def test_the_export_scan_reads_docs_and_the_tests_too(self):
        # the scan once skipped docs/ and six test files, which is how a shipped doc and the
        # tests named the build machine; plant one of its words in each place and expect a stop
        word = _plain_marker()
        for n, rel in enumerate(("docs/INSTALL.md", "docs/AGENT-API.md", "test_game.py",
                                 "test_bridge.py", "test_zero.py", "setup.py")):
            def plant(tree, rel=rel):
                with open(os.path.join(tree, rel), "a", encoding="utf-8") as handle:
                    handle.write(f"\n# built on {word}\n")
            with self.subTest(rel=rel):
                code, out, err = self.scratch_export(f"scan{n}", plant)
                self.assertEqual(code, 2, out + err)
                self.assertIn(rel, out)

    def test_an_image_nobody_reviewed_stops_the_export(self):
        # a text scan cannot read pixels: a screenshot that is not in reviewed-images.txt stops it
        def plant(tree):
            with open(os.path.join(tree, "docs", "new-shot.png"), "wb") as handle:
                handle.write(b"\x89PNG\r\n\x1a\n" + os.urandom(32))
        code, out, err = self.scratch_export("image", plant)
        self.assertEqual(code, 2, out + err)
        self.assertIn("image not reviewed: docs/new-shot.png", err)
        # and the images the tree does ship are the ones somebody opened
        with open(os.path.join(HERE, "reviewed-images.txt"), encoding="utf-8") as handle:
            listed = handle.read()
        for folder, _dirs, files in os.walk(self.tree):
            for name in files:
                if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    with open(os.path.join(folder, name), "rb") as handle:
                        self.assertIn(hashlib.sha256(handle.read()).hexdigest(), listed, name)

    def test_the_demo_the_screenshots_come_from_uses_neutral_names(self):
        with open(os.path.join(self.tree, "README.md"), encoding="utf-8") as handle:
            self.assertIn("docs/demo.png", handle.read())
        with open(os.path.join(self.tree, "demo.py"), encoding="utf-8") as handle:
            demo = handle.read()
        for word in ("recruiter", "DIGEST", "FLEET"):
            self.assertNotIn(word, demo, "the demo workspace uses neutral names")

    # modules other changes still rewrite: their comments are scrubbed by the same pass once those land
    HISTORY_PENDING = ("world3d/", "skins/", "themepacks.py", "demo.py", "constellations.py", "ui/chrome.", "ui/constellations.js",
                       "ui/hud3d.css", "vendor/")

    def test_no_owner_names_or_build_history_in_shipped_files(self):
        """One person's model nickname, files, folders and tools, and the build's own lane and spec numbers,
        stay out of everything that ships (the words are split so this file does not name them)."""
        hard = re.compile("|".join((r"\bS" r"ol\b", r"CONTAINER" r"\.md", r"~/" r"tools", r"/tmp/cm" r"3d", r"GPT-6\.1",
                                    r"\.local/" r"bin", r"area:~" r"tools")))
        history = re.compile("|".join((r"\bSP" r"EC [A-Z0-9]", r"\bSP" r"EC §", r"\blane B\d", r"\bB\d{1,2}-[TK]\d", r"SEE" r"D\+",
                                       r"LANE-" r"RULES", r"TRACE" r"ABILITY", r"checker's rep" r"ro")))
        hits = []
        for base, _dirs, files in os.walk(self.tree):
            for name in sorted(files):
                path = os.path.join(base, name)
                rel = os.path.relpath(path, self.tree)
                if not name.endswith((".py", ".js", ".css", ".md", ".json", ".sh", ".html", ".toml", ".txt")):
                    continue
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        pending = rel.startswith(self.HISTORY_PENDING)
                        if hard.search(line) and not rel.startswith("vendor/"):
                            hits.append(f"{rel}:{number}: {line.strip()[:100]}")
                        elif history.search(line) and not pending:
                            hits.append(f"{rel}:{number}: {line.strip()[:100]}")
        self.assertEqual(hits, [])

    def test_ships_one_skin(self):
        # the neutral skin and its 3D craft (every skin's 3D file falls back to it); the owner's skins stay home
        self.assertEqual(sorted(os.listdir(os.path.join(self.tree, "skins"))), ["neutral-3d.json", "neutral-planets3d", "neutral-planets3d.json", "neutral.json"])

    def test_ships_no_owner_files(self):
        for rel in ("verbs/sync-conflicts.json", "verbs/LOOP.md", "verbs/_candidates",
                    "verbs/_attic", "container.json", "rounds.py", "seats.json", "areas.json",
                    "legacy-ids.json", *OWNER_ONLY):
            self.assertFalse(os.path.exists(os.path.join(self.tree, rel)), rel)
        for rel in ("cli.py", "setup.py", "MANIFEST.in", "LICENSE", "NOTICE", "fonts/OFL.txt",
                    "render-game.py", "docs/INSTALL.md", "verbs/size.json",
                    "container.example.json", "container.sources-example.json", "themepacks.py",
                    "themes/classic.json", "themes/earthen.json", "themes/blueprint.json", "skins/neutral.json",
                    "themes/synthwave.json", "themes/phosphor.json", "words.py", "workflows.py", "vendor3d.py",
                    "meta.py", "container_map_mcp.py", "playbooks/meta.md", "ui/words.js", "ui/chrome.css", "world3d/core.js", "vendor/README.txt",
                    "tests/fixtures/workflow-demo.json", "test_seams.py"):
            self.assertTrue(os.path.exists(os.path.join(self.tree, rel)), rel)
        with open(os.path.join(self.tree, "pyproject.toml"), encoding="utf-8") as f:
            text = f.read()
        mods = re.findall(r'^\s+"([a-z_]+)",$', text, re.M)
        self.assertIn("cli", mods)
        for mod in mods:
            self.assertTrue(os.path.isfile(os.path.join(self.tree, mod + ".py")), mod)

    def test_nothing_shipped_imports_or_names_an_owner_only_tool(self):
        # the improvement loop, its verbs and the screenshot helper stay home, so a shipped file
        # that imports or points at one is a broken import or a dead link for a stranger
        words = [re.escape(rel) for rel in OWNER_ONLY]
        words += [r"\bimport selfimprove\b", r"\bfrom selfimprove\b", r"\btest_selfimprove\b",
                  r'"selfimprove"']
        pattern = re.compile("|".join(words))
        hits = []
        for base, _dirs, files in os.walk(self.tree):
            for name in sorted(files):
                if name == "test_zero.py" or not name.endswith(
                        (".py", ".sh", ".md", ".json", ".toml", ".in", ".mjs", ".txt")):
                    continue                  # test_zero.py names them: it is the list
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for number, line in enumerate(handle, 1):
                        if pattern.search(line):
                            hits.append(f"{os.path.relpath(path, self.tree)}:{number}: {line.strip()[:80]}")
        self.assertEqual(hits, [])
        with open(os.path.join(self.tree, "MANIFEST.in"), encoding="utf-8") as handle:
            self.assertNotIn("snap.mjs", handle.read())

    def test_stranger_install_from_the_export(self):
        # env -i PATH=$PATH HOME=<tmp>, plus the crontab shim so this machine's crontab stays out
        home = os.path.join(self.tmp, "home-a")
        root = self.stranger_root("stranger-root")
        env = clean_env(home)
        cli = os.path.join(self.tree, "cli.py")
        # doctor names the folder it is asked about (a stranger's ~/work may not exist: exit 1)
        r = subprocess.run([sys.executable, cli, "doctor", root], capture_output=True, text=True,
                           env=env, cwd=home, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = subprocess.run([sys.executable, cli, "doctor"], capture_output=True, text=True,
                           env=env, cwd=home, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(os.path.join(home, "work"), r.stdout)
        self.assertIn("does not exist", r.stdout)
        r = subprocess.run([sys.executable, cli, "init", root], capture_output=True, text=True,
                           env=env, cwd=home, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        conf = os.path.join(root, ".container-map", "container.json")
        with open(conf, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["work_root"], root)
        r = subprocess.run([sys.executable, cli, "render", root], capture_output=True, text=True,
                           env=env, cwd=home, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
        page = os.path.join(root, ".container-map", "game.html")
        self.assertTrue(os.path.isfile(page))
        self.assert_page_clean(page, self.tree)

    def test_owner_group_labels_never_appear_in_any_shipped_file(self):
        own = os.path.join(HERE, "container.json")
        if not os.path.isfile(own):
            self.skipTest("the owner's config does not ship")
        with open(own, encoding="utf-8") as handle:
            labels = [s["name"] for s in json.load(handle).get("constellations", [])
                      if isinstance(s, dict) and " " in s.get("name", "")]
        self.assertTrue(labels)
        for base, dirs, names in os.walk(self.tree):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in names:
                path = os.path.join(base, name)
                with open(path, "rb") as handle:
                    raw = handle.read()
                if b"\0" in raw[:8192]:
                    continue
                text = raw.decode("utf-8", "replace")
                for label in labels:
                    self.assertNotIn(label, text, os.path.relpath(path, self.tree))

    def test_demo_runs_from_the_export(self):
        self.assert_demo_loop(["bash", os.path.join(self.tree, "demo.sh")],
                              os.path.join(self.tmp, "home-demo"), self.tree)

    def test_zip(self):
        dist = os.path.join(self.tmp, "dist")           # the zip never lands in the tree under test
        r = subprocess.run(["bash", EXPORT, os.path.join(self.tmp, "tree-zip"), "--zip"],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, EXPORT_DIST=dist))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        path = [x for x in r.stdout.splitlines() if x.startswith("zip: ")][-1][5:]
        self.assertEqual(os.path.dirname(path), dist)
        version = _cli_constants()[0]
        self.assertEqual(os.path.basename(path), f"container-map-{version}.zip")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            top = f"container-map-{version}/"
            self.assertTrue(all(n.startswith(top) for n in names), names[:5])
            for rel in ("cli.py", "render-game.py", "docs/INSTALL.md", "setup.py", "demo.py",
                        "demo_agent.py", "LICENSE", "NOTICE", "MANIFEST.in"):
                self.assertIn(top + rel, names)
            self.assertNotIn(top + ".public-export", names)
            self.assertTrue(archive.getinfo(top + "demo.sh").external_attr >> 16 & 0o100)

    @unittest.skipUnless(_pip_ready(), "pip and setuptools are not installed")
    def test_pip_install_from_the_export(self):
        target = os.path.join(self.tmp, "pip-target")
        r = subprocess.run([sys.executable, "-m", "pip", "install", ".", "--target", target,
                            "--no-build-isolation", "--no-index", "--disable-pip-version-check",
                            "-q"], capture_output=True, text=True, cwd=self.tree, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-3000:])
        for name in ("cli.py", "tasks.py", "render-game.py", "world.sh", "container.example.json",
                     "themepacks.py", "themes/classic.json", "themes/phosphor.json", "skins/neutral.json",
                     "demo.py", "demo_agent.py", "verbs/size.json", "fonts/Silkscreen-Regular.ttf",
                     "fonts/OFL.txt", "words.py", "workflows.py", "vendor3d.py", "meta.py", "container_map_mcp.py", "playbooks/meta.md",
                     "ui/words.js", "ui/a11y.css", "world3d/core.js", "vendor/README.txt"):
            self.assertTrue(os.path.isfile(os.path.join(target, name)), name)
        home = os.path.join(self.tmp, "home-b")
        root = self.stranger_root("pip-root")
        env = clean_env(home)
        r = subprocess.run([sys.executable, os.path.join(target, "cli.py"), "render", root],
                           capture_output=True, text=True, env=env, cwd=home, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr[-2000:])
        self.assert_page_clean(os.path.join(root, ".container-map", "game.html"), target)
        with open(os.path.join(root, ".container-map", "game.html"), encoding="utf-8") as f:
            self.assertIn("const THEME_PACKS = {\"classic\"", f.read())      # the packs travelled with the install
        r = subprocess.run([sys.executable, os.path.join(target, "render-game.py"), "--root", root,
                            "--json"], capture_output=True, text=True, env=env, cwd=home,
                           timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertEqual(json.loads(r.stdout)["name"], "PIP-ROOT")
        # the demo from the install (a site-packages stand-in): the scripted agent travelled with
        # it, the loop runs, and the install is not written to
        self.assert_demo_loop([sys.executable, os.path.join(target, "cli.py"), "demo"],
                              os.path.join(self.tmp, "home-pip-demo"), target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
