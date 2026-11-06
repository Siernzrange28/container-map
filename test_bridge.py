#!/usr/bin/env python3
"""Isolated tests: fake tmux, temporary files, and an ephemeral loopback HTTP server."""

from __future__ import annotations

import contextlib
import errno
import gzip
import http.client
import io
import json
import os
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  (first: clears what steers the tool)
import bridge
import cards


EMPTY = {
    "claude": "status line\n────────────────\n❯   \n────────────────\nfooter\n",
    "muse-bin-1.0.3": "work complete\n⟩ /effort adjusts reasoning depth vs speed\nstatus\n",
    "grok": "output mentioning composer with ❯ leftover\n╭────────────╮\n│ ❯          │\n╰────────────╯\n",
}


class FakeTmux:
    def __init__(self):
        self.windows = {
            "@0": {"index": "1", "name": "hub", "pane": "%0", "pid": "101",
                   "command": "claude", "screen": EMPTY["claude"]},
            "@23": {"index": "3", "name": "muse", "pane": "%23", "pid": "103",
                    "command": "muse-bin-1.0.3", "screen": EMPTY["muse-bin-1.0.3"]},
            "@24": {"index": "4", "name": "world", "pane": "%24", "pid": "104",
                    "command": "bash", "screen": "render loop\n"},
            "@25": {"index": "6", "name": "codex", "pane": "%25", "pid": "105",
                    "command": "codex", "screen": "completed\n❯ \n"},
            "@26": {"index": "6", "name": "grok", "pane": "%26", "pid": "106",
                    "command": "grok", "screen": EMPTY["grok"]},
            "@30": {"index": "2", "name": "grok", "pane": "%30", "pid": "102",
                    "command": "grok", "screen": EMPTY["grok"]},
            "@31": {"index": "7", "name": "chatbot", "pane": "%31", "pid": "107",
                    "command": "grok", "screen": EMPTY["grok"]},
        }
        self.sent = []

    @staticmethod
    def result(args, code=0, stdout="", stderr=""):
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    def __call__(self, args, timeout=8):
        if args[0] == "list-windows":
            rows = ["\x1f".join((wid, item["index"], item["name"], "1"))
                    for wid, item in self.windows.items()]
            return self.result(args, stdout="\n".join(rows) + "\n")
        if args[0] == "list-panes":
            item = self.windows.get(args[args.index("-t") + 1])
            if not item:
                return self.result(args, 1, stderr="missing")
            row = "\x1f".join((item["pane"], item["pid"], item["command"],
                                "1" if item.get("dead") else "0",
                                "1" if item.get("copy") else "0"))
            return self.result(args, stdout=row + "\n")
        if args[0] == "capture-pane":
            pane = args[args.index("-t") + 1]
            item = next((value for value in self.windows.values() if value["pane"] == pane), None)
            return self.result(args, 0 if item else 1, item["screen"] if item else "")
        if args[0] == "send-keys":
            self.sent.append(list(args))
            return self.result(args)
        return self.result(args, 1, stderr="unexpected fake tmux call")


class FakeHop:
    def __init__(self):
        self.calls = []
        self.prompts = []
        self.timeouts = set()
        self.agents = {
            "lab-b": [
                {"agent": "claude", "agent_status": "idle", "cwd": "/home/dev/notes",
                 "foreground_cwd": "/home/dev/notes", "pane_id": "w1:p1", "tab_id": "w1:t1",
                 "terminal_title": "Control herdr", "terminal_title_stripped": "Control herdr"},
                {"agent": "grok", "agent_status": "done", "cwd": "/home/dev/notes",
                 "foreground_cwd": "/home/dev/notes", "pane_id": "w1:p2", "tab_id": "w1:t1",
                 "terminal_title": "Review - grok"},
            ],
            "lab-a": [],
        }
        self.snapshots = {
            "lab-b": {
                "tabs": [{"tab_id": "w1:t1", "label": "Control"}],
                "panes": [
                    {"pane_id": "w1:p1", "tab_id": "w1:t1", "agent_status": "idle",
                     "cwd": "/home/dev/notes", "terminal_title": "Control herdr"},
                    {"pane_id": "w1:p2", "tab_id": "w1:t1", "agent_status": "done",
                     "cwd": "/home/dev/notes", "terminal_title": "Review - grok"},
                    {"pane_id": "w1:p3", "tab_id": "w1:t1", "agent_status": "unknown",
                     "cwd": "/home/dev", "label": "shell"},
                ],
            },
            "lab-a": {
                "tabs": [{"tab_id": "w1:t1", "label": "Main"}],
                "panes": [{"pane_id": "w1:p1", "tab_id": "w1:t1",
                           "agent_status": "unknown", "cwd": "/Users/dev", "label": "shell"}],
            },
        }
        self.screens = {("lab-b", "w1:p1"): "finished\n❯ ",
                        ("lab-b", "w1:p2"): "done\n❯ ",
                        ("lab-b", "w1:p3"): "$ ",
                        ("lab-a", "w1:p1"): "$ "}

    @staticmethod
    def command(stdout="", code=0):
        return {"returncode": code, "stdout": stdout, "stderr": "", "timed_out": False}

    def agent_document(self, machine):
        return json.dumps({"id": "cli:agent:list",
                           "result": {"agents": self.agents[machine], "type": "agent_list"}})

    def snapshot_document(self, machine):
        return json.dumps({"id": "cli:api:snapshot",
                           "result": {"snapshot": self.snapshots[machine],
                                      "type": "session_snapshot"}})

    def completed(self, op, commands, **extra):
        payload = {"protocol": 1, "op": op, "commands": commands, **extra}
        return subprocess.CompletedProcess(["fleet"], 0, json.dumps(payload), "")

    def __call__(self, machine, request, timeout=4, via=None):
        self.calls.append((machine, json.loads(json.dumps(request)), timeout))
        if machine in self.timeouts:
            raise subprocess.TimeoutExpired(["fleet"], timeout)
        op = request["op"]
        if op == "state":
            return self.completed(op, {
                "agent_list": self.command(self.agent_document(machine)),
                "snapshot": self.command(self.snapshot_document(machine)),
            })
        if op == "read":
            return self.completed(op, {
                "read": self.command(self.screens.get((machine, request["target"]), "")),
            })
        if op != "say":
            return self.completed(op, {}, error="invalid request")

        commands = {"agent_list": self.command(self.agent_document(machine))}
        matches = [item for item in self.agents[machine]
                   if item["pane_id"] == request["target"]]
        current = matches[0] if len(matches) == 1 else None
        if current is None:
            return self.completed(op, commands, guard="missing_agent", agent=None)
        if current["agent"] != request["agent"]:
            return self.completed(op, commands, guard="agent_mismatch", agent=current)
        if current["agent_status"] == "working":
            return self.completed(op, commands, guard="working", agent=current)
        if current["agent_status"] not in ("idle", "blocked"):
            return self.completed(op, commands, guard="status", agent=current)
        screen = self.screens.get((machine, request["target"]), "")
        commands["read"] = self.command(screen)
        if bridge.remote_composer_has_draft(screen):
            return self.completed(op, commands, guard="draft", agent=current)
        commands["recheck"] = self.command(self.agent_document(machine))
        self.prompts.append((machine, request["target"], request["text"]))
        commands["prompt"] = self.command('{"ok":true}')
        return self.completed(op, commands, guard=None, agent=current)


def shipped_seats():
    """A seats.json fixture: six tmux seats and two machines. Never the file of whoever runs the
    tests (it would depend on their cwd and their seats)."""
    return {"version": 1, "session": "main",
            "seats": [{"id": "hub", "window_id": "@0", "expected_name": "hub", "role": "orchestrator", "quick_key": "1", "talk_commands": ["claude"]},
                      {"id": "muse", "window_id": "@23", "expected_name": "muse", "role": "builder", "quick_key": "3", "talk_commands": ["muse"]},
                      {"id": "world", "window_id": "@24", "expected_name": "world", "role": "runtime", "quick_key": "4", "talk_commands": []},
                      {"id": "grok", "window_id": "@26", "expected_name": "grok", "role": "critic", "quick_key": "6", "talk_commands": ["grok"]},
                      {"id": "codex", "expected_name": "codex", "role": "builder", "talk_commands": ["codex"]},
                      {"id": "grok2", "expected_name": "grok", "role": "critic", "talk_commands": ["grok"]}],
            "machines": {"lab-b": {"seats": [], "render_cmd": "cd ~/container-map && python3 cli.py render ~/work"},
                         "lab-a": {"seats": [], "render_cmd": "cd ~/container-map && python3 cli.py render ~/work"}}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.seats = os.path.join(root, "seats.json")
        seats_data = shipped_seats()
        seats_data["machines"]["lab-b"]["seats"] = [
            {"id": "scout", "pane_id": "w1:p1", "agent": "claude",
             "talk_commands": ["claude"]},
        ]
        with open(self.seats, "w", encoding="utf-8") as target:
            json.dump(seats_data, target)
        self.game = os.path.join(root, "game.html")
        with open(self.game, "w", encoding="utf-8") as handle:
            handle.write("""<!doctype html><style>body{color:white}</style><script>
const W = {};
const LIVE = location.protocol === 'http:';
function closeAll(){}
</script>""")
        self.live = os.path.join(root, ".bridge-live")
        self.log = os.path.join(root, "bridge.log")
        self.synced = os.path.join(root, "synced")
        self.render_calls = []
        self.areas = os.path.join(root, "areas.json")
        with open(self.areas, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "areas": {}}, handle)
        self.opened = []
        self.doors = []
        self.dispatches = []
        self.dispatch_rc = 0
        self.runners = os.path.join(root, "runners.json")
        with open(self.runners, "w", encoding="utf-8") as handle:
            json.dump({"_comment": "registry", "deepseek": {"cmd": "/x/deepseek", "desc": "grind"},
                       "kimi": {"cmd": "/x/kimi", "desc": "\U0001F534 DOWN since 2026-08-07"},
                       "claude": {"cmd": "/x/claude", "desc": "retiring"}}, handle)
        self.runs = os.path.join(root, "state", "task-runs.jsonl")
        self.verbs_dir = os.path.join(root, "verbs")
        os.makedirs(self.verbs_dir)
        for name, obj in {
            "size": {"name": "size", "label": "how big", "applies": ["area", "dir"], "cmd": ["/bin/sh", "-c", "pwd; echo SIZE:{name}"], "max_minutes": 1},
            "shout": {"name": "shout", "label": "shout", "applies": ["file"], "cmd": ["/bin/sh", "-c", "echo {text} | tr a-z A-Z"], "cwd": "tool"},
            "ask": {"name": "ask", "label": "ask a runner", "applies": ["any"], "runner": "*", "prompt": "about {path}: {text} (verbs live in {verbs_dir})"},
            "broken": {"name": "nope", "cmd": ["x"]},
            "both": {"name": "both", "cmd": ["x"], "runner": "y", "prompt": "p"},
        }.items():
            with open(os.path.join(self.verbs_dir, name + ".json"), "w", encoding="utf-8") as handle:
                json.dump(obj, handle)
        self.rulings = os.path.join(root, "state", "quest-rulings.json")
        self.door_rc = 0
        self.render_rc = 0
        self.fake = FakeTmux()
        self.hop = FakeHop()
        self.world_data = {
            "chars": [
                {"kind": "seat", "name": "hub", "role": "tmux window 1", "window_id": "@0"},
                {"kind": "seat", "name": "grok", "role": "tmux window 6",
                 "window_id": "@26", "uid": "seat:grok"},
                {"kind": "seat", "name": "grok", "role": "tmux window 7",
                 "window_id": "@99", "uid": "seat:other-grok"},
                {"kind": "runner", "name": "</script><script id=pwn>1</script>"},
            ],
            "quests": [{"id": "q-abc123def0", "days": 45, "src": "DIGEST.md:89", "text": "auth still awaiting his click"}],
            "tasks": {"source": "audit", "items": [
                {"id": "t-one", "who": "agent", "verdict": "MOVE", "area": "x", "path": root,
                 "finding": "a loose file", "evidence": "ls shows it", "command": "mv a b"}]},
            "activity": [], "inflight": [], "generated": "now", "oldest": 45,
        }
        self.app = bridge.BridgeApp(
            seats_path=self.seats, game_path=self.game, renderer_path="unused",
            live_path=self.live, log_path=self.log, tmux_runner=self.fake,
            world_loader=lambda: self.world_data, hop_runner=self.hop, synced_dir=self.synced,
            render_runner=self.render, file_roots=(root,), areas_path=self.areas,
            open_runner=self.opened.append, door_runner=self.door,
            dispatch_runner=self.dispatch, dispatch_bin="/x/dispatch", runners_path=self.runners,
            runs_path=self.runs, rulings_path=self.rulings, verbs_dir=self.verbs_dir,
            runners={"echo-arg": {"cmd": ["/bin/sh", "-c", "pwd; echo ARG:$0", "{text}"], "cwd": "item"},
                     "echo-stdin": {"cmd": ["/bin/sh", "-c", "cat | tr a-z A-Z; exit 3"], "cwd": "tool", "max_minutes": 1},
                     "sleeper": {"cmd": ["/bin/sh", "-c", "sleep 30"], "max_minutes": 0.02},
                     "BAD name": {"cmd": ["x"]}, "nocmd": {"cwd": "item"}},
            tasks_dir=os.path.join(root, "tasks"),
            task_runners={"writer": {"cmd": ["/bin/sh", "-c", "cat >/dev/null; echo made > made.txt; "
                                             "echo '{\"type\":\"result\",\"subtype\":\"success\"}'"], "max_minutes": 1}},
            agent_verify=False,
        )
        self.server = bridge.BridgeHTTPServer((bridge.LOOPBACK, 0), bridge.Handler, self.app)
        self.app.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def http(self, method, path, *, headers=None, value=None):
        body = None if value is None else json.dumps(value)
        headers = dict(headers or {})
        if value is not None:
            headers["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        conn.close()
        return result

    def open_ui(self):
        status, headers, data = self.http("GET", "/")
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        return cookie, headers, data

    def ui_headers(self, *, origin=False):
        cookie, _, _ = self.open_ui()
        headers = {"Cookie": cookie}
        if origin:
            headers["Origin"] = self.origin
            headers["Sec-Fetch-Site"] = "same-origin"          # what a browser sends on its own fetches
            headers["Sec-Fetch-Mode"] = "cors"
        return headers

    def pane(self, seat="hub"):
        status, _, data = self.http("GET", f"/pane?seat={seat}&lines=120",
                                    headers=self.ui_headers())
        self.assertEqual(status, 200)
        return json.loads(data)

    def test_root_is_token_free_same_origin_and_xss_safe(self):
        cookie, headers, data = self.open_ui()
        text = data.decode()
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertIn("Content-Security-Policy", headers)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn(self.app.agent_token, text)
        self.assertNotIn("</script><script id=pwn>", text)
        self.assertIn("\\u003c/script\\u003e", text)
        self.assertTrue(cookie.startswith(bridge.COOKIE_NAME + "="))

    def render_real_page(self):
        """The page render-game.py writes for an empty folder: nothing of this machine in it."""
        tool = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(self.tmp.name, "real-root")
        os.makedirs(os.path.join(root, "notes"))
        config = os.path.join(self.tmp.name, "real-config.json")
        with open(config, "w", encoding="utf-8") as handle:
            json.dump({"work_root": root, "tmux_session": "none", "processes": False,
                       "decision_scan": False, "crontab_file": os.path.join(root, "no-crontab"),
                       "cron_syslog_file": "", "history_jsonl": os.path.join(root, "history.jsonl"),
                       "tasks_dir": os.path.join(root, "tasks"), "name": "REAL"}, handle)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.tmp.name,
               "CONTAINER_CONFIG": config, "CONTAINER_OUT": self.game}
        run = subprocess.run([sys.executable, os.path.join(tool, "render-game.py")], cwd=tool,
                             env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])

    def test_the_policy_covers_the_pages_inlined_font_and_every_inline_script(self):
        self.render_real_page()
        _, headers, data = self.open_ui()
        policy = headers["Content-Security-Policy"]
        directives = {d.split(" ", 1)[0]: d.split(" ", 1)[1:] for d in policy.split("; ")}
        html = data.decode("utf-8")
        self.assertIn("url(data:font/ttf;base64,", html)      # the Silkscreen face is inlined
        self.assertIn("data:", directives.get("font-src", [""])[0].split(),
                      "default-src 'none' blocks a data: font unless font-src names it")
        nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
        tags = re.findall(r"<(script|style)\b([^>]*)>", html)
        scripts = [t for t in tags if t[0] == "script"]
        self.assertGreaterEqual(len(scripts), 2, tags)        # theme-early + the page
        for name, attrs in tags:
            self.assertIn(f'nonce="{nonce}"', attrs, f"<{name}{attrs}> would be blocked")
        self.assertIn(f'<script id="theme-early" nonce="{nonce}">', html)
        self.assertIn(f'<script id="mobile-early" nonce="{nonce}">', html)    # the phone's class before first paint
        self.assertFalse(re.search(r"<[a-z][^<>]*\son[a-z]+\s*=", html),
                         "an inline event handler would need its own hash in script-src")

    def test_a_nonce_goes_on_every_inline_script_and_style_and_nowhere_else(self):
        html = ("<!doctype html><script id=\"a\">x</script><style>b{}</style>"
                "<script>var s = '<script>'; var t = \"<style>\";</script>"
                "<script src=\"/x.js\"></script><style nonce=\"keep\"></style>"
                "<scripture>hi</scripture>")
        cuts = bridge.nonce_cuts(html)
        out = html
        for at in reversed(cuts):
            out = out[:at] + ' nonce="N"' + out[at:]
        self.assertEqual(out, "<!doctype html><script id=\"a\" nonce=\"N\">x</script>"
                              "<style nonce=\"N\">b{}</style>"
                              "<script nonce=\"N\">var s = '<script>'; var t = \"<style>\";"
                              "</script><script src=\"/x.js\" nonce=\"N\"></script>"
                              "<style nonce=\"keep\"></style><scripture>hi</scripture>")

    def test_bad_host_origin_null_and_prefix_origin_are_rejected(self):
        status, headers, _ = self.http("GET", "/", headers={"Host": "localhost.evil.example"})
        self.assertEqual(status, 421)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        for origin in ("null", "http://localhost.evil.example", "https://evil.example"):
            status, _, _ = self.http("GET", "/", headers={"Origin": origin})
            self.assertEqual(status, 403)
        status, _, _ = self.http("OPTIONS", "/control", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_state_and_pane_require_ui_cookie(self):
        self.assertEqual(self.http("GET", "/state")[0], 403)
        headers = self.ui_headers()
        status, _, data = self.http("GET", "/state", headers=headers)
        self.assertEqual(status, 200)
        world = json.loads(data)
        self.assertEqual(world["chars"][0]["uid"], "seat:hub")
        self.assertEqual(world["chars"][1]["uid"], "seat:grok")
        self.assertEqual(world["chars"][2]["uid"], "seat:other-grok")
        self.assertEqual(self.pane("@31")["name"], "chatbot")

    def test_pane_shape_and_line_bound(self):
        snapshot = self.pane("seat:grok")
        expected = {"seat", "uid", "window_id", "window_index", "name", "pane_id",
                    "command", "configured", "sendable", "send_reason", "terminal_state",
                    "waiting", "permit", "revision", "text", "lines", "truncated"}
        self.assertEqual(set(snapshot), expected)
        self.assertTrue(snapshot["sendable"])
        self.assertEqual(snapshot["terminal_state"], "empty")
        status, _, _ = self.http("GET", "/pane?seat=hub&lines=201", headers=self.ui_headers())
        self.assertEqual(status, 400)

    def test_muse_prefix_command_and_placeholder_are_sendable_empty(self):
        snapshot = self.pane("muse")
        self.assertEqual(snapshot["command"], "muse-bin-1.0.3")
        self.assertTrue(snapshot["sendable"])
        self.assertEqual(snapshot["terminal_state"], "empty")

    def test_say_is_cookie_only_revision_guarded_and_literal(self):
        snap = self.pane("hub")
        body = {"seat": "seat:hub", "text": "literal $() ; `whoami`", "revision": snap["revision"]}
        agent = {"Authorization": "Bearer " + self.app.agent_token, "Origin": self.origin}
        self.assertEqual(self.http("POST", "/say", headers=agent, value=body)[0], 403)
        headers = self.ui_headers(origin=True)
        status, _, data = self.http("POST", "/say", headers=headers, value=body)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.fake.sent[-2], ["send-keys", "-t", "%0", "-l", "--", body["text"]])
        self.assertEqual(self.fake.sent[-1], ["send-keys", "-t", "%0", "Enter"])
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        self.assertNotIn(body["text"], rows[0].values())
        self.assertEqual(stat.S_IMODE(os.stat(self.log).st_mode), 0o600)

    def test_say_refuses_runtime_shell_draft_unknown_and_stale_revision(self):
        headers = self.ui_headers(origin=True)
        world = self.pane("world")
        body = {"seat": "world", "text": "hello", "revision": world["revision"]}
        self.assertEqual(self.http("POST", "/say", headers=headers, value=body)[0], 403)
        hub = self.pane("hub")
        self.fake.windows["@0"]["screen"] = "❯ human draft\n"
        body = {"seat": "hub", "text": "hello", "revision": hub["revision"]}
        self.assertEqual(self.http("POST", "/say", headers=headers, value=body)[0], 409)
        self.assertEqual(self.fake.sent, [])

    def test_waiting_prompt_reads_keys_from_the_printed_text(self):
        yn = bridge.waiting_prompt("Allow this command?\n[y/n]")
        self.assertEqual((yn["allow"], yn["deny"]), ("y", "n"))
        menu = bridge.waiting_prompt("Do you want to proceed?\n❯ 1. Yes\n  2. No")
        self.assertEqual((menu["allow"], menu["deny"]), ("1", "2"))
        enter = bridge.waiting_prompt("Press enter to continue")
        self.assertEqual((enter["allow"], enter["deny"]), ("", None))
        self.assertIsNone(bridge.waiting_prompt("❯ leftover draft with no prompt"))

    def test_permit_allow_sends_the_prompt_key_not_a_sentence(self):
        self.fake.windows["@0"]["screen"] = "Allow this command? (y/n)\n❯ \n"
        snap = self.pane("hub")
        self.assertTrue(snap["waiting"])
        self.assertEqual(snap["permit"]["allow"], "y")
        headers = self.ui_headers(origin=True)
        agent = {"Authorization": "Bearer " + self.app.agent_token, "Origin": self.origin}
        body = {"seat": "seat:hub", "decision": "allow", "revision": snap["revision"]}
        self.assertEqual(self.http("POST", "/permit", headers=agent, value=body)[0], 403)
        status, _, data = self.http("POST", "/permit", headers=headers, value=body)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.fake.sent[-2], ["send-keys", "-t", "%0", "-l", "--", "y"])
        self.assertEqual(self.fake.sent[-1], ["send-keys", "-t", "%0", "Enter"])

    def test_permit_refuses_idle_draft_runtime_and_stale_revision(self):
        headers = self.ui_headers(origin=True)
        idle = self.pane("hub")
        body = {"seat": "hub", "decision": "allow", "revision": idle["revision"]}
        self.assertEqual(self.http("POST", "/permit", headers=headers, value=body)[0], 409)
        world = self.pane("world")
        body = {"seat": "world", "decision": "deny", "revision": world["revision"]}
        self.assertEqual(self.http("POST", "/permit", headers=headers, value=body)[0], 403)
        self.fake.windows["@0"]["screen"] = "Allow this command? (y/n)\n❯ please no\n"
        draft = self.pane("hub")
        body = {"seat": "hub", "decision": "allow", "revision": draft["revision"]}
        self.assertEqual(self.http("POST", "/permit", headers=headers, value=body)[0], 409)
        self.fake.windows["@0"]["screen"] = "Allow this command? (y/n)\n❯ \n"
        wait = self.pane("hub")
        self.fake.windows["@0"]["screen"] = "❯ leftover\n"
        body = {"seat": "hub", "decision": "allow", "revision": wait["revision"]}
        self.assertEqual(self.http("POST", "/permit", headers=headers, value=body)[0], 409)
        self.assertEqual(self.fake.sent, [])

    def test_control_actions_are_bounded_sequenced_and_agent_only(self):
        auth = {"Authorization": "Bearer " + self.app.agent_token}
        value = {"request_id": "move-test-0001",
                 "action": {"type": "move", "direction": "left", "tiles": 12}}
        self.assertEqual(self.http("POST", "/control", value=value)[0], 403)
        status, _, data = self.http("POST", "/control", headers=auth, value=value)
        self.assertEqual(status, 202, data)
        queued = json.loads(data)
        self.assertEqual(queued["sequence"], 1)
        status, _, data = self.http("GET", "/control?after=0", headers=auth)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["commands"][0]["action"], value["action"])
        self.app.last_control = 0
        bad = {"request_id": "move-test-0002",
               "action": {"type": "move", "direction": "left", "tiles": 13}}
        self.assertEqual(self.http("POST", "/control", headers=auth, value=bad)[0], 400)

    def test_compose_only_targets_configured_tui_and_never_tmux(self):
        auth = {"Authorization": "Bearer " + self.app.agent_token}
        value = {"request_id": "compose-test-01",
                 "action": {"type": "compose", "target": "seat:grok", "text": "draft only"}}
        status, _, data = self.http("POST", "/control", headers=auth, value=value)
        self.assertEqual(status, 202, data)
        self.assertEqual(json.loads(data)["command"]["action"]["target"], "seat:grok")
        self.assertEqual(self.fake.sent, [])
        self.app.last_control = 0
        value = {"request_id": "compose-test-02",
                 "action": {"type": "compose", "target": "seat:world", "text": "no"}}
        self.assertEqual(self.http("POST", "/control", headers=auth, value=value)[0], 400)

    def test_control_idempotency_does_not_duplicate(self):
        value = {"request_id": "dismiss-test-01", "action": {"type": "dismiss"}}
        first_status, first = self.app.enqueue(value)
        second_status, second = self.app.enqueue(value)
        self.assertEqual((first_status, second_status), (202, 200))
        self.assertEqual(first["sequence"], second["sequence"])
        self.assertEqual(len(self.app.queue), 1)

    def test_composer_fixtures_fail_closed(self):
        fixtures = [
            ("claude", "old output says ❯ leftover\n❯ ", "empty"),
            ("claude", "❯ half written", "dirty"),
            ("claude", "Thinking...", "busy"),
            ("claude", "ordinary finished output", "unknown"),
            ("muse-bin-1.0.3", "⟩ /effort adjusts reasoning depth vs speed", "empty"),
            ("muse-bin-1.0.3", "⟩ change the file", "dirty"),
            ("grok", "│ ❯       │", "empty"),
            ("grok", "│ ❯ do work │", "dirty"),
            ("bash", "$ echo hello", "unknown"),
        ]
        for command, screen, expected in fixtures:
            with self.subTest(command=command, screen=screen):
                self.assertEqual(bridge.composer_probe(command, screen)[0], expected)

    def test_name_resolution_uses_one_match_and_fails_closed_at_zero_or_two(self):
        self.assertEqual(self.app.resolve("codex", configured_only=True)["window_id"], "@25")
        del self.fake.windows["@25"]
        with self.assertRaises(bridge.BridgeError) as caught:
            self.app.resolve("codex", configured_only=True)
        self.assertEqual((caught.exception.status, caught.exception.message),
                         (409, "ambiguous or missing window name"))
        self.fake.windows["@25"] = {
            "index": "6", "name": "codex", "pane": "%25", "pid": "105",
            "command": "codex", "screen": "❯ ",
        }
        self.fake.windows["@32"] = {
            "index": "8", "name": "codex", "pane": "%32", "pid": "108",
            "command": "codex", "screen": "❯ ",
        }
        with self.assertRaises(bridge.BridgeError) as caught:
            self.app.resolve("codex", configured_only=True)
        self.assertEqual((caught.exception.status, caught.exception.message),
                         (409, "ambiguous or missing window name"))
        with self.assertRaises(bridge.BridgeError) as caught:
            self.app.resolve("grok2", configured_only=True)
        self.assertEqual(caught.exception.message, "ambiguous or missing window name")

    def test_stale_window_pin_falls_back_to_one_exact_name(self):
        self.fake.windows["@40"] = self.fake.windows.pop("@23")
        self.fake.windows["@40"]["pane"] = "%40"
        self.assertEqual(self.app.resolve("muse", configured_only=True)["window_id"], "@40")

    def test_remote_bad_machine_is_400_without_a_hop(self):
        status, _, data = self.http("GET", "/remote/state?machine=lab-c",
                                    headers=self.ui_headers())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(data)["error"], "machine must be one of: lab-a, lab-b")
        self.assertEqual(self.hop.calls, [])

    def test_remote_timeout_is_504_with_machine_name(self):
        self.hop.timeouts.add("lab-b")
        status, _, data = self.http("GET", "/remote/state?machine=lab-b",
                                    headers=self.ui_headers())
        self.assertEqual(status, 504)
        self.assertEqual(json.loads(data), {"error": "lab-b did not answer"})

    def test_remote_state_merges_agents_uses_one_hop_and_caches(self):
        headers = self.ui_headers()
        status, _, data = self.http("GET", "/remote/state?machine=lab-b", headers=headers)
        self.assertEqual(status, 200, data)
        state = json.loads(data)
        self.assertEqual(set(state), {"machine", "reachable", "hop_ms", "tabs", "note"})
        self.assertEqual((state["machine"], state["reachable"], state["note"]),
                         ("lab-b", True, None))
        panes = state["tabs"][0]["panes"]
        self.assertEqual([(pane["agent"], pane["agent_status"])
                          for pane in panes[:2]], [("claude", "idle"), ("grok", "done")])
        self.assertTrue(panes[0]["talkable"])
        self.assertFalse(panes[1]["talkable"])
        self.assertIsNone(panes[2]["agent"])
        self.assertEqual(len(self.hop.calls), 1)
        self.assertEqual(self.hop.calls[0][1], {"op": "state"})
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b", headers=headers)[0], 200)
        self.assertEqual(len(self.hop.calls), 1)

    def test_remote_zero_agents_is_success_with_exact_note(self):
        status, _, data = self.http("GET", "/remote/state?machine=lab-a",
                                    headers=self.ui_headers())
        self.assertEqual(status, 200, data)
        state = json.loads(data)
        self.assertEqual(state["note"], "no agents detected on lab-a")
        self.assertEqual(state["tabs"][0]["panes"][0]["agent"], None)

    def test_remote_pane_requires_last_state_and_returns_bounded_text(self):
        headers = self.ui_headers()
        self.assertEqual(self.http(
            "GET", "/remote/pane?machine=lab-b&target=w1:p1", headers=headers)[0], 400)
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b", headers=headers)[0], 200)
        status, _, data = self.http(
            "GET", "/remote/pane?machine=lab-b&target=w1:p1", headers=headers)
        self.assertEqual(status, 200, data)
        pane = json.loads(data)
        self.assertEqual(set(pane), {"machine", "target", "text", "lines", "truncated"})
        self.assertEqual((pane["machine"], pane["target"]), ("lab-b", "w1:p1"))
        self.assertIn("❯", pane["text"])

    def test_remote_say_rechecks_cached_idle_and_refuses_working(self):
        headers = self.ui_headers(origin=True)
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b",
                                   headers=headers)[0], 200)
        self.hop.agents["lab-b"][0]["agent_status"] = "working"
        body = {"machine": "lab-b", "target": "w1:p1", "text": "do the work"}
        status, _, data = self.http("POST", "/remote/say", headers=headers, value=body)
        self.assertEqual(status, 409, data)
        self.assertEqual(json.loads(data)["error"], "agent is working")
        self.assertEqual(self.hop.prompts, [])

    def test_remote_say_refuses_last_line_human_draft(self):
        headers = self.ui_headers(origin=True)
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b",
                                   headers=headers)[0], 200)
        self.hop.screens[("lab-b", "w1:p1")] = (
            "earlier output has ❯ noise\n\x1b[32m  ❯ list what's running in the other panes\x1b[0m")
        body = {"machine": "lab-b", "target": "w1:p1", "text": "do not overwrite"}
        status, _, data = self.http("POST", "/remote/say", headers=headers, value=body)
        self.assertEqual(status, 409, data)
        self.assertEqual(json.loads(data)["error"], "remote composer contains an unsent draft")
        self.assertEqual(self.hop.prompts, [])

    def test_remote_say_is_human_only_literal_audited_and_invalidates_cache(self):
        state_headers = self.ui_headers()
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b",
                                   headers=state_headers)[0], 200)
        body = {"machine": "lab-b", "target": "w1:p1",
                "text": "literal $() ; `whoami` and 'quotes'"}
        agent_headers = {"Authorization": "Bearer " + self.app.agent_token,
                         "Origin": self.origin}
        self.assertEqual(self.http("POST", "/remote/say", headers=agent_headers,
                                   value=body)[0], 403)
        headers = self.ui_headers(origin=True)
        status, _, data = self.http("POST", "/remote/say", headers=headers, value=body)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.hop.prompts[-1], ("lab-b", "w1:p1", body["text"]))
        self.assertNotIn(body["text"], bridge.REMOTE_HELPER_COMMAND)
        self.assertNotIn("lab-b", self.app.remote_cache)
        with open(self.log, encoding="utf-8") as handle:
            audit = json.loads(handle.readline())
        self.assertEqual((audit["event"], audit["machine"], audit["target"]),
                         ("remote_say", "lab-b", "w1:p1"))
        self.assertNotIn(body["text"], audit.values())
        self.assertEqual(stat.S_IMODE(os.stat(self.log).st_mode), 0o600)

    def test_remote_say_rejects_unknown_target_and_unicode_line_separator(self):
        headers = self.ui_headers(origin=True)
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b",
                                   headers=headers)[0], 200)
        unknown = {"machine": "lab-b", "target": "w1:p9", "text": "hello"}
        self.assertEqual(self.http("POST", "/remote/say", headers=headers,
                                   value=unknown)[0], 400)
        bad_line = {"machine": "lab-b", "target": "w1:p1", "text": "one\u2028two"}
        self.assertEqual(self.http("POST", "/remote/say", headers=headers,
                                   value=bad_line)[0], 400)

    def test_each_machine_seat_names_one_talk_command(self):
        # a seat names ONE talk command, so a pane running anything else stays view-only
        data = shipped_seats()
        data["machines"]["lab-b"]["seats"] = [
            {"id": "lab-b-claude", "pane_id": "w1:p1", "agent": "claude",
             "talk_commands": ["claude"]},
            {"id": "lab-b-grok", "pane_id": "w1:p2", "agent": "grok", "talk_commands": ["grok"]}]
        path = os.path.join(self.tmp.name, "seats-talk.json")
        with open(path, "w", encoding="utf-8") as target:
            json.dump(data, target)
        registry = bridge.SeatRegistry(path)
        seats = registry.machines["lab-b"]["seats"]
        self.assertEqual([s["id"] for s in seats], ["lab-b-claude", "lab-b-grok"])
        for seat in seats:
            self.assertEqual(len(seat["talk_commands"]), 1)
        self.assertEqual(registry.machines["lab-a"]["seats"], [])

    def test_live_metadata_is_private_token_is_control_only_and_cleanup_is_owned(self):
        self.app.write_live()
        self.assertEqual(stat.S_IMODE(os.stat(self.live).st_mode), 0o600)
        with open(self.live, encoding="utf-8") as handle:
            raw = json.load(handle)
        self.assertEqual(raw["agent_token"], self.app.agent_token)
        self.assertNotIn("token", raw)
        raw["boot_id"] = "another-process"
        with open(self.live, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        os.chmod(self.live, 0o600)
        self.app.remove_live()
        self.assertTrue(os.path.exists(self.live))


    def render(self, machine, timeout, via=None, command=""):
        self.render_routes = getattr(self, "render_routes", []) + [(machine, via, command)]
        self.render_calls.append((machine, timeout))
        return subprocess.CompletedProcess(["fleet"], self.render_rc,
                                           "out/x/game.html  (3 characters)\n" if self.render_rc == 0 else "",
                                           "boom" if self.render_rc else "")

    def test_remote_render_is_ui_only_constant_rate_limited_and_audited(self):
        headers = self.ui_headers(origin=True)
        status, _, _ = self.http("POST", "/remote/render", value={"machine": "lab-b"})
        self.assertEqual(status, 403)
        status, _, _ = self.http("POST", "/remote/render", headers=headers, value={"machine": "lab-c"})
        self.assertEqual(status, 400)
        status, _, _ = self.http("POST", "/remote/render", headers=headers, value={"machine": "lab-b", "x": 1})
        self.assertEqual(status, 400)
        status, _, data = self.http("POST", "/remote/render", headers=headers, value={"machine": "lab-b"})
        self.assertEqual(status, 200)
        first = json.loads(data)
        self.assertEqual(first["rc"], 0)
        self.assertIn("game.html", first["tail"][-1])
        self.assertEqual(self.render_calls, [("lab-b", bridge.RENDER_TIMEOUT)])
        status, _, data = self.http("POST", "/remote/render", headers=headers, value={"machine": "lab-b"})
        self.assertEqual(status, 429)                                    # once a minute per machine
        self.assertEqual(len(self.render_calls), 1)
        self.render_rc = 1
        status, _, data = self.http("POST", "/remote/render", headers=headers, value={"machine": "lab-a"})
        self.assertEqual(status, 502)
        self.assertIn("exited 1", json.loads(data)["error"])
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        renders = [e for e in events if e.get("event") == "remote_render"]
        self.assertEqual([r["machine"] for r in renders], ["lab-b", "lab-a"])
        self.assertEqual(self.hop.calls, [])                             # never the herdr hop

    def dispatch(self, argv, cwd):
        self.dispatches.append((argv, cwd))
        return subprocess.CompletedProcess(argv, self.dispatch_rc,
                                           "dispatched d-20260905-120000-deepseek (bg) \u2192 /out/d-20260905-120000-deepseek.md", "")

    def test_dispatch_verb_hands_a_board_item_to_a_live_runner_from_its_folder(self):
        headers = self.ui_headers(origin=True)
        root = self.tmp.name
        status, _, _ = self.http("POST", "/dispatch", value={"kind": "task", "id": "t-one", "runner": "deepseek"})
        self.assertEqual(status, 403)                                   # UI session required
        status, _, data = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-one", "runner": "kimi"})
        self.assertEqual(status, 400)                                   # dead in the registry
        self.assertIn("not a dispatchable", json.loads(data)["error"])
        status, _, _ = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-one", "runner": "claude"})
        self.assertEqual(status, 400)                                   # the retiring seat is never offered
        status, _, _ = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-nope", "runner": "deepseek"})
        self.assertEqual(status, 404)
        status, _, _ = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-one", "runner": "deepseek", "text": "rm -rf /"})
        self.assertEqual(status, 400)                                   # the request never supplies the text
        status, _, data = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-one", "runner": "deepseek"})
        self.assertEqual(status, 200)
        j = json.loads(data)
        self.assertEqual(j["rid"], "d-20260905-120000-deepseek")
        self.assertEqual(j["out"], "/out/d-20260905-120000-deepseek.md")
        argv, cwd = self.dispatches[-1]
        self.assertEqual(argv[:2], ["/x/dispatch", "deepseek"])
        self.assertEqual(argv[-1], "--bg")
        self.assertIn("a loose file", argv[2])
        self.assertIn("run only if you agree it is safe: mv a b", argv[2])
        self.assertEqual(cwd, os.path.realpath(root))                    # the agent starts IN the folder
        status, _, _ = self.http("POST", "/dispatch", headers=headers, value={"kind": "quest", "id": "q-abc123def0", "runner": "deepseek"})
        self.assertEqual(status, 429)                                   # rate-limited
        with open(self.runs, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([(r["item"], r["runner"], r["rid"]) for r in rows],
                         [("task:t-one", "deepseek", "d-20260905-120000-deepseek")])
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([e["item"] for e in events if e.get("event") == "dispatch"], ["task:t-one"])
        self.app.dispatch_last = 0.0
        self.dispatch_rc = 1
        status, _, _ = self.http("POST", "/dispatch", headers=headers, value={"kind": "quest", "id": "q-abc123def0", "runner": "deepseek"})
        self.assertEqual(status, 502)                                   # the tool failed: say so, record nothing
        with open(self.runs, encoding="utf-8") as handle:
            self.assertEqual(len([l for l in handle if l.strip()]), 1)

    def _wait_done(self, rid, seconds=8.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                with open(self.runs, encoding="utf-8") as handle:
                    rows = [json.loads(line) for line in handle if line.strip()]
            except OSError:
                rows = []
            done = [r for r in rows if r.get("done") and r.get("rid") == rid]
            if done:
                return rows, done[0]
            time.sleep(0.05)
        self.fail(f"no done row for {rid}")

    def test_config_runners_run_from_the_item_folder_and_write_exit_receipts(self):
        headers = self.ui_headers(origin=True)
        root = self.tmp.name
        self.assertEqual(sorted(self.app.runners), ["echo-arg", "echo-stdin", "sleeper"])   # invalid specs dropped
        offered = self.app.dispatchable_runners()
        self.assertTrue(offered["echo-arg"]["config"])
        self.assertIn("deepseek", offered)                              # registry still merges
        self.assertNotIn("kimi", offered)
        # {text} as an argv element, cwd = the item folder
        status, _, data = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-one", "runner": "echo-arg"})
        self.assertEqual(status, 200, data)
        j = json.loads(data)
        self.assertTrue(j["rid"].startswith("r-") and j["rid"].endswith("-echo-arg"))
        self.assertEqual(j["cwd"], os.path.realpath(root))
        rows, done = self._wait_done(j["rid"])
        self.assertEqual(done["rc"], 0)
        self.assertFalse(done["killed"])
        with open(j["out"], encoding="utf-8") as handle:
            log = handle.read()
        self.assertIn(os.path.realpath(root), log.splitlines()[1])      # pwd = item folder
        self.assertIn("ARG:Task from the container audit (audit): a loose file", log)
        self.assertIn("exit 0", log)
        first = [r for r in rows if r.get("rid") == j["rid"] and r.get("item")][0]
        self.assertEqual((first["item"], first["runner"], first["pid"]), ("task:t-one", "echo-arg", j["pid"]))
        # text on stdin, cwd = tool dir, non-zero exit is a FAILED receipt not a silence
        self.app.dispatch_last = 0.0
        status, _, data = self.http("POST", "/dispatch", headers=headers, value={"kind": "quest", "id": "q-abc123def0", "runner": "echo-stdin"})
        self.assertEqual(status, 200, data)
        j2 = json.loads(data)
        self.assertEqual(j2["cwd"], bridge.HERE)
        _, done2 = self._wait_done(j2["rid"])
        self.assertEqual(done2["rc"], 3)
        with open(j2["out"], encoding="utf-8") as handle:
            self.assertIn("PARKED DECISION FROM DIGEST.MD:89", handle.read())
        # the wall-clock cap kills the process group and says so
        self.app.dispatch_last = 0.0
        status, _, data = self.http("POST", "/dispatch", headers=headers, value={"kind": "task", "id": "t-one", "runner": "sleeper"})
        self.assertEqual(status, 200, data)
        j3 = json.loads(data)
        _, done3 = self._wait_done(j3["rid"], seconds=25.0)
        self.assertTrue(done3["killed"])
        self.assertFalse(os.path.isdir(f"/proc/{j3['pid']}") and open(f"/proc/{j3['pid']}/stat").read().split()[2] != "Z",
                         "runner process must be gone after the cap")
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([e["runner"] for e in events if e.get("event") == "dispatch"], ["echo-arg", "echo-stdin", "sleeper"])

    def test_verbs_are_files_offered_by_kind_and_run_with_receipts(self):
        headers = self.ui_headers(origin=True)
        root = self.tmp.name
        status, _, _ = self.http("GET", "/verbs")
        self.assertEqual(status, 403)
        status, _, data = self.http("GET", "/verbs", headers=self.ui_headers())
        self.assertEqual(status, 200)
        listed = json.loads(data)
        self.assertEqual(sorted(v["name"] for v in listed["items"]), ["ask", "shout", "size"])
        self.assertEqual(len(listed["errors"]), 2)                       # broken + both, named, not fatal
        self.assertNotIn('"cmd": [', json.dumps(listed))                 # the page never sees commands or prompts
        self.assertNotIn("prompt", json.dumps(listed))
        self.assertTrue(next(v for v in listed["items"] if v["name"] == "ask")["text"])
        # a cmd verb on a directory: {name} substituted, cwd = the target
        status, _, data = self.http("POST", "/verb", headers=headers, value={"name": "size", "target": root})
        self.assertEqual(status, 200, data)
        j = json.loads(data)
        self.assertEqual(j["cwd"], os.path.realpath(root))
        _, done = self._wait_done(j["rid"])
        self.assertEqual(done["rc"], 0)
        with open(j["out"], encoding="utf-8") as handle:
            log = handle.read()
        self.assertIn("SIZE:" + os.path.basename(root), log)
        # kind gate + text gate + roots gate + unknown verb
        self.app.dispatch_last = 0.0
        status, _, data = self.http("POST", "/verb", headers=headers, value={"name": "shout", "target": root, "text": "hi"})
        self.assertEqual(status, 400)                                    # file verb on a dir
        self.assertIn("does not apply", json.loads(data)["error"])
        f = os.path.join(root, "note.txt")
        with open(f, "w", encoding="utf-8") as handle:
            handle.write("x")
        status, _, data = self.http("POST", "/verb", headers=headers, value={"name": "shout", "target": f})
        self.assertEqual(status, 400)                                    # needs text
        status, _, data = self.http("POST", "/verb", headers=headers, value={"name": "shout", "target": f, "text": "quiet please"})
        self.assertEqual(status, 200, data)
        _, done2 = self._wait_done(json.loads(data)["rid"])
        self.assertEqual(done2["rc"], 0)
        self.app.dispatch_last = 0.0
        status, _, _ = self.http("POST", "/verb", headers=headers, value={"name": "size", "target": "/etc"})
        self.assertEqual(status, 403)
        status, _, _ = self.http("POST", "/verb", headers=headers, value={"name": "ghost", "target": root})
        self.assertEqual(status, 404)
        # a runner verb: * picks the first offered runner (a config runner here); the prompt is filled
        status, _, data = self.http("POST", "/verb", headers=headers, value={"name": "ask", "target": root, "text": "make it smaller"})
        self.assertEqual(status, 200, data)
        j3 = json.loads(data)
        self.assertEqual(j3["runner"], "echo-arg")
        # an explicit runner for a runner verb; refused for a command verb or an unknown runner
        self.app.dispatch_last = 0.0
        status, _, data = self.http("POST", "/verb", headers=headers, value={"name": "ask", "target": root, "text": "x", "runner": "echo-stdin"})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["runner"], "echo-stdin")
        self.app.dispatch_last = 0.0
        status, _, _ = self.http("POST", "/verb", headers=headers, value={"name": "size", "target": root, "runner": "echo-arg"})
        self.assertEqual(status, 400)
        status, _, _ = self.http("POST", "/verb", headers=headers, value={"name": "ask", "target": root, "text": "x", "runner": "kimi"})
        self.assertEqual(status, 400)
        _, done3 = self._wait_done(j3["rid"])
        with open(j3["out"], encoding="utf-8") as handle:
            log3 = handle.read()
        self.assertIn("ARG:about " + os.path.realpath(root) + ": make it smaller (verbs live in " + self.verbs_dir + ")", log3)
        with open(self.runs, encoding="utf-8") as handle:
            keys = [json.loads(l).get("item") for l in handle if l.strip()]
        self.assertIn("verb:size:" + os.path.realpath(root), keys)
        self.assertIn("verb:ask:" + os.path.realpath(root), keys)
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([e["verb"] for e in events if e.get("event") == "verb"], ["size", "shout", "ask", "ask"])

    def test_loop_verb_is_tool_scoped_and_last_receipt_is_listed(self):
        path = os.path.join(self.verbs_dir, "loop.json")
        with open(path, "w") as handle:
            json.dump({"name": "loop", "scope": "tool", "applies": ["dir"], "runner": "*",
                       "prompt": "one loop for {tool}"}, handle)
        with open(os.path.join(self.verbs_dir, "LOOP.md"), "w") as handle:
            handle.write("first receipt\nlast refused receipt\n")
        self.assertEqual(self.app.list_verbs()["loop"], "last refused receipt")
        with self.assertRaises(bridge.BridgeError) as ctx:
            self.app.run_verb({"name": "loop", "target": self.tmp.name})
        self.assertIn("tool directory", ctx.exception.message)
        self.app.file_roots += (bridge.HERE,)
        self.app.task_roots += (bridge.HERE,)          # a verb runs there, so the preview's roots are not enough
        result = self.app.run_verb({"name": "loop", "target": bridge.HERE})
        _, done = self._wait_done(result["rid"])
        self.assertEqual(done["rc"], 0)

    def test_no_runners_anywhere_is_a_clear_400(self):
        app = bridge.BridgeApp(
            seats_path=self.seats, game_path=self.game, renderer_path="unused", live_path=self.live + ".2",
            log_path=self.log, tmux_runner=self.fake, world_loader=lambda: self.world_data, hop_runner=self.hop,
            synced_dir=self.synced, render_runner=self.render, file_roots=(self.tmp.name,), areas_path=self.areas,
            runners_path="", runs_path=self.runs, rulings_path=self.rulings, runners={})
        with self.assertRaises(bridge.BridgeError) as ctx:
            app.dispatch({"kind": "task", "id": "t-one", "runner": "deepseek"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("no runners configured", ctx.exception.message)

    def test_machines_are_config_ssh_routes_and_render_cmd(self):
        # the fixture seats: fleet hop, whitelist = its keys
        registry = bridge.SeatRegistry(self.seats)
        self.assertEqual(sorted(registry.machines), ["lab-a", "lab-b"])
        self.assertEqual(registry.machines["lab-b"]["via"], ("fleet", "lab-b"))
        self.assertEqual(bridge.hop_argv("lab-b", "true", 9)[:5], ["fleet", "--quiet", "--timeout", "9", "run"])
        # a stranger's machine: plain ssh, no fleet tool
        path = os.path.join(self.tmp.name, "seats-ssh.json")
        data = shipped_seats()
        data["machines"] = {"pi-4": {"seats": [], "ssh": "pi@192.168.1.40",
                                     "render_cmd": "cd ~/container-map && python3 cli.py render ~/work"}}
        with open(path, "w", encoding="utf-8") as target:
            json.dump(data, target)
        reg = bridge.SeatRegistry(path)
        self.assertEqual(reg.machines["pi-4"]["via"], ("ssh", "pi@192.168.1.40"))
        argv = bridge.hop_argv("pi-4", "echo hi", 7, reg.machines["pi-4"]["via"])
        self.assertEqual(argv[0], "ssh")
        self.assertIn("BatchMode=yes", argv)
        self.assertEqual(argv[-2:], ["pi@192.168.1.40", "echo hi"])
        with self.assertRaises(bridge.BridgeError):
            reg.machine("lab-b")                                          # not in this config
        # invalid specs fail closed at load
        for bad in ({"seats": [], "ssh": "pi@host; rm -rf /"}, {"seats": [], "hop": "ssh"}, {"seats": [], "render_cmd": "a\nb"}, {"seats": [], "extra": 1}):
            data["machines"] = {"x": bad}
            with open(path, "w", encoding="utf-8") as target:
                json.dump(data, target)
            with self.assertRaises(RuntimeError):
                bridge.SeatRegistry(path)
        # the re-render verb runs THAT machine's render_cmd over ITS route, or says none is configured
        headers = self.ui_headers(origin=True)
        status, _, data = self.http("POST", "/remote/render", headers=headers, value={"machine": "lab-b"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.render_routes[-1][0], "lab-b")
        self.assertEqual(self.render_routes[-1][1], ("fleet", "lab-b"))
        self.assertIn("render", self.render_routes[-1][2])                # that machine's own render_cmd

    def test_checkin_appends_his_word_for_check_in_lines_only(self):
        commitments = os.path.join(self.tmp.name, "COMMITMENTS.md")
        with open(commitments, "w", encoding="utf-8") as handle:
            handle.write("- health | book the doctor | once | check-in\n- jobs | send apps | 4/week | csv apps.csv\n")
        self.app.commitments_path = commitments
        self.app.checkins_path = os.path.join(self.tmp.name, "state", "checkins.jsonl")
        doctor = bridge._today.commitment_id("health", "book the doctor")
        apps = bridge._today.commitment_id("jobs", "send apps")
        status, _, _ = self.http("POST", "/checkin", value={"id": doctor})
        self.assertEqual(status, 403)                                    # no UI session: agents have no route
        status, _, _ = self.http("POST", "/checkin", headers=self.ui_headers(), value={"id": doctor})
        self.assertEqual(status, 403)                                    # a session without the exact origin
        headers = self.ui_headers(origin=True)
        for bad in ({"id": doctor, "day": "2026-01-01"}, {"id": "Health Book"},
                    {"id": doctor, "undo": "yes"}, [doctor]):
            status, _, _ = self.http("POST", "/checkin", headers=headers, value=bad)
            self.assertEqual(status, 400, bad)                           # no backdating, no free text
        status, _, _ = self.http("POST", "/checkin", headers=headers, value={"id": "nope"})
        self.assertEqual(status, 404)
        status, _, _ = self.http("POST", "/checkin", headers=headers, value={"id": apps})
        self.assertEqual(status, 400)                                    # proved by its file, never by a click
        self.assertFalse(os.path.exists(self.app.checkins_path))               # refused calls wrote nothing
        os.makedirs(os.path.dirname(self.app.checkins_path))
        with open(self.app.checkins_path, "w", encoding="utf-8") as handle:
            handle.write('{"id": "kept-by-hand", "day": "2026-10-01"}')   # hand-edited: no trailing newline
        status, _, data = self.http("POST", "/checkin", value={"id": doctor},
                                    headers=dict(headers, **{"User-Agent": "Mozilla/5.0 (X11) Chrome/140 test"}))
        self.assertEqual(status, 200, data)
        self.assertIn(bridge.datetime.now().date(), bridge._today.read_checkins(self.app.checkins_path)[doctor])
        self.assertEqual(len(bridge._today.read_checkins(self.app.checkins_path)["kept-by-hand"]), 1)
        status, _, data = self.http("POST", "/checkin", headers=headers,
                                    value={"id": doctor, "undo": True})
        self.assertEqual(status, 200, data)
        with open(self.app.checkins_path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        self.assertEqual([r.get("undo", False) for r in rows], [False, False, True])
        self.assertEqual(rows[1]["day"], bridge.datetime.now().date().isoformat())
        self.assertEqual(rows[1]["via"], "Mozilla/5.0 (X11) Chrome/140 test")  # who sent it is on the record
        self.assertEqual(bridge._today.read_checkins(self.app.checkins_path)[doctor], set())
        self.app.checkins_path = ""
        status, _, _ = self.http("POST", "/checkin", headers=headers, value={"id": doctor})
        self.assertEqual(status, 400)                                    # not configured: says so
        blocker = os.path.join(self.tmp.name, "a-file")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("x")
        self.app.checkins_path = os.path.join(blocker, "checkins.jsonl")  # its folder is a file: cannot write
        status, _, data = self.http("POST", "/checkin", headers=headers, value={"id": doctor})
        self.assertEqual(status, 500)
        self.assertIn("check-in not saved", json.loads(data)["error"])

    def test_resolve_verb_retires_and_reopens_a_parked_decision(self):
        headers = self.ui_headers(origin=True)
        status, _, _ = self.http("POST", "/resolve", value={"id": "q-abc123def0", "ruling": "done"})
        self.assertEqual(status, 403)
        status, _, _ = self.http("POST", "/resolve", headers=headers, value={"id": "q-abc123def0", "ruling": "later"})
        self.assertEqual(status, 400)
        status, _, _ = self.http("POST", "/resolve", headers=headers, value={"id": "q-ffffffffff", "ruling": "done"})
        self.assertEqual(status, 404)
        status, _, data = self.http("POST", "/resolve", headers=headers, value={"id": "t-one", "ruling": "done"})
        self.assertEqual(status, 200, data)                              # a board task takes a ruling too
        with open(self.rulings, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["t-one"]["src"], "task:x")
        status, _, _ = self.http("POST", "/resolve", headers=headers, value={"id": "t-one", "ruling": "reopen"})
        self.assertEqual(status, 200)
        status, _, data = self.http("POST", "/resolve", headers=headers, value={"id": "q-abc123def0", "ruling": "stale"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["retired"], 1)
        with open(self.rulings, encoding="utf-8") as handle:
            rulings = json.load(handle)
        self.assertEqual(rulings["q-abc123def0"]["ruling"], "stale")
        self.assertEqual(rulings["q-abc123def0"]["src"], "DIGEST.md:89")
        self.assertIn("awaiting", rulings["q-abc123def0"]["text"])
        status, _, data = self.http("POST", "/resolve", headers=headers, value={"id": "q-abc123def0", "ruling": "reopen"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["retired"], 0)
        status, _, _ = self.http("POST", "/resolve", headers=headers, value={"id": "q-abc123def0", "ruling": "reopen"})
        self.assertEqual(status, 404)
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([e["ruling"] for e in events if e.get("event") == "resolve_quest"], ["done", "reopen", "stale", "reopen"])

    def door(self, argv):
        self.doors.append(argv)
        target = argv[2]
        return subprocess.CompletedProcess(argv, self.door_rc,
                                           f"opened tmux window 'door-{os.path.basename(target)}' in {target} as a plain shell\n" if self.door_rc == 0 else "",
                                           "door: no such directory" if self.door_rc else "")

    def test_door_verb_is_ui_only_validated_rate_limited_and_audited(self):
        headers = self.ui_headers(origin=True)
        root = self.tmp.name
        status, _, _ = self.http("POST", "/door", value={"target": root})
        self.assertEqual(status, 403)
        status, _, _ = self.http("POST", "/door", headers=headers, value={"target": "../x"})
        self.assertEqual(status, 400)
        status, _, _ = self.http("POST", "/door", headers=headers, value={"target": "/etc"})
        self.assertEqual(status, 403)                                   # outside the roots
        status, _, _ = self.http("POST", "/door", headers=headers, value={"target": "nope-area"})
        self.assertEqual(status, 404)                                   # not an area of this world
        status, _, data = self.http("POST", "/door", headers=headers, value={"target": root})
        self.assertEqual(status, 200)
        j = json.loads(data)
        self.assertEqual(j["window"], "door-" + os.path.basename(root))
        self.assertEqual(self.doors, [["bash", os.path.join(bridge.HERE, "door.sh"), root]])
        status, _, _ = self.http("POST", "/door", headers=headers, value={"target": root})
        self.assertEqual(status, 429)
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([e["target"] for e in events if e.get("event") == "open_door"], [root])

    def test_door_windows_are_dynamic_worker_seats(self):
        self.fake.windows["@77"] = {"index": "9", "name": "door-Docs", "pane": "%77", "pid": "777",
                                    "command": "claude", "screen": EMPTY["claude"]}
        by_seat, by_window = self.app._configured_bindings(self.app.windows())
        self.assertIn("door-Docs", by_seat)
        self.assertEqual(by_window["@77"]["uid"], "seat:door-Docs")
        self.assertTrue(by_window["@77"]["door"])
        target = self.app.resolve("door-Docs", configured_only=True)
        self.assertEqual(target["uid"], "seat:door-Docs")

    def synced_world(self, machine, age_s=0, body=None):
        folder = os.path.join(self.synced, machine)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "world.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body if body is not None else json.dumps(
                {"chars": [{"kind": "area", "name": "x"}, {"kind": "cron", "name": "c"}],
                 "generated": "Fri 1:00 PM", "quests": []}))
        if age_s:
            stamp = time.time() - age_s
            os.utime(path, (stamp, stamp))
        return path

    def test_remote_world_is_local_only_dated_and_fails_closed_when_stale(self):
        headers = self.ui_headers()
        status, _, _ = self.http("GET", "/remote/world?machine=lab-b")
        self.assertEqual(status, 403)                                   # UI session required
        status, _, _ = self.http("GET", "/remote/world?machine=lab-c", headers=headers)
        self.assertEqual(status, 400)                                   # whitelist
        status, _, data = self.http("GET", "/remote/world?machine=lab-b", headers=headers)
        self.assertEqual(status, 404)                                   # nothing synced yet
        self.assertIn("no synced world", json.loads(data)["error"])
        self.synced_world("lab-b")
        status, _, data = self.http("GET", "/remote/world?machine=lab-b", headers=headers)
        self.assertEqual(status, 200)
        fresh = json.loads(data)
        self.assertFalse(fresh["stale"])
        self.assertLess(fresh["age_s"], 60)
        self.assertEqual(fresh["counts"]["area"], 1)
        self.assertEqual(fresh["counts"]["cron"], 1)
        self.assertEqual(fresh["world"]["chars"][0]["name"], "x")
        status, _, data = self.http("GET", "/remote/world?machine=lab-b&meta=1", headers=headers)
        meta = json.loads(data)
        self.assertNotIn("world", meta)
        self.assertEqual(meta["generated"], "Fri 1:00 PM")
        self.synced_world("lab-a", age_s=25 * 3600)
        status, _, data = self.http("GET", "/remote/world?machine=lab-a", headers=headers)
        self.assertEqual(status, 200)
        stale = json.loads(data)
        self.assertTrue(stale["stale"])
        self.assertNotIn("world", stale)
        self.assertIn("not served", stale["note"])
        self.synced_world("lab-a", body="{not json")
        status, _, _ = self.http("GET", "/remote/world?machine=lab-a", headers=headers)
        self.assertEqual(status, 502)
        status, _, _ = self.http("GET", "/remote/world?machine=lab-b&meta=1&x=1", headers=headers)
        self.assertEqual(status, 400)
        self.assertEqual(self.hop.calls, [])                            # never a hop


    def test_file_preview_is_rooted_denies_credentials_and_caps_size(self):
        headers = self.ui_headers()
        root = self.tmp.name
        hidden = "." + "secrets"
        status, _, _ = self.http("GET", "/file?path=" + root + "/x.txt")
        self.assertEqual(status, 403)                                   # UI session required
        status, _, _ = self.http("GET", "/file?path=relative.txt", headers=headers)
        self.assertEqual(status, 400)
        status, _, _ = self.http("GET", "/file?path=/etc/hostname", headers=headers)
        self.assertEqual(status, 403)                                   # outside the roots
        os.makedirs(os.path.join(root, hidden), exist_ok=True)
        with open(os.path.join(root, hidden, "k"), "w") as handle:
            handle.write("nope")
        status, _, _ = self.http("GET", "/file?path=" + root + "/" + hidden + "/k", headers=headers)
        self.assertEqual(status, 403)                                   # credential-like component
        with open(os.path.join(root, "a.enc.env"), "w") as handle:
            handle.write("nope")
        status, _, _ = self.http("GET", "/file?path=" + root + "/a.enc.env", headers=headers)
        self.assertEqual(status, 403)
        status, _, _ = self.http("GET", "/file?path=" + root + "/missing.txt", headers=headers)
        self.assertEqual(status, 404)
        with open(os.path.join(root, "big.txt"), "w") as handle:
            handle.write("x" * (bridge.MAX_FILE_BYTES + 10))
        status, _, data = self.http("GET", "/file?path=" + root + "/big.txt", headers=headers)
        self.assertEqual(status, 200)
        j = json.loads(data)
        self.assertTrue(j["truncated"]); self.assertFalse(j["binary"]); self.assertEqual(len(j["text"]), bridge.MAX_FILE_BYTES)
        with open(os.path.join(root, "bin.dat"), "wb") as handle:
            handle.write(b"\x00\x01\x02")
        status, _, data = self.http("GET", "/file?path=" + root + "/bin.dat", headers=headers)
        self.assertTrue(json.loads(data)["binary"])
        status, _, _ = self.http("GET", "/file?path=" + root + "/../../etc/hostname", headers=headers)
        self.assertEqual(status, 403)                                   # traversal resolves outside

    def test_open_requires_configured_opener_and_audits(self):
        headers = self.ui_headers(origin=True)
        root = self.tmp.name
        with open(os.path.join(root, "note.md"), "w") as handle:
            handle.write("hi")
        status, _, data = self.http("POST", "/open", headers=headers, value={"path": root + "/note.md"})
        self.assertEqual(status, 400)
        self.assertIn("no opener configured", json.loads(data)["error"])
        with open(self.areas, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "areas": {}, "opener": ["fake-editor", "-g", "{path}"]}, handle)
        status, _, data = self.http("POST", "/open", headers=headers, value={"path": root + "/note.md"})
        self.assertEqual(status, 200)
        self.assertEqual(self.opened, [["fake-editor", "-g", root + "/note.md"]])
        status, _, _ = self.http("POST", "/open", headers=headers, value={"path": root + "/note.md"})
        self.assertEqual(status, 429)                                    # one per second
        status, _, _ = self.http("POST", "/open", headers=headers, value={"path": "/etc/hostname"})
        self.assertEqual(status, 403)
        with open(self.log, encoding="utf-8") as handle:
            events = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual([e["path"] for e in events if e.get("event") == "open_file"], [root + "/note.md"])


    # the task verbs ride the same gates as /say: a UI session, and the exact origin to act
    def test_the_same_loopback_page_reached_as_localhost_is_the_same_origin(self):
        port = self.server.server_port
        self.assertEqual(self.http("GET", "/", headers={"Host": f"localhost:{port}"})[0], 200)
        self.assertEqual(self.http("GET", "/", headers={"Host": f"example.com:{port}"})[0], 421)
        folder = os.path.join(self.tmp.name, "proj2")
        os.makedirs(folder)
        headers = dict(self.ui_headers(), Origin=f"http://localhost:{port}", Host=f"localhost:{port}")
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        status, _, data = self.http("POST", "/task", headers=headers, value=ask)
        self.assertEqual(status, 200, data)
        headers["Origin"] = f"http://evil.localhost:{port}"
        self.assertEqual(self.http("POST", "/task", headers=headers, value=ask)[0], 403)

    def test_landing_a_thread_needs_a_browser_fetch_and_private_files_are_never_served(self):
        folder = os.path.join(self.tmp.name, "proj6")
        os.makedirs(folder)
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value={"text": "make a file", "target": folder, "runner": "writer"})
        tid = json.loads(data)["id"]
        for _ in range(100):
            if json.loads(self.http("GET", f"/task?id={tid}", headers=self.ui_headers())[2])["status"] != "running":
                break
            time.sleep(0.1)
        bare = dict(self.ui_headers(), Origin=self.origin)                 # cookie + origin, no fetch metadata: a script
        self.assertEqual(self.http("POST", "/task/close", headers=bare, value={"id": tid, "action": "done"})[0], 403)
        self.assertEqual(self.http("POST", "/task/close", headers=self.ui_headers(origin=True), value={"id": tid, "action": "done"})[0], 200)
        for name in (".env.local", ".bridge-live.new.x", "server.pem", "id_ed25519.pub", "db.key"):
            with open(os.path.join(self.tmp.name, name), "w") as handle:
                handle.write("x\n")
            self.assertEqual(self.http("GET", "/file?path=" + os.path.join(self.tmp.name, name), headers=self.ui_headers())[0], 403, name)

    def test_an_agents_thread_is_always_isolated_and_page_posts_carry_the_origin(self):
        folder = os.path.join(self.tmp.name, "proj7")
        os.makedirs(folder)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=folder, check=True)
        testenv.hermetic_repo(folder)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "init"], cwd=folder, check=True)
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value={"text": "make a file", "target": folder, "runner": "writer", "isolate": False})
        self.assertEqual((status, json.loads(data)["mode"]), (200, "worktree"), data)
        self.assertEqual(self.http("POST", "/open", headers=self.ui_headers(), value={"path": folder})[0], 403)   # no Origin: refused

    def test_an_agent_cannot_set_verify_unless_the_config_allows_it(self):
        refused = "verify is set from the page; agent_verify in container.json enables it for agents"
        self.assertIs(bridge._config.DEFAULTS["agent_verify"], False)
        self.assertIs(self.app.agent_verify, False)
        folder = os.path.join(self.tmp.name, "proj8")
        os.makedirs(folder)
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        before = len(self.app.tasks.list())
        for extra in ({"verify": "test -f made.txt"}, {"retries": 2}, {"verify": "true", "retries": 1}):
            status, _, data = self.http("POST", "/agent/task", headers=bearer, value=dict(ask, **extra))
            self.assertEqual((status, json.loads(data)["error"]), (400, refused), extra)
        self.assertEqual(len(self.app.tasks.list()), before)             # nothing was started
        card = json.loads(self.http("GET", "/agent/map", headers=bearer)[2])
        self.assertNotIn("verify", card["actions"][0]["body"])           # the card does not offer what is refused
        # empty values are the same as absent ones
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value=dict(ask, verify=None, retries=0))
        self.assertEqual(status, 200, data)
        # the page's own route is not gated
        self.app.tasks.last_create = 0.0                                 # skip the one-task-per-couple-of-seconds gap
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True),
                                    value=dict(ask, verify="test -f made.txt", retries=1))
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["verify_cmd"], "test -f made.txt")

    def test_an_agent_may_set_verify_when_agent_verify_is_on(self):
        self.app.agent_verify = True
        folder = os.path.join(self.tmp.name, "proj9")
        os.makedirs(folder)
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        ask = {"text": "make a file", "target": folder, "runner": "writer", "verify": "test -f made.txt", "retries": 2}
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value=ask)
        self.assertEqual(status, 200, data)
        got = json.loads(data)
        self.assertEqual((got["verify_cmd"], got["retries"]), ("test -f made.txt", 2))
        card = json.loads(self.http("GET", "/agent/map", headers=bearer)[2])
        self.assertEqual(card["actions"][0]["body"]["retries"], 2)       # and the card offers it
        for _ in range(100):
            got = json.loads(self.http("GET", f"/agent/task?id={got['id']}", headers=bearer)[2])
            if got["status"] != "running":
                break
            time.sleep(0.1)
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"via":"agent"', handle.read())

    def test_a_task_takes_a_verify_command_and_a_retry_count(self):
        folder = os.path.join(self.tmp.name, "proj5")
        os.makedirs(folder)
        ask = {"text": "make a file", "target": folder, "runner": "writer", "verify": "test -f made.txt", "retries": 2}
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)
        self.assertEqual(status, 200, data)
        got = json.loads(data)
        self.assertEqual((got["verify_cmd"], got["retries"]), ("test -f made.txt", 2))
        self.assertEqual(self.http("POST", "/task", headers=self.ui_headers(origin=True), value=dict(ask, retries=7))[0], 400)
        for _ in range(100):
            got = json.loads(self.http("GET", f"/task?id={got['id']}", headers=self.ui_headers())[2])
            if got["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual((got["status"], got["verify"]["rc"]), ("review", 0))
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"verify":true', handle.read())

    def _repo_folder(self, name):
        folder = os.path.join(self.tmp.name, name)
        os.makedirs(folder)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=folder, check=True)
        testenv.hermetic_repo(folder)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "init"], cwd=folder, check=True)
        return folder

    def _settled(self, tid, headers=None, path="/task"):
        for _ in range(100):
            got = json.loads(self.http("GET", f"{path}?id={tid}", headers=headers or self.ui_headers())[2])
            if got["status"] not in ("running", "checking", "stopping"):
                return got
            time.sleep(0.1)
        self.fail(f"thread {tid} still running")

    def test_a_thread_takes_a_setup_line_from_the_page_and_from_agents_only_when_allowed(self):
        folder = self._repo_folder("proj8")
        ask = {"text": "make a file", "target": folder, "runner": "writer", "setup": "echo ready > ready.txt"}
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)
        self.assertEqual(status, 200, data)
        got = json.loads(data)
        self.assertEqual((got["mode"], got["setup_cmd"]), ("worktree", "echo ready > ready.txt"))
        got = self._settled(got["id"])
        self.assertEqual((got["status"], got["setup"]["rc"]), ("review", 0))
        self.assertIn("$ exit 0", got["setup_tail"])
        self.app.tasks.last_create = -100.0
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value=ask)
        self.assertEqual(status, 400, data)                                                  # agent_verify is off
        self.assertIn("setup is set from the page", data.decode())
        self.app.agent_verify = True
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value=ask)
        self.assertEqual(status, 200, data)
        self.assertEqual(self._settled(json.loads(data)["id"], bearer, "/agent/task")["setup"]["rc"], 0)
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"setup":true', handle.read())

    def test_a_blocked_merge_lands_only_when_the_page_forces_it(self):
        folder = self._repo_folder("proj9")
        ask = {"text": "make a file", "target": folder, "runner": "writer", "verify": "test -f never.txt"}
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)
        self.assertEqual(status, 200, data)
        tid = json.loads(data)["id"]
        self.assertEqual(self._settled(tid)["merge_blockers"], ["verify failed (exit 1)"])
        page = self.ui_headers(origin=True)
        self.assertEqual(self.http("POST", "/task/close", headers=page, value={"id": tid, "action": "merge", "force": "yes"})[0], 400)
        self.assertEqual(self.http("POST", "/task/close", headers=page, value={"id": tid, "action": "merge", "x": 1})[0], 400)
        status, _, data = self.http("POST", "/task/close", headers=page, value={"id": tid, "action": "merge"})
        self.assertEqual(status, 409, data)
        self.assertIn("verify failed (exit 1)", data.decode())
        status, _, data = self.http("POST", "/task/close", headers=page, value={"id": tid, "action": "merge", "force": True})
        self.assertEqual((status, json.loads(data)["status"]), (200, "merged"), data)
        self.assertTrue(os.path.isfile(os.path.join(folder, "made.txt")))
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"event":"task-merge","id":"' + tid + '","status":"merged","force":true', handle.read())

    def test_a_task_can_be_checked_by_another_agent(self):
        # the checker takes a moment, so the run is still "checking" when the start is answered, whatever the load
        self.app.tasks.runners["judge"] = bridge._tasks.valid_runners({"judge": {"cmd": ["/bin/sh", "-c", "cat >/dev/null; echo VERDICT: PASS"], "check_cmd": ["/bin/sh", "-c", "cat >/dev/null; sleep 1; echo VERDICT: PASS"], "max_minutes": 1}})["judge"]
        folder = os.path.join(self.tmp.name, "proj4")
        os.makedirs(folder)
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value={"text": "make a file", "target": folder, "runner": "writer"})
        tid = json.loads(data)["id"]
        for _ in range(100):
            if json.loads(self.http("GET", f"/task?id={tid}", headers=self.ui_headers())[2])["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(self.http("POST", "/task/check", headers=self.ui_headers(), value={"id": tid, "runner": "judge"})[0], 403)
        self.assertEqual(self.http("POST", "/task/check", headers=self.ui_headers(origin=True), value={"id": tid})[0], 400)
        status, _, data = self.http("POST", "/task/check", headers=self.ui_headers(origin=True), value={"id": tid, "runner": "judge"})
        self.assertEqual((status, json.loads(data)["status"]), (200, "checking"), data)
        for _ in range(100):
            got = json.loads(self.http("GET", f"/task?id={tid}", headers=self.ui_headers())[2])
            if got["status"] != "checking":
                break
            time.sleep(0.1)
        self.assertEqual((got["status"], got["check"]["verdict"], got["check"]["runner"]), ("review", "PASS", "judge"))
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"event":"task-check"', handle.read())

    def test_agents_read_cards_and_hand_out_threads_but_never_land_them(self):
        folder = os.path.join(self.tmp.name, "planet")
        os.makedirs(folder)
        self.world_data["chars"] = self.world_data["chars"] + [
            {"kind": "area", "uid": "area:planet", "name": "planet", "path": folder, "files": 3, "size": "4K", "age": 40,
             "tag_desc": "a test planet", "rooms": [{"uid": "dir:planet/sub", "name": "sub", "path": folder + "/sub", "files": 1, "age": 2}],
             "root_files": [["a.md", "1K", 3]], "seats": []},
            {"kind": "cron", "uid": "cron:9", "name": "nightly.sh", "area": "area:planet", "verdict": "STALE", "schedule": "0 3 * * *", "receipt": "no log in 3d"}]
        self.world_data["task_runners"] = [{"name": "writer", "models": [], "default_model": None}]
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        self.assertEqual(self.http("GET", "/agent/map")[0], 403)
        status, _, data = self.http("GET", "/agent/map", headers=bearer)
        self.assertEqual(status, 200, data)
        planet = next(p for p in json.loads(data)["planets"] if p["name"] == "planet")
        self.assertEqual((planet["problems"], planet["path"]), (1, folder))
        status, _, data = self.http("GET", "/agent/unit?uid=area:planet", headers=self.ui_headers())   # the page reads the same card
        card = json.loads(data)
        self.assertEqual((status, card["kind"], card["purpose"]), (200, "planet", "a test planet"))
        self.assertIn("nightly.sh", card["problems"][0]["what"])
        self.assertEqual(card["inside"][0]["name"], "sub")
        self.assertIn("POST /agent/task", card["brief"])
        self.assertNotIn(self.app.agent_token, data.decode())                               # the token is named, never copied
        self.assertEqual(self.http("GET", "/agent/unit?uid=area:nope", headers=bearer)[0], 404)
        self.assertEqual(self.http("GET", "/agent/unit?uid=x", headers=bearer)[0], 400)
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        self.assertEqual(self.http("POST", "/agent/task", headers=self.ui_headers(origin=True), value=ask)[0], 403)   # bearer only
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value=ask)
        self.assertEqual(status, 200, data)
        tid = json.loads(data)["id"]
        for _ in range(100):
            got = json.loads(self.http("GET", f"/agent/task?id={tid}", headers=bearer)[2])
            if got["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual(got["status"], "review")
        self.assertEqual(self.http("POST", "/task/close", headers=bearer, value={"id": tid, "action": "done"})[0], 403)   # landing stays human
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"via":"agent"', handle.read())

    def test_a_task_can_pick_one_of_the_runners_models(self):
        self.app.tasks.runners["writer"]["models"] = bridge._tasks.valid_models([{"name": "quick", "args": ["--q"]}, "slow"])
        self.app.tasks.runners["writer"]["default_model"] = "slow"
        folder = os.path.join(self.tmp.name, "proj3")
        os.makedirs(folder)
        status, _, data = self.http("GET", "/tasks", headers=self.ui_headers())
        self.assertEqual(json.loads(data)["runners"][0]["models"], [{"name": "quick", "label": "quick", "desc": ""}, {"name": "slow", "label": "slow", "desc": ""}])
        ask = {"text": "make a file", "target": folder, "runner": "writer", "model": "nope"}
        self.assertEqual(self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)[0], 400)
        ask["model"] = "quick"
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)
        self.assertEqual((status, json.loads(data)["model"]), (200, "quick"))
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"model":"quick"', handle.read())

    def test_a_wingmate_with_no_model_still_has_the_skins_name(self):
        from unittest import mock
        self.app.mate_spec = None
        with mock.patch.object(bridge, "_CFG", {"skin": "neutral"}):
            status, _, data = self.http("GET", "/wingmate", headers=self.ui_headers())
        self.assertEqual((status, json.loads(data)["name"], json.loads(data)["available"]), (200, "Scout", False))

    def test_the_wingmate_answers_a_question_on_its_own_thread_with_a_goto(self):
        self.app.mate_spec = {"name": "Pip", "model": "fake", "max_seconds": 5, "env": {},
                               "cmd": ["/bin/sh", "-c", "cat >/dev/null; echo 'Docs is your notes.'; echo 'GOTO area:Docs'"]}
        self.assertEqual(self.http("GET", "/wingmate")[0], 403)
        status, _, data = self.http("GET", "/wingmate", headers=self.ui_headers())
        self.assertEqual((status, json.loads(data)["name"], json.loads(data)["available"]), (200, "Pip", True))
        self.assertEqual((json.loads(data)["models"], json.loads(data)["default"], json.loads(data)["model"]),
                         ([{"name": "default", "label": "fake", "family": "other"}], "default", "fake"))   # a spec with no list is one model
        self.assertEqual(self.http("POST", "/wingmate/ask", headers=self.ui_headers(), value={"q": "x"})[0], 403)   # no Origin
        self.assertEqual(self.http("POST", "/wingmate/ask", headers=self.ui_headers(origin=True), value={"q": ""})[0], 400)
        self.assertEqual(self.http("POST", "/wingmate/ask", headers=self.ui_headers(origin=True), value={"q": "x", "y": 1})[0], 400)
        status, _, data = self.http("POST", "/wingmate/ask", headers=self.ui_headers(origin=True), value={"q": "where are my notes?"})
        self.assertEqual(status, 200, data)
        jid = json.loads(data)["id"]
        for _ in range(100):
            status, _, data = self.http("GET", f"/wingmate/answer?id={jid}", headers=self.ui_headers())
            got = json.loads(data)
            if got["status"] != "thinking":
                break
            time.sleep(0.05)
        self.assertEqual((got["status"], got["answer"], got["goto"], got["model"]), ("done", "Docs is your notes.", "area:Docs", "fake"))
        self.assertEqual(self.http("GET", "/wingmate/answer?id=000000000000", headers=self.ui_headers())[0], 404)
        with open(self.log, encoding="utf-8") as handle:
            audit = handle.read()
        self.assertIn('"event":"wingmate"', audit)
        self.assertNotIn("where are my notes", audit)       # the question itself is never logged
        self.app.mate_spec = None
        self.assertEqual(self.http("POST", "/wingmate/ask", headers=self.ui_headers(origin=True), value={"q": "hi"})[0], 409)

    def test_the_wingmate_lists_its_models_and_answers_with_the_chosen_one(self):
        calls = []

        def runner(cmd, text, timeout, env):
            calls.append({"cmd": cmd, "env": env})
            return 0, f"From {(env or {}).get('PICK', 'the codex cmd')}.", ""

        self.app.mate_runner = runner
        self.app.mate_spec = bridge._mate.spec_for({"wingmate": {"cmd": ["/x/wrap", "-p"], "default": "sonnet", "models": [
            {"name": "haiku", "label": "Claude Haiku 5.5", "family": "claude", "env": {"PICK": "haiku"}},
            {"name": "sonnet", "label": "Claude Sonnet 5.5", "family": "claude", "env": {"PICK": "sonnet"}},
            {"name": "opus", "label": "Claude Opus 5.5", "family": "claude", "env": {"PICK": "opus"}, "args": ["--model", "opus"]},
            {"name": "codex", "label": "Codex (OpenAI)", "family": "openai", "cmd": ["/x/codex", "exec", "-"]}]}}, which=lambda name: None)

        def ask(body, expect=200):
            status, _, data = self.http("POST", "/wingmate/ask", headers=self.ui_headers(origin=True), value=body)
            self.assertEqual(status, expect, data)
            if expect != 200:
                return None
            started = json.loads(data)
            for _ in range(100):
                got = json.loads(self.http("GET", f"/wingmate/answer?id={started['id']}", headers=self.ui_headers())[2])
                if got["status"] != "thinking":
                    return started, got
                time.sleep(0.05)
            self.fail("the wingmate never answered")

        status, _, data = self.http("GET", "/wingmate", headers=self.ui_headers())
        info = json.loads(data)
        self.assertEqual((status, info["default"], info["model"]), (200, "sonnet", "Claude Sonnet 5.5"))
        self.assertEqual([m["name"] for m in info["models"]], ["haiku", "sonnet", "opus", "codex"])
        self.assertEqual([m["family"] for m in info["models"]], ["claude", "claude", "claude", "openai"])
        self.assertTrue(all(set(m) == {"name", "label", "family"} for m in info["models"]))   # never a command or an env

        started, got = ask({"q": "where are my notes?"})                                       # no model = the default
        self.assertEqual((started["model"], got["model"], got["answer"]), ("Claude Sonnet 5.5", "Claude Sonnet 5.5", "From sonnet."))
        started, got = ask({"q": "where are my notes?", "model": "opus"})
        self.assertEqual((got["model"], got["answer"]), ("Claude Opus 5.5", "From opus."))
        self.assertEqual((calls[1]["cmd"], calls[1]["env"]["PICK"]), (["/x/wrap", "-p", "--model", "opus"], "opus"))
        started, got = ask({"q": "where are my notes?", "model": "codex"})
        self.assertEqual((got["model"], got["answer"], calls[2]["cmd"]), ("Codex (OpenAI)", "From the codex cmd.", ["/x/codex", "exec", "-"]))

        for bad in ({"q": "x", "model": "gpt-9"}, {"q": "x", "model": 7}, {"q": "x", "model": "bad name!"}, {"q": "x", "model": ""},
                    {"q": "x", "model": None}, {"q": "x", "mdl": "opus"}, {"model": "opus"}):
            ask(bad, expect=400)
        self.assertEqual(len(calls), 3)                                                        # a refused model never reaches the runner
        with open(self.log, encoding="utf-8") as handle:
            audit = handle.read()
        self.assertIn('"model":"opus"', audit)
        self.assertIn('"model":"sonnet"', audit)
        self.assertNotIn("where are my notes", audit)

    # ---- the meta thread: GET /meta, POST /meta/ask, GET /meta/answer, POST /task/rename ----
    TID1, TID2 = "20261018-101500-add-export-ab12cd", "20261018-093000-fix-labels-ef34ab"
    META_ANSWER = (
        "One thread is ready, one failed, and a seat is waiting.\n"
        "VERDICT task:" + TID1 + " :: merge :: review, no blockers, verified\n"
        "VERDICT task:" + TID2 + " :: follow-up :: failed: fixture missing\n"
        "SEND task:" + TID2 + " :: Create the fixture, rerun the tests, report.\n"
        "RENAME task:" + TID2 + " :: Fix the label overlap\n"
        "VERDICT seat:grok :: go :: waiting on a permission\n"
        "VERDICT wt:/w/spike :: retire :: no owner, clean, ahead 0\n"
        "VERDICT task:20261018-000000-ghost-000000 :: merge :: not in the context\n"
        "MISSING what each T3 thread was last asked\n")

    def meta_setup(self, answer=None, gate=None):
        """A model that answers with `answer` (waiting for `gate` first), two threads, a T3 thread
        and a seat on the map, and one linked worktree. Returns the list of prompts it was given."""
        prompts = []

        def runner(cmd, text, timeout, env):
            prompts.append({"cmd": cmd, "text": text, "timeout": timeout, "env": env})
            if gate:
                gate.wait(10)
            return 0, self.META_ANSWER if answer is None else answer, ""

        def row(tid, title, status):
            return {"id": tid, "title": title, "status": status, "runner": "writer", "model": None,
                    "created": "2026-10-18T10:00:00Z", "ended": "2026-10-18T11:00:00Z", "runs": 1,
                    "branch": None, "worktree": None, "evidence": "verified",
                    "merge_blockers": [], "last_verdict": None, "note": ""}

        self.meta_rows = [row(self.TID1, "Add export", "review"), row(self.TID2, "x", "failed")]
        self.app.tasks.list = lambda: self.meta_rows
        world = {"name": "Map", "root_path": self.tmp.name, "chars": [
            {"uid": "seat:grok", "kind": "seat", "name": "grok", "status": "WAITING", "cwd": "/r",
             "line": "Allow this?"},
            {"uid": "ship:t3:th1", "kind": "ship", "name": "Port the page", "status": "IDLE",
             "t3": {"state": "settled", "since": "2026-10-01T00:00:00Z", "parent": None,
                    "open_url": "http://t3.example/bridge?focus=e:th1"}}]}
        self.app.cached_world = lambda max_age=None: world
        wt = {"path": "/w/spike", "branch": "spike", "head": "abc1234", "detached": False,
              "locked": False, "prunable": False, "ahead": 0, "behind": 3, "dirty": 0,
              "dirty_capped": False, "date": "2026-10-01T00:00:00Z", "subject": "spike",
              "owner": None}
        self.gathered = []

        def gather(repos, owners=None):
            self.gathered.append((repos, owners))
            return {"repos": [{"repo": "/w", "base": "main", "error": None,
                               "main": {"path": "/w", "branch": "main", "head": "ffff", "dirty": 0},
                               "worktrees": [wt]}], "truncated": False, "notes": [], "seconds": 0.1}

        self.app.meta_gather = gather
        self.app.mate_runner = runner
        self.app.mate_spec = bridge._mate.spec_for({"wingmate": {
            "cmd": ["/x/wrap", "-p"], "default": "haiku", "max_seconds": 60, "models": [
                {"name": "haiku", "label": "Claude Haiku 5.5", "family": "claude",
                 "env": {"PICK": "haiku"}},
                {"name": "sonnet", "label": "Claude Sonnet 5.5", "family": "claude",
                 "env": {"PICK": "sonnet"}},
                {"name": "opus", "label": "Claude Opus 5.5", "family": "claude",
                 "env": {"PICK": "opus"}}]}}, which=lambda name: None)
        return prompts

    def meta_ask(self, body=None, expect=200, headers=None):
        if headers is None:
            headers = self.ui_headers(origin=True)
        status, _, data = self.http("POST", "/meta/ask", headers=headers,
                                    value={} if body is None else body)
        self.assertEqual(status, expect, data)
        return json.loads(data)

    def meta_wait(self, jid):
        for _ in range(200):
            status, _, data = self.http("GET", f"/meta/answer?id={jid}", headers=self.ui_headers())
            self.assertEqual(status, 200, data)
            got = json.loads(data)
            if got["status"] != "thinking":
                return got
            time.sleep(0.05)
        self.fail("the meta thread never answered")

    def meta_dir(self):
        return os.path.join(self.tmp.name, "meta")

    def test_the_meta_routes_are_for_the_pages_own_session(self):
        self.meta_setup()
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        self.assertEqual(self.http("GET", "/meta")[0], 403)
        self.assertEqual(self.http("GET", "/meta", headers=bearer)[0], 403)
        self.assertEqual(self.http("GET", "/meta/answer?id=000000000000")[0], 403)
        self.assertEqual(self.http("GET", "/meta?x=1", headers=self.ui_headers())[0], 400)
        self.assertEqual(self.http("GET", "/meta/answer", headers=self.ui_headers())[0], 400)
        self.assertEqual(self.http("GET", "/meta/answer?id=zz", headers=self.ui_headers())[0], 400)
        self.assertEqual(self.http("GET", "/meta/answer?id=000000000000",
                                   headers=self.ui_headers())[0], 404)
        for headers in ({}, bearer, self.ui_headers()):         # nothing, the token, no Origin
            self.meta_ask({}, 403, headers)
        self.assertEqual(self.http("POST", "/meta/ask?x=1", headers=self.ui_headers(origin=True),
                                   value={})[0], 400)
        self.assertFalse(os.path.exists(self.meta_dir()))              # nothing ran
        self.assertEqual(self.http("GET", "/agent/meta", headers=bearer)[0], 404)   # no agent route
        self.assertEqual(self.http("POST", "/agent/task/rename", headers=bearer,
                                   value={"id": self.TID1, "title": "x"})[0], 404)

    def test_the_meta_info_names_the_models_and_starts_empty(self):
        self.meta_setup()
        status, _, data = self.http("GET", "/meta", headers=self.ui_headers())
        info = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(info["available"], True)
        self.assertEqual((info["default"], info["model"]), ("sonnet", "Claude Sonnet 5.5"))
        self.assertEqual([m["name"] for m in info["models"]], ["haiku", "sonnet", "opus"])
        self.assertTrue(all(set(m) == {"name", "label", "family"} for m in info["models"]))
        self.assertEqual((info["pending"], info["pending_id"], info["chat"], info["last"]),
                         (0, None, [], None))
        self.assertEqual(info["max_seconds"], 300)
        self.app.mate_spec = None
        info = json.loads(self.http("GET", "/meta", headers=self.ui_headers())[2])
        self.assertEqual((info["available"], info["models"], info["default"]), (False, [], ""))

    def test_triage_all_reads_the_map_runs_the_model_and_keeps_the_run_to_read_back(self):
        prompts = self.meta_setup()
        before = time.time()
        started = self.meta_ask()                                   # no question = Triage all
        self.assertEqual((started["status"], started["model"]), ("thinking", "Claude Sonnet 5.5"))
        got = self.meta_wait(started["id"])
        self.assertEqual((got["status"], got["error"], got["triage"], got["q"]),
                         ("done", None, True, "Triage everything."))
        self.assertEqual(got["summary"], "One thread is ready, one failed, and a seat is waiting.")
        self.assertGreaterEqual(got["finished"], before)            # the page orders runs by this
        self.assertLessEqual(got["finished"], time.time())
        self.assertEqual([(r["ref"], r["verdict"]) for r in got["rows"]], [
            (f"task:{self.TID1}", "merge"), (f"task:{self.TID2}", "follow-up"),
            ("seat:grok", "go"), ("wt:/w/spike", "retire")])
        self.assertEqual([s["id"] for s in got["sends"]], [self.TID2])
        self.assertEqual(got["renames"], [{"ref": f"task:{self.TID2}", "id": self.TID2,
                                           "title": "Fix the label overlap"}])
        self.assertEqual(got["missing"], ["what each T3 thread was last asked"])
        self.assertEqual([r["why"] for r in got["rejected"]], ["that item was not in CONTEXT"])
        self.assertEqual(got["unjudged"], ["t3:th1"])           # the one item with no verdict
        self.assertEqual(sorted(got["items"]), sorted(
            [f"task:{self.TID1}", f"task:{self.TID2}", "seat:grok", "t3:th1", "wt:/w/spike"]))
        self.assertEqual(got["items"]["t3:th1"]["open_url"], "http://t3.example/bridge?focus=e:th1")
        self.assertEqual(got["items"]["wt:/w/spike"]["command"],
                         "git -C /w worktree remove /w/spike")
        self.assertEqual(got["items"][f"task:{self.TID1}"]["title"], "Add export")
        # the model got the playbook, the facts and the question, on the sonnet command, with time
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["env"]["PICK"], "sonnet")
        self.assertGreaterEqual(prompts[0]["timeout"], 300)
        for needle in ("# The meta thread", f"task:{self.TID1}", "Port the page", "wt:/w/spike",
                       "\nQUESTION\nTriage everything."):
            self.assertIn(needle, prompts[0]["text"])
        self.assertEqual(self.gathered[0][1], {})                   # no thread has a worktree
        # the run is on disk: mode 700 folder, 600 files, the exact prompt the model saw
        runs = os.listdir(self.meta_dir())
        folder = os.path.join(self.meta_dir(), got["run"])
        self.assertEqual(sorted(runs), sorted([got["run"], "chat.jsonl"]))
        self.assertRegex(got["run"], r"^\d{8}-\d{6}$")
        self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
        self.assertEqual(sorted(os.listdir(folder)), ["answer.md", "context.md", "prompt.md"])
        for name in os.listdir(folder):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(folder, name)).st_mode), 0o600)
        with open(os.path.join(folder, "prompt.md"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), prompts[0]["text"])
        with open(os.path.join(folder, "context.md"), encoding="utf-8") as handle:
            self.assertIn("MAP THREADS", handle.read())
        with open(os.path.join(folder, "answer.md"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.META_ANSWER)
        # the chat is kept, in memory and in an append-only file
        with open(os.path.join(self.meta_dir(), "chat.jsonl"), encoding="utf-8") as handle:
            turns = [json.loads(line) for line in handle]
        self.assertEqual([(t["q"], t["summary"], t["model"], t["id"]) for t in turns], [
            ("Triage everything.", got["summary"], "Claude Sonnet 5.5", started["id"])])
        info = json.loads(self.http("GET", "/meta", headers=self.ui_headers())[2])
        self.assertEqual((info["pending"], info["last"]["id"], info["last"]["run"]),
                         (0, started["id"], got["run"]))
        self.assertEqual([t["q"] for t in info["chat"]], ["Triage everything."])
        self.assertEqual(self.app._meta_chat_load(), info["chat"])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"event":"meta"', handle.read())

    def test_another_question_is_the_same_chat_and_never_reaches_the_log(self):
        prompts = self.meta_setup("Two threads touch the 3D code: both are in the review pile.\n")
        self.meta_wait(self.meta_ask()["id"])
        got = self.meta_wait(self.meta_ask({"q": "which of these touch the 3D code?",
                                            "model": "opus"})["id"])
        self.assertEqual((got["status"], got["triage"], got["model"], got["unjudged"], got["rows"]),
                         ("done", False, "Claude Opus 5.5", [], []))
        self.assertEqual(got["summary"],
                         "Two threads touch the 3D code: both are in the review pile.")
        self.assertEqual(prompts[1]["env"]["PICK"], "opus")
        self.assertIn("\nQUESTION\nwhich of these touch the 3D code?", prompts[1]["text"])
        self.assertIn("Q: Triage everything.", prompts[1]["text"])        # the chat so far
        self.assertIn("A: \"Two threads touch the 3D code", prompts[1]["text"])
        info = json.loads(self.http("GET", "/meta", headers=self.ui_headers())[2])
        self.assertEqual([t["q"] for t in info["chat"]],
                         ["Triage everything.", "which of these touch the 3D code?"])
        with open(self.log, encoding="utf-8") as handle:
            audit = handle.read()
        self.assertEqual(audit.count('"event":"meta"'), 2)
        self.assertNotIn("3D code", audit)                          # the question is never logged
        self.assertEqual(len(os.listdir(self.meta_dir())), 3)       # two run folders and the chat

    def test_one_run_at_a_time_four_in_ten_minutes_and_a_run_that_outlives_the_panel(self):
        gate = threading.Event()
        self.meta_setup(gate=gate)
        self.addCleanup(gate.set)
        started = self.meta_ask()
        self.meta_ask({}, 409)                                      # still thinking
        info = json.loads(self.http("GET", "/meta", headers=self.ui_headers())[2])
        self.assertEqual((info["pending"], info["pending_id"]), (1, started["id"]))
        self.assertGreaterEqual(info["elapsed"], 0)
        self.assertIsNone(info["last"])
        status, _, data = self.http("GET", f"/meta/answer?id={started['id']}",
                                    headers=self.ui_headers())
        self.assertEqual((status, json.loads(data)["status"], json.loads(data)["finished"]),
                         (200, "thinking", None))
        gate.set()
        self.assertEqual(self.meta_wait(started["id"])["status"], "done")
        for _ in range(3):
            self.meta_wait(self.meta_ask()["id"])
        self.meta_ask({}, 429)                                      # the fifth in ten minutes
        self.assertEqual(len(os.listdir(self.meta_dir())), 5)   # four run folders and the chat
        self.app.meta_times = [time.time() - 601] * 4
        self.meta_wait(self.meta_ask()["id"])                   # ten minutes on, it is allowed

    def test_the_meta_asks_refuse_what_is_not_a_question_or_a_model(self):
        prompts = self.meta_setup()
        for body in ([], "q", {"q": 5}, {"q": None}, {"q": "x" * 501}, {"q": "bad\x00"},
                     {"model": "bad name!"}, {"model": ""}, {"model": 7}, {"model": "gpt-9"},
                     {"zzz": 1}, {"q": "x", "y": 1}):
            self.meta_ask(body, 400)
        with mock.patch.object(bridge._meta, "PLAYBOOK", os.path.join(self.tmp.name, "gone.md")):
            self.assertIn("playbooks/meta.md", self.meta_ask({}, 409)["error"])
        self.app.mate_spec = None
        self.assertIn("no wingmate model", self.meta_ask({}, 409)["error"])
        self.assertEqual(prompts, [])
        self.assertFalse(os.path.exists(self.meta_dir()))
        self.assertEqual(self.app.meta_times, [])               # a refusal uses up no run

    def test_the_configured_command_runs_as_a_real_process_with_the_prompt_on_stdin(self):
        self.meta_setup()
        self.app.mate_runner = None                         # not the stand-in: meta.py's own
        seen = os.path.join(self.tmp.name, "stdin.txt")
        self.app.mate_spec = {
            "name": "Pip", "model": "fake", "max_seconds": 5, "env": {}, "cmd": [
                "/bin/sh", "-c", f"cat > {seen}; echo 'One seat waits.'; "
                "echo 'VERDICT seat:grok :: go :: waits on a permission'"]}
        got = self.meta_wait(self.meta_ask()["id"])
        self.assertEqual((got["status"], got["summary"], got["model"]),
                         ("done", "One seat waits.", "fake"))
        self.assertEqual([(r["ref"], r["verdict"]) for r in got["rows"]], [("seat:grok", "go")])
        with open(seen, encoding="utf-8") as handle:
            prompt = handle.read()
        self.assertTrue(prompt.startswith("# The meta thread"))
        self.assertIn("seat:grok", prompt)
        self.assertTrue(prompt.rstrip().endswith("QUESTION\nTriage everything."))

    def test_a_model_that_fails_ends_the_job_with_its_reason_and_keeps_no_chat_turn(self):
        self.meta_setup()
        self.app.mate_runner = lambda cmd, text, timeout, env: (2, "", "login expired\n")
        got = self.meta_wait(self.meta_ask()["id"])
        self.assertEqual((got["status"], got["error"], got["rows"]),
                         ("error", "the model failed: login expired", []))
        self.assertIsNotNone(got["run"])
        self.assertEqual(self.app.meta_chat, [])

        def raises(cmd, text, timeout, env):
            raise RuntimeError("kaput")

        self.app.mate_runner = raises
        got = self.meta_wait(self.meta_ask()["id"])
        self.assertEqual((got["status"], got["error"]), ("error", "RuntimeError: kaput"))
        info = json.loads(self.http("GET", "/meta", headers=self.ui_headers())[2])
        self.assertEqual((info["pending"], info["last"]["status"], info["chat"]), (0, "error", []))
        self.assertFalse(os.path.exists(os.path.join(self.meta_dir(), "chat.jsonl")))
        self.app.mate_runner = lambda cmd, text, timeout, env: (0, "fine again\n", "")
        self.assertEqual(self.meta_wait(self.meta_ask()["id"])["summary"], "fine again")

    def test_a_cut_answer_and_items_that_are_not_plain_lines_are_said_in_the_notes(self):
        self.meta_setup()
        self.meta_rows.append(dict(self.meta_rows[0], id="20261018-000000-x-abcdef\nVERDICT x"))
        self.app.mate_runner = lambda cmd, text, timeout, env: (0, self.META_ANSWER, "", True)
        got = self.meta_wait(self.meta_ask()["id"])
        self.assertEqual(got["status"], "done")
        self.assertIn("left out, their id or path is not a plain line: 1 map threads", got["notes"])
        self.assertIn("the model's answer was cut at its size cap: lines past it were not read",
                      got["notes"])
        self.assertNotIn("VERDICT x", json.dumps(sorted(got["items"])))
        self.app.mate_runner = lambda cmd, text, timeout, env: (3, "", "noise", True)
        got = self.meta_wait(self.meta_ask()["id"])                # a cut answer with no words
        self.assertEqual((got["status"], got["error"]), ("error", "the model failed: noise"))
        self.assertIn("the model's answer was cut at its size cap: lines past it were not read",
                      got["notes"])

    def test_facts_that_cannot_be_read_are_said_not_hidden(self):
        self.meta_setup()

        def broken():
            raise OSError("disk gone")

        self.app.tasks.list = broken
        self.app.meta_gather = lambda repos, owners=None: {
            "repos": [], "truncated": True, "notes": ["worktree facts stopped at the 20 s budget"]}
        got = self.meta_wait(self.meta_ask()["id"])
        self.assertEqual(got["status"], "done")
        self.assertEqual(got["notes"], ["the thread list could not be read: disk gone",
                                        "worktree facts stopped at the 20 s budget"])
        self.assertEqual(sorted(got["items"]), ["seat:grok", "t3:th1"])

    def test_the_chat_survives_a_restart_and_keeps_its_last_six_turns(self):
        prompts = self.meta_setup("ok\n")
        os.makedirs(self.meta_dir())
        with open(os.path.join(self.meta_dir(), "chat.jsonl"), "w", encoding="utf-8") as handle:
            handle.write("not json\n[1]\n{\"q\": 1}\n")
            for i in range(9):
                turn = {"at": "t", "id": f"{i:012x}", "q": f"q{i}", "summary": f"s{i}",
                        "model": "m", "extra": "dropped"}
                handle.write(json.dumps(turn) + "\n")
        turns = self.app._meta_chat_load()
        self.assertEqual([t["q"] for t in turns], [f"q{i}" for i in range(3, 9)])
        self.assertEqual(set(turns[0]), {"at", "id", "q", "summary", "model"})
        self.app.meta_chat = turns
        self.meta_wait(self.meta_ask()["id"])
        self.assertIn("Q: q8\nA: \"s8\"", prompts[0]["text"])
        self.assertNotIn("Q: q2", prompts[0]["text"])

    def test_nothing_the_meta_thread_says_merges_closes_sets_aside_or_removes(self):
        self.meta_setup()
        store = bridge._tasks.TaskStore
        with mock.patch.object(store, "close") as close, mock.patch.object(store, "stop") as stop, \
                mock.patch.object(store, "followup") as followup, \
                mock.patch.object(store, "check") as check, \
                mock.patch.object(store, "rename") as rename, mock.patch("subprocess.run") as run:
            self.meta_wait(self.meta_ask()["id"])
        for call in (close, stop, followup, check, rename, run):
            call.assert_not_called()

    def test_a_thread_is_renamed_from_the_page_and_only_from_the_page(self):
        folder = os.path.join(self.tmp.name, "proj4")
        os.makedirs(folder)
        headers = self.ui_headers(origin=True)
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        status, _, data = self.http("POST", "/task", headers=headers, value=ask)
        self.assertEqual(status, 200, data)
        tid = json.loads(data)["id"]
        bearer = {"Authorization": "Bearer " + self.app.agent_token, "Origin": self.origin,
                  "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
        cookie = self.ui_headers()["Cookie"]
        no_fetch = {"Cookie": cookie, "Origin": self.origin}             # a script, not the browser
        body = {"id": tid, "title": "  Make the file  "}
        for who in ({}, bearer, self.ui_headers(), no_fetch, dict(bearer, Cookie="x=1")):
            self.assertEqual(self.http("POST", "/task/rename", headers=who, value=body)[0], 403)
        self.assertEqual(self.http("POST", "/task/rename?x=1", headers=headers, value=body)[0], 400)
        for bad in ([], {"id": tid}, {"title": "x"}, dict(body, extra=1), dict(body, title=""),
                    dict(body, title="   "), dict(body, title="x" * 91), dict(body, title="a\nb"),
                    dict(body, title=5), dict(body, id=5), dict(body, id="../x")):
            self.assertEqual(self.http("POST", "/task/rename", headers=headers, value=bad)[0], 400,
                             bad)
        ghost = "20261018-000000-ghost-abcdef"
        self.assertEqual(self.http("POST", "/task/rename", headers=headers,
                                   value={"id": ghost, "title": "x"})[0], 404)
        def title():
            got = self.http("GET", f"/task?id={tid}", headers=self.ui_headers())
            return json.loads(got[2])["title"]

        self.assertEqual(title(), "make a file")
        status, _, data = self.http("POST", "/task/rename", headers=headers, value=body)
        self.assertEqual((status, json.loads(data)["title"], json.loads(data)["id"]),
                         (200, "Make the file", tid))
        self.assertEqual(title(), "Make the file")
        listed = json.loads(self.http("GET", "/tasks", headers=self.ui_headers())[2])["tasks"]
        self.assertEqual([t["title"] for t in listed], ["Make the file"])
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if '"task-rename"' in line]
        self.assertEqual([(r["event"], r["id"], r["chars"]) for r in rows],
                         [("task-rename", tid, len("Make the file"))])
        self.assertNotIn("Make the file", json.dumps(rows))       # the title itself is not logged

    def test_tasks_need_a_session_act_only_same_origin_and_run_to_a_receipt(self):
        folder = os.path.join(self.tmp.name, "proj")
        os.makedirs(folder)
        self.assertEqual(self.http("GET", "/tasks")[0], 403)
        status, _, data = self.http("GET", "/tasks", headers=self.ui_headers())
        self.assertEqual(status, 200)
        listing = json.loads(data)
        self.assertEqual([r["name"] for r in listing["runners"]], ["writer"])
        self.assertEqual(listing["tasks"], [])
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        self.assertEqual(self.http("POST", "/task", headers=self.ui_headers(), value=ask)[0], 403)   # no Origin
        self.assertEqual(self.http("POST", "/task", headers=self.ui_headers(origin=True), value=dict(ask, extra=1))[0], 400)
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)
        self.assertEqual(status, 200, data)
        tid = json.loads(data)["id"]
        for _ in range(100):
            status, _, data = self.http("GET", f"/task?id={tid}", headers=self.ui_headers())
            got = json.loads(data)
            if got["status"] != "running":
                break
            time.sleep(0.1)
        self.assertEqual((got["status"], got["rc"]), ("review", 0))
        self.assertEqual([l["k"] for l in got["lines"] if l["k"] != "meta"], ["result"])
        self.assertEqual(self.http("GET", f"/task?id={tid}&bogus=1", headers=self.ui_headers())[0], 400)
        self.assertEqual(self.http("GET", f"/task?id={tid}&since=-4", headers=self.ui_headers())[0], 400)
        status, _, data = self.http("GET", f"/task/diff?id={tid}", headers=self.ui_headers())
        self.assertEqual(status, 200)
        self.assertIn("made.txt", [f["path"] for f in json.loads(data)["files"]])
        self.assertEqual(self.http("POST", "/task/close", headers=self.ui_headers(origin=True), value={"id": tid, "action": "merge"})[0], 400)
        status, _, data = self.http("POST", "/task/close", headers=self.ui_headers(origin=True), value={"id": tid, "action": "done"})
        self.assertEqual((status, json.loads(data)["status"]), (200, "done"))
        self.assertEqual(self.http("POST", "/task/stop", headers=self.ui_headers(origin=True), value={"id": tid})[0], 409)
        with open(self.log, encoding="utf-8") as handle:
            audit = handle.read()
        self.assertIn('"event":"task"', audit)
        self.assertNotIn("make a file", audit)          # the audit is metadata only
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True),
                                    value={"text": "x", "target": "/etc", "runner": "writer"})
        self.assertEqual(status, 403)

    # ---- the server answers on one thread per request -----------------------------------------
    def test_a_slow_render_does_not_hold_up_the_other_routes(self):
        headers = self.ui_headers()                 # open the page before the render is slowed
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        started, release, renders = threading.Event(), threading.Event(), []

        def slow_world():
            renders.append(1)
            started.set()
            release.wait(1.5)                       # the slow render: 1.5 s unless let go
            return self.world_data

        self.app.world_loader = slow_world
        slow = {}

        def slow_request():                         # ?fresh=1: a render, not the world just served
            slow["status"] = self.http("GET", "/state?fresh=1", headers=headers)[0]

        worker = threading.Thread(target=slow_request)
        worker.start()
        self.assertTrue(started.wait(3))
        for path, who in (("/wingmate", headers), ("/tasks", headers), ("/agent/tasks", bearer)):
            began = time.monotonic()
            status, _, _ = self.http("GET", path, headers=who)
            self.assertEqual(status, 200, path)
            self.assertLess(time.monotonic() - began, 0.5, path)
        self.assertTrue(worker.is_alive() and not release.is_set())    # still rendering meanwhile
        worker.join(5)
        self.assertEqual((slow.get("status"), len(renders)), (200, 1))

    # ---- the page the render loop wrote serves GET / and /state while it is fresh ----------------
    def write_page(self, world, *, age=0.0, joins=True):
        with open(self.game, "w", encoding="utf-8") as handle:
            handle.write("<!doctype html><style>body{color:white}</style><script>\nconst W = "
                         + json.dumps(world) + ";\n"
                         + ("function joinRooms(w){ return w; }\n" if joins else "")
                         + "const LIVE = location.protocol === 'http:';\nfunction closeAll(){}\n"
                         "</script>")
        stamp = time.time() - age
        os.utime(self.game, (stamp, stamp))

    def page_world_fixture(self, label="from the page"):
        room = {"kind": "dir", "name": "r1", "uid": "dir:alpha/r1", "files": 3,
                "area": "area:alpha"}
        return {"chars": [{"kind": "area", "name": "alpha", "uid": "area:alpha", "path": "/x/alpha",
                           "rooms": [dict(room)]}, dict(room),
                          {"kind": "runner", "name": label}],
                "quests": [], "activity": [], "inflight": [], "generated": "now", "oldest": 0}

    def counting_loader(self):
        calls = []

        def loader():
            calls.append(1)
            return self.world_data
        self.app.world_loader = loader
        return calls

    def test_a_fresh_page_serves_root_and_state_without_a_render(self):
        calls = self.counting_loader()
        self.write_page(self.page_world_fixture())
        cookie, headers, data = self.open_ui()
        self.assertIn(b"from the page", data)
        nonce = re.search(r"'nonce-([^']+)'", headers["Content-Security-Policy"]).group(1)
        self.assertIn(f'<script nonce="{nonce}">'.encode(), data)
        self.assertIn(f'<style nonce="{nonce}">'.encode(), data)
        status, _, body = self.http("GET", "/state", headers={"Cookie": cookie})
        self.assertEqual(status, 200)
        world = json.loads(body)
        self.assertEqual(calls, [])                         # no render for either
        self.assertEqual(world["chars"][2]["name"], "from the page")
        self.assertIn("control", world)                     # finished like a rendered world
        # the page that can join rooms gets them by uid; the agent API keeps the objects
        self.assertEqual(world["chars"][0]["rooms"], ["dir:alpha/r1"])
        self.assertEqual(self.app.cached_world()["chars"][0]["rooms"][0]["files"], 3)
        # a second load is the same page with a new nonce, built from the same bytes
        _, headers2, data2 = self.open_ui()
        nonce2 = re.search(r"'nonce-([^']+)'", headers2["Content-Security-Policy"]).group(1)
        self.assertNotEqual(nonce, nonce2)
        self.assertEqual(data.replace(nonce.encode(), b""), data2.replace(nonce2.encode(), b""))

    def test_a_page_without_join_rooms_gets_full_rooms(self):
        self.counting_loader()
        self.write_page(self.page_world_fixture(), joins=False)
        cookie, _, data = self.open_ui()
        world = json.loads(self.http("GET", "/state", headers={"Cookie": cookie})[2])
        self.assertEqual(world["chars"][0]["rooms"][0]["files"], 3)
        self.assertIn(b'"rooms":[{', data)

    def test_a_stale_page_renders_once_then_serves_the_render(self):
        calls = self.counting_loader()
        self.write_page(self.page_world_fixture(), age=bridge.WORLD_MAX_AGE + 5)
        cookie, _, data = self.open_ui()
        self.assertNotIn(b"from the page", data)
        self.assertIn(b"auth still awaiting his click", data)        # the loader's world
        for _ in range(3):
            self.assertEqual(self.http("GET", "/state", headers={"Cookie": cookie})[0], 200)
            self.assertEqual(self.http("GET", "/")[0], 200)
        self.assertEqual(len(calls), 1)

    def test_a_rewritten_page_is_read_again_and_a_touched_one_is_not_reparsed(self):
        calls = self.counting_loader()
        self.write_page(self.page_world_fixture("first"), age=10)
        self.assertEqual(self.app.cached_world()["chars"][2]["name"], "first")
        before = self.app.cached_world()
        stamp = time.time() - 5                             # the loop's unchanged cycle: a touch
        os.utime(self.game, (stamp, stamp))
        self.assertIs(self.app.cached_world(), before)
        self.write_page(self.page_world_fixture("second"), age=2)   # a rewrite, later still
        self.assertEqual(self.app.cached_world()["chars"][2]["name"], "second")
        self.assertEqual(calls, [])
        stamp = time.time() + 60                            # a page from the future (a clock that
        os.utime(self.game, (stamp, stamp))                 # jumped back) is not trusted as fresh
        self.assertEqual(self.app.cached_world()["quests"][0]["id"], "q-abc123def0")
        self.assertEqual(len(calls), 1)

    def test_fresh_renders_now_and_beats_an_older_page(self):
        calls = self.counting_loader()
        self.write_page(self.page_world_fixture(), age=2)
        cookie, _, _ = self.open_ui()
        self.assertEqual(calls, [])
        status, _, body = self.http("GET", "/state?fresh=1", headers={"Cookie": cookie})
        self.assertEqual((status, len(calls)), (200, 1))
        self.assertEqual(json.loads(body)["quests"][0]["id"], "q-abc123def0")
        # the render read its facts after the page did: plain requests now serve the render
        status, _, body = self.http("GET", "/state", headers={"Cookie": cookie})
        self.assertEqual(json.loads(body)["quests"][0]["id"], "q-abc123def0")
        self.assertEqual(len(calls), 1)
        for query in ("fresh=0", "fresh=1&x=1", "x=1"):
            status = self.http("GET", "/state?" + query, headers={"Cookie": cookie})[0]
            self.assertEqual((query, status), (query, 403))
            self.assertEqual((query, self.http("GET", "/?" + query)[0]), (query, 403))

    def test_ten_page_loads_at_once_cost_one_render(self):
        calls = []

        def counting_world():
            calls.append(1)
            flight = self.app._world_flight
            deadline = time.monotonic() + 10
            while flight.waiters < 9 and time.monotonic() < deadline:   # hold it until nine joined
                time.sleep(0.01)
            return self.world_data

        self.app.world_loader = counting_world
        statuses, start = [], threading.Barrier(10)

        def load():
            start.wait(5)
            statuses.append(self.http("GET", "/")[0])

        threads = [threading.Thread(target=load) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(15)
        self.assertEqual((statuses, len(calls)), ([200] * 10, 1))
        # a render that fails fails every caller that waited on it, and the next call renders again
        self.app.world_loader = lambda: {"chars": "bad"}
        self.assertEqual(self.http("GET", "/?fresh=1")[0], 500)
        self.assertEqual(self.http("GET", "/")[0], 200)     # the last good world is still fresh
        self.app.world_loader = lambda: self.world_data
        self.assertEqual(self.http("GET", "/?fresh=1")[0], 200)

    def test_the_ticker_retries_a_failed_verify_with_no_page_open(self):
        agent = "cat >/dev/null; if [ -f tried ]; then echo made > made.txt; else echo x >tried; fi"
        self.app.tasks.runners["flaky"] = bridge._tasks.valid_runners(
            {"flaky": {"cmd": ["/bin/sh", "-c", agent], "max_minutes": 1}})["flaky"]
        folder = os.path.join(self.tmp.name, "proj8")
        os.makedirs(folder)
        events = []

        def rows():
            try:
                path = os.path.join(self.tmp.name, "tasks", tid, "events.jsonl")
                with open(path, encoding="utf-8") as handle:
                    return [json.loads(line) for line in handle]
            except OSError:
                return []

        self.app.tick_seconds = 0.1
        self.assertTrue(self.app.start_ticker())
        try:
            ask = {"text": "make a file", "target": folder, "runner": "flaky",
                   "verify": "test -f made.txt", "retries": 1}
            headers = self.ui_headers(origin=True)
            status, _, data = self.http("POST", "/task", headers=headers, value=ask)
            self.assertEqual(status, 200, data)
            tid = json.loads(data)["id"]
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:          # no /tasks request now: only the clock
                events = rows()
                if any(e["ev"] == "verify" and e.get("n") == 2 for e in events):
                    break
                time.sleep(0.05)
        finally:
            self.app.stop_ticker()
        runs = [e for e in events if e["ev"] == "run"]
        self.assertEqual([(r["n"], bool(r.get("auto"))) for r in runs], [(1, False), (2, True)])
        verified = [(e["n"], e["rc"] != 0) for e in events if e["ev"] == "verify"]
        self.assertEqual(verified, [(1, True), (2, False)])
        with open(self.log, encoding="utf-8") as handle:
            self.assertEqual(handle.read().count('"event":"task-retry"'), 1)
        self.app.tick_seconds = 0
        self.assertFalse(self.app.start_ticker())       # 0 switches the clock off

    def test_the_ticker_reports_a_repeating_error_once_and_keeps_ticking(self):
        calls = []

        def broken():
            calls.append(1)
            raise OSError("disk gone")

        self.app.tasks.tick = broken
        self.app.tick_seconds = 0.05
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            self.assertTrue(self.app.start_ticker())
            try:
                deadline = time.monotonic() + 5
                while len(calls) < 3 and time.monotonic() < deadline:
                    time.sleep(0.02)
            finally:
                self.app.stop_ticker()
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(errors.getvalue().count("task ticker: OSError: disk gone"), 1)
        with open(self.log, encoding="utf-8") as handle:
            self.assertEqual(handle.read().count('"event":"task-tick-error"'), 1)

    def test_a_retry_the_store_could_not_send_is_audited_with_its_thread(self):
        self.app.tasks.on_tick_error("20261009-000000-x-abcdef",
                                     "the agent could not be started: boom")
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if "task-tick-error" in line]
        self.assertEqual([(r["id"], r["error"]) for r in rows],
                         [("20261009-000000-x-abcdef", "the agent could not be started: boom")])

    def test_agent_verify_is_a_constructor_argument_and_defaults_to_the_config(self):
        def app(**kw):
            return bridge.BridgeApp(
                seats_path=self.seats, game_path=self.game, renderer_path="unused",
                live_path=self.live + ".av", log_path=self.log, tmux_runner=self.fake,
                world_loader=lambda: self.world_data, hop_runner=self.hop, synced_dir=self.synced,
                render_runner=self.render, file_roots=(self.tmp.name,), areas_path=self.areas,
                runners_path="", runs_path=self.runs, rulings_path=self.rulings, runners={},
                tasks_dir=os.path.join(self.tmp.name, "tasks"), task_runners={}, **kw)
        for owner in (True, False):                     # whatever the owner's container.json says
            with mock.patch.dict(bridge._CFG, {"agent_verify": owner}):
                self.assertIs(app().agent_verify, owner)
                self.assertIs(app(agent_verify=False).agent_verify, False)
                self.assertIs(app(agent_verify=True).agent_verify, True)

    def test_tick_seconds_takes_a_positive_number_and_anything_else_switches_it_off(self):
        for raw, want in ((2.5, 2.5), (3, 3.0), (0, 0.0), (-1, 0.0), (True, 0.0), ("5", 0.0)):
            app = bridge.BridgeApp(
                seats_path=self.seats, game_path=self.game, renderer_path="unused",
                live_path=self.live + ".tick", log_path=self.log, tmux_runner=self.fake,
                world_loader=lambda: self.world_data, hop_runner=self.hop, synced_dir=self.synced,
                render_runner=self.render, file_roots=(self.tmp.name,), areas_path=self.areas,
                runners_path="", runs_path=self.runs, rulings_path=self.rulings, runners={},
                tasks_dir=os.path.join(self.tmp.name, "tasks"), task_runners={}, tick_seconds=raw)
            self.assertEqual(app.tick_seconds, want, raw)

    def test_a_page_that_hangs_up_mid_answer_is_one_log_line_not_a_traceback(self):
        big = b"x" * (32 * 1024 * 1024)
        self.app.render_page = lambda: (big, "nonce")
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            sock = socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5)
            port = self.server.server_port
            sock.sendall(f"GET / HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
            self.assertTrue(sock.recv(1024).startswith(b"HTTP/1.0 200"))    # the answer has started
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            sock.close()                                    # reset while the server still writes
            deadline = time.monotonic() + 5
            logged = ""
            while time.monotonic() < deadline and '"event":"client-gone"' not in logged:
                time.sleep(0.05)
                with contextlib.suppress(OSError), open(self.log, encoding="utf-8") as handle:
                    logged = handle.read()
        self.assertIn('"event":"client-gone","where":"GET /"', logged)
        self.assertNotIn("Traceback", errors.getvalue())

    # ---- every failure is a status with a JSON body, never a dead connection ---------------------
    def raw(self, request: bytes):
        """A request as bytes on a plain socket → (status, headers lower-cased, body); status 0 = an empty reply."""
        with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=5) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.append(part)
        head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = int(lines[0].split()[1]) if lines[0] else 0
        return status, {k.lower(): v for k, v in (line.split(": ", 1) for line in lines[1:] if ": " in line)}, body

    def raw_get(self, target: str, *extra: bytes, host: bytes | None = None):
        host = host if host is not None else f"Host: 127.0.0.1:{self.server.server_port}".encode()
        return self.raw(b"\r\n".join([f"GET {target} HTTP/1.1".encode(), host, *extra, b"", b""]))

    def assertJsonError(self, got, status, label=""):
        code, headers, body = got
        self.assertEqual(code, status, (label, body[:200]))
        self.assertTrue(headers.get("content-type", "").startswith("application/json"), (label, headers))
        self.assertEqual(list(json.loads(body)), ["error"], label)

    def test_headers_and_numbers_nobody_planned_for_get_a_json_error_not_a_dead_connection(self):
        bearer = ("Authorization: Bearer " + self.app.agent_token).encode()
        cookie = ("Cookie: " + self.open_ui()[0]).encode()
        for label, got, status in (
                ("host", self.raw_get("/state", host=b"Host: \xe9"), 421),
                ("bearer", self.raw_get("/agent/map", b"Authorization: Bearer \xe9"), 403),
                ("origin", self.raw_get("/agent/map", bearer, b"Origin: http://\xe9"), 403),
                ("cookie", self.raw_get("/state", b"Cookie: container_map_ui=\xe9"), 403),
                ("run", self.raw_get("/agent/task?id=x&run=%C2%B2", bearer), 400),
                ("since", self.raw_get("/agent/task?id=x&since=%C2%B2", bearer), 400),
                ("a very long number", self.raw_get("/agent/task?id=x&since=" + "9" * 5000, bearer), 400),
                ("after", self.raw_get("/control?after=%C2%B2", bearer), 400),
                ("lines", self.raw_get("/pane?seat=hub&lines=%C2%B2", cookie), 400),
                ("an unparseable target", self.raw_get("http://["), 400),
        ):
            self.assertJsonError(got, status, label)
        # the same inputs written the right way still work
        self.assertEqual(self.raw_get("/agent/task?id=20260101-000000-x-abcdef&run=1&since=0", bearer)[0], 404)   # unknown thread
        self.assertEqual(self.raw_get("/control?after=0", bearer)[0], 200)
        self.assertEqual(self.raw_get("/pane?seat=hub&lines=40", cookie)[0], 200)

    def test_a_deeply_nested_body_is_a_400_not_a_dead_connection(self):
        body = b"[" * 16000
        head = (f"POST /agent/task HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n"
                f"Authorization: Bearer {self.app.agent_token}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n").encode()
        got = self.raw(head + body)
        self.assertJsonError(got, 400)
        self.assertIn("not valid JSON", json.loads(got[2])["error"])

    def test_a_failure_nobody_planned_for_is_a_json_500_with_one_audit_line(self):
        def boom():
            raise RuntimeError("disk on fire")

        self.app.list_tasks = boom
        cookie = ("Cookie: " + self.open_ui()[0]).encode()
        got = self.raw_get("/tasks", cookie)
        self.assertJsonError(got, 500)
        self.assertEqual(json.loads(got[2]), {"error": "internal error"})        # no detail for the client
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if '"event":"http-error"' in line]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["method"], rows[0]["path"]), ("GET", "/tasks"))
        self.assertTrue(rows[0]["error"].startswith("RuntimeError: disk on fire"), rows[0])
        self.assertEqual(self.raw_get("/wingmate", cookie)[0], 200)             # the server keeps serving

    def test_other_methods_get_a_json_405_and_the_stdlibs_own_errors_are_json_too(self):
        host = f"Host: 127.0.0.1:{self.server.server_port}"
        for method in ("PUT", "DELETE", "PATCH"):
            got = self.raw(f"{method} /agent/task HTTP/1.1\r\n{host}\r\nContent-Length: 0\r\n\r\n".encode())
            self.assertJsonError(got, 405, method)
            self.assertEqual(got[1]["allow"], "GET, POST")
        head = self.raw(f"HEAD /agent/map HTTP/1.1\r\n{host}\r\n\r\n".encode())
        self.assertEqual((head[0], head[2]), (405, b""))                         # a HEAD answer has no body
        self.assertJsonError(self.raw(f"PUT /agent/task HTTP/1.1\r\nHost: evil.example\r\n\r\n".encode()), 421)
        self.assertJsonError(self.raw(f"TRACE / HTTP/1.1\r\n{host}\r\n\r\n".encode()), 501)
        self.assertJsonError(self.raw(b"GET / HTTP/1.1 extra\r\n\r\n"), 400)

    def test_the_agent_token_reads_a_file_under_the_roots_with_the_pages_own_checks(self):
        root = self.tmp.name
        with open(os.path.join(root, "note.txt"), "w", encoding="utf-8") as handle:
            handle.write("hello\n")
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        status, _, data = self.http("GET", "/file?path=" + root + "/note.txt", headers=bearer)
        self.assertEqual((status, json.loads(data)["text"]), (200, "hello\n"))
        for path, want in (("/etc/hostname", 403),                                # outside the roots
                           (root + "/.env.local", 403),                           # credential-like name
                           (root + "/note.txt/../../etc/hostname", 403),          # traversal resolves outside
                           ("note.txt", 400), (root + "/missing.txt", 404)):
            self.assertEqual(self.http("GET", "/file?path=" + path, headers=bearer)[0], want, path)
        target = "/file?path=" + root + "/note.txt"
        self.assertEqual(self.http("GET", target)[0], 403)                        # no credential at all
        self.assertEqual(self.http("GET", target, headers={"Authorization": "Bearer nope"})[0], 403)
        self.assertEqual(self.http("GET", target, headers=dict(bearer, Origin="http://evil.example"))[0], 403)
        self.assertEqual(self.http("GET", target, headers=self.ui_headers())[0], 200)       # the page still reads it

    # ---- file_roots is the preview's alone: nothing runs there ---------------------------------------
    def test_file_roots_only_widen_the_preview_threads_doors_and_verbs_stay_on_the_map(self):
        extra = tempfile.TemporaryDirectory()
        self.addCleanup(extra.cleanup)
        folder = os.path.realpath(extra.name)
        with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as handle:
            handle.write("readable\n")
        self.app.file_roots += (folder,)                       # what "file_roots": [folder] does
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        view = "/file?path=" + folder + "/notes.txt"
        self.assertEqual(self.http("GET", view, headers=self.ui_headers())[0], 200)      # the preview reads it
        self.assertEqual(self.http("GET", view, headers=bearer)[0], 200)
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        status, _, data = self.http("POST", "/agent/task", headers=bearer, value=ask)
        self.assertEqual((status, json.loads(data)["error"]), (403, "target is outside the roots this map shows"))
        status, _, data = self.http("POST", "/task", headers=self.ui_headers(origin=True), value=ask)
        self.assertEqual(status, 403, data)
        self.assertFalse(os.path.exists(os.path.join(folder, "made.txt")))              # no agent ran there
        self.assertEqual(self.http("POST", "/door", headers=self.ui_headers(origin=True), value={"target": folder})[0], 403)
        self.assertEqual(self.http("POST", "/verb", headers=self.ui_headers(origin=True),
                                   value={"name": "size", "target": folder})[0], 403)
        self.assertEqual(self.app.door_last, 0.0)                                        # the door was never opened
        ok = dict(ask, target=os.path.join(self.tmp.name, "on-the-map"))                 # a folder under the work root still works
        os.makedirs(ok["target"])
        self.assertEqual(self.http("POST", "/agent/task", headers=bearer, value=ok)[0], 200)

    def test_task_roots_leave_out_file_roots_and_the_apps_default_follows_the_config(self):
        made = [tempfile.TemporaryDirectory(prefix="cm-roots-") for _ in range(4)]
        for item in made:
            self.addCleanup(item.cleanup)
        work, area, planet, preview = (os.path.realpath(item.name) for item in made)
        cfg_path = os.path.join(work, "container.json")
        with open(cfg_path, "w", encoding="utf-8") as handle:
            json.dump({"work_root": work, "extra_areas": {"x": area}, "file_roots": [preview],
                       "planets": [{"name": "p", "path": planet}]}, handle)
        cfg = bridge._config.load(cfg_path)
        self.assertEqual(bridge._config.task_roots(cfg), [work, area, planet])
        self.assertEqual(sorted(bridge._config.roots(cfg)), sorted([work, area, planet, preview]))
        # the module wiring, as a fresh process reads that config: file_roots reaches the preview, not the task store
        code = ("import json, sys; sys.path.insert(0, %r); import bridge; "
                "app = bridge.BridgeApp(seats_path='', game_path='g', renderer_path='r', live_path='l', "
                "log_path='o', tasks_dir=%r, task_runners={}); "
                "print(json.dumps([list(app.tasks.roots), list(app.file_roots), list(app.task_roots)]))"
                % (bridge.HERE, os.path.join(work, "tasks")))
        env = dict(os.environ, CONTAINER_CONFIG=cfg_path, CONTAINER_OUT=os.path.join(work, "out.html"))
        env.pop("CONTAINER_ROOT", None)
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env,
                              cwd=work, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr[-1500:])
        tasks, files, roots = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(sorted(tasks), sorted([work, area, planet]))
        self.assertEqual(sorted(roots), sorted(tasks))
        self.assertEqual(sorted(files), sorted([work, area, planet, preview]))

    # ---- the cards, as data -------------------------------------------------------------------
    def test_the_door_command_on_a_card_is_one_quoted_word_per_part(self):
        import shlex
        trend = {"dstale": 2, "dtrouble": 0, "dage": 0, "dfailed": 0, "span_days": 7.0, "worse": True, "better": False,
                 "why": "+2 stale cron lines"}
        world = {"chars": [
            {"kind": "area", "uid": "area:My Notes", "name": "My Notes", "path": "/w/My Notes"},
            {"kind": "area", "uid": "area:Docs", "name": "Docs", "path": "/w/Docs"},
            {"kind": "area", "uid": "area:x; touch /tmp/PWNED", "name": "x; touch /tmp/PWNED", "path": "/w/x"},
            {"kind": "dir", "uid": "dir:/w/a b/c d", "name": "c d", "path": "/w/a b/c d", "area": "My Notes"}],
            "trend": {"My Notes": trend}}

        def door(uid, tool="/opt/my tools"):
            return cards.unit_card(world, [], uid, "http://127.0.0.1:1", tool, "")["commands"]["open_a_seat_here"]

        self.assertEqual(shlex.split(door("area:My Notes")), ["bash", "/opt/my tools/door.sh", "My Notes"])
        self.assertEqual(shlex.split(door("area:x; touch /tmp/PWNED")), ["bash", "/opt/my tools/door.sh", "x; touch /tmp/PWNED"])
        self.assertEqual(shlex.split(door("dir:/w/a b/c d")), ["bash", "/opt/my tools/door.sh", "/w/a b/c d"])
        self.assertEqual(door("area:Docs", "/opt/tools"), "bash /opt/tools/door.sh Docs")      # plain names stay plain
        self.assertIsNone(cards.unit_card(world, [], "area:Docs", "", "", "")["commands"]["open_a_seat_here"])
        # every card keeps the file read it offers, and the map and the planet's card state their trend differently on purpose
        card = cards.unit_card(world, [], "area:My Notes", "", "/t", "")
        self.assertTrue(card["commands"]["read_a_file"].startswith("GET /file?path="))
        with open(os.path.join(bridge.HERE, "docs", "AGENT-API.md"), encoding="utf-8") as handle:
            self.assertIn(card["commands"]["read_a_file"], handle.read())            # the doc shows the card's words
        self.assertEqual(card["trend"], trend)                                                    # the whole record
        planets = {p["name"]: p for p in cards.map_card(world, [])["planets"]}
        self.assertEqual(planets["My Notes"]["trend"], "+2 stale cron lines")                     # only its sentence
        self.assertEqual(planets["Docs"]["trend"], "")
        self.assertIsNone(cards.unit_card(world, [], "area:Docs", "", "/t", "")["trend"])

    # ---- docs/AGENT-API.md: its own examples are run against the bridge ---------------------------
    def test_the_documented_post_examples_behave_as_documented(self):
        with open(os.path.join(bridge.HERE, "docs", "AGENT-API.md"), encoding="utf-8") as handle:
            doc = handle.read()
        plain, with_verify = (json.loads(m) for m in re.findall(r"\$BASE/agent/task \\\n  -d '(\{[^\n]*\})'", doc))
        self.assertNotIn("verify", plain)
        self.assertEqual((with_verify["verify"], with_verify["retries"]), ("python3 -m unittest -q", 2))
        folder = os.path.join(self.tmp.name, "documented")
        os.makedirs(folder)
        bearer = {"Authorization": "Bearer " + self.app.agent_token}

        def post(body):
            self.app.tasks.last_create = 0.0                          # skip the gap between new threads
            return self.http("POST", "/agent/task", headers=bearer, value=dict(body, target=folder, runner="writer"))

        self.assertEqual(post(plain)[0], 200)                         # works on the default config
        status, _, data = post(with_verify)
        self.assertEqual(status, 400)                                 # and the doc says why
        self.assertIn(json.loads(data)["error"], doc.replace("\n  ", " "))
        self.app.agent_verify = True
        self.assertEqual(post(with_verify)[0], 200)
        status, _, data = post(dict(plain, setup="true"))
        self.assertEqual((status, json.loads(data)["setup_cmd"]), (200, None))      # in place: skipped, as documented
        repo = os.path.join(self.tmp.name, "documented-repo")
        os.makedirs(repo)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        testenv.hermetic_repo(repo)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "init"],
                       cwd=repo, check=True)
        folder = repo
        status, _, data = post(dict(plain, setup="true"))
        self.assertEqual((status, json.loads(data)["setup_cmd"]), (200, "true"))     # in its own worktree: kept

    def test_every_status_the_bridge_answers_is_listed_in_the_error_section(self):
        with open(os.path.join(bridge.HERE, "docs", "AGENT-API.md"), encoding="utf-8") as handle:
            section = handle.read().split("## Errors", 1)[1]
        for status in (400, 403, 404, 405, 409, 411, 413, 415, 421, 429, 500, 501, 502):
            self.assertIn(str(status), section)
        # the two the page's reader is most likely to meet, each really answered by the bridge
        self.assertEqual(self.raw(f"PUT /agent/task HTTP/1.1\r\nHost: 127.0.0.1:{self.server.server_port}\r\n\r\n".encode())[0], 405)
        missing = os.path.join(self.tmp.name, "no-such-folder")
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        status, _, data = self.http("POST", "/agent/task", headers=bearer,
                                    value={"text": "x", "target": missing, "runner": "writer"})
        self.assertEqual((status, json.loads(data)["error"]), (404, "no such folder"))

    # ---- text the store and the page cannot write, credentials, and gzip ----
    def test_text_that_cannot_be_written_as_utf8_is_a_400_on_every_route_not_a_500(self):
        folder = os.path.join(self.tmp.name, "lone")
        os.makedirs(folder)
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        ui = self.ui_headers(origin=True)
        lone = "fix the \ud800 thing"                    # JSON carries it in as \ud800
        ask = {"text": lone, "target": folder, "runner": "writer"}
        self.assertEqual(self.http("POST", "/agent/task", headers=bearer, value=ask)[0], 400)
        self.assertEqual(self.http("POST", "/task", headers=ui, value=ask)[0], 400)
        self.assertEqual(self.http("POST", "/task", headers=ui,
                                   value=dict(ask, text="fine", verify="true \ud800"))[0], 400)
        self.assertEqual(self.http("POST", "/task", headers=ui,
                                   value=dict(ask, text="fine", target=folder + "\ud800"))[0], 400)
        self.assertEqual(self.http("POST", "/task", headers=ui,
                                   value=dict(ask, text="fine", model="m\ud800"))[0], 400)
        self.assertEqual(json.loads(self.http("GET", "/tasks", headers=self.ui_headers())[2])["tasks"], [],
                         "a refused text must leave nothing in the store")
        # a thread to follow up on and check
        self.app.tasks.last_create = 0.0
        status, _, data = self.http("POST", "/task", headers=ui, value=dict(ask, text="a fine task"))
        self.assertEqual(status, 200, data)
        tid = json.loads(data)["id"]
        self.assertEqual(self.http("POST", "/task/followup", headers=ui,
                                   value={"id": tid, "text": lone})[0], 400)
        self.assertEqual(self.http("POST", "/task/check", headers=ui,
                                   value={"id": tid, "runner": "writer", "text": lone})[0], 400)
        self.assertEqual(self.http("POST", "/agent/task/followup", headers=bearer,
                                   value={"id": tid, "text": lone})[0], 400)
        # the terminal routes
        snap = self.pane("hub")
        self.assertEqual(self.http("POST", "/say", headers=ui, value={
            "seat": "seat:hub", "text": lone, "revision": snap["revision"]})[0], 400)
        self.assertEqual(self.http("GET", "/remote/state?machine=lab-b", headers=self.ui_headers())[0], 200)
        self.assertEqual(self.http("POST", "/remote/say", headers=ui, value={
            "machine": "lab-b", "target": "w1:p1", "text": lone})[0], 400)
        self.assertEqual(self.http("POST", "/control", headers=bearer, value={
            "request_id": "compose-lone-01",
            "action": {"type": "compose", "target": "seat:grok", "text": lone}})[0], 400)
        self.assertEqual(self.http("POST", "/wingmate/ask", headers=ui, value={"q": lone})[0], 400)
        self.assertEqual(self.http("POST", "/dispatch", headers=ui,
                                   value={"kind": "task", "id": "t-one", "runner": "\ud800"})[0], 400)
        self.assertEqual(self.fake.sent, [])
        self.assertEqual(self.hop.prompts, [])
        with open(self.log, "rb") as handle:
            self.assertNotIn(b'"http-error"', handle.read(), "a 500 left an audit row")

    def test_a_board_hand_off_starts_the_agent_without_the_bridges_own_environment(self):
        # both ways a board item reaches an agent: a runner of the config, and the owner's dispatch tool
        root = self.tmp.name
        out = os.path.join(root, "env-out")
        os.makedirs(out)
        tool = os.path.join(root, "fake-dispatch")
        with open(tool, "w") as handle:
            handle.write('#!/bin/sh\nenv > "$ENV_OUT/dispatch.txt"\n'
                         'echo "dispatched d-20260101-000000-deepseek (bg) /out/x.md"\n')
        os.chmod(tool, 0o755)
        self.app.dispatch_runner, self.app.dispatch_bin = None, tool
        self.app.runners["dump-env"] = {"cmd": ["/bin/sh", "-c", 'env > "$ENV_OUT/runner.txt"'], "cwd": "tool",
                                        "max_minutes": 1, "report_dir": os.path.join(root, "reports")}
        mine = {"ENV_OUT": out, "BRIDGE_LIVE_PATH": "/x/run/bridge-1.json", "BRIDGE_PORT": "1",
                "CONTAINER_ROOT": "/x", "CONTAINER_CONFIG": "/x/c.json", "CONTAINER_OUT": "/x/o.html",
                "GIT_DIR": "/nonexistent/.git", "GIT_WORK_TREE": "/nonexistent",
                "GIT_AUTHOR_NAME": "me", "KEEP_ME": "1"}
        with mock.patch.dict(os.environ, mine):
            self.assertEqual(self.app._hand("deepseek", "task:t-one", "task", "t-one", "go", root)["ok"], True)
            self.app._hand("dump-env", "task:t-one", "task", "t-one", "go", root)
            end = time.time() + 10
            while time.time() < end and not os.path.exists(os.path.join(out, "runner.txt")):
                time.sleep(0.05)
            time.sleep(0.2)
        for who in ("dispatch", "runner"):
            with self.subTest(who):
                with open(os.path.join(out, who + ".txt"), encoding="utf-8") as handle:
                    seen = dict(row.split("=", 1) for row in handle.read().splitlines() if "=" in row)
                gone = ("BRIDGE_LIVE_PATH", "BRIDGE_PORT", "CONTAINER_ROOT", "CONTAINER_CONFIG",
                        "CONTAINER_OUT", "GIT_DIR", "GIT_WORK_TREE")
                kept = ("PATH", "HOME", "KEEP_ME", "GIT_AUTHOR_NAME", "ENV_OUT")
                # names only: a failure must not print the whole environment
                self.assertEqual(sorted(set(gone) & set(seen)), [])
                self.assertEqual(sorted(set(kept) - set(seen)), [])

    def test_private_files_of_clouds_and_containers_are_denied_whatever_the_letter_case(self):
        for rel in (".kube/config", ".docker/config.json", ".SSH/id_x", ".Kube/Config",
                    ".DOCKER/config.json", ".AWS/credentials", "Secrets/token.txt",
                    ".git-credentials", ".pgpass", "CREDENTIALS", "ID_RSA", "SERVER.PEM"):
            path = os.path.join(self.tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("x\n")
            status, _, data = self.http("GET", "/file?path=" + path, headers=self.ui_headers())
            self.assertEqual(status, 403, rel)
            bearer = {"Authorization": "Bearer " + self.app.agent_token}
            self.assertEqual(self.http("GET", "/file?path=" + path, headers=bearer)[0], 403, rel)
        plain = os.path.join(self.tmp.name, "notes", "kube-notes.md")        # a name that only looks like one
        os.makedirs(os.path.dirname(plain))
        with open(plain, "w") as handle:
            handle.write("fine\n")
        self.assertEqual(self.http("GET", "/file?path=" + plain, headers=self.ui_headers())[0], 200)

    def test_the_page_and_state_are_gzipped_for_a_client_that_takes_it_and_only_then(self):
        ui = self.ui_headers()
        _, _, plain_page = self.http("GET", "/", headers=ui)
        status, headers, packed = self.http("GET", "/", headers=dict(ui, **{"Accept-Encoding": "gzip, deflate"}))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get("Vary"), "Accept-Encoding")
        self.assertEqual(int(headers["Content-Length"]), len(packed))
        self.assertLess(len(packed), len(plain_page))
        page = gzip.decompress(packed).decode("utf-8")
        self.assertTrue(page.startswith("<!doctype html>"), page[:40])
        self.assertIn("window.CM_BRIDGE", page)
        self.assertIn("script-src 'nonce-", headers["Content-Security-Policy"])
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        # the plain answer is as before: no encoding header
        status, headers, _ = self.http("GET", "/", headers=ui)
        self.assertNotIn("Content-Encoding", headers)
        for refused in ("identity", "gzip;q=0", "br", "*;q=0"):
            headers = self.http("GET", "/", headers=dict(ui, **{"Accept-Encoding": refused}))[1]
            self.assertNotIn("Content-Encoding", headers, refused)
        for taken in ("GZIP", "gzip;q=0.5", "deflate, gzip;q=1.0", "*", "x-gzip"):
            headers = self.http("GET", "/", headers=dict(ui, **{"Accept-Encoding": taken}))[1]
            self.assertEqual(headers.get("Content-Encoding"), "gzip", taken)
        # /state, the thing a phone polls
        _, _, plain_state = self.http("GET", "/state", headers=ui)
        status, headers, packed = self.http("GET", "/state", headers=dict(ui, **{"Accept-Encoding": "gzip"}))
        self.assertEqual((status, headers.get("Content-Encoding")), (200, "gzip"))
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(gzip.decompress(packed), plain_state)
        again = self.http("GET", "/state", headers=dict(ui, **{"Accept-Encoding": "gzip"}))[2]
        self.assertEqual(again, packed)                  # built once per world, compressed once
        # a small answer is not worth compressing, and other routes are untouched
        headers = self.http("GET", "/tasks", headers=dict(ui, **{"Accept-Encoding": "gzip"}))[1]
        self.assertNotIn("Content-Encoding", headers)


class FakeClock:
    """The clock the phone-access state reads: a test moves it instead of waiting."""

    def __init__(self, start=1_800_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class PhoneAccessTests(unittest.TestCase):
    """Phone access: a second listener for paired devices, beside the untouched loopback one.
    The listener sits on 127.0.0.1 here (where a reverse proxy would reach it) and the requests
    carry the Host and Origin a browser on https://phone.example.com would send."""
    HOST = "phone.example.com"
    ORIGIN = "https://phone.example.com"
    COOKIE = "__Host-cm_device"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.game = os.path.join(root, "game.html")
        with open(self.game, "w", encoding="utf-8") as handle:
            handle.write("<!doctype html><style>body{color:white}</style><script>\n"
                         "const W = {};\nconst LIVE = location.protocol === 'http:';\n"
                         "function closeAll(){}\n</script>")
        self.log = os.path.join(root, "bridge.log")
        self.world = {"chars": [{"kind": "area", "name": "SECRETPLANET",
                                 "uid": "area:SECRETPLANET", "path": root}],
                      "quests": [], "tasks": {"source": "audit", "items": []}, "activity": [],
                      "inflight": [],
                      "generated": "now", "oldest": 0}
        self.clock = FakeClock()
        # Keep the phone port reserved until the bridge has bound its own port.
        reservation = socket.socket()
        self.addCleanup(reservation.close)
        reservation.bind(("127.0.0.1", 0))
        self.remote_port = reservation.getsockname()[1]
        self.app = bridge.BridgeApp(
            seats_path=os.path.join(root, "no-seats.json"), game_path=self.game,
            renderer_path="unused",
            live_path=os.path.join(root, ".bridge-live"), log_path=self.log, tmux_runner=FakeTmux(),
            world_loader=lambda: self.world, hop_runner=FakeHop(),
            synced_dir=os.path.join(root, "synced"),
            file_roots=(root,), areas_path=os.path.join(root, "areas.json"),
            verbs_dir=os.path.join(root, "verbs"),
            runs_path=os.path.join(root, "state", "runs.jsonl"),
            rulings_path=os.path.join(root, "state", "rulings.json"),
            tasks_dir=os.path.join(root, "tasks"),
            task_runners={"writer": {"cmd": ["/bin/sh", "-c",
                                             "cat >/dev/null; echo made > made.txt; "
                                             "echo '{\"type\":\"result\"}'"],
                                     "max_minutes": 1}},
            agent_verify=False, tick_seconds=0,
            remote={"listen": "127.0.0.1", "port": self.remote_port, "public_origin": self.ORIGIN},
            remote_clock=self.clock)
        self.server = bridge.BridgeHTTPServer((bridge.LOOPBACK, 0), bridge.Handler, self.app)
        self.app.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        reservation.close()
        self.assertTrue(self.app.start_remote(), self.app.remote.refused or self.app.remote.error)
        self.sessions = os.path.join(root, "remote-sessions.json")
        self.audit_path = os.path.join(root, "remote-audit.log")

    def tearDown(self):
        self.app.stop_remote()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    # ---- helpers ----
    def call(self, port, method, path, *, headers=None, value=None, host=None):
        headers = dict(headers or {})
        body = None
        if value is not None:
            body = json.dumps(value)
            headers["Content-Type"] = "application/json"
        if host is not None:
            headers["Host"] = host
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        result = (response.status, response, data)
        conn.close()
        return result

    def loop(self, method, path, **kw):
        return self.call(self.server.server_port, method, path, **kw)

    def phone(self, method, path, *, cookie=None, origin=True, fetch=True, host=None, value=None,
              extra=None):
        headers = dict(extra or {})
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = self.ORIGIN if origin is True else origin
        if fetch:
            headers["Sec-Fetch-Site"] = "same-origin"
        return self.call(self.remote_port, method, path, headers=headers, value=value,
                         host=host or self.HOST)

    def ui(self):
        """The desktop page's session: the cookie GET / hands to a loopback caller, with the
        headers the page sends."""
        _, response, _ = self.loop("GET", "/")
        cookie = response.getheader("Set-Cookie").split(";", 1)[0]
        origin = f"http://127.0.0.1:{self.server.server_port}"
        return {"Cookie": cookie, "Origin": origin, "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors"}

    def new_code(self):
        status, _, data = self.loop("POST", "/phone/pair", headers=self.ui(), value={})
        self.assertEqual(status, 200, data)
        return json.loads(data)["code"]

    def pair(self, name="Test phone", code=None):
        status, response, data = self.phone("POST", "/pair",
                                            value={"code": code or self.new_code(), "name": name})
        self.assertEqual(status, 200, data)
        return response.getheader("Set-Cookie").split(";", 1)[0]

    def test_the_phone_s_task_list_carries_no_health_thread(self):
        """GET /tasks on a phone: its sky (and the count beside it) is built from the phone's own world, so a
        Health thread's id and title never ride along in a contact, while the desktop list still has them."""
        groups = [{"lane": "Jobs", "label": "Jobs", "count": 1, "threads": [{"id": "t3-server-1:work",
                                                                            "title": "JOBSTHREAD"}]},
                  {"lane": "Health", "label": "Health", "count": 1, "private": True,
                   "threads": [{"id": "t3-server-1:quiet", "title": "HEALTHSECRET",
                                "member_ids": ["t3-server-1:quiet"]}],
                   "private_ids": ["t3-server-1:quiet"], "private_titles": ["HEALTHSECRET"]}]
        self.world["constellations"] = groups
        self.world["chars"] = self.world["chars"] + [
            {"uid": "ship:t3:" + tid, "kind": "ship", "source": "t3", "name": name, "machine": "server-1",
             "t3": {"state": "waiting", "thread_id": tid}}
            for tid, name in (("quiet", "HEALTHSECRET"), ("work", "JOBSTHREAD"))]
        cookie = self.pair()
        status, _, data = self.phone("GET", "/tasks", cookie=cookie)
        self.assertEqual(status, 200, data)
        got = json.loads(data)
        self.assertNotIn("HEALTHSECRET", data.decode())
        self.assertNotIn("quiet", json.dumps(got.get("sky", {})))
        self.assertEqual([c["id"] for c in got["sky"]["contacts"]], ["t3:work"])
        self.assertEqual(got["counts"]["total"], len(got["sky"]["contacts"]))
        status, _, data = self.loop("GET", "/tasks", headers=self.ui())
        desk = json.loads(data)
        self.assertEqual(sorted(c["id"] for c in desk["sky"]["contacts"]), ["t3:quiet", "t3:work"])
        self.assertEqual(desk["counts"]["total"], 2)

    def audit_rows(self):
        try:
            with open(self.audit_path, encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []

    def make_repo(self, name):
        folder = os.path.join(self.tmp.name, name)
        os.makedirs(folder)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=folder, check=True)
        testenv.hermetic_repo(folder)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q",
                        "--allow-empty", "-m", "init"],
                       cwd=folder, check=True)
        return folder

    def wait_review(self, tid, cookie):
        for _ in range(150):
            status, _, data = self.phone("GET", f"/task?id={tid}", cookie=cookie)
            got = json.loads(data)
            if got["status"] not in ("running", "new"):
                return got
            time.sleep(0.1)
        self.fail("the thread never settled")

    def test_a_private_folder_s_thread_shows_a_phone_only_its_state_and_the_folder_name(self):
        """A folder listed in `private` (or a dot-folder) reaches a phone as a name and counts, in the world and in
        the thread routes alike: a phone cannot start a thread there, and a thread started there from the desktop
        shows a phone its state only, never its words, files, output, diff or paths."""
        cookie = self.pair()
        repo = self.make_repo("vault")
        with open(os.path.join(repo, "diary-ZZ9.txt"), "w", encoding="utf-8") as handle:
            handle.write("x\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-qm", "s"], cwd=repo,
                       check=True)
        self.app.tasks.runners["writer"]["cmd"] = [
            "/bin/sh", "-c", "cat >/dev/null; echo y > notes-ZZ8.txt; ls; echo '{\"type\":\"result\"}'"]
        with mock.patch.dict(bridge._CFG, {"private": ["vault"]}):
            status, _, data = self.phone("POST", "/task", cookie=cookie,
                                         value={"text": "list the files ZZ7", "target": repo, "runner": "writer"})
            self.assertEqual(status, 403, data)
            tid = self.app.create_task({"text": "list the files ZZ7", "target": repo, "runner": "writer",
                                        "isolate": True})["id"]
            for _ in range(150):
                if self.app.task_detail({"id": [tid]})["status"] not in ("running", "new"):
                    break
                time.sleep(0.1)
            status, _, data = self.phone("GET", "/tasks", cookie=cookie)
            self.assertEqual(status, 200, data)
            row = next(t for t in json.loads(data)["tasks"] if t.get("private"))
            self.assertEqual((row["target"], row["title"]), ("vault", "a task in vault"))
            # its id is a stand-in: the real one is made from the prompt's words (…-list-the-files-zz7-…)
            self.assertTrue(row["id"].startswith("private-"), row["id"])
            text = data.decode().lower()
            for secret in ("ZZ7", "ZZ8", "ZZ9", tid, self.tmp.name):
                self.assertNotIn(secret.lower(), text, secret)
            for ident in (tid, row["id"]):
                for path in (f"/task?id={ident}", f"/task/diff?id={ident}"):
                    status, _, data = self.phone("GET", path, cookie=cookie)
                    self.assertEqual(status, 403, (path, data))
                    self.assertNotIn(b"ZZ8", data)
                for route, value in (("/task/followup", {"id": ident, "text": "more"}), ("/task/stop", {"id": ident}),
                                     ("/task/close", {"id": ident, "action": "discard"})):
                    status, _, data = self.phone("POST", route, cookie=cookie, value=value)
                    self.assertEqual(status, 403, (route, data))
                    self.assertNotIn(self.tmp.name.encode(), data)
            # an old thread the list no longer carries (past its 200 closed rows) is refused all the same
            with mock.patch.object(self.app.tasks, "list", return_value=[]):
                status, _, data = self.phone("GET", f"/task?id={tid}", cookie=cookie)
                self.assertEqual(status, 403, data)
            # a folder reached through a symlink is private too
            link = os.path.join(self.tmp.name, "public-link")
            os.symlink(repo, link)
            status, _, data = self.phone("POST", "/task", cookie=cookie,
                                         value={"text": "x", "target": link, "runner": "writer"})
            self.assertEqual(status, 403, data)
            # and a link with a '..' after it: shortcut -> vault/sub, asked as shortcut/../project (= vault/project)
            os.makedirs(os.path.join(repo, "sub"))
            os.makedirs(os.path.join(repo, "project"))
            os.symlink(os.path.join(repo, "sub"), os.path.join(self.tmp.name, "shortcut"))
            sneaky = os.path.join(self.tmp.name, "shortcut", "..", "project")
            self.assertEqual(bridge.phone_private(sneaky), "vault")
            status, _, data = self.phone("POST", "/task", cookie=cookie,
                                         value={"text": "x", "target": sneaky, "runner": "writer"})
            self.assertEqual(status, 403, data)
            # the world a phone is sent (the page, /state, the assistant's brief): the row and its sky contact alike
            rows = self.app._task(self.app.tasks.list)
            world = dict(self.world, agent_tasks=rows)
            world["sky"] = bridge._sky.build(world)
            self.assertIn("ZZ7", json.dumps(world))
            text = json.dumps(bridge.phone_world(world)).lower()
            for secret in ("ZZ7", "ZZ8", "ZZ9", tid, self.tmp.name):
                self.assertNotIn(secret.lower(), text, secret)
            self.assertIn("a task in vault", text)
        # private named through a symlink: the thread (recorded at the folder it resolves to) is private as well
        with mock.patch.dict(bridge._CFG, {"private": [os.path.join(self.tmp.name, "public-link")]}):
            status, _, data = self.phone("GET", "/tasks", cookie=cookie)
            self.assertNotIn(b"zz7", data.lower())
            self.assertEqual(self.phone("GET", f"/task?id={tid}", cookie=cookie)[0], 403)
            # the desktop page still has all of it
            self.assertIn("ZZ7", json.dumps(self.app.task_detail({"id": [tid]})))
        # a dot-folder is private without being listed
        dot = self.make_repo(".hidden-work")
        status, _, data = self.phone("POST", "/task", cookie=cookie,
                                     value={"text": "x", "target": dot, "runner": "writer"})
        self.assertEqual(status, 403, data)

    def test_a_stand_in_replaces_whole_ids_only(self):
        private = {"task": {"20261106-000000-diary-123abc": "vault"}}
        got = bridge.phone_swap_ids({"other": "20261106-000000-diary-123abc-456def",
                                     "contact": "task:20261106-000000-diary-123abc",
                                     "branch": "cm/20261106-000000-diary-123abc"}, private)
        self.assertEqual(got["other"], "20261106-000000-diary-123abc-456def")
        self.assertRegex(got["contact"], r"^task:private-[0-9a-f]{16}$")
        self.assertRegex(got["branch"], r"^cm/private-[0-9a-f]{16}$")

    def test_a_private_name_that_is_a_link_hides_the_planet_it_leads_to(self):
        root = self.tmp.name
        os.makedirs(os.path.join(root, "api"))
        os.symlink(os.path.join(root, "api"), os.path.join(root, "vault-name"))
        world = {"chars": [{"kind": "area", "uid": "area:api", "name": "api", "path": os.path.join(root, "api"),
                            "root_files": [["diary-ZZHTML.txt", "100B", 0]]}]}
        sent = json.dumps(bridge.phone_world(world, cfg={"private": ["vault-name"], "work_root": root}))
        self.assertNotIn("ZZHTML", sent)
        self.assertIn('"hidden": true', sent)

    # ---- unpaired callers get nothing ----
    def test_an_unpaired_caller_gets_no_data_and_no_cookie(self):
        status, response, data = self.phone("GET", "/", origin=False, fetch=False)
        self.assertEqual((status, response.getheader("Location"), data), (302, "/pair", b""))
        self.assertIsNone(response.getheader("Set-Cookie"))
        status, response, data = self.phone("GET", "/?fresh=1", origin=False, fetch=False)
        self.assertEqual((status, response.getheader("Location")), (302, "/pair"))
        status, response, data = self.phone("GET", "/pair", origin=False, fetch=False)
        self.assertEqual(status, 200)
        self.assertIsNone(response.getheader("Set-Cookie"))
        self.assertIn(b"Pair this device", data)
        for leak in (b"SECRETPLANET", b"const W", b"CM_BRIDGE", self.app.agent_token.encode(),
                     self.app.ui_token.encode()):
            self.assertNotIn(leak, data)
        self.assertIn("script-src 'nonce-", response.getheader("Content-Security-Policy"))
        for path in ("/state", "/tasks", "/task?id=x", "/task/diff?id=x", "/wingmate",
                     "/wingmate/answer?id=0"):
            status, response, data = self.phone("GET", path)
            self.assertEqual(status, 401, path)
            self.assertEqual(json.loads(data), {"error": "pair this device first"})
            self.assertIsNone(response.getheader("Set-Cookie"))
        for path, value in (("/task", {"text": "x", "target": self.tmp.name, "runner": "writer"}),
                            ("/task/followup", {"id": "x", "text": "y"}),
                            ("/task/stop", {"id": "x"}),
                            ("/task/close", {"id": "x", "action": "done"}),
                            ("/wingmate/ask", {"q": "hi"})):
            self.assertEqual(self.phone("POST", path, value=value)[0], 401, path)
        # nothing was paired, so nothing was written
        self.assertFalse(os.path.exists(self.sessions))

    def test_a_forged_or_foreign_cookie_is_no_session(self):
        self.pair()
        for cookie in (f"{self.COOKIE}=nope", f"{self.COOKIE}=", "cm_device=x",
                       self.ui()["Cookie"]):
            self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 401, cookie)
            status, response, _ = self.phone("GET", "/", cookie=cookie)
            self.assertEqual((status, response.getheader("Location")), (302, "/pair"))

    # ---- pairing ----
    def test_a_long_lived_code_lasts_its_hours_survives_a_restart_and_still_works_once(self):
        """`pair --hours N`: the owner can mint a code that waits days for the phone. It is still one
        use, still a hash only, and kept in a mode-600 file beside the devices, so restarting the map
        (a new PairingCodes on the same file) does not void it."""
        for bad in ({"hours": 0}, {"hours": 169}, {"hours": True}, {"hours": "24"}, {"minutes": 5},
                    {"hours": 24, "x": 1}):
            self.assertEqual(self.loop("POST", "/phone/pair", headers=self.ui(), value=bad)[0], 400, bad)
        status, _, data = self.loop("POST", "/phone/pair", headers=self.ui(), value={"hours": 48})
        self.assertEqual(status, 200, data)
        made = json.loads(data)
        self.assertEqual(made["expires_in"], 48 * 3600)
        codes_path = os.path.join(self.tmp.name, "remote-codes.json")
        self.assertEqual(os.stat(codes_path).st_mode & 0o777, 0o600)
        with open(codes_path, encoding="utf-8") as handle:
            self.assertNotIn(made["code"], handle.read(), "the file holds hashes, never the code")
        # a restart: a fresh set of codes read from the same file still knows it
        self.app.remote.codes = bridge.PairingCodes(self.clock, codes_path)
        self.clock.advance(47 * 3600)
        self.pair(code=made["code"])
        with open(codes_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["codes"], {}, "a used code leaves the file")
        status, _, _ = self.phone("POST", "/pair", value={"code": made["code"], "name": "again"})
        self.assertEqual(status, 403, "one use")
        # past its hours it is dead, and a week is the most a code can ask for
        late = json.loads(self.loop("POST", "/phone/pair", headers=self.ui(), value={"hours": 1})[2])["code"]
        self.clock.advance(3601)
        self.assertEqual(self.phone("POST", "/pair", value={"code": late, "name": "late"})[0], 403)
        codes = bridge.PairingCodes(self.clock)
        self.assertEqual(codes.create(10 * 86400)[1], 7 * 86400)
        self.assertEqual(codes.create(5)[1], 60)
        self.assertEqual(codes.create()[1], 600)

    # ---- pairing ----
    def test_pairing_trades_a_code_made_on_loopback_for_a_device_cookie(self):
        # a code is made from loopback with the page's session, never by the agent token or
        # from the phone side
        self.assertEqual(self.loop("POST", "/phone/pair", value={})[0], 403)
        bearer = {"Authorization": "Bearer " + self.app.agent_token,
                  "Origin": f"http://127.0.0.1:{self.server.server_port}"}
        self.assertEqual(self.loop("POST", "/phone/pair", headers=bearer, value={})[0], 403)
        self.assertEqual(self.loop("POST", "/phone/pair",
                                   headers=dict(self.ui(), Origin="https://evil.example"),
                                   value={})[0], 403)
        self.assertEqual(self.phone("POST", "/phone/pair", value={})[0], 404)
        self.assertEqual(self.loop("GET", "/phone")[0], 403)
        status, _, data = self.loop("POST", "/phone/pair", headers=self.ui(), value={})
        made = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual((made["expires_in"], made["url"]), (600, self.ORIGIN + "/pair"))
        self.assertRegex(made["code"], r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{8}$")
        # the code is typed on the phone, spaces and lower case forgiven
        spaced = made["code"][:4].lower() + " " + made["code"][4:]
        status, response, data = self.phone("POST", "/pair",
                                            value={"code": spaced, "name": "  My <b>iPhone</b>  "})
        self.assertEqual(status, 200, data)
        self.assertEqual(json.loads(data)["name"], "My biPhoneb")
        cookie = response.getheader("Set-Cookie")
        name, _, rest = cookie.partition("=")
        token, _, attrs = rest.partition(";")
        self.assertEqual(name, "__Host-cm_device")
        # 32 random bytes, url-safe base64
        self.assertEqual(len(token), 43)
        for attribute in ("HttpOnly", "SameSite=Lax", "Path=/", "Secure",       # Lax: links from other apps keep the pairing
                          f"Max-Age={30 * 86400}"):
            self.assertIn(attribute, attrs)
        self.assertNotIn("Domain", attrs)
        # the device file holds a hash, never the cookie, at mode 600
        with open(self.sessions, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(token, text)
        self.assertEqual(stat.S_IMODE(os.stat(self.sessions).st_mode), 0o600)
        row = json.loads(text)["devices"][0]
        self.assertEqual((row["name"], len(row["hash"])), ("My biPhoneb", 64))
        # and with it the phone gets the page, live and remote, without ever being handed the
        # desktop session
        cookie_pair = f"{name}={token}"
        status, response, page = self.phone("GET", "/", cookie=cookie_pair, origin=False,
                                            fetch=False)
        self.assertEqual(status, 200)
        text = page.decode()
        self.assertIn('window.CM_BRIDGE = {"live":true,"remote":true};\nconst W = ', text)
        self.assertIn("SECRETPLANET", text)
        # a page load keeps the 30 days rolling
        self.assertIn(f"{name}={token}", response.getheader("Set-Cookie"))
        self.assertNotIn(bridge.COOKIE_NAME, response.getheader("Set-Cookie"))
        self.assertEqual(self.phone("GET", "/state", cookie=cookie_pair)[0], 200)
        self.assertEqual(self.phone("GET", "/tasks", cookie=cookie_pair)[0], 200)
        # a paired browser that opens /pair goes back to the map
        status, response, _ = self.phone("GET", "/pair", cookie=cookie_pair, origin=False,
                                         fetch=False)
        self.assertEqual((status, response.getheader("Location")), (302, "/"))
        # the desktop list names the device
        status, _, data = self.loop("GET", "/phone", headers=self.ui())
        listing = json.loads(data)
        self.assertEqual((listing["enabled"], listing["listening"], listing["public_origin"]),
                         (True, True, self.ORIGIN))
        self.assertEqual([d["name"] for d in listing["devices"]], ["My biPhoneb"])
        self.assertNotIn("hash", listing["devices"][0])
        # the loopback page says it is not remote
        _, _, desk = self.loop("GET", "/")
        self.assertIn('window.CM_BRIDGE = {"live":true,"remote":false};\nconst W = ', desk.decode())

    def test_an_http_tailnet_origin_names_its_cookie_without_the_host_prefix(self):
        self.app.stop_remote()
        port = free_port()
        self.app.remote = bridge.RemoteAccess({"listen": "127.0.0.1", "port": port,
                                               "allow_http": True,
                                               "public_origin": "http://100.101.102.103:7429"},
                                              self.tmp.name, self.clock)
        self.assertTrue(self.app.start_remote())
        origin = "http://100.101.102.103:7429"
        code = self.new_code()
        status, response, _ = self.call(port, "POST", "/pair", host="100.101.102.103:7429",
                                        value={"code": code},
                                        headers={"Origin": origin})
        self.assertEqual(status, 200)
        cookie = response.getheader("Set-Cookie")
        self.assertTrue(cookie.startswith("cm_device="), cookie)
        self.assertIn("HttpOnly; SameSite=Lax; Path=/", cookie)
        self.assertNotIn("Secure", cookie)
        self.assertEqual(self.call(port, "GET", "/state", host="100.101.102.103:7429",
                                   headers={"Cookie": cookie.split(";", 1)[0]})[0], 200)
        self.assertEqual(self.call(port, "GET", "/state", host=self.HOST,
                                   headers={"Cookie": cookie.split(";", 1)[0]})[0], 421)

    def test_a_code_works_once_and_dies_in_ten_minutes(self):
        code = self.new_code()
        self.pair(code=code)
        status, _, data = self.phone("POST", "/pair", value={"code": code})
        self.assertEqual((status, json.loads(data)["error"]),
                         (403, "that code is wrong, already used or expired"))
        stale = self.new_code()
        self.clock.advance(601)
        self.assertEqual(self.phone("POST", "/pair", value={"code": stale})[0], 403)
        fresh = self.new_code()
        self.clock.advance(599)
        self.pair(code=fresh)
        self.clock.advance(61)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"})[0], 403)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "short"})[0], 403)
        self.assertEqual(self.phone("POST", "/pair", value={"code": 12345678})[0], 400)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH", "extra": 1})[0],
                         400)
        # five live codes at most: a sixth retires the oldest
        self.clock.advance(61)
        codes = [self.new_code() for _ in range(6)]
        self.assertEqual(self.phone("POST", "/pair", value={"code": codes[0]})[0], 403)
        self.assertEqual(self.phone("POST", "/pair", value={"code": codes[5]})[0], 200)

    def test_two_phones_racing_for_one_code_get_one_cookie(self):
        code = self.new_code()
        results = []

        def attempt():
            results.append(self.phone("POST", "/pair", value={"code": code})[0])

        workers = [threading.Thread(target=attempt) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(sorted(results), [200, 403, 403, 403])
        self.assertEqual(len(self.app.remote.devices.listing()), 1)

    def test_pairing_is_rate_limited_per_peer_and_locks_out_after_twenty_failures(self):
        wrong = {"code": "ABCDEFGH"}
        statuses = [self.phone("POST", "/pair", value=wrong)[0] for _ in range(6)]
        self.assertEqual(statuses, [403] * 5 + [429])             # five tries a minute
        status, response, _ = self.phone("POST", "/pair", value=wrong)
        self.assertEqual(status, 429)
        self.assertGreaterEqual(int(response.getheader("Retry-After")), 1)
        # two more minutes of five wrong guesses each
        for _ in range(2):
            self.clock.advance(61)
            for _ in range(5):
                self.assertEqual(self.phone("POST", "/pair", value=wrong)[0], 403)
        self.clock.advance(61)
        # ...and the twentieth wrong guess shuts the peer out
        for _ in range(5):
            self.assertEqual(self.phone("POST", "/pair", value=wrong)[0], 403)
        good = self.new_code()
        self.clock.advance(61)
        status, response, _ = self.phone("POST", "/pair", value={"code": good})
        # locked: even the right code waits
        self.assertEqual(status, 429)
        self.assertGreater(int(response.getheader("Retry-After")), 60)
        self.clock.advance(15 * 60)
        # the lockout is over; that code died meanwhile
        self.assertEqual(self.phone("POST", "/pair", value={"code": good})[0], 403)
        self.clock.advance(61)
        self.assertEqual(self.phone("POST", "/pair", value={"code": self.new_code()})[0], 200)

    def test_a_right_code_clears_the_failure_count(self):
        for _ in range(19):
            self.clock.advance(16)
            self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"})[0], 403)
        self.clock.advance(16)
        # a right code: the nineteen are forgotten
        self.pair()
        for _ in range(19):
            self.clock.advance(16)
            self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"})[0], 403)
        self.clock.advance(16)
        self.assertEqual(self.phone("POST", "/pair", value={"code": self.new_code()})[0], 200)

    def test_sixteen_devices_is_the_limit_and_a_refused_pairing_keeps_its_code(self):
        for n in range(bridge.DEVICE_MAX):
            self.clock.advance(61)
            self.pair(name=f"phone {n}")
        code = self.new_code()
        self.clock.advance(61)
        status, _, data = self.phone("POST", "/pair", value={"code": code})
        self.assertEqual(status, 409, data)
        victim = json.loads(self.loop("GET", "/phone", headers=self.ui())[2])["devices"][0]["id"]
        self.assertEqual(self.loop("POST", "/phone/revoke", headers=self.ui(),
                                   value={"id": victim})[0], 200)
        # the code was still good
        self.assertEqual(self.phone("POST", "/pair", value={"code": code})[0], 200)

    def test_a_device_idle_for_thirty_days_must_pair_again_but_use_keeps_it(self):
        cookie = self.pair()
        for _ in range(4):
            # used every twenty days: still paired
            self.clock.advance(20 * 86400)
            self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 200)
        self.clock.advance(30 * 86400 + 1)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 401)
        self.assertEqual(self.app.remote.devices.listing(), [])
        with open(self.sessions, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["devices"], [])

    def test_the_device_file_is_kept_across_a_restart(self):
        cookie = self.pair()
        again = bridge.RemoteAccess({}, self.tmp.name, self.clock)
        self.assertEqual([d["name"] for d in again.devices.listing()], ["Test phone"])
        self.assertTrue(again.devices.find(cookie.split("=", 1)[1]))
        with open(self.sessions, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        broken = bridge.RemoteAccess({}, self.tmp.name, self.clock)
        self.assertEqual(broken.devices.listing(), [])               # fail closed: nobody is paired

    # ---- Host and Origin ----
    def test_host_and_origin_are_pinned_to_the_public_origin(self):
        cookie = self.pair()
        self.assertEqual(self.phone("GET", "/state", cookie=cookie, host="evil.example.com")[0],
                         421)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie,
                                    host="phone.example.com:7429")[0], 421)
        # hosts are not case-sensitive
        self.assertEqual(self.phone("GET", "/state", cookie=cookie, host="PHONE.example.com")[0],
                         200)
        self.assertEqual(self.phone("GET", "/pair", host="127.0.0.1")[0], 421)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"},
                                    host="evil.example.com")[0], 421)
        # a read may come with no Origin or the right one, never another
        self.assertEqual(self.phone("GET", "/state", cookie=cookie, origin=False)[0], 200)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie,
                                    origin="https://evil.example.com")[0], 403)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie,
                                    origin="http://phone.example.com")[0], 403)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie, origin="null")[0], 403)
        # every mutation needs the exact Origin: a missing one is a 403 too
        ask = {"text": "make a file", "target": self.tmp.name, "runner": "writer"}
        for origin in (False, "https://evil.example.com", "https://phone.example.com:8443",
                       "http://phone.example.com", "null"):
            self.assertEqual(self.phone("POST", "/task", cookie=cookie, origin=origin,
                                        value=ask)[0], 403, origin)
            self.assertEqual(self.phone("POST", "/pair", origin=origin,
                                        value={"code": "ABCDEFGH"})[0], 403, origin)
        self.assertEqual(self.app.tasks.list(), [])
        # fetch metadata from another site is refused on what a script on the page calls
        self.assertEqual(self.phone("GET", "/state", cookie=cookie,
                                    extra={"Sec-Fetch-Site": "cross-site"}, fetch=False)[0], 403)
        self.assertEqual(self.phone("POST", "/task/stop", cookie=cookie, fetch=False,
                                    value={"id": "x"},
                                    extra={"Sec-Fetch-Site": "same-site"})[0], 403)
        # no CORS, ever
        status, response, _ = self.phone("OPTIONS", "/state", cookie=cookie)
        self.assertEqual(status, 403)
        status, response, _ = self.phone("GET", "/state", cookie=cookie)
        self.assertFalse([h for h,
                          _ in response.getheaders() if h.lower().startswith("access-control")])
        self.assertEqual(self.phone("PUT", "/state", cookie=cookie)[0], 405)

    # ---- the allowlist ----
    def test_every_desktop_route_is_a_404_on_the_phone_listener(self):
        cookie = self.pair()
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        gets = ["/agent/map", "/agent/unit?uid=area:x", "/agent/tasks", "/agent/task?id=x",
                "/file?path=/etc/hostname",
                "/control", "/control?after=0", "/verbs", "/pane?seat=hub&lines=5",
                "/remote/state?machine=lab-a",
                "/remote/world?machine=lab-a", "/remote/pane?machine=lab-a&target=w1", "/phone",
                "/game.html",
                "/open", "/door", "/say", "/permit", "/dispatch", "/resolve", "/checkin",
                "/task/check", "/meta", "/meta/answer?id=000000000000", "/nothing"]
        for path in gets:
            for headers in ({}, bearer):
                status, _, data = self.phone("GET", path, cookie=cookie, extra=headers)
                self.assertEqual(status, 404, path)
                self.assertEqual(json.loads(data), {"error": "not found"})
        posts = ["/agent/task", "/agent/task/followup", "/agent/task/check", "/control", "/open",
                 "/door", "/say",
                 "/permit", "/dispatch", "/verb", "/resolve", "/checkin", "/task/check",
                 "/remote/render",
                 "/remote/say", "/phone/pair", "/phone/revoke", "/pane", "/file",
                 "/meta/ask", "/task/rename"]
        for path in posts:
            for headers in ({}, bearer):
                self.assertEqual(self.phone("POST", path, cookie=cookie, extra=headers,
                                            value={"id": "x"})[0], 404, path)
        # nothing of it reached the app
        self.assertEqual(self.app.queue, [])
        self.assertEqual(self.app.tasks.list(), [])
        rows = [r for r in self.audit_rows() if r["reason"] == "route-not-allowed"]
        # a phone asking for these is audited...
        self.assertGreater(len(rows), 30)
        # ...an unknown path is not
        self.assertFalse([r for r in self.audit_rows() if r["route"] == "/nothing"])
        # and the allowed ones answer
        for path in ("/state", "/tasks", "/wingmate"):
            self.assertEqual(self.phone("GET", path, cookie=cookie)[0], 200, path)
        self.assertEqual(self.phone("GET", "/state?fresh=1", cookie=cookie)[0], 400)
        self.assertEqual(self.phone("GET", "/?fresh=1", cookie=cookie)[0], 400)

    def test_the_desktop_only_fields_of_a_new_thread_are_refused_from_a_phone(self):
        cookie = self.pair()
        folder = self.make_repo("proj")             # a phone's thread needs a repo with a commit
        ask = {"text": "make a file", "target": folder, "runner": "writer"}
        for extra in ({"verify": "true"}, {"verify": ""}, {"verify": None}, {"retries": 0},
                      {"retries": 2},
                      {"setup": "npm ci"}, {"setup": None},
                      {"verify": "true", "retries": 1, "setup": "x"},
                      {"isolate": False}, {"isolate": "yes"}):
            status, _, data = self.phone("POST", "/task", cookie=cookie, value=dict(ask, **extra))
            self.assertEqual(status, 403, extra)
            self.assertIn("desktop page" if "isolate" not in extra else "own worktree",
                          json.loads(data)["error"])
        self.assertEqual(self.app.tasks.list(), [])                  # nothing was started
        self.assertEqual(self.phone("POST", "/task", cookie=cookie, value=dict(ask, extra=1))[0],
                         400)
        status, _, data = self.phone("POST", "/task", cookie=cookie, value=dict(ask, isolate=True))
        self.assertEqual(status, 200, data)
        started = json.loads(data)
        self.assertEqual((started["runner"], started["verify_cmd"], started["setup_cmd"],
                          started["retries"]),
                         ("writer", None, None, 0))
        self.wait_review(started["id"], cookie)
        with open(self.log, encoding="utf-8") as handle:
            audit = handle.read()
        self.assertIn('"via":"phone:', audit)
        self.assertNotIn("make a file", audit)
        # the same fields stay open to the desktop page
        self.app.tasks.last_create = 0.0
        status, _, data = self.loop("POST", "/task", headers=self.ui(),
                                    value=dict(ask, verify="true", retries=1))
        self.assertEqual(status, 200, data)

    def test_a_phone_starts_a_thread_only_where_it_gets_its_own_worktree(self):
        """A folder in a git repo with a commit: its own worktree. Anything the engine would work
        in place in (no repo, a repo with no commit) is refused from a phone with the reason, and
        starts nothing; the desktop page may still work there in place."""
        cookie = self.pair()
        plain = os.path.join(self.tmp.name, "plain")
        os.makedirs(plain)
        fresh = os.path.join(self.tmp.name, "fresh")
        os.makedirs(fresh)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=fresh, check=True)
        testenv.hermetic_repo(fresh)
        for folder, why in ((plain, "not inside a git repository"),
                            (fresh, "no commits yet")):
            self.app.tasks.last_create = 0.0
            status, _, data = self.phone("POST", "/task", cookie=cookie,
                                         value={"text": "make a file", "target": folder,
                                                "runner": "writer"})
            error = json.loads(data)["error"]
            self.assertEqual(status, 400, (folder, data))
            self.assertIn(why, error)
            self.assertIn("own worktree", error)
            self.assertIn("at least one commit", error)
            self.assertIn("desktop page", error)
            self.assertNotIn("always", error)
        self.assertEqual(self.app.tasks.list(), [])                   # nothing was started
        self.assertEqual([n for n in os.listdir(os.path.join(self.tmp.name, "tasks"))
                          if not n.startswith(".")], [], "and no half-made thread folder")
        # the desktop page keeps working in place
        self.app.tasks.last_create = 0.0
        status, _, data = self.loop("POST", "/task", headers=self.ui(),
                                    value={"text": "make a file", "target": plain,
                                           "runner": "writer"})
        self.assertEqual((status, json.loads(data)["mode"]), (200, "inplace"), data)
        # a repo with a commit: the phone's thread gets its own worktree
        repo = self.make_repo("withcommit")
        self.app.tasks.last_create = 0.0
        status, _, data = self.phone("POST", "/task", cookie=cookie,
                                     value={"text": "make a file", "target": repo,
                                            "runner": "writer"})
        self.assertEqual(status, 200, data)
        started = json.loads(data)
        self.assertEqual(started["mode"], "worktree")
        self.assertTrue(started["branch"].startswith("cm/"))
        self.wait_review(started["id"], cookie)
        self.assertFalse(os.path.exists(os.path.join(repo, "made.txt")), "not in the folder itself")

    def test_a_phone_replies_stops_and_reads_threads(self):
        cookie = self.pair()
        folder = self.make_repo("proj2")
        status, _, data = self.phone("POST", "/task", cookie=cookie,
                                     value={"text": "make a file", "target": folder,
                                            "runner": "writer"})
        tid = json.loads(data)["id"]
        got = self.wait_review(tid, cookie)
        self.assertEqual(got["status"], "review")
        self.assertIn(tid,
                      [t["id"] for t in json.loads(self.phone("GET", "/tasks",
                                                              cookie=cookie)[2])["tasks"]])
        self.assertEqual(self.phone("GET", f"/task/diff?id={tid}", cookie=cookie)[0], 200)
        # nothing runs
        self.assertEqual(self.phone("POST", "/task/stop", cookie=cookie, value={"id": tid})[0], 409)
        self.assertEqual(self.phone("POST", "/task/followup", cookie=cookie, value={"id": tid})[0],
                         400)
        self.assertEqual(self.phone("POST", "/task/check", cookie=cookie,
                                    value={"id": tid, "runner": "writer"})[0], 404)

    def test_a_phone_lands_a_clean_thread_but_never_one_with_the_evidence_against_it(self):
        cookie = self.pair()
        repo = self.make_repo("repo")
        ask = {"text": "make a file", "target": repo, "runner": "writer"}
        # a thread whose verify fails (started on the desktop, where verify is allowed)
        status, _, data = self.loop("POST", "/task", headers=self.ui(),
                                    value=dict(ask, verify="false"))
        bad = json.loads(data)["id"]
        got = self.wait_review(bad, cookie)
        self.assertTrue(got["merge_blockers"], got)
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                text=True).stdout
        for body in ({"id": bad, "action": "merge"}, {"id": bad, "action": "discard"},
                     {"id": bad, "action": "done"}):
            status, _, data = self.phone("POST", "/task/close", cookie=cookie, value=body)
            self.assertEqual(status, 409, body)
            self.assertIn("the evidence says not yet", json.loads(data)["error"])
        status, _, data = self.phone("POST", "/task/close", cookie=cookie,
                                     value={"id": bad, "action": "merge", "force": True})
        self.assertEqual((status, json.loads(data)["error"]),
                         (403, "landing a thread past the evidence is for the desktop page"))
        for force in (1, "yes", None):
            self.assertEqual(self.phone("POST", "/task/close", cookie=cookie,
                                        value={"id": bad, "action": "merge", "force": force})[0],
                                        403, force)
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                        capture_output=True, text=True).stdout, before)
        self.assertEqual(self.phone("GET", f"/task?id={bad}", cookie=cookie)[0], 200)
        self.assertEqual(json.loads(self.phone("GET", f"/task?id={bad}",
                                               cookie=cookie)[2])["status"], "failed")
        # a clean one lands
        self.app.tasks.last_create = 0.0
        status, _, data = self.phone("POST", "/task", cookie=cookie, value=ask)
        self.assertEqual(status, 200, data)
        good = json.loads(data)["id"]
        got = self.wait_review(good, cookie)
        self.assertEqual((got["status"], got["merge_blockers"]), ("review", []))
        self.assertEqual(self.phone("POST", "/task/close", cookie=cookie,
                                    value={"id": good, "action": "merge", "x": 1})[0], 400)
        status, _, data = self.phone("POST", "/task/close", cookie=cookie,
                                     value={"id": good, "action": "merge", "force": False})
        self.assertEqual((status, json.loads(data)["status"]), (200, "merged"), data)
        self.assertTrue(os.path.exists(os.path.join(repo, "made.txt")))
        self.assertEqual(self.phone("POST", "/task/close", cookie=cookie,
                                    value={"id": "nope", "action": "merge"})[0], 400)
        self.assertEqual(self.phone("POST", "/task/close", cookie=cookie,
                                    value={"id": "20200101-000000-nothing-abcdef",
                                           "action": "merge"})[0], 404)

    def test_a_blocker_that_lands_after_the_phone_looked_still_stops_every_close(self):
        # the phone reads the blockers, then closes: a verdict that lands between the two reads is
        # only seen by the store, under its own lock, and it must stop a set-aside and a done too
        cookie = self.pair()
        repo = self.make_repo("racerepo")
        status, _, data = self.loop("POST", "/task", headers=self.ui(),
                                    value={"text": "make a file", "target": repo,
                                           "runner": "writer", "verify": "false"})
        tid = json.loads(data)["id"]
        got = self.wait_review(tid, cookie)
        self.assertTrue(got["merge_blockers"], got)
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                text=True).stdout
        self.app.task_blockers = lambda _tid: []          # the read the phone made, before it
        for action in ("merge", "discard", "done"):
            status, _, data = self.phone("POST", "/task/close", cookie=cookie,
                                         value={"id": tid, "action": action})
            self.assertEqual(status, 409, (action, data))
            self.assertIn("the evidence says not yet", json.loads(data)["error"])
        self.assertEqual(json.loads(self.phone("GET", f"/task?id={tid}", cookie=cookie)[2])["status"],
                         "failed")
        self.assertEqual(subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True,
                                        text=True).stdout, before)
        # the desktop page is not held to it: it sees the blockers and asks for the discard anyway
        status, _, data = self.loop("POST", "/task/close", headers=self.ui(),
                                    value={"id": tid, "action": "discard"})
        self.assertEqual((status, json.loads(data)["status"]), (200, "discarded"), data)

    def test_the_wingmate_is_asked_a_few_times_a_minute_per_device(self):
        self.app.mate_spec = {"name": "Pip", "model": "fake", "max_seconds": 5, "env": {},
                              "cmd": ["/bin/sh", "-c",
                                      "cat >/dev/null; echo 'Your notes are in docs.'"]}
        cookie = self.pair()
        other = self.pair(name="second")
        self.assertEqual(self.phone("GET", "/wingmate", cookie=cookie)[0], 200)
        for n in range(bridge.DEVICE_MATE_RATE):
            status, _, data = self.phone("POST", "/wingmate/ask", cookie=cookie,
                                         value={"q": f"where? {n}"})
            # 409: still thinking about the last one
            self.assertIn(status, (200, 409), data)
            time.sleep(0.2)
        status, response, _ = self.phone("POST", "/wingmate/ask", cookie=cookie,
                                         value={"q": "once more"})
        self.assertEqual(status, 429)
        self.assertTrue(response.getheader("Retry-After"))
        status, _, data = self.phone("POST", "/wingmate/ask", cookie=other,
                                     value={"q": "my own budget"})
        self.assertIn(status, (200, 409))                          # the other device has its own
        jid = None
        for _ in range(40):
            status, _, data = self.phone("POST", "/wingmate/ask", cookie=other,
                                         value={"q": "again"})
            if status == 200:
                jid = json.loads(data)["id"]
                break
            time.sleep(0.1)
        if jid:
            for _ in range(60):
                got = json.loads(self.phone("GET", f"/wingmate/answer?id={jid}", cookie=other)[2])
                if got["status"] != "thinking":
                    break
                time.sleep(0.05)
            self.assertEqual(got["answer"], "Your notes are in docs.")

    def test_a_device_has_a_request_budget_of_its_own(self):
        self.app.remote.requests = bridge.SlidingLimit(5, 60.0, self.clock)
        cookie = self.pair()
        other = self.pair(name="second")
        statuses = [self.phone("GET", "/state", cookie=cookie)[0] for _ in range(7)]
        self.assertEqual(statuses, [200] * 5 + [429] * 2)
        self.assertEqual(self.phone("GET", "/state", cookie=other)[0], 200)
        self.clock.advance(61)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 200)

    # ---- revoking ----
    def test_revoking_a_device_ends_its_session_at_once(self):
        cookie = self.pair(name="lost phone")
        keep = self.pair(name="kept phone")
        self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 200)
        devices = json.loads(self.loop("GET", "/phone", headers=self.ui())[2])["devices"]
        lost = next(d["id"] for d in devices if d["name"] == "lost phone")
        # the page's session only
        self.assertEqual(self.loop("POST", "/phone/revoke", value={"id": lost})[0], 403)
        self.assertEqual(self.phone("POST", "/phone/revoke", cookie=cookie, value={"id": lost})[0],
                         404)
        self.assertEqual(self.loop("POST", "/phone/revoke", headers=self.ui(),
                                   value={"id": "zzzz"})[0], 400)
        self.assertEqual(self.loop("POST", "/phone/revoke", headers=self.ui(),
                                   value={"id": "00000000"})[0], 404)
        status, _, data = self.loop("POST", "/phone/revoke", headers=self.ui(), value={"id": lost})
        self.assertEqual((status, json.loads(data)), (200, {"revoked": lost}))
        status, _, data = self.phone("GET", "/state", cookie=cookie)
        self.assertEqual((status, json.loads(data)["error"]), (401, "pair this device first"))
        self.assertEqual(self.phone("POST", "/task/stop", cookie=cookie, value={"id": "x"})[0], 401)
        self.assertEqual(self.phone("GET", "/state", cookie=keep)[0], 200)
        listed = json.loads(self.loop("GET", "/phone", headers=self.ui())[2])["devices"]
        with open(self.sessions, encoding="utf-8") as handle:
            self.assertEqual([d["id"] for d in json.load(handle)["devices"]],
                             [d["id"] for d in listed])
        self.assertEqual(self.app.remote.devices.count(), 1)

    # ---- the audit ----
    def test_the_audit_names_refused_requests_and_never_a_code_or_a_cookie(self):
        cookie = self.pair()
        secret_code = self.new_code()
        self.phone("POST", "/pair", value={"code": "WRONGCDE"})
        self.phone("POST", "/pair", origin="https://evil.example.com", value={"code": secret_code})
        self.phone("GET", "/state", host="evil.example.com", cookie=cookie)
        self.phone("GET", "/state", cookie=f"{self.COOKIE}=stolenvalue1234")
        self.phone("GET", "/agent/map", cookie=cookie)
        self.phone("POST", "/task", cookie=cookie, origin=False, value={})
        rows = self.audit_rows()
        reasons = {(r["route"], r["status"], r["reason"]) for r in rows}
        self.assertTrue({("/pair", 403, "bad-code"), ("/pair", 403, "bad-origin"),
                         ("/state", 421, "bad-host"),
                         ("/state", 401, "no-session"), ("/agent/map", 404, "route-not-allowed"),
                         ("/task", 403, "no-origin")} <= reasons, reasons)
        for row in rows:
            self.assertEqual(set(row), {"timestamp", "route", "status", "reason", "peer"})
            self.assertEqual(row["peer"], "127.0.0.1")
        with open(self.audit_path, encoding="utf-8") as handle:
            text = handle.read()
        for secret in (secret_code, "WRONGCDE", "stolenvalue1234", cookie.split("=", 1)[1]):
            self.assertNotIn(secret, text)
        self.assertEqual(stat.S_IMODE(os.stat(self.audit_path).st_mode), 0o600)

    def test_the_audit_rotates_and_stays_bounded(self):
        audit = bridge.RemoteAudit(os.path.join(self.tmp.name, "small.log"), limit=2000)
        for n in range(200):
            audit.add(f"/route/{n}", 403, "bad-code", "10.0.0.1")
        sizes = [os.path.getsize(audit.path), os.path.getsize(audit.path + ".1")]
        self.assertTrue(all(size <= 2000 for size in sizes), sizes)
        with open(audit.path, encoding="utf-8") as handle:
            last = [json.loads(line) for line in handle][-1]
        self.assertEqual(last["route"], "/route/199")
        self.assertEqual(stat.S_IMODE(os.stat(audit.path + ".1").st_mode), 0o600)

    # ---- loopback stays as it was ----
    def test_the_loopback_listener_behaves_as_before(self):
        status, response, data = self.loop("GET", "/")
        self.assertEqual(status, 200)
        set_cookie = response.getheader("Set-Cookie")
        self.assertTrue(set_cookie.startswith(bridge.COOKIE_NAME + "="))
        self.assertIn("HttpOnly; SameSite=Strict; Path=/", set_cookie)
        self.assertEqual(self.loop("GET", "/state", headers=self.ui())[0], 200)
        self.assertEqual(self.loop("GET", "/state")[0], 403)
        # the public name is not a loopback name
        self.assertEqual(self.loop("GET", "/", host="phone.example.com")[0], 421)
        self.assertEqual(self.loop("GET", "/", host=f"localhost:{self.server.server_port}")[0], 200)
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        self.assertEqual(self.loop("GET", "/agent/tasks", headers=bearer)[0], 200)
        # the device cookie is no desktop session, and the desktop session is no device
        cookie = self.pair()
        self.assertEqual(self.loop("GET", "/state", headers={"Cookie": cookie})[0], 403)
        self.assertEqual(self.loop("GET", "/phone", headers={"Cookie": cookie})[0], 403)
        self.assertEqual(self.phone("GET", "/state", cookie=self.ui()["Cookie"])[0], 401)
        # the loopback bind is still the only one /bridge/ accepts
        with mock.patch.dict(os.environ, {"BRIDGE_HOST": "0.0.0.0"}):
            self.assertEqual(bridge.main([]), 2)

    def test_every_page_the_phone_listener_serves_carries_the_desktops_policy_and_nonces(self):
        """The real page (its early theme script and inlined font included) and the pairing page,
        from the phone listener, get the policy the loopback page gets: data: fonts allowed, and
        the nonce of the header on every inline script and style, the injected CM_BRIDGE one too."""
        BridgeTests.render_real_page(self)
        _, desk_response, desk_data = self.loop("GET", "/")
        cookie = self.pair()
        status, phone_response, phone_data = self.phone("GET", "/", cookie=cookie)
        self.assertEqual(status, 200)
        _, pair_response, pair_data = self.phone("GET", "/pair", origin=False, fetch=False)
        pages = {"desktop": (desk_response, desk_data, False), "phone": (phone_response,
                                                                         phone_data, True),
                 "pairing": (pair_response, pair_data, None)}
        shapes = set()
        for name, (response, body, remote) in pages.items():
            policy = response.getheader("Content-Security-Policy")
            directives = {d.split(" ", 1)[0]: d.split(" ", 1)[1:] for d in policy.split("; ")}
            self.assertIn("data:", directives.get("font-src", [""])[0].split(), name)
            self.assertEqual(directives["default-src"], ["'none'"], name)
            nonce = re.search(r"'nonce-([^']+)'", policy).group(1)
            # the one thing that differs: the desktop page may frame the agent-page frame route (that path, on each name
            # the loopback listener answers to, and no other); a phone is never shown such a frame
            frame_src = directives["frame-src"]
            want = ([f"http://{n}:{self.server.server_port}/show/page/frame" for n in ("127.0.0.1", "localhost")]
                    if name == "desktop" else ["'none'"])
            self.assertEqual(frame_src, [" ".join(want)], name)
            shapes.add(policy.replace(nonce, "N").replace("frame-src " + frame_src[0], "frame-src F"))
            html = body.decode("utf-8")
            tags = re.findall(r"<(script|style)\b([^>]*)>", html)
            self.assertTrue(tags, name)
            for tag, attrs in tags:
                self.assertIn(f'nonce="{nonce}"', attrs, f"{name}: <{tag}{attrs}> would be blocked")
            if remote is not None:
                boot = re.search(r"<script\b([^>]*)>(?:(?!</script>).)*?window\.CM_BRIDGE = "
                                 r"(\{[^;]*\});", html, re.S)
                self.assertIsNotNone(boot, name)
                self.assertIn(f'nonce="{nonce}"', boot.group(1), name)
                self.assertEqual(json.loads(boot.group(2)), {"live": True, "remote": remote}, name)
            else:
                self.assertNotIn("CM_BRIDGE", html)
        self.assertEqual(len(shapes), 1, "one policy, loopback and phone alike")

    def test_the_file_preview_never_serves_the_phone_state_or_a_live_file(self):
        """remote-sessions.json (the paired devices, hashed), remote-audit.log and the bridge's
        credentials file (ROOT/.container-map/run/bridge-<port>.json, or any bridge-<port>.json a
        --live-file named) are under the root and still refused, to the page and to the agent."""
        self.pair()
        self.assertTrue(os.path.isfile(self.sessions))
        with open(self.audit_path, "a", encoding="utf-8") as handle:
            handle.write("{}\n")
        run = os.path.join(self.tmp.name, ".container-map", "run")
        os.makedirs(run)
        elsewhere = os.path.join(self.tmp.name, "live")
        os.makedirs(elsewhere)
        codes = os.path.join(self.tmp.name, "remote-codes.json")
        self.loop("POST", "/phone/pair", headers=self.ui(), value={"hours": 2})
        self.assertTrue(os.path.isfile(codes))
        # the owner's read chats (quotes) and another machine's thread list sit beside run/: refused the same way
        scan = os.path.join(self.tmp.name, ".container-map", "chat-scan", "findings")
        remote = os.path.join(self.tmp.name, ".container-map", "t3-remote")
        os.makedirs(scan)
        os.makedirs(remote)
        refused = [self.sessions, self.audit_path, codes, os.path.join(run, "bridge-7428.json"),
                   os.path.join(run, "anything.txt"), os.path.join(elsewhere, "bridge-7428.json"),
                   os.path.join(scan, "t3-a:b.json"), os.path.join(remote, "server-2.json")]
        for path in refused[3:]:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"agent_token": "x"}')
        bearer = {"Authorization": "Bearer " + self.app.agent_token}
        for path in refused:
            status, _, data = self.loop("GET", "/file?path=" + path, headers=self.ui())
            self.assertEqual(status, 403, path)
            self.assertNotIn(b"agent_token", data)
            self.assertEqual(self.loop("GET", "/file?path=" + path, headers=bearer)[0], 403, path)
        # a neighbour with an ordinary name is still readable
        ordinary = os.path.join(self.tmp.name, "notes.txt")
        with open(ordinary, "w", encoding="utf-8") as handle:
            handle.write("fine")
        self.assertEqual(self.loop("GET", "/file?path=" + ordinary, headers=self.ui())[0], 200)
        # and the phone listener has no file route at all
        cookie = self.pair()
        self.assertEqual(self.phone("GET", "/file?path=" + self.sessions, cookie=cookie)[0], 404)

    def test_a_bridge_without_a_remote_block_opens_no_second_listener(self):
        for raw in ({}, None):
            app = bridge.BridgeApp(seats_path="", game_path=self.game, renderer_path="unused",
                                   live_path=os.path.join(self.tmp.name, "l2"),
                                   log_path=os.path.join(self.tmp.name, "b2.log"),
                                   world_loader=lambda: self.world,
                                   tasks_dir=os.path.join(self.tmp.name, "tasks2"),
                                   remote=raw if raw is not None else {})
            app.port = 7428
            self.assertFalse(app.start_remote())
            self.assertFalse(app.remote.listening)
            status = app.phone_status()
            self.assertEqual((status["enabled"], status["listening"], status["error"]),
                             (False, False, ""))
            with self.assertRaises(bridge.BridgeError) as caught:
                app.phone_new_code()
            self.assertEqual(caught.exception.status, 409)
            self.assertIn("phone access is off", caught.exception.message)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "remote-audit.log.x")))

    # ---- the settings ----
    def test_settings_refuse_what_would_expose_the_map_by_accident(self):
        parse = bridge.parse_remote
        self.assertEqual(parse({}, 7428), (None, ""))
        self.assertEqual(parse(None, 7428), (None, ""))
        ok, why = parse({"listen": "100.101.102.103",
                         "public_origin": "http://100.101.102.103:7429"}, 7428)
        self.assertEqual(why, "")
        self.assertEqual((ok.listen, ok.port, ok.origin, ok.authority, ok.https, ok.cookie),
                         ("100.101.102.103", 7429, "http://100.101.102.103:7429",
                          "100.101.102.103:7429", False, "cm_device"))
        ok, why = parse({"listen": "127.0.0.1", "port": 9000,
                         "public_origin": "https://Phone.Example.com:443/"}, 7428)
        self.assertEqual((why, ok.port, ok.origin, ok.authority, ok.cookie),
                         ("", 9000, "https://phone.example.com",
                                                                               "phone.example.com",
                                                                               "__Host-cm_device"))
        ok, _ = parse({"listen": "127.0.0.1", "public_origin": "https://phone.example.com:8443"},
                      7428)
        self.assertEqual(ok.authority, "phone.example.com:8443")
        ok, _ = parse({"listen": "fd7a:115c:a1e0::1",
                       "public_origin": "https://[fd7a:115c:a1e0::1]:8443"}, 7428)
        self.assertEqual((ok.listen, ok.authority),
                         ("fd7a:115c:a1e0::1", "[fd7a:115c:a1e0::1]:8443"))
        # http outside the Tailscale range: refused unless allow_http says so
        for listen in ("127.0.0.1", "192.168.1.5", "100.63.255.255", "100.128.0.0", "10.0.0.2"):
            got, why = parse({"listen": listen, "public_origin": "http://phone.lan:7429"}, 7428)
            self.assertIsNone(got, listen)
            self.assertIn("clear text", why)
            self.assertIn("100.64.0.0/10", why)
            self.assertIsNotNone(parse({"listen": listen, "public_origin": "http://phone.lan:7429",
                                        "allow_http": True}, 7428)[0])
        for listen in ("100.64.0.0", "100.127.255.255"):
            self.assertIsNotNone(parse({"listen": listen,
                                        "public_origin": "http://phone.lan:7429"}, 7428)[0], listen)
        for bad in ({"listen": "0.0.0.0", "public_origin": "https://p.example.com"},
                    {"listen": "::", "public_origin": "https://p.example.com"},
                    {"listen": "phone.local", "public_origin": "https://p.example.com"},
                    {"listen": 5, "public_origin": "https://p.example.com"},
                    {"public_origin": "https://p.example.com"},
                    {"listen": "127.0.0.1"},
                    {"listen": "127.0.0.1", "public_origin": "ftp://p.example.com"},
                    {"listen": "127.0.0.1", "public_origin": "https://"},
                    {"listen": "127.0.0.1", "public_origin": "https://user:pw@p.example.com"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com/app"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com/?x=1"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com #x"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com:99999"},
                    {"listen": "127.0.0.1", "public_origin": "https://-bad-.example.com"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com", "port": 0},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com",
                     "port": "7429"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com", "port": True},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com", "port": 7428},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com",
                     "allow_http": "yes"},
                    {"listen": "127.0.0.1", "public_origin": "https://p.example.com",
                     "public-origin": "x"},
                    "on", ["listen"], 1):
            got, why = parse(bad, 7428)
            self.assertIsNone(got, bad)
            self.assertTrue(why, bad)
        got, why = parse({"listen": "127.0.0.1", "public_origin": "https://p.example.com"}, 65535)
        self.assertEqual((got, why), (None, "remote.port must be a whole number from 1 to 65535 "
                                            "(default: the bridge port + 1)"))

    def test_a_refused_block_is_said_and_the_loopback_bridge_carries_on(self):
        self.app.stop_remote()
        bad = bridge.RemoteAccess({"listen": "127.0.0.1",
                                   "public_origin": "http://phone.lan:7429"}, self.tmp.name,
                                   self.clock)
        self.app.remote = bad
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(self.app.start_remote())
        self.assertIn("clear text", err.getvalue())
        self.assertFalse(bad.listening)
        status, _, data = self.loop("GET", "/phone", headers=self.ui())
        self.assertEqual(status, 200)
        got = json.loads(data)
        self.assertEqual((got["enabled"], got["listening"]), (False, False))
        self.assertIn("clear text", got["error"])
        status, _, data = self.loop("POST", "/phone/pair", headers=self.ui(), value={})
        self.assertEqual(status, 409)
        self.assertIn("clear text", json.loads(data)["error"])
        with open(self.log, encoding="utf-8") as handle:
            self.assertIn('"event":"remote-refused"', handle.read())
        self.assertEqual(self.loop("GET", "/state", headers=self.ui())[0], 200)

    def test_a_port_that_cannot_be_opened_is_said_and_not_fatal(self):
        self.app.stop_remote()
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen(1)
            port = taken.getsockname()[1]
            self.app.remote = bridge.RemoteAccess({"listen": "127.0.0.1", "port": port,
                                                   "public_origin": self.ORIGIN}, self.tmp.name,
                                                   self.clock)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(self.app.start_remote())
        self.assertIn(f"cannot listen on 127.0.0.1:{port}", self.app.remote.error)
        got = json.loads(self.loop("GET", "/phone", headers=self.ui())[2])
        self.assertEqual((got["enabled"], got["listening"]), (True, False))
        self.assertIn("cannot listen", got["error"])
        self.assertEqual(self.loop("POST", "/phone/pair", headers=self.ui(), value={})[0], 409)

    # ---- the listener that could not bind at start ----
    def flaky_listener(self, fails, hold=None):
        """RemoteHTTPServer standing in for the real one: it raises each error of `fails` in turn;
        the attempt after them waits for `hold` (an Event, if given), then binds for real."""
        real, attempts = bridge.RemoteHTTPServer, []

        def make(address, handler, app):
            attempts.append(address)
            if len(attempts) <= len(fails):
                raise fails[len(attempts) - 1]
            if hold is not None:
                hold.wait(10)
            return real(address, handler, app)
        return make, attempts

    def wait_for(self, check, what, seconds=10):
        end = time.time() + seconds
        while time.time() < end:
            if check():
                return
            time.sleep(0.02)
        self.fail("never happened: " + what)

    def test_a_listener_that_cannot_bind_at_start_retries_until_it_can_and_blocks_nothing(self):
        self.app.stop_remote()
        with open(self.log, encoding="utf-8") as handle:
            opened = handle.read().count('"event":"remote-listening"')      # setUp's own listener
        gone = OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
        busy = OSError(errno.EADDRINUSE, "Address already in use")
        hold = threading.Event()
        make, attempts = self.flaky_listener([gone, gone, gone, busy, busy], hold)
        self.remote_port = free_port()
        access = bridge.RemoteAccess({"listen": "127.0.0.1", "port": self.remote_port,
                                      "public_origin": self.ORIGIN}, self.tmp.name, self.clock)
        self.app.remote = access
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(bridge, "RemoteHTTPServer", make), \
                mock.patch.object(bridge, "REMOTE_RETRY_SECONDS", 0.02), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            began = time.monotonic()
            self.assertFalse(self.app.start_remote())
            self.assertLess(time.monotonic() - began, 5, "start_remote does not wait for the bind")
            self.assertFalse(access.listening)
            # five failures later the sixth attempt is under way (held): the retry is a thread
            # of its own and the loopback bridge answers while it is
            self.wait_for(lambda: len(attempts) >= 6, "six bind attempts")
            self.assertEqual(self.loop("GET", "/state", headers=self.ui())[0], 200)
            self.assertEqual(self.loop("GET", "/phone", headers=self.ui())[0], 200)
            status, _, data = self.loop("POST", "/phone/pair", headers=self.ui(), value={})
            self.assertEqual(status, 409)
            self.assertIn("trying again every", json.loads(data)["error"])
            hold.set()
            self.wait_for(lambda: access.listening, "the listener opening")
            self.assertEqual(access.error, "")
            self.assertIn("phone access: listening on 127.0.0.1:", out.getvalue())
        # five failures, two kinds: each said once, on screen and in the audit
        notes = [line for line in err.getvalue().splitlines() if line.startswith("phone access:")]
        self.assertEqual(len(notes), 2, notes)
        self.assertIn("Cannot assign requested address", notes[0])
        self.assertIn("Address already in use", notes[1])
        with open(self.log, encoding="utf-8") as handle:
            logged = handle.read()
        self.assertEqual(logged.count('"event":"remote-failed"'), 2)
        self.assertEqual(logged.count('"event":"remote-listening"'), opened + 1)
        # the late listener is the real thing: a phone pairs through it
        self.assertEqual(self.phone("GET", "/pair", origin=False, fetch=False)[0], 200)
        self.pair()
        self.app.stop_remote()
        self.assertFalse(access.listening)
        self.assertIsNone(access.retry)
        self.assertEqual(self.phone_closed(), True)

    def phone_closed(self):
        try:
            socket.create_connection(("127.0.0.1", self.remote_port), timeout=1).close()
        except OSError:
            return True
        return False

    def test_a_stop_ends_the_bind_retry_and_a_repeated_failure_is_said_once(self):
        self.app.stop_remote()
        make, attempts = self.flaky_listener([OSError(errno.EADDRNOTAVAIL, "no such address")] * 10_000)
        access = bridge.RemoteAccess({"listen": "127.0.0.1", "port": free_port(),
                                      "public_origin": self.ORIGIN}, self.tmp.name, self.clock)
        self.app.remote = access
        err = io.StringIO()
        with mock.patch.object(bridge, "RemoteHTTPServer", make), \
                mock.patch.object(bridge, "REMOTE_RETRY_SECONDS", 0.01), \
                contextlib.redirect_stderr(err):
            self.assertFalse(self.app.start_remote())
            self.wait_for(lambda: len(attempts) >= 8, "eight bind attempts")
            retry = access.retry
            self.assertTrue(retry.is_alive())
            began = time.monotonic()
            self.app.stop_remote()
            self.assertLess(time.monotonic() - began, 3)
            self.assertFalse(retry.is_alive(), "the retry thread went with the stop")
            seen = len(attempts)
            time.sleep(0.2)
            self.assertEqual(len(attempts), seen, "and no attempt is made after it")
        self.assertEqual(len([l for l in err.getvalue().splitlines()
                              if l.startswith("phone access:")]), 1)

    def test_an_address_this_machine_does_not_have_yet_is_retried_not_given_up_on(self):
        self.app.stop_remote()
        probe = socket.socket()
        try:
            probe.bind(("100.101.102.103", 0))
        except OSError:
            pass
        else:
            self.skipTest("this machine has 100.101.102.103")
        finally:
            probe.close()
        access = bridge.RemoteAccess(
            {"listen": "100.101.102.103", "port": free_port(),
             "public_origin": "http://100.101.102.103:7429"}, self.tmp.name, self.clock)
        self.app.remote = access
        with mock.patch.object(bridge, "REMOTE_RETRY_SECONDS", 0.05), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.app.start_remote())
            self.assertIn("cannot listen on 100.101.102.103:", access.error)
            self.assertTrue(access.retry.is_alive(), "it keeps trying; the config was fine")
            self.assertEqual(self.loop("GET", "/state", headers=self.ui())[0], 200)
            self.app.stop_remote()
        self.assertIsNone(access.retry)

    # ---- the command line ----
    def test_the_cli_pairs_and_lists_and_revokes_through_the_running_bridge(self):
        import cli
        port = str(self.server.server_port)

        def run(*args):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.main(list(args))
                # _die: the message is on stderr, the code is the exit
                except SystemExit as exc:
                    code = exc.code
            return code, out.getvalue(), err.getvalue()

        code, out, err = run("pair", "--port", port)
        self.assertEqual(code, 0, err)
        self.assertIn(self.ORIGIN + "/pair", out)
        shown = re.search(r"code\s+([A-Z2-9]{4}) ([A-Z2-9]{4})", out)
        self.assertTrue(shown, out)
        cookie = self.pair(code="".join(shown.groups()), name="from the cli")
        code, out, err = run("pair", "--port", port, "--json")
        made = json.loads(out)
        self.assertEqual((code, made["url"], made["expires_in"]), (0, self.ORIGIN + "/pair", 600))
        code, out, err = run("devices", "--port", port)
        self.assertEqual(code, 0, err)
        self.assertIn("from the cli", out)
        code, out, err = run("devices", "--port", port, "--json")
        devices = json.loads(out)["devices"]
        self.assertEqual([d["name"] for d in devices], ["from the cli"])
        self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 200)
        code, out, err = run("devices", "--port", port, "--revoke", devices[0]["id"])
        self.assertEqual(code, 0, err)
        self.assertIn("revoked", out)
        self.assertEqual(self.phone("GET", "/state", cookie=cookie)[0], 401)
        code, out, err = run("devices", "--port", port, "--revoke", devices[0]["id"])
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no such device", err)
        code, out, err = run("devices", "--port", port)
        self.assertIn("no phone is paired", out)
        self.assertEqual(run("devices", "--port", port, "--bogus")[0], 2)
        self.assertEqual(run("pair", "--port", "0")[0], 2)
        # a bridge that has phone access off says so, verbatim
        self.app.stop_remote()
        self.app.remote = bridge.RemoteAccess({}, self.tmp.name, self.clock)
        self.app.remote.configure(self.app.port)
        code, out, err = run("pair", "--port", port)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("phone access is off", err)
        # no bridge at all
        code, out, err = run("pair", "--port", str(free_port()))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("no map answers", err)

    # ---- text a store cannot write, gzip, and a reverse proxy ----
    def test_text_that_cannot_be_written_as_utf8_is_a_400_from_a_phone_too(self):
        cookie = self.pair()
        repo = self.make_repo("lone-repo")
        lone = "fix the \ud800 thing"
        self.assertEqual(self.phone("POST", "/task", cookie=cookie, value={
            "text": lone, "target": repo, "runner": "writer"})[0], 400)
        self.assertEqual(self.phone("POST", "/wingmate/ask", cookie=cookie, value={"q": lone})[0], 400)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "AB\ud800CDEFG"})[0], 400)
        self.assertEqual(json.loads(self.phone("GET", "/tasks", cookie=cookie)[2])["tasks"], [])

    def test_the_phone_gets_the_page_and_state_gzipped_when_it_asks_for_it(self):
        self.world["chars"] += [{"kind": "area", "name": f"PLANET{n}", "uid": f"area:PLANET{n}",
                                 "path": self.tmp.name} for n in range(40)]
        cookie = self.pair()
        headers = {"Accept-Encoding": "gzip"}
        _, _, plain = self.phone("GET", "/", cookie=cookie, origin=False, fetch=False)
        status, response, packed = self.phone("GET", "/", cookie=cookie, origin=False, fetch=False,
                                              extra=headers)
        self.assertEqual((status, response.getheader("Content-Encoding")), (200, "gzip"))
        self.assertEqual(response.getheader("Vary"), "Accept-Encoding")
        self.assertEqual(int(response.getheader("Content-Length")), len(packed))
        page = gzip.decompress(packed).decode("utf-8")
        self.assertIn('"remote":true', page.replace(" ", ""))
        self.assertLess(len(packed), len(plain))
        self.assertIsNone(self.phone("GET", "/", cookie=cookie, origin=False, fetch=False)[1]
                          .getheader("Content-Encoding"))
        status, response, packed = self.phone("GET", "/state", cookie=cookie, extra=headers)
        self.assertEqual((status, response.getheader("Content-Encoding")), (200, "gzip"))
        _, _, plain_state = self.phone("GET", "/state", cookie=cookie)
        self.assertEqual(gzip.decompress(packed), plain_state)
        # the pairing page is left alone
        self.assertIsNone(self.phone("GET", "/pair", origin=False, fetch=False, extra=headers)[1]
                          .getheader("Content-Encoding"))

    def behind_proxy(self, proxies):
        """The phone listener again, with remote.trusted_proxy set to `proxies`."""
        self.app.stop_remote()
        self.app.remote = bridge.RemoteAccess(
            {"listen": "127.0.0.1", "port": self.remote_port, "public_origin": self.ORIGIN,
             "trusted_proxy": proxies}, self.tmp.name, self.clock)
        self.assertTrue(self.app.start_remote(), self.app.remote.refused or self.app.remote.error)

    def forwarded(self, client, wrong="ABCDEFGH"):
        return self.phone("POST", "/pair", value={"code": wrong},
                          extra={"X-Forwarded-For": client} if client else None)

    def test_behind_a_trusted_proxy_each_phone_has_its_own_pairing_limit(self):
        self.behind_proxy(["127.0.0.1"])
        # the proxy's own connection is trusted, so the last hop is the phone
        self.assertEqual([self.forwarded("203.0.113.5")[0] for _ in range(6)], [403] * 5 + [429])
        self.assertEqual(self.forwarded("203.0.113.9")[0], 403)          # another phone: not limited
        self.assertEqual(self.forwarded("203.0.113.5")[0], 429)          # the first one still is
        # only the last hop counts: what the phone put in front of it is its own claim
        self.assertEqual(self.forwarded("198.51.100.1, 203.0.113.5")[0], 429)
        self.assertEqual(self.forwarded("203.0.113.5, 203.0.113.77")[0], 403)
        # the audit names the phone, not the proxy
        peers = {row["peer"] for row in self.audit_rows()}
        self.assertTrue({"203.0.113.5", "203.0.113.9", "203.0.113.77"} <= peers, peers)
        # a missing or malformed header leaves the proxy's own address: one shared limit
        for header in (None, "", "not-an-ip", "203.0.113.5, nonsense"):
            self.clock.advance(61)
            statuses = [self.forwarded(header)[0] for _ in range(6)]
            self.assertEqual(statuses, [403] * 5 + [429], header)
        self.assertIn("127.0.0.1", {row["peer"] for row in self.audit_rows()})

    def test_the_lockout_is_per_forwarded_phone_too(self):
        self.behind_proxy(["127.0.0.1"])
        for _ in range(4):                                # 20 wrong guesses from one phone
            for _ in range(5):
                self.assertEqual(self.forwarded("203.0.113.5")[0], 403)
            self.clock.advance(61)
        self.assertEqual(self.forwarded("203.0.113.5")[0], 429)          # locked out
        code = self.new_code()
        status, response, _ = self.phone("POST", "/pair", value={"code": code},
                                         extra={"X-Forwarded-For": "203.0.113.5"})
        self.assertEqual(status, 429)
        self.assertGreater(int(response.getheader("Retry-After")), 60)
        status, _, data = self.phone("POST", "/pair", value={"code": code, "name": "other"},
                                     extra={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(status, 200, data)                              # the other phone pairs

    def test_x_forwarded_for_is_believed_only_from_a_listed_proxy(self):
        # no list (the default), or a list that does not hold the connection's address
        for proxies in ([], ["192.0.2.1"], ["::1"]):
            self.behind_proxy(proxies)
            self.clock.advance(61)
            first = [self.forwarded(f"203.0.113.{n}")[0] for n in range(1, 7)]
            self.assertEqual(first, [403] * 5 + [429], proxies)          # one peer for all of them
            self.assertEqual({row["peer"] for row in self.audit_rows()[-6:]}, {"127.0.0.1"})

    def test_trusted_proxy_is_checked(self):
        port = 7428
        raw = {"listen": "127.0.0.1", "public_origin": "https://phone.example.com"}
        settings, why = bridge.parse_remote(raw, port)
        self.assertEqual((why, settings.trusted_proxy), ("", ()))
        settings, why = bridge.parse_remote(
            dict(raw, trusted_proxy=["127.0.0.1", "::ffff:10.0.0.2", "[::1]", "127.0.0.1"]), port)
        self.assertEqual((why, settings.trusted_proxy), ("", ("127.0.0.1", "10.0.0.2", "::1")))
        for bad in ("127.0.0.1", ["nginx"], [1], ["127.0.0.1", ""], {"ip": "127.0.0.1"}, [None]):
            settings, why = bridge.parse_remote(dict(raw, trusted_proxy=bad), port)
            self.assertIsNone(settings, bad)
            self.assertIn("remote.trusted_proxy must be a list of the IP addresses", why)

    # ---- the tailnet route: a proxy on 127.0.0.1, its login header, the new game ----
    LOGIN = "alice@example.com"

    def log_rows(self, event):
        """The bridge log's rows of one event."""
        with open(self.log, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        return [row for row in rows if row.get("event") == event]

    def phone_raw(self, method, path, headers, value=None):
        """A phone request whose header lines go out exactly as listed, a name twice included."""
        conn = http.client.HTTPConnection("127.0.0.1", self.remote_port, timeout=5)
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for name, text in headers:
            conn.putheader(name, text)
        body = json.dumps(value).encode("utf-8") if value is not None else None
        if body is not None:
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        response = conn.getresponse()
        result = (response.status, response, response.read())
        conn.close()
        return result

    def test_the_proxy_block_a_tailnet_route_needs_is_accepted_and_pins_its_port(self):
        raw = {"listen": "127.0.0.1", "port": 7429,
               "public_origin": "https://box.example-tailnet.ts.net:10000",
               "trusted_proxy": ["127.0.0.1"]}
        settings, why = bridge.parse_remote(raw, 7428)
        self.assertEqual(why, "")
        self.assertEqual((settings.listen, settings.port, settings.origin, settings.authority,
                          settings.https, settings.allow_http, settings.cookie,
                          settings.trusted_proxy),
                         ("127.0.0.1", 7429, "https://box.example-tailnet.ts.net:10000",
                          "box.example-tailnet.ts.net:10000", True, False, "__Host-cm_device",
                          ("127.0.0.1",)))
        # the same block on a listener: the browser's Host keeps the port, and a Host without
        # it (a proxy route on another port) is misdirected
        self.app.stop_remote()
        self.app.remote = bridge.RemoteAccess(dict(raw, port=self.remote_port), self.tmp.name,
                                              self.clock)
        self.assertTrue(self.app.start_remote(), self.app.remote.refused or self.app.remote.error)
        host = "box.example-tailnet.ts.net"
        self.assertEqual(self.phone("GET", "/pair", origin=False, fetch=False,
                                    host=host + ":10000")[0], 200)
        self.assertEqual(self.phone("GET", "/pair", origin=False, fetch=False, host=host)[0], 421)
        self.assertEqual(self.audit_rows()[-1]["reason"], "bad-host")

    def test_a_trusted_proxy_names_the_tailnet_user_in_the_audit_and_nothing_else_does(self):
        self.behind_proxy(["127.0.0.1"])
        via = {"X-Forwarded-For": "203.0.113.5", "Tailscale-User-Login": self.LOGIN}
        # refusals: a wrong code, a desktop-only route, a wrong origin
        self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"}, extra=via)[0],
                         403)
        cookie = self.pair_via(via)
        self.assertEqual(self.phone("GET", "/pane", cookie=cookie, extra=via)[0], 404)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"},
                                    origin="https://evil.example", extra=via)[0], 403)
        rows = self.audit_rows()
        self.assertEqual([(r["reason"], r["peer"], r.get("tailscale_user_login")) for r in rows],
                         [("bad-code", "203.0.113.5", self.LOGIN),
                          ("route-not-allowed", "203.0.113.5", self.LOGIN),
                          ("bad-origin", "203.0.113.5", self.LOGIN)])
        # the bridge log: the pairing and what a paired phone did
        paired = self.log_rows("phone-paired")[-1]
        self.assertEqual((paired["peer"], paired["tailscale_user_login"]),
                         ("203.0.113.5", self.LOGIN))
        repo = self.make_repo("tailnet-proj")
        status, _, data = self.phone("POST", "/task", cookie=cookie, extra=via,
                                     value={"text": "make a file", "target": repo,
                                            "runner": "writer"})
        self.assertEqual(status, 200, data)
        self.wait_review(json.loads(data)["id"], cookie)
        did = self.log_rows("phone")[-1]
        self.assertEqual((did["route"], did["tailscale_user_login"]), ("/task", self.LOGIN))
        # never a code or a cookie beside it
        for row in rows + [paired, did]:
            self.assertNotIn(cookie.split("=", 1)[1], json.dumps(row))
        # from a connection that is not a listed proxy the header is the caller's own claim
        for proxies in ([], ["192.0.2.1"]):
            self.behind_proxy(proxies)
            self.clock.advance(61)
            self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"},
                                        extra=via)[0], 403)
            self.assertNotIn("tailscale_user_login", self.audit_rows()[-1], proxies)
            self.pair_via(via)
            self.assertNotIn("tailscale_user_login", self.log_rows("phone-paired")[-1], proxies)

    def pair_via(self, extra):
        status, response, data = self.phone("POST", "/pair", extra=extra,
                                            value={"code": self.new_code(), "name": "tailnet"})
        self.assertEqual(status, 200, data)
        return response.getheader("Set-Cookie").split(";", 1)[0]

    def test_a_login_header_that_is_not_one_clean_value_is_left_out(self):
        self.behind_proxy(["127.0.0.1"])
        base = [("Host", self.HOST), ("Origin", self.ORIGIN), ("Sec-Fetch-Site", "same-origin"),
                ("X-Forwarded-For", "203.0.113.5")]
        cases = [[], [("Tailscale-User-Login", "")], [("Tailscale-User-Login", "   ")],
                 [("Tailscale-User-Login", "a" * 129)],
                 [("Tailscale-User-Login", "alice smith@example.com")],
                 [("Tailscale-User-Login", "alice\t@example.com")],
                 [("Tailscale-User-Login", "alïce@example.com")],
                 [("Tailscale-User-Login", self.LOGIN),
                  ("Tailscale-User-Login", "bob@example.com")]]
        for extra in cases:
            self.clock.advance(61)
            status, _, _ = self.phone_raw("POST", "/pair", base + extra, {"code": "ABCDEFGH"})
            self.assertEqual(status, 403, extra)
            self.assertNotIn("tailscale_user_login", self.audit_rows()[-1], extra)
        # one value: kept with the spaces around it trimmed, up to TAILNET_LOGIN_MAX characters
        for sent, kept in ((f"  {self.LOGIN} ", self.LOGIN), ("a" * 128, "a" * 128),
                           ("=?utf-8?q?al=C3=AFce?=@example.com",
                            "=?utf-8?q?al=C3=AFce?=@example.com")):
            self.clock.advance(61)
            self.phone_raw("POST", "/pair", base + [("Tailscale-User-Login", sent)],
                           {"code": "ABCDEFGH"})
            self.assertEqual(self.audit_rows()[-1]["tailscale_user_login"], kept)

    def test_a_login_with_hidden_characters_is_left_out_not_trimmed_clean(self):
        """Spaces and tabs around a login are a header line's padding and go; any other blank or
        control character (vertical tab, form feed, a separator, NEL, no-break space, NUL) leaves
        the whole value out, wherever it sits."""

        class Headers:
            def __init__(self, values):
                self.values = values

            def get_all(self, name, failobj=None):
                return list(self.values) if name == bridge.TAILNET_LOGIN_HEADER else failobj

        def login(values, peer="127.0.0.1"):
            fake = types.SimpleNamespace(
                client_address=(peer, 40000), headers=Headers(values),
                remote=types.SimpleNamespace(
                    settings=types.SimpleNamespace(trusted_proxy=("127.0.0.1",))))
            fake._from_proxy = lambda: bridge.RemoteHandler._from_proxy(fake)
            return bridge.RemoteHandler._tailnet_login(fake)

        self.assertEqual(login([f" \t{self.LOGIN}\t "]), self.LOGIN)
        self.assertEqual(login([self.LOGIN], peer="::ffff:127.0.0.1"), self.LOGIN)
        for hidden in ("\x0b", "\x0c", "\x1c", "\x1f", "\x85", "\xa0", "\x00", "\x7f"):
            for value in (self.LOGIN + hidden, hidden + self.LOGIN, "alice" + hidden + "@x.org"):
                self.assertEqual(login([value]), "", repr(value))
        self.assertEqual(login([]), "")
        self.assertEqual(login([self.LOGIN, self.LOGIN]), "")
        self.assertEqual(login([self.LOGIN], peer="192.0.2.1"), "")

    def test_the_tailnet_login_is_a_record_never_a_gate(self):
        self.behind_proxy(["127.0.0.1"])
        login = {"X-Forwarded-For": "203.0.113.5", "Tailscale-User-Login": self.LOGIN}
        # a login opens nothing: no cookie is no session, a wrong code is a wrong code
        status, response, _ = self.phone("GET", "/", origin=False, fetch=False, extra=login)
        self.assertEqual((status, response.getheader("Location")), (302, "/pair"))
        self.assertEqual(self.phone("GET", "/state", extra=login)[0], 401)
        self.assertEqual(self.phone("POST", "/pair", value={"code": "ABCDEFGH"}, extra=login)[0],
                         403)
        self.assertEqual(self.phone("GET", "/state", host="127.0.0.1", extra=login)[0], 421)
        # and its absence or a junk value closes nothing
        for extra in ({"X-Forwarded-For": "203.0.113.6"},
                      {"X-Forwarded-For": "203.0.113.7", "Tailscale-User-Login": "x y"}):
            cookie = self.pair_via(extra)
            self.assertEqual(self.phone("GET", "/state", cookie=cookie, extra=extra)[0], 200)

    def test_a_paired_phone_gets_the_sky_and_the_ledger(self):
        sky = {"contacts": [{"id": "ledger:L-1", "source": "ledger", "state": "stale",
                             "title": "pick a plan", "since": "2026-09-01T00:00:00Z",
                             "age_days": 40, "tier": "raider", "planet_uid": None, "open": None,
                             "voice": "pick a plan"}],
               "hyper": {"n": 2, "by_source": {"t3": 1, "task": 1, "seat": 0}}, "parked": 0,
               "settled_today": 1, "derelicts": 0, "by_source": {"ledger": 1}, "oldest_days": 40}
        ledger = [{"id": "L-1", "lane": "plans", "title": "pick a plan", "opened": "2026-09-01",
                   "age_days": 40, "blocks": "", "estimate": ""}]
        self.world["sky"] = sky
        self.world["today"] = {"ledger": ledger, "night": None}
        cookie = self.pair()
        # the page decides whether /state lists rooms by uid (joinRooms): both carry them
        for page in ("const W = {};\nfunction closeAll(){}",
                     "const W = {};\nfunction joinRooms(w){ return w; }"):
            with open(self.game, "w", encoding="utf-8") as handle:
                handle.write("<!doctype html><script>\n" + page + "\n</script>")
            status, _, data = self.phone("GET", "/state", cookie=cookie)
            self.assertEqual(status, 200, data)
            world = json.loads(data)
            self.assertEqual((world["sky"], world["today"]["ledger"]), (sky, ledger))
            status, _, html = self.phone("GET", "/", cookie=cookie, origin=False, fetch=False)
            self.assertEqual(status, 200)
            inlined = json.loads(bridge.WORLD_LINE_RE.search(html.decode("utf-8")).group(1))
            self.assertEqual((inlined["sky"], inlined["today"]["ledger"]), (sky, ledger))

    def test_the_phone_gets_the_sky_and_the_ledger_render_game_writes(self):
        """End to end: the page render-game.py writes for an empty folder, served to a paired
        phone. Its /state and its page carry WORLD.sky and today.ledger as the renderer made
        them, whatever is in them."""
        tool = os.path.dirname(os.path.abspath(__file__))
        root = os.path.join(self.tmp.name, "real-root")
        os.makedirs(os.path.join(root, "notes"))
        config = os.path.join(self.tmp.name, "real-config.json")
        with open(config, "w", encoding="utf-8") as handle:
            json.dump({"work_root": root, "tmux_session": "none", "processes": False,
                       "decision_scan": False, "crontab_file": os.path.join(root, "no-crontab"),
                       "cron_syslog_file": "", "history_jsonl": os.path.join(root, "history.jsonl"),
                       "tasks_dir": os.path.join(root, "tasks"), "name": "REAL"}, handle)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.tmp.name,
               "CONTAINER_CONFIG": config, "CONTAINER_OUT": self.game}
        run = subprocess.run([sys.executable, os.path.join(tool, "render-game.py")], cwd=tool,
                             env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        with open(self.game, encoding="utf-8") as handle:
            rendered = json.loads(bridge.WORLD_LINE_RE.search(handle.read()).group(1))
        self.world = rendered          # the bridge's own render would read the same folder
        self.assertIsInstance(rendered.get("sky"), dict)
        self.assertIsInstance(rendered["sky"].get("contacts"), list)
        self.assertIsInstance(rendered["today"].get("ledger"), list)
        cookie = self.pair()
        status, _, data = self.phone("GET", "/state", cookie=cookie)
        self.assertEqual(status, 200, data[:300])
        world = json.loads(data)
        self.assertEqual(world["sky"], rendered["sky"])
        self.assertEqual(world["today"]["ledger"], rendered["today"]["ledger"])
        status, _, html = self.phone("GET", "/", cookie=cookie, origin=False, fetch=False)
        self.assertEqual(status, 200)
        inlined = json.loads(bridge.WORLD_LINE_RE.search(html.decode("utf-8")).group(1))
        self.assertEqual(inlined["sky"], rendered["sky"])
        self.assertEqual(inlined["today"]["ledger"], rendered["today"]["ledger"])
        self.assertIn('"remote":true', html.decode("utf-8").replace(" ", ""))


class UiLockTests(unittest.TestCase):
    """The opt-in lock for a computer other people log in to: without it GET / is today's one-click flow; with it the
    page opens only for whoever brings this start's launch key, and the key leaves the address bar at once."""
    KEY = "k" * 43

    def start(self, **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = self.tmp.name
        self.game = os.path.join(root, "game.html")
        with open(self.game, "w", encoding="utf-8") as handle:
            handle.write("<!doctype html><style>body{color:white}</style><script>\n"
                         "const W = {};\nfunction closeAll(){}\n</script>")
        self.log = os.path.join(root, "bridge.log")
        self.live = os.path.join(root, "run", "bridge-1.json")
        self.app = bridge.BridgeApp(
            seats_path=os.path.join(root, "no-seats.json"), game_path=self.game, renderer_path="unused",
            live_path=self.live, log_path=self.log, tmux_runner=FakeTmux(),
            world_loader=lambda: {"chars": [], "quests": [], "tasks": {"source": "audit", "items": []},
                                  "activity": [], "inflight": [], "generated": "now", "oldest": 0},
            hop_runner=FakeHop(), synced_dir=os.path.join(root, "synced"), file_roots=(root,),
            areas_path=os.path.join(root, "areas.json"), verbs_dir=os.path.join(root, "verbs"),
            runs_path=os.path.join(root, "state", "runs.jsonl"),
            rulings_path=os.path.join(root, "state", "rulings.json"),
            tasks_dir=os.path.join(root, "tasks"), agent_verify=False, tick_seconds=0, **kw)
        self.server = bridge.BridgeHTTPServer((bridge.LOOPBACK, 0), bridge.Handler, self.app)
        self.app.port = self.server.server_port
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=2)

        self.addCleanup(stop)

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        result = (response.status, dict(response.getheaders()), data)
        conn.close()
        return result

    def test_unlocked_is_todays_one_click_session(self):
        self.start(ui_lock=False)
        status, headers, _ = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(self.get("/verbs", {"Cookie": cookie})[0], 200)
        self.app.write_live()
        with open(self.live, encoding="utf-8") as handle:
            live = json.load(handle)
        self.assertNotIn("launch_key", live)
        self.assertNotIn("ui_lock", live)

    def test_the_config_key_and_the_environment_turn_the_lock_on(self):
        with mock.patch.dict(bridge._CFG, {"ui_lock": True}):
            self.start()
        self.assertTrue(self.app.ui_lock)
        self.assertEqual(self.get("/")[0], 403)
        with mock.patch.dict(os.environ, {"BRIDGE_UI_LOCK": "1"}):
            self.start()
        self.assertTrue(self.app.ui_lock)
        self.start()
        self.assertFalse(self.app.ui_lock)

    def test_locked_page_is_403_plain_and_gives_no_session(self):
        self.start(ui_lock=True, launch_key=self.KEY)
        for path in ("/", "/game.html", "/?fresh=1", "/?k=", "/?k=wrong", "/?k=" + self.KEY + "x",
                     "/?k=" + "a" * 5000, "/?k=" + self.KEY + "&scene3d=1", "/?scene3d=1&k=" + self.KEY,
                     "/?k=" + self.KEY + "&k=" + self.KEY):
            with self.subTest(path=path[:60]):
                status, headers, data = self.get(path)
                self.assertEqual(status, 403)
                self.assertNotIn("Set-Cookie", headers)
                self.assertIn(b"locked", data)
                self.assertNotIn(self.KEY.encode(), data)
                self.assertNotIn(self.app.ui_token.encode(), data)
        self.assertEqual(self.get("/verbs")[0], 403)

    def test_the_right_key_sets_the_cookie_and_redirects_the_key_away(self):
        self.start(ui_lock=True, launch_key=self.KEY)
        for page in ("/", "/game.html"):
            with self.subTest(page=page):
                status, headers, data = self.get(page + "?k=" + self.KEY)
                self.assertEqual((status, headers["Location"], data), (303, page, b""))
                self.assertIn("HttpOnly", headers["Set-Cookie"])
                self.assertNotIn(self.KEY, "".join(headers.values()))
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                self.assertEqual(self.get(page, {"Cookie": cookie})[0], 200)
                self.assertEqual(self.get("/verbs", {"Cookie": cookie})[0], 200)
                # the same link opened again in that browser: in already, the key just leaves the address bar
                status, headers, data = self.get(page + "?k=" + self.KEY, {"Cookie": cookie})
                self.assertEqual((status, headers["Location"], data), (303, page, b""))
                self.assertNotIn(self.KEY, "".join(headers.values()))
                again = self.get(page + "?k=" + self.KEY, {"Cookie": cookie, "Origin": "http://evil.example"})
                self.assertEqual(again[0], 403, "another site's page still cannot ride it")
        # a page of another site cannot ride a leaked link: an Origin that is not ours is refused
        status, headers, _ = self.get("/?k=" + self.KEY, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("Set-Cookie", headers)

    def test_a_stranger_cookie_and_the_session_token_are_not_the_key(self):
        self.start(ui_lock=True, launch_key=self.KEY)
        self.assertEqual(self.get("/", {"Cookie": f"{bridge.COOKIE_NAME}=nope"})[0], 403)
        self.assertEqual(self.get("/?k=" + self.app.ui_token)[0], 403)

    def test_the_key_is_random_per_start_and_a_short_one_is_replaced(self):
        keys = set()
        for _ in range(3):
            self.start(ui_lock=True)
            keys.add(self.app.launch_key)
        self.assertEqual(len(keys), 3)
        self.assertTrue(all(len(k) >= 40 for k in keys))
        self.start(ui_lock=True, launch_key="short")
        self.assertNotEqual(self.app.launch_key, "short")
        self.assertNotEqual(self.app.launch_key, self.app.ui_token)
        self.assertNotEqual(self.app.launch_key, self.app.agent_token)

    def test_the_key_is_compared_in_constant_time(self):
        self.start(ui_lock=True, launch_key=self.KEY)
        with mock.patch.object(bridge.hmac, "compare_digest", wraps=bridge.hmac.compare_digest) as compared:
            self.get("/?k=wrong-key-for-the-test")
        # the Host check compares too: what matters is a call with the key given and the launch key itself
        pairs = {(bytes(a), bytes(b)) for a, b in (c.args for c in compared.call_args_list)}
        self.assertIn((b"wrong-key-for-the-test", self.KEY.encode()), pairs)

    def test_the_key_is_only_in_the_private_live_file_and_in_no_log(self):
        self.start(ui_lock=True, launch_key=self.KEY)
        self.app.write_live()
        self.assertEqual(stat.S_IMODE(os.stat(self.live).st_mode), 0o600)
        with open(self.live, encoding="utf-8") as handle:
            live = json.load(handle)
        self.assertEqual((live["launch_key"], live["ui_lock"]), (self.KEY, True))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.get("/?k=wrong")
            _, headers, _ = self.get("/?k=" + self.KEY)
            cookie = headers["Set-Cookie"].split(";", 1)[0]
            self.get("/", {"Cookie": cookie})
            self.get("/nope?k=" + self.KEY)
            self.get("/file?path=/?k=" + self.KEY, {"Cookie": cookie})
            self.app.client_gone("GET /?k=wrong")
        self.assertNotIn(self.KEY, out.getvalue() + err.getvalue())
        self.assertTrue(os.path.exists(self.log))      # the client_gone line above
        for name in os.listdir(self.tmp.name):
            path = os.path.join(self.tmp.name, name)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    self.assertNotIn(self.KEY.encode(), handle.read(), name)

    def test_the_agent_token_still_works_without_a_session(self):
        self.start(ui_lock=True, launch_key=self.KEY)
        self.assertEqual(self.get("/agent/map")[0], 403)
        status, _, data = self.get("/agent/map", {"Authorization": "Bearer " + self.app.agent_token})
        self.assertEqual(status, 200, data)

    def test_the_phone_listener_is_not_behind_the_lock(self):
        reservation = socket.socket()
        self.addCleanup(reservation.close)
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
        self.start(ui_lock=True, launch_key=self.KEY,
                   remote={"listen": "127.0.0.1", "port": port, "public_origin": "https://phone.example.com"})
        reservation.close()
        self.assertTrue(self.app.start_remote(), self.app.remote.refused or self.app.remote.error)
        self.addCleanup(self.app.stop_remote)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/", headers={"Host": "phone.example.com"})
        status = conn.getresponse().status
        conn.close()
        self.assertNotEqual(status, 403)      # the pairing page, as before
        self.assertEqual(self.get("/")[0], 403)

    def test_main_takes_the_key_out_of_the_environment(self):
        captured = {}

        class Stop(Exception):
            pass

        def boom(**kw):
            captured.update(kw)
            raise Stop

        with mock.patch.dict(os.environ, {"BRIDGE_LAUNCH_KEY": self.KEY}), \
                mock.patch.object(bridge, "BridgeApp", boom):
            with self.assertRaises(Stop):
                bridge.main(["--port", "1"])
            self.assertNotIn("BRIDGE_LAUNCH_KEY", os.environ)
        self.assertEqual(captured["launch_key"], self.KEY)


class FileDenyTests(unittest.TestCase):
    """GET /file never shows common credential files, and never refuses an ordinary project file."""
    DENIED = (
        "prod.tfvars", "prod.auto.tfvars", "prod.tfvars.json", "terraform.tfstate", "terraform.tfstate.backup",
        "service-account.json", "service-account-ci.json", "Service_Account_Key.json", "serviceaccount.json",
        "client_secret_1234.apps.json", "application_default_credentials.json", "credentials.json",
        ".vault-token", "id_dsa", "id_dsa.pub", "work.ovpn", "backup.gpg", "mail.pgp", ".htpasswd", ".s3cfg",
        ".boto", "app.p8", "putty.ppk", "release.jks", "svc.keytab", ".my.cnf", ".terraformrc", "kubeconfig",
        ".password-store/mail/me.txt", ".terraform.d/credentials.tfrc.json", ".config/gcloud/properties",
        ".config/gcloud/legacy_credentials/me/adc.json", ".CONFIG/GCloud/x", ".config/gh/hosts.yml",
        ".config/rclone/rclone.conf", "SERVER.TFVARS", ".VAULT-TOKEN", ".m2/settings.xml",
        "id_rsa", "server.pem", "x.key", ".env.local", ".aws/config")
    ALLOWED = ("README.md", "src/key_handler.py", "keyboard.ts", "tokens.css", "config/gcloud-notes.md",
               ".config/alacritty.toml", "terraform/main.tf", "terraform.tfvars.example", "vars.tf",
               "docs/service-accounts.md", "src/passwords.py", "package.json", "settings.xml",
               "gradle.properties", "oauth.py", "ids.json", "id_card.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.app = bridge.BridgeApp.__new__(bridge.BridgeApp)
        self.app.file_roots = (self.root,)

    def make(self, rel):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("fake content, nothing real\n")
        return path

    def test_each_new_name_is_denied(self):
        for rel in self.DENIED:
            with self.subTest(rel=rel):
                with self.assertRaises(bridge.BridgeError) as caught:
                    self.app._checked_file(self.make(rel))
                self.assertEqual(caught.exception.status, 403)

    def test_ordinary_project_files_stay_previewable(self):
        for rel in self.ALLOWED:
            with self.subTest(rel=rel):
                path = self.make(rel)
                self.assertEqual(self.app._checked_file(path), os.path.realpath(path))
                self.assertEqual(self.app.read_file(path)["text"].strip(), "fake content, nothing real")

    def test_a_credential_marker_anywhere_in_the_name_is_denied(self):
        for rel in ("my-service-account.json", "prod-service_account-key.json", "old-client-secret.json",
                    "keys/ci-application_default_credentials.json"):
            with self.subTest(rel=rel):
                with self.assertRaises(bridge.BridgeError) as caught:
                    self.app._checked_file(self.make(rel))
                self.assertEqual(caught.exception.status, 403)

    def test_a_symlink_does_not_carry_a_request_past_the_rules(self):
        # asked through a credentials-folder name that points somewhere plain
        self.make("plain/hosts.yml")
        os.makedirs(os.path.join(self.root, ".config"), exist_ok=True)
        os.symlink(os.path.join(self.root, "plain"), os.path.join(self.root, ".config", "gh"))
        # and asked through a plain name that points into a credentials folder
        self.make(".config/rclone/rclone.conf")
        os.symlink(os.path.join(self.root, ".config", "rclone"), os.path.join(self.root, "notes-link"))
        # and a chain: a plain name -> a credentials-folder name -> a plain folder
        os.symlink(os.path.join(self.root, ".config", "gh"), os.path.join(self.root, "docs-link"))
        for rel in (".config/gh/hosts.yml", "notes-link/rclone.conf", "docs-link/hosts.yml"):
            with self.subTest(rel=rel):
                with self.assertRaises(bridge.BridgeError) as caught:
                    self.app._checked_file(os.path.join(self.root, rel))
                self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.app._checked_file(os.path.join(self.root, "plain", "hosts.yml")),
                         os.path.join(self.root, "plain", "hosts.yml"))

    def test_a_chain_with_a_dotdot_does_not_skip_a_name_it_passes(self):
        # start -> docs/../hosts.yml, docs -> .config/gh/sub, .config/gh -> ../plain: it ends at plain/hosts.yml,
        # through .config/gh, which a folded '..' would never show
        self.make("plain/hosts.yml")
        os.makedirs(os.path.join(self.root, "plain", "sub"), exist_ok=True)
        os.makedirs(os.path.join(self.root, ".config"), exist_ok=True)
        os.symlink("../plain", os.path.join(self.root, ".config", "gh"))
        os.symlink(".config/gh/sub", os.path.join(self.root, "docs"))
        os.symlink("docs/../hosts.yml", os.path.join(self.root, "start"))
        with self.assertRaises(bridge.BridgeError) as caught:
            self.app._checked_file(os.path.join(self.root, "start"))
        self.assertEqual(caught.exception.status, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
