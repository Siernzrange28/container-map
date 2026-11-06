#!/usr/bin/env python3
"""test_cli.py — the install commands a stranger runs: doctor, init, install, uninstall, the
argument handling of serve/render/config, the headless thread commands (`thread …`) and the
live `demo` (a scripted agent, a temp HOME).

Nothing here talks to a real service manager: install and uninstall run either with --dry-run in
a child process (temp HOME) or in-process with the manager replaced by a recorder, and the real
runner is patched to fail the test if it is ever reached. No agent CLI is ever executed: the fake
`claude` on PATH exits 99 and is only looked up, and the thread tests run a fake agent script.

Run: python3 -m unittest test_cli
"""
import contextlib
import http.client
import io
import json
import os
import plistlib
import shutil
import signal
import threading
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
CLI = os.path.join(HERE, "cli.py")
sys.path.insert(0, HERE)
import testenv  # noqa: E402,F401  (first: clears what steers the tool)
import cli  # noqa: E402
import demo  # noqa: E402


def stranger_env(home, path=None):
    """env -i style: only PATH, HOME and a locale reach the child."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin") if path is None else path,
            "HOME": home, "LANG": "C.UTF-8"}


def run_cli(args, env, cwd=None):
    return subprocess.run([sys.executable, CLI] + args, capture_output=True, text=True, env=env,
                          cwd=cwd or HERE, timeout=120)


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def tool_copy(dst):
    """The runtime files of this checkout in a folder of their own (what an unpacked release is),
    so a test can start a real bridge without ever touching this folder's .bridge-live or log."""
    os.makedirs(dst)
    for name in os.listdir(HERE):
        path = os.path.join(HERE, name)
        runtime = name.endswith((".py", ".sh")) and not name.startswith("test_")
        if os.path.isfile(path) and (runtime or name.startswith("container.") and name.endswith(
                "example.json")):
            shutil.copy2(path, dst)
    for folder in ("themes", "fonts"):
        shutil.copytree(os.path.join(HERE, folder), os.path.join(dst, folder))


class FakeBridge:
    """Enough of a bridge for the install and doctor tests: answers GET / with 200 and the UI
    cookie, and writes a live file the way the real one does (a new boot id on every start)."""

    def __init__(self, root, port=0, pid=None):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<html>map</html>"
                self.send_response(200)
                self.send_header("Set-Cookie", "container_map_ui=x; HttpOnly")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self.root, self.pid, self.boots = root, pid or os.getpid(), 0
        self.live = os.path.join(root, ".container-map", "run", f"bridge-{self.port}.json")
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def write_live(self):
        self.boots += 1
        os.makedirs(os.path.dirname(self.live), exist_ok=True)
        with open(self.live, "w") as handle:
            json.dump({"version": 1, "boot_id": f"boot-{self.boots}", "pid": self.pid,
                       "port": self.port, "agent_token": "t"}, handle)
        os.chmod(self.live, 0o600)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Served:
    """A real `cli.py serve` (from a tool copy) on a free port, in its own process group: what a
    terminal's Ctrl-C reaches is the whole group."""

    def __init__(self, test, tool, root, home, extra=()):
        import threading
        self.port = free_port()
        self.root, self.lines = root, []
        self.live = os.path.join(root, ".container-map", "run", f"bridge-{self.port}.json")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(tool, "cli.py"), "serve", root, "--port", str(self.port), *extra],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=stranger_env(home),
            cwd=tool, start_new_session=True)
        test.addCleanup(self.kill)
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip("\n"))

    def wait_up(self, test, seconds=60):
        end = time.time() + seconds
        while time.time() < end:
            if self.proc.poll() is not None:
                test.fail("serve exited early: " + "\n".join(self.lines))
            try:
                with open(self.live, encoding="utf-8") as handle:
                    if json.load(handle).get("port") == self.port:
                        return
            except (OSError, ValueError):
                pass
            time.sleep(0.2)
        test.fail("no live file after %ss: %s" % (seconds, "\n".join(self.lines)))

    def wait_line(self, test, text, seconds=60):
        end = time.time() + seconds
        while time.time() < end and not any(text in line for line in self.lines):
            time.sleep(0.01)
        test.assertTrue(any(text in line for line in self.lines), "\n".join(self.lines))

    def signal(self, sig):
        os.killpg(self.proc.pid, sig)

    def finish(self, seconds=30):
        """Wait for serve to exit; (its exit code, everything it printed)."""
        code = self.proc.wait(timeout=seconds)
        self.reader.join(5)
        return code, "\n".join(self.lines)

    def kill(self):
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait()
        self.reader.join(5)
        self.proc.stdout.close()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cm-cli-")
        self.home = os.path.join(self.tmp, "home")
        self.root = os.path.join(self.tmp, "my-work")
        self.bin = os.path.join(self.tmp, "bin")
        for d in (self.home, self.bin, os.path.join(self.root, "app", ".git"),
                  os.path.join(self.root, "lib"), os.path.join(self.root, "web"),
                  os.path.join(self.root, "notes"), os.path.join(self.root, ".hidden")):
            os.makedirs(d)
        for f in ("lib/pyproject.toml", "web/package.json", "loose.txt"):
            open(os.path.join(self.root, f), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_agent(self, name="claude"):
        path = os.path.join(self.bin, name)
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\nexit 99\n")
        os.chmod(path, 0o755)
        return path


class Arguments(Fixture):
    def test_flag_values_are_not_the_root(self):
        for args in (["--port", "8000", "/x"], ["/x", "--port", "8000"], ["--port=8000", "/x"],
                     ["--config", "c.json", "/x"], ["--out", "page.html", "/x", "--dry-run"]):
            self.assertEqual(cli._root(args), "/x", args)
        self.assertEqual(cli._root(["--port", "8000"]), "")

    def test_config_flag_reaches_config_and_render(self):
        conf = os.path.join(self.tmp, "mine.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1, "name": "FROMFLAG", "tmux_session": "none"}, handle)
        env = stranger_env(self.home)
        r = run_cli(["config", "--config", conf, self.root], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(r.stdout)
        self.assertEqual(cfg["_path"], conf)
        self.assertEqual(cfg["name"], "FROMFLAG")
        self.assertEqual(cfg["work_root"], self.root)
        page = os.path.join(self.tmp, "page.html")
        r = run_cli(["render", self.root, "--config", conf, "--out", page], env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with open(page, encoding="utf-8") as handle:
            self.assertIn("FROMFLAG", handle.read())

    def test_render_port_is_baked_into_the_static_pages_live_link(self):
        env = stranger_env(self.home)
        page = os.path.join(self.tmp, "page.html")
        link = "const LIVE_URL = LIVE ? location.origin + '/' : "
        r = run_cli(["render", self.root, "--port", "8123", "--out", page], env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with open(page, encoding="utf-8") as handle:
            self.assertIn(link + '"http://127.0.0.1:8123/";', handle.read())
        env["BRIDGE_PORT"] = "8200"                       # what serve sets for the renders it runs
        r = run_cli(["render", self.root, "--out", page], env)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with open(page, encoding="utf-8") as handle:
            self.assertIn(link + '"http://127.0.0.1:8200/";', handle.read())
        r = run_cli(["render", self.root, "--port", "99999", "--out", page], env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("port must be a number from 1 to 65535", r.stderr)

    def test_missing_config_file_is_named(self):
        r = run_cli(["config", "--config", os.path.join(self.tmp, "nope.json")],
                    stranger_env(self.home))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no such config file", r.stderr)


class Serve(Fixture):
    def test_busy_port_is_one_readable_line(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            env = stranger_env(self.home)
            env["BRIDGE_LIVE_PATH"] = os.path.join(self.tmp, "live.json")
            r = run_cli(["serve", "--port", str(port), self.root], env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn(f"port {port}", r.stderr)
        self.assertFalse(os.path.exists(env["BRIDGE_LIVE_PATH"]))

    def test_renders_once_before_the_url(self):
        out, calls = io.StringIO(), []

        def record(argv, env=None, cwd=None):
            calls.append((os.path.basename(argv[-1]), "live at" in out.getvalue(), env))
            return 0

        port = free_port()
        with mock.patch.object(cli, "_call", record), contextlib.redirect_stdout(out), \
                mock.patch.dict(os.environ, {"HOME": self.home}):
            rc = cli.cmd_serve([self.root, "--port", str(port)])
        self.assertEqual(rc, 0)
        self.assertEqual([(c[0], c[1]) for c in calls],
                         [("render-game.py", False), ("world.sh", True)])
        for _, _, env in calls:
            self.assertEqual(env["CONTAINER_ROOT"], self.root)
            self.assertEqual(env["BRIDGE_PORT"], str(port))
        self.assertIn(f"http://127.0.0.1:{port}/", out.getvalue())

    def test_ctrl_c_in_the_foreground_is_handled_not_a_traceback(self):
        out, err = io.StringIO(), io.StringIO()

        def interrupted(argv, env=None, cwd=None):
            if argv[-1].endswith("world.sh"):
                raise KeyboardInterrupt
            return 0

        with mock.patch.object(cli, "_call", interrupted), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err), mock.patch.dict(os.environ, {"HOME": self.home}):
            try:
                rc = cli.cmd_serve([self.root, "--port", str(free_port())])
            except KeyboardInterrupt:
                self.fail("Ctrl-C escaped serve as a traceback")
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip().splitlines()[-1], "container-map: stopped")
        self.assertEqual(err.getvalue(), "")


class CallSignals(unittest.TestCase):
    """cli._call: the stop signals are never lost, never ignored on the child's behalf, and the
    child is stopped, not orphaned."""

    def test_a_signal_that_lands_while_the_child_starts_still_stops_it(self):
        real = subprocess.Popen
        started = []
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            with self.subTest(signal=sig.name):
                def popen_then_signal(*args, **kwargs):
                    proc = real(*args, **kwargs)
                    started.append(proc)
                    # the user is quick: it lands inside Popen. Aimed at this thread, the one _call blocks it
                    # in: sent to the process, a thread another test left running could take it and die.
                    signal.pthread_kill(threading.get_ident(), sig)
                    return proc

                began = time.time()
                with mock.patch.object(cli.subprocess, "Popen", popen_then_signal):
                    with self.assertRaises(KeyboardInterrupt):
                        cli._call(["sleep", "30"])
                self.assertLess(time.time() - began, 20, "it waited out the child")
                self.assertIsNotNone(started[-1].poll(), "the child was stopped")

    def test_the_child_ignores_ctrl_c_and_this_process_does_not(self):
        if not os.path.exists("/proc/self/status"):
            self.skipTest("needs /proc")
        before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM,
                                                          signal.SIGHUP)}
        out = os.path.join(tempfile.mkdtemp(prefix="cm-call-"), "status")
        self.addCleanup(shutil.rmtree, os.path.dirname(out), True)
        rc = cli._call(["sh", "-c", f"grep -E '^Sig(Ign|Blk):' /proc/$$/status > {out}"])
        self.assertEqual(rc, 0)
        with open(out) as handle:
            fields = {line.split(":")[0]: int(line.split()[1], 16) for line in handle}
        self.assertTrue(fields["SigIgn"] & (1 << (signal.SIGINT - 1)),
                        "SIGINT is ignored in the child")
        self.assertFalse(fields["SigIgn"] & (1 << (signal.SIGTERM - 1)), "SIGTERM is not")
        self.assertEqual(fields["SigBlk"], 0, "nothing stays blocked in the child")
        self.assertEqual({sig: signal.getsignal(sig) for sig in before}, before,
                         "the handlers here are put back")
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), set())

    def test_the_exit_code_comes_through(self):
        self.assertEqual(cli._call(["sh", "-c", "exit 7"]), 7)


class ServeRuns(Fixture):
    """A real serve from a copy of the tool, on a free port: where the bridge keeps its
    credentials, who can find them, and how Ctrl-C ends it."""

    def setUp(self):
        super().setUp()
        self.tool = os.path.join(self.tmp, "tool")
        tool_copy(self.tool)

    def tool_run(self, script, args, cwd=None):
        return subprocess.run([sys.executable, os.path.join(self.tool, script)] + args,
                              capture_output=True, text=True, env=stranger_env(self.home),
                              cwd=cwd or self.root, timeout=60)

    def test_the_live_file_is_per_root_and_per_port_and_everything_finds_it(self):
        legacy = os.path.join(self.tool, ".bridge-live")
        with open(legacy, "w") as handle:           # an earlier run's file, its process long gone
            json.dump({"version": 1, "pid": 2 ** 22 + 1, "port": 1, "agent_token": "old"}, handle)
        os.chmod(legacy, 0o600)
        served = Served(self, self.tool, self.root, self.home)
        served.wait_up(self)
        self.assertEqual(os.stat(served.live).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(served.live)).st_mode & 0o777, 0o700)
        with open(served.live) as handle:
            live = json.load(handle)
        self.assertEqual((live["version"], live["port"]), (1, served.port))
        with open(legacy) as handle:
            self.assertEqual(json.load(handle)["agent_token"], live["agent_token"],
                             "a legacy file that exists stays current for the hooks that read it")
        # gamectl, run from a folder inside the root, finds the same file with no flags
        r = self.tool_run("gamectl.py", ["state"], cwd=os.path.join(self.root, "app"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIsInstance(json.loads(r.stdout), dict)
        # doctor knows whose bridge holds the port
        r = self.tool_run("cli.py", ["doctor", self.root, "--port", str(served.port)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("own bridge", r.stdout)
        self.assertIn(served.live, r.stdout)
        served.signal(signal.SIGTERM)               # what systemd sends: the file goes with it
        end = time.time() + 20
        while time.time() < end and (os.path.exists(served.live) or cli._port_busy(served.port)):
            time.sleep(0.2)
        self.assertFalse(os.path.exists(served.live), "the stopped bridge removed its live file")

    def test_ctrl_c_stops_the_whole_run_with_one_line(self):
        # Ctrl-C the moment the url is printed (while world.sh is being started: the CLI must not
        # be deaf to it then, three times over), and once the bridge is up
        for index, moment in enumerate(("the url is out",) * 3 + ("the bridge is up",)):
            with self.subTest(moment, index=index):
                served = Served(self, self.tool, self.root, self.home)
                if moment == "the url is out":
                    served.wait_line(self, "live at")
                else:
                    served.wait_up(self)
                served.signal(signal.SIGINT)            # a terminal sends it to the whole group
                code, text = served.finish()
                self.assertEqual(code, 0, text)
                self.assertNotIn("Traceback", text)
                self.assertNotIn("KeyboardInterrupt", text)
                self.assertEqual(text.splitlines()[-1], "container-map: stopped", text)
                end = time.time() + 20
                while time.time() < end and (os.path.exists(served.live)
                                             or cli._port_busy(served.port)):
                    time.sleep(0.2)
                self.assertEqual(cli._port_busy(served.port), "", "the bridge went with it")
                self.assertFalse(os.path.exists(served.live))

    def test_a_stop_sent_to_the_cli_alone_takes_the_bridge_with_it(self):
        served = Served(self, self.tool, self.root, self.home)
        served.wait_up(self)
        os.kill(served.proc.pid, signal.SIGTERM)        # `kill PID`, not a group signal
        code, text = served.finish()
        self.assertEqual(code, 0, text)
        self.assertEqual(text.splitlines()[-1], "container-map: stopped", text)
        end = time.time() + 20
        while time.time() < end and cli._port_busy(served.port):
            time.sleep(0.2)
        self.assertEqual(cli._port_busy(served.port), "", "no orphaned bridge keeps the port")

    def test_the_bridge_takes_ctrl_c_even_when_it_was_started_with_it_ignored(self):
        port = free_port()
        env = dict(stranger_env(self.home), CONTAINER_ROOT=self.root, BRIDGE_PORT=str(port))
        live = os.path.join(self.root, ".container-map", "run", f"bridge-{port}.json")
        proc = subprocess.Popen(           # what a backgrounded job or the CLI's serve hands on
            [sys.executable, os.path.join(self.tool, "bridge.py")], env=env, cwd=self.tool,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(),
                                 proc.stderr.close()))
        end = time.time() + 30
        while time.time() < end and not os.path.exists(live):
            self.assertIsNone(proc.poll(), "the bridge exited early")
            time.sleep(0.1)
        self.assertTrue(os.path.exists(live))
        with open(f"/proc/{proc.pid}/status") as handle:
            ignored = int(next(l for l in handle if l.startswith("SigIgn:")).split()[1], 16)
        self.assertFalse(ignored & (1 << (signal.SIGINT - 1)),
                         "the runners and verify commands it starts must not inherit the ignore")
        proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(timeout=20), 0, proc.stderr.read())
        self.assertFalse(os.path.exists(live), "a clean stop removes the live file")

    def test_the_old_live_file_outlives_a_stop_and_the_next_start_refreshes_it(self):
        legacy = os.path.join(self.tool, ".bridge-live")
        with open(legacy, "w") as handle:
            json.dump({"version": 1, "pid": 2 ** 22 + 1, "port": 1, "agent_token": "old"}, handle)
        os.chmod(legacy, 0o600)
        tokens = []
        for boot in ("first", "second", "third"):
            served = Served(self, self.tool, self.root, self.home)
            served.wait_up(self)
            with open(served.live) as handle:
                tokens.append(json.load(handle)["agent_token"])
            with open(legacy) as handle:
                self.assertEqual(json.load(handle)["agent_token"], tokens[-1],
                                 f"the {boot} start refreshes the hooks' file")
            served.signal(signal.SIGTERM)               # a reboot, a service restart
            served.finish()
            self.assertTrue(os.path.exists(legacy), f"the {boot} stop leaves the old file alone")
            self.assertFalse(os.path.exists(served.live))
        self.assertEqual(len(set(tokens)), 3, "a new token on every boot")


    def test_a_stop_that_lands_while_the_bridge_is_still_starting_removes_the_live_file(self):
        """The live file is written before the ticker and the phone listener start. A SIGTERM in
        that gap used to meet the default action: the process died with its credentials file left
        behind. The driver slows the rest of the start down so the gap is wide."""
        port = free_port()
        live = os.path.join(self.root, ".container-map", "run", f"bridge-{port}.json")
        driver = ("import sys, time\nimport bridge\nreal = bridge.BridgeApp.start_ticker\n"
                  "def slow(self):\n    time.sleep(4)\n    return real(self)\n"
                  "bridge.BridgeApp.start_ticker = slow\nsys.exit(bridge.main([]))\n")
        proc = subprocess.Popen([sys.executable, "-c", driver], cwd=self.tool,
                                env=dict(stranger_env(self.home), CONTAINER_ROOT=self.root,
                                         BRIDGE_PORT=str(port)),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        def reap():
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
        self.addCleanup(reap)
        end = time.time() + 30
        while not os.path.exists(live):
            self.assertIsNone(proc.poll(), "the bridge exited before it wrote its file")
            self.assertLess(time.time(), end, "no live file")
            time.sleep(0.02)
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(20), 0, "a clean stop, not a kill by the signal")
        self.assertFalse(os.path.exists(live), "the credentials file went with it")


class Version(Fixture):
    def test_version_flag_prints_one_line_from_the_constant(self):
        for flag in ("--version", "-V"):
            r = run_cli([flag], stranger_env(self.home))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, f"container-map {cli.VERSION}\n")
            self.assertEqual(r.stderr, "")
        self.assertRegex(cli.VERSION, r"^\d+\.\d+\.\d+$")
        self.assertEqual(cli.MIN_PYTHON, (3, 10))

    def test_help_names_the_flag_and_doctor_prints_the_version(self):
        r = run_cli([], stranger_env(self.home))
        self.assertIn("container-map --version", r.stdout)
        r = run_cli(["doctor", self.root, "--port", str(free_port())], stranger_env(self.home))
        self.assertRegex(r.stdout, rf"(?m)^  version\s+{cli.VERSION}\b")

    def test_the_floor_message_follows_the_constant(self):
        with mock.patch.object(cli, "MIN_PYTHON", (3, 14)):
            self.assertEqual(cli.missing_pieces((3, 13, 0), which=lambda name: "/bin/" + name),
                             ["python 3.14+ (this is 3.13)"])


class Doctor(Fixture):
    def test_ready_machine(self):
        conf = os.path.join(self.tmp, "c.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1}, handle)
        r = run_cli(["doctor", self.root, "--config", conf, "--port", str(free_port())],
                    stranger_env(self.home))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for word in ("python", "git", "bash", "tmux", "systemd-run", "claude", "codex", "gemini",
                     "grok", "aider", conf, self.root, "port", "free", "ready."):
            self.assertIn(word, r.stdout)

    def test_missing_git_is_named(self):
        os.symlink(shutil.which("bash"), os.path.join(self.bin, "bash"))
        r = run_cli(["doctor"], stranger_env(self.home, path=self.bin))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("not ready: missing git", r.stdout)

    def test_missing_pieces(self):
        self.assertEqual(cli.missing_pieces((3, 9, 0), which=lambda name: None),
                         ["python 3.10+ (this is 3.9)", "git", "bash"])
        self.assertEqual(cli.missing_pieces((3, 12, 1), which=lambda name: "/bin/" + name), [])

    def doctor(self, args, env=None, **more):
        env = env or stranger_env(self.home)
        env.update(more)
        return run_cli(["doctor"] + args, env)

    def config_file(self, text):
        path = os.path.join(self.tmp, "c.json")
        with open(path, "w") as handle:
            handle.write(text if isinstance(text, str) else json.dumps(text))
        return path

    def test_a_root_that_does_not_exist_is_not_ready_and_is_not_created(self):
        gone = os.path.join(self.tmp, "gone", "deeper")
        conf = self.config_file({"version": 1, "work_root": gone})
        for label, args, extra in (("env", [], {"CONTAINER_ROOT": gone}),
                                   ("config", ["--config", conf], {}),
                                   ("argument", [gone], {})):
            r = self.doctor(args + ["--port", str(free_port())], **extra)
            self.assertEqual(r.returncode, 1, (label, r.stdout, r.stderr))
            self.assertIn(gone, r.stdout + r.stderr, label)
            self.assertIn("does not exist", r.stdout + r.stderr, label)
            self.assertNotIn("Traceback", r.stdout + r.stderr, label)
            self.assertNotIn("ready.", r.stdout, label)
            self.assertFalse(os.path.exists(os.path.dirname(gone)), f"{label}: doctor made it")

    def test_without_a_root_the_missing_default_is_named_not_assumed(self):
        tool = os.path.join(self.tmp, "tool")
        tool_copy(tool)                      # no container.json beside it, like a release
        r = subprocess.run([sys.executable, os.path.join(tool, "cli.py"), "doctor", "--port",
                            str(free_port())], capture_output=True, text=True,
                           env=stranger_env(self.home), timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(os.path.join(self.home, "work"), r.stdout)
        self.assertIn("does not exist", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, "work")))

    def test_a_malformed_config_is_not_ready_and_says_where(self):
        conf = self.config_file('{\n  "name": "x",\n  "theme": \n}\n')
        r = self.doctor([self.root, "--config", conf, "--port", str(free_port())])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(conf, r.stdout)
        self.assertRegex(r.stdout, r"line 4 column 1")
        self.assertIn("not ready", r.stdout)
        self.assertNotIn("ready.", r.stdout)
        conf = self.config_file("[1, 2]")
        r = self.doctor([self.root, "--config", conf, "--port", str(free_port())])
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("JSON object", r.stdout)

    def test_a_port_held_by_something_else_is_named(self):
        conf = self.config_file({"version": 1})
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            r = self.doctor([self.root, "--config", conf, "--port", str(port)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)       # the machine is fine
        self.assertIn("in use by something else", r.stdout)
        self.assertIn(f"note: port {port} is held by something else", r.stdout)
        self.assertIn("serve and install refuse it; pass --port N", r.stdout)
        self.assertIn("ready.", r.stdout)

    def test_a_port_held_by_this_maps_own_bridge_is_named_and_fine(self):
        conf = self.config_file({"version": 1})
        bridge = FakeBridge(self.root)
        self.addCleanup(bridge.close)
        bridge.write_live()
        r = self.doctor([self.root, "--config", conf, "--port", str(bridge.port)])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("this map's own bridge", r.stdout)
        self.assertIn(f"pid {os.getpid()}", r.stdout)
        self.assertIn(bridge.live, r.stdout)

    def test_a_custom_live_path_is_the_one_doctor_prints_and_recognises(self):
        conf = self.config_file({"version": 1})
        bridge = FakeBridge(self.root)
        self.addCleanup(bridge.close)
        custom = os.path.join(self.tmp, "elsewhere", "live.json")
        bridge.live = custom
        bridge.write_live()
        r = self.doctor([self.root, "--config", conf, "--port", str(bridge.port)],
                        BRIDGE_LIVE_PATH=custom)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(custom, r.stdout, "the same path the bridge and gamectl would use")
        self.assertIn("this map's own bridge", r.stdout)

    def test_no_agent_cli_says_so_and_points_at_the_demo(self):
        for exe in ("git", "bash"):
            os.symlink(shutil.which(exe), os.path.join(self.bin, exe))
        conf = self.config_file({"version": 1})
        r = self.doctor([self.root, "--config", conf, "--port", str(free_port())],
                        stranger_env(self.home, path=self.bin))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("no agent CLI found", r.stdout)
        self.assertIn("container-map demo", r.stdout)
        self.fake_agent("claude")
        r = self.doctor([self.root, "--config", conf, "--port", str(free_port())],
                        stranger_env(self.home, path=self.bin))
        self.assertNotIn("no agent CLI found", r.stdout)


class Init(Fixture):
    def written(self):
        with open(os.path.join(self.root, ".container-map", "container.json")) as handle:
            return handle.read()

    def test_writes_what_is_there(self):
        claude = self.fake_agent()
        r = run_cli(["init", self.root], stranger_env(self.home, path=self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.written()
        cfg = json.loads(text)
        self.assertEqual(cfg["name"], "my-work")
        self.assertEqual(cfg["work_root"], self.root)
        self.assertEqual([(p["name"], p["kind"]) for p in cfg["planets"]],
                         [("app", "code"), ("lib", "code"), ("notes", "knowledge"),
                          ("web", "code")])
        self.assertEqual(list(cfg["runners"]), ["claude"])
        self.assertEqual(cfg["runners"]["claude"]["cmd"][0], claude)
        self.assertEqual(cfg["wingmate"]["cmd"], [claude, "-p"])           # models carry --model
        self.assertEqual([m["name"] for m in cfg["wingmate"]["models"]][:3], ["haiku", "sonnet", "opus"])
        self.assertEqual(cfg["wingmate"]["default"], "haiku")
        self.assertEqual(cfg["tmux_session"], "")
        self.assertNotIn("~/", text, "the example's sample paths are not carried over")
        r = subprocess.run([sys.executable, os.path.join(HERE, "config.py"), "--root", self.root],
                           capture_output=True, text=True, env=stranger_env(self.home),
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        loaded = json.loads(r.stdout)
        self.assertEqual(loaded["_path"], os.path.join(self.root, ".container-map",
                                                       "container.json"))
        self.assertEqual([p["name"] for p in loaded["planets"]], ["app", "lib", "notes", "web"])
        self.assertEqual(loaded["wingmate"]["name"], "Scout")

    def test_the_install_guide_names_what_init_writes(self):
        self.fake_agent("claude")
        self.fake_agent("codex")
        r = run_cli(["init", self.root], stranger_env(self.home, path=self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(self.written())
        with open(os.path.join(HERE, "docs", "INSTALL.md"), encoding="utf-8") as handle:
            section = handle.read().split("## 3.", 1)[1].split("## 4.", 1)[0]
        for key in ("name", "work_root", "planets", "runners", "wingmate", "tmux_session", "theme",
                    "processes", "decision_scan", "decision_words", "detect_runners",
                    "agent_verify", "task_scope", "no_ai_trailers", "tick_seconds"):
            self.assertIn(key, cfg, f"init no longer writes {key}")
            self.assertIn(f"`{key}`", section, f"the guide does not name {key}")
        self.assertEqual(cfg["agent_verify"], False)
        self.assertEqual(cfg["runners"]["claude"]["default_model"], "sonnet")
        self.assertEqual([m["name"] for m in cfg["runners"]["claude"]["models"]],
                         ["sonnet", "haiku", "opus"])
        self.assertIn("sonnet (the default), haiku and opus", section)
        written = os.path.join(self.root, ".container-map", "container.json")
        self.assertEqual(os.stat(written).st_mode & 0o777, 0o600)
        self.assertIn("(mode 600)", section)

    def test_no_agents_found(self):
        r = run_cli(["init", self.root], stranger_env(self.home, path=self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads(self.written())
        self.assertEqual(cfg["runners"], {})
        self.assertEqual(cfg["wingmate"], {})
        self.assertIn("none found on PATH", r.stdout)

    def test_codex_or_gemini_without_claude(self):
        # each model then carries its own command and the base is empty: init must still finish
        for cli in ("codex", "gemini"):
            with self.subTest(cli=cli):
                for name in os.listdir(self.bin):
                    os.unlink(os.path.join(self.bin, name))
                side = os.path.join(self.root, ".container-map")
                if os.path.isdir(side):
                    shutil.rmtree(side)
                exe = self.fake_agent(cli)
                r = run_cli(["init", self.root], stranger_env(self.home, path=self.bin))
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertIn(f"wingmate: {json.loads(self.written())['wingmate']['name']} on {cli}", r.stdout)
                self.assertTrue(exe)

    def test_never_overwrites(self):
        side = os.path.join(self.root, ".container-map")
        os.makedirs(side)
        mine = '{"name": "MINE"}\n'
        with open(os.path.join(side, "container.json"), "w") as handle:
            handle.write(mine)
        r = run_cli(["init", self.root], stranger_env(self.home, path=self.bin))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.written(), mine)
        with open(os.path.join(side, "container.json.new")) as handle:
            self.assertEqual(json.load(handle)["name"], "my-work")
        self.assertIn("container.json.new", r.stdout)
        self.assertIn("not touched", r.stdout)

    def test_bad_arguments(self):
        env = stranger_env(self.home, path=self.bin)
        self.assertEqual(run_cli(["init"], env).returncode, 2)
        r = run_cli(["init", os.path.join(self.tmp, "nope")], env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not a directory", r.stderr)

    def test_a_folder_that_does_not_exist_is_named_and_made_only_on_request(self):
        env = stranger_env(self.home, path=self.bin)
        gone = os.path.join(self.tmp, "new-work")
        r = run_cli(["init", gone], env)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn(f"not a directory: {gone} (no such folder", r.stderr)
        self.assertIn("--create", r.stderr)
        self.assertFalse(os.path.exists(gone), "init never makes a folder unasked")
        r = run_cli(["init", gone, "--create"], env)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(gone, ".container-map", "container.json")) as handle:
            cfg = json.load(handle)
        self.assertEqual((cfg["name"], cfg["planets"], cfg["work_root"]), ("new-work", [], gone))
        file_path = os.path.join(self.tmp, "a-file")
        open(file_path, "w").close()
        r = run_cli(["init", file_path, "--create"], env)
        self.assertEqual(r.returncode, 1)
        self.assertIn(f"not a directory: {file_path}", r.stderr)

    def make_repo(self, path):
        os.makedirs(path)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True,
                       capture_output=True)
        testenv.hermetic_repo(path)
        with open(os.path.join(path, "README"), "w") as handle:
            handle.write("x\n")
        return path

    def test_in_a_repo_the_map_folder_does_not_show_as_untracked(self):
        repo = self.make_repo(os.path.join(self.tmp, "repo"))
        inner = os.path.join(repo, "pkg")
        os.makedirs(inner)
        for target in (repo, inner, repo):          # the second init of a folder changes nothing
            r = run_cli(["init", target], stranger_env(self.home))
            self.assertEqual(r.returncode, 0, r.stderr)
        status = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo,
                                capture_output=True, text=True, check=True).stdout
        self.assertNotIn(".container-map", status)
        with open(os.path.join(repo, ".git", "info", "exclude")) as handle:
            self.assertEqual(handle.read().splitlines().count(".container-map/"), 1)
        # a folder that is no repo gets no git file written
        r = run_cli(["init", self.root], stranger_env(self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, ".git", "info")))


class InstallHelpers:
    """The recorder that stands in for systemctl, shared by the install tests."""

    def setUp(self):
        super().setUp()
        boom = mock.Mock(side_effect=AssertionError("a real service manager was called"))
        patcher = mock.patch.object(cli, "_run", boom)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []
        self.answer = (0, "")

    def recorder(self, argv):
        self.calls.append(list(argv))
        return self.answer

    def quiet(self, fn, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fn(*args, **kwargs)
        return rc, out.getvalue(), err.getvalue()

    def unit(self):
        return os.path.join(self.home, ".config", "systemd", "user", "container-map.service")


class InstallLive(InstallHelpers, Fixture):
    """install against a real port and a stand-in bridge: what it checks before it writes
    anything and what it waits for after it starts the service."""

    WHICH = staticmethod(lambda name: "/usr/bin/" + name)

    def install(self, port, run=None):
        """(exit code, stdout, stderr); a command that stops with _die has its code returned."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.cmd_install([self.root, "--port", str(port)], platform="linux",
                                     home=self.home, run=run or self.recorder, which=self.WHICH)
            except SystemExit as exc:
                rc = exc.code
        return rc, out.getvalue(), err.getvalue()

    def test_a_port_held_by_something_else_is_refused_before_anything_is_written(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            rc, out, err = self.install(port)
        self.assertEqual(rc, 1, out + err)
        self.assertIn(f"port {port}", err)
        self.assertIn("something else", err)
        self.assertIn("--port", err)
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.unit()))
        self.assertNotIn("installed", out)

    def test_it_waits_for_the_new_bridge_before_it_says_started(self):
        import threading
        port, started = free_port(), []

        def service_manager(argv):
            self.calls.append(list(argv))
            if argv[-2:] == ["restart", "container-map.service"]:
                def come_up():
                    time.sleep(0.5)                  # a bridge takes a moment to bind
                    bridge = FakeBridge(self.root, port)
                    self.addCleanup(bridge.close)
                    bridge.write_live()
                    started.append(time.time())
                threading.Thread(target=come_up, daemon=True).start()
            return 0, ""

        rc, out, err = self.install(port, run=service_manager)
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(started, "install returned before the bridge was up")
        self.assertIn("installed and started", out)
        self.assertIn(f"http://127.0.0.1:{port}/", out)

    def test_its_own_bridge_on_the_port_is_a_restart_not_a_refusal(self):
        bridge = FakeBridge(self.root)
        self.addCleanup(bridge.close)
        bridge.write_live()

        def service_manager(argv):
            self.calls.append(list(argv))
            if argv[-2:] == ["restart", "container-map.service"]:
                bridge.write_live()                  # the restarted service: a new boot id
            return 0, ""

        rc, out, err = self.install(bridge.port, run=service_manager)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("installed and started", out)

    def test_the_old_bridges_live_file_is_not_the_new_bridge(self):
        bridge = FakeBridge(self.root)
        self.addCleanup(bridge.close)
        bridge.write_live()
        with mock.patch.object(cli, "WAIT_SECONDS", 1.0):
            rc, out, err = self.install(bridge.port)         # the service never restarts it
        self.assertEqual(rc, 1, out + err)
        self.assertNotIn("installed and started", out)
        self.assertIn("boot-1", err + out, "it says the file is still the old bridge's")

    def test_no_bridge_in_time_prints_the_service_log_and_fails(self):
        port = free_port()

        def service_manager(argv):
            self.calls.append(list(argv))
            if argv[0] == "journalctl":
                return 0, "Oct 10 python3[42]: OSError: Address already in use\nOct 10 exited"
            return 0, ""

        with mock.patch.object(cli, "WAIT_SECONDS", 0.6):
            rc, out, err = self.install(port, run=service_manager)
        self.assertEqual(rc, 1, out + err)
        self.assertNotIn("installed and started", out)
        self.assertIn(f"no bridge came up on port {port}", err)
        self.assertIn("OSError: Address already in use", err)
        self.assertIn("may still come up", err, "a big root renders before the bridge starts")
        self.assertTrue(any(call[0] == "journalctl" for call in self.calls), self.calls)
        self.assertTrue(os.path.exists(self.unit()), "the unit stays; uninstall is the way out")
        self.assertIn("uninstall", err)

    def test_uninstall_that_cannot_stop_the_service_keeps_the_unit(self):
        os.makedirs(os.path.dirname(self.unit()))
        with open(self.unit(), "w") as handle:
            handle.write("[Unit]\n")
        self.answer = (1, "Failed to connect to bus: No medium found")
        rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                  run=self.recorder, which=self.WHICH)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("Failed to connect to bus", err)
        self.assertTrue(os.path.exists(self.unit()))
        self.assertEqual([f for f in os.listdir(os.path.dirname(self.unit()))
                          if ".retired-" in f], [])
        rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                  run=self.recorder, which=self.WHICH)
        self.assertEqual(rc, 1, "a retry still finds it: " + out + err)
        self.assertNotIn("nothing installed", out)
        self.answer = (0, "")
        rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                  run=self.recorder, which=self.WHICH)
        self.assertEqual(rc, 0, out + err)
        self.assertFalse(os.path.exists(self.unit()))

    def unit_file(self):
        os.makedirs(os.path.dirname(self.unit()))
        with open(self.unit(), "w") as handle:
            handle.write("[Unit]\n")

    def test_a_service_that_was_never_loaded_counts_as_stopped(self):
        for said in ("Unit container-map.service not loaded.",
                     "Failed to disable unit: Unit file container-map.service does not exist."):
            with self.subTest(said):
                self.unit_file()
                self.answer = (1, said)
                rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                          run=self.recorder, which=self.WHICH)
                self.assertEqual(rc, 0, out + err)
                self.assertFalse(os.path.exists(self.unit()))
                shutil.rmtree(os.path.dirname(self.unit()))

    def test_force_retires_the_unit_when_the_bus_cannot_be_reached(self):
        self.unit_file()
        self.answer = (1, "Failed to connect to bus: No medium found")
        rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                  run=self.recorder, which=self.WHICH)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("uninstall --force", err, "the way out is named")
        self.assertIn(self.unit(), err, "and so is the file")
        self.assertTrue(os.path.exists(self.unit()))
        rc, out, err = self.quiet(cli.cmd_uninstall, ["--force"], platform="linux",
                                  home=self.home, run=self.recorder, which=self.WHICH)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("Failed to connect to bus", err, "the failure is still shown")
        self.assertIn("retired", out)
        self.assertFalse(os.path.exists(self.unit()))
        retired = [f for f in os.listdir(os.path.dirname(self.unit())) if ".retired-" in f]
        self.assertEqual(len(retired), 1, "retired, not deleted")

    def test_uninstall_without_a_service_manager_keeps_the_unit_too(self):
        os.makedirs(os.path.dirname(self.unit()))
        with open(self.unit(), "w") as handle:
            handle.write("[Unit]\n")
        rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                  run=self.recorder, which=lambda name: None)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("systemctl not found", err)
        self.assertTrue(os.path.exists(self.unit()))


class Install(InstallHelpers, Fixture):
    def setUp(self):
        super().setUp()
        # these tests never start a service: the port check and the wait for the bridge are
        # InstallLive's business, and 7428 may well be in use on the machine running them
        for name, value in (("wait_for_bridge", (True, "")), ("_port_busy", "")):
            patcher = mock.patch.object(cli, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_dry_run_prints_and_writes_nothing(self):
        r = run_cli(["install", self.root, "--dry-run"], stranger_env(self.home))
        if sys.platform.startswith("linux"):
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(self.unit(), r.stdout)
            self.assertIn("[Service]", r.stdout)
            self.assertIn(f'ExecStart="{sys.executable}" "{CLI}" "serve" "{self.root}" "--port" '
                          f'"7428"', r.stdout)
            self.assertIn("WantedBy=default.target", r.stdout)
        elif sys.platform == "darwin":
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("local.container-map.plist", r.stdout)
        else:
            self.assertEqual(r.returncode, 2)
        self.assertEqual(os.listdir(self.home), [], "a dry run writes nothing")

    def test_unknown_option_is_refused(self):
        r = run_cli(["install", self.root, "--dryrun"], stranger_env(self.home))
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown option --dryrun", r.stderr)
        self.assertEqual(os.listdir(self.home), [])

    def test_launchd_plan(self):
        plan = cli.service_plan(self.root, 7500, platform="darwin", home=self.home,
                                path_env="/opt/bin:/usr/bin", uid=501)
        plist = os.path.join(self.home, "Library", "LaunchAgents", "local.container-map.plist")
        self.assertEqual(plan["file"], plist)
        data = plistlib.loads(plan["text"].encode("utf-8"))
        self.assertEqual(data["Label"], "local.container-map")
        self.assertEqual(data["ProgramArguments"],
                         [sys.executable, CLI, "serve", self.root, "--port", "7500"])
        self.assertEqual(data["EnvironmentVariables"], {"PATH": "/opt/bin:/usr/bin"})
        self.assertTrue(data["RunAtLoad"])
        self.assertIn((["launchctl", "bootstrap", "gui/501", plist], True), plan["start"])

    def test_systemd_quoting(self):
        odd = os.path.join(self.tmp, 'a b%c$d"e')
        os.makedirs(odd)
        plan = cli.service_plan(odd, platform="linux", home=self.home, path_env="/usr/bin:/bin")
        line = [x for x in plan["text"].splitlines() if x.startswith("ExecStart=")][0]
        self.assertIn('a b%%c$$d\\"e"', line)
        self.assertIn('Environment="PATH=/usr/bin:/bin"', plan["text"])

    def verify_unit(self, text, name="container-map.service"):
        """(returncode, output) of `systemd-analyze --user verify` on a unit with this text."""
        folder = tempfile.mkdtemp(prefix="unit-", dir=self.tmp)
        path = os.path.join(folder, name)
        with open(path, "w") as handle:
            handle.write(text)
        r = subprocess.run(["systemd-analyze", "--user", "verify", path], capture_output=True,
                           text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_systemd_accepts_the_quoting(self):
        # systemd-analyze --user needs a user systemd manager (XDG_RUNTIME_DIR and its bus): a
        # container, a CI box or a WSL without systemd has the tool and no manager, and then even a
        # valid unit fails. Probe with one that must pass; skip when it does not.
        if not (sys.platform.startswith("linux") and shutil.which("systemd-analyze")):
            self.skipTest("no systemd-analyze on this machine")
        rc, output = self.verify_unit('[Service]\nExecStart="/bin/true"\n', "probe.service")
        if rc != 0:
            self.skipTest("no working user systemd manager here: " + output.strip()[:120])
        odd = os.path.join(self.tmp, 'a b%c$d"e')
        os.makedirs(odd)
        plan = cli.service_plan(odd, platform="linux", home=self.home, path_env="/usr/bin:/bin")
        rc, output = self.verify_unit(plan["text"])
        self.assertEqual(rc, 0, output)

    def test_install_then_uninstall_never_deletes(self):
        which = lambda name: "/usr/bin/" + name  # noqa: E731
        rc, out, err = self.quiet(cli.cmd_install, [self.root], platform="linux", home=self.home,
                                  run=self.recorder, which=which)
        self.assertEqual(rc, 0, err)
        with open(self.unit()) as handle:
            first = handle.read()
        self.assertIn('"--port" "7428"', first)
        self.assertEqual(self.calls, [["systemctl", "--user", "daemon-reload"],
                                      ["systemctl", "--user", "enable", "container-map.service"],
                                      ["systemctl", "--user", "restart", "container-map.service"]])
        rc, out, err = self.quiet(cli.cmd_install, [self.root, "--port", "7501"],
                                  platform="linux", home=self.home, run=self.recorder,
                                  which=which)
        self.assertEqual(rc, 0, err)
        folder = os.path.dirname(self.unit())
        kept = [f for f in os.listdir(folder) if ".retired-" in f]
        self.assertEqual(len(kept), 1, "a changed unit keeps the old one beside it")
        with open(os.path.join(folder, kept[0])) as handle:
            self.assertEqual(handle.read(), first)
        with open(self.unit()) as handle:
            second = handle.read()
        self.assertIn('"--port" "7501"', second)
        self.calls = []
        rc, out, err = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                  run=self.recorder, which=which)
        self.assertEqual(rc, 0, err)
        self.assertFalse(os.path.exists(self.unit()))
        self.assertEqual(self.calls[0], ["systemctl", "--user", "disable", "--now",
                                         "container-map.service"])
        retired = sorted(f for f in os.listdir(folder) if ".retired-" in f)
        self.assertEqual(len(retired), 2, retired)
        texts = []
        for name in retired:
            with open(os.path.join(folder, name)) as handle:
                texts.append(handle.read())
        self.assertEqual(sorted(texts), sorted([first, second]), "both units kept, none lost")

    def test_uninstall_dry_run_and_nothing_installed(self):
        rc, out, _ = self.quiet(cli.cmd_uninstall, [], platform="linux", home=self.home,
                                run=self.recorder)
        self.assertEqual(rc, 0)
        self.assertIn("nothing installed", out)
        os.makedirs(os.path.dirname(self.unit()))
        with open(self.unit(), "w") as handle:
            handle.write("[Unit]\n")
        rc, out, _ = self.quiet(cli.cmd_uninstall, ["--dry-run"], platform="linux",
                                home=self.home, run=self.recorder)
        self.assertEqual(rc, 0)
        self.assertIn("would run systemctl --user disable --now", out)
        self.assertTrue(os.path.exists(self.unit()))
        self.assertEqual(self.calls, [])

    def test_failed_start_is_reported(self):
        self.answer = (1, "Failed to connect to bus")
        rc, out, err = self.quiet(cli.cmd_install, [self.root], platform="linux", home=self.home,
                                  run=self.recorder, which=lambda name: "/usr/bin/" + name)
        self.assertEqual(rc, 1)
        self.assertIn("daemon-reload", err)
        self.assertIn("Failed to connect to bus", err)
        self.assertIn("serve", err)

    def test_other_platforms_are_told_plainly(self):
        with self.assertRaises(SystemExit) as caught:
            self.quiet(cli.cmd_install, [self.root], platform="win32", home=self.home,
                       run=self.recorder)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(self.home), [])


class Scripts(Fixture):
    """world.sh and demo.sh run the Python the CLI runs on (CM_PYTHON), never a bare python3: the
    CLI may be in a venv, and the PATH the scripts see may have no python3 at all."""

    def setUp(self):
        super().setUp()
        self.fakebin = os.path.join(self.tmp, "fakebin")          # every tool but python3
        os.makedirs(self.fakebin)
        for exe in ("bash", "dirname", "sleep", "grep", "cat", "mkdir"):
            os.symlink(shutil.which(exe), os.path.join(self.fakebin, exe))
        self.log = os.path.join(self.tmp, "calls.log")

    def fake_python(self, body):
        path = os.path.join(self.tmp, "fake-python")
        with open(path, "w") as handle:
            handle.write('#!/bin/sh\necho "$*" >> "$FAKE_LOG"\n' + body + "\n")
        os.chmod(path, 0o755)
        return path

    def calls(self):
        try:
            with open(self.log) as handle:
                return handle.read().splitlines()
        except OSError:
            return []

    def test_demo_sh_starts_the_demo_on_cm_python(self):
        folder = os.path.join(self.tmp, "d")
        os.makedirs(folder)
        shutil.copy(os.path.join(HERE, "demo.sh"), folder)
        fake = self.fake_python("exit 0")
        env = {"PATH": self.fakebin, "HOME": self.home, "CM_PYTHON": fake, "FAKE_LOG": self.log}
        r = subprocess.run([shutil.which("bash"), os.path.join(folder, "demo.sh"), "--no-serve"],
                           env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.calls(), ["cli.py demo --no-serve"],
                         "the demo runs on the Python the CLI named, not a bare python3")

    def test_world_sh_runs_the_bridge_the_probe_and_the_renderer_on_cm_python(self):
        folder = os.path.join(self.tmp, "w")
        os.makedirs(folder)
        shutil.copy(os.path.join(HERE, "world.sh"), folder)
        live = os.path.join(self.tmp, "fake-live.json")
        fake = self.fake_python('case "$1" in\n'
                                '  bridge.py) echo "{}" > "$FAKE_LIVE"; exec sleep 30 ;;\n'
                                '  -c) echo "$FAKE_LIVE" ;;\n'
                                '  -) cat > /dev/null; test -f "$2" ;;\n'
                                'esac')
        env = {"PATH": self.fakebin, "HOME": self.home, "CM_PYTHON": fake, "FAKE_LOG": self.log,
               "FAKE_LIVE": live, "CONTAINER_RENDER_SECONDS": "1"}
        proc = subprocess.Popen([shutil.which("bash"), os.path.join(folder, "world.sh")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            end = time.time() + 20
            while time.time() < end and proc.poll() is None and not any(
                    "render-game.py --loop-render" in line for line in self.calls()):
                time.sleep(0.1)
            exited = proc.poll()
        finally:
            if proc.poll() is None:
                proc.terminate()
            out, err = proc.communicate(timeout=20)
        calls = self.calls()
        self.assertIsNone(exited, f"world.sh exited early: {out}{err}")
        self.assertIn("bridge.py", calls)
        self.assertTrue(any(line.startswith(f"- {live} ") for line in calls),
                        "the readiness probe polls the file the default path helper named")
        self.assertIn("render-game.py --loop-render", calls)

    def test_world_sh_stops_a_render_in_flight_with_the_rest(self):
        folder = os.path.join(self.tmp, "w")
        os.makedirs(folder)
        shutil.copy(os.path.join(HERE, "world.sh"), folder)
        os.symlink(shutil.which("pgrep"), os.path.join(self.fakebin, "pgrep"))
        pidfile = os.path.join(self.tmp, "render.pid")
        live = os.path.join(self.tmp, "fake-live.json")
        fake = self.fake_python('case "$1" in\n'
                                '  bridge.py) echo "{}" > "$FAKE_LIVE"; exec sleep 60 ;;\n'
                                '  -c) echo "$FAKE_LIVE" ;;\n'
                                '  -) cat > /dev/null; test -f "$2" ;;\n'
                                '  render-game.py) echo $$ > "$FAKE_PID"; exec sleep 60 ;;\n'
                                'esac')
        env = {"PATH": self.fakebin, "HOME": self.home, "CM_PYTHON": fake, "FAKE_LOG": self.log,
               "FAKE_LIVE": live, "FAKE_PID": pidfile}
        proc = subprocess.Popen([shutil.which("bash"), os.path.join(folder, "world.sh")], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        render = 0
        try:
            end = time.time() + 20
            while time.time() < end and not render:
                try:
                    with open(pidfile) as handle:
                        render = int(handle.read().strip() or 0)
                except (OSError, ValueError):
                    time.sleep(0.1)
            self.assertTrue(render, "the render never started")
            proc.terminate()                    # what the CLI sends it on Ctrl-C
            proc.communicate(timeout=20)
            end = time.time() + 10
            while time.time() < end:
                try:
                    os.kill(render, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            with self.assertRaises(ProcessLookupError):
                os.kill(render, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=20)
            if render:
                try:
                    os.kill(render, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_the_cli_hands_its_python_to_world_sh_for_serve_and_for_a_served_demo(self):
        seen = []

        def record(argv, env=None, cwd=None):
            seen.append((os.path.basename(argv[-1]), (env or {}).get("CM_PYTHON")))
            return 0

        class Gone:                     # a world.sh that exits at once: the demo reports and returns
            returncode = 3

            def poll(self):
                return self.returncode

        def popen(argv, env=None, **kw):
            seen.append((os.path.basename(argv[-1]), (env or {}).get("CM_PYTHON")))
            return Gone()

        dest = os.path.join(self.tmp, "demo-ws")
        os.makedirs(dest)
        with mock.patch.object(cli, "_call", record), mock.patch.object(cli, "_page_path",
                                                                       lambda env: ""), \
                mock.patch.object(cli, "_config_locked", lambda env: False), \
                mock.patch.object(cli.subprocess, "Popen", popen), \
                contextlib.redirect_stdout(io.StringIO()), \
                mock.patch.dict(os.environ, {"HOME": self.home}):
            self.assertEqual(cli._demo_serve(dest, free_port(), lambda text: None), 3)
            cli.cmd_serve([self.root, "--port", str(free_port())])
        self.assertEqual([name for name, _ in seen],
                         ["render-game.py", "world.sh", "render-game.py", "world.sh"])
        self.assertEqual({py for _, py in seen}, {sys.executable})


class LivePath(Fixture):
    """The bridge's credentials file: per root and per port, found the same way by the bridge,
    gamectl and the CLI, never served, and the old beside-the-code file kept for its readers."""

    def bridge_default(self, argv, **env_more):
        code = ("import sys; sys.path.insert(0, %r); import bridge; "
                "print(bridge.parse_args(%r).live_file)" % (HERE, argv))
        env = stranger_env(self.home)
        env.update(env_more)
        r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                           cwd=self.tmp, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def live_file(self, port, pid=2 ** 22 + 1):
        path = os.path.join(self.root, ".container-map", "run", f"bridge-{port}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump({"version": 1, "pid": pid, "port": port, "agent_token": "t"}, handle)
        os.chmod(path, 0o600)
        return path

    def test_the_bridge_defaults_to_a_file_per_root_and_port(self):
        run = os.path.join(self.root, ".container-map", "run")
        self.assertEqual(self.bridge_default(["--port", "7611"], CONTAINER_ROOT=self.root),
                         os.path.join(run, "bridge-7611.json"))
        self.assertEqual(self.bridge_default([], CONTAINER_ROOT=self.root, BRIDGE_PORT="7612"),
                         os.path.join(run, "bridge-7612.json"))
        other = os.path.join(self.tmp, "other-work")
        os.makedirs(other)
        self.assertEqual(self.bridge_default(["--port", "7611"], CONTAINER_ROOT=other),
                         os.path.join(other, ".container-map", "run", "bridge-7611.json"))
        elsewhere = os.path.join(self.tmp, "elsewhere.json")
        self.assertEqual(self.bridge_default(["--port", "7611"], CONTAINER_ROOT=self.root,
                                             BRIDGE_LIVE_PATH=elsewhere), elsewhere)
        self.assertEqual(self.bridge_default(["--port", "7611", "--live-file", elsewhere],
                                             CONTAINER_ROOT=self.root), elsewhere)

    def test_a_root_that_is_not_there_keeps_the_old_place_beside_the_code(self):
        gone = os.path.join(self.tmp, "gone")
        self.assertEqual(self.bridge_default(["--port", "7611"], CONTAINER_ROOT=gone),
                         os.path.join(HERE, ".bridge-live"))
        self.assertFalse(os.path.exists(gone))

    def test_gamectl_and_the_cli_resolve_the_same_file(self):
        import argparse
        import config
        import gamectl
        first, second = self.live_file(7611), self.live_file(7612)
        self.assertEqual(config.live_path(self.root, 7612), second)

        def pick(root="", port=0, environ=None, cwd=None):
            args = argparse.Namespace(live_file=None, root=root, port=port)
            return gamectl.live_file(args, environ={} if environ is None else environ,
                                     cwd=cwd or self.tmp)

        self.assertEqual(pick(self.root, 7612), second)
        self.assertEqual(pick(environ={"BRIDGE_PORT": "7611"}, cwd=os.path.join(self.root, "app")),
                         first)
        self.assertEqual(pick(environ={"CONTAINER_ROOT": self.root, "BRIDGE_PORT": "7612"}), second)
        elsewhere = os.path.join(self.tmp, "x.json")
        self.assertEqual(pick(self.root, 7612, environ={"BRIDGE_LIVE_PATH": elsewhere}), elsewhere)
        with self.assertRaises(RuntimeError) as caught:         # two maps, nothing says which
            pick(cwd=os.path.join(self.root, "app"))
        self.assertIn("--port", str(caught.exception))
        self.live_file(7612, pid=os.getpid())                   # only one is running
        self.assertEqual(pick(cwd=os.path.join(self.root, "app")), second)

    def test_the_old_file_is_refreshed_only_when_it_is_not_another_maps(self):
        import config
        legacy = os.path.join(self.tmp, "legacy-live")
        primary = os.path.join(self.tmp, "run", "bridge-7611.json")

        def write(pid, port):
            with open(legacy, "w") as handle:
                json.dump({"version": 1, "pid": pid, "port": port}, handle)

        with mock.patch.object(config, "LEGACY_LIVE", legacy):
            self.assertEqual(config.legacy_mirror(primary, 7611), "", "no old file, none made")
            write(2 ** 22 + 1, 7428)
            self.assertEqual(config.legacy_mirror(primary, 7611), legacy, "its map is gone")
            write(os.getpid(), 7428)
            self.assertEqual(config.legacy_mirror(primary, 7611), "", "another running map's")
            self.assertEqual(config.legacy_mirror(primary, 7428), legacy, "the same port's")
            self.assertEqual(config.legacy_mirror(legacy, 7428), "", "never itself")
            with open(legacy, "w") as handle:
                handle.write("not json")
            self.assertEqual(config.legacy_mirror(primary, 7611), legacy, "a broken file is stale")

    def test_the_bridge_writes_the_mirrors_keeps_them_at_a_stop_and_never_serves_them(self):
        import bridge
        app = bridge.BridgeApp(
            seats_path="", game_path=os.path.join(self.tmp, "game.html"), renderer_path="unused",
            live_path=os.path.join(self.root, ".container-map", "run", "bridge-7611.json"),
            log_path=os.path.join(self.tmp, "audit.log"), file_roots=(self.root,),
            tasks_dir=os.path.join(self.tmp, "tasks"), task_runners={}, mate_spec={},
            tick_seconds=0)
        mirror = os.path.join(self.tmp, "legacy-live")
        app.live_mirrors = (mirror,)
        app.port = 7611
        app.write_live()
        for path in (app.live_path, mirror):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600, path)
            with open(path) as handle:
                self.assertEqual(json.load(handle)["boot_id"], app.boot_id)
        self.assertEqual(os.stat(os.path.dirname(app.live_path)).st_mode & 0o777, 0o700)
        with self.assertRaises(bridge.BridgeError) as caught:
            app._checked_file(app.live_path)            # it sits inside a root the preview reads
        self.assertEqual(caught.exception.status, 403)
        page = os.path.join(self.root, ".container-map", "game.html")
        with open(page, "w") as handle:
            handle.write("<html></html>")
        self.assertEqual(app._checked_file(page), os.path.realpath(page))
        app.remove_live()                  # its own boot id is in both files
        self.assertFalse(os.path.exists(app.live_path))
        self.assertTrue(os.path.exists(mirror), "a stop must not end the mirroring: it only "
                                                "follows a file that exists")
        with open(mirror) as handle:
            self.assertEqual(json.load(handle)["boot_id"], app.boot_id)     # stale, pid dead soon
        app.write_live()                   # the next boot finds it and refreshes it
        with open(mirror) as handle:
            self.assertEqual(json.load(handle)["boot_id"], app.boot_id)

    def bridge_app(self, live):
        import bridge
        return bridge.BridgeApp(
            seats_path="", game_path=os.path.join(self.tmp, "game.html"), renderer_path="unused",
            live_path=live, log_path=os.path.join(self.tmp, "audit.log"),
            file_roots=(self.root,), tasks_dir=os.path.join(self.tmp, "tasks"), task_runners={},
            mate_spec={}, tick_seconds=0)

    def test_a_root_that_cannot_take_the_file_falls_back_instead_of_crashing(self):
        if os.geteuid() == 0:
            self.skipTest("root writes anywhere")
        import config
        ro = os.path.join(self.tmp, "ro-work")
        os.makedirs(ro)
        os.chmod(ro, 0o555)
        self.addCleanup(lambda: os.path.exists(ro) and os.chmod(ro, 0o755))
        # beside the code, where it always went, when that can be written
        self.assertEqual(self.bridge_default(["--port", "7611"], CONTAINER_ROOT=ro),
                         os.path.join(HERE, ".bridge-live"))
        # neither: a per-user runtime folder, and the bridge really starts there
        tool = os.path.join(self.tmp, "ro-tool")
        os.makedirs(tool)
        os.chmod(tool, 0o555)
        self.addCleanup(lambda: os.path.exists(tool) and os.chmod(tool, 0o755))
        runtime = os.path.join(self.tmp, "runtime")
        with mock.patch.object(config, "HERE", tool), \
                mock.patch.object(config, "LEGACY_LIVE", os.path.join(tool, ".bridge-live")), \
                mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": runtime}):
            path = config.default_live_path({"work_root": ro}, 7611)
            self.assertEqual(path, os.path.join(runtime, "container-map", "bridge-7611.json"))
            app = self.bridge_app(path)
            app.port = 7611
            app.write_live()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(config.resolve_live(ro, 7611, environ={}), path)
            self.assertEqual(config.resolve_live(ro, environ={}), path,
                             "no port: the one running bridge there is found")
        self.assertEqual(os.listdir(ro), [], "nothing was made in the read-only root")

    def git_repo(self, path):
        os.makedirs(path)
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "T"),
                     ("config", "user.email", "t@example.invalid"),
                     ("config", "commit.gpgsign", "false"), ("config", "tag.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
        with open(os.path.join(path, "README"), "w") as handle:
            handle.write("repo\n")
        for args in (("add", "-A"), ("commit", "-q", "-m", "init")):
            subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
        return path

    def untracked(self, repo):
        return subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo,
                              capture_output=True, text=True, check=True).stdout

    def test_the_token_file_stays_out_of_the_repo_even_when_the_map_folder_is_older(self):
        repo = self.git_repo(os.path.join(self.tmp, "repo"))
        side = os.path.join(repo, ".container-map")
        os.makedirs(side)                    # made by an earlier version: no exclude was written
        with open(os.path.join(side, "container.json"), "w") as handle:
            handle.write("{}\n")
        subprocess.run(["git", "add", "-f", ".container-map/container.json"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "config"], cwd=repo, check=True,
                       capture_output=True)
        app = self.bridge_app(os.path.join(side, "run", "bridge-7611.json"))
        app.port = 7611
        app.write_live()
        self.assertTrue(os.path.exists(app.live_path))
        self.assertEqual(self.untracked(repo), "", "no token file for `git add .` to take")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=repo, check=True,
                                capture_output=True, text=True).stdout
        self.assertEqual(staged, "")
        # a fresh .container-map made by the bridge is hidden from the repo too
        other = self.git_repo(os.path.join(self.tmp, "repo2"))
        app = self.bridge_app(os.path.join(other, ".container-map", "run", "bridge-7611.json"))
        app.port = 7611
        app.write_live()
        self.assertEqual(self.untracked(other), "")

    def test_a_map_folder_made_by_the_history_or_the_cron_cache_is_hidden_too(self):
        import districts
        import history
        repo = self.git_repo(os.path.join(self.tmp, "repo"))
        history.record(os.path.join(repo, ".container-map", "history.jsonl"), 1_700_000_000,
                       {"app": [1, 2, 0, 0, 0]})
        self.assertTrue(os.path.exists(os.path.join(repo, ".container-map", "history.jsonl")))
        self.assertEqual(self.untracked(repo), "")
        other = self.git_repo(os.path.join(self.tmp, "repo2"))
        districts._state_dir(os.path.join(other, ".container-map"))
        districts._state_dir(os.path.join(other, ".container-map", "render-cache"), 0o700)
        self.assertEqual(self.untracked(other), "")


class ThreadMap(Fixture):
    """`thread` with no --root and no --config: the map is the nearest .container-map/container.json
    above the target (new) or above the folder you are in (everything else)."""

    def setUp(self):
        super().setUp()
        self.agent = os.path.join(self.bin, "writer")
        with open(self.agent, "w") as handle:
            handle.write("#!/bin/sh\ncat > /dev/null\necho made-by-the-agent > note.txt\n")
        os.chmod(self.agent, 0o755)
        self.env = stranger_env(self.home)        # no CONTAINER_* variables, nothing in HOME
        self.ws = os.path.join(self.tmp, "code")
        self.notes = os.path.join(self.ws, "notes")
        os.makedirs(self.notes)
        self.conf = self.map_config(self.ws)

    def map_config(self, root, **more):
        path = os.path.join(root, ".container-map", "container.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cfg = {"version": 1, "work_root": root, "detect_runners": False, "task_scope": False,
               "tmux_session": "none", "runners": {"writer": {"cmd": [self.agent],
                                                              "max_minutes": 1}}}
        cfg.update(more)
        with open(path, "w") as handle:
            json.dump(cfg, handle)
        return path

    def thread(self, *args, cwd=None, env=None):
        return run_cli(["thread", *args], env or self.env, cwd=cwd or self.tmp)

    def threads(self, **kw):
        r = self.thread("list", "--json", **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)["threads"]

    def test_the_target_and_the_current_folder_find_the_map_that_holds_them(self):
        r = self.thread("new", self.notes, "write a note", "--agent", "writer", "--wait")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("review", r.stdout)
        self.assertTrue(os.path.isdir(os.path.join(self.ws, ".container-map", "tasks")))
        found = self.threads(cwd=self.notes)
        self.assertEqual(len(found), 1)
        r = self.thread("show", found[0]["id"], cwd=os.path.join(self.notes))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # a file in the folder walks up the same way
        deep = os.path.join(self.notes, "a", "b")
        os.makedirs(deep)
        self.assertEqual([t["id"] for t in self.threads(cwd=deep)], [found[0]["id"]])

    def test_container_config_root_and_config_win_over_the_search(self):
        r = self.thread("new", self.notes, "write a note", "--agent", "writer", "--wait")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        elsewhere = os.path.join(self.tmp, "elsewhere")
        os.makedirs(elsewhere)
        other = self.map_config(elsewhere)
        env = dict(self.env, CONTAINER_CONFIG=other)
        self.assertEqual(self.threads(cwd=self.notes, env=env), [])
        r = self.thread("list", "--json", "--root", elsewhere, cwd=self.notes)
        self.assertEqual(json.loads(r.stdout)["threads"], [])
        r = self.thread("list", "--json", "--config", other, cwd=self.notes)
        self.assertEqual(json.loads(r.stdout)["threads"], [])
        self.assertEqual(len(self.threads(cwd=self.notes)), 1)

    def test_a_map_served_without_init_is_found_by_its_tasks_folder(self):
        zero = os.path.join(self.tmp, "zero")           # as `serve ZERO` leaves it: no container.json
        notes = os.path.join(zero, "notes")
        os.makedirs(notes)
        conf = os.path.join(self.tmp, "elsewhere.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1, "work_root": zero, "detect_runners": False,
                       "task_scope": False, "tmux_session": "none",
                       "runners": {"writer": {"cmd": [self.agent], "max_minutes": 1}}}, handle)
        r = self.thread("new", notes, "write a note", "--agent", "writer", "--wait",
                        "--config", conf)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(zero, ".container-map", "container.json")))
        found = self.threads(cwd=notes)
        self.assertEqual(len(found), 1, "the map has threads: thread list must see them")

    def test_a_target_outside_every_map_names_the_map_it_was_checked_against(self):
        stray = os.path.join(self.tmp, "stray")
        os.makedirs(stray)
        r = self.thread("new", stray, "x", "--agent", "writer", cwd=self.notes)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("outside the roots", r.stderr)
        self.assertIn(self.conf, r.stderr)

    def test_the_stores_folder_is_hidden_from_the_repo_it_lives_in(self):
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(repo)
        git = [("init", "-q", "-b", "main"), ("config", "user.name", "T"),
               ("config", "user.email", "t@example.invalid"), ("config", "core.hooksPath",
                                                                "/dev/null"),
               ("config", "commit.gpgsign", "false"), ("config", "tag.gpgsign", "false")]
        for args in git:
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        with open(os.path.join(repo, "README"), "w") as handle:
            handle.write("repo\n")
        for args in (("add", "-A"), ("commit", "-q", "-m", "init")):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        conf = os.path.join(self.tmp, "repo-map.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1, "work_root": repo, "detect_runners": False,
                       "task_scope": False, "tmux_session": "none",
                       "tasks_dir": os.path.join(repo, ".container-map", "tasks"),
                       "runners": {"writer": {"cmd": [self.agent], "max_minutes": 1}}}, handle)
        r = self.thread("new", repo, "write a note", "--agent", "writer", "--wait", "--config",
                        conf)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.isdir(os.path.join(repo, ".container-map", "tasks")))
        status = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo,
                                capture_output=True, text=True, check=True).stdout
        self.assertNotIn(".container-map", status)


THREAD_FAKE = textwrap.dedent('''
    import json, sys, time
    mode = sys.argv[1]
    sys.stdin.read()
    if mode == "write":
        open("note.txt", "w").write("hello from the agent\\n")
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I wrote note.txt"}]}}))
    elif mode == "sleep":
        print("sleeping", flush=True)
        time.sleep(60)
    elif mode == "judge":
        print("read the diff")
        print("VERDICT: PASS - note.txt is there")
''')


class Thread(unittest.TestCase):
    """`cli.py thread …` end to end, no bridge: a temp store and repo, a fake agent (never a real
    model CLI), real git and real supervisor processes. --wait drives the oracle loop itself."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cm-thread-"))
        self.work = os.path.join(self.tmp, "work")
        self.repo = os.path.join(self.work, "proj")
        self.store_dir = os.path.join(self.tmp, "tasks")
        side = os.path.join(self.tmp, "side")
        for d in (self.repo, side):
            os.makedirs(d)
        fake = os.path.join(side, "fake_agent.py")
        with open(fake, "w") as handle:
            handle.write(THREAD_FAKE)
        for args in (["init", "-q", "-b", "main"], ["config", "user.name", "Test"],
                     ["config", "user.email", "test@example.invalid"],
                     ["config", "core.hooksPath", "/dev/null"],
                     ["config", "commit.gpgsign", "false"], ["config", "tag.gpgsign", "false"]):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)
        with open(os.path.join(self.repo, "README"), "w") as handle:
            handle.write("proj\n")
        for args in (["add", "-A"], ["commit", "-q", "-m", "init"]):
            subprocess.run(["git", *args], cwd=self.repo, check=True, capture_output=True)
        py = sys.executable
        self.runners = {
            "writer": {"cmd": [py, fake, "write"], "followup_cmd": [py, fake, "write"],
                       "max_minutes": 1},
            "sleeper": {"cmd": [py, fake, "sleep"], "followup_cmd": [py, fake, "write"],
                        "max_minutes": 1},
            "judge": {"cmd": [py, fake, "judge"], "role": "check", "max_minutes": 1},
        }
        self.conf = os.path.join(self.tmp, "container.json")
        with open(self.conf, "w") as handle:
            json.dump({"version": 1, "work_root": self.work, "tasks_dir": self.store_dir,
                       "task_scope": False, "detect_runners": False, "tmux_session": "none",
                       "runners": self.runners}, handle)
        for patcher in (mock.patch.object(cli, "TICK_SECONDS", 0.5),
                        mock.patch.object(cli, "POLL_SECONDS", 0.2),
                        mock.patch.dict(os.environ)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("CONTAINER_ROOT", None)
        os.environ.pop("CONTAINER_CONFIG", None)

    def tearDown(self):
        store = cli.thread_store({"--config": self.conf})
        for t in store.list():
            if t.get("status") in ("running", "checking"):
                try:
                    store.stop(t["id"])
                except Exception:
                    pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["thread", *args, "--config", self.conf])
        return rc, out.getvalue(), err.getvalue()

    def json(self, *args):
        rc, out, err = self.cli(*args, "--json")
        self.assertEqual(len(out.strip().splitlines()), 1, "one JSON object: " + out + err)
        return rc, json.loads(out), err

    def events(self, tid):
        with open(os.path.join(self.store_dir, tid, "events.jsonl")) as handle:
            return [json.loads(line) for line in handle]

    def settle(self, tid, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            rc, data, _ = self.json("show", tid)
            if data["thread"]["status"] not in ("running", "checking", "stopping"):
                return data["thread"]
            time.sleep(0.1)
        self.fail(f"{tid} still running after {timeout}s")

    def test_new_wait_reaches_review_and_list_show_diff_read_it(self):
        import tasks
        rc, out, err = self.cli("new", self.repo, "write a note", "--agent", "writer",
                                "--verify", "test -f note.txt", "--wait")
        self.assertEqual(rc, 0, out + err)
        tid = out.splitlines()[0]
        self.assertRegex(tid, tasks.TASK_ID_RE)
        self.assertIn("I wrote note.txt", out, "the run's log streams while it waits")
        self.assertIn(f"{tid}: review", out)
        self.assertIn("verify  `test -f note.txt`: exit 0", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "note.txt")), "own worktree")
        rc, out, _ = self.cli("list")
        self.assertEqual(rc, 0)
        self.assertRegex(out, rf"{tid}\s+review\s+writer\s+write a note")
        rc, data, _ = self.json("list", "--open")
        self.assertEqual(([t["id"] for t in data["threads"]], data["ok"]), ([tid], True))
        self.assertEqual(sorted(a["name"] for a in data["agents"]), ["judge", "sleeper", "writer"])
        rc, out, _ = self.cli("show", tid)
        self.assertEqual(rc, 0)
        for text in (f"{tid}: review", "--- run 1 (task): write a note", "I wrote note.txt",
                     f"worktree on cm/{tid} from main"):
            self.assertIn(text, out)
        rc, data, _ = self.json("show", tid, "--run", "1")
        self.assertEqual((rc, data["run"], data["thread"]["status"]), (0, 1, "review"))
        self.assertTrue(any("note.txt" in line["s"] for line in data["lines"]))
        self.assertIn("$ exit 0", data["verify_tail"])
        rc, out, _ = self.cli("diff", tid)
        self.assertEqual(rc, 0)
        self.assertIn("+hello from the agent", out)
        rc, data, _ = self.json("diff", tid)
        self.assertEqual([f["path"] for f in data["files"]], ["note.txt"])

    def test_a_failing_verify_with_retries_is_sent_back_by_the_clis_own_tick(self):
        rc, data, err = self.json("new", self.repo, "write a note", "--agent", "writer",
                                  "--verify", "test -f never.txt", "--retries", "1", "--wait")
        self.assertEqual(rc, 1, err)
        t = data["thread"]
        self.assertEqual((data["ok"], data["waited"]), (False, True))
        self.assertEqual((t["status"], t["attempts"], t["runs"], t["verify"]["rc"],
                          t["retry_due"]), ("failed", 2, 2, 1, False))
        self.assertIn("$ exit 1", data["verify_tail"])
        self.assertIn("goes back to its agent", err, "--json streams the log to stderr")
        retry = [e for e in self.events(data["id"]) if e["ev"] == "run" and e["n"] == 2][0]
        self.assertTrue(retry["auto"] and retry["followup"])

    def test_merge_refuses_on_the_evidence_then_lands_with_force(self):
        rc, data, _ = self.json("new", self.repo, "write a note", "--agent", "writer",
                                "--verify", "test -f never.txt", "--wait")
        self.assertEqual((rc, data["thread"]["status"]), (1, "failed"))
        tid = data["id"]
        rc, data, _ = self.json("merge", tid)
        self.assertEqual((rc, data["ok"], data["merged"]), (2, False, False))
        self.assertEqual(data["merge_blockers"], ["verify failed (exit 1)"])
        rc, out, _ = self.cli("merge", tid)
        self.assertEqual(rc, 2)
        self.assertIn("verify failed (exit 1)", out)
        self.assertIn(f"thread merge {tid} --force", out)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "note.txt")))
        rc, data, _ = self.json("merge", tid, "--force")
        self.assertEqual((rc, data["merged"], data["forced"], data["thread"]["status"]),
                         (0, True, True, "merged"))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "note.txt")))
        merged = [e for e in self.events(tid) if e["ev"] == "merged"][0]
        self.assertEqual(merged["evidence"], {"verify": 1, "check": None, "forced": True})

    def test_stop_reply_check_aside_and_done(self):
        rc, data, _ = self.json("new", self.repo, "wait around", "--agent", "sleeper")
        self.assertEqual((rc, data["waited"], data["thread"]["status"]), (0, False, "running"))
        tid = data["id"]
        rc, data, _ = self.json("stop", tid)
        self.assertEqual(rc, 0)
        self.assertIn(data["thread"]["status"], ("stopping", "stopped"))
        self.assertEqual(self.settle(tid)["status"], "stopped")
        rc, out, err = self.cli("reply", tid, "now write the note", "--wait")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"{tid}: review", out)
        rc, _, err = self.cli("check", tid)
        self.assertEqual(rc, 2)
        self.assertIn("offered: judge", err)
        rc, data, _ = self.json("check", tid, "--agent", "judge", "--wait")
        self.assertEqual((rc, data["verdict"], data["run"]), (0, "PASS", 3))
        self.assertIn("VERDICT: PASS - note.txt is there", data["reason"])
        rc, data, _ = self.json("aside", tid)
        self.assertEqual((rc, data["thread"]["status"]), (0, "discarded"))
        self.assertIn(f"cm/{tid}", data["message"])
        rc, data, _ = self.json("new", self.repo, "write a note here", "--agent", "writer",
                                "--in-place", "--wait")
        self.assertEqual((rc, data["thread"]["mode"]), (0, "inplace"))
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "note.txt")))
        rc, out, _ = self.cli("done", data["id"])
        self.assertEqual((rc, out.strip()), (0, f"{data['id']}: done"))
        rc, out, _ = self.cli("list", "--open")
        self.assertEqual(out.strip(), "no open threads")

    def test_a_running_map_and_the_cli_share_one_store_and_both_tick(self):
        import bridge
        # the bridge's app with its own clock on, no HTTP server and no live file: its ticker and
        # the CLI's --wait both run the oracle loop on the same store; the retry goes out once
        app = bridge.BridgeApp(seats_path="", game_path=os.path.join(self.tmp, "game.html"),
                               renderer_path="unused", live_path=os.path.join(self.tmp, "live"),
                               log_path=os.path.join(self.tmp, "audit.log"),
                               file_roots=(self.work,), tasks_dir=self.store_dir,
                               task_runners=self.runners, mate_spec={}, tick_seconds=0.1)
        self.assertTrue(app.start_ticker())
        try:
            rc, data, err = self.json("new", self.repo, "write a note", "--agent", "writer",
                                      "--verify", "test -f never.txt", "--retries", "1", "--wait")
        finally:
            app.stop_ticker()
        self.assertEqual(rc, 1, err)
        tid = data["id"]
        self.assertEqual((data["thread"]["status"], data["thread"]["attempts"]), ("failed", 2))
        events = self.events(tid)
        self.assertEqual([e["n"] for e in events if e["ev"] == "run"], [1, 2])
        self.assertEqual([e["n"] for e in events if e["ev"] == "verify"], [1, 2])
        self.assertIn(tid, [t["id"] for t in app.list_tasks()["tasks"]], "the map sees it")
        app.tasks.last_create = -100.0
        made = app.tasks.create("write a note too", self.repo, "writer")
        rc, data, _ = self.json("list")
        self.assertEqual([t["id"] for t in data["threads"]][:1], [made["id"]], "and the CLI it")

    def test_usage_and_store_errors(self):
        rc, out, _ = self.cli("--help")
        self.assertEqual(rc, 0)
        self.assertIn("thread new TARGET", out)
        for args, code, words in (
                (["bogus"], 2, "unknown thread command"),
                (["list", "--bogus"], 2, "unknown option --bogus"),
                (["new", self.repo, "x"], 2, "offered: writer, sleeper"),
                (["new", self.repo, "x", "--agent", "writer", "--retries", "two"], 2,
                 "--retries takes a whole number"),
                (["show"], 2, "usage:"),
                (["show", "20990101-000000-nope-abcdef"], 1, "no such task"),
                (["new", self.tmp, "x", "--agent", "writer"], 1, "outside the roots"),
                (["new", self.repo, "x", "--agent", "nobody"], 1, "pick one of the offered")):
            rc, out, err = self.cli(*args)
            self.assertEqual(rc, code, (args, out, err))
            self.assertIn(words, err, args)
            self.assertNotIn("Traceback", err)
        rc, data, _ = self.json("show", "20990101-000000-nope-abcdef")
        self.assertEqual((rc, data), (1, {"ok": False, "error": "no such task", "exit": 1}))
        rc, data, _ = self.json("list", "--bogus")
        self.assertEqual((rc, data["ok"], data["exit"]), (2, False, 2))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["thread", "list", "--config", os.path.join(self.tmp, "nope.json")])
        self.assertEqual(rc, 1)
        self.assertIn("no such config file", err.getvalue())


def beside_code():
    """Everything in the tool's own folder as (size, mtime): bytecode and git state aside, a demo
    must leave it exactly as it found it."""
    seen = {}
    for base, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".claude")]
        for name in dirs + files:
            full = os.path.join(base, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            seen[os.path.relpath(full, HERE)] = (st.st_size, st.st_mtime_ns)
    return seen


class Hardening(Fixture):
    """Lane H1: the argument handling every command shares, the symlinked CLI, the Python floor and
    the config keys that are ignored."""

    COMMANDS = ("render", "serve", "demo", "config", "doctor", "init", "install", "uninstall",
                "thread", "pair", "devices", "link")

    def short(self, args, seconds=8, env=None, argv=None):
        """(code, stdout, stderr) of the CLI with no map and no port of the caller's: a command
        that would start a server (and so never end) fails the test instead of hanging it."""
        env = dict(env or stranger_env(self.home), BRIDGE_PORT=str(free_port()))
        proc = subprocess.Popen(argv or [sys.executable, CLI, *args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env, cwd=self.tmp,
                                start_new_session=True)
        try:
            out, err = proc.communicate(timeout=seconds)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            self.fail(f"{' '.join(args)} did not end: it started something")
        return proc.returncode, out, err

    def test_render_serve_config_and_demo_refuse_options_they_do_not_take(self):
        page = os.path.join(self.root, ".container-map", "game.html")
        for cmd, takes in (("render", ("--out", "--port", "--config")),
                           ("serve", ("--port", "--config")),
                           ("config", ("--config",)),
                           ("demo", ("--no-serve", "--port", "--static", "--root"))):
            for bogus in ("--bogus", "--bogus=1", "-x"):
                with self.subTest(cmd=cmd, option=bogus):
                    args = [cmd, bogus] + ([] if cmd == "demo" else [self.root])
                    code, out, err = self.short(args)
                    self.assertEqual(code, 2, (out, err))
                    self.assertIn(bogus.split("=")[0] if bogus != "-x" else "-x", err)
                    for option in takes:
                        self.assertIn(option, err)
                    self.assertEqual(out, "")
        self.assertFalse(os.path.exists(page), "a refused render wrote the page")
        self.assertEqual(os.listdir(self.home), [])

    def test_options_after_a_flag_value_are_not_mistaken_for_options(self):
        code, out, err = self.short(["render", self.root, "--out", "-odd.html"], seconds=60)
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "-odd.html")))
        # a value that looks like an option is skipped, not the rest of the line: a real unknown
        # option after it is still refused (and nothing is written)
        code, out, err = self.short(["render", self.root, "--out", "-odd2.html", "--bogus"], seconds=60)
        self.assertEqual((code, out), (2, ""), err)
        self.assertIn("--bogus", err)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "-odd2.html")), "a refused render wrote the page")

    def test_help_prints_help_for_every_command_and_starts_nothing(self):
        for cmd in self.COMMANDS:
            for flag in ("--help", "-h"):
                with self.subTest(cmd=cmd, flag=flag):
                    code, out, err = self.short([cmd, flag])
                    self.assertEqual((code, err), (0, ""))
                    self.assertIn(f"container-map {cmd}", out)
                    self.assertGreater(len(out.splitlines()), 2)
        # after other options and arguments too: serve --help once started a server
        for args in (["serve", self.root, "--port", str(free_port()), "--help"],
                     ["render", "--help", "--bogus"],
                     ["install", self.root, "--dry-run", "-h"], ["thread", "new", "--help"],
                     ["demo", "--no-serve", "--help"], ["uninstall", "--force", "--help"]):
            with self.subTest(args=args):
                code, out, err = self.short(args)
                self.assertEqual((code, err), (0, ""))
                self.assertIn("container-map " + args[0], out)
        self.assertEqual(os.listdir(self.home), [], "a help wrote into HOME")
        self.assertFalse(os.path.exists(os.path.join(self.root, ".container-map")))

    def test_every_command_the_docstring_lists_has_its_help(self):
        for cmd in self.COMMANDS:
            self.assertIn(cmd, cli.COMMANDS)
            self.assertTrue(cli.HELP.get(cmd) or cmd == "thread", cmd)
        self.assertEqual(sorted(set(cli.HELP) | {"thread"}), sorted(cli.COMMANDS))
        for cmd, text in cli.HELP.items():
            self.assertTrue(text.startswith(f"usage: container-map {cmd}"), cmd)
            self.assertIn(f"container-map {cmd}", cli.__doc__)

    def test_a_cli_on_path_through_a_symlink_finds_its_own_folder(self):
        link = os.path.join(self.bin, "container-map")
        os.symlink(CLI, link)
        self.assertEqual(os.path.dirname(os.path.realpath(CLI)), cli.HERE)
        conf = os.path.join(self.tmp, "c.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1, "name": "VIALINK", "tmux_session": "none"}, handle)
        env = stranger_env(self.home)
        r = subprocess.run([sys.executable, link, "config", "--config", conf, self.root],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["name"], "VIALINK")
        page = os.path.join(self.tmp, "via-link.html")
        r = subprocess.run([sys.executable, link, "render", self.root, "--config", conf, "--out", page],
                           capture_output=True, text=True, env=env, cwd=self.tmp, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        with open(page, encoding="utf-8") as handle:
            self.assertIn("VIALINK", handle.read())

    def test_every_command_but_doctor_and_thread_stops_on_a_python_below_the_floor(self):
        # a child process whose cli says the floor is 3.99; its PATH has no service manager, so a
        # command that wrongly went on could not touch this machine's services
        wrap = ("import sys; import cli; cli.MIN_PYTHON = (3, 99); sys.exit(cli.main(sys.argv[1:]))")
        for tool in ("bash", "git"):
            os.symlink(shutil.which(tool), os.path.join(self.bin, tool))
        env = dict(stranger_env(self.home, path=self.bin), PYTHONPATH=cli.HERE)
        for cmd in ("render", "serve", "demo", "config", "init", "install", "uninstall",
                    "pair", "devices"):
            with self.subTest(cmd=cmd):
                args = [cmd, self.root] if cmd in ("render", "serve", "config", "init", "install") else [cmd]
                code, out, err = self.short(args, env=env, argv=[sys.executable, "-c", wrap, *args])
                self.assertEqual((code, out), (1, ""), err)
                self.assertIn("python 3.99+ is needed", err)
                self.assertIn(f"container-map {cmd}", err)
        with mock.patch.object(cli, "MIN_PYTHON", (3, 99)):
            # help still prints, doctor reports it in its own rows, thread keeps its JSON contract
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["render", "--help"]), 0)
            self.assertIn("usage: container-map render", out.getvalue())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["thread", "list", "--json"]), 1)
            self.assertFalse(json.loads(out.getvalue())["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.root, ".container-map")),
                         "a refused command still made the map's folder")
        self.assertEqual(os.listdir(self.home), [])

    def test_thread_new_refuses_text_that_is_not_utf8_and_makes_nothing(self):
        # an argument from a shell that was not UTF-8 reaches Python as a lone surrogate
        repo = os.path.join(self.tmp, "proj")
        os.makedirs(repo)
        conf = os.path.join(self.tmp, "t.json")
        agent = self.fake_agent("agent")
        with open(conf, "w") as handle:
            json.dump({"version": 1, "work_root": self.tmp, "detect_runners": False,
                       "task_scope": False, "tmux_session": "none",
                       "tasks_dir": os.path.join(self.tmp, "store"),
                       "runners": {"writer": {"cmd": [agent], "max_minutes": 1}}}, handle)
        proc = subprocess.run([sys.executable, CLI, "thread", "new", repo, b"fix the \xff thing",
                               "--agent", "writer", "--config", conf],
                              capture_output=True, text=True, env=stranger_env(self.home),
                              cwd=self.tmp, timeout=60)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("text must be valid text", proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "store")) and
                         [n for n in os.listdir(os.path.join(self.tmp, "store")) if not n.startswith(".")],
                         "a refused text left a thread behind")

    def test_a_timed_out_verify_and_setup_are_shown_as_timeouts(self):
        thread = {"id": "20261010-000000-x-abcdef", "status": "failed", "rc": 0, "attempts": 1,
                  "retries": 0, "verify_cmd": "sleep 900", "setup_cmd": "npm ci",
                  "verify": {"n": 1, "rc": -15, "seconds": 600.0, "timed_out": True, "limit": 600.0},
                  "setup": {"n": 1, "rc": -15, "seconds": 600.0, "timed_out": True, "limit": 600},
                  "merge_blockers": ["verify timed out after 600 s"]}
        line = cli._verify_line(thread)
        self.assertIn("timed out after 600 s", line)
        self.assertNotIn("600.0", line)
        shown = "\n".join(cli._evidence_lines(thread))
        self.assertIn("setup   `npm ci`: timed out after 600 s", shown)
        self.assertIn("merge blockers: verify timed out after 600 s", shown)
        plain = dict(thread, verify={"n": 1, "rc": 3, "seconds": 2.5}, setup={"n": 1, "rc": 4})
        self.assertEqual(cli._verify_line(plain), "verify  `sleep 900`: exit 3 in 2.5s (run 1)")
        self.assertIn("exit 4 (run 1); no agent ran", "\n".join(cli._evidence_lines(plain)))

    # ---- config keys that are ignored ----
    def config_file(self, text):
        path = os.path.join(self.tmp, "keys.json")
        with open(path, "w") as handle:
            handle.write(text if isinstance(text, str) else json.dumps(text))
        return path

    def test_load_collects_the_keys_it_ignores_with_the_reason(self):
        import config
        path = self.config_file({
            "version": 1, "_what": "a comment", "name": "OK", "tick_seconds": 2.5,
            "tasks_dir": 5, "bogus_key": 1, "merge_skip": "node_modules/", "processes": "yes",
            "t3_state": ["/a/state.sqlite"], "today": [], "planets": {}})
        cfg = config.load(path)
        got = {item["key"]: item["reason"] for item in cfg["_dropped"]}
        self.assertEqual(sorted(got), ["bogus_key", "merge_skip", "planets", "processes",
                                       "tasks_dir", "tick_seconds", "today"])
        self.assertIn("expected a whole number, got a decimal number", got["tick_seconds"])
        self.assertIn("expected text, got a whole number", got["tasks_dir"])
        self.assertIn("expected a list, got text", got["merge_skip"])
        self.assertIn("expected true or false, got text", got["processes"])
        self.assertIn("expected an object, got a list", got["today"])
        self.assertIn("no setting by that name", got["bogus_key"])
        self.assertEqual((cfg["name"], cfg["t3_state"], cfg["tick_seconds"], cfg["merge_skip"] != "node_modules/"),
                         ("OK", ["/a/state.sqlite"], 10, True))     # what is kept is unchanged
        clean = config.load(self.config_file({"version": 1, "_what": "x", "name": "Y"}))
        self.assertEqual(clean["_dropped"], [])
        self.assertEqual(config.load(os.path.join(self.tmp, "absent.json"))["_dropped"], [])
        self.assertEqual(config.load(self.config_file("{ not json"))["_dropped"], [])
        bad_t3 = config.load(self.config_file({"t3_state": [1, 2]}))
        self.assertEqual([i["key"] for i in bad_t3["_dropped"]], ["t3_state"])
        self.assertIn("text or a list of text", bad_t3["_dropped"][0]["reason"])

    def test_the_shipped_example_configs_drop_nothing(self):
        import config
        for name in ("container.example.json", "container.sources-example.json"):
            self.assertEqual(config.load(os.path.join(cli.HERE, name))["_dropped"], [], name)

    def test_doctor_prints_what_the_config_ignored(self):
        path = self.config_file({"version": 1, "tick_seconds": 2.5, "bogus_key": 1, "_what": "x",
                                 "merge_skip": "no"})
        r = run_cli(["doctor", self.root, "--config", path, "--port", str(free_port())],
                    stranger_env(self.home))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = [line for line in r.stdout.splitlines() if line.lstrip().startswith("ignored")]
        self.assertEqual(len(lines), 3, r.stdout)
        text = "\n".join(lines)
        self.assertIn("tick_seconds", text)
        self.assertIn("expected a whole number, got a decimal number", text)
        self.assertIn("bogus_key", text)
        self.assertIn("no setting by that name", text)
        self.assertIn("merge_skip", text)
        self.assertNotIn("_what", r.stdout)
        # a clean config prints no such line
        clean = self.config_file({"version": 1, "name": "FINE"})
        r = run_cli(["doctor", self.root, "--config", clean, "--port", str(free_port())],
                    stranger_env(self.home))
        self.assertFalse([l for l in r.stdout.splitlines() if l.lstrip().startswith("ignored")])


class ClaimFixCliTests(InstallHelpers, Fixture):
    """The ship review's confirmed claims about the CLI, install and demo."""

    WHICH = staticmethod(lambda name: "/usr/bin/" + name)

    # ---- c014, c029: --config is written down as what it is ----------------------------------------
    def test_pair_hours_takes_a_value_and_checks_it(self):
        port = str(free_port())                              # nothing answers there
        for args in (["pair", "--hours", "168", "--port", port], ["pair", "--hours=24", "--port", port]):
            with self.subTest(args=args):
                r = run_cli(args, stranger_env(self.home))
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)      # parsed: only the map is missing
                self.assertNotIn("takes no arguments", r.stderr)
        for bad in ("0", "169", "two", ""):
            with self.subTest(bad=bad):
                r = run_cli(["pair", "--hours", bad, "--port", port], stranger_env(self.home))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("--hours", r.stderr)

    def test_pair_devices_and_uninstall_refuse_config_and_every_doc_says_so(self):
        conf = os.path.join(self.tmp, "c.json")
        with open(conf, "w") as handle:
            json.dump({"version": 1}, handle)
        port = str(free_port())
        for args in (["pair", "--config", conf, "--port", port],
                     ["devices", "--config=" + conf, "--port", port],
                     ["uninstall", "--config", conf, "--dry-run"]):
            with self.subTest(args=args):
                r = run_cli(args, stranger_env(self.home))
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn("unknown option --config", r.stderr)
                self.assertIn(f"{args[0]} --help", r.stderr)
        for cmd in ("pair", "devices", "uninstall"):
            self.assertIn("reads no config file", " ".join(cli.HELP[cmd].split()), cmd)
        with open(os.path.join(HERE, "docs", "CLI.md"), encoding="utf-8") as handle:
            para = " ".join(handle.read().split("`--config FILE` works with every command")[1]
                            .split("\n\n")[0].split())
        for word in ("demo", "pair", "devices", "uninstall"):
            self.assertIn(f"`{word}`", para)
        self.assertIn("pair", " ".join(cli.__doc__.split("--config FILE works")[1].split("\n\n")[0].split()))

    # ---- c026: demo --static needs neither git nor Python 3.10 ---------------------------------
    def test_the_static_demo_is_not_stopped_by_the_python_floor_but_the_live_one_is(self):
        ran = []
        with mock.patch.object(cli, "MIN_PYTHON", (3, 99)), \
                mock.patch.dict(cli.COMMANDS, {"demo": lambda rest: ran.append(rest) or 0}):
            self.assertEqual(cli.main(["demo", "--static", "--root", "x"]), 0)
            self.assertEqual(ran, [["--static", "--root", "x"]])
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
                cli.main(["demo", "--no-serve"])
            self.assertEqual(stop.exception.code, 1)
            self.assertIn("python 3.99+ is needed", err.getvalue())
            with mock.patch.object(cli, "STATIC_DEMO_PYTHON", (3, 99)):      # its own floor
                err2 = io.StringIO()
                with contextlib.redirect_stderr(err2), self.assertRaises(SystemExit) as stop:
                    cli.main(["demo", "--static"])
                self.assertEqual(stop.exception.code, 1)
                self.assertIn("python 3.99+ is needed", err2.getvalue())
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                cli.main(["serve", "--static"])             # only demo takes --static
            self.assertEqual(ran, [["--static", "--root", "x"]])
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                cli.main(["demo", "--", "--static"])        # after -- it is not an option
            self.assertEqual(len(ran), 1)

    # ---- c040: native Windows is told where to go, not handed a traceback ----------------------
    def test_native_windows_is_refused_in_one_line_that_names_wsl2(self):
        with mock.patch.object(cli, "_native_windows", lambda: True):
            for cmd in ("serve", "demo", "install", "thread"):
                with self.subTest(cmd=cmd):
                    err = io.StringIO()
                    with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
                        cli.main([cmd, "x"])
                    self.assertEqual(stop.exception.code, 1)
                    self.assertIn("WSL2", err.getvalue())
                    self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
            self.assertTrue(any("WSL2" in piece for piece in cli.missing_pieces(sys.version_info)))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["serve", "--help"]), 0)        # help still prints
        for name in ("README.md", os.path.join("docs", "INSTALL.md"), os.path.join("docs", "CLI.md")):
            with open(os.path.join(HERE, name), encoding="utf-8") as handle:
                self.assertIn("WSL2", handle.read(), name)

    # ---- c041: a world.sh that will not leave takes what it started with it --------------------
    @unittest.skipUnless(shutil.which("ps"), "needs ps")
    def test_a_world_sh_that_ignores_the_stop_does_not_leave_its_children_listening(self):
        pidfile = os.path.join(self.tmp, "child.pid")
        proc = subprocess.Popen(["bash", "-c", f"sleep 300 & echo $! > {pidfile}; wait"],
                                stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.poll() is None and (proc.kill() or proc.wait()))
        end = time.time() + 10
        while time.time() < end and not os.path.exists(pidfile):
            time.sleep(0.05)
        time.sleep(0.2)
        with open(pidfile) as handle:
            child = int(handle.read().strip())
        self.addCleanup(lambda: cli._alive(child) and os.kill(child, signal.SIGKILL))
        os.kill(proc.pid, signal.SIGSTOP)           # cannot run its trap: the SIGTERM waits
        self.assertIn(child, cli._descendants(proc.pid))
        with mock.patch.object(cli, "STOP_SECONDS", 0.5):
            cli._stop_child(proc)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        end = time.time() + 5
        while time.time() < end and cli._alive(child):
            time.sleep(0.05)
        self.assertFalse(cli._alive(child), "the child outlived world.sh")

    # ---- c097: another map's bridge is not this root's -----------------------------------------
    def test_a_shared_live_file_counts_only_when_it_names_this_root(self):
        import config
        shared = os.path.join(self.tmp, "legacy-live.json")
        gone = os.path.join(self.tmp, "no-user-file.json")
        other = os.path.join(self.tmp, "other-map")
        os.makedirs(other)
        port = 7611

        def live(**extra):
            with open(shared, "w") as handle:
                json.dump(dict({"pid": os.getpid(), "port": port}, **extra), handle)

        with mock.patch.object(config, "LEGACY_LIVE", shared), \
                mock.patch.object(config, "user_live_path", lambda p: gone):
            live(root=os.path.realpath(self.root))
            self.assertEqual(cli._own_bridge(self.root, port)["port"], port)
            live(root=os.path.realpath(other))
            self.assertIsNone(cli._own_bridge(self.root, port))
            live()                                   # a bridge from before the field: proves nothing
            self.assertIsNone(cli._own_bridge(self.root, port))
            live(root=os.path.realpath(self.root))
            self.assertIsNone(cli._own_bridge(self.root, port + 1))
            # the root's own file needs no field: its path is the root's
            own = config.live_path(self.root, port)
            os.makedirs(os.path.dirname(own))
            with open(own, "w") as handle:
                json.dump({"pid": os.getpid(), "port": port}, handle)
            live(root=os.path.realpath(other))
            self.assertEqual(cli._own_bridge(self.root, port)["port"], port)

    def test_install_does_not_replace_another_maps_service_through_the_shared_file(self):
        import config
        shared = os.path.join(self.tmp, "legacy-live.json")
        other = os.path.join(self.tmp, "other-map")
        os.makedirs(other)
        bridge = FakeBridge(other)
        self.addCleanup(bridge.close)
        with open(shared, "w") as handle:
            json.dump({"pid": os.getpid(), "port": bridge.port, "root": os.path.realpath(other)}, handle)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(config, "LEGACY_LIVE", shared), \
                mock.patch.object(config, "user_live_path", lambda p: os.path.join(self.tmp, "x")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = cli.cmd_install([self.root, "--port", str(bridge.port)], platform="linux",
                                     home=self.home, run=self.recorder, which=self.WHICH)
            except SystemExit as exc:
                rc = exc.code
        self.assertEqual(rc, 1, out.getvalue() + err.getvalue())
        self.assertIn("held by something else", err.getvalue())
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.unit()))

    def test_the_bridge_writes_its_root_into_its_live_file(self):
        code = ("import json, os, sys; sys.path.insert(0, %r); import bridge\n"
                "app = bridge.BridgeApp(seats_path=os.devnull, game_path=os.devnull,\n"
                "                       renderer_path=os.devnull, live_path=sys.argv[1],\n"
                "                       log_path=os.devnull)\n"
                "app.port = 7611\n"
                "app.write_live()\n" % HERE)
        live = os.path.join(self.tmp, "run", "bridge-7611.json")
        env = dict(stranger_env(self.home), CONTAINER_ROOT=self.root)
        r = subprocess.run([sys.executable, "-c", code, live], env=env, capture_output=True,
                           text=True, cwd=self.tmp, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(live, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["root"], os.path.realpath(self.root))

    # ---- c034, c086: the demo ------------------------------------------------------------------
    def test_the_demo_verify_line_survives_a_path_with_no_python3_and_a_spaced_interpreter(self):
        with mock.patch.object(demo.shutil, "which", lambda name: "/usr/bin/python3"):
            self.assertEqual(demo.verify_line(), "python3 -m unittest -q")
        spaced = "/opt/my python/bin/python3"
        with mock.patch.object(demo.shutil, "which", lambda name: None), \
                mock.patch.object(demo, "PY", spaced):
            import shlex
            self.assertEqual(shlex.split(demo.verify_line()), [spaced, "-m", "unittest", "-q"])

    def test_git_config_from_the_callers_environment_never_reaches_the_demos_git(self):
        mine = os.path.join(self.tmp, "mine")
        os.makedirs(mine)
        subprocess.run(["git", "init", "-q", mine], check=True, capture_output=True)
        conf = os.path.join(mine, ".git", "config")
        subprocess.run(["git", "config", "user.name", "Existing Owner"], cwd=mine, check=True)
        subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=mine, check=True)
        with open(conf, "rb") as handle:
            before = handle.read()
        with mock.patch.dict(os.environ, {"GIT_CONFIG": conf}):
            self.assertNotIn("GIT_CONFIG", demo.clean_env())
            repo = os.path.join(self.tmp, "demo-repo")
            demo.make_repo(repo, {"a.txt": "x\n"})
        with open(conf, "rb") as handle:
            self.assertEqual(handle.read(), before, "the caller's repo config was written")
        out = subprocess.run(["git", "config", "user.name"], cwd=repo, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "container-map demo")


class Demo(unittest.TestCase):
    """`container-map demo`: a live workspace with a scripted agent (never a model CLI), its two
    threads put through the real engine, in a new folder under the user's own cache."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="cm-demo-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.env = dict(stranger_env(self.home), PYTHONDONTWRITEBYTECODE="1")
        self.cache = os.path.join(self.home, ".cache", "container-map")

    def workspace(self):
        (name,) = os.listdir(self.cache)
        self.assertRegex(name, r"^demo-[0-9]{8}-[0-9]{6}-")
        return os.path.join(self.cache, name)

    def events(self, ws, tid, kind):
        path = os.path.join(ws, ".container-map", "tasks", tid, "events.jsonl")
        with open(path, encoding="utf-8") as handle:
            return [e for e in map(json.loads, handle) if e["ev"] == kind]

    def test_no_serve_puts_two_threads_through_the_real_engine_and_writes_only_its_folder(self):
        before = beside_code()
        r = run_cli(["demo", "--no-serve"], self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(beside_code(), before, "nothing beside the code")
        ws = self.workspace()
        self.assertIn(f"serve {ws}", r.stdout, "it says how to serve what it built")
        r = run_cli(["thread", "list", "--json", "--root", ws], self.env)
        data = json.loads(r.stdout)
        self.assertEqual([a["label"] for a in data["agents"]], ["Demo agent (scripted)"])
        by_status = {}
        for t in data["threads"]:
            by_status.setdefault(t["status"], []).append(t)
        self.assertEqual(sorted(by_status), ["merged", "review"], data["threads"])
        (merged,), (review,) = by_status["merged"], by_status["review"]
        self.assertEqual((merged["attempts"], merged["retries"], merged["verify"]["rc"]), (2, 2, 0))
        self.assertEqual(merged["check"]["verdict"], "PASS")
        self.assertEqual([e["rc"] for e in self.events(ws, merged["id"], "verify")], [1, 0],
                         "the first verify failed, the retry's passed")
        runs = [e for e in self.events(ws, merged["id"], "run") if e.get("kind") != "check"]
        self.assertEqual([bool(e.get("auto")) for e in runs], [False, True],
                         "run 2 is the oracle loop's retry")
        self.assertEqual(review["verify"]["rc"], 0)
        self.assertEqual(review["merge_blockers"], [])
        done = subprocess.run([sys.executable, "-m", "unittest", "-q"], env=self.env,
                              cwd=os.path.join(ws, "todo-app"), capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(done.returncode, 0, "the merge landed the fix in the repo: " + done.stderr)
        with open(os.path.join(ws, "notes-site", "README.md"), encoding="utf-8") as handle:
            self.assertIn("TODO:", handle.read(), "the thread in review is not merged yet")
        self.assertTrue(os.path.isdir(review["cwd"]), "its worktree waits for the page")
        with open(os.path.join(ws, ".container-map", "container.json"), encoding="utf-8") as handle:
            cfg = json.load(handle)
        self.assertEqual((cfg["detect_runners"], sorted(cfg["runners"]), cfg["wingmate"]),
                         (False, ["demo-agent"], {"off": True}))
        self.assertEqual(cfg["runners"]["demo-agent"]["cmd"][0], sys.executable)
        self.assertEqual(os.path.getsize(cfg["crontab_file"]), 0)
        self.assertEqual(len(cfg["planets"]), 7)
        self.assertEqual({p["kind"] for p in cfg["planets"]}, {"code", "knowledge", "reference", "data", "archive"})
        self.assertEqual(sorted(n for n in os.listdir(ws) if not n.startswith(".")),
                         sorted(p["name"] for p in cfg["planets"]))
        self.assertEqual(sum(os.path.isdir(os.path.join(ws, p["name"], ".git")) for p in cfg["planets"]), 3)

    def test_static_keeps_the_one_page_demo_in_a_new_folder(self):
        before = beside_code()
        dest = os.path.join(self.tmp, "static")
        r = subprocess.run(["bash", os.path.join(HERE, "demo.sh"), "--static", "--root", dest],
                           capture_output=True, text=True, env=self.env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(beside_code(), before, "nothing beside the code")
        page = os.path.join(dest, "demo.html")
        self.assertIn(page, r.stdout)
        with open(page, encoding="utf-8") as handle:
            self.assertIn("DEMO", handle.read())
        self.assertFalse(os.path.exists(self.cache), "--root keeps it out of the cache")

    def test_a_folder_with_anything_in_it_is_refused_and_left_alone(self):
        mine = os.path.join(self.tmp, "mine")
        os.makedirs(mine)
        keep = os.path.join(mine, "notes.txt")
        with open(keep, "w") as handle:
            handle.write("mine\n")
        for flag in ([], ["--static"]):
            r = run_cli(["demo", "--no-serve", "--root", mine, *flag], self.env)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("is not an empty folder", r.stderr)
            self.assertEqual(os.listdir(mine), ["notes.txt"])
        with open(keep) as handle:
            self.assertEqual(handle.read(), "mine\n")

    def test_bad_arguments(self):
        for args, words in ((["--bogus"], "unknown argument --bogus"),
                            (["--port", "abc"], "port must be a number"),
                            (["--root"], "--root needs a value"),
                            (["somewhere"], "unknown argument somewhere")):
            r = run_cli(["demo", *args], self.env)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
            self.assertIn(words, r.stderr)
            self.assertNotIn("Traceback", r.stderr)
        self.assertFalse(os.path.exists(self.cache))

    def test_the_scripted_agent_only_works_inside_a_thread_worktree(self):
        dest = os.path.join(self.tmp, "ws")
        os.makedirs(dest)
        demo.build_live(dest)
        notes, todo = os.path.join(dest, "notes-site"), os.path.join(dest, "todo-app")
        agent = os.path.join(HERE, "demo_agent.py")
        before = beside_code()
        with open(os.path.join(notes, "README.md"), "rb") as handle:
            readme = handle.read()
        # the tool's own folder is where the page's hand-off starts a run; the repos' main
        # branches are where an agent without a worktree would edit in place
        for where in (HERE, notes, todo):
            for extra in ([], ["--check"]):
                r = subprocess.run([sys.executable, agent, *extra], cwd=where, env=self.env,
                                   input="TODO: write something\n", capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, 1, (where, extra, r.stdout, r.stderr))
                self.assertIn("refusing to touch " + where, r.stdout)
                self.assertNotIn("VERDICT", r.stdout)
        self.assertEqual(beside_code(), before, "nothing changed beside the code")
        with open(os.path.join(notes, "README.md"), "rb") as handle:
            self.assertEqual(handle.read(), readme)
        for repo in (notes, todo):
            porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=repo, env=self.env,
                                       capture_output=True, text=True)
            self.assertEqual(porcelain.stdout, "", repo)
        # inside a thread's worktree (the engine keeps task.json beside it) it does the work
        thread_dir = os.path.join(self.tmp, "tasks", "t-1")
        worktree = os.path.join(thread_dir, "worktree")
        os.makedirs(worktree)
        for path, text in ((os.path.join(thread_dir, "task.json"), "{}\n"),
                           (os.path.join(worktree, "README.md"), "# x\n\nTODO: write it\n")):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        r = subprocess.run([sys.executable, agent], cwd=worktree, env=self.env, input="task\n",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(worktree, "README.md"), encoding="utf-8") as handle:
            self.assertIn("## Getting started", handle.read())
        # the engine keeps new worktrees outside the receipts: a linked worktree on cm/<its name>
        fresh = os.path.join(self.tmp, "tasks-worktrees", "t-2")
        subprocess.run(["git", "worktree", "add", "-q", "-b", "cm/t-2", fresh], cwd=notes,
                       env=self.env, check=True, capture_output=True)
        r = subprocess.run([sys.executable, agent], cwd=fresh, env=self.env, input="task\n",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(os.path.join(fresh, "README.md"), encoding="utf-8") as handle:
            self.assertIn("## Getting started", handle.read())
        # a linked worktree on any other branch is not a thread's
        other = os.path.join(self.tmp, "tasks-worktrees", "t-3")
        subprocess.run(["git", "worktree", "add", "-q", "-b", "my-feature", other], cwd=notes,
                       env=self.env, check=True, capture_output=True)
        r = subprocess.run([sys.executable, agent], cwd=other, env=self.env, input="task\n",
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("refusing to touch " + other, r.stdout)

    def test_git_pointed_at_another_repo_by_the_caller_never_reaches_the_demo(self):
        outer = os.path.join(self.tmp, "outer")
        os.makedirs(outer)
        for argv in (["init", "-q"], ["config", "commit.gpgsign", "false"],
                     ["-c", "user.name=t", "-c", "user.email=t@localhost",
                      "commit", "-q", "--allow-empty", "-m", "outer"]):
            done = subprocess.run(["git", *argv], cwd=outer, env=self.env, capture_output=True,
                                  text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        git_dir = os.path.join(outer, ".git")

        def state():
            with open(os.path.join(git_dir, "config"), "rb") as handle:
                config = handle.read()
            log = subprocess.run(["git", "log", "--all", "--format=%H %s"], cwd=outer,
                                 env=self.env, capture_output=True, text=True).stdout
            return config, log
        before = state()
        env = dict(self.env, GIT_DIR=git_dir, GIT_WORK_TREE=outer,
                   GIT_INDEX_FILE=os.path.join(git_dir, "index"))
        r = run_cli(["demo", "--no-serve"], env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(state(), before, "the caller's repo is exactly as it was")
        ws = self.workspace()
        log = subprocess.run(["git", "log", "--format=%s"], cwd=os.path.join(ws, "todo-app"),
                             env=self.env, capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(log[-1], "initial commit", "the demo's own repo got the commits")
        self.assertGreater(len(log), 1)

    def test_a_folder_that_cannot_be_made_is_one_line_not_a_traceback(self):
        blocker = os.path.join(self.tmp, "a-file")
        with open(blocker, "w") as handle:
            handle.write("not a folder\n")
        for env, args in ((dict(self.env, HOME=blocker), []),
                          (self.env, ["--root", os.path.join(blocker, "sub")])):
            r = run_cli(["demo", "--no-serve", *args], env)
            self.assertEqual(r.returncode, 1, (args, r.stdout, r.stderr))
            self.assertNotIn("Traceback", r.stderr)
            (line,) = r.stderr.strip().splitlines()
            self.assertTrue(line.startswith("container-map demo: "), line)
            self.assertIn(blocker, line, "the OS's own message, with the path")

    def test_a_git_that_times_out_is_one_line_too(self):
        slow = subprocess.TimeoutExpired(["git", "init", "-q"], 60)
        err = io.StringIO()
        with mock.patch.object(demo.subprocess, "run", side_effect=slow), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                cli.cmd_demo(["--no-serve", "--root", os.path.join(self.tmp, "slow")])
        self.assertEqual(caught.exception.code, 1)
        self.assertRegex(err.getvalue().strip(), r"^container-map demo: git init -q could not run "
                                                 r"in .*timed out after 60")

    def test_ctrl_c_before_the_bridge_starts_stops_with_a_message(self):
        said = []
        with mock.patch.object(cli, "_page_path", return_value=""), \
                mock.patch.object(cli, "_first_render", side_effect=KeyboardInterrupt), \
                mock.patch.object(cli.subprocess, "Popen") as popen:
            rc = cli._demo_serve(self.tmp, 1, said.append)
        self.assertEqual(rc, 0)
        popen.assert_not_called()
        self.assertEqual(len(said), 1, said)
        self.assertTrue(said[0].startswith("stopped. The workspace stays at " + self.tmp), said)

    def test_a_second_ctrl_c_while_the_bridge_stops_kills_it(self):
        class Child:
            returncode = None
            calls = []

            def poll(self):
                return None

            def terminate(self):
                self.calls.append("terminate")

            def kill(self):
                self.calls.append("kill")

            def wait(self, timeout=None):
                self.calls.append(f"wait({timeout})")
                if len(self.calls) < 4:             # the serve wait, then the wait for a clean stop
                    raise KeyboardInterrupt
                return 0
        child, said = Child(), []
        with mock.patch.object(cli, "_page_path", return_value=""), \
                mock.patch.object(cli, "_first_render"), \
                mock.patch.object(cli, "_port_busy", return_value="taken"), \
                mock.patch.object(cli.subprocess, "Popen", return_value=child):
            rc = cli._demo_serve(self.tmp, 1, said.append)
        self.assertEqual(rc, 0)
        self.assertEqual(child.calls, ["wait(None)", "terminate", "wait(10)", "kill", "wait(None)"])
        self.assertTrue(said[-1].startswith("stopped."), said)

    def test_serving_keeps_its_runtime_files_in_the_demo_folder_and_stops_on_ctrl_c(self):
        before = beside_code()
        port, log = free_port(), os.path.join(self.tmp, "demo.log")
        with open(log, "w") as out:
            proc = subprocess.Popen([sys.executable, CLI, "demo", "--port", str(port)],
                                    stdout=out, stderr=subprocess.STDOUT, env=self.env, cwd=HERE)

        def stop():
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(30)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        self.addCleanup(stop)

        def said():
            with open(log, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        end = time.time() + 120
        while "Ctrl-C stops the demo" not in said():
            self.assertIsNone(proc.poll(), "the demo exited early:\n" + said())
            self.assertLess(time.time(), end, "no URL after 120 s:\n" + said())
            time.sleep(0.2)
        self.assertIn(f"http://127.0.0.1:{port}/", said())

        def fetch(method, path, headers=None, value=None):
            # http.client, not urllib: urllib follows HTTP_PROXY and would never reach loopback
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
            headers = dict(headers or {})
            if value is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=None if value is None else json.dumps(value),
                         headers=headers)
            response = conn.getresponse()
            result = (response.status, dict(response.getheaders()), response.read())
            conn.close()
            return result
        status, headers, body = fetch("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("DEMO", body.decode("utf-8"))
        notes = os.path.join(self.workspace(), "notes-site")
        with open(os.path.join(notes, "README.md"), "rb") as handle:
            readme = handle.read()
        # The page offers the scripted agent on the parked TODO line, and that hand-off starts the
        # run in the tool's own folder: the agent has to refuse there and change nothing.
        ui = {"Cookie": headers["Set-Cookie"].split(";", 1)[0],
              "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin",
              "Sec-Fetch-Mode": "cors"}
        status, _, body = fetch("GET", "/state", ui)
        self.assertEqual(status, 200)
        world = json.loads(body)
        self.assertEqual(len([u for u in world["chars"] if u["kind"] == "area"]), 7)
        self.assertEqual(sorted(t["status"] for t in world["agent_tasks"]), ["merged", "review", "running"])
        running = next(t for t in world["agent_tasks"] if t["status"] == "running")
        (quest,) = world["quests"]
        self.assertIn("demo-agent", world["dispatchable"])
        status, _, body = fetch("POST", "/dispatch", ui,
                                {"kind": "quest", "id": quest["id"], "runner": "demo-agent"})
        self.assertEqual(status, 200, body)
        run_log = json.loads(body)["out"]
        end, run = time.time() + 30, ""
        while " · exit " not in run:
            self.assertLess(time.time(), end, "the hand-off never ended:\n" + run)
            time.sleep(0.2)
            with open(run_log, encoding="utf-8") as handle:
                run = handle.read()
        self.assertRegex(run, r"(?s)refusing to touch .*· exit 1 ·")
        self.assertEqual(beside_code(), before, "the hand-off did not touch the tool's folder")
        with open(os.path.join(notes, "README.md"), "rb") as handle:
            self.assertEqual(handle.read(), readme)
        porcelain = subprocess.run(["git", "status", "--porcelain"], cwd=notes, env=self.env,
                                   capture_output=True, text=True)
        self.assertEqual(porcelain.stdout, "", "notes-site's main branch is untouched")
        side = os.path.join(self.workspace(), ".container-map")
        self.assertTrue(os.path.isfile(os.path.join(side, ".bridge-live")))
        self.assertTrue(os.path.isfile(os.path.join(side, "game.html")))
        self.assertEqual(beside_code(), before, "the bridge's files are in the demo folder")
        proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(30), 0, said())
        self.assertIn("The workspace stays at", said())
        result = run_cli(["thread", "show", running["id"], "--json", "--root", self.workspace()], self.env)
        self.assertNotIn(json.loads(result.stdout)["thread"]["status"], ("running", "stopping"))
        self.assertEqual(cli._port_busy(port), "", "the bridge went with it")

    def test_serving_stops_on_ctrl_c_also_when_it_was_started_with_sigint_ignored(self):
        """A job a script put in the background starts with SIGINT ignored, and Python then keeps
        it ignored: the demo has to take Ctrl-C back, or nothing can stop it but a kill."""
        port, log = free_port(), os.path.join(self.tmp, "demo.log")
        with open(log, "w") as out:
            proc = subprocess.Popen(
                [sys.executable, CLI, "demo", "--port", str(port)], stdout=out,
                stderr=subprocess.STDOUT, env=self.env, cwd=HERE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))

        def said():
            with open(log, encoding="utf-8", errors="replace") as handle:
                return handle.read()

        def stop():
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        self.addCleanup(stop)
        end = time.time() + 120
        while "Ctrl-C stops the demo" not in said():
            self.assertIsNone(proc.poll(), "the demo exited early:\n" + said())
            self.assertLess(time.time(), end, "no URL after 120 s:\n" + said())
            time.sleep(0.2)
        proc.send_signal(signal.SIGINT)
        self.assertEqual(proc.wait(30), 0, said())
        self.assertIn("The workspace stays at", said())
        self.assertEqual(cli._port_busy(port), "", "the bridge went with it")


class UiLock(Fixture):
    """--lock and "ui_lock": serve hands the bridge a launch key and shows it only to a terminal; install writes
    --lock into the service; link shows the key again; doctor warns on a shared computer; pair and devices still work."""

    def serve_calls(self, args, tty, config=None):
        out, calls = io.StringIO(), []
        if config is not None:
            os.makedirs(os.path.join(self.root, ".container-map"), exist_ok=True)
            with open(os.path.join(self.root, ".container-map", "container.json"), "w") as handle:
                json.dump(config, handle)

        def record(argv, env=None, cwd=None):
            calls.append((os.path.basename(argv[-1]), env))
            return 0

        with mock.patch.object(cli, "_call", record), contextlib.redirect_stdout(out), \
                mock.patch.object(cli, "_is_tty", lambda: tty), mock.patch.dict(os.environ, {"HOME": self.home}):
            rc = cli.cmd_serve([self.root, "--port", str(free_port())] + args)
        self.assertEqual(rc, 0)
        return out.getvalue(), calls

    def test_serve_without_the_lock_is_unchanged(self):
        for tty in (True, False):
            out, calls = self.serve_calls([], tty)
            for _, env in calls:
                self.assertNotIn("BRIDGE_UI_LOCK", env)
                self.assertNotIn("BRIDGE_LAUNCH_KEY", env)
            self.assertIn("live at http://127.0.0.1:", out)
            self.assertNotIn("?k=", out)

    def test_serve_lock_prints_the_full_link_to_a_terminal_only(self):
        out, calls = self.serve_calls(["--lock"], tty=True)
        envs = [env for name, env in calls if name == "world.sh"]
        key = envs[0]["BRIDGE_LAUNCH_KEY"]
        self.assertEqual(envs[0]["BRIDGE_UI_LOCK"], "1")
        self.assertGreaterEqual(len(key), 40)
        self.assertRegex(out, r"http://127\.0\.0\.1:\d+/\?k=" + key)
        out2, calls2 = self.serve_calls(["--lock"], tty=True)
        self.assertNotEqual(calls2[-1][1]["BRIDGE_LAUNCH_KEY"], key)          # new at every start
        # a service's output is a log: the key stays out of it, and the line says how to get it
        out3, calls3 = self.serve_calls(["--lock"], tty=False)
        self.assertNotIn(calls3[-1][1]["BRIDGE_LAUNCH_KEY"], out3)
        self.assertNotIn("?k=", out3)
        self.assertIn("link --port", out3)

    def test_the_config_key_locks_serve_without_the_flag(self):
        out, calls = self.serve_calls([], tty=True, config={"version": 1, "ui_lock": True})
        self.assertIn("?k=", out)
        self.assertEqual(calls[-1][1]["BRIDGE_UI_LOCK"], "1")
        out, calls = self.serve_calls([], tty=True, config={"version": 1, "ui_lock": False})
        self.assertNotIn("?k=", out)

    def test_install_lock_writes_it_into_the_service_command(self):
        for platform in ("linux", "darwin"):
            plan = cli.service_plan(self.root, 7500, platform=platform, home=self.home, lock=True)
            self.assertIn("--lock", plan["text"])
            self.assertEqual(plan["text"].count("--lock"), 1)
            self.assertNotIn("--lock", cli.service_plan(self.root, 7500, platform=platform, home=self.home)["text"])
        r = run_cli(["install", self.root, "--port", "7500", "--dry-run", "--lock"], stranger_env(self.home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'ExecStart=.*"serve" ".*" "--port" "7500" "--lock"')
        r = run_cli(["install", self.root, "--port", "7500", "--dry-run"], stranger_env(self.home))
        self.assertNotIn("--lock", r.stdout)

    def test_install_prints_the_link_to_its_terminal_not_the_service_log(self):
        port = free_port()
        key = "L" * 43
        reads = iter([None, {"launch_key": key, "ui_lock": True}])
        with mock.patch.object(cli, "_run", lambda argv: (0, "")), \
                mock.patch.object(cli, "wait_for_bridge", lambda *a, **k: (True, "")), \
                mock.patch("config.read_live", lambda path: next(reads, {"launch_key": key})):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = cli.cmd_install([self.root, "--port", str(port), "--lock"], platform="linux", home=self.home,
                                     run=lambda argv: (0, ""), which=lambda name: "/usr/bin/" + name)
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn(f"http://127.0.0.1:{port}/?k={key}", out.getvalue())

    def test_link_prints_the_key_of_the_running_locked_map_and_only_that(self):
        port = free_port()
        live = os.path.join(self.root, ".container-map", "run", f"bridge-{port}.json")
        os.makedirs(os.path.dirname(live))
        with open(live, "w") as handle:
            json.dump({"version": 1, "pid": os.getpid(), "port": port, "agent_token": "t",
                       "launch_key": "K" * 43, "ui_lock": True}, handle)
        os.chmod(live, 0o600)
        r = run_cli(["link", self.root, "--port", str(port)], stranger_env(self.home))
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"http://127.0.0.1:{port}/?k={'K' * 43}"), r.stderr)
        os.chmod(live, 0o644)                       # readable by others: the key is not shown
        r = run_cli(["link", self.root, "--port", str(port)], stranger_env(self.home))
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertNotIn("K" * 43, r.stderr)
        os.chmod(live, 0o600)
        with open(live, "w") as handle:           # an unlocked map has no link to show
            json.dump({"version": 1, "pid": os.getpid(), "port": port, "agent_token": "t"}, handle)
        os.chmod(live, 0o600)
        r = run_cli(["link", self.root, "--port", str(port)], stranger_env(self.home))
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("not locked", r.stderr)
        with open(live, "w") as handle:           # a dead map's file proves nothing
            json.dump({"version": 1, "pid": 2 ** 22 + 1, "port": port, "launch_key": "K" * 43}, handle)
        os.chmod(live, 0o600)
        r = run_cli(["link", self.root, "--port", str(port)], stranger_env(self.home))
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertIn("no running map", r.stderr)

    def test_a_real_locked_serve_end_to_end(self):
        tool = os.path.join(self.tmp, "tool")
        tool_copy(tool)
        served = Served(self, tool, self.root, self.home, extra=("--lock",))
        served.wait_up(self)
        with open(served.live) as handle:
            live = json.load(handle)
        key = live["launch_key"]
        self.assertEqual(os.stat(served.live).st_mode & 0o777, 0o600)
        self.assertNotIn(key, "\n".join(served.lines), "the key reached serve's output, which a service logs")

        def get(path):
            conn = http.client.HTTPConnection("127.0.0.1", served.port, timeout=10)
            conn.request("GET", path)
            reply = conn.getresponse()
            reply.read()
            conn.close()
            return reply.status, reply.getheader("Set-Cookie"), reply.getheader("Location")

        self.assertEqual(get("/")[0], 403)
        self.assertEqual(get("/?k=wrong")[0], 403)
        status, cookie, where = get("/?k=" + key)
        self.assertEqual((status, where), (303, "/"))
        self.assertIn("HttpOnly", cookie)
        r = subprocess.run([sys.executable, os.path.join(tool, "cli.py"), "link", "--port", str(served.port)],
                           capture_output=True, text=True, env=stranger_env(self.home), cwd=self.root, timeout=60)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, f"http://127.0.0.1:{served.port}/?k={key}"), r.stderr)
        # the phone commands reach a locked map through the key in its private live file
        r = subprocess.run([sys.executable, os.path.join(tool, "cli.py"), "devices", "--port", str(served.port)],
                           capture_output=True, text=True, env=stranger_env(self.home), cwd=self.root, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("phone access", r.stdout)
        # from a folder that cannot find the live file, the lock is named, not a bare 403
        r = subprocess.run([sys.executable, os.path.join(tool, "cli.py"), "devices", "--port", str(served.port)],
                           capture_output=True, text=True, env=stranger_env(self.home), cwd=self.tmp, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("locked", r.stderr)
        self.assertNotIn(key, r.stdout + r.stderr)

    def doctor_out(self, people, config=None):
        out = io.StringIO()
        args = [self.root, "--port", str(free_port())]
        if config is not None:
            path = os.path.join(self.tmp, "c.json")
            with open(path, "w") as handle:
                json.dump(config, handle)
            args += ["--config", path]
        with mock.patch.object(cli, "human_accounts", lambda *a, **k: people), contextlib.redirect_stdout(out), \
                mock.patch.dict(os.environ, {"HOME": self.home}):
            rc = cli.cmd_doctor(args)
        return rc, out.getvalue()

    def test_doctor_warns_on_a_shared_computer_with_the_lock_off(self):
        base, text = self.doctor_out(["ann"])
        self.assertIn("ui lock", text)
        self.assertNotIn("warning", text)
        rc, text = self.doctor_out(["ann", "bob"])
        self.assertEqual(rc, base, "the warning alone never fails doctor")
        line = [l for l in text.splitlines() if "ui lock" in l]
        self.assertEqual(len(line), 1)
        for word in ("warning", "2 people", "ann", "bob", "--lock", "ui_lock", "start agent tasks"):
            self.assertIn(word, line[0])
        rc, text = self.doctor_out(["ann", "bob"], config={"version": 1, "ui_lock": True})
        self.assertEqual(rc, base)
        self.assertNotIn("warning", text)
        self.assertRegex(text, r"ui lock\s+on")

    def test_login_accounts_are_counted_by_user_id_and_shell(self):
        passwd = os.path.join(self.tmp, "passwd")
        with open(passwd, "w") as handle:
            handle.write("\n".join([
                "root:x:0:0:root:/root:/bin/bash",
                "daemon:x:1:1::/usr/sbin:/usr/sbin/nologin",
                "ann:x:1000:1000:Ann:/home/ann:/bin/bash",
                "bob:x:1001:1001:Bob:/home/bob:/usr/bin/zsh",
                "svc:x:1002:1002::/srv:/usr/sbin/nologin",
                "jobs:x:1003:1003::/srv:/bin/false",
                "noshell:x:1004:1004::/home/n:",
                "nobody:x:65534:65534:nobody:/nonexistent:/bin/bash",
                "bad line", ""]))
        self.assertEqual(cli.human_accounts("linux", passwd), ["ann", "bob"])
        self.assertEqual(cli.human_accounts("linux", os.path.join(self.tmp, "missing")), [])
        dscl = ("RecordName: alice\nUniqueID: 501\nUserShell: /bin/zsh\n-\n"
                "RecordName: bob\nUniqueID: 502\nUserShell: /bin/bash\n-\n"
                "RecordName: _www www\nUniqueID: 70\nUserShell: /usr/bin/false\n-\n"
                "RecordName: guest\nUniqueID: 201\nUserShell: /bin/bash\n-\n"
                "RecordName:\n daemon\nUniqueID: 501\nUserShell: /usr/bin/false")
        self.assertEqual(cli.human_accounts("darwin", run=lambda argv: (0, dscl)), ["alice", "bob"])
        self.assertEqual(cli.human_accounts("darwin", run=lambda argv: (1, "")), [])

    def test_help_and_the_flag_lists_name_the_lock(self):
        for cmd in ("serve", "install", "link"):
            self.assertIn(cmd, cli.HELP)
        self.assertIn("--lock", cli.HELP["serve"])
        self.assertIn("--lock", cli.HELP["install"])
        r = run_cli(["serve", "--bogus", self.root], stranger_env(self.home))
        self.assertEqual(r.returncode, 2)
        self.assertIn("--lock", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
