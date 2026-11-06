#!/usr/bin/env python3
"""test_processes.py — the thread engine's process and locking robustness: helpers a setup or verify
line leaves behind, a CLI and a bridge sharing one store, a crash while a thread is being prepared,
a retry that can never go out, lost threads that still hold a place, a long history, macOS (no
/proc) and a caller's GIT_* variables. Real git and real processes, a fake agent, temp dirs only.

Run: python3 -m unittest test_processes
"""
import contextlib
import io
import itertools
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cli  # noqa: E402
import tasks as T  # noqa: E402
import test_tasks as TT  # noqa: E402


def race(calls, timeout=30):
    """Run every call at the same moment; what each gave back, or the TaskError it raised."""
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
        thread.join(timeout)
    return out


def hold_lock(test, path, seconds):
    """Another process takes an exclusive flock on `path` and keeps it for `seconds`."""
    code = ("import fcntl, sys, time\n"
            "fd = open(sys.argv[1], 'a+')\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('held', flush=True)\n"
            "time.sleep(float(sys.argv[2]))\n")
    proc = subprocess.Popen([sys.executable, "-c", code, path, str(seconds)],
                            stdout=subprocess.PIPE, text=True)
    test.addCleanup(lambda: proc.poll() is None and (proc.kill() or proc.wait()))
    test.assertEqual(proc.stdout.readline().strip(), "held")
    return proc


def no_proc(test):
    """This process sees no /proc, the way macOS does: process groups come from ps, and the
    folder each process works in from a stand-in for lsof (read from /proc here)."""
    if not shutil.which("ps"):
        test.skipTest("needs a ps binary (macOS has no /proc: its process groups come from ps)")
    gone = "/nonexistent-proc"
    for name in ("_group_members", "_group_pids"):
        real = getattr(T, name, None)
        if real is None:
            continue

        def forced(pgid, proc="/proc", real=real):
            return real(pgid, gone)
        patcher = mock.patch.object(T, name, forced)
        patcher.start()
        test.addCleanup(patcher.stop)

    def cwds(pids):
        out = {}
        for pid in pids:
            with contextlib.suppress(OSError):
                out[pid] = os.readlink(f"/proc/{pid}/cwd")
        return out
    patcher = mock.patch.object(T, "_ps_cwds", cwds, create=True)
    patcher.start()
    test.addCleanup(patcher.stop)


class SweepTests(TT.Harness):
    """Item 1: setup and verify shells get their own groups; what they leave ends with them."""

    helpers = ()

    def pid_file(self, name):
        path = os.path.join(self.side, name)
        self.helpers = [*self.helpers, path]
        return path

    def tearDown(self):
        for path in self.helpers:                 # before the folder holding the pid files goes
            self.kill_pid_file(path)
        super().tearDown()

    @staticmethod
    def kill_pid_file(path):
        with contextlib.suppress(OSError, ValueError), open(path) as handle:
            os.kill(int(handle.read()), signal.SIGKILL)

    def test_a_verify_helper_left_in_the_background_ends_before_review(self):
        folder = os.path.join(self.work, "bg-verify")
        os.makedirs(folder)
        pidfile = self.pid_file("verify-helper.pid")
        t = self.make("write a note", folder, "writer",
                      verify=f"sleep 300 & echo $! > {pidfile}; exit 0")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["verify"]["rc"]), ("review", 0))
        with open(pidfile) as handle:
            helper = int(handle.read())
        self.assertTrue(self.gone(helper), "the verify's background helper outlived the verify")
        log = TT.read(os.path.join(self.store_dir, t["id"], "run-1.log"))
        self.assertRegex(log, r"verify · ended 1 leftover process")

    def test_a_verify_helper_cannot_keep_changing_files_after_a_failed_verify_either(self):
        folder = os.path.join(self.work, "bg-late")
        os.makedirs(folder)
        late = os.path.join(folder, "late.txt")
        t = self.make("write a note", folder, "writer",
                      verify="(sleep 1.5; echo late > late.txt) & exit 3")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["verify"]["rc"]), ("failed", 3))
        time.sleep(2.2)
        self.assertFalse(os.path.exists(late), "a helper wrote after the verify ended")

    def test_a_setup_helper_ends_before_the_agent_starts(self):
        repo = self.repo()
        pidfile = self.pid_file("setup-helper.pid")
        t = self.make("write a note", repo, "writer",
                      setup=f"sleep 300 & echo $! > {pidfile}; exit 0")
        s = self.wait(t["id"])
        self.assertEqual(s["status"], "review")
        with open(pidfile) as handle:
            helper = int(handle.read())
        self.assertTrue(self.gone(helper), "the setup's background helper outlived the setup")

    def test_a_group_found_empty_is_never_looked_at_again(self):
        # an empty group's number is free for any other process to take; the last sweep before the
        # receipt must not signal whatever now has it
        live = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(lambda: live.poll() is None and (live.kill() or live.wait()))
        end = time.time() + 3
        while time.time() < end and not T._group_pids(live.pid):
            time.sleep(0.02)
        groups = [2 ** 22 + 99, live.pid]
        T._forget_empty(groups)
        self.assertEqual(groups, [live.pid])

    def test_every_recorded_group_is_swept_together(self):
        a = subprocess.Popen(["sleep", "30"], start_new_session=True)
        b = subprocess.Popen(["sleep", "30"], start_new_session=True)
        for p in (a, b):
            self.addCleanup(lambda p=p: p.poll() is None and (p.kill() or p.wait()))
        end = time.time() + 3
        while time.time() < end and not (T._group_pids(a.pid) and T._group_pids(b.pid)):
            time.sleep(0.02)
        self.assertEqual(T._sweep_groups([a.pid, b.pid]), 2)
        self.assertEqual((a.wait(3), b.wait(3)), (-signal.SIGTERM, -signal.SIGTERM))
        self.assertEqual(T._sweep_groups([a.pid, b.pid]), 0)


class NoProcTests(TT.Harness):
    """Item 7: macOS has no /proc. Groups, their folders and a lost thread's agents come from ps."""

    def test_ps_output_is_read_the_way_macos_prints_it(self):
        canned = "    1     1 Ss\n 4242  4242 S+\n 4243  4242 Z\n   77    77 R\nnot a row\n"
        done = subprocess.CompletedProcess([], 0, canned, "")
        with mock.patch.object(T.subprocess, "run", return_value=done) as ran:
            rows = T._ps_rows()
        self.assertEqual(rows, [(1, 1, "Ss"), (4242, 4242, "S+"), (4243, 4242, "Z"), (77, 77, "R")])
        self.assertEqual(ran.call_args[0][0], ["ps", "-axo", "pid=,pgid=,stat="])
        failed = subprocess.CompletedProcess([], 1, "", "ps: nope")
        with mock.patch.object(T.subprocess, "run", return_value=failed):
            self.assertIsNone(T._ps_rows())
        with mock.patch.object(T.subprocess, "run", side_effect=OSError("no ps")):
            self.assertIsNone(T._ps_rows())

    def test_lsof_output_gives_each_processs_folder(self):
        canned = "p4242\nfcwd\nn/Users/dev/code/task one\np4243\nfcwd\nn/tmp\np9\nfcwd\n"
        done = subprocess.CompletedProcess([], 1, canned, "")     # lsof exits 1 when a pid vanished
        with mock.patch.object(T.shutil, "which", return_value="/usr/sbin/lsof"), \
                mock.patch.object(T.subprocess, "run", return_value=done) as ran:
            got = T._ps_cwds([4242, 4243, 9])
        self.assertEqual(got, {4242: "/Users/dev/code/task one", 4243: "/tmp"})
        self.assertEqual(ran.call_args[0][0][-3:], ["-Fpn", "-p", "4242,4243,9"])
        with mock.patch.object(T.shutil, "which", return_value=None):
            self.assertEqual(T._ps_cwds([4242]), {})
        self.assertEqual(T._ps_cwds([]), {})

    def test_a_group_and_the_folder_it_works_in_are_found_through_ps(self):
        no_proc(self)
        p = subprocess.Popen(["sleep", "30"], cwd=self.side, start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and (p.kill() or p.wait()))
        end = time.time() + 3
        while time.time() < end and not T._group_members(p.pid):
            time.sleep(0.02)
        self.assertEqual(T._group_members(p.pid), [(p.pid, os.path.realpath(self.side))])
        self.assertTrue(T._owned_group(p.pid, "/"))
        self.assertTrue(T._owned_group(p.pid, self.side))
        self.assertFalse(T._owned_group(p.pid, self.work))

    def test_a_group_whose_folder_ps_cannot_name_is_not_taken_for_ours(self):
        no_proc(self)
        p = subprocess.Popen(["sleep", "30"], cwd=self.side, start_new_session=True)
        self.addCleanup(lambda: p.poll() is None and (p.kill() or p.wait()))
        end = time.time() + 3
        while time.time() < end and not T._group_pids(p.pid):
            time.sleep(0.02)
        with mock.patch.object(T, "_ps_cwds", lambda pids: {}):         # no lsof
            self.assertEqual(T._group_members(p.pid), [(p.pid, "")])
            self.assertFalse(T._owned_group(p.pid, "/"))
            self.assertEqual(T._sweep_groups([p.pid]), 1, "our own group is swept all the same")

    def test_a_zombie_writes_nothing(self):
        no_proc(self)
        p = subprocess.Popen(["true"], start_new_session=True)
        self.addCleanup(p.wait)
        end = time.time() + 3
        while time.time() < end:
            with contextlib.suppress(OSError), open(f"/proc/{p.pid}/stat") as handle:
                if handle.read().rsplit(")", 1)[1].split()[0] == "Z":
                    break
            time.sleep(0.02)
        self.assertEqual(T._group_members(p.pid), [])

    def test_stopping_a_lost_thread_ends_what_its_agent_left_without_proc(self):
        no_proc(self)
        folder = os.path.join(self.work, "mac-lost")
        os.makedirs(folder)
        t = self.make("sleep", folder, "sleeper")
        child = self.child_event(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.kill_supervisor(t["id"])
        self.assertEqual(self.store.summary(path)["status"], "lost")
        task, events = self.store._read_task(path), self.store._events(path)
        self.assertTrue(self.store._still_writing(task, events), "orphan detection saw nothing")
        self.store.stop(t["id"])
        self.assertTrue(self.group_gone(child["pid"]), "the lost agent was left running")
        self.assertEqual(self.store.summary(path)["status"], "stopped")

    def test_ending_a_group_gives_up_on_a_child_that_will_not_die(self):
        class Stuck:
            pid = 2 ** 22 + 12345

            def wait(self, timeout=None):
                if timeout is None:
                    raise AssertionError("an unbounded wait on a child that will not die")
                raise subprocess.TimeoutExpired("stuck", timeout)

        sent = []
        with mock.patch.object(T.os, "killpg", lambda pg, sig: sent.append(sig)), \
                mock.patch.object(T, "_group_members", lambda *a, **k: []), \
                mock.patch.object(T, "_group_pids", lambda *a, **k: [], create=True), \
                mock.patch.object(T, "TERM_GRACE", 0.01, create=True), \
                mock.patch.object(T, "KILL_GRACE", 0.01, create=True), \
                mock.patch.object(T, "REAP_GRACE", 0.01, create=True):
            rc = T._end_group(Stuck())
        self.assertEqual(rc, -signal.SIGKILL)
        self.assertEqual(sent[:2], [signal.SIGTERM, signal.SIGKILL])


class LockTests(TT.Harness):
    """Item 2: the CLI and a bridge are two stores on one directory and one repo."""

    def second_store(self):
        store = T.TaskStore(self.store_dir, self.runners, [self.work])
        store.last_create = -100.0
        return store

    def test_two_stores_cannot_both_take_the_last_place(self):
        other = self.second_store()
        self.store.max_running = other.max_running = 1
        folders = []
        for name in ("cap-a", "cap-b"):
            folders.append(os.path.join(self.work, name))
            os.makedirs(folders[-1])
        real = T.TaskStore._start_run

        def slow(store, *args, **kw):
            time.sleep(0.5)                       # between "there is room" and "it is on record"
            return real(store, *args, **kw)

        with mock.patch.object(T.TaskStore, "_start_run", slow):
            got = race([lambda: self.store.create("one", folders[0], "sleeper"),
                        lambda: other.create("two", folders[1], "sleeper")])
        refused = [g for g in got if isinstance(g, T.TaskError)]
        self.assertEqual((len(got) - len(refused), [g.status for g in refused]), (1, [429]), got)
        self.assertEqual(self.store.running_count(), 1)

    def test_two_stores_cannot_both_follow_up_into_the_last_place(self):
        other = self.second_store()
        done = []
        for name in ("fa", "fb"):
            folder = os.path.join(self.work, name)
            os.makedirs(folder)
            done.append(self.make("make a note", folder, "sleeper"))
            self.child_event(done[-1]["id"])
            self.store.stop(done[-1]["id"])                  # stopped: a follow-up may restart it
            self.assertEqual(self.wait(done[-1]["id"])["status"], "stopped")
        self.store.max_running = other.max_running = 1
        real = T.TaskStore._start_run

        def slow(store, *args, **kw):
            time.sleep(0.5)
            return real(store, *args, **kw)

        with mock.patch.object(T.TaskStore, "_start_run", slow):
            got = race([lambda: self.store.followup(done[0]["id"], "again"),
                        lambda: other.followup(done[1]["id"], "again")])
        refused = [g for g in got if isinstance(g, T.TaskError)]
        self.assertEqual((len(got) - len(refused), [g.status for g in refused]), (1, [429]), got)

    def test_a_process_holding_the_store_lock_holds_up_a_new_thread(self):
        os.makedirs(self.store_dir, exist_ok=True)
        folder = os.path.join(self.work, "held-store")
        os.makedirs(folder)
        hold_lock(self, os.path.join(self.store_dir, ".store.lock"), 1.5)
        started = time.monotonic()
        t = self.make("make a note", folder, "writer")
        self.assertGreaterEqual(time.monotonic() - started, 1.0, "create did not wait for the lock")
        self.assertEqual(self.wait(t["id"])["status"], "review")

    def test_a_lock_that_stays_held_is_reported_not_waited_on_forever(self):
        os.makedirs(self.store_dir, exist_ok=True)
        folder = os.path.join(self.work, "stuck-store")
        os.makedirs(folder)
        hold_lock(self, os.path.join(self.store_dir, ".store.lock"), 3)
        with mock.patch.object(T, "LOCK_WAIT", 0.3, create=True):
            with self.assertRaises(T.TaskError) as caught:
                self.make("make a note", folder, "writer")
        self.assertEqual(caught.exception.status, 503)
        self.assertIn("thread store", caught.exception.message)
        self.assertEqual(self.store.list(), [], "nothing was started or half made")

    def test_a_process_holding_the_repository_lock_holds_up_a_merge(self):
        repo = self.repo()
        t = self.make("write a note", repo, "writer")
        self.wait(t["id"])
        lock = os.path.join(repo, ".git", "container-map.lock")
        os.makedirs(os.path.dirname(lock), exist_ok=True)
        hold_lock(self, lock, 1.5)
        started = time.monotonic()
        res = self.store.close(t["id"], "merge")
        self.assertGreaterEqual(time.monotonic() - started, 1.0, "the merge did not wait")
        self.assertEqual(res["status"], "merged")

    def test_two_stores_do_not_interleave_changes_to_one_repo(self):
        repo = self.repo()
        other = self.second_store()
        a = self.make("note a", repo, "writer")
        b = self.make("note b", repo, "writer")
        self.wait(a["id"])
        self.wait(b["id"])
        seen, real = [], T._git

        def traced(args, cwd, *rest, **kw):
            if args[:2] != ["rev-parse", "--git-common-dir"]:         # how the lock finds its file
                seen.append(threading.current_thread().name)
                time.sleep(0.05)
            return real(args, cwd, *rest, **kw)

        def go(name, call):
            threading.current_thread().name = name
            return call()

        with mock.patch.object(T, "_git", traced):
            got = race([lambda: go("merge-a", lambda: self.store.close(a["id"], "merge")),
                        lambda: go("discard-b", lambda: other.close(b["id"], "discard"))])
        self.assertEqual([g["status"] for g in got], ["merged", "discarded"], got)
        order = [name for name, _ in itertools.groupby(seen)]
        self.assertEqual(len(order), 2, f"the two changes ran interleaved: {order}")
        self.assertTrue(os.path.isfile(os.path.join(repo, ".git", "container-map.lock")))
        self.assertTrue(os.path.isfile(os.path.join(self.store_dir, ".store.lock")))

    def test_the_repository_lock_lives_in_the_common_dir_of_a_worktree_too(self):
        repo = self.repo()
        t = self.make("write a note", repo, "writer")
        self.wait(t["id"])
        wt = self.store._read_task(os.path.join(self.store_dir, t["id"]))["worktree"]
        self.assertEqual(self.store._repo_lock_path(wt),
                         os.path.join(repo, ".git", "container-map.lock"))

    def test_a_repo_whose_common_dir_cannot_be_found_is_locked_from_the_store(self):
        repo = self.repo()
        real = T._git

        def no_common_dir(args, cwd, *rest, **kw):
            if args[:2] == ["rev-parse", "--git-common-dir"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: not a repo")
            return real(args, cwd, *rest, **kw)
        with mock.patch.object(T, "_git", no_common_dir):
            path = self.store._repo_lock_path(repo)
            t = self.make("write a note", repo, "writer")
        self.assertEqual(os.path.dirname(path), self.store_dir)
        self.assertRegex(os.path.basename(path), r"^\.repo-[0-9a-f]{16}\.lock$")
        self.assertEqual(self.wait(t["id"])["status"], "review")

    def test_without_fcntl_everything_still_works_in_one_process(self):
        repo = self.repo()
        with mock.patch.object(T, "fcntl", None):
            t = self.make("write a note", repo, "writer")
            self.wait(t["id"])
            res = self.store.close(t["id"], "merge")
        self.assertEqual(res["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))

    def test_the_module_imports_where_fcntl_is_missing(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); sys.modules['fcntl'] = None\n"
                "import tasks; print(tasks.fcntl is None)\n")
        out = subprocess.run([sys.executable, "-I", "-c", code, HERE], capture_output=True,
                             text=True)
        self.assertEqual((out.returncode, out.stdout.strip()), (0, "True"), out.stderr)


class PreparingTests(TT.Harness):
    """Item 3: a crash between the worktree and task.json leaves something the store can manage."""

    @contextlib.contextmanager
    def crash_writing_the_record(self):
        real = json.dump

        def dump(obj, fp, *a, **kw):
            if "task.json" in getattr(fp, "name", ""):
                raise KeyboardInterrupt("simulated crash")
            return real(obj, fp, *a, **kw)
        with mock.patch.object(T.json, "dump", dump):
            with self.assertRaises(KeyboardInterrupt):
                yield

    def only_dir(self):
        names = [n for n in os.listdir(self.store_dir) if T.TASK_ID_RE.fullmatch(n)]
        self.assertEqual(len(names), 1, names)
        return names[0], os.path.join(self.store_dir, names[0])

    def test_a_worktree_made_before_a_crash_comes_back_as_a_failed_thread(self):
        repo = self.repo()
        with self.crash_writing_the_record():
            self.make("write a note", repo, "writer")
        tid, path = self.only_dir()
        self.assertFalse(os.path.exists(os.path.join(path, "task.json")))
        self.assertTrue(os.path.isdir(os.path.join(self.store.worktrees_dir, tid)))
        reopened = T.TaskStore(self.store_dir, self.runners, [self.work])
        rows = reopened.list()
        self.assertEqual([r["id"] for r in rows], [tid], rows)
        self.assertEqual(rows[0]["status"], "failed", rows[0])
        self.assertIn("interrupted", rows[0]["note"])
        self.assertEqual(rows[0]["mode"], "worktree")
        self.assertEqual(reopened.reconciled, [(tid, "failed")])
        res = reopened.close(tid, "discard")                      # the repo and the folder agree
        self.assertEqual(res["status"], "discarded")
        self.assertFalse(os.path.isdir(os.path.join(self.store.worktrees_dir, tid)))
        self.assertIn(f"cm/{tid}", git(repo, "branch", "--list"))

    def test_the_recovered_thread_can_be_sent_to_its_agent(self):
        repo = self.repo()
        with self.crash_writing_the_record():
            self.make("write a note", repo, "writer")
        tid, path = self.only_dir()
        reopened = T.TaskStore(self.store_dir, self.runners, [self.work])
        reopened.followup(tid, "go on")
        end = time.time() + 20
        while time.time() < end and reopened.summary(path)["status"] in ("running", "failed"):
            time.sleep(0.1)
        self.assertEqual(reopened.summary(path)["status"], "review")
        self.assertTrue(os.path.isfile(os.path.join(path, "prompt-2.txt")))

    def test_a_crash_before_anything_was_made_closes_the_empty_thread(self):
        repo = self.repo()
        real = T._git

        def git_that_dies(args, cwd, *rest, **kw):
            if args[:2] == ["worktree", "add"]:
                raise KeyboardInterrupt("simulated crash")
            return real(args, cwd, *rest, **kw)
        with mock.patch.object(T, "_git", git_that_dies):
            with self.assertRaises(KeyboardInterrupt):
                self.make("write a note", repo, "writer")
        tid, path = self.only_dir()
        reopened = T.TaskStore(self.store_dir, self.runners, [self.work])
        self.assertEqual(reopened.reconciled, [(tid, "abandoned")])
        row = reopened.list()[0]
        self.assertEqual((row["id"], row["status"]), (tid, "done"))
        self.assertIn("nothing was created", row["note"])
        self.assertEqual(git(repo, "worktree", "list").count("\n"), 0, "no worktree was made")

    def test_a_creator_that_is_still_alive_is_left_alone(self):
        repo = self.repo()
        with self.crash_writing_the_record():
            self.make("write a note", repo, "writer")
        tid, path = self.only_dir()
        with T._locked(path):                                    # the creator, still working
            busy = T.TaskStore(self.store_dir, self.runners, [self.work])
            self.assertEqual(busy.reconciled, [])
            self.assertFalse(os.path.exists(os.path.join(path, "task.json")))
        self.assertEqual(busy.reconcile(), [(tid, "failed")])    # gone now: the next pass
        self.assertEqual(busy.reconcile(), [], "and only once")

    def test_the_tick_of_a_running_store_recovers_a_creation_it_did_not_start_with(self):
        repo = self.repo()
        with self.crash_writing_the_record():
            self.make("write a note", repo, "writer")
        tid, path = self.only_dir()
        self.assertEqual(self.store.tick(), [])
        self.assertEqual(self.store.reconciled, [(tid, "failed")])
        self.assertEqual(self.store.summary(path)["status"], "failed")

    def test_an_ordinary_create_leaves_no_open_preparation(self):
        repo = self.repo()
        t = self.make("write a note", repo, "writer")
        self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertFalse(os.path.exists(os.path.join(path, "preparing.json")))
        self.assertEqual(json.loads(TT.read(os.path.join(path, "prepared.json")))["state"], "ready")
        self.assertEqual(T.TaskStore(self.store_dir, self.runners, [self.work]).reconciled, [])

    def test_a_record_that_cannot_be_written_is_recovered_at_once(self):
        repo = self.repo()
        real, failed = json.dump, []

        def dump(obj, fp, *a, **kw):
            if "task.json" in getattr(fp, "name", "") and not failed:      # the first write only
                failed.append(1)
                raise OSError(28, "No space left on device")
            return real(obj, fp, *a, **kw)
        with mock.patch.object(T.json, "dump", dump):
            with self.assertRaises(OSError) as caught:
                self.make("write a note", repo, "writer")
        self.assertIn("No space left", str(caught.exception))      # the cause, verbatim
        rows = self.store.list()
        self.assertEqual([r["status"] for r in rows], ["failed"], rows)
        self.assertIn("interrupted", rows[0]["note"])

    def test_a_refused_worktree_leaves_no_folder_behind(self):
        repo = self.repo("broken")
        real = T._git

        def refusing(args, cwd, *rest, **kw):
            if args[:2] == ["worktree", "add"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: refused")
            return real(args, cwd, *rest, **kw)
        with mock.patch.object(T, "_git", refusing):
            with self.assertRaises(T.TaskError) as caught:
                self.make("write a note", repo, "writer")
        self.assertEqual(caught.exception.status, 502)
        self.assertIn("fatal: refused", caught.exception.message)
        self.assertEqual([n for n in os.listdir(self.store_dir) if T.TASK_ID_RE.fullmatch(n)], [])


def git(cwd, *args):
    return TT.git(cwd, *args)


class BlockedRetryTests(TT.Harness):
    """Item 4: a retry that can never go out is blocked with its reason, and --wait ends."""

    def due_thread(self, repo):
        t = self.make("write a note", repo, "writer", verify="test -f never.txt", retries=1)
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["retry_due"]), ("failed", True))
        return t["id"], os.path.join(self.store_dir, t["id"])

    def test_a_retry_whose_agent_is_gone_is_blocked_with_the_reason(self):
        repo = self.repo()
        tid, path = self.due_thread(repo)
        del self.store.runners["writer"]
        self.assertEqual(self.store.tick(), [])
        s = self.store.summary(path)
        self.assertEqual((s["status"], s["retry_due"]), ("failed", False))
        self.assertIn("writer is not offered any more", s["blocked"])
        self.assertEqual(self.store.tick(), [])
        self.assertEqual(self.store.tick_errors, [], "a second pass has nothing new to report")
        self.assertEqual(sum(1 for e in self.store._events(path) if e["ev"] == "blocked"), 1)
        self.assertEqual(self.store.running_count(), 0)

    def test_a_retry_whose_worktree_is_gone_is_blocked_with_the_reason(self):
        repo = self.repo()
        tid, path = self.due_thread(repo)
        shutil.rmtree(os.path.join(self.store.worktrees_dir, tid))
        errors = []
        self.store.on_tick_error = lambda t, m: errors.append((t, m))
        self.store.tick()
        s = self.store.summary(path)
        self.assertEqual((s["status"], s["retry_due"]), ("failed", False))
        self.assertIn("worktree is gone", s["blocked"])
        self.assertEqual([t for t, m in errors], [tid])
        self.assertIn("worktree is gone", errors[0][1])

    def test_a_reply_after_a_blocked_retry_goes_through_and_clears_it(self):
        repo = self.repo()
        tid, path = self.due_thread(repo)
        saved = self.store.runners.pop("writer")
        self.store.tick()
        self.assertIsNotNone(self.store.summary(path)["blocked"])
        self.store.runners["writer"] = saved
        self.store.followup(tid, "try again")
        s = self.wait(tid)
        self.assertIsNone(s["blocked"])
        self.assertEqual(s["runs"], 2)

    def test_wait_ends_with_a_failure_and_the_reason_instead_of_polling_forever(self):
        repo = self.repo()
        t = self.make("write a note", repo, "writer", verify="test -f never.txt", retries=1)
        del self.store.runners["writer"]
        out, result = io.StringIO(), []
        with mock.patch.object(cli, "TICK_SECONDS", 0.3), \
                mock.patch.object(cli, "POLL_SECONDS", 0.1):
            worker = threading.Thread(daemon=True, target=lambda: result.append(
                cli._follow(self.store, t["id"], cli._work_settled, out)))
            worker.start()
            worker.join(25)
        self.assertFalse(worker.is_alive(), "--wait is still polling a retry that can never go out")
        code, got, _ = result[0]
        self.assertEqual(code, 1)
        text = cli._final_text(cli._summary(got), got.get("verify_tail"))
        self.assertIn("retry blocked", text)
        self.assertIn("writer is not offered any more", text)


class LostThreadTests(TT.Harness):
    """Item 5: a lost thread whose agent group still lives keeps its place until it is stopped."""

    def test_a_surviving_agent_counts_toward_max_running_until_it_is_stopped(self):
        self.store.max_running = 1
        folder = os.path.join(self.work, "lost-count")
        os.makedirs(folder)
        t = self.make("sleep", folder, "sleeper")
        child = self.child_event(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.kill_supervisor(t["id"])
        self.assertEqual(self.store.summary(path)["status"], "lost")
        self.assertEqual(self.store.running_count(), 1)
        other = os.path.join(self.work, "lost-count-2")
        os.makedirs(other)
        with self.assertRaises(T.TaskError) as caught:
            self.make("another", other, "writer")
        self.assertEqual(caught.exception.status, 429)
        self.store.stop(t["id"])
        self.assertTrue(self.group_gone(child["pid"]))
        self.assertEqual(self.store.running_count(), 0)
        self.assertIn(self.make("another", other, "writer")["status"], ("running", "review"))

    def test_a_lost_thread_with_nothing_left_alive_holds_no_place(self):
        folder = os.path.join(self.work, "lost-dead")
        os.makedirs(folder)
        t = self.make("sleep", folder, "sleeper")
        child = self.child_event(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.kill_supervisor(t["id"])
        os.killpg(child["pid"], signal.SIGKILL)
        self.assertTrue(self.group_gone(child["pid"]))
        self.assertEqual(self.store.summary(path)["status"], "lost")
        self.assertEqual(self.store.running_count(), 0)

    def test_a_lost_checker_that_is_still_alive_holds_a_place_too(self):
        repo = self.repo()
        t = self.make("note", repo, "writer")
        self.wait(t["id"])
        tid = t["id"]
        path = os.path.join(self.store_dir, tid)
        self.store.check(tid, "slowjudge")
        child = self.child_event(tid, n=2)
        self.kill_supervisor(tid, n=2)
        self.assertEqual(self.store.summary(path)["check"]["verdict"], "lost")
        self.assertEqual(self.store.running_count(), 1)
        self.store.stop(tid)
        self.assertTrue(self.group_gone(child["pid"]))
        self.assertEqual(self.store.running_count(), 0)


class HistoryTests(TT.Harness):
    """Item 6: the list limit is for closed history; an open thread is never cut off."""

    def thread_at(self, offset, folder_name, runner, text="make a note"):
        folder = os.path.join(self.work, folder_name)
        os.makedirs(folder)
        base = 1_900_000_000
        self.store.now = lambda: base + offset
        return self.make(text, folder, runner)

    def test_an_old_open_thread_stays_listed_beyond_the_closed_history(self):
        old = self.thread_at(0, "old", "writer")
        self.wait(old["id"])
        closed = []
        for i in range(1, 5):
            t = self.thread_at(i * 10, f"new{i}", "writer")
            self.wait(t["id"])
            self.store.close(t["id"], "done")
            closed.append(t["id"])
        with mock.patch.object(T, "MAX_TASKS_LISTED", 2):
            ids = [r["id"] for r in self.store.list()]
        self.assertIn(old["id"], ids, "the old open thread fell off the list")
        self.assertEqual([i for i in ids if i in closed], closed[::-1][:2],
                         "closed history is cut to the newest, newest first")
        self.assertEqual(ids[-1], old["id"])
        self.assertEqual(len(ids), 3)

    def test_an_old_running_thread_still_counts_toward_max_running(self):
        old = self.thread_at(0, "old-run", "sleeper")
        self.child_event(old["id"])
        for i in range(1, 4):
            t = self.thread_at(i * 10, f"newer{i}", "writer")
            self.wait(t["id"])
            self.store.close(t["id"], "done")
        with mock.patch.object(T, "MAX_TASKS_LISTED", 2):
            self.assertEqual(self.store.running_count(), 1)
            self.assertIn(old["id"], [r["id"] for r in self.store.list()])
            running = [r["id"] for r in self.store.list() if r["status"] == "running"]
            self.assertEqual(running, [old["id"]])


class GitEnvTests(TT.Harness):
    """Item 8: a bridge started from a hook or inside a repo command carries GIT_DIR and friends;
    the engine's own git calls must not."""

    def test_the_callers_git_variables_are_dropped_and_the_few_that_matter_kept(self):
        env = {"GIT_DIR": "/x", "GIT_WORK_TREE": "/y", "GIT_INDEX_FILE": "/z",
               "GIT_COMMON_DIR": "/c",
               "GIT_OBJECT_DIRECTORY": "/o", "GIT_SSH_COMMAND": "ssh -i k", "GIT_SSH": "/bin/ssh",
               "GIT_ASKPASS": "/bin/pw", "GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin",
               "HOME": "/h"}
        with mock.patch.dict(os.environ, env, clear=True):
            got = T._git_env({"GIT_INDEX_FILE": "/scratch"})
            plain = T._git_env()
        self.assertEqual(sorted(k for k in got if k.startswith("GIT_")),
                         ["GIT_ASKPASS", "GIT_INDEX_FILE", "GIT_SSH", "GIT_SSH_COMMAND",
                          "GIT_TERMINAL_PROMPT"])
        self.assertEqual(got["GIT_INDEX_FILE"], "/scratch")
        self.assertNotIn("GIT_INDEX_FILE", plain)
        self.assertEqual((plain["PATH"], plain["HOME"]), ("/usr/bin", "/h"))

    def test_a_thread_works_with_git_dir_and_friends_in_the_environment(self):
        repo = self.repo()
        decoy = self.repo("decoy")
        decoy_head = git(decoy, "rev-parse", "HEAD")
        poisoned = {"GIT_DIR": os.path.join(decoy, ".git"), "GIT_WORK_TREE": decoy,
                    "GIT_INDEX_FILE": os.path.join(self.root, "no-such-index")}
        with mock.patch.dict(os.environ, poisoned):
            t = self.make("write a note", repo, "writer")
            s = self.wait(t["id"])
            diff = self.store.diff(t["id"])
            res = self.store.close(t["id"], "merge")
        self.assertEqual((t["mode"], t["repo"], s["status"]), ("worktree", repo, "review"))
        self.assertEqual([f["path"] for f in diff["files"]], ["agent-note.txt"])
        self.assertEqual(res["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))
        self.assertEqual(git(decoy, "rev-parse", "HEAD"), decoy_head, "the decoy repo was touched")
        self.assertFalse(os.path.exists(os.path.join(decoy, "agent-note.txt")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
