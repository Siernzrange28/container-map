#!/usr/bin/env python3
"""test_worktree_hygiene.py — what a thread leaves behind and how far its evidence can be trusted:
build junk is not committed onto the branch, verify passing is reported as what it is (and a check
can be required), the worktree lives outside the receipts, and a worktree git cannot remove is
moved aside instead of failing the close. Real git, real processes, temp dirs, a fake agent."""
import contextlib
import io
import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T
import test_tasks as TT

ROOT = Path(__file__).resolve().parent
git, read = TT.git, TT.read

FAKE = textwrap.dedent(r'''
    import os, sys
    mode = sys.argv[1]
    sys.stdin.read()

    def put(path, text="x\n"):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)

    if mode in ("junker", "pyc"):
        put("real.py", "print(1)\n")
        put("__pycache__/real.cpython-312.pyc")
        put("pkg/__pycache__/mod.cpython-312.pyc")
        put(".pytest_cache/v/cache/lastfailed", "{}")
        put("node_modules/dep/index.js")
        put(".DS_Store")
        put("lib.egg-info/PKG-INFO")
        put(".venv/bin/python")
        if mode == "junker":
            put(".mimocode/session.json", "{}")
    elif mode == "dsstore":                      # touches a tracked file that matches a pattern
        with open(".DS_Store", "a") as handle:
            handle.write("changed\n")
    elif mode == "logs":
        put("keep.txt")
        put("build.log")
        put("secret.env")
        put("__pycache__/m.pyc")
    elif mode == "mixed":                        # tracked edits and deletes, odd names, junk, all at once
        with open("shared.txt", "a") as handle:
            handle.write("edited\n")
        os.remove("gone.txt")
        put("new file [1].txt", "n\n")
        put("sub dir/ünï.py", "u\n")
        put("__pycache__/x y.pyc")
    elif mode == "forge":                        # an agent reaching for ../ from its worktree
        with open(os.path.join("..", "events.jsonl"), "a") as handle:
            handle.write('{"ev":"merged","forged":true}\n')
        put(os.path.join("..", "task.json"), "{}")
        put("agent-note.txt")
    elif mode == "buildlog":                     # your own skip list names a file in build/
        put("build/important.log", "keep me\n")
        put("note.txt")
    elif mode == "bulk":                         # a big install beside a few real files
        for i in range(int(os.environ.get("BULK_JUNK", "30000"))):
            put(f"node_modules/pkg{i % 40}/f{i}.js", "")
        for i in range(200):
            put(f"src/m{i}.py", "x = 1\n")
    elif mode == "rodir":                        # an ignored build folder git cannot delete
        put("node_modules/x/i.js")
        os.chmod(os.path.join("node_modules", "x"), 0o555)
        put("note.txt")
''')


# chmod 0555 stops nobody who is root (a CI container): git removes the folder, nothing is retired
NOT_ROOT = unittest.skipIf(getattr(os, "geteuid", lambda: 1)() == 0,
                           "root can delete a read-only folder, so there is nothing to move aside")


def has_junk(paths):
    return [p for p in paths if any(part in ("__pycache__", "node_modules", ".pytest_cache", ".venv",
                                            ".mimocode") or part.endswith((".pyc", ".egg-info"))
                                    or part == ".DS_Store" for part in p.split("/"))]


class E3Harness(TT.Harness):
    def setUp(self):
        super().setUp()
        self.fake3 = os.path.join(self.side, "fake_e3.py")
        with open(self.fake3, "w") as handle:
            handle.write(FAKE)
        self.runners.update(T.valid_runners({
            name: {"cmd": [sys.executable, self.fake3, name], "max_minutes": 1}
            for name in ("junker", "pyc", "dsstore", "logs", "mixed", "forge", "rodir", "bulk",
                         "buildlog")}))
        self.wt_root = os.path.join(self.root, "store-worktrees")

    def store_with(self, **kw):
        self.store = T.TaskStore(self.store_dir, self.runners, [self.work], **kw)
        self.store.last_create = -100.0
        return self.store

    def events(self, tid):
        return self.store._events(os.path.join(self.store_dir, tid))

    def closing(self, tid, ev):
        return next(e for e in self.events(tid) if e["ev"] == ev)

    def tracked(self, repo, ref="HEAD"):
        return git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()


class MergeSkipTests(E3Harness):
    def test_the_default_list_is_the_one_the_config_documents(self):
        import config
        self.assertEqual(config.DEFAULTS["merge_skip"], T.DEFAULT_MERGE_SKIP)
        for needed in ("__pycache__/", "*.pyc", ".pytest_cache/", ".mypy_cache/", "node_modules/",
                       ".DS_Store", ".mimocode/", "*.egg-info/", ".venv/"):
            self.assertIn(needed, T.DEFAULT_MERGE_SKIP)

    def test_malformed_patterns_are_dropped_and_an_empty_list_stays_empty(self):
        self.assertEqual(T.valid_merge_skip(["a/", "", "  ", 5, None, "x\ny", "b" * 300, " *.log "]),
                         ["a/", "*.log"])
        self.assertEqual(T.valid_merge_skip([]), [])
        self.assertEqual(T.valid_merge_skip("not a list"), T.DEFAULT_MERGE_SKIP)
        self.assertEqual(T.valid_merge_skip(None), T.DEFAULT_MERGE_SKIP)
        self.assertEqual(T.TaskStore(self.store_dir, self.runners, [self.work]).merge_skip,
                         T.DEFAULT_MERGE_SKIP)
        self.assertEqual(T.TaskStore(self.store_dir, self.runners, [self.work], merge_skip=[]).merge_skip, [])

    def test_build_junk_stays_off_the_branch_and_the_receipt_names_it(self):
        repo = self.repo()                                  # no .gitignore: the case that bit
        t = self.make("write code", repo, "junker")
        self.assertEqual(self.wait(t["id"])["status"], "review")
        out = self.store.close(t["id"], "merge")
        self.assertEqual(out["status"], "merged")
        self.assertEqual(has_junk(self.tracked(repo, f"cm/{t['id']}")), [])
        self.assertEqual(has_junk(self.tracked(repo)), [])
        self.assertIn("real.py", self.tracked(repo))
        self.assertTrue(os.path.isfile(os.path.join(repo, "real.py")))
        merged = self.closing(t["id"], "merged")
        self.assertTrue(merged["committed"])
        self.assertEqual(merged["skipped"]["count"], 8)
        self.assertEqual(merged["skipped"]["paths"],
                         [".DS_Store", ".mimocode/", ".pytest_cache/", ".venv/", "__pycache__/",
                          "lib.egg-info/", "node_modules/", "pkg/__pycache__/"])
        self.assertIn("skipped", out["message"])
        self.assertIn("__pycache__/", out["message"])

    def test_discard_skips_the_same_junk_and_records_it(self):
        repo = self.repo()
        t = self.make("write code", repo, "junker")
        self.wait(t["id"])
        out = self.store.close(t["id"], "discard")
        self.assertEqual(out["status"], "discarded")
        self.assertEqual(has_junk(self.tracked(repo, f"cm/{t['id']}")), [])
        self.assertIn("real.py", self.tracked(repo, f"cm/{t['id']}"))
        self.assertEqual(self.closing(t["id"], "discarded")["skipped"]["count"], 8)

    def test_a_thread_that_left_no_junk_records_no_skipped_key(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        self.store.close(t["id"], "merge")
        self.assertNotIn("skipped", self.closing(t["id"], "merged"))

    def test_an_empty_merge_skip_commits_everything_as_before(self):
        self.store_with(merge_skip=[])
        repo = self.repo()
        t = self.make("write code", repo, "pyc")
        self.wait(t["id"])
        self.store.close(t["id"], "merge")
        tracked = self.tracked(repo)
        for junk in ("__pycache__/real.cpython-312.pyc", "node_modules/dep/index.js", ".DS_Store"):
            self.assertIn(junk, tracked)
        self.assertNotIn("skipped", self.closing(t["id"], "merged"))

    def test_a_tracked_file_that_matches_a_pattern_is_still_committed(self):
        repo = self.repo()
        with open(os.path.join(repo, ".DS_Store"), "w") as handle:
            handle.write("seed\n")
        git(repo, "add", "-f", ".DS_Store")
        git(repo, "commit", "-q", "-m", "tracked junk")
        t = self.make("touch", repo, "dsstore")
        self.wait(t["id"])
        self.store.close(t["id"], "merge")
        self.assertEqual(read(os.path.join(repo, ".DS_Store")), "seed\nchanged\n")
        self.assertNotIn("skipped", self.closing(t["id"], "merged"))

    def test_the_configured_patterns_replace_the_defaults(self):
        self.store_with(merge_skip=["*.log", "secret.env"])
        repo = self.repo()
        t = self.make("write", repo, "logs")
        self.wait(t["id"])
        self.store.close(t["id"], "merge")
        tracked = self.tracked(repo)
        self.assertIn("keep.txt", tracked)
        self.assertIn("__pycache__/m.pyc", tracked)             # not in this list any more
        self.assertNotIn("build.log", tracked)
        self.assertNotIn("secret.env", tracked)
        self.assertEqual(self.closing(t["id"], "merged")["skipped"],
                         {"count": 2, "paths": ["build.log", "secret.env"]})

    def test_tracked_edits_deletes_and_oddly_named_files_land_beside_the_skipped_junk(self):
        repo = self.repo()
        with open(os.path.join(repo, "gone.txt"), "w") as handle:
            handle.write("bye\n")
        git(repo, "add", "gone.txt")
        git(repo, "commit", "-q", "-m", "second file")
        t = self.make("tidy", repo, "mixed")
        self.wait(t["id"])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")
        self.assertEqual(read(os.path.join(repo, "shared.txt")), "original\nedited\n")
        self.assertFalse(os.path.exists(os.path.join(repo, "gone.txt")))
        self.assertEqual(read(os.path.join(repo, "new file [1].txt")), "n\n")
        self.assertEqual(read(os.path.join(repo, "sub dir", "ünï.py")), "u\n")
        self.assertFalse(os.path.exists(os.path.join(repo, "__pycache__")))
        self.assertEqual(self.closing(t["id"], "merged")["skipped"],
                         {"count": 1, "paths": ["__pycache__/"]})
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_the_receipt_lists_a_few_paths_and_counts_the_rest(self):
        units = T._skip_units([f"d{i:02d}/f.txt" for i in range(30)] + ["a.log"], ["d*/", "*.log"])
        self.assertEqual(units["count"], 31)
        self.assertEqual(len(units["paths"]), T.MAX_SKIP_SHOWN)
        self.assertEqual(units["paths"][0], "a.log")
        self.assertEqual(units["paths"][1], "d00/")

    def test_a_big_install_is_skipped_in_linear_time(self):
        # 30000 junk files and 200 real ones: the skip must not rescan the whole listing per file
        repo = self.repo()
        t = self.make("install", repo, "bulk")
        self.assertEqual(self.wait(t["id"], timeout=120)["status"], "review")
        began = time.monotonic()
        out = self.store.close(t["id"], "merge")
        took = time.monotonic() - began
        self.assertEqual(out["status"], "merged")
        self.assertLess(took, 4.0, f"merging 30000 skipped files took {took:.1f}s")
        tracked = self.tracked(repo)
        self.assertEqual(has_junk(tracked), [])
        self.assertEqual(len([p for p in tracked if p.startswith("src/")]), 200)
        self.assertEqual(self.closing(t["id"], "merged")["skipped"],
                         {"count": 30000, "paths": ["node_modules/"]})

    def test_a_listing_git_cannot_make_commits_nothing(self):
        repo = self.repo()
        t = self.make("write code", repo, "junker")
        self.wait(t["id"])
        real = T._git

        def broken(args, cwd, *rest, **kw):
            if args[:2] == ["ls-files", "--others"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: index file corrupt")
            return real(args, cwd, *rest, **kw)
        with unittest_patch(T, "_git", broken):
            with self.assertRaises(T.TaskError) as err:
                self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 502)
        self.assertIn("merge_skip", err.exception.message)
        self.assertIn("index file corrupt", err.exception.message)
        self.assertEqual(git(repo, "rev-list", "--count", f"HEAD..cm/{t['id']}"), "0")
        self.assertTrue(os.path.isdir(os.path.join(self.wt_root, t["id"])))
        summary = self.store.summary(os.path.join(self.store_dir, t["id"]))
        self.assertEqual(summary["status"], "review")


class EvidenceTests(E3Harness):
    def test_the_summary_says_how_far_the_work_was_proved(self):
        repo = self.repo()
        bare = self.make("add a note", repo, "sessioned")
        s = self.wait(bare["id"])
        self.assertEqual((s["evidence"], s["verify"]), ("none", None))
        self.store.check(bare["id"], "sessioned", None, "please look")
        self.assertEqual(self.wait(bare["id"])["evidence"], "checked")
        both = self.make("add a note", repo, "sessioned", True, None, "test -f agent-note.txt")
        self.assertEqual(self.wait(both["id"])["evidence"], "verified")
        self.store.check(both["id"], "sessioned", None, "please look")
        s = self.wait(both["id"])
        self.assertEqual((s["evidence"], s["check"]["verdict"]), ("verified and checked", "PASS"))
        self.store.followup(both["id"], "more")                  # work after the check makes it stale
        s = self.wait(both["id"])
        self.assertEqual((s["evidence"], s["check"]["stale"]), ("verified", True))
        failed = self.make("x", repo, "writer", True, None, "false")
        self.assertEqual(self.wait(failed["id"])["evidence"], "none")
        self.assertEqual({t["id"]: t["evidence"] for t in self.store.list()},
                         {bare["id"]: "checked", both["id"]: "verified", failed["id"]: "none"})
        self.assertEqual(self.store.get(failed["id"])["evidence"], "none")

    def test_a_failing_check_is_not_evidence(self):
        repo = self.repo()
        t = self.make("add a note", repo, "sessioned")
        self.wait(t["id"])
        self.store.check(t["id"], "sessioned", None, "please fail this")
        s = self.wait(t["id"])
        self.assertEqual((s["check"]["verdict"], s["evidence"]), ("FAIL", "none"))

    def test_by_default_a_merge_needs_no_check(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer", True, None, "test -f agent-note.txt")
        self.assertEqual(self.wait(t["id"])["merge_blockers"], [])
        self.assertFalse(self.store.merge_requires_check)
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")

    def test_merge_requires_check_blocks_until_a_fresh_pass(self):
        self.store_with(merge_requires_check=True)
        repo = self.repo()
        t = self.make("add a note", repo, "sessioned", True, None, "test -f agent-note.txt")
        s = self.wait(t["id"])
        self.assertEqual((s["status"], s["evidence"], s["merge_blockers"]),
                         ("review", "verified", ["no independent check"]))
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "merge")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("no independent check", err.exception.message)
        self.assertFalse(os.path.exists(os.path.join(repo, "agent-note.txt")))
        self.store.check(t["id"], "sessioned", None, "please look")
        s = self.wait(t["id"])
        self.assertEqual((s["evidence"], s["merge_blockers"]), ("verified and checked", []))
        self.store.followup(t["id"], "one more thing")           # the pass goes stale
        s = self.wait(t["id"])
        self.assertEqual(s["merge_blockers"], ["no independent check"])
        self.store.check(t["id"], "sessioned", None, "please look again")
        self.assertEqual(self.wait(t["id"])["merge_blockers"], [])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")

    def test_a_fresh_fail_is_named_once_and_force_lands_past_a_missing_check(self):
        self.store_with(merge_requires_check=True)
        repo = self.repo()
        failed = self.make("add a note", repo, "sessioned")
        self.wait(failed["id"])
        self.store.check(failed["id"], "sessioned", None, "please fail this")
        self.assertEqual(self.wait(failed["id"])["merge_blockers"], ["check by sessioned: FAIL"])
        bare = self.make("add a note", repo, "writer")
        self.assertEqual(self.wait(bare["id"])["merge_blockers"], ["no independent check"])
        self.assertEqual(self.store.close(bare["id"], "merge", force=True)["status"], "merged")
        self.assertTrue(self.closing(bare["id"], "merged")["evidence"]["forced"])

    def test_the_flag_is_never_served_from_another_stores_cache(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual(self.store.summary(path)["merge_blockers"], [])
        strict = T.TaskStore(self.store_dir, self.runners, [self.work], merge_requires_check=True)
        self.assertEqual(strict.summary(path)["merge_blockers"], ["no independent check"])
        self.assertEqual(self.store.summary(path)["merge_blockers"], [])

    def test_the_cli_prints_the_evidence(self):
        import cli
        conf = os.path.join(self.root, "container.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1, "work_root": self.work, "tasks_dir": self.store_dir,
                       "task_scope": False, "detect_runners": False, "tmux_session": "none",
                       "merge_requires_check": True,
                       "runners": {"writer": {"cmd": [sys.executable, self.fake, "write"],
                                              "max_minutes": 1}}}, handle)
        repo = self.repo()
        t = self.make("add a note", repo, "writer", True, None, "test -f agent-note.txt")
        self.wait(t["id"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["thread", "show", t["id"], "--config", conf])
        self.assertEqual(rc, 0)
        self.assertIn("evidence verified", out.getvalue())
        self.assertIn("merge blockers: no independent check", out.getvalue())
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["thread", "show", t["id"], "--json", "--config", conf])
        self.assertEqual(json.loads(out.getvalue())["thread"]["evidence"], "verified")

    def test_the_readme_says_verify_is_not_proof_and_recommends_a_check(self):
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("verify passing is not proof of correctness", readme.lower())
        self.assertIn("merge_requires_check", readme)


class WorktreePlacementTests(E3Harness):
    def worktree_of(self, tid):
        return json.loads(read(os.path.join(self.store_dir, tid, "task.json")))["worktree"]

    def test_a_new_threads_worktree_is_not_under_its_receipts(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        wt = self.worktree_of(t["id"])
        self.assertEqual(wt, os.path.join(self.wt_root, t["id"]))
        self.assertFalse(os.path.realpath(wt).startswith(os.path.join(self.store_dir, t["id"]) + "/"))
        self.assertTrue(os.path.isdir(wt))
        self.assertFalse(os.path.exists(os.path.join(self.store_dir, t["id"], "worktree")))
        self.assertEqual(self.wait(t["id"])["cwd"], wt)
        self.assertEqual(self.store.worktrees_dir, self.wt_root)

    def test_the_folder_can_be_configured(self):
        elsewhere = os.path.join(self.root, "elsewhere", "wts")
        self.store_with(worktrees_dir=elsewhere)
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.assertEqual(self.worktree_of(t["id"]), os.path.join(elsewhere, t["id"]))
        self.wait(t["id"])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))
        self.assertFalse(os.path.exists(os.path.join(elsewhere, t["id"])))

    def test_default_folder_is_a_sibling_named_after_the_store(self):
        self.assertEqual(T.default_worktrees_dir("/a/b/tasks"), "/a/b/tasks-worktrees")
        self.assertEqual(T.default_worktrees_dir("/a/b/tasks/"), "/a/b/tasks-worktrees")

    def test_an_agent_reaching_for_dotdot_cannot_forge_the_receipts(self):
        repo = self.repo()
        t = self.make("add a note", repo, "forge")
        self.wait(t["id"])
        path = os.path.join(self.store_dir, t["id"])
        self.assertNotIn("forged", read(os.path.join(path, "events.jsonl")))
        self.assertEqual(json.loads(read(os.path.join(path, "task.json")))["id"], t["id"])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")    # still trusted
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))

    def test_a_thread_made_the_old_way_is_still_read_and_closed(self):
        repo = self.repo()
        legacy = lambda tid, path: os.path.join(path, "worktree")
        with unittest_patch(self.store, "_worktree_path", legacy):
            t = self.make("add a note", repo, "writer")
        self.assertEqual(self.worktree_of(t["id"]), os.path.join(self.store_dir, t["id"], "worktree"))
        self.assertEqual(self.wait(t["id"])["status"], "review")
        self.assertIn("agent-note.txt", [f["path"] for f in self.store.diff(t["id"])["files"]])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "agent-note.txt")))

    def test_a_record_pointing_the_worktree_elsewhere_is_refused(self):
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        self.wait(t["id"])
        record = os.path.join(self.store_dir, t["id"], "task.json")
        task = json.loads(read(record))
        other = os.path.join(self.root, "other", t["id"])
        os.makedirs(other)
        task["worktree"] = task["cwd"] = other
        with open(record, "w") as handle:
            json.dump(task, handle)
        with self.assertRaises(T.TaskError) as err:
            self.store.close(t["id"], "discard")
        self.assertEqual(err.exception.status, 409)
        self.assertIn("altered", err.exception.message)

    def test_a_thread_cannot_be_aimed_at_the_worktree_folder(self):
        self.store = T.TaskStore(self.store_dir, self.runners, [self.root])     # the folder is inside its roots
        self.store.last_create = -100.0
        repo = self.repo()
        t = self.make("add a note", repo, "writer")
        with self.assertRaises(T.TaskError) as err:
            self.make("sneak", self.worktree_of(t["id"]), "writer")
        self.assertEqual(err.exception.status, 403)
        self.assertIn("task store", err.exception.message)

    def test_store_for_takes_the_configured_folder_only_for_its_own_store(self):
        cfg = {"tasks_dir": self.store_dir, "worktrees_dir": os.path.join(self.root, "cfg-wts"),
               "task_scope": False}
        self.assertEqual(T.store_for(cfg, [self.work], runners={}).worktrees_dir, cfg["worktrees_dir"])
        other = os.path.join(self.root, "tests-store")
        self.assertEqual(T.store_for(cfg, [self.work], other, runners={}).worktrees_dir, other + "-worktrees")
        self.assertEqual(T.store_for({"tasks_dir": self.store_dir}, [self.work], runners={}).worktrees_dir,
                         self.store_dir + "-worktrees")

    def test_ships_know_a_process_in_the_worktree_folder_belongs_to_a_thread(self):
        import ships
        belongs = ships.thread_filter(self.store_dir)
        self.assertTrue(belongs(1, 1, os.path.join(self.wt_root, "20261010-000000-x-abcdef")))
        self.assertTrue(belongs(1, 1, os.path.join(self.store_dir, "20261010-000000-x-abcdef")))
        self.assertFalse(belongs(1, 1, self.work))
        custom = os.path.join(self.root, "wts")
        self.assertTrue(ships.thread_filter(self.store_dir, custom)(1, 1, custom + "/x"))

    def test_a_worktrees_folder_that_cannot_be_made_says_how_to_fix_it(self):
        blocker = os.path.join(self.root, "a-file")
        with open(blocker, "w") as handle:
            handle.write("x\n")
        self.store_with(worktrees_dir=os.path.join(blocker, "wts"))
        repo = self.repo()
        with self.assertRaises(T.TaskError) as err:
            self.make("add a note", repo, "writer")
        self.assertEqual(err.exception.status, 502)
        self.assertIn("could not make the worktrees folder", err.exception.message)
        self.assertIn(os.path.join(blocker, "wts"), err.exception.message)
        self.assertIn("Set worktrees_dir", err.exception.message)

    def test_a_worktrees_folder_inside_the_receipts_is_refused_before_anything_is_made(self):
        self.store_with(worktrees_dir=os.path.join(self.store_dir, "inside"))
        repo = self.repo()
        with self.assertRaises(T.TaskError) as err:
            self.make("add a note", repo, "writer")
        self.assertEqual(err.exception.status, 500)
        self.assertIn("inside tasks_dir", err.exception.message)
        threads = [n for n in os.listdir(self.store_dir) if T.TASK_ID_RE.fullmatch(n)] \
            if os.path.isdir(self.store_dir) else []
        self.assertEqual(threads, [])                  # (the store's own .store.lock is no thread)

    def test_the_trust_model_is_written_down(self):
        text = " ".join((ROOT / "docs" / "CONFIG.md").read_text(encoding="utf-8").split())
        self.assertIn("worktrees_dir", text)
        self.assertIn("run as your user", text)
        self.assertIn("determined agent", text)


@contextlib.contextmanager
def unittest_patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


class RetireTests(E3Harness):
    """A worktree git cannot remove, or one holding what is not a build byproduct, moves aside; one
    holding only byproducts is removed. Neither fails the close, and the work is on the branch."""

    def ignoring_repo(self):
        repo = self.repo()
        with open(os.path.join(repo, ".gitignore"), "w") as handle:
            handle.write("node_modules/\n")
        git(repo, "add", ".gitignore")
        git(repo, "commit", "-q", "-m", "ignore")
        return repo

    def aside(self, tid):
        return [n for n in os.listdir(self.wt_root) if n.startswith(tid + ".retired-")]

    @NOT_ROOT
    def test_ignored_build_artifacts_git_cannot_delete_move_the_worktree_aside(self):
        repo = self.ignoring_repo()
        t = self.make("build", repo, "rodir")
        self.assertEqual(self.wait(t["id"])["status"], "review")
        out = self.store.close(t["id"], "discard")          # git worktree remove alone: Permission denied
        self.assertEqual(out["status"], "discarded")
        wt = os.path.join(self.wt_root, t["id"])
        self.assertFalse(os.path.exists(wt))
        aside = self.aside(t["id"])
        self.assertEqual(len(aside), 1)
        self.assertTrue(os.path.isfile(os.path.join(self.wt_root, aside[0], "node_modules", "x", "i.js")))
        closed = self.closing(t["id"], "discarded")
        self.assertEqual(closed["retired"], os.path.join(self.wt_root, aside[0]))
        self.assertIn("could not remove", out["message"])
        self.assertIn("note.txt", self.tracked(repo, f"cm/{t['id']}"))
        self.assertNotIn(aside[0], git(repo, "worktree", "list"))

    @NOT_ROOT
    def test_merge_survives_it_too(self):
        repo = self.ignoring_repo()
        t = self.make("build", repo, "rodir")
        self.wait(t["id"])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")
        self.assertTrue(os.path.isfile(os.path.join(repo, "note.txt")))
        self.assertEqual(len(self.aside(t["id"])), 1)

    def test_untracked_byproducts_alone_are_removed_with_the_worktree(self):
        repo = self.repo()                                  # no .gitignore: they are untracked, not ignored
        t = self.make("build", repo, "pyc")
        self.wait(t["id"])
        out = self.store.close(t["id"], "discard")
        self.assertEqual(out["status"], "discarded")
        self.assertFalse(os.path.exists(os.path.join(self.wt_root, t["id"])))
        self.assertEqual(self.aside(t["id"]), [])
        self.assertNotIn("retired", self.closing(t["id"], "discarded"))
        self.assertEqual(has_junk(self.tracked(repo, f"cm/{t['id']}")), [])

    def test_an_untracked_file_that_is_not_a_byproduct_keeps_the_worktree(self):
        self.store_with(merge_skip=["*.log", "secret.*"])
        repo = self.repo()
        t = self.make("write", repo, "logs")
        self.wait(t["id"])
        out = self.store.close(t["id"], "discard")
        closed = self.closing(t["id"], "discarded")
        self.assertEqual([n.split(".")[0] for n in closed["kept"]], ["build", "secret"])
        for name in closed["kept"]:
            self.assertTrue(os.path.isfile(os.path.join(closed["retired"], name)))
        self.assertIn("build.log", out["message"])
        self.assertNotIn("build.log", self.tracked(repo, f"cm/{t['id']}"))

    def test_what_the_default_list_names_goes_with_the_worktree_mimocode_included(self):
        repo = self.repo()
        t = self.make("build", repo, "junker")              # leaves .mimocode/, a default-list name
        self.wait(t["id"])
        out = self.store.close(t["id"], "discard")
        self.assertEqual(out["status"], "discarded")
        closed = self.closing(t["id"], "discarded")
        self.assertNotIn("retired", closed)
        self.assertNotIn("kept", closed)
        self.assertFalse(os.path.exists(os.path.join(self.wt_root, t["id"])))
        self.assertEqual(self.aside(t["id"]), [])
        self.assertIn(".mimocode/", out["message"])
        self.assertIn("removed with the worktree", out["message"])
        self.assertIn("real.py", self.tracked(repo, f"cm/{t['id']}"))

    def test_a_skipped_file_in_a_build_folder_is_not_lost_with_the_worktree(self):
        self.store_with(merge_skip=["*.log"])
        repo = self.repo()
        t = self.make("write", repo, "buildlog")
        self.wait(t["id"])
        self.assertEqual(self.store.close(t["id"], "merge")["status"], "merged")
        closed = self.closing(t["id"], "merged")
        self.assertEqual(closed["kept"], ["build/important.log"])
        kept = os.path.join(closed["retired"], "build", "important.log")
        self.assertEqual(read(kept), "keep me\n")
        self.assertNotIn("build/important.log", self.tracked(repo))
        self.assertIn("note.txt", self.tracked(repo))

    def test_an_ignored_mimocode_folder_goes_with_the_worktree_like_other_byproducts(self):
        self.assertTrue(T._regenerable(".mimocode/session.json"))

    @NOT_ROOT
    def test_the_old_location_retires_beside_the_old_worktree(self):
        repo = self.ignoring_repo()
        with unittest_patch(self.store, "_worktree_path", lambda tid, path: os.path.join(path, "worktree")):
            t = self.make("build", repo, "rodir")
        self.wait(t["id"])
        self.store.close(t["id"], "discard")
        path = os.path.join(self.store_dir, t["id"])
        self.assertEqual(len([n for n in os.listdir(path) if n.startswith("worktree.retired-")]), 1)


if __name__ == "__main__":
    unittest.main()
