#!/usr/bin/env python3
"""test_tasks.py — the T3-style task loop end to end, with a fake agent: create → run (detached
supervisor) → exit receipt → diff → merge / discard / done, plus stop, the time cap, follow-ups,
log polling and the refusals. Real git, real processes, temp dirs only."""
import contextlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testenv  # noqa: E402  (first: clears what steers the tool; hermetic_repo for fixtures)
import tasks as T

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = sys.argv[1]
    prompt = sys.stdin.read()
    open(os.path.join(os.environ.get("FAKE_OUT", "."), "prompt-seen.txt"), "a").write(prompt + "\\n===\\n")
    open(os.path.join(os.environ.get("FAKE_OUT", "."), "argv-seen.txt"), "a").write(json.dumps([sys.argv[1:], os.environ.get("FAKE_MODEL")]) + "\\n")
    if mode == "write":
        open("agent-note.txt", "w").write("hello from the agent\\n")
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I wrote agent-note.txt"},
            {"type": "tool_use", "name": "Write", "input": {"file_path": "agent-note.txt"}}]}}))
        print(json.dumps({"type": "result", "subtype": "success", "num_turns": 1, "duration_ms": 1200, "result": "done"}))
    elif mode == "shared":
        open("shared.txt", "w").write("agent version\\n")
        print("changed shared.txt")
    elif mode == "fail":
        print("boom")
        sys.exit(3)
    elif mode == "sleep":
        print("sleeping", flush=True)
        time.sleep(60)
    elif mode == "signed":
        import subprocess
        open("signed.txt", "w").write("x\\n")
        subprocess.run(["git", "add", "signed.txt"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add signed", "-m", "Co-Authored-By: Some Bot <bot@example.invalid>"], check=True)
    elif mode == "orphan":
        import subprocess
        subprocess.Popen(["sleep", "60"])            # left behind in the agent's group, in its folder
        print("left a helper running", flush=True)
        time.sleep(1.5)
    elif mode == "lines":
        for i in range(5):
            print(f"line {i}", flush=True)
    elif mode == "session":
        print(json.dumps({"type": "system", "subtype": "init", "session_id": "sess-" + os.path.basename(os.getcwd())[:8]}))
        open("agent-note.txt", "w").write("hello\\n")
        print(json.dumps({"type": "result", "subtype": "success", "num_turns": 1, "duration_ms": 5, "result": "done"}))
    elif mode in ("leaver", "straggler"):
        import subprocess
        # leaver: a daemon that escapes the process group; straggler: a helper left in the group
        left = subprocess.Popen(["sleep", "300"], start_new_session=mode == "leaver")
        open(os.path.join(os.environ.get("FAKE_OUT", "."), mode + ".pid"), "w").write(str(left.pid))
        open("agent-note.txt", "w").write("hello\\n")
        print("left a daemon behind")
    elif mode in ("ignorer", "builder"):
        os.makedirs(os.path.join("node_modules", "pkg"), exist_ok=True)     # a build byproduct
        open(os.path.join("node_modules", "pkg", "index.js"), "w").write("x\\n")
        if mode == "ignorer":                               # ignored, and nobody can regenerate it
            open("notes.txt", "w").write("my own notes\\n")
            open("tracked-new.txt", "w").write("new\\n")
    elif mode == "mkverifydir":            # from the worktree root: <task>/verify-1.log, a folder
        os.makedirs(os.path.join("..", "verify-1.log"))
        open("agent-note.txt", "w").write("hello\\n")
    elif mode == "slowwriter":
        open("agent-note.txt", "w").write("hello\\n")
        print("wrote it, now resting", flush=True)
        time.sleep(60)
    elif mode == "stubborn":                   # half a line, then takes a while to honour a stop
        import signal
        asked = []
        signal.signal(signal.SIGTERM, lambda *a: asked.append(1))
        print("abc", end="", flush=True)
        end = time.time() + 30
        while time.time() < end and not asked:
            time.sleep(0.05)
        time.sleep(1.5)
        print("def", flush=True)
    elif mode == "tamper":                     # rewrites ../task.json so its own work passes
        record = os.path.join("..", "task.json")
        data = json.load(open(record))
        data["verify"] = "true"
        json.dump(data, open(record, "w"))
        open("agent-note.txt", "w").write("hello\\n")
    elif mode in ("detach", "elsewhere"):      # commits its work, but not on the task's branch
        import subprocess
        open("off-branch.txt", "w").write("work\\n")
        subprocess.run(["git", "checkout", "-q", "--detach"] if mode == "detach"
                       else ["git", "checkout", "-q", "-b", "elsewhere"], check=True)
        subprocess.run(["git", "add", "off-branch.txt"], check=True)
        subprocess.run(["git", "commit", "-q", "-m", "work off the branch"], check=True)
    elif mode == "emit":                       # prints a file verbatim: a checker's whole output
        sys.stdout.write(open(sys.argv[2]).read())
    elif mode == "env":                        # what the map left in the agent's environment
        json.dump(dict(os.environ), open(os.path.join(os.environ.get("FAKE_OUT", "."), "env-agent.json"), "w"))
        open("agent-note.txt", "w").write("hello\\n")
    elif mode == "evilgit":                    # repoints its worktree's .git at a copy of the repository whose config runs a command
        import shutil, subprocess
        common = os.path.realpath(subprocess.run(["git", "rev-parse", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip())
        shutil.copytree(common, "evilgit-copy", symlinks=True)
        mark = os.path.join(os.environ.get("FAKE_OUT", "."), "PWNED")
        script = os.path.abspath("evil-monitor.sh")
        open(script, "w").write("#!/bin/sh\\necho \\"fsmonitor in $(pwd)\\" >> " + mark + "\\nexit 0\\n")
        os.chmod(script, 0o755)
        subprocess.run(["git", "config", "--file", "evilgit-copy/config", "core.fsmonitor", script], check=True)
        open(".git", "w").write("gitdir: " + os.path.abspath("evilgit-copy") + "\\n")
        open("agent-note.txt", "w").write("hello\\n")
    elif mode == "latin1":                     # a text file that is not UTF-8
        open("legacy.txt", "wb").write(b"caf\\xe9 au lait\\n")
    elif mode == "trappass":                   # the time cap's SIGTERM makes it print a PASS and exit 0
        import signal
        def bye(*a):
            print("VERDICT: PASS - answered on the way out", flush=True)
            sys.exit(0)
        signal.signal(signal.SIGTERM, bye)
        time.sleep(60)
    elif mode == "judge":
        print("looked at the diff")
        print("VERDICT: " + ("PASS" if "fail" not in prompt.lower().split("the person adds:")[-1][:40] else "FAIL") + " - because")
''')


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.work = os.path.join(self.root, "work")
        self.store_dir = os.path.join(self.root, "store")
        self.side = os.path.join(self.root, "side")
        for d in (self.work, self.side):
            os.makedirs(d)
        self.fake = os.path.join(self.side, "fake_agent.py")
        with open(self.fake, "w") as handle:
            handle.write(FAKE)
        os.environ["FAKE_OUT"] = self.side
        self.runners = T.valid_runners({
            "writer": {"cmd": [sys.executable, self.fake, "write"], "followup_cmd": [sys.executable, self.fake, "lines"], "max_minutes": 1},
            "shared": {"cmd": [sys.executable, self.fake, "shared"], "max_minutes": 1},
            "failer": {"cmd": [sys.executable, self.fake, "fail"], "max_minutes": 1},
            "sleeper": {"cmd": [sys.executable, self.fake, "sleep"], "max_minutes": 1},
            "capped": {"cmd": [sys.executable, self.fake, "sleep"], "max_minutes": 0.02},
            "signed": {"cmd": [sys.executable, self.fake, "signed"], "max_minutes": 1},
            "orphan": {"cmd": [sys.executable, self.fake, "orphan"], "max_minutes": 1},
            "sessioned": {"cmd": [sys.executable, self.fake, "session"], "followup_cmd": [sys.executable, self.fake, "lines", "--resume", "{session}"],
                          "check_cmd": [sys.executable, self.fake, "judge", "--read-only"], "max_minutes": 1},
            "leaver": {"cmd": [sys.executable, self.fake, "leaver"], "max_minutes": 1},
            "straggler": {"cmd": [sys.executable, self.fake, "straggler"], "max_minutes": 1},
            "ignorer": {"cmd": [sys.executable, self.fake, "ignorer"], "max_minutes": 1},
            "builder": {"cmd": [sys.executable, self.fake, "builder"], "max_minutes": 1},
            "mkverifydir": {"cmd": [sys.executable, self.fake, "mkverifydir"], "max_minutes": 1},
            "slowwriter": {"cmd": [sys.executable, self.fake, "slowwriter"], "max_minutes": 1},
            "slowjudge": {"cmd": [sys.executable, self.fake, "sleep"], "role": "check",
                          "max_minutes": 1},
            "stubborn": {"cmd": [sys.executable, self.fake, "stubborn"], "max_minutes": 1},
            "tamper": {"cmd": [sys.executable, self.fake, "tamper"], "max_minutes": 1},
            "quiet": {"cmd": [sys.executable, self.fake, "lines"], "max_minutes": 1},
            "detacher": {"cmd": [sys.executable, self.fake, "detach"], "max_minutes": 1},
            "switcher": {"cmd": [sys.executable, self.fake, "elsewhere"], "max_minutes": 1},
            "stubjudge": {"cmd": [sys.executable, self.fake, "stubborn"], "role": "check",
                          "max_minutes": 1},
            "evilgit": {"cmd": [sys.executable, self.fake, "evilgit"], "max_minutes": 1},
            "latin1": {"cmd": [sys.executable, self.fake, "latin1"], "max_minutes": 1},
        })
        # a store built from a config file: its threads name it, so it is the one that may call an
        # agent gone (the file need not exist; a store with no file owns no thread, see Hardening)
        self.config = os.path.join(self.root, "container.json")
        self.store = T.TaskStore(self.store_dir, self.runners, [self.work], config_path=self.config)
        self.store.last_create = -100.0

    def tearDown(self):
        for t in self.store.list():
            if t.get("status") == "running":
                try:
                    self.store.stop(t["id"])
                except T.TaskError:
                    pass
        time.sleep(0.2)
        self.tmp.cleanup()

    def make(self, *args, **kw):
        self.store.last_create = -100.0
        return self.store.create(*args, **kw)

    def left_pid(self, mode):
        """The pid the fake agent's `mode` left running; ended at cleanup if a test fails first."""
        pid = int(read(os.path.join(self.side, mode + ".pid")))

        def end():
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
        self.addCleanup(end)
        return pid

    def child_event(self, tid, n=1, kind=None, timeout=10):
        """Run n's `child` receipt (the agent's, or a setup/verify line's when kind is given)."""
        path = os.path.join(self.store_dir, tid)
        end = time.time() + timeout
        while time.time() < end:
            for e in self.store._events(path):
                if e["ev"] == "child" and e.get("n") == n and e.get("kind") == kind:
                    self.addCleanup(self.end_group, e["pid"])         # never outlives a failed test
                    return e
            time.sleep(0.05)
        self.fail(f"no child receipt for run {n} ({kind or 'agent'})")

    @staticmethod
    def end_group(pgid):
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)

    @staticmethod
    def group_gone(pgid, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def kill_supervisor(self, tid, n=1):
        """SIGKILL run n's supervisor and wait until the store sees it gone."""
        path = os.path.join(self.store_dir, tid)
        run = next(e for e in self.store._events(path) if e["ev"] == "run" and e.get("n") == n)
        os.kill(run["pid"], signal.SIGKILL)
        end = time.time() + 10
        while time.time() < end and T._pid_alive(run["pid"], tid):
            time.sleep(0.05)

    @staticmethod
    def gone(pid, timeout=3.0):
        """True once `pid` no longer exists (a zombie we do not parent is reaped by init)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    def wait(self, tid, timeout=20):
        """The summary once every run has its exit receipt: a status read alone can say "lost"
        or "new" for a moment (a pid looked at between two receipts), and the next verb would
        then find the run still going."""
        path = os.path.join(self.store_dir, tid)
        end = time.time() + timeout
        while time.time() < end:
            events = self.store._events(path)
            runs = {e.get("n") for e in events if e["ev"] == "run"}
            exits = {e.get("n") for e in events if e["ev"] == "exit"}
            s = self.store.summary(path)
            if runs and runs <= exits and s["status"] not in ("running", "checking", "stopping"):
                return s
            time.sleep(0.1)
        self.fail(f"task {tid} still running after {timeout}s")

    def repo(self, name="proj"):
        path = os.path.join(self.work, name)
        os.makedirs(path)
        git(path, "init", "-q", "-b", "main")
        testenv.hermetic_repo(path)
        git(path, "config", "user.name", "Test")
        git(path, "config", "user.email", "test@example.invalid")
        git(path, "config", "core.hooksPath", "/dev/null")
        with open(os.path.join(path, "shared.txt"), "w") as handle:
            handle.write("original\n")
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "init")
        return path



class TaskLoopTests(Harness):
    def test_in_place_folder_runs_to_review_lists_changed_files_and_closes_done(self):
        folder = os.path.join(self.work, "notes")
        os.makedirs(folder)
        t = self.make("write a note", folder, "writer")
        self.assertEqual(t["mode"], "inplace")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["rc"]), ("review", 0))
        self.assertTrue(os.path.isfile(os.path.join(folder, "agent-note.txt")))
        d = self.store.diff(t["id"])
        self.assertIn("agent-note.txt", [f["path"] for f in d["files"]])
        self.assertIn("not a git folder", d["note"])
        got = self.store.get(t["id"])
        kinds = [l["k"] for l in got["lines"]]
        self.assertIn("tool", kinds)
        self.assertIn("result", kinds)
        self.assertTrue(any(l["k"] == "text" and "agent-note" in l["s"] for l in got["lines"]))
        with self.assertRaises(T.TaskError) as cm:
            self.store.close(t["id"], "merge")
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(self.store.close(t["id"], "done")["status"], "done")

    def test_repo_task_runs_in_its_own_worktree_and_merges_into_the_starting_branch(self):
        repo = self.repo()
        t = self.make("add a note file", repo, "writer")
        self.assertEqual(t["mode"], "worktree")
        self.assertEqual(t["base_branch"], "main")
        task = json.loads(read(os.path.join(self.store_dir, t["id"], "task.json")))
        self.assertTrue(os.path.isdir(task["worktree"]))
        self.assertEqual(git(task["worktree"], "rev-parse", "--abbrev-ref", "HEAD"), f"cm/{t['id']}")
        s = self.wait(t["id"])
        self.assertEqual(s["status"], "review")
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")), "the agent must not touch the main tree")
        d = self.store.diff(t["id"])
        self.assertEqual([(f["path"], f["status"]) for f in d["files"]], [("agent-note.txt", "A")])
        self.assertIn("+hello from the agent", d["patch"])
        self.assertEqual(git(task["worktree"], "status", "--porcelain"), "?? agent-note.txt", "the real index is untouched")
        out = self.store.close(t["id"], "merge")
        self.assertEqual(out["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))
        self.assertFalse(os.path.exists(task["worktree"]))
        self.assertIn(f"cm/{t['id']}", git(repo, "branch", "--list", f"cm/{t['id']}"))
        log = git(repo, "log", "--format=%B", "-1")
        self.assertNotIn("Co-Authored-By", log)

    def test_merge_refuses_to_overwrite_uncommitted_work_and_changes_nothing(self):
        repo = self.repo()
        t = self.make("rewrite shared", repo, "shared")
        self.wait(t["id"])
        with open(os.path.join(repo, "shared.txt"), "w") as handle:
            handle.write("my own edit\n")
        before = git(repo, "rev-parse", "HEAD")
        with self.assertRaises(T.TaskError) as cm:
            self.store.close(t["id"], "merge")
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(read(os.path.join(repo, "shared.txt")), "my own edit\n")
        self.assertEqual(git(repo, "rev-parse", "HEAD"), before)
        self.assertNotEqual(subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=repo).returncode, 0)
        self.assertEqual(self.store.summary(os.path.join(self.store_dir, t["id"]))["status"], "review")

    def test_discard_keeps_the_work_on_its_branch_and_removes_the_worktree(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        task = json.loads(read(os.path.join(self.store_dir, t["id"], "task.json")))
        out = self.store.close(t["id"], "discard")
        self.assertEqual(out["status"], "discarded")
        self.assertFalse(os.path.exists(task["worktree"]))
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))
        self.assertIn("agent-note.txt", git(repo, "show", "--stat", "--format=", f"cm/{t['id']}"))

    def test_no_ai_trailers_refuses_a_signed_branch_and_says_so_in_the_prompt(self):
        repo = self.repo()
        self.store.no_ai_trailers = True
        t = self.make("commit something", repo, "signed")
        self.assertEqual(self.wait(t["id"])["status"], "review")
        self.assertIn("do not add Co-Authored-By", read(os.path.join(self.store_dir, t["id"], "prompt-1.txt")))
        with self.assertRaises(T.TaskError) as cm:
            self.store.close(t["id"], "merge")
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("trailer", cm.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, "signed.txt")))
        self.store.no_ai_trailers = False
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")

    def test_failure_stop_and_time_cap_each_leave_an_exit_receipt(self):
        folder = os.path.join(self.work, "f")
        os.makedirs(folder)
        bad = self.make("fail please", folder, "failer")
        self.assertEqual((self.wait(bad["id"])["status"], self.wait(bad["id"])["rc"]), ("failed", 3))
        slow = self.make("sleep please", folder, "sleeper")
        time.sleep(0.6)
        self.assertEqual(self.store.summary(os.path.join(self.store_dir, slow["id"]))["status"], "running")
        self.store.stop(slow["id"])
        self.assertEqual(self.wait(slow["id"])["status"], "stopped")
        capped = self.make("sleep past the cap", folder, "capped")
        s = self.wait(capped["id"], timeout=25)
        self.assertEqual(s["status"], "failed")
        log = read(os.path.join(self.store_dir, capped["id"], "run-1.log"))
        self.assertIn("KILLED after the time cap", log)

    def test_follow_up_is_a_second_run_with_the_original_task_attached(self):
        folder = os.path.join(self.work, "g")
        os.makedirs(folder)
        t = self.make("first ask", folder, "writer")
        self.wait(t["id"])
        self.store.followup(t["id"], "and then this")
        s = self.wait(t["id"])
        self.assertEqual((s["runs"], s["status"]), (2, "review"))
        seen = read(os.path.join(self.side, "prompt-seen.txt"))
        self.assertIn("and then this", seen)
        self.assertIn("The original task was:\nfirst ask", seen)
        got = self.store.get(t["id"])
        self.assertEqual(got["run"], 2)
        self.assertEqual([p["text"] for p in got["prompts"]], ["first ask", "and then this"])
        self.assertTrue(any(l["s"] == "line 4" for l in got["lines"]))
        first = self.store.get(t["id"], run=1)
        self.assertTrue(any(l["k"] == "tool" for l in first["lines"]))

    def test_log_is_read_in_whole_lines_from_an_offset(self):
        folder = os.path.join(self.work, "h")
        os.makedirs(folder)
        t = self.make("x", folder, "writer")
        self.wait(t["id"])
        whole = self.store.get(t["id"])
        again = self.store.get(t["id"], since=whole["offset"])
        self.assertEqual(again["lines"], [])
        self.assertFalse(again["more"])
        part = self.store.get(t["id"], since=0)
        self.assertEqual(part["offset"], whole["offset"])

    def test_refusals(self):
        folder = os.path.join(self.work, "i")
        os.makedirs(folder)
        outside = os.path.join(self.root, "outside")
        os.makedirs(outside)
        cases = [(("", folder, "writer"), 400), (("x", folder, "nope"), 400), (("x", outside, "writer"), 403),
                 (("x", "relative/path", "writer"), 400), (("x", os.path.join(self.work, "missing"), "writer"), 404),
                 (("bad\x07text", folder, "writer"), 400), (("x" * (T.MAX_TEXT + 1), folder, "writer"), 400)]
        for args, status in cases:
            with self.assertRaises(T.TaskError, msg=str(args)[:60]) as cm:
                self.make(*args)
            self.assertEqual(cm.exception.status, status, msg=str(args)[:60])
        for bad in ("../x", "20260101-000000-x", "", None, 5):
            with self.assertRaises(T.TaskError):
                self.store.get(bad)
        with self.assertRaises(T.TaskError) as cm:
            self.store.close("20260101-000000-x-abcdef", "merge")
        self.assertEqual(cm.exception.status, 404)
        t = self.make("x", folder, "writer")
        with self.assertRaises(T.TaskError) as cm:
            self.store.create("y", folder, "writer")              # no last_create reset: rate limited
        self.assertEqual(cm.exception.status, 429)
        self.wait(t["id"])
        with self.assertRaises(T.TaskError):
            self.store.close(t["id"], "explode")

    def test_the_receipt_does_not_depend_on_the_store_object(self):
        folder = os.path.join(self.work, "j")
        os.makedirs(folder)
        t = self.make("x", folder, "writer")
        del self.store                                              # the bridge restarting
        self.store = T.TaskStore(self.store_dir, self.runners, [self.work])
        self.assertEqual(self.wait(t["id"])["status"], "review")


class AuditFixTests(Harness):
    """Fixes from the 2026-10-07 MiMo and Muse audit, each pinned by a test."""

    @unittest.skipUnless(shutil.which("ps"), "needs a ps binary")
    def test_pid_check_falls_back_to_ps_and_fails_closed(self):
        folder = os.path.join(self.work, "p")
        os.makedirs(folder)
        t = self.make("sleep", folder, "sleeper")
        run = next(e for e in self.store._events(os.path.join(self.store_dir, t["id"])) if e["ev"] == "run")
        self.assertTrue(T._pid_alive(run["pid"], t["id"], proc="/nonexistent-proc"))
        self.assertFalse(T._pid_alive(os.getpid(), t["id"], proc="/nonexistent-proc"))   # alive, but not our supervisor
        self.assertFalse(T._pid_alive(True, t["id"]))
        self.store.stop(t["id"])
        self.assertEqual(self.wait(t["id"])["status"], "stopped")

    TAIL = ["first line", "last words without a newline"]

    def tail_fixture(self, exited):
        """A thread record whose run 1 log ends without a newline; with an exit receipt or not."""
        tid = "20260101-000000-tail-abcdef"
        path = os.path.join(self.store_dir, tid)
        os.makedirs(path)
        with open(os.path.join(path, "task.json"), "w") as handle:
            json.dump({"id": tid, "title": "t", "text": "t", "runner": "writer", "target": self.work, "cwd": self.work,
                       "mode": "inplace", "created": T.stamp()}, handle)
        T.TaskStore._append(path, {"ev": "run", "n": 1, "pid": 0, "runner": "writer", "text": "t", "at": T.stamp()})
        if exited:
            T.TaskStore._append(path, {"ev": "exit", "n": 1, "rc": 0, "killed": False,
                                       "stopped": False, "at": T.stamp()})
        with open(os.path.join(path, "run-1.log"), "w") as handle:
            handle.write("first line\nlast words without a newline")
        return tid

    def test_a_lost_runs_last_partial_line_is_delivered(self):
        tid = self.tail_fixture(exited=False)
        got = self.store.get(tid)
        self.assertEqual(got["status"], "lost")
        self.assertEqual([l["s"] for l in got["lines"]], self.TAIL)
        self.assertFalse(got["more"])

    def test_a_finished_runs_last_partial_line_is_delivered(self):
        tid = self.tail_fixture(exited=True)
        got = self.store.get(tid)
        self.assertEqual(got["status"], "review")
        self.assertEqual([l["s"] for l in got["lines"]], self.TAIL)
        self.assertFalse(got["more"])

    def test_a_stopping_run_hands_out_whole_lines_only(self):
        folder = os.path.join(self.work, "half")
        os.makedirs(folder)
        t = self.make("x", folder, "stubborn")
        self.child_event(t["id"])
        log = os.path.join(self.store_dir, t["id"], "run-1.log")
        end = time.time() + 10
        while time.time() < end and not read(log).endswith("abc"):
            time.sleep(0.02)
        self.assertEqual(self.store.stop(t["id"])["status"], "stopping")
        got = self.store.get(t["id"])
        self.assertEqual(got["status"], "stopping")
        self.assertNotIn("abc", [l["s"] for l in got["lines"]])           # the half line waits
        self.wait(t["id"])
        rest = self.store.get(t["id"], since=got["offset"])
        self.assertEqual(rest["lines"][0]["s"], "abcdef")                  # and arrives whole

    def test_an_append_inside_one_timestamp_tick_is_never_served_stale(self):
        tid = self.tail_fixture(exited=True)
        path = os.path.join(self.store_dir, tid)
        self.assertEqual(self.store.summary(path)["status"], "review")          # cached
        events = os.path.join(path, "events.jsonl")
        before = os.stat(events)
        T.TaskStore._append(path, {"ev": "done", "at": T.stamp()})
        os.utime(events, ns=(before.st_atime_ns, before.st_mtime_ns))     # same tick, more bytes
        self.assertEqual(self.store.summary(path)["status"], "done")

    def test_the_records_note_is_in_the_summary_and_the_detail(self):
        folder = os.path.join(self.work, "noted")
        os.makedirs(folder)
        t = self.make("x", folder, "writer", setup="touch own.txt")
        self.assertIn("setup runs only in a thread's own worktree", t["note"])
        self.wait(t["id"])
        self.assertEqual(self.store.get(t["id"])["note"], t["note"])
        plain = self.make("y", folder, "writer")
        self.assertEqual(plain["note"], "")
        self.wait(plain["id"])

    def test_a_failed_worktree_leaves_no_orphan_task_folder(self):
        repo = self.repo()
        real = T._git
        def broken(args, cwd, **kw):
            if args[:2] == ["worktree", "add"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: simulated lock")
            return real(args, cwd, **kw)
        T._git = broken
        try:
            with self.assertRaises(T.TaskError) as cm:
                self.make("x", repo, "writer")
        finally:
            T._git = real
        self.assertEqual(cm.exception.status, 502)
        left = os.listdir(self.store_dir) if os.path.isdir(self.store_dir) else []
        self.assertEqual([n for n in left if n != ".store.lock"], [])    # the store's lock file stays
        self.assertEqual(self.store.list(), [])

    def test_in_place_in_a_repo_copies_your_uncommitted_files_aside_first(self):
        repo = self.repo()
        with open(os.path.join(repo, "shared.txt"), "w") as handle:
            handle.write("my uncommitted edit\n")
        t = self.make("rewrite shared", repo, "shared", isolate=False)
        self.assertEqual(t["mode"], "inplace")
        self.wait(t["id"])
        self.assertEqual(read(os.path.join(repo, "shared.txt")), "agent version\n")
        self.assertEqual(read(os.path.join(self.store_dir, t["id"], "before", "shared.txt")), "my uncommitted edit\n")
        task = json.loads(read(os.path.join(self.store_dir, t["id"], "task.json")))
        self.assertEqual(task["saved_before"], ["shared.txt"])

    def test_a_run_whose_supervisor_died_can_still_be_stopped(self):
        folder = os.path.join(self.work, "q")
        os.makedirs(folder)
        t = self.make("sleep", folder, "sleeper")
        path = os.path.join(self.store_dir, t["id"])
        for _ in range(50):
            child = next((e for e in self.store._events(path) if e["ev"] == "child"), None)
            if child:
                break
            time.sleep(0.1)
        run = next(e for e in self.store._events(path) if e["ev"] == "run")
        os.kill(run["pid"], signal.SIGKILL)                       # the supervisor dies; the agent keeps going
        for _ in range(50):
            if self.store.summary(path)["status"] == "lost":
                break
            time.sleep(0.1)
        self.assertEqual(self.store.summary(path)["status"], "lost")
        self.store.stop(t["id"])
        self.assertEqual(self.store.summary(path)["status"], "stopped")
        time.sleep(0.5)
        with self.assertRaises(ProcessLookupError):
            os.killpg(child["pid"], 0)

    def test_what_a_dead_agent_left_running_in_its_folder_can_still_be_stopped(self):
        folder = os.path.join(self.work, "r")
        os.makedirs(folder)
        t = self.make("leave a helper", folder, "orphan")
        path = os.path.join(self.store_dir, t["id"])
        for _ in range(50):
            child = next((e for e in self.store._events(path) if e["ev"] == "child"), None)
            if child:
                break
            time.sleep(0.1)
        run = next(e for e in self.store._events(path) if e["ev"] == "run")
        time.sleep(0.4)
        os.kill(run["pid"], signal.SIGKILL)                       # supervisor gone
        time.sleep(2.0)                                           # the agent (group leader) exits; its helper stays
        self.assertEqual(self.store.summary(path)["status"], "lost")
        members = T._group_members(child["pid"])
        self.assertTrue(members and all(pid != child["pid"] for pid, _ in members), members)
        self.store.stop(t["id"])
        self.assertEqual(self.store.summary(path)["status"], "stopped")
        time.sleep(0.5)
        self.assertEqual(T._group_members(child["pid"]), [])

    def test_a_lost_checker_holds_the_thread_until_it_is_stopped(self):
        repo = self.repo()
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        self.store.check(t["id"], "slowjudge")
        checker = self.child_event(t["id"], 2)
        self.kill_supervisor(t["id"], 2)                         # the checker's agent keeps going
        path = os.path.join(self.store_dir, t["id"])
        s = self.store.summary(path)
        self.assertEqual((s["status"], s["check"]["verdict"]), ("review", "lost"))
        for refused in (lambda: self.store.followup(t["id"], "more"),
                        lambda: self.store.check(t["id"], "slowjudge"),
                        lambda: self.store.close(t["id"], "discard")):
            with self.assertRaises(T.TaskError) as err:
                refused()
            self.assertEqual(err.exception.status, 409)
            self.assertIn("still runs in its folder", err.exception.message)
        s = self.store.stop(t["id"])
        self.assertEqual((s["status"], s["check"]["verdict"]), ("review", "stopped"))
        self.assertTrue(self.group_gone(checker["pid"]))
        self.assertEqual(self.store.followup(t["id"], "more")["status"], "running")
        self.wait(t["id"])

    def test_a_lost_setup_or_verify_line_can_still_be_stopped(self):
        repo = self.repo()
        for which, kw in (("setup", {"setup": "sleep 60"}), ("verify", {"verify": "sleep 60"})):
            t = self.make("note", repo, "writer", **kw)
            line = self.child_event(t["id"], 1, which)
            self.kill_supervisor(t["id"])
            path = os.path.join(self.store_dir, t["id"])
            self.assertEqual(self.store.summary(path)["status"], "lost", which)
            for refused in (lambda: self.store.followup(t["id"], "more"),
                            lambda: self.store.close(t["id"], "discard")):
                with self.assertRaises(T.TaskError) as err:
                    refused()
                self.assertEqual(err.exception.status, 409, which)
            self.assertEqual(self.store.stop(t["id"])["status"], "stopped", which)
            self.assertTrue(self.group_gone(line["pid"]), which)
            self.assertEqual(self.store.close(t["id"], "discard")["status"], "discarded", which)

    def test_the_task_store_is_refused_as_a_target_even_through_a_symlink(self):
        link = os.path.join(self.work, "storelink")
        os.symlink(self.store_dir, link)
        os.makedirs(self.store_dir, exist_ok=True)
        store = T.TaskStore(os.path.join(link), self.runners, [self.work, self.store_dir])
        store.last_create = -100.0
        os.makedirs(os.path.join(self.store_dir, "inside"), exist_ok=True)
        with self.assertRaises(T.TaskError) as cm:
            store.create("x", os.path.join(link, "inside"), "writer")
        self.assertEqual(cm.exception.status, 403)

    def test_a_refused_merge_writes_nothing_and_a_hand_removed_worktree_still_closes(self):
        repo = self.repo()
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        git(repo, "checkout", "-q", "-b", "elsewhere")
        with self.assertRaises(T.TaskError) as cm:
            self.store.close(t["id"], "merge")
        self.assertIn("switch back to main", cm.exception.message)
        self.assertEqual(git(repo, "rev-list", "--count", f"main..cm/{t['id']}"), "0")   # no leftover commit was made
        git(repo, "checkout", "-q", "main")
        task = json.loads(read(os.path.join(self.store_dir, t["id"], "task.json")))
        git(repo, "worktree", "remove", "--force", task["worktree"])
        self.assertEqual(self.store.close(t["id"], "discard")["status"], "discarded")
        signed = self.make("commit", repo, "signed")
        self.wait(signed["id"])
        task = json.loads(read(os.path.join(self.store_dir, signed["id"], "task.json")))
        git(repo, "worktree", "remove", "--force", task["worktree"])
        self.assertEqual(self.store.close(signed["id"], "merge")["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "signed.txt")))


def scoped_store(test):
    """A store whose supervisors run in their own systemd scope; skips without a user manager."""
    T._SCOPE.clear()
    test.addCleanup(T._SCOPE.clear)
    if not T.scope_prefix("20261007-000000-x-abcdef", 1):
        test.skipTest("no systemd user manager here")
    store = T.TaskStore(test.store_dir, test.runners, [test.work], scope=True)
    store.last_create = -100.0
    return store


class ScopeTests(Harness):
    def test_a_scoped_supervisor_runs_in_this_runs_own_cm_task_scope_and_reports(self):
        store = scoped_store(self)
        self.assertEqual(T.scope_prefix("20261007-000000-x-abcdef", 1)[1:5],
                         ["--user", "--scope", "--quiet", "--collect"])
        folder = os.path.join(self.work, "scoped")
        os.makedirs(folder)
        t = store.create("make a note", folder, "sleeper")
        path = os.path.join(self.store_dir, t["id"])
        run = next(e for e in store._events(path) if e["ev"] == "run")
        unit, cgroup = T._scope_unit(t["id"], 1), ""
        for _ in range(100):                      # systemd-run joins its scope a beat after exec
            with contextlib.suppress(OSError), open(f"/proc/{run['pid']}/cgroup") as handle:
                cgroup = handle.read()
            if unit.search(cgroup):
                break
            time.sleep(0.02)
        self.assertRegex(cgroup, unit)            # the scope named for this run, not the caller's
        self.assertIsNone(T._scope_unit(t["id"], 2).search(cgroup))
        store.stop(t["id"])
        self.assertEqual(self.wait(t["id"])["status"], "stopped")   # the receipt still comes back
        T._SCOPE["exe"] = ""                              # no systemd: the plain launch, as before
        self.assertEqual(T.scope_prefix("x", 1), [])

    def test_a_shared_scope_is_never_swept(self):
        # the caller's cgroup (init.scope under WSL, session-N.scope over ssh) holds the shell and
        # every agent; sweeping it once took down the whole WSL side
        self.assertEqual(T._scope_members("20261007-000000-x-abcdef", 1), [])
        for shared in ("0::/init.scope", "0::/user.slice/user-1000.slice/session-3.scope",
                       "0::/user.slice/user-1000.slice/user@1000.service/app.slice/run-r1.scope"):
            self.assertIsNone(T._OWN_SCOPE.search(shared))
            self.assertIsNone(T._scope_unit("20261007-000000-x-abcdef", 1).search(shared))
        self.assertIsNotNone(T._OWN_SCOPE.search(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/cm-task-abcdef-1-0a1b2c.scope"))

    def test_only_the_scope_made_for_this_run_is_swept(self):
        # a supervisor started inside another thread's scope (a store with scope off, run by an
        # agent of that thread) inherits that scope: it must never sweep it
        fake = os.path.join(self.root, "proc")
        victim = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: victim.poll() is None and (victim.kill() or victim.wait()))
        scope = ("0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
                 "cm-task-aaaaaa-1-bbbbbb.scope\n")
        for name in ("self", str(victim.pid)):
            os.makedirs(os.path.join(fake, name))
            with open(os.path.join(fake, name, "cgroup"), "w") as handle:
                handle.write(scope)
        mine, other = "20261009-000000-x-aaaaaa", "20261009-000000-x-cccccc"
        self.assertEqual(T._scope_members(other, 1, fake), [])
        self.assertEqual(T._scope_members(mine, 2, fake), [])          # same thread, another run
        self.assertEqual(T._scope_members(mine, 1, fake), [victim.pid])
        agent = types.SimpleNamespace(pid=victim.pid)            # its group lists nobody in `fake`
        self.assertEqual(T._sweep_after(agent, (other, 1), fake), 0)
        self.assertEqual(T._sweep_after(agent, None, fake), 0)   # a store with scope off
        self.assertIsNone(victim.poll())

        def reap():                                              # the fake /proc forgets the dead
            victim.wait()
            shutil.rmtree(os.path.join(fake, str(victim.pid)))
        threading.Thread(target=reap, daemon=True).start()
        self.assertEqual(T._sweep_after(agent, (mine, 1), fake), 1)
        self.assertIsNotNone(victim.wait(3))


class OracleTests(Harness):
    """The oracle loop: a verify command is the thread's own done-check; a failing one sends the
    agent back with the failure until it passes or the retries are spent."""

    def test_verify_pass_fail_and_the_retry_loop(self):
        folder = os.path.join(self.work, "oracle")
        os.makedirs(folder)
        with self.assertRaises(T.TaskError):
            self.make("x", folder, "writer", True, None, "true", 9)
        with self.assertRaises(T.TaskError):
            self.make("x", folder, "writer", True, None, None, 1)          # retries without a verify
        ok = self.make("make a note", folder, "writer", True, None, "test -f agent-note.txt")
        self.assertEqual((ok["verify_cmd"], ok["retries"]), ("test -f agent-note.txt", 0))
        s = self.wait(ok["id"])
        self.assertEqual((s["status"], s["verify"]["rc"], s["verify"]["n"]), ("review", 0, 1))
        self.assertTrue(os.path.isfile(os.path.join(self.store_dir, ok["id"], "verify-1.log")))
        got = self.store.get(ok["id"])
        self.assertIn("$ exit 0", got["verify_tail"])
        bad = self.make("make a note", folder, "writer", True, None, "test -f never-made.txt", 1)
        s = self.wait(bad["id"])
        self.assertEqual((s["status"], s["verify"]["rc"], s["attempts"]), ("failed", 1, 1))
        self.assertEqual(self.store.tick(), [bad["id"]])                    # retry 1 of 1 goes out
        self.assertEqual(self.store.tick(), [])                             # not twice while it runs
        s = self.wait(bad["id"])
        self.assertEqual((s["status"], s["runs"], s["attempts"]), ("failed", 2, 2))
        self.assertEqual(self.store.tick(), [])                             # retries spent
        got = self.store.get(bad["id"], 2)
        auto = [p for p in got["prompts"] if p["n"] == 2][0]
        self.assertTrue(auto["auto"] and auto["followup"])
        self.assertIn("verify command failed (exit 1)", auto["text"])
        self.assertIn("attempt 2 of 2", auto["text"])
        c = self.store.check(bad["id"], "sessioned")                         # a check run never runs the verify
        self.wait(bad["id"])
        events = [json.loads(l) for l in read(os.path.join(self.store_dir, bad["id"], "events.jsonl")).splitlines()]
        self.assertEqual([e["n"] for e in events if e["ev"] == "verify"], [1, 2])
        self.assertEqual(self.store.summary(os.path.join(self.store_dir, bad["id"]))["status"], "failed")


class ReviewFixTests(Harness):
    """Fixes from a review by two other models (Gemini and Codex)."""

    def test_follow_up_resumes_the_makers_own_session_and_checks_run_read_only(self):
        folder = os.path.join(self.work, "sess")
        os.makedirs(folder)
        t = self.make("do", folder, "sessioned")
        self.wait(t["id"])
        self.store.check(t["id"], "sessioned")                 # a check by the same agent in between
        self.wait(t["id"])
        f = self.store.followup(t["id"], "more")
        self.wait(t["id"])
        seen = [json.loads(l) for l in read(os.path.join(self.side, "argv-seen.txt")).splitlines()]
        self.assertEqual(seen[0][0], ["session"])
        self.assertEqual(seen[1][0], ["judge", "--read-only"])                       # check_cmd, not the worker command
        self.assertEqual(seen[2][0][:2], ["lines", "--resume"])
        self.assertTrue(seen[2][0][2].startswith("sess-"))                           # the maker's id, not the reviewer's run
        self.assertEqual(self.store.summary(os.path.join(self.store_dir, t["id"]))["check"]["stale"], True)   # work moved on after the check
        no = T.valid_runners({"plain": {"cmd": ["x"], "role": "both"}})["plain"]
        self.assertEqual((no["role"], no["check_cmd"]), ("work", None))             # no read-only command: not a reviewer

    def test_a_reviewer_that_crashes_passes_nothing(self):
        runners = dict(self.runners, **T.valid_runners({"crasher": {"cmd": [sys.executable, self.fake, "fail"], "role": "check", "max_minutes": 1}}))
        store = T.TaskStore(self.store_dir, runners, [self.work])
        store.last_create = -100.0
        folder = os.path.join(self.work, "crash")
        os.makedirs(folder)
        t = store.create("do", folder, "writer")
        self.wait(t["id"])
        store.check(t["id"], "crasher")
        self.wait(t["id"])
        v = store.summary(os.path.join(self.store_dir, t["id"]))["check"]["verdict"]
        self.assertTrue(v.startswith("no verdict"), v)

    def test_an_altered_record_is_refused_and_a_repo_above_the_roots_works_in_place(self):
        folder = os.path.join(self.work, "alt")
        os.makedirs(folder)
        t = self.make("do", folder, "writer")
        self.wait(t["id"])
        rec = os.path.join(self.store_dir, t["id"], "task.json")
        data = json.load(open(rec))
        data["cwd"] = "/tmp"
        json.dump(data, open(rec, "w"))
        with self.assertRaises(T.TaskError) as err:
            self.store.followup(t["id"], "more")
        self.assertIn("altered", err.exception.message)
        with self.assertRaises(T.TaskError):
            self.store.check(t["id"], "writer")
        repo = self.repo("outer")
        inner = os.path.join(repo, "inner")
        os.makedirs(inner)
        store = T.TaskStore(self.store_dir, self.runners, [inner])
        store.last_create = -100.0
        t2 = store.create("do", inner, "writer")
        self.assertEqual((t2["mode"], t2["repo"]), ("inplace", None))

    def test_a_daemon_that_escaped_the_group_dies_with_the_agent_in_a_scoped_store(self):
        store = scoped_store(self)
        folder = os.path.join(self.work, "leave")
        os.makedirs(folder)
        t = store.create("do", folder, "leaver")
        s = self.wait(t["id"])
        daemon = self.left_pid("leaver")
        self.assertEqual(s["status"], "review")
        log = read(os.path.join(self.store_dir, t["id"], "run-1.log"))
        self.assertIn("left a daemon behind", log)
        self.assertIn("ended 1 leftover", log)
        self.assertTrue(self.gone(daemon), "the escaped daemon outlived the agent")

    def test_a_helper_left_in_the_agents_group_dies_with_it(self):
        folder = os.path.join(self.work, "straggle")
        os.makedirs(folder)
        t = self.make("do", folder, "straggler")                # the Harness store: no scope
        s = self.wait(t["id"])
        helper = self.left_pid("straggler")
        self.assertEqual(s["status"], "review")
        self.assertIn("ended 1 leftover", read(os.path.join(self.store_dir, t["id"], "run-1.log")))
        self.assertTrue(self.gone(helper), "the helper outlived the agent")


class CheckTests(Harness):
    def test_a_check_is_a_second_opinion_that_leaves_the_status_alone(self):
        runners = dict(self.runners, **T.valid_runners({"judge": {"cmd": [sys.executable, self.fake, "judge"], "check_cmd": [sys.executable, self.fake, "judge"], "max_minutes": 1,
                                                                   "models": ["fast"], "default_model": "fast"}}))
        store = T.TaskStore(self.store_dir, runners, [self.work])
        store.last_create = -100.0
        folder = os.path.join(self.work, "plain")
        os.makedirs(folder)
        t = store.create("make a note", folder, "writer")
        self.wait(t["id"])
        with self.assertRaises(T.TaskError) as err:
            store.check(t["id"], "nobody")
        self.assertEqual(err.exception.status, 400)
        c = store.check(t["id"], "judge")
        self.assertEqual((c["status"], c["runs"], c["check"]["verdict"]), ("checking", 2, "checking"))
        with self.assertRaises(T.TaskError):
            store.followup(t["id"], "more")                  # not while the checker runs
        self.wait(t["id"])
        s = store.summary(os.path.join(self.store_dir, t["id"]))
        self.assertEqual((s["status"], s["runs"], s["check"]["n"], s["check"]["verdict"], s["check"]["runner"], s["check"]["model"]), ("review", 2, 2, "PASS", "judge", "fast"))
        prompt = read(os.path.join(self.store_dir, t["id"], "prompt-2.txt"))
        self.assertIn("CHECK, not a task", prompt)
        self.assertIn("make a note", prompt)
        self.assertIn("Do NOT modify", prompt)
        c2 = store.check(t["id"], "judge", None, "please fail this")
        self.wait(t["id"])
        s = store.summary(os.path.join(self.store_dir, t["id"]))
        self.assertEqual((s["status"], s["check"]["n"], s["check"]["verdict"]), ("review", 3, "FAIL"))
        got = store.get(t["id"], 3)
        self.assertEqual([l["k"] for l in got["lines"] if l["k"] != "meta"], ["raw", "raw"])
        f = store.followup(t["id"], "now do more")            # work continues after a verdict, as run 4
        self.assertEqual((f["runs"], f["status"]), (4, "running"))
        self.wait(t["id"])
        self.assertEqual(store.summary(os.path.join(self.store_dir, t["id"]))["check"]["n"], 3)   # the old verdict is still shown, dated
        self.assertEqual(store.close(t["id"], "done")["status"], "done")
        with self.assertRaises(T.TaskError):
            store.check(t["id"], "judge")                       # closed


class RecordTrustTests(Harness):
    """task.json is in reach of the agent (../task.json from its worktree): the verify line the
    supervisor runs comes from its argv, every verb that acts on the record's paths or refs checks
    it against the digest taken at creation, and a live supervisor can always be stopped."""

    def write_record(self, path, data):
        with open(os.path.join(path, "task.json"), "w") as handle:
            json.dump(data, handle)

    def test_an_agent_that_rewrites_its_verify_line_is_still_judged_by_the_original(self):
        repo = self.repo()
        # a thread made before worktrees left the receipts: ../task.json is the record
        self.store._worktree_path = lambda tid, path: os.path.join(path, "worktree")
        t = self.make("x", repo, "tamper", True, None, "test -f never.txt")
        s = self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual((s["status"], s["verify"]["rc"], s["merge_blockers"]),
                         ("failed", 1, ["verify failed (exit 1)"]))
        self.assertIn("$ test -f never.txt", read(os.path.join(path, "verify-1.log")))
        for refused in (lambda: self.store.followup(t["id"], "more"),
                        lambda: self.store.check(t["id"], "sessioned"),
                        lambda: self.store.diff(t["id"]),
                        lambda: self.store.close(t["id"], "merge", force=True)):
            with self.assertRaises(T.TaskError) as err:
                refused()
            self.assertEqual(err.exception.status, 409)
            self.assertIn("altered after it started", err.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))

    def test_stop_diff_and_close_refuse_an_altered_record_and_signal_no_one(self):
        repo, other = self.repo(), self.repo("other")
        t = self.make("x", repo, "slowwriter")
        agent = self.child_event(t["id"])
        self.kill_supervisor(t["id"])
        os.killpg(agent["pid"], signal.SIGKILL)
        self.assertTrue(self.group_gone(agent["pid"]))
        path = os.path.join(self.store_dir, t["id"])
        victim = subprocess.Popen(["sleep", "30"], cwd=self.side, start_new_session=True)
        self.addCleanup(lambda: victim.poll() is None and (victim.kill() or victim.wait()))
        T.TaskStore._append(path, {"ev": "child", "n": 1, "pid": victim.pid, "at": T.stamp()})
        data = json.loads(read(os.path.join(path, "task.json")))
        for key, value, says in (("cwd", self.side, "worktree record was altered"),
                                 ("branch", "main", "altered after it started"),
                                 ("repo", other, "altered after it started"),
                                 ("base_sha", "0" * 40, "altered after it started")):
            self.write_record(path, dict(data, **{key: value}))
            for call in (lambda: self.store.stop(t["id"]), lambda: self.store.diff(t["id"]),
                         lambda: self.store.close(t["id"], "merge", force=True)):
                with self.assertRaises(T.TaskError, msg=key) as err:
                    call()
                self.assertEqual(err.exception.status, 409, key)
                self.assertIn(says, err.exception.message, key)
            self.assertIsNone(victim.poll(), key)
        self.write_record(path, data)                         # restored: nothing of ours runs
        with self.assertRaises(T.TaskError) as err:
            self.store.stop(t["id"])
        self.assertEqual(err.exception.message, "nothing is running")
        self.assertIsNone(victim.poll())

    def test_a_live_supervisor_is_stopped_whatever_the_record_says(self):
        folder = os.path.join(self.work, "altered-live")
        os.makedirs(folder)
        t = self.make("x", folder, "sleeper")
        self.child_event(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        data = json.loads(read(os.path.join(path, "task.json")))
        self.write_record(path, dict(data, cwd=self.side, verify="true"))
        self.store.stop(t["id"])
        end = time.time() + 20
        while time.time() < end and not any(e["ev"] == "exit" for e in self.store._events(path)):
            time.sleep(0.05)
        exit_row = next(e for e in self.store._events(path) if e["ev"] == "exit")
        self.assertTrue(exit_row["stopped"])

    def test_a_group_in_any_folder_is_inside_the_root_folder(self):
        p = subprocess.Popen(["sleep", "30"], cwd=self.side, start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and (p.kill() or p.wait()))
        end = time.time() + 3
        while time.time() < end and not T._group_members(p.pid):
            time.sleep(0.02)
        self.assertTrue(T._owned_group(p.pid, "/"))
        self.assertTrue(T._owned_group(p.pid, self.side))
        self.assertFalse(T._owned_group(p.pid, self.work))


class SetupVerifyEdgeTests(Harness):
    """Stops and time caps inside the setup and verify lines, and a forced merge of nothing."""

    def supervise(self, verify, setup, **consts):
        """Run the supervisor by hand with its timeouts lowered; returns (events, run log)."""
        tid = "20261009-000000-edge-abcdef"
        path = os.path.join(self.store_dir, tid)
        os.makedirs(path)
        with open(os.path.join(path, "prompt-1.txt"), "w") as handle:
            handle.write("x")
        # the supervisor starts nothing until its run is on record (the store writes it right
        # after it launches one)
        self.store._append(path, {"ev": "run", "n": 1, "pid": os.getpid(), "runner": "writer",
                                  "kind": "task", "text": "x", "at": T.stamp()})
        lowered = "; ".join(f"tasks.{k} = {v!r}" for k, v in consts.items())
        code = f"import sys, tasks; {lowered}; raise SystemExit(tasks._supervise(sys.argv[1:]))"
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(T.__file__)))
        subprocess.run([sys.executable, "-c", code, path, "1", "60", "0", tid, verify, setup, "0",
                        "--", sys.executable, self.fake, "write"],
                       cwd=self.work, env=env, timeout=60, check=True)
        return self.store._events(path), read(os.path.join(path, "run-1.log"))

    def test_a_setup_past_its_time_cap_fails_and_starts_no_agent(self):
        events, log = self.supervise("", "sleep 30", SETUP_TIMEOUT=1)
        setup = next(e for e in events if e["ev"] == "setup")
        last = next(e for e in events if e["ev"] == "exit")
        self.assertEqual((setup["rc"], last["rc"], last["setup_failed"], last["stopped"]),
                         (-15, 125, True, False))
        self.assertIn("setup failed (exit -15)", log)
        self.assertEqual([e.get("kind") for e in events if e["ev"] == "child"], ["setup"])

    def test_a_verify_past_its_time_cap_is_a_failed_verify(self):
        events, log = self.supervise("sleep 30", "", VERIFY_TIMEOUT=1)
        verify = next(e for e in events if e["ev"] == "verify")
        last = next(e for e in events if e["ev"] == "exit")
        self.assertEqual((verify["rc"], last["rc"]), (-15, 0))
        self.assertIn("# verify · exit -15", log)

    def test_a_stop_during_setup_starts_no_agent_and_says_so(self):
        repo = self.repo()
        t = self.make("x", repo, "writer", setup="sleep 30")
        line = self.child_event(t["id"], 1, "setup")
        self.store.stop(t["id"])
        s = self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        last = next(e for e in self.store._events(path) if e["ev"] == "exit")
        self.assertEqual((s["status"], last["rc"], last["stopped"], last["setup_failed"]),
                         ("stopped", 125, True, True))
        self.assertIn("stopped by you during setup", read(os.path.join(path, "run-1.log")))
        self.assertTrue(self.group_gone(line["pid"]))
        self.assertFalse(os.path.exists(os.path.join(self.side, "argv-seen.txt")))   # no agent ran

    def test_forcing_a_merge_of_nothing_closes_it_done(self):
        repo = self.repo()
        t = self.make("x", repo, "quiet", True, None, "false")
        self.assertEqual(self.wait(t["id"])["merge_blockers"], ["verify failed (exit 1)"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        out = self.store.close(t["id"], "merge", force=True)
        self.assertEqual((out["status"], out["message"]),
                         ("done", "the agent changed nothing, so there was nothing to merge"))


class TickTests(Harness):
    """The oracle loop's pass: retries judged on the newest work run, one broken thread never
    stops the rest, and a retry waits for a free place."""

    def failing(self, text, runner="writer", retries=1, folder=None):
        if folder is None:
            folder = os.path.join(self.work, text.replace(" ", "-"))
            os.makedirs(folder)
        t = self.make(text, folder, runner, True, None, "test -f never-made.txt", retries)
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["retry_due"]), ("failed", True))
        return t

    def test_checking_a_failed_thread_does_not_cancel_its_retry(self):
        t = self.failing("checked then retried", retries=2)
        self.store.check(t["id"], "sessioned")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["check"]["n"], s["retry_due"]), ("failed", 2, True))
        self.assertEqual(self.store.tick(), [t["id"]])
        s = self.wait(t["id"])
        self.assertEqual((s["runs"], s["attempts"], s["verify"]["n"]), (3, 2, 3))
        auto = [p for p in self.store.get(t["id"])["prompts"] if p["n"] == 3][0]
        self.assertTrue(auto["auto"])

    def test_one_broken_thread_never_holds_back_the_others(self):
        repo = self.repo()
        self.store.runners["gone"] = self.store.runners["writer"]
        old = self.failing("a old", runner="gone", folder=repo)
        new = self.failing("b new", folder=repo)
        del self.store.runners["gone"]                           # its agent is not offered any more
        self.assertEqual(self.store.tick(), [new["id"]])
        self.wait(new["id"])
        old_path = os.path.join(self.store_dir, old["id"])
        s = self.store.summary(old_path)             # blocked with its reason, no retry burned
        self.assertEqual((s["runs"], s["retry_due"]), (1, False))
        self.assertIn("gone is not offered any more", s["blocked"])
        # a worktree removed by hand: blocked too, no retry burned on a 127
        gone_wt = self.failing("c gone worktree", folder=repo)
        last = self.failing("d last", folder=repo)
        git(repo, "worktree", "remove", "--force",
            self.store._read_task(os.path.join(self.store_dir, gone_wt["id"]))["worktree"])
        self.assertEqual(self.store.tick(), [last["id"]])
        self.wait(last["id"])
        s = self.store.summary(os.path.join(self.store_dir, gone_wt["id"]))
        self.assertEqual((s["runs"], s["retry_due"]), (1, False))
        self.assertIn("worktree is gone", s["blocked"])
        self.assertEqual([t for t, m in self.store.tick_errors], [gone_wt["id"]])

    def test_a_retry_that_cannot_start_is_reported_once_and_the_pass_goes_on(self):
        a, b = self.failing("a first"), self.failing("b second")
        seen = []
        self.store.on_tick_error = lambda tid, message: seen.append((tid, message))
        real = self.store._start_run

        def start(path, task, *args, **kw):
            if task["id"] == a["id"]:
                raise T.TaskError(502, "the agent could not be started: boom")
            return real(path, task, *args, **kw)
        boom = (a["id"], "the agent could not be started: boom")
        with mock.patch.object(self.store, "_start_run", start):
            self.assertEqual(self.store.tick(), [b["id"]])
            self.assertEqual(self.store.tick_errors, [boom])
            self.assertEqual(self.store.tick(), [])          # b runs; a fails the same way again
            self.assertEqual(self.store.tick_errors, [boom])
        self.assertEqual(seen, [boom])                       # reported once
        self.wait(b["id"])

    def test_a_due_retry_waits_for_a_free_place(self):
        self.store.max_running = 1
        t = self.failing("waits its turn")
        folder = os.path.join(self.work, "busy")
        os.makedirs(folder)
        busy = self.make("x", folder, "sleeper")
        self.child_event(busy["id"])
        self.assertEqual(self.store.tick(), [])
        self.store.stop(busy["id"])
        self.wait(busy["id"])
        self.assertEqual(self.store.tick(), [t["id"]])
        self.wait(t["id"])


class StopCheckTests(Harness):
    """Stopping a check reads like stopping the work: "stopping" while it ends, one stop event
    however often it is asked, and a check verdict of "stopped" once it has."""

    def test_stopping_a_check_says_stopping_once_and_ends_stopped(self):
        folder = os.path.join(self.work, "stopcheck")
        os.makedirs(folder)
        t = self.make("make a note", folder, "writer")
        self.wait(t["id"])
        self.store.check(t["id"], "stubjudge")
        self.child_event(t["id"], 2)
        s = self.store.stop(t["id"])
        self.assertEqual((s["status"], s["check"]["verdict"]), ("stopping", "stopping"))
        again = self.store.stop(t["id"])                       # a second click changes nothing
        self.assertEqual((again["status"], again["check"]["verdict"]), ("stopping", "stopping"))
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual([e["n"] for e in self.store._events(path) if e["ev"] == "stop"], [2])
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["check"]["n"], s["check"]["verdict"]),
                         ("review", 2, "stopped"))
        self.assertEqual(s["merge_blockers"], [])

    def test_a_second_stop_of_the_work_is_the_same_answer_not_a_409(self):
        folder = os.path.join(self.work, "stopwork")
        os.makedirs(folder)
        t = self.make("x", folder, "stubborn")
        self.child_event(t["id"])
        self.assertEqual(self.store.stop(t["id"])["status"], "stopping")
        self.assertEqual(self.store.stop(t["id"])["status"], "stopping")
        self.assertEqual(self.wait(t["id"])["status"], "stopped")
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual([e["n"] for e in self.store._events(path) if e["ev"] == "stop"], [1])


class ModelTests(Harness):
    def test_models_are_offered_validated_and_applied_as_args_and_env(self):
        runners = T.valid_runners({"modeled": {"cmd": [sys.executable, self.fake, "write"], "max_minutes": 1,
                                               "models": [{"name": "fast", "args": ["--speed", "fast"], "env": {"FAKE_MODEL": "fast"}, "desc": "quick"},
                                                          "plain", {"name": "bad name!"}, {"name": "fast"}, {"name": "x", "env": {"lower": "no"}}],
                                               "default_model": "plain"},
                                   "bare": {"cmd": [sys.executable, self.fake, "write"], "max_minutes": 1}})
        self.assertEqual([m["name"] for m in runners["modeled"]["models"]], ["fast", "plain"])
        self.assertEqual(runners["modeled"]["default_model"], "plain")
        self.assertEqual((runners["bare"]["models"], runners["bare"]["default_model"]), ([], None))
        pub = {r["name"]: r for r in T.public_runners(runners)}
        self.assertEqual(pub["modeled"]["models"], [{"name": "fast", "label": "fast", "desc": "quick"}, {"name": "plain", "label": "plain", "desc": ""}])
        store = T.TaskStore(self.store_dir, runners, [self.work], now=lambda: 1_700_000_000)
        with self.assertRaises(T.TaskError) as err:
            store.create("x", self.work, "modeled", True, "nope")
        self.assertEqual(err.exception.status, 400)
        with self.assertRaises(T.TaskError) as err:
            store.create("x", self.work, "bare", True, "fast")
        self.assertIn("no model choice", err.exception.message)
        def settle(tid):
            for _ in range(200):
                if store.summary(os.path.join(self.store_dir, tid))["status"] != "running":
                    return
                time.sleep(0.1)
            self.fail("still running")
        store.last_create = -100.0
        a = store.create("one", self.work, "modeled", True, "fast")
        self.assertEqual(a["model"], "fast")
        settle(a["id"])
        store.last_create = -100.0
        b = store.create("two", self.work, "modeled", True)
        self.assertEqual(b["model"], "plain")            # the runner's default when none is picked
        settle(b["id"])
        seen = [json.loads(l) for l in read(os.path.join(self.side, "argv-seen.txt")).splitlines()]
        self.assertEqual(seen[0], [["write", "--speed", "fast"], "fast"])
        self.assertEqual(seen[1], [["write"], None])
        events = [json.loads(l) for l in read(os.path.join(self.store_dir, a["id"], "events.jsonl")).splitlines()]
        self.assertEqual(events[0]["model"], "fast")
        self.assertEqual([t["model"] for t in store.list()], ["plain", "fast"])

    def test_labels_and_roles_a_reviewer_never_gets_the_work(self):
        runners = T.valid_runners({"maker": {"cmd": [sys.executable, self.fake, "write"], "max_minutes": 1, "label": "Maker",
                                             "models": [{"name": "big", "label": "Big Model 9.1", "desc": "strong"}]},
                                   "judge": {"cmd": [sys.executable, self.fake, "judge"], "max_minutes": 1, "role": "check",
                                             "models": [{"name": "m3", "label": "Judge M3"}]},
                                   "odd": {"cmd": ["x"], "role": "boss"}})
        self.assertEqual((runners["judge"]["role"], runners["maker"]["role"], runners["odd"]["role"]), ("check", "work", "work"))   # no read-only command = never a reviewer
        pub = {r["name"]: r for r in T.public_runners(runners)}
        self.assertEqual((pub["maker"]["label"], pub["maker"]["models"][0]["label"], pub["judge"]["role"]), ("Maker", "Big Model 9.1", "check"))
        store = T.TaskStore(self.store_dir, runners, [self.work])
        store.last_create = -100.0
        with self.assertRaises(T.TaskError) as err:
            store.create("do it", self.work, "judge")
        self.assertIn("only checks", err.exception.message)
        t = store.create("do it", self.work, "maker")
        for _ in range(200):
            if store.summary(os.path.join(self.store_dir, t["id"]))["status"] != "running":
                break
            time.sleep(0.1)
        c = store.check(t["id"], "judge")
        self.assertEqual((c["check"]["runner"], c["check"]["model"]), ("judge", "m3"))

    def test_model_args_land_before_the_stdin_marker_or_the_text_slot(self):
        self.assertEqual(T.with_model_args(["codex", "exec", "--json", "-"], ["-c", "k=v"]), ["codex", "exec", "--json", "-c", "k=v", "-"])
        self.assertEqual(T.with_model_args(["claude", "-p"], ["--model", "haiku"]), ["claude", "-p", "--model", "haiku"])
        self.assertEqual(T.with_model_args(["aider", "--yes", "--message", "{text}"], ["--model", "m"]), ["aider", "--yes", "--model", "m", "--message", "{text}"])
        self.assertEqual(T.with_model_args(["x", "-"], []), ["x", "-"])
        self.assertEqual(T.with_model_args(["gemini-seat", "-p", "{text}"], ["--model", "g"]), ["gemini-seat", "--model", "g", "-p", "{text}"])
        self.assertEqual(T.with_model_args(["grok", "-p", "{text}", "--always-approve"], ["-m", "k"]), ["grok", "-m", "k", "-p", "{text}", "--always-approve"])


class VerifyAnyExitTests(Harness):
    """The verify runs after every exit of the agent's own, not only a clean one: the oracle, not
    the agent's exit code, decides whether the work counts. A stop or the time cap skips it."""

    def test_a_crashing_agent_whose_verify_passes_is_review(self):
        folder = os.path.join(self.work, "crash-ok")
        os.makedirs(folder)
        t = self.make("x", folder, "failer", True, None, "true")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["rc"], s["verify"]["rc"], s["merge_blockers"]), ("review", 3, 0, []))
        self.assertIn("the agent exited 3, but the verify passed", read(os.path.join(self.store_dir, t["id"], "run-1.log")))

    def test_a_crashing_agent_whose_verify_fails_is_sent_back(self):
        folder = os.path.join(self.work, "crash-bad")
        os.makedirs(folder)
        t = self.make("x", folder, "failer", True, None, "false", 1)
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["rc"], s["verify"]["rc"], s["retry_due"]), ("failed", 3, 1, True))
        self.assertEqual(self.store.tick(), [t["id"]])                     # the auto-retry goes out
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["attempts"], s["verify"]["n"], s["retry_due"]), ("failed", 2, 2, False))

    def test_a_stopped_or_capped_run_is_never_verified(self):
        folder = os.path.join(self.work, "nov")
        os.makedirs(folder)
        slow = self.make("x", folder, "sleeper", True, None, "true")
        time.sleep(0.6)
        self.store.stop(slow["id"])
        self.assertEqual((self.wait(slow["id"])["status"], self.wait(slow["id"])["verify"]), ("stopped", None))
        capped = self.make("x", folder, "capped", True, None, "true")
        self.assertEqual((self.wait(capped["id"], timeout=25)["status"], self.wait(capped["id"])["verify"]), ("failed", None))
        for t in (slow, capped):
            self.assertFalse(os.path.exists(os.path.join(self.store_dir, t["id"], "verify-1.log")))


class SetupTests(Harness):
    """A worktree thread's setup line runs in its folder before the agent, on the first run only,
    with its own receipt; when it fails no agent starts and nothing retries."""

    def test_setup_runs_first_once_with_its_own_receipt(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer", setup="echo ready > setup-made.txt")
        self.assertEqual(t["setup_cmd"], "echo ready > setup-made.txt")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["setup"]["rc"], s["setup"]["n"]), ("review", 0, 1))
        path = os.path.join(self.store_dir, t["id"])
        task = json.loads(read(os.path.join(path, "task.json")))
        self.assertEqual(read(os.path.join(task["worktree"], "setup-made.txt")), "ready\n")
        log = read(os.path.join(path, "run-1.log"))
        self.assertLess(log.index("# setup · exit 0"), log.index("I wrote agent-note.txt"))
        self.assertIn("$ exit 0", self.store.get(t["id"])["setup_tail"])
        self.store.followup(t["id"], "more")                             # a follow-up never runs it again
        self.wait(t["id"])
        self.store.check(t["id"], "sessioned")                           # nor does a check
        self.wait(t["id"])
        self.assertEqual([e["n"] for e in self.store._events(path) if e["ev"] == "setup"], [1])
        self.assertFalse(os.path.exists(os.path.join(path, "setup-2.log")))

    def test_a_failed_setup_starts_no_agent_and_is_not_retried(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer", True, None, "true", 2, "echo broken; exit 4")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["rc"], s["setup"]["rc"], s["retry_due"]), ("failed", 125, 4, False))
        self.assertEqual(s["merge_blockers"], ["setup failed (exit 4)"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertIn("setup failed (exit 4)", read(os.path.join(path, "run-1.log")))
        self.assertIn("broken", read(os.path.join(path, "setup-1.log")))
        self.assertFalse(os.path.exists(os.path.join(self.side, "argv-seen.txt")))    # the agent never started
        events = self.store._events(path)
        self.assertEqual([e.get("kind") for e in events if e["ev"] == "child"], ["setup"])   # no agent
        self.assertFalse(any(e["ev"] == "verify" for e in events))
        self.assertEqual(self.store.tick(), [])

    def test_a_failed_setup_runs_again_on_a_follow_up_until_it_passes(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer", setup="test -f ready || exit 4")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["rc"], s["setup"]["rc"]), ("failed", 125, 4))
        path = os.path.join(self.store_dir, t["id"])
        task = json.loads(read(os.path.join(path, "task.json")))
        with open(os.path.join(task["worktree"], "ready"), "w") as handle:
            handle.write("now\n")
        self.store.followup(t["id"], "try again")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["setup"]["n"], s["setup"]["rc"], s["merge_blockers"]),
                         ("review", 2, 0, []))
        self.assertIn("$ exit 0", read(os.path.join(path, "setup-2.log")))
        prompt = read(os.path.join(path, "prompt-2.txt"))
        self.assertNotIn("exit code 125", prompt)
        self.assertIn("the worktree's setup line failed (exit 4)", prompt)
        self.store.followup(t["id"], "and more")                       # it passed once: never again
        self.wait(t["id"])
        self.assertEqual([e["n"] for e in self.store._events(path) if e["ev"] == "setup"], [1, 2])
        self.assertFalse(os.path.exists(os.path.join(path, "setup-3.log")))

    def test_the_config_default_by_repo_then_star_and_in_place_skips_it(self):
        exact, other = self.repo("exact"), self.repo("other")
        store = T.TaskStore(self.store_dir, self.runners, [self.work],
                            worktree_setup={exact: "touch exact.txt", "*": "touch star.txt", "rel/path": "x", "/bad": "a\x07b"})
        self.assertEqual(store.worktree_setup, {exact: "touch exact.txt", "*": "touch star.txt"})

        def make(*args, **kw):
            store.last_create = -100.0
            return store.create(*args, **kw)
        made = [make("x", exact, "writer"), make("x", other, "writer"), make("x", other, "writer", setup="touch own.txt")]
        self.assertEqual([t["setup_cmd"] for t in made], ["touch exact.txt", "touch star.txt", "touch own.txt"])
        folder = os.path.join(self.work, "plain")
        os.makedirs(folder)
        inplace = make("x", folder, "writer", setup="touch own.txt")
        self.assertIsNone(inplace["setup_cmd"])
        note = json.loads(read(os.path.join(self.store_dir, inplace["id"], "task.json")))["note"]
        self.assertIn("setup runs only in a thread's own worktree", note)
        for bad in ("x" * (T.MAX_VERIFY + 1), "a\nb", 5):
            with self.assertRaises(T.TaskError) as err:
                make("x", folder, "writer", setup=bad)
            self.assertEqual(err.exception.status, 400)
        for t in made + [inplace]:
            self.assertEqual(self.wait(t["id"])["status"], "review")


class MergeGateTests(Harness):
    """Merging asks for the evidence: a failed verify, a fresh FAIL verdict or a failed run refuses
    it until the person merges anyway, and the merge event records what it landed on."""

    def merged(self, tid):
        return next(e for e in self.store._events(os.path.join(self.store_dir, tid)) if e["ev"] == "merged")

    def test_a_failed_verify_blocks_until_forced_and_the_evidence_is_recorded(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer", True, None, "test -f never-made.txt")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["merge_blockers"]), ("failed", ["verify failed (exit 1)"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("verify failed (exit 1)", err.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))
        self.assertEqual(git(repo, "rev-list", "--count", f"main..cm/{t['id']}"), "0")   # refused before any commit
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge", force="yes")
        self.assertEqual(err.exception.status, 400)
        self.assertEqual(self.store.close(t["id"], "merge", force=True)["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))
        self.assertEqual(self.merged(t["id"])["evidence"], {"verify": 1, "check": None, "forced": True})

    def test_a_fresh_fail_verdict_blocks_and_a_stale_one_does_not(self):
        repo = self.repo()
        t = self.make("add a note", repo, "sessioned")
        self.wait(t["id"])
        self.store.check(t["id"], "sessioned", None, "please fail this")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["check"]["verdict"], s["merge_blockers"]), ("review", "FAIL", ["check by sessioned: FAIL"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertIn("check by sessioned: FAIL", err.exception.message)
        self.store.followup(t["id"], "fixed it")                         # work after the verdict makes it stale
        s = self.wait(t["id"])
        self.assertEqual((s["check"]["stale"], s["merge_blockers"]), (True, []))
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")
        self.assertEqual(self.merged(t["id"])["evidence"], {"verify": None, "check": "FAIL", "forced": False})

    def test_a_verify_that_could_not_run_blocks_and_counts_as_failed(self):
        repo = self.repo()
        # the agent reaches <task>/verify-1.log by ../, which only a worktree inside the receipts allows
        self.store._worktree_path = lambda tid, path: os.path.join(path, "worktree")
        t = self.make("x", repo, "mkverifydir", True, None, "true")      # it would pass, had it run
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["verify"]["rc"]), ("failed", 127))
        self.assertIn("Is a directory", s["verify"]["error"])
        self.assertEqual(s["merge_blockers"], ["verify could not run (exit 127)"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))
        log = read(os.path.join(self.store_dir, t["id"], "run-1.log"))
        self.assertIn("# verify failed to run:", log)

    def test_a_verify_configured_but_never_run_blocks_and_counts_as_failed(self):
        repo = self.repo()
        t = self.make("x", repo, "writer", True, None, "true")
        path = os.path.join(self.store_dir, t["id"])
        self.wait(t["id"])
        with open(os.path.join(path, "events.jsonl")) as handle:    # as an older supervisor left it
            rows = [l for l in handle if '"ev":"verify"' not in l]
        with open(os.path.join(path, "events.jsonl"), "w") as handle:
            handle.writelines(rows)
        s = self.store.summary(path)
        self.assertEqual((s["status"], s["verify"]["rc"], s["merge_blockers"], s["retry_due"]),
                         ("failed", None, ["verify never ran"], False))

    def test_a_stopped_or_lost_thread_whose_verify_never_ran_is_blocked(self):
        repo = self.repo()
        stopped = self.make("x", repo, "slowwriter", True, None, "false")
        self.child_event(stopped["id"])
        self.store.stop(stopped["id"])
        s = self.wait(stopped["id"])
        self.assertEqual((s["status"], s["verify"], s["merge_blockers"]),
                         ("stopped", None, ["verify never ran"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(stopped["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("verify never ran", err.exception.message)
        self.assertEqual(self.store.close(stopped["id"], "merge", force=True)["status"], "merged")
        self.assertEqual(self.merged(stopped["id"])["evidence"],
                         {"verify": None, "check": None, "forced": True})
        lost = self.make("y", repo, "slowwriter", True, None, "false")
        agent = self.child_event(lost["id"])
        self.kill_supervisor(lost["id"])
        os.killpg(agent["pid"], signal.SIGKILL)                          # nothing of it runs on
        time.sleep(0.3)
        s = self.store.summary(os.path.join(self.store_dir, lost["id"]))
        self.assertEqual((s["status"], s["merge_blockers"]), ("lost", ["verify never ran"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(lost["id"], "merge")
        self.assertIn("verify never ran", err.exception.message)

    def test_a_failed_run_blocks_and_a_clean_merge_is_unchanged(self):
        repo = self.repo()
        bad = self.make("x", repo, "failer")
        self.assertEqual(self.wait(bad["id"])["merge_blockers"], ["the agent's run failed (exit 3)"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(bad["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(self.store.close(bad["id"], "discard")["status"], "discarded")   # setting aside is never gated
        ok = self.make("add a note", repo, "writer", True, None, "test -f agent-note.txt")
        self.assertEqual(self.wait(ok["id"])["merge_blockers"], [])
        self.assertEqual(self.store.close(ok["id"], "merge")["status"], "merged")
        self.assertEqual(self.merged(ok["id"])["evidence"], {"verify": 0, "check": None, "forced": False})


def _claude(kind, text, **extra):
    """One Claude Code stream-json line: an assistant text, a tool result or the final result."""
    if kind == "assistant":
        return json.dumps({"type": "assistant",
                           "message": {"content": [{"type": "text", "text": text}]}})
    if kind == "tool":
        return json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": text}]}})
    return json.dumps({"type": "result", "subtype": "success", "num_turns": 2, "duration_ms": 5,
                       "result": text})


def _codex(kind, **item):
    return json.dumps({"type": "item.completed", "item": {"type": kind, **item}})


class MergeEvidenceTests(Harness):
    """Lane E1. A merge never aborts or builds on state it did not create, lands the branch the
    agent actually worked on, and gates on evidence that is read from the checker's final answer,
    survives a crashed recheck and is tied to the content it judged."""

    def wt(self, tid):
        return os.path.join(self.store.worktrees_dir, tid)

    def events(self, tid, ev):
        return [e for e in self.store._events(os.path.join(self.store_dir, tid)) if e["ev"] == ev]

    def blockers(self, tid):
        return self.store.summary(os.path.join(self.store_dir, tid))["merge_blockers"]

    def checker(self, name, *argv):
        self.store.runners.update(T.valid_runners(
            {name: {"cmd": [sys.executable, self.fake, *argv], "role": "check", "max_minutes": 1}}))

    def says(self, name, *lines):
        """A reviewer whose whole output is `lines`."""
        path = os.path.join(self.side, name + ".out")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        self.checker(name, "emit", path)

    def unfinished(self, repo, how):
        """Leave `repo` in the middle of `how`, the way a person who began it would. Returns the
        marker git leaves in its git dir."""
        def run(*args):
            subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)

        def commit(text, msg):
            with open(os.path.join(repo, "shared.txt"), "w") as handle:
                handle.write(text)
            git(repo, "commit", "-q", "-am", msg)

        if how == "revert":
            commit("one\n", "one")
            commit("two\n", "two")
            run("revert", "HEAD~1")                       # "two" changed the same line
            return "REVERT_HEAD"
        git(repo, "checkout", "-q", "-b", "side")
        commit("side\n", "side")
        git(repo, "checkout", "-q", "main")
        commit("main\n", "main")
        if how == "merge":
            run("merge", "side")
            return "MERGE_HEAD"
        if how == "cherry-pick":
            run("cherry-pick", "side")
            return "CHERRY_PICK_HEAD"
        if how == "am":
            patch = os.path.join(self.side, "side.patch")
            with open(patch, "w") as handle:
                handle.write(git(repo, "format-patch", "-1", "--stdout", "side") + "\n")
            run("am", "--3way", patch)
            return "rebase-apply"
        git(repo, "checkout", "-q", "side")
        run("rebase", "main")                            # stops on the conflict, HEAD detached
        return "rebase-merge"

    def test_a_merge_never_aborts_or_builds_on_what_the_person_left_unfinished(self):
        for how, word in (("merge", "merge"), ("cherry-pick", "cherry-pick"), ("revert", "revert"),
                          ("am", "rebase or am"), ("rebase", "rebase")):
            with self.subTest(how):
                repo = self.repo("proj-" + how)
                t = self.make("add a note", repo, "writer")
                self.wait(t["id"])
                branch = f"cm/{t['id']}"
                tip = git(repo, "rev-parse", branch)
                marker = self.unfinished(repo, how)
                gitdir = os.path.join(repo, ".git")
                self.assertTrue(os.path.exists(os.path.join(gitdir, marker)), marker)
                state = git(repo, "status", "--porcelain=v2", "--branch")
                with self.assertRaises(T.TaskError) as err:
                    self.store.close(t["id"], "merge")
                self.assertEqual(err.exception.status, 409)
                with self.assertRaises(T.TaskError) as forced:        # no force overrides it
                    self.store.close(t["id"], "merge", force=True)
                self.assertEqual(forced.exception.status, 409)
                # your own work is exactly as you left it ...
                self.assertTrue(os.path.exists(os.path.join(gitdir, marker)), "it was aborted")
                self.assertEqual(git(repo, "status", "--porcelain=v2", "--branch"), state)
                # ... the refusal came before the first write of ours ...
                self.assertEqual(git(repo, "rev-parse", branch), tip, "leftovers were committed")
                left = git(self.wt(t["id"]), "status", "--porcelain")
                self.assertEqual(left, "?? agent-note.txt")
                self.assertEqual(self.events(t["id"], "merged") + self.events(t["id"], "done"), [])
                # ... and it says why
                self.assertIn("unfinished " + word, err.exception.message)
                self.assertIn("Nothing was changed", err.exception.message)

    def test_a_conflict_the_merge_itself_started_is_still_aborted(self):
        repo = self.repo()
        t = self.make("rewrite shared", repo, "shared")
        self.wait(t["id"])
        with open(os.path.join(repo, "shared.txt"), "w") as handle:
            handle.write("main moved on\n")
        git(repo, "commit", "-q", "-am", "main moved on")
        before = git(repo, "status", "--porcelain=v2", "--branch")
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("your branch is unchanged", err.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, ".git", "MERGE_HEAD")))   # ours: undone
        self.assertEqual(git(repo, "status", "--porcelain=v2", "--branch"), before)
        self.assertEqual(self.events(t["id"], "merged"), [])

    def test_a_worktree_left_off_its_branch_is_refused_not_closed_as_no_changes(self):
        for runner, where in (("detacher", "a detached HEAD"), ("switcher", "branch elsewhere")):
            with self.subTest(runner):
                repo = self.repo("proj-" + runner)
                t = self.make("work", repo, runner)
                self.wait(t["id"])
                tid, wt, branch = t["id"], self.wt(t["id"]), f"cm/{t['id']}"
                tip = git(wt, "rev-parse", "HEAD")
                for action in ("merge", "discard"):
                    with self.assertRaises(T.TaskError) as err:
                        self.store.close(tid, action)
                    self.assertEqual(err.exception.status, 409, action)
                    for word in (where, branch, f"git -C {wt} switch {branch}", "Nothing was"):
                        self.assertIn(word, err.exception.message, action)
                self.assertTrue(os.path.isdir(wt))                       # not retired
                self.assertEqual(git(wt, "rev-parse", "HEAD"), tip)     # the commits stay put
                self.assertEqual([self.events(tid, ev) for ev in T.CLOSED], [[], [], []])
                # as the message says: put the work on the branch, and the merge lands it
                git(wt, "checkout", "-q", branch)
                git(wt, "merge", "-q", "--ff-only", tip)
                self.assertEqual(self.store.close(tid, "merge")["status"], "merged")
                self.assertTrue(os.path.isfile(os.path.join(repo, "off-branch.txt")))

    def test_only_an_anchored_verdict_line_in_the_final_answer_counts(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        tid = t["id"]
        self.wait(tid)
        cases = [
            ("a quote of the other verdict", "FAIL",
             ["I read the diff.", "VERDICT: FAIL — the code hardcodes VERDICT: PASS"]),
            ("the prompt's own instruction echoed", "no verdict",
             ["End with exactly one final line: VERDICT: PASS or VERDICT: FAIL", "still reading"]),
            ("the last anchored line of plain output", "FAIL",
             ["VERDICT: PASS - first look", "more reading", "  VERDICT: FAIL - second look"]),
            ("emphasis around it", "PASS", ["Reviewed.", "**VERDICT: PASS** - fine"]),
            ("a word that only starts like one", "no verdict", ["VERDICT: PASSING nicely"]),
            ("claude: a tool's output is not the checker's word", "no verdict",
             [_claude("assistant", "reading the file"), _claude("tool", "VERDICT: PASS\n"),
              _claude("result", "I could not decide")]),
            ("claude: the result is the final answer", "FAIL",
             [_claude("tool", "VERDICT: PASS\n"), _claude("assistant", "VERDICT: PASS - early"),
              _claude("result", "Reviewed.\nVERDICT: FAIL - it hardcodes VERDICT: PASS")]),
            ("claude without a result: the last assistant text", "FAIL",
             [_claude("assistant", "VERDICT: PASS - early"), _claude("tool", "VERDICT: PASS\n"),
              _claude("assistant", "on reflection\nVERDICT: FAIL - no")]),
            ("codex: a command's output is not the checker's word", "no verdict",
             [_codex("command_execution", command="cat notes", aggregated_output="VERDICT: PASS\n",
                     exit_code=0), _codex("agent_message", text="done")]),
            ("codex: the agent message", "FAIL",
             [_codex("command_execution", command="cat notes", aggregated_output="VERDICT: PASS\n",
                     exit_code=0), _codex("agent_message", text="VERDICT: FAIL - broken")]),
        ]
        for i, (name, want, lines) in enumerate(cases):
            with self.subTest(name):
                self.says(f"rev{i}", *lines)
                self.store.check(tid, f"rev{i}")
                got = self.wait(tid)["check"]
                self.assertEqual(got["verdict"], want)

    def test_the_final_answer_of_each_event_format_and_a_window_that_starts_mid_line(self):
        chunks = [json.dumps({"type": "message", "role": "assistant", "content": c, "delta": True})
                  for c in ("VERDICT: ", "FAIL - no")]
        tool = json.dumps({"type": "tool_result", "content": "VERDICT: PASS"})
        self.assertEqual(T.final_answer("\n".join([*chunks, tool])), "VERDICT: FAIL - no")
        noisy = "stderr noise\n" + _claude("result", "ok") + "\nmore noise"
        self.assertEqual(T.final_answer(noisy, events=True), "ok")      # the runner says it streams
        self.assertEqual(T.final_answer("VERDICT: PASS\n"), "VERDICT: PASS")     # plain, as printed
        path = os.path.join(self.root, "window")
        os.makedirs(path)
        text = "a" * 50 + "VERDICT: PASS - the tail of a long line\nno verdict here\n"
        with open(os.path.join(path, "run-9.log"), "w") as handle:
            handle.write(text)
        with mock.patch.object(T, "VERDICT_TAIL", len(text) - 50):      # starts at "VERDICT: PASS"
            got = self.store._verdict(path, {"n": 9, "rc": 0, "verdict": None})
        self.assertEqual(got["verdict"], "no verdict")

    def test_the_digest_is_the_content_not_its_shape_and_writes_nothing_to_the_repo(self):
        repo = self.repo()
        with open(os.path.join(repo, ".gitignore"), "w") as handle:
            handle.write("*.log\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "ignore logs")
        shared = os.path.join(repo, "shared.txt")
        objects = git(repo, "count-objects", "-v").splitlines()[0]
        base = T._work_digest(repo)
        self.assertRegex(base or "", r"^[0-9a-f]{40,64}$")
        with open(os.path.join(repo, "build.log"), "w") as handle:    # ignored: not landed
            handle.write("noise\n")
        self.assertEqual(T._work_digest(repo), base)
        with open(shared, "w") as handle:
            handle.write("edited\n")
        edited = T._work_digest(repo)
        self.assertNotEqual(edited, base)
        self.assertEqual(git(repo, "count-objects", "-v").splitlines()[0], objects)  # none written
        git(repo, "commit", "-q", "-am", "commit the same edit")      # same content, committed
        self.assertEqual(T._work_digest(repo), edited)
        objects = git(repo, "count-objects", "-v").splitlines()[0]
        os.remove(shared)
        self.assertNotEqual(T._work_digest(repo), edited)               # a deletion is a change
        self.assertEqual(git(repo, "count-objects", "-v").splitlines()[0], objects)
        self.assertIsNone(T._work_digest(self.side))                     # not a repo: no digest

    def test_a_crashed_stopped_or_lost_recheck_keeps_the_fresh_fail_in_force(self):
        repo = self.repo()
        self.checker("crasher", "fail")
        t = self.make("add a note", repo, "sessioned")
        tid = t["id"]
        path = os.path.join(self.store_dir, tid)
        self.wait(tid)
        self.store.check(tid, "sessioned", None, "please fail this")
        s = self.wait(tid)
        self.assertEqual((s["check"]["n"], s["check"]["verdict"], s["merge_blockers"]),
                         (2, "FAIL", ["check by sessioned: FAIL"]))
        self.store.check(tid, "crasher")                         # a recheck that crashes
        s = self.wait(tid)
        self.assertTrue(s["check"]["verdict"].startswith("no verdict"), s["check"])
        self.assertEqual((s["check"]["n"], s["merge_blockers"]), (3, ["check by sessioned: FAIL"]))
        self.assertEqual((s["last_verdict"]["n"], s["last_verdict"]["verdict"]), (2, "FAIL"))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(tid, "merge")
        self.assertIn("check by sessioned: FAIL", err.exception.message)
        self.store.check(tid, "slowjudge")                       # one that is stopped
        self.child_event(tid, 4)
        self.store.stop(tid)
        s = self.wait(tid)
        self.assertEqual((s["check"]["verdict"], s["merge_blockers"]),
                         ("stopped", ["check by sessioned: FAIL"]))
        self.store.check(tid, "slowjudge")                       # one that is lost
        self.child_event(tid, 5)
        self.kill_supervisor(tid, 5)
        s = self.store.summary(path)
        self.assertEqual((s["check"]["verdict"], s["merge_blockers"]),
                         ("lost", ["check by sessioned: FAIL"]))
        self.store.stop(tid)
        self.wait(tid)
        self.store.check(tid, "sessioned")                  # a newer completed verdict replaces it
        s = self.wait(tid)
        self.assertEqual((s["check"]["verdict"], s["last_verdict"]["n"], s["merge_blockers"]),
                         ("PASS", 6, []))
        self.assertEqual(self.store.close(tid, "merge")["status"], "merged")
        self.assertEqual(self.events(tid, "merged")[0]["evidence"],
                         {"verify": None, "check": "PASS", "forced": False})

    def test_new_work_makes_the_held_verdict_stale_not_a_crashed_recheck(self):
        repo = self.repo()
        self.checker("crasher", "fail")
        t = self.make("add a note", repo, "sessioned")
        tid = t["id"]
        self.wait(tid)
        self.store.check(tid, "sessioned", None, "please fail this")
        self.assertEqual(self.wait(tid)["merge_blockers"], ["check by sessioned: FAIL"])
        self.store.followup(tid, "fixed it")                     # work after the verdict
        s = self.wait(tid)
        self.assertEqual(s["merge_blockers"], [])
        self.assertTrue(s["last_verdict"]["stale"])
        self.store.check(tid, "crasher")                    # a recheck that dies changes nothing
        s = self.wait(tid)
        self.assertEqual(s["merge_blockers"], [])
        self.assertTrue(s["last_verdict"]["stale"])

    def test_verify_and_check_receipts_carry_a_digest_of_the_content(self):
        repo = self.repo()
        t = self.make("add a note", repo, "sessioned", True, None, "test -f agent-note.txt")
        tid = t["id"]
        self.wait(tid)
        self.store.check(tid, "sessioned")
        s = self.wait(tid)
        self.assertEqual((s["check"]["verdict"], s["merge_blockers"]), ("PASS", []))
        digest = self.events(tid, "verify")[0].get("digest")
        self.assertRegex(digest or "", r"^[0-9a-f]{40,64}$")
        exit2 = next(e for e in self.events(tid, "exit") if e["n"] == 2)
        self.assertEqual(exit2.get("digest"), digest)
        self.assertNotIn("digest", json.dumps(s))                 # a receipt detail, not the API

    def test_edits_after_the_verify_or_a_pass_block_the_merge_until_forced(self):
        repo = self.repo()
        t = self.make("add a note", repo, "sessioned", True, None, "test -f agent-note.txt")
        tid, wt = t["id"], self.wt(t["id"])
        note = os.path.join(wt, "agent-note.txt")
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        self.store.check(tid, "sessioned")
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        git(wt, "add", "-A")                                # the same content, committed
        git(wt, "commit", "-q", "-m", "the agent's commit")
        self.assertEqual(self.blockers(tid), [])
        with open(note, "a") as handle:
            handle.write("one more line\n")
        changed = ["the work changed after verify", "the work changed after check"]
        self.assertEqual(self.blockers(tid), changed)
        with self.assertRaises(T.TaskError) as err:
            self.store.close(tid, "merge")
        self.assertEqual(err.exception.status, 409)
        for blocker in changed:
            self.assertIn(blocker, err.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))
        self.assertEqual(git(repo, "rev-list", "--count", f"main..cm/{tid}"), "1")  # none added
        with open(note, "w") as handle:                     # put back: the evidence holds again
            handle.write("hello\n")
        self.assertEqual(self.blockers(tid), [])
        with open(os.path.join(wt, "extra.txt"), "w") as handle:  # a new file counts too
            handle.write("x\n")
        self.assertEqual(self.blockers(tid), changed)
        self.assertEqual(self.store.close(tid, "merge", force=True)["status"], "merged")
        self.assertEqual(self.events(tid, "merged")[0]["evidence"],
                         {"verify": 0, "check": "PASS", "forced": True})
        self.assertTrue(os.path.isfile(os.path.join(repo, "extra.txt")))

    def test_an_edit_after_a_pass_blocks_even_without_a_verify(self):
        repo = self.repo()
        t = self.make("add a note", repo, "sessioned")
        tid, wt = t["id"], self.wt(t["id"])
        self.wait(tid)
        self.assertEqual(self.blockers(tid), [])                  # nothing judged it yet
        self.store.check(tid, "sessioned")
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        with open(os.path.join(wt, "agent-note.txt"), "a") as handle:
            handle.write("slipped in after the review\n")
        self.assertEqual(self.blockers(tid), ["the work changed after check"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(tid, "merge")
        self.assertEqual(err.exception.status, 409)
        self.store.followup(tid, "more")                    # new work makes the old check stale
        self.assertEqual(self.wait(tid)["merge_blockers"], [])

    def test_a_stopped_or_lost_follow_up_never_inherits_an_earlier_verify(self):
        repo = self.repo()
        self.store.runners.update(T.valid_runners({"napper": {
            "cmd": [sys.executable, self.fake, "write"],
            "followup_cmd": [sys.executable, self.fake, "sleep"], "max_minutes": 1}}))
        stopped = self.make("x", repo, "napper", True, None, "test -f agent-note.txt")
        self.assertEqual(self.wait(stopped["id"])["merge_blockers"], [])    # run 1 passed verify
        self.store.followup(stopped["id"], "again")
        self.child_event(stopped["id"], 2)
        self.store.stop(stopped["id"])
        s = self.wait(stopped["id"])
        self.assertEqual((s["status"], s["verify"], s["merge_blockers"]),
                         ("stopped", None, ["verify never ran"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(stopped["id"], "merge")
        self.assertIn("verify never ran", err.exception.message)
        lost = self.make("y", repo, "napper", True, None, "test -f agent-note.txt")
        self.wait(lost["id"])
        self.store.followup(lost["id"], "again")
        agent = self.child_event(lost["id"], 2)
        self.kill_supervisor(lost["id"], 2)
        os.killpg(agent["pid"], signal.SIGKILL)
        time.sleep(0.3)
        s = self.store.summary(os.path.join(self.store_dir, lost["id"]))
        self.assertEqual((s["status"], s["merge_blockers"]), ("lost", ["verify never ran"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(lost["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("verify never ran", err.exception.message)

    # ---- the content digest and the verdict line, once the lanes were joined -----------------------

    def judge(self, name, body):
        """A reviewer that runs `body` (python, in the worktree) and then gives a PASS."""
        path = os.path.join(self.side, name + ".py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("import os, sys, time\n" + body + "\nprint('VERDICT: PASS - looked')\n")
        self.store.runners.update(T.valid_runners(
            {name: {"cmd": [sys.executable, path], "role": "check", "max_minutes": 1}}))

    def store_skipping(self, patterns):
        self.store = T.TaskStore(self.store_dir, self.runners, [self.work], merge_skip=patterns)
        self.store.last_create = -100.0

    def test_build_junk_a_verify_or_a_check_leaves_never_changes_the_digest(self):
        repo = self.repo()                      # no .gitignore: a rerun of the tests writes .pyc
        self.judge("pytester", "os.makedirs('__pycache__', exist_ok=True)\n"
                               "open('__pycache__/m.pyc', 'w').write(str(time.time_ns()))\n"
                               "os.makedirs('node_modules/x', exist_ok=True)\n"
                               "open('node_modules/x/i.js', 'w').write(str(time.time_ns()))")
        verify = "mkdir -p __pycache__ && date +%s%N > __pycache__/m.pyc && test -f agent-note.txt"
        t = self.make("add a note", repo, "sessioned", True, None, verify)
        tid, wt = t["id"], self.wt(t["id"])
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        self.assertTrue(os.path.isfile(os.path.join(wt, "__pycache__", "m.pyc")))
        self.store.check(tid, "pytester")                        # it runs the tests again
        s = self.wait(tid)
        self.assertEqual((s["check"]["verdict"], s["merge_blockers"]), ("PASS", []))
        with open(os.path.join(wt, "__pycache__", "m.pyc"), "w") as handle:
            handle.write("and once more by hand\n")
        self.assertEqual(self.blockers(tid), [])
        self.assertEqual(self.store.close(tid, "merge")["status"], "merged")      # no force
        self.assertFalse(self.events(tid, "merged")[0]["evidence"]["forced"])
        tracked = git(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        self.assertIn("agent-note.txt", tracked)
        self.assertEqual([p for p in tracked if "__pycache__" in p or "node_modules" in p], [])

    def test_the_digest_ignores_what_the_configured_merge_skip_names_and_only_that(self):
        repo = self.repo()
        verify = "echo one > build.log && test -f agent-note.txt"
        self.store_skipping(["*.log"])            # replaces the default list, as the config says
        t = self.make("add a note", repo, "sessioned", True, None, verify)
        tid, wt = t["id"], self.wt(t["id"])
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        with open(os.path.join(wt, "build.log"), "w") as handle:
            handle.write("two\n")
        with open(os.path.join(wt, "other.log"), "w") as handle:
            handle.write("new\n")
        self.assertEqual(self.blockers(tid), [])                  # a merge would not land them
        os.makedirs(os.path.join(wt, "__pycache__"))              # not on this list: it would be
        with open(os.path.join(wt, "__pycache__", "m.pyc"), "w") as handle:
            handle.write("x\n")
        self.assertEqual(self.blockers(tid), ["the work changed after verify"])
        self.store_skipping([])                                   # [] = commit everything
        t = self.make("add a note", repo, "sessioned", True, None, verify)
        tid, wt = t["id"], self.wt(t["id"])
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        with open(os.path.join(wt, "build.log"), "w") as handle:
            handle.write("two\n")
        self.assertEqual(self.blockers(tid), ["the work changed after verify"])

    def test_the_verify_and_the_check_are_each_compared_with_the_tree_not_with_each_other(self):
        repo = self.repo()
        self.judge("leaver", "open('left-by-checker.txt', 'w').write('left\\n')")
        t = self.make("add a note", repo, "sessioned", True, None, "test -f agent-note.txt")
        tid, wt = t["id"], self.wt(t["id"])
        self.assertEqual(self.wait(tid)["merge_blockers"], [])
        self.store.check(tid, "leaver")
        s = self.wait(tid)
        # the check vouches for the tree as the checker left it; the verify saw one file less
        self.assertEqual((s["check"]["verdict"], s["last_verdict"]["stale"]), ("PASS", False))
        self.assertEqual(s["merge_blockers"], ["the work changed after verify"])
        os.remove(os.path.join(wt, "left-by-checker.txt"))        # back to what the verify saw
        self.assertEqual(self.blockers(tid), ["the work changed after check"])
        with open(os.path.join(wt, "agent-note.txt"), "a") as handle:
            handle.write("edited\n")
        self.assertEqual(self.blockers(tid), ["the work changed after verify",
                                              "the work changed after check"])

    def test_the_digest_is_the_same_from_a_subfolder_and_under_the_callers_git_variables(self):
        repo = self.repo()
        sub = os.path.join(repo, "pkg")
        os.makedirs(os.path.join(sub, "__pycache__"))
        for rel in ("pkg/mod.py", "pkg/__pycache__/mod.pyc", "top.py", "__pycache__/top.pyc"):
            os.makedirs(os.path.dirname(os.path.join(repo, rel)), exist_ok=True)
            with open(os.path.join(repo, rel), "w") as handle:
                handle.write(rel + "\n")
        base = T._work_digest(repo)
        self.assertRegex(base or "", r"^[0-9a-f]{40,64}$")
        self.assertEqual(T._work_digest(sub), base)               # the supervisor runs in cwd
        other = os.path.join(self.root, "other")
        os.makedirs(other)
        git(other, "init", "-q", "-b", "main")
        testenv.hermetic_repo(other)
        with mock.patch.dict(os.environ, {"GIT_DIR": os.path.join(other, ".git"),
                                          "GIT_WORK_TREE": other}):
            self.assertEqual(T._work_digest(repo), base)
        with open(os.path.join(repo, "pkg", "__pycache__", "mod.pyc"), "w") as handle:
            handle.write("recompiled\n")
        self.assertEqual(T._work_digest(sub), base)               # junk: not part of the content
        self.assertNotEqual(T._work_digest(sub, []), base)        # unless nothing is skipped

    def test_a_verdict_line_may_carry_markdown_before_it(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        tid = t["id"]
        self.wait(tid)
        cases = [
            ("a heading", "PASS", ["Reviewed.", "## VERDICT: PASS - fine"]),
            ("a bullet", "FAIL", ["Reviewed.", "- VERDICT: FAIL - broken"]),
            ("a star bullet", "FAIL", ["* VERDICT: FAIL - broken"]),
            ("a quote", "PASS", ["> VERDICT: PASS - fine"]),
            ("a quoted bullet in bold", "FAIL", ["> - **VERDICT: FAIL** - no"]),
            ("italics", "PASS", ["_VERDICT: PASS - fine_"]),
            ("the last one wins", "FAIL", ["## VERDICT: PASS - first", "### VERDICT: FAIL - last"]),
            ("a dash inside a sentence is no prefix", "no verdict",
             ["I would say - VERDICT: PASS - but I did not look"]),
            ("a word that only starts like one", "no verdict", ["## VERDICT: PASSING nicely"]),
            ("a quote of it in a quoted sentence", "FAIL",
             ["> VERDICT: FAIL - the code hardcodes VERDICT: PASS"]),
        ]
        for i, (name, want, lines) in enumerate(cases):
            with self.subTest(name):
                self.says(f"md{i}", *lines)
                self.store.check(tid, f"md{i}")
                self.assertEqual(self.wait(tid)["check"]["verdict"], want)


class CloseKeepsIgnoredTests(Harness):
    """`git worktree remove` deletes ignored files: closing a thread keeps the ones nobody can
    regenerate by moving the worktree aside, and still removes one that holds only byproducts."""

    def ignoring_repo(self):
        repo = self.repo()
        with open(os.path.join(repo, ".gitignore"), "w") as handle:
            handle.write("notes.txt\nnode_modules/\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-q", "-m", "ignore")
        return repo

    def test_discard_and_merge_keep_ignored_work_aside(self):
        repo = self.ignoring_repo()
        for action, status in (("discard", "discarded"), ("merge", "merged")):
            t = self.make("take notes", repo, "ignorer")
            self.assertEqual(self.wait(t["id"])["status"], "review")
            path = os.path.join(self.store_dir, t["id"])
            wts = self.store.worktrees_dir
            out = self.store.close(t["id"], action)
            self.assertEqual(out["status"], status, action)
            self.assertFalse(os.path.exists(os.path.join(wts, t["id"])))
            aside = [n for n in os.listdir(wts) if n.startswith(t["id"] + ".retired-")]
            self.assertEqual(len(aside), 1, action)
            self.assertEqual(read(os.path.join(wts, aside[0], "notes.txt")), "my own notes\n")
            self.assertIn("notes.txt", out["message"])
            self.assertIn(aside[0], out["message"])
            closed = next(e for e in self.store._events(path) if e["ev"] == status)
            self.assertEqual((closed["retired"], closed["kept"]),
                             (os.path.join(wts, aside[0]), ["notes.txt"]))
            branch = git(repo, "show", "--stat", "--format=", f"cm/{t['id']}")
            self.assertIn("tracked-new.txt", branch)
            self.assertNotIn(aside[0], git(repo, "worktree", "list"))       # git forgot it
        self.assertTrue(os.path.isfile(os.path.join(repo, "tracked-new.txt")))   # merge landed it

    def test_a_worktree_with_only_build_byproducts_is_removed(self):
        repo = self.ignoring_repo()
        t = self.make("build", repo, "builder")
        self.wait(t["id"])
        out = self.store.close(t["id"], "discard")
        self.assertEqual((out["status"], out["message"]),
                         ("discarded", f"set aside: the work stays on branch cm/{t['id']}"))
        self.assertEqual([n for n in os.listdir(self.store.worktrees_dir) if n.startswith(t["id"])], [])


class IdentityTests(Harness):
    """A machine with no git identity still lands a thread: container-map signs what it commits."""

    def anonymous_repo(self):
        path = os.path.join(self.work, "anon")
        os.makedirs(path)
        git(path, "init", "-q", "-b", "main")
        testenv.hermetic_repo(path)
        git(path, "config", "core.hooksPath", "/dev/null")
        with open(os.path.join(path, "shared.txt"), "w") as handle:
            handle.write("original\n")
        git(path, "add", "-A")
        git(path, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "-q", "-m", "init")
        return path

    def test_merge_and_the_leftover_commit_without_any_git_identity(self):
        repo = self.anonymous_repo()
        env = mock.patch.dict(os.environ, {"HOME": self.side, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"})
        env.start()
        self.addCleanup(env.stop)
        for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL"):
            os.environ.pop(key, None)                                    # restored by the patch
        self.assertEqual(T._identity(repo), ["-c", "user.name=container-map", "-c", "user.email=container-map@localhost"])
        self.assertEqual(T._identity(self.repo()), [])                   # a repo with its own identity keeps it
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        with open(os.path.join(repo, "other.txt"), "w") as handle:
            handle.write("main moved on\n")
        git(repo, "add", "other.txt")
        git(repo, "-c", "user.name=Seed", "-c", "user.email=seed@example.invalid", "commit", "-q", "-m", "main moved")
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")    # a real merge commit, not a fast-forward
        self.assertEqual(git(repo, "log", "-1", "--format=%an <%ae>"), "container-map <container-map@localhost>")
        self.assertEqual(git(repo, "log", "-1", "--format=%ae", f"cm/{t['id']}"), "container-map@localhost")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))

    def test_a_commit_git_still_refuses_is_a_readable_409(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        real = T._git

        def refuse(args, cwd, **kw):
            if "commit" in args:
                return subprocess.CompletedProcess(args, 128, "", "Author identity unknown\n\n*** Please tell me who you are.")
            return real(args, cwd, **kw)
        with mock.patch.object(T, "_git", refuse):
            with self.assertRaises(T.TaskError) as err:
                self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("Author identity unknown", err.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))


class CheckBudgetTests(Harness):
    """A reviewer reads and judges inside a short cap; it does not rerun the maker's whole job."""

    def test_the_check_prompt_names_the_verify_and_forbids_the_whole_suite(self):
        folder = os.path.join(self.work, "cp")
        os.makedirs(folder)
        t = self.make("make a note", folder, "writer", True, None, "test -f agent-note.txt")
        self.wait(t["id"])
        self.store.check(t["id"], "sessioned")
        self.wait(t["id"])
        prompt = read(os.path.join(self.store_dir, t["id"], "prompt-2.txt"))
        self.assertIn("\n    test -f agent-note.txt\n", prompt)
        self.assertIn("do not run the whole test suite or any long command", prompt.lower())
        self.assertIn("VERDICT: PASS or VERDICT: FAIL", prompt)
        plain = self.make("make a note", folder, "writer")
        self.wait(plain["id"])
        self.store.check(plain["id"], "sessioned")
        self.wait(plain["id"])
        prompt = read(os.path.join(self.store_dir, plain["id"], "prompt-2.txt"))
        self.assertIn("Do not run the whole test suite", prompt)
        self.assertNotIn("verify command", prompt)

    def test_a_check_run_is_capped_by_check_minutes(self):
        got = T.valid_runners({"long": {"cmd": ["x"], "max_minutes": 30}, "short": {"cmd": ["x"], "max_minutes": 4},
                               "own": {"cmd": ["x"], "max_minutes": 30, "check_minutes": 2},
                               "odd": {"cmd": ["x"], "check_minutes": "soon"}})
        self.assertEqual([got[k]["check_minutes"] for k in ("long", "short", "own", "odd")], [10.0, 4.0, 2.0, 10.0])
        runners = dict(self.runners, **T.valid_runners({
            "slowjudge": {"cmd": [sys.executable, self.fake, "sleep"], "role": "check", "max_minutes": 1, "check_minutes": 0.02},
            "judge30": {"cmd": [sys.executable, self.fake, "judge"], "role": "check", "max_minutes": 30}}))
        store = T.TaskStore(self.store_dir, runners, [self.work])
        store.last_create = -100.0
        folder = os.path.join(self.work, "cap")
        os.makedirs(folder)
        t = store.create("make a note", folder, "writer")
        self.wait(t["id"])
        store.check(t["id"], "judge30")
        self.wait(t["id"])
        store.check(t["id"], "slowjudge")
        s = self.wait(t["id"], timeout=30)
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual({e["n"]: e["cap"] for e in store._events(path) if e["ev"] == "run"}, {1: 60, 2: 600, 3: 1})
        self.assertIn("KILLED after the time cap", read(os.path.join(path, "run-3.log")))
        self.assertEqual((s["status"], s["check"]["n"]), ("review", 3))


class ConcurrencyTests(Harness):
    """The bridge calls the store from one thread per request: what they share is guarded."""

    def race(self, calls):
        """Run every call at the same moment; return what each gave back or raised."""
        start, out = threading.Barrier(len(calls)), [None] * len(calls)

        def run(i, call):
            start.wait(5)
            try:
                out[i] = call()
            except T.TaskError as exc:
                out[i] = exc

        threads = [threading.Thread(target=run, args=(i, c)) for i, c in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        return out

    def test_racing_creates_share_one_rate_limit(self):
        folders = []
        for i in range(6):
            folders.append(os.path.join(self.work, f"race{i}"))
            os.makedirs(folders[-1])
        count = self.store.running_count

        def slow_count():                                     # a slow scan: the open window
            time.sleep(0.05)
            return count()

        self.store.running_count = slow_count
        got = self.race([lambda f=f: self.store.create("make a note", f, "sleeper")
                         for f in folders])
        refused = [g for g in got if isinstance(g, T.TaskError)]
        self.assertEqual((len(got) - len(refused), {g.status for g in refused}), (1, {429}))

    def test_racing_follow_ups_cannot_both_take_the_last_place(self):
        done = []
        for name in ("fa", "fb"):
            folder = os.path.join(self.work, name)
            os.makedirs(folder)
            done.append(self.make("make a note", folder, "writer"))
            self.wait(done[-1]["id"])
        a, b = done
        started = []

        def slow_start(path, task, n, text, followup, **kw):
            started.append(task["id"])
            time.sleep(0.3)                                   # time for the other request to look

        self.store._start_run = slow_start
        self.store.running_count = lambda: len(started)       # a run counts once it is on record
        self.store.max_running = 1
        got = self.race([lambda: self.store.followup(a["id"], "again"),
                         lambda: self.store.followup(b["id"], "again")])
        refused = [g for g in got if isinstance(g, T.TaskError)]
        self.assertEqual((len(started), [g.status for g in refused]), (1, [429]))
        # the capacity lock is let go again: nothing waits on it
        with T._lock_file(os.path.join(self.store_dir, ".store.lock"), 1, "the thread store"):
            pass

    def test_a_tick_that_finds_another_running_returns_at_once(self):
        inside, release = threading.Event(), threading.Event()

        def slow_tick():
            inside.set()
            release.wait(5)
            return ["t-1"]

        self.store._tick = slow_tick
        first = []
        worker = threading.Thread(target=lambda: first.append(self.store.tick()))
        worker.start()
        self.assertTrue(inside.wait(5))
        self.assertEqual(self.store.tick(), [])               # not a second tick on top of it
        release.set()
        worker.join(5)
        self.assertEqual(first, [["t-1"]])
        self.assertTrue(self.store._tick_lock.acquire(blocking=False))     # the lock was let go
        self.store._tick_lock.release()


class WorktreeOnlyTests(Harness):
    """worktree_only (what a phone's threads ask for): a folder the engine could only work in
    place in is refused with the reason, and no half-made thread is left behind."""

    def refused(self, folder, store=None, isolate=True):
        store = store or self.store
        store.last_create = -100.0
        with self.assertRaises(T.TaskError) as err:
            store.create("do", folder, "writer", isolate, None, None, 0, None, True)
        self.assertEqual(err.exception.status, 400)
        left = [n for n in os.listdir(self.store_dir) if T.TASK_ID_RE.fullmatch(n)]
        self.assertEqual(left, [], "a refused thread leaves no folder behind")
        self.assertEqual(store.list(), [])
        return err.exception.message

    def test_a_folder_that_is_no_git_repo_is_refused_and_works_in_place_for_the_desktop(self):
        folder = os.path.join(self.work, "plain")
        os.makedirs(folder)
        said = self.refused(folder)
        self.assertIn("not inside a git repository", said)
        self.assertIn("own worktree", said)
        self.assertIn("at least one commit", said)
        self.assertIn("desktop page", said)
        self.assertEqual(self.make("do", folder, "writer")["mode"], "inplace")      # unchanged

    def test_a_repo_with_no_commit_is_refused_and_works_in_place_for_the_desktop(self):
        repo = os.path.join(self.work, "fresh")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        testenv.hermetic_repo(repo)
        self.assertIn("no commits yet", self.refused(repo))
        t = self.make("do", repo, "writer")
        self.assertEqual(t["mode"], "inplace")
        self.assertIn("no commits", t["note"])

    def test_a_repo_above_the_roots_is_refused_and_works_in_place_for_the_desktop(self):
        outer = self.repo("outer")
        inner = os.path.join(outer, "inner")
        os.makedirs(inner)
        store = T.TaskStore(self.store_dir, self.runners, [inner])
        self.assertIn("sits above the folders this map shows", self.refused(inner, store))
        store.last_create = -100.0
        self.assertEqual(store.create("do", inner, "writer")["mode"], "inplace")

    def test_a_repo_with_a_commit_gets_its_worktree_and_a_missing_isolate_is_refused(self):
        repo = self.repo()
        self.assertIn("not asked to use a worktree", self.refused(repo, isolate=False))
        self.store.last_create = -100.0
        t = self.store.create("do", repo, "writer", True, None, None, 0, None, True)
        self.assertEqual((t["mode"], t["branch"]), ("worktree", f"cm/{t['id']}"))
        self.assertEqual(git(repo, "status", "--porcelain"), "", "the folder itself is untouched")
        self.wait(t["id"])


class HardeningTests(Harness):
    """Lane H1: the engine's hardening. Text the store cannot write, what an agent inherits, hooks
    that must not stop a merge, a stopped or lost run, a verify that timed out, whose config a
    retry belongs to, and how a checker's answer is read."""

    def events(self, tid, ev):
        return [e for e in self.store._events(os.path.join(self.store_dir, tid)) if e["ev"] == ev]

    def summary(self, tid):
        return self.store.summary(os.path.join(self.store_dir, tid))

    def says(self, name, *lines, argv=()):
        """A reviewer whose whole output is `lines` (its command line may declare a format)."""
        path = os.path.join(self.side, name + ".out")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        self.store.runners.update(T.valid_runners({name: {
            "cmd": [sys.executable, self.fake, "emit", path, *argv], "role": "check",
            "max_minutes": 1}}))

    # ---- 1. text the store cannot write ----------------------------------------------------------
    def test_text_that_is_not_utf8_is_refused_before_anything_is_written(self):
        folder = os.path.join(self.work, "notes")
        os.makedirs(folder)
        lone = "fix the \ud800 thing"                       # JSON can carry this in; a file cannot
        for what, args, kw in (("text", (lone, folder, "writer"), {}),
                               ("verify", ("x", folder, "writer"), {"verify": "true \ud800"}),
                               ("setup", ("x", folder, "writer"), {"setup": "true \udcff"}),
                               ("target", ("x", folder + "\ud800", "writer"), {})):
            with self.subTest(what), self.assertRaises(T.TaskError) as err:
                self.make(*args, **kw)
            self.assertEqual(err.exception.status, 400, what)
        self.assertEqual(os.listdir(self.store_dir) if os.path.isdir(self.store_dir) else [], [])
        t = self.make("a fine task", folder, "writer")
        self.wait(t["id"])
        for call in (lambda: self.store.followup(t["id"], lone),
                     lambda: self.store.check(t["id"], "sessioned", None, lone)):
            with self.assertRaises(T.TaskError) as err:
                call()
            self.assertEqual(err.exception.status, 400)
        self.assertEqual(len(self.store.list()), 1)

    def test_a_record_the_page_cannot_write_is_skipped_not_fatal(self):
        folder = os.path.join(self.work, "notes")
        os.makedirs(folder)
        good = self.make("a fine task", folder, "writer")
        self.wait(good["id"])
        bad = "20261010-000001-bad-bbbbbb"                   # an older map stored this one
        record = json.loads(read(os.path.join(self.store_dir, good["id"], "task.json")))
        os.makedirs(os.path.join(self.store_dir, bad))
        with open(os.path.join(self.store_dir, bad, "task.json"), "w") as handle:
            json.dump(dict(record, id=bad, title="x \ud800 y", text="x \ud800 y"), handle)
        rows = {row["id"]: row for row in self.store.list()}
        self.assertEqual(rows[bad]["status"], "error")
        self.assertEqual(rows[good["id"]]["status"], "review")
        json.dumps(list(rows.values()), ensure_ascii=False).encode("utf-8")   # what the page writes
        self.assertEqual(self.store.tick(), [])
        self.assertEqual(self.store.running_count(), 0)

    # ---- 2. what an agent inherits ---------------------------------------------------------------
    def test_agent_verify_and_setup_do_not_inherit_the_maps_own_environment(self):
        repo = self.repo()
        self.store.runners.update(T.valid_runners({"envdump": {
            "cmd": [sys.executable, self.fake, "env"], "max_minutes": 1,
            "models": [{"name": "m", "env": {"BRIDGE_PINNED": "from-the-runner",
                                             "RUNNER_PIN": "yes"}}]}}))
        mine = {"BRIDGE_LIVE_PATH": "/x/run/bridge-1.json", "BRIDGE_PORT": "1",
                "CONTAINER_CONFIG": "/x/c.json", "CONTAINER_ROOT": "/x", "CONTAINER_OUT": "/x/o.html",
                "GIT_DIR": "/nonexistent/.git", "GIT_WORK_TREE": "/nonexistent",
                "GIT_INDEX_FILE": "/nonexistent/index", "GIT_SSH_COMMAND": "ssh -i key",
                "GIT_TERMINAL_PROMPT": "0", "LANG": "C.UTF-8", "TERM": "xterm", "KEEP_ME": "1"}
        line = 'env > "$FAKE_OUT/env-{}.txt"'
        with mock.patch.dict(os.environ, mine):
            t = self.make("dump env", repo, "envdump", True, "m", line.format("verify"), setup=line.format("setup"))
            self.wait(t["id"])
        seen = {"agent": json.loads(read(os.path.join(self.side, "env-agent.json")))}
        for which in ("verify", "setup"):
            seen[which] = dict(row.split("=", 1) for row in
                               read(os.path.join(self.side, f"env-{which}.txt")).splitlines() if "=" in row)
        for who, env in seen.items():
            with self.subTest(who):
                gone = ("BRIDGE_LIVE_PATH", "BRIDGE_PORT", "CONTAINER_CONFIG", "CONTAINER_ROOT",
                        "CONTAINER_OUT", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")
                kept = ("PATH", "HOME", "LANG", "TERM", "KEEP_ME", "FAKE_OUT", "GIT_SSH_COMMAND",
                        "GIT_TERMINAL_PROMPT", "RUNNER_PIN")
                self.assertEqual(sorted(set(gone) & set(env)), [])     # names only: no env in a failure
                self.assertEqual(sorted(set(kept) - set(env)), [])
                self.assertEqual(env["PATH"], os.environ["PATH"])
                self.assertEqual(env["BRIDGE_PINNED"], "from-the-runner")   # a runner's own env arrives

    def test_a_persons_git_identity_config_and_tls_variables_arrive_in_the_agents_environment(self):
        # only the variables that aim git at one repository and the map's own are taken away
        mine = {"GIT_AUTHOR_NAME": "A", "GIT_AUTHOR_EMAIL": "a@x", "GIT_COMMITTER_NAME": "C",
                "GIT_COMMITTER_EMAIL": "c@x", "GIT_CONFIG_GLOBAL": "/x/gitconfig", "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "a.b", "GIT_CONFIG_VALUE_0": "c", "GIT_SSL_CAINFO": "/x/ca.pem",
                "GIT_HTTP_PROXY_AUTHMETHOD": "basic", "GIT_EXEC_PATH": "/x/git-core",
                "GIT_SSH": "/x/ssh", "GIT_ASKPASS": "/x/ask", "GIT_TERMINAL_PROMPT": "0",
                "CONTAINER_HOST": "ssh://podman", "CONTAINER_CONNECTION": "remote",
                "BRIDGE_ANYTHING_ELSE": "kept: not a name the tool reads"}
        gone = {"GIT_DIR": "/n", "GIT_WORK_TREE": "/n", "GIT_INDEX_FILE": "/n/i",
                "GIT_OBJECT_DIRECTORY": "/n/o", "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/n/a",
                "GIT_COMMON_DIR": "/n/c", "GIT_NAMESPACE": "ns", "GIT_PREFIX": "sub/",
                "BRIDGE_LIVE_PATH": "/n/b.json", "BRIDGE_PORT": "1", "CONTAINER_CONFIG": "/n/c.json",
                "CONTAINER_ROOT": "/n", "CONTAINER_OUT": "/n/o.html"}
        env = T.agent_env({"RUNNER_PIN": "yes"}, base=dict(mine, **gone, HOME="/h", PATH="/p"))
        self.assertEqual(sorted(set(gone) & set(env)), [])
        self.assertEqual({k: env[k] for k in mine}, mine)
        self.assertEqual((env["HOME"], env["PATH"], env["RUNNER_PIN"]), ("/h", "/p", "yes"))
        # and through a real process: git commits as that person, under an empty HOME
        repo = self.repo()
        home = os.path.join(self.root, "emptyhome")
        os.makedirs(home)
        base = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        child = T.agent_env(base=dict(base, HOME=home, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="Ann",
                                      GIT_AUTHOR_EMAIL="ann@x", GIT_COMMITTER_NAME="Ann",
                                      GIT_COMMITTER_EMAIL="ann@x", GIT_DIR="/nonexistent/.git"))
        done = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "commit", "--allow-empty", "-qm", "x"],
                              cwd=repo, env=child, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(git(repo, "log", "-1", "--format=%an <%ae>"), "Ann <ann@x>")

    def test_every_variable_the_tool_reads_from_its_own_namespaces_is_left_out_of_the_agents(self):
        # a BRIDGE_/CONTAINER_ name added to the sources and forgotten in MAP_ENV would leak to agents
        here = os.path.dirname(os.path.abspath(T.__file__))
        names = set()
        for name in sorted(os.listdir(here)):
            if name.endswith((".py", ".sh")) and not name.startswith("test_") and name != "testenv.py":
                with open(os.path.join(here, name), encoding="utf-8") as handle:
                    names |= set(re.findall(r"\b(?:BRIDGE|CONTAINER)_[A-Z0-9_]*[A-Z0-9]\b", handle.read()))
        self.assertTrue(names, "found no variables: the pattern or the folder is wrong")
        self.assertEqual(sorted(names - set(T.MAP_ENV)), [])
        self.assertEqual(sorted(set(T.MAP_ENV) - names), [], "MAP_ENV names a variable nothing reads")

    # ---- 3. git hooks never decide a merge -------------------------------------------------------
    def hooked_repo(self, name):
        """A repo whose hooks refuse every commit and merge, and leave a marker when one runs."""
        repo = self.repo(name)
        hooks = os.path.join(repo, ".git", "hooks")
        marker = os.path.join(self.side, name + ".hook-ran")
        git(repo, "config", "core.hooksPath", hooks)
        for hook, body in (("pre-commit", "exit 1"), ("commit-msg", "exit 1"),
                           ("pre-merge-commit", "exit 1"), ("pre-push", "exit 1"),
                           ("post-commit", f"echo post-commit >> {marker}"),
                           ("post-merge", f"echo post-merge >> {marker}"),
                           ("prepare-commit-msg", f"echo prepare >> {marker}")):
            path = os.path.join(hooks, hook)
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\n" + body + "\n")
            os.chmod(path, 0o755)
        return repo, marker

    def test_a_failing_hook_does_not_stop_a_merge_and_no_hook_runs_during_it(self):
        repo, marker = self.hooked_repo("hooked")
        t = self.make("add a note", repo, "writer")      # leaves agent-note.txt uncommitted
        self.wait(t["id"])
        probe = subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "x"], cwd=repo)
        self.assertNotEqual(probe.returncode, 0, "the fixture's hooks must refuse a commit")
        with open(os.path.join(repo, "other.txt"), "w") as handle:      # main moved on: a real merge
            handle.write("other\n")
        git(repo, "add", "other.txt")
        git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "main moves on")
        out = self.store.close(t["id"], "merge")
        self.assertEqual(out["status"], "merged")
        self.assertEqual(len(git(repo, "log", "-1", "--format=%P").split()), 2)     # a merge commit
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))
        self.assertFalse(os.path.exists(marker), "a hook ran during the engine's commit or merge")
        merged = self.events(t["id"], "merged")[0]
        self.assertEqual(merged["hooks"], "off")
        self.assertIn("hooks were not run", out["message"])

    def test_a_failing_hook_does_not_stop_a_forced_merge_or_a_set_aside(self):
        repo, marker = self.hooked_repo("hooked")
        failed = self.make("add a note", repo, "writer", True, None, "false")   # blocked: verify fails
        self.wait(failed["id"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(failed["id"], "merge")
        self.assertIn("verify failed", err.exception.message)
        self.assertEqual(self.store.close(failed["id"], "merge", force=True)["status"], "merged")
        aside = self.make("another note", repo, "writer")
        self.wait(aside["id"])
        out = self.store.close(aside["id"], "discard")
        self.assertEqual(out["status"], "discarded")
        self.assertEqual(self.events(aside["id"], "discarded")[0]["hooks"], "off")
        self.assertIn("agent-note.txt", git(repo, "show", "--stat", f"cm/{aside['id']}"))
        self.assertFalse(os.path.exists(marker))

    # ---- 4. a stopped or lost run blocks whatever the verify says --------------------------------
    def test_a_stopped_thread_with_no_verify_line_has_a_merge_blocker(self):
        repo = self.repo()
        t = self.make("x", repo, "slowwriter")
        self.child_event(t["id"])
        self.store.stop(t["id"])
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["verify_cmd"]), ("stopped", None))
        self.assertEqual(len(s["merge_blockers"]), 1)
        self.assertIn("stopped", s["merge_blockers"][0])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("stopped", err.exception.message)
        out = self.store.close(t["id"], "merge", force=True)
        self.assertEqual(out["status"], "merged")
        self.assertTrue(self.events(t["id"], "merged")[0]["evidence"]["forced"])

    def lose(self, tid, n=1):
        agent = self.child_event(tid, n)
        self.kill_supervisor(tid, n)
        os.killpg(agent["pid"], signal.SIGKILL)
        time.sleep(0.3)

    def test_a_lost_thread_with_no_verify_line_has_a_merge_blocker(self):
        repo = self.repo()
        t = self.make("x", repo, "slowwriter")
        self.lose(t["id"])
        for _ in range(50):                 # a loaded box can take longer than lose()'s 0.3 s to see it gone
            s = self.summary(t["id"])
            if s["status"] == "lost":
                break
            time.sleep(0.1)
        self.assertEqual((s["status"], s["verify_cmd"]), ("lost", None))
        self.assertEqual(len(s["merge_blockers"]), 1)
        self.assertIn("lost", s["merge_blockers"][0])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertEqual(self.store.close(t["id"], "merge", force=True)["status"], "merged")

    # ---- 5. a verify that hit its time cap says so -----------------------------------------------
    def test_a_verify_or_setup_that_hit_its_cap_is_recorded_and_shown_as_a_timeout(self):
        repo = self.repo()
        self.store.verify_timeout = 1
        t = self.make("x", repo, "writer", True, None, "sleep 30")
        s = self.wait(t["id"])
        verify = self.events(t["id"], "verify")[0]
        self.assertEqual((verify["rc"], verify["timed_out"], verify["limit"]), (-15, True, 1))
        self.assertIn("timed out after 1 s", read(os.path.join(self.store_dir, t["id"], "run-1.log")))
        self.assertIn("timed out after 1 s", read(os.path.join(self.store_dir, t["id"], "verify-1.log")))
        self.assertEqual((s["verify"]["timed_out"], s["verify"]["limit"]), (True, 1))
        self.assertEqual(s["merge_blockers"], ["verify timed out after 1 s"])
        self.store.setup_timeout = 1
        u = self.make("y", repo, "writer", setup="sleep 30")
        s = self.wait(u["id"])
        setup = self.events(u["id"], "setup")[0]
        self.assertEqual((setup["timed_out"], setup["limit"]), (True, 1))
        self.assertEqual(s["merge_blockers"], ["setup timed out after 1 s"])
        self.assertIn("setup failed (exit -15) · timed out after 1 s",
                      read(os.path.join(self.store_dir, u["id"], "run-1.log")))
        # an ordinary failure is still just a failure
        v = self.make("z", repo, "writer", True, None, "exit 3")
        s = self.wait(v["id"])
        self.assertEqual(s["merge_blockers"], ["verify failed (exit 3)"])
        self.assertNotIn("timed_out", s["verify"])
        self.assertNotIn("timed_out", self.events(v["id"], "verify")[0])

    def test_the_agent_is_told_when_its_verify_line_timed_out(self):
        repo = self.repo()
        self.store.verify_timeout = 1
        t = self.make("x", repo, "writer", True, None, "sleep 30", 1)
        self.wait(t["id"])
        self.assertEqual(self.store.tick(), [t["id"]])
        self.wait(t["id"])
        prompt = read(os.path.join(self.store_dir, t["id"], "prompt-2.txt"))
        self.assertIn("timed out after 1 s", prompt)
        self.assertNotIn("failed (exit", prompt)

    # ---- 6. a follow-up after a lost run ---------------------------------------------------------
    def test_a_follow_up_after_a_lost_run_says_it_was_lost(self):
        repo = self.repo()
        t = self.make("x", repo, "slowwriter")
        self.lose(t["id"])
        self.store.followup(t["id"], "carry on")
        prompt = read(os.path.join(self.store_dir, t["id"], "prompt-2.txt"))
        self.assertIn("lost", prompt)
        self.assertNotIn("None", prompt)
        done = self.make("y", repo, "failer")
        self.wait(done["id"])
        self.store.followup(done["id"], "again")
        self.assertIn("ended with exit code 3",
                      read(os.path.join(self.store_dir, done["id"], "prompt-2.txt")))

    # ---- 7. a retry is blocked only by the config the thread was made with -----------------------
    def other_store(self, config, runners):
        store = T.TaskStore(self.store_dir, T.valid_runners(runners), [self.work], config_path=config)
        store.last_create = -100.0
        return store

    def test_a_store_of_another_config_leaves_a_due_retry_for_the_one_that_offers_the_agent(self):
        repo = self.repo()
        mine, theirs = os.path.join(self.root, "a.json"), os.path.join(self.root, "b.json")
        offered = {"quiet": {"cmd": [sys.executable, self.fake, "lines"], "max_minutes": 1}}
        a = self.other_store(mine, offered)
        t = a.create("x", repo, "quiet", True, None, "false", 1)
        self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual(json.loads(read(os.path.join(path, "task.json")))["config"], os.path.realpath(mine))
        elsewhere = self.other_store(theirs, {"other": offered["quiet"]})    # no "quiet" here
        self.assertEqual(elsewhere.tick(), [])
        s = elsewhere.summary(path)
        self.assertEqual((s["retry_due"], s["blocked"]), (True, None))
        self.assertEqual(self.events(t["id"], "blocked"), [])
        self.assertEqual(a.tick(), [t["id"]])                        # the owner's store sends it
        self.wait(t["id"])
        # the thread's own config no longer offers its agent: now it is blocked, and says so
        a.last_create = -100.0
        b = a.create("y", repo, "quiet", True, None, "false", 1)
        self.wait(b["id"])
        gone = self.other_store(mine, {"other": offered["quiet"]})
        self.assertEqual(gone.tick(), [])
        s = gone.summary(os.path.join(self.store_dir, b["id"]))
        self.assertEqual(s["retry_due"], False)
        self.assertIn("not offered any more", s["blocked"])

    def test_a_thread_made_with_no_config_file_is_not_called_gone_by_a_store_without_its_agent(self):
        # zero-config: each process finds its agents on PATH, so the CLI's set and a bridge started
        # before an agent was installed differ, and neither is the thread's owner
        repo = self.repo()
        offered = {"quiet": {"cmd": [sys.executable, self.fake, "lines"], "max_minutes": 1}}
        a = self.other_store("", offered)
        t = a.create("x", repo, "quiet", True, None, "false", 1)
        self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual(json.loads(read(os.path.join(path, "task.json")))["config"], "")
        for config in ("", os.path.join(self.root, "b.json")):
            elsewhere = self.other_store(config, {"other": offered["quiet"]})    # no "quiet" here
            self.assertEqual(elsewhere.tick(), [], config)
            self.assertEqual(elsewhere.tick_errors, [], config)
            s = elsewhere.summary(path)
            self.assertEqual((s["retry_due"], s["blocked"]), (True, None), config)
        self.assertEqual(self.events(t["id"], "blocked"), [], "a store without the agent wrote a block")
        self.assertEqual(a.tick(), [t["id"]])                        # the store that offers it sends it
        s = self.wait(t["id"])
        self.assertEqual((s["runs"], s["attempts"]), (2, 2))
        # a worktree that is gone is still a blocker for any store: that does not depend on an owner
        a.last_create = -100.0
        b = a.create("y", repo, "quiet", True, None, "false", 1)
        self.wait(b["id"])
        git(repo, "worktree", "remove", "--force",
            json.loads(read(os.path.join(self.store_dir, b["id"], "task.json")))["worktree"])
        stale = self.other_store("", {"other": offered["quiet"]})
        stale.tick()
        self.assertIn("worktree is gone", stale.summary(os.path.join(self.store_dir, b["id"]))["blocked"])

    # ---- 8. how a checker's answer is read -------------------------------------------------------
    def test_a_plain_text_checker_that_prints_a_json_line_still_gets_its_verdict(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        self.says("plain", "reading the diff", '{"files": 1, "ok": true}', "VERDICT: PASS - fine")
        self.store.check(t["id"], "plain")
        self.assertEqual(self.wait(t["id"])["check"]["verdict"], "PASS")
        # a stream whose first line is noise is still a stream when the runner says it is one
        self.says("declared", "stderr noise", _claude("assistant", "VERDICT: PASS - early"),
                  _claude("result", "VERDICT: FAIL - on reflection"), argv=("--json",))
        self.store.check(t["id"], "declared")
        self.assertEqual(self.wait(t["id"])["check"]["verdict"], "FAIL")
        # and one that does not say so is read from its first line
        self.says("undeclared", _claude("result", "VERDICT: PASS - fine"))
        self.store.check(t["id"], "undeclared")
        self.assertEqual(self.wait(t["id"])["check"]["verdict"], "PASS")

    def test_final_answer_decides_by_the_declared_format_or_the_first_line(self):
        json_line = '{"files": 1}'
        self.assertEqual(T.final_answer("\n".join(["looking", json_line, "VERDICT: PASS"])),
                         "looking\n" + json_line + "\nVERDICT: PASS")
        stream = "noise first\n" + _claude("result", "ok") + "\nnoise last"
        self.assertEqual(T.final_answer(stream), stream)                 # first line: plain
        self.assertEqual(T.final_answer(stream, events=True), "ok")      # declared: a stream
        self.assertEqual(T.final_answer(_claude("result", "ok") + "\nnoise last"), "ok")
        header = "# run 3 · agent · in /x · started 2026-10-10T00:00:00Z\n"
        self.assertEqual(T.final_answer(header + _claude("result", "ok")), "ok")
        self.assertTrue(T.declares_events(["codex", "exec", "--json", "-"]))
        self.assertTrue(T.declares_events(["claude", "-p", "--output-format", "stream-json"]))
        self.assertTrue(T.declares_events(["gemini", "-o=stream-json"]))
        self.assertFalse(T.declares_events(["aider", "--message", "{text}"]))
        self.assertFalse(T.declares_events(None))


class RepointedGitTests(Harness):
    """An agent can write its worktree, .git included. Pointed at a copy of the repository it made, a
    git the map runs there would read that copy's config (core.fsmonitor runs a command) and run it
    as you, outside the agent's sandbox and time cap. The map checks the pointer before any git in a
    worktree; one that fails runs nothing, is refused a merge, and is set aside whole on discard."""

    def started(self, verify=None):
        repo = self.repo()
        t = self.make("do", repo, "evilgit", True, None, verify)
        self.wait(t["id"])
        wt = os.path.join(self.root, "store-worktrees", t["id"])
        self.assertTrue(read(os.path.join(wt, ".git")).startswith("gitdir: " + os.path.join(wt, "evilgit-copy")),
                        "the fake agent repointed .git")
        return repo, t["id"], wt, os.path.join(self.side, "PWNED")

    def marks(self, mark):
        return read(mark).count("fsmonitor in") if os.path.exists(mark) else 0

    def test_the_trap_is_armed_and_the_map_never_springs_it(self):
        repo, tid, wt, mark = self.started(verify="true")
        self.assertEqual(self.marks(mark), 0, "the run itself, its verify receipt and the exit digest ran no git there")
        subprocess.run(["git", "status", "--short"], cwd=wt, capture_output=True, env=T._git_env())
        self.assertGreater(self.marks(mark), 0, "control: a plain git status in that folder does run the agent's command")
        os.unlink(mark)
        path = os.path.join(self.store_dir, tid)
        verify = next(e for e in self.store._events(path) if e["ev"] == "verify")
        self.assertIsNone(verify.get("digest"), "no digest was taken in a worktree whose .git was changed")
        self.assertTrue(any(".git" in b and "no git is run" in b for b in self.store.summary(path)["merge_blockers"]),
                        self.store.summary(path)["merge_blockers"])
        with self.assertRaises(T.TaskError) as err:
            self.store.diff(tid)
        self.assertEqual(err.exception.status, 409)
        self.assertIn(".git", err.exception.message)
        for force in (False, True):
            with self.assertRaises(T.TaskError) as err:
                self.store.close(tid, "merge", force)
            self.assertEqual(err.exception.status, 409)
            self.assertIn("Nothing was merged", err.exception.message)
        self.assertEqual(self.marks(mark), 0, "viewing, listing and a refused merge ran nothing")

    def test_discard_sets_the_worktree_aside_untouched_and_keeps_the_branch(self):
        repo, tid, wt, mark = self.started()
        out = self.store.close(tid, "discard")
        self.assertEqual(out["status"], "discarded")
        self.assertEqual(self.marks(mark), 0, "set aside without a single git run in it")
        self.assertFalse(os.path.exists(wt))
        retired = [n for n in os.listdir(os.path.dirname(wt)) if ".retired-" in n]
        self.assertEqual(len(retired), 1)
        self.assertTrue(os.path.isdir(os.path.join(os.path.dirname(wt), retired[0], "evilgit-copy")), "nothing was deleted")
        self.assertEqual(git(repo, "rev-parse", "--verify", f"refs/heads/cm/{tid}") != "", True, "the branch stays")
        self.assertNotIn(wt, git(repo, "worktree", "list", "--porcelain"))

    def test_only_the_file_git_wrote_passes(self):
        repo = self.repo()
        t = self.make("do", repo, "writer", True, None, None)
        self.wait(t["id"])
        wt = os.path.join(self.root, "store-worktrees", t["id"])
        self.assertIsNone(T._worktree_unsafe(wt, repo))
        original = read(os.path.join(wt, ".git"))
        admin = original.split("gitdir:", 1)[1].strip()
        evil = os.path.join(self.side, "evil")
        shutil.copytree(git(repo, "rev-parse", "--git-common-dir") if os.path.isabs(git(repo, "rev-parse", "--git-common-dir"))
                        else os.path.join(repo, ".git"), evil)
        gitfile = os.path.join(wt, ".git")
        cases = {
            "a copy of the repository": "gitdir: " + evil + "\n",
            "the repository itself": "gitdir: " + os.path.join(repo, ".git") + "\n",
            "not a pointer": "ref: refs/heads/main\n",
            "two lines": original + "gitdir: " + evil + "\n",
            "another worktree's folder": "gitdir: " + os.path.join(os.path.dirname(admin), "elsewhere") + "\n",
        }
        for what, text in cases.items():
            with open(gitfile, "w") as handle:
                handle.write(text)
            self.assertIsNotNone(T._worktree_unsafe(wt, repo), what)
        os.unlink(gitfile)
        os.makedirs(gitfile)                       # a folder of its own is a repository of its own
        self.assertIsNotNone(T._worktree_unsafe(wt, repo), "a .git folder")
        os.rmdir(gitfile)
        os.symlink(admin, gitfile)
        self.assertIsNotNone(T._worktree_unsafe(wt, repo), "a symlink")
        os.unlink(gitfile)
        with open(gitfile, "w") as handle:
            handle.write(original)
        self.assertIsNone(T._worktree_unsafe(wt, repo), "put back, it passes again")
        # a copy of git's own folder for this worktree, kept elsewhere, is not git's folder
        shutil.copytree(admin, evil + "-admin")
        with open(gitfile, "w") as handle:
            handle.write("gitdir: " + evil + "-admin\n")
        self.assertIsNotNone(T._worktree_unsafe(wt, repo), "a copy of the worktree's own folder")

    def test_a_hook_in_the_shared_git_runs_for_neither_the_diff_nor_the_merge_staging(self):
        repo = self.repo()
        hooks = os.path.join(self.side, "hooks")
        os.makedirs(hooks)
        mark = os.path.join(self.side, "HOOK")
        hook = os.path.join(hooks, "post-index-change")
        with open(hook, "w") as handle:
            handle.write('#!/bin/sh\necho "post-index-change in $(pwd)" >> ' + mark + "\n")
        os.chmod(hook, 0o755)
        git(repo, "config", "core.hooksPath", hooks)
        # the index must change for the hook to fire: an add on a clean index writes nothing (and an empty commit does not always
        # refresh it), so the control edits a tracked file first, and the add that stages it is the one that writes the index
        with open(os.path.join(repo, "shared.txt"), "a") as handle:
            handle.write("control\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, env=T._git_env())
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "control"], cwd=repo, capture_output=True, env=T._git_env())
        self.assertTrue(os.path.exists(mark), "control: a plain git add in that repository runs the hook")
        os.unlink(mark)
        t = self.make("do", repo, "writer", True, None, None)
        self.wait(t["id"])
        if os.path.exists(mark):
            os.unlink(mark)                      # git worktree add is the repository's own business: its checkout runs the hook
        self.store.diff(t["id"])
        self.assertFalse(os.path.exists(mark), "the diff's scratch-index add ran no hook")
        out = self.store.close(t["id"], "merge")
        self.assertEqual(out["status"], "merged")
        self.assertFalse(os.path.exists(mark), "staging the leftovers and merging ran no hook")

    def test_text_in_another_encoding_is_shown_not_a_crash(self):
        repo = self.repo()
        t = self.make("do", repo, "latin1", True, None, None)
        self.wait(t["id"])
        d = self.store.diff(t["id"])
        self.assertIn("legacy.txt", json.dumps(d["files"]))
        self.assertIn("caf", d["patch"])
        self.assertIn("\ufffd", d["patch"])


class ClaimFixTests(Harness):
    """The ship review's confirmed claims about the engine, each with the state that showed it:
    evidence that cannot be set against the content, a time cap that reads as a crash, a
    supervisor that starts before its run is on record, a repository lock a kill let go of, a
    worktree add that leaves a branch behind, early supervisor exits that skip the sweep, a forced
    removal that took a submodule's ignored files, and a close past a blocker that landed after
    the caller looked."""

    def wt(self, tid):
        return os.path.join(self.store.worktrees_dir, tid)

    def path(self, tid):
        return os.path.join(self.store_dir, tid)

    def blockers(self, tid):
        return self.store.summary(self.path(tid))["merge_blockers"]

    def verified(self, verify="test -f agent-note.txt"):
        """(repo, id) of a worktree thread whose verify passed: the evidence in force."""
        repo = self.repo()
        t = self.make("note", repo, "writer", True, None, verify)
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["merge_blockers"]), ("review", []))
        return repo, t["id"]

    def add_runners(self, **specs):
        self.store.runners.update(T.valid_runners(specs))

    # ---- c003, c004, c087: the digest gate -----------------------------------------------------
    def test_a_digest_that_cannot_be_read_blocks_the_merge_until_forced(self):
        repo, tid = self.verified()
        with mock.patch.object(T, "_work_digest", return_value=None):
            got = self.blockers(tid)
            self.assertEqual(len(got), 1, got)
            self.assertIn("cannot be compared with its evidence", got[0])
            self.assertIn("git could not read the worktree", got[0])
            with self.assertRaises(T.TaskError) as err:
                self.store.close(tid, "merge")
            self.assertEqual(err.exception.status, 409)
            self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))
            done = self.store.close(tid, "merge", force=True)
        self.assertEqual(done["status"], "merged")
        merged = next(e for e in self.store._events(self.path(tid)) if e["ev"] == "merged")
        self.assertTrue(merged["evidence"]["forced"])
        self.assertTrue(os.path.exists(os.path.join(repo, "agent-note.txt")))

    def test_a_running_thread_is_not_blocked_by_a_digest_that_is_not_taken(self):
        repo, tid = self.verified()
        task = self.store._read_task(self.path(tid))
        d = self.store._derive(task, self.store._events(self.path(tid)))
        with mock.patch.object(T, "_work_digest", return_value=None):
            for status in ("running", "checking", "stopping"):
                self.assertEqual(self.store._live_digest(task, dict(d, status=status)), (None, None))
            self.assertEqual(self.store._live_digest(task, d)[1], "git could not read the worktree")

    def test_a_worktree_gone_after_verify_cannot_land_unreviewed_commits_without_force(self):
        repo, tid = self.verified()
        wt, branch = self.wt(tid), "cm/" + tid
        git(repo, "worktree", "remove", "--force", wt)           # taken away by hand
        side = os.path.join(self.side, "second-look")
        git(repo, "worktree", "add", "-q", side, branch)         # and a commit nobody reviewed
        with open(os.path.join(side, "unreviewed.txt"), "w") as handle:
            handle.write("never verified\n")
        git(side, "add", "-A")
        git(side, "commit", "-q", "-m", "after the verify")
        git(repo, "worktree", "remove", "--force", side)
        self.assertEqual(self.blockers(tid), [
            "the work cannot be compared with its evidence: the worktree folder is gone"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(tid, "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertFalse(os.path.exists(os.path.join(repo, "unreviewed.txt")))
        self.store.close(tid, "merge", force=True)               # the person saw it and lands it
        self.assertTrue(os.path.exists(os.path.join(repo, "unreviewed.txt")))

    def test_a_worktree_gone_with_no_evidence_to_compare_still_closes(self):
        repo = self.repo()
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        git(repo, "worktree", "remove", "--force", self.wt(t["id"]))
        self.assertEqual(self.blockers(t["id"]), [])
        self.assertEqual(self.store.close(t["id"], "discard")["status"], "discarded")

    def rewrite_receipt(self, tid, ev, edit):
        file = os.path.join(self.path(tid), "events.jsonl")
        rows = [json.loads(line) for line in read(file).splitlines() if line.strip()]
        for row in rows:
            if row["ev"] == ev:
                edit(row)
        with open(file, "w") as handle:
            handle.write("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows))

    def test_a_receipt_whose_digest_failed_is_not_the_same_as_one_without_a_digest(self):
        repo, tid = self.verified()
        self.rewrite_receipt(tid, "verify", lambda row: row.__setitem__("digest", None))
        self.assertEqual(self.blockers(tid), [T.UNPROVEN[T.CHANGED_VERIFY]])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(tid, "merge")
        self.assertEqual(err.exception.status, 409)
        self.rewrite_receipt(tid, "verify", lambda row: row.pop("digest", None))   # from before digests
        self.assertEqual(self.blockers(tid), [])
        self.assertEqual(self.store.close(tid, "merge")["status"], "merged")

    # ---- c009, c010: the time cap -----------------------------------------------------------------
    def test_an_agent_the_time_cap_killed_says_so_to_the_person_and_to_the_agent(self):
        repo = self.repo()
        t = self.make("slow", repo, "capped")
        s = self.wait(t["id"], 30)
        self.assertEqual((s["status"], s["rc"]), ("failed", -15))
        self.assertEqual(s["merge_blockers"], ["the agent's run was killed at its time cap (1 s)"])
        self.store.followup(t["id"], "go on")
        prompt = read(os.path.join(self.path(t["id"]), "prompt-2.txt"))
        self.assertIn("hit its time cap (1 s) and was killed", prompt)
        self.assertNotIn("exit code -15", prompt)

    def test_a_check_the_time_cap_killed_gave_no_verdict_even_if_it_answered_on_the_way_out(self):
        self.add_runners(napper={"cmd": [sys.executable, self.fake, "sleep"], "role": "check",
                                 "max_minutes": 1, "check_minutes": 0.02},
                         trapper={"cmd": [sys.executable, self.fake, "trappass"], "role": "check",
                                  "max_minutes": 1, "check_minutes": 0.02})
        repo = self.repo()
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        for runner, rc in (("napper", -15), ("trapper", 0)):
            self.store.check(t["id"], runner)
            s = self.wait(t["id"], 30)
            exit_row = [e for e in self.store._events(self.path(t["id"])) if e["ev"] == "exit"][-1]
            self.assertEqual((exit_row["rc"], exit_row["killed"]), (rc, True), runner)
            self.assertEqual(s["check"]["verdict"], "no verdict · timed out after 1 s", runner)
            self.assertIsNone(s["last_verdict"], runner)
            self.assertNotEqual(s["evidence"], "checked", runner)

    # ---- c090: a supervisor starts nothing until its run is on record ---------------------------
    def supervise_by_hand(self, tid, **consts):
        path = self.path(tid)
        os.makedirs(path)
        with open(os.path.join(path, "prompt-1.txt"), "w") as handle:
            handle.write("x")
        lowered = "; ".join(f"tasks.{k} = {v!r}" for k, v in consts.items())
        code = f"import sys, tasks; {lowered}; raise SystemExit(tasks._supervise(sys.argv[1:]))"
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(T.__file__)))
        done = subprocess.run([sys.executable, "-c", code, path, "1", "60", "0", tid, "", "", "0",
                               "--", sys.executable, self.fake, "write"],
                              cwd=self.work, env=env, timeout=60)
        return path, done.returncode

    def test_a_supervisor_with_no_run_on_record_starts_no_agent(self):
        path, rc = self.supervise_by_hand("20261010-000000-orphan-abcdef", LAUNCH_WAIT=0.3)
        self.assertEqual(rc, 3)
        self.assertEqual(self.store._events(path), [])
        self.assertIn("not started", read(os.path.join(path, "run-1.log")))
        self.assertFalse(os.path.exists(os.path.join(self.side, "argv-seen.txt")))

    def test_a_creator_that_cannot_record_its_run_leaves_no_agent_running_under_recovery(self):
        repo = self.repo()
        real = T.TaskStore._append

        def failing(path, row):          # the creator dies right after it launched the supervisor
            if row.get("ev") == "run" and row.get("n") == 1 and row.get("pid"):
                raise OSError("no space left on device")
            return real(path, row)
        with mock.patch.object(T.TaskStore, "_append", staticmethod(failing)):
            with self.assertRaises(OSError):
                self.make("note", repo, "sleeper")
        tid = next(n for n in os.listdir(self.store_dir) if T.TASK_ID_RE.fullmatch(n))
        events = self.store._events(self.path(tid))
        self.assertEqual([(e["ev"], e.get("pid")) for e in events if e["ev"] == "run"], [("run", 0)])
        log = os.path.join(self.path(tid), "run-1.log")
        end = time.time() + 10
        while time.time() < end and "not started" not in (read(log) if os.path.exists(log) else ""):
            time.sleep(0.05)
        self.assertIn("not started", read(log))
        time.sleep(1.0)
        self.assertFalse(os.path.exists(os.path.join(self.side, "argv-seen.txt")),
                         "the agent ran although recovery had closed its run")
        self.assertEqual(self.store.summary(self.path(tid))["status"], "failed")

    # ---- c091: a refused worktree add leaves nothing --------------------------------------------
    def test_a_failed_worktree_add_takes_back_the_branch_and_the_folder_it_made(self):
        repo = self.repo()
        with open(os.path.join(repo, ".gitattributes"), "w") as handle:
            handle.write("shared.txt filter=boom\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "attributes")
        git(repo, "config", "filter.boom.required", "true")
        git(repo, "config", "filter.boom.clean", "cat")
        git(repo, "config", "filter.boom.smudge", "false")      # a checkout through it fails
        with self.assertRaises(T.TaskError) as err:
            self.make("note", repo, "writer")
        self.assertEqual(err.exception.status, 502)
        self.assertIn("git worktree add failed", err.exception.message)
        self.assertEqual(git(repo, "branch", "--list", "cm/*"), "")
        self.assertEqual(git(repo, "worktree", "list").count("\n"), 0)
        wts = self.store.worktrees_dir
        self.assertEqual(os.listdir(wts) if os.path.isdir(wts) else [], [])
        self.assertEqual([n for n in os.listdir(self.store_dir) if T.TASK_ID_RE.fullmatch(n)], [])

    # ---- c092: the repository lock outlives a killed owner while its git runs --------------------
    def test_the_repository_lock_is_held_by_a_git_run_after_its_owner_is_killed(self):
        repo = self.repo()
        pidfile = os.path.join(self.side, "nap.pid")
        code = textwrap.dedent("""
            import sys, tasks as T
            store = T.TaskStore(sys.argv[1], {}, [sys.argv[2]])
            with store._repo_lock(sys.argv[3]):
                T._git(["-c", "alias.nap=!echo $$ > " + sys.argv[4] + "; exec sleep 20", "nap"],
                       sys.argv[3], timeout=60)
        """)
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(T.__file__)))
        owner = subprocess.Popen([sys.executable, "-c", code, self.store_dir, self.work, repo,
                                  pidfile], env=env)
        self.addCleanup(lambda: owner.poll() is None and (owner.kill() or owner.wait()))
        end = time.time() + 20
        while time.time() < end and not os.path.exists(pidfile):
            time.sleep(0.05)
        time.sleep(0.2)
        nap = int(read(pidfile).strip())
        self.addCleanup(lambda: os.path.exists(f"/proc/{nap}") and os.kill(nap, signal.SIGKILL))
        lock = self.store._repo_lock_path(repo)
        with self.assertRaises(T.LockBusy):                       # the owner is alive: busy
            with T._lock_file(lock, 0, "this repository"):
                pass
        owner.kill()
        owner.wait()
        with self.assertRaises(T.LockBusy):                       # and still busy: git runs on
            with T._lock_file(lock, 0, "this repository"):
                pass
        os.kill(nap, signal.SIGKILL)
        end = time.time() + 10
        while time.time() < end:
            try:
                with T._lock_file(lock, 0, "this repository"):
                    break
            except T.LockBusy:
                time.sleep(0.05)
        else:
            self.fail("the lock was not let go when the git run ended")

    def test_a_hooked_git_run_is_not_given_the_lock_and_a_locked_one_cannot_detach_housekeeping(self):
        seen = []
        real = subprocess.run

        def spy(cmd, **kw):
            seen.append((cmd, kw.get("pass_fds")))
            return real(cmd, **kw)
        repo = self.repo()
        with mock.patch.object(T.subprocess, "run", spy):
            with self.store._repo_lock(repo):
                T._git(["status", "--short"], repo)
                T._git(["status", "--short"], repo, hooks=True)
            T._git(["status", "--short"], repo)
        locked, hooked, free = (s for s in seen if "status" in s[0])
        self.assertEqual(len(locked[1]), 1)
        self.assertIn("gc.auto=0", locked[0])
        self.assertEqual((hooked[1], free[1]), ((), ()))
        self.assertNotIn("gc.auto=0", free[0])

    # ---- c093: every exit receipt follows a last sweep --------------------------------------------
    def test_a_failed_setup_and_an_agent_that_cannot_start_sweep_the_scope_before_the_receipt(self):
        for label, setup, agent in (("setup", "exit 1", None), ("launch", "true", "/no/such/agent")):
            tid = f"20261010-00000{len(label)}-sweep-abcde{len(label)}"
            path = os.path.join(self.store_dir, tid)
            os.makedirs(path)
            with open(os.path.join(path, "prompt-1.txt"), "w") as handle:
                handle.write("x")
            self.store._append(path, {"ev": "run", "n": 1, "pid": os.getpid(), "runner": "writer",
                                      "kind": "task", "text": "x", "at": T.stamp()})
            calls = os.path.join(self.side, f"sweeps-{label}.json")
            code = ("import json, sys, tasks; real = tasks._sweep_groups; seen = []\n"
                    "def spy(groups, scope=None, proc='/proc'):\n"
                    "    seen.append(list(scope) if scope else None)\n"
                    "    return real(groups, scope, proc)\n"
                    "tasks._sweep_groups = spy\n"
                    "rc = tasks._supervise(sys.argv[1:])\n"
                    f"json.dump(seen, open({calls!r}, 'w'))\n"
                    "raise SystemExit(rc)")
            env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.abspath(T.__file__)))
            subprocess.run([sys.executable, "-c", code, path, "1", "60", "0", tid, "", setup, "1",
                            "--", agent or sys.executable, self.fake, "write"],
                           cwd=self.work, env=env, timeout=60)
            exit_row = next(e for e in self.store._events(path) if e["ev"] == "exit")
            self.assertEqual(exit_row["rc"], 125 if agent is None else 127, label)
            self.assertIn([tid, 1], json.loads(read(calls)), label)      # the scope swept, last

    # ---- c094: a submodule's own ignored files survive a set-aside --------------------------------
    def test_a_worktree_with_an_initialized_submodule_is_moved_aside_never_forced_away(self):
        sub = os.path.join(self.side, "subrepo")
        os.makedirs(sub)
        git(sub, "init", "-q", "-b", "main")
        testenv.hermetic_repo(sub)
        git(sub, "config", "user.name", "Test")
        git(sub, "config", "user.email", "test@example.invalid")
        with open(os.path.join(sub, "lib.txt"), "w") as handle:
            handle.write("lib\n")
        with open(os.path.join(sub, ".gitignore"), "w") as handle:
            handle.write("*.personal\n")
        git(sub, "add", "-A")
        git(sub, "commit", "-q", "-m", "lib")
        repo = self.repo()
        git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", sub, "vendor")
        git(repo, "commit", "-q", "-m", "add the submodule")
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        wt = self.wt(t["id"])
        git(wt, "-c", "protocol.file.allow=always", "submodule", "update", "--init", "-q")
        with open(os.path.join(wt, "vendor", "diary.personal"), "w") as handle:
            handle.write("mine\n")                                  # ignored inside the submodule
        with open(os.path.join(wt, "junk.pyc"), "w") as handle:
            handle.write("x")                                       # default junk at the root
        self.store.close(t["id"], "discard")
        ev = next(e for e in self.store._events(self.path(t["id"])) if e["ev"] == "discarded")
        self.assertTrue(ev.get("retired"), ev)
        self.assertIn("initialized submodule", ev.get("why", ""))
        self.assertEqual(read(os.path.join(ev["retired"], "vendor", "diary.personal")), "mine\n")
        self.assertFalse(os.path.exists(wt))

    # ---- c099: a close that must be clear is judged under the close's own lock -------------------
    def failing_check(self, tid):
        self.add_runners(judge={"cmd": [sys.executable, self.fake, "judge"], "role": "check",
                                "max_minutes": 1})
        self.store.check(tid, "judge", None, "please fail this")
        s = self.wait(tid)
        self.assertEqual(s["check"]["verdict"], "FAIL")

    def test_a_close_that_must_be_clear_refuses_discard_and_done_with_a_fresh_fail(self):
        repo = self.repo()
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        self.failing_check(t["id"])
        for action in ("merge", "discard"):
            with self.assertRaises(T.TaskError) as err:
                self.store.close(t["id"], action, require_clear=True)
            self.assertEqual(err.exception.status, 409, action)
            self.assertIn("FAIL", err.exception.message)
            self.assertTrue(os.path.isdir(self.wt(t["id"])), action)
        place = os.path.join(self.work, "plain")
        os.makedirs(place)
        u = self.make("note", place, "writer")
        self.wait(u["id"])
        self.failing_check(u["id"])
        with self.assertRaises(T.TaskError) as err:
            self.store.close(u["id"], "done", require_clear=True)
        self.assertEqual(err.exception.status, 409)
        with self.assertRaises(T.TaskError) as err:
            self.store.close(u["id"], "done", force=True, require_clear=True)
        self.assertEqual(err.exception.status, 400)
        self.assertEqual(self.store.close(u["id"], "done")["status"], "done")      # the desktop still can
        self.assertEqual(self.store.close(t["id"], "discard")["status"], "discarded")

    # ---- c030, c031, c032: the API doc says what the engine does -----------------------------------
    def test_the_docs_describe_the_stopped_setup_and_blocked_cases_as_the_engine_has_them(self):
        here = os.path.dirname(os.path.abspath(T.__file__))

        def doc(*parts):
            with open(os.path.join(here, *parts), encoding="utf-8") as handle:
                return " ".join(handle.read().split())
        api, config = doc("docs", "AGENT-API.md"), doc("docs", "CONFIG.md")
        # a stopped or lost run reads as it says only with no verify line: with one it is the
        # single "verify never ran" (the engine test below pins that)
        self.assertNotIn("with or without a verify line", api)
        self.assertIn("with a verify line that never ran, the single `verify never ran`", api)
        # setup runs again on a follow-up until one has passed, and never on a check
        for text in (api, config):
            self.assertNotIn("never on a follow-up or a check", text)
            self.assertIn("only until one setup has passed", text)
        # a reply does not clear a block while its cause stands (the engine refuses it, 409)
        self.assertIn("is refused with 409 for those same two reasons", api)
        self.assertIn("or when its agent is not offered any more", api)

    def test_a_stopped_thread_with_a_verify_line_reads_verify_never_ran_and_without_one_stopped(self):
        repo = self.repo()
        with_verify = self.make("note", repo, "sleeper", True, None, "true")
        self.child_event(with_verify["id"], 1)
        self.store.stop(with_verify["id"])
        self.assertEqual(self.wait(with_verify["id"])["merge_blockers"], ["verify never ran"])
        plain = self.make("note", repo, "sleeper")
        self.child_event(plain["id"], 1)
        self.store.stop(plain["id"])
        self.assertEqual(self.wait(plain["id"])["merge_blockers"],
                         ["the agent's run was stopped before it finished"])


class RenameTests(Harness):
    """A thread's title is the one thing the meta board may change: task.json, whole or not at all,
    under the thread's own lock, for a thread of any status."""

    def made(self, name="notes"):
        folder = os.path.join(self.work, name)
        os.makedirs(folder)
        t = self.make("write a note", folder, "writer")
        self.wait(t["id"])
        return t["id"], os.path.join(self.store_dir, t["id"])

    def test_a_rename_changes_only_the_title_and_survives_a_new_store(self):
        tid, path = self.made()
        before = json.loads(read(os.path.join(path, "task.json")))
        events = read(os.path.join(path, "events.jsonl"))
        got = self.store.rename(tid, "  Write the release note  ")
        self.assertEqual((got["title"], got["status"], got["runs"]),
                         ("Write the release note", "review", 1))
        after = json.loads(read(os.path.join(path, "task.json")))
        self.assertEqual(after, dict(before, title="Write the release note"))
        self.assertEqual(read(os.path.join(path, "events.jsonl")), events)
        self.assertEqual(sorted(n for n in os.listdir(path) if n.endswith(".tmp")), [])
        again = T.TaskStore(self.store_dir, self.runners, [self.work], config_path=self.config)
        self.assertEqual([t["title"] for t in again.list()], ["Write the release note"])
        self.assertEqual(self.store.get(tid)["title"], "Write the release note")

    def test_a_closed_thread_may_be_renamed_too(self):
        tid, path = self.made()
        self.assertEqual(self.store.close(tid, "done")["status"], "done")
        got = self.store.rename(tid, "Release note, finished")
        self.assertEqual((got["title"], got["status"]), ("Release note, finished", "done"))

    def test_a_bad_title_or_id_is_refused_and_nothing_is_written(self):
        tid, path = self.made()
        record = read(os.path.join(path, "task.json"))
        for bad in ("", None, "   ", "x" * 91, "two\nlines", "tab\there", "bell\x07", 5, ["a"],
                    "lone \ud800"):
            with self.assertRaises(T.TaskError, msg=repr(bad)) as err:
                self.store.rename(tid, bad)
            self.assertEqual(err.exception.status, 400, repr(bad))
        self.assertEqual(self.store.rename(tid, "x" * 90)["title"], "x" * 90)    # 90 is the limit
        self.store.rename(tid, "Back again")
        for bad_id, status in (("20261001-000000-nope-abcdef", 404), ("../x", 400), (None, 400),
                               (5, 400)):
            with self.assertRaises(T.TaskError, msg=repr(bad_id)) as err:
                self.store.rename(bad_id, "A fine title")
            self.assertEqual(err.exception.status, status, repr(bad_id))
        want = json.dumps(dict(json.loads(record), title="Back again"), indent=1)
        self.assertEqual(read(os.path.join(path, "task.json")), want)

    def test_a_rename_waits_briefly_for_a_busy_thread_then_says_so(self):
        tid, path = self.made()
        held, release = threading.Event(), threading.Event()

        def hold():
            with T._locked(path):
                held.set()
                release.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        self.addCleanup(holder.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        with mock.patch.object(T, "RENAME_WAIT", 0.2):
            with self.assertRaises(T.LockBusy) as err:
                self.store.rename(tid, "Too busy")
        self.assertEqual(err.exception.status, 503)
        self.assertEqual(self.store.summary(path)["title"], "write a note")
        release.set()
        holder.join()
        self.assertEqual(self.store.rename(tid, "Free now")["title"], "Free now")

    def test_an_altered_record_is_not_rewritten(self):
        tid, path = self.made()
        data = json.loads(read(os.path.join(path, "task.json")))
        with open(os.path.join(path, "task.json"), "w") as handle:
            json.dump(dict(data, cwd=self.side), handle)
        altered = read(os.path.join(path, "task.json"))
        with self.assertRaises(T.TaskError) as err:
            self.store.rename(tid, "Sneaky")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("altered", err.exception.message)
        self.assertEqual(read(os.path.join(path, "task.json")), altered)


class ParseAndDetectTests(unittest.TestCase):
    def test_claude_and_codex_lines(self):
        p = T.parse_line
        self.assertEqual(p(json.dumps({"type": "system", "subtype": "init", "model": "m1"})), [{"k": "info", "s": "session started · model m1"}])
        tool = p(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}]}}))
        self.assertEqual(tool, [{"k": "tool", "s": "Bash: $ ls -la"}])
        self.assertEqual(p(json.dumps({"type": "item.started", "item": {"type": "command_execution", "command": "pytest"}})), [{"k": "cmd", "s": "$ pytest"}])
        self.assertEqual(p(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done it"}})), [{"k": "text", "s": "done it"}])
        self.assertEqual(p(json.dumps({"type": "item.completed", "item": {"type": "file_change", "changes": [{"path": "a.py", "kind": "update"}]}})), [{"k": "file", "s": "update a.py"}])
        self.assertEqual(p("plain words"), [{"k": "raw", "s": "plain words"}])
        self.assertEqual(p("{not json"), [{"k": "raw", "s": "{not json"}])
        self.assertEqual(p("   "), [])
        self.assertEqual(p("# not · the supervisor")[0]["k"], "raw")
        self.assertEqual(p("# run 2 · exit 0 · 2026-10-07T00:00:00Z")[0]["k"], "meta")
        long_path = p(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a" * 5000}}]}}))
        self.assertLess(len(long_path[0]["s"]), 300)

    def test_detect_only_what_is_installed(self):
        found = T.detect_runners(which=lambda name: "/opt/bin/" + name if name in ("codex", "aider") else None)
        self.assertEqual(sorted(found), ["aider", "codex"])
        self.assertEqual(found["codex"]["cmd"][0], "/opt/bin/codex")
        self.assertIn("{session}", found["codex"]["followup_cmd"])
        self.assertIn("{text}", found["aider"]["cmd"])
        self.assertEqual(found["codex"]["default_model"], "default")
        claude = T.detect_runners(which=lambda name: "/opt/bin/claude" if name == "claude" else None)["claude"]
        self.assertEqual([m["name"] for m in claude["models"]], ["sonnet", "haiku", "opus"])
        self.assertEqual(T.with_model_args(claude["cmd"], claude["models"][1]["args"])[-2:], ["--model", "haiku"])

    def test_valid_runners_drops_malformed_specs(self):
        got = T.valid_runners({"ok": {"cmd": ["a"]}, "Bad Name": {"cmd": ["a"]}, "nocmd": {}, "zero": {"cmd": ["a"], "max_minutes": 0},
                               "badfollow": {"cmd": ["a"], "followup_cmd": "x"}})
        self.assertEqual(sorted(got), ["badfollow", "ok"])
        self.assertIsNone(got["badfollow"]["followup_cmd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
