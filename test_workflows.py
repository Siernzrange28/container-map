#!/usr/bin/env python3
"""test_workflows.py — the workflow engine (workflows.py) end to end with a fake agent, as test_tasks.py does:
validation and the order of the steps (plain errors), tokens, the engine's transaction (launch, deferral, a
failed launch, a lost launch found again by its origin, two tickers at once), the second-opinion rule, the
artifacts and their file modes, the update budget, replay frames, the night run as a workflow, the owner's
templates, the daily launch cap, and the sky's workflow contacts. Real processes, temp dirs only."""
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import testenv  # noqa: E402,F401  (first: clears what steers the tool)
import sky  # noqa: E402
import tasks as T  # noqa: E402
import workflows as W  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "tests", "fixtures")

FAKE = textwrap.dedent('''
    import json, os, sys, time
    mode = sys.argv[1]
    prompt = sys.stdin.read()
    out = os.environ.get("FAKE_OUT", ".")
    open(os.path.join(out, "prompts.txt"), "a").write(prompt + "\\n===\\n")
    if mode == "write":
        open("agent-note.txt", "a").write("hello from the agent\\n")
        print("I wrote agent-note.txt")
    elif mode in ("env", "envslow"):
        keys = {k: v for k, v in os.environ.items() if k.startswith("CM_WORKFLOW")}
        json.dump(keys, open(os.path.join(out, "env-" + keys.get("CM_WORKFLOW_STEP", "x") + ".json"), "w"))
        if mode == "envslow":
            time.sleep(float(os.environ.get("FAKE_SLEEP", "3")))
        print("env kept")
    elif mode == "fail":
        print("boom")
        sys.exit(3)
    elif mode == "slow":
        time.sleep(float(os.environ.get("FAKE_SLEEP", "6")))
        print("took my time")
    elif mode == "judge":
        print("looked at the diff")
        print("VERDICT: PASS - the work holds")
    elif mode == "judgefail":
        print("looked at the diff")
        print("VERDICT: FAIL - it does not")
''')


# test_today.DIGEST_NIGHT and test_today.NOW, kept here too: test_today.py does not ship and this file does
# (public-export.sh); test_the_night_fixture_is_test_todays keeps the two equal where both exist
NIGHT_NOW = datetime(2026, 10, 11, 9, 0)
NIGHT_DIGEST = """- 2026-10-09 04:10 night run 10-09: 1 ran, 1 ready for your keep/drop, 0 FAILED
- ready: an older one (1 file changed)
- 2026-10-10 04:12 night run 10-10: 3 ran, 2 ready for your keep/drop, 1 FAILED, 1 refused (no check)
- ready: fix the adder: it returns the wrong sum (1 file changed, 1 insertion(+), 1 deletion(-))
- ready: write answer.txt (no repo: answer in the thread)
- FAILED: rename the module (verify failed twice)
- refused: a task with no check
Say "keep" or "drop" in any session.
- ready: past the line that ended the list, so not an item
- 2026-10-10 05:00 something else entirely
- ready: Night run selftest: no date stamp, so not a report line
- 2026-10-10 06:00 night run is live on the spare box (04:00 daily, cap 4)
"""


class Clock:
    def __init__(self, t=None):
        # a fixed local noon, so "today" and local midnight are what the test says
        self.t = t if t is not None else datetime(2026, 10, 12, 12, 0, 0).timestamp()

    def __call__(self):
        return self.t

    def tick(self, s):
        self.t += s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cm-wf-")
        self.root = os.path.realpath(self.tmp.name)
        self.work = os.path.join(self.root, "work")
        self.side = os.path.join(self.root, "side")
        for d in ("api", "ops", "docs", "site", os.path.join("ops", "logs")):
            os.makedirs(os.path.join(self.work, d), exist_ok=True)
        os.makedirs(self.side)
        self.fake = os.path.join(self.side, "fake_agent.py")
        with open(self.fake, "w") as handle:
            handle.write(FAKE)
        self._env = mock.patch.dict(os.environ, {"FAKE_OUT": self.side})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.chars = [{"kind": "area", "uid": "area:" + n, "name": n, "path": os.path.join(self.work, n),
                       "rooms": ([{"uid": "dir:ops/logs", "kind": "dir", "name": "logs",
                                   "path": os.path.join(self.work, "ops", "logs")}] if n == "ops" else [])}
                      for n in ("api", "ops", "docs", "site")]
        self.clock = Clock()
        self.wf_dir = os.path.join(self.root, "wf")
        self.tasks = None
        self.store = self.make_store()

    def tearDown(self):
        if self.tasks is not None:
            for t in self.tasks.list():
                if t.get("status") in ("running", "checking"):
                    with contextlib.suppress(T.TaskError):
                        self.tasks.stop(t["id"])
        time.sleep(0.1)
        self.tmp.cleanup()

    def runners(self, **extra):
        py = sys.executable
        base = {"claude": {"cmd": [py, self.fake, "write"], "max_minutes": 1},
                "codex": {"cmd": [py, self.fake, "write"], "check_cmd": [py, self.fake, "judge"], "max_minutes": 1},
                "writer": {"cmd": [py, self.fake, "write"], "max_minutes": 1},
                "failer": {"cmd": [py, self.fake, "fail"], "max_minutes": 1},
                "envy": {"cmd": [py, self.fake, "env"], "max_minutes": 1},
                "envslow": {"cmd": [py, self.fake, "envslow"], "max_minutes": 1},
                "slow": {"cmd": [py, self.fake, "slow"], "max_minutes": 1},
                "judge": {"cmd": [py, self.fake, "judge"], "role": "check", "max_minutes": 1},
                "harsh": {"cmd": [py, self.fake, "judgefail"], "role": "check", "max_minutes": 1}}
        base.update(extra)
        return T.valid_runners(base)

    def make_store(self, runners=None, **kw):
        self.tasks = T.TaskStore(os.path.join(self.root, "tasks"), runners or self.runners(), [self.work])
        self.tasks.last_create = -100.0
        return W.WorkflowStore(self.wf_dir, task_store=self.tasks, roots=[self.work], now=self.clock,
                               chars=self.chars, **kw)

    def fix_spec(self, **changes):
        with open(os.path.join(FIXTURES, "workflow-spec.json"), encoding="utf-8") as handle:
            spec = json.loads(handle.read().replace("{root}", self.work))
        spec.update(changes)
        return spec

    def note_spec(self, n=3, **changes):
        steps = [{"key": f"s{i}", "label": f"Step {i}", "at": os.path.join(self.work, ("api", "ops", "docs")[i % 3]),
                  "kind": "note", "after": [f"s{i - 1}"] if i else []} for i in range(n)]
        spec = {"title": "Fix the phone labels", "goal": "every name fits", "by": {"name": "Claude Code"}, "steps": steps}
        spec.update(changes)
        return spec

    def task_spec(self, runner="writer", n=1, parallel=True, **task):
        steps = []
        for i in range(n):
            t = {"text": f"write the note {i}", "runner": runner}
            t.update(task)
            steps.append({"key": f"t{i}", "label": f"Task {i}", "at": os.path.join(self.work, ("ops", "api", "site")[i % 3]),
                          "kind": "task", "after": [] if parallel or not i else [f"t{i - 1}"], "task": t})
        return {"title": "Run the tasks", "by": {"name": "an agent"}, "steps": steps}

    def create(self, spec, **kw):
        self.store.last_create = -1e9
        return self.store.create(spec, **kw)

    def state(self, wid):
        s = self.store.summary(wid)
        return s["state"], {st["key"]: st["state"] for st in s["steps"]}

    def until(self, wid, pred, timeout=20.0, tick=True):
        end = time.time() + timeout
        s = None
        while time.time() < end:
            if tick:
                self.tasks.last_create = -100.0
                self.store.tick()
            s = self.store.summary(wid)
            if pred(s):
                return s
            time.sleep(0.1)
        self.fail(f"workflow {wid} never got there: {json.dumps(s)[:600]}")

    def events(self, wid, ev=None):
        rec = self.store._read(self.store._dir(wid))
        return [e for e in rec.events if ev is None or e["ev"] == ev]


class Validation(Base):
    def test_dag_errors(self):
        """Dependency errors: a wait on a missing step first, then a cycle, both said plainly."""
        with self.assertRaises(W.WorkflowError) as ctx:
            W.validate({"title": "x", "steps": [{"key": "a", "label": "A", "after": ["b"]},
                                                {"key": "b", "label": "B", "after": ["a"]}]})
        self.assertEqual((ctx.exception.status, ctx.exception.message), (400, "steps 'a' and 'b' wait on each other"))
        with self.assertRaises(W.WorkflowError) as ctx:
            W.validate({"title": "x", "steps": [{"key": "a", "label": "A", "after": ["b"]},
                                                {"key": "b", "label": "B", "after": ["a"]},
                                                {"key": "c", "label": "C", "after": ["z"]}]})
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (400, "step 'c' waits on 'z', which does not exist"))      # missing is said before the cycle
        with self.assertRaises(W.WorkflowError) as ctx:
            W.validate({"title": "x", "steps": [{"key": "a", "label": "A", "after": ["c"]}, {"key": "b", "label": "B", "after": ["a"]},
                                                {"key": "c", "label": "C", "after": ["b"]}, {"key": "d", "label": "D"}]})
        self.assertEqual(ctx.exception.message, "steps 'a', 'b' and 'c' wait on each other")
        with self.assertRaises(W.WorkflowError) as ctx:
            W.validate({"title": "x", "steps": [{"key": "a", "label": "A", "after": ["a"]}]})
        self.assertEqual(ctx.exception.message, "step 'a' waits on itself")
        clean, _ = W.validate({"title": "x", "steps": [{"key": "c", "label": "C", "after": ["a", "b"]},
                                                       {"key": "b", "label": "B", "after": ["a"]}, {"key": "a", "label": "A"}]})
        self.assertEqual(clean["order"], ["a", "b", "c"])
        made = self.create(self.note_spec())                  # the order is stored for replay
        with open(os.path.join(self.wf_dir, made["id"], "workflow.json")) as handle:
            self.assertEqual(json.load(handle)["spec"]["order"], ["s0", "s1", "s2"])

    def test_bounds_and_text(self):
        steps = [{"key": f"s{i}", "label": f"S{i}"} for i in range(25)]
        with self.assertRaises(W.WorkflowError) as ctx:
            self.create({"title": "too long", "steps": steps})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("24", ctx.exception.message)
        self.assertIn("25", ctx.exception.message)
        for bad in ({"title": "a‮b", "steps": steps[:1]}, {"title": "x" * 61, "steps": steps[:1]},
                    {"title": "x", "steps": [{"key": "A!", "label": "a"}]}, {"title": "x", "steps": []},
                    {"title": "x", "steps": steps[:1], "extra": 1},
                    {"title": "x", "steps": [{"key": "a", "label": "a", "at": "/etc"}]},
                    {"title": "x", "steps": [{"key": "a", "label": "a", "kind": "task", "at": "place:docs site",
                                              "task": {"text": "x", "runner": "writer"}}]}):
            with self.assertRaises(W.WorkflowError, msg=json.dumps(bad)[:80]):
                self.create(bad)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.create(self.note_spec(), depth=3)
        self.assertIn("2 deep", ctx.exception.message)

    def test_same_family_check_is_refused(self):
        runners = self.runners(**{"sonnet-lane": {"cmd": [sys.executable, self.fake, "judge"], "role": "check",
                                                  "models": [{"name": "sonnet"}], "max_minutes": 1}})
        self.store = self.make_store(runners)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.create(self.task_spec("claude", check_runner="sonnet-lane"))
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (400, "a second opinion must come from a different model family"))
        self.assertTrue(T.same_family("claude", "sonnet-lane", runners))
        self.assertFalse(T.same_family("claude", "codex", runners))
        self.assertEqual(T.family_of("gemini"), "google")
        self.create(self.task_spec("claude", check_runner="codex"))       # another family: fine

    def test_shell_lines_wait_for_the_owner(self):
        made = self.create(self.task_spec("writer", verify="true", retries=1))
        self.assertEqual(made["start"], "plan")
        self.assertIn("run shell lines as you", made["notes"][0])
        self.assertEqual(self.state(made["id"])[0], "planned")
        self.assertEqual(self.store.shell_lines(made["id"])[0]["verify"], "true")
        self.assertEqual(self.store.tick(), [])
        self.store.agent_verify = True
        self.assertEqual(self.create(self.task_spec("writer", verify="true"))["start"], "now")


class Tokens(Base):
    def test_wrong_token_is_403_and_task_states_are_the_engines(self):
        made = self.create(self.note_spec())
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.step(made["id"], "s0", "not-the-token", state="working")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.step(made["id"], "s0", None, state="working")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.pulse(made["id"], "nope")
        self.assertEqual(ctx.exception.status, 403)
        out = self.store.step(made["id"], "s0", made["token"], state="working", says="reading")
        self.assertEqual(out["workflow"]["steps"][0]["state"], "working")
        with open(os.path.join(self.wf_dir, made["id"], "workflow.json")) as handle:
            text = handle.read()
        self.assertNotIn(made["token"], text)                               # only its sha256 is kept
        self.assertIn(hashlib.sha256(made["token"].encode()).hexdigest(), text)

    def test_a_step_token_reaches_its_agent_and_only_says(self):
        made = self.create(self.task_spec("envy"))
        self.store.tick()
        s = self.until(made["id"], lambda s: s["steps"][0]["state"] in ("done", "needs_you"))
        with open(os.path.join(self.side, "env-t0.json")) as handle:
            env = json.load(handle)
        self.assertEqual((env["CM_WORKFLOW"], env["CM_WORKFLOW_STEP"], env["CM_WORKFLOW_DEPTH"]), (made["id"], "t0", "1"))
        token = env["CM_WORKFLOW_TOKEN"]
        self.clock.tick(30)
        self.store.step(made["id"], "t0", token, says="kept the env", progress="3/9")
        self.assertEqual(self.store.summary(made["id"])["steps"][0]["says"], "kept the env")
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.step(made["id"], "t0", token, state="done")
        self.assertEqual(ctx.exception.status, 403)
        with self.assertRaises(W.WorkflowError):
            self.store.done(made["id"], token)                              # a step token cannot end it
        self.assertNotIn(token, json.dumps(self.store.events(made["id"])))
        self.assertTrue(self.store.pulse(made["id"], token)["ok"])
        self.assertEqual(s["steps"][0]["tid"], self.tasks.find_by_origin({"workflow": made["id"], "step": "t0", "attempt": 1})[0])


class Engine(Base):
    def setUp(self):
        super().setUp()
        gap = mock.patch.object(T, "CREATE_MIN_GAP", 0.0)
        gap.start()
        self.addCleanup(gap.stop)

    def test_tick_launches_exactly_one_task_for_the_ready_step(self):
        made = self.create(self.task_spec("writer", n=2, parallel=False))
        did = self.store.tick()
        self.assertEqual([(k, w) for _, k, w in did], [("t0", "launched")])
        self.assertEqual(len(self.tasks.list()), 1)
        self.store.tick()
        self.store.tick()
        self.assertEqual(len([e for e in self.events(made["id"], "launch") if e["key"] == "t0"]), 1)
        row = self.tasks.list()[0]
        self.assertEqual((row["title"], row["origin"]), ("Task 0", {"workflow": made["id"], "step": "t0", "attempt": 1}))
        s = self.until(made["id"], lambda s: s["state"] == "done")
        self.assertEqual(len(self.tasks.list()), 2)
        self.assertEqual([st["state"] for st in s["steps"]], ["done", "done"])

    def test_429_defers_and_the_next_tick_is_attempt_two(self):
        made = self.create(self.task_spec("writer"))
        real = self.tasks.create
        calls = []

        def busy(*a, **kw):
            calls.append(kw.get("origin"))
            if len(calls) == 1:
                raise T.TaskError(429, "4 tasks are already running; wait for one to finish")
            return real(*a, **kw)
        with mock.patch.object(self.tasks, "create", side_effect=busy):
            self.store.tick()
            st = self.store.summary(made["id"])["steps"][0]
            self.assertEqual((st["state"], st["attempt"]), ("ready", 1))
            self.assertIn("waiting for a free agent slot", st["why"])
            self.assertEqual(self.events(made["id"], "deferred")[0]["attempt"], 1)
            self.clock.tick(1)                                  # the bridge's next tick (the 1 s nudge loop)
            self.store.tick()
        self.assertEqual(calls[1]["attempt"], 2)
        task = self.events(made["id"], "task")[0]
        self.assertEqual((task["attempt"], len(self.tasks.list())), (2, 1))
        self.assertEqual(self.store.summary(made["id"])["steps"][0]["attempt"], 2)

    def test_429_twice_in_a_row_backs_off(self):
        """Every deferral is an event and touches .nudge: from the second in a row on, the engine waits 10 s,
        20 s, ... 300 s, so the bridge's 1 s nudge loop does not ask for a slot every second."""
        made = self.create(self.task_spec("writer"))
        real = self.tasks.create
        calls = []

        def busy(*a, **kw):
            calls.append(kw.get("origin"))
            if len(calls) <= 2:
                raise T.TaskError(429, "4 tasks are already running; wait for one to finish")
            return real(*a, **kw)
        with mock.patch.object(self.tasks, "create", side_effect=busy):
            self.store.tick()
            self.clock.tick(1)
            self.store.tick()                                   # attempt 2: busy again
            self.assertEqual([e["attempt"] for e in self.events(made["id"], "deferred")], [1, 2])
            for _ in range(int(W.DEFER_S) - 1):                 # nine more 1 s ticks: backing off, no call
                self.clock.tick(1)
                self.store.tick()
            self.assertEqual(len(calls), 2)
            self.clock.tick(1.5)
            self.store.tick()
        self.assertEqual(calls[2]["attempt"], 3)
        self.assertEqual(self.events(made["id"], "task")[0]["attempt"], 3)

    def test_a_refused_launch_fails_the_step_and_blocks_what_waits(self):
        made = self.create(self.task_spec("writer", n=3, parallel=False))
        with mock.patch.object(self.tasks, "create", side_effect=T.TaskError(400, "pick one of the models writer offers")):
            self.store.tick()
        s = self.store.summary(made["id"])
        self.assertEqual([st["state"] for st in s["steps"]], ["failed", "blocked", "blocked"])
        self.assertEqual(s["steps"][0]["why"], "pick one of the models writer offers")
        self.assertEqual(s["steps"][2]["why"], "won't start: 'Task 0' failed")
        self.assertEqual(s["state"], "failed")
        lf = self.events(made["id"], "launch_failed")[0]
        self.assertEqual((lf["code"], lf["attempt"]), (400, 1))
        self.store.retry(made["id"], "t0")                       # the owner's retry: a new attempt, launched
        self.store.tick()
        self.assertEqual(self.events(made["id"], "task")[0]["attempt"], 2)
        self.assertFalse(self.events(made["id"], "launch")[-1]["counted"])

    def test_a_launch_lost_between_launch_and_task_is_found_by_its_origin(self):
        made = self.create(self.task_spec("writer"))
        path = self.store._dir(made["id"])
        with self.store._lock(path):                  # the map died right after TaskStore.create
            rec = self.store._read(path)
            self.store._append(rec, {"ev": "launch", "key": "t0", "attempt": 1, "token_sha256": "0" * 64,
                                     "counted": True, "by": "map"})
        row = self.tasks.create("write the note 0", os.path.join(self.work, "ops"), "writer", title="Task 0",
                                origin={"workflow": made["id"], "step": "t0", "attempt": 1})
        self.assertEqual(self.store.summary(made["id"])["steps"][0]["state"], "working")
        did = self.store.tick()
        self.assertIn((made["id"], "t0", "rebound"), did)
        self.assertEqual(self.events(made["id"], "task")[0]["tid"], row["id"])
        self.store.tick()
        self.assertEqual(len(self.tasks.list()), 1)                          # no second task
        # none found and older than 120 s: deferred (launch lost), then a fresh attempt
        made2 = self.create(self.task_spec("writer"))
        path2 = self.store._dir(made2["id"])
        with self.store._lock(path2):
            rec = self.store._read(path2)
            self.store._append(rec, {"ev": "launch", "key": "t0", "attempt": 1, "token_sha256": "0" * 64,
                                     "counted": True, "by": "map"})
        self.store.tick()
        self.assertEqual(self.events(made2["id"], "deferred"), [])
        self.clock.tick(W.LOST_LAUNCH_S + 1)
        self.store.tick()
        self.assertEqual(self.events(made2["id"], "deferred")[0]["why"], W.LOST_WHY)

    def test_two_tickers_at_once_make_one_task(self):
        made = self.create(self.task_spec("writer"))
        other_tasks = T.TaskStore(os.path.join(self.root, "tasks"), self.runners(), [self.work])
        other = W.WorkflowStore(self.wf_dir, task_store=other_tasks, roots=[self.work], now=self.clock,
                                chars=self.chars)
        gate = threading.Barrier(2)
        errors = []

        def run(store):
            try:
                gate.wait()
                store.tick()
            except Exception as exc:
                errors.append(exc)
        threads = [threading.Thread(target=run, args=(s,)) for s in (self.store, other)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.tasks.list()), 1)
        self.assertEqual(len(self.events(made["id"], "launch")), 1)

    def test_wait_clear_with_a_checker_needs_a_pass(self):
        """done = review AND verify passed AND (a checker named: a fresh PASS) AND no merge blockers."""
        made = self.create(self.task_spec("writer", check_runner="judge", wait="clear"))
        self.store.tick()
        tid = self.events(made["id"], "task")[0]["tid"]
        row = self.wait_task(tid)
        self.assertEqual((row["status"], row["merge_blockers"]), ("review", []))
        st = self.store.summary(made["id"])["steps"][0]
        self.assertEqual((st["state"], st["why"]), ("needs_you", "waiting for a second opinion that passes it"))
        s = self.until(made["id"], lambda s: s["steps"][0]["state"] == "done")
        self.assertEqual(s["steps"][0]["check"]["verdict"], "PASS")
        self.assertIsNone(s["steps"][0]["branch"])                           # in place: no branch of its own
        self.assertEqual(len(self.events(made["id"], "check")), 1)
        # a FAIL keeps it waiting on the person
        made2 = self.create(self.task_spec("writer", check_runner="harsh", wait="clear"))
        s2 = self.until(made2["id"], lambda s: (s["steps"][0]["check"] or {}).get("verdict") == "FAIL")
        self.assertEqual((s2["steps"][0]["state"], s2["steps"][0]["why"]), ("needs_you", "the second opinion said FAIL"))
        self.assertEqual(len(self.events(made2["id"], "check")), 1)

    def test_merged_wait_and_a_failed_agent(self):
        made = self.create(self.task_spec("writer", wait="merged"))
        s = self.until(made["id"], lambda s: s["steps"][0]["state"] == "needs_you")
        self.assertEqual(s["steps"][0]["why"], "accept changes to continue")
        self.tasks.close(s["steps"][0]["tid"], "done")
        self.assertEqual(self.store.summary(made["id"])["state"], "done")
        made2 = self.create(self.task_spec("failer"))
        s2 = self.until(made2["id"], lambda s: s["state"] == "failed")
        self.assertEqual(s2["steps"][0]["why"], "the agent failed (exit 3)")

    def wait_task(self, tid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            row = self.tasks.summary(os.path.join(self.tasks.tasks_dir, tid))
            if row["status"] not in ("new", "running", "checking", "stopping"):
                return row
            time.sleep(0.1)
        self.fail(f"task {tid} still running")


class Budget(Base):
    def test_ten_thousand_progress_updates_stay_small_and_done_gets_through(self):
        made = self.create(self.note_spec(1))
        wid, token = made["id"], made["token"]
        self.store.step(wid, "s0", token, state="working")
        for i in range(10000):
            self.clock.tick(1)
            self.store.step(wid, "s0", token, state="working", progress=f"{i}/10000")
        lines = 0
        for seg in self.store._segments(self.store._dir(wid)):
            with open(seg, "rb") as handle:
                lines += handle.read().count(b"\n")
        self.assertLess(lines, 1500)
        self.assertEqual(self.store.summary(wid)["steps"][0]["progress"], {"done": 9999, "total": 10000})
        self.store.events_cap = self.store._events_bytes(self.store._dir(wid))      # the budget is used up
        self.clock.tick(60)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.step(wid, "s0", token, says="more words")
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (413, "this workflow used its update budget; start a new one"))
        self.store.step(wid, "s0", token, state="done", says="finished")      # a terminal update still lands
        self.assertEqual(self.state(wid), ("done", {"s0": "done"}))
        self.store.done(wid, token)

    def lines(self, wid):
        n = 0
        for seg in self.store._segments(self.store._dir(wid)):
            with open(seg, "rb") as handle:
                n += handle.read().count(b"\n")
        return n

    def test_saying_done_again_is_an_ordinary_update(self):
        """Only the move that finishes an open step is exempt. "done" again and again, with new
        progress each time, is coalesced, rate-limited and capped like any same-state update."""
        made = self.create(self.note_spec(1))
        wid, token = made["id"], made["token"]
        self.store.step(wid, "s0", token, state="done")
        for i in range(10000):
            self.clock.tick(1)
            self.store.step(wid, "s0", token, state="done", progress=f"{i}/10000")
        self.assertLess(self.lines(wid), 1500)
        self.assertEqual(self.store.summary(wid)["steps"][0]["progress"], {"done": 9999, "total": 10000})
        self.store.events_cap = self.store._events_bytes(self.store._dir(wid))      # the budget is used up
        before = self.lines(wid)
        replies = []
        for _ in range(100):
            self.clock.tick(11)                           # past the 10 s window: each one would be appended
            try:
                replies.append(self.store.step(wid, "s0", token, state="done", says="still here").get("coalesced"))
            except W.WorkflowError as exc:
                replies.append(exc.status)
        self.assertEqual(self.lines(wid), before)                          # nothing past the budget
        self.assertEqual(set(replies), {413})
        for state in ("failed", "skipped"):              # nor a move from one finished state to another
            with self.assertRaises(W.WorkflowError) as ctx:
                self.store.step(wid, "s0", token, state=state)
            self.assertEqual(ctx.exception.status, 413)
        self.assertEqual(self.state(wid), ("done", {"s0": "done"}))

    def test_reopening_a_step_over_and_over_stops_at_the_budget(self):
        made = self.create(self.note_spec(2))
        wid, token = made["id"], made["token"]
        for _ in range(50):
            self.clock.tick(1)                            # one reopen a second: under the rate
            self.store.step(wid, "s0", token, state="working")
            self.store.step(wid, "s0", token, state="done")
        self.store.events_cap = self.store._events_bytes(self.store._dir(wid))
        before = self.lines(wid)
        for _ in range(200):
            self.clock.tick(11)
            for state in ("working", "done", "failed"):
                with contextlib.suppress(W.WorkflowError):
                    self.store.step(wid, "s0", token, state=state)
        self.assertEqual(self.lines(wid), before)                          # done stays done, nothing grows
        self.store.step(wid, "s1", token, state="done", says="the last one")      # an open step still finishes
        self.assertEqual(self.state(wid), ("done", {"s0": "done", "s1": "done"}))
        self.assertGreater(self.lines(wid), before)

    def test_reopening_a_finished_step_meets_the_rate(self):
        """working, done, working, done ... 2,000 times in one second (the check's spam_done.py wrote 12,002
        lines): a finished step's change of state meets the rate (4 at once, 1 a second) and is refused with 429
        past it; the move that finishes a step and every forward move are never held back."""
        made = self.create(self.note_spec(2, steps=[
            {"key": "s0", "label": "A", "at": os.path.join(self.work, "api")},
            {"key": "s1", "label": "B", "at": os.path.join(self.work, "ops")}]))
        wid, token = made["id"], made["token"]
        self.store.step(wid, "s0", token, state="working")
        self.store.step(wid, "s0", token, state="done")
        before = self.lines(wid)
        refused = []
        for _ in range(2000):
            for state in ("working", "done"):
                try:
                    self.store.step(wid, "s0", token, state=state)
                except W.WorkflowError as exc:
                    refused.append((state, exc.status, exc.message))
        self.assertEqual(len(refused), 2000 - W.BOUNDS["updates_burst"])        # 4 reopens, then none
        self.assertEqual(set(refused), {("working", 429, "step 's0' is finished; a finished step changes state "
                                                         "once a second at most, so send this again in a second")})
        self.assertLess(self.lines(wid) - before, 40)
        self.assertEqual(self.state(wid)[1]["s0"], "done")
        for state in ("failed", "skipped"):              # done to another finished state is held back the same way
            with self.assertRaises(W.WorkflowError) as ctx:
                self.store.step(wid, "s0", token, state=state)
            self.assertEqual(ctx.exception.status, 429)
        self.store.step(wid, "s1", token, state="working", says="on it")      # forward moves never meet the rate
        self.store.step(wid, "s1", token, state="done")
        self.clock.tick(1)                                                      # a second later: one more
        self.store.step(wid, "s0", token, state="working", says="one more look")
        self.assertEqual(self.state(wid), ("working", {"s0": "working", "s1": "done"}))

    def test_outputs_meet_the_rate(self):
        """An output is never coalesced (the file it names would be lost), so a same-state update that carries
        one meets the per-workflow rate: 20 at one instant append 4, the rest get 429 and write nothing; 10,000
        output texts over ten seconds stay a handful of lines. The move that finishes the step, output and all,
        still lands past the budget."""
        made = self.create(self.note_spec(1))
        wid, token = made["id"], made["token"]
        self.store.step(wid, "s0", token, state="working")
        files = []
        for i in range(20):
            p = os.path.join(self.work, "api", f"o{i}.txt")
            with open(p, "w") as handle:
                handle.write(str(i))
            files.append(p)
        before = self.lines(wid)
        refused = []
        for p in files:
            try:
                self.assertNotIn("coalesced", self.store.step(wid, "s0", token, output=p))
            except W.WorkflowError as exc:
                refused.append((exc.status, exc.message))
        self.assertEqual(len(self.events(wid, "output")), W.BOUNDS["updates_burst"])
        self.assertEqual(refused, [(429, "step 's0' takes 4 outputs at once and one a second after that, "
                                         "so send this again in a second")] * (20 - W.BOUNDS["updates_burst"]))
        self.assertEqual(self.lines(wid) - before, 2 * W.BOUNDS["updates_burst"])    # its step line + its output
        for i in range(10000):
            self.clock.tick(0.001)
            with contextlib.suppress(W.WorkflowError):
                self.store.step(wid, "s0", token, output=f"result {i}", says="still going")
        self.assertLess(self.lines(wid), 1500)
        self.assertLess(len(self.events(wid, "output")), 40)
        self.store.events_cap = self.store._events_bytes(self.store._dir(wid))      # the budget is used up
        self.store.step(wid, "s0", token, state="done", output=files[-1])          # the finishing move lands
        self.assertEqual(self.state(wid), ("done", {"s0": "done"}))
        self.assertIn("o19.txt", [e.get("name") for e in self.events(wid, "artifact")])

    def test_pulses_over_the_rate_are_coalesced(self):
        made = self.create(self.note_spec(1))
        replies = [self.store.pulse(made["id"], made["token"]) for _ in range(8)]
        self.assertTrue(all(r["ok"] for r in replies))
        self.assertEqual(sum(1 for r in replies if r.get("coalesced")), 8 - W.BOUNDS["updates_burst"])
        self.assertFalse(any(e["ev"] == "pulse" for e in self.events(made["id"])))       # latest.json only


class Artifacts(Base):
    def test_a_snapshot_has_the_files_sha_and_secret_names_are_refused(self):
        made = self.create(self.note_spec(2))
        report = os.path.join(self.work, "api", "report.md")
        with open(report, "w") as handle:
            handle.write("6 cut labels\n" * 40)
        self.store.step(made["id"], "s0", made["token"], state="done", output=report)
        art = self.events(made["id"], "artifact")[0]
        with open(report, "rb") as handle:
            want = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual((art["sha256"], art["name"], art["size"]), (want, "report.md", os.path.getsize(report)))
        kept = os.path.join(self.wf_dir, made["id"], "artifacts", "s0", "report.md")
        with open(kept, "rb") as handle:
            self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), want)
        s = self.store.summary(made["id"])
        self.assertEqual((s["steps"][0]["artifact"], s["steps"][0]["output"]), ("report.md", "report.md"))
        for name in (".env", "deploy.pem", "id_ed25519", "my-secret.txt"):
            bad = os.path.join(self.work, "api", name)
            with open(bad, "w") as handle:
                handle.write("x")
            with self.assertRaises(W.WorkflowError) as ctx:
                self.store.step(made["id"], "s1", made["token"], output=bad)
            self.assertIn("refused", ctx.exception.message)
        with self.assertRaises(W.WorkflowError):
            self.store.step(made["id"], "s1", made["token"], output="/etc/hostname")
        detail = self.store.detail(made["id"])
        self.assertEqual(detail["outputs"]["s0"], report)
        self.assertNotIn(self.work, json.dumps(s))                            # summaries carry names, no paths

    def test_what_the_file_route_refuses_is_never_kept(self):
        """a path the bridge's /file route refuses is refused as an output too, never copied: the
        run folder where a bridge keeps its credentials, a live file by its name anywhere under a root, and the
        rest of the deny lists (which must not drift from bridge.py's)."""
        import bridge
        self.assertEqual(W.FILE_DENY_PARTS, bridge.FILE_DENY_PARTS)
        self.assertLessEqual(set(bridge.FILE_DENY_PREFIX), set(W.FILE_DENY_PREFIX))
        self.assertLessEqual(set(bridge.FILE_DENY_END) | set(bridge.FILE_DENY_SUFFIX), set(W.FILE_DENY_END))
        self.assertEqual(W.LIVE_FILE_NAME_RE.pattern, bridge.LIVE_FILE_NAME_RE.pattern)
        route = bridge.BridgeApp.__new__(bridge.BridgeApp)
        route.file_roots = (self.work,)
        made = self.create(self.note_spec(1))
        wid, token = made["id"], made["token"]
        for rel in (os.path.join(".container-map", "run", "bridge-7428.json"),
                    os.path.join(".container-map", "chat-scan", "findings", "t3-a:b.json"),
                    os.path.join(".container-map", "t3-remote", "server-2.json"),
                    os.path.join(".container-map", "run", "plain.txt"),
                    os.path.join("api", ".container-map", "RUN", "notes.txt"),
                    os.path.join("api", "bridge-7428.json"), os.path.join("api", "Bridge-7428.json.new.x"),
                    os.path.join("api", "x.p12"), os.path.join("api", "x.kdbx"), os.path.join("api", "x.enc.env"),
                    os.path.join("api", "remote-sessions.json"), os.path.join("api", "credentials", "a.txt")):
            p = os.path.join(self.work, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as handle:
                handle.write('{"token": "SYNTHETIC"}')
            with self.assertRaises(bridge.BridgeError, msg=rel):                # the route refuses it ...
                route._checked_file(p)
            with self.assertRaises(W.WorkflowError, msg=rel) as ctx:            # ... and so does a step
                self.store.step(wid, "s0", token, state="working", output=p)
            self.assertEqual(ctx.exception.status, 400)
            self.assertIn("refused", ctx.exception.message)
        self.assertEqual(self.store._refuse_real(os.path.join(self.work, ".container-map", "run", "plain.txt")),
                         "it is inside the map's run folder (where a bridge keeps its credentials)")
        ok = os.path.join(self.work, "api", "bridge-notes.json")              # a name like it is fine
        with open(ok, "w") as handle:
            handle.write("{}")
        route._checked_file(ok)
        self.store.step(wid, "s0", token, state="done", output=ok)
        self.assertEqual(self.events(wid, "output")[-1]["path"], ok)
        self.assertEqual(os.listdir(os.path.join(self.wf_dir, wid, "artifacts", "s0")), ["bridge-notes.json"])

    def test_file_modes_after_a_fix_run(self):
        """K3: every folder under a fresh workflows_dir is 0700 and every file 0600 after a FIX run."""
        with mock.patch.object(T, "CREATE_MIN_GAP", 0.0):
            spec = self.fix_spec()
            spec["steps"][1]["task"].update(runner="writer", verify=None, retries=0, check_runner="judge")
            made = self.create(spec)
            token = made["token"]
            cut = os.path.join(self.work, "api", "cut-labels.txt")
            with open(cut, "w") as handle:
                handle.write("ops\nsite\n")
            self.store.step(made["id"], "find", token, state="done", says="6 labels are cut", output=cut)
            self.until(made["id"], lambda s: s["steps"][2]["state"] == "needs_you")
            with open(os.path.join(self.side, "prompts.txt")) as handle:
                prompt = handle.read()
            self.assertIn("cut-labels.txt from step 'find'", prompt)            # the input, read only, by its snapshot
            self.store.gate(made["id"], "approve", True)
            self.store.step(made["id"], "docs", token, state="done")
            self.assertEqual(self.store.summary(made["id"])["state"], "done")
        for top, dirs, files in os.walk(self.wf_dir):
            for d in dirs:
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(top, d)).st_mode), 0o700, os.path.join(top, d))
            for f in files:
                self.assertEqual(stat.S_IMODE(os.stat(os.path.join(top, f)).st_mode), 0o600, os.path.join(top, f))
        self.assertEqual(stat.S_IMODE(os.stat(self.wf_dir).st_mode), 0o700)

    def test_a_parent_swapped_for_a_link_between_check_and_open_is_refused(self):
        """K3: O_NOFOLLOW guards the last part only; /proc/self/fd/N tells where the open really went."""
        if not os.path.isfile("/etc/passwd") or not os.path.isdir("/proc/self/fd"):
            self.skipTest("needs /etc/passwd and /proc")
        made = self.create(self.note_spec(1))
        sub = os.path.join(self.work, "api", "sub")
        os.makedirs(sub)
        src = os.path.join(sub, "passwd")
        with open(src, "w") as handle:
            handle.write("not the system one\n")
        self.store.step(made["id"], "s0", made["token"], state="working", output=src)
        real_open = os.open
        swapped = []

        def hook(path, flags, *a, **kw):
            if path == src and not swapped:
                os.rename(sub, sub + ".moved")
                os.symlink("/etc", sub)
                swapped.append(1)
            return real_open(path, flags, *a, **kw)
        self.clock.tick(30)
        with mock.patch.object(W.os, "open", side_effect=hook):
            self.store.step(made["id"], "s0", made["token"], state="done")
        self.assertEqual(swapped, [1])
        art = self.events(made["id"], "artifact")[0]
        self.assertIn("link that leads outside the map", art["refused"])
        self.assertFalse(os.path.exists(os.path.join(self.wf_dir, made["id"], "artifacts", "s0")))


class Replay(Base):
    def test_folding_the_frames_gives_the_final_summary_with_planets(self):
        """T2 (space-truckers cargoUnit.js:101-117): every derived event carries its planet, place, who and time."""
        spec = self.fix_spec()
        spec["steps"][1] = {"key": "fix", "label": "Fix the label slots", "at": os.path.join(self.work, "ops", "logs"),
                            "kind": "note", "after": ["find"]}
        made = self.create(spec)
        wid, token = made["id"], made["token"]
        for key, says in (("find", "6 labels cut"), ("fix", "moving the slots")):
            self.clock.tick(15)
            self.store.step(wid, key, token, state="working", says=says)
            self.clock.tick(15)
            self.store.step(wid, key, token, state="done")
        self.clock.tick(15)
        self.store.gate(wid, "approve", True)
        self.clock.tick(15)
        self.store.step(wid, "docs", token, state="done", says="wrote it up")
        s = self.store.summary(wid)
        rp = self.store.replay(wid)
        self.assertEqual(W.fold(rp["frames"], s["steps"]), {"state": s["state"], "steps": {st["key"]: st["state"] for st in s["steps"]}})
        self.assertEqual(rp["final"]["state"], "done")
        step_frames = [f for f in rp["frames"] if f["key"]]
        self.assertTrue(step_frames and all(f["planet_uid"] for f in step_frames))
        self.assertEqual({f["planet_uid"] for f in step_frames if f["key"] == "fix"}, {"area:ops"})
        self.assertEqual(s["steps"][1]["moon_uid"], "dir:ops/logs")
        self.assertTrue(all(f["by"] and isinstance(f["t"], float) for f in rp["frames"]))
        self.assertEqual([f["state"] for f in step_frames if f["key"] == "find"], ["ready", "working", "done"])
        self.assertEqual(rp["order"], ["find", "fix", "approve", "docs"])
        self.assertFalse(any(f.get("synthetic") for f in rp["frames"]))
        derived = self.events(wid, "derived")
        self.assertTrue(all({"planet_uid", "place", "by", "t"} <= set(e) for e in derived))

    def test_the_fix_summary_says_a_sentence(self):
        made = self.create(self.fix_spec(title="Fix the phone labels"))
        self.assertEqual(made["start"], "plan")                          # FIX's task step has verify + retries
        self.store.start(made["id"])
        self.store.step(made["id"], "find", made["token"], state="done")
        self.clock.tick(20)
        s = self.store.summary(made["id"])
        self.assertTrue(s["sentence"])
        self.assertEqual(list(s), list(W.SUMMARY_KEYS))
        self.assertEqual(list(s["steps"][0])[:len(W.STEP_KEYS)], list(W.STEP_KEYS))
        self.assertTrue(set(W.STEP_KEYS) <= set(s["steps"][1]) and {"task_status", "wait", "check"} <= set(s["steps"][1]))

    def test_the_sentence_is_the_words_lanes_when_it_says_one(self):
        """T2: `sentence` is exactly words.wf_sentence(summary) whenever that is non-empty; the workflow engine's own line only
        fills in for an empty answer or a failing one (the words are a nicety, never a dead summary)."""
        import words
        made = self.create(self.fix_spec(title="Fix the phone labels"))
        self.store.start(made["id"])
        self.store.step(made["id"], "find", made["token"], state="done")
        self.clock.tick(20)
        s = self.store.summary(made["id"])
        said = words.wf_sentence({**s, "sentence": ""})                 # the real words module, stub or filled
        self.assertEqual(s["sentence"], said if said.strip() else W._fallback_sentence(s))
        seen = []

        def custom(summary):
            seen.append(summary)
            return "Now: custom words."
        with mock.patch.object(words, "wf_sentence", side_effect=custom):
            self.assertEqual(self.store.summary(made["id"])["sentence"], "Now: custom words.")
            self.assertEqual(self.store.list()[0]["sentence"], "Now: custom words.")
        self.assertEqual((seen[0]["id"], seen[0]["title"], [st["key"] for st in seen[0]["steps"]]),
                         (made["id"], "Fix the phone labels", ["find", "fix", "approve", "docs"]))
        for answer in ({"return_value": ""}, {"return_value": "   "}, {"return_value": None},
                       {"side_effect": RuntimeError("boom")}):
            with mock.patch.object(words, "wf_sentence", **answer):
                got = self.store.summary(made["id"])
                self.assertEqual(got["sentence"], W._fallback_sentence(got))
                self.assertTrue(got["sentence"])

    def test_the_night_report_is_one_derived_workflow(self):
        """The repo's night fixture (NIGHT_DIGEST, 10-10: "3 ran, 2 ready, 1 FAILED, 1 refused") read by
        today.py: one workflow, one step per task that ran. The refused item never ran, so it is no step."""
        import today
        digest = os.path.join(self.side, "DIGEST.md")
        with open(digest, "w", encoding="utf-8") as handle:
            handle.write(NIGHT_DIGEST)
        night = today.build({"today": {"night_marker": "night run"}, "digest": digest}, [], [], {},
                            now=NIGHT_NOW)["night"]
        self.assertEqual((night["ran"], len(night["items"])), (3, 4))
        w = W.night_workflow(night, self.chars)
        self.assertTrue(W.ID_RE.match(w["id"]))
        self.assertEqual((w["title"], w["total"], w["done"], w["state"], w["derived"]),
                         ("Night run · 3 tasks", 3, 2, "failed", "night"))
        self.assertEqual([(s["label"], s["state"]) for s in w["steps"]],
                         [("fix the adder: it returns the wrong sum (1 file changed, 1…", "done"),        # cut at 60
                          ("write answer.txt (no repo: answer in the thread)", "done"),
                          ("rename the module (verify failed twice)", "failed")])
        self.assertIn("1 refused", w["goal"])                                # the night's own line still says it
        self.assertTrue(w["sentence"])
        frames = W.summary_frames(w)
        self.assertEqual(W.fold(frames, w["steps"]), {"state": "failed", "steps": {"n1": "done", "n2": "done", "n3": "failed"}})
        out = sky.build({"chars": self.chars, "workflows": [w], "today": {"night": night}}, self.clock())
        self.assertEqual(out["by_source"].get("workflow"), None)             # the night source already has them
        self.assertEqual(out["by_source"].get("night"), 4)                   # the refused one among them

    def test_the_night_fixture_is_test_todays(self):
        """NIGHT_DIGEST is test_today.DIGEST_NIGHT, copied because test_today.py stays home (public-export.sh)
        and this file ships: where both are present they must not drift."""
        try:
            import test_today
        except ImportError:
            self.skipTest("test_today.py is not shipped")
        self.assertEqual((NIGHT_DIGEST, NIGHT_NOW), (test_today.DIGEST_NIGHT, test_today.NOW))

    def test_a_night_from_its_report_file_places_its_steps(self):
        night = {"date": "2026-10-11", "ran": 2, "ready": 1, "failed": 1, "refused": 1, "source": "json",
                 "line": "night run 10-11: 2 ran, 1 ready, 1 failed, 1 refused",
                 "items": [{"id": "a", "title": "tidy the api tests", "repo": os.path.join(self.work, "api"), "verdict": "ready", "minutes": 12},
                           {"id": "b", "title": "bump the docs links", "repo": "docs", "verdict": "failed", "minutes": 3},
                           {"id": "c", "title": "rename the site pages", "repo": None, "verdict": "Refused", "minutes": None}]}
        w = W.night_workflow(night, self.chars)
        self.assertEqual((w["title"], w["total"], w["state"]), ("Night run · 2 tasks", 2, "failed"))
        self.assertEqual([(s["planet_uid"], s["state"], s["says"]) for s in w["steps"]],
                         [("area:api", "done", "ready for your keep or drop · 12 min"),
                          ("area:docs", "failed", "failed in the night run · 3 min")])
        self.assertIsNone(W.night_workflow({"date": "2026-10-11", "items": []}))
        self.assertIsNone(W.night_workflow({"date": "2026-10-11", "items": [{"title": "x", "verdict": "refused"}]}))


class Templates(Base):
    def test_each_template_at_a_planet_validates_as_the_owners_plan(self):
        ops = os.path.join(self.work, "ops")
        for name in W.TEMPLATES:
            spec = self.store.instantiate(name, ops, "Make the tests pass", "claude")
            clean, _ = W.validate(spec, [self.work], self.tasks.runners)
            self.assertEqual((clean["start"], clean["by"]["name"]), ("plan", "you"), name)
            for st in clean["steps"]:
                t = st.get("task")
                if t:
                    self.assertEqual((t["verify"], t["setup"], t["retries"]), (None, None, 0), name)
                    self.assertEqual(st["at"], ops)
        fix = self.store.instantiate("fix-and-check", ops, "Make the tests pass", "claude")
        self.assertEqual(fix["steps"][0]["task"]["check_runner"], "codex")         # another family, can check
        two = self.store.instantiate("two-opinions", ops, "Make the tests pass", "claude")
        self.assertEqual([s["task"]["runner"] for s in two["steps"] if s.get("task")], ["claude", "codex"])
        made = self.store.new_from_template("fix-and-check", ops, "Make the tests pass", "claude")
        self.assertEqual((made["start"], made["workflow"]["state"]), ("plan", "planned"))
        names = [t["name"] for t in self.store.list_templates()]
        self.assertEqual(names, ["fix-and-check", "review-folder", "two-opinions"])

    def test_with_only_one_family_the_check_is_dropped_and_said(self):
        runners = T.valid_runners({"claude": {"cmd": [sys.executable, self.fake, "write"], "max_minutes": 1}})
        self.store = self.make_store(runners)
        ops = os.path.join(self.work, "ops")
        fix = self.store.instantiate("fix-and-check", ops, "Make the tests pass", "claude")
        self.assertNotIn("check_runner", fix["steps"][0]["task"])
        self.assertEqual(fix["steps"][0]["note"], W.NO_CHECKER)
        made = self.create(fix, owner=True)
        self.assertEqual(made["workflow"]["steps"][0]["says"], W.NO_CHECKER)          # the card says so
        two = self.store.instantiate("two-opinions", ops, "Make the tests pass", "claude")
        self.assertEqual([s["key"] for s in two["steps"]], ["one", "pick"])
        self.assertEqual(two["steps"][1]["after"], ["one"])
        with self.assertRaises(W.WorkflowError):
            self.store.instantiate("fix-and-check", ops, "Make the tests pass", "codex")    # not set up

    def test_save_a_finished_workflow_and_run_it_elsewhere(self):
        made = self.create(self.note_spec(3, goal="every name fits on the phone"))
        for k in ("s0", "s1", "s2"):
            self.clock.tick(15)
            self.store.step(made["id"], k, made["token"], state="done")
        self.assertEqual(self.store.summary(made["id"])["state"], "done")
        self.assertEqual(self.store.save_template(made["id"], "label-pass"), {"ok": True, "name": "label-pass"})
        file = os.path.join(self.wf_dir, "templates", "label-pass.json")
        self.assertEqual(stat.S_IMODE(os.stat(file).st_mode), 0o600)
        with open(file, "rb") as handle:
            before = handle.read()
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.save_template(made["id"], "label-pass")
        self.assertEqual(ctx.exception.message, "pick another name; nothing was overwritten")
        with open(file, "rb") as handle:
            self.assertEqual(handle.read(), before)
        site = os.path.join(self.work, "site")
        spec = self.store.instantiate("label-pass", site, "every page title fits", "writer")
        self.assertEqual([(s["key"], s["label"], s["kind"]) for s in spec["steps"]],
                         [(s["key"], s["label"], s["kind"]) for s in self.note_spec(3)["steps"]])
        self.assertEqual((spec["steps"][0]["at"], spec["goal"]), (site, "every page title fits"))
        self.assertEqual(spec["steps"][1]["at"], os.path.join(self.work, "ops"))     # not its at: kept as it was
        W.validate(spec, [self.work], self.tasks.runners)
        self.assertEqual(self.store.list_templates()[-1]["name"], "label-pass")
        self.store.last_create = -1e9
        again = self.store.run_again(made["id"])
        self.assertEqual((again["from"], again["workflow"]["state"], again["workflow"]["by"]["name"]),
                         (made["id"], "planned", "you"))


class DailyCap(Base):
    def setUp(self):
        super().setUp()
        gap = mock.patch.object(T, "CREATE_MIN_GAP", 0.0)
        gap.start()
        self.addCleanup(gap.stop)
        self.store = self.make_store(launches_per_day=2)

    def test_agent_launches_stop_at_the_daily_cap_and_the_owner_is_not_counted(self):
        made = self.create(self.task_spec("writer", n=3))
        self.store.tick()
        s = self.store.summary(made["id"])
        launched = [st for st in s["steps"] if st["tid"]]
        waiting = [st for st in s["steps"] if not st["tid"]]
        self.assertEqual((len(launched), len(waiting)), (2, 1))
        self.assertEqual(waiting[0]["state"], "ready")
        self.assertEqual(waiting[0]["why"], "today's agent launch limit (2) is used up; it starts tomorrow or when you press Retry")
        self.store.tick()
        self.assertEqual(len(self.events(made["id"], "deferred")), 1)             # said once, not every tick
        self.until(made["id"], lambda s: all(st["state"] == "done" for st in s["steps"] if st["tid"]))  # slots free
        made2 = self.create(self.task_spec("writer", n=1))                     # deferred, then the user's Retry
        self.store.tick()
        key2 = self.store.summary(made2["id"])["steps"][0]
        self.assertEqual((key2["state"], key2["tid"]), ("ready", None))
        self.store.retry(made2["id"], "t0")
        self.store.tick()
        self.assertFalse(self.events(made2["id"], "launch")[-1]["counted"])
        self.until(made2["id"], lambda s: s["state"] == "done")
        planned = self.create(dict(self.task_spec("writer", n=3), start="plan"))      # the same, started by the user
        self.store.start(planned["id"])
        self.until(planned["id"], lambda s: all(st["tid"] for st in s["steps"]))     # as slots free up
        self.assertFalse(any(e["counted"] for e in self.events(planned["id"], "launch")))
        midnight = datetime.fromtimestamp(self.clock()).replace(hour=0, minute=0, second=0) + timedelta(days=1)
        self.clock.t = midnight.timestamp() + 60                       # a new day: the count starts again
        s = self.until(made["id"], lambda s: all(st["tid"] for st in s["steps"]))
        self.assertTrue(all(not e.get("cap") for e in self.events(made["id"], "deferred")[1:]))

    def test_a_step_waiting_for_a_slot_spends_none_of_the_days_launches(self):
        """The thread engine refuses a create with 429 before any task exists: no agent ran, so that launch is
        given back (a refund line in the day's ledger). 40 tries with no free slot leave the day's count at 0
        and the step waiting for a slot, not for tomorrow (the workflow's own 48 still counts every attempt)."""
        made = self.create(self.task_spec("writer", n=1))                     # agent-made: its launches count
        with mock.patch.object(self.tasks, "create", side_effect=T.TaskError(429, "4 tasks are already running")):
            for _ in range(40):
                self.clock.tick(400)                                         # past the longest back-off
                self.store.tick()
        launches = self.events(made["id"], "launch")
        self.assertEqual((len(launches), len(self.events(made["id"], "task"))), (40, 0))
        self.assertTrue(all(e["counted"] for e in launches))
        self.assertEqual(self.store._launches_today(self.clock()), 0)
        self.assertEqual(self.events(made["id"], "launch_failed"), [])
        step = self.store.summary(made["id"])["steps"][0]
        self.assertEqual((step["state"], step["why"]), ("ready", "waiting for a free agent slot: 4 tasks are already running"))
        with open(self.store._ledger(self.clock()), encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        self.assertEqual((len(rows), sum(1 for r in rows if r.get("refund"))), (80, 40))
        self.clock.tick(400)
        self.store.tick()                                                    # a slot is free: it launches
        self.assertEqual(len(self.events(made["id"], "task")), 1)
        self.assertEqual(self.store._launches_today(self.clock()), 1)
        late = self.create(self.task_spec("writer", n=2))                      # one launch left today, not zero
        self.store.tick()
        s = self.store.summary(late["id"])
        self.assertEqual(sorted(bool(st["tid"]) for st in s["steps"]), [False, True])
        self.assertEqual(self.store._launches_today(self.clock()), 2)


class Owner(Base):
    def test_gates_retry_skip_stop_and_done(self):
        made = self.create(self.note_spec(3, steps=[
            {"key": "a", "label": "A", "at": os.path.join(self.work, "api")},
            {"key": "g", "label": "Look at it", "kind": "gate", "after": ["a"]},
            {"key": "b", "label": "B", "after": ["g"], "at": "place:docs site"}]))
        wid, token = made["id"], made["token"]
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.gate(wid, "g", True)
        self.assertEqual((ctx.exception.status, ctx.exception.message), (409, "this gate has not been reached yet"))
        self.store.step(wid, "a", token, state="done")
        self.assertEqual(self.state(wid), ("needs_you", {"a": "done", "g": "needs_you", "b": "waiting"}))
        with self.assertRaises(W.WorkflowError):
            self.store.step(wid, "g", token, state="done")                  # only the owner passes a gate
        self.store.gate(wid, "g", False, why="the shots are blurry")
        self.assertEqual(self.state(wid)[1], {"a": "done", "g": "failed", "b": "blocked"})
        self.store.retry(wid, "g")
        self.assertEqual(self.state(wid)[1]["g"], "needs_you")
        self.store.gate(wid, "g", True)
        s = self.store.summary(wid)
        self.assertEqual((s["steps"][2]["place"], s["steps"][2]["where"], s["steps"][2]["planet_uid"]),
                         ("docs site", "place", None))
        self.store.skip(wid, "b")
        self.assertEqual(self.state(wid)[0], "done")
        made2 = self.create(self.note_spec(2))
        self.store.stop(made2["id"], why="enough", owner=True)
        self.assertEqual(self.state(made2["id"])[0], "stopped")
        with self.assertRaises(W.WorkflowError):
            self.store.step(made2["id"], "s0", made2["token"], state="working")
        made3 = self.create(self.note_spec(2))
        self.store.done(made3["id"], made3["token"])
        self.assertEqual(self.state(made3["id"]), ("done", {"s0": "skipped", "s1": "skipped"}))

    def test_retire_moves_closed_workflows_to_an_attic_with_an_undo(self):
        made = self.create(self.note_spec(1))
        self.store.step(made["id"], "s0", made["token"], state="done")
        live = self.create(self.note_spec(1))
        self.assertEqual(self.store.tick(), [])
        self.assertIn(made["id"], self.store._closed)                       # a closed one is not read again
        self.clock.tick(31 * 86400)
        attic = os.path.join(self.root, "attic", "workflows-retire")
        out = self.store.retire(30, attic)
        self.assertEqual(out["moved"], [made["id"]])
        self.assertTrue(os.path.isfile(os.path.join(attic, made["id"], "workflow.json")))
        with open(os.path.join(attic, "MANIFEST.tsv")) as handle:
            self.assertIn(made["id"], handle.read())
        with open(os.path.join(attic, "UNDO.sh")) as handle:
            self.assertIn(f"mv '{os.path.join(attic, made['id'])}'", handle.read())
        self.assertEqual([s["id"] for s in self.store.list()], [live["id"]])

    def test_list_since_and_the_open_cap(self):
        first = self.create(self.note_spec(1))
        self.clock.tick(120)
        mark = W._iso(self.clock())
        self.clock.tick(5)
        second = self.create(self.note_spec(1))
        self.assertEqual([s["id"] for s in self.store.list()], [second["id"], first["id"]])
        self.assertEqual([s["id"] for s in self.store.list(since=mark)], [second["id"]])
        for _ in range(W.BOUNDS["open"] - 2):
            self.create(self.note_spec(1))
        with self.assertRaises(W.WorkflowError) as ctx:
            self.create(self.note_spec(1))
        self.assertEqual(ctx.exception.status, 429)

    def test_a_running_agent_is_news_enough(self):
        """A task step whose agent process is alive keeps its workflow out of 'stale' (the thread engine watches
        the pid and max_minutes); once nothing runs and nothing reports, the stale rule holds again."""
        made = self.create(self.task_spec("slow"))
        self.store.tick()
        s = self.until(made["id"], lambda s: s["steps"][0]["task_status"] == "running", tick=False)
        self.clock.tick(16 * 60)
        s = self.store.summary(made["id"])
        self.assertEqual((s["state"], s["steps"][0]["state"], s["steps"][0]["task_status"]),
                         ("working", "working", "running"))
        out = sky.build({"chars": self.chars, "workflows": [s], "agent_tasks": self.tasks.list()}, self.clock())
        self.assertEqual([c for c in out["contacts"] if c["source"] == "workflow"], [])
        notes = self.create(self.note_spec(2, steps=[
            {"key": "a", "label": "A", "kind": "note", "at": os.path.join(self.work, "api")},
            {"key": "b", "label": "B", "kind": "task", "after": ["a"], "at": os.path.join(self.work, "ops"),
             "task": {"text": "after a", "runner": "slow"}}]))
        self.store.step(notes["id"], "a", notes["token"], state="working")
        self.clock.tick(16 * 60)
        self.assertEqual(self.store.summary(notes["id"])["state"], "stale")      # its thread has not started

    def test_stale_and_an_altered_record(self):
        made = self.create(self.note_spec(1))
        self.store.step(made["id"], "s0", made["token"], state="working")
        self.clock.tick(16 * 60)
        self.assertEqual(self.store.summary(made["id"])["state"], "stale")
        self.store.pulse(made["id"], made["token"])
        self.assertEqual(self.store.summary(made["id"])["state"], "working")
        file = os.path.join(self.wf_dir, made["id"], "workflow.json")
        with open(file) as handle:
            data = json.load(handle)
        data["spec"]["title"] = "something else"
        os.chmod(file, 0o600)
        with open(file, "w") as handle:
            json.dump(data, handle)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.summary(made["id"])
        self.assertEqual(ctx.exception.status, 409)


class Sky(Base):
    def wf(self, name):
        with open(os.path.join(FIXTURES, "workflow-variants.json"), encoding="utf-8") as handle:
            return json.load(handle)[name]

    def test_workflow_contacts_and_the_threads_they_own(self):
        gate, merge, failed = self.wf("FIX_GATE"), self.wf("FIX_MERGE"), self.wf("FIX_FAILED")
        for w, n in ((merge, "m"), (failed, "f")):
            w["id"] = w["id"][:-6] + n * 6
        tid = merge["steps"][1]["tid"]
        world = {"chars": self.chars, "workflows": [gate, merge, failed],
                 "agent_tasks": [{"id": tid, "status": "review", "title": "Fix the label slots", "target": os.path.join(self.work, "ops")}]}
        out = sky.build(world, self.clock())
        by_id = {c["id"]: c for c in out["contacts"]}
        self.assertEqual(by_id[f"workflow:{gate['id']}:fix"]["state"], "blocked")          # needs your answer
        self.assertEqual(by_id[f"workflow:{gate['id']}:fix"]["planet_uid"], "area:ops")
        self.assertEqual(by_id[f"workflow:{merge['id']}:fix"]["state"], "review")          # accept the changes
        self.assertEqual(by_id[f"workflow:{failed['id']}:approve"]["state"], "failed")
        self.assertNotIn("task:" + tid, by_id)                                            # the step's thread, once
        self.assertEqual(out["by_source"]["workflow"], 3)
        stale = dict(self.wf("FIX_GATE"), state="stale", current=["find"])
        out = sky.build({"chars": self.chars, "workflows": [stale]}, self.clock())
        self.assertEqual([(c["source"], c["state"]) for c in out["contacts"]], [("workflow", "stale")])

    def test_a_store_workflow_reaches_the_sky_as_one_contact(self):
        spec = self.fix_spec()
        spec["steps"][1] = {"key": "fix", "label": "Fix the label slots", "at": os.path.join(self.work, "ops"),
                            "kind": "gate", "after": ["find"]}
        made = self.create(spec)
        self.store.step(made["id"], "find", made["token"], state="done")
        rows = self.store.list()
        self.assertEqual((rows[0]["title"], rows[0]["steps"][1]["state"]), ("Fix the phone labels", "needs_you"))
        out = sky.build({"chars": self.chars, "workflows": rows}, self.clock())
        self.assertEqual([(c["source"], c["state"], c["planet_uid"]) for c in out["contacts"]],
                         [("workflow", "blocked", "area:ops")])


class Tasks(Base):
    def test_create_keeps_title_and_origin_and_finds_by_origin(self):
        self.tasks.last_create = -100
        row = self.tasks.create("a long prompt\nwith lines", os.path.join(self.work, "ops"), "writer",
                                title="Fix the slots", origin={"workflow": "w-x", "step": "a", "attempt": 1})
        self.assertEqual((row["title"], row["origin"]), ("Fix the slots", {"workflow": "w-x", "step": "a", "attempt": 1}))
        self.assertEqual(self.tasks.find_by_origin({"workflow": "w-x", "step": "a", "attempt": 1}), [row["id"]])
        self.assertEqual(self.tasks.find_by_origin({"workflow": "w-x", "step": "a", "attempt": 2}), [])
        self.tasks.last_create = -100
        plain = self.tasks.create("just text", os.path.join(self.work, "ops"), "writer")
        self.assertEqual((plain["title"], plain["origin"]), ("just text", None))
        with open(os.path.join(self.tasks.tasks_dir, plain["id"], "task.json")) as handle:
            self.assertNotIn("origin", json.load(handle))
        for bad in ({"title": "a\nb"}, {"origin": "x"}, {"env": {"PATH": "/tmp"}}):
            self.tasks.last_create = -100
            with self.assertRaises(T.TaskError):
                self.tasks.create("x", os.path.join(self.work, "ops"), "writer", **bad)


class Hardening(Base):
    """Fixes found in review: each test is a reviewer's repro turned into a regression test."""

    def setUp(self):
        super().setUp()
        gap = mock.patch.object(T, "CREATE_MIN_GAP", 0.0)
        gap.start()
        self.addCleanup(gap.stop)

    def wait_env(self, key, timeout=20.0):
        file = os.path.join(self.side, f"env-{key}.json")
        end = time.time() + timeout
        while not os.path.exists(file) and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.2)
        with open(file) as handle:
            return json.load(handle)

    def wait_thread(self, tid, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            row = self.tasks.summary(os.path.join(self.tasks.tasks_dir, tid))
            if row["status"] not in ("new", "running", "checking", "stopping"):
                return row
            time.sleep(0.1)
        self.fail(f"thread {tid} still running")

    def test_a_note_behind_an_unanswered_gate_cannot_be_reported(self):
        """no agent route passes a gate. gate -> note -> task: 'done' on the note is refused while
        the gate waits, so nothing launches; once the owner passes the gate the note is reported as usual."""
        ops = os.path.join(self.work, "ops")
        made = self.create({"title": "gate then work", "by": {"name": "an agent"}, "steps": [
            {"key": "g", "label": "Owner approves", "kind": "gate", "at": ops},
            {"key": "n", "label": "Agent note", "kind": "note", "after": ["g"], "at": ops},
            {"key": "t", "label": "Launch work", "kind": "task", "after": ["n"], "at": ops,
             "task": {"text": "do it", "runner": "writer"}}]})
        wid, token = made["id"], made["token"]
        for state in ("done", "skipped", "working"):
            with self.assertRaises(W.WorkflowError) as ctx:
                self.store.step(wid, "n", token, state=state)
            self.assertEqual((ctx.exception.status, ctx.exception.message),
                             (409, "step 'n' waits on 'g'; report it once that is through"))
        self.store.step(wid, "n", token, says="reading the brief")              # words alone are fine
        self.store.tick()
        self.assertEqual((self.tasks.list(), self.state(wid)[1]), ([], {"g": "needs_you", "n": "waiting", "t": "waiting"}))
        # a record written before this rule (a note 'done' past the open gate) still launches nothing
        path = self.store._dir(wid)
        with self.store._lock(path):
            rec = self.store._read(path)
            self.store._append(rec, {"ev": "step", "key": "n", "state": "done", "by": "an agent"})
        self.store.tick()
        self.assertEqual((self.tasks.list(), self.state(wid)[1]["n"]), ([], "waiting"))
        self.store.gate(wid, "g", False, why="not yet")
        with self.assertRaises(W.WorkflowError) as ctx:
            self.store.step(wid, "n", token, state="done")
        self.assertEqual(ctx.exception.message, "step 'n' won't start: 'Owner approves' failed")
        self.store.retry(wid, "g")
        self.store.gate(wid, "g", True)
        self.store.step(wid, "n", token, state="done")
        self.assertEqual(self.state(wid)[1], {"g": "done", "n": "done", "t": "ready"})

    def test_a_dependent_launches_on_the_snapshot_of_its_producers_output(self):
        """a producer task declares its output while it runs and finishes between ticks; the one tick
        that sees it done keeps the file BEFORE the dependent launches, so the dependent's text names the
        snapshot and its sha256, never 'not found'."""
        ops = os.path.join(self.work, "ops")
        report = os.path.join(ops, "report.md")
        made = self.create({"title": "handoff", "by": {"name": "an agent"}, "steps": [
            {"key": "p", "label": "Produce", "kind": "task", "at": ops,
             "task": {"text": "make report", "runner": "envslow", "wait": "clear"}},
            {"key": "c", "label": "Consume", "kind": "task", "after": ["p"], "at": ops,
             "inputs": [{"from": "p", "path": "report.md"}],
             "task": {"text": "read report", "runner": "writer", "wait": "clear"}}]})
        wid = made["id"]
        self.store.tick()
        env = self.wait_env("p")
        with open(report, "w") as handle:
            handle.write("the report\n")
        self.clock.tick(30)
        self.store.step(wid, "p", env["CM_WORKFLOW_TOKEN"], output=report)
        self.wait_thread(self.events(wid, "task")[0]["tid"])
        self.store.tick()
        art = [e for e in self.events(wid, "artifact") if not e.get("refused")]
        launched = [e for e in self.events(wid, "task") if e["key"] == "c"]
        self.assertTrue(art and launched)
        self.assertLess(art[0]["seq"], self.events(wid, "launch")[-1]["seq"])     # kept, then launched
        with open(os.path.join(self.tasks.tasks_dir, launched[0]["tid"], "task.json")) as handle:
            text = json.load(handle)["text"]
        self.assertIn(os.path.join(self.wf_dir, wid, "artifacts", "p", "report.md"), text)
        self.assertIn(art[0]["sha256"][:16], text)
        self.assertNotIn("not found", text)

    def test_the_last_steps_output_is_kept_when_the_workflow_finishes(self):
        ops = os.path.join(self.work, "ops")
        final = os.path.join(ops, "final.md")
        made = self.create({"title": "last", "by": {"name": "an agent"}, "steps": [
            {"key": "p", "label": "Produce", "kind": "task", "at": ops,
             "task": {"text": "make report", "runner": "envslow", "wait": "clear"}}]})
        wid = made["id"]
        self.store.tick()
        env = self.wait_env("p")
        with open(final, "w") as handle:
            handle.write("final\n")
        self.clock.tick(30)
        self.store.step(wid, "p", env["CM_WORKFLOW_TOKEN"], output=final)
        self.wait_thread(self.events(wid, "task")[0]["tid"])
        self.store.tick()
        self.assertEqual(self.store.summary(wid)["state"], "done")
        art = [e for e in self.events(wid, "artifact") if not e.get("refused")]
        self.assertEqual([a["name"] for a in art], ["final.md"])
        self.store.tick()                                                     # kept once, not every tick
        self.assertEqual(len(self.events(wid, "artifact")), 1)

    def test_the_daily_cap_holds_across_two_stores_on_one_folder(self):
        """The bridge's tick and a CLI's tick (two stores on one folder) on two workflows, each counting before
        the other launches: the folder's lock makes the count and the launch one step, so cap 1 is one."""
        self.store = self.make_store(launches_per_day=1)
        a = self.create(self.task_spec("writer"))
        b = self.create(self.task_spec("writer"))
        other_tasks = T.TaskStore(os.path.join(self.root, "tasks"), self.runners(), [self.work])
        other_tasks.last_create = -100.0
        other = W.WorkflowStore(self.wf_dir, task_store=other_tasks, roots=[self.work], now=self.clock,
                                chars=self.chars, launches_per_day=1)
        self.store._ids = lambda: [a["id"]]                                   # each ticks its own workflow
        other._ids = lambda: [b["id"]]
        counting = threading.Barrier(2, timeout=3)
        real = W.WorkflowStore._launches_today

        def slow(store, now):
            n = real(store, now)
            with contextlib.suppress(threading.BrokenBarrierError):
                counting.wait()                       # without the lock both would count 0 here
            return n
        with mock.patch.object(W.WorkflowStore, "_launches_today", slow):
            runs = [threading.Thread(target=s.tick) for s in (self.store, other)]
            for t in runs:
                t.start()
            for t in runs:
                t.join(30)
        counted = [e for w in (a, b) for e in self.events(w["id"], "launch") if e["counted"]]
        deferred = [e for w in (a, b) for e in self.events(w["id"], "deferred") if e.get("cap")]
        self.assertEqual((len(counted), len(deferred)), (1, 1))
        ledger = self.store._ledger(self.clock())
        self.assertEqual(stat.S_IMODE(os.stat(ledger).st_mode), 0o600)
        self.assertEqual(self.store._launches_today(self.clock()), 1)

    def test_a_folder_swapped_for_a_link_into_credentials_inside_the_root_is_refused(self):
        """+ 3.10 item 4: every rule the path passed is applied again to where the open really went;
        a hard-linked file (another name elsewhere) is refused too."""
        made = self.create(self.note_spec(2))
        api = os.path.join(self.work, "api")
        pub = os.path.join(api, "public")
        os.makedirs(pub)
        os.makedirs(os.path.join(api, ".ssh"))
        src = os.path.join(pub, "report.md")
        with open(src, "w") as handle:
            handle.write("ok\n")
        with open(os.path.join(api, ".ssh", "report.md"), "w") as handle:
            handle.write("PRIVATE KEY MATERIAL\n")
        self.store.step(made["id"], "s0", made["token"], state="working", output=src)
        real_open = os.open
        swapped = []

        def hook(path, flags, *a, **kw):
            if path == src and not swapped:
                os.rename(pub, pub + ".moved")
                os.symlink(os.path.join(api, ".ssh"), pub)
                swapped.append(1)
            return real_open(path, flags, *a, **kw)
        self.clock.tick(30)
        with mock.patch.object(W.os, "open", side_effect=hook):
            self.store.step(made["id"], "s0", made["token"], state="done")
        art = self.events(made["id"], "artifact")[0]
        self.assertEqual(art["refused"], "a folder on its way was swapped for a link: "
                                         "it is inside a folder of credentials or of git's own")
        self.assertFalse(os.path.exists(os.path.join(self.wf_dir, made["id"], "artifacts", "s0")))
        twin = os.path.join(api, "twin.md")
        with open(twin, "w") as handle:
            handle.write("two names\n")
        os.link(twin, os.path.join(self.side, "elsewhere.md"))
        self.clock.tick(30)
        self.store.step(made["id"], "s1", made["token"], state="done", output=twin)
        art = [e for e in self.events(made["id"], "artifact") if e["key"] == "s1"][0]
        self.assertIn("hard link", art["refused"])

    def test_latest_json_is_written_through_a_fresh_name_that_follows_no_link(self):
        made = self.create(self.note_spec(1))
        victim = os.path.join(self.side, "victim.txt")
        with open(victim, "w") as handle:
            handle.write("precious")
        os.chmod(victim, 0o644)
        os.symlink(victim, os.path.join(self.wf_dir, made["id"], "latest.json.tmp"))
        self.store.step(made["id"], "s0", made["token"], state="working")
        self.clock.tick(1)
        self.store.step(made["id"], "s0", made["token"], state="working", progress="1/9")
        self.store.pulse(made["id"], made["token"])
        with open(victim) as handle:
            self.assertEqual((handle.read(), stat.S_IMODE(os.stat(victim).st_mode)), ("precious", 0o644))
        latest = os.path.join(self.wf_dir, made["id"], "latest.json")
        self.assertFalse(os.path.islink(latest))
        self.assertEqual(stat.S_IMODE(os.stat(latest).st_mode), 0o600)
        leftovers = [n for n in os.listdir(os.path.join(self.wf_dir, made["id"])) if n.endswith(".tmp")]
        self.assertEqual(leftovers, ["latest.json.tmp"])                       # only the planted link

    def test_the_family_is_the_selected_models(self):
        py = sys.executable
        runners = self.runners(**{
            "multi": {"cmd": [py, self.fake, "write"], "default_model": "gemini-3", "max_minutes": 1,
                      "models": [{"name": "gemini-3", "args": ["--model", "gemini-3"]},
                                 {"name": "sonnet", "args": ["--model", "sonnet"]}]},
            "claudecheck": {"cmd": [py, self.fake, "judge"], "role": "check", "max_minutes": 1,
                            "models": [{"name": "sonnet", "args": ["--model", "sonnet"]}]}})
        self.assertEqual((T.family_of("multi", runners), T.family_of("multi", runners, "sonnet"),
                          T.family_of("multi", runners, "gemini-3"), T.family_of("claude", runners, "nope")),
                         ("google", "anthropic", "google", "anthropic"))
        self.store = self.make_store(runners)
        with self.assertRaises(W.WorkflowError) as ctx:
            self.create(self.task_spec("multi", model="sonnet", check_runner="claudecheck"))
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (400, "a second opinion must come from a different model family"))
        self.create(self.task_spec("multi", model="gemini-3", check_runner="claudecheck"))

    def test_older_open_workflows_count_toward_the_open_cap(self):
        base = self.clock.t
        self.clock.t = base + 3 * 3600                                        # the closed ones are the newer ids
        for _ in range(W.BOUNDS["open"] + W.BOUNDS["closed_listed"] + 64):
            self.clock.tick(61)
            m = self.create(self.note_spec(1))
            self.store.step(m["id"], "s0", m["token"], state="done")
        self.clock.t = base
        for _ in range(W.BOUNDS["open"]):
            self.clock.tick(61)
            self.create(self.note_spec(1))
        self.clock.t = base + 6 * 3600
        with self.assertRaises(W.WorkflowError) as ctx:
            self.create(self.note_spec(1))
        self.assertEqual(ctx.exception.status, 429)
        listed = self.store.list()
        self.assertEqual((sum(s["state"] not in W.CLOSED for s in listed), sum(s["state"] in W.CLOSED for s in listed)),
                         (W.BOUNDS["open"], W.BOUNDS["closed_listed"]))
        calls = []
        real = self.store._derive
        with mock.patch.object(self.store, "_derive", side_effect=lambda rec: calls.append(rec.id) or real(rec)):
            self.store.list()
        self.assertEqual(len(calls), W.BOUNDS["open"])                        # settled history is not derived again

    def test_a_short_goal_is_put_back_as_the_hole(self):
        ops = os.path.join(self.work, "ops")
        made = self.create({"title": "Fix bug", "goal": "Fix bug", "by": {"name": "x"}, "steps": [
            {"key": "a", "label": "A", "kind": "task", "at": ops,
             "task": {"text": "Fix bug", "runner": "writer", "wait": "clear"}},
            {"key": "b", "label": "B", "kind": "task", "at": ops, "after": ["a"],
             "task": {"text": "Then check Fix bug again, and fix bugs", "runner": "writer", "wait": "clear"}}]})
        self.until(made["id"], lambda s: s["state"] == "done", timeout=30)
        self.store.save_template(made["id"], "short")
        spec = self.store.instantiate("short", os.path.join(self.work, "site"), "Fix labels", "writer")
        self.assertEqual([s["task"]["text"] for s in spec["steps"]],
                         ["Fix labels", "Then check Fix labels again, and fix bugs"])
        self.assertEqual(W._goal_hole("Fix the tests", "Fix"), "Fix the tests")      # one short word: left be

    def test_two_opinions_takes_a_second_agent_that_does_work(self):
        runners = T.valid_runners({"claude": {"cmd": [sys.executable, self.fake, "write"], "max_minutes": 1},
                                   "codex": {"cmd": [sys.executable, self.fake, "judge"], "role": "check",
                                             "max_minutes": 1}})
        self.store = self.make_store(runners)
        ops = os.path.join(self.work, "ops")
        two = self.store.instantiate("two-opinions", ops, "Make the tests pass", "claude")
        W.validate(two, [self.work], self.tasks.runners)
        self.assertEqual([s["key"] for s in two["steps"]], ["one", "pick"])       # check-only codex: dropped, said
        self.assertEqual(two["steps"][1]["note"], W.NO_CHECKER)
        fix = self.store.instantiate("fix-and-check", ops, "Make the tests pass", "claude")
        self.assertEqual(fix["steps"][0]["task"]["check_runner"], "codex")        # it does check
        W.validate(fix, [self.work], self.tasks.runners)

    def test_a_task_step_records_branch_worktree_and_files_changed(self):
        """when the thread first reads finished, the step keeps {branch, worktree, files_changed}."""
        repo = os.path.join(self.work, "site")

        def git(*args):
            subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
        git("init", "-q", "-b", "main")
        import testenv as _testenv
        _testenv.hermetic_repo(repo)
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        git("config", "core.hooksPath", "/dev/null")
        with open(os.path.join(repo, "index.html"), "w") as handle:
            handle.write("<p>hi</p>\n")
        git("add", "-A")
        git("commit", "-q", "-m", "init")
        made = self.create({"title": "Write a note", "by": {"name": "an agent"}, "steps": [
            {"key": "w", "label": "Write it", "kind": "task", "at": repo,
             "task": {"text": "write the note", "runner": "writer", "wait": "merged"}}]})
        s = self.until(made["id"], lambda s: s["steps"][0]["state"] == "needs_you", timeout=30)
        self.assertEqual(s["steps"][0]["files_changed"], 1)
        res = self.events(made["id"], "result")
        self.assertEqual(len(res), 1)
        tid = s["steps"][0]["tid"]
        self.assertEqual((res[0]["branch"], res[0]["files_changed"], res[0]["tid"]), ("cm/" + tid, ["agent-note.txt"], tid))
        self.assertTrue(res[0]["worktree"] and os.path.isdir(res[0]["worktree"]))
        self.assertEqual(s["steps"][0]["branch"], "cm/" + tid)
        detail = self.store.detail(made["id"])
        self.assertEqual(detail["results"]["w"]["files_changed"], ["agent-note.txt"])
        self.assertNotIn(res[0]["worktree"], json.dumps(s))                       # paths only in detail()
        self.store.tick()
        self.assertEqual(len(self.events(made["id"], "result")), 1)              # once per attempt

    def test_folding_the_frames_gives_the_summary_in_full(self):
        """Replay frames fold to the summary's states and to each step's says, why, progress, output and kept
        file; words said without a change of state get one closing frame."""
        made = self.create(self.note_spec(2))
        wid, token = made["id"], made["token"]
        report = os.path.join(self.work, "api", "report.md")
        with open(report, "w") as handle:
            handle.write("r\n")
        self.store.step(wid, "s0", token, state="working", says="reading", progress="1/4")
        self.clock.tick(15)
        self.store.step(wid, "s0", token, state="done", says="read it", output=report)
        self.clock.tick(15)
        self.store.step(wid, "s1", token, state="working", says="on it")
        rp = self.store.replay(wid)
        s = self.store.summary(wid)
        want = {st["key"]: W._detail_of(st) for st in s["steps"]}
        self.assertEqual(W.fold(rp["frames"], s["steps"], detail=True)["detail"], want)
        self.assertEqual(rp["final"]["detail"], want)
        self.assertEqual(want["s0"], {"says": "read it", "why": None, "progress": {"done": 1, "total": 4},
                                      "output": "report.md", "artifact": "report.md"})
        self.clock.tick(15)
        self.store.step(wid, "s1", token, says="halfway", progress="2/4")      # same state: words only
        rp = self.store.replay(wid)
        s = self.store.summary(wid)
        self.assertEqual(W.fold(rp["frames"], s["steps"], detail=True)["detail"],
                         {st["key"]: W._detail_of(st) for st in s["steps"]})
        self.assertEqual([f["key"] for f in rp["frames"] if f.get("synthetic")], ["s1"])

    def test_a_pass_from_the_makers_own_family_is_no_second_opinion(self):
        py = sys.executable
        runners = self.runners(claudecheck={"cmd": [py, self.fake, "judge"], "role": "check", "max_minutes": 1,
                                            "models": [{"name": "sonnet", "args": ["--model", "sonnet"]}]})
        self.store = self.make_store(runners)
        st = {"task": {"runner": "claude", "model": None, "check_runner": "codex", "wait": "clear"}}

        def row(runner):
            return {"status": "review", "merge_blockers": [],
                    "last_verdict": {"n": 2, "runner": runner, "model": None, "verdict": "PASS", "stale": False}}
        self.assertEqual(self.store._task_state(st, row("codex"))[:3], ("done", "", None))
        self.assertEqual(self.store._task_state(st, row("claudecheck"))[:3],
                         ("needs_you", "", "waiting for a second opinion that passes it"))
        self.assertEqual(self.store._task_state(st, row("judge"))[0], "done")     # another family still counts


def k4_spec(root):
    """the workflow engine's spec: tests/fixtures/workflow-spec.json at `root` with its step 2 turned into a gate."""
    with open(os.path.join(FIXTURES, "workflow-spec.json"), encoding="utf-8") as handle:
        spec = json.loads(handle.read().replace("{root}", root))
    two = spec["steps"][1]
    spec["steps"][1] = {"key": two["key"], "label": two["label"], "at": two["at"], "kind": "gate", "after": two["after"]}
    return spec


def k4_seed(scratch, wf_dir):
    """the workflow engine's harness. The bench workspace in <scratch>/ws (the one the probe builds there and reuses), then
    k4_spec() created into wf_dir through the store (what `wf new` does) and its step 1 reported done (what
    `wf step find --state done` does). Step 1 done is what FIX, the fixture the check compares with, shows: a
    gate waits on its `after` steps, so with 'find' still open the gate reads waiting, not needs_you. Returns
    {id, state, steps}. From a shell: python3 test_workflows.py --k4-seed SCRATCH WF_DIR, then the probe with
    --scratch SCRATCH --config-extra '{"workflows_dir": "WF_DIR"}' and the wf-world check."""
    import bench
    ws = os.path.join(scratch, "ws")
    if not os.path.exists(os.path.join(ws, bench.MARKER)):
        os.makedirs(ws, exist_ok=True)
        bench.build_workspace(ws, 1, [])
    root = os.path.join(ws, "root")
    with open(os.path.join(ws, "config.json"), encoding="utf-8") as handle:
        cfg = json.load(handle)
    cfg["workflows_dir"] = wf_dir
    store = W.store_for(cfg, roots=[root])
    made = store.create(k4_spec(root))
    store.step(made["id"], "find", made["token"], state="done", says="6 labels are cut")
    s = store.summary(made["id"])
    return {"id": made["id"], "state": s["state"], "steps": [[x["key"], x["kind"], x["state"]] for x in s["steps"]]}


class K4World(unittest.TestCase):
    """the workflow engine-K4 [wf-world] on a real render of the bench (render-game.py, as the probe serves it): the derived summary
    reaches the page, the gate is one "needs your answer" contact, summaries name no path. The check's counts parts
    (W.counts.answer >= 1, W.counts.total == the contacts) read sky.counts, 's: the seed stub counts nothing,
    and merges the workflow engine before the words module, so that part is skipped until the words module's counts are in the tree."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(os.path.join(HERE, "bench.py")):
            raise unittest.SkipTest("bench.py is not shipped")
        cls.tmp = tempfile.TemporaryDirectory(prefix="cm-wf-k4-")
        scratch = os.path.realpath(cls.tmp.name)
        wf_dir = os.path.join(scratch, "wf")
        cls.seeded = k4_seed(scratch, wf_dir)
        import bench
        ws = os.path.join(scratch, "ws")
        cls.root = os.path.join(ws, "root")
        cfg_path = os.path.join(ws, "config.json")
        with open(cfg_path, encoding="utf-8") as handle:
            cfg = json.load(handle)
        cfg["workflows_dir"] = wf_dir                     # the probe's --config-extra
        with open(cfg_path, "w", encoding="utf-8") as handle:
            json.dump(cfg, handle, indent=1)
        env = bench.env_for(cfg_path, ws)
        env["CONTAINER_OUT"] = os.path.join(scratch, "page.html")
        run = subprocess.run([sys.executable, os.path.join(HERE, "render-game.py")], cwd=HERE, env=env,
                             capture_output=True, text=True, timeout=300)
        if run.returncode:
            raise AssertionError(run.stderr[-2000:])
        with open(env["CONTAINER_OUT"], encoding="utf-8") as handle:
            cls.world = json.loads(re.search(r"(?m)^const W = (.*);$", handle.read()).group(1))
        with open(os.path.join(FIXTURES, "workflow-demo.json"), encoding="utf-8") as handle:
            cls.fix = json.load(handle)

    @classmethod
    def tearDownClass(cls):
        hist = os.path.join(cls.tmp.name, "ws", "hist")
        if os.path.isdir(hist):
            os.chmod(hist, 0o755)                         # the bench makes it read-only
        cls.tmp.cleanup()

    def test_the_seed_matches_fix(self):
        self.assertEqual(self.seeded["state"], "needs_you")
        self.assertEqual(self.seeded["steps"][:2], [["find", "note", "done"], ["fix", "gate", "needs_you"]])

    def test_the_derived_summary_reaches_the_page(self):
        w = self.world["workflows"][0]
        self.assertEqual((w["title"], w["steps"][1]["state"]), (self.fix["title"], "needs_you"))
        self.assertEqual([(s["key"], s["planet_uid"]) for s in w["steps"]],
                         [("find", "area:api"), ("fix", "area:ops"), ("approve", "area:ops"), ("docs", "area:docs")])
        self.assertTrue(w["sentence"])

    def test_the_gate_is_one_needs_your_answer_contact(self):
        mine = [c for c in self.world["sky"]["contacts"] if c["source"] == "workflow"]
        self.assertEqual([(c["state"], c["planet_uid"]) for c in mine], [("blocked", "area:ops")])

    def test_summaries_name_no_path(self):
        text = json.dumps(self.world["workflows"])
        self.assertNotIn(self.root, text)
        self.assertNotIn(self.tmp.name, text)

    def test_every_contact_is_counted_once(self):
        one = {"contacts": [dict(self.world["sky"]["contacts"][0])]}
        if sky.counts(one, [], [])["total"] == 0:
            self.skipTest("W.counts is 's (sky.counts) and the seed stub counts nothing; "
                          "merges the workflow engine before the words module")
        counts = self.world["counts"]
        self.assertGreaterEqual(counts["answer"], 1)
        self.assertEqual(counts["total"], len(self.world["sky"]["contacts"]))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--k4-seed":
        print(json.dumps(k4_seed(os.path.realpath(sys.argv[2]), os.path.realpath(sys.argv[3]))))
        sys.exit(0)
    unittest.main()
